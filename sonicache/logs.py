"""A rolling log file and the report a person can paste into a bug report,
and the small JSON files the program keeps beside it.

The window swallows the detail of a failed download on purpose — a person
wants "не найдено", not a yt-dlp traceback — so the detail goes here instead.
Nothing is ever sent anywhere: the file sits next to the settings and is only
opened when somebody asks for it.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .engine.logs import log
from .i18n import t

LOG_NAME = "sonicache.log"
_MAX_BYTES = 1_000_000
_BACKUPS = 2
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# What the program was called before 3.0. Its folder is still looked for, once,
# so that an update does not start from nothing (adopt_former_data)
FORMER_NAME = "Trackhound"


def data_dir() -> Path:
    """Where the program keeps what it writes for itself.

    Each desktop has its own place for this: %LOCALAPPDATA% on Windows,
    ~/Library/Application Support on macOS, $XDG_DATA_HOME on Linux. The
    settings file stays in the home folder, where it has always been.
    """
    return _folder_of("Sonicache")


def _folder_of(name: str) -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return root / name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / name.lower()


def adopt_former_data() -> bool:
    """Brings over what the program kept under its former name:
    playlists, listens, the queue, what is watched. True when it did.

    Once, on the first start under the new name, and as a copy: an older
    version still installed beside this one keeps its own. The copy is made
    under another name and renamed when whole, so a failure halfway leaves
    nothing that would pass for the folder next time. The old log stays behind.
    """
    new, old = data_dir(), _folder_of(FORMER_NAME)
    if new.exists() or not old.is_dir():
        return False
    spare = new.with_name(f"{new.name}.{os.getpid()}.tmp")
    try:
        shutil.copytree(old, spare, ignore=shutil.ignore_patterns("logs"))
        os.replace(spare, new)
    except OSError:
        shutil.rmtree(spare, ignore_errors=True)
        return False
    return True


def read_json(path: Path, kind: type = dict):
    """One of the program's own files, or an empty dict (or list) when it is
    missing, unreadable or holds something else."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return kind()
    return data if isinstance(data, kind) else kind()


def write_json(path: Path, data, indent: int | None = None) -> None:
    """Writes one of them whole or not at all: a crash midway leaves the old
    file, and a read-only profile costs the file, not the program."""
    spare = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        spare.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
        os.replace(spare, path)
    except OSError as e:
        log.warning("не записал %s: %s", path.name, e)


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
    adopted = adopt_former_data()  # before the folder is made for the log, or it would look adopted already
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_file(), maxBytes=_MAX_BYTES, backupCount=_BACKUPS,
                                      encoding="utf-8")
    except OSError:
        return None  # a read-only or missing profile must not stop the program
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(_FORMAT))
    log.addHandler(handler)
    log.info("— запуск: Sonicache %s, %s, Python %s —", __version__, platform.platform(),
             platform.python_version())
    if adopted:
        log.info("данные прежней версии скопированы из %s", _folder_of(FORMER_NAME))
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
        f"Sonicache {__version__}",
        f"{platform.platform()}, Python {platform.python_version()}, "
        f"{t('сборка') if getattr(sys, 'frozen', False) else t('из исходников')}",
        f"yt-dlp {package_version('yt_dlp')}, ytmusicapi {package_version('ytmusicapi')}",
    ]
    for name, path in (tools or {}).items():
        parts.append(f"{name}: {path or t('не найден')}")
    if settings:
        parts.append(t("настройки: формат {format}, потоков {threads}, cookies: {cookies}",
                       format=settings.get("format"), threads=settings.get("threads"),
                       cookies=settings.get("cookies_browser") or t("не используются")))
        parts.append(t("папка: {folder}", folder=settings.get("folder")))
    for problem in problems or []:
        parts.append(t("проблема: {problem}", problem=problem))
    recent = tail()
    if recent:
        parts.append(t("\n--- {path} (последние {lines} строк) ---",
                       path=log_file(), lines=len(recent)))
        parts.extend(recent)
    return "\n".join(parts)


def package_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        module = sys.modules.get(name)
        return getattr(module, "__version__", t("не установлен"))

