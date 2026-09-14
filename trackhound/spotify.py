"""Spotify metadata from public web pages, no API keys required.

Two public sources are combined:
  * the embed player page (open.spotify.com/embed/...) carries a JSON blob
    with the track list: titles, artists, durations;
  * the regular page, requested with a link-preview User-Agent, carries
    Open Graph / music:* meta tags: release date, disc and track numbers,
    the album a track belongs to, a 640px cover.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request

from .models import Album, SourceError, Track
from .net import BROWSER_UA, fetch_text

# Spotify renders meta tags only for link-preview bots, not for browsers.
PREVIEW_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"

_LINK_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[\w-]+/)?(?:embed/)?(album|track)/"
    r"|spotify:(album|track):)([A-Za-z0-9]{22})"
)
_SHORT_LINK_RE = re.compile(r"https?://(?:spotify\.link|spoti\.fi)/\S+")
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
_META_RE = re.compile(r"<meta\s[^>]*>", re.I)
_ATTR_RE = re.compile(r'([\w:-]+)="([^"]*)"')


def parse_link(link: str) -> tuple[str, str]:
    """Return ("album" | "track", id) for a Spotify link or URI."""
    link = link.strip()
    if _SHORT_LINK_RE.match(link):
        link = _resolve_short_link(link)
    m = _LINK_RE.search(link)
    if not m:
        if re.search(r"spotify\.com/(?:intl-[\w-]+/)?playlist/", link):
            raise SourceError("Плейлисты Spotify пока не поддерживаются: откройте альбом или трек")
        raise SourceError(f"Не похоже на ссылку на альбом или трек Spotify: {link}")
    return m.group(1) or m.group(2), m.group(3)


def fetch_album(album_id: str) -> Album:
    entity = _embed_entity("album", album_id)
    meta = _meta_tags("album", album_id)

    # music:song is followed by its own music:song:disc / music:song:track tags.
    positions: dict[str, dict[str, int]] = {}
    song = None
    for key, value in meta:
        if key == "music:song":
            song = positions.setdefault(_id_from_url(value), {})
        elif song is not None and key.startswith("music:song:") and value.isdigit():
            song[key.rsplit(":", 1)[1]] = int(value)

    tracks = []
    disc, number = 1, 0
    for item in entity.get("trackList") or []:
        track_id = item["uri"].rsplit(":", 1)[-1]
        pos = positions.get(track_id, {})
        disc = pos.get("disc", disc)
        number = pos.get("track", number + 1)
        tracks.append(Track(
            id=track_id,
            title=item["title"],
            artists=item.get("subtitle") or entity.get("subtitle", ""),
            duration=(item.get("duration") or 0) / 1000,
            track_number=number,
            disc_number=disc,
            explicit=bool(item.get("isExplicit")),
        ))
    if not tracks:
        raise SourceError(f"В альбоме {album_id} не найдено треков")

    # og:description looks like "Daft Punk · album · 2001 · 14 songs"
    description = _first(meta, "og:description").split(" · ")
    release_date = _first(meta, "music:release_date")
    if not release_date and len(description) > 2 and description[2].isdigit():
        release_date = description[2]

    return Album(
        id=album_id,
        name=entity.get("name") or entity.get("title", ""),
        artist=entity.get("subtitle", ""),
        release_date=release_date,
        kind=description[1].lower() if len(description) > 1 else "album",
        cover_url=_first(meta, "og:image") or _embed_cover(entity),
        tracks=tracks,
        service="Spotify",
    )


def fetch_track(track_id: str) -> tuple[Album, Track]:
    """A single track plus the album it belongs to (for tags, cover, numbering)."""
    meta = _meta_tags("track", track_id)
    album_id = _id_from_url(_first(meta, "music:album"))
    album = None
    if album_id:
        try:
            album = fetch_album(album_id)
        except SourceError:
            pass

    entity = _embed_entity("track", track_id)
    title = entity.get("name") or entity.get("title", "")
    duration = (entity.get("duration") or 0) / 1000

    if album:
        for track in album.tracks:
            same_song = (track.title.casefold() == title.casefold()
                         and abs(track.duration - duration) < 2)
            if track.id == track_id or same_song:
                return album, track

    # og:description looks like "Rick Astley · Whenever You Need Somebody · Song · 1987"
    description = _first(meta, "og:description").split(" · ")
    artists = ", ".join(a["name"] for a in entity.get("artists") or []) or description[0]
    number = _first(meta, "music:album:track")
    track = Track(
        id=track_id,
        title=title,
        artists=artists,
        duration=duration,
        track_number=int(number) if number.isdigit() else 1,
        explicit=bool(entity.get("isExplicit")),
    )
    if album is None:
        album = Album(
            id=album_id or track_id,
            name=description[1] if len(description) > 2 else title,
            artist=artists,
            release_date=(_first(meta, "music:release_date")
                          or ((entity.get("releaseDate") or {}).get("isoString") or "")[:10]),
            kind="single",
            cover_url=_first(meta, "og:image") or _embed_cover(entity),
            tracks=[track],
            service="Spotify",
        )
    return album, track


def _embed_entity(kind: str, spotify_id: str) -> dict:
    page = fetch_text(f"https://open.spotify.com/embed/{kind}/{spotify_id}", service="Spotify")
    m = _NEXT_DATA_RE.search(page)
    try:
        return json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        raise SourceError(
            f"Не удалось получить данные Spotify для {kind}/{spotify_id}: "
            "ссылка неверна или Spotify изменил формат страницы"
        ) from e


def _meta_tags(kind: str, spotify_id: str) -> list[tuple[str, str]]:
    """Meta tags in document order; empty when the page is unavailable."""
    try:
        page = fetch_text(f"https://open.spotify.com/{kind}/{spotify_id}", service="Spotify",
                          user_agent=PREVIEW_UA)
    except SourceError:
        return []
    tags = []
    for tag in _META_RE.findall(page):
        attrs = dict(_ATTR_RE.findall(tag))
        key = attrs.get("property") or attrs.get("name")
        if key and "content" in attrs:
            tags.append((key, html.unescape(attrs["content"])))
    return tags


def _embed_cover(entity: dict) -> str:
    images = (entity.get("visualIdentity") or {}).get("image") or []
    return max(images, key=lambda i: i.get("maxWidth") or 0, default={}).get("url", "")


def _first(meta: list[tuple[str, str]], key: str) -> str:
    return next((value for k, value in meta if k == key), "")


def _id_from_url(url: str) -> str:
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] if url else ""


def _resolve_short_link(link: str) -> str:
    """spotify.link / spoti.fi redirect to open.spotify.com (sometimes via HTML)."""
    request = urllib.request.Request(link, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            if _LINK_RE.search(resp.geturl()):
                return resp.geturl()
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        raise SourceError(f"Не удалось открыть короткую ссылку {link}: {e}") from e
    m = _LINK_RE.search(body)
    return m.group(0) if m else link
