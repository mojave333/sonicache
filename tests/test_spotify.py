"""Spotify is read off public pages, so its parsing rots the moment they change."""

import json
from pathlib import Path

import pytest

from trackhound.engine import spotify
from trackhound.engine.models import SourceError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def pages(monkeypatch):
    """Serves the saved embed and meta pages instead of going to Spotify."""
    embed = (FIXTURES / "spotify_embed_album.html").read_text(encoding="utf-8")
    meta = (FIXTURES / "spotify_meta_album.html").read_text(encoding="utf-8")
    served = {}

    def fetch_text(url, *, service, user_agent=spotify.BROWSER_UA, retries=3):
        served[url] = user_agent
        return embed if "/embed/" in url else meta

    monkeypatch.setattr(spotify, "fetch_text", fetch_text)
    return served


class TestParseLink:
    @pytest.mark.parametrize("link, expected", [
        ("https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc",
         ("album", "2noRn2Aes5aoNVsU6iWThc")),
        ("https://open.spotify.com/intl-de/album/2noRn2Aes5aoNVsU6iWThc",
         ("album", "2noRn2Aes5aoNVsU6iWThc")),
        ("https://open.spotify.com/embed/track/0DiWol3AO6WpXZgp0goxAV?utm_source=x",
         ("track", "0DiWol3AO6WpXZgp0goxAV")),
        ("spotify:album:2noRn2Aes5aoNVsU6iWThc", ("album", "2noRn2Aes5aoNVsU6iWThc")),
        ("spotify:track:0DiWol3AO6WpXZgp0goxAV", ("track", "0DiWol3AO6WpXZgp0goxAV")),
        ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
         ("playlist", "37i9dQZF1DXcBWIGoYBM5M")),
        ("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", ("playlist", "37i9dQZF1DXcBWIGoYBM5M")),
    ])
    def test_known_shapes(self, link, expected):
        assert spotify.parse_link(link) == expected

    def test_nonsense_is_refused(self):
        with pytest.raises(SourceError):
            spotify.parse_link("https://open.spotify.com/artist/4tZwfgrHOc3mvqYlEYSvVi")


class TestFetchAlbum:
    def test_reads_the_track_list(self, pages):
        album = spotify.fetch_album("2noRn2Aes5aoNVsU6iWThc")
        assert (album.name, album.artist) == ("Discovery", "Daft Punk")
        assert [t.title for t in album.tracks] == ["One More Time", "Aerodynamic", "Digital Love"]

    def test_durations_come_back_in_seconds(self, pages):
        album = spotify.fetch_album("2noRn2Aes5aoNVsU6iWThc")
        assert album.tracks[0].duration == pytest.approx(320.357)

    def test_disc_and_track_numbers_come_from_the_meta_tags(self, pages):
        tracks = spotify.fetch_album("2noRn2Aes5aoNVsU6iWThc").tracks
        assert [(t.disc_number, t.track_number) for t in tracks] == [(1, 1), (1, 2), (2, 1)]

    def test_release_date_kind_and_cover(self, pages):
        album = spotify.fetch_album("2noRn2Aes5aoNVsU6iWThc")
        assert album.release_date == "2001-03-12"
        assert album.year == "2001"
        assert album.kind == "album"
        assert album.cover_url == "https://i.test/640.jpg"

    def test_explicit_survives(self, pages):
        assert [t.explicit for t in spotify.fetch_album("x").tracks] == [False, False, True]

    def test_the_meta_page_is_asked_as_a_link_preview_bot(self, pages):
        spotify.fetch_album("2noRn2Aes5aoNVsU6iWThc")
        regular = next(url for url in pages if "/embed/" not in url)
        assert pages[regular] == spotify.PREVIEW_UA

    def test_a_changed_page_is_named_as_such(self, monkeypatch):
        monkeypatch.setattr(spotify, "fetch_text",
                            lambda url, **kwargs: "<html><body>no data here</body></html>")
        with pytest.raises(SourceError, match="изменил формат страницы"):
            spotify.fetch_album("2noRn2Aes5aoNVsU6iWThc")

    def refuse(self, monkeypatch, props):
        page = f'<script id="__NEXT_DATA__" type="application/json">{{"props":{{"pageProps":{props}}}}}</script>'
        monkeypatch.setattr(spotify, "fetch_text", lambda url, **kwargs: page)
        logged = []
        monkeypatch.setattr(spotify, "log", type("Log", (), {"warning": lambda self, text, *args: logged.append(text % args)})())
        with pytest.raises(SourceError, match="прокси") as refused:
            spotify.fetch_playlist("0pcHRkLh6EUBsHy6ZJVIdY")
        # What came instead goes to the log, for a report from where it happens
        assert json.dumps(json.loads(props), ensure_ascii=False) in logged[0]
        return refused.value

    @pytest.mark.parametrize("props", ['{"state":{"data":{}}}', '{"state":null}', '{"status":451}'])
    def test_a_page_without_the_release_points_to_a_proxy(self, monkeypatch, props):
        error = self.refuse(monkeypatch, props)
        assert error.code == "unavailable"
        assert (error.details["kind"], error.details["id"]) == ("playlist", "0pcHRkLh6EUBsHy6ZJVIdY")

    def test_spotifys_own_error_page_is_quoted(self, monkeypatch):
        """Spotify's error page comes with HTTP 200, a status and a title in its data."""
        error = self.refuse(monkeypatch, '{"status":404,"title":"Page not found",'
                                         '"description":"We can’t seem to find the page you are looking for."}')
        assert error.code == "not_found" and error.details["status"] == 404
        assert "«Page not found»" in str(error)

    def test_an_album_without_tracks_is_an_error(self, monkeypatch):
        empty = ('<script id="__NEXT_DATA__" type="application/json">'
                 '{"props":{"pageProps":{"state":{"data":{"entity":{"name":"X","trackList":[]}}}}}}</script>')
        monkeypatch.setattr(spotify, "fetch_text", lambda url, **kwargs: empty)
        with pytest.raises(SourceError, match="не найдено треков"):
            spotify.fetch_album("2noRn2Aes5aoNVsU6iWThc")

    def test_the_meta_page_may_be_missing(self, monkeypatch):
        embed = (FIXTURES / "spotify_embed_album.html").read_text(encoding="utf-8")

        def fetch_text(url, *, service, user_agent=spotify.BROWSER_UA, retries=3):
            if "/embed/" in url:
                return embed
            raise SourceError("Spotify ответил HTTP 404")

        monkeypatch.setattr(spotify, "fetch_text", fetch_text)
        album = spotify.fetch_album("2noRn2Aes5aoNVsU6iWThc")
        assert [t.track_number for t in album.tracks] == [1, 2, 3]  # numbered in page order
        assert album.cover_url == "https://i.test/640.jpg"  # the widest embed image


