"""File names, error wording and the checks that guard a finished download."""

import json
import subprocess
import threading

import pytest
from mutagen import File as MutagenFile

from trackhound.engine import downloader
from trackhound.engine.downloader import (DownloaderError, Options, Report, _check_duration, _clear_partials,
                                   _direct_match, _error_text, _file_stem, _legacy_stem, _mmss,
                                   _safe_name)
from trackhound.engine.models import Album, Track


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


class TestNamingStyles:
    """The same release, named three ways."""

    def guest(self):
        return Track(id="1", title="Instant Crush", artists="Daft Punk, Julian Casablancas",
                     duration=0, track_number=5)

    def own(self):
        return Track(id="2", title="Aerodynamic", artists="Daft Punk", duration=0, track_number=2)

    @pytest.mark.parametrize("style, expected", [
        ("auto", "05. Daft Punk, Julian Casablancas - Instant Crush"),
        ("artist", "05. Daft Punk, Julian Casablancas - Instant Crush"),
        ("title", "05. Instant Crush"),
    ])
    def test_a_guest_track(self, style, expected):
        assert _file_stem(album(), self.guest(), False, style) == expected

    @pytest.mark.parametrize("style, expected", [
        ("auto", "02. Aerodynamic"),
        ("artist", "02. Daft Punk - Aerodynamic"),
        ("title", "02. Aerodynamic"),
    ])
    def test_a_track_by_the_album_artist(self, style, expected):
        assert _file_stem(album(), self.own(), False, style) == expected

    def test_a_single_is_named_the_same_whatever_the_style(self):
        for style in ("auto", "artist", "title"):
            assert _file_stem(album(), self.own(), True, style) == "Daft Punk - Aerodynamic"


class TestAlbumFolder:
    @pytest.mark.parametrize("style, expected", [
        ("flat", ["Daft Punk - Discovery (2001)"]),
        ("nested", ["Daft Punk", "Discovery (2001)"]),
        ("album", ["Discovery (2001)"]),
    ])
    def test_the_three_layouts(self, tmp_path, style, expected):
        record = album()
        record.release_date = "2001-03-12"
        folder = downloader._album_folder(tmp_path, record, style)
        assert folder.relative_to(tmp_path).parts == tuple(expected)

    def test_an_album_without_a_year(self, tmp_path):
        folder = downloader._album_folder(tmp_path, album(), "flat")
        assert folder.name == "Daft Punk - Discovery"

    def test_an_album_without_an_artist_is_named_by_itself(self, tmp_path):
        record = album(artist="")
        assert downloader._album_folder(tmp_path, record, "flat").name == "Discovery"
        assert downloader._album_folder(tmp_path, record, "nested").name == "Discovery"

    def test_unsafe_characters_never_reach_the_disk(self, tmp_path):
        record = album(artist="AC/DC", name="Back in Black: Live?")
        folder = downloader._album_folder(tmp_path, record, "nested")
        assert folder.relative_to(tmp_path).parts == ("AC-DC", "Back in Black - Live")


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


class TestPause:
    """Pausing holds back the next track; what is downloading is left alone."""

    def loader(self, tmp_path, **kwargs):
        return downloader.Downloader(Options(tmp_path), log=lambda message: None, **kwargs)

    def test_a_fresh_downloader_is_not_paused(self, tmp_path):
        assert self.loader(tmp_path).resume_event.is_set()

    def test_a_paused_track_waits_before_it_starts(self, tmp_path):
        import threading

        resume = threading.Event()  # cleared: paused
        loader = self.loader(tmp_path, resume_event=resume)
        track = Track(id="1", title="X", artists="Y", duration=10, track_number=1)
        started = threading.Event()
        done = threading.Event()

        def run():
            started.set()
            loader._process(album(), track, tmp_path, False, None)
            done.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        started.wait(1)
        assert not done.wait(0.3)  # still held back
        loader.stop_event.set()  # let it go without downloading anything
        assert done.wait(2)

    def test_stopping_releases_a_paused_track(self, tmp_path):
        import threading

        resume = threading.Event()
        loader = self.loader(tmp_path, resume_event=resume)
        loader.stop_event.set()
        track = Track(id="1", title="X", artists="Y", duration=10, track_number=1)
        assert loader._process(album(), track, tmp_path, False, None) is None


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
        assert (options.audio_format, options.threads, options.dry_run) == ("mp3", 3, False)


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


