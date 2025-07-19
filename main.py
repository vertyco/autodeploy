"""Compile with 'pyinstaller.exe --clean app.spec'"""

import logging
import os
import sys
from configparser import ConfigParser
from pathlib import Path
from time import perf_counter, sleep

from watchdog.events import (
    FileModifiedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from utils import Const, LogFormatter, PrettyFormatter, Tools

IS_EXE = True if (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")) else False
if IS_EXE:
    ROOT_DIR = Path(os.path.dirname(os.path.abspath(sys.executable)))
else:
    ROOT_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent


log = logging.getLogger("autodeploy")


def setup_logging():
    console = logging.StreamHandler()
    console.setFormatter(PrettyFormatter())
    log.setLevel(logging.DEBUG)
    console.setLevel(logging.DEBUG)
    log.addHandler(console)
    host = os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME")
    logpath = ROOT_DIR / ".autodeploy-logs"
    logpath.mkdir(parents=True, exist_ok=True)
    logfile = logpath / f"{host}.log"
    file = logging.FileHandler(logfile, mode="a")
    file.setFormatter(LogFormatter())
    file.setLevel(logging.DEBUG)
    log.addHandler(file)


class CustomEventHandler(FileSystemEventHandler):
    def __init__(self, target_file: Path, target_process: str):
        super().__init__()
        self.target_file = target_file
        self.target_process = target_process

        # To prevent duplicate events when editors call the save kernel twice
        self._debounce_time = 5
        self._last_events: dict[str, float] = {}
        self._target_hash = Tools.file_hash(self.target_file)

    def _is_duplicate_event(self, event: FileSystemEvent) -> bool:
        """
        Check if the event is a duplicate based on the debounce time.
        Returns True if the event is a duplicate, False otherwise.
        """
        # Use a more unique key based on relative path and event type
        path = (
            event.dest_path
            if hasattr(event, "dest_path") and event.dest_path
            else event.src_path
        )
        event_key = f"{os.path.basename(path)}:{type(event).__name__}"
        # log.debug(event)
        current_time = perf_counter()
        last_event_time = self._last_events.get(event_key, 0)
        delta = current_time - last_event_time
        if delta < self._debounce_time:
            log.debug(
                f"Ignoring event {type(event)} due to debounce delta {delta:.3f} seconds"
            )
            return True
        self._last_events[event_key] = current_time
        # log.debug("---")
        return False

    def on_modified(self, event: FileModifiedEvent) -> None:
        if self._is_duplicate_event(event):
            return

        # Make sure file names match
        if Path(event.src_path).name != self.target_file.name:
            return

        log.info(f"{self.target_file.name} was modified, comparing hashes")
        Tools.wait_until_file_lock_released(event.src_path)

        self.do_update(Path(event.src_path))

    def do_update(self, source_path: Path) -> None:
        # Compare file hashes
        if Tools.file_hash(source_path) == self._target_hash:
            log.info("File hash matches, no action needed.")
            return

        log.info("File hash does not match, updating target file!")
        if Tools.is_running(self.target_process):
            log.info(f"Killing {self.target_process} process")
            Tools.kill(self.target_process)
            sleep(5)

        if "arkview" in self.target_process.lower():
            Tools.kill("ASVExport.exe")

        tmp_path = self.target_file.parent / f"{self.target_file.stem}.tmp"
        try:
            with open(source_path, "rb") as src_file:
                with open(tmp_path, "wb") as target_file:
                    target_file.write(src_file.read())
                    target_file.flush()
                    os.fsync(target_file.fileno())

            tries = 0
            while tries < 10:
                tries += 1
                try:
                    self.target_file.unlink(missing_ok=True)
                    break
                except PermissionError:
                    log.debug("Something is accessing the target file, waiting...")
                    Tools.kill(self.target_process)
                    sleep(3)

            tmp_path.rename(self.target_file)

            if o_dir := getattr(os, "O_DIRECTORY", None):
                fd = os.open(self.target_file.parent, o_dir)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except Exception as e:
            log.error(f"Failed to read source file {source_path}", exc_info=e)
            if tmp_path.exists():
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception as e:
                    log.error(f"Failed to remove temporary file {tmp_path}", exc_info=e)

        # Now we need to restart the process
        if (
            not Tools.is_running(self.target_process)
            and "arkwipe" not in str(self.target_file).lower()
            and self.target_file.name.endswith(".exe")
        ):
            if Tools.is_unc_path(self.target_file):
                log.warning("Target file is on a UNC path, cannot start process.")
            else:
                log.info(f"Starting {self.target_process} process back up")
                current_dir = os.getcwd()
                os.chdir(self.target_file.parent)
                os.system(f"start /MIN {self.target_file.name}")
                os.chdir(current_dir)

        log.info(f"Updated {self.target_file.name} successfully!")
        self._target_hash = Tools.file_hash(self.target_file)


class AutoDeploy:
    def __init__(self):
        self.observer = Observer()

    def run(self):
        parser = ConfigParser()
        configpath = Path(os.getcwd()) / "config.ini"
        if not configpath.exists():
            log.error("No config file! Making default")
            with open(configpath, "w") as f:
                parser["Settings"] = Const().defaults
                parser.write(f)
            print("\nPlease update the config file and restart the program.")
            input("Press any key to exit")
            return

        parser.read(configpath)
        settings = parser["Settings"]
        for app_name, paths in settings.items():
            parts = [i.replace('"', "").strip() for i in paths.split(",")]
            if len(parts) not in (3, 4):
                log.error(f"Skipping {app_name} due to missing parts: {paths}")
                continue
            if len(parts) == 3:
                process_name, source_raw, target_raw = parts
            else:
                process_name, source_raw, target_raw, _ = parts

            source, target = Path(source_raw), Path(target_raw)
            if not source.exists():
                log.error(f"Source file for {app_name} not found!")
                continue
            if not source.is_file():
                log.error(f"Source for {app_name} is not a file!")
                continue
            if not target.is_file():
                log.error(f"Target for {app_name} is not a file!")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)

            log.info(f"Watching {source} for changes...")
            handler = CustomEventHandler(
                target_file=target,
                target_process=process_name,
            )
            handler.do_update(source)
            self.observer.schedule(handler, path=source.parent, recursive=False)

        self.observer.start()
        try:
            while True:
                sleep(1)
        finally:
            self.observer.stop()
            self.observer.join()


if __name__ == "__main__":
    setup_logging()
    try:
        AutoDeploy().run()
    except Exception as e:
        log.error("An error occurred during deployment", exc_info=e)
        input("Press any key to exit")
