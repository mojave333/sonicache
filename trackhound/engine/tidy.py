"""Putting what was downloaded before in order, without downloading it again.

Older downloads lack what later versions write, such as the genre, lyrics and
a cover in every file, and files from elsewhere may lack most of their tags.
The release is looked up again, by the link an album folder keeps or else by
the names its files and folder carry, and each file gets only what it is
missing: nothing it already says is changed, and the audio is not touched.
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import folders, lyrics, sources
from .downloader import FORMATS, MARKER_NAME, _write_lrc, _write_tags, fetch_cover, read_tags
from .i18n import t
from .logs import log
from .matcher import _norm, _similarity
from .models import Release, Track

# How far a file's length may be from a track's for the file to be that track, in seconds
LENGTH_SLACK = 3
# How alike a file's title must be to a track's for the file to be that track
TITLE_LIKENESS = 0.85
AUDIO_SUFFIXES = {f".{name}" for name in FORMATS}
# "01. ", "1-01. ", "01 - " at the start of a file name
_NUMBER_RE = re.compile(r"^\d+(?:-\d+)?(?:\.\s*|\s+-\s+|\s+)")
_log = log.getChild("tidy")


@dataclass
class Outcome:
    found: bool = False  # the release was found and the files are its tracks
    files: int = 0  # files that got at least one tag
    tags: int = 0  # tags written in all
    cover: bool = False  # a cover.jpg was put into the album folder
    lyrics: int = 0  # files that got lyrics


def tidy(entry: Path, artist: str = "", title: str = "", with_lyrics: bool = True,
         stop: threading.Event | None = None, workers: int = 4) -> Outcome:
    """Fills in what the files of one library entry lack. The entry is an
    album folder or a single track; artist and title are what its name says,
    for files that do not say it themselves."""
    files = folders.album_files(entry)[0] if entry.is_dir() else [entry]
    known = {file: read_tags(file) for file in files}
    known = {file: tags for file, tags in known.items() if tags}  # unreadable files are left alone
    outcome = Outcome()
    if not known:
        return outcome
    release, trusted = _release(entry, known, artist, title)
    if release is None:
        return outcome
    album = release.album
    places = {file: _place(file, tags, release) for file, tags in known.items()}
    placed = sum(1 for track in places.values() if track)
    # A release found by name is taken only when most files are its tracks;
    # the link an album was downloaded from is its release whatever happened since
    if not placed or (not trusted and placed * 2 < len(known)):
        _log.info("«%s»: найденный релиз «%s — %s» не подошёл (%d из %d файлов)",
                  entry.name, album.artist, album.name, placed, len(known))
        return outcome
    outcome.found = True

    if album.kind != "playlist" and any(not tags.get("genre") for tags in known.values()):
        try:
            album.genre = sources.find_genre(album.artist, album.name, album.genre)
        except Exception as e:  # the genre is a nicety, as in a download
            _log.warning("жанр для «%s» не нашёлся: %s", album.name, e)
    # A folder with a picture of its own under any usual name keeps it and gets no cover.jpg beside it
    folder_cover = entry / "cover.jpg" if entry.is_dir() and folders.cover_file(entry) is None else None
    cover = None
    if any(not tags.get("cover") for tags in known.values()) or folder_cover:
        cover = fetch_cover(album.cover_url, _log.warning)
    if cover and folder_cover:
        try:
            folder_cover.write_bytes(cover)
            outcome.cover = True
        except OSError as e:
            _log.warning("не записал %s: %s", folder_cover, e)

    def fill(file: Path) -> list[str]:
        if stop is not None and stop.is_set():
            return []
        tags = known[file]
        track = places[file] or _own_track(file, tags, album.artist)
        words = lyrics.find(track, album) if with_lyrics and not tags.get("lyrics") else None
        try:
            written = _write_tags(file, album, track, cover, words.plain if words else "", fill=True)
        except Exception as e:  # a file mutagen cannot save costs only itself
            _log.warning("не дописал теги в %s: %s", file.name, e)
            return []
        if words and words.synced and not file.with_suffix(".lrc").exists():
            _write_lrc(file, words.synced)
        return written

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for written in pool.map(fill, list(known)):
            outcome.files += bool(written)
            outcome.tags += len(written)
            outcome.lyrics += "lyrics" in written
    return outcome


def _release(entry: Path, known: dict[Path, dict], artist: str, title: str) -> tuple[Release | None, bool]:
    """The release the entry is, and whether it came from the link it was
    downloaded from rather than a search by name."""
    if entry.is_dir():
        link = _marker(entry).get("link")
        if link:
            try:
                return sources.resolve(link), True
            except Exception as e:  # gone or out of reach: the names may still find it
                _log.warning("«%s»: ссылка %s не открылась: %s", entry.name, link, e)
        album = _most([tags.get("album") for tags in known.values()]) or title
        who = (_most([tags.get("albumartist") for tags in known.values()])
               or _most([_first_artist(tags.get("artist")) for tags in known.values()]) or artist)
        if not album or not who:
            return None, False
        return sources.find_album(who, album, t("поиск")), False
    tags = known[entry]
    song = tags.get("title") or title
    who = tags.get("artist") or artist
    if not song or not who:
        return None, False
    try:
        release = sources.find_track(_first_artist(who), song, t("поиск"), album=tags.get("album", ""))
    except Exception as e:
        _log.warning("«%s» не нашёлся: %s", entry.name, e)
        return None, False
    if not release.album.cover_url and not release.tracks[0].duration:
        return None, False  # known to no catalogue: only its own name came back
    return release, False


def _place(file: Path, tags: dict, release: Release) -> Track | None:
    """The track of the release this file is, or None."""
    length = tags.get("duration") or 0

    def fits_length(track: Track) -> bool:
        return not length or not track.duration or abs(length - track.duration) <= LENGTH_SLACK

    names = _names(file, tags)
    best, best_score = None, 0.0
    for track in release.tracks:
        if not fits_length(track):
            continue
        score = max(_similarity(_norm(name), _norm(track.title)) for name in names)
        # Two takes of one song on an album: the one at the file's place wins
        if (tags.get("track"), tags.get("disc") or 1) == (track.track_number, track.disc_number):
            score += 0.01
        if score > best_score:
            best, best_score = track, score
    if best is not None and best_score >= TITLE_LIKENESS:
        return best
    if release.single:
        return None
    # No title to go by ("Track 01"): the place and the length together will do
    for track in release.tracks:
        if (tags.get("track"), tags.get("disc") or 1) == (track.track_number, track.disc_number):
            if length and track.duration and abs(length - track.duration) <= LENGTH_SLACK:
                return track
    return None


def _names(file: Path, tags: dict) -> list[str]:
    """What the file may be called: its title tag, and its file name without
    the number, with and without the performer in front."""
    stem = _NUMBER_RE.sub("", file.stem)
    names = [tags.get("title") or "", stem, stem.partition(" - ")[2]]
    return [name for name in names if name.strip()] or [file.stem]


def _own_track(file: Path, tags: dict, album_artist: str) -> Track:
    """A file that is none of the release's tracks keeps its own names and
    gets no number; it still gets the album's genre, year and cover."""
    return Track(id=file.name, title=tags.get("title") or _NUMBER_RE.sub("", file.stem),
                 artists=tags.get("artist") or album_artist, duration=tags.get("duration") or 0,
                 track_number=0)


def _is_audio(path: Path) -> bool:
    # _part_ files are tracks still being downloaded
    return path.suffix.lower() in AUDIO_SUFFIXES and not path.name.startswith("_part_") and path.is_file()


def _marker(folder: Path) -> dict:
    try:
        data = json.loads((folder / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _most(values: list) -> str:
    """The value most files agree on, "" when none has one."""
    counted = Counter(value.strip() for value in values if value and value.strip())
    return counted.most_common(1)[0][0] if counted else ""


def _first_artist(artists: str | None) -> str:
    return (artists or "").split(",")[0].split(";")[0].strip()
