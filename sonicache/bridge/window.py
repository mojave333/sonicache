"""The window itself and the program around it: the tray, the frame, full screen, the
taskbar buttons, updates, the diagnostics and the network check.

One part of gui.Api: the methods the page calls on window.pywebview.api.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from .. import __version__, logs, mediacontrols, prefs, relay_for, thumbbar, tray, winframe
from ..engine import network
from ..engine.downloader import DEFAULT_OUTPUT_DIR, Downloader, Options
from ..i18n import t
from ..system import _copy_to_clipboard, _exit_now, _newer, _package_manager, _release_age, installer_command

TITLE = "Sonicache"

WEB_DIR = Path(__file__).parent.parent / "web"  # beside gui.py, a folder up

REPO = "mojave333/sonicache"

RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

UPDATE_HOST = "github.com"

# On Windows the window has no frame of its own: the top bar is its title bar and the page draws the
# buttons and the edges to size it by.
FRAMELESS = sys.platform == "win32"

WINDOW_SIZE = (1160, 800)

MIN_SIZE = (720, 520)

def _tray_words() -> dict[str, str]:
    return {"open": t("Открыть Sonicache"), "check": t("Проверить слежение сейчас"), "quit": t("Выход")}


class Window:
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
        if not (tray.SUPPORTED and prefs._load_settings()["tray"] and self._tray is not None and self._tray.shown):
            return False
        with self._lock:
            return self._worker is not None or bool(self._watched) or self._tidying is not None

    def _on_closing(self):
        """The window's close button. Returns False to keep the program going
        in the tray; anything else lets the window close and the program end."""
        if self._quitting or not self._keeps_running():
            self._quitting = True
            self._close_extras()
            return None
        threading.Thread(target=self._hide, daemon=True).start()
        return False

    def _close_extras(self) -> None:
        """The tray icon, the taskbar buttons and Windows' media controls go before the window."""
        for extra in (self._tray, self._thumbbar, self._media):
            if extra is not None:
                extra.close()

    def _hide(self) -> None:
        if self._window is None:
            return
        self._window.hide()
        self._hidden = True
        logs.log.info("окно: спрятано в трей")
        if not self._told_hidden and self._tray is not None:
            self._told_hidden = True
            self._tray.notify(t("Sonicache работает в фоне"),
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

    def diagnostics(self) -> dict:
        """Where the log is and what a bug report should say."""
        downloader = Downloader(Options(DEFAULT_OUTPUT_DIR))
        tools = {"ffmpeg": downloader.ffmpeg or "",
                 **{name: runtime["path"] for name, runtime in downloader.js_runtimes.items()}}
        ytdlp = logs.package_version("yt_dlp")
        return {
            "log": str(logs.log_file()),
            "exists": logs.log_file().exists(),
            "report": logs.report(prefs._load_settings(), downloader.environment_problems(), tools),
            "ytdlp": ytdlp,
            "ytdlp_age": _release_age(ytdlp),
        }

    def open_logs(self) -> bool:
        logs.log_dir().mkdir(parents=True, exist_ok=True)
        return self.open_folder(str(logs.log_file() if logs.log_file().exists() else logs.log_dir()))

    def check_network(self) -> dict:
        """What this network lets through, and the VPN client proxies found here."""
        result = network.check(relay=relay_for(prefs._load_settings()["relay"]))
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
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"Sonicache/{__version__}"},
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
        # Installed by a package manager, the program is that manager's to
        # update: the installer would put a second copy beside it
        manager = _package_manager()
        setup = next((asset for asset in data.get("assets") or []
                      if str(asset.get("name", "")).endswith("-setup.exe")), {}) \
            if sys.platform == "win32" and not manager else {}
        return {
            "version": version,
            "manager": manager,
            "url": data.get("html_url") or RELEASES_PAGE,
            "installer": setup.get("browser_download_url") or "",
            "size": setup.get("size") or 0,
            "digest": setup.get("digest") or "",
        }

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
            if parts.scheme != "https" or host != UPDATE_HOST \
                    or not parts.path.startswith(f"/{REPO}/releases/download/"):
                raise ValueError(t("Ссылка на установщик не с GitHub"))
            if not digest.startswith("sha256:"):
                raise ValueError(t("GitHub не сообщил хеш установщика"))

            setup = Path(tempfile.gettempdir()) / f"Sonicache-{release.get('version', 'new')}-setup.exe"
            total = int(release.get("size") or 0)
            reader = hashlib.sha256()
            done = 0
            last = 0.0
            request = urllib.request.Request(url, headers={"User-Agent": f"Sonicache/{__version__}"})
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
        always follow: Sonicache.exe stayed loaded, the installer could not
        replace it, and the update stopped with the new files beside the old
        program. So the exit is forced a moment later, whatever the window did.
        """
        self._quitting = True  # the tray must not catch this close
        self._close_extras()
        self._presence.close()
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

    def _media_button(self, name: str) -> None:
        if self._window is not None and name in mediacontrols.BUTTONS.values():
            self._window.evaluate_js(f"mediaAction({json.dumps(name)})")

    def _thumbbar_click(self, name: str) -> None:
        if self._window is not None and name in thumbbar.BUTTONS:
            self._window.evaluate_js(f"taskbarAction({json.dumps(name)})")

    def fullscreen(self) -> None:
        """Now playing asks for the whole screen, and gives it back as it was.

        pywebview takes the whole screen by dropping the window's frame style and
        gives it back as a window of the screen's size that is not maximized, a
        little past every edge. Without a frame of the system's (FRAMELESS) the
        style is put back here, and a window that filled the screen fills it again."""
        if self._window is None:
            return
        entering = not self._in_fullscreen()
        if entering:
            self._was_maximized = self.window_state()
        self._window.toggle_fullscreen()
        if entering or not FRAMELESS or not self._handle():
            return
        try:
            winframe.restore_frame_style(self._handle())
            if getattr(self, "_was_maximized", False):
                winframe.centre(self._handle(), WINDOW_SIZE)  # what it goes back to when it is un-maximized
                self._window.maximize()
        except Exception:
            logs.log.debug("окно: рамка после полного экрана не возвращена", exc_info=True)

    def _in_fullscreen(self) -> bool:
        return bool(getattr(self._window.native, "is_fullscreen", False)) if self._window is not None else False

    def page_error(self, text: str) -> None:
        """An error in the page's script, kept in the log: a window that stops answering leaves nothing else."""
        logs.log.warning("страница: %s", str(text)[:2000])

    # The window's own buttons and edges, for a window without a frame (FRAMELESS):
    # the page draws them and reports a press here

    def _handle(self) -> int:
        return self._window.native.Handle.ToInt64() if self._window is not None and self._window.native else 0

    def window_state(self) -> bool:
        """Whether the window fills the screen."""
        return winframe.is_maximized(self._handle()) if FRAMELESS else False

    def window_minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def window_maximize(self) -> bool:
        """Fills the screen, or gives the window back its size; answers whether it fills it now."""
        form = self._window.native if self._window is not None else None
        if form is None or self._in_fullscreen():  # the whole screen is left by Now playing's own button
            return self.window_state()
        try:
            from System import Func, Type
            from System.Windows.Forms import FormWindowState

            def toggle():
                form.WindowState = (FormWindowState.Normal if form.WindowState == FormWindowState.Maximized
                                    else FormWindowState.Maximized)

            form.Invoke(Func[Type](toggle))
        except Exception:
            logs.log.debug("окно: развернуть не вышло", exc_info=True)
        return self.window_state()

    def window_close(self) -> None:
        """The window's own close button: into the tray while there is work, else the end, as the tray's
        Quit is, made sure of a moment later.

        It used to hand the window's thread the form's Close and wait for it. Now and then the window's thread
        never took the call up (seen on 2026-10-19 in a window open for a while: the press reached the page and
        this call, the closing handler never ran, the window stayed), and the program could not be closed by
        its button at all; the system's own ways of closing it (Alt+F4, the taskbar) still go through the form.
        """
        if self._window is None:
            return
        if self._keeps_running():
            threading.Thread(target=self._hide, daemon=True).start()
            return
        logs.log.info("окно: закрыто кнопкой")
        self._close_window()

    def window_drag(self) -> None:
        """The top bar was pressed: the window follows the cursor until the button is let go."""
        if FRAMELESS and self._handle() and not self._in_fullscreen():
            winframe.in_background(winframe.drag, self._handle())

    def window_resize(self, edge: str) -> None:
        """An edge or a corner was pressed: the window is sized by it until the button is let go."""
        if FRAMELESS and self._handle() and not self._in_fullscreen():
            winframe.in_background(winframe.resize, self._handle(), edge, MIN_SIZE)

    def _keep_frame_style(self) -> None:
        """Once the window is up: it animates, and the taskbar's button and the system menu work on it, as on any window."""
        if not FRAMELESS or not self._handle():
            return
        try:
            winframe.keep_system_frame_style(self._handle())
        except Exception:
            logs.log.debug("окно: стиль рамки не возвращён", exc_info=True)
        # The window opens at its size and fills the screen once it has its style: asked to
        # fill it before, it is laid out wrongly (at its own place, a screen's size)
        if self._window is not None:
            winframe.centre(self._handle(), WINDOW_SIZE)  # what the window goes back to when it is un-maximized
            self._window.maximize()
