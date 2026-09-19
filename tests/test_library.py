"""What the library reads out of the music folder: tags, covers, missing tracks, artist photos."""

import json
import subprocess
from pathlib import Path

import pytest

from trackhound import gui
from trackhound.engine import downloader, sources
from trackhound.engine.models import Album, SourceError, Track

FFMPEG = downloader.find_tool("ffmpeg")
CODECS = {"m4a": ["-c:a", "aac", "-b:a", "96k"], "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
          "opus": ["-c:a", "libopus", "-b:a", "64k"]}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def tagged(folder: Path, ext: str, number: int, title: str, cover: bytes | None = None) -> Path:
    """A one-second file tagged the way the downloader tags its own."""
    path = folder / f"{number:02d}. {title}.{ext}"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=1", *CODECS[ext], str(path)], check=True)
    tracks = [Track(id=str(n), title=f"T{n}", artists="Radiohead", duration=1, track_number=n)
              for n in (1, 2, 3)]
    album = Album(id="a", name="In Rainbows", artist="Radiohead", release_date="2007-10-10", tracks=tracks)
    track = Track(id=str(number), title=title, artists="Radiohead, Guest", duration=1, track_number=number)
    downloader._write_tags(path, album, track, cover)
    return path


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
class TestTags:
    @pytest.mark.parametrize("ext", ["m4a", "mp3", "opus"])
    def test_what_the_downloader_wrote_is_read_back(self, tmp_path, ext):
        info = gui._tags(tagged(tmp_path, ext, 2, "Bodysnatchers"))
        assert (info["title"], info["artists"], info["album"], info["album_artist"]) == (
            "Bodysnatchers", "Radiohead, Guest", "In Rainbows", "Radiohead")
        assert (info["number"], info["disc"], info["year"], info["format"]) == (2, 1, "2007", ext)
        assert info["duration"] == 1 and info["bitrate"] > 0

    @pytest.mark.parametrize("ext", ["m4a", "mp3", "opus"])
    def test_the_cover_inside_a_file_is_found(self, tmp_path, ext):
        path = tagged(tmp_path, ext, 1, "15 Step", cover=PNG)
        assert gui._embedded_cover(path) == PNG
        assert gui.Api.cover(None, str(path)).startswith("data:image/png;base64,")

    def test_a_file_without_tags_is_named_after_itself(self, tmp_path):
        path = tmp_path / "Loose Song.mp3"
        subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=duration=1", "-map_metadata", "-1", *CODECS["mp3"], str(path)], check=True)
        info = gui._tags(path)
        assert (info["title"], info["number"], info["artists"]) == ("Loose Song", 0, "")
        assert gui._embedded_cover(path) is None

    def test_the_album_page_lists_the_tracks_in_order_and_the_missing_ones(self, tmp_path):
        folder = tmp_path / "Radiohead - In Rainbows (2007)"
        folder.mkdir()
        tagged(folder, "mp3", 3, "Nude")
        tagged(folder, "mp3", 1, "15 Step")
        (folder / downloader.MARKER_NAME).write_text(json.dumps({
            "link": "https://www.deezer.com/album/1", "tracks": 3, "service": "Deezer",
            "tracklist": [{"disc": 1, "number": n, "title": title, "artists": "Radiohead", "duration": 200}
                          for n, title in ((1, "15 Step"), (2, "Bodysnatchers"), (3, "Nude"))],
        }), encoding="utf-8")
        page = gui.Api.album(None, str(folder))
        assert [track["title"] for track in page["tracks"]] == ["15 Step", "Nude"]
        assert [track["title"] for track in page["missing"]] == ["Bodysnatchers"]
        assert (page["expected"], page["link"], page["service"]) == (3, "https://www.deezer.com/album/1", "Deezer")


class TestMarker:
    def test_the_marker_keeps_the_whole_tracklist(self, tmp_path):
        tracks = [Track(id="1", title="One More Time", artists="Daft Punk", duration=320.4, track_number=1),
                  Track(id="2", title="Too Long", artists="Daft Punk", duration=600, track_number=1, disc_number=2)]
        album = Album(id="a", name="Discovery", artist="Daft Punk", tracks=tracks)
        downloader._write_marker(tmp_path, album, "https://example.test/album")
        marker = json.loads((tmp_path / downloader.MARKER_NAME).read_text(encoding="utf-8"))
        assert marker["tracklist"] == [
            {"disc": 1, "number": 1, "title": "One More Time", "artists": "Daft Punk", "duration": 320},
            {"disc": 2, "number": 1, "title": "Too Long", "artists": "Daft Punk", "duration": 600},
        ]


