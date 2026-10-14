"""The database a classic iPod plays from: iPod_Control/iTunes/iTunesDB.

Its stock firmware shows only the songs listed here, not whatever lies on the
disk. The format was worked out by the people behind libgpod (gtkpod,
Rhythmbox, Banshee); records, sizes and field offsets below are the ones
libgpod writes, so an iPod that takes its databases takes these.

The file is a tree of little-endian records: "mhbd" holds datasets ("mhsd"),
a dataset holds a list (tracks "mhlt", playlists "mhlp", albums "mhla",
artists "mhli"), the list holds items ("mhit", "mhyp", "mhia", "mhii"), and
items hold strings and other data ("mhod"). Everything is kept as it was
read, records nobody here knows included, so a database iTunes filled loses
nothing when the program adds its own songs to it.
"""

from __future__ import annotations

import hashlib
import math
import random
import struct
import time
from dataclasses import dataclass

LISTS = (b"mhlt", b"mhlp", b"mhla", b"mhli")  # the third field counts children, not bytes
FIRST_ID = 52  # libgpod numbers tracks from here
MAC_EPOCH = 2082844800  # seconds from 1904, when an iPod's clock starts, to 1970

# Data object types (mhod)
TITLE, PATH, ALBUM, ARTIST, GENRE, FILETYPE, COMPOSER = 1, 2, 3, 4, 5, 6, 12
ALBUMARTIST, SORT_ARTIST, SORT_TITLE, SORT_ALBUM, SORT_COMPOSER = 22, 23, 27, 28, 30
INDEX, JUMPS, POSITION = 52, 53, 100
ALBUM_ALBUM, ALBUM_ARTIST, ALBUM_SORT_ARTIST, ARTIST_NAME = 200, 201, 202, 300
STRINGS = {*range(1, 15), *range(18, 32), 200, 201, 202, 300}  # the ones with a UTF-16 string inside

SORTS = {"title": 0x03, "album": 0x04, "artist": 0x05, "genre": 0x07, "composer": 0x12}
FILETYPES = {"mp3": "MPEG audio file", "m4a": "AAC audio file"}


class Record:
    """One record: its header as read, the records inside it, and whatever
    else is inside that is not records (a string, a list of numbers)."""

    def __init__(self, tag: bytes, header: bytearray, children: list[Record] | None = None, body: bytes = b""):
        self.tag = tag
        self.header = header
        self.children = children if children is not None else []
        self.body = body

    def u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.header, offset)[0]

    def set_u32(self, offset: int, value: int) -> None:
        struct.pack_into("<I", self.header, offset, value)

    def u64(self, offset: int) -> int:
        return struct.unpack_from("<Q", self.header, offset)[0]

    def mhods(self, kind: int | None = None) -> list[Record]:
        return [child for child in self.children if child.tag == b"mhod" and (kind is None or child.u32(12) == kind)]

    def to_bytes(self) -> bytes:
        inner = b"".join(child.to_bytes() for child in self.children) + self.body
        header = self.header
        # What counts the children is set from the children there are now
        if self.tag in LISTS:
            struct.pack_into("<I", header, 8, len(self.children))
        else:
            struct.pack_into("<I", header, 8, len(header) + len(inner))
        if self.tag == b"mhbd":
            struct.pack_into("<I", header, 0x14, len(self.children))
        elif self.tag in (b"mhit", b"mhip", b"mhia", b"mhii"):
            struct.pack_into("<I", header, 12, len(self.mhods()))
        elif self.tag == b"mhyp":
            struct.pack_into("<II", header, 12, len(self.mhods()),
                             sum(child.tag == b"mhip" for child in self.children))
        return bytes(header) + inner


