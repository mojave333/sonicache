"""Scoring a candidate is what decides whether the right song is downloaded."""

import pytest

from trackhound.engine.matcher import (GOOD_SCORE, MIN_SCORE, Matcher, SearchError, _artist_score, _norm,
                                _score, _similarity)
from trackhound.engine.models import Album, Track


def track(title="One More Time", artists="Daft Punk", duration=320.0, explicit=False):
    return Track(id="1", title=title, artists=artists, duration=duration, track_number=1,
                 explicit=explicit)


def album(name="Discovery"):
    return Album(id="a", name=name, artist="Daft Punk")


def candidate(title="One More Time", artists="Daft Punk", duration=320.0, source="song",
              album_name="Discovery", explicit=None):
    return {"source": source, "url": f"https://example.test/{source}/{title}",
            "page_url": "https://example.test",
            "title": title, "artists": artists, "duration": duration, "album": album_name,
            "explicit": explicit}


class TestNorm:
    def test_drops_remaster_and_feature_noise(self):
        assert _norm("One More Time (2021 Remaster)") == "one more time"
        assert _norm("Starboy (feat. Daft Punk)") == "starboy"
        assert _norm("Numb - 2020 Remastered Version") == "numb"

    def test_folds_punctuation_and_case(self):
        assert _norm("AC/DC") == "ac dc"
        assert _norm("Rock & Roll") == "rock and roll"
        assert _norm("  Spaced   Out  ") == "spaced out"

    def test_strips_diacritics(self):
        assert _norm("Björk") == "bjork"


class TestSimilarity:
    def test_identical_strings_match(self):
        assert _similarity("one more time", "one more time") == 1.0

    def test_empty_never_matches(self):
        assert _similarity("", "anything") == 0.0

    def test_transliterated_cyrillic_matches_latin(self):
        assert _similarity("кино", "kino") > 0.8

    def test_substring_counts_as_a_near_match(self):
        assert _similarity("one more time", "daft punk one more time") >= 0.85

    def test_a_short_name_inside_a_long_one_is_not_a_match(self):
        # "Never" is an album of its own, not "Never Gonna Give You Up"
        assert _similarity("never gonna give you up", "never") < 0.6


class TestArtistScore:
    def test_main_artist_found(self):
        assert _artist_score("Daft Punk", "Daft Punk - One More Time") == 0.7 + 0.3

    def test_unknown_artist_scores_zero(self):
        assert _artist_score("Daft Punk", "Some Cover Band") == 0.0

    def test_featured_artists_add_up(self):
        score = _artist_score("The Weeknd, Daft Punk", "The Weeknd - Starboy")
        assert 0.7 <= score < 1.0

    def test_missing_artist_is_neutral(self):
        assert _artist_score("", "anything") == 0.5


class TestScore:
    def test_exact_official_audio_ends_the_search(self):
        match = _score(candidate(), track(), album())
        assert match is not None and match.score >= GOOD_SCORE

    def test_wrong_length_ranks_far_below_the_right_one(self):
        right = _score(candidate(), track(), album())
        wrong = _score(candidate(duration=95.0), track(), album())
        assert wrong.score < right.score - 0.3

    def test_other_artist_is_refused(self):
        assert _score(candidate(artists="Cover Band", title="One More Time", album_name=""),
                      track(), album()) is None

    def test_live_version_is_penalised(self):
        studio = _score(candidate(), track(), album())
        live = _score(candidate(title="One More Time (Live)"), track(), album())
        assert live.score < studio.score - 0.2

    def test_live_is_kept_when_the_original_is_live(self):
        wanted = track(title="One More Time (Live)")
        match = _score(candidate(title="One More Time (Live)"), wanted, album())
        assert match.score >= GOOD_SCORE

    def test_official_audio_outranks_a_video(self):
        song = _score(candidate(source="song"), track(), album())
        video = _score(candidate(source="video"), track(), album())
        assert song.score > video.score

    def test_unknown_duration_still_matches(self):
        match = _score(candidate(duration=0), track(duration=0), album())
        assert match is not None and match.score >= MIN_SCORE