class TestArtistPicture:
    @pytest.fixture
    def data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gui.logs, "data_dir", lambda: tmp_path)
        return tmp_path

    def test_a_name_is_asked_about_once(self, data_dir, monkeypatch):
        asked = []
        monkeypatch.setattr(gui.sources, "artist_picture",
                            lambda name: asked.append(name) or "https://img.test/r.jpg")
        api = gui.Api.__new__(gui.Api)
        assert api.artist_picture("Radiohead") == "https://img.test/r.jpg"
        assert api.artist_picture("  radiohead ") == "https://img.test/r.jpg"
        assert asked == ["Radiohead"]

    def test_an_unknown_artist_waits_a_week_before_the_next_question(self, data_dir, monkeypatch):
        asked = []
        monkeypatch.setattr(gui.sources, "artist_picture", lambda name: asked.append(name) or "")
        api = gui.Api.__new__(gui.Api)
        assert api.artist_picture("Nobody") == "" and api.artist_picture("Nobody") == ""
        assert asked == ["Nobody"]
        stale = json.loads((data_dir / gui.ARTISTS_FILE).read_text(encoding="utf-8"))
        stale["nobody"]["asked"] -= gui.ARTIST_RETRY + 1
        (data_dir / gui.ARTISTS_FILE).write_text(json.dumps(stale), encoding="utf-8")
        api.artist_picture("Nobody")
        assert asked == ["Nobody", "Nobody"]

    def test_being_offline_is_not_remembered(self, data_dir, monkeypatch):
        def offline(name):
            raise SourceError("no network")

        monkeypatch.setattr(gui.sources, "artist_picture", offline)
        assert gui.Api.__new__(gui.Api).artist_picture("Radiohead") == ""
        assert not (data_dir / gui.ARTISTS_FILE).exists()


class TestDeezerArtist:
    def answer(self, monkeypatch, items):
        monkeypatch.setattr(sources, "fetch_json", lambda url, **kwargs: {"data": items})

    def test_only_the_exact_name_counts(self, monkeypatch):
        self.answer(monkeypatch, [{"name": "Radiohead Tribute", "picture_big": "https://img.test/fake.jpg"},
                                  {"name": "Radiohead", "picture_big": "https://img.test/real.jpg"}])
        assert sources.artist_picture("radiohead") == "https://img.test/real.jpg"

    def test_deezers_placeholder_is_no_photo(self, monkeypatch):
        self.answer(monkeypatch, [{"name": "Kino",
                                   "picture_big": "https://cdn.test/images/artist//500x500-000000-80-0-0.jpg"}])
        assert sources.artist_picture("Kino") == ""

    def test_nobody_by_that_name(self, monkeypatch):
        self.answer(monkeypatch, [{"name": "Someone Else", "picture_big": "https://img.test/x.jpg"}])
        assert sources.artist_picture("Radiohead") == ""


@pytest.mark.parametrize("given, expected", [("grid", "grid"), ("list", "list"), ("tiles", "grid"), (None, "grid")])
def test_the_library_view_setting(given, expected):
    assert gui._normalize({"library_view": given})["library_view"] == expected


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
@pytest.mark.parametrize("ext", ["m4a", "mp3", "opus"])
def test_the_genre_goes_into_every_format(tmp_path, ext):
    path = tmp_path / f"song.{ext}"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=duration=1", *CODECS[ext], str(path)], check=True)
    track = Track(id="1", title="Ebony", artists="Dean Blunt", duration=1, track_number=1)
    album = Album(id="a", name="Black is Beautiful", artist="Dean Blunt", tracks=[track], genre="Electronic")
    downloader._write_tags(path, album, track, None)
    assert gui._tags(path)["genre"] == "Electronic"


