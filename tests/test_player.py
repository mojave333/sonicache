"""The built-in player: files streamed with seeking, and the lyrics beside them."""

import subprocess
import urllib.error
import urllib.request

import pytest

from trackhound import gui
from trackhound.engine import downloader
from trackhound.engine.lyrics import Lyrics
from trackhound.engine.models import Album, Track

FFMPEG = downloader.find_tool("ffmpeg")
SYNCED = "[00:00.50] Hello\n[00:01.00] World"

pytestmark = pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")


@pytest.fixture
def song(tmp_path):
    path = tmp_path / "01. Song.mp3"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2",
                    "-c:a", "libmp3lame", "-b:a", "64k", str(path)], check=True)
    track = Track(id="1", title="Song", artists="Band", duration=2, track_number=1)
    downloader._write_tags(path, Album(id="a", name="Record", artist="Band", tracks=[track]), track, None)
    return path


def fetch(url, byte_range=None):
    request = urllib.request.Request(url, headers={"Range": byte_range} if byte_range else {})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, dict(response.headers), response.read()


class TestStream:
    def test_a_file_comes_whole_and_by_the_part_asked_for(self, song):
        api = gui.Api()
        info = api.play(str(song))
        assert (info["title"], info["artists"], info["album"]) == ("Song", "Band", "Record")
        whole = song.read_bytes()
        status, headers, body = fetch(info["url"])
        assert (status, headers["Content-Type"], headers["Accept-Ranges"], body) == (200, "audio/mpeg", "bytes", whole)
        status, headers, body = fetch(info["url"], "bytes=100-199")
        assert (status, headers["Content-Range"], body) == (206, f"bytes 100-199/{len(whole)}", whole[100:200])
        status, headers, body = fetch(info["url"], "bytes=500-")
        assert body == whole[500:]
        assert fetch(info["url"], "bytes=-50")[2] == whole[-50:]

    def test_a_range_past_the_end_is_refused(self, song):
        url = gui.Api().play(str(song))["url"]
        with pytest.raises(urllib.error.HTTPError) as refused:
            fetch(url, f"bytes={song.stat().st_size + 10}-")
        assert refused.value.code == 416

    def test_only_what_the_window_asked_for_is_served(self, song):
        api = gui.Api()
        url = api.play(str(song))["url"]
        with pytest.raises(urllib.error.HTTPError) as refused:
            fetch(url.rsplit("/", 1)[0] + "/not-a-token")
        assert refused.value.code == 404
        # A cover's token does not open the file as audio, nor the other way round
        cover = api.cover(str(song)).split("?")[0]
        with pytest.raises(urllib.error.HTTPError):
            fetch(cover.replace("/cover/", "/audio/"))

    def test_anything_but_music_gets_no_address(self, tmp_path):
        text = tmp_path / "notes.txt"
        text.write_text("secret", encoding="utf-8")
        assert gui.Api().play(str(text)) is None


class TestLyrics:
    def test_synced_ones_come_from_the_lrc_beside(self, song):
        song.with_suffix(".lrc").write_text(SYNCED + "\n", encoding="utf-8")
        assert gui.Api().lyrics(str(song)) == {"synced": SYNCED, "plain": "", "source": "file"}

    def test_plain_ones_come_from_the_tags(self, song):
        track = Track(id="1", title="Song", artists="Band", duration=2, track_number=1)
        downloader._write_tags(song, Album(id="a", name="Record", artist="Band", tracks=[track]), track, None,
                               "Hello\nWorld")
        assert gui.Api().lyrics(str(song))["plain"] == "Hello\nWorld"

    def test_a_file_without_any_asks_lrclib(self, song, monkeypatch):
        asked = []
        monkeypatch.setattr(gui.lyrics, "find", lambda track, album: asked.append((track.title, album.name))
                            or Lyrics("Hello", SYNCED))
        assert gui.Api().lyrics(str(song)) == {"synced": SYNCED, "plain": "Hello", "source": "lrclib"}
        assert asked == [("Song", "Record")]
        assert not song.with_suffix(".lrc").exists()  # shown, not written: the file is left as it was