def _parse(data: bytes, offset: int, end: int) -> tuple[Record, int]:
    if end - offset < 12 or data[offset:offset + 2] != b"mh":
        raise ValueError(f"no record at {offset}")
    tag = data[offset:offset + 4]
    header_len, third = struct.unpack_from("<II", data, offset + 4)
    if header_len < 12 or offset + header_len > end:
        raise ValueError(f"{tag!r} at {offset} does not fit")
    header = bytearray(data[offset:offset + header_len])
    position = offset + header_len
    if tag in LISTS:
        children = []
        for _ in range(third):
            child, position = _parse(data, position, end)
            children.append(child)
        return Record(tag, header, children), position
    stop = offset + third
    if third < header_len or stop > end:
        raise ValueError(f"{tag!r} at {offset} does not fit")
    if tag == b"mhod":
        return Record(tag, header, body=data[position:stop]), stop
    children = []
    while position < stop and data[position:position + 2] == b"mh":
        try:
            child, after = _parse(data, position, stop)
        except ValueError:
            break  # not records after all: kept as they are
        children.append(child)
        position = after
    return Record(tag, header, children, data[position:stop]), stop


def string_of(mhod: Record) -> str:
    """The text of a string data object."""
    if len(mhod.body) < 16:
        return ""
    encoding, length = struct.unpack_from("<II", mhod.body, 0)
    raw = mhod.body[16:16 + length]
    return raw.decode("utf-8" if encoding == 2 else "utf-16-le", errors="replace")


def _mhod(kind: int, body: bytes) -> Record:
    return Record(b"mhod", bytearray(struct.pack("<4sIIIII", b"mhod", 24, 0, kind, 0, 0)), body=body)


def _string(kind: int, text: str) -> Record:
    raw = text.encode("utf-16-le")
    return _mhod(kind, struct.pack("<IIII", 1, len(raw), 1, 0) + raw)


def _record(tag: bytes, size: int, fields: dict[int, tuple[str, int]] | None = None) -> Record:
    header = bytearray(size)
    struct.pack_into("<4sI", header, 0, tag, size)
    for offset, (kind, value) in (fields or {}).items():
        struct.pack_into("<" + kind, header, offset, value)
    return Record(tag, header)


def mac_time(unix: float) -> int:
    """An iPod's clock: seconds from 1904, in local time."""
    if not unix:
        return 0
    return (int(unix) + MAC_EPOCH + time.localtime(unix).tm_gmtoff) & 0xFFFFFFFF


def ipod_path(rel: str) -> str:
    """iPod_Control/Music/F00/X.mp3 as the database writes it: :iPod_Control:Music:F00:X.mp3."""
    return ":" + rel.replace("/", ":")


@dataclass
class TrackInfo:
    """What the database says about a song the program puts on the iPod."""
    path: str  # ":iPod_Control:Music:F07:SNC4KQ7Z.mp3"
    title: str
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    genre: str = ""
    composer: str = ""
    kind: str = "mp3"  # or "m4a"
    year: int = 0
    track: int = 0
    disc: int = 0
    length: int = 0  # ms
    bitrate: int = 0  # kbit/s
    samplerate: int = 44100
    size: int = 0
    compilation: bool = False
    dbid: int = 0
    added: int = 0  # unix time


class Track:
    """A track as the database has it."""

    def __init__(self, record: Record):
        self.record = record
        self.strings = {mhod.u32(12): string_of(mhod) for mhod in record.mhods() if mhod.u32(12) in STRINGS}

    id = property(lambda self: self.record.u32(0x10))
    dbid = property(lambda self: self.record.u64(0x70))
    track = property(lambda self: self.record.u32(0x2C))
    disc = property(lambda self: self.record.u32(0x5C))
    title = property(lambda self: self.strings.get(TITLE, ""))
    artist = property(lambda self: self.strings.get(ARTIST, ""))
    album = property(lambda self: self.strings.get(ALBUM, ""))
    albumartist = property(lambda self: self.strings.get(ALBUMARTIST, ""))
    genre = property(lambda self: self.strings.get(GENRE, ""))
    composer = property(lambda self: self.strings.get(COMPOSER, ""))
    path = property(lambda self: self.strings.get(PATH, ""))


