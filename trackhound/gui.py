"""Desktop window: an HTML/CSS interface rendered by Edge WebView2 via pywebview.

web/app.js calls Api methods as window.pywebview.api.<name>() and polls for
download events. Downloads run one link at a time in a background thread.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import datetime
import json
import logging
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import webview
from mutagen import File as MutagenFile
from mutagen.id3 import Frames
from mutagen.mp3 import MP3
from mutagen.flac import Picture

from . import __version__, logs, relay_for, thumbbar, tray, watch
from .i18n import LANGUAGES, resolve, set_language, t
from .engine import batch, catalog, folders, loudness, lyrics, network, sources, use_relay
from .engine.models import Album, SourceError, Track
from .engine.downloader import (DEFAULT_OUTPUT_DIR, FOLDER_NAMES, FORMATS, MARKER_NAME, TRACK_NAMES,
                                Downloader, Options, read_tags, use_proxy)
from .engine.matcher import Match
from .engine.tidy import tidy as tidy_up

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
# A Spotify relay's address, or "off"; empty is the program's own (SPOTIFY_RELAY)
_RELAY_RE = re.compile(r"^https?://[^\s?#]+(?:\?\S*)?$", re.I)
# How the taskbar button shows the downloads, as ITaskbarList3 numbers them:
# nothing, a sweep while no count is known yet, green, red, yellow
TASKBAR_STATES = {"none": 0, "indeterminate": 1, "normal": 2, "error": 4, "paused": 8}
AUDIO_SUFFIXES = {f".{name}" for name in FORMATS}
# What a profile remembers: where the music goes and in what shape. The rest of
# the settings — theme, language, proxy — are about the program, not the music,
# and stay the same whichever profile is picked.
PROFILE_KEYS = ("folder", "format", "track_name", "folder_name")
PROFILE_LIMIT = 12
UPDATE_HOST = "github.com"
# The pages a candidate can be listened to on before it is chosen
LISTEN_HOSTS = {"music.youtube.com", "www.youtube.com", "youtube.com", "youtu.be", "soundcloud.com",
                "m.soundcloud.com"}
MATCH_FIELDS = ("source", "url", "page_url", "title", "artists", "duration", "score")
LIST_BYTES = 2_000_000  # a list of links this long is already hundreds of thousands of lines
# Album folders are named by the downloader as "Artist - Album (Year)", single tracks as "Artist - Title"
_ALBUM_NAME = re.compile(r"^(?P<artist>.+?) - (?P<title>.+?)(?: \((?P<year>\d{4})\))?$")
_TRACK_NAME = re.compile(r"^(?P<artist>.+?) - (?P<title>.+)$")
# An album inside an artist's folder is named without the artist: "Discovery (2001)"
_ALBUM_UNDER_ARTIST = re.compile(r"^(?P<title>.+?)(?: \((?P<year>\d{4})\))?$")
LIBRARY_VIEWS = ("grid", "list")
ARTISTS_FILE = "artists.json"  # names already looked up on Deezer, beside the history
# Library entries tidied up, with their files as they were after it: as long
# as they stay so, the Fill in tags button does not offer them again, even
# where a genre or lyrics could not be found
TIDIED_FILE = "tidied.json"
# The tags read from the library's files, kept between runs so that opening
# the library reads again only the files changed since
LIBRARY_CACHE_FILE = "library-cache.json"
ARTIST_RETRY = 7 * 86400  # an artist Deezer did not know is asked about again after a week
# Tags read from a file, kept while its size and time stay the same: the Tracks
# tab reads every file in the library, and the second visit should be instant
_TAG_CACHE: dict[tuple[str, int, int], dict] = {}
_ARTIST_LOCK = threading.Lock()
_ARTIST_QUERIES = threading.BoundedSemaphore(4)  # Deezer allows 50 requests in 5 seconds


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
        self._updating = False
        self._watched = watch.load()
        self._watcher: threading.Thread | None = None
        self._covers = _CoverServer()
        self._tidying: threading.Event | None = None  # set to stop the tidy-up that runs
        self._tray: tray.Tray | None = None
        self._thumbbar: thumbbar.ThumbBar | None = None  # the player's buttons under the taskbar picture
        self._hidden = False  # in the tray, the window closed
        self._focused = True  # the window is the one being used; said by the page
        self._quitting = False  # the window closes for good, not into the tray
        self._told_hidden = False  # the first time in the tray is explained once
        self._finished: list[dict] = []  # releases done since the queue last ran empty, for the notice

    def init(self) -> dict:
        # The page is drawn and its script reached Python: the build check
        # waits for this line, and it tells a bug report the window got this far
        logs.log.info("окно: интерфейс загружен")
        # Anything that was still running when the window closed is offered
        # again rather than silently lost.
        for entry in self._history:
            if entry.get("state") in ("queued", "running"):
                entry["state"] = "cancelled"
        settings = _load_settings()
        with self._lock:
            if self._watcher is None:
                self._watcher = threading.Thread(target=self._watch_loop, daemon=True)
                self._watcher.start()
        return {
            "version": __version__,
            "first_run": not SETTINGS_FILE.exists(),  # the welcome screen, once
            "tray": tray.SUPPORTED,  # whether the window can go on in the notification area
            "language": set_language(settings["language"]),  # "system" resolved to ru or en
            "settings": settings,
            "problems": Downloader(Options(DEFAULT_OUTPUT_DIR)).environment_problems(),
            "history": self._history,
            "watched": self.watched(),
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
        use_relay(relay_for(settings["relay"]))
        set_language(settings["language"])  # errors from now on speak it
        _save_settings(settings)
        self._sync_tray(settings)

    def focus(self, focused: bool) -> None:
        """The page says whether the window is the one being used."""
        self._focused = bool(focused)

    # The notification area: the window goes on there when closed, and the
    # notice about finished downloads comes from there

    def _sync_tray(self, settings: dict) -> None:
        """The icon is there while going on in the background or notices are wanted."""
        if not tray.SUPPORTED:
            return
        wanted = settings["tray"] or settings["notify"] or self._hidden
        if wanted and self._tray is None:
            self._tray = tray.Tray(WEB_DIR / "icon.ico", TITLE, _tray_words, on_open=self.show_window,
                                   on_check=lambda: self.check_watched(force=True), on_quit=self.quit)
        if wanted and self._tray is not None and not self._tray.shown:
            self._tray.show()
        elif not wanted and self._tray is not None:
            self._tray.close()

    def _keeps_running(self) -> bool:
        """Whether closing the window should only hide it: something is still
        downloading, or watched playlists wait for their next check."""
        if not (tray.SUPPORTED and _load_settings()["tray"] and self._tray is not None and self._tray.shown):
            return False
        with self._lock:
            return self._worker is not None or bool(self._watched) or self._tidying is not None

    def _on_closing(self):
        """The window's close button. Returns False to keep the program going
        in the tray; anything else lets the window close and the program end."""
        if self._quitting or not self._keeps_running():
            self._quitting = True
            if self._tray is not None:
                self._tray.close()
            if self._thumbbar is not None:
                self._thumbbar.close()
            return None
        threading.Thread(target=self._hide, daemon=True).start()
        return False

    def _hide(self) -> None:
        if self._window is None:
            return
        self._window.hide()
        self._hidden = True
        logs.log.info("окно: спрятано в трей")
        if not self._told_hidden and self._tray is not None:
            self._told_hidden = True
            self._tray.notify(t("Trackhound работает в фоне"),
                              t("Загрузки и слежение продолжаются. Открыть окно или выйти — через значок у часов"))

    def show_window(self) -> None:
        if self._window is None:
            return
        self._window.show()
        self._window.restore()  # a minimized window comes back to its size
        self._hidden = False

    def quit(self) -> None:
        """Ends the program, from the tray's menu."""
        self._quitting = True
        self._close_window()

    def _announce(self) -> None:
        """Tells how the downloads went, once the queue has run empty, when
        nobody is looking at the window: it is in the tray, minimized or behind others."""
        with self._lock:
            finished, self._finished = self._finished, []
        if not finished or (self._focused and not self._hidden) or not _load_settings()["notify"]:
            return
        title, text = _notice(finished)
        if self._tray is not None and self._tray.shown:
            self._tray.notify(title, text)
        elif not tray.SUPPORTED:
            tray.notify_elsewhere(title, text)

    def choose_folder(self, current: str) -> str | None:
        start = current if current and Path(current).is_dir() else str(Path.home())
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        return os.path.normpath(result[0]) if result else None

    def paste(self) -> str:
        return _clipboard_text()

    def pick_list(self) -> dict | None:
        """Asks for a text or CSV file of links and says what is in it."""
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=str(Path.home()),
            file_types=(t("Списки ссылок (*.txt;*.csv)"), t("Все файлы (*.*)")))
        if not result:
            return None
        path = Path(result[0])
        try:
            if path.stat().st_size > LIST_BYTES:
                return {"name": path.name, "entries": [], "skipped": 0,
                        "error": t("Файл слишком большой для списка ссылок")}
            entries, skipped = batch.read(path.read_bytes(), path.name)
        except OSError as e:
            return {"name": path.name, "entries": [], "skipped": 0, "error": str(e)}
        return {"name": path.name, "entries": entries, "skipped": skipped}

    def parse_list(self, text: str, name: str = "") -> dict:
        """The same, for a file dropped on the window, which arrives as text."""
        entries, skipped = batch.parse(str(text or "")[:LIST_BYTES], str(name or ""))
        return {"name": name, "entries": entries, "skipped": skipped}

    def download(self, links: list[str], settings: dict) -> list[dict]:
        settings = _normalize(settings)
        _save_settings(settings)
        return [self._enqueue(link, settings) for link in _expand_artists(links)]

    def artist(self, link: str) -> dict:
        """An artist's page for Search: picture, name and every release, newest first."""
        try:
            page = catalog.artist(link)
        except SourceError as e:
            return {"error": str(e)}
        return {**page, "watched": any(entry["link"] == page["link"] for entry in self._watched)}

    def watch_artist(self, link: str) -> list[dict]:
        """Starts watching an artist for new releases, into the music folder.
        What the artist has today is remembered, so none of it downloads."""
        try:
            page = catalog.artist(link)
        except SourceError as e:
            logs.log.warning("слежение: исполнитель %s не открылся: %s", link, e)
            return self.watched()
        with self._lock:
            self._watched = watch.add(self._watched, page["link"], page["name"], page["service"],
                                      _load_settings(), known=[release["link"] for release in page["releases"]])
            watch.save(self._watched)
        return self.watched()

    def download_choices(self, link: str, settings: dict, choices: dict) -> dict | None:
        """Downloads the tracks of a release a person chose a match for, each
        from the match chosen, into the release's folder, as a card of its own."""
        matches = {}
        for track_id, candidate in (choices or {}).items():
            try:
                match = Match(**{key: candidate[key] for key in MATCH_FIELDS})
            except (KeyError, TypeError):
                continue
            if match.url.startswith("https://"):  # only what a search of ours put forward
                matches[str(track_id)] = match
        if not matches:
            return None
        return self._enqueue(link, {**_normalize(settings), "dry_run": False}, matches)

    def open_page(self, url: str) -> bool:
        """A candidate's own page, to listen to it before choosing."""
        if urllib.parse.urlsplit(str(url)).hostname not in LISTEN_HOSTS:
            return False
        webbrowser.open(url)
        return True

    def _enqueue(self, link: str, settings: dict, choices: dict | None = None) -> dict:
        options = Options(Path(settings["folder"]).expanduser(), settings["format"],
                          settings["threads"], settings["dry_run"], settings["cookies_browser"],
                          settings["track_name"], settings["folder_name"],
                          settings["rate_limit"], settings["proxy"], settings["replaygain"],
                          ask=settings.get("ask_doubtful", True), choices=choices or {},
                          lyrics=settings.get("lyrics", True))
        with self._lock:
            self._job_counter += 1
            job = self._job_counter
            self._jobs.put((job, link, options))
            # The music folder and the naming rules are kept so the release can be
            # watched later and its new tracks sent to the same place. Not under
            # "folder": the release event fills that with the album's own folder,
            # and a check started from there nests the album inside itself.
            self._remember({"job": job, "link": link, "state": "queued",
                            "format": settings["format"], "dry_run": settings["dry_run"],
                            "music_folder": settings["folder"],
                            "track_name": settings["track_name"],
                            "folder_name": settings["folder_name"], "time": time.time()})
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, daemon=True)
                self._worker.start()
        return {"job": job, "link": link}

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

    def progress(self, state: str, ratio: float = 0.0) -> None:
        """How far the downloads have come, where it is seen with the window
        minimised: in the title, and on Windows on the taskbar button too."""
        window = self._window
        if window is None or state not in TASKBAR_STATES:
            return
        percent = min(100, max(0, int(ratio * 100)))
        counted = state in ("normal", "error", "paused")
        window.set_title(f"{percent}% · {TITLE}" if counted else TITLE)
        if sys.platform != "win32":
            return
        try:
            _taskbar_progress(window.native.Handle.ToInt64(), TASKBAR_STATES[state], percent)
        except Exception as e:  # only a picture of the progress: never worth a failed call
            logs.log.debug("панель задач: %s", e)

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

    def library(self, roots: str | list[str]) -> list[dict]:
        """Album folders and single tracks in the music folder and the other
        folders the library reads, newest first."""
        roots = [roots] if isinstance(roots, str) else [str(root) for root in roots or []]
        # A folder added on its own is read first, so its albums are named as
        # its own and not as the insides of a folder that happens to hold it
        roots.sort(key=lambda root: len(Path(root).expanduser().parts), reverse=True)
        _load_library_cache()
        items, seen = [], set()
        with folders.scanning():
            for root in roots:
                for item in _library_items(Path(root).expanduser()):
                    if item["path"] not in seen:  # one folder inside another is read once
                        seen.add(item["path"])
                        items.append(item)
        items.sort(key=lambda item: item["modified"], reverse=True)
        _save_library_cache(_roots_key(roots), items)
        return items

    def library_cached(self, roots: str | list[str]) -> list[dict]:
        """What the library held when it was last read, shown at once while
        it is read again; nothing for folders not read before."""
        roots = [roots] if isinstance(roots, str) else [str(root) for root in roots or []]
        _load_library_cache()
        return _LIBRARY_CACHE["items"].get(_roots_key(roots), [])

    def delete(self, paths: list[str]) -> dict:
        """Send albums and tracks to the recycle bin; a wrong pick stays undoable."""
        failed, gone = [], set()
        for raw in paths:
            path = Path(raw).expanduser()
            try:
                _recycle(path)
            except OSError:
                failed.append(path.name)
                continue
            gone.add(str(raw))
            # A track's synced lyrics go with it; an album's are inside its folder already
            lrc = path.with_suffix(".lrc")
            if path.suffix.lower() in AUDIO_SUFFIXES and lrc.is_file():
                try:
                    _recycle(lrc)
                except OSError:
                    pass
        _forget_in_library_cache(gone)
        return {"deleted": len(paths) - len(failed), "failed": failed}

    def search(self, query: str) -> dict:
        """Albums, tracks and artists from the catalogues, for the Search view."""
        try:
            return catalog.search(query)
        except SourceError as e:
            return {"error": str(e), "service": "", "albums": [], "tracks": [], "artists": []}

    def release(self, link: str) -> dict:
        """A found album's tracklist, for its page in Search, before anything is downloaded."""
        try:
            album = sources.resolve(link).album
        except Exception as e:  # shown on the page instead of the tracks
            logs.log.warning("поиск: %s не открылся: %s", link, e)
            return {"error": str(e) or type(e).__name__}
        return {
            "title": album.name, "artist": album.artist, "year": album.year, "kind": album.kind,
            "cover": album.cover_url, "service": album.service,
            "tracks": [{"number": track.track_number, "disc": track.disc_number, "title": track.title,
                        "artists": track.artists, "duration": round(track.duration),
                        "link": _track_link(album, track)} for track in album.tracks],
        }

    def tidy(self, entries: list[dict], settings: dict) -> bool:
        """Starts filling in what these library entries lack, in the background.

        Each entry is {"path", "artist", "title"} as the library listed it: the
        names are what a folder's name says, for files that say nothing
        themselves. False when a tidy-up is running already; one at a time is
        what MusicBrainz, at a request a second, can take anyway.
        """
        wanted = [(Path(str(entry["path"])).expanduser(), str(entry.get("artist") or ""),
                   str(entry.get("title") or ""))
                  for entry in entries if isinstance(entry, dict) and entry.get("path")]
        with self._lock:
            if self._tidying is not None or not wanted:
                return False
            stop = self._tidying = threading.Event()
        with_lyrics = bool(settings.get("lyrics", True))
        threading.Thread(target=self._tidy, args=(wanted, with_lyrics, stop), daemon=True).start()
        return True

    def stop_tidy(self) -> None:
        with self._lock:
            if self._tidying is not None:
                self._tidying.set()

    def _tidy(self, wanted: list[tuple[Path, str, str]], with_lyrics: bool, stop: threading.Event) -> None:
        result = {"type": "tidy", "state": "done", "total": len(wanted), "entries": 0, "files": 0,
                  "covers": 0, "lyrics": 0, "missed": []}
        logs.log.info("библиотека: привожу в порядок записей: %d", len(wanted))
        try:
            for index, (path, artist, title) in enumerate(wanted):
                if stop.is_set():
                    break
                name = title or path.stem
                self._events.put({"type": "tidy", "state": "running", "done": index, "total": len(wanted),
                                  "title": name})
                try:
                    outcome = tidy_up(path, artist, title, with_lyrics=with_lyrics, stop=stop)
                except Exception:  # one odd folder must not end the rest
                    logs.log.exception("библиотека: %s не приведён в порядок", path)
                    outcome = None
                if not stop.is_set():  # done with, whatever it came to: not offered again as it is
                    _remember_tidied(path)
                if outcome is None or not outcome.found:
                    result["missed"].append(name)
                    continue
                result["entries"] += bool(outcome.files or outcome.cover)
                result["files"] += outcome.files
                result["covers"] += outcome.cover
                result["lyrics"] += outcome.lyrics
        finally:
            with self._lock:
                self._tidying = None
            result["stopped"] = stop.is_set()
            logs.log.info("библиотека: дописаны теги в файлов: %d, обложек: %d, текстов: %d, не найдено: %s",
                          result["files"], result["covers"], result["lyrics"], ", ".join(result["missed"]) or "—")
            self._events.put(result)

    def tag_gaps(self, paths: list[str], settings: dict) -> list[str]:
        """Which of these library entries a tidy-up has something to add to:
        a file without a title, artist, album, genre, year, number, cover or,
        when lyrics are on, lyrics, or an album folder without cover.jpg.
        Entries tidied before and not changed since are left out."""
        with_lyrics = bool(settings.get("lyrics", True))
        tidied = _read_json(logs.data_dir() / TIDIED_FILE)
        _load_library_cache()
        found = []
        with folders.scanning():
            for raw in paths or []:
                entry = Path(str(raw))
                files = _entry_files(entry)
                if files and tidied.get(str(entry)) != _files_signature(files) and _has_gaps(entry, files, with_lyrics):
                    found.append(str(raw))
        _save_library_cache()
        return found

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

    def check_network(self) -> dict:
        """What this network lets through, and the VPN client proxies found here."""
        result = network.check(relay=relay_for(_load_settings()["relay"]))
        logs.log.info("сеть: %s", json.dumps(result, ensure_ascii=False))
        return result

    def find_proxies(self) -> list[dict]:
        """VPN client proxies on this computer, offered on a card Spotify refused."""
        found = network.find_proxies()
        logs.log.info("прокси VPN-клиентов: %s", json.dumps(found, ensure_ascii=False))
        return found

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
        # The installer is offered for download in the window; the hash GitHub
        # publishes beside it is what makes that safe to do unattended. Only
        # Windows has an installer: elsewhere the window offers the page.
        setup = next((asset for asset in data.get("assets") or []
                      if str(asset.get("name", "")).endswith("-setup.exe")), {}) if sys.platform == "win32" else {}
        return {
            "version": version,
            "url": data.get("html_url") or RELEASES_PAGE,
            "installer": setup.get("browser_download_url") or "",
            "size": setup.get("size") or 0,
            "digest": setup.get("digest") or "",
        }

    # Watching: a release downloaded again now and then for what was added since

    def watched(self) -> list[dict]:
        """The watched links, each with how its last check went."""
        with self._lock:
            running = {entry.get("job") for entry in self._history
                       if entry.get("state") in ("queued", "running")}
            result = []
            for entry in self._watched:
                last = entry.get("last") or {}
                result.append({
                    "kind": entry.get("kind") or "release",
                    "link": entry["link"], "title": entry.get("title") or entry["link"],
                    "service": entry.get("service") or "", "checked": entry.get("checked") or 0,
                    "state": "running" if entry.get("job") in running else last.get("state", ""),
                    "added_tracks": last.get("ok"),
                })
            return result

    def _finish_watch_check(self, job: int, state: str, counts: dict) -> bool:
        """Records how a check went; answers whether its card should go quietly.

        A check that found nothing is the usual case — twice a day for every
        watched playlist — and a card saying "already downloaded" each time
        would bury the downloads that matter. So an empty check leaves no card
        and no history, only the line in the watch list.
        """
        with self._lock:
            entry = next((item for item in self._watched if item.get("job") == job), None)
            if entry is None:
                return False
            entry["last"] = {"state": state, "ok": counts["ok"], "failed": counts["failed"]}
            watch.save(self._watched)
            quiet = state == "done" and not counts["ok"] and not counts["failed"]
            if quiet:
                self._history = [item for item in self._history if item.get("job") != job]
                _save_history(self._history)
        self._events.put({"type": "watched", "list": self.watched()})
        return quiet

    def watch(self, job: int) -> list[dict]:
        """Starts watching the release a finished card came from."""
        with self._lock:
            entry = next((item for item in self._history if item.get("job") == job), None)
            if entry and entry.get("link") and not entry.get("dry_run"):
                used = {"folder": entry.get("music_folder"), "format": entry.get("format"),
                        "track_name": entry.get("track_name"),
                        "folder_name": entry.get("folder_name")}
                settings = {**_load_settings(), **{key: value for key, value in used.items() if value}}
                self._watched = watch.add(self._watched, entry["link"], entry.get("title", ""),
                                          entry.get("service", ""), settings)
                watch.save(self._watched)
        return self.watched()

    def unwatch(self, link: str) -> list[dict]:
        with self._lock:
            self._watched = watch.remove(self._watched, link)
            watch.save(self._watched)
        return self.watched()

    def check_watched(self, force: bool = False) -> list[dict]:
        """Queues every watched link that is due, or all of them when asked.

        A check is an ordinary download of the same link into the same folder:
        the downloader skips what is already there, so only new tracks arrive.
        """
        now = time.time()
        artists = []
        with self._lock:
            entries = list(self._watched) if force else watch.due(self._watched, now)
            if not entries:
                return self.watched()
            settings = _load_settings()
            for entry in entries:
                if entry.get("kind") == "artist":  # read outside the lock: it asks the catalogue
                    entry["checked"] = now
                    artists.append(entry)
                    continue
                # A check runs with nobody to ask, so the best match is taken as before
                queued = self._enqueue(entry["link"], {**settings, **entry["settings"],
                                                       "dry_run": False, "ask_doubtful": False})
                entry["checked"], entry["job"] = now, queued["job"]
                # The window did not start this job, so it is told to draw a card
                self._events.put({"type": "added", **queued, "dry_run": False, "watched": True,
                                  "format": entry["settings"].get("format", settings["format"])})
            watch.save(self._watched)
        for entry in artists:
            self._check_artist(entry, settings)
        logs.log.info("слежение: проверяю %d", len(entries))
        return self.watched()

    def _check_artist(self, entry: dict, settings: dict) -> None:
        """Queues the releases a watched artist has put out since the last look."""
        try:
            releases = catalog.artist(entry["link"])["releases"]
        except SourceError as e:
            logs.log.warning("слежение: %s не открылся: %s", entry["link"], e)
            with self._lock:
                entry["last"] = {"state": "error", "ok": None, "failed": 0}
                watch.save(self._watched)
            return
        with self._lock:
            fresh = watch.new_releases(entry, releases)
            entry["last"] = {"state": "done", "ok": len(fresh), "failed": 0}
            for release in fresh:
                queued = self._enqueue(release["link"], {**settings, **entry["settings"],
                                                         "dry_run": False, "ask_doubtful": False})
                self._events.put({"type": "added", **queued, "dry_run": False, "watched": True,
                                  "format": entry["settings"].get("format", settings["format"])})
            watch.save(self._watched)
        if fresh:
            logs.log.info("слежение: у «%s» новых релизов: %d", entry.get("title"), len(fresh))
        self._events.put({"type": "watched", "list": self.watched()})

    def _watch_loop(self) -> None:
        time.sleep(30)  # let the window settle before the first check
        while True:
            try:
                if watch.due(self._watched):
                    self.check_watched()
                    self._events.put({"type": "watched", "list": self.watched()})
            except Exception:
                logs.log.exception("слежение: проверка не удалась")
            time.sleep(15 * 60)

    def install_update(self, release: dict) -> bool:
        """Downloads the installer for a newer release and starts it.

        Answers at once and reports progress through the same event queue the
        downloads use, so the window stays alive while a hundred megabytes come
        down. The window closes itself once the installer is running: it cannot
        replace files the running program is holding open.
        """
        with self._lock:
            if self._updating:
                return False
            self._updating = True
        threading.Thread(target=self._install_update, args=(release or {},), daemon=True).start()
        return True

    def _install_update(self, release: dict) -> None:
        def say(**event) -> None:
            self._events.put({"type": "update", **event})

        url = str(release.get("installer") or "")
        digest = str(release.get("digest") or "")
        try:
            parts = urllib.parse.urlsplit(url)
            host = (parts.hostname or "").lower()
            # Only the project's own releases, and only over TLS: the file is
            # about to be run, and nothing else about it is verified by Windows.
            if parts.scheme != "https" or host != UPDATE_HOST                     or not parts.path.startswith(f"/{REPO}/releases/download/"):
                raise ValueError(t("Ссылка на установщик не с GitHub"))
            if not digest.startswith("sha256:"):
                raise ValueError(t("GitHub не сообщил хеш установщика"))

            setup = Path(tempfile.gettempdir()) / f"Trackhound-{release.get('version', 'new')}-setup.exe"
            total = int(release.get("size") or 0)
            reader = hashlib.sha256()
            done = 0
            last = 0.0
            request = urllib.request.Request(url, headers={"User-Agent": f"Trackhound/{__version__}"})
            with urllib.request.urlopen(request, timeout=30) as response, setup.open("wb") as out:
                while chunk := response.read(262_144):
                    out.write(chunk)
                    reader.update(chunk)
                    done += len(chunk)
                    if time.time() - last > 0.2:  # a message per chunk would flood the queue
                        last = time.time()
                        say(state="downloading", percent=int(done * 100 / total) if total else 0)
            say(state="checking", percent=100)
            if reader.hexdigest() != digest.split(":", 1)[1].lower():
                setup.unlink(missing_ok=True)
                raise ValueError(t("Скачанный установщик не совпал с хешем на GitHub"))

            logs.log.info("обновление: запускаю %s", setup)
            subprocess.Popen(installer_command(setup, os.getpid()))
            say(state="starting")
            # A moment for the window to show the last message, then out of the
            # way: the installer waits for this process to be gone before it
            # replaces a single file.
            threading.Timer(1.5, self._close_window).start()
        except Exception as e:
            logs.log.exception("обновление не установилось")
            say(state="error", message=str(e) or type(e).__name__)
            with self._lock:
                self._updating = False

    def _close_window(self) -> None:
        """Closes the window and makes sure the process really ends.

        Closing the window was all this used to do, and the process did not
        always follow: Trackhound.exe stayed loaded, the installer could not
        replace it, and the update stopped with the new files beside the old
        program. So the exit is forced a moment later, whatever the window did.
        """
        self._quitting = True  # the tray must not catch this close
        if self._tray is not None:
            self._tray.close()
        if self._thumbbar is not None:
            self._thumbbar.close()
        threading.Timer(3.0, _exit_now).start()
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            _exit_now()

    def open_url(self, url: str) -> bool:
        if not url.startswith("https://github.com/"):
            return False
        webbrowser.open(url)
        return True

    def cover(self, path: str) -> str | None:
        """The address the window loads an album's cover.jpg from, or the
        picture inside a single track's file.

        It used to be the picture itself as a data: string, and the page kept
        every one it had shown until the window closed: a library of a thousand
        albums held hundreds of megabytes of base64. An address is a few dozen
        characters, and the picture behind it lives in the browser's cache,
        which lets go of what is out of sight.
        """
        target = Path(path)
        source = (folders.cover_file(target) or next(iter(folders.album_files(target)[0]), None)
                  if target.is_dir() else target)
        try:
            stat = source.stat()
        except (OSError, AttributeError):
            return None
        # The file's time in the address makes a replaced cover a new picture to the browser
        return f"{self._covers.address(str(target))}?v={stat.st_mtime_ns:x}"

    def album(self, path: str) -> dict:
        """The tracks of one library entry as their tags tell it, for the album page.

        An album this program downloaded also knows its whole tracklist, so the
        tracks that never arrived are listed too.
        """
        target = Path(path)
        files = folders.album_files(target)[0] if target.is_dir() else [target]
        tracks = [{**_tags(file), "path": str(file)} for file in files]
        tracks.sort(key=lambda track: (track["disc"], track["number"] or 999, track["title"].casefold()))
        marker = _read_marker(target) if target.is_dir() else {}
        have = {(track["disc"], track["number"]) for track in tracks}
        missing = [item for item in marker.get("tracklist") or []
                   if isinstance(item, dict) and (item.get("disc", 1), item.get("number")) not in have]
        return {
            "tracks": tracks,
            "missing": missing,
            # Older downloads kept only the count: then the page says how many are missing
            "expected": marker.get("tracks") or 0,
            "link": marker.get("link", ""),
            "service": marker.get("service", ""),
        }

    def tracks(self, roots: str | list[str]) -> list[dict]:
        """Every track in the library's folders with its tags, for the Tracks tab."""
        result = []
        with folders.scanning():
            for item in self.library(roots):
                entry = Path(item["path"])
                files = folders.album_files(entry)[0] if item["album"] else [entry]
                for file in files:
                    try:
                        modified = folders.stat(file).st_mtime
                    except OSError:
                        continue
                    result.append({**_tags(file), "path": str(file), "entry": item["path"],
                                   "cover": item["cover"], "modified": modified})
        _save_library_cache()
        return result

    # The player: the window streams a file from the local server and reads
    # its lyrics from beside it, from its tags, or from LRCLIB

    def play(self, path: str) -> dict | None:
        """Where the window streams one file from, with its tags and cover."""
        target = Path(path)
        if not _is_audio(target):
            return None
        cover = self.cover(str(target.parent if folders.cover_file(target.parent) else target))
        try:  # how the sound is made, for the line under the title in Now playing
            sound = MutagenFile(target).info
        except Exception:
            sound = None
        return {**_tags(target), "path": str(target), "url": self._covers.audio_address(str(target)),
                "cover": cover or "", "sample_rate": getattr(sound, "sample_rate", 0) or 0,
                "channels": getattr(sound, "channels", 0) or 0,
                # ReplayGain the player turns the volume by, from whatever tagged the file
                "gain": loudness.playback_gains(target)}

    def player_buttons(self, buttons: dict | None) -> None:
        """Previous, play or pause and next under the window's picture on the
        taskbar, as the player has them; None while nothing plays hides them."""
        if not thumbbar.SUPPORTED or self._window is None:
            return
        if self._thumbbar is None:
            if not buttons:
                return
            try:
                self._thumbbar = thumbbar.ThumbBar(self._window.native.Handle.ToInt64(), self._thumbbar_click)
            except Exception as e:  # a picture on the taskbar is never worth a failed call
                logs.log.debug("кнопки на панели задач: %s", e)
                return
        self._thumbbar.show(buttons)

    def _thumbbar_click(self, name: str) -> None:
        if self._window is not None and name in thumbbar.BUTTONS:
            self._window.evaluate_js(f"taskbarAction({json.dumps(name)})")

    def fullscreen(self) -> None:
        """Now playing asks for the whole screen, and gives it back."""
        if self._window is not None:
            self._window.toggle_fullscreen()

    def lyrics(self, path: str) -> dict:
        """The words of a track: synced ones from the .lrc beside it, plain ones
        from its tags, or both from LRCLIB when the file has none."""
        target = Path(path)
        if not _is_audio(target):
            return {"synced": "", "plain": "", "source": ""}
        try:
            synced = target.with_suffix(".lrc").read_text(encoding="utf-8-sig", errors="replace").strip()
        except OSError:
            synced = ""
        plain = _embedded_lyrics(target)
        if synced or plain:
            return {"synced": synced, "plain": plain, "source": "file"}
        tags = _tags(target)
        found = lyrics.find(Track(id="", title=tags["title"], artists=tags["artists"] or tags["album_artist"],
                                  duration=tags["duration"], track_number=0),
                            Album(id="", name=tags["album"], artist=tags["album_artist"]))
        if not found:
            return {"synced": "", "plain": "", "source": ""}
        return {"synced": found.synced, "plain": found.plain, "source": "lrclib"}

    def artist_picture(self, name: str) -> str:
        """A photo of the artist, looked up on Deezer once and remembered."""
        key = " ".join(str(name or "").split()).casefold()
        if not key:
            return ""
        path = logs.data_dir() / ARTISTS_FILE
        with _ARTIST_LOCK:
            known = _read_json(path)
        entry = known.get(key)
        if isinstance(entry, dict) and (entry.get("url") or time.time() - entry.get("asked", 0) < ARTIST_RETRY):
            return entry.get("url", "")
        with _ARTIST_QUERIES:
            try:
                url = sources.artist_picture(name)
            except SourceError:
                return ""  # offline or refused: asked again next time, nothing remembered
        with _ARTIST_LOCK:
            known = _read_json(path)
            known[key] = {"url": url, "asked": int(time.time())}
            _write_json(path, known)
        return url

    def watch_album(self, path: str) -> list[dict]:
        """Starts watching an album from its page in the library.

        New tracks go to the music folder the album sits in, in the format its
        files already have, named by the current rules.
        """
        target = Path(path)
        marker = _read_marker(target)
        link = marker.get("link")
        if not link:
            return self.watched()
        try:
            formats = [file.suffix[1:].lower() for file in target.iterdir() if _is_audio(file)]
        except OSError:
            formats = []
        settings = {**_load_settings(), "folder": str(target.parent)}
        if formats:
            settings["format"] = max(set(formats), key=formats.count)
        with self._lock:
            self._watched = watch.add(self._watched, link, marker.get("album") or target.name,
                                      marker.get("service", ""), settings)
            watch.save(self._watched)
        return self.watched()

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
                    threading.Thread(target=self._announce, daemon=True).start()
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
        names = {"title": link}  # the release's own name once it is known, for the notice

        def finished_tracks() -> list[dict]:
            return [dict(item, state="cancel") if item["state"] in UNFINISHED else item
                    for item in tracks.values()]

        def emit(**event) -> None:
            self._events.put({"job": job, **event})
            kind = event.get("type")
            if kind == "release":  # the card's title and tracklist, kept for next time
                names.update(title=event["title"], folder=event["folder"], single=bool(event.get("single")))
                tracks.clear()
                for item in event["tracks"]:
                    tracks[item["id"]] = {**item, "state": "waiting", "source": "", "text": ""}
                self._remember({"job": job, "link": link, "state": "running", "title": event["title"],
                                "artist": event["artist"], "kind": event["kind"], "year": event["year"],
                                "service": event["service"], "album": event["album"],
                                "cover": event["cover"], "folder": event["folder"],
                                "total": len(event["tracks"]), "tracks": list(tracks.values()),
                                "single": bool(event.get("single"))})
            elif kind == "track":
                known = tracks.get(event.get("id"))
                if known is not None:
                    known["state"] = event.get("state", known["state"])
                    known["text"] = event.get("text", "")
                    if event.get("source"):
                        known["source"] = event["source"]
                    # A held-back track keeps its candidates, to be chosen from after a restart
                    if event.get("candidates"):
                        known["candidates"] = event["candidates"]

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
            emit(type="job", state="error", message=message, code=getattr(e, "code", ""))
            self._remember({"job": job, "state": "error", "message": message,
                            "tracks": finished_tracks()})
            if not self._stop.is_set():
                with self._lock:
                    self._finished.append({"title": names["title"], "state": "error", "message": message})
            return
        state = "cancelled" if self._stop.is_set() else "done"
        counts = {"ok": len(report.ok), "skipped": len(report.skipped), "failed": len(report.failed),
                  "doubtful": len(report.doubtful)}
        self._remember({"job": job, "state": state, "dry_run": options.dry_run,
                        "tracks": finished_tracks(), **counts})
        quiet = self._finish_watch_check(job, state, counts)
        if state == "done" and not options.dry_run and (report.ok or report.skipped):
            _remember_downloaded(names, getattr(downloader, "_paths", {}).values())
        emit(type="job", state=state, dry_run=options.dry_run, quiet=quiet, **counts)
        # A check that found nothing new, a trial run and a stopped one are not news
        if state == "done" and not quiet and not options.dry_run:
            with self._lock:
                self._finished.append({"title": names["title"], "state": state, **counts})


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


