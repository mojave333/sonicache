"""Releases the program keeps an eye on.

A watched link — usually a playlist — is downloaded again now and then into the
folder it went to the first time. The downloader already skips every file that
is on disk, so each check costs a page read and fetches only the tracks that
were added since.

An artist can be watched too. Then a check reads the artist's releases and
downloads the ones that are new: those not among the releases known when the
watch began, and not older than it by more than a couple of months, since a
catalogue now and then adds an old record it did not have.

The list is a small JSON file beside the history. Everything here is plain data
and dates; the queueing itself belongs to the window's Api.
"""

from __future__ import annotations

import datetime
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
# A release this much older than the watch is an old one the catalogue added late
LATE_ADDITION = 60 * 86400


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
        now: float | None = None, known: list[str] | None = None) -> list[dict]:
    """The list with this link watched; watching it again refreshes the entry.
    known is given for an artist: the links of the releases they already have."""
    now = time.time() if now is None else now
    kept = {key: settings[key] for key in KEPT if key in settings}
    rest = [entry for entry in entries if entry["link"] != link]
    entry = {"link": link, "title": title or link, "service": service or "",
             "settings": kept, "added": now, "checked": now, "job": None}
    if known is not None:
        entry.update(kind="artist", known=sorted(set(known)))
    rest.insert(0, entry)
    return rest[:WATCH_LIMIT]


def new_releases(entry: dict, releases: list[dict]) -> list[dict]:
    """The releases of a watched artist that came out since the watch began,
    oldest first; the entry learns every release it has now seen."""
    known = set(entry.get("known") or [])
    since = float(entry.get("added") or 0)
    fresh = [release for release in releases
             if release["link"] not in known and _released_since(release.get("date") or "", since)]
    entry["known"] = sorted(known | {release["link"] for release in releases})
    return fresh[::-1]


def _released_since(date: str, since: float) -> bool:
    try:
        if len(date) >= 10:
            day = datetime.datetime.strptime(date[:10], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
            return day.timestamp() >= since - LATE_ADDITION
        if len(date) == 4:  # YouTube Music names only the year
            return int(date) >= datetime.datetime.fromtimestamp(since, datetime.timezone.utc).year
    except ValueError:
        pass
    return True  # no date to go by: a release unseen before is taken for new


def remove(entries: list[dict], link: str) -> list[dict]:
    return [entry for entry in entries if entry["link"] != link]


def due(entries: list[dict], now: float | None = None, every: float = CHECK_EVERY) -> list[dict]:
    """The entries whose last check is old enough for another."""
    now = time.time() if now is None else now
    return [entry for entry in entries if now - float(entry.get("checked") or 0) >= every]
