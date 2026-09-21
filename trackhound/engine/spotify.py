"""Spotify metadata from public web pages, no API keys required.

Two public sources are combined:
  * the embed player page (open.spotify.com/embed/...) carries a JSON blob
    with the track list: titles, artists, durations;
  * the regular page, requested with a link-preview User-Agent, carries
    Open Graph / music:* meta tags: release date, disc and track numbers,
    the album a track belongs to, a 640px cover.
"""

from __future__ import annotations

import gzip
import html
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .i18n import t
from .logs import log
from .models import Album, SourceError, Track
from .net import BROWSER_UA, fetch_text

# Spotify renders meta tags only for link-preview bots, not for browsers.
PREVIEW_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"

_LINK_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[\w-]+/)?(?:embed/)?(album|track|playlist)/"
    r"|spotify:(album|track|playlist):)([A-Za-z0-9]{22})"
)
# The embed page hands over this many playlist tracks and no more
PLAYLIST_LIMIT = 100
_SHORT_LINK_RE = re.compile(r"https?://(?:spotify\.link|spoti\.fi)/\S+")
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
_META_RE = re.compile(r"<meta\s[^>]*>", re.I)
# The preview page's title of an album: "Discovery - Album by Daft Punk | Spotify"
_ALBUM_TITLE_RE = re.compile(r"^(?P<name>.+) - (?:Album|Single|EP|Compilation) by .+ \| Spotify$")
# The preview pages of a release's tracks, read this many at a time
_PREVIEW_WORKERS = 8
_ATTR_RE = re.compile(r'([\w:-]+)="([^"]*)"')


def parse_link(link: str) -> tuple[str, str]:
    """Return ("album" | "track" | "playlist", id) for a Spotify link or URI."""
    link = link.strip()
    if _SHORT_LINK_RE.match(link):
        link = _resolve_short_link(link)
    m = _LINK_RE.search(link)
    if not m:
        raise SourceError.of("unsupported_link",
                             "Не похоже на ссылку на альбом, трек или плейлист Spotify: {link}",
                             link=link)
    return m.group(1) or m.group(2), m.group(3)


def fetch_album(album_id: str, whole: bool = True) -> Album:
    """An album with its tracks. Without `whole`, an album read off its
    preview page has only the places of its tracks, not their names: enough
    for one track of it, which fills in its own."""
    entity, meta = _release("album", album_id, whole)

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
        raise SourceError.of("empty", "В альбоме {id} не найдено треков", id=album_id)

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
        note=_short_note(entity, len(tracks)),
    )


def fetch_playlist(playlist_id: str) -> Album:
    """A playlist as a release: tracks in playlist order, artists per track.

    The embed page stops at PLAYLIST_LIMIT tracks and nothing else on the
    public pages lists the rest, so a longer playlist comes back cut short and
    says so in its note.
    """
    entity, meta = _release("playlist", playlist_id)

    tracks = []
    for number, item in enumerate(entity.get("trackList") or [], 1):
        uri = item.get("uri") or ""
        tracks.append(Track(
            id=uri.rsplit(":", 1)[-1] or str(number),
            title=item.get("title", ""),
            artists=_artists(item.get("subtitle")),
            duration=(item.get("duration") or 0) / 1000,
            track_number=number,
            explicit=bool(item.get("isExplicit")),
        ))
    if not tracks:
        raise SourceError.of("empty", "В плейлисте {id} не найдено треков или он закрыт",
                             id=playlist_id)

    note = _short_note(entity, len(tracks))
    if not note and len(tracks) >= PLAYLIST_LIMIT:
        note = t("Spotify отдаёт по ссылке первые {limit} треков плейлиста. Если их больше, "
                 "остальные придётся добавить отдельно", limit=PLAYLIST_LIMIT)
    return Album(
        id=playlist_id,
        name=entity.get("name") or entity.get("title", ""),
        artist=_artists(entity.get("subtitle")) or t("Разные исполнители"),
        kind="playlist",
        cover_url=_first(meta, "og:image") or _embed_cover(entity),
        tracks=tracks,
        service="Spotify",
        note=note,
    )


