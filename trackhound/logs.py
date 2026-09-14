"""A rolling log file and the report a person can paste into a bug report.

The window swallows the detail of a failed download on purpose — a person
wants "не найдено", not a yt-dlp traceback — so the detail goes here instead.
Nothing is ever sent anywhere: the file sits next to the settings and is only
opened when somebody asks for it.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "trackhound.log"
_MAX_BYTES = 1_000_000
_BACKUPS = 2
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

log = logging.getLogger("trackhound")


def data_dir() -> Path:
    """Where the program keeps what it writes for itself.

    %LOCALAPPDATA%\\Trackhound on Windows, ~/.trackhound elsewhere. Settings
    stay in the home folder, where they have always been.
    """
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return root / "Trackhound"
    return Path.home() / ".trackhound"


def log_dir() -> Path:
    return data_dir() / "logs"


def log_file() -> Path:
    return log_dir() / LOG_NAME


def setup(console: bool = False) -> Path | None:
    """Starts writing the log file; returns its path, or None if it cannot be written."""
    log.setLevel(logging.DEBUG)
    log.propagate = False
    if any(isinstance(handler, RotatingFileHandler) for handler in log.handlers):
        return log_file()
    if console:
        stream = logging.StreamHandler()
        stream.setLevel(logging.WARNING)
        stream.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        log.addHandler(stream)
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_file(), maxBytes=_MAX_BYTES, backupCount=_BACKUPS,
                                      encoding="utf-8")
    except OSError:
        return None  # a read-only or missing profile must not stop the program
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(_FORMAT))
    log.addHandler(handler)
    log.info("— запуск: Trackhound %s, %s, Python %s —", _version(), platform.platform(),
             platform.python_version())
    return log_file()


def tail(lines: int = 60) -> list[str]:
    """The end of the log, for the report; an unreadable file is simply empty."""
    try:
        text = log_file().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def report(settings: dict | None = None, problems: list[str] | None = None,
           tools: dict[str, str] | None = None) -> str:
    """Everything worth pasting into an issue, as plain text."""
    parts = [
        f"Trackhound {_version()}",
        f"{platform.platform()}, Python {platform.python_version()}, "
        f"{'сборка' if getattr(sys, 'frozen', False) else 'из исходников'}",
        f"yt-dlp {package_version('yt_dlp')}, ytmusicapi {package_version('ytmusicapi')}",
    ]
    for name, path in (tools or {}).items():
        parts.append(f"{name}: {path or 'не найден'}")
    if settings:
        parts.append("настройки: формат {format}, потоков {threads}, cookies: {cookies}".format(
            format=settings.get("format"), threads=settings.get("threads"),
            cookies=settings.get("cookies_browser") or "не используются"))
        parts.append(f"папка: {settings.get('folder')}")
    for problem in problems or []:
        parts.append(f"проблема: {problem}")
    recent = tail()
    if recent:
        parts.append(f"\n--- {log_file()} (последние {len(recent)} строк) ---")
        parts.extend(recent)
    return "\n".join(parts)


def package_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        module = sys.modules.get(name)
        return getattr(module, "__version__", "не установлен")


def _version() -> str:
    from . import __version__

    return __version__


class YtdlpLogger:
    """Hands yt-dlp's chatter to the log file instead of the console.

    yt-dlp talks in paragraphs and in English; the window shows its own short
    message and the whole text lands here, where it can be read afterwards.
    """

    def __init__(self, name: str = "yt-dlp"):
        self._log = log.getChild(name)

    def debug(self, message: str) -> None:
        self._log.debug("%s", message)

    def info(self, message: str) -> None:
        self._log.debug("%s", message)

    def warning(self, message: str) -> None:
        self._log.warning("%s", message)

    def error(self, message: str) -> None:
        self._log.error("%s", message)
