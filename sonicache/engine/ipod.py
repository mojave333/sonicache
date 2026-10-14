"""A classic iPod in disk mode: Classic, Video, nano, mini.

It shows up as a drive, but its own firmware plays only what its database
lists (itunesdb.py). This module tells the models apart, by what the iPod
says of itself and by the database already on it, and syncs to the ones
whose database can be written: the older ones need nothing, a Classic and a
nano of the 3rd and 4th generation need it signed with their FireWire GUID.
An iPod with Rockbox plays from folders and is synced as a plain drive.
"""

from __future__ import annotations

import plistlib
import posixpath
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import devices, itunesdb
from .i18n import t
from .logs import log

CONTROL = "iPod_Control"
DATABASE = f"{CONTROL}/iTunes/iTunesDB"
_log = log.getChild("ipod")

# Model numbers (ModelNumStr in SysInfo, without its first letter), from libgpod's itdb_device.c
_GENERATIONS = {
    "classic": ("iPod Classic", "B029 B147 B145 B150 B562 B565 C293 C297"),
    "nano3": ("iPod nano 3G", "A978 A980 B261 B249 B253 B257"),
    "nano4": ("iPod nano 4G", "B480 B651 B654 B657 B660 B663 B666 B598 B732 B735 B739 B742 B745 B748 B751 "
                              "B754 B903 B905 B907 B909 B911 B913 B915 B917 B918"),
    "nano5": ("iPod nano 5G", "C027 C031 C034 C037 C040 C043 C046 C049 C050 C060 C062 C064 C066 C068 C070 "
                              "C072 C074 C075"),
    "nano6": ("iPod nano 6G", "C525 C688 C689 C690 C691 C692 C693 C526 C694 C695 C696 C697 C698 C699"),
    "shuffle": ("iPod shuffle", "9724 9725 A546 A947 A949 A951 A953 C167 B225 B233 B231 B227 B228 B229 B518 "
                                "B520 B522 B524 B526 C306 C323 C381 C384 C387 B867 C164 C303 C307 C328 C331 "
                                "C584 C585 C749 C750 C751"),
    "touch": ("iPod touch", "A623 A627 B376 B528 B531 B533 C086 C008 C011 C540 C544 C547"),
    "video": ("iPod video", "A002 A146 A003 A147 A452 A444 A446 A664 A448 A450"),
    "nano": ("iPod nano", "A350 A352 A004 A099 A005 A107 A477 A426 A428 A487 A489 A725 A726 A497"),
    "mini": ("iPod mini", "9160 9436 9435 9434 9437 9800 9802 9804 9806 9801 9803 9805 9807"),
    "photo": ("iPod photo", "A079 A127 9829 9585 9830 9586 S492"),
    "old": ("iPod", "8513 8541 8697 8709 8737 8740 8738 8741 8976 8946 9460 9244 8948 9245 9282 9787 9268 E436"),
}
MODELS = {number: generation for generation, (_, numbers) in _GENERATIONS.items() for number in numbers.split()}
HASH58 = {"classic", "nano3", "nano4"}
LOCKED = {"nano5", "nano6", "touch"}


@dataclass
class IpodInfo:
    model: str  # what to call it
    checksum: str  # "none" or "hash58"
    guid: bytes | None  # the FireWire GUID hash58 is keyed by
    note: str = ""  # why it cannot be synced; nothing when it can


def is_ipod(root: Path) -> bool:
    """An iPod whose own firmware plays: one with Rockbox plays from folders, like any player."""
    return (Path(root) / CONTROL / "iTunes").is_dir() and not (Path(root) / ".rockbox").exists()


def _sysinfo(root: Path) -> dict[str, str]:
    try:
        text = (root / CONTROL / "Device" / "SysInfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {key.strip(): value.strip() for key, _, value in (line.partition(":") for line in text.splitlines())
            if value}


def _extended(root: Path) -> dict:
    try:
        found = plistlib.loads((root / CONTROL / "Device" / "SysInfoExtended").read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}
    return found if isinstance(found, dict) else {}


