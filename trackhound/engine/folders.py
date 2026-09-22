"""Where the music of an album is on disk, as collections are really kept.

An album is a folder with music in it. Its files can reach into folders
below it: a disc per folder, or a title with a slash in it ("Weird
Fishes/Arpeggi", "Session 25/08/83") that some downloaders turned into
folders. Those are the album's own when their album tag says so; a folder
below with another album in its tags is an album of its own.

The cover is looked for under the names players and shops use for it, and
inside the files when there is no picture beside them.
"""

from __future__ import annotations

import threading
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TALB

from .downloader import FORMATS

AUDIO_SUFFIXES = {f".{name}" for name in FORMATS}
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
# The names a cover goes by, in order: this program's own, Windows Media
# Player's and foobar2000's, a scanner's
COVER_NAMES = ("cover", "folder", "front", "albumart", "album", "albumartlarge", "albumartsmall")
# How far below an album its own files are looked for
DEPTH = 4
# Without album tags to go by, a folder below with more tracks than this is
# taken for an album of its own; one or two are a title split by a slash
LOOSE_TRACKS = 2

_TAGS: dict[tuple[str, int], str] = {}
_TAGS_LOCK = threading.Lock()


def is_audio(path: Path) -> bool:
    # _part_ files are tracks still being downloaded
    return path.suffix.lower() in AUDIO_SUFFIXES and not path.name.startswith("_part_") and path.is_file()


def album_tag(path: Path) -> str:
    """The album a file says it is on, read once per version of the file; "" when it says nothing."""
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return ""
    with _TAGS_LOCK:
        if key in _TAGS:
            return _TAGS[key]
    try:
        if path.suffix.lower() == ".mp3":  # the one frame, not the lyrics and pictures around it
            frame = ID3(path, known_frames={"TALB": TALB}).get("TALB")
            values = frame.text if frame else None
        else:
            audio = MutagenFile(path, easy=True)
            values = (audio.tags or {}).get("album") if audio is not None else None
        name = str(values[0]).strip() if values else ""
    except Exception:  # a broken file, or one without tags, has no album to speak of
        name = ""
    with _TAGS_LOCK:
        if len(_TAGS) > 50_000:
            _TAGS.clear()
        _TAGS[key] = name
    return name


def album_files(folder: Path) -> tuple[list[Path], list[Path]]:
    """The album's files, those in its own folders below included, and the
    folders below that hold albums of their own. A folder with no music in it
    is no album: an artist's folder, say, whose albums are each a folder."""
    files = _direct(folder)
    if not files:
        return [], []
    own, apart = list(files), []
    tag = _key(album_tag(files[0]))
    for sub in _subfolders(folder):
        _gather(sub, tag, own, apart, 1)
    return sorted(own), apart


def cover_file(folder: Path) -> Path | None:
    """The picture that is the album's cover, beside its files."""
    try:
        pictures = {path.name.lower(): path for path in folder.iterdir()
                    if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file()}
    except OSError:
        return None
    for name in COVER_NAMES:
        for suffix in IMAGE_SUFFIXES:
            if f"{name}{suffix}" in pictures:
                return pictures[f"{name}{suffix}"]
    return None


def _gather(folder: Path, tag: str, own: list[Path], apart: list[Path], depth: int) -> None:
    files = _direct(folder)
    if files:
        other = _key(album_tag(files[0]))
        if (tag and other and other != tag) or (not (tag and other) and len(files) > LOOSE_TRACKS):
            apart.append(folder)
            return
        own.extend(files)
    if depth < DEPTH:
        for sub in _subfolders(folder):
            _gather(sub, tag, own, apart, depth + 1)


def _direct(folder: Path) -> list[Path]:
    try:
        return sorted(path for path in folder.iterdir() if is_audio(path))
    except OSError:
        return []


def _subfolders(folder: Path) -> list[Path]:
    try:
        return sorted(path for path in folder.iterdir()
                      if path.is_dir() and not path.name.startswith((".", "$")))
    except OSError:
        return []


def _key(name: str) -> str:
    return " ".join(name.casefold().split())
