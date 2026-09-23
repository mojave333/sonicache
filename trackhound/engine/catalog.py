"""Looking music up in the catalogues to choose what to download: albums,
tracks and artists with their pictures, for the window's Search.

Deezer answers first: one quick request for each kind, pictures included,
no account needed. Where it does not answer, YouTube Music is asked instead.
Every result carries a link that sources.resolve() opens, so a chosen result
downloads exactly the way the same link pasted by hand would.

An artist's page lists every release of theirs, newest first. Deezer and
YouTube Music list them openly; Spotify and Apple Music do not, so an artist
link from them is followed to the same artist on Deezer by name. The artist
link is also what a discography download and a watch for new releases start from.
"""

from __future__ import annotations

import difflib
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import spotify
from .logs import log
from .matcher import _norm, ytmusic
from .models import SourceError
from .net import BROWSER_UA, fetch_json

LIMITS = {"album": 24, "track": 20, "artist": 8}
_KINDS = {"album": "album", "single": "single", "ep": "ep", "compile": "compilation", "compilation": "compilation"}
_log = log.getChild("search")
# The kinds of release a discography download takes: singles are mostly songs
# the albums have as well, and compilations are someone else's selection
DISCOGRAPHY = ("album", "ep")
ARTIST_LINK_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"deezer\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?artist/(?P<deezer>\d+)"
    r"|music\.youtube\.com/channel/(?P<youtube>UC[\w-]+)"
    r"|open\.spotify\.com/(?:intl-[\w-]+/)?artist/(?P<spotify>[A-Za-z0-9]{22})"
    r"|music\.apple\.com/[a-z]{2}/artist/(?:[^/?#]+/)?(?P<apple>\d+)"
    r")", re.I)


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


# What an album's title says of its edition, not of the album: "(Remastered)",
# "[Deluxe Edition]", "- 2011 Remaster", "(Compilation)"
_EDITION_RE = re.compile(
    r"\s*[(\[][^)\]]*\b(?:remaster\w*|deluxe|edition|expanded|anniversary|bonus|compilation|reissue|mono|stereo|version)\b[^)\]]*[)\]]"
    r"|\s[-–—]\s[^-–—]*\b(?:remaster\w*|deluxe|edition)\b[^-–—]*$", re.I)


def album_cover(artist: str, album: str) -> str:
    """The address of an album's cover, found by its artist and title: on
    Deezer, else in Apple's catalogue; empty when neither has the album.

    The title must be the album's own, its edition aside, and the artist the
    same, a slip of the pen aside ("Madvillian"): a cover of another album
    would be worse than the program's logo. Deezer's search by fields is
    asked first, then its plain search, which finds what the fields miss.
    """
    first = re.split(r",|;|&|\s+(?:feat|ft)\.?\s", artist or "")[0].strip()
    bare = _EDITION_RE.sub("", album or "").strip() or (album or "").strip()
    title = _plain(album)
    if not title:
        return ""
    queries = [f'artist:"{first}" album:"{album}"' if first else "", f"{first} {bare}".strip(), bare]
    for query in dict.fromkeys(filter(None, queries)):
        try:
            items = _deezer("search/album", q=query, limit=10)
        except (OSError, ValueError):
            continue
        for item in items:
            if _plain(item.get("title")) == title and _same_artist(first, (item.get("artist") or {}).get("name")):
                return item.get("cover_big") or item.get("cover_medium") or ""
    return _apple_cover(first, bare, title)


def track_cover(artist: str, title: str) -> str:
    """The cover of a release on Deezer that has this track by this artist:
    for an EP or a demo no catalogue has, the album its songs came out on."""
    first = re.split(r",|;|&|\s+(?:feat|ft)\.?\s", artist or "")[0].strip()
    wanted = _plain(title)
    if not wanted or not first:
        return ""
    try:
        items = _deezer("search/track", q=f"{first} {_EDITION_RE.sub('', title).strip() or title}", limit=10)
    except (OSError, ValueError):
        return ""
    for item in items:
        album = item.get("album") or {}
        if _plain(item.get("title")) == wanted and _same_artist(first, (item.get("artist") or {}).get("name")):
            return album.get("cover_big") or album.get("cover_medium") or ""
    return ""


def _apple_cover(artist: str, bare: str, title: str) -> str:
    query = urllib.parse.urlencode({"term": f"{artist} {bare}".strip(), "entity": "album", "limit": 10})
    try:
        results = fetch_json(f"https://itunes.apple.com/search?{query}", service="Apple Music").get("results") or []
    except (OSError, ValueError, SourceError):
        return ""
    for result in results:
        if _plain(result.get("collectionName")) == title and _same_artist(artist, result.get("artistName")):
            return re.sub(r"/\d+x\d+bb(\.\w+)$", r"/600x600bb\1", result.get("artworkUrl100") or "")
    return ""


def _plain(text) -> str:
    """A title as compared: its edition, case and punctuation aside."""
    text = _EDITION_RE.sub("", str(text or ""))
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def _same_artist(wanted: str, found) -> bool:
    wanted, found = _plain(wanted), _plain(found)
    if not wanted:
        return True
    return wanted == found or (bool(found) and (wanted in found or found in wanted)) \
        or difflib.SequenceMatcher(None, wanted, found).ratio() >= 0.8


def _deezer(path: str, **params) -> list[dict]:
    """The items of one answer."""
    return [item for item in _deezer_object(path, **params).get("data") or [] if isinstance(item, dict)]


