"""The library copied to a player or a phone that shows up as a drive.

What goes is chosen elsewhere: this module gets the files, works out what the
device lacks and what it holds that is no longer wanted, and carries that out.

Everything the program puts on a device lives in one folder, Sonicache/, and
is listed in a manifest beside it. Only what the manifest lists is ever
deleted: music the person keeps on the device in other folders, or put into
ours by hand, stays where it is. The manifest lives on the device, not on the
computer, so a reinstalled program or another computer still knows what is
its own there.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import folders
from .logs import log

SYNC_DIR = "Sonicache"
MANIFEST = ".sonicache-sync.json"
MANIFEST_VERSION = 1
BITRATES = (128, 192, 256, 320)  # kbit/s offered for the mp3s made for a device
DEFAULT_BITRATE = 256
PART_LIMIT, PATH_LIMIT = 120, 240  # FAT32 takes 255 per name; players choke well before

_FAT_UNSAFE = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_log = log.getChild("devices")


def safe_rel(rel: str) -> str:
    """A relative path, "/" between its parts, as FAT32 and exFAT take it."""
    parts = []
    for part in rel.split("/"):
        part = _FAT_UNSAFE.sub("_", part)[:PART_LIMIT].rstrip(". ")
        if part:
            parts.append(part)
    path = "/".join(parts)
    if len(path) > PATH_LIMIT:  # the name is kept whole, the folders above give way
        name = parts[-1]
        head = "/".join(parts[:-1])[:max(0, PATH_LIMIT - len(name) - 1)].rstrip("/. ")
        path = f"{head}/{name}" if head else name[:PATH_LIMIT]
    return path


def _manifest_file(root: Path) -> Path:
    return Path(root) / SYNC_DIR / MANIFEST


def load_manifest(root: Path) -> dict[str, dict]:
    """What the program put on the device, by its path there; empty when that cannot be told."""
    path = _manifest_file(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        _log.warning("список синхронизации %s не прочитался: %s", path, e)
        return {}
    files = data.get("files") if isinstance(data, dict) and data.get("version") == MANIFEST_VERSION else None
    if not isinstance(files, dict):
        _log.warning("список синхронизации %s другой версии: ничего не будет удалено", path)
        return {}
    return {key: value for key, value in files.items() if isinstance(value, dict)}


def save_manifest(root: Path, files: dict[str, dict]) -> None:
    """Written beside and then put in place, so a pulled cable leaves the old list or the new one."""
    path = _manifest_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{MANIFEST}.tmp")
    partial.write_text(json.dumps({"version": MANIFEST_VERSION, "files": files}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    os.replace(partial, path)


PLAYLISTS_DIR = f"{SYNC_DIR}/Playlists"
TAG_ROOM = 65536  # what an mp3's tags and cover add to its estimated size


@dataclass
class Source:
    """A file of the library, and where it goes inside Sonicache/ ("/" between parts)."""
    path: Path
    rel: str
    duration: float = 0.0  # seconds, for the size of the mp3 made of it


@dataclass
class PlaylistSpec:
    name: str
    sources: list[Path]


@dataclass
class Item:
    """One file to write to the device."""
    source: Path
    dest: str  # from the device's root, "/" between parts
    mode: str  # "copy" or "mp3:<kbit/s>"
    size: int  # what it will take there, estimated for an mp3
    audio: bool = True  # a cover or lyrics beside the music are not counted as tracks


@dataclass
class Plan:
    write: list[Item] = field(default_factory=list)
    delete: list[str] = field(default_factory=list)
    keep: int = 0
    conflicts: list[str] = field(default_factory=list)  # someone else's files where ours would go
    playlists: dict[str, str] = field(default_factory=dict)  # path on the device -> its .m3u8 text
    free: int = 0
    need: int = 0  # bytes the sync takes beyond what it frees; less than nothing when it frees more
    fits: bool = True


def plan(root: Path, sources: list[Source], playlists: list[PlaylistSpec], transcode: bool = False,
         bitrate: int = DEFAULT_BITRATE, can_transcode: bool = True) -> Plan:
    """What the device lacks of these files, what it holds that is no longer
    wanted, and whether the difference fits on it."""
    root = Path(root)
    manifest = load_manifest(root)
    wanted: dict[str, Item] = {}
    taken: set[str] = set()  # FAT does not tell names apart by case
    by_source: dict[str, str] = {}
    covered: set[str] = set()

    def place(dest: str) -> str:
        stem, suffix = posixpath.splitext(dest)
        number = 1
        while dest.casefold() in taken:
            number += 1
            dest = f"{stem} ({number}){suffix}"
        taken.add(dest.casefold())
        return dest

    for source in sources:
        path = Path(source.path)
        try:
            size = path.stat().st_size
        except OSError:
            continue  # gone since it was chosen: the window counts it as missing
        mp3 = transcode and can_transcode and path.suffix.lower() != ".mp3"
        rel = safe_rel(source.rel)
        if mp3:
            rel = f"{posixpath.splitext(rel)[0]}.mp3"
            size = int(source.duration * bitrate * 1000 / 8) + TAG_ROOM
        dest = place(f"{SYNC_DIR}/{rel}")
        wanted[dest] = Item(path, dest, f"mp3:{bitrate}" if mp3 else "copy", size)
        by_source[os.path.normcase(str(path))] = dest
        # What players show beside the music: the album's cover and the track's lyrics
        lyrics = path.with_suffix(".lrc")
        if lyrics.is_file():
            beside = place(f"{posixpath.splitext(dest)[0]}.lrc")
            wanted[beside] = Item(lyrics, beside, "copy", lyrics.stat().st_size, audio=False)
        folder_key = os.path.normcase(str(path.parent))
        if folder_key not in covered:
            covered.add(folder_key)
            cover = folders.cover_file(path.parent)
            if cover is not None:
                beside = place(f"{posixpath.dirname(dest)}/{safe_rel(cover.name)}")
                wanted[beside] = Item(cover, beside, "copy", cover.stat().st_size, audio=False)

    result = Plan()
    freed = 0
    for dest, item in wanted.items():
        entry = manifest.get(dest)
        on_device = root / dest
        if entry is None:
            if on_device.exists():
                result.conflicts.append(dest)
            else:
                result.write.append(item)
            continue
        stat = item.source.stat()
        if (entry.get("source") == str(item.source) and entry.get("size") == stat.st_size
                and entry.get("mtime_ns") == stat.st_mtime_ns and entry.get("mode") == item.mode
                and on_device.is_file()):
            result.keep += 1
        else:
            result.write.append(item)
            freed += int(entry.get("bytes") or 0)

    for spec in playlists:
        lines = ["#EXTM3U", f"#PLAYLIST:{spec.name}"]
        for path in spec.sources:
            dest = by_source.get(os.path.normcase(str(path)))
            if dest:
                lines.append(posixpath.relpath(dest, PLAYLISTS_DIR))
        name = safe_rel(spec.name.replace("/", "_")) or "_"
        result.playlists[place(f"{PLAYLISTS_DIR}/{name}.m3u8")] = "\n".join(lines) + "\n"

    for dest, entry in manifest.items():
        if dest not in wanted and dest not in result.playlists:
            result.delete.append(dest)
            freed += int(entry.get("bytes") or 0)
    result.free = shutil.disk_usage(root).free
    result.need = sum(item.size for item in result.write) - freed
    result.fits = result.need <= result.free
    return result