class Playlist:
    def __init__(self, record: Record):
        self.record = record

    pid = property(lambda self: self.record.u64(0x1C))
    master = property(lambda self: self.record.header[0x14] == 1)
    name = property(lambda self: next((string_of(m) for m in self.record.mhods(TITLE)), ""))

    @property
    def track_ids(self) -> list[int]:
        return [child.u32(0x18) for child in self.record.children if child.tag == b"mhip"]


class Database:
    def __init__(self, root: Record):
        self.root = root
        self._joining: list[int] = []  # tracks added since the last finish(): they join the main list

    def dataset(self, kind: int) -> Record | None:
        return next((record for record in self.root.children if record.tag == b"mhsd" and record.u32(12) == kind),
                    None)

    def _list(self, kind: int) -> Record | None:
        dataset = self.dataset(kind)
        return dataset.children[0] if dataset is not None and dataset.children else None

    @property
    def tracks(self) -> list[Track]:
        found = self._list(1)
        return [Track(record) for record in found.children] if found is not None else []

    def playlists_of(self, kind: int) -> list[Playlist]:
        found = self._list(kind)
        return [Playlist(record) for record in found.children if record.tag == b"mhyp"] if found is not None else []

    @property
    def playlists(self) -> list[Playlist]:
        return self.playlists_of(2)

    def track_ids_in_order(self) -> list[int]:
        return [track.id for track in self.tracks]

    def add_track(self, info: TrackInfo) -> int:
        """Puts a track at the end of the list; its id. The playlists get it in finish()."""
        tracks = self._list(1)
        number = max([FIRST_ID - 1, *(track.id for track in self.tracks)]) + 1
        tracks.children.append(_track(info, number, self.root.u64(0x24)))
        self._joining.append(number)
        return number

    def put_track(self, info: TrackInfo) -> int:
        """A track written again in its place, with its id and what was listened to
        on the iPod, so playlists made there keep it; a new one at the end."""
        tracks = self._list(1)
        for index, record in enumerate(tracks.children):
            if record.u64(0x70) == info.dbid:
                made = _track(info, record.u32(0x10), self.root.u64(0x24))
                copy_listens(record, made)
                tracks.children[index] = made
                return record.u32(0x10)
        return self.add_track(info)

    def remove_tracks(self, dbids: set[int]) -> None:
        tracks = self._list(1)
        gone = {track.id for track in self.tracks if track.dbid in dbids}
        tracks.children = [record for record in tracks.children if record.u64(0x70) not in dbids]
        for kind in (2, 3):
            for playlist in self.playlists_of(kind):
                playlist.record.children = [child for child in playlist.record.children
                                            if child.tag != b"mhip" or child.u32(0x18) not in gone]

    def add_playlist(self, name: str, pid: int, track_ids: list[int]) -> None:
        for kind in (2, 3):
            found = self._list(kind)
            if found is not None:
                found.children.append(_playlist(name, pid, track_ids, master=False))

    def remove_playlists(self, pids: set[int]) -> None:
        for kind in (2, 3):
            found = self._list(kind)
            if found is not None:
                found.children = [record for record in found.children
                                  if record.tag != b"mhyp" or record.header[0x14] == 1 or record.u64(0x1C) not in pids]

    def finish(self) -> None:
        """What the iPod reads its menus from, made again from the tracks:
        the main playlist with all of them and its sorted indexes, the albums
        and the artists. libgpod does the same at every write."""
        if self.root.u32(0x10) < 0x30:
            self.root.set_u32(0x10, 0x30)
        # The main list keeps who is in it (iTunes leaves podcasts out) and gets the new tracks;
        # its tracks come first in the list of tracks, in its order, as libgpod writes them
        found = {track.id: track.record for track in self.tracks}
        masters = [playlist for playlist in self.playlists_of(2) + self.playlists_of(3) if playlist.master]
        ids = list(dict.fromkeys(number for number in [*(masters[0].track_ids if masters else found), *self._joining]
                                 if number in found))
        self._joining = []
        listed = set(ids)
        self._list(1).children = [found[number] for number in ids] + [record for number, record in found.items()
                                                                      if number not in listed]
        tracks = self.tracks
        indexes = _indexes(tracks[:len(ids)]) if ids else []
        for kind in (2, 3):
            for playlist in self.playlists_of(kind):
                if playlist.master:
                    record = playlist.record
                    kept = [child for child in record.mhods() if child.u32(12) not in (INDEX, JUMPS)]
                    record.children = kept + indexes + [_item(number, position) for position, number in enumerate(ids)]
                    break
        albums: dict[tuple[str, str], tuple[int, Track]] = {}
        artists: dict[str, tuple[int, Track]] = {}
        for track in tracks:
            album_id = artist_id = 0
            if track.album:
                key = (track.album, track.albumartist or track.artist)
                album_id = albums.setdefault(key, (len(albums) + 1, track))[0]
            if track.artist:
                artist_id = artists.setdefault(track.artist, (len(artists) + 1, track))[0]
            header = track.record.header
            if len(header) >= 0x124:
                struct.pack_into("<I", header, 0x120, album_id)
            if len(header) >= 0x1E4:
                struct.pack_into("<I", header, 0x1E0, artist_id)
        album_list = _record(b"mhla", 92)
        for (album, artist), (number, track) in albums.items():
            mhia = _record(b"mhia", 88, {16: ("I", number), 20: ("Q", random.getrandbits(64)), 28: ("I", 2)})
            mhia.children = [_string(ALBUM_ALBUM, album)] + ([_string(ALBUM_ARTIST, artist)] if artist else [])
            album_list.children.append(mhia)
        artist_list = _record(b"mhli", 92)
        for artist, (number, track) in artists.items():
            mhii = _record(b"mhii", 80, {16: ("I", number), 20: ("Q", random.getrandbits(64)), 28: ("I", 2)})
            mhii.children = [_string(ARTIST_NAME, artist)]
            artist_list.children.append(mhii)
        for kind, made in ((4, album_list), (8, artist_list)):
            dataset = self.dataset(kind)
            if dataset is None:
                dataset = _dataset(kind)
                self.root.children.append(dataset)
            dataset.children = [made]
            dataset.body = b""

    def to_bytes(self) -> bytes:
        return self.root.to_bytes()


