"""The covers a classic iPod shows: iPod_Control/Artwork/ArtworkDB and its .ithmb files.

The iPod does not read pictures out of the music. Each cover is drawn
beforehand at every size the model shows it, as raw RGB565 pixels side by
side in files named after the size (F1055_1.ithmb), and ArtworkDB says which
picture lies where and whose it is. A track in the iTunesDB points at its
picture by a number. Records, sizes and fields are the ones libgpod writes
(db-artwork-writer.c, ithumb-writer.c), as for the iTunesDB.

The program keeps its covers in .ithmb files of its own and leaves those of
iTunes alone: the pictures of the tracks iTunes put on the iPod stay listed
as they were. sonicache-art.json beside them remembers which cover lies in
which place of the program's files, so a cover drawn once is not drawn again,
and the place of one that went is taken by the next new one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import folders, itunesdb
from .logs import log

ARTWORK = "iPod_Control/Artwork"
DATABASE = f"{ARTWORK}/ArtworkDB"
STATE = f"{ARTWORK}/sonicache-art.json"
_log = log.getChild("ipod")

# The cover sizes each model shows (format id, width, height), from libgpod's itdb_device.c; all RGB565 little-endian
_CLASSIC = ((1061, 56, 56), (1055, 128, 128), (1068, 128, 128), (1060, 320, 320))
FORMATS = {
    "photo": ((1017, 56, 56), (1016, 140, 140)),
    "video": ((1028, 100, 100), (1029, 200, 200)),
    "nano": ((1031, 42, 42), (1027, 100, 100)),
    "nano3": _CLASSIC,
    "classic": _CLASSIC,
    "nano4": ((1055, 128, 128), (1068, 128, 128), (1071, 240, 240), (1074, 50, 50), (1078, 80, 80),
              (1084, 240, 240)),
}
# These list one picture for every track of an album; the older ones want a picture listed for each track
SHARED = {"nano3", "nano4", "classic"}
FIRST_IMAGE = 0x64  # libgpod numbers pictures from here
# The sizes of the headers, as iTunes writes them
_HEADERS = {b"mhfd": 0x84, b"mhsd": 0x60, b"mhli": 0x5C, b"mhla": 0x5C, b"mhlf": 0x5C, b"mhii": 0x98,
            b"mhni": 0x4C, b"mhif": 0x7C}


@dataclass
class Cover:
    """A track's picture as the iPod gets it."""
    key: str  # the picture's hash: tracks with the same picture share it
    size: int  # bytes of the picture it was drawn from
    data: bytes = b""  # the picture itself, while it may still have to be drawn


def cover_of(source: Path, seen: dict[str, Cover | None] | None = None) -> Cover | None:
    """The cover of a track of the library: the album's picture beside it, or else the one inside the file."""
    seen = {} if seen is None else seen
    folder = os.path.normcase(str(source.parent))
    if folder not in seen:
        picture = folders.cover_file(source.parent)
        try:
            seen[folder] = _cover(picture.read_bytes()) if picture is not None else None
        except OSError:
            seen[folder] = None
    return seen[folder] or _cover(folders.embedded_cover(source))


def _cover(data: bytes | None) -> Cover | None:
    return Cover(hashlib.sha1(data).hexdigest(), len(data), data) if data else None


