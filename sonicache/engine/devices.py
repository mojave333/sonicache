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
import re
from pathlib import Path

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
