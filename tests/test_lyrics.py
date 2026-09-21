"""Lyrics from LRCLIB: found, cleaned up and written where players look for them."""

import contextlib
import io
import json
import subprocess
import urllib.error
import urllib.parse

import pytest
from mutagen import File as MutagenFile

from trackhound.engine import downloader, lyrics
from trackhound.engine.models import Album, Track

FFMPEG = downloader.find_tool("ffmpeg")
CODECS = {"m4a": ["-c:a", "aac", "-b:a", "96k"], "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
          "opus": ["-c:a", "libopus", "-b:a", "64k"]}
SYNCED = "[00:30.75] One more time\n[00:33.18] We're gonna celebrate"


def track(title="One More Time", artists="Daft Punk", duration=320):
    return Track(id="1", title=title, artists=artists, duration=duration, track_number=1)


def album():
    return Album(id="a", name="Discovery", artist="Daft Punk")


@pytest.fixture
def lrclib(monkeypatch):
    """LRCLIB as a dict of answers by endpoint; what is asked is kept."""
    answers, asked = {}, []

    def urlopen(request, timeout):
        url = urllib.parse.urlsplit(request.full_url)
        endpoint = url.path.rsplit("/", 1)[-1]
        asked.append((endpoint, dict(urllib.parse.parse_qsl(url.query))))
        answer = answers.get(endpoint)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        return contextlib.closing(io.BytesIO(json.dumps(answer).encode()))

    monkeypatch.setattr(lyrics.urllib.request, "urlopen", urlopen)
    return answers, asked


class TestFind:
    def test_the_exact_song_is_asked_for(self, lrclib):
        answers, asked = lrclib
        answers["get"] = {"plainLyrics": "One more time", "syncedLyrics": SYNCED, "instrumental": False}
        found = lyrics.find(track(), album())
        assert (found.plain, found.synced) == ("One more time", SYNCED)
        assert asked == [("get", {"artist_name": "Daft Punk", "track_name": "One More Time",
                                  "album_name": "Discovery", "duration": "320"})]

    def test_a_remaster_is_asked_for_by_its_plain_title_and_first_artist(self, lrclib):
        answers, asked = lrclib
        answers["get"] = {"plainLyrics": "Give me fuel", "syncedLyrics": "", "instrumental": False}
        lyrics.find(track("Fuel - Remastered", "Metallica, Guest"), album())
        assert (asked[0][1]["track_name"], asked[0][1]["artist_name"]) == ("Fuel", "Metallica")

    def test_a_miss_is_searched_for_and_the_length_decides(self, lrclib):
        answers, asked = lrclib
        answers["search"] = [
            {"duration": 246, "plainLyrics": "a live take", "syncedLyrics": ""},
            {"duration": 321, "plainLyrics": "", "syncedLyrics": SYNCED},
        ]
        found = lyrics.find(track(), album())
        assert [endpoint for endpoint, _ in asked] == ["get", "search"]
        # Only synced ones there: the plain text is made from them
        assert found.plain == "One more time\nWe're gonna celebrate"

    def test_an_instrumental_has_none(self, lrclib):
        lrclib[0]["get"] = {"instrumental": True, "plainLyrics": None, "syncedLyrics": None}
        assert lyrics.find(track(), album()) is None

    def test_lrclib_out_of_reach_costs_nothing_but_the_lyrics(self, lrclib):
        lrclib[0]["get"] = urllib.error.URLError("unreachable")
        assert lyrics.find(track(), album()) is None


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
@pytest.mark.parametrize("ext, read", [
    ("mp3", lambda tags: str(tags.getall("USLT")[0])),
    ("m4a", lambda tags: tags["\xa9lyr"][0]),
    ("opus", lambda tags: tags["lyrics"][0]),
])
def test_the_words_go_into_every_format(tmp_path, ext, read):
    path = tmp_path / f"song.{ext}"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=duration=1", *CODECS[ext], str(path)], check=True)
    downloader._write_tags(path, album(), track(), None, "One more time\nWe're gonna celebrate")
    assert read(MutagenFile(path).tags) == "One more time\nWe're gonna celebrate"


def test_synced_lyrics_lie_beside_the_track_under_its_name(tmp_path):
    song = tmp_path / "01. One More Time.mp3"
    downloader._write_lrc(song, SYNCED)
    assert (tmp_path / "01. One More Time.lrc").read_text(encoding="utf-8") == SYNCED + "\n"


def test_a_server_error_is_asked_again_once(lrclib, monkeypatch):
    answers, asked = lrclib
    monkeypatch.setattr(lyrics.time, "sleep", lambda seconds: None)
    replies = iter([urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None),
                    {"plainLyrics": "Give me fuel", "syncedLyrics": "", "instrumental": False}])

    def urlopen(request, timeout):
        asked.append(request.full_url)
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return contextlib.closing(io.BytesIO(json.dumps(reply).encode()))

    monkeypatch.setattr(lyrics.urllib.request, "urlopen", urlopen)
    assert lyrics.find(track("Fuel"), album()).plain == "Give me fuel"
    assert len(asked) == 2
