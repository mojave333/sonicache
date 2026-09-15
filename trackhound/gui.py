"""Desktop window: an HTML/CSS interface rendered by Edge WebView2 via pywebview.

web/app.js calls Api methods as window.pywebview.api.<name>() and polls for
download events. Downloads run one link at a time in a background thread.
"""

from __future__ import annotations

import base64
import ctypes
import datetime
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import webview

from . import __version__, logs
from .i18n import LANGUAGES, resolve, set_language, t
from .downloader import (DEFAULT_OUTPUT_DIR, FOLDER_NAMES, FORMATS, MARKER_NAME, TRACK_NAMES,
                         Downloader, Options, use_proxy)

TITLE = "Trackhound"
WEB_DIR = Path(__file__).with_name("web")
SETTINGS_FILE = Path.home() / ".trackhound.json"
HISTORY_LIMIT = 100  # releases remembered between runs
# Track states that mean "not finished": a job that ends while a track is in
# one of them was interrupted, and the saved card should say so.
UNFINISHED = {"waiting", "search", "download"}
REPO = "mojave333/trackhound"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
WEBVIEW2_SETUP_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
THEMES = ("system", "light", "dark")
# Browsers yt-dlp can read cookies from; "" means it takes none
COOKIE_BROWSERS = ("", "chrome", "edge", "firefox", "brave", "chromium", "opera", "vivaldi")
# Speeds offered in the settings, bytes per second; 0 is no limit
RATE_LIMITS = (0, 512_000, 1_048_576, 2_097_152, 5_242_880, 10_485_760)
_PROXY_RE = re.compile(r"^(?:https?|socks4|socks5h?)://[^\s/]+$", re.I)
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
        self._resume = threading.Event()
        self._resume.set()
        # Reentrant: download() calls _remember() while already holding this lock
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._history = _load_history()
        self._job_counter = max((entry.get("job") or 0 for entry in self._history), default=0)

    def init(self) -> dict:
        # Anything that was still running when the window closed is offered
        # again rather than silently lost.
        for entry in self._history:
            if entry.get("state") in ("queued", "running"):
                entry["state"] = "cancelled"
        settings = _load_settings()
        return {
            "version": __version__,
            "language": set_language(settings["language"]),  # "system" resolved to ru or en
            "settings": settings,
            "problems": Downloader(Options(DEFAULT_OUTPUT_DIR)).environment_problems(),
            "history": self._history,
        }

    def set_language(self, setting: str) -> str:
        """Applies the language and answers with the one actually chosen."""
        return set_language(setting if setting in LANGUAGES else "system")

    def forget_history(self) -> None:
        """Clearing the finished cards clears what is remembered about them."""
        with self._lock:
            self._history = [entry for entry in self._history
                             if entry.get("state") in ("queued", "running")]
            _save_history(self._history)

    def save_settings(self, settings: dict) -> None:
        settings = _normalize(settings)
        use_proxy(settings["proxy"])  # metadata requests start using it at once
        set_language(settings["language"])  # errors from now on speak it
        _save_settings(settings)

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
                          settings["threads"], settings["dry_run"], settings["cookies_browser"],
                          settings["track_name"], settings["folder_name"],
                          settings["rate_limit"], settings["proxy"])
        jobs = []
        with self._lock:
            for link in links:
                self._job_counter += 1
                jobs.append({"job": self._job_counter, "link": link})
                self._jobs.put((self._job_counter, link, options))
                self._remember({"job": self._job_counter, "link": link, "state": "queued",
                                "format": settings["format"], "dry_run": settings["dry_run"],
                                "time": time.time()})
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, daemon=True)
                self._worker.start()
        return jobs

    def pause(self, paused: bool) -> bool:
        """Pauses between tracks: what is downloading finishes, the rest waits."""
        if paused:
            self._resume.clear()
        else:
            self._resume.set()
        logs.log.info("пауза" if paused else "продолжаем")
        return paused

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            self._resume.set()  # a paused queue still has to be able to stop
            while not self._jobs.empty():
                job, _, _ = self._jobs.get_nowait()
                self._events.put({"type": "job", "job": job, "state": "cancelled"})

    def open_folder(self, path: str) -> bool:
        folder = Path(path).expanduser()
        if folder.is_file() and _reveal(folder):
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

    def delete(self, paths: list[str]) -> dict:
        """Send albums and tracks to the recycle bin; a wrong pick stays undoable."""
        failed = []
        for raw in paths:
            path = Path(raw).expanduser()
            try:
                _recycle(path)
            except OSError:
                failed.append(path.name)
        return {"deleted": len(paths) - len(failed), "failed": failed}

    def diagnostics(self) -> dict:
        """Where the log is and what a bug report should say."""
        downloader = Downloader(Options(DEFAULT_OUTPUT_DIR))
        tools = {"ffmpeg": downloader.ffmpeg or "",
                 **{name: runtime["path"] for name, runtime in downloader.js_runtimes.items()}}
        ytdlp = logs.package_version("yt_dlp")
        return {
            "log": str(logs.log_file()),
            "exists": logs.log_file().exists(),
            "report": logs.report(_load_settings(), downloader.environment_problems(), tools),
            "ytdlp": ytdlp,
            "ytdlp_age": _release_age(ytdlp),
        }

    def open_logs(self) -> bool:
        logs.log_dir().mkdir(parents=True, exist_ok=True)
        return self.open_folder(str(logs.log_file() if logs.log_file().exists() else logs.log_dir()))

    def copy(self, text: str) -> bool:
        return _copy_to_clipboard(text)

    def latest_release(self) -> dict | None:
        """The newest published version, or None when this one is current.

        Called from the window after startup; a failure here is never shown.
        """
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"Trackhound/{__version__}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        version = str(data.get("tag_name") or "").lstrip("vV")
        if not _newer(version, __version__):
            return None
        return {"version": version, "url": data.get("html_url") or RELEASES_PAGE}

    def open_url(self, url: str) -> bool:
        if not url.startswith("https://github.com/"):
            return False
        webbrowser.open(url)
        return True

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
        # The tracklist and how each track ended. Without it a card restored on
        # the next run has nothing to unfold and hides its arrow. Track events
        # are far too many to write the file on each one, so the states are kept
        # here and saved once, when the job is over.
        tracks: dict[str, dict] = {}

        def finished_tracks() -> list[dict]:
            return [dict(item, state="cancel") if item["state"] in UNFINISHED else item
                    for item in tracks.values()]

        def emit(**event) -> None:
            self._events.put({"job": job, **event})
            kind = event.get("type")
            if kind == "release":  # the card's title and tracklist, kept for next time
                tracks.clear()
                for item in event["tracks"]:
                    tracks[item["id"]] = {**item, "state": "waiting", "source": "", "text": ""}
                self._remember({"job": job, "link": link, "state": "running", "title": event["title"],
                                "artist": event["artist"], "kind": event["kind"], "year": event["year"],
                                "service": event["service"], "album": event["album"],
                                "cover": event["cover"], "folder": event["folder"],
                                "total": len(event["tracks"]), "tracks": list(tracks.values())})
            elif kind == "track":
                known = tracks.get(event.get("id"))
                if known is not None:
                    known["state"] = event.get("state", known["state"])
                    known["text"] = event.get("text", "")
                    if event.get("source"):
                        known["source"] = event["source"]

        emit(type="job", state="running")
        self._remember({"job": job, "state": "running"})
        downloader = Downloader(
            options,
            log=lambda message: None,
            progress=lambda done, total: emit(type="progress", done=done, total=total),
            events=lambda kind, data: emit(type=kind, **data),
            stop_event=self._stop,
            resume_event=self._resume,
        )
        logs.log.info("ссылка: %s", link)
        try:
            report = downloader.download_link(link)
        except Exception as e:  # shown on the card, the next link still runs
            logs.log.exception("ссылка не скачалась: %s", link)
            message = str(e) or type(e).__name__
            emit(type="job", state="error", message=message)
            self._remember({"job": job, "state": "error", "message": message,
                            "tracks": finished_tracks()})
            return
        state = "cancelled" if self._stop.is_set() else "done"
        counts = {"ok": len(report.ok), "skipped": len(report.skipped), "failed": len(report.failed)}
        emit(type="job", state=state, dry_run=options.dry_run, **counts)
        self._remember({"job": job, "state": state, "dry_run": options.dry_run,
                        "tracks": finished_tracks(), **counts})


    def _remember(self, entry: dict) -> None:
        """Adds to, or updates, what is known about a job, and saves it."""
        with self._lock:
            known = next((item for item in self._history if item.get("job") == entry.get("job")), None)
            if known is None:
                self._history.append(entry)
            else:
                known.update(entry)
            del self._history[:-HISTORY_LIMIT]
            _save_history(self._history)


