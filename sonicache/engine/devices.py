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

import getpass
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import folders
from .i18n import t
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
    return {key: value for key, value in files.items() if isinstance(value, dict) and _ours(key)}


def _ours(dest: str) -> bool:
    """A path the program could have written: inside Sonicache/, with no way out of it.
    The manifest is a file on a device anyone may have edited, and its paths are deleted."""
    parts = dest.split("/")
    return (len(parts) > 1 and parts[0] == SYNC_DIR and "\\" not in dest and ":" not in dest
            and all(part not in ("", ".", "..") for part in parts))


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


SAVE_EVERY = 20  # files written between two saves of the manifest


@dataclass
class Report:
    copied: int = 0  # tracks; a cover or lyrics beside them are not counted
    deleted: int = 0
    failed: list[str] = field(default_factory=list)  # names of the files that could not be made
    conflicts: list[str] = field(default_factory=list)
    stopped: bool = False
    error: str = ""  # why it ended early: the device went away or is full


class _Failed(Exception):
    """One file could not be made; the rest go on."""


def run(root: Path, plan: Plan, ffmpeg: str | None = None,
        progress: Callable[[int, int, str], None] | None = None,
        stop: threading.Event | None = None) -> Report:
    """Carries a plan out: deletions first, to free the room, then the files
    one by one, then the playlists. The manifest is kept up with the work, so
    a sync cut short by a pulled cable or a stop goes on from there next time."""
    root = Path(root)
    report = Report(conflicts=list(plan.conflicts))
    files = load_manifest(root)
    try:
        _remove_parts(root)
        for dest in plan.delete:
            target = root / dest
            target.unlink(missing_ok=True)
            files.pop(dest, None)
            report.deleted += 1
            _prune(root, target.parent)
        save_manifest(root, files)
        total = len(plan.write)
        for index, item in enumerate(plan.write):
            if progress is not None:
                progress(index, total, item.source.name)
            if stop is not None and stop.is_set():
                report.stopped = True
                break
            try:
                files[item.dest] = _write(root, item, ffmpeg, stop)
            except _Failed as e:
                if stop is not None and stop.is_set():  # ffmpeg was cut short by the stop itself
                    report.stopped = True
                    break
                _log.warning("синхронизация: %s не записан: %s", item.source, e)
                report.failed.append(item.source.name)
                continue
            report.copied += item.audio
            if (index + 1) % SAVE_EVERY == 0:
                save_manifest(root, files)
        if not report.stopped:
            for dest, text in plan.playlists.items():
                _write_playlist(root, dest, text, files, report)
    except OSError as e:
        _log.warning("синхронизация с %s прервана: %s", root, e)
        report.error = t("Устройство отключено или заполнено: {error}", error=e.strerror or e)
    finally:
        try:
            save_manifest(root, files)
        except OSError as e:  # the device is gone: what was done is what the last save says
            _log.warning("список синхронизации на %s не сохранён: %s", root, e)
    return report


def _write(root: Path, item: Item, ffmpeg: str | None, stop: threading.Event | None) -> dict:
    """One file onto the device, under a .part name until it is whole; its manifest entry."""
    try:
        stat = item.source.stat()
    except OSError as e:
        raise _Failed(e) from e
    target = root / item.dest
    partial = target.with_name(f"{target.name}.part")
    target.parent.mkdir(parents=True, exist_ok=True)
    if item.mode == "copy":
        try:
            shutil.copyfile(item.source, partial)
        except OSError as e:
            # The file in the library went away or is locked: that one file failed.
            # Anything else is the device's own trouble and ends the sync (run).
            if e.filename != str(item.source) and item.source.is_file():
                raise
            partial.unlink(missing_ok=True)
            raise _Failed(e) from e
    else:
        if not ffmpeg:
            raise _Failed("ffmpeg")
        _to_mp3(ffmpeg, item.source, partial, int(item.mode.partition(":")[2]), stop)
    os.replace(partial, target)
    return {"source": str(item.source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "mode": item.mode,
            "bytes": target.stat().st_size}


def _write_playlist(root: Path, dest: str, text: str, files: dict[str, dict], report: Report) -> None:
    target = root / dest
    if target.exists():
        if dest not in files:
            report.conflicts.append(dest)
            return
        if target.read_text(encoding="utf-8", errors="replace") == text:
            return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.part")
    partial.write_text(text, encoding="utf-8", newline="\n")
    os.replace(partial, target)
    files[dest] = {"source": "", "size": 0, "mtime_ns": 0, "mode": "playlist", "bytes": len(text.encode())}


def _to_mp3(ffmpeg: str, source: Path, target: Path, bitrate: int, stop: threading.Event | None) -> None:
    """An mp3 of a file for a player that takes nothing else, or for a small card:
    the tags and the cover go along, the file in the library is not touched."""
    command = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(source),
               "-map", "0:a:0", "-map", "0:v?", "-c:a", "libmp3lame", "-b:a", f"{bitrate}k", "-ar", "44100",
               "-c:v", "copy", "-map_metadata", "0", "-id3v2_version", "3", "-f", "mp3", str(target)]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # no console flashing up
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", errors="replace", creationflags=flags)
    complaints: list[str] = []
    reader = threading.Thread(target=lambda: complaints.extend(process.stderr), daemon=True)
    reader.start()
    try:
        while process.poll() is None:
            if stop is not None and stop.is_set():
                raise _Failed("stopped")
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=5)
    if process.returncode != 0:
        target.unlink(missing_ok=True)
        lines = "".join(complaints).strip().splitlines()
        raise _Failed(lines[-1] if lines else f"ffmpeg {process.returncode}")