class TestFormats:
    """Which stream is asked for, and what ffmpeg is told to make of it."""

    def fetch(self, monkeypatch, tmp_path, audio_format, ffmpeg="ffmpeg", source="song", acodec="opus",
              ext="webm"):
        seen, converted = {}, {}

        class FakeYoutubeDL:
            def __init__(self, opts):
                seen.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download):
                path = tmp_path / f"stem.{ext}"
                path.write_bytes(b"audio")
                return {"acodec": acodec, "duration": 200, "requested_downloads": [{"filepath": str(path)}]}

        def convert(ffmpeg_path, source_path, target, audio_format, copy, duration, on_fraction=None, stop=None):
            converted.update(source=source_path.name, target=target.name, copy=copy, duration=duration)
            target.write_bytes(b"converted")

        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL)
        monkeypatch.setattr(downloader, "_convert", convert)
        loader = downloader.Downloader(Options(tmp_path, audio_format), log=lambda message: None)
        loader.ffmpeg = ffmpeg
        match = downloader.Match(source=source, url="https://music.youtube.test/watch?v=x", page_url="",
                                 title="T", artists="A", duration=200, score=1.0)
        track = Track(id="1", title="T", artists="A", duration=200, track_number=1)
        path = loader._download_audio(match, tmp_path, "stem", track, "YouTube Music")
        return seen, converted, path

    def test_m4a_is_made_from_the_full_band_opus(self, monkeypatch, tmp_path):
        opts, converted, path = self.fetch(monkeypatch, tmp_path, "m4a")
        assert opts["format"].index("bestaudio[acodec=opus]") < opts["format"].index("bestaudio[ext=m4a]/")
        assert opts["format"].startswith("bestaudio[ext=m4a][abr>=200]")  # Premium's AAC 256 as is
        assert converted == {"source": "stem.webm", "target": "stem.m4a", "copy": False, "duration": 200}
        assert path.name == "stem.m4a"

    def test_premium_aac_is_only_rewrapped(self, monkeypatch, tmp_path):
        _, converted, _ = self.fetch(monkeypatch, tmp_path, "m4a", acodec="mp4a.40.2", ext="m4a")
        assert converted["copy"] is True

    def test_m4a_without_ffmpeg_takes_youtubes_own_aac(self, monkeypatch, tmp_path):
        opts, converted, path = self.fetch(monkeypatch, tmp_path, "m4a", ffmpeg=None, acodec="mp4a.40.2", ext="m4a")
        assert opts["format"].startswith("bestaudio[ext=m4a]/")
        assert converted == {} and path.name == "stem.m4a"

    def test_mp3_is_encoded_and_opus_only_rewrapped(self, monkeypatch, tmp_path):
        assert self.fetch(monkeypatch, tmp_path, "mp3")[1]["copy"] is False
        assert self.fetch(monkeypatch, tmp_path, "opus")[1]["copy"] is True

    def test_soundclouds_mp3_stays_as_it_is(self, monkeypatch, tmp_path):
        _, converted, _ = self.fetch(monkeypatch, tmp_path, "mp3", source="soundcloud", acodec="mp3", ext="mp3")
        assert converted["copy"] is True

    def test_yt_dlp_converts_nothing_itself(self, monkeypatch, tmp_path):
        opts, _, _ = self.fetch(monkeypatch, tmp_path, "mp3")
        assert "postprocessors" not in opts and "postprocessor_args" not in opts

    def test_mp3_comes_first_and_is_the_default(self, tmp_path):
        assert downloader.FORMATS[0] == "mp3" == Options(tmp_path).audio_format