def _guid(text: str) -> bytes | None:
    text = str(text or "").strip()
    text = text[2:] if text[:2].lower() == "0x" else text
    return bytes.fromhex(text) if re.fullmatch(r"[0-9A-Fa-f]{16}", text) else None


def _database_signs(root: Path) -> str:
    """What the database already on the iPod says of its signing: "hash58", "locked" or ""."""
    try:
        with open(root / DATABASE, "rb") as file:
            head = file.read(0xA0)
    except OSError:
        return ""
    if len(head) < 0xA0 or head[:4] != b"mhbd":
        return ""
    if int.from_bytes(head[0x70:0x72], "little") in (2, 4) or any(head[0x72:0xA0]):
        return "locked"
    return "hash58" if any(head[0x58:0x6C]) else ""


def inspect(root: Path) -> IpodInfo:
    """Which iPod this is, and whether and how its database can be written."""
    root = Path(root)
    info, extended = _sysinfo(root), _extended(root)
    number = re.sub(r"^[A-Za-z](?=[A-Z0-9]{4})", "", info.get("ModelNumStr", ""))[:4].upper()
    generation = MODELS.get(number, "")
    model = _GENERATIONS[generation][0] if generation else "iPod"
    locked = t("Этот iPod не поддерживается: его база защищена подписью, которую нельзя сделать без iTunes")
    itunes = root / CONTROL / "iTunes"
    if generation == "shuffle" or (itunes / "iTunesSD").exists():
        return IpodInfo(_GENERATIONS["shuffle"][0], "none", None, t("iPod shuffle не поддерживается"))
    # iTunes 10 and later leave "iTunes Library.itlp" on every iPod it syncs: only the compressed database says more
    if generation in LOCKED or (itunes / "iTunesCDB").exists():
        return IpodInfo(model, "none", None, locked)
    version = extended.get("DBVersion")
    signs = _database_signs(root)
    if isinstance(version, int) and version > 3 or signs == "locked":
        return IpodInfo(model, "none", None, locked)
    if not (generation in HASH58 or version == 3 or signs == "hash58"):
        return IpodInfo(model, "none", None)
    guid = _guid(extended.get("FireWireGUID")) or _guid(info.get("FirewireGuid")) or _guid(_usb_serial(root))
    if guid is None:
        return IpodInfo(model, "hash58", None, t("Не удалось узнать FireWire ID этого iPod: без него он не примет базу"))
    return IpodInfo(model, "hash58", guid)


def _usb_serial(root: Path) -> str:
    """The serial number Windows reads from the drive's USB side: an iPod's is its FireWire GUID."""
    match = re.match(r"^([A-Za-z]):[\\/]?$", str(root))
    if sys.platform != "win32" or not match:
        return ""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                       wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                       wintypes.LPVOID]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # No access asked for: the descriptor is readable without opening the volume for reading
    handle = kernel.CreateFileW(f"\\\\.\\{match.group(1)}:", 0, 3, None, 3, 0, None)  # share read+write, open existing
    if handle in (None, wintypes.HANDLE(-1).value):
        return ""
    try:
        query = (ctypes.c_uint32 * 3)(0, 0, 0)  # StorageDeviceProperty, PropertyStandardQuery
        buffer = ctypes.create_string_buffer(1024)
        returned = wintypes.DWORD()
        if not kernel.DeviceIoControl(handle, 0x2D1400, query, ctypes.sizeof(query), buffer, len(buffer),
                                      ctypes.byref(returned), None):  # IOCTL_STORAGE_QUERY_PROPERTY
            return ""
        offset = int.from_bytes(buffer.raw[24:28], "little")  # STORAGE_DEVICE_DESCRIPTOR.SerialNumberOffset
        if not 0 < offset < returned.value:
            return ""
        return buffer.raw[offset:].split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
    finally:
        kernel.CloseHandle(handle)


