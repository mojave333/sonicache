"""Settings, the update check and the library listing behind the window."""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from trackhound import gui
from trackhound.engine.downloader import DEFAULT_OUTPUT_DIR


class TestNormalize:
    def test_empty_settings_get_the_defaults(self):
        settings = gui._normalize({})
        assert settings == {
            "folder": str(DEFAULT_OUTPUT_DIR),
            "format": "mp3",
            "threads": 3,
            "theme": "system",
            "dry_run": False,
            "cookies_browser": "",
            "rate_limit": 0,
            "proxy": "",
            "relay": "",  # the program's own
            "language": gui.resolve("system"),  # settled on load, never left as "system"
            "track_name": "auto",
            "folder_name": "flat",
            "sidebar": 64,
            "replaygain": False,
            "ask_doubtful": True,
            "lyrics": True,
            "tray": True,
            "notify": True,
            "profiles": [],
            "library_folders": [],
            "library_view": "grid",
            "volume": 0.8,
            "shuffle": False,
            "repeat": "off",
            "discord": False,
        }

    @pytest.mark.parametrize("given, expected", [(0, 1), (1, 1), (4, 4), (8, 8), (99, 8), ("5", 5)])
    def test_threads_stay_within_reach(self, given, expected):
        assert gui._normalize({"threads": given})["threads"] == expected

    @pytest.mark.parametrize("given", ["", None, "many", 3.5])
    def test_unreadable_threads_fall_back_to_three(self, given):
        assert gui._normalize({"threads": given})["threads"] in (1, 3)

    @pytest.mark.parametrize("given, expected", [("m4a", "m4a"), ("opus", "opus"),
                                                 ("flac", "mp3"), (None, "mp3")])
    def test_only_known_formats_survive(self, given, expected):
        assert gui._normalize({"format": given})["format"] == expected

    @pytest.mark.parametrize("given, expected", [("dark", "dark"), ("light", "light"),
                                                 ("neon", "system")])
    def test_only_known_themes_survive(self, given, expected):
        assert gui._normalize({"theme": given})["theme"] == expected

    def test_only_supported_browsers_survive(self):
        assert gui._normalize({"cookies_browser": "firefox"})["cookies_browser"] == "firefox"
        assert gui._normalize({"cookies_browser": "netscape"})["cookies_browser"] == ""

    @pytest.mark.parametrize("given, expected", [(30, 64), (64, 64), (208, 208), (900, 320),
                                                 ("x", 64)])
    def test_the_side_panel_width_is_clamped(self, given, expected):
        assert gui._normalize({"sidebar": given})["sidebar"] == expected


class TestSpeedAndProxy:
    @pytest.mark.parametrize("given, expected", [
        (0, 0), (1_048_576, 1_048_576), (5_242_880, 5_242_880),
        (777, 0),          # not one of the offered speeds
        ("1048576", 1_048_576),
        ("fast", 0), (None, 0),
    ])
    def test_only_the_offered_speeds_survive(self, given, expected):
        assert gui._normalize({"rate_limit": given})["rate_limit"] == expected

    @pytest.mark.parametrize("given, expected", [
        ("http://127.0.0.1:1080", "http://127.0.0.1:1080"),
        ("socks5://localhost:9050", "socks5://localhost:9050"),
        ("SOCKS5H://proxy.test:1080", "SOCKS5H://proxy.test:1080"),
        ("  http://127.0.0.1:1080  ", "http://127.0.0.1:1080"),
        ("127.0.0.1:1080", ""),        # no scheme: yt-dlp would not take it either
        ("http://", ""),
        ("javascript:alert(1)", ""),
        (None, ""),
    ])
    def test_a_proxy_has_to_look_like_an_address(self, given, expected):
        assert gui._normalize({"proxy": given})["proxy"] == expected

    @pytest.mark.parametrize("given, expected", [
        ("https://relay.example.workers.dev", "https://relay.example.workers.dev"),
        ("https://relay.test/spotify?key=1", "https://relay.test/spotify?key=1"),
        ("off", "off"),
        ("relay.test", ""), ("javascript:alert(1)", ""), (None, ""),
    ])
    def test_a_relay_is_an_address_or_off(self, given, expected):
        assert gui._normalize({"relay": given})["relay"] == expected

    def test_an_empty_relay_setting_means_the_programs_own(self, monkeypatch):
        import trackhound

        monkeypatch.setattr(trackhound, "SPOTIFY_RELAY", "https://own.test")
        assert [trackhound.relay_for(value) for value in ("", "off", "https://mine.test")] == [
            "https://own.test", "", "https://mine.test"]


