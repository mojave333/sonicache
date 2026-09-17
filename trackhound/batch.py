"""Reading a list of things to download out of a file.

Two shapes are understood. A plain text file holds one entry per line: a link,
or a name the way it would be typed into the window, with blank lines and lines
starting with # ignored. A CSV file is what the playlist exporters write —
Exportify, TuneMyMusic, Soundiiz — and is read by its column names: a link or
URI column when there is one, otherwise the track and artist columns.

A row of a CSV export is a single song, so an entry built from its names is
marked "track:". Searching by name prefers an album over a track, which is
right for something typed into the window and wrong here: a playlist that has
the title track of an album would otherwise download the whole album.
"""

from __future__ import annotations

import csv
import io
import re

LIMIT = 500  # entries taken from one file
TRACK_PREFIX = "track:"
_LINK_RE = re.compile(r"https?://\S+|spotify:(?:album|track|playlist):[A-Za-z0-9]{22}", re.I)

# Header names; both sides are reduced by _key() before they are compared
_LINK_COLUMNS = ("Track URI", "Spotify URI", "URI", "Track URL", "Spotify URL", "URL", "Link",
                 "Spotify Track URI", "Spotify - id")
_TRACK_COLUMNS = ("Track Name", "Track", "Title", "Song", "Song Name", "Name")
_ARTIST_COLUMNS = ("Artist Name(s)", "Artist Name", "Artists", "Artist", "Album Artist")


def parse(text: str, name: str = "") -> tuple[list[str], int]:
    """The entries in a list, in order and without repeats, and how many lines
    were not understood."""
    text = text.lstrip("﻿")
    lower = name.lower()
    if lower.endswith(".csv") or (not lower.endswith(".txt") and _looks_like_csv(text)):
        entries, skipped = _from_csv(text)
    else:
        entries, skipped = _from_lines(text)
    seen, unique = set(), []
    for entry in entries:
        key = entry.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(entry)
    return unique[:LIMIT], skipped


def read(data: bytes, name: str = "") -> tuple[list[str], int]:
    """Like parse(), from the bytes of a file in whatever encoding it came in."""
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return parse(data.decode(encoding), name)
        except UnicodeDecodeError:
            continue
    return parse(data.decode("latin-1"), name)


def _key(name: str) -> str:
    """A header reduced to its letters, so "Artist Name(s)" and "artist names" meet."""
    return " ".join(re.sub(r"[^\w ]+", "", (name or "").lower()).split())


def _from_lines(text: str) -> tuple[list[str], int]:
    entries, skipped = [], 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        links = _LINK_RE.findall(line)
        if links:
            entries.extend(link.rstrip(".,;)") for link in links)
        elif len(line) >= 2:
            entries.append(" ".join(line.split()))
        else:
            skipped += 1
    return entries, skipped


def _looks_like_csv(text: str) -> bool:
    first = next((line for line in text.splitlines() if line.strip()), "")
    if not any(mark in first for mark in (",", ";", "\t")):
        return False
    cells = {_key(cell) for cell in re.split(r"[,;\t]", first)}
    known = {_key(name) for name in _TRACK_COLUMNS + _LINK_COLUMNS}
    return bool(cells & known)


def _from_csv(text: str) -> tuple[list[str], int]:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = {_key(name): name for name in reader.fieldnames or []}

    def column(candidates):
        return next((columns[_key(name)] for name in candidates if _key(name) in columns), None)

    link_col, track_col, artist_col = column(_LINK_COLUMNS), column(_TRACK_COLUMNS), column(_ARTIST_COLUMNS)

    entries, skipped = [], 0
    for row in reader:
        link = (row.get(link_col) or "").strip() if link_col else ""
        if _LINK_RE.fullmatch(link):
            entries.append(link)
            continue
        track = " ".join((row.get(track_col) or "").split()) if track_col else ""
        if track:
            # Exporters put every artist in one cell; a search for the song goes
            # by the first of them.
            artists = (row.get(artist_col) or "").strip() if artist_col else ""
            artist = re.split(r"\s*[,;]\s*", artists)[0] if artists else ""
            entries.append(f"{TRACK_PREFIX}{artist} - {track}" if artist else f"{TRACK_PREFIX}{track}")
            continue
        found = next((match.group(0) for cell in row.values() if isinstance(cell, str)
                      for match in [_LINK_RE.search(cell)] if match), None)
        if found:
            entries.append(found)
        elif any(isinstance(cell, str) and cell.strip() for cell in row.values()):
            skipped += 1
    return entries, skipped