def draw(data: bytes, formats: tuple[tuple[int, int, int], ...], ffmpeg: str) -> dict[int, bytes] | None:
    """The picture at every size, fitted into the square with black on the sides; None when ffmpeg cannot read it."""
    with tempfile.TemporaryDirectory(prefix="sonicache-art-") as folder:
        source = Path(folder) / "cover"
        source.write_bytes(data)
        chains = [f"[0:v]split={len(formats)}" + "".join(f"[s{n}]" for n in range(len(formats)))]
        outputs = []
        for n, (format_id, width, height) in enumerate(formats):
            chains.append(f"[s{n}]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,format=rgb565le[o{n}]")
            outputs += ["-map", f"[o{n}]", "-frames:v", "1", "-f", "rawvideo", str(Path(folder) / f"{format_id}.raw")]
        command = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(source),
                   "-filter_complex", ";".join(chains), *outputs]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # no console flashing up
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=flags, timeout=60)
        drawn = {}
        for format_id, width, height in formats:
            raw = Path(folder) / f"{format_id}.raw"
            if done.returncode != 0 or not raw.is_file() or raw.stat().st_size != width * height * 2:
                _log.warning("обложка не нарисована для iPod: %s", done.stderr.strip()[-300:])
                return None
            drawn[format_id] = raw.read_bytes()
        return drawn


class ArtworkError(ValueError):
    """An ArtworkDB that could not be read: it is left as it is."""


def _records(data: bytes, offset: int, count: int, end: int) -> list[bytes]:
    """`count` records one after another, each the bytes its own total length says."""
    found = []
    for _ in range(count):
        if end - offset < 12 or data[offset:offset + 2] != b"mh":
            raise ArtworkError(f"no record at {offset}")
        total = struct.unpack_from("<I", data, offset + 8)[0]
        if total < 12 or offset + total > end:
            raise ArtworkError(f"a record at {offset} does not fit")
        found.append(data[offset:offset + total])
        offset += total
    return found


def parse(data: bytes) -> tuple[bytes, dict[int, tuple[bytes, list[bytes]]]]:
    """The header of ArtworkDB, and for each of its three datasets the list's header and the items in it."""
    if data[:4] != b"mhfd" or len(data) < 24:
        raise ArtworkError("not an ArtworkDB")
    header_len, total, = struct.unpack_from("<II", data, 4)
    children = struct.unpack_from("<I", data, 20)[0]
    if total != len(data) or header_len > total:
        raise ArtworkError("an ArtworkDB of another length than it says")
    datasets = {}
    for dataset in _records(data, header_len, children, total):
        kind = struct.unpack_from("<H", dataset, 12)[0]
        inner = struct.unpack_from("<I", dataset, 4)[0]
        if dataset[inner:inner + 2] != b"mh" or len(dataset) < inner + 12:
            raise ArtworkError("a dataset without its list")
        list_len, count = struct.unpack_from("<II", dataset, inner + 4)
        datasets[kind] = (dataset[inner:inner + list_len], _records(dataset, inner + list_len, count, len(dataset)))
    return data[:header_len], datasets


def _header(tag: bytes, fields: dict[int, tuple[str, int]] | None = None) -> bytearray:
    header = bytearray(_HEADERS[tag])
    struct.pack_into("<4sI", header, 0, tag, len(header))
    for offset, (kind, value) in (fields or {}).items():
        struct.pack_into("<" + kind, header, offset, value)
    return header


def _sized(header: bytearray, inner: bytes) -> bytes:
    """A record whose third field is its whole length."""
    struct.pack_into("<I", header, 8, len(header) + len(inner))
    return bytes(header) + inner


def _listed(tag: bytes, items: list[bytes], header: bytes | None = None) -> bytes:
    """A list whose third field counts its items."""
    made = bytearray(header) if header else _header(tag)
    struct.pack_into("<I", made, 8, len(items))
    return bytes(made) + b"".join(items)


def _file_name(format_id: int, index: int) -> str:
    return f":F{format_id}_{index}.ithmb"