def parse(data: bytes) -> Database:
    """The database in these bytes; ValueError when they are not one."""
    if data[:4] != b"mhbd":
        raise ValueError("not an iTunesDB")
    root, end = _parse(data, 0, len(data))
    if end != len(data) or root.body or not root.children or any(child.tag != b"mhsd" for child in root.children):
        raise ValueError("not an iTunesDB")
    db = Database(root)
    if db._list(1) is None or db._list(1).tag != b"mhlt" or db._list(2) is None or db._list(2).tag != b"mhlp":
        raise ValueError("an iTunesDB without its tracks or playlists")
    return db


def _dataset(kind: int, inner: Record | None = None) -> Record:
    dataset = _record(b"mhsd", 96, {12: ("I", kind)})
    dataset.children = [inner] if inner is not None else []
    return dataset


def new_database(name: str) -> Database:
    """An empty database as libgpod makes one, its main playlist named after the iPod."""
    root = _record(b"mhbd", 244, {12: ("I", 1), 16: ("I", 0x30), 0x18: ("Q", random.getrandbits(64)),
                                  0x20: ("H", 2), 0x24: ("Q", random.getrandbits(64)),
                                  0x48: ("Q", random.getrandbits(64))})
    root.header[0x46:0x48] = b"en"

    def playlists() -> Record:
        found = _record(b"mhlp", 92)
        found.children = [_playlist(name, random.getrandbits(64), [], master=True)]
        return found

    root.children = [_dataset(1, _record(b"mhlt", 92)), _dataset(3, playlists()), _dataset(2, playlists()),
                     _dataset(4, _record(b"mhla", 92)), _dataset(8, _record(b"mhli", 92)),
                     _dataset(6, _record(b"mhlt", 92)), _dataset(10, _record(b"mhlt", 92)),
                     _dataset(5, _record(b"mhlp", 92))]
    return Database(root)


