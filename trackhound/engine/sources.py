"""Turning a link from a music service into a release to download.

Every source returns tracks with metadata. Services that stream audio openly
(YouTube, SoundCloud and other sites yt-dlp understands, e.g. Bandcamp) also
give each track its own audio URL. For the rest (Spotify, Apple Music,
Deezer, Last.fm) the audio is matched on YouTube Music and SoundCloud later.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import yt_dlp

from . import spotify
from .i18n import language, t
from .logs import YtdlpLogger
from .matcher import _NOISE_RE, _VERSION_WORDS, _artist_score, _norm, _similarity, ytmusic
from .models import Album, Release, SourceError, Track
from .net import BROWSER_UA, fetch_json, fetch_text

SUPPORTED = "Spotify, Apple Music, Deezer, YouTube, SoundCloud, Last.fm и сайтов вроде Bandcamp"
_SUPPORTED_EN = "Spotify, Apple Music, Deezer, YouTube, SoundCloud, Last.fm and sites such as Bandcamp"

_APPLE_PATH_RE = re.compile(r"^/(?:([a-z]{2})/)?([\w-]+)/(?:[^/]+/)?(?:id)?(\d+)", re.I)
_APPLE_DATA_RE = re.compile(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', re.S)
_DEEZER_PATH_RE = re.compile(r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(album|track|playlist)/(\d+)", re.I)
_DEEZER_LINK_RE = re.compile(r"https://www\.deezer\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:album|track|playlist)/\d+",
                             re.I)
_LASTFM_PATH_RE = re.compile(r"/music/([^/?#]+)(?:/([^/?#]+))?(?:/([^/?#]+))?")
_LASTFM_ROW_RE = re.compile(r'<tr\s+class="\s*chartlist-row.*?</tr>', re.S)
# Music videos and user uploads, as opposed to official audio tracks
_VIDEO_TYPES = {"MUSIC_VIDEO_TYPE_UGC", "MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_PODCAST_EPISODE"}
_YTDLP_OPTS = {"quiet": True, "no_warnings": True, "skip_download": True, "logger": YtdlpLogger("sources")}
_SERVICE_NAMES = {"soundcloud": "SoundCloud", "bandcamp": "Bandcamp", "mixcloud": "Mixcloud"}
# MusicBrainz asks every program to name itself and to send one request a second
_MUSICBRAINZ_UA = "Trackhound ( https://github.com/mojave333/trackhound )"
_MUSICBRAINZ_LOCK = threading.Lock()
_musicbrainz_last = 0.0
GENRE_LIMIT = 3  # the most voted genres written into the tags
_GENRE_SMALL_WORDS = {"and", "of", "the", "n", "in", "de"}
_GENRE_CAPITALS = {"r&b", "idm", "edm", "ebm", "uk", "us", "dj", "mpb", "aor", "nwobhm", "ccm", "dnb"}


def resolve(link: str) -> Release:
    link = link.strip()
    if link.startswith("spotify:"):
        return _spotify(link)
    if link[:6].lower() == "track:":
        return search_track(link[6:])
    url = link if re.match(r"^https?://", link, re.I) else f"https://{link}"
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if "." not in host or " " in link:
        return search(link)  # a name rather than a link

    def on(*domains: str) -> bool:
        return any(host == domain or host.endswith(f".{domain}") for domain in domains)

    if on("spotify.com", "spotify.link", "spoti.fi"):
        return _spotify(url)  # short links are only recognised with https://
    if on("music.apple.com", "itunes.apple.com"):
        return _apple(parts)
    if on("deezer.com", "deezer.page.link", "dzr.page.link"):
        return _deezer(url, parts)
    if on("youtube.com", "youtu.be"):
        return _youtube(parts)
    if on("last.fm", "lastfm.ru"):
        return _lastfm(url, parts)
    if on("vk.com", "vk.ru", "vkontakte.ru"):
        raise SourceError.of("login_required",
                             "VK показывает музыку только после входа в аккаунт, поэтому альбом по этой ссылке "
                             "не прочитать. Найдите этот релиз в Spotify, Apple Music, Deezer, YouTube или "
                             "SoundCloud и вставьте ссылку оттуда.")
    return _ytdlp(url)


# Spotify

def _spotify(link: str) -> Release:
    kind, spotify_id = spotify.parse_link(link)
    if kind == "album":
        album = spotify.fetch_album(spotify_id)
        return Release(album, album.tracks)
    if kind == "playlist":
        album = spotify.fetch_playlist(spotify_id)
        return Release(album, album.tracks)
    album, track = spotify.fetch_track(spotify_id)
    return Release(album, [track], single=True)


# Apple Music: the public iTunes lookup API has the whole tracklist

def _apple(parts: urllib.parse.SplitResult) -> Release:
    if re.match(r"^/(?:[a-z]{2}/)?playlist/", parts.path, re.I):
        return _apple_playlist(urllib.parse.urlunsplit(parts))
    m = _APPLE_PATH_RE.match(parts.path)
    if not m or m.group(2) not in ("album", "song"):
        raise SourceError.of("unsupported_link",
                             "Из Apple Music поддерживаются ссылки на альбомы, песни и плейлисты")
    country = (m.group(1) or "us").lower()
    song_id = m.group(3) if m.group(2) == "song" else urllib.parse.parse_qs(parts.query).get("i", [""])[0]
    if not song_id:
        return _apple_album(m.group(3), country)

    songs = _itunes("lookup", id=song_id, country=country)
    if not songs:
        raise SourceError.of("not_found",
                             "Apple Music не нашёл песню {id}: возможно, её нет в регионе {country}",
                             id=song_id, country=country.upper())
    release = _apple_album(songs[0]["collectionId"], country)
    track = next((t for t in release.album.tracks if t.id == song_id), None)
    if track is None:
        raise SourceError.of("not_in_album", "Песни {id} нет в альбоме «{album}»",
                             id=song_id, album=release.album.name)
    return Release(release.album, [track], single=True)


def _apple_album(collection_id: int | str, country: str = "us") -> Release:
    results = _itunes("lookup", id=collection_id, entity="song", country=country, limit=200)
    collection = next((r for r in results if r.get("wrapperType") == "collection"), None)
    if collection is None:
        raise SourceError.of("not_found",
                             "Apple Music не нашёл альбом {id}: возможно, его нет в регионе {country}",
                             id=collection_id, country=country.upper())
    songs = [r for r in results if r.get("wrapperType") == "track" and r.get("kind") == "song"]
    tracks = [Track(
        id=str(song["trackId"]),
        title=song.get("trackName", ""),
        artists=song.get("artistName", ""),
        duration=(song.get("trackTimeMillis") or 0) / 1000,
        track_number=song.get("trackNumber") or number,
        disc_number=song.get("discNumber") or 1,
        explicit=song.get("trackExplicitness") == "explicit",
    ) for number, song in enumerate(songs, 1)]
    if not tracks:
        raise SourceError.of("empty", "В альбоме Apple Music {id} нет доступных песен", id=collection_id)

    name, kind = _split_kind(collection.get("collectionName", ""))
    album = Album(
        id=str(collection_id),
        name=name,
        artist=collection.get("artistName", ""),
        release_date=(collection.get("releaseDate") or "")[:10],
        kind="single" if kind == "album" and len(tracks) == 1 else kind,
        # artwork URLs end with the size, any size can be requested
        cover_url=re.sub(r"/\d+x\d+bb(\.\w+)$", r"/1000x1000bb\1", collection.get("artworkUrl100") or ""),
        tracks=tracks,
        service="Apple Music",
        genre=collection.get("primaryGenreName") or "",
    )
    return Release(album, tracks)


def _apple_playlist(url: str) -> Release:
    """Apple has no public API for playlists, so the page's own data is read.

    The page hands over its first tracks only; the header says how many the
    playlist really has, and the release notes the difference.
    """
    sections = _apple_sections(fetch_text(url, service="Apple Music"))
    items = (sections.get("trackLockup") or {}).get("items") or []
    header = ((sections.get("containerDetailHeaderLockup") or {}).get("items") or [{}])[0]
    tracks = [Track(
        id=str(item.get("id") or number).rsplit(" - ", 1)[-1],
        title=item.get("title", ""),
        artists=item.get("artistName", ""),
        duration=(item.get("duration") or 0) / 1000,
        track_number=number,
    ) for number, item in enumerate(items, 1)]
    if not tracks:
        raise SourceError.of("empty", "В плейлисте Apple Music нет доступных песен")

    total = header.get("trackCount") or len(tracks)
    note = ""
    if total > len(tracks):
        note = t("Страница Apple Music отдаёт первые {shown} треков из {total}. "
                 "Остальные придётся добавить отдельно", shown=len(tracks), total=total)
    curator = next((link.get("title") for link in header.get("subtitleLinks") or [] if link.get("title")), "")
    album = Album(
        id=url.rstrip("/").rsplit("/", 1)[-1],
        name=header.get("title") or t("Плейлист"),
        artist=curator or t("Разные исполнители"),
        kind="playlist",
        cover_url=_apple_artwork(header.get("artwork")),
        tracks=tracks,
        service="Apple Music",
        note=note,
    )
    return Release(album, tracks)


def _apple_sections(page: str) -> dict[str, dict]:
    m = _APPLE_DATA_RE.search(page)
    try:
        sections = json.loads(m.group(1))["data"][0]["data"]["sections"]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as e:
        raise SourceError.of("unreadable", "Не удалось прочитать страницу Apple Music: ссылка неверна или "
                                           "страница изменила формат") from e
    return {section.get("itemKind"): section for section in sections}


def _apple_artwork(artwork: dict | None) -> str:
    """Artwork URLs are templates: the size is filled in by whoever asks."""
    url = ((artwork or {}).get("dictionary") or {}).get("url") or ""
    return url.replace("{w}", "1000").replace("{h}", "1000").replace("{f}", "jpg")


def _itunes(endpoint: str, **params) -> list[dict]:
    query = urllib.parse.urlencode(params)
    return fetch_json(f"https://itunes.apple.com/{endpoint}?{query}", service="Apple Music").get("results") or []


def _split_kind(name: str) -> tuple[str, str]:
    """Apple Music names releases like "Title - EP" and "Title - Single"."""
    m = re.match(r"^(.*?)\s+-\s+(EP|Single)$", name)
    return (m.group(1), m.group(2).lower()) if m else (name, "album")


# Deezer: the public API has albums, tracks and whole playlists, no key needed

def _deezer(url: str, parts: urllib.parse.SplitResult) -> Release:
    if (parts.hostname or "").lower() not in ("deezer.com", "www.deezer.com"):
        url = _deezer_short_link(url)
        parts = urllib.parse.urlsplit(url)
    m = _DEEZER_PATH_RE.match(parts.path)
    if not m:
        raise SourceError.of("unsupported_link",
                             "Из Deezer поддерживаются ссылки на альбомы, треки и плейлисты")
    kind, deezer_id = m.group(1).lower(), m.group(2)
    if kind == "album":
        return _deezer_album(deezer_id)
    if kind == "playlist":
        return _deezer_playlist(deezer_id)
    return _deezer_track(deezer_id)


def _deezer_short_link(link: str) -> str:
    """link.deezer.com and deezer.page.link redirect to www.deezer.com."""
    request = urllib.request.Request(link, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            final = resp.geturl()
            body = "" if _DEEZER_LINK_RE.search(final) else resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        raise SourceError.of("short_link_broken", "Не удалось открыть короткую ссылку {link}: {error}",
                             link=link, error=e) from e
    m = _DEEZER_LINK_RE.search(final) or _DEEZER_LINK_RE.search(body)
    if not m:
        raise SourceError.of("short_link_broken", "Короткая ссылка Deezer никуда не ведёт: {link}", link=link)
    return m.group(0)


def _deezer_album(album_id: str) -> Release:
    data = _deezer_get(f"album/{album_id}", t("Deezer не нашёл альбом {id}", id=album_id))
    items = _deezer_pages(f"album/{album_id}/tracks", t("Deezer не нашёл альбом {id}", id=album_id))
    tracks = [_deezer_item(item, number) for number, item in enumerate(items, 1)]
    if not tracks:
        raise SourceError.of("empty", "В альбоме Deezer {id} нет треков", id=album_id)
    kind = data.get("record_type") or "album"
    album = Album(
        id=str(album_id),
        name=data.get("title", ""),
        artist=(data.get("artist") or {}).get("name", ""),
        release_date=data.get("release_date") or "",
        kind={"compile": "compilation"}.get(kind, kind),
        cover_url=data.get("cover_xl") or data.get("cover_big") or "",
        tracks=tracks,
        service="Deezer",
        genre=_deezer_genre(data),
    )
    return Release(album, tracks)


def _deezer_genre(album: dict) -> str:
    genres = (album.get("genres") or {}).get("data") or []
    return (genres[0].get("name") or "") if genres else ""


def _deezer_playlist(playlist_id: str) -> Release:
    missing = t("Deezer не нашёл плейлист {id}: он удалён или закрыт", id=playlist_id)
    data = _deezer_get(f"playlist/{playlist_id}", missing)
    items = _deezer_pages(f"playlist/{playlist_id}/tracks", missing)
    # A playlist numbers its own rows, and the same song may sit in it twice
    tracks = [_deezer_item(item, number, playlist=True) for number, item in enumerate(items, 1)]
    if not tracks:
        raise SourceError.of("empty", "Плейлист пуст или закрыт")
    album = Album(
        id=str(playlist_id),
        name=data.get("title") or t("Плейлист"),
        artist=(data.get("creator") or {}).get("name") or t("Разные исполнители"),
        kind="playlist",
        cover_url=data.get("picture_xl") or data.get("picture_big") or "",
        tracks=tracks,
        service="Deezer",
    )
    return Release(album, tracks)


def _deezer_track(track_id: str) -> Release:
    data = _deezer_get(f"track/{track_id}", t("Deezer не нашёл трек {id}", id=track_id))
    release = _deezer_album((data.get("album") or {}).get("id") or "")
    track = next((t for t in release.album.tracks if t.id == str(track_id)), None)
    if track is None:
        raise SourceError.of("not_in_album",
                             "Трека {id} нет в альбоме «{album}»", id=track_id, album=release.album.name)
    return Release(release.album, [track], single=True)


def _deezer_item(item: dict, number: int, playlist: bool = False) -> Track:
    return Track(
        id=f"{number}-{item.get('id')}" if playlist else str(item.get("id") or number),
        # The full title keeps the version ("Radio Edit", "Live"), which the matcher needs
        title=item.get("title") or item.get("title_short") or "",
        artists=(item.get("artist") or {}).get("name", ""),
        duration=item.get("duration") or 0,
        track_number=number if playlist else item.get("track_position") or number,
        disc_number=1 if playlist else item.get("disk_number") or 1,
        explicit=bool(item.get("explicit_lyrics")),
        isrc=item.get("isrc") or "",
    )


def _deezer_get(path: str, missing: str) -> dict:
    """Deezer answers a wrong id with HTTP 200 and an error object."""
    url = path if path.startswith("https://") else f"https://api.deezer.com/{path}"
    data = fetch_json(url, service="Deezer")
    error = data.get("error")
    if error:
        if error.get("code") == 800:  # "no data"
            raise SourceError(missing, "not_found")
        raise SourceError.of("service_error", "Deezer ответил ошибкой: {error}",
                             error=error.get("message") or error.get("type") or error)
    return data


def _deezer_pages(path: str, missing: str) -> list[dict]:
    """Track lists come in pages, each naming the next one."""
    items: list[dict] = []
    url = f"https://api.deezer.com/{path}?limit=500"
    for _ in range(100):
        page = _deezer_get(url, missing)
        items += page.get("data") or []
        url = page.get("next") or ""
        if not url.startswith("https://api.deezer.com/"):
            break
    return items


# ISRC: the code of one recording, the same in every catalogue. Deezer names it
# for every track it lists, Spotify's pages and Apple's catalogue do not; with
# it YouTube Music finds that very recording rather than one of the same name.

# Deezer allows 50 requests in 5 seconds; lookups for many tracks at once stay under
ISRC_PACE = 0.12  # seconds between two lookups
# After Deezer failed to answer, tracks are matched by name alone for this long,
# so that a network where it is blocked does not wait on it for every track
ISRC_REST = 300  # seconds
# Words that make a recording another one: "Fuel" and "Fuel - Remastered" have codes of their own
_VERSION_MARKS = _VERSION_WORDS | {"mix", "mono", "radio", "version"}
_isrc_lock = threading.Lock()
_isrc_clock = {"last": 0.0, "rest_until": 0.0}


def borrow_isrcs(album: Album) -> int:
    """Gives the album's tracks the codes Deezer has for the same album, which
    is two requests for all of them. Answers how many tracks got one."""
    if not album.artist or not album.name or all(track.isrc for track in album.tracks):
        return 0
    found = _deezer_quick("search/album", q=f"{album.artist} {album.name}", limit=10)
    fits = [item for item in found
            if _similarity(_norm(album.name), _norm(item.get("title") or "")) >= 0.9
            and _artist_fits(album.artist, (item.get("artist") or {}).get("name") or "")]
    if not fits:
        return 0
    # The same edition first: a deluxe one lists the same songs among others
    fits.sort(key=lambda item: item.get("nb_tracks") != len(album.tracks))
    items = _deezer_quick(f"album/{fits[0].get('id')}/tracks", limit=500)
    given = 0
    for track in album.tracks:
        if not track.isrc:
            track.isrc = _same_recording(track, items)
            given += bool(track.isrc)
    return given


def track_isrc(track: Track) -> str:
    """The code of one recording, looked up on Deezer by its names; "" when no
    result is surely the same recording."""
    artist = track.artists.split(",")[0].strip()
    title = " ".join(_NOISE_RE.sub(" ", track.title).split()) or track.title
    found = _deezer_quick("search/track", q=f"{artist} {title}", limit=10)
    return _same_recording(track, [item for item in found
                                   if _artist_fits(artist, (item.get("artist") or {}).get("name") or "")])


def _same_recording(track: Track, items: list[dict]) -> str:
    """The code of the item that is this track: the same title, the same
    version words and, when both are known, the same length within 2 seconds."""
    marks = _version_marks(track.title)
    for item in items:
        title = item.get("title") or ""
        length = item.get("duration") or 0
        if (item.get("isrc") and _similarity(_norm(track.title), _norm(title)) >= 0.9
                and _version_marks(title) == marks
                and (not track.duration or not length or abs(length - track.duration) <= 2)):
            return str(item["isrc"])
    return ""


def _version_marks(title: str) -> set[str]:
    words = re.findall(r"\w+", title.casefold())
    return {"remaster" if word.startswith("remaster") else word
            for word in words if word.startswith("remaster") or word in _VERSION_MARKS}


def _artist_fits(wanted: str, name: str) -> bool:
    return _artist_score(wanted, name) > 0 or _similarity(_norm(wanted), _norm(name)) >= 0.8


def _deezer_quick(path: str, **params) -> list[dict]:
    """One paced request with a short wait and no retries: a code is a nicety,
    and a track must not wait twenty seconds for one. Raises SourceError when
    Deezer does not answer, and then keeps quiet for ISRC_REST seconds."""
    with _isrc_lock:
        now = time.monotonic()
        if now < _isrc_clock["rest_until"]:
            raise SourceError.of("offline", "Deezer недавно не ответил, ISRC пока не ищем")
        wait = _isrc_clock["last"] + ISRC_PACE - now
        _isrc_clock["last"] = now + max(0.0, wait)
    if wait > 0:
        time.sleep(wait)
    request = urllib.request.Request(f"https://api.deezer.com/{path}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read())
    except (OSError, ValueError) as e:
        with _isrc_lock:
            _isrc_clock["rest_until"] = time.monotonic() + ISRC_REST
        raise SourceError.of("offline", "Deezer не ответил: {error}", error=e) from e
    if not isinstance(data, dict) or data.get("error"):
        return []  # a quota answer or "no data": this track goes without a code
    return [item for item in data.get("data") or [] if isinstance(item, dict)]


# YouTube and YouTube Music: metadata and audio in one place

def _youtube(parts: urllib.parse.SplitResult) -> Release:
    query = urllib.parse.parse_qs(parts.query)
    path = parts.path.rstrip("/")
    video_id = ""
    if (parts.hostname or "").endswith("youtu.be"):
        video_id = path.lstrip("/")
    elif path == "/watch":
        video_id = query.get("v", [""])[0]
    elif path.startswith(("/shorts/", "/live/", "/embed/")):
        video_id = path.split("/")[2]
    if video_id:
        return _youtube_track(video_id)
    if path.startswith("/browse/MPRE"):
        return _youtube_album(path.split("/")[2])
    if query.get("list"):
        return _youtube_playlist(query["list"][0])
    raise SourceError.of("unsupported_link", "Из YouTube поддерживаются ссылки на видео, альбомы и плейлисты")


def _youtube_album(browse_id: str, audio_items: list[dict] | None = None) -> Release:
    data = _call_ytmusic(lambda: ytmusic().get_album(browse_id), t("альбом"))
    artist = _names(data.get("artists"))
    items = data.get("tracks") or []
    if data.get("audioPlaylistId") and any(item.get("videoType") in _VIDEO_TYPES for item in items):
        # Album pages may point to music videos with intros; the album's audio playlist has plain tracks
        if audio_items is None:
            try:
                audio_items = ytmusic().get_playlist(data["audioPlaylistId"], limit=None).get("tracks") or []
            except Exception:
                audio_items = []
        items = [_prefer_audio(item, index, audio_items) for index, item in enumerate(items)]
    tracks = [_youtube_item(item, number, artist) for number, item in enumerate(items, 1)]
    if not tracks:
        raise SourceError.of("empty", "В альбоме YouTube Music нет треков")
    album = Album(
        id=browse_id,
        name=data.get("title", ""),
        artist=artist,
        release_date=data.get("year") or "",
        kind=(data.get("type") or "album").lower(),
        cover_url=_thumbnail(data.get("thumbnails")),
        tracks=tracks,
        service="YouTube Music",
    )
    return Release(album, tracks)


def _youtube_playlist(playlist_id: str) -> Release:
    data = _call_ytmusic(lambda: ytmusic().get_playlist(playlist_id, limit=None), t("плейлист"))
    items = data.get("tracks") or []
    album_ids = {(item.get("album") or {}).get("id") for item in items}
    if playlist_id.startswith("OLAK5uy_") and len(album_ids) == 1 and None not in album_ids:
        return _youtube_album(album_ids.pop(), items)  # an album page has better tags than its playlist

    tracks = [_youtube_item(item, number, "") for number, item in enumerate(items, 1)]
    if not tracks:
        raise SourceError.of("empty", "Плейлист пуст или закрыт")
    author = data.get("author")
    album = Album(
        id=playlist_id,
        name=data.get("title", ""),
        artist=(author.get("name") if isinstance(author, dict) else author) or t("Разные исполнители"),
        release_date=data.get("year") or "",
        kind="playlist",
        cover_url=_thumbnail(data.get("thumbnails")),
        tracks=tracks,
        service="YouTube",
    )
    return Release(album, tracks)


def _youtube_track(video_id: str) -> Release:
    data = _call_ytmusic(lambda: ytmusic().get_watch_playlist(video_id, limit=1), t("видео"))
    item = next((t for t in data.get("tracks") or [] if t.get("videoId") == video_id), None)
    if item is None:
        raise SourceError.of("not_found", "YouTube не нашёл видео {id}", id=video_id)
    audio_url = f"https://www.youtube.com/watch?v={video_id}"
    audio_source = "video" if item.get("videoType") in _VIDEO_TYPES else "song"

    album_id = (item.get("album") or {}).get("id")
    if album_id:  # tag the track with its album when YouTube Music knows it
        try:
            release = _youtube_album(album_id)
        except SourceError:
            release = None
        if release:
            same = ([t for t in release.album.tracks if t.audio_url == audio_url]
                    or [t for t in release.album.tracks if _norm(t.title) == _norm(item.get("title", ""))])
            if same:
                same[0].audio_url, same[0].audio_source = audio_url, audio_source
                return Release(release.album, same[:1], single=True)

    track = Track(
        id=video_id,
        title=item.get("title", ""),
        artists=_names(item.get("artists")),
        duration=_seconds(item.get("length")),
        track_number=1,
        audio_url=audio_url,
        audio_source=audio_source,
    )
    album = Album(id=video_id, name=track.title, artist=track.artists, release_date=item.get("year") or "",
                  kind="single", cover_url=_thumbnail(item.get("thumbnail")), tracks=[track], service="YouTube")
    return Release(album, [track], single=True)


def _youtube_item(item: dict, number: int, album_artist: str) -> Track:
    video_id = item.get("videoId") if item.get("isAvailable", True) else None
    return Track(
        id=f"{number}-{video_id or 'unavailable'}",
        title=item.get("title", ""),
        artists=_names(item.get("artists")) or album_artist,
        duration=item.get("duration_seconds") or 0,
        track_number=item.get("trackNumber") or number,
        explicit=bool(item.get("isExplicit")),
        audio_url=f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        audio_source=("video" if item.get("videoType") in _VIDEO_TYPES else "song") if video_id else "",
    )


def _prefer_audio(item: dict, index: int, audio_items: list[dict]) -> dict:
    """Swap a music video for the plain audio track with the same title."""
    if item.get("videoType") not in _VIDEO_TYPES:
        return item
    candidates = audio_items[index:index + 1] + audio_items
    for audio in candidates:
        if (audio.get("videoId") and audio.get("videoType") not in _VIDEO_TYPES
                and _norm(audio.get("title", "")) == _norm(item.get("title", ""))):
            return {**item, "videoId": audio["videoId"], "videoType": audio.get("videoType")}
    return item


def _call_ytmusic(func, what: str):
    try:
        return func()
    except Exception as e:  # ytmusicapi raises bare exceptions and KeyErrors on unknown ids
        raise SourceError.of("service_error",
                             "YouTube Music не отдал {what}: {error}", what=what, error=e) from e


# Last.fm: the link already names the artist and the album

def _lastfm(url: str, parts: urllib.parse.SplitResult) -> Release:
    m = _LASTFM_PATH_RE.search(parts.path)
    if not m or not m.group(2) or m.group(2).startswith("+"):
        raise SourceError.of("unsupported_link", "Из Last.fm поддерживаются ссылки на альбомы и треки, "
                                                 "а не на исполнителей")
    artist = urllib.parse.unquote_plus(m.group(1))
    second = urllib.parse.unquote_plus(m.group(2))
    third = urllib.parse.unquote_plus(m.group(3)) if m.group(3) and not m.group(3).startswith("+") else ""
    if m.group(2) == "_":
        return find_track(artist, third, "Last.fm")
    if third:
        return find_track(artist, third, "Last.fm", album=second)
    return find_album(artist, second, "Last.fm") or _lastfm_tracklist(url, artist, second)


def _lastfm_tracklist(url: str, artist: str, album_name: str) -> Release:
    page = fetch_text(url, service="Last.fm")
    tracks = []
    for row in _LASTFM_ROW_RE.findall(page):
        title = re.search(r'class="chartlist-name"[^>]*>\s*<a [^>]*title="([^"]*)"', row)
        if not title:
            continue
        position = re.search(r'class="chartlist-index"[^>]*>\s*(\d+)', row)
        length = re.search(r'class="chartlist-duration"[^>]*>\s*(\d+(?::\d+)+)', row)
        tracks.append(Track(
            id=str(len(tracks) + 1),
            title=html.unescape(title.group(1)),
            artists=artist,
            duration=_seconds(length.group(1)) if length else 0,
            track_number=int(position.group(1)) if position else len(tracks) + 1,
        ))
    if not tracks:
        raise SourceError.of("empty", "Не нашёл треки альбома «{album}» ({artist}): его нет на YouTube Music "
                                      "и в Apple Music, а на Last.fm у него нет списка треков",
                             album=album_name, artist=artist)
    released = re.search(r"Release Date</dt>\s*<dd[^>]*>[^<]*?(\d{4})", page)
    cover = re.search(r'<meta property="og:image" content="([^"]+)"', page)
    album = Album(id=url, name=album_name, artist=artist, release_date=released.group(1) if released else "",
                  cover_url=cover.group(1) if cover else "", tracks=tracks, service="Last.fm")
    return Release(album, tracks)


def find_genre(artist: str, title: str, known: str = "") -> str:
    """The genres to tag an album with, most voted first: "Alternative Rock; Art Rock".

    MusicBrainz comes first: its genres are voted on by listeners, as on Rate
    Your Music, and are as fine-grained. An album nobody has voted on there
    keeps the genre its own service named (Deezer and Apple Music name one);
    for Spotify, YouTube and Last.fm, which name none, the album is looked up
    in Apple's catalogue and then on Deezer. "" when nothing knows it.
    """
    if not artist or not title:
        return known
    try:
        genres = musicbrainz_genres(artist, title)
    except SourceError:
        genres = []
    if genres:
        return "; ".join(genres)
    if known:
        return known
    fields = lambda result: (_split_kind(result.get("collectionName", ""))[0], result.get("artistName", ""))
    try:
        best = _best(_itunes("search", term=f"{artist} {title}", entity="album", limit=10), title, artist, fields)
    except SourceError:
        best = None
    if best and best.get("primaryGenreName"):
        return best["primaryGenreName"]
    try:
        query = urllib.parse.quote(f'artist:"{artist}" album:"{title}"')
        found = fetch_json(f"https://api.deezer.com/search/album?q={query}&limit=5", service="Deezer")
        best = _best(found.get("data") or [], title, artist,
                     lambda result: (result.get("title", ""), (result.get("artist") or {}).get("name", "")))
        if best:
            return _deezer_genre(_deezer_get(f"album/{best['id']}", ""))
    except SourceError:
        pass
    return ""


def musicbrainz_genres(artist: str, title: str) -> list[str]:
    """Up to GENRE_LIMIT genres of the album, by listeners' votes on MusicBrainz.

    A genre with far fewer votes than the first is left out: one listener's
    guess should not stand beside what twenty agreed on.
    """
    query = urllib.parse.quote(f'releasegroup:"{_lucene(title)}" AND artist:"{_lucene(artist)}"')
    found = _musicbrainz(f"release-group/?query={query}&limit=5")
    fields = lambda group: (group.get("title", ""),
                            " ".join(credit.get("name", "") for credit in group.get("artist-credit") or []))
    best = _best(found.get("release-groups") or [], title, artist, fields)
    if not best:
        return []
    genres = _musicbrainz(f"release-group/{best['id']}?inc=genres").get("genres") or []
    ranked = sorted(genres, key=lambda genre: (-genre.get("count", 0), genre.get("name", "")))
    if not ranked:
        return []
    top = ranked[0].get("count", 0)
    chosen = [genre["name"] for genre in ranked[:GENRE_LIMIT]
              if genre.get("name") and genre.get("count", 0) * 4 >= top]
    return [_genre_case(name) for name in chosen]


def _musicbrainz(path: str) -> dict:
    global _musicbrainz_last
    with _MUSICBRAINZ_LOCK:
        wait = _musicbrainz_last + 1.1 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return fetch_json(f"https://musicbrainz.org/ws/2/{path}&fmt=json" if "?" in path
                              else f"https://musicbrainz.org/ws/2/{path}?fmt=json",
                              service="MusicBrainz", user_agent=_MUSICBRAINZ_UA)
        finally:
            _musicbrainz_last = time.monotonic()


def _lucene(text: str) -> str:
    """Quotes and backslashes would end the phrase in a MusicBrainz query."""
    return text.replace("\\", " ").replace('"', " ")


def _genre_case(name: str) -> str:
    """MusicBrainz writes "alternative rock"; tags usually say "Alternative Rock"."""
    words = name.split(" ")
    return " ".join(word if index and word in _GENRE_SMALL_WORDS
                    else "-".join(part.upper() if part in _GENRE_CAPITALS else part[:1].upper() + part[1:]
                                  for part in word.split("-"))
                    for index, word in enumerate(words))


def artist_picture(name: str) -> str:
    """A photo of the artist from Deezer's catalogue, or "" when it has none.

    Only an artist of exactly that name counts: a near match would put a
    stranger's face on somebody's albums.
    """
    query = urllib.parse.quote(name)
    data = fetch_json(f"https://api.deezer.com/search/artist?q={query}&limit=5", service="Deezer")
    for item in data.get("data") or []:
        if _norm(item.get("name") or "") == _norm(name):
            url = item.get("picture_big") or item.get("picture_medium") or ""
            # An artist without a photo gets Deezer's placeholder, whose address has no image id
            return "" if "/images/artist//" in url else url
    return ""


def search(query: str) -> Release:
    """A release found by name: "Исполнитель - Альбом", or just a title.

    Typed instead of a link. An album wins over a track of the same name,
    because a person who means one song usually says so in the title.
    """
    query = " ".join(query.split())
    if not query:
        raise SourceError.of("empty_query",
                             "Вставьте ссылку или напишите, что искать: «Исполнитель - Альбом»")
    artist, title = _split_query(query)
    album = find_album(artist, title, t("поиск")) if title else None
    if album:
        return album
    return find_track(artist, title or query, t("поиск"))


def search_track(query: str) -> Release:
    """A single song found by name, never the album of the same name.

    Lists read from a playlist export name songs, and a playlist often holds
    the title track of an album; search() would take the album for it.
    """
    query = " ".join(query.split())
    if not query:
        raise SourceError.of("empty_query",
                             "Вставьте ссылку или напишите, что искать: «Исполнитель - Альбом»")
    artist, title = _split_query(query)
    return find_track(artist, title or query, t("поиск"))


def _split_query(query: str) -> tuple[str, str]:
    """Splits "Artist - Title" on the dash people actually type."""
    for separator in (" - ", " — ", " – ", " -- "):
        artist, found, title = query.partition(separator)
        if found and artist.strip() and title.strip():
            return artist.strip(), title.strip()
    return "", query


# Finding a release by name, for links that carry nothing else

def _try(func):
    """The release, or None when the catalogue refuses to open it."""
    try:
        return func()
    except SourceError:
        return None


def find_album(artist: str, title: str, service: str) -> Release | None:
    """The album on YouTube Music (with audio) or Apple Music (tags only).

    None also covers the case where a catalogue matched the name but then
    refused to hand over the release: every caller has a fallback of its own,
    and letting the refusal through as an exception would skip it.
    """
    return _try(lambda: _find_album(artist, title, service))


def _find_album(artist: str, title: str, service: str) -> Release | None:
    query = f"{artist} {title}"
    try:
        results = ytmusic().search(query, filter="albums", limit=10)
    except Exception:
        results = []
    best = _best(results, title, artist, lambda r: (r.get("title", ""), _names(r.get("artists"))))
    if best:
        release = _youtube_album(best["browseId"])
    else:
        for term in (query, title):  # the catalogue search sometimes misses "artist + title"
            try:
                results = _itunes("search", term=term, entity="album", limit=25)
            except SourceError:
                results = []
            best = _best(results, title, artist,
                         lambda r: (_split_kind(r.get("collectionName", ""))[0], r.get("artistName", "")))
            if best:
                break
        if not best:
            return None
        release = _apple_album(best["collectionId"])
    release.album.service = service
    return release


def find_track(artist: str, title: str, service: str, album: str = "") -> Release:
    if not title:
        raise SourceError.of("no_title", "В ссылке нет названия трека")
    query = f"{artist} {title}"
    try:
        songs = ytmusic().search(query, filter="songs", limit=10)
    except Exception:
        songs = []
    best = _best(songs, title, artist, lambda r: (r.get("title", ""), _names(r.get("artists"))))
    if best and best.get("videoId"):
        # A catalogue that matched the name but will not open the release is
        # no better than no match: the searches below still have a chance.
        release = _try(lambda: _youtube_track(best["videoId"]))
        if release:
            release.album.service = service
            return release

    try:
        songs = _itunes("search", term=query, entity="song", limit=10)
    except SourceError:
        songs = []
    best = _best(songs, title, artist, lambda r: (r.get("trackName", ""), r.get("artistName", "")))
    if best:
        release = _try(lambda: _apple_album(best["collectionId"]))
        track = next((t for t in release.album.tracks if t.id == str(best["trackId"])), None) if release else None
        if track:
            release.album.service = service
            return Release(release.album, [track], single=True)

    # Unknown to both catalogues: the audio is searched by name alone
    track = Track(id="1", title=title, artists=artist, duration=0, track_number=1)
    found = Album(id=title, name=album or title, artist=artist, kind="single", tracks=[track], service=service)
    return Release(found, [track], single=True)


def _best(results: list[dict], title: str, artist: str, fields) -> dict | None:
    scored = []
    for result in results:
        name, names = fields(result)
        title_score = _similarity(_norm(title), _norm(name))
        if title_score >= 0.85 and (_artist_score(artist, names) > 0
                                    or _similarity(_norm(artist), _norm(names)) >= 0.8):
            scored.append((title_score, result))
    return max(scored, key=lambda pair: pair[0])[1] if scored else None


# SoundCloud and other sites yt-dlp can read

def _ytdlp(url: str) -> Release:
    try:
        with yt_dlp.YoutubeDL({**_YTDLP_OPTS, "extract_flat": "in_playlist"}) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        message = str(e).removeprefix("ERROR: ")
        if "DRM" in message and "soundcloud.com" in url:
            track = _protected_track(url, 1, "")  # the audio is looked for elsewhere
            return find_track(track.artists, track.title, "SoundCloud")
        if "Unsupported URL" in message or message.startswith("[generic]"):
            raise SourceError.of("unsupported_link",
                                 "По этой ссылке не нашлось музыки. Подойдут ссылки из {services}",
                                 services=SUPPORTED if language() == "ru" else _SUPPORTED_EN) from e
        raise SourceError.of("link_failed", "Не удалось открыть ссылку: {error}", error=message) from e

    service = _service_name(info, url)
    if info.get("_type") != "playlist":
        track = _ytdlp_track(info, 1, "")
        album = Album(id=str(info.get("id") or url), name=info.get("album") or track.title,
                      artist=info.get("album_artist") or track.artists,
                      release_date=_ymd(info.get("release_date") or info.get("upload_date")),
                      kind="single", cover_url=_ytdlp_cover(info), tracks=[track], service=service)
        return Release(album, [track], single=True)

    urls = [e.get("url") or e.get("webpage_url") for e in info.get("entries") or [] if e]
    with ThreadPoolExecutor(max_workers=4) as pool:
        details = list(pool.map(_ytdlp_details, urls))
    album_artist = info.get("album_artist") or info.get("uploader") or info.get("channel") or ""
    name = info.get("album") or info.get("title") or ""
    tracks = [_ytdlp_track(detail, number, album_artist) if detail else _protected_track(entry, number, album_artist)
              for number, (entry, detail) in enumerate(zip(urls, details), 1)]
    if not tracks:
        raise SourceError.of("empty", "Плейлист пуст или закрыт")
    if not all(details):  # some tracks are protected: the same album elsewhere may be fully open
        found = find_album(album_artist, name, service)
        if found:
            return found
    album = Album(
        id=str(info.get("id") or url),
        name=name,
        artist=album_artist,
        release_date=_ymd(info.get("release_date") or info.get("upload_date")),
        kind=(info.get("album_type") or "playlist").lower(),
        cover_url=_ytdlp_cover(info) or next((_ytdlp_cover(d) for d in details if d and _ytdlp_cover(d)), ""),
        tracks=tracks,
        service=service,
    )
    return Release(album, tracks)


def _ytdlp_details(url: str) -> dict | None:
    try:
        with yt_dlp.YoutubeDL(_YTDLP_OPTS) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError:
        return None  # e.g. DRM-protected, matched elsewhere by its title


def _ytdlp_track(info: dict, number: int, album_artist: str) -> Track:
    extractor = (info.get("extractor_key") or "").lower()
    return Track(
        id=f"{number}-{info.get('id')}",
        title=info.get("track") or info.get("title") or "",
        artists=(info.get("artist") or ", ".join(info.get("artists") or [])
                 or info.get("uploader") or album_artist),
        duration=info.get("duration") or 0,
        track_number=info.get("track_number") or number,
        audio_url=info.get("webpage_url") or info.get("original_url") or "",
        audio_source="soundcloud" if extractor.startswith("soundcloud") else "web",
    )


def _protected_track(url: str, number: int, artist: str) -> Track:
    """Metadata for a track whose audio cannot be taken from the link."""
    title = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].replace("-", " ").capitalize()
    if "soundcloud.com" in url:
        try:
            data = fetch_json("https://soundcloud.com/oembed?" + urllib.parse.urlencode({"format": "json", "url": url}),
                              service="SoundCloud")
            artist = data.get("author_name") or artist
            title = (data.get("title") or title).removesuffix(f" by {artist}")
        except SourceError:
            pass
    return Track(id=f"{number}-protected", title=title, artists=artist, duration=0, track_number=number)


def _service_name(info: dict, url: str) -> str:
    extractor = (info.get("extractor_key") or "").lower()
    for key, name in _SERVICE_NAMES.items():
        if extractor.startswith(key):
            return name
    return (urllib.parse.urlsplit(url).hostname or "").removeprefix("www.")


def _ytdlp_cover(info: dict) -> str:
    thumbnails = info.get("thumbnails") or []
    square = next((t["url"] for t in thumbnails if t.get("id") == "t500x500" and t.get("url")), "")
    if square:
        return square
    best = max(thumbnails, key=lambda t: (t.get("preference") or 0, t.get("width") or 0), default={})
    return best.get("url") or info.get("thumbnail") or ""


# Small helpers

def _names(artists) -> str:
    return ", ".join(a["name"] for a in artists or [] if a.get("name"))


def _thumbnail(thumbnails) -> str:
    if not thumbnails:
        return ""
    url = max(thumbnails, key=lambda t: t.get("width") or 0).get("url", "")
    return re.sub(r"=w\d+-h\d+", "=w1200-h1200", url)  # Google image URLs scale on request


def _seconds(text: str | None) -> float:
    total = 0
    for part in (text or "").split(":"):
        if not part.isdigit():
            return 0
        total = total * 60 + int(part)
    return total


def _ymd(value: str | None) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if value and len(value) == 8 and value.isdigit() else ""
