"""The catalogue search behind the Search view: Deezer first, YouTube Music where it does not answer."""

import contextlib
import io
import json
import urllib.error
import urllib.parse

import pytest

from trackhound import gui
from trackhound.engine import catalog
from trackhound.engine.models import Album, Release, SourceError, Track

DEEZER = {
    "search/album": {"data": [{"id": 14879699, "title": "OK Computer", "link": "https://www.deezer.com/album/14879699",
                               "cover_medium": "https://cdn.test/250x250-a.jpg", "nb_tracks": 12,
                               "record_type": "album", "artist": {"name": "Radiohead"}}]},
    "search/track": {"data": [{"id": 138547415, "title": "Creep", "link": "https://www.deezer.com/track/138547415",
                               "duration": 238, "artist": {"name": "Radiohead"},
                               "album": {"title": "Pablo Honey", "cover_small": "https://cdn.test/56.jpg"}}]},
    "search/artist": {"data": [{"id": 399, "name": "Radiohead", "link": "https://www.deezer.com/artist/399",
                                "picture_medium": "https://cdn.test/p.jpg", "nb_album": 45, "nb_fan": 4000000}]},
}


@pytest.fixture
def deezer(monkeypatch):
    asked = []
    answers = dict(DEEZER)

    def urlopen(request, timeout):
        url = urllib.parse.urlsplit(request.full_url)
        asked.append((url.path.lstrip("/"), dict(urllib.parse.parse_qsl(url.query))))
        answer = answers[url.path.lstrip("/")]
        if callable(answer):  # an answer that depends on the question
            answer = answer(dict(urllib.parse.parse_qsl(url.query)).get("q", ""))
        if isinstance(answer, Exception):
            raise answer
        return contextlib.closing(io.BytesIO(json.dumps(answer).encode()))

    monkeypatch.setattr(catalog.urllib.request, "urlopen", urlopen)
    return answers, asked


class FakeYouTube:
    def search(self, query, filter, limit):
        return {
            "albums": [{"title": "OK Computer", "type": "Album", "year": "1997", "browseId": "MPREb_x",
                        "artists": [{"name": "Radiohead"}],
                        "thumbnails": [{"url": "https://yt.test/a=w60-h60-l90", "width": 60},
                                       {"url": "https://yt.test/a=w544-h544-l90", "width": 544}]}],
            "songs": [{"title": "Creep", "videoId": "9RfVp", "duration_seconds": 239, "artists": [{"name": "Radiohead"}],
                       "album": {"name": "Pablo Honey"}, "thumbnails": [{"url": "https://yt.test/s=w60-h60", "width": 60}]}],
            "artists": [{"artist": "Radiohead", "browseId": "UCr", "thumbnails": []}],
        }[filter]


def test_deezer_answers_every_kind_with_pictures_and_links(deezer):
    found = catalog.search("  radiohead  ")
    assert found["service"] == "Deezer"
    assert found["albums"] == [{"kind": "album", "title": "OK Computer", "artist": "Radiohead", "type": "album",
                                "year": "", "tracks": 12, "explicit": False, "cover": "https://cdn.test/250x250-a.jpg",
                                "link": "https://www.deezer.com/album/14879699"}]
    assert (found["tracks"][0]["album"], found["tracks"][0]["duration"]) == ("Pablo Honey", 238)
    assert (found["artists"][0]["albums"], found["artists"][0]["link"]) == (45, "https://www.deezer.com/artist/399")
    assert {params["q"] for _, params in deezer[1]} == {"radiohead"}


def test_an_albums_cover_is_the_one_named_so_its_edition_aside(deezer):
    answers, asked = deezer
    answers["search/album"] = {"data": [
        {"title": "OK Computer OKNOTOK 1997 2017", "artist": {"name": "Radiohead"}, "cover_big": "https://cdn.test/500-reissue.jpg"},
        {"title": "OK Computer (Deluxe Edition)", "artist": {"name": "Radiohead"}, "cover_big": "https://cdn.test/500-a.jpg"}]}
    assert catalog.album_cover("Radiohead", "OK Computer [Remastered]") == "https://cdn.test/500-a.jpg"
    assert asked[-1] == ("search/album", {"q": 'artist:"Radiohead" album:"OK Computer [Remastered]"', "limit": "10"})