def _deezer_object(path: str, **params) -> dict:
    """One request with a short wait: a search is typed by a person waiting for it."""
    url = path if path.startswith("https://") else f"https://api.deezer.com/{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read())
    if not isinstance(data, dict):
        raise ValueError(f"not an object: {url}")
    if data.get("error"):
        raise ValueError(f"Deezer: {data['error']}")
    return data


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


# Artists

def is_artist_link(link: str) -> bool:
    return bool(ARTIST_LINK_RE.match(str(link).strip()))


def artist(link: str) -> dict:
    """An artist's name, picture and releases, newest first, from an artist link."""
    match = ARTIST_LINK_RE.match(str(link).strip())
    if not match:
        raise SourceError.of("unsupported_link", "Не похоже на ссылку на исполнителя: {link}", link=link)
    try:
        if match["deezer"]:
            return _deezer_artist_page(match["deezer"])
        if match["youtube"]:
            return _youtube_artist_page(match["youtube"])
        name = _spotify_name(match["spotify"]) if match["spotify"] else _apple_name(match["apple"])
        return {**_deezer_artist_page(_deezer_artist_id(name)), "link": str(link).strip()}
    except SourceError:
        raise
    except Exception as e:  # network errors, and whatever ytmusicapi meets in a changed page
        raise SourceError.of("offline", "Страница исполнителя не открылась: {error}", error=e) from e


def discography(link: str) -> list[dict]:
    """The artist's albums and EPs, oldest first: the order they are downloaded in."""
    releases = [release for release in artist(link)["releases"] if release["type"] in DISCOGRAPHY]
    return releases[::-1]


def _deezer_artist_page(artist_id: str) -> dict:
    info = _deezer_object(f"artist/{artist_id}")
    items, url = [], f"https://api.deezer.com/artist/{artist_id}/albums?limit=100"
    for _ in range(10):  # a hundred a page; nobody has a thousand releases
        page = _deezer_object(url)
        items += [item for item in page.get("data") or [] if isinstance(item, dict)]
        url = page.get("next") or ""
        if not url.startswith("https://api.deezer.com/"):
            break
    name = info.get("name") or ""
    releases = [{**_deezer_album({**item, "artist": {"name": name}}), "date": item.get("release_date") or ""}
                for item in items]
    return {
        "name": name,
        "picture": info.get("picture_big") or info.get("picture_medium") or "",
        "albums": info.get("nb_album") or len(releases),
        "fans": info.get("nb_fan") or 0,
        "service": "Deezer",
        "link": info.get("link") or f"https://www.deezer.com/artist/{artist_id}",
        "releases": _newest_first(releases),
    }


def _youtube_artist_page(channel_id: str) -> dict:
    client = ytmusic()
    data = client.get_artist(channel_id)
    name = data.get("name") or ""
    releases = []
    for section, kind in (("albums", "album"), ("singles", "single")):
        block = data.get(section) or {}
        items = block.get("results") or []
        if block.get("browseId") and block.get("params"):
            try:  # the page shows ten; the rest are one more request away
                items = client.get_artist_albums(block["browseId"], block["params"], limit=None) or items
            except Exception as e:
                _log.warning("YouTube Music не отдал все релизы %s: %s", name, e)
        releases += [{
            "kind": "album",
            "title": item.get("title") or "",
            "artist": name,
            "type": _KINDS.get(str(item.get("type") or kind).lower(), kind),
            "year": str(item.get("year") or ""),
            "date": str(item.get("year") or ""),
            "tracks": 0,
            "explicit": bool(item.get("isExplicit")),
            "cover": _picture(item.get("thumbnails")),
            "link": f"https://music.youtube.com/browse/{item['browseId']}",
        } for item in items if item.get("browseId")]
    return {
        "name": name,
        "picture": _picture(data.get("thumbnails"), 500),
        "albums": len(releases),
        "fans": 0,
        "service": "YouTube Music",
        "link": f"https://music.youtube.com/channel/{channel_id}",
        "releases": _newest_first(releases),
    }


def _newest_first(releases: list[dict]) -> list[dict]:
    unique = {release["link"]: release for release in releases}  # an album can sit in two sections
    return sorted(unique.values(), key=lambda release: release["date"], reverse=True)


def _deezer_artist_id(name: str) -> str:
    """The artist of that name on Deezer: the same name, the most listened to."""
    found = [item for item in _deezer("search/artist", q=name, limit=10)
             if _norm(item.get("name") or "") == _norm(name)]
    if not found:
        raise SourceError.of("not_found", "Исполнитель «{name}» не нашёлся на Deezer", name=name)
    return str(max(found, key=lambda item: item.get("nb_fan") or 0)["id"])


def _spotify_name(artist_id: str) -> str:
    title = dict(spotify._meta_tags("artist", artist_id)).get("og:title", "")
    if not title:
        raise SourceError.of("unavailable", "Spotify не показал исполнителя {id}: вставьте ссылку на него "
                             "из Deezer или YouTube Music", id=artist_id)
    return title


def _apple_name(artist_id: str) -> str:
    data = fetch_json(f"https://itunes.apple.com/lookup?id={artist_id}", service="Apple Music")
    results = data.get("results") or []
    name = (results[0].get("artistName") or "") if results else ""
    if not name:
        raise SourceError.of("not_found", "Apple Music не нашёл исполнителя {id}", id=artist_id)
    return name
