"""Song lyrics from LRCLIB (lrclib.net), a free open database, plain and synced.

A track's plain lyrics go into its tags, where players and phones show them;
the synced ones, "[mm:ss.xx] line" per line, go into an .lrc file beside it,
which players with a lyrics pane scroll along with the song.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .logs import log
from .matcher import _NOISE_RE
from .models import Album, Track

API = "https://lrclib.net/api"
# LRCLIB asks the programs that use it to name themselves
USER_AGENT = "Trackhound (https://github.com/mojave333/trackhound)"
# How far a found song's length may be from the track's, in seconds
LENGTH_SLACK = 3
_TIMESTAMP_RE = re.compile(r"^\[\d+:\d+(?:[.:]\d+)?\]\s?", re.M)


@dataclass
class Lyrics:
    plain: str
    synced: str = ""  # LRC; empty when LRCLIB has no timings for the song


def find(track: Track, album: Album) -> Lyrics | None:
    """The lyrics of this track, or None: none known, an instrumental, or no
    answer from LRCLIB. Lyrics are a nicety, so nothing here raises."""
    title = " ".join(_NOISE_RE.sub(" ", track.title).split()) or track.title
    artist = track.artists.split(",")[0].strip()
    duration = round(track.duration)
    try:
        found = None
        if duration:
            found = _ask("get", artist_name=artist, track_name=title, album_name=album.name, duration=duration)
        if not found:
            songs = _ask("search", artist_name=artist, track_name=title) or []
            found = next((song for song in songs if isinstance(song, dict) and _fits(song, duration)), None)
    except (OSError, ValueError) as e:
        log.getChild("lyrics").warning("текст для «%s» не получен: %s", track.title, e)
        return None
    if not found or found.get("instrumental"):
        return None
    synced = (found.get("syncedLyrics") or "").strip()
    plain = (found.get("plainLyrics") or "").strip() or _TIMESTAMP_RE.sub("", synced).strip()
    return Lyrics(plain, synced) if plain else None


def _fits(song: dict, duration: int) -> bool:
    has_words = song.get("plainLyrics") or song.get("syncedLyrics")
    length = song.get("duration") or 0
    return bool(has_words) and (not duration or not length or abs(length - duration) <= LENGTH_SLACK)


def _ask(endpoint: str, **params):
    """LRCLIB's answer, or None for a song it does not know. A server error is
    asked again once: LRCLIB answers 503 now and then and the next moment not."""
    request = urllib.request.Request(f"{API}/{endpoint}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": USER_AGENT})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 2 or e.code < 500:
                raise
        time.sleep(1)
    return None
