"""Playlists of one's own, and what has been listened to.

A playlist is a name and a list of tracks. Each track keeps its path and what
its row shows (title, artists, album, length), so a playlist opens without
reading a hundred files' tags, and a file that has since gone is still named.
They are kept in playlists.json beside the library's cache, and go out to other
players as .m3u8, the list of paths every player reads; one comes back in the
same way.

What was listened to goes into plays.jsonl, a line a track, once the player has
played enough of it to count (the rule Last.fm and ListenBrainz use: half the
track or four minutes). The lines only grow, so nothing is rewritten; they make
the play counts, "Recently played" and "Most played".
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections import Counter
from pathlib import Path

from . import logs

TRACK_FIELDS = ("path", "title", "artists", "album", "album_artist", "duration")
NAME_LIMIT = 120
TRACKS_LIMIT = 20_000
SMART = ("recent", "top")  # the playlists made from the plays themselves
SMART_SIZE = 100

_lock = threading.Lock()


def playlists_file() -> Path:
    return logs.data_dir() / "playlists.json"


def plays_file() -> Path:
    return logs.data_dir() / "plays.jsonl"


def track(value: dict) -> dict | None:
    """What a playlist keeps of a track, or None for something that is not one."""
    if not isinstance(value, dict) or not str(value.get("path") or "").strip():
        return None
    kept = {field: str(value.get(field) or "").strip() for field in TRACK_FIELDS if field != "duration"}
    try:
        kept["duration"] = max(0, round(float(value.get("duration") or 0)))
    except (TypeError, ValueError):
        kept["duration"] = 0
    if not kept["title"]:
        kept["title"] = Path(kept["path"]).stem
    return kept


def _playlist(value) -> dict | None:
    if not isinstance(value, dict) or not isinstance(value.get("id"), str):
        return None
    tracks = [kept for kept in map(track, value.get("tracks") or []) if kept][:TRACKS_LIMIT]
    name = " ".join(str(value.get("name") or "").split())[:NAME_LIMIT]
    return {"id": value["id"], "name": name or "Playlist", "tracks": tracks,
            "created": int(value.get("created") or 0), "modified": int(value.get("modified") or 0)}


def load() -> list[dict]:
    try:
        data = json.loads(playlists_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [kept for kept in map(_playlist, data if isinstance(data, list) else []) if kept]


def _save(playlists: list[dict]) -> None:
    path = playlists_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        spare = path.with_suffix(".tmp")
        spare.write_text(json.dumps(playlists, ensure_ascii=False), encoding="utf-8")
        os.replace(spare, path)  # a crash mid-write leaves the old list, not half of one
    except OSError as e:
        logs.log.warning("плейлисты не записаны: %s", e)


def save(playlist_id: str | None, name: str, tracks: list[dict]) -> dict:
    """Writes one playlist, a new one when there is no id, and returns it."""
    with _lock:
        playlists = load()
        now = int(time.time())
        found = next((entry for entry in playlists if entry["id"] == playlist_id), None)
        if found is None:
            found = {"id": secrets.token_hex(6), "created": now}
            playlists.append(found)
        found.update({"name": name, "tracks": tracks, "modified": now})
        kept = _playlist(found)
        playlists[playlists.index(found)] = kept
        _save(playlists)
        return kept


def add(playlist_id: str, tracks: list[dict]) -> dict | None:
    """Puts tracks at the end of a playlist; None when it is gone."""
    with _lock:
        playlists = load()
        found = next((entry for entry in playlists if entry["id"] == playlist_id), None)
        if found is None:
            return None
        found["tracks"] = (found["tracks"] + [kept for kept in map(track, tracks) if kept])[:TRACKS_LIMIT]
        found["modified"] = int(time.time())
        _save(playlists)
        return found


def delete(playlist_id: str) -> None:
    with _lock:
        _save([entry for entry in load() if entry["id"] != playlist_id])


# .m3u8: the list of paths, with each track's length and name on the line above

def to_m3u8(playlist: dict, target: Path) -> str:
    """The playlist as .m3u8 text for a file at target: a path on the same drive
    is written relative to it, so the folder can be moved or copied whole."""
    lines = ["#EXTM3U", f"#PLAYLIST:{playlist['name']}"]
    for entry in playlist["tracks"]:
        name = " - ".join(part for part in (entry["artists"] or entry["album_artist"], entry["title"]) if part)
        lines.append(f"#EXTINF:{entry['duration'] or -1},{name}")
        try:
            lines.append(os.path.relpath(entry["path"], target.parent))
        except ValueError:  # another drive has no relative way to it
            lines.append(entry["path"])
    return "\n".join(lines) + "\n"


def from_m3u8(path: Path, text: str) -> dict:
    """A playlist's name and tracks from .m3u or .m3u8 text: the paths, taken
    relative to the file, and the names its #EXTINF lines give them."""
    name, tracks, extinf = path.stem, [], None
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if line.startswith("#PLAYLIST:"):
            name = line.removeprefix("#PLAYLIST:").strip() or name
        elif line.startswith("#EXTINF:"):
            extinf = line.removeprefix("#EXTINF:")
        elif line and not line.startswith("#"):
            if "://" in line and not line.lower().startswith("file://"):
                extinf = None
                continue  # a stream or a web address is no file of the library
            file = Path(line.removeprefix("file:///") if line.lower().startswith("file://") else line)
            file = file if file.is_absolute() else path.parent / file
            entry = {"path": os.path.normpath(str(file)), "title": "", "artists": "", "duration": 0}
            if extinf:
                seconds, _, shown = extinf.partition(",")
                artists, _, title = shown.partition(" - ")
                entry.update({"title": (title or artists).strip(), "artists": artists.strip() if title else "",
                              "duration": _seconds(seconds)})
            tracks.append(track(entry))
            extinf = None
    return {"name": name, "tracks": [entry for entry in tracks if entry]}


