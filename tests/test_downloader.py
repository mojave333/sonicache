"""File names, error wording and the checks that guard a finished download."""

import json

import pytest

from trackhound import downloader
from trackhound.downloader import (DownloaderError, Options, Report, _check_duration, _clear_partials,
                                   _direct_match, _error_text, _file_stem, _legacy_stem, _mmss,
                                   _safe_name)
from trackhound.models import Album, Track


def album(artist="Daft Punk", name="Discovery", discs=1):
    tracks = [Track(id=str(n), title=f"Track {n}", artists=artist, duration=200, track_number=n,
                    disc_number=1 + (n - 1) % discs) for n in range(1, 3)]
    return Album(id="a", name=name, artist=artist, tracks=tracks)


class TestSafeName:
    @pytest.mark.parametrize("raw, expected", [
        ("AC/DC", "AC-DC"),
        ("Where?", "Where"),
        ('He said "no"', "He said 'no'"),
        ("Drop: The Bass", "Drop - The Bass"),
        ("a<b>c|d*e", "a(b)c-de"),
        ("  spaced   out  ", "spaced out"),
        ("trailing dots...", "trailing dots"),
    ])
    def test_windows_unsafe_characters_are_replaced(self, raw, expected):
        assert _safe_name(raw) == expected

    @pytest.mark.parametrize("reserved", ["CON", "nul", "COM1.mp3", "LPT9"])
    def test_reserved_device_names_are_escaped(self, reserved):
        assert _safe_name(reserved).startswith("_")

    def test_an_empty_name_still_makes_a_file(self):
        assert _safe_name("???") == "_"
        assert _safe_name("") == "_"

    def test_long_names_are_cut_to_the_limit(self):
        assert len(_safe_name("x" * 400)) == 120

    def test_control_characters_are_dropped(self):
        assert _safe_name(f"a{chr(0)}b{chr(31)}c") == "abc"


class TestFileStem:
    def test_a_single_is_named_by_artist_and_title(self):
        track = Track(id="1", title="One More Time", artists="Daft Punk", duration=0, track_number=1)
        assert _file_stem(album(), track, single=True) == "Daft Punk - One More Time"

    def test_album_tracks_are_numbered(self):
        track = Track(id="1", title="Aerodynamic", artists="Daft Punk", duration=0, track_number=2)
        assert _file_stem(album(), track, single=False) == "02. Aerodynamic"

    def test_multi_disc_albums_carry_the_disc(self):
        many = album(discs=2)
        track = Track(id="1", title="Aerodynamic", artists="Daft Punk", duration=0, track_number=1,
                      disc_number=2)
        assert _file_stem(many, track, single=False) == "2-01. Aerodynamic"

    def test_a_guest_artist_stays_in_the_name(self):
        track = Track(id="1", title="Starboy", artists="The Weeknd, Daft Punk", duration=0,
                      track_number=3)
        assert _file_stem(album(), track, single=False) == "03. The Weeknd, Daft Punk - Starboy"


class TestLegacyNames:
    """Albums downloaded before guests were named must not download twice."""

    def test_the_old_name_of_a_guest_track_is_recognised(self):
        track = Track(id="1", title="Instant Crush", artists="Daft Punk, Julian Casablancas",
                      duration=0, track_number=5)
        assert _legacy_stem(album(), track, single=False) == "05. Instant Crush"

    def test_a_track_by_the_album_artist_alone_never_changed_name(self):
        track = Track(id="1", title="Aerodynamic", artists="Daft Punk", duration=0, track_number=2)
        assert _legacy_stem(album(), track, single=False) == ""

    def test_singles_never_changed_name(self):
        track = Track(id="1", title="Starboy", artists="The Weeknd, Daft Punk", duration=0,
                      track_number=1)
        assert _legacy_stem(album(), track, single=True) == ""


class TestErrorText:
    def test_the_yt_dlp_prefix_is_dropped(self):
        assert _error_text(Exception("ERROR: something broke")) == "something broke"

    @pytest.mark.parametrize("raw", [
        "ERROR: Sign in to confirm your age",
        "ERROR: This video is age-restricted",
    ])
    def test_age_gates_get_a_plain_answer(self, raw):
        assert "возрастным ограничением" in _error_text(Exception(raw))

    def test_locked_cookies_get_a_plain_answer(self):
        assert "закройте браузер" in _error_text(Exception("Could not copy Chrome cookie database"))

    def test_an_empty_message_falls_back_to_the_type(self):
        assert _error_text(TimeoutError()) == "TimeoutError"


class TestReport:
    def test_counts_every_outcome(self):
        report = Report(ok=["a", "b"], skipped=["c"], failed=["d: не найдено"])
        summary = report.summary()
        assert "скачано: 2" in summary and "уже было: 1" in summary and "ошибок: 1" in summary
        assert "d: не найдено" in summary

    def test_a_dry_run_says_found_instead_of_downloaded(self):
        assert "найдено: 1" in Report(ok=["a"]).summary(dry_run=True)