class _Guid(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16), ("data3", ctypes.c_uint16),
                ("data4", ctypes.c_ubyte * 8)]


_CLSID_TASKBAR_LIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
_IID_TASKBAR_LIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"


def _taskbar_progress(hwnd: int, flag: int, percent: int) -> None:
    """Fills the program's taskbar button the way Explorer does while copying.

    ITaskbarList3 is a COM interface with no wrapper in the standard library,
    so its methods are called by their place in its table: 2 Release, 3 HrInit,
    9 SetProgressValue, 10 SetProgressState. The window's calls each come on a
    thread of their own, so each one sets up COM for itself.
    """
    ole32 = ctypes.oledll.ole32
    try:
        ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        owned = True
    except OSError:  # the thread already has COM in another mode, which serves as well
        owned = False
    try:
        clsid, iid = _Guid(), _Guid()
        ole32.CLSIDFromString(_CLSID_TASKBAR_LIST, ctypes.byref(clsid))
        ole32.CLSIDFromString(_IID_TASKBAR_LIST3, ctypes.byref(iid))
        taskbar = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid),  # CLSCTX_INPROC_SERVER
                               ctypes.byref(taskbar))
        methods = ctypes.cast(taskbar, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(methods[2])
        init = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(methods[3])
        set_value = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_ulonglong, ctypes.c_ulonglong)(methods[9])
        set_state = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_int)(methods[10])
        try:
            init(taskbar)
            set_state(taskbar, hwnd, flag)
            if flag not in (0, 1):  # a value would turn the sweep back into a plain fill
                set_value(taskbar, hwnd, percent, 100)
        finally:
            release(taskbar)
    finally:
        if owned:
            ole32.CoUninitialize()


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
    return _is_audio_name(path) and path.is_file()