class TestLanguageSetting:
    @pytest.mark.parametrize("given", ["ru", "en"])
    def test_a_chosen_language_is_kept(self, given):
        assert gui._normalize({"language": given})["language"] == given

    @pytest.mark.parametrize("given", ["system", "de", None, 7])
    def test_everything_else_is_settled_now(self, given):
        """The window offers two languages and no "as in the system" button, so
        anything else has to become one of the two before it is drawn."""
        assert gui._normalize({"language": given})["language"] in ("ru", "en")


class TestNamingSettings:
    @pytest.mark.parametrize("given, expected", [
        ("auto", "auto"), ("artist", "artist"), ("title", "title"), ("fancy", "auto"), (None, "auto"),
    ])
    def test_track_names(self, given, expected):
        assert gui._normalize({"track_name": given})["track_name"] == expected

    @pytest.mark.parametrize("given, expected", [
        ("flat", "flat"), ("nested", "nested"), ("album", "album"), ("deep", "flat"), (7, "flat"),
    ])
    def test_folder_names(self, given, expected):
        assert gui._normalize({"folder_name": given})["folder_name"] == expected


class TestProxyEnvironment:
    def test_setting_a_proxy_fills_the_variables(self, monkeypatch):
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        gui.use_proxy("http://127.0.0.1:1080")
        import os

        assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:1080"
        assert os.environ["http_proxy"] == "http://127.0.0.1:1080"

    def test_clearing_it_takes_them_away(self, monkeypatch):
        import os

        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1080")
        gui.use_proxy("")
        assert "HTTPS_PROXY" not in os.environ


class TestSettingsFile:
    def test_a_broken_file_does_not_stop_the_window(self, tmp_path, monkeypatch):
        broken = tmp_path / ".trackhound.json"
        broken.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(gui, "SETTINGS_FILE", broken)
        assert gui._load_settings()["format"] == "mp3"

    def test_check_only_is_never_remembered(self, tmp_path, monkeypatch):
        path = tmp_path / ".trackhound.json"
        monkeypatch.setattr(gui, "SETTINGS_FILE", path)
        gui._save_settings(gui._normalize({"dry_run": True, "format": "mp3"}))
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert "dry_run" not in saved and saved["format"] == "mp3"

    def test_a_saved_file_is_read_back(self, tmp_path, monkeypatch):
        path = tmp_path / ".trackhound.json"
        monkeypatch.setattr(gui, "SETTINGS_FILE", path)
        gui._save_settings(gui._normalize({"threads": 6, "theme": "dark"}))
        assert gui._load_settings()["threads"] == 6
        assert gui._load_settings()["theme"] == "dark"


class TestNewer:
    @pytest.mark.parametrize("candidate, current, expected", [
        ("1.2.3", "1.2.2", True),
        ("1.10.0", "1.9.9", True),
        ("2.0", "1.9.9", True),
        ("1.2.3", "1.2.3", False),
        ("1.2.2", "1.2.3", False),
        ("", "1.0.0", False),
        ("v1.3.0", "1.2.0", True),
    ])
    def test_version_comparison(self, candidate, current, expected):
        assert gui._newer(candidate, current) is expected


class TestReleaseAge:
    @pytest.mark.parametrize("version", ["", "nightly", "2026.13.40", "master"])
    def test_an_unreadable_version_has_no_age(self, version):
        assert gui._release_age(version) == -1

    def test_a_dated_version_is_measured_in_days(self, monkeypatch):
        import datetime

        class Today(datetime.date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 14)

        monkeypatch.setattr(gui.datetime, "date", Today)
        assert gui._release_age("2026.9.14") == 0
        assert gui._release_age("2026.8.19") == 26
        assert gui._release_age("2026.9.20") == 0  # a build from the future is not negative