def fetch_track(track_id: str) -> tuple[Album, Track]:
    """A single track plus the album it belongs to (for tags, cover, numbering)."""
    entity, meta = _release("track", track_id)
    album_id = _id_from_url(_first(meta, "music:album"))
    album = None
    if album_id:
        try:
            album = fetch_album(album_id, whole=False)
        except SourceError:
            pass

    title = entity.get("name") or entity.get("title", "")
    duration = (entity.get("duration") or 0) / 1000
    # og:description looks like "Rick Astley · Whenever You Need Somebody · Song · 1987"
    description = _first(meta, "og:description").split(" · ")
    artists = ", ".join(a["name"] for a in entity.get("artists") or []) or description[0]

    if album:
        for track in album.tracks:
            same_song = (track.title.casefold() == title.casefold()
                         and abs(track.duration - duration) < 2)
            if track.id == track_id or same_song:
                if not track.title:  # its place in an album read off the preview page
                    track.title, track.artists, track.duration = title, artists, duration
                return album, track

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


def use_relay(url: str) -> None:
    """Sets the relay Spotify's pages are read through when Spotify refuses
    them to this network; empty goes without one. See relay/worker.js."""
    global _relay
    _relay = url.strip()


_relay = ""


def _release(kind: str, spotify_id: str, whole: bool = True) -> tuple[dict, list[tuple[str, str]]]:
    """The release as the embed page has it, and the preview page's meta tags.

    Spotify keeps its pages from the countries it does not work in, Russia
    among them, going by where the request comes from. So a refusal is taken
    to the relay, which reads the same pages from abroad; without one, or when
    it fails too, what the preview pages give is used, if anything: the
    release's name and each track on a page of its own. The audio never comes
    from Spotify anyway. Without `whole`, the tracks' own pages are not read:
    the album's list of them is all a single track needs.
    """
    meta = _meta_tags(kind, spotify_id)
    try:
        return _embed_entity(kind, spotify_id), meta
    except SourceError:
        relayed = _relayed(kind, spotify_id)
        if relayed:
            return relayed
        entity = _preview_entity(kind, meta, whole)
        if not entity:
            raise
        log.info("Spotify: %s/%s собран со страниц-превью, треков: %d",
                 kind, spotify_id, len(entity.get("trackList") or [entity]))
        return entity, meta


