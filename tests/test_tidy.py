"""Tidying up the library: the tags, cover and lyrics older files lack, and nothing else."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TIT2, TXXX

from trackhound.engine import downloader, tidy
from trackhound.engine.lyrics import Lyrics
from trackhound.engine.models import Album, Release, Track

FFMPEG = downloader.find_tool("ffmpeg")
CODECS = {"m4a": ["-c:a", "aac", "-b:a", "96k"], "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
          "opus": ["-c:a", "libopus", "-b:a", "64k"]}
JPEG = b"\xff\xd8\xff" + b"cover" * 20
SYNCED = "[00:00.50] Hello"

pytestmark = pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")


def silence(path: Path, seconds: int = 1) -> Path:
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=duration={seconds}", *CODECS[path.suffix[1:]], str(path)], check=True)
    return path


def release(kind="album", genre="") -> Release:
    tracks = [Track(id=str(n), title=title, artists="Radiohead", duration=1, track_number=n)
              for n, title in ((1, "15 Step"), (2, "Bodysnatchers"), (3, "Nude"))]
    album = Album(id="a", name="In Rainbows", artist="Radiohead", release_date="2007-10-10", kind=kind,
                  cover_url="https://cover.test/a.jpg", tracks=tracks, genre=genre)
    return Release(album, tracks)


def old_file(folder: Path, name: str, ext: str = "mp3", **tags) -> Path:
    """A file the way an older version or another program left it: a few tags, no more."""
    path = silence(folder / f"{name}.{ext}")
    track = Track(id="x", title=tags.get("title", ""), artists=tags.get("artist", ""), duration=1,
                  track_number=tags.get("number", 0))
    album = Album(id="x", name=tags.get("album", ""), artist=tags.get("albumartist", ""), tracks=[track])
    downloader._write_tags(path, album, track, None)
    return path


@pytest.fixture
def services(monkeypatch):
    """The catalogues, the cover and LRCLIB, answering from here; what was asked is kept."""
    asked = {"resolve": [], "find_album": [], "genre": [], "cover": [], "lyrics": []}
    answers = {"release": release(), "genre": "Art Rock"}

    def resolve(link):
        asked["resolve"].append(link)
        return answers["release"]

    def find_album(artist, title, service):
        asked["find_album"].append((artist, title))
        return answers["release"]

    def find_genre(artist, title, known=""):
        asked["genre"].append((artist, title))
        return answers["genre"]

    def fetch_cover(url, say):
        asked["cover"].append(url)
        return JPEG

    def find_lyrics(track, album):
        asked["lyrics"].append(track.title)
        return Lyrics("Hello", SYNCED)

    monkeypatch.setattr(tidy.sources, "resolve", resolve)
    monkeypatch.setattr(tidy.sources, "find_album", find_album)
    monkeypatch.setattr(tidy.sources, "find_genre", find_genre)
    monkeypatch.setattr(tidy, "fetch_cover", fetch_cover)
    monkeypatch.setattr(tidy.lyrics, "find", find_lyrics)
    return answers, asked


class TestFill:
    @pytest.mark.parametrize("ext", ["mp3", "m4a", "opus"])
    def test_only_the_missing_tags_are_written(self, tmp_path, ext):
        path = old_file(tmp_path, "song", ext, title="Nude (fixed by hand)", artist="Radiohead")
        album = release(genre="Art Rock").album
        written = downloader._write_tags(path, album, album.tracks[2], JPEG, "Hello", fill=True)
        tags = downloader.read_tags(path)
        assert tags["title"] == "Nude (fixed by hand)"
        assert (tags["genre"], tags["album"], tags["track"], tags["cover"], tags["lyrics"]) == (
            "Art Rock", "In Rainbows", 3, True, True)
        assert "title" not in written and {"genre", "cover", "lyrics", "track"} <= set(written)

    def test_a_file_with_everything_is_not_touched(self, tmp_path):
        album = release(genre="Art Rock").album
        path = silence(tmp_path / "song.mp3")
        downloader._write_tags(path, album, album.tracks[0], JPEG, "Hello")
        os.utime(path, (1_000_000_000, 1_000_000_000))
        assert downloader._write_tags(path, album, album.tracks[0], JPEG, "Hello", fill=True) == []
        assert path.stat().st_mtime == 1_000_000_000

    def test_an_id3v24_file_stays_v24_with_the_frames_it_had(self, tmp_path):
        path = silence(tmp_path / "song.mp3")
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Nude"))
        tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text="-6.50 dB"))
        tags.save(path, v2_version=4)
        album = release(genre="Art Rock").album
        downloader._write_tags(path, album, album.tracks[2], None, fill=True)
        saved = ID3(path)
        assert saved.version >= (2, 4, 0)
        assert str(saved["TXXX:REPLAYGAIN_TRACK_GAIN"]) == "-6.50 dB"
        assert str(saved["TCON"]) == "Art Rock"

    @pytest.mark.parametrize("ext", ["mp3", "m4a", "opus"])
    def test_what_a_download_wrote_reads_back(self, tmp_path, ext):
        album = release(kind="playlist", genre="Art Rock").album
        album.tracks[1].isrc = "GBSTK0700002"
        path = silence(tmp_path / f"song.{ext}")
        downloader._write_tags(path, album, album.tracks[1], JPEG, "Hello")
        tags = downloader.read_tags(path)
        assert tags == {"duration": pytest.approx(1, abs=0.1), "title": "Bodysnatchers", "artist": "Radiohead",
                        "album": "In Rainbows", "albumartist": "Разные исполнители", "compilation": True,
                        "track": 2, "disc": 1, "date": tags["date"], "genre": "Art Rock", "lyrics": True,
                        "cover": True, "isrc": "GBSTK0700002"}
        assert tags["date"].startswith("2007")


class TestTidy:
    def test_an_album_folder_gets_its_genre_cover_and_lyrics_from_its_link(self, tmp_path, services):
        answers, asked = services
        folder = tmp_path / "Radiohead - In Rainbows (2007)"
        folder.mkdir()
        (folder / downloader.MARKER_NAME).write_text(json.dumps({"link": "https://open.spotify.com/album/x"}))
        for number, title in ((1, "15 Step"), (2, "Bodysnatchers")):
            old_file(folder, f"{number:02d}. {title}", title=title, artist="Radiohead", album="In Rainbows",
                     albumartist="Radiohead", number=number)
        outcome = tidy.tidy(folder)
        assert asked["resolve"] == ["https://open.spotify.com/album/x"] and not asked["find_album"]
        assert (outcome.found, outcome.files, outcome.cover, outcome.lyrics) == (True, 2, True, 2)
        assert (folder / "cover.jpg").read_bytes() == JPEG
        tags = downloader.read_tags(folder / "02. Bodysnatchers.mp3")
        assert (tags["genre"], tags["date"][:4], tags["cover"], tags["lyrics"]) == ("Art Rock", "2007", True, True)
        assert (folder / "02. Bodysnatchers.lrc").read_text(encoding="utf-8") == SYNCED + "\n"

    def test_a_folder_without_a_link_is_found_by_the_names_in_its_tags(self, tmp_path, services):
        answers, asked = services
        folder = tmp_path / "whatever"
        folder.mkdir()
        old_file(folder, "a", title="Nude", artist="Radiohead, Guest", album="In Rainbows")
        outcome = tidy.tidy(folder, artist="Folder Artist", title="Folder Title")
        assert asked["find_album"] == [("Radiohead", "In Rainbows")]
        assert outcome.found and downloader.read_tags(folder / "a.mp3")["track"] == 3

    def test_the_folder_name_speaks_for_files_without_tags(self, tmp_path, services):
        answers, asked = services
        folder = tmp_path / "Radiohead - In Rainbows (2007)"
        folder.mkdir()
        silence(folder / "03. Nude.mp3")
        outcome = tidy.tidy(folder, artist="Radiohead", title="In Rainbows")
        assert asked["find_album"] == [("Radiohead", "In Rainbows")]
        tags = downloader.read_tags(folder / "03. Nude.mp3")
        assert outcome.found and (tags["title"], tags["artist"], tags["track"]) == ("Nude", "Radiohead", 3)

    def test_a_release_the_files_do_not_fit_changes_nothing(self, tmp_path, services):
        answers, asked = services
        folder = tmp_path / "Somebody - Something"
        folder.mkdir()
        for name in ("One", "Two", "Three"):
            old_file(folder, name, title=name, artist="Somebody", album="Something")
        before = {path.name: path.read_bytes() for path in folder.iterdir()}
        outcome = tidy.tidy(folder)
        assert not outcome.found and not asked["cover"] and not asked["lyrics"]
        assert {path.name: path.read_bytes() for path in folder.iterdir()} == before

    def test_lyrics_already_there_are_not_asked_for_again(self, tmp_path, services):
        answers, asked = services
        folder = tmp_path / "album"
        folder.mkdir()
        path = silence(folder / "01. 15 Step.mp3")
        album = release().album
        downloader._write_tags(path, album, album.tracks[0], None, "Their own words")
        tidy.tidy(folder, with_lyrics=True)
        assert asked["lyrics"] == []
        assert str(MutagenFile(path).tags.getall("USLT")[0]) == "Their own words"

    def test_lyrics_can_be_left_out(self, tmp_path, services):
        answers, asked = services
        folder = tmp_path / "album"
        folder.mkdir()
        old_file(folder, "01. Nude", title="Nude", artist="Radiohead", album="In Rainbows")
        assert tidy.tidy(folder, with_lyrics=False).lyrics == 0 and asked["lyrics"] == []

    def test_a_playlist_gets_no_genre(self, tmp_path, services):
        answers, asked = services
        answers["release"] = release(kind="playlist")
        folder = tmp_path / "mix"
        folder.mkdir()
        (folder / downloader.MARKER_NAME).write_text(json.dumps({"link": "https://open.spotify.com/playlist/x"}))
        old_file(folder, "01. Nude", title="Nude", artist="Radiohead")
        tidy.tidy(folder)
        assert asked["genre"] == []
        assert downloader.read_tags(folder / "01. Nude.mp3")["compilation"]

    def test_a_single_track_unknown_to_every_catalogue_is_left_alone(self, tmp_path, services, monkeypatch):
        placeholder = Track(id="1", title="Nude", artists="Radiohead", duration=0, track_number=1)
        found = Release(Album(id="Nude", name="Nude", artist="Radiohead", tracks=[placeholder]), [placeholder],
                        single=True)
        monkeypatch.setattr(tidy.sources, "find_track", lambda *args, **kwargs: found)
        path = old_file(tmp_path, "Radiohead - Nude", title="Nude", artist="Radiohead")
        assert not tidy.tidy(path).found


class TestPlace:
    def track(self, number):
        return release().tracks[number - 1]

    def test_a_guest_in_the_file_name_does_not_hide_the_title(self, tmp_path):
        path = tmp_path / "02. Guest - Bodysnatchers.mp3"
        assert tidy._place(path, {"duration": 1}, release()) == self.track(2)

    def test_a_title_like_track_01_is_placed_by_number_and_length(self, tmp_path):
        path = tmp_path / "Track 01.mp3"
        assert tidy._place(path, {"title": "Track 01", "track": 1, "duration": 1.2}, release()) == self.track(1)

    def test_a_different_length_is_a_different_recording(self, tmp_path):
        path = tmp_path / "01. 15 Step.mp3"
        assert tidy._place(path, {"title": "15 Step", "track": 1, "duration": 300}, release()) is None