class TestHistory:
    """What the Downloads list showed has to survive the window closing."""

    @pytest.fixture
    def data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gui.logs, "data_dir", lambda: tmp_path)
        return tmp_path

    def test_nothing_remembered_yet_reads_as_empty(self, data_dir):
        assert gui._load_history() == []

    def test_a_broken_file_is_not_fatal(self, data_dir):
        (data_dir / "history.json").write_text("{ not json", encoding="utf-8")
        assert gui._load_history() == []

    def test_an_entry_is_saved_and_read_back(self, data_dir):
        api = gui.Api()
        api._remember({"job": 1, "link": "https://x.test/a", "state": "queued"})
        assert gui._load_history() == [{"job": 1, "link": "https://x.test/a", "state": "queued"}]

    def test_download_does_not_deadlock_on_its_own_lock(self, data_dir, tmp_path, monkeypatch):
        """download() remembers the queued job while still holding self._lock;
        _remember() takes the same lock, so it must be reentrant or every
        download — by link or by name — hangs the window's Download button
        forever (it never gets back a reply to re-enable itself)."""
        import threading

        monkeypatch.setattr(gui, "SETTINGS_FILE", tmp_path / ".trackhound.json")
        api = gui.Api()
        monkeypatch.setattr(api, "_work", lambda: None)  # no real network search here

        result = []
        thread = threading.Thread(
            target=lambda: result.append(api.download(["Daft Punk - Discovery"], {})))
        thread.start()
        thread.join(timeout=5)

        assert not thread.is_alive(), "download() deadlocked instead of returning"
        assert result and result[0][0]["link"] == "Daft Punk - Discovery"

    def test_the_same_job_is_updated_not_duplicated(self, data_dir):
        api = gui.Api()
        api._remember({"job": 1, "link": "https://x.test/a", "state": "queued"})
        api._remember({"job": 1, "state": "done", "ok": 12})
        assert gui._load_history() == [
            {"job": 1, "link": "https://x.test/a", "state": "done", "ok": 12}]

    def test_only_the_last_hundred_are_kept(self, data_dir):
        api = gui.Api()
        for number in range(gui.HISTORY_LIMIT + 20):
            api._remember({"job": number, "state": "done"})
        history = gui._load_history()
        assert len(history) == gui.HISTORY_LIMIT
        assert history[0]["job"] == 20

    def test_job_numbering_carries_on_after_a_restart(self, data_dir):
        gui.Api()._remember({"job": 7, "state": "done"})
        assert gui.Api()._job_counter == 7

    def test_what_was_running_comes_back_as_stopped(self, data_dir):
        api = gui.Api()
        api._remember({"job": 1, "state": "running"})
        api._remember({"job": 2, "state": "queued"})
        api._remember({"job": 3, "state": "done"})
        states = [entry["state"] for entry in gui.Api().init()["history"]]
        assert states == ["cancelled", "cancelled", "done"]

    def test_clearing_the_finished_cards_forgets_them(self, data_dir):
        api = gui.Api()
        api._remember({"job": 1, "state": "done"})
        api._remember({"job": 2, "state": "running"})
        api.forget_history()
        assert [entry["job"] for entry in gui._load_history()] == [2]

    def test_an_unwritable_folder_costs_the_history_not_the_download(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gui.logs, "data_dir", lambda: tmp_path / "nope")

        def refuse(*args, **kwargs):
            raise OSError("read-only")

        monkeypatch.setattr("pathlib.Path.mkdir", refuse)
        gui.Api()._remember({"job": 1, "state": "done"})  # must not raise


