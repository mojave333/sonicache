"""Link routing and the shapes each service answers with."""

import urllib.parse
from pathlib import Path

import pytest

from trackhound import sources
from trackhound.models import Album, SourceError

FIXTURES = Path(__file__).parent / "fixtures"

APPLE_ALBUM = [
    {"wrapperType": "collection", "collectionId": 697194953, "collectionName": "Discovery",
     "artistName": "Daft Punk", "releaseDate": "2001-03-12T07:00:00Z",
     "artworkUrl100": "https://apple.test/100x100bb.jpg"},
    {"wrapperType": "track", "kind": "song", "trackId": 697195045, "trackName": "One More Time",
     "artistName": "Daft Punk", "trackTimeMillis": 320357, "trackNumber": 1, "discNumber": 1,
     "trackExplicitness": "notExplicit"},
    {"wrapperType": "track", "kind": "song", "trackId": 697195046, "trackName": "Aerodynamic",
     "artistName": "Daft Punk", "trackTimeMillis": 212546, "trackNumber": 2, "discNumber": 2,
     "trackExplicitness": "explicit"},
]


def split(url):
    return urllib.parse.urlsplit(url)


class TestResolveRouting:
    def test_plain_text_is_searched_for(self, monkeypatch):
        asked = []
        monkeypatch.setattr(sources, "search", lambda query: asked.append(query) or "release")
        assert sources.resolve("Daft Punk Discovery") == "release"
        assert asked == ["Daft Punk Discovery"]

    def test_vk_explains_itself(self):
        with pytest.raises(SourceError, match="VK"):
            sources.resolve("https://vk.com/audio123")

    def test_spotify_uri_goes_to_spotify(self, monkeypatch):
        seen = []
        monkeypatch.setattr(sources.spotify, "parse_link",
                            lambda link: seen.append(link) or ("album", "x"))
        monkeypatch.setattr(sources.spotify, "fetch_album",
                            lambda album_id: Album(id=album_id, name="X", artist="Y"))
        sources.resolve("spotify:album:2noRn2Aes5aoNVsU6iWThc")
        assert seen == ["spotify:album:2noRn2Aes5aoNVsU6iWThc"]

    def test_a_bare_domain_still_resolves(self, monkeypatch):
        monkeypatch.setattr(sources, "_apple", lambda parts: "apple")
        assert sources.resolve("music.apple.com/us/album/discovery/697194953") == "apple"


class TestSearchByName:
    """Typed instead of a link: "Исполнитель - Альбом"."""

    @pytest.mark.parametrize("query, expected", [
        ("Daft Punk - Discovery", ("Daft Punk", "Discovery")),
        ("Кино — Группа крови", ("Кино", "Группа крови")),
        ("Portishead – Dummy", ("Portishead", "Dummy")),
        ("Daft Punk -- Discovery", ("Daft Punk", "Discovery")),
        ("Discovery", ("", "Discovery")),
        ("AC/DC - Back in Black", ("AC/DC", "Back in Black")),
        ("Wham! - Last Christmas - Single", ("Wham!", "Last Christmas - Single")),
    ])
    def test_the_artist_is_split_off(self, query, expected):
        assert sources._split_query(query) == expected

    def test_an_album_is_preferred(self, monkeypatch):
        monkeypatch.setattr(sources, "find_album", lambda artist, title, service: "album")
        monkeypatch.setattr(sources, "find_track", lambda *a, **k: "track")
        assert sources.search("Daft Punk - Discovery") == "album"

    def test_a_track_is_the_fallback(self, monkeypatch):
        monkeypatch.setattr(sources, "find_album", lambda artist, title, service: None)
        monkeypatch.setattr(sources, "find_track",
                            lambda artist, title, service, album="": (artist, title, service))
        assert sources.search("Rick Astley - Never Gonna Give You Up") == (
            "Rick Astley", "Never Gonna Give You Up", "поиск")

    def test_a_bare_title_is_searched_without_an_artist(self, monkeypatch):
        asked = []
        monkeypatch.setattr(sources, "find_album",
                            lambda artist, title, service: asked.append((artist, title)) or None)
        monkeypatch.setattr(sources, "find_track",
                            lambda artist, title, service, album="": (artist, title))
        assert sources.search("Never Gonna Give You Up") == ("", "Never Gonna Give You Up")
        assert asked == [("", "Never Gonna Give You Up")]

    def test_an_empty_query_says_what_to_type(self):
        with pytest.raises(SourceError, match="Исполнитель"):
            sources.search("   ")


