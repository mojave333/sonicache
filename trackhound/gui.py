"""Desktop window: an HTML/CSS interface rendered by Edge WebView2 via pywebview.

web/app.js calls Api methods as window.pywebview.api.<name>() and polls for
download events. Downloads run one link at a time in a background thread.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

import webview

from . import __version__
from .downloader import DEFAULT_OUTPUT_DIR, FORMATS, Downloader, Options

TITLE = "Trackhound"
WEB_DIR = Path(__file__).with_name("web")
SETTINGS_FILE = Path.home() / ".trackhound.json"
THEMES = ("system", "light", "dark")
# Browsers yt-dlp can read cookies from; "" means it takes none
COOKIE_BROWSERS = ("", "chrome", "edge", "firefox", "brave", "chromium", "opera", "vivaldi")
AUDIO_SUFFIXES = {f".{name}" for name in FORMATS}
# Album folders are named by the downloader as "Artist - Album (Year)", single tracks as "Artist - Title"
_ALBUM_NAME = re.compile(r"^(?P<artist>.+?) - (?P<title>.+?)(?: \((?P<year>\d{4})\))?$")
_TRACK_NAME = re.compile(r"^(?P<artist>.+?) - (?P<title>.+)$")


class Api:
    def __init__(self):
        self._window: webview.Window | None = None
        self._events: queue.Queue[dict] = queue.Queue()
        self._jobs: queue.Queue[tuple[int, str, Options]] = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._job_counter = 0

    def init(self) -> dict:
        return {
            "version": __version__,
            "settings": _load_settings(),
            "problems": Downloader(Options(DEFAULT_OUTPUT_DIR)).environment_problems(),
        }

    def save_settings(self, settings: dict) -> None:
        _save_settings(_normalize(settings))

    def choose_folder(self, current: str) -> str | None:
        start = current if current and Path(current).is_dir() else str(Path.home())
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        return os.path.normpath(result[0]) if result else None

    def paste(self) -> str:
        return _clipboard_text()

    def download(self, links: list[str], settings: dict) -> list[dict]:
        settings = _normalize(settings)
        _save_settings(settings)
        options = Options(Path(settings["folder"]).expanduser(), settings["format"],
                          settings["threads"], settings["dry_run"], settings["cookies_browser"])
        jobs = []
        with self._lock:
            for link in links:
                self._job_counter += 1
                jobs.append({"job": self._job_counter, "link": link})
                self._jobs.put((self._job_counter, link, options))
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, daemon=True)
                self._worker.start()
        return jobs

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            while not self._jobs.empty():
                job, _, _ = self._jobs.get_nowait()
                self._events.put({"type": "job", "job": job, "state": "cancelled"})

    def open_folder(self, path: str) -> bool:
        folder = Path(path).expanduser()
        if folder.is_file() and sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{folder}"')
            return True
        while not folder.is_dir() and folder.parent != folder:
            folder = folder.parent  # a dry run never creates the album folder
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
        except OSError:
            return False
        return True

    def library(self, folder: str) -> list[dict]:
        """Album folders and single tracks in the music folder, newest first."""
        try:
            children = list(Path(folder).expanduser().iterdir())
        except OSError:
            return []
        items = []
        for path in children:
            try:
                if path.is_dir():
                    files = [file for file in path.iterdir() if _is_audio(file)]
                    if files:
                        items.append(_library_item(path, files))
                elif _is_audio(path):
                    items.append(_library_item(path, [path]))
            except OSError:
                continue  # removed or locked while scanning
        items.sort(key=lambda item: item["modified"], reverse=True)
        return items

    def cover(self, folder: str) -> str | None:
        try:
            data = (Path(folder) / "cover.jpg").read_bytes()
        except OSError:
            return None
        mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def poll(self) -> list[dict]:
        events = []
        while len(events) < 500:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def _work(self) -> None:
        while True:
            with self._lock:
                if self._jobs.empty():
                    self._worker = None
                    return
                job, link, options = self._jobs.get_nowait()
                self._stop.clear()  # a stop cancels only what was started or queued before it
            self._run(job, link, options)

    def _run(self, job: int, link: str, options: Options) -> None:
        def emit(**event) -> None:
            self._events.put({"job": job, **event})

        emit(type="job", state="running")
        downloader = Downloader(
            options,
            log=lambda message: None,
            progress=lambda done, total: emit(type="progress", done=done, total=total),
            events=lambda kind, data: emit(type=kind, **data),
            stop_event=self._stop,
        )
        try:
            report = downloader.download_link(link)
        except Exception as e:  # shown on the card, the next link still runs
            emit(type="job", state="error", message=str(e) or type(e).__name__)
            return
        emit(type="job", state="cancelled" if self._stop.is_set() else "done",
             ok=len(report.ok), skipped=len(report.skipped), failed=len(report.failed),
             dry_run=options.dry_run)


def _is_audio(path: Path) -> bool:
    # _part_ files are tracks still being downloaded
    return path.suffix.lower() in AUDIO_SUFFIXES and not path.name.startswith("_part_") and path.is_file()


def _library_item(path: Path, files: list[Path]) -> dict:
    album = path.is_dir()
    stats = [file.stat() for file in files]
    match = (_ALBUM_NAME if album else _TRACK_NAME).match(path.name if album else path.stem)
    return {
        "album": album,
        "title": match["title"] if match else (path.name if album else path.stem),
        "artist": match["artist"] if match else "",
        "year": (match["year"] or "") if match and album else "",
        "path": str(path),
        "tracks": len(files),
        "size": sum(stat.st_size for stat in stats),
        "modified": max(stat.st_mtime for stat in stats),
        "cover": album and (path / "cover.jpg").is_file(),
    }


def _normalize(settings: dict) -> dict:
    try:
        threads = min(8, max(1, int(settings.get("threads", 3))))
    except (TypeError, ValueError):
        threads = 3
    return {
        "folder": str(settings.get("folder") or DEFAULT_OUTPUT_DIR),
        "format": settings.get("format") if settings.get("format") in FORMATS else "m4a",
        "threads": threads,
        "theme": settings.get("theme") if settings.get("theme") in THEMES else "system",
        "dry_run": bool(settings.get("dry_run", False)),
        "cookies_browser": (settings.get("cookies_browser")
                            if settings.get("cookies_browser") in COOKIE_BROWSERS else ""),
        "sidebar": bool(settings.get("sidebar", False)),  # the side panel is expanded
    }


def _load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return _normalize(data if isinstance(data, dict) else {})


def _save_settings(settings: dict) -> None:
    # "check only" is not remembered: forgetting it on would silently skip downloads
    data = {key: value for key, value in settings.items() if key != "dry_run"}
    try:
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _clipboard_text() -> str:
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    cf_unicodetext = 13

    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(cf_unicodetext)
        pointer = kernel32.GlobalLock(handle) if handle else None
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _system_dark() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def main() -> None:
    api = Api()
    theme = _load_settings()["theme"]
    dark = theme == "dark" or (theme == "system" and _system_dark())
    api._window = webview.create_window(
        TITLE,
        url=str(WEB_DIR / "index.html"),
        js_api=api,
        width=1160,
        height=800,
        min_size=(720, 520),
        background_color="#151210" if dark else "#FBFAF8",  # no white flash before CSS loads
        text_select=True,
    )
    webview.start(http_server=True)
