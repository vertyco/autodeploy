"""Compile with 'pyinstaller.exe --clean app.spec'"""

import csv
import logging
import os
import shutil
import subprocess
import sys
from configparser import ConfigParser
from pathlib import Path
from time import sleep

from utils import (
    Const,
    LogFormatter,
    SafeRotatingFileHandler,
    Tools,
    disable_quickedit,
    enable_console_vt,
)

log = logging.getLogger("autodeploy")
IS_EXE = True if (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")) else False
if IS_EXE:
    ROOT_DIR = Path(os.path.dirname(os.path.abspath(sys.executable)))
else:
    ROOT_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent

HOSTNAME = os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME")
CONFIG_PATH = ROOT_DIR / "config.ini"
PARSER = ConfigParser(interpolation=None)

red = lambda text: f"\033[91m{text}\033[0m"  # noqa
blue = lambda text: f"\033[94m{text}\033[0m"  # noqa
yellow = lambda text: f"\033[93m{text}\033[0m"  # noqa


def pause_and_exit(message: str = "Press any key to exit") -> None:
    """Wait for a keypress only when running interactively.

    When launched headless (scheduled task, service, no console stdin),
    ``input()`` would block forever; skip it so the process exits cleanly.
    """
    if sys.stdin is not None and sys.stdin.isatty():
        input(message)


class AutoDeploy:
    def __init__(self):
        self.skipped: set[str] = set()
        # {target: source signature at last deploy}. A signature is
        # (size, sha256(tail)) -- see Tools.tail_signature. We trust this
        # record instead of re-reading the target every cycle.
        self.deployed_sig: dict[str, tuple[int, str]] = {}
        self.config_mtime: float = 0.0

    @classmethod
    def run(cls):
        """Sets up and runs the application."""
        # Console init first: stop accidental clicks from pausing the loop,
        # and make ANSI colors render on a fresh PyInstaller console.
        disable_quickedit()
        enable_console_vt()
        cls.setup_logging()

        if not CONFIG_PATH.exists():
            print(red("No config file found!"))
            with open(CONFIG_PATH, "w") as f:
                parser = ConfigParser(interpolation=None)
                parser["Settings"] = Const().defaults
                parser.write(f)
            print(yellow("\nPlease update the config file and restart the program."))
            pause_and_exit()
            return

        if not HOSTNAME:
            log.error("Hostname not found!")
            pause_and_exit()
            return

        log.info("AutoDeploy service started.")
        log.info(f"Config file: {CONFIG_PATH}")
        cls().update_checker()

    @staticmethod
    def setup_logging():
        console = logging.StreamHandler()
        console.setFormatter(LogFormatter())
        log.setLevel(logging.DEBUG)
        console.setLevel(logging.DEBUG)
        log.addHandler(console)
        log_folder = ROOT_DIR / ".autodeploy-logs"
        log_file = log_folder / f"{HOSTNAME}.log"
        os.makedirs(log_folder, exist_ok=True)
        file = SafeRotatingFileHandler(
            filename=str(log_file),
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        file.setFormatter(LogFormatter())
        file.setLevel(logging.DEBUG)
        log.addHandler(file)

    def update_checker(self):
        global PARSER
        while True:
            sleep(5)
            try:
                # Only re-parse config when the file has changed
                try:
                    cfg_mtime = os.path.getmtime(CONFIG_PATH)
                except OSError:
                    cfg_mtime = 0.0
                if cfg_mtime != self.config_mtime:
                    # Fresh parser so entries removed from the file stop being
                    # managed (ConfigParser.read merges, it never deletes keys).
                    PARSER = ConfigParser(interpolation=None)
                    PARSER.read(CONFIG_PATH)
                    self.config_mtime = cfg_mtime
                settings = PARSER["Settings"]
                for app_name, paths in settings.items():
                    try:
                        # csv parse respects quotes, so paths containing commas
                        # survive; drop empty fields from stray/trailing commas.
                        parts = [
                            p.strip()
                            for p in next(csv.reader([paths], skipinitialspace=True))
                            if p.strip()
                        ]
                        if len(parts) < 3:
                            log.error(
                                f"Skipping {app_name} due to missing parts: {paths}"
                            )
                            continue
                        process_name, source, target = parts[:3]
                        related_processes = parts[3:]
                        if not os.path.exists(source):
                            log.error(f"Source file for {app_name} not found!")
                            continue
                        if not os.path.isfile(source):
                            log.error(f"Source for {app_name} is not a file!")
                            continue
                        if os.path.exists(target) and not os.path.isfile(target):
                            log.error(f"Target for {app_name} is not a file!")
                            continue
                        # If the paths parent doesnt exist, skip it
                        if not os.path.exists(Path(target).parent):
                            if app_name not in self.skipped:
                                self.skipped.add(app_name)
                                log.warning(
                                    f"Parent directory for {app_name} target file does not exist, skipping."
                                )
                            continue
                        # Parent exists again: re-arm the warning if it vanishes later.
                        self.skipped.discard(app_name)
                        os.makedirs(str(Path(target).parent), exist_ok=True)
                        self.check_update(
                            process_name, source, target, related_processes
                        )
                    except Exception as e:
                        log.error(f"Error processing {app_name}", exc_info=e)
                        continue
            except KeyError:
                log.error(
                    "Config file is missing the [Settings] section. Please check."
                )
            except Exception as e:
                log.error("An unexpected error occurred", exc_info=e)
            sleep(60)

    def check_update(
        self, process_name: str, source: str, target: str, related_processes: list[str]
    ):
        # Cheap change signature: (size, hash of the file tail). For a
        # PyInstaller exe the appended archive + TOC + cookie live at the tail,
        # so a rebuild changes this without reading the whole file over the
        # network. mtime is deliberately ignored (unreliable over SMB).
        source_sig = Tools.tail_signature(source)
        if source_sig is None:
            # Source unreadable this cycle; skip and try again next loop.
            return

        known = self.deployed_sig.get(target)
        if known is None:
            # First time we've seen this target this run: read the target once
            # to learn its state, so a restart doesn't redeploy everything.
            if Tools.tail_signature(target) == source_sig:
                self.deployed_sig[target] = source_sig
                return
        elif known == source_sig:
            # Already deployed this version; no target read needed.
            return

        Tools.wait_until_file_lock_released(target)

        log.info(f"Updating {process_name} from {source}!")
        if Tools.is_running(process_name):
            log.info(f"Killing {process_name} process")
            if Tools.kill(process_name):
                sleep(5)  # Wait for process to terminate and release handles

        for proc in related_processes:
            if Tools.is_running(proc):
                log.info(f"Killing related process {proc}")
                if Tools.kill(proc):
                    sleep(1)

        tmp_path = Path(target).with_suffix(".tmp")
        replaced = False
        try:
            with open(source, "rb") as src_file:
                with open(tmp_path, "wb") as target_file:
                    shutil.copyfileobj(src_file, target_file)
                    target_file.flush()
                    os.fsync(target_file.fileno())

            tries = 0
            while tries < 10:
                tries += 1
                try:
                    os.replace(tmp_path, target)
                    replaced = True
                    break
                except (PermissionError, OSError, IOError):
                    log.debug("Something is accessing the target file, waiting...")
                    # Best-effort: kill the holder if it's our process, then
                    # always back off. The lock may be held by antivirus or an
                    # SMB oplock that no kill releases, so the sleep must not be
                    # conditional on kill succeeding.
                    Tools.kill(process_name)
                    sleep(3)

        except Exception as e:
            log.error(f"Failed to update {target} from {source}", exc_info=e)

        if not replaced:
            log.error(
                f"Could not replace {target} after multiple attempts; will retry next cycle."
            )
            if tmp_path.exists():
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception as e:
                    log.error(f"Failed to remove temporary file {tmp_path}", exc_info=e)
            return

        if (
            not Tools.is_running(process_name)
            and "arkwipe" not in str(target).lower()
            and target.lower().endswith(".exe")
        ):
            if Tools.is_unc_path(target):
                log.warning("Target file is on a UNC path, cannot start process.")
            else:
                log.info(f"Starting {Path(target).name} process back up")
                # Use subprocess.Popen to start the process in its own directory
                # without changing the current working directory.
                subprocess.Popen(
                    [target],
                    cwd=Path(target).parent,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )

        log.info(f"Update completed for {process_name}")
        # Target now equals source byte-for-byte, so record the source
        # signature as deployed -- no need to re-read the target.
        self.deployed_sig[target] = source_sig


if __name__ == "__main__":
    AutoDeploy.run()
