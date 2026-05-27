import ctypes
import hashlib
import logging
import os
import subprocess
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter, sleep

log = logging.getLogger("autodeploy.utils")

IS_WINDOWS = os.name == "nt"


def disable_quickedit() -> None:
    """Turn off QuickEdit on the Windows console.

    QuickEdit pauses every stdout write while a user click holds the console
    in selection mode -- that pause blocks the polling loop and freezes
    deployments until the selection is cleared. This is the most likely
    "it silently stopped" failure for a minimized console. No-op off Windows.
    """
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        std_input_handle = ctypes.c_ulong(-10)
        enable_quick_edit = 0x0040
        enable_extended_flags = 0x0080
        handle = kernel32.GetStdHandle(std_input_handle)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        mode.value &= ~enable_quick_edit
        mode.value |= enable_extended_flags
        kernel32.SetConsoleMode(handle, mode)
    except (OSError, AttributeError) as exc:
        log.debug("disable_quickedit failed: %s", exc)


def enable_console_vt() -> None:
    """Enable ANSI VT escape processing on the Windows console.

    Win10+ supports VT but the flag is off by default for fresh consoles
    (PyInstaller exes); without it the color codes render as literal text.
    No-op off Windows.
    """
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        enable_vt = 0x0004
        enable_processed = 0x0001
        for handle_id in (ctypes.c_ulong(-11), ctypes.c_ulong(-12)):  # stdout, stderr
            h = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if not kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                continue
            kernel32.SetConsoleMode(h, mode.value | enable_vt | enable_processed)
    except (OSError, AttributeError) as exc:
        log.debug("enable_console_vt failed: %s", exc)


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
    def tail_signature(
        path: Path | str, tail_bytes: int = 131072, algo: str = "sha256"
    ) -> tuple[int, str] | None:
        """Cheap change signature: ``(size, hash of the last tail_bytes)``.

        Avoids streaming the whole file over the network on every poll. For a
        PyInstaller one-file exe the bootloader stub at the head is identical
        across rebuilds, while the appended CArchive (PYZ + data + TOC + the
        24-byte MEI cookie) lives at the tail -- so any rebuild changes the tail
        and/or the size. mtime is intentionally not consulted (unreliable over
        SMB). Returns ``None`` if the file can't be read.
        """
        try:
            size = os.path.getsize(path)
            h = hashlib.new(algo)
            with open(path, "rb") as f:
                if size > tail_bytes:
                    f.seek(size - tail_bytes)
                h.update(f.read())
            return (size, h.hexdigest())
        except (IOError, OSError):
            return None

    @staticmethod
    def wait_until_stable(
        path: Path | str,
        timeout: float = 60.0,
        poll: float = 0.5,
        stable_reads: int = 3,
    ) -> bool:
        """Wait until a file has finished being written.

        Returns ``True`` once the size is unchanged across ``stable_reads``
        consecutive polls AND the file opens for reading. The writer is usually
        on another machine over SMB, where its lock state isn't visible to us --
        size stability is the reliable signal that the write has stopped.
        Returns ``False`` on timeout (caller should skip and retry next cycle).
        """
        start = perf_counter()
        last_size = -1
        streak = 0
        while True:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            if size >= 0 and size == last_size:
                streak += 1
                if streak >= stable_reads:
                    try:
                        with open(path, "rb"):
                            return True
                    except (IOError, OSError):
                        streak = 0  # still locked by the writer; keep waiting
            else:
                streak = 0
                last_size = size
            if perf_counter() - start > timeout:
                log.error(f"Timeout waiting for {path} to stop changing")
                return False
            sleep(poll)

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
