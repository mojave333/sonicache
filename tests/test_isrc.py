"""ISRC: the code of one recording, borrowed from Deezer and looked up on YouTube Music."""

import contextlib
import io
import json
import urllib.error
import urllib.parse

import pytest

from trackhound.engine import sources
from trackhound.engine.matcher import ISRC_SCORE, Matcher, doubtful
from trackhound.engine.models import Album, Track


def track(title="Fuel", artists="Metallica", duration=269.0, isrc=""):
    return Track(id="1", title=title, artists=artists, duration=duration, track_number=1, isrc=isrc)


def song(title="Fuel", artists="Metallica", duration=270, video="abc"):
    return {"source": "song", "url": f"https://www.youtube.com/watch?v={video}",
            "page_url": f"https://music.youtube.com/watch?v={video}", "title": title, "artists": artists,
            "duration": duration, "album": "", "explicit": None}


class TestMatcher:
    def matcher(self, monkeypatch, by_code, by_name=()):
        finder = Matcher()
        asked = []

        def songs(query):
            asked.append(query)
            return list(by_code) if query.startswith("GB") else list(by_name)

        monkeypatch.setattr(finder, "_youtube_music_songs", songs)
        monkeypatch.setattr(finder, "_soundcloud", lambda query: asked.append("soundcloud") or [])
        monkeypatch.setattr(finder, "_youtube_videos", lambda query: asked.append("videos") or [])
        return finder, asked

    def test_the_recording_under_the_code_wins_and_is_never_doubted(self, monkeypatch):
        finder, asked = self.matcher(monkeypatch, [song(video="exact")], [song("Fuel (Live)", video="live")])
        matches = finder.find_all(track(isrc="GBAMC9700001"), Album(id="a", name="Reload", artist="Metallica"))
        assert [match.url for match in matches] == ["https://www.youtube.com/watch?v=exact"]
        assert matches[0].score == ISRC_SCORE and not doubtful(matches)
        assert asked == ["GBAMC9700001"]  # no search by name needed

    def test_a_code_youtube_does_not_know_brings_nothing_but_look_alikes(self, monkeypatch):
        finder, asked = self.matcher(monkeypatch, [song("Something Else", "Nobody", 180, "random")],
                                     [song(video="named")])
        matches = finder.find_all(track(isrc="GBAMC9700001"), Album(id="a", name="Reload", artist="Metallica"))
        assert matches[0].url.endswith("named") and matches[0].score < ISRC_SCORE
        assert asked[:2] == ["GBAMC9700001", "Metallica Fuel"]

    def test_another_length_under_the_code_is_not_taken(self, monkeypatch):
        finder, asked = self.matcher(monkeypatch, [song(duration=400, video="long")], [song(video="named")])
        matches = finder.find_all(track(isrc="GBAMC9700001"), Album(id="a", name="Reload", artist="Metallica"))
        assert matches[0].url.endswith("named")

    def test_the_wide_search_keeps_the_exact_one_first_and_adds_the_rest(self, monkeypatch):
        finder, asked = self.matcher(monkeypatch, [song(video="exact")], [song(video="named")])
        matches = finder.find_all(track(isrc="GBAMC9700001"), Album(id="a", name="Reload", artist="Metallica"),
                                  exhaustive=True)
        assert [match.url[-5:] for match in matches] == ["exact", "named"]


@pytest.fixture
def deezer(monkeypatch):
    """Deezer as answers by path; what was asked is kept."""
    answers, asked = {}, []
    monkeypatch.setattr(sources, "ISRC_PACE", 0)
    monkeypatch.setitem(sources._isrc_clock, "rest_until", 0.0)

    def urlopen(request, timeout):
        url = urllib.parse.urlsplit(request.full_url)
        path = url.path.removeprefix("/")
        asked.append((path, dict(urllib.parse.parse_qsl(url.query))))
        answer = answers.get(path, {"data": []})
        if isinstance(answer, Exception):
            raise answer
        return contextlib.closing(io.BytesIO(json.dumps(answer).encode()))

    monkeypatch.setattr(sources.urllib.request, "urlopen", urlopen)
    return answers, asked


