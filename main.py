import hashlib
import logging
import os
import sys
import time
from configparser import ConfigParser
from contextlib import suppress
from pathlib import Path

from utils import Const, LogFormatter, PrettyFormatter, Tools

# Config setup
parser = ConfigParser()
# Log setup
log = logging.getLogger("autodeploy")
# Console logs
console = logging.StreamHandler()
console.setFormatter(PrettyFormatter())
# Set log level
log.setLevel(logging.DEBUG)
console.setLevel(logging.DEBUG)
# Add handlers
log.addHandler(console)

IS_WINDOWS: bool = sys.platform.startswith("win")
IS_EXE = True if (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")) else False
if IS_EXE and IS_WINDOWS:
    ROOT_DIR = Path(os.path.dirname(os.path.abspath(sys.executable)))
else:
    ROOT_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent

# Get the host system name
host = os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME")
if host != "ALEX-DESKTOP":
    # Set log file
    logpath = ROOT_DIR / ".autodeploy-logs"
    logpath.mkdir(parents=True, exist_ok=True)
    logfile = logpath / f"{host}.log"
    file = logging.FileHandler(logfile, mode="a")
    file.setFormatter(LogFormatter())
    file.setLevel(logging.DEBUG)
    log.addHandler(file)


def file_hash(path, algo="sha256") -> str:
    """Compute the hash of a file."""
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class AutoDeploy:
    """Compile with 'pyinstaller.exe --clean app.spec'"""

    def __init__(self):
        self.cwd = os.getcwd()

    def run(self):
        configpath = Path(self.cwd) / "config.ini"
        if not configpath.exists():
            log.error("No config file! Making default")
            with open(configpath, "w") as f:
                parser["Settings"] = Const().defaults
                parser.write(f)
            print("\nPlease update the config file and restart the program.")
            input("Press any key to exit")
            return

        with suppress(Exception):
            killed = Tools().kill("ASVExport.exe")
            if killed:
                log.info("Killed ASVExport.exe")

        parser.read(configpath)
        settings = parser["Settings"]
        for app_name, app_paths in settings.items():
            parts = [i.replace('"', "").strip() for i in app_paths.split(",")]
            if len(parts) < 3:
                log.warning(f"Skipping {app_name} due to missing parts: {app_paths}")
                continue
            proc, source_raw, target_raw = parts
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

            # Compare file hashes instead of just file sizes
            try:
                source_hash = file_hash(source)
                target_hash = file_hash(target)
                if source_hash == target_hash:
                    log.info(f"{app_name} is already up to date, skipping.")
                    continue
            except Exception as e:
                log.error(f"Failed to compare files for {app_name}", exc_info=e)
                continue

            log.warning(f"Updating {app_name}")
            target.parent.mkdir(parents=True, exist_ok=True)

            tries = 0
            while tries < 3:
                tries += 1
                try:
                    killed = Tools().kill(proc)
                    if killed:
                        log.info(f"Killed {proc}")
                    break
                except Exception as e:
                    log.error(f"Failed to kill {proc}", exc_info=e)
                    continue

            log.info("Writing new file")
            try:
                source_file_bytes = source.read_bytes()
                tmp_path = target.parent / f"{target.stem}.tmp"
                with tmp_path.open(mode="wb") as fs:
                    fs.write(source_file_bytes)
                    fs.flush()
                    os.fsync(fs.fileno())

                tries = 0
                while tries < 3:
                    tries += 1
                    try:
                        target.unlink(missing_ok=True)
                        break
                    except PermissionError:
                        log.debug(f"Waiting for {app_name} to close")
                        time.sleep(0.5)
                        with suppress(Exception):
                            Tools().kill(proc)
                        continue

                tmp_path.rename(target)

                if hasattr(os, "O_DIRECTORY"):
                    fd = os.open(target.parent, os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            except Exception as e:
                log.error(f"Failed to write new file for {app_name}", exc_info=e)
                continue

            if "arkwipe" not in str(target).lower():
                time.sleep(0.5)
                os.chdir(target.parent)  # Change to target directory
                os.system(f"start /MIN {target.name}")
                os.chdir(self.cwd)  # Go back to original directory

        log.info("Update COMPLETE")


if __name__ == "__main__":
    try:
        AutoDeploy().run()
    except Exception as e:
        log.error("An error occurred during deployment", exc_info=e)
        # input("Press any key to exit")
