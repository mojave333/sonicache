"""Desktop window: an HTML/CSS interface rendered by Edge WebView2 via pywebview.

web/js/*.js call Api methods as window.pywebview.api.<name>() and poll for
download events. Downloads run one link at a time in a background thread.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

import webview

from . import __version__, logs, mediacontrols, prefs, presence, relay_for, scrobble, subsonic, thumbbar, tray, watch
from .bridge.catalogue import Catalogue
from .bridge.devices import Devices
from .bridge.downloads import Downloads
from .bridge.library import Library
from .bridge.player import Player
from .bridge.window import FRAMELESS, MIN_SIZE, TITLE, WEB_DIR, WINDOW_SIZE, Window
from .engine import use_relay
from .engine.downloader import DEFAULT_OUTPUT_DIR, Downloader, Options, use_proxy
from .history import _load_history
from .i18n import LANGUAGES, set_language, t
from .libraryscan import _favourites, _seen
from .mediaserver import _CoverServer
from .prefs import _adopt_former_settings, _normalize
from .system import _system_dark

WEBVIEW2_SETUP_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

# The page's background in each theme (--bg in web/style.css): the window takes it before the page is drawn
PAGE_LIGHT, PAGE_DARK = "#F6F0DE", "#23212C"


class Api(Window, Downloads, Catalogue, Library, Player, Devices):
    """What the page calls on window.pywebview.api: the parts in bridge/, and the start and the settings
    here. pywebview finds the methods of all of them on the one object."""

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
        self._syncing: threading.Event | None = None  # set to stop the sync to a device that runs
        self._wifi: subsonic.Listener | None = None  # the library on the home network, while it is on
        self._wifi_port = 0
        self._wifi_error = ""
        self._tray: tray.Tray | None = None
        self._thumbbar: thumbbar.ThumbBar | None = None  # the player's buttons under the taskbar picture
        self._media: mediacontrols.MediaControls | None = None  # the track in Windows' own media controls
        # Each of the two is made once: the page's calls come on threads of their own, two at once at the start,
        # and a second one made and dropped left Windows calling into its freed procedures, which ended the program
        self._extras_lock = threading.Lock()
        self._presence = presence.Presence(self._web_cover)  # what plays, on the Discord profile
        self._discord = False  # whether the person wants it shown there
        self._now: dict | None = None  # what plays, as the player last said
        self._scrobbler = scrobble.Scrobbler()  # listens sent to Last.fm and ListenBrainz
        self._announced = ""  # the track "now playing" was last sent for
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
        settings = prefs._load_settings()
        self._discord = settings["discord"]
        with self._lock:
            if self._watcher is None:
                self._watcher = threading.Thread(target=self._watch_loop, daemon=True)
                self._watcher.start()
        self.start_wifi_if_on()
        return {
            "version": __version__,
            "first_run": not prefs.SETTINGS_FILE.exists(),  # the welcome screen, once
            "seen": sorted(_seen()),
            "favourites": _favourites(),
            "tray": tray.SUPPORTED,  # whether the window can go on in the notification area
            "frameless": FRAMELESS,  # the page draws the window's buttons and edges
            "language": set_language(settings["language"]),  # "system" resolved to ru or en
            "settings": settings,
            "problems": Downloader(Options(DEFAULT_OUTPUT_DIR)).environment_problems(),
            "history": self._history,
            "watched": self.watched(),
        }

    def set_language(self, setting: str) -> str:
        """Applies the language and answers with the one actually chosen."""
        return set_language(setting if setting in LANGUAGES else "system")

    def save_settings(self, settings: dict) -> None:
        settings = _normalize(settings)
        use_proxy(settings["proxy"])  # metadata requests start using it at once
        use_relay(relay_for(settings["relay"]))
        set_language(settings["language"])  # errors from now on speak it
        prefs._save_settings(settings)
        self._sync_tray(settings)
        if settings["discord"] != self._discord:
            self._discord = settings["discord"]
            self._presence.show(self._now if self._discord else None)


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
    _adopt_former_settings()
    settings = prefs._load_settings()
    set_language(settings["language"])
    use_proxy(settings["proxy"])
    use_relay(relay_for(settings["relay"]))
    if not _webview2_installed() and not _offer_webview2():
        return
    # The window's engine leaves Windows' media controls to the program,
    # which says what plays there (mediacontrols); set by hand, it is left alone
    if not os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"):
        arguments = mediacontrols.browser_arguments()
        if arguments:
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = arguments
    api = Api()
    theme = prefs._load_settings()["theme"]
    dark = theme == "dark" or (theme == "system" and _system_dark())
    api._window = webview.create_window(
        TITLE,
        url=str(WEB_DIR / "index.html"),
        js_api=api,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],  # the size the window returns to when it is un-maximized
        min_size=MIN_SIZE,
        frameless=FRAMELESS,
        easy_drag=False,  # the program moves the window itself (winframe), without the lag of a call per step
        maximized=not FRAMELESS,  # a window without a frame is maximized once it is up (_keep_frame_style)
        background_color=PAGE_DARK if dark else PAGE_LIGHT,  # matches --bg, so no flash before CSS loads
        text_select=True,
    )
    api._window.events.closing += api._on_closing
    api._window.events.shown += api._keep_frame_style
    api._sync_tray(prefs._load_settings())
    webview.start(http_server=True)