def _relayed(kind: str, spotify_id: str) -> tuple[dict, list[tuple[str, str]]] | None:
    """The release as the relay reads it abroad; None without a relay or an answer."""
    if not _relay:
        return None
    joiner = "&" if "?" in _relay else "?"
    request = urllib.request.Request(f"{_relay}{joiner}kind={kind}&id={spotify_id}",
                                     headers={"User-Agent": BROWSER_UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
        data = json.loads(body)
    except (OSError, ValueError) as e:
        log.warning("Spotify: зеркало %s не ответило на %s/%s: %s", _relay, kind, spotify_id, e)
        return None
    entity = data.get("entity") if isinstance(data, dict) else None
    if not entity:
        log.warning("Spotify: %s/%s не отдан и зеркалу: %s", kind, spotify_id,
                    data.get("refused") if isinstance(data, dict) else data)
        return None
    meta = [(pair[0], pair[1]) for pair in data.get("meta") or [] if isinstance(pair, list) and len(pair) == 2]
    log.info("Spotify: %s/%s взят через зеркало", kind, spotify_id)
    return entity, meta


def _preview_entity(kind: str, meta: list[tuple[str, str]], whole: bool = True) -> dict:
    """The embed page's data rebuilt from preview pages; empty if they have none."""
    if kind == "track":
        title = _first(meta, "og:title")
        duration = _first(meta, "music:duration")
        names = _first(meta, "music:musician_description")
        return {"name": title, "duration": int(duration) * 1000 if duration.isdigit() else 0,
                "artists": [{"name": name} for name in names.split(", ") if name]} if title else {}
    songs = [_id_from_url(value) for key, value in meta if key == "music:song"]
    if whole:
        with ThreadPoolExecutor(max_workers=_PREVIEW_WORKERS) as pool:
            pages = list(pool.map(lambda song: _meta_tags("track", song), songs))
        track_list = []
        for song, page in zip(songs, pages):
            track = _preview_entity("track", page)
            if track:  # a track whose page did not come is left out, and the note counts it
                track_list.append({"uri": f"spotify:track:{song}", "title": track["name"],
                                   "subtitle": ", ".join(artist["name"] for artist in track["artists"]),
                                   "duration": track["duration"]})
    else:
        # Places in the list with nothing in them yet: a single track fills in its own
        track_list = [{"uri": f"spotify:track:{song}", "title": "", "duration": 0} for song in songs]
    if not track_list:
        return {}
    # "Daft Punk · album · 2001 · 14 songs", "Playlist · 808filth · 175 items · 49.4K saves"
    description = _first(meta, "og:description").split(" · ")
    title = _first(meta, "og:title")
    if kind == "album":
        named = _ALBUM_TITLE_RE.match(title)
        name, subtitle = named["name"] if named else title.removesuffix(" | Spotify"), description[0]
        total = description[3] if len(description) > 3 else ""
    else:
        name, subtitle = title.removesuffix(" | Spotify"), description[1] if len(description) > 1 else ""
        total = _first(meta, "music:song_count")
    count = re.match(r"\d+", total.replace(",", ""))
    return {"name": name, "subtitle": subtitle, "trackList": track_list,
            "previewTotal": int(count.group()) if count else 0}


def _short_note(entity: dict, count: int) -> str:
    """Said of a release rebuilt from preview pages that list fewer tracks than it has."""
    total = entity.get("previewTotal") or 0
    if total <= count:
        return ""
    return t("Spotify не показывает этот релиз там, откуда идёт запрос программы, и на страницах-превью "
             "нашлись только {count} из {total} треков. С прокси в настройках скачается всё",
             count=count, total=total)


def _embed_entity(kind: str, spotify_id: str) -> dict:
    url = f"https://open.spotify.com/embed/{kind}/{spotify_id}"
    page = fetch_text(url, service="Spotify")
    m = _NEXT_DATA_RE.search(page)
    try:
        props = json.loads(m.group(1))["props"]["pageProps"]
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        title = re.search(r"<title[^>]*>(.*?)</title>", page, re.S)
        log.warning("Spotify: в %s нет данных страницы (%d байт, заголовок %r)",
                    url, len(page), title.group(1).strip() if title else "")
        raise SourceError.of("unreadable",
                             "Не удалось получить данные Spotify для {kind}/{id}: ссылка неверна или "
                             "Spotify изменил формат страницы", kind=kind, id=spotify_id) from e
    if not isinstance(props, dict):
        props = {}
    entity = ((props.get("state") or {}).get("data") or {}).get("entity")
    if entity:
        return entity
    # The page came and the release did not. In its place Spotify puts its own
    # error page, sent with HTTP 200: a status, a title, a description. It does
    # so for a wrong id, and for a real release it will not show to the place
    # the request comes from, while a browser behind a VPN still sees the album.
    log.warning("Spotify: в %s нет релиза; вместо него: %s", url, json.dumps(props, ensure_ascii=False)[:800])
    status = props.get("status")
    code = "not_found" if status == 404 else "unavailable"
    advice = t("Если ссылка открывается в браузере, Spotify не показывает этот релиз там, откуда идёт "
               "запрос программы: включите прокси в настройках или вставьте ссылку на этот релиз из "
               "Deezer, Apple Music или YouTube Music")
    if props.get("title"):
        raise SourceError.of(code, "Spotify не отдал {kind}/{id} и ответил «{reason}». {advice}",
                             kind=kind, id=spotify_id, reason=props["title"], status=status, advice=advice)
    raise SourceError.of(code, "Spotify не отдал {kind}/{id}. {advice}",
                         kind=kind, id=spotify_id, status=status, advice=advice)


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


def _artists(subtitle: str | None) -> str:
    """Spotify joins names with a comma and a non-breaking space."""
    parts = [part.strip() for part in (subtitle or "").replace(" ", " ").split(",")]
    return ", ".join(part for part in parts if part)


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
        raise SourceError.of("short_link_broken", "Не удалось открыть короткую ссылку {link}: {error}",
                             link=link, error=e) from e
    m = _LINK_RE.search(body)
    return m.group(0) if m else link
