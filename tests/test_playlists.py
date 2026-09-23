"""Playlists of one's own, .m3u8 both ways, and what has been listened to."""

import os

import pytest

from trackhound import gui, playlists


def song(path, title="Song", artists="Band", album="Record", duration=200):
    return {"path": str(path), "title": title, "artists": artists, "album": album, "album_artist": artists,
            "duration": duration}


class TestPlaylists:
    def test_a_playlist_is_kept_with_what_its_rows_show(self, tmp_path):
        first = playlists.save(None, "Road", [song(tmp_path / "a.mp3", "A"), {"no": "path"}])
        assert first["name"] == "Road" and [track["title"] for track in first["tracks"]] == ["A"]
        again = playlists.save(first["id"], "Road trip", first["tracks"] + [song(tmp_path / "b.mp3", "B")])
        assert again["id"] == first["id"]
        (kept,) = playlists.load()
        assert kept["name"] == "Road trip" and [track["title"] for track in kept["tracks"]] == ["A", "B"]

    def test_tracks_are_added_at_the_end_and_a_playlist_goes_whole(self, tmp_path):
        entry = playlists.save(None, "Mix", [song(tmp_path / "a.mp3", "A")])
        playlists.add(entry["id"], [song(tmp_path / "b.mp3", "B")])
        assert [track["title"] for track in playlists.load()[0]["tracks"]] == ["A", "B"]
        assert playlists.add("gone", [song(tmp_path / "c.mp3")]) is None
        playlists.delete(entry["id"])
        assert playlists.load() == []

    def test_a_track_without_a_title_is_named_by_its_file(self, tmp_path):
        assert playlists.track({"path": str(tmp_path / "07. Seven.mp3")})["title"] == "07. Seven"

    def test_a_broken_file_is_no_playlists(self):
        playlists.playlists_file().parent.mkdir(parents=True, exist_ok=True)
        playlists.playlists_file().write_text("{not json", encoding="utf-8")
        assert playlists.load() == []


class TestM3u8:
    def test_paths_on_the_same_drive_are_written_relative_to_the_file(self, tmp_path):
        entry = {"name": "Mix", "tracks": [playlists.track(song(tmp_path / "Music" / "Band" / "01. A.mp3", "A"))]}
        text = playlists.to_m3u8(entry, tmp_path / "Music" / "Mix.m3u8")
        assert text.splitlines() == ["#EXTM3U", "#PLAYLIST:Mix", "#EXTINF:200,Band - A", os.path.join("Band", "01. A.mp3")]

    def test_one_read_back_names_its_tracks_from_the_extinf_lines(self, tmp_path):
        text = "﻿#EXTM3U\n#PLAYLIST:Night\n#EXTINF:215,Band - Song\nsub/one.mp3\n" \
               "#EXTINF:-1,Stream\nhttps://radio.test/live\n" + str(tmp_path / "two.flac") + "\n"
        found = playlists.from_m3u8(tmp_path / "list.m3u8", text)
        assert found["name"] == "Night"
        assert [(track["path"], track["title"], track["artists"], track["duration"]) for track in found["tracks"]] == [
            (os.path.normpath(str(tmp_path / "sub" / "one.mp3")), "Song", "Band", 215),
            (os.path.normpath(str(tmp_path / "two.flac")), "two", "", 0),  # a web address is no file of the library
        ]

    def test_an_imported_list_becomes_a_playlist(self, tmp_path):
        (tmp_path / "list.m3u").write_text("#EXTINF:10,Band - Song\nsong.mp3\n", encoding="cp1251")
        entry = gui.Api()._import_playlist(tmp_path / "list.m3u")
        assert entry["name"] == "list" and entry["count"] == 1
        assert playlists.load()[0]["tracks"][0]["title"] == "Song"


class TestListens:
    def test_every_listen_is_a_line_and_counts(self, tmp_path):
        a, b = song(tmp_path / "a.mp3", "A"), song(tmp_path / "b.mp3", "B")
        playlists.record(a, 100)
        playlists.record(b, 200)
        playlists.record(a, 300)
        assert playlists.counts() == {a["path"]: 2, b["path"]: 1}
        assert [(entry["title"], entry["time"]) for entry in playlists.plays()] == [("A", 100), ("B", 200), ("A", 300)]

    def test_a_line_cut_short_is_skipped(self, tmp_path):
        playlists.record(song(tmp_path / "a.mp3"), 100)
        with playlists.plays_file().open("a", encoding="utf-8") as lines:
            lines.write('{"path": "half')
        assert len(playlists.plays()) == 1

    def test_recent_and_most_played(self, tmp_path):
        a, b, c = (song(tmp_path / f"{name}.mp3", name) for name in "abc")
        for listen, when in ((a, 1), (b, 2), (a, 3), (c, 4), (b, 5), (a, 6)):
            playlists.record(listen, when)
        assert [entry["title"] for entry in playlists.smart("recent")] == ["a", "b", "c"]
        top = playlists.smart("top")
        assert [(entry["title"], entry["plays"]) for entry in top] == [("a", 3), ("b", 2), ("c", 1)]

    def test_the_api_says_which_files_are_gone(self, tmp_path):
        there = tmp_path / "there.mp3"
        there.write_bytes(b"")
        playlists.record(song(there, "There"), 1)
        playlists.record(song(tmp_path / "gone.mp3", "Gone"), 2)
        recent = gui.Api().playlist("recent")
        assert [(track["title"], track["missing"]) for track in recent["tracks"]] == [("Gone", True), ("There", False)]
        assert recent["smart"] and recent["count"] == 2

    def test_a_listen_is_written_and_sent_on(self, tmp_path):
        api = gui.Api()
        sent = []
        api._scrobbler.scrobble = sent.append
        api.listened(song(tmp_path / "a.mp3", "A"))
        assert api.play_counts() == {str(tmp_path / "a.mp3"): 1}
        assert [listen["title"] for listen in sent] == ["A"]


class TestPlayerSettings:
    def test_the_equaliser_is_kept_within_its_range(self):
        eq = gui._normalize({"eq": {"on": 1, "bands": [20, -30, "x", 3.25], "preset": "rock"}})["eq"]
        assert eq == {"on": True, "bands": [12, -12, 0, 3.2, 0, 0, 0, 0, 0, 0], "preset": "rock"}

    @pytest.mark.parametrize("given, expected", [(None, 0), (-3, 0), (5, 5), (40, 12), ("x", 0)])
    def test_the_crossfade_is_whole_seconds_up_to_twelve(self, given, expected):
        assert gui._normalize({"crossfade": given})["crossfade"] == expected

    def test_albums_play_without_gaps_unless_told_otherwise(self):
        assert gui._normalize({})["gapless"] is True
        assert gui._normalize({"gapless": False})["gapless"] is False

