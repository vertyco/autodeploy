import hashlib
import logging
import os
import sys
import time
from configparser import ConfigParser
from contextlib import suppress
from pathlib import Path
from time import perf_counter

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from utils import Const, LogFormatter, PrettyFormatter, Tools

IS_WINDOWS: bool = sys.platform.startswith("win")
IS_EXE = True if (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")) else False
if IS_EXE and IS_WINDOWS:
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


def file_hash(path: Path | str, algo="sha256") -> str:
    """Compute the hash of a file."""
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class CustomEventHandler(FileSystemEventHandler):
    def __init__(self, target_file: Path, target_process: str):
        super().__init__()
        self.target_file = target_file
        self.target_process = target_process
        self._debounce_time = (
            0.03  # To prevent duplicate events when editors call the save kernel twice
        )
        self._last_events: dict[str, float] = {}
        self._target_hash = file_hash(self.target_file)

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
        log.debug(event)
        current_time = perf_counter()
        last_event_time = self._last_events.get(event_key, 0)
        delta = current_time - last_event_time
        if delta < self._debounce_time:
            log.debug(
                f"Ignoring event {type(event)} due to debounce delta {delta:.3f} seconds"
            )
            return True
        self._last_events[event_key] = current_time
        log.debug("---")
        return False

    def on_modified(self, event: FileModifiedEvent) -> None:
        if self._is_duplicate_event(event):
            return

        log.info(f"{self.target_file.name} was modified!")


class AutoDeploy:
    """Compile with 'pyinstaller.exe --clean app.spec'"""

    def __init__(self):
        self.cwd = os.getcwd()

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

            handler = CustomEventHandler(
                target_file=target, target_process=process_name
            )
            self.observer.schedule(handler, path=source.parent, recursive=False)
            log.info(f"Watching {source} for changes...")

        self.observer.start()
        try:
            while True:
                time.sleep(0.5)
        finally:
            self.observer.stop()
            self.observer.join()


if __name__ == "__main__":
    setup_logging()
    try:
        AutoDeploy().run()
    except Exception as e:
        log.error("An error occurred during deployment", exc_info=e)
        # input("Press any key to exit")
