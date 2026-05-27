import hashlib
import logging
import os
import subprocess
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter, sleep

log = logging.getLogger("autodeploy.utils")


class LogFormatter(logging.Formatter):
    def __init__(self):
        super().__init__(fmt="%(asctime)s - %(levelname)s - %(message)s", datefmt="%m/%d %I:%M:%S %p")


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler resilient to SMB / network-share file-handle issues.

    When the executable runs directly from a NAS path, the log file stream can
    go stale due to SMB oplock breaks, brief network interruptions, or
    concurrent access from other machines.  The stdlib handler crashes with
    ``OSError: [Errno 22] Invalid argument`` when it calls seek()/tell() on a
    stale handle during ``shouldRollover()``, or when the rename chain in
    ``doRollover()`` fails.

    This subclass:
    * catches ``OSError`` in ``emit()`` and re-opens the stream once,
    * wraps ``doRollover()`` so a failed rotation doesn't kill the logger.
    """

    def _reopen_stream(self):
        """Close the current stream (ignoring errors) and open a fresh one."""
        try:
            if self.stream:
                self.stream.close()
        except OSError:
            pass
        self.stream = self._open()

    def emit(self, record):
        try:
            super().emit(record)
        except OSError:
            self._reopen_stream()
            try:
                super().emit(record)
            except OSError:
                self.handleError(record)

    def doRollover(self):
        """Attempt rotation, but fall back to truncation if renames fail over SMB."""
        try:
            super().doRollover()
        except OSError:
            # Rotation rename chain failed — just reopen the file to keep logging
            self._reopen_stream()


class Tools:
    @staticmethod
    def is_running(process: str) -> bool:
        """Check if a process is running using tasklist."""
        if not process:
            # Empty name is a substring of every tasklist output -> false match.
            return False
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            output = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {process}"],
                startupinfo=startupinfo,
                stderr=subprocess.STDOUT,
            ).decode(errors="ignore")
            return process.lower() in output.lower()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    @staticmethod
    def kill(process: str) -> bool:
        """Find and kill all processes matching the given name using taskkill.

        ``/T`` also terminates child processes, so helpers spawned by the target
        (e.g. ASVExport.exe under ArkViewer.exe) get cleaned up too.
        """
        if not process:
            return False
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            subprocess.check_call(
                ["taskkill", "/F", "/T", "/IM", process],
                startupinfo=startupinfo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    @staticmethod
    def file_hash(path: Path | str, algo="sha256") -> str:
        """Compute the hash of a file."""
        try:
            h = hashlib.new(algo)
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except (IOError, FileNotFoundError):
            log.error(f"Could not read file for hashing: {path}")
            return ""

    @staticmethod
    def wait_until_file_lock_released(file_path: Path | str, timeout: int = 60) -> bool:
        """Wait until the file lock is released."""
        if not os.path.exists(file_path):
            return True
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
        "arkviewer": '"ArkViewer.exe", "Path/To/Source/File", "Path/To/Target/File", "ASVExport.exe"',
        "arkhandler": '"ArkHandler.exe", "Path/To/Source/File", "Path/To/Target/File"',
    }