class TestApple:
    def test_album_tracks_and_tags(self, monkeypatch):
        monkeypatch.setattr(sources, "_itunes", lambda endpoint, **params: APPLE_ALBUM)
        release = sources._apple(split("https://music.apple.com/us/album/discovery/697194953"))
        album = release.album
        assert (album.name, album.artist, album.release_date) == ("Discovery", "Daft Punk", "2001-03-12")
        assert [t.title for t in album.tracks] == ["One More Time", "Aerodynamic"]
        assert album.tracks[0].duration == pytest.approx(320.357)
        assert album.tracks[1].explicit is True
        assert album.total_discs == 2

    def test_the_cover_is_asked_for_at_full_size(self, monkeypatch):
        monkeypatch.setattr(sources, "_itunes", lambda endpoint, **params: APPLE_ALBUM)
        release = sources._apple(split("https://music.apple.com/us/album/discovery/697194953"))
        assert release.album.cover_url == "https://apple.test/1000x1000bb.jpg"

    def test_a_song_link_downloads_one_track_of_its_album(self, monkeypatch):
        def itunes(endpoint, **params):
            if params.get("entity") == "song":
                return APPLE_ALBUM
            return [{"collectionId": 697194953, "trackId": 697195046}]

        monkeypatch.setattr(sources, "_itunes", itunes)
        release = sources._apple(split("https://music.apple.com/us/song/aerodynamic/697195046"))
        assert release.single is True
        assert [t.title for t in release.tracks] == ["Aerodynamic"]
        assert len(release.album.tracks) == 2  # the album is kept for tags and numbering

    def test_an_artist_page_is_refused(self):
        with pytest.raises(SourceError, match="альбом"):
            sources._apple(split("https://music.apple.com/us/artist/daft-punk/5468295"))

    def test_a_missing_album_names_the_region(self, monkeypatch):
        monkeypatch.setattr(sources, "_itunes", lambda endpoint, **params: [])
        with pytest.raises(SourceError, match="DE"):
            sources._apple(split("https://music.apple.com/de/album/discovery/697194953"))


class TestApplePlaylist:
    @pytest.fixture
    def page(self, monkeypatch):
        html = (FIXTURES / "apple_playlist.html").read_text(encoding="utf-8")
        monkeypatch.setattr(sources, "fetch_text", lambda url, **kwargs: html)

    def url(self):
        return "https://music.apple.com/us/playlist/todays-hits/pl.f4d106fed2bd41149aaacabb233eb5eb"

    def test_a_playlist_link_is_read_off_the_page(self, page):
        release = sources.resolve(self.url())
        assert release.album.kind == "playlist"
        assert release.album.name == "Today's Hits"
        assert [t.title for t in release.tracks] == ["Been By Now", "Golden", "Manchild"]
        assert release.tracks[0].artists == "Morgan Wallen"
        assert release.tracks[0].duration == pytest.approx(213.806)
        assert [t.track_number for t in release.tracks] == [1, 2, 3]

    def test_the_curator_stands_in_for_the_artist(self, page):
        assert sources.resolve(self.url()).album.artist == "Apple Music Hits"

    def test_the_artwork_template_is_filled_in(self, page):
        assert sources.resolve(self.url()).album.cover_url == "https://is1.test/image/1000x1000bb.jpg"

    def test_a_cut_short_playlist_says_so(self, page):
        note = sources.resolve(self.url()).album.note
        assert "первые 3 треков из 5" in note

    def test_a_changed_page_is_named_as_such(self, monkeypatch):
        monkeypatch.setattr(sources, "fetch_text", lambda url, **kwargs: "<html>no data</html>")
        with pytest.raises(SourceError, match="изменила формат"):
            sources.resolve(self.url())

    def test_a_playlist_without_songs_is_an_error(self, monkeypatch):
        empty = ('<script type="application/json" id="serialized-server-data">'
                 '{"data":[{"data":{"sections":[]}}]}</script>')
        monkeypatch.setattr(sources, "fetch_text", lambda url, **kwargs: empty)
        with pytest.raises(SourceError, match="нет доступных песен"):
            sources.resolve(self.url())


