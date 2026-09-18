"""Settings, the update check and the library listing behind the window."""

import json
from pathlib import Path

import pytest

from trackhound import gui
from trackhound.engine.downloader import DEFAULT_OUTPUT_DIR


class TestNormalize:
    def test_empty_settings_get_the_defaults(self):
        settings = gui._normalize({})
        assert settings == {
            "folder": str(DEFAULT_OUTPUT_DIR),
            "format": "m4a",
            "threads": 3,
            "theme": "system",
            "dry_run": False,
            "cookies_browser": "",
            "rate_limit": 0,
            "proxy": "",
            "language": gui.resolve("system"),  # settled on load, never left as "system"
            "track_name": "auto",
            "folder_name": "flat",
            "sidebar": 64,
            "replaygain": False,
            "profiles": [],
        }

    @pytest.mark.parametrize("given, expected", [(0, 1), (1, 1), (4, 4), (8, 8), (99, 8), ("5", 5)])
    def test_threads_stay_within_reach(self, given, expected):
        assert gui._normalize({"threads": given})["threads"] == expected

    @pytest.mark.parametrize("given", ["", None, "many", 3.5])
    def test_unreadable_threads_fall_back_to_three(self, given):
        assert gui._normalize({"threads": given})["threads"] in (1, 3)

    @pytest.mark.parametrize("given, expected", [("mp3", "mp3"), ("opus", "opus"),
                                                 ("flac", "m4a"), (None, "m4a")])
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
        assert gui._load_settings()["format"] == "m4a"

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
        assert odd["profiles"][0]["format"] == "m4a"
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
