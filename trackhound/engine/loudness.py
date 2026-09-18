"""Loudness tags, so tracks from different sources play at one volume.

The audio is left exactly as it is. Each file is measured once with ffmpeg's
EBU R128 meter and gets a gain its player applies on playback: ReplayGain 2.0
for m4a and mp3, and for Opus the R128 tags its specification asks for
instead (RFC 7845). An album also gets one gain shared by all its tracks, so a
quiet interlude stays quiet beside a loud single.

A file that already carries a track gain is not measured again. A watched
playlist is checked twice a day, and listening to every track each time would
cost minutes for nothing; the album gain is worked out afresh from the stored
values instead, which is arithmetic.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TXXX, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggopus import OggOpus

REFERENCE = -18.0       # LUFS, ReplayGain 2.0
OPUS_REFERENCE = -23.0  # LUFS, the R128_* tags of RFC 7845
SILENCE = -70.0         # the meter's floor; such a track says nothing about an album

_ITUNES = "----:com.apple.iTunes:"
_SUMMARY_I = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_SUMMARY_PEAK = re.compile(r"Peak:\s*(-?\d+(?:\.\d+)?|-inf)\s*dBFS")


@dataclass
class Loudness:
    lufs: float
    peak: float     # linear, 1.0 is full scale; 0 when the format keeps none
    seconds: float


def measure(path: Path, ffmpeg: str) -> Loudness | None:
    """Integrated loudness and true peak, or None when ffmpeg cannot say."""
    command = [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
               # framelog=quiet: without it the meter prints a line per 100 ms of audio,
               # hundreds of kilobytes a track, and piping that costs a third of the time
               "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # no console flashing up
    try:
        done = subprocess.run(command, capture_output=True, timeout=600, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_summary(done.stderr.decode("utf-8", "replace"), _seconds(path))


def parse_summary(text: str, seconds: float = 0.0) -> Loudness | None:
    """Reads the Summary block ffmpeg's ebur128 filter prints when it is done."""
    summary = text.rpartition("Summary:")[2]
    level = _SUMMARY_I.search(summary)
    if not level:
        return None
    peak = _SUMMARY_PEAK.search(summary)
    peak_db = peak.group(1) if peak else "-inf"
    linear = 0.0 if peak_db == "-inf" else 10 ** (float(peak_db) / 20)
    return Loudness(float(level.group(1)), linear, seconds)


def album(tracks: Iterable[Loudness]) -> Loudness | None:
    """One loudness for the whole album, from its tracks' measurements.

    Loudness is logarithmic, so the tracks are averaged as energy and weighted
    by how long each plays. It differs from metering the album end to end only
    by gating, well inside what anyone can hear.
    """
    audible = [track for track in tracks if track.lufs > SILENCE and track.seconds > 0]
    if not audible:
        return None
    total = sum(track.seconds for track in audible)
    energy = sum(track.seconds * 10 ** (track.lufs / 10) for track in audible) / total
    return Loudness(10 * math.log10(energy), max(track.peak for track in audible), total)


def apply(paths: Iterable[Path], ffmpeg: str, whole_album: bool,
          log: Callable[[str], None] = lambda message: None,
          stop: threading.Event | None = None, workers: int = 1) -> int:
    """Measures what is not measured yet and writes the tags.

    Metering decodes the whole file, a few seconds a track, so files are
    measured side by side on as many threads as the downloads used. Answers how
    many files had to be measured.
    """
    found: dict[Path, Loudness] = {}
    album_written: dict[Path, str] = {}
    pending: list[Path] = []
    for path in paths:
        if not path.exists():
            continue
        known, album_text = _stored(path)
        album_written[path] = album_text or ""
        if known is None:
            pending.append(path)
        else:
            found[path] = known

    def one(path: Path) -> tuple[Path, Loudness | None]:
        if stop is not None and stop.is_set():
            return path, None
        return path, measure(path, ffmpeg)

    measured = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for path, known in pool.map(one, pending):
            if known is None:
                if stop is None or not stop.is_set():
                    log(f"! {path.name}: ffmpeg did not measure the loudness")
                continue
            _write(path, track=known)
            found[path] = known
            measured += 1
    if stop is not None and stop.is_set():
        return measured

    shared = album(found.values()) if whole_album and len(found) > 1 else None
    if shared is not None:
        for path in found:
            # Rewritten only when it moved: a check that added nothing touches no file
            if album_written[path] != gain_text(path, shared):
                _write(path, album=shared)
    return measured