@pytest.mark.skipif(not downloader.find_tool("ffmpeg"), reason="needs ffmpeg")
class TestConvert:
    """ffmpeg makes the format asked for and says how far it has got."""

    FFMPEG = downloader.find_tool("ffmpeg")

    def source(self, tmp_path, seconds=20):
        path = tmp_path / "_part_1.webm"
        subprocess.run([self.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "libopus", "-b:a", "96k",
                        str(path)], check=True)
        return path

    @pytest.mark.parametrize("audio_format, copy, kind", [
        ("mp3", False, "MP3"), ("m4a", False, "MP4"), ("opus", True, "OggOpus"),
    ])
    def test_every_format_comes_out(self, tmp_path, audio_format, copy, kind):
        source = self.source(tmp_path)
        target = tmp_path / f"_part_1.{audio_format}"
        seen = []
        downloader._convert(self.FFMPEG, source, target, audio_format, copy, 20, on_fraction=seen.append)
        audio = MutagenFile(target)
        assert type(audio).__name__ == kind and audio.info.length == pytest.approx(20, abs=0.5)
        if audio_format == "mp3":
            assert (audio.info.sample_rate, round(audio.info.bitrate / 1000)) == (44100, 320)
        assert not source.exists() and not list(tmp_path.glob("*.converting.*"))
        assert seen and seen == sorted(seen) and seen[-1] == pytest.approx(1, abs=0.1)

    def test_a_stream_ffmpeg_cannot_read_is_a_coded_failure(self, tmp_path):
        broken = tmp_path / "_part_1.webm"
        broken.write_bytes(b"not audio at all")
        with pytest.raises(DownloaderError) as failed:
            downloader._convert(self.FFMPEG, broken, tmp_path / "_part_1.mp3", "mp3", False, 20)
        assert failed.value.code == "convert_failed"
        assert not list(tmp_path.glob("*.mp3"))

    def test_a_stop_ends_the_conversion(self, tmp_path):
        stop = threading.Event()
        stop.set()
        with pytest.raises(downloader.DownloadCancelled):
            downloader._convert(self.FFMPEG, self.source(tmp_path, 60), tmp_path / "_part_1.mp3", "mp3", False, 60,
                                stop=stop)


class TestSteps:
    """What the window is told while a track goes through the downloader."""

    def loader(self, tmp_path, events):
        return downloader.Downloader(Options(tmp_path), log=lambda message: None,
                                     events=lambda kind, data: events.append(data))

    def match(self, url="https://music.youtube.test/watch?v=x"):
        return downloader.Match(source="song", url=url, page_url="", title="T", artists="A",
                                duration=200, score=1.0)

    def download(self, monkeypatch, tmp_path, retry=False):
        class FakeYoutubeDL:
            def __init__(self, opts):
                self.hooks = opts["progress_hooks"]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download):
                for status in ({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100, "eta": 1},
                               {"status": "finished", "total_bytes": 100}):
                    for hook in self.hooks:
                        hook(status)
                (tmp_path / "stem.webm").write_bytes(b"audio")
                return {"acodec": "opus", "requested_downloads": [{"filepath": str(tmp_path / "stem.webm")}]}

        def convert(ffmpeg, source, target, audio_format, copy, duration, on_fraction=None, stop=None):
            on_fraction(0.5)
            target.write_bytes(b"converted")

        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL)
        monkeypatch.setattr(downloader, "_convert", convert)
        monkeypatch.setattr(downloader, "_check_duration", lambda path, track: None)  # the file is fake
        events = []
        loader = self.loader(tmp_path, events)
        loader.ffmpeg = "ffmpeg"
        track = Track(id="1", title="T", artists="A", duration=200, track_number=1)
        loader._clocks["1"] = {"clock": downloader.Clock(200), "track": track, "source": "", "retry": False}
        loader._fetch(self.match(), tmp_path, "stem", track, "YouTube Music", retry)
        return loader, events

    def test_a_download_says_how_far_and_how_long(self, monkeypatch, tmp_path):
        loader, events = self.download(monkeypatch, tmp_path)
        first = events[0]
        assert (first["state"], first["source"], first["retry"]) == ("download", "YouTube Music", False)
        assert 0 <= first["percent"] < 100 and first["eta"] > 0

    def test_a_download_from_a_second_source_says_so(self, monkeypatch, tmp_path):
        loader, events = self.download(monkeypatch, tmp_path, retry=True)
        assert events[0]["retry"] is True

    def test_ticks_move_on_and_stop_with_the_track(self, monkeypatch, tmp_path):
        loader, events = self.download(monkeypatch, tmp_path)
        loader._tick()
        loader._tick()
        ticks = events[1:]
        assert ticks and all(event["state"] == "download" and "eta" in event for event in ticks)
        assert [event["percent"] for event in ticks] == sorted(event["percent"] for event in ticks)
        loader._track_event(Track(id="1", title="T", artists="A", duration=200, track_number=1), "done")
        count = len(events)
        loader._tick()
        assert len(events) == count  # nothing after "done" could set the row back to downloading

    def test_the_second_search_is_the_wide_one(self, tmp_path):
        events = []
        loader = self.loader(tmp_path, events)
        loader.matcher = type("Matcher", (), {"find_all": lambda self, track, album, wide: []})()
        track = Track(id="1", title="X", artists="Y", duration=10, track_number=1)
        loader._process(album(), track, tmp_path, False, None)
        assert [(event["state"], event.get("wide")) for event in events] == [
            ("search", False), ("search", True), ("missing", None)]

    def test_the_source_after_a_refusal_is_fetched_as_a_retry(self, tmp_path):
        events = []
        loader = self.loader(tmp_path, events)
        matches = [self.match("https://first.test"), self.match("https://second.test")]
        loader.matcher = type("Matcher", (), {"find_all": lambda self, track, album, wide: list(matches)})()
        fetched = []

        def fetch(match, folder, stem, track, source, retry=False):
            fetched.append((match.url, retry))
            if len(fetched) == 1:
                raise DownloaderError("refused")
            raise downloader.DownloadCancelled("enough")

        loader._fetch = fetch
        track = Track(id="1", title="X", artists="Y", duration=10, track_number=1)
        loader._process(album(), track, tmp_path, False, None)
        assert fetched == [("https://first.test", False), ("https://second.test", True)]