def _track(info: TrackInfo, number: int, library_id: int) -> Record:
    suffix = info.kind.upper().ljust(4)[:4].encode("ascii")
    added = mac_time(info.added)
    record = _record(b"mhit", 0x248, {
        0x10: ("I", number), 0x14: ("I", 1), 0x18: ("I", int.from_bytes(suffix, "big")),
        0x1C: ("B", 1 if info.kind == "mp3" else 0), 0x1D: ("B", 1 if info.kind == "mp3" else 0),
        0x1E: ("B", int(info.compilation)), 0x20: ("I", added), 0x24: ("I", info.size),
        0x28: ("I", info.length), 0x2C: ("I", info.track), 0x34: ("I", info.year), 0x38: ("I", info.bitrate),
        0x3E: ("H", info.samplerate), 0x5C: ("I", info.disc), 0x68: ("I", added), 0x70: ("Q", info.dbid),
        0x88: ("f", float(info.samplerate)), 0xA4: ("B", 2), 0xA8: ("Q", info.dbid), 0xB2: ("B", 1),
        0xD0: ("I", 1), 0x124: ("Q", library_id), 0x12C: ("I", info.size), 0x134: ("Q", 0x808080808080),
        0x168: ("I", 1)})
    strings = [(TITLE, info.title), (ARTIST, info.artist), (ALBUM, info.album),
               (FILETYPE, FILETYPES.get(info.kind, "")), (PATH, info.path), (GENRE, info.genre),
               (COMPOSER, info.composer), (ALBUMARTIST, info.albumartist)]
    record.children = [_string(kind, text) for kind, text in strings if text]
    return record


# The preferences every playlist carries (mk_long_mhod_id_playlist in libgpod): what
# iTunes shows of it. Left as libgpod writes it, or iTunes shows the playlist empty
_PLAYLIST_PREFS = struct.pack("<156I", *[0] * 4, 0x010084, 0x05, 0x09, 0x03, 0x120001, *[0] * 3,
                              0xC80002, 0, 0, 0, 0x3C000D, 0, 0, 0, 0x7D0004, 0, 0, 0, 0x7D0003, 0, 0, 0,
                              0x640008, 0, 0, 0, 0x640017, 1, 0, 0, 0x500014, 1, 0, 0, 0x7D0015, 1, *[0] * 114)


def _playlist(name: str, pid: int, track_ids: list[int], master: bool) -> Record:
    record = _record(b"mhyp", 108, {0x14: ("B", 1 if master else 0), 0x18: ("I", mac_time(time.time())),
                                    0x1C: ("Q", pid), 0x28: ("H", 1)})
    record.children = [_string(TITLE, name), _mhod(POSITION, _PLAYLIST_PREFS)]
    record.children += [_item(number, position) for position, number in enumerate(track_ids)]
    return record


def _item(track_id: int, position: int) -> Record:
    item = _record(b"mhip", 76, {0x18: ("I", track_id)})
    item.children = [_mhod(POSITION, struct.pack("<I", position) + bytes(16))]
    return item


def _sort_artist(track: Track) -> str:
    """As libgpod sorts it: a sort name if there is one, "Band, The" for "The Band"."""
    given = track.strings.get(SORT_ARTIST)
    if given:
        return given
    if track.artist[:4].lower() == "the ":
        return f"{track.artist[4:]}, The"
    return track.artist