class TestLibrary:
    def album(self, root, name, tracks=("01. One.m4a", "02. Two.m4a")):
        folder = root / name
        folder.mkdir()
        for track in tracks:
            (folder / track).write_bytes(b"x" * 1024)
        return folder

    def test_albums_and_single_tracks_are_listed(self, tmp_path):
        self.album(tmp_path, "Daft Punk - Discovery (2001)")
        (tmp_path / "Rick Astley - Never Gonna Give You Up.m4a").write_bytes(b"x" * 2048)
        items = gui.Api().library(str(tmp_path))
        by_title = {item["title"]: item for item in items}
        assert by_title["Discovery"]["artist"] == "Daft Punk"
        assert by_title["Discovery"]["year"] == "2001"
        assert by_title["Discovery"]["tracks"] == 2
        assert by_title["Discovery"]["album"] is True
        assert by_title["Never Gonna Give You Up"]["album"] is False

    def test_newest_comes_first(self, tmp_path):
        old = self.album(tmp_path, "A - Old (1999)")
        new = self.album(tmp_path, "B - New (2024)")
        import os
        os.utime(old / "01. One.m4a", (1_000_000_000, 1_000_000_000))
        os.utime(old / "02. Two.m4a", (1_000_000_000, 1_000_000_000))
        items = gui.Api().library(str(tmp_path))
        assert [item["title"] for item in items] == ["New", "Old"]
        assert new.exists()

    def test_folders_without_audio_are_skipped(self, tmp_path):
        (tmp_path / "Artwork").mkdir()
        (tmp_path / "Artwork" / "cover.jpg").write_bytes(b"x")
        assert gui.Api().library(str(tmp_path)) == []

    def test_a_missing_folder_is_empty_not_an_error(self, tmp_path):
        assert gui.Api().library(str(tmp_path / "nope")) == []

    def test_size_adds_up_every_track(self, tmp_path):
        self.album(tmp_path, "A - B (2020)", tracks=("01. a.m4a", "02. b.m4a", "03. c.m4a"))
        assert gui.Api().library(str(tmp_path))[0]["size"] == 3 * 1024

    def test_a_cover_is_reported_but_not_counted_as_a_track(self, tmp_path):
        folder = self.album(tmp_path, "A - B (2020)")
        (folder / "cover.jpg").write_bytes(b"x")
        item = gui.Api().library(str(tmp_path))[0]
        assert item["cover"] is True and item["tracks"] == 2

    def test_an_album_remembers_the_link_it_came_from(self, tmp_path):
        folder = self.album(tmp_path, "Daft Punk - Discovery (2001)")
        (folder / ".trackhound.json").write_text(
            json.dumps({"link": "https://open.spotify.com/album/x", "tracks": 14}),
            encoding="utf-8")
        item = gui.Api().library(str(tmp_path))[0]
        assert item["link"] == "https://open.spotify.com/album/x"
        assert item["expected"] == 14

    def test_an_album_from_elsewhere_has_no_link(self, tmp_path):
        self.album(tmp_path, "Daft Punk - Discovery (2001)")
        item = gui.Api().library(str(tmp_path))[0]
        assert item["link"] == "" and item["expected"] == 0

    def test_a_broken_marker_is_ignored(self, tmp_path):
        folder = self.album(tmp_path, "A - B (2020)")
        (folder / ".trackhound.json").write_text("{ not json", encoding="utf-8")
        assert gui.Api().library(str(tmp_path))[0]["link"] == ""

    def test_the_marker_is_not_counted_as_a_track(self, tmp_path):
        folder = self.album(tmp_path, "A - B (2020)")
        (folder / ".trackhound.json").write_text("{}", encoding="utf-8")
        assert gui.Api().library(str(tmp_path))[0]["tracks"] == 2

    @pytest.mark.parametrize("name, audio", [
        ("01. Track.m4a", True), ("01. Track.mp3", True), ("01. Track.opus", True),
        ("_part_7.m4a", False), ("cover.jpg", False), ("notes.txt", False),
    ])
    def test_what_counts_as_audio(self, tmp_path, name, audio):
        path = tmp_path / name
        path.write_bytes(b"x")
        assert gui._is_audio(path) is audio


class TestClipboard:
    """A Python built without Tcl/Tk must leave the buttons quiet. An exception
    here reaches the window as a rejected promise, and the js_api caller has
    nowhere to show it, so the button would look broken instead of empty."""

    @pytest.fixture
    def without_tkinter(self, monkeypatch):
        import builtins

        real = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name == "tkinter" or name.startswith("tkinter."):
                raise ImportError("no tkinter in this build")
            return real(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)

    def test_paste_answers_empty(self, without_tkinter):
        assert gui._clipboard_text() == ""

    def test_copy_answers_false(self, without_tkinter, monkeypatch):
        monkeypatch.setattr(gui.sys, "platform", "linux")  # the win32 path never reaches Tk
        # A desktop with neither pbcopy, wl-copy, xclip nor xsel: the runner
        # this test lands on decides which of them exist, so none of them do.
        monkeypatch.setattr(gui.shutil, "which", lambda name: None)
        assert gui._copy_to_clipboard("https://open.spotify.com/album/x") is False


