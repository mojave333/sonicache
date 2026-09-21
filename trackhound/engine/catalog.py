"""Looking music up in the catalogues to choose what to download: albums,
tracks and artists with their pictures, for the window's Search.

Deezer answers first: one quick request for each kind, pictures included,
no account needed. Where it does not answer, YouTube Music is asked instead.
Every result carries a link that sources.resolve() opens, so a chosen result
downloads exactly the way the same link pasted by hand would.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .logs import log
from .matcher import ytmusic
from .models import SourceError
from .net import BROWSER_UA

LIMITS = {"album": 24, "track": 20, "artist": 8}
_KINDS = {"album": "album", "single": "single", "ep": "ep", "compile": "compilation", "compilation": "compilation"}
_log = log.getChild("search")


def search(query: str) -> dict:
    """Albums, tracks and artists for the query, and the catalogue they come from."""
    query = " ".join(str(query).split())
    if not query:
        return {"service": "", "albums": [], "tracks": [], "artists": []}
    try:
        return _deezer_search(query)
    except (OSError, ValueError) as e:
        _log.warning("Deezer не ответил на поиск «%s»: %s; ищу на YouTube Music", query, e)
    try:
        return _youtube_search(query)
    except Exception as e:  # ytmusicapi raises whatever its parsing meets
        raise SourceError.of("offline", "Поиск не удался: ни Deezer, ни YouTube Music не ответили ({error})",
                             error=e) from e


def _deezer_search(query: str) -> dict:
    with ThreadPoolExecutor(max_workers=3) as pool:
        found = {kind: pool.submit(_deezer, f"search/{kind}", q=query, limit=limit) for kind, limit in LIMITS.items()}
        answers = {kind: future.result() for kind, future in found.items()}
    return {
        "service": "Deezer",
        "albums": [_deezer_album(item) for item in answers["album"]],
        "tracks": [_deezer_track(item) for item in answers["track"]],
        "artists": [_deezer_artist(item) for item in answers["artist"]],
    }


def _deezer(path: str, **params) -> list[dict]:
    """One request with a short wait: a search is typed by a person waiting for it."""
    url = f"https://api.deezer.com/{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read())
    if not isinstance(data, dict):
        raise ValueError(f"not an object: {url}")
    if data.get("error"):
        raise ValueError(f"Deezer: {data['error']}")
    return [item for item in data.get("data") or [] if isinstance(item, dict)]


def _deezer_album(item: dict) -> dict:
    return {
        "kind": "album",
        "title": item.get("title") or "",
        "artist": (item.get("artist") or {}).get("name") or "",
        "type": _KINDS.get(item.get("record_type") or "", "album"),
        "year": (item.get("release_date") or "")[:4],
        "tracks": item.get("nb_tracks") or 0,
        "explicit": bool(item.get("explicit_lyrics")),
        "cover": item.get("cover_medium") or item.get("cover_big") or "",
        "link": item.get("link") or f"https://www.deezer.com/album/{item.get('id')}",
    }


def _deezer_track(item: dict) -> dict:
    album = item.get("album") or {}
    return {
        "kind": "track",
        "title": item.get("title") or "",
        "artist": (item.get("artist") or {}).get("name") or "",
        "album": album.get("title") or "",
        "duration": item.get("duration") or 0,
        "explicit": bool(item.get("explicit_lyrics")),
        "cover": album.get("cover_small") or album.get("cover_medium") or "",
        "link": item.get("link") or f"https://www.deezer.com/track/{item.get('id')}",
    }


def _deezer_artist(item: dict) -> dict:
    return {
        "kind": "artist",
        "name": item.get("name") or "",
        "albums": item.get("nb_album") or 0,
        "fans": item.get("nb_fan") or 0,
        "picture": item.get("picture_medium") or item.get("picture_big") or "",
        "link": item.get("link") or f"https://www.deezer.com/artist/{item.get('id')}",
    }


def _youtube_search(query: str) -> dict:
    client = ytmusic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        albums = pool.submit(client.search, query, filter="albums", limit=LIMITS["album"])
        songs = pool.submit(client.search, query, filter="songs", limit=LIMITS["track"])
        artists = pool.submit(client.search, query, filter="artists", limit=LIMITS["artist"])
        albums, songs, artists = albums.result(), songs.result(), artists.result()
    return {
        "service": "YouTube Music",
        "albums": [{
            "kind": "album",
            "title": item.get("title") or "",
            "artist": _names(item.get("artists")),
            "type": _KINDS.get(str(item.get("type") or "").lower(), "album"),
            "year": str(item.get("year") or ""),
            "tracks": 0,
            "explicit": bool(item.get("isExplicit")),
            "cover": _picture(item.get("thumbnails")),
            "link": f"https://music.youtube.com/browse/{item['browseId']}",
        } for item in albums if item.get("browseId")],
        "tracks": [{
            "kind": "track",
            "title": item.get("title") or "",
            "artist": _names(item.get("artists")),
            "album": (item.get("album") or {}).get("name") or "",
            "duration": item.get("duration_seconds") or 0,
            "explicit": bool(item.get("isExplicit")),
            "cover": _picture(item.get("thumbnails"), 120),
            "link": f"https://music.youtube.com/watch?v={item['videoId']}",
        } for item in songs if item.get("videoId")],
        "artists": [{
            "kind": "artist",
            "name": item.get("artist") or "",
            "albums": 0,
            "fans": 0,
            "picture": _picture(item.get("thumbnails")),
            "link": f"https://music.youtube.com/channel/{item['browseId']}",
        } for item in artists if item.get("browseId")],
    }


def _names(artists) -> str:
    return ", ".join(artist.get("name", "") for artist in artists or [] if isinstance(artist, dict))


def _picture(thumbnails, size: int = 400) -> str:
    """The largest thumbnail, asked for at the size the page shows: Google's
    picture addresses carry their size and serve any other on request."""
    if not thumbnails:
        return ""
    url = max(thumbnails, key=lambda thumb: thumb.get("width") or 0).get("url") or ""
    return re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", url)
