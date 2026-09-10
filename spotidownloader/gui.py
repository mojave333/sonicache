"""Desktop window: an HTML/CSS interface rendered by Edge WebView2 via pywebview.

web/app.js calls Api methods as window.pywebview.api.<name>() and polls for
download events. Downloads run one link at a time in a background thread.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import webview

from . import __version__
from .downloader import DEFAULT_OUTPUT_DIR, FORMATS, Downloader, Options

TITLE = "Spotidownloader"
WEB_DIR = Path(__file__).with_name("web")
SETTINGS_FILE = Path.home() / ".spotidownloader.json"
THEMES = ("system", "light", "dark")


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
                          settings["threads"], settings["dry_run"])
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
        width=960,
        height=820,
        min_size=(620, 600),
        background_color="#171311" if dark else "#FFFBF5",  # no white flash before CSS loads
        text_select=True,
    )
    webview.start(http_server=True)