# The sync. The plan of part 1 names files as they would lie in Sonicache/ on
# a drive; on an iPod each lies in iPod_Control/Music/Fnn under a name of its
# own, and the manifest keeps which, together with what the database says of
# it. Playlists are no files at all: the manifest keeps their text, and they
# go into the database.

MANIFEST_FILE = f"{CONTROL}/iTunes/sonicache-sync.json"
MUSIC = f"{CONTROL}/Music"
OUR_FILE = re.compile(rf"^{MUSIC}/F\d{{2}}/SNC[0-9A-Z]{{5}}\.(mp3|m4a)$")
NAME_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
FOLDERS = 20  # made when an iPod has no Fnn folder at all


class IpodTarget(devices.Target):
    plays = frozenset({".mp3", ".m4a"})
    companions = False

    def __init__(self, root: Path, info: IpodInfo, name: str = "iPod"):
        self.root = Path(root)
        self.info = info
        self.name = name or info.model
        self.drive = devices.DriveTarget(self.root)
        self.entries: dict[str, dict] = {}
        self.entries = devices.load_manifest(self)
        # What of the database is ours: its tracks and playlists by these ids, also after they leave the manifest
        self.dbids = {entry["dbid"] for entry in self.entries.values() if _number(entry.get("dbid"))}
        self.pids = {entry["pid"] for entry in self.entries.values() if _number(entry.get("pid"))}
        self.written: dict[str, dict] = {}

    def __str__(self) -> str:
        return f"iPod {self.root}"

    def _file(self, dest: str) -> str:
        """The file on the iPod a path of the plan stands for; nothing unless it is one of ours."""
        file = (self.entries.get(dest) or {}).get("file")
        return file if isinstance(file, str) and OUR_FILE.match(file) else ""

    def _playlist(self, dest: str) -> dict | None:
        entry = self.entries.get(dest)
        return entry if entry and entry.get("mode") == "playlist" and isinstance(entry.get("text"), str) else None

    def free(self) -> int:
        return self.drive.free()

    def exists(self, dest: str) -> bool:
        if dest == devices.MANIFEST_PATH:
            return self.drive.exists(MANIFEST_FILE)
        file = self._file(dest)
        return self._playlist(dest) is not None or bool(file) and self.drive.exists(file)

    def is_file(self, dest: str) -> bool:
        if dest == devices.MANIFEST_PATH:
            return self.drive.is_file(MANIFEST_FILE)
        file = self._file(dest)
        return self._playlist(dest) is not None or bool(file) and self.drive.is_file(file)

    def read_bytes(self, dest: str) -> bytes:
        if dest == devices.MANIFEST_PATH:
            return self.drive.read_bytes(MANIFEST_FILE)
        playlist = self._playlist(dest)
        if playlist is None:
            raise FileNotFoundError(dest)
        return playlist["text"].encode("utf-8")

    def write_bytes(self, dest: str, data: bytes) -> None:
        if dest == devices.MANIFEST_PATH:
            self.drive.write_bytes(MANIFEST_FILE, data)
            return
        # A playlist: it goes into the database at the end
        old = self.entries.get(dest) or {}
        pid = old["pid"] if _number(old.get("pid")) else random.getrandbits(63) | 1
        self.pids.add(pid)
        self.written[dest] = {"text": data.decode("utf-8"), "pid": pid}
        self.entries[dest] = {"mode": "playlist", **self.written[dest]}

    def put(self, local: Path, dest: str) -> int:
        suffix = posixpath.splitext(dest)[1].lower()
        old = self.entries.get(dest) or {}
        file = self._file(dest)
        if file and not file.endswith(suffix):  # made mp3 now, or no longer
            self.drive.delete(file)
            file = ""
        file = file or self._new_file(suffix)
        written = self.drive.put(local, file)
        dbid = old["dbid"] if _number(old.get("dbid")) else random.getrandbits(63) | 1
        self.dbids.add(dbid)
        self.written[dest] = {"file": file, "dbid": dbid, "track": _describe(local, dest, written)}
        self.entries[dest] = {**old, **self.written[dest]}
        return written

    def _new_file(self, suffix: str) -> str:
        music = self.root / MUSIC
        try:
            found = sorted(entry.name for entry in music.iterdir() if re.fullmatch(r"F\d{2}", entry.name)
                           and entry.is_dir())
        except FileNotFoundError:
            found = []
        if not found:
            found = [f"F{number:02d}" for number in range(FOLDERS)]
            for folder in found:
                (music / folder).mkdir(parents=True, exist_ok=True)
        while True:
            name = "SNC" + "".join(random.choice(NAME_LETTERS) for _ in range(5))
            file = f"{MUSIC}/{random.choice(found)}/{name}{suffix}"
            if not (self.root / file).exists():
                return file

    def describe(self, dest: str) -> dict:
        return self.written.pop(dest, {})

    def delete(self, dest: str) -> None:
        file = self._file(dest)
        if file:
            self.drive.delete(file)
        self.entries.pop(dest, None)

    def prune(self, folder: str) -> None:
        pass  # the Fnn folders are the iPod's own

    def clean_parts(self) -> None:
        for folder in (self.root / MUSIC).glob("F[0-9][0-9]"):
            for part in folder.glob("SNC*.part"):
                part.unlink(missing_ok=True)
        for name in ("iTunesDB.part", "sonicache-sync.json.part"):
            (self.root / CONTROL / "iTunes" / name).unlink(missing_ok=True)

    def begin(self, plan: devices.Plan) -> None:
        """The database without what is about to be deleted, before it is: the iPod never lists a file that is gone."""
        leaving = set(plan.delete)
        self._write_database({dest: entry for dest, entry in self.entries.items() if dest not in leaving})

    def finish(self, files: dict[str, dict]) -> None:
        self._write_database(files)

    def _write_database(self, files: dict[str, dict]) -> None:
        itunes = self.root / CONTROL / "iTunes"
        try:
            data = (self.root / DATABASE).read_bytes()
        except FileNotFoundError:
            db = itunesdb.new_database(self.name)
        else:
            try:
                db = itunesdb.parse(data)
            except ValueError as e:
                raise devices.TargetError(t("База iPod не прочиталась, она не тронута: {error}", error=e)) from e
            # The hash iTunes left proves the ID found is the iPod's: with another one it would refuse the database
            if self.info.guid is not None and any(data[0x58:0x6C]) \
                    and itunesdb.sign(data, self.info.guid)[0x58:0x6C] != data[0x58:0x6C]:
                raise devices.TargetError(t("FireWire ID этого iPod не подошёл к его базе: база не тронута"))
        # What the iPod recorded since the last write is carried into the tracks first
        counts = itunes / "Play Counts"
        if counts.is_file():
            try:
                itunesdb.apply_play_counts(db, counts.read_bytes())
            except ValueError as e:
                _log.warning("прослушивания на %s не прочитались: %s", self.root, e)
        order = [track.dbid for track in db.tracks]
        on_the_go = []
        for path in sorted(itunes.glob("OTGPlaylistInfo*")):
            try:
                on_the_go.append([order[index] for index in itunesdb.otg_indexes(path.read_bytes())
                                  if index < len(order)])
            except (OSError, ValueError) as e:
                _log.warning("плейлист On-The-Go %s не прочитался: %s", path, e)

        # Our tracks that stay are written again in their places, so playlists made on the iPod keep them
        tracks = {dest: entry for dest, entry in files.items()
                  if entry.get("mode") != "playlist" and isinstance(entry.get("file"), str)
                  and OUR_FILE.match(entry["file"]) and _number(entry.get("dbid")) and isinstance(entry.get("track"), dict)}
        ours = self.dbids | {entry["dbid"] for entry in files.values() if _number(entry.get("dbid"))}
        db.remove_tracks(ours - {entry["dbid"] for entry in tracks.values()})
        db.remove_playlists(self.pids | {entry["pid"] for entry in files.values() if _number(entry.get("pid"))})
        added = {dest: db.put_track(_info(entry["file"], entry["dbid"], entry["track"]))
                 for dest, entry in tracks.items()}
        for dest, entry in files.items():
            if entry.get("mode") == "playlist" and isinstance(entry.get("text"), str) and _number(entry.get("pid")):
                name, members = _parse_playlist(entry["text"])
                db.add_playlist(name, entry["pid"], [added[member] for member in members if member in added])
        by_dbid = {track.dbid: track.id for track in db.tracks}
        for number, dbids in enumerate(on_the_go, 1):
            kept = [by_dbid[dbid] for dbid in dbids if dbid in by_dbid]
            if kept:  # made on the iPod: the person's own playlist from now on, as iTunes does it
                db.add_playlist(f"On-The-Go {number}", random.getrandbits(63) | 1, kept)
        db.finish()
        data = db.to_bytes()
        if self.info.checksum == "hash58":
            if self.info.guid is None:
                raise devices.TargetError(self.info.note)
            data = itunesdb.sign(data, self.info.guid)
        self.drive.write_bytes(DATABASE, data)
        for path in [counts, *itunes.glob("OTGPlaylistInfo*")]:
            path.unlink(missing_ok=True)


