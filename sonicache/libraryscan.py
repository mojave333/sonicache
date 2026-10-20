"""The library as the window lists it: albums and tracks read from the folders and their tags,
the covers and lyrics inside the files, which albums lack tags, and what is kept of all
that between runs; also the albums seen and the favourites.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import Frames
from mutagen.mp3 import MP3

from . import logs, playlists
from .engine import folders
from .engine.downloader import MARKER_NAMES, read_marker, read_tags

# Album folders are named by the downloader as "Artist - Album (Year)", single tracks as "Artist - Title"
_ALBUM_NAME = re.compile(r"^(?P<artist>.+?) - (?P<title>.+?)(?: \((?P<year>\d{4})\))?$")

_TRACK_NAME = re.compile(r"^(?P<artist>.+?) - (?P<title>.+)$")

# An album inside an artist's folder is named without the artist: "Discovery (2001)"
_ALBUM_UNDER_ARTIST = re.compile(r"^(?P<title>.+?)(?: \((?P<year>\d{4})\))?$")

# Library entries tidied up, with their files as they were after it: as long
# as they stay so, the Fill in tags button does not offer them again, even
# where a genre or lyrics could not be found
TIDIED_FILE = "tidied.json"

# The albums opened or played since they arrived: their covers no longer say "New".
# The mark lasts a week at most (web/js/pages.js), so older entries are dropped
SEEN_FILE = "seen.json"

# The albums marked with a heart: a list of their folders
FAVOURITES_FILE = "favourites.json"

SEEN_FOR = 8 * 24 * 3600

# The tags read from the library's files, kept between runs so that opening
# the library reads again only the files changed since
LIBRARY_CACHE_FILE = "library-cache.json"

# Tags read from a file, kept while its size and time stay the same: the Tracks
# tab reads every file in the library, and the second visit should be instant
_TAG_CACHE: dict[tuple[str, int, int], dict] = {}

_ARTIST_LOCK = threading.Lock()

# How deep the library looks into a folder: iTunes Media / Music / Artist / Album is four
LIBRARY_DEPTH = 6

def _library_items(root: Path, artist: str = "", depth: int = 0) -> list[dict]:
    """Every album and single track under a folder. Loose tracks count as
    single tracks at the top, where downloaded singles are put."""
    items = []
    for entry in folders.listing(root):
        if entry.name.startswith((".", "$")):
            continue  # hidden folders and the recycle bin
        path = Path(entry.path)
        try:
            if not entry.is_dir():
                if depth == 0 and entry.is_file() and folders.audio_name(entry.name):
                    items.append(_library_item(path, [path]))
                continue
            items += _folder_items(path, artist, depth)
        except OSError:
            continue  # removed or locked while scanning
    return items

def _folder_items(path: Path, artist: str, depth: int) -> list[dict]:
    """A folder with music in it is an album, its folders below with the same
    album in their tags included (discs, titles split by a slash). One without
    is looked into, and what is inside takes its name for the artist when the
    tags do not say, as the "nested" naming and most collections have it.
    Loose tracks of different albums side by side are single tracks."""
    files, apart = folders.album_files(path)
    if not files:
        return _library_items(path, artist=path.name, depth=depth + 1) if depth < LIBRARY_DEPTH else []
    loose = [file for file in files if file.parent == path]
    # The first, middle and last are enough to tell an album from a heap of singles,
    # and reading every file's tags would make a large collection slow to open
    sample = {loose[0], loose[len(loose) // 2], loose[-1]}
    if len({folders.album_tag(file) for file in sample} - {""}) > 1:
        items = [_library_item(file, [file]) for file in loose]
        return items + (_library_items(path, artist=path.name, depth=depth + 1) if depth < LIBRARY_DEPTH else [])
    items = [_library_item(path, files, artist=artist)]
    for other in apart:
        items += _folder_items(other, path.name, depth + 1)
    return items

def _library_item(path: Path, files: list[Path], artist: str = "") -> dict:
    """What the library says about an album or a track: its own tags first,
    and the names of its folder and the one above where they say nothing."""
    album = path.is_dir()
    stats = [folders.stat(file) for file in files]
    pattern = _ALBUM_UNDER_ARTIST if artist else _ALBUM_NAME if album else _TRACK_NAME
    match = pattern.match(path.name if album else path.stem)
    # Only a folder with the marker beside its files is opened to read it
    marker = read_marker(path) if album and any(entry.name in MARKER_NAMES for entry in folders.listing(path)) else {}
    tags = _tags(files[0])
    named = {
        "title": match["title"] if match else (path.name if album else path.stem),
        "artist": artist or (match["artist"] if match else ""),
        "year": (match["year"] or "") if match and album else "",
    }
    if album and tags["album"]:
        named = {"title": tags["album"],
                 "artist": tags["album_artist"] or tags["artists"].split(",")[0].strip() or named["artist"],
                 "year": tags["year"] or named["year"]}
    elif not album and tags["artists"]:
        named = {"title": tags["title"], "artist": tags["artists"], "year": ""}
    return {
        # The link the album came from, when it was downloaded by this program
        "link": marker.get("link", ""),
        "expected": marker.get("tracks") or 0,
        "album": album,
        **named,
        "path": str(path),
        # Its first file's, as "Alternative Rock; Rock": the library's genre filter splits it
        "genre": tags["genre"],
        "tracks": len(files),
        "size": sum(stat.st_size for stat in stats),
        "modified": max(stat.st_mtime for stat in stats),
        # A picture beside the files or inside them: the cover server finds which, or neither
        "cover": True,
    }

# The ID3 frames behind what the library shows, by the names the other formats use
_SHOWN_NAMES = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2", "tracknumber": "TRCK",
                "discnumber": "TPOS", "date": "TDRC", "genre": "TCON"}

_SHOWN_FRAMES = {name: Frames[name] for name in (*_SHOWN_NAMES.values(), "TYER", "TDAT")}

def _tags(path: Path) -> dict:
    """Title, artists, album, numbers, length and bitrate of one file."""
    try:
        stat = folders.stat(path)
    except OSError:
        return _blank_tags(path)
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _TAG_CACHE:
        return _TAG_CACHE[key]
    info = _blank_tags(path)
    mp3 = path.suffix.lower() == ".mp3"
    try:
        # An mp3's lyrics and pictures are most of its tags and none of what is
        # shown here: only the frames that are shown are read, twice as fast
        audio = MP3(path, known_frames=_SHOWN_FRAMES) if mp3 else MutagenFile(path, easy=True)
    except Exception:  # a broken or half-written file still gets its row
        audio = None
    if audio is not None:
        tags = audio.tags or {}

        def first(name: str) -> str:
            try:
                values = (tags.get(_SHOWN_NAMES[name]) or []) if mp3 else (tags.get(name) or [])
                values = list(values) if mp3 else values
            except (KeyError, ValueError, TypeError):
                values = []
            return str(values[0]).strip() if values else ""

        info.update({
            "title": first("title") or info["title"],
            "artists": first("artist"),
            "album": first("album"),
            "album_artist": first("albumartist"),
            "number": _leading_number(first("tracknumber")),
            "disc": _leading_number(first("discnumber")) or 1,
            "year": first("date")[:4],
            "genre": first("genre"),
            "duration": round(getattr(audio.info, "length", 0) or 0),
            "bitrate": round((getattr(audio.info, "bitrate", 0) or 0) / 1000),
        })
    if len(_TAG_CACHE) > 50_000:
        _TAG_CACHE.clear()
    _TAG_CACHE[key] = info
    return info

def _blank_tags(path: Path) -> dict:
    return {"title": path.stem, "artists": "", "album": "", "album_artist": "", "number": 0, "disc": 1,
            "year": "", "genre": "", "duration": 0, "bitrate": 0, "format": path.suffix[1:].lower()}

def _leading_number(text: str) -> int:
    """A "3/10" and a "03" are both track 3."""
    match = re.match(r"\s*(\d+)", text or "")
    return int(match.group(1)) if match else 0

def _embedded_lyrics(path: Path) -> str:
    """The plain lyrics in a file's own tags, "" when it has none."""
    try:
        tags = MutagenFile(path).tags
    except Exception:
        return ""
    if tags is None:
        return ""
    if hasattr(tags, "getall"):  # ID3
        found = tags.getall("USLT")
        return str(found[0].text).strip() if found else ""
    for key in ("\xa9lyr", "lyrics", "unsyncedlyrics"):
        try:
            values = tags.get(key)
        except (KeyError, ValueError):
            values = None
        if values:
            return str(values[0]).strip()
    return ""