def _mhii(image_id: int, song_id: int, size: int, places: list[tuple[int, int, int, int, int]]) -> bytes:
    """One picture: where each of its sizes lies (format, file index, offset, width, height)."""
    now = itunesdb.mac_time(time.time())
    inner = b""
    for format_id, index, offset, width, height in places:
        name = _file_name(format_id, index).encode("utf-16-le")
        padding = -(36 + len(name)) % 4
        # The file's name; its header length says 24 although 36 bytes come before the name, as iTunes writes it
        location = bytearray(36)
        struct.pack_into("<4sIIHBBIIIB", location, 0, b"mhod", 24, 36 + len(name) + padding, 3, 0, padding,
                         0, 0, len(name), 2)
        mhni = _header(b"mhni", {12: ("I", 1), 16: ("I", format_id), 20: ("I", offset),
                                 24: ("I", width * height * 2), 32: ("H", height), 34: ("H", width)})
        thumbnail = _sized(mhni, bytes(location) + name + bytes(padding))
        container = bytearray(24)
        struct.pack_into("<4sI", container, 0, b"mhod", 24)
        struct.pack_into("<H", container, 12, 2)
        inner += _sized(container, thumbnail)
    mhii = _header(b"mhii", {12: ("I", len(places)), 16: ("I", image_id), 20: ("Q", song_id), 40: ("I", now),
                             44: ("I", now), 48: ("I", size + 1)})  # libgpod adds the 1, as iTunes does
    return _sized(mhii, inner)