class TestProfiles:
    """A profile remembers where the music goes and in what shape, nothing else."""

    def profile(self, **extra):
        return {"name": "D drive, mp3", "folder": "D:/Audio", "format": "mp3",
                "track_name": "artist", "folder_name": "nested", **extra}

    def test_a_profile_keeps_only_the_keys_it_is_for(self):
        saved = gui._normalize({"profiles": [self.profile(theme="light", proxy="http://x:1")]})
        assert saved["profiles"] == [{"name": "D drive, mp3", "folder": "D:/Audio",
                                      "format": "mp3", "track_name": "artist",
                                      "folder_name": "nested"}]

    def test_names_are_trimmed_and_unnamed_profiles_dropped(self):
        raw = [self.profile(name="  spaced  "), self.profile(name="   "), self.profile(name="")]
        assert [p["name"] for p in gui._normalize({"profiles": raw})["profiles"]] == ["spaced"]

    def test_a_name_is_taken_once_whatever_its_case(self):
        raw = [self.profile(name="Music"), self.profile(name="music", folder="E:/Other")]
        kept = gui._normalize({"profiles": raw})["profiles"]
        assert len(kept) == 1 and kept[0]["folder"] == "D:/Audio"

    def test_values_are_checked_the_way_the_live_settings_are(self):
        odd = gui._normalize({"profiles": [self.profile(format="flac", folder_name="sideways")]})
        assert odd["profiles"][0]["format"] == "mp3"
        assert odd["profiles"][0]["folder_name"] == "flat"

    @pytest.mark.parametrize("raw", ["not a list", 7, None, [None, 5, "x"]])
    def test_nonsense_leaves_no_profiles(self, raw):
        assert gui._normalize({"profiles": raw})["profiles"] == []

    def test_there_is_a_limit(self):
        many = [self.profile(name=f"profile {i}") for i in range(gui.PROFILE_LIMIT + 8)]
        assert len(gui._normalize({"profiles": many})["profiles"]) == gui.PROFILE_LIMIT


class TestUpdateInstaller:
    """The file is about to be run, so where it came from is checked first."""

    def api(self):
        api = gui.Api.__new__(gui.Api)
        api._events = __import__("queue").Queue()
        api._lock = __import__("threading").RLock()
        api._updating = False
        api._window = None
        return api

    def events(self, api):
        return [api._events.get_nowait() for _ in range(api._events.qsize())]

    @pytest.mark.parametrize("url", [
        "http://github.com/mojave333/trackhound/releases/download/v9/x-setup.exe",  # not TLS
        "https://github.com.evil.test/mojave333/trackhound/releases/download/v9/x-setup.exe",
        "https://example.com/mojave333/trackhound/releases/download/v9/x-setup.exe",
        "https://github.com/someone/else/releases/download/v9/x-setup.exe",
        "",
    ])
    def test_an_installer_from_anywhere_else_is_refused(self, url):
        api = self.api()
        api._install_update({"installer": url, "digest": "sha256:" + "0" * 64})
        states = [event["state"] for event in self.events(api)]
        assert states == ["error"]

    @pytest.mark.parametrize("platform, offered", [("win32", True), ("darwin", False), ("linux", False)])
    def test_only_windows_is_offered_the_installer(self, monkeypatch, platform, offered):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({"tag_name": "v99.0.0", "html_url": "https://github.test/release",
                                   "assets": [{"name": "Trackhound-v99.0.0-windows-x64-setup.exe",
                                               "browser_download_url": "https://github.test/setup.exe",
                                               "size": 1, "digest": "sha256:00"}]}).encode()

        monkeypatch.setattr(gui.urllib.request, "urlopen", lambda request, timeout: Response())
        monkeypatch.setattr(gui.sys, "platform", platform)
        release = self.api().latest_release()
        assert release["version"] == "99.0.0"
        assert bool(release["installer"]) is offered

    def test_a_release_without_a_hash_is_refused(self):
        api = self.api()
        api._install_update({
            "installer": f"https://github.com/{gui.REPO}/releases/download/v9/x-setup.exe",
            "digest": "",
        })
        assert [event["state"] for event in self.events(api)] == ["error"]

    def test_only_one_update_runs_at_a_time(self):
        api = self.api()
        api._updating = True
        assert api.install_update({}) is False

    def test_a_good_installer_is_checked_then_started(self, monkeypatch, tmp_path):
        """The whole path without a network or a real installer: the bytes are
        hashed, the hash is compared, and only then is anything run."""
        import hashlib
        import subprocess
        payload = b"pretend installer" * 5000
        digest = hashlib.sha256(payload).hexdigest()

        class Response:
            def __init__(self):
                self.left = [payload[i:i + 4096] for i in range(0, len(payload), 4096)]

            def read(self, _size):
                return self.left.pop(0) if self.left else b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        started, closed = [], []
        monkeypatch.setattr(gui.urllib.request, "urlopen", lambda *a, **k: Response())
        monkeypatch.setattr(gui.tempfile, "gettempdir", lambda: str(tmp_path))
        monkeypatch.setattr(subprocess, "Popen", lambda args, **k: started.append(args))
        monkeypatch.setattr(gui.threading, "Timer", lambda *a, **k: type(
            "T", (), {"start": lambda self: closed.append(True)})())

        api = self.api()
        api._install_update({
            "version": "9.9.9", "size": len(payload),
            "installer": f"https://github.com/{gui.REPO}/releases/download/v9.9.9/x-setup.exe",
            "digest": f"sha256:{digest}",
        })
        states = [event["state"] for event in self.events(api)]
        assert "checking" in states and states[-1] == "starting"
        assert started and Path(started[0][0]).exists()
        assert closed == [True]  # the window is asked to go away for the installer

    def test_an_installer_that_does_not_match_its_hash_is_thrown_away(self, monkeypatch, tmp_path):
        import subprocess

        class Response:
            def __init__(self):
                self.left = [b"not what was promised"]

            def read(self, _size):
                return self.left.pop(0) if self.left else b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        started = []
        monkeypatch.setattr(gui.urllib.request, "urlopen", lambda *a, **k: Response())
        monkeypatch.setattr(gui.tempfile, "gettempdir", lambda: str(tmp_path))
        monkeypatch.setattr(subprocess, "Popen", lambda args, **k: started.append(args))

        api = self.api()
        api._install_update({
            "version": "9.9.9", "size": 21,
            "installer": f"https://github.com/{gui.REPO}/releases/download/v9.9.9/x-setup.exe",
            "digest": "sha256:" + "0" * 64,
        })
        assert [event["state"] for event in self.events(api)][-1] == "error"
        assert not started  # nothing is run
        assert not list(tmp_path.glob("*.exe"))  # and the file does not linger