# Shapes as api.deezer.com answers them, trimmed to what is read
DEEZER = {
    "https://api.deezer.com/album/302127": {
        "id": 302127, "title": "Discovery", "release_date": "2001-03-07", "record_type": "album",
        "cover_xl": "https://dzcdn.test/1000x1000.jpg", "artist": {"name": "Daft Punk"},
    },
    "https://api.deezer.com/album/302127/tracks?limit=500": {
        "data": [
            {"id": 3135553, "title": "One More Time", "duration": 320, "track_position": 1,
             "disk_number": 1, "explicit_lyrics": False, "artist": {"name": "Daft Punk"}},
            {"id": 3135554, "title": "Aerodynamic", "duration": 212, "track_position": 2,
             "disk_number": 1, "explicit_lyrics": True, "artist": {"name": "Daft Punk"}},
        ],
        "total": 3, "next": "https://api.deezer.com/album/302127/tracks?limit=500&index=2",
    },
    "https://api.deezer.com/album/302127/tracks?limit=500&index=2": {
        "data": [{"id": 3135560, "title": "Too Long", "duration": 600, "track_position": 1,
                  "disk_number": 2, "artist": {"name": "Daft Punk"}}],
        "total": 3,
    },
    "https://api.deezer.com/track/3135554": {"id": 3135554, "album": {"id": 302127}},
    "https://api.deezer.com/playlist/908622995": {
        "id": 908622995, "title": "Chill", "picture_xl": "https://dzcdn.test/playlist.jpg",
        "creator": {"name": "Deezer Editors"},
    },
    "https://api.deezer.com/playlist/908622995/tracks?limit=500": {
        "data": [
            {"id": 1, "title": "Song", "duration": 180, "artist": {"name": "A"}},
            {"id": 1, "title": "Song", "duration": 180, "artist": {"name": "A"}},
        ],
    },
}