class TestGenre:
    @pytest.fixture(autouse=True)
    def nothing_on_musicbrainz(self, monkeypatch):
        monkeypatch.setattr(sources, "musicbrainz_genres", lambda artist, title: [])

    def test_apple_music_names_the_genre_of_its_album(self, monkeypatch):
        monkeypatch.setattr(sources, "_itunes", lambda endpoint, **params: [
            {"wrapperType": "collection", "collectionName": "Discovery", "artistName": "Daft Punk",
             "primaryGenreName": "Electronic"},
            {"wrapperType": "track", "kind": "song", "trackId": 1, "trackName": "One More Time",
             "artistName": "Daft Punk", "trackNumber": 1}])
        assert sources._apple_album("1").album.genre == "Electronic"

    def test_deezer_takes_the_first_of_its_genres(self):
        assert sources._deezer_genre({"genres": {"data": [{"name": "Electro"}, {"name": "Dance"}]}}) == "Electro"
        assert sources._deezer_genre({}) == ""

    def test_a_missing_genre_is_looked_up_in_apples_catalogue(self, monkeypatch):
        monkeypatch.setattr(sources, "_itunes", lambda endpoint, **params: [
            {"collectionName": "Black is Beautiful - EP", "artistName": "Dean Blunt & Inga Copeland",
             "primaryGenreName": "Electronic"}])
        monkeypatch.setattr(sources, "fetch_json", lambda url, **kwargs: pytest.fail("Deezer was not needed"))
        assert sources.find_genre("Dean Blunt", "Black is Beautiful") == "Electronic"

    def test_deezer_is_asked_when_apple_does_not_know(self, monkeypatch):
        monkeypatch.setattr(sources, "_itunes", lambda endpoint, **params: [])
        answers = {
            "search": {"data": [{"id": 302127, "title": "Discovery", "artist": {"name": "Daft Punk"}}]},
            "album": {"genres": {"data": [{"name": "Electro"}]}},
        }
        monkeypatch.setattr(sources, "fetch_json",
                            lambda url, **kwargs: answers["search" if "/search/" in url else "album"])
        assert sources.find_genre("Daft Punk", "Discovery") == "Electro"

    def test_a_different_album_gives_no_genre(self, monkeypatch):
        monkeypatch.setattr(sources, "_itunes", lambda endpoint, **params: [
            {"collectionName": "Homework", "artistName": "Daft Punk", "primaryGenreName": "Dance"}])
        monkeypatch.setattr(sources, "fetch_json", lambda url, **kwargs: {"data": []})
        assert sources.find_genre("Daft Punk", "Discovery") == ""

    def test_the_marker_remembers_the_genre(self, tmp_path):
        album = Album(id="a", name="Discovery", artist="Daft Punk", genre="Electro",
                      tracks=[Track(id="1", title="Aerodynamic", artists="Daft Punk", duration=212, track_number=2)])
        downloader._write_marker(tmp_path, album, "https://www.deezer.com/album/302127")
        assert json.loads((tmp_path / downloader.MARKER_NAME).read_text(encoding="utf-8"))["genre"] == "Electro"


class TestGenreLookupWhenDownloading:
    def downloader_for(self, tmp_path, monkeypatch, album, dry_run=False):
        monkeypatch.setattr(downloader.sources, "resolve", lambda link: sources.Release(album, album.tracks))
        loader = downloader.Downloader(downloader.Options(tmp_path, dry_run=dry_run), log=lambda message: None)
        monkeypatch.setattr(loader, "_download_tracks", lambda *args, **kwargs: downloader.Report())
        return loader

    def album(self, kind="album", genre=""):
        return Album(id="a", name="Discovery", artist="Daft Punk", kind=kind, genre=genre,
                     tracks=[Track(id="1", title="One More Time", artists="Daft Punk", duration=320, track_number=1)])

    def test_an_album_without_a_genre_gets_one_looked_up(self, tmp_path, monkeypatch):
        album = self.album()
        monkeypatch.setattr(downloader.sources, "find_genre", lambda artist, title, known="": "Electronic")
        self.downloader_for(tmp_path, monkeypatch, album).download_link("x")
        assert album.genre == "Electronic"

    @pytest.mark.parametrize("kind, genre, dry_run", [("playlist", "", False), ("album", "", True)])
    def test_no_lookup_for_playlists_or_dry_runs(self, tmp_path, monkeypatch, kind, genre, dry_run):
        album = self.album(kind, genre)
        monkeypatch.setattr(downloader.sources, "find_genre", lambda artist, title, known="": pytest.fail("looked up"))
        self.downloader_for(tmp_path, monkeypatch, album, dry_run).download_link("x")
        assert album.genre == genre

    def test_a_failed_lookup_does_not_stop_the_download(self, tmp_path, monkeypatch):
        def broken(artist, title, known=""):
            raise RuntimeError("catalogue down")

        album = self.album()
        monkeypatch.setattr(downloader.sources, "find_genre", broken)
        assert isinstance(self.downloader_for(tmp_path, monkeypatch, album).download_link("x"), downloader.Report)
        assert album.genre == ""


