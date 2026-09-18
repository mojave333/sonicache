"""Loudness tags: reading ffmpeg's meter, the album arithmetic, and the tags."""

import subprocess
import threading
from pathlib import Path

import pytest

from trackhound.engine import downloader, loudness
from trackhound.engine.downloader import Downloader, Options
from trackhound.engine.models import Album, Track

SUMMARY = """[Parsed_ebur128_0 @ 0000] t: 1.0 M: -20.1 S: -20.3
[Parsed_ebur128_0 @ 0000] Summary:

  Integrated loudness:
    I:         -9.6 LUFS
    Threshold: -19.8 LUFS

  True peak:
    Peak:        0.4 dBFS
"""


class TestMeter:
    def test_the_summary_is_read(self):
        found = loudness.parse_summary(SUMMARY, 200.0)
        assert found.lufs == -9.6 and found.seconds == 200.0
        assert found.peak == pytest.approx(10 ** (0.4 / 20))

    def test_silence_has_no_peak(self):
        found = loudness.parse_summary("Summary:\n I: -70.0 LUFS\n Peak: -inf dBFS\n")
        assert found.lufs == -70.0 and found.peak == 0.0

    def test_output_without_a_summary_says_nothing(self):
        assert loudness.parse_summary("Invalid data found when processing input") is None


class TestAlbum:
    def test_tracks_are_averaged_as_energy_by_length(self):
        shared = loudness.album([loudness.Loudness(-10.0, 0.9, 300), loudness.Loudness(-20.0, 0.4, 100)])
        # a tenth of the energy for a quarter of the time
        assert shared.lufs == pytest.approx(10 * __import__("math").log10((300 * 0.1 + 100 * 0.01) / 400))  # -11.11
        assert shared.peak == 0.9 and shared.seconds == 400

    def test_silent_tracks_do_not_drag_it_down(self):
        loud = loudness.Loudness(-12.0, 1.0, 200)
        assert loudness.album([loud, loudness.Loudness(-70.0, 0.0, 60)]).lufs == pytest.approx(-12.0)

    def test_nothing_audible_is_no_album(self):
        assert loudness.album([loudness.Loudness(-70.0, 0.0, 10)]) is None

    def test_gains_as_each_format_writes_them(self):
        level = loudness.Loudness(-25.43, 0.1, 9)
        assert loudness.gain_text(Path("a.m4a"), level) == "7.43 dB"
        assert loudness.gain_text(Path("a.mp3"), level) == "7.43 dB"
        assert loudness.gain_text(Path("a.opus"), level) == str(round(2.43 * 256))  # Q7.8, -23 LUFS


FFMPEG = downloader.find_tool("ffmpeg")
CODECS = {"m4a": ["-c:a", "aac", "-b:a", "128k"], "mp3": ["-c:a", "libmp3lame", "-q:a", "4"],
          "opus": ["-c:a", "libopus", "-b:a", "96k"]}


def tone(folder: Path, name: str, ext: str, volume: float, seconds: int) -> Path:
    path = folder / f"{name}.{ext}"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency=440:duration={seconds}", "-af", f"volume={volume}",
                    *CODECS[ext], str(path)], check=True)
    return path


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
class TestFiles:
    @pytest.mark.parametrize("ext", ["m4a", "mp3", "opus"])
    def test_real_files_are_measured_tagged_and_read_back(self, tmp_path, ext):
        loud = tone(tmp_path, "loud", ext, 0.8, 3)   # lavfi's sine is at 1/8 of full scale
        quiet = tone(tmp_path, "quiet", ext, 0.1, 2)
        assert loudness.apply([loud, quiet], FFMPEG, whole_album=True) == 2
        (loud_level, loud_album), (quiet_level, quiet_album) = (loudness._stored(loud),
                                                                loudness._stored(quiet))
        assert quiet_level.lufs == pytest.approx(loud_level.lufs - 18.1, abs=0.5)  # 0.1/0.8
        assert loud_album and loud_album == quiet_album
        if ext != "opus":
            assert loud_level.peak == pytest.approx(0.1, abs=0.01)

    def test_a_tagged_file_is_not_measured_again(self, tmp_path, monkeypatch):
        first = tone(tmp_path, "a", "m4a", 0.5, 2)
        loudness.apply([first], FFMPEG, whole_album=False)
        monkeypatch.setattr(loudness, "measure", lambda *a, **k: pytest.fail("measured twice"))
        assert loudness.apply([first], FFMPEG, whole_album=False) == 0

    def test_the_other_tags_survive(self, tmp_path):
        path = tone(tmp_path, "song", "mp3", 0.5, 2)
        album = Album(id="1", name="Record", artist="Band")
        downloader._write_tags(path, album, Track(id="1", title="Song", artists="Band", duration=2,
                                                  track_number=1), None)
        loudness.apply([path], FFMPEG, whole_album=False)
        from mutagen.id3 import ID3
        tags = ID3(path)
        assert str(tags["TIT2"]) == "Song" and "TXXX:REPLAYGAIN_TRACK_GAIN" in tags


class TestDownloaderWiring:
    def loader(self, tmp_path, monkeypatch, **options):
        calls = []
        monkeypatch.setattr(loudness, "apply", lambda paths, ffmpeg, **k: calls.append((list(paths), k)) or 0)
        d = Downloader(Options(tmp_path, **options), log=lambda message: None)
        d.ffmpeg = "ffmpeg"
        tracks = [Track(id=str(n), title=f"t{n}", artists="a", duration=1, track_number=n) for n in (1, 2)]
        for track in tracks:
            path = tmp_path / f"{track.id}.m4a"
            path.write_bytes(b"x")
            d._paths[track.id] = path
        return d, tracks, calls

    def test_off_by_default(self, tmp_path, monkeypatch):
        d, tracks, calls = self.loader(tmp_path, monkeypatch)
        d._tag_loudness(Album(id="1", name="n", artist="a"), tracks, single=False)
        assert calls == []

    def test_an_album_gets_an_album_gain(self, tmp_path, monkeypatch):
        d, tracks, calls = self.loader(tmp_path, monkeypatch, replaygain=True)
        d._tag_loudness(Album(id="1", name="n", artist="a", kind="album"), tracks, single=False)
        assert len(calls[0][0]) == 2 and calls[0][1]["whole_album"] is True

    def test_a_playlist_does_not(self, tmp_path, monkeypatch):
        d, tracks, calls = self.loader(tmp_path, monkeypatch, replaygain=True)
        d._tag_loudness(Album(id="1", name="n", artist="a", kind="playlist"), tracks, single=False)
        assert calls[0][1]["whole_album"] is False

    def test_a_dry_run_and_a_stop_measure_nothing(self, tmp_path, monkeypatch):
        d, tracks, calls = self.loader(tmp_path, monkeypatch, replaygain=True, dry_run=True)
        d._tag_loudness(Album(id="1", name="n", artist="a"), tracks, single=False)
        d.options.dry_run = False
        d.stop_event.set()
        d._tag_loudness(Album(id="1", name="n", artist="a"), tracks, single=False)
        assert calls == []