def _cover_bytes(path: Path) -> bytes | None:
    """An album's cover beside its files, or else the picture inside its
    first file; the picture inside a single track's file."""
    try:
        if path.parent == playlists.covers_dir():  # a picture chosen for a playlist is the cover itself
            return path.read_bytes()
        if not path.is_dir():
            return folders.embedded_cover(path)
        picture = folders.cover_file(path)
        if picture is not None:
            return picture.read_bytes()
        first = next(iter(folders.album_files(path)[0]), None)
        return folders.embedded_cover(first) if first else None
    except OSError:
        return None

# What a tidy-up fills in, and where a file's tags say so; lyrics only when they are on
GAP_TAGS = ("title", "artist", "album", "genre", "date", "cover")

_GAP_CACHE: dict[tuple[str, int, int], frozenset] = {}

def _entry_files(entry: Path) -> list[Path]:
    if entry.is_dir():
        return folders.album_files(entry)[0]
    return [entry] if folders.is_audio(entry) else []

def _files_signature(files: list[Path]) -> str:
    """How many files there are and when the newest changed: a tidy-up or a
    new file changes it."""
    try:
        return f"{len(files)}:{max(folders.stat(file).st_mtime_ns for file in files)}"
    except (OSError, ValueError):
        return ""

def _has_gaps(entry: Path, files: list[Path], with_lyrics: bool) -> bool:
    if entry.is_dir() and folders.cover_file(entry) is None:
        return True
    wanted = set(GAP_TAGS) | ({"track"} if entry.is_dir() else set()) | ({"lyrics"} if with_lyrics else set())
    return any(not wanted <= _present_tags(file) for file in files)

