"""Where the music of an album is on disk, as collections are really kept.

An album is a folder with music in it. Its files can reach into folders
below it: a disc per folder, or a title with a slash in it ("Weird
Fishes/Arpeggi", "Session 25/08/83") that some downloaders turned into
folders. Those are the album's own when their album tag says so; a folder
below with another album in its tags is an album of its own.

The cover is looked for under the names players and shops use for it, and
inside the files when there is no picture beside them.

A library scan asks about the same folders many times. Inside scanning() each
folder is listed once, and a file's size and times come from its folder's
listing: on Windows that is no call per file, where a stat of each one made a
slow or watched disk the larger part of opening the library.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
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
_SCAN = threading.local()


class _Listing:
    def __init__(self, entries: list[os.DirEntry]):
        self.entries = entries
        self.by_name = {entry.name: entry for entry in entries}


@contextmanager
def scanning():
    """Each folder is listed once while this lasts, on this thread; nested
    calls share the outer one's listings."""
    outer = getattr(_SCAN, "listings", None)
    if outer is None:
        _SCAN.listings = {}
    try:
        yield
    finally:
        if outer is None:
            _SCAN.listings = None


def listing(folder: Path) -> list[os.DirEntry]:
    """What is in a folder, in the order of its paths, each entry knowing
    whether it is a folder and its size and times."""
    return _listing(folder).entries


def _listing(folder: Path) -> _Listing:
    listings = getattr(_SCAN, "listings", None)
    key = str(folder)
    if listings is not None and key in listings:
        return listings[key]
    entries = []
    try:
        with os.scandir(folder) as found:
            for entry in found:
                try:
                    entry.is_dir()
                    entry.stat()  # asked now, while the listing holds the answer
                except OSError:
                    continue  # removed or locked while listed
                entries.append(entry)
    except OSError:
        pass
    entries.sort(key=lambda entry: os.path.normcase(entry.name))  # as paths sort: by case only where it counts
    result = _Listing(entries)
    if listings is not None:
        listings[key] = result
    return result


def stat(path: Path) -> os.stat_result:
    """A file's size and times: from its folder's listing during a scan, else asked for."""
    listings = getattr(_SCAN, "listings", None)
    if listings is not None:
        folder = listings.get(str(path.parent))
        entry = folder.by_name.get(path.name) if folder is not None else None
        if entry is not None:
            return entry.stat()
    return path.stat()


def is_audio(path: Path) -> bool:
    return _audio_name(path.name) and path.is_file()


def _audio_name(name: str) -> bool:
    # _part_ files are tracks still being downloaded
    return os.path.splitext(name)[1].lower() in AUDIO_SUFFIXES and not name.startswith("_part_")


def known_albums() -> dict[tuple[str, int], str]:
    """The album tags read so far, to be kept between runs."""
    with _TAGS_LOCK:
        return dict(_TAGS)


def remember_albums(known: dict[tuple[str, int], str]) -> None:
    """Album tags kept from an earlier run; a file changed since is read again."""
    with _TAGS_LOCK:
        for key, name in known.items():
            _TAGS.setdefault(key, name)


def album_tag(path: Path) -> str:
    """The album a file says it is on, read once per version of the file; "" when it says nothing."""
    try:
        key = (str(path), stat(path).st_mtime_ns)
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
    pictures = {entry.name.lower(): Path(entry.path) for entry in listing(folder)
                if os.path.splitext(entry.name)[1].lower() in IMAGE_SUFFIXES and entry.is_file()}
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
    return [Path(entry.path) for entry in listing(folder) if _audio_name(entry.name) and entry.is_file()]


def _subfolders(folder: Path) -> list[Path]:
    return [Path(entry.path) for entry in listing(folder)
            if entry.is_dir() and not entry.name.startswith((".", "$"))]


def _key(name: str) -> str:
    return " ".join(name.casefold().split())
