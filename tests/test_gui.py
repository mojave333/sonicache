"""Settings, the update check and the library listing behind the window."""

import json

import pytest

from trackhound import gui
from trackhound.downloader import DEFAULT_OUTPUT_DIR


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
            "sidebar": 64,
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

    @pytest.mark.parametrize("name, audio", [
        ("01. Track.m4a", True), ("01. Track.mp3", True), ("01. Track.opus", True),
        ("_part_7.m4a", False), ("cover.jpg", False), ("notes.txt", False),
    ])
    def test_what_counts_as_audio(self, tmp_path, name, audio):
        path = tmp_path / name
        path.write_bytes(b"x")
        assert gui._is_audio(path) is audio