class TestFindAll:
    def _matcher(self, monkeypatch, songs=(), soundcloud=(), videos=()):
        finder = Matcher()
        monkeypatch.setattr(finder, "_youtube_music_songs", lambda query: list(songs))
        monkeypatch.setattr(finder, "_soundcloud", lambda query: list(soundcloud))
        monkeypatch.setattr(finder, "_youtube_videos", lambda query: list(videos))
        return finder

    def test_a_great_hit_stops_the_later_sources(self, monkeypatch):
        asked = []

        finder = Matcher()
        monkeypatch.setattr(finder, "_youtube_music_songs", lambda query: [candidate()])
        monkeypatch.setattr(finder, "_soundcloud",
                            lambda query: asked.append("soundcloud") or [])
        monkeypatch.setattr(finder, "_youtube_videos", lambda query: asked.append("videos") or [])
        matches = finder.find_all(track(), album())
        assert [m.source for m in matches] == ["song"]
        assert asked == []

    def test_an_exhaustive_search_asks_everyone(self, monkeypatch):
        finder = self._matcher(monkeypatch, songs=[candidate()],
                               soundcloud=[candidate(source="soundcloud")])
        assert len(finder.find_all(track(), album(), exhaustive=True)) == 2

    def test_the_same_url_is_not_returned_twice(self, monkeypatch):
        same = candidate()
        finder = self._matcher(monkeypatch, songs=[same], videos=[dict(same, source="video")])
        assert len(finder.find_all(track(), album(), exhaustive=True)) == 1

    def test_best_candidate_comes_first(self, monkeypatch):
        finder = self._matcher(
            monkeypatch,
            songs=[candidate(title="One More Time (Live)"), candidate()],
        )
        matches = finder.find_all(track(), album(), exhaustive=True)
        assert matches[0].title == "One More Time"

    def test_a_source_that_does_not_answer_rests_and_is_asked_again_later(self, monkeypatch):
        """Where YouTube is blocked, each track must not wait out its retries."""
        asked = []
        clock = [1000.0]
        finder = Matcher()

        def blocked(query):
            asked.append("youtube")
            raise SearchError("поиск на YouTube Music не удался: timed out")

        monkeypatch.setattr("trackhound.engine.matcher.time.monotonic", lambda: clock[0])
        monkeypatch.setattr(finder, "_youtube_music_songs", blocked)
        monkeypatch.setattr(finder, "_youtube_videos", blocked)
        monkeypatch.setattr(finder, "_soundcloud", lambda query: [candidate(source="soundcloud")])
        for _ in range(3):
            assert [m.source for m in finder.find_all(track(), album())] == ["soundcloud"]
        assert asked == ["youtube"]  # once, and not for the videos either: the same API
        clock[0] += Matcher.REST + 1
        finder.find_all(track(), album())
        assert asked == ["youtube", "youtube"]

    def test_a_resting_source_still_explains_an_empty_search(self, monkeypatch):
        finder = Matcher()

        def blocked(query):
            raise SearchError("поиск на YouTube Music не удался: timed out")

        monkeypatch.setattr(finder, "_youtube_music_songs", blocked)
        monkeypatch.setattr(finder, "_youtube_videos", blocked)
        monkeypatch.setattr(finder, "_soundcloud", lambda query: [])
        for _ in range(2):
            with pytest.raises(SearchError, match="timed out"):
                finder.find_all(track(), album())

    def test_a_failing_source_is_reported_when_nothing_is_found(self, monkeypatch):
        finder = Matcher()

        def broken(query):
            raise SearchError("поиск на YouTube Music не удался: 503")

        monkeypatch.setattr(finder, "_youtube_music_songs", broken)
        monkeypatch.setattr(finder, "_soundcloud", lambda query: [])
        monkeypatch.setattr(finder, "_youtube_videos", lambda query: [])
        try:
            finder.find_all(track(), album())
        except SearchError as error:
            assert "503" in str(error)
        else:
            raise AssertionError("the search error was swallowed")
