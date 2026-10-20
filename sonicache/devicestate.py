"""The players and phones synced to and the library over Wi-Fi, as the window remembers them,
and which files of the library a device gets.
"""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

from . import logs, playlists, subsonic
from .engine import devices, folders
from .i18n import t
from .libraryscan import _tags

# The players and phones synced to: the folders chosen as devices by hand, and
# what each device gets, by its id (engine/devices.py)
DEVICES_FILE = "devices.json"

DEFAULT_SELECTION = {"all": False, "playlists": [], "albums": [], "artists": [], "transcode": False,
                     "bitrate": devices.DEFAULT_BITRATE}

# The names the window gives the playlists made of the plays, for their .m3u8 on a device
SMART_NAMES = {"recent": "Недавно слушали", "top": "Часто слушаю"}

_DEVICES_LOCK = threading.Lock()

# The library over Wi-Fi (subsonic.py): whether it is on, the port and the password. Not in the
# settings: the window saves those whole from its own copy, and would put back a password changed here
WIFI_FILE = "wifi.json"

WIFI_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # nothing to mistake for something else on a phone's keyboard

WIFI_PASSWORD_LENGTH = 10

def _new_wifi_password() -> str:
    return "".join(secrets.choice(WIFI_ALPHABET) for _ in range(WIFI_PASSWORD_LENGTH))

def _wifi_settings() -> dict:
    """wifi.json as it is, with a password made and kept the first time it is asked for."""
    data = logs.read_json(logs.data_dir() / WIFI_FILE)
    password = data.get("password")
    port = data.get("port")
    kept = {"on": bool(data.get("on")),
            "port": port if isinstance(port, int) and 1024 <= port <= 65535 else subsonic.PORT,
            "password": password if isinstance(password, str) and len(password) >= 6 else _new_wifi_password()}
    if kept["password"] != password:
        logs.write_json(logs.data_dir() / WIFI_FILE, kept)
    return kept

def _devices_data() -> dict:
    """devices.json as it is, with the two keys always there."""
    data = logs.read_json(logs.data_dir() / DEVICES_FILE)
    chosen = [str(path) for path in data.get("folders") or [] if isinstance(path, str)]
    kept = {key: value for key, value in (data.get("devices") or {}).items() if isinstance(value, dict)}
    return {"folders": chosen, "devices": kept}

def _selection(value) -> dict:
    """What a device gets, as the window sent or the file kept it, with nothing else in it."""
    value = value if isinstance(value, dict) else {}
    names = lambda key: [str(entry) for entry in value.get(key) or [] if isinstance(entry, str)]  # noqa: E731
    bitrate = value.get("bitrate")
    return {"all": bool(value.get("all")), "playlists": names("playlists"), "albums": names("albums"),
            "artists": [name.casefold() for name in names("artists")], "transcode": bool(value.get("transcode")),
            "bitrate": bitrate if bitrate in devices.BITRATES else devices.DEFAULT_BITRATE}

def _synced(device: devices.Device) -> bool:
    """Whether the program put something on the device before. A phone is not
    opened to tell: it is asked for every few seconds."""
    if device.kind == "mtp" or device.note:
        return False
    try:
        with devices.open_target(device) as target:
            return bool(devices.load_manifest(target))
    except OSError:
        return False

def _find_device(device_id: str) -> devices.Device | None:
    return next((device for device in devices.find_devices(_devices_data()["folders"]) if device.id == device_id),
                None)

def _sync_sources(selection: dict, roots: list[str],
                  library: list[dict]) -> tuple[list[devices.Source], list[devices.PlaylistSpec], int]:
    """The files a device gets for what was chosen, each once; its playlists;
    and how many of the files chosen are gone from the disk."""
    everything = selection["all"]
    albums = {os.path.normcase(path) for path in selection["albums"]}
    artists = set(selection["artists"])
    paths: list[Path] = []
    for item in library:
        if everything or os.path.normcase(item["path"]) in albums or (item.get("artist") or "").casefold() in artists:
            entry = Path(item["path"])
            paths += folders.album_files(entry)[0] if item["album"] else [entry]
    chosen = set(selection["playlists"])
    lists = [{"name": t(SMART_NAMES[kind]), "tracks": playlists.smart(kind)}
             for kind in playlists.SMART if everything or kind in chosen]
    lists += [entry for entry in playlists.load() if everything or entry["id"] in chosen]
    specs = []
    for entry in lists:
        files = [Path(track["path"]) for track in entry["tracks"]]
        if files:  # a list of the plays is empty until something has been played
            specs.append(devices.PlaylistSpec(entry["name"], files))
            paths += files
    ordered = sorted((Path(root).expanduser() for root in roots), key=lambda root: len(root.parts), reverse=True)
    found, seen, missing = [], set(), 0
    for path in paths:
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            missing += 1
            continue
        found.append(devices.Source(path, _device_rel(path, ordered), _tags(path)["duration"]))
    return found, specs, missing

def _device_rel(path: Path, roots: list[Path]) -> str:
    """Where a file goes inside Sonicache/: as it lies in its library folder,
    or under Other/ when a playlist took it from outside them."""
    for root in roots:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return f"Other/{path.parent.name}/{path.name}"