def _present_tags(path: Path) -> frozenset:
    """The names of the tags a file has, read once per version of the file."""
    try:
        stat = folders.stat(path)
    except OSError:
        return frozenset(GAP_TAGS) | {"track", "lyrics"}  # gone: nothing to add to it
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _GAP_CACHE:
        if len(_GAP_CACHE) > 50_000:
            _GAP_CACHE.clear()
        _GAP_CACHE[key] = frozenset(name for name, value in read_tags(path).items() if value)
    return _GAP_CACHE[key]

_LIBRARY_CACHE = {"loaded": False, "saved": -1, "items": {}}

_LIBRARY_CACHE_LOCK = threading.Lock()

def _load_library_cache() -> None:
    """The tags read in an earlier run, once per run. Each is kept with the
    file's size and time, so a file changed since is simply read again."""
    with _LIBRARY_CACHE_LOCK:
        if _LIBRARY_CACHE["loaded"]:
            return
        _LIBRARY_CACHE["loaded"] = True
        data = logs.read_json(logs.data_dir() / LIBRARY_CACHE_FILE)
        if data.get("version") != 1:
            return
        items = data.get("items")
        if isinstance(items, dict):
            _LIBRARY_CACHE["items"] = {key: value for key, value in items.items() if isinstance(value, list)}
        try:
            for path, mtime, size, info in data.get("tags", []):
                _TAG_CACHE.setdefault((path, mtime, size), info)
            for path, mtime, size, names in data.get("gaps", []):
                _GAP_CACHE.setdefault((path, mtime, size), frozenset(names))
            folders.remember_albums({(path, mtime): name for path, mtime, name in data.get("albums", [])})
        except (TypeError, ValueError):  # a file from elsewhere: what was read of it stays
            logs.log.warning("кэш библиотеки не прочитался, файлы будут прочитаны заново")
        _LIBRARY_CACHE["saved"] = _library_cache_size()