class Artwork:
    """The covers of one iPod, as the program keeps them there."""

    def __init__(self, root: Path, generation: str):
        self.root = Path(root)
        self.formats = FORMATS.get(generation, ())
        self.shared = generation in SHARED

    def write(self, db: itunesdb.Database, covers: dict[int, Cover | None], ours: set[int],
              ffmpeg: str | None) -> None:
        """Lists the covers of the program's tracks (by their dbid) in ArtworkDB and points the tracks at them.

        `ours` is every dbid the program ever gave a track here: their pictures
        are dropped from the list before this sync's are added. Tracks with no
        cover, or none that could be drawn, get none. An ArtworkDB that cannot
        be read is left alone, and so are the tracks.
        """
        if not self.formats:
            return
        artwork = self.root / ARTWORK
        try:
            head, datasets = parse((artwork / "ArtworkDB").read_bytes())
        except FileNotFoundError:
            if not any(covers.values()):
                return  # nothing to show, and nothing listed before
            head, datasets = bytes(_header(b"mhfd", {16: ("I", 2), 0x30: ("B", 2)})), {}
        except (OSError, ArtworkError) as e:
            _log.warning("ArtworkDB на %s не прочитался, обложки не тронуты: %s", self.root, e)
            return
        # What iTunes listed stays, and so do the files its pictures lie in; the program's own are listed again below
        listed = datasets.get(1, (None, []))
        foreign = [item for item in listed[1] if struct.unpack_from("<Q", item, 20)[0] not in ours]
        theirs = {(int(found[1]), int(found[2])) for item in foreign
                  for found in re.finditer(r":F(\d+)_(\d+)\.ithmb", item.decode("utf-16-le", errors="replace"))}
        state = self._state()
        files = self._files(state, theirs)
        image_id = max([FIRST_IMAGE - 1, *(struct.unpack_from("<I", item, 16)[0] for item in foreign)])

        slots = self._place({cover.key: cover for cover in covers.values() if cover}, state, files, ffmpeg)
        pictures, first = [], {}
        for track in db.tracks:
            cover = covers.get(track.dbid) if track.dbid in ours else None
            slot = slots.get(cover.key) if cover else None
            header = track.record.header
            if cover is None or slot is None:
                if track.dbid in ours:
                    struct.pack_into("<HHI", header, 0x7C, 0, 0, 0)
                    header[0xA4] = 2  # no picture
                    struct.pack_into("<I", header, 0x160, 0)
                continue
            if self.shared and cover.key in first:
                number = first[cover.key]
            else:
                image_id += 1
                number = first[cover.key] = image_id
                places = [(format_id, files[format_id], slot * width * height * 2, width, height)
                          for format_id, width, height in self.formats]
                pictures.append(_mhii(number, track.dbid, cover.size, places))
            struct.pack_into("<HHI", header, 0x7C, 1, 0, cover.size)
            header[0xA4] = 1
            struct.pack_into("<I", header, 0x160, number)

        formats = datasets.get(3, (None, []))
        known = {struct.unpack_from("<I", item, 16)[0] for item in formats[1]}
        described = formats[1] + [bytes(_header(b"mhif", {8: ("I", _HEADERS[b"mhif"]), 16: ("I", format_id),
                                                        20: ("I", width * height * 2)}))
                                  for format_id, width, height in self.formats if format_id not in known]
        albums = datasets.get(2, (None, []))
        lists = {1: _listed(b"mhli", foreign + pictures, listed[0]),
                 2: _listed(b"mhla", albums[1], albums[0]),
                 3: _listed(b"mhlf", described, formats[0])}
        body = b"".join(_sized(_header(b"mhsd", {12: ("H", kind)}), lists[kind]) for kind in (1, 2, 3))
        top = bytearray(head)
        struct.pack_into("<I", top, 20, 3)
        struct.pack_into("<I", top, 28, image_id if image_id >= FIRST_IMAGE else 0)
        self._save(DATABASE, _sized(top, body))
        self._save(STATE, json.dumps({"files": {str(k): v for k, v in files.items()},
                                      "slots": state["slots"]}).encode("utf-8"))

    def _state(self) -> dict:
        try:
            state = json.loads((self.root / STATE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        slots = state.get("slots") if isinstance(state, dict) else None
        files = state.get("files") if isinstance(state, dict) else None
        return {"slots": {key: slot for key, slot in (slots or {}).items()
                          if isinstance(key, str) and isinstance(slot, int) and 0 <= slot < 1 << 20},
                "files": {key: index for key, index in (files or {}).items()
                          if str(key).isdigit() and isinstance(index, int) and 0 < index < 1000}}

    def _files(self, state: dict, theirs: set[tuple[int, int]]) -> dict[int, int]:
        """The number in the name of the program's .ithmb file of each size: one no file of iTunes has."""
        taken = set(theirs)
        for entry in (self.root / ARTWORK).glob("F*_*.ithmb") if (self.root / ARTWORK).is_dir() else []:
            found = re.fullmatch(r"F(\d+)_(\d+)\.ithmb", entry.name)
            if found:
                taken.add((int(found[1]), int(found[2])))
        files = {}
        for format_id, _, _ in self.formats:
            kept = state["files"].get(str(format_id))
            if kept is None or (format_id, kept) in theirs:
                kept = max([0, *(index for number, index in taken if number == format_id)]) + 1
                state["slots"].clear()  # a file of ours not seen before: whatever was in its places is unknown
            files[format_id] = kept
        return files

    def _place(self, covers: dict[str, Cover], state: dict, files: dict[int, int],
               ffmpeg: str | None) -> dict[str, int]:
        """Each cover's place in the program's files: the one it has, or a free one, drawn there now."""
        slots = state["slots"]
        for key in [key for key in slots if key not in covers]:
            del slots[key]
        for format_id, width, height in self.formats:  # a place past a file's end was never written
            path = self.root / ARTWORK / _file_name(format_id, files[format_id])[1:]
            size = path.stat().st_size if path.is_file() else 0
            for key in [key for key, slot in slots.items() if (slot + 1) * width * height * 2 > size]:
                del slots[key]
        free = iter(sorted(set(range(len(slots) + len(covers))) - set(slots.values())))
        for key, cover in covers.items():
            if key in slots or not ffmpeg or not cover.data:
                continue
            drawn = draw(cover.data, self.formats, ffmpeg)
            if drawn is None:
                continue
            slot = next(free)
            for format_id, width, height in self.formats:
                path = self.root / ARTWORK / _file_name(format_id, files[format_id])[1:]
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "r+b" if path.exists() else "wb") as file:
                    file.seek(slot * width * height * 2)
                    file.write(drawn[format_id])
            slots[key] = slot
        return slots

    def _save(self, rel: str, data: bytes) -> None:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        part.write_bytes(data)
        os.replace(part, target)
