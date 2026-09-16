"""Releases the program keeps an eye on.

A watched link — usually a playlist — is downloaded again now and then into the
folder it went to the first time. The downloader already skips every file that
is on disk, so each check costs a page read and fetches only the tracks that
were added since.

The list is a small JSON file beside the history. Everything here is plain data
and dates; the queueing itself belongs to the window's Api.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import logs

WATCH_NAME = "watch.json"
CHECK_EVERY = 12 * 3600  # seconds between automatic checks of one link
WATCH_LIMIT = 50
# What a check reuses from the first download, so the new tracks land beside
# the old ones even if the settings have changed since.
KEPT = ("folder", "format", "track_name", "folder_name")


def watch_file() -> Path:
    return logs.data_dir() / WATCH_NAME


def load() -> list[dict]:
    try:
        data = json.loads(watch_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [entry for entry in data if _valid(entry)] if isinstance(data, list) else []


def save(entries: list[dict]) -> None:
    try:
        watch_file().parent.mkdir(parents=True, exist_ok=True)
        watch_file().write_text(json.dumps(entries[:WATCH_LIMIT], ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except OSError as e:  # a read-only profile costs the list, not the program
        logs.log.warning("не сохранил список слежения: %s", e)


def _valid(entry) -> bool:
    return (isinstance(entry, dict) and isinstance(entry.get("link"), str) and entry["link"].strip()
            and isinstance(entry.get("settings"), dict))


def add(entries: list[dict], link: str, title: str, service: str, settings: dict,
        now: float | None = None) -> list[dict]:
    """The list with this link watched; watching it again refreshes the entry."""
    now = time.time() if now is None else now
    kept = {key: settings[key] for key in KEPT if key in settings}
    rest = [entry for entry in entries if entry["link"] != link]
    rest.insert(0, {"link": link, "title": title or link, "service": service or "",
                    "settings": kept, "added": now, "checked": now, "job": None})
    return rest[:WATCH_LIMIT]


def remove(entries: list[dict], link: str) -> list[dict]:
    return [entry for entry in entries if entry["link"] != link]


def due(entries: list[dict], now: float | None = None, every: float = CHECK_EVERY) -> list[dict]:
    """The entries whose last check is old enough for another."""
    now = time.time() if now is None else now
    return [entry for entry in entries if now - float(entry.get("checked") or 0) >= every]