class TestFetchPlaylist:
    @pytest.fixture
    def playlist_pages(self, monkeypatch):
        embed = (FIXTURES / "spotify_embed_playlist.html").read_text(encoding="utf-8")
        meta = (FIXTURES / "spotify_meta_playlist.html").read_text(encoding="utf-8")

        def fetch_text(url, *, service, user_agent=spotify.BROWSER_UA, retries=3):
            return embed if "/embed/" in url else meta

        monkeypatch.setattr(spotify, "fetch_text", fetch_text)

    def test_tracks_keep_playlist_order(self, playlist_pages):
        album = spotify.fetch_playlist("37i9dQZF1DXcBWIGoYBM5M")
        assert album.kind == "playlist"
        assert [t.track_number for t in album.tracks] == [1, 2, 3]
        assert [t.title for t in album.tracks] == ["BbY WOW", "Loser", "Локальный файл"]

    def test_artists_are_joined_with_plain_commas(self, playlist_pages):
        first = spotify.fetch_playlist("x").tracks[0]
        assert first.artists == "KAROL G, Judeline, rusowsky"

    def test_a_track_without_a_uri_still_gets_an_id(self, playlist_pages):
        assert spotify.fetch_playlist("x").tracks[2].id == "3"

    def test_the_cover_comes_from_the_meta_page(self, playlist_pages):
        assert spotify.fetch_playlist("x").cover_url == "https://i.test/playlist.jpg"

    def test_a_short_playlist_has_nothing_to_warn_about(self, playlist_pages):
        assert spotify.fetch_playlist("x").note == ""

    def test_a_full_page_says_the_rest_is_missing(self, monkeypatch, playlist_pages):
        monkeypatch.setattr(spotify, "PLAYLIST_LIMIT", 3)
        assert "первые 3" in spotify.fetch_playlist("x").note

    def test_an_empty_playlist_is_an_error(self, monkeypatch):
        empty = ('<script id="__NEXT_DATA__" type="application/json">'
                 '{"props":{"pageProps":{"state":{"data":{"entity":{"name":"X","trackList":[]}}}}}}</script>')
        monkeypatch.setattr(spotify, "fetch_text", lambda url, **kwargs: empty)
        with pytest.raises(SourceError, match="не найдено треков"):
            spotify.fetch_playlist("37i9dQZF1DXcBWIGoYBM5M")


class TestHelpers:
    def test_id_from_url(self):
        assert spotify._id_from_url("https://open.spotify.com/track/abc?si=1") == "abc"
        assert spotify._id_from_url("") == ""

    def test_meta_tags_keep_document_order(self, pages):
        tags = spotify._meta_tags("album", "2noRn2Aes5aoNVsU6iWThc")
        keys = [key for key, _ in tags]
        assert keys.index("music:song") < keys.index("music:song:track")
        assert ("music:release_date", "2001-03-12") in tags

    @pytest.mark.parametrize("subtitle, expected", [
        ("A, B", "A, B"),
        ("Tame Impala", "Tame Impala"),
        ("", ""),
        (None, ""),
        ("A,, B", "A, B"),
    ])
    def test_artists_are_tidied(self, subtitle, expected):
        assert spotify._artists(subtitle) == expected

    def test_meta_entities_are_unescaped(self, pages):
        description = spotify._first(spotify._meta_tags("album", "x"), "og:description")
        assert "·" in description
