"""Watched releases: the list itself, and how a check is queued."""

import queue
import threading
from pathlib import Path

import pytest

from trackhound import gui, watch

SETTINGS = {"folder": "D:/Music", "format": "mp3", "track_name": "artist", "folder_name": "nested",
            "theme": "dark", "proxy": ""}


class TestList:
    def test_watching_keeps_only_where_and_how_the_music_went(self):
        entries = watch.add([], "https://x/playlist", "Mix", "Spotify", SETTINGS, now=100)
        assert entries[0]["settings"] == {"folder": "D:/Music", "format": "mp3",
                                          "track_name": "artist", "folder_name": "nested"}

    def test_a_link_just_downloaded_is_not_due_at_once(self):
        entries = watch.add([], "https://x/p", "Mix", "", SETTINGS, now=1000)
        assert watch.due(entries, now=1000 + watch.CHECK_EVERY - 1) == []
        assert len(watch.due(entries, now=1000 + watch.CHECK_EVERY)) == 1

    def test_watching_again_refreshes_instead_of_doubling(self):
        entries = watch.add([], "https://x/p", "Old", "", SETTINGS, now=1)
        entries = watch.add(entries, "https://x/p", "New", "", SETTINGS, now=2)
        assert [entry["title"] for entry in entries] == ["New"]

    def test_removing(self):
        entries = watch.add([], "https://x/a", "A", "", SETTINGS)
        entries = watch.add(entries, "https://x/b", "B", "", SETTINGS)
        assert [entry["link"] for entry in watch.remove(entries, "https://x/a")] == ["https://x/b"]

    def test_there_is_a_limit(self):
        entries = []
        for i in range(watch.WATCH_LIMIT + 5):
            entries = watch.add(entries, f"https://x/{i}", str(i), "", SETTINGS)
        assert len(entries) == watch.WATCH_LIMIT

    def test_the_file_round_trips_and_nonsense_is_dropped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch.logs, "data_dir", lambda: tmp_path)
        good = watch.add([], "https://x/p", "Mix", "", SETTINGS)
        watch.save(good + [{"link": ""}, {"no": "link"}, "text"])
        assert [entry["link"] for entry in watch.load()] == ["https://x/p"]

    def test_a_damaged_file_means_an_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch.logs, "data_dir", lambda: tmp_path)
        (tmp_path / watch.WATCH_NAME).write_text("{not json", encoding="utf-8")
        assert watch.load() == []


class TestChecks:
    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watch.logs, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(gui, "_save_history", lambda history: None)
        monkeypatch.setattr(gui, "_load_settings", lambda: gui._normalize({"folder": "C:/Now"}))
        api = gui.Api.__new__(gui.Api)
        api._events = queue.Queue()
        api._jobs = queue.Queue()
        api._lock = threading.RLock()
        api._history = [{"job": 7, "link": "https://x/playlist", "state": "done", "title": "Mix",
                         "service": "Spotify", "music_folder": "D:/Music",
                         "folder": "D:/Music/Artist - Mix", "format": "mp3",
                         "track_name": "artist", "folder_name": "nested", "ok": 12}]
        api._job_counter = 7
        api._worker = object()  # no real downloads start
        api._watched = []
        return api

    def test_watching_a_card_uses_the_settings_it_was_downloaded_with(self, api):
        listed = api.watch(7)
        assert [entry["title"] for entry in listed] == ["Mix"]
        assert api._watched[0]["settings"]["folder"] == "D:/Music"  # not today's C:/Now

    def test_a_dry_run_card_cannot_be_watched(self, api):
        api._history[0]["dry_run"] = True
        assert api.watch(7) == []

    def test_a_check_goes_to_the_music_folder_not_inside_the_album(self, api):
        """The card's folder is the album's own; starting a check from it put a
        second copy of the album inside the first."""
        api.watch(7)
        api.check_watched(force=True)
        _, _, options = api._jobs.get_nowait()
        assert options.output_dir == Path("D:/Music")

    def test_a_check_queues_the_same_link_into_the_same_folder(self, api):
        api.watch(7)
        api.check_watched(force=True)
        job, link, options = api._jobs.get_nowait()
        assert link == "https://x/playlist"
        assert options.output_dir == Path("D:/Music")
        assert options.audio_format == "mp3" and options.folder_name == "nested"
        assert not options.dry_run

    def test_the_window_is_told_to_draw_a_card_for_it(self, api):
        api.watch(7)
        api.check_watched(force=True)
        added = [event for event in list(api._events.queue) if event["type"] == "added"]
        assert len(added) == 1 and added[0]["link"] == "https://x/playlist"

    def test_nothing_is_queued_before_it_is_due(self, api):
        api.watch(7)  # watched just now, so not due for twelve hours
        api.check_watched()
        assert api._jobs.empty()

    def test_the_list_reports_how_the_last_check_went(self, api):
        api.watch(7)
        api.check_watched(force=True)
        job = api._watched[0]["job"]
        assert api._finish_watch_check(job, "done", {"ok": 3, "skipped": 9, "failed": 0}) is False
        assert api.watched()[0]["added_tracks"] == 3

    def test_a_check_that_found_nothing_leaves_no_card_behind(self, api):
        api.watch(7)
        api.check_watched(force=True)
        job = api._watched[0]["job"]
        assert api._finish_watch_check(job, "done", {"ok": 0, "skipped": 12, "failed": 0}) is True
        assert all(entry.get("job") != job for entry in api._history)
        assert api.watched()[0]["added_tracks"] == 0

    def test_a_check_with_failures_stays_visible(self, api):
        api.watch(7)
        api.check_watched(force=True)
        job = api._watched[0]["job"]
        assert api._finish_watch_check(job, "done", {"ok": 0, "skipped": 10, "failed": 2}) is False

    def test_an_ordinary_download_is_not_mistaken_for_a_check(self, api):
        assert api._finish_watch_check(7, "done", {"ok": 0, "skipped": 0, "failed": 0}) is False

    def test_unwatching(self, api):
        api.watch(7)
        assert api.unwatch("https://x/playlist") == []
