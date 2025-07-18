import logging
from pathlib import Path
from typing import Optional

import colorama
import psutil
from colorama import Back, Fore, Style


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
        running = [p.name() for p in psutil.process_iter()]
        return process in running

    @staticmethod
    def kill(process: str) -> bool:
        for p in psutil.process_iter():
            if p.name() == process:
                p.kill()
                return True
        return False

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


class Const:
    defaults = {
        "arkviewer": '"ArkViewer.exe", "Path/To/Source/File", "Path/To/Target/File"',
        "arkhandler": '"ArkHandler.exe", "Path/To/Source/File", "Path/To/Target/File"',
    }