def _letter(text: str) -> int:
    """The letter a list jumps to: the first letter or digit, upper case; "0" for a digit or nothing."""
    for char in text:
        if char.isalnum():
            if char.isalpha():
                upper = char.upper()
                return ord(upper[0]) if len(upper) == 1 and ord(upper) < 0x10000 else ord("0")
            return ord("0")
    return ord("0")


def _indexes(tracks: list[Track]) -> list[Record]:
    """The main playlist's five sorted indexes (52) and their jump tables (53), as libgpod makes them."""
    rows = []
    for position, track in enumerate(tracks):
        rows.append({"title": track.strings.get(SORT_TITLE) or track.title,
                     "album": track.strings.get(SORT_ALBUM) or track.album,
                     "artist": _sort_artist(track), "genre": track.genre,
                     "composer": track.strings.get(SORT_COMPOSER) or track.composer,
                     "track": track.track, "disc": track.disc, "position": position})

    def key(row: dict, *names: str) -> tuple:
        values = []
        for name in names:
            value = row[name]
            values.append(value.casefold() if isinstance(value, str) else value)
        return tuple(values)

    orders = {"title": ("title",), "album": ("album", "disc", "track", "title"),
              "artist": ("artist", "album", "disc", "track", "title"),
              "genre": ("genre", "artist", "album", "disc", "track", "title"),
              "composer": ("composer", "album", "disc", "track", "title")}
    made = []
    for name in ("title", "artist", "album", "genre", "composer"):
        ordered = sorted(rows, key=lambda row: key(row, *orders[name]))
        body = struct.pack("<II", SORTS[name], len(ordered)) + bytes(40)
        body += b"".join(struct.pack("<I", row["position"]) for row in ordered)
        made.append(_mhod(INDEX, body))
        jumps: list[list[int]] = []
        for index, row in enumerate(ordered):
            letter = _letter(row[name])
            if not jumps or jumps[-1][0] != letter:
                jumps.append([letter, index, 0])
            jumps[-1][2] += 1
        body = struct.pack("<II", SORTS[name], len(jumps)) + bytes(8)
        body += b"".join(struct.pack("<HHII", letter, 0, start, count) for letter, start, count in jumps)
        made.append(_mhod(JUMPS, body))
    return made



# Signing: an iPod Classic or a nano of the 3rd or 4th generation takes no
# database without it. hash58 is an HMAC-SHA1 of the whole file, keyed by the
# device's FireWire GUID (itdb_hash58.c in libgpod, from wtbw's work)

def _sboxes() -> tuple[bytes, bytes]:
    """AES's S-box and its inverse: the two tables hash58 takes its key bytes from."""
    forward = bytearray(256)
    p = q = 1
    while True:
        p = (p ^ (p << 1) ^ (0x1B if p & 0x80 else 0)) & 0xFF  # p times 3 in GF(2^8)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09  # q divided by 3: the inverse of p
        rotate = lambda value, shift: ((value << shift) | (value >> (8 - shift))) & 0xFF  # noqa: E731
        forward[p] = q ^ rotate(q, 1) ^ rotate(q, 2) ^ rotate(q, 3) ^ rotate(q, 4) ^ 0x63
        if p == 1:
            break
    forward[0] = 0x63
    inverse = bytearray(256)
    for index, value in enumerate(forward):
        inverse[value] = index
    return bytes(forward), bytes(inverse)


SBOX, INVERSE_SBOX = _sboxes()
_HASH58_FIXED = bytes.fromhex("6723FE304533F890992107C1D012B2A10781")