class TestGuards:
    def test_a_preview_instead_of_the_track_is_refused(self, monkeypatch, tmp_path):
        monkeypatch.setattr(downloader, "MutagenFile",
                            lambda path: type("Audio", (), {"info": type("Info", (), {"length": 30})})())
        track = Track(id="1", title="X", artists="Y", duration=320, track_number=1)
        with pytest.raises(DownloaderError, match="фрагмент"):
            _check_duration(tmp_path / "x.m4a", track)

    def test_a_full_length_file_passes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(downloader, "MutagenFile",
                            lambda path: type("Audio", (), {"info": type("Info", (), {"length": 318})})())
        track = Track(id="1", title="X", artists="Y", duration=320, track_number=1)
        _check_duration(tmp_path / "x.m4a", track)

    def test_partial_files_are_swept_up(self, tmp_path):
        for name in ("_part_7.m4a", "_part_7.part", "keep.m4a"):
            (tmp_path / name).write_bytes(b"x")
        _clear_partials(tmp_path, "_part_7")
        assert [p.name for p in tmp_path.iterdir()] == ["keep.m4a"]


class TestMarker:
    """The album folder keeps the link it came from, for the library to reuse."""

    def test_the_link_and_tags_are_written(self, tmp_path):
        downloader._write_marker(tmp_path, album(), "https://open.spotify.com/album/x")
        written = json.loads((tmp_path / downloader.MARKER_NAME).read_text(encoding="utf-8"))
        assert written["link"] == "https://open.spotify.com/album/x"
        assert written["artist"] == "Daft Punk" and written["album"] == "Discovery"
        assert written["tracks"] == 2

    def test_nothing_is_written_without_a_link(self, tmp_path):
        downloader._write_marker(tmp_path, album(), "")
        assert not (tmp_path / downloader.MARKER_NAME).exists()

    def test_a_folder_that_refuses_to_be_written_is_only_logged(self, tmp_path, monkeypatch):
        def refuse(*args, **kwargs):
            raise OSError("read-only")

        monkeypatch.setattr("pathlib.Path.write_text", refuse)
        downloader._write_marker(tmp_path, album(), "https://x.test/a")  # must not raise


class TestDirectMatch:
    def test_a_track_that_streams_openly_needs_no_search(self):
        track = Track(id="1", title="X", artists="Y", duration=10, track_number=1,
                      audio_url="https://soundcloud.test/x", audio_source="soundcloud")
        match = _direct_match(track)
        assert match.url == "https://soundcloud.test/x" and match.score == 1.0

    def test_a_track_without_audio_has_to_be_matched(self):
        assert _direct_match(Track(id="1", title="X", artists="Y", duration=10, track_number=1)) is None


class TestOptions:
    def test_defaults_are_the_documented_ones(self, tmp_path):
        options = Options(tmp_path)
        assert (options.audio_format, options.threads, options.dry_run) == ("m4a", 3, False)


class Answer:
    """A urlopen response that hands back a fixed body."""

    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        return self.body[:limit] if limit and limit > 0 else self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestCover:
    """The address comes from someone else's metadata and the bytes are copied
    into every track of the album, so both size and type have to be checked."""

    JPEG = b"\xff\xd8\xff" + b"cover" * 100
    PNG = b"\x89PNG\r\n\x1a\n" + b"cover" * 100

    def fetch(self, monkeypatch, tmp_path, body):
        said = []
        monkeypatch.setattr(downloader.urllib.request, "urlopen", lambda *a, **k: Answer(body))
        loader = downloader.Downloader(Options(tmp_path), log=said.append)
        return loader._fetch_cover("https://cover.test/art"), said

    @pytest.mark.parametrize("body", [JPEG, PNG])
    def test_an_image_comes_through_whole(self, monkeypatch, tmp_path, body):
        assert self.fetch(monkeypatch, tmp_path, body)[0] == body

    def test_an_oversized_body_is_refused(self, monkeypatch, tmp_path):
        huge = b"\xff\xd8\xff" + b"x" * downloader.MAX_COVER_BYTES
        data, said = self.fetch(monkeypatch, tmp_path, huge)
        assert data is None and "больше" in said[0]

    def test_a_page_where_a_picture_should_be_is_refused(self, monkeypatch, tmp_path):
        data, said = self.fetch(monkeypatch, tmp_path, b"<!doctype html><html>no image here</html>")
        assert data is None and "JPEG" in said[0]

    def test_no_address_means_no_request(self, tmp_path):
        assert downloader.Downloader(Options(tmp_path))._fetch_cover("") is None


def test_mmss():
    assert _mmss(0) == "0:00"
    assert _mmss(61.4) == "1:01"
    assert _mmss(3599) == "59:59"