class TestInstallerCommand:
    """How an update is handed to the installer: the installer, not the
    window, decides when files are safe to replace."""

    def test_it_waits_for_this_process_and_reopens_the_program(self):
        command = gui.installer_command(Path("C:/Temp/Trackhound-9.9.9-setup.exe"), 4321)
        assert command[0].endswith("Trackhound-9.9.9-setup.exe")
        assert "/WAITPID=4321" in command and "/UPDATE=1" in command

    def test_it_runs_without_a_wizard_or_questions(self):
        command = gui.installer_command(Path("setup.exe"), 1)
        assert "/SILENT" in command and "/SUPPRESSMSGBOXES" in command and "/NORESTART" in command


class TestProgress:
    """The count shown outside the window: in its title and on the taskbar button."""

    class Window:
        def __init__(self):
            self.titles = []
            handle = type("Handle", (), {"ToInt64": lambda self: 77})()
            self.native = type("Form", (), {"Handle": handle})()

        def set_title(self, title):
            self.titles.append(title)

    def api(self, monkeypatch, platform="linux", taskbar=None):
        monkeypatch.setattr(gui.sys, "platform", platform)
        monkeypatch.setattr(gui, "_taskbar_progress", taskbar or (lambda *args: None))
        api = gui.Api()
        api._window = self.Window()
        return api

    def test_the_title_carries_the_percent_while_counting(self, monkeypatch):
        api = self.api(monkeypatch)
        for state in ("normal", "paused", "error"):
            api.progress(state, 0.426)
        assert api._window.titles == [f"42% · {gui.TITLE}"] * 3

    def test_with_nothing_to_count_the_title_is_the_name(self, monkeypatch):
        api = self.api(monkeypatch)
        api.progress("indeterminate")
        api.progress("none")
        assert api._window.titles == [gui.TITLE, gui.TITLE]

    def test_an_unknown_state_changes_nothing(self, monkeypatch):
        api = self.api(monkeypatch)
        api.progress("spinning", 0.5)
        assert api._window.titles == []

    def test_on_windows_the_taskbar_button_fills(self, monkeypatch):
        calls = []
        api = self.api(monkeypatch, "win32", lambda *args: calls.append(args))
        api.progress("paused", 0.5)
        api.progress("normal", 1.7)  # a count that ran over stays at the whole
        assert calls == [(77, gui.TASKBAR_STATES["paused"], 50), (77, gui.TASKBAR_STATES["normal"], 100)]

    def test_a_taskbar_that_refuses_does_not_fail_the_call(self, monkeypatch):
        def refuse(*args):
            raise OSError("no taskbar")

        api = self.api(monkeypatch, "win32", refuse)
        api.progress("normal", 0.1)
        assert api._window.titles == [f"10% · {gui.TITLE}"]