def item(title, isrc, duration, artist="Metallica"):
    return {"title": title, "isrc": isrc, "duration": duration, "artist": {"name": artist}}


class TestDeezer:
    def test_an_album_borrows_the_codes_of_the_same_album(self, deezer):
        answers, asked = deezer
        answers["search/album"] = {"data": [
            {"id": 1, "title": "Reload (Deluxe)", "nb_tracks": 30, "artist": {"name": "Metallica"}},
            {"id": 2, "title": "Reload", "nb_tracks": 2, "artist": {"name": "Metallica"}},
            {"id": 3, "title": "Reload", "nb_tracks": 2, "artist": {"name": "Vitamin String Quartet"}},
        ]}
        answers["album/2/tracks"] = {"data": [item("Fuel", "GBAMC9700001", 269),
                                              item("The Memory Remains", "GBAMC9700002", 279)]}
        tracks = [track(), track("The Memory Remains (feat. Marianne Faithfull)", duration=280),
                  track("Devil's Dance", duration=318)]
        album = Album(id="s", name="Reload", artist="Metallica", tracks=tracks)
        assert sources.borrow_isrcs(album) == 2
        assert [t.isrc for t in tracks] == ["GBAMC9700001", "GBAMC9700002", ""]
        assert [path for path, _ in asked] == ["search/album", "album/2/tracks"]

    def test_a_remaster_does_not_take_the_original_code(self, deezer):
        answers, asked = deezer
        answers["search/track"] = {"data": [item("Fuel", "GBAMC9700001", 269),
                                            item("Fuel (Remastered)", "USUM72100001", 270)]}
        assert sources.track_isrc(track("Fuel - Remastered", duration=270)) == "USUM72100001"
        assert asked[0][1]["q"] == "Metallica Fuel"

    def test_a_live_take_or_another_artist_gives_no_code(self, deezer):
        answers, asked = deezer
        answers["search/track"] = {"data": [item("Fuel (Live)", "X1", 269), item("Fuel", "X2", 269, "Tribute Band")]}
        assert sources.track_isrc(track()) == ""

    def test_deezer_out_of_reach_is_left_alone_for_a_while(self, deezer):
        answers, asked = deezer
        answers["search/track"] = urllib.error.URLError("blocked")
        with pytest.raises(sources.SourceError):
            sources.track_isrc(track())
        with pytest.raises(sources.SourceError):
            sources.track_isrc(track())
        assert len(asked) == 1


class TestDownload:
    def run(self, tmp_path, monkeypatch, lookup):
        from trackhound.engine import downloader
        from trackhound.engine.downloader import Match, Options
        events, looked = [], []
        monkeypatch.setattr(downloader.sources, "track_isrc", lambda t: looked.append(t.title) or lookup(t))
        loader = downloader.Downloader(Options(tmp_path, dry_run=True), log=lambda message: None,
                                       events=lambda kind, data: events.append(data))

        def find_all(self, t, album, wide):
            score = ISRC_SCORE if t.isrc else 1.0
            return [Match("song", "https://www.youtube.com/watch?v=x", "", t.title, t.artists, 269, score)]

        loader.matcher = type("Matcher", (), {"find_all": find_all})()
        subject = track()
        result = loader._process(Album(id="a", name="Reload", artist="Metallica"), subject, tmp_path, False, None)
        return result, subject, events[-1], looked

    def test_the_code_found_by_name_leads_to_the_exact_recording(self, tmp_path, monkeypatch):
        result, subject, event, looked = self.run(tmp_path, monkeypatch, lambda t: "GBAMC9700001")
        assert result[0] == "ok" and subject.isrc == "GBAMC9700001"
        assert event["source"] == "YouTube Music · ISRC"

    def test_a_failed_lookup_leaves_the_search_by_name(self, tmp_path, monkeypatch):
        def fail(t):
            raise sources.SourceError("Deezer не ответил", "offline")

        result, subject, event, looked = self.run(tmp_path, monkeypatch, fail)
        assert result[0] == "ok" and subject.isrc == "" and event["source"] == "YouTube Music"