def _save_library_cache(roots: str = "", items: list[dict] | None = None) -> None:
    """Writes the tags down when anything new was read, for a file read in
    two versions the later one, and what the library holds when it changed."""
    with _LIBRARY_CACHE_LOCK:
        size = _library_cache_size()
        kept = _LIBRARY_CACHE["items"]
        new_items = items is not None and kept.get(roots) != items
        if size == _LIBRARY_CACHE["saved"] and not new_items:
            return
        if new_items:
            _LIBRARY_CACHE["items"] = kept = {roots: items}  # only the folders read last
        tags = {key[0]: [*key, info] for key, info in list(_TAG_CACHE.items())}
        gaps = {key[0]: [*key, sorted(names)] for key, names in list(_GAP_CACHE.items())}
        albums = {key[0]: [*key, name] for key, name in folders.known_albums().items()}
        logs.write_json(logs.data_dir() / LIBRARY_CACHE_FILE, {
            "version": 1, "items": kept, "tags": list(tags.values()), "gaps": list(gaps.values()),
            "albums": list(albums.values())})
        _LIBRARY_CACHE["saved"] = size

# What library() read last, for the Tracks tab that is opened right after the library
# has been: walking every folder again to learn what was just found takes seconds
_LIBRARY_LAST: dict[str, tuple[str, float, list[dict]] | None] = {"read": None}

LIBRARY_FRESH = 60  # seconds

def _library_just_read(roots: str | list[str]) -> list[dict] | None:
    """The entries library() found within the last minute for these folders, else None."""
    roots = [roots] if isinstance(roots, str) else [str(root) for root in roots or []]
    read = _LIBRARY_LAST["read"]
    if read is not None and read[0] == _roots_key(roots) and time.monotonic() - read[1] < LIBRARY_FRESH:
        return read[2]
    return None

def _forget_in_library_cache(paths: set[str]) -> None:
    """What was deleted is not shown again at the next start, while the folders are read."""
    if not paths:
        return
    _LIBRARY_LAST["read"] = None
    _load_library_cache()
    with _LIBRARY_CACHE_LOCK:
        kept = _LIBRARY_CACHE["items"]
        _LIBRARY_CACHE["items"] = {key: [item for item in items if item.get("path") not in paths]
                                   for key, items in kept.items()}
        _LIBRARY_CACHE["saved"] = -1  # written on the next save, even with no tags read
    _save_library_cache()

def _roots_key(roots: list[str]) -> str:
    return "|".join(sorted(str(Path(root).expanduser()) for root in roots))

def _library_cache_size() -> int:
    return len(_TAG_CACHE) + len(_GAP_CACHE) + len(folders.known_albums())

def _remember_downloaded(names: dict, paths) -> None:
    """A download looked up all a tag fill-in would: the genre, the cover and
    the lyrics. What it could not find, a fill-in would not find either, so a
    release just downloaded is not offered one until its files change: an album
    by its folder, a single track by its file."""
    try:
        if names.get("single"):
            for path in paths:
                _remember_tidied(Path(path))
        elif names.get("folder"):
            _remember_tidied(Path(names["folder"]))
    except Exception as e:  # only spares a needless offer later: never worth a failed download
        logs.log.warning("не запомнил скачанное как готовое: %s", e)

def _remember_tidied(entry: Path) -> None:
    files = _entry_files(entry)
    if not files:
        return
    path = logs.data_dir() / TIDIED_FILE
    with _ARTIST_LOCK:  # the same small-file lock the artists' photos use
        known = logs.read_json(path)
        known[str(entry)] = _files_signature(files)
        logs.write_json(path, known)

def _favourites() -> list[str]:
    return [entry for entry in logs.read_json(logs.data_dir() / FAVOURITES_FILE, list) if isinstance(entry, str)]

def _seen() -> dict[str, float]:
    """Path to when it was opened, for the albums opened lately."""
    now = time.time()
    return {path: when for path, when in logs.read_json(logs.data_dir() / SEEN_FILE).items()
            if isinstance(when, (int, float)) and now - when < SEEN_FOR}