def test_the_plain_search_finds_what_the_fields_miss_and_a_slip_is_forgiven(deezer):
    answers, asked = deezer
    found = {"data": [{"title": "Madvillainy", "artist": {"name": "Madvillain"}, "cover_big": "https://cdn.test/mv.jpg"}]}
    answers["search/album"] = lambda query: found if not query.startswith("artist:") else {"data": []}
    assert catalog.album_cover("Madvillian", "Madvillainy") == "https://cdn.test/mv.jpg"
    assert [query["q"] for _, query in asked] == ['artist:"Madvillian" album:"Madvillainy"', "Madvillian Madvillainy"]


def test_another_artists_album_of_that_name_is_no_cover(deezer, monkeypatch):
    answers, _ = deezer
    answers["search/album"] = {"data": [{"title": "Greatest Hits", "artist": {"name": "Queen"}, "cover_big": "https://cdn.test/q.jpg"}]}
    monkeypatch.setattr(catalog, "fetch_json", lambda url, service: {"results": [
        {"collectionName": "Greatest Hits", "artistName": "The Cure",
         "artworkUrl100": "https://apple.test/100x100bb.jpg"}]})
    # Deezer has only Queen's; Apple has the right one
    assert catalog.album_cover("The Cure", "Greatest Hits") == "https://apple.test/600x600bb.jpg"
    monkeypatch.setattr(catalog, "fetch_json", lambda url, service: {"results": []})
    assert catalog.album_cover("The Cure", "Greatest Hits") == ""


def test_a_record_no_catalogue_has_takes_the_cover_of_one_with_the_same_song(deezer):
    answers, asked = deezer
    answers["search/track"] = {"data": [
        {"title": "Soon", "artist": {"name": "Pale Saints"}, "album": {"cover_big": "https://cdn.test/other.jpg"}},
        {"title": "Soon", "artist": {"name": "my bloody valentine"}, "album": {"cover_big": "https://cdn.test/eps.jpg"}}]}
    assert catalog.track_cover("My Bloody Valentine", "Soon") == "https://cdn.test/eps.jpg"
    assert catalog.track_cover("Slowdive", "Soon") == ""


def test_youtube_music_answers_where_deezer_does_not(deezer, monkeypatch):
    deezer[0]["search/track"] = urllib.error.URLError("blocked")
    monkeypatch.setattr(catalog, "ytmusic", FakeYouTube)
    found = catalog.search("radiohead")
    assert found["service"] == "YouTube Music"
    assert found["albums"][0]["link"] == "https://music.youtube.com/browse/MPREb_x"
    assert found["albums"][0]["cover"] == "https://yt.test/a=w400-h400-l90"  # asked for at the size shown
    assert found["tracks"][0]["link"] == "https://music.youtube.com/watch?v=9RfVp"
    assert found["artists"][0]["link"] == "https://music.youtube.com/channel/UCr"


def test_nothing_answering_is_one_error(deezer, monkeypatch):
    deezer[0]["search/album"] = urllib.error.URLError("blocked")

    class Down:
        def search(self, *args, **kwargs):
            raise ConnectionError("no route")

    monkeypatch.setattr(catalog, "ytmusic", Down)
    with pytest.raises(SourceError):
        catalog.search("radiohead")
    assert gui.Api.search(None, "radiohead")["error"]


def test_an_empty_query_asks_nobody(deezer):
    assert catalog.search("   ")["albums"] == [] and deezer[1] == []


def test_a_found_album_lists_its_tracks_with_links_of_their_own(monkeypatch):
    tracks = [Track(id="138539971", title="Airbag", artists="Radiohead", duration=287.4, track_number=1)]
    album = Album(id="1", name="OK Computer", artist="Radiohead", release_date="1997-05-21", tracks=tracks,
                  service="Deezer", cover_url="https://cdn.test/c.jpg")
    monkeypatch.setattr(gui.sources, "resolve", lambda link: Release(album, tracks))
    page = gui.Api.release(None, "https://www.deezer.com/album/1")
    assert (page["title"], page["year"], page["cover"]) == ("OK Computer", "1997", "https://cdn.test/c.jpg")
    assert page["tracks"] == [{"number": 1, "disc": 1, "title": "Airbag", "artists": "Radiohead", "duration": 287,
                               "link": "https://www.deezer.com/track/138539971"}]


def test_an_album_that_does_not_open_says_why(monkeypatch):
    def refuse(link):
        raise SourceError("Deezer не нашёл альбом 1", "not_found")

    monkeypatch.setattr(gui.sources, "resolve", refuse)
    assert gui.Api.release(None, "https://www.deezer.com/album/1") == {"error": "Deezer не нашёл альбом 1"}