class TestFailures:
    """A track that does not arrive is reported with a code as well as a sentence."""

    def process(self, tmp_path, error=None, found=True):
        events = []
        loader = downloader.Downloader(Options(tmp_path), log=lambda message: None,
                                       events=lambda kind, data: events.append(data))
        match = downloader.Match(source="song", url="https://only.test", page_url="", title="T",
                                 artists="A", duration=200, score=1.0)
        loader.matcher = type("Matcher", (), {"find_all": lambda self, track, album, wide: [match] if found else []})()

        def fetch(*args, **kwargs):
            raise error

        loader._fetch = fetch
        track = Track(id="1", title="X", artists="Y", duration=10, track_number=1)
        return loader._process(album(), track, tmp_path, False, None), events[-1]

    def test_a_track_found_nowhere_is_no_source(self, tmp_path):
        (kind, line, failure), event = self.process(tmp_path, found=False)
        assert (kind, failure.code, event["state"], event["code"]) == ("failed", "no_source", "missing", "no_source")
        assert line == f"Y - X: {failure.message}"

    @pytest.mark.parametrize("error, code", [
        (DownloaderError.of("preview_only", "скачался фрагмент {got} вместо {expected}", got="0:30", expected="3:20"),
         "preview_only"),
        (Exception("ERROR: Sign in to confirm your age"), "age_restricted"),
        (Exception("ERROR: HTTP Error 403: Forbidden"), "download_failed"),
    ])
    def test_a_refused_download_keeps_its_code(self, tmp_path, error, code):
        (kind, line, failure), event = self.process(tmp_path, error)
        assert (kind, failure.code, event["state"], event["code"]) == ("failed", code, "error", code)
        assert line == f"Y - X: {failure.message}" and failure.message == event["text"]

    def test_the_report_lists_the_failures_beside_the_lines(self, tmp_path):
        loader = downloader.Downloader(Options(tmp_path, dry_run=True), log=lambda message: None)
        track = Track(id="1", title="X", artists="Y", duration=10, track_number=1)
        failure = downloader.Failure(track, "no_source", "nowhere")
        loader._process = lambda album, track, folder, single, cover: ("failed", "Y - X: nowhere", failure)
        report = loader._download_tracks(album(), [track], tmp_path, False)
        assert (report.failed, report.failures) == (["Y - X: nowhere"], [failure])