def _is_audio_name(path: Path) -> bool:
    # _part_ files are tracks still being downloaded
    return path.suffix.lower() in AUDIO_SUFFIXES and not path.name.startswith("_part_")


# How deep the library looks into a folder: iTunes Media / Music / Artist / Album is four
LIBRARY_DEPTH = 6


def _library_items(root: Path, artist: str = "", depth: int = 0) -> list[dict]:
    """Every album and single track under a folder. Loose tracks count as
    single tracks at the top, where downloaded singles are put."""
    items = []
    for entry in folders.listing(root):
        if entry.name.startswith((".", "$")):
            continue  # hidden folders and the recycle bin
        path = Path(entry.path)
        try:
            if not entry.is_dir():
                if depth == 0 and entry.is_file() and _is_audio_name(path):
                    items.append(_library_item(path, [path]))
                continue
            items += _folder_items(path, artist, depth)
        except OSError:
            continue  # removed or locked while scanning
    return items


def _folder_items(path: Path, artist: str, depth: int) -> list[dict]:
    """A folder with music in it is an album, its folders below with the same
    album in their tags included (discs, titles split by a slash). One without
    is looked into, and what is inside takes its name for the artist when the
    tags do not say, as the "nested" naming and most collections have it.
    Loose tracks of different albums side by side are single tracks."""
    files, apart = folders.album_files(path)
    if not files:
        return _library_items(path, artist=path.name, depth=depth + 1) if depth < LIBRARY_DEPTH else []
    loose = [file for file in files if file.parent == path]
    # The first, middle and last are enough to tell an album from a heap of singles,
    # and reading every file's tags would make a large collection slow to open
    sample = {loose[0], loose[len(loose) // 2], loose[-1]}
    if len({folders.album_tag(file) for file in sample} - {""}) > 1:
        items = [_library_item(file, [file]) for file in loose]
        return items + (_library_items(path, artist=path.name, depth=depth + 1) if depth < LIBRARY_DEPTH else [])
    items = [_library_item(path, files, artist=artist)]
    for other in apart:
        items += _folder_items(other, path.name, depth + 1)
    return items


def _library_item(path: Path, files: list[Path], artist: str = "") -> dict:
    """What the library says about an album or a track: its own tags first,
    and the names of its folder and the one above where they say nothing."""
    album = path.is_dir()
    stats = [folders.stat(file) for file in files]
    pattern = _ALBUM_UNDER_ARTIST if artist else _ALBUM_NAME if album else _TRACK_NAME
    match = pattern.match(path.name if album else path.stem)
    # Only a folder with the marker beside its files is opened to read it
    marker = _read_marker(path) if album and any(entry.name == MARKER_NAME for entry in folders.listing(path)) else {}
    tags = _tags(files[0])
    named = {
        "title": match["title"] if match else (path.name if album else path.stem),
        "artist": artist or (match["artist"] if match else ""),
        "year": (match["year"] or "") if match and album else "",
    }
    if album and tags["album"]:
        named = {"title": tags["album"],
                 "artist": tags["album_artist"] or tags["artists"].split(",")[0].strip() or named["artist"],
                 "year": tags["year"] or named["year"]}
    elif not album and tags["artists"]:
        named = {"title": tags["title"], "artist": tags["artists"], "year": ""}
    return {
        # The link the album came from, when it was downloaded by this program
        "link": marker.get("link", ""),
        "expected": marker.get("tracks") or 0,
        "album": album,
        **named,
        "path": str(path),
        # Its first file's, as "Alternative Rock; Rock": the library's genre filter splits it
        "genre": tags["genre"],
        "tracks": len(files),
        "size": sum(stat.st_size for stat in stats),
        "modified": max(stat.st_mtime for stat in stats),
        # A picture beside the files or inside them: the cover server finds which, or neither
        "cover": True,
    }


# The ID3 frames behind what the library shows, by the names the other formats use
_SHOWN_NAMES = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2", "tracknumber": "TRCK",
                "discnumber": "TPOS", "date": "TDRC", "genre": "TCON"}
_SHOWN_FRAMES = {name: Frames[name] for name in (*_SHOWN_NAMES.values(), "TYER", "TDAT")}


def _tags(path: Path) -> dict:
    """Title, artists, album, numbers, length and bitrate of one file."""
    try:
        stat = folders.stat(path)
    except OSError:
        return _blank_tags(path)
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _TAG_CACHE:
        return _TAG_CACHE[key]
    info = _blank_tags(path)
    mp3 = path.suffix.lower() == ".mp3"
    try:
        # An mp3's lyrics and pictures are most of its tags and none of what is
        # shown here: only the frames that are shown are read, twice as fast
        audio = MP3(path, known_frames=_SHOWN_FRAMES) if mp3 else MutagenFile(path, easy=True)
    except Exception:  # a broken or half-written file still gets its row
        audio = None
    if audio is not None:
        tags = audio.tags or {}

        def first(name: str) -> str:
            try:
                values = (tags.get(_SHOWN_NAMES[name]) or []) if mp3 else (tags.get(name) or [])
                values = list(values) if mp3 else values
            except (KeyError, ValueError, TypeError):
                values = []
            return str(values[0]).strip() if values else ""

        info.update({
            "title": first("title") or info["title"],
            "artists": first("artist"),
            "album": first("album"),
            "album_artist": first("albumartist"),
            "number": _leading_number(first("tracknumber")),
            "disc": _leading_number(first("discnumber")) or 1,
            "year": first("date")[:4],
            "genre": first("genre"),
            "duration": round(getattr(audio.info, "length", 0) or 0),
            "bitrate": round((getattr(audio.info, "bitrate", 0) or 0) / 1000),
        })
    if len(_TAG_CACHE) > 50_000:
        _TAG_CACHE.clear()
    _TAG_CACHE[key] = info
    return info


def _blank_tags(path: Path) -> dict:
    return {"title": path.stem, "artists": "", "album": "", "album_artist": "", "number": 0, "disc": 1,
            "year": "", "genre": "", "duration": 0, "bitrate": 0, "format": path.suffix[1:].lower()}


def _leading_number(text: str) -> int:
    """A "3/10" and a "03" are both track 3."""
    match = re.match(r"\s*(\d+)", text or "")
    return int(match.group(1)) if match else 0


class _CoverServer:
    """Serves the library's covers to the window on 127.0.0.1.

    Only a path the window has asked a cover for gets an address, and the
    address names it by a random token, so nothing else on the computer can
    read files through it. The server starts with the first cover asked for.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._paths: dict[str, str] = {}  # token → path
        self._tokens: dict[str, str] = {}  # (kind, path) → token
        self._port = 0

    def address(self, path: str) -> str:
        return self._address("cover", path)

    def audio_address(self, path: str) -> str:
        """The player's address for a music file, streamed with seeking."""
        return self._address("audio", path)

    def _address(self, kind: str, path: str) -> str:
        with self._lock:
            if not self._port:
                self._port = self._start()
            token = self._tokens.get(f"{kind}:{path}")
            if token is None:
                token = secrets.token_urlsafe(16)
                self._tokens[f"{kind}:{path}"] = token
                self._paths[token] = f"{kind}:{path}"
            return f"http://127.0.0.1:{self._port}/{kind}/{token}"

    def path(self, token: str, kind: str = "cover") -> str | None:
        with self._lock:
            known = self._paths.get(token) or ""
        return known.removeprefix(f"{kind}:") if known.startswith(f"{kind}:") else None

    def _start(self) -> int:
        covers = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # one connection for a whole screen of covers

            def do_GET(self):
                route = urllib.parse.urlsplit(self.path).path
                if route.startswith("/audio/"):
                    path = covers.path(route.removeprefix("/audio/"), "audio")
                    try:
                        if path:
                            _send_audio(self, Path(path))
                        else:
                            self.send_error(404)
                    except (ConnectionError, OSError):
                        pass  # the player dropped the request to seek elsewhere
                    return
                path = covers.path(route.removeprefix("/cover/")) if route.startswith("/cover/") else None
                data = _cover_bytes(Path(path)) if path else None
                if not data:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png" if data.startswith(b"\x89PNG") else "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=86400")
                # The album page reads the cover's colour through a canvas, which needs this
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass  # a line for every cover would bury the log

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server.server_port


AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".opus": "audio/ogg"}
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def _send_audio(handler: BaseHTTPRequestHandler, path: Path) -> None:
    """A music file, whole or the byte range asked for: the player seeks by
    asking for the part of the file it jumps to."""
    try:
        size = path.stat().st_size
        source = path.open("rb")
    except OSError:
        handler.send_error(404)
        return
    with source:
        start, end = 0, size - 1
        asked = _RANGE_RE.match(handler.headers.get("Range") or "")
        if asked and (asked[1] or asked[2]):
            if asked[1]:
                start = int(asked[1])
                end = min(int(asked[2]), size - 1) if asked[2] else size - 1
            else:  # "bytes=-500": the last 500 bytes
                start = max(0, size - int(asked[2]))
            if start > end:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{size}")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return
        handler.send_response(206 if asked else 200)
        handler.send_header("Content-Type", AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream"))
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Length", str(end - start + 1))
        if asked:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        handler.end_headers()
        source.seek(start)
        left = end - start + 1
        while left > 0:
            chunk = source.read(min(256 * 1024, left))
            if not chunk:
                break
            handler.wfile.write(chunk)
            left -= len(chunk)


def _embedded_lyrics(path: Path) -> str:
    """The plain lyrics in a file's own tags, "" when it has none."""
    try:
        tags = MutagenFile(path).tags
    except Exception:
        return ""
    if tags is None:
        return ""
    if hasattr(tags, "getall"):  # ID3
        found = tags.getall("USLT")
        return str(found[0].text).strip() if found else ""
    for key in ("\xa9lyr", "lyrics", "unsyncedlyrics"):
        try:
            values = tags.get(key)
        except (KeyError, ValueError):
            values = None
        if values:
            return str(values[0]).strip()
    return ""


def _cover_bytes(path: Path) -> bytes | None:
    """An album's cover beside its files, or else the picture inside its
    first file; the picture inside a single track's file."""
    try:
        if not path.is_dir():
            return _embedded_cover(path)
        picture = folders.cover_file(path)
        if picture is not None:
            return picture.read_bytes()
        first = next(iter(folders.album_files(path)[0]), None)
        return _embedded_cover(first) if first else None
    except OSError:
        return None


def _embedded_cover(path: Path) -> bytes | None:
    """The front cover stored inside an mp3, m4a or opus file."""
    try:
        audio = MutagenFile(path)
    except Exception:
        return None
    tags = getattr(audio, "tags", None)
    if not tags:
        return None
    if hasattr(tags, "getall"):  # ID3
        pictures = tags.getall("APIC")
        return pictures[0].data if pictures else None
    if "covr" in tags:  # MP4
        return bytes(tags["covr"][0])
    for block in tags.get("metadata_block_picture", []) if hasattr(tags, "get") else []:
        try:
            return Picture(base64.b64decode(block)).data
        except (ValueError, TypeError):
            continue
    return None


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logs.log.warning("не записал %s: %s", path.name, e)


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
    # Empty is the program's own relay, "off" none at all
    relay = str(settings.get("relay") or "").strip()
    if relay != "off" and not _RELAY_RE.match(relay):
        relay = ""
    return {
        "folder": str(settings.get("folder") or DEFAULT_OUTPUT_DIR),
        "format": settings.get("format") if settings.get("format") in FORMATS else "mp3",
        "threads": threads,
        "theme": settings.get("theme") if settings.get("theme") in THEMES else "system",
        "dry_run": bool(settings.get("dry_run", False)),
        "cookies_browser": (settings.get("cookies_browser")
                            if settings.get("cookies_browser") in COOKIE_BROWSERS else ""),
        "rate_limit": rate_limit,
        "proxy": proxy,
        "relay": relay,
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
        "replaygain": bool(settings.get("replaygain", False)),
        # Hold back a track whose match may be the wrong song, for a choice
        "ask_doubtful": bool(settings.get("ask_doubtful", True)),
        # Lyrics from LRCLIB into the tags, synced ones into an .lrc beside the track
        "lyrics": bool(settings.get("lyrics", True)),
        # Closing the window leaves the program in the tray while it has work (Windows)
        "tray": bool(settings.get("tray", True)),
        # A notice when downloads finish and the window is not in use
        "notify": bool(settings.get("notify", True)),
        "profiles": _profiles(settings.get("profiles")),
        # Folders besides the music folder that the library shows, left where they are
        "library_folders": _library_folders(settings.get("library_folders"),
                                            str(settings.get("folder") or DEFAULT_OUTPUT_DIR)),
        "library_view": settings.get("library_view") if settings.get("library_view") in LIBRARY_VIEWS else "grid",
    }


def _expand_artists(links: list[str]) -> list[str]:
    """Each artist link stands for their albums and EPs, oldest first. One
    that cannot be read stays as it is, and its card says why."""
    expanded = []
    for link in links:
        if not catalog.is_artist_link(link):
            expanded.append(link)
            continue
        try:
            releases = catalog.discography(link)
        except SourceError as e:
            logs.log.warning("дискография %s не прочиталась: %s", link, e)
            expanded.append(link)
            continue
        logs.log.info("дискография %s: альбомов и EP: %d", link, len(releases))
        expanded += [release["link"] for release in releases] or [link]
    return expanded


def _track_link(album, track) -> str:
    """A link to one track of a found album, to download it alone; "" where the
    catalogue gives none."""
    if album.service == "Deezer" and track.id.isdigit():
        return f"https://www.deezer.com/track/{track.id}"
    if track.audio_url.startswith("https://www.youtube.com/watch?v="):
        return track.audio_url.replace("https://www.youtube.com/", "https://music.youtube.com/", 1)
    return ""


# What a tidy-up fills in, and where a file's tags say so; lyrics only when they are on
GAP_TAGS = ("title", "artist", "album", "genre", "date", "cover")
_GAP_CACHE: dict[tuple[str, int, int], frozenset] = {}


def _entry_files(entry: Path) -> list[Path]:
    if entry.is_dir():
        return folders.album_files(entry)[0]
    return [entry] if _is_audio(entry) else []


def _files_signature(files: list[Path]) -> str:
    """How many files there are and when the newest changed: a tidy-up or a
    new file changes it."""
    try:
        return f"{len(files)}:{max(folders.stat(file).st_mtime_ns for file in files)}"
    except (OSError, ValueError):
        return ""


def _has_gaps(entry: Path, files: list[Path], with_lyrics: bool) -> bool:
    if entry.is_dir() and folders.cover_file(entry) is None:
        return True
    wanted = set(GAP_TAGS) | ({"track"} if entry.is_dir() else set()) | ({"lyrics"} if with_lyrics else set())
    return any(not wanted <= _present_tags(file) for file in files)


def _present_tags(path: Path) -> frozenset:
    """The names of the tags a file has, read once per version of the file."""
    try:
        stat = folders.stat(path)
    except OSError:
        return frozenset(GAP_TAGS) | {"track", "lyrics"}  # gone: nothing to add to it
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _GAP_CACHE:
        if len(_GAP_CACHE) > 50_000:
            _GAP_CACHE.clear()
        _GAP_CACHE[key] = frozenset(name for name, value in read_tags(path).items() if value)
    return _GAP_CACHE[key]


_LIBRARY_CACHE = {"loaded": False, "saved": -1, "items": {}}
_LIBRARY_CACHE_LOCK = threading.Lock()


def _load_library_cache() -> None:
    """The tags read in an earlier run, once per run. Each is kept with the
    file's size and time, so a file changed since is simply read again."""
    with _LIBRARY_CACHE_LOCK:
        if _LIBRARY_CACHE["loaded"]:
            return
        _LIBRARY_CACHE["loaded"] = True
        data = _read_json(logs.data_dir() / LIBRARY_CACHE_FILE)
        if data.get("version") != 1:
            return
        items = data.get("items")
        if isinstance(items, dict):
            _LIBRARY_CACHE["items"] = {key: value for key, value in items.items() if isinstance(value, list)}
        try:
            for path, mtime, size, info in data.get("tags", []):
                _TAG_CACHE.setdefault((path, mtime, size), info)
            for path, mtime, size, names in data.get("gaps", []):
                _GAP_CACHE.setdefault((path, mtime, size), frozenset(names))
            folders.remember_albums({(path, mtime): name for path, mtime, name in data.get("albums", [])})
        except (TypeError, ValueError):  # a file from elsewhere: what was read of it stays
            logs.log.warning("кэш библиотеки не прочитался, файлы будут прочитаны заново")
        _LIBRARY_CACHE["saved"] = _library_cache_size()


def _save_library_cache(roots: str = "", items: list[dict] | None = None) -> None:
    """Writes the tags down when anything new was read, for a file read in
    two versions the later one, and what the library holds when it changed."""
    with _LIBRARY_CACHE_LOCK:
        size = _library_cache_size()
        kept = _LIBRARY_CACHE["items"]
        new_items = items is not None and kept.get(roots) != items
        if size == _LIBRARY_CACHE["saved"] and not new_items:
            return
        if new_items:
            _LIBRARY_CACHE["items"] = kept = {roots: items}  # only the folders read last
        tags = {key[0]: [*key, info] for key, info in list(_TAG_CACHE.items())}
        gaps = {key[0]: [*key, sorted(names)] for key, names in list(_GAP_CACHE.items())}
        albums = {key[0]: [*key, name] for key, name in folders.known_albums().items()}
        _write_json(logs.data_dir() / LIBRARY_CACHE_FILE, {
            "version": 1, "items": kept, "tags": list(tags.values()), "gaps": list(gaps.values()),
            "albums": list(albums.values())})
        _LIBRARY_CACHE["saved"] = size


def _forget_in_library_cache(paths: set[str]) -> None:
    """What was deleted is not shown again at the next start, while the folders are read."""
    if not paths:
        return
    _load_library_cache()
    with _LIBRARY_CACHE_LOCK:
        kept = _LIBRARY_CACHE["items"]
        _LIBRARY_CACHE["items"] = {key: [item for item in items if item.get("path") not in paths]
                                   for key, items in kept.items()}
        _LIBRARY_CACHE["saved"] = -1  # written on the next save, even with no tags read
    _save_library_cache()


def _roots_key(roots: list[str]) -> str:
    return "|".join(sorted(str(Path(root).expanduser()) for root in roots))


def _library_cache_size() -> int:
    return len(_TAG_CACHE) + len(_GAP_CACHE) + len(folders.known_albums())


def _remember_downloaded(names: dict, paths) -> None:
    """A download looked up all a tag fill-in would: the genre, the cover and
    the lyrics. What it could not find, a fill-in would not find either, so a
    release just downloaded is not offered one until its files change: an album
    by its folder, a single track by its file."""
    try:
        if names.get("single"):
            for path in paths:
                _remember_tidied(Path(path))
        elif names.get("folder"):
            _remember_tidied(Path(names["folder"]))
    except Exception as e:  # only spares a needless offer later: never worth a failed download
        logs.log.warning("не запомнил скачанное как готовое: %s", e)


def _remember_tidied(entry: Path) -> None:
    files = _entry_files(entry)
    if not files:
        return
    path = logs.data_dir() / TIDIED_FILE
    with _ARTIST_LOCK:  # the same small-file lock the artists' photos use
        known = _read_json(path)
        known[str(entry)] = _files_signature(files)
        _write_json(path, known)


def _library_folders(raw, folder: str) -> list[str]:
    result = []
    for item in raw if isinstance(raw, list) else []:
        text = str(item or "").strip()
        if text and text != folder and text not in result:
            result.append(text)
    return result[:20]


def _tray_words() -> dict[str, str]:
    return {"open": t("Открыть Trackhound"), "check": t("Проверить слежение сейчас"), "quit": t("Выход")}


def _notice(finished: list[dict]) -> tuple[str, str]:
    """The title and text of the notice about these finished releases."""
    def counts(items: list[dict]) -> str:
        total = {key: sum(item.get(key) or 0 for item in items) for key in ("ok", "skipped", "failed", "doubtful")}
        parts = [t("скачано: {count}", count=total["ok"]) if total["ok"] else "",
                 t("уже были: {count}", count=total["skipped"]) if total["skipped"] else "",
                 t("не скачалось: {count}", count=total["failed"]) if total["failed"] else "",
                 t("ждут выбора: {count}", count=total["doubtful"]) if total["doubtful"] else ""]
        return ", ".join(filter(None, parts))

    if len(finished) == 1:
        item = finished[0]
        if item["state"] == "error":
            return t("Не скачалось: {title}", title=item["title"]), item.get("message", "")
        whole = not item.get("failed") and not item.get("doubtful")
        title = t("Скачано: {title}", title=item["title"]) if whole else t("Скачано не всё: {title}",
                                                                          title=item["title"])
        return title, counts([item])
    errors = sum(item["state"] == "error" for item in finished)
    text = counts(finished)
    if errors:
        text = ", ".join(filter(None, [text, t("ссылок с ошибкой: {count}", count=errors)]))
    return t("Загрузки завершены: {count}", count=len(finished)), text


def installer_command(setup: Path, pid: int) -> list[str]:
    """How the downloaded installer is started for an update.

    Silently, because the person has already said yes by pressing the button;
    told the process id to wait for, so nothing is locked when files are
    replaced; and marked as an update, so the installer opens the new version
    when it is done. The installer also closes any copy it still finds running
    from its folder, which is what saves an update started by an older release
    that passes none of this.
    """
    return [str(setup), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1", f"/WAITPID={pid}"]


def _exit_now() -> None:
    logging.shutdown()
    os._exit(0)


def _profiles(raw) -> list[dict]:
    """The saved folder-and-format sets, cleaned the way the settings are.

    A profile is only a name and the keys in PROFILE_KEYS, each checked against
    the same rules as the live setting, so a hand-edited file cannot put the
    window into a state its own controls could not reach.
    """
    profiles, seen = [], set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()[:40]
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        clean = _normalize(entry)
        profiles.append({"name": name, **{key: clean[key] for key in PROFILE_KEYS}})
        if len(profiles) >= PROFILE_LIMIT:
            break
    return profiles


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
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        # The Linux build draws the window with Qt WebEngine, whose Chromium
        # sandbox needs user namespaces that Ubuntu 24.04 and others forbid to
        # unconfined programs; without this the window dies on start. The page
        # is the program's own, not the open web.
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    logs.setup()
    settings = _load_settings()
    set_language(settings["language"])
    use_proxy(settings["proxy"])
    use_relay(relay_for(settings["relay"]))
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
    api._window.events.closing += api._on_closing
    api._sync_tray(_load_settings())
    webview.start(http_server=True)