def _history_file() -> Path:
    return logs.data_dir() / "history.json"


def _load_history() -> list[dict]:
    try:
        data = json.loads(_history_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def _save_history(history: list[dict]) -> None:
    try:
        _history_file().parent.mkdir(parents=True, exist_ok=True)
        _history_file().write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # a read-only profile costs the history, not the download


def _release_age(version: str) -> int:
    """Days since a yt-dlp release (its version is the date it was built).

    YouTube breaks yt-dlp every few weeks, so an old copy is the likeliest
    reason downloads stop working; -1 means the version said nothing.
    """
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})", version or "")
    if not m:
        return -1
    try:
        built = datetime.date(*(int(piece) for piece in m.groups()))
    except ValueError:
        return -1
    return max(0, (datetime.date.today() - built).days)


def _newer(candidate: str, current: str) -> bool:
    def parts(version: str) -> list[int]:
        return [int(piece) for piece in re.findall(r"\d+", version)] or [0]

    return parts(candidate) > parts(current)


class _FileOperation(ctypes.Structure):
    """SHFILEOPSTRUCTW: the shell call that knows about the recycle bin."""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


_FO_DELETE = 3
# Undoable, and quiet: the window asks for confirmation itself
_FOF_FLAGS = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI


def _reveal(path: Path) -> bool:
    """Opens the file manager with the file itself picked out."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{path}"')
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
            return True
        # Nautilus, Dolphin, Nemo and the rest answer this; the caller falls
        # back to opening the folder itself when they do not.
        subprocess.run(["dbus-send", "--session", "--print-reply",
                        "--dest=org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                        "org.freedesktop.FileManager1.ShowItems",
                        f"array:string:{path.as_uri()}", "string:"],
                       check=True, capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _recycle(path: Path) -> None:
    """Moves a file or folder to the desktop's trash, wherever that is.

    Nothing here deletes outright: a wrong click in the library has to stay
    undoable, so a desktop with no trash of its own raises instead.
    """
    if sys.platform == "win32":
        operation = _FileOperation(
            wFunc=_FO_DELETE,
            pFrom=f"{path.resolve()}\0\0",  # the shell wants a double-null terminated list
            fFlags=_FOF_FLAGS,
        )
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation)):
            raise OSError(t("не удалось удалить {path}", path=path))
        return

    if sys.platform == "darwin":
        script = f'tell application "Finder" to delete POSIX file "{path.resolve()}"'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode:
            raise OSError(t("не удалось удалить {path}: {error}",
                            path=path, error=result.stderr.strip()))
        return

    if shutil.which("gio"):
        result = subprocess.run(["gio", "trash", str(path.resolve())],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return
        logs.log.warning("gio trash не справился: %s", result.stderr.strip())
    _trash_by_hand(path)


def _trash_by_hand(path: Path) -> None:
    """The freedesktop.org trash, for a desktop without gio.

    A trashed file goes to ~/.local/share/Trash/files with a .trashinfo note
    beside it saying where it came from, which is what restores it.
    """
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
    files, info = root / "files", root / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)
    source = path.resolve()
    name = source.name
    for attempt in range(1, 1000):  # the trash may already hold this name
        if not (files / name).exists() and not (info / f"{name}.trashinfo").exists():
            break
        name = f"{source.stem}.{attempt}{source.suffix}"
    note = info / f"{name}.trashinfo"
    note.write_text(
        "[Trash Info]\n"
        f"Path={urllib.parse.quote(str(source))}\n"
        f"DeletionDate={datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n",
        encoding="utf-8")
    try:
        shutil.move(str(source), str(files / name))
    except OSError:
        note.unlink(missing_ok=True)
        raise


def _is_audio(path: Path) -> bool:
    # _part_ files are tracks still being downloaded
    return path.suffix.lower() in AUDIO_SUFFIXES and not path.name.startswith("_part_") and path.is_file()


def _library_item(path: Path, files: list[Path]) -> dict:
    album = path.is_dir()
    stats = [file.stat() for file in files]
    match = (_ALBUM_NAME if album else _TRACK_NAME).match(path.name if album else path.stem)
    marker = _read_marker(path) if album else {}
    return {
        # The link the album came from, when it was downloaded by this program
        "link": marker.get("link", ""),
        "expected": marker.get("tracks") or 0,
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


def _read_marker(folder: Path) -> dict:
    try:
        data = json.loads((folder / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalize(settings: dict) -> dict:
    try:
        threads = min(8, max(1, int(settings.get("threads", 3))))
    except (TypeError, ValueError):
        threads = 3
    try:  # older versions stored a boolean here
        sidebar = min(320, max(64, int(settings.get("sidebar", 64))))
    except (TypeError, ValueError):
        sidebar = 64
    try:
        rate_limit = int(settings.get("rate_limit") or 0)
    except (TypeError, ValueError):
        rate_limit = 0
    if rate_limit not in RATE_LIMITS:
        rate_limit = 0
    proxy = str(settings.get("proxy") or "").strip()
    if not _PROXY_RE.match(proxy):
        proxy = ""
    return {
        "folder": str(settings.get("folder") or DEFAULT_OUTPUT_DIR),
        "format": settings.get("format") if settings.get("format") in FORMATS else "m4a",
        "threads": threads,
        "theme": settings.get("theme") if settings.get("theme") in THEMES else "system",
        "dry_run": bool(settings.get("dry_run", False)),
        "cookies_browser": (settings.get("cookies_browser")
                            if settings.get("cookies_browser") in COOKIE_BROWSERS else ""),
        "rate_limit": rate_limit,
        "proxy": proxy,
        # The window offers Russian and English and nothing else, so the
        # language is settled here: "system" from an older settings file, or a
        # first run with nothing saved, becomes whichever of the two the
        # computer asks for.
        "language": (settings.get("language")
                     if settings.get("language") in ("ru", "en") else resolve("system")),
        "track_name": (settings.get("track_name")
                       if settings.get("track_name") in TRACK_NAMES else "auto"),
        "folder_name": (settings.get("folder_name")
                        if settings.get("folder_name") in FOLDER_NAMES else "flat"),
        # Width of the side panel in pixels. The window uses two of them, 64 for
        # the icon rail and 208 for the labelled column, and reads anything in
        # between — a file left by the build where the panel was dragged — as
        # whichever of the two it sits nearer.
        "sidebar": sidebar,
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


def _copy_to_clipboard(text: str) -> bool:
    """Windows keeps clipboard data after the program that put it there exits,
    which Tk on its own does not: hence the shell call."""
    if sys.platform == "win32":
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        buffer = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buffer)
        handle = kernel32.GlobalAlloc(0x2000, size)  # GMEM_MOVEABLE
        if not handle or not user32.OpenClipboard(None):
            return False
        try:
            ctypes.memmove(kernel32.GlobalLock(handle), buffer, size)
            kernel32.GlobalUnlock(handle)
            user32.EmptyClipboard()
            return bool(user32.SetClipboardData(13, handle))  # CF_UNICODETEXT
        finally:
            user32.CloseClipboard()

    # macOS and the Linux desktops keep what a command line tool pipes in
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.run(command, input=text.encode("utf-8"), check=True, timeout=5)
            return True
        except (OSError, subprocess.SubprocessError):
            continue

    root = None
    try:
        from tkinter import Tk

        root = Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        return True
    except Exception:  # a Python built without Tcl/Tk, or no display to talk to
        return False
    finally:
        if root is not None:
            root.destroy()


def _clipboard_text() -> str:
    """Tk owns a hidden root of its own here: the window itself is WebView2,
    which gives Python no clipboard of its own to ask.

    Everything is guarded, including the import and the root: a Python built
    without Tcl/Tk must leave the button quiet, not raise into the window,
    where the js_api call would come back as a rejected promise and lose the
    message about the clipboard being empty.
    """
    root = None
    try:
        from tkinter import Tk

        root = Tk()
        root.withdraw()  # created and hidden before it can ever be drawn
        return root.clipboard_get()
    except Exception:  # no Tcl/Tk, no display, empty, or not text
        return ""
    finally:
        if root is not None:
            root.destroy()


def _system_dark() -> bool:
    """Whether the desktop is in its dark theme; light when nothing says so."""
    if sys.platform == "darwin":
        # The key exists only while the dark theme is on
        return "dark" in _ask(["defaults", "read", "-g", "AppleInterfaceStyle"]).lower()
    if sys.platform != "win32":
        scheme = (_ask(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
                  or _ask(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"]))
        return "dark" in scheme.lower()
    import winreg

    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def _ask(command: list[str]) -> str:
    """Runs a small command and returns its output, or "" when it is not there."""
    if not shutil.which(command[0]):
        return ""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _webview2_installed() -> bool:
    """The window is drawn by Edge WebView2, which Windows 10 may not have."""
    if sys.platform != "win32":
        return True
    import winreg

    # The runtime registers itself per machine (32-bit view) or per user
    client = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    places = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{client}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{client}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{client}"),
    ]
    for root, path in places:
        try:
            with winreg.OpenKey(root, path) as key:
                if winreg.QueryValueEx(key, "pv")[0] not in ("", "0.0.0.0"):
                    return True
        except OSError:
            continue
    return False


def _offer_webview2() -> bool:
    """Asks to install the runtime and runs Microsoft's installer if allowed."""
    from tkinter import messagebox

    agreed = messagebox.askyesno(
        TITLE,
        t("Для окна программы нужен компонент Microsoft Edge WebView2, его нет в системе.\n\n"
          "Скачать и установить его сейчас? Установщик официальный, с сайта Microsoft."),
    )
    if not agreed:
        return False
    try:
        setup = Path(tempfile.gettempdir()) / "MicrosoftEdgeWebview2Setup.exe"
        urllib.request.urlretrieve(WEBVIEW2_SETUP_URL, setup)
        subprocess.run([str(setup)], check=True)
    except Exception as e:  # network, antivirus, cancelled elevation prompt
        messagebox.showerror(TITLE, t("Не удалось установить WebView2 ({error}).\n\nСкачайте его "
                                      "вручную: https://go.microsoft.com/fwlink/p/?LinkId=2124703",
                                      error=e))
        return False
    return _webview2_installed()


def main() -> None:
    logs.setup()
    settings = _load_settings()
    set_language(settings["language"])
    use_proxy(settings["proxy"])
    if not _webview2_installed() and not _offer_webview2():
        return
    api = Api()
    theme = _load_settings()["theme"]
    dark = theme == "dark" or (theme == "system" and _system_dark())
    api._window = webview.create_window(
        TITLE,
        url=str(WEB_DIR / "index.html"),
        js_api=api,
        width=1160,
        height=800,  # the size the window returns to when it is un-maximized
        min_size=(720, 520),
        maximized=True,
        background_color="#23212C" if dark else "#FDFDF6",  # matches --bg, so no flash before CSS loads
        text_select=True,
    )
    webview.start(http_server=True)