class TestDoubts:
    """A track whose match may be the wrong song waits for a person's choice."""

    def loader(self, tmp_path, events, **options):
        return downloader.Downloader(Options(tmp_path, **options), log=lambda message: None,
                                     events=lambda kind, data: events.append(data))

    def match(self, title, score, duration=10):
        return downloader.Match(source="song", url=f"https://music.test/{title}", page_url=f"https://page.test/{title}",
                                title=title, artists="Y", duration=duration, score=score)

    def process(self, tmp_path, matches, **options):
        events, fetched = [], []
        loader = self.loader(tmp_path, events, **options)
        loader.matcher = type("Matcher", (), {"find_all": lambda self, track, album, wide: list(matches)})()

        def fetch(match, *args, **kwargs):
            fetched.append(match.url)
            raise downloader.DownloadCancelled("enough")  # nothing is downloaded for real

        loader._fetch = fetch
        track = Track(id="1", title="X", artists="Y", duration=10, track_number=1)
        return loader._process(album(), track, tmp_path, False, None), events, fetched

    def test_a_doubtful_match_is_held_with_its_candidates(self, tmp_path):
        matches = [self.match("X", 0.8), self.match("X (Live)", 0.7, 30)]
        (kind, line, doubt), events, fetched = self.process(tmp_path, matches, ask=True)
        assert (kind, line, fetched) == ("doubtful", "Y - X", [])
        assert [c.title for c in doubt.candidates] == ["X", "X (Live)"]
        event = events[-1]
        assert event["state"] == "doubtful"
        assert [(c["title"], c["score"], c["source_name"]) for c in event["candidates"]] == [
            ("X", 0.8, "YouTube Music"), ("X (Live)", 0.7, "YouTube Music")]

    def test_without_asking_the_best_is_downloaded_as_before(self, tmp_path):
        result, events, fetched = self.process(tmp_path, [self.match("X", 0.8)])
        assert result is None and fetched == ["https://music.test/X"]  # cancelled mid-download

    def test_a_clear_match_is_not_held(self, tmp_path):
        result, events, fetched = self.process(tmp_path, [self.match("X", 1.1)], ask=True)
        assert fetched == ["https://music.test/X"] and "doubtful" not in [e["state"] for e in events]

    def test_a_chosen_match_is_downloaded_without_a_search(self, tmp_path):
        chosen = self.match("X (the right one)", 0.7)
        result, events, fetched = self.process(tmp_path, [self.match("X", 1.1)], ask=True, choices={"1": chosen})
        assert fetched == ["https://music.test/X (the right one)"]
        assert "search" not in [e["state"] for e in events]

    def test_the_report_counts_what_waits(self, tmp_path):
        loader = self.loader(tmp_path, [], dry_run=True)
        track = Track(id="1", title="X", artists="Y", duration=10, track_number=1)
        doubt = downloader.Doubt(track, [self.match("X", 0.8)])
        loader._process = lambda album, track, folder, single, cover: ("doubtful", "Y - X", doubt)
        report = loader._download_tracks(album(), [track], tmp_path, False)
        assert (report.doubtful, report.doubts) == (["Y - X"], [doubt])
        assert "ждут выбора: 1" in report.summary()

    def test_only_the_chosen_tracks_of_a_release_are_downloaded(self, tmp_path, monkeypatch):
        whole = album()
        monkeypatch.setattr(downloader.sources, "resolve",
                            lambda link: downloader.sources.Release(whole, whole.tracks))
        loader = self.loader(tmp_path, [], dry_run=True, choices={"2": self.match("Track 2", 0.7)})
        seen = []
        loader._download_tracks = lambda album, tracks, folder, single, link="": seen.append(
            [t.id for t in tracks]) or downloader.Report()
        loader.download_link("https://open.spotify.com/album/x")
        assert seen == [["2"]]