class TestCovers:
    """A cover reaches the window as a local address, not as the picture itself."""

    JPEG = b"\xff\xd8\xff\xe0cover"

    def fetch(self, url):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # a proxy set for downloads must not get in
        with opener.open(url, timeout=5) as response:
            return response.headers["Content-Type"], response.read()

    def test_the_cover_is_served_at_its_address(self, tmp_path):
        (tmp_path / "cover.jpg").write_bytes(self.JPEG)
        url = gui.Api().cover(str(tmp_path))
        assert url.startswith("http://127.0.0.1:")
        assert self.fetch(url) == ("image/jpeg", self.JPEG)

    def test_an_album_without_a_cover_has_no_address(self, tmp_path):
        assert gui.Api().cover(str(tmp_path)) is None

    def test_only_the_addresses_given_out_are_served(self, tmp_path):
        (tmp_path / "cover.jpg").write_bytes(self.JPEG)
        url = gui.Api().cover(str(tmp_path))
        with pytest.raises(urllib.error.HTTPError) as refused:
            self.fetch(url.split("/cover/")[0] + "/cover/guessed")
        assert refused.value.code == 404

    def test_a_replaced_cover_gets_a_new_address(self, tmp_path):
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(self.JPEG)
        api = gui.Api()
        first = api.cover(str(tmp_path))
        os.utime(cover, ns=(cover.stat().st_atime_ns, cover.stat().st_mtime_ns + 10**9))
        assert api.cover(str(tmp_path)) != first


class TestChoices:
    """What a person chose for a held-back track goes back to the downloader."""

    CANDIDATE = {"source": "soundcloud", "url": "https://soundcloud.com/a/b", "page_url": "https://soundcloud.com/a/b",
                 "title": "Resonance", "artists": "DV-i", "duration": 179.0, "score": 0.8, "source_name": "SoundCloud"}

    def api(self, monkeypatch):
        api = gui.Api()
        queued = []
        monkeypatch.setattr(api, "_enqueue", lambda link, settings, choices=None: queued.append(
            (link, settings["dry_run"], choices)) or {"job": 1, "link": link})
        return api, queued

    def test_the_chosen_match_is_queued_for_its_track(self, monkeypatch):
        api, queued = self.api(monkeypatch)
        assert api.download_choices("https://x.test/album", {"dry_run": True}, {"7": self.CANDIDATE}) == {
            "job": 1, "link": "https://x.test/album"}
        link, dry_run, choices = queued[0]
        assert (link, dry_run, choices["7"].url, choices["7"].score) == (
            "https://x.test/album", False, "https://soundcloud.com/a/b", 0.8)

    def test_nothing_usable_queues_nothing(self, monkeypatch):
        api, queued = self.api(monkeypatch)
        assert api.download_choices("https://x.test/album", {}, {"7": {"title": "no url"},
                                                                 "8": {**self.CANDIDATE, "url": "file:///C:/x"}}) is None
        assert queued == []

    @pytest.mark.parametrize("url, opened", [
        ("https://music.youtube.com/watch?v=x", True), ("https://soundcloud.com/a/b", True),
        ("https://evil.test/", False), ("file:///C:/Windows", False),
    ])
    def test_only_a_candidates_own_page_is_opened(self, monkeypatch, url, opened):
        seen = []
        monkeypatch.setattr(gui.webbrowser, "open", seen.append)
        assert gui.Api().open_page(url) is opened
        assert seen == ([url] if opened else [])


