"""Artists: their pages, their discography by link, and watching them for new releases."""

import contextlib
import datetime
import io
import json
import urllib.parse

import pytest

from trackhound import gui, watch
from trackhound.engine import catalog
from trackhound.engine.models import SourceError


def release(title, kind="album", date="2007-10-10", number=1):
    return {"id": number, "title": title, "record_type": kind, "release_date": date,
            "link": f"https://www.deezer.com/album/{number}", "cover_medium": f"https://cdn.test/{number}.jpg"}


@pytest.fixture
def deezer(monkeypatch):
    answers = {
        "artist/399": {"id": 399, "name": "Radiohead", "nb_album": 3, "nb_fan": 100,
                       "link": "https://www.deezer.com/artist/399", "picture_big": "https://cdn.test/p.jpg"},
        "artist/399/albums": {"data": [release("Pablo Honey", date="1993-02-22", number=1),
                                       release("Creep", "single", "1992-09-21", 2),
                                       release("In Rainbows", date="2007-10-10", number=3)],
                              "next": "https://api.deezer.com/artist/399/albums?limit=100&index=100"},
        "artist/399/albums?index=100": {"data": [release("My Iron Lung", "ep", "1994-09-26", 4)]},
        "search/artist": {"data": [{"id": 12, "name": "Radiohead Tribute", "nb_fan": 999},
                                   {"id": 399, "name": "Radiohead", "nb_fan": 100}]},
    }
    asked = []

    def urlopen(request, timeout):
        url = urllib.parse.urlsplit(request.full_url)
        path = url.path.lstrip("/")
        query = dict(urllib.parse.parse_qsl(url.query))
        asked.append((path, query))
        key = f"{path}?index={query['index']}" if "index" in query else path
        return contextlib.closing(io.BytesIO(json.dumps(answers[key]).encode()))

    monkeypatch.setattr(catalog.urllib.request, "urlopen", urlopen)
    return answers, asked


class TestLinks:
    @pytest.mark.parametrize("link", [
        "https://www.deezer.com/artist/399", "https://www.deezer.com/en/artist/399",
        "https://music.youtube.com/channel/UCr_iyUANcn9OX_yy9piYoLw",
        "https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb",
        "https://open.spotify.com/intl-de/artist/4Z8W4fKeB5YxbusRsdQVPb",
        "https://music.apple.com/us/artist/radiohead/657515",
    ])
    def test_artist_links_are_known(self, link):
        assert catalog.is_artist_link(link)

    @pytest.mark.parametrize("link", [
        "https://www.deezer.com/album/302127", "https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc",
        "https://www.youtube.com/channel/UCr_iyUANcn9OX_yy9piYoLw", "Radiohead - In Rainbows",
    ])
    def test_releases_and_names_are_not(self, link):
        assert not catalog.is_artist_link(link)


class TestPage:
    def test_every_release_newest_first_across_pages(self, deezer):
        page = catalog.artist("https://www.deezer.com/artist/399")
        assert (page["name"], page["service"], page["fans"]) == ("Radiohead", "Deezer", 100)
        assert [item["title"] for item in page["releases"]] == ["In Rainbows", "My Iron Lung", "Pablo Honey", "Creep"]
        assert page["releases"][0]["artist"] == "Radiohead"

    def test_the_discography_is_albums_and_eps_oldest_first(self, deezer):
        assert [item["title"] for item in catalog.discography("https://www.deezer.com/artist/399")] == [
            "Pablo Honey", "My Iron Lung", "In Rainbows"]

    def test_a_spotify_artist_is_followed_to_deezer_by_the_same_name(self, deezer, monkeypatch):
        monkeypatch.setattr(catalog.spotify, "_meta_tags", lambda kind, spotify_id: [("og:title", "Radiohead")])
        link = "https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb"
        page = catalog.artist(link)
        assert page["name"] == "Radiohead" and page["link"] == link  # watched under the link given
        assert ("artist/399", {}) in deezer[1]  # the one of that very name, not the more popular tribute

    def test_a_spotify_page_that_does_not_open_says_where_else_to_look(self, deezer, monkeypatch):
        monkeypatch.setattr(catalog.spotify, "_meta_tags", lambda kind, spotify_id: [])
        with pytest.raises(SourceError, match="Deezer"):
            catalog.artist("https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb")


class TestDownload:
    def test_an_artist_link_becomes_their_albums(self, monkeypatch):
        monkeypatch.setattr(gui.catalog, "discography", lambda link: [{"link": "https://x.test/a"},
                                                                      {"link": "https://x.test/b"}])
        assert gui._expand_artists(["https://x.test/r", "https://www.deezer.com/artist/399"]) == [
            "https://x.test/r", "https://x.test/a", "https://x.test/b"]

    def test_an_artist_that_does_not_open_stays_a_link_for_its_card_to_explain(self, monkeypatch):
        def refuse(link):
            raise SourceError("нет связи", "offline")

        monkeypatch.setattr(gui.catalog, "discography", refuse)
        assert gui._expand_artists(["https://www.deezer.com/artist/399"]) == ["https://www.deezer.com/artist/399"]


def day(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()


class TestWatch:
    def entry(self):
        return watch.add([], "https://www.deezer.com/artist/399", "Radiohead", "Deezer", {"folder": "M"},
                         now=day("2026-06-01"), known=["https://x.test/old"])[0]

    def test_only_what_came_out_since_is_new(self):
        entry = self.entry()
        fresh = watch.new_releases(entry, [
            {"link": "https://x.test/new-single", "date": "2026-09-01"},
            {"link": "https://x.test/new-album", "date": "2026-07-01"},
            {"link": "https://x.test/old", "date": "2007-10-10"},
            {"link": "https://x.test/reissue-added-late", "date": "1997-05-21"},
            {"link": "https://x.test/youtube", "date": "2026"},
        ])
        assert [item["link"] for item in fresh] == [
            "https://x.test/youtube", "https://x.test/new-album", "https://x.test/new-single"]
        # Every release seen is known from now on, the late reissue included
        assert "https://x.test/reissue-added-late" in entry["known"]
        assert watch.new_releases(entry, [{"link": "https://x.test/new-single", "date": "2026-09-01"}]) == []

    def test_a_check_queues_the_new_ones_into_the_music_folder(self, monkeypatch):
        api = gui.Api()
        api._watched = [self.entry()]
        monkeypatch.setattr(gui.watch, "save", lambda entries: None)
        queued = []
        monkeypatch.setattr(api, "_enqueue", lambda link, settings: queued.append((link, settings["folder"],
                                                                                  settings["ask_doubtful"]))
                            or {"job": len(queued), "link": link})
        monkeypatch.setattr(gui.catalog, "artist", lambda link: {"releases": [
            {"link": "https://x.test/new", "date": "2026-09-01"}, {"link": "https://x.test/old", "date": "2007-10-10"}]})
        api.check_watched(force=True)
        assert queued == [("https://x.test/new", "M", False)]
        assert api.watched()[0]["kind"] == "artist" and api.watched()[0]["added_tracks"] == 1

    def test_watching_an_artist_remembers_what_they_have(self, monkeypatch):
        api = gui.Api()
        api._watched = []
        monkeypatch.setattr(gui.watch, "save", lambda entries: None)
        monkeypatch.setattr(gui.catalog, "artist", lambda link: {
            "link": link, "name": "Radiohead", "service": "Deezer", "releases": [{"link": "https://x.test/a"}]})
        listed = api.watch_artist("https://www.deezer.com/artist/399")
        assert listed[0]["title"] == "Radiohead" and api._watched[0]["known"] == ["https://x.test/a"]