class TestMusicBrainz:
    GROUPS = {"release-groups": [
        {"id": "wrong", "title": "In Rainbows Disk 2", "artist-credit": [{"name": "Radiohead"}]},
        {"id": "right", "title": "In Rainbows", "artist-credit": [{"name": "Radiohead"}]},
    ]}
    GENRES = {"genres": [{"name": "rock", "count": 12}, {"name": "alternative rock", "count": 20},
                         {"name": "art rock", "count": 9}, {"name": "indie rock", "count": 4},
                         {"name": "space rock", "count": 2}]}

    @pytest.fixture
    def answers(self, monkeypatch):
        asked = []

        def fetch_json(url, **kwargs):
            asked.append((url, kwargs.get("user_agent", "")))
            return self.GENRES if "/right?" in url else self.GROUPS

        monkeypatch.setattr(sources, "fetch_json", fetch_json)
        monkeypatch.setattr(sources.time, "sleep", lambda seconds: None)
        return asked

    def test_the_three_most_voted_genres_of_the_right_album(self, answers):
        assert sources.musicbrainz_genres("Radiohead", "In Rainbows") == ["Alternative Rock", "Rock", "Art Rock"]
        assert all("Trackhound" in agent for _, agent in answers)  # MusicBrainz wants to know who asks
        assert answers[1][0].startswith("https://musicbrainz.org/ws/2/release-group/right?inc=genres")

    def test_a_genre_far_behind_the_first_is_left_out(self, answers, monkeypatch):
        monkeypatch.setattr(self, "GENRES", {"genres": [{"name": "electronic", "count": 20},
                                                         {"name": "synth-pop", "count": 4}]})
        assert sources.musicbrainz_genres("Radiohead", "In Rainbows") == ["Electronic"]

    def test_find_genre_prefers_musicbrainz_to_the_services_own(self, answers):
        assert sources.find_genre("Radiohead", "In Rainbows", "Alternative") == "Alternative Rock; Rock; Art Rock"

    def test_the_services_genre_stays_when_musicbrainz_has_none(self, monkeypatch):
        monkeypatch.setattr(sources, "musicbrainz_genres", lambda artist, title: [])
        monkeypatch.setattr(sources, "_itunes", lambda *a, **k: pytest.fail("the service had named one"))
        assert sources.find_genre("Daft Punk", "Discovery", "Electro") == "Electro"

    def test_musicbrainz_being_down_is_not_an_error(self, monkeypatch):
        def down(artist, title):
            raise SourceError("503")

        monkeypatch.setattr(sources, "musicbrainz_genres", down)
        assert sources.find_genre("Daft Punk", "Discovery", "Electro") == "Electro"

    def test_requests_keep_a_second_apart(self, monkeypatch):
        naps = []
        monkeypatch.setattr(sources, "fetch_json", lambda url, **kwargs: {})
        monkeypatch.setattr(sources.time, "sleep", naps.append)
        sources._musicbrainz("release-group/x?inc=genres")
        sources._musicbrainz("release-group/y?inc=genres")
        assert naps and naps[-1] > 1.0

    @pytest.mark.parametrize("name, cased", [("alternative rock", "Alternative Rock"), ("synth-pop", "Synth-Pop"),
                                             ("drum and bass", "Drum and Bass"), ("hip hop", "Hip Hop"),
                                             ("r&b", "R&B"), ("uk garage", "UK Garage"), ("idm", "IDM")])
    def test_genre_names_are_capitalised(self, name, cased):
        assert sources._genre_case(name) == cased