def _seconds(text: str) -> int:
    try:
        return max(0, round(float(text.strip().split()[0])))
    except (ValueError, IndexError):
        return 0


# What has been listened to

def record(played: dict, when: float | None = None) -> dict | None:
    """Writes one listen; returns it as written, or None for no track."""
    kept = track(played)
    if kept is None:
        return None
    kept["time"] = int(when if when is not None else time.time())
    path = plays_file()
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as lines:
                lines.write(json.dumps(kept, ensure_ascii=False) + "\n")
        except OSError as e:
            logs.log.warning("прослушивание не записано: %s", e)
    return kept


def plays() -> list[dict]:
    """Every listen, oldest first; a line cut short by a crash is skipped."""
    try:
        text = plays_file().read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kept = track(entry)
        if kept is not None:
            kept["time"] = int(entry.get("time") or 0) if isinstance(entry.get("time"), (int, float)) else 0
            found.append(kept)
    return found


def counts(listens: list[dict] | None = None) -> dict[str, int]:
    """How many times each file was listened to, by its path."""
    return dict(Counter(entry["path"] for entry in (plays() if listens is None else listens)))


def smart(kind: str, listens: list[dict] | None = None, size: int = SMART_SIZE) -> list[dict]:
    """The tracks of a playlist made from the plays: "recent", the latest
    first, each once; "top", the most listened to, ties going to the later."""
    listens = plays() if listens is None else listens
    latest: dict[str, dict] = {}
    for entry in listens:
        latest[entry["path"]] = entry  # the newest line names the track as it is now
    if kind == "recent":
        ordered = sorted(latest.values(), key=lambda entry: entry["time"], reverse=True)
        return [{**entry, "plays": 0} for entry in ordered[:size]]
    tally = counts(listens)
    ordered = sorted(latest.values(), key=lambda entry: (tally[entry["path"]], entry["time"]), reverse=True)
    return [{**entry, "plays": tally[entry["path"]]} for entry in ordered[:size]]