class TestTidy:
    """The library's tidy-up runs in the background and says how it went."""

    def run(self, monkeypatch, outcomes, tmp_path):
        monkeypatch.setattr(gui.logs, "data_dir", lambda: tmp_path)  # what was tidied is remembered there
        api = gui.Api()
        asked = []

        def tidy_up(path, artist, title, with_lyrics, stop):
            asked.append((path.name, artist, title, with_lyrics))
            outcome = outcomes[path.name]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(gui, "tidy_up", tidy_up)
        entries = [{"path": f"C:/Music/{name}", "artist": "A", "title": name} for name in outcomes]
        assert api.tidy(entries, {"lyrics": False})
        for _ in range(1000):  # up to 20 s: a busy machine runs threads late
            events = api.poll()
            done = [event for event in events if event.get("state") == "done"]
            if done:
                return api, asked, done[0]
            time.sleep(0.02)
        raise AssertionError("the tidy-up never finished")

    def test_the_counts_add_up_and_a_miss_is_named(self, monkeypatch, tmp_path):
        from trackhound.engine.tidy import Outcome
        api, asked, done = self.run(monkeypatch, {
            "One": Outcome(found=True, files=3, tags=9, cover=True, lyrics=2),
            "Two": Outcome(found=False),
            "Three": RuntimeError("odd folder"),
        }, tmp_path)
        assert [name for name, *_ in asked] == ["One", "Two", "Three"]
        assert asked[0][1:] == ("A", "One", False)
        assert (done["files"], done["covers"], done["lyrics"], done["missed"]) == (3, 1, 2, ["Two", "Three"])
        assert api.tidy([{"path": "C:/Music/One"}], {})  # over, so another may start

    def test_one_at_a_time(self, monkeypatch):
        api = gui.Api()
        api._tidying = gui.threading.Event()
        assert not api.tidy([{"path": "C:/Music/One"}], {})
        api.stop_tidy()
        assert api._tidying.is_set()


class TestBackground:
    """Closing the window hides it while there is work; notices say how downloads went."""

    class FakeTray:
        shown = True

        def __init__(self):
            self.said = []

        def notify(self, title, text):
            self.said.append((title, text))
            return True

        def close(self):
            self.shown = False

    def api(self, monkeypatch, watched=(), tray_on=True):
        api = gui.Api()
        api._tray = self.FakeTray()
        api._watched = list(watched)
        monkeypatch.setattr(gui.tray, "SUPPORTED", True)
        monkeypatch.setattr(gui, "_load_settings", lambda: gui._normalize({"tray": tray_on}))
        return api

    def test_closing_with_watched_playlists_only_hides_the_window(self, monkeypatch):
        api = self.api(monkeypatch, watched=[{"link": "x"}])
        hidden = []
        monkeypatch.setattr(api, "_hide", lambda: hidden.append(True))
        assert api._on_closing() is False
        for _ in range(500):  # up to 5 s: the hiding runs on a thread of its own
            if hidden:
                break
            time.sleep(0.01)
        assert hidden

    def test_closing_with_nothing_to_do_ends_the_program(self, monkeypatch):
        api = self.api(monkeypatch)
        assert api._on_closing() is None and not api._tray.shown

    def test_the_tray_switched_off_ends_the_program_too(self, monkeypatch):
        api = self.api(monkeypatch, watched=[{"link": "x"}], tray_on=False)
        assert api._on_closing() is None

    def test_a_close_for_an_update_is_never_caught(self, monkeypatch):
        api = self.api(monkeypatch, watched=[{"link": "x"}])
        api._quitting = True
        assert api._on_closing() is None

    def test_a_notice_waits_for_the_window_to_be_out_of_use(self, monkeypatch):
        api = self.api(monkeypatch)
        api._finished = [{"title": "In Rainbows", "state": "done", "ok": 10, "skipped": 0, "failed": 0, "doubtful": 0}]
        api._announce()
        assert api._tray.said == []  # looked at: the card says it already
        api._finished = [{"title": "In Rainbows", "state": "done", "ok": 10, "skipped": 0, "failed": 0, "doubtful": 0}]
        api.focus(False)
        api._announce()
        assert api._tray.said == [("Скачано: In Rainbows", "скачано: 10")]
        assert api._finished == []

    def test_many_releases_make_one_notice(self):
        title, text = gui._notice([
            {"title": "A", "state": "done", "ok": 3, "skipped": 1, "failed": 1, "doubtful": 0},
            {"title": "B", "state": "done", "ok": 2, "skipped": 0, "failed": 0, "doubtful": 1},
            {"title": "C", "state": "error", "message": "Spotify не отдал"},
        ])
        assert title == "Загрузки завершены: 3"
        assert text == "скачано: 5, уже были: 1, не скачалось: 1, ждут выбора: 1, ссылок с ошибкой: 1"

    def test_a_release_not_all_downloaded_says_so(self):
        assert gui._notice([{"title": "A", "state": "done", "ok": 3, "skipped": 0, "failed": 2,
                             "doubtful": 0}])[0] == "Скачано не всё: A"
        assert gui._notice([{"title": "A", "state": "error", "message": "нет сети"}]) == ("Не скачалось: A", "нет сети")
