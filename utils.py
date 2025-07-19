import hashlib
import logging
from pathlib import Path
from time import perf_counter, sleep
from typing import Optional

import colorama
import psutil
from colorama import Back, Fore, Style

log = logging.getLogger("autodeploy.utils")


class PrettyFormatter(logging.Formatter):
    colorama.init(autoreset=True)
    fmt = "%(asctime)s - %(levelname)s - %(message)s"
    formats = {
        logging.DEBUG: Fore.LIGHTGREEN_EX + Style.BRIGHT + fmt,
        logging.INFO: Fore.LIGHTWHITE_EX + Style.BRIGHT + fmt,
        logging.WARNING: Fore.YELLOW + Style.BRIGHT + fmt,
        logging.ERROR: Fore.LIGHTMAGENTA_EX + Style.BRIGHT + fmt,
        logging.CRITICAL: Fore.LIGHTYELLOW_EX + Back.RED + Style.BRIGHT + fmt,
    }

    def format(self, record):
        log_fmt = self.formats.get(record.levelno)
        formatter = logging.Formatter(fmt=log_fmt, datefmt="%m/%d %I:%M:%S %p")
        return formatter.format(record)


class LogFormatter(logging.Formatter):
    def format(self, record):
        log_fmt = "%(asctime)s - %(levelname)s - %(message)s"
        formatter = logging.Formatter(fmt=log_fmt, datefmt="%m/%d %I:%M:%S %p")
        return formatter.format(record)


class Tools:
    @staticmethod
    def is_running(process: str) -> bool:
        """Check if a process is running"""
        running = [p.name().lower() for p in psutil.process_iter()]
        return process.lower() in running

    @staticmethod
    def kill(process: str) -> bool:
        while True:
            try:
                for p in psutil.process_iter():
                    if p.name().lower() == process.lower():
                        p.kill()
                        return True
                return False
            except Exception as e:
                log.warning(f"Failed to kill process {process}: {e}")
                sleep(1)

    @staticmethod
    def get_proc_path(process: str) -> Optional[Path]:
        for p in psutil.process_iter():
            try:
                path = Path(p.cwd())
            except Exception:
                continue
            name = p.name()
            if name == process:
                return path

    @staticmethod
    def file_hash(path: Path | str, algo="sha256") -> str:
        """Compute the hash of a file."""
        h = hashlib.new(algo)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def wait_until_file_lock_released(file_path: Path, timeout: int = 60) -> bool:
        """Wait until the file lock is released."""
        start_time = perf_counter()
        while True:
            try:
                with open(file_path, "rb"):
                    return True
            except (IOError, PermissionError):
                if perf_counter() - start_time > timeout:
                    log.error(f"Timeout waiting for file lock on {file_path}")
                    return False
            sleep(0.1)

    @staticmethod
    def is_unc_path(path: Path | str) -> bool:
        """Check if the path is a UNC path."""
        if isinstance(path, str):
            path = Path(path)
        return path.is_absolute() and path.parts[0].startswith("\\\\")


class Const:
    defaults = {
        "arkviewer": '"ArkViewer.exe", "Path/To/Source/File", "Path/To/Target/File"',
        "arkhandler": '"ArkHandler.exe", "Path/To/Source/File", "Path/To/Target/File"',
    }
