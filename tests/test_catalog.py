"""The catalogue search behind the Search view: Deezer first, YouTube Music where it does not answer."""

import contextlib
import datetime
import io
import json
import urllib.error
import urllib.parse

import pytest

from sonicache import gui
from sonicache.engine import catalog
from sonicache.engine.models import Album, Release, SourceError, Track

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


class TestHome:
    """The Catalogue's first screen: the chart, and what is new from the library's artists."""

    TODAY = datetime.date(2026, 10, 14)

    CHART = {"data": [{"id": 1, "title": "Chart One", "link": "https://www.deezer.com/album/1", "cover": "c",
                       "record_type": "album", "artist": {"name": "Somebody"}},
                      {"id": 2, "title": "Chart Two", "link": "https://www.deezer.com/album/2",
                       "record_type": "album", "artist": {"name": "Another"}}]}

    @staticmethod
    def releases(*items):
        return {"data": [{"id": number, "title": title, "link": f"https://www.deezer.com/album/{number}",
                          "release_date": date, "record_type": kind, "cover_medium": "https://cdn.test/c.jpg"}
                         for number, title, date, kind in items]}

    @pytest.fixture
    def answered(self, deezer):
        answers, asked = deezer
        answers["chart/0/albums"] = self.CHART
        answers["search/artist"] = lambda name: {"data": [
            {"id": 399, "name": name, "link": "x", "nb_fan": 10}]}
        answers["artist/399/albums"] = self.releases(
            (10, "This Year", "2026-03-01", "album"),
            (11, "Last Year", "2025-12-24", "ep"),
            (12, "Long Ago", "2019-01-01", "album"),
            (13, "Next Week", "2026-10-18", "single"),
            (14, "Far Future", "2027-05-01", "album"),
            (15, "Someone Else's Mix", "2026-02-02", "compile"))
        return answers, asked

    def test_the_chart_comes_first_in_the_order_deezer_gives(self, answered):
        popular = catalog.home([], today=self.TODAY)["popular"]
        assert [album["title"] for album in popular] == ["Chart One", "Chart Two"]
        assert popular[0]["artist"] == "Somebody"

    def test_what_is_new_is_the_last_year_up_to_next_week_newest_first(self, answered):
        fresh = catalog.home(["Radiohead"], today=self.TODAY)["fresh"]
        assert [album["title"] for album in fresh] == ["Next Week", "This Year", "Last Year"]
        assert {album["artist"] for album in fresh} == {"Radiohead"}  # the library's spelling, not Deezer's

    def test_a_compilation_is_not_new_music(self, answered):
        fresh = catalog.home(["Radiohead"], today=self.TODAY)["fresh"]
        assert "Someone Else's Mix" not in [album["title"] for album in fresh]

    def test_an_artist_found_is_remembered_and_not_looked_up_again(self, answered):
        _, asked = answered
        known = {}
        catalog.home(["Radiohead"], known, today=self.TODAY)
        assert known == {"radiohead": "399"}
        before = sum(1 for path, _ in asked if path == "search/artist")
        catalog.home(["Radiohead"], known, today=self.TODAY)
        assert sum(1 for path, _ in asked if path == "search/artist") == before

    def test_an_artist_deezer_does_not_know_is_left_out_and_the_rest_still_shown(self, answered):
        answers, _ = answered
        answers["search/artist"] = lambda name: {"data": ([{"id": 399, "name": name, "nb_fan": 1}]
                                                          if name == "Radiohead" else [])}
        home = catalog.home(["Nobody Known", "Radiohead"], today=self.TODAY)
        assert len(home["fresh"]) == 3 and home["popular"]

    def test_only_the_first_dozen_of_artists_are_asked_about(self, answered):
        _, asked = answered
        catalog.home([f"Artist {number}" for number in range(30)], today=self.TODAY)
        assert sum(1 for path, _ in asked if path == "search/artist") == catalog.HOME_ARTISTS

    def test_with_the_chart_down_what_is_new_is_still_shown(self, answered):
        answers, _ = answered
        answers["chart/0/albums"] = urllib.error.URLError("down")
        home = catalog.home(["Radiohead"], today=self.TODAY)
        assert home["popular"] == [] and len(home["fresh"]) == 3

    def test_with_nothing_at_all_the_answer_is_an_error_not_an_empty_page(self, answered):
        answers, _ = answered
        answers["chart/0/albums"] = urllib.error.URLError("down")
        with pytest.raises(SourceError):
            catalog.home([], today=self.TODAY)


class TestCatalogueHomeApi:
    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gui.logs, "data_dir", lambda: tmp_path)
        return gui.Api()

    def test_a_second_opening_within_hours_asks_nobody(self, api, monkeypatch):
        calls = []
        monkeypatch.setattr(gui.catalog, "home", lambda names, known: calls.append(names) or
                            {"service": "Deezer", "popular": [{"title": "A"}], "fresh": []})
        first = api.catalogue_home(["Radiohead"])
        assert api.catalogue_home(["Radiohead"]) == first and len(calls) == 1

    def test_other_artists_or_a_refresh_ask_again(self, api, monkeypatch):
        calls = []
        monkeypatch.setattr(gui.catalog, "home", lambda names, known: calls.append(names) or
                            {"service": "Deezer", "popular": [], "fresh": []})
        api.catalogue_home(["Radiohead"])
        api.catalogue_home(["Boris"])
        api.catalogue_home(["Boris"], refresh=True)
        assert len(calls) == 3

    def test_when_deezer_is_down_what_was_shown_last_is_shown_marked(self, api, monkeypatch):
        monkeypatch.setattr(gui.catalog, "home", lambda names, known: {"service": "Deezer", "popular": [{"title": "A"}], "fresh": []})
        api.catalogue_home(["Radiohead"])
        monkeypatch.setattr(gui.time, "time", lambda: 10 ** 10)  # long after
        monkeypatch.setattr(gui.catalog, "home", lambda names, known: (_ for _ in ()).throw(SourceError.of("offline", "x")))
        shown = api.catalogue_home(["Radiohead"])
        assert shown["stale"] is True and shown["popular"] == [{"title": "A"}]

    def test_with_nothing_kept_and_deezer_down_the_page_gets_an_error(self, api, monkeypatch):
        monkeypatch.setattr(gui.catalog, "home", lambda names, known: (_ for _ in ()).throw(SourceError.of("offline", "x")))
        assert api.catalogue_home([])["error"]

    def test_the_ids_found_are_kept_for_next_time(self, api, monkeypatch):
        def home(names, known):
            known["boris"] = "77"
            return {"service": "Deezer", "popular": [], "fresh": []}
        monkeypatch.setattr(gui.catalog, "home", home)
        api.catalogue_home(["Boris"])
        seen = {}
        monkeypatch.setattr(gui.catalog, "home", lambda names, known: seen.update(known) or
                            {"service": "Deezer", "popular": [], "fresh": []})
        api.catalogue_home(["Boris"], refresh=True)
        assert seen == {"boris": "77"}