def _remove_parts(root: Path) -> None:
    """Files a sync cut short left half written."""
    for folder, _, names in os.walk(root / SYNC_DIR):
        for name in names:
            if name.endswith(".part"):
                os.remove(os.path.join(folder, name))


def _prune(root: Path, folder: Path) -> None:
    """Folders left empty inside Sonicache/, from the deepest up."""
    top = root / SYNC_DIR
    while folder != top and top in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return  # not empty: what is above is not either
        folder = folder.parent


DRIVE_REMOVABLE = 2  # GetDriveTypeW: card readers, players, phones as a drive


@dataclass
class Device:
    id: str  # "vol:<volume serial>" or "folder:<hash of the path>"
    name: str
    root: str
    free: int
    total: int
    kind: str  # "drive" or "folder"

    def to_dict(self) -> dict:
        return asdict(self)


def folder_id(path: str) -> str:
    """A chosen folder's id: the same however its path is spelled."""
    key = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
    return f"folder:{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def find_devices(chosen: list[str]) -> list[Device]:
    """The removable drives plugged in now, then the folders chosen by hand that are there."""
    found = []
    for root, label, serial in _drives():
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue  # pulled out while it was looked at
        letter = root.rstrip("\\/") if re.match(r"^[A-Za-z]:", root) else Path(root).name
        found.append(Device(f"vol:{serial:08X}", label or t("Диск {letter}", letter=letter), root,
                            usage.free, usage.total, "drive"))
    for path in chosen:
        if not os.path.isdir(path):
            continue
        usage = shutil.disk_usage(path)
        found.append(Device(folder_id(path), Path(path).name or path, path, usage.free, usage.total, "folder"))
    return found


def _drives() -> list[tuple[str, str, int]]:
    """(root, label, volume serial) of each removable drive with a medium in it."""
    if sys.platform == "win32":
        import ctypes

        kernel = ctypes.windll.kernel32
        mask = kernel.GetLogicalDrives()
        found = []
        old_mode = kernel.SetErrorMode(1)  # an empty card reader is no reason for a "No disk" dialog
        try:
            for index in range(26):
                root = f"{chr(65 + index)}:\\"
                if not mask & (1 << index) or kernel.GetDriveTypeW(root) != DRIVE_REMOVABLE:
                    continue
                label = ctypes.create_unicode_buffer(261)
                serial = ctypes.c_uint32()
                if kernel.GetVolumeInformationW(root, label, 261, ctypes.byref(serial), None, None, None, 0):
                    found.append((root, label.value, serial.value))
        finally:
            kernel.SetErrorMode(old_mode)
        return found
    user = getpass.getuser()
    bases = [Path("/Volumes")] if sys.platform == "darwin" else [Path("/media") / user, Path("/run/media") / user]
    found = []
    for base in bases:
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir() and entry.resolve() != Path("/"):  # macOS lists its own disk there too
                    found.append((str(entry), entry.name, os.stat(entry).st_dev))
            except OSError:
                continue
    return found