def gain_text(path: Path, loudness: Loudness) -> str:
    """The gain exactly as it is written into this file's tags."""
    if path.suffix.lower() == ".opus":
        return str(_q78(OPUS_REFERENCE - loudness.lufs))
    return f"{REFERENCE - loudness.lufs:.2f} dB"


def _q78(decibels: float) -> int:
    """Opus keeps gain as a signed Q7.8 number of decibels."""
    return max(-32768, min(32767, round(decibels * 256)))


def _seconds(path: Path) -> float:
    try:
        info = MutagenFile(path)
        return float(info.info.length) if info is not None else 0.0
    except Exception:
        return 0.0


def _stored(path: Path) -> tuple[Loudness | None, str | None]:
    """The track loudness a file already carries, and its album gain as written."""
    ext = path.suffix.lower()
    try:
        if ext == ".opus":
            tags = OggOpus(path)
            track = (tags.get("R128_TRACK_GAIN") or [None])[0]
            album_gain = (tags.get("R128_ALBUM_GAIN") or [None])[0]
            if track is None:
                return None, album_gain
            return Loudness(OPUS_REFERENCE - int(track) / 256, 0.0, _seconds(path)), album_gain

        if ext == ".m4a":
            items = MP4(path).tags or {}

            def read(key):
                value = (items.get(_ITUNES + key) or [b""])[0]
                return bytes(value).decode("utf-8", "replace") or None
        elif ext == ".mp3":
            try:
                frames = ID3(path)
            except ID3NoHeaderError:
                return None, None

            def read(key):
                frame = frames.get(f"TXXX:{key}")
                return str(frame.text[0]) if frame is not None and frame.text else None
        else:
            return None, None

        gain, peak, album_gain = (read("REPLAYGAIN_TRACK_GAIN"), read("REPLAYGAIN_TRACK_PEAK"),
                                  read("REPLAYGAIN_ALBUM_GAIN"))
        if gain is None:
            return None, album_gain
        decibels = float(gain.split()[0])
        return Loudness(REFERENCE - decibels, float(peak) if peak else 0.0, _seconds(path)), album_gain
    except (ValueError, OSError, IndexError, KeyError):
        return None, None


def _write(path: Path, track: Loudness | None = None, album: Loudness | None = None) -> None:
    ext = path.suffix.lower()
    fields = {}
    for scope, loudness in (("TRACK", track), ("ALBUM", album)):
        if loudness is None:
            continue
        if ext == ".opus":
            fields[f"R128_{scope}_GAIN"] = str(_q78(OPUS_REFERENCE - loudness.lufs))
        else:
            fields[f"REPLAYGAIN_{scope}_GAIN"] = f"{REFERENCE - loudness.lufs:.2f} dB"
            fields[f"REPLAYGAIN_{scope}_PEAK"] = f"{loudness.peak:.6f}"
    if not fields:
        return
    if ext == ".opus":
        audio = OggOpus(path)
        for key, value in fields.items():
            audio[key] = value
        audio.save()
    elif ext == ".m4a":
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        for key, value in fields.items():
            audio.tags[_ITUNES + key] = [MP4FreeForm(value.encode("utf-8"))]
        audio.save()
    elif ext == ".mp3":
        try:
            frames = ID3(path)
        except ID3NoHeaderError:
            frames = ID3()
        for key, value in fields.items():
            frames.delall(f"TXXX:{key}")
            frames.add(TXXX(encoding=3, desc=key, text=[value]))
        frames.save(path, v2_version=3)