def _number(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**64


def _parse_playlist(text: str) -> tuple[str, list[str]]:
    """The name and the tracks (paths of the plan) of a playlist the plan wrote as .m3u8."""
    name, members = "", []
    for line in text.splitlines():
        if line.startswith("#PLAYLIST:"):
            name = line.removeprefix("#PLAYLIST:")
        elif line and not line.startswith("#"):
            members.append(posixpath.normpath(posixpath.join(devices.PLAYLISTS_DIR, line)))
    return name, members


def _describe(local: Path, dest: str, size: int) -> dict:
    """What the database says of a file put on the iPod, read from the file itself."""
    from mutagen import File as MutagenFile

    from .downloader import read_tags

    tags = read_tags(local)
    try:
        audio = MutagenFile(local)
    except Exception:  # a file mutagen cannot read still goes, with what is known of it
        audio = None
    stream = getattr(audio, "info", None)
    year = re.match(r"\d{4}", str(tags.get("date") or ""))
    return {"title": tags.get("title") or posixpath.splitext(posixpath.basename(dest))[0],
            "artist": tags.get("artist") or "", "album": tags.get("album") or "",
            "albumartist": tags.get("albumartist") or "", "genre": tags.get("genre") or "",
            "year": int(year.group()) if year else 0, "track": int(tags.get("track") or 0),
            "disc": int(tags.get("disc") or 0), "compilation": bool(tags.get("compilation")),
            "length": round((getattr(stream, "length", 0) or 0) * 1000),
            "bitrate": round((getattr(stream, "bitrate", 0) or 0) / 1000),
            "samplerate": int(getattr(stream, "sample_rate", 0) or 44100), "size": size,
            "kind": posixpath.splitext(dest)[1].lower().lstrip("."), "added": int(time.time())}


def _info(file: str, dbid: int, track: dict) -> itunesdb.TrackInfo:
    """A track of the manifest for the database; the manifest is a file anyone may have edited."""
    text = lambda key: str(track.get(key) or "")[:500]  # noqa: E731
    number = lambda key: track.get(key) if _number(track.get(key)) and track.get(key) < 2**32 else 0  # noqa: E731
    kind = text("kind") if text("kind") in ("mp3", "m4a") else posixpath.splitext(file)[1].lstrip(".")
    return itunesdb.TrackInfo(path=itunesdb.ipod_path(file), title=text("title"), artist=text("artist"),
                              album=text("album"), albumartist=text("albumartist"), genre=text("genre"), kind=kind,
                              year=number("year"), track=number("track"), disc=number("disc"),
                              length=number("length"), bitrate=number("bitrate"),
                              samplerate=min(number("samplerate") or 44100, 0xFFFF), size=number("size"),
                              compilation=bool(track.get("compilation")), dbid=dbid, added=number("added"))