class TestDeezer:
    @pytest.fixture(autouse=True)
    def api(self, monkeypatch):
        asked = []

        def fetch_json(url, **kwargs):
            asked.append(url)
            return DEEZER.get(url, {"error": {"type": "DataException", "message": "no data", "code": 800}})

        monkeypatch.setattr(sources, "fetch_json", fetch_json)
        return asked

    def test_album_tracks_and_tags_across_pages(self):
        release = sources.resolve("https://www.deezer.com/en/album/302127")
        album = release.album
        assert (album.name, album.artist, album.release_date, album.kind) == (
            "Discovery", "Daft Punk", "2001-03-07", "album")
        assert album.service == "Deezer"
        assert album.cover_url == "https://dzcdn.test/1000x1000.jpg"
        assert [t.title for t in album.tracks] == ["One More Time", "Aerodynamic", "Too Long"]
        assert album.tracks[1].explicit is True
        assert (album.tracks[2].track_number, album.tracks[2].disc_number) == (1, 2)
        assert album.total_discs == 2

    def test_a_link_without_a_language_works_too(self):
        assert sources.resolve("deezer.com/album/302127").album.name == "Discovery"

    def test_a_track_link_downloads_one_track_of_its_album(self):
        release = sources.resolve("https://www.deezer.com/fr/track/3135554?utm_source=deezer")
        assert release.single is True
        assert [t.title for t in release.tracks] == ["Aerodynamic"]
        assert len(release.album.tracks) == 3  # the album is kept for tags and numbering

    def test_a_playlist_numbers_its_own_rows(self):
        release = sources.resolve("https://www.deezer.com/playlist/908622995")
        album = release.album
        assert (album.kind, album.name, album.artist) == ("playlist", "Chill", "Deezer Editors")
        assert [t.track_number for t in release.tracks] == [1, 2]
        assert len({t.id for t in release.tracks}) == 2  # the same song twice stays two rows

    def test_a_missing_album_says_so(self):
        with pytest.raises(SourceError, match="не нашёл альбом 1"):
            sources.resolve("https://www.deezer.com/album/1")

    def test_other_errors_are_passed_on(self, monkeypatch):
        monkeypatch.setattr(sources, "fetch_json", lambda url, **kwargs: {
            "error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}})
        with pytest.raises(SourceError, match="Quota limit exceeded"):
            sources.resolve("https://www.deezer.com/album/302127")

    def test_an_artist_page_is_refused(self, api):
        with pytest.raises(SourceError, match="альбомы, треки и плейлисты"):
            sources.resolve("https://www.deezer.com/en/artist/27")
        assert api == []

    def test_a_short_link_is_followed(self, monkeypatch):
        monkeypatch.setattr(sources, "_deezer_short_link",
                            lambda link: "https://www.deezer.com/album/302127")
        assert sources.resolve("https://link.deezer.com/s/30ZA1D9TrYl3").album.name == "Discovery"

    def test_a_dead_short_link_says_so(self, monkeypatch):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def geturl(self):
                return "https://www.deezer.com/deezer-links-404"

            def read(self):
                return b"<html>nothing here</html>"

        monkeypatch.setattr(sources.urllib.request, "urlopen", lambda request, timeout: Response())
        with pytest.raises(SourceError, match="никуда не ведёт"):
            sources.resolve("https://link.deezer.com/s/30ZA1D9TrYl3")


class TestLastfm:
    def test_the_tracklist_is_read_off_the_page(self, monkeypatch):
        page = (FIXTURES / "lastfm_album.html").read_text(encoding="utf-8")
        monkeypatch.setattr(sources, "fetch_text", lambda url, **kwargs: page)
        release = sources._lastfm_tracklist("https://www.last.fm/music/Daft+Punk/Discovery",
                                            "Daft Punk", "Discovery")
        titles = [t.title for t in release.tracks]
        assert titles == ["One More Time", "Aerodynamic", "Digital Love & More"]
        assert release.tracks[0].duration == 320
        assert release.tracks[2].duration == 0  # no length on the page
        assert release.album.release_date == "2001"
        assert release.album.cover_url == "https://lastfm.test/cover.jpg"

    def test_an_empty_page_is_an_error(self, monkeypatch):
        monkeypatch.setattr(sources, "fetch_text", lambda url, **kwargs: "<html></html>")
        with pytest.raises(SourceError, match="Не нашёл треки"):
            sources._lastfm_tracklist("https://www.last.fm/music/A/B", "A", "B")

    def test_an_artist_link_is_refused(self):
        with pytest.raises(SourceError, match="исполнител"):
            sources._lastfm("https://www.last.fm/music/Daft+Punk",
                            split("https://www.last.fm/music/Daft+Punk"))

    def test_a_refusing_catalogue_still_leaves_the_page(self, monkeypatch):
        """The album matches on YouTube Music but its page will not open: the
        tracklist on the Last.fm page itself is the whole point of the fallback."""
        page = (FIXTURES / "lastfm_album.html").read_text(encoding="utf-8")
        monkeypatch.setattr(sources, "fetch_text", lambda url, **kwargs: page)
        monkeypatch.setattr(sources, "ytmusic", lambda: Catalogue(
            [{"browseId": "MPREb_x", "title": "Discovery", "artists": [{"name": "Daft Punk"}]}]))
        monkeypatch.setattr(sources, "_youtube_album", refuses("YouTube Music не отдал альбом"))
        release = sources._lastfm("https://www.last.fm/music/Daft+Punk/Discovery",
                                  split("https://www.last.fm/music/Daft+Punk/Discovery"))
        assert [t.title for t in release.tracks] == ["One More Time", "Aerodynamic", "Digital Love & More"]
        assert release.album.service == "Last.fm"


class Catalogue:
    """A YouTube Music client whose search always answers the same way."""

    def __init__(self, results):
        self.results = results

    def search(self, *args, **kwargs):
        return self.results


def refuses(message):
    def refuse(*args, **kwargs):
        raise SourceError(message)

    return refuse


class TestCatalogueRefusals:
    """A catalogue that matches a name and then will not open the release must
    read as "not found", so that the caller's own fallback still gets its turn."""

    def test_find_album_answers_none(self, monkeypatch):
        monkeypatch.setattr(sources, "ytmusic", lambda: Catalogue(
            [{"browseId": "MPREb_x", "title": "Discovery", "artists": [{"name": "Daft Punk"}]}]))
        monkeypatch.setattr(sources, "_youtube_album", refuses("YouTube Music не отдал альбом"))
        assert sources.find_album("Daft Punk", "Discovery", "Last.fm") is None

    def test_find_album_answers_none_for_apple_too(self, monkeypatch):
        monkeypatch.setattr(sources, "ytmusic", lambda: Catalogue([]))
        monkeypatch.setattr(sources, "_itunes", lambda *a, **k: [
            {"collectionId": 697194953, "collectionName": "Discovery", "artistName": "Daft Punk"}])
        monkeypatch.setattr(sources, "_apple_album", refuses("Apple Music не нашёл альбом"))
        assert sources.find_album("Daft Punk", "Discovery", "Last.fm") is None

    def test_find_track_falls_back_to_the_name(self, monkeypatch):
        monkeypatch.setattr(sources, "ytmusic", lambda: Catalogue(
            [{"videoId": "abc", "title": "One More Time", "artists": [{"name": "Daft Punk"}]}]))
        monkeypatch.setattr(sources, "_youtube_track", refuses("YouTube не нашёл видео abc"))
        monkeypatch.setattr(sources, "_itunes", lambda *a, **k: [])
        release = sources.find_track("Daft Punk", "One More Time", "Last.fm")
        assert release.single
        assert release.tracks[0].title == "One More Time"
        assert release.album.service == "Last.fm"

    def test_find_track_survives_a_refusing_apple_album(self, monkeypatch):
        monkeypatch.setattr(sources, "ytmusic", lambda: Catalogue([]))
        monkeypatch.setattr(sources, "_itunes", lambda *a, **k: [
            {"collectionId": 697194953, "trackId": 697195045,
             "trackName": "One More Time", "artistName": "Daft Punk"}])
        monkeypatch.setattr(sources, "_apple_album", refuses("Apple Music не нашёл альбом"))
        release = sources.find_track("Daft Punk", "One More Time", "Last.fm")
        assert release.tracks[0].title == "One More Time"


class TestBest:
    def fields(self, result):
        return result["name"], result["artist"]

    def test_picks_the_closest_title(self):
        results = [{"name": "Discovery (Deluxe)", "artist": "Daft Punk"},
                   {"name": "Discovery", "artist": "Daft Punk"}]
        assert sources._best(results, "Discovery", "Daft Punk", self.fields)["name"] == "Discovery"

    def test_another_artist_is_not_a_match(self):
        results = [{"name": "Discovery", "artist": "Someone Else"}]
        assert sources._best(results, "Discovery", "Daft Punk", self.fields) is None

    def test_a_different_album_is_not_a_match(self):
        results = [{"name": "Homework", "artist": "Daft Punk"}]
        assert sources._best(results, "Discovery", "Daft Punk", self.fields) is None


class TestHelpers:
    @pytest.mark.parametrize("name, expected", [
        ("Random Access Memories", ("Random Access Memories", "album")),
        ("Alive 2007 - EP", ("Alive 2007", "ep")),
        ("One More Time - Single", ("One More Time", "single")),
    ])
    def test_split_kind(self, name, expected):
        assert sources._split_kind(name) == expected

    @pytest.mark.parametrize("text, expected", [
        ("3:32", 212), ("1:02:03", 3723), ("45", 45), ("", 0), (None, 0), ("n/a", 0),
    ])
    def test_seconds(self, text, expected):
        assert sources._seconds(text) == expected

    @pytest.mark.parametrize("value, expected", [
        ("20010312", "2001-03-12"), ("2001", ""), ("", ""), (None, ""), ("abcdefgh", ""),
    ])
    def test_ymd(self, value, expected):
        assert sources._ymd(value) == expected

    def test_service_name_from_the_extractor(self):
        assert sources._service_name({"extractor_key": "SoundcloudSet"}, "https://x.test") == "SoundCloud"
        assert sources._service_name({}, "https://www.example.test/a") == "example.test"

    def test_thumbnail_asks_for_a_big_image(self):
        thumbnails = [{"url": "https://i.test/a=w60-h60", "width": 60},
                      {"url": "https://i.test/b=w544-h544", "width": 544}]
        assert sources._thumbnail(thumbnails) == "https://i.test/b=w1200-h1200"
        assert sources._thumbnail([]) == ""

    def test_names_joins_artists(self):
        assert sources._names([{"name": "A"}, {"name": "B"}, {}]) == "A, B"
        assert sources._names(None) == ""