def hash58_key(guid: bytes) -> bytes:
    """The 64-byte HMAC key of a device: from the least common multiple of each pair of its GUID's first 8 bytes."""
    material = bytearray()
    for a, b in zip(guid[0:8:2], guid[1:8:2]):
        lcm = 1 if not a or not b else a * b // math.gcd(a, b)
        high, low = (lcm >> 8) & 0xFF, lcm & 0xFF
        material += bytes((SBOX[high], INVERSE_SBOX[high], SBOX[low], INVERSE_SBOX[low]))
    return hashlib.sha1(_HASH58_FIXED + bytes(material)).digest().ljust(64, b"\0")


def sign(data: bytes, guid: bytes) -> bytes:
    """The database with its hash58 in place, for an iPod with this FireWire GUID."""
    signed = bytearray(data)
    kept = signed[0x18:0x20], signed[0x32:0x46]
    signed[0x18:0x20] = bytes(8)
    signed[0x32:0x46] = bytes(20)
    signed[0x58:0x6C] = bytes(20)
    struct.pack_into("<H", signed, 0x30, 1)
    key = hash58_key(guid)
    inner = hashlib.sha1(bytes(byte ^ 0x36 for byte in key) + signed).digest()
    signed[0x58:0x6C] = hashlib.sha1(bytes(byte ^ 0x5C for byte in key) + inner).digest()
    signed[0x18:0x20], signed[0x32:0x46] = kept
    return bytes(signed)


def _counted(data: bytes, tag: bytes, smallest_header: int, smallest_entry: int) -> tuple[int, int, int]:
    """Header length, entry length and number of entries of a file the firmware writes; ValueError if it is not one."""
    if len(data) < 16 or data[:4] != tag:
        raise ValueError(f"not a {tag.decode()} file")
    header, entry, count = struct.unpack_from("<III", data, 4)
    if header < smallest_header or entry < smallest_entry or header + entry * count > len(data):
        raise ValueError(f"a broken {tag.decode()} file")
    return header, entry, count


# What the iPod itself changes in a track: rating, listens, last played,
# when added, bookmark, skips, last skipped, the "unplayed" mark
LISTENS = ((0x1F, 1), (0x50, 4), (0x54, 4), (0x58, 4), (0x68, 4), (0x6C, 4), (0x9C, 4), (0xA0, 4), (0xB2, 1))


def copy_listens(source: Record, target: Record) -> None:
    """A track written again keeps what was listened to on the iPod."""
    for offset, size in LISTENS:
        target.header[offset:offset + size] = source.header[offset:offset + size]


def apply_play_counts(db: Database, data: bytes) -> None:
    """What the iPod recorded since the database was written ("Play Counts"),
    entry by entry in the order of the tracks: listens and skips added, the
    last time, the bookmark and a rating given on the iPod taken."""
    header, entry, count = _counted(data, b"mhdp", 0x60, 0x0C)
    for track, number in zip(db.tracks, range(count)):
        at = header + number * entry
        plays, last_played, bookmark = struct.unpack_from("<III", data, at)
        mhit = track.record
        mhit.set_u32(0x50, mhit.u32(0x50) + plays)
        if plays:
            mhit.header[0xB2] = 1  # no longer marked unplayed
        if last_played:
            mhit.set_u32(0x58, last_played)
        if bookmark:
            mhit.set_u32(0x6C, bookmark)
        if entry >= 0x10:
            rating = struct.unpack_from("<I", data, at + 12)[0]
            if rating <= 100:  # the iPod's own rating, unrated included
                mhit.header[0x1F] = rating
        if entry >= 0x1C:
            skips, last_skipped = struct.unpack_from("<II", data, at + 20)
            mhit.set_u32(0x9C, mhit.u32(0x9C) + skips)
            if last_skipped:
                mhit.set_u32(0xA0, last_skipped)


def otg_indexes(data: bytes) -> list[int]:
    """An On-The-Go playlist made on the iPod ("OTGPlaylistInfo"): positions in the list of tracks."""
    header, entry, count = _counted(data, b"mhpo", 0x14, 0x04)
    return [struct.unpack_from("<I", data, header + number * entry)[0] for number in range(count)]
