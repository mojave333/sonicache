"""Turning a link from a music service into a release to download.

Every source returns tracks with metadata. Services that stream audio openly
(YouTube, SoundCloud and other sites yt-dlp understands, e.g. Bandcamp) also
give each track its own audio URL. For the rest (Spotify, Apple Music,
Last.fm) the audio is matched on YouTube Music and SoundCloud later.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import yt_dlp

from . import spotify
from .matcher import SilentLogger, _artist_score, _norm, _similarity, ytmusic
from .models import Album, Release, SourceError, Track
from .net import fetch_json, fetch_text

SUPPORTED = "Spotify, Apple Music, YouTube, SoundCloud, Last.fm и сайтов вроде Bandcamp"

_APPLE_PATH_RE = re.compile(r"^/(?:([a-z]{2})/)?([\w-]+)/(?:[^/]+/)?(?:id)?(\d+)", re.I)
_LASTFM_PATH_RE = re.compile(r"/music/([^/?#]+)(?:/([^/?#]+))?(?:/([^/?#]+))?")
_LASTFM_ROW_RE = re.compile(r'<tr\s+class="\s*chartlist-row.*?</tr>', re.S)
# Music videos and user uploads, as opposed to official audio tracks
_VIDEO_TYPES = {"MUSIC_VIDEO_TYPE_UGC", "MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_PODCAST_EPISODE"}
_YTDLP_OPTS = {"quiet": True, "no_warnings": True, "skip_download": True, "logger": SilentLogger()}
_SERVICE_NAMES = {"soundcloud": "SoundCloud", "bandcamp": "Bandcamp", "mixcloud": "Mixcloud"}


def resolve(link: str) -> Release:
    link = link.strip()
    if link.startswith("spotify:"):
        return _spotify(link)
    url = link if re.match(r"^https?://", link, re.I) else f"https://{link}"
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if "." not in host or " " in link:
        raise SourceError(f"Не похоже на ссылку: {link}")

    def on(*domains: str) -> bool:
        return any(host == domain or host.endswith(f".{domain}") for domain in domains)

    if on("spotify.com", "spotify.link", "spoti.fi"):
        return _spotify(url)  # short links are only recognised with https://
    if on("music.apple.com", "itunes.apple.com"):
        return _apple(parts)
    if on("youtube.com", "youtu.be"):
        return _youtube(parts)
    if on("last.fm", "lastfm.ru"):
        return _lastfm(url, parts)
    if on("vk.com", "vk.ru", "vkontakte.ru"):
        raise SourceError(
            "VK показывает музыку только после входа в аккаунт, поэтому альбом по этой ссылке не прочитать. "
            "Найдите этот релиз в Spotify, Apple Music, YouTube или SoundCloud и вставьте ссылку оттуда."
        )
    return _ytdlp(url)


# Spotify

def _spotify(link: str) -> Release:
    kind, spotify_id = spotify.parse_link(link)
    if kind == "album":
        album = spotify.fetch_album(spotify_id)
        return Release(album, album.tracks)
    album, track = spotify.fetch_track(spotify_id)
    return Release(album, [track], single=True)


# Apple Music: the public iTunes lookup API has the whole tracklist

def _apple(parts: urllib.parse.SplitResult) -> Release:
    m = _APPLE_PATH_RE.match(parts.path)
    if not m or m.group(2) not in ("album", "song"):
        raise SourceError("Из Apple Music поддерживаются ссылки на альбомы и песни")
    country = (m.group(1) or "us").lower()
    song_id = m.group(3) if m.group(2) == "song" else urllib.parse.parse_qs(parts.query).get("i", [""])[0]
    if not song_id:
        return _apple_album(m.group(3), country)

    songs = _itunes("lookup", id=song_id, country=country)
    if not songs:
        raise SourceError(f"Apple Music не нашёл песню {song_id}: возможно, её нет в регионе {country.upper()}")
    release = _apple_album(songs[0]["collectionId"], country)
    track = next((t for t in release.album.tracks if t.id == song_id), None)
    if track is None:
        raise SourceError(f"Песни {song_id} нет в альбоме «{release.album.name}»")
    return Release(release.album, [track], single=True)


def _apple_album(collection_id: int | str, country: str = "us") -> Release:
    results = _itunes("lookup", id=collection_id, entity="song", country=country, limit=200)
    collection = next((r for r in results if r.get("wrapperType") == "collection"), None)
    if collection is None:
        raise SourceError(f"Apple Music не нашёл альбом {collection_id}: "
                          f"возможно, его нет в регионе {country.upper()}")
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
        raise SourceError(f"В альбоме Apple Music {collection_id} нет доступных песен")

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
    )
    return Release(album, tracks)


def _itunes(endpoint: str, **params) -> list[dict]:
    query = urllib.parse.urlencode(params)
    return fetch_json(f"https://itunes.apple.com/{endpoint}?{query}", service="Apple Music").get("results") or []


def _split_kind(name: str) -> tuple[str, str]:
    """Apple Music names releases like "Title - EP" and "Title - Single"."""
    m = re.match(r"^(.*?)\s+-\s+(EP|Single)$", name)
    return (m.group(1), m.group(2).lower()) if m else (name, "album")


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
    raise SourceError("Из YouTube поддерживаются ссылки на видео, альбомы и плейлисты")


def _youtube_album(browse_id: str, audio_items: list[dict] | None = None) -> Release:
    data = _call_ytmusic(lambda: ytmusic().get_album(browse_id), "альбом")
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
        raise SourceError("В альбоме YouTube Music нет треков")
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
    data = _call_ytmusic(lambda: ytmusic().get_playlist(playlist_id, limit=None), "плейлист")
    items = data.get("tracks") or []
    album_ids = {(item.get("album") or {}).get("id") for item in items}
    if playlist_id.startswith("OLAK5uy_") and len(album_ids) == 1 and None not in album_ids:
        return _youtube_album(album_ids.pop(), items)  # an album page has better tags than its playlist

    tracks = [_youtube_item(item, number, "") for number, item in enumerate(items, 1)]
    if not tracks:
        raise SourceError("Плейлист пуст или закрыт")
    author = data.get("author")
    album = Album(
        id=playlist_id,
        name=data.get("title", ""),
        artist=(author.get("name") if isinstance(author, dict) else author) or "Разные исполнители",
        release_date=data.get("year") or "",
        kind="playlist",
        cover_url=_thumbnail(data.get("thumbnails")),
        tracks=tracks,
        service="YouTube",
    )
    return Release(album, tracks)


def _youtube_track(video_id: str) -> Release:
    data = _call_ytmusic(lambda: ytmusic().get_watch_playlist(video_id, limit=1), "видео")
    item = next((t for t in data.get("tracks") or [] if t.get("videoId") == video_id), None)
    if item is None:
        raise SourceError(f"YouTube не нашёл видео {video_id}")
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
        raise SourceError(f"YouTube Music не отдал {what}: {e}") from e


# Last.fm: the link already names the artist and the album

def _lastfm(url: str, parts: urllib.parse.SplitResult) -> Release:
    m = _LASTFM_PATH_RE.search(parts.path)
    if not m or not m.group(2) or m.group(2).startswith("+"):
        raise SourceError("Из Last.fm поддерживаются ссылки на альбомы и треки, а не на исполнителей")
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
        raise SourceError(f"Не нашёл треки альбома «{album_name}» ({artist}): его нет на YouTube Music "
                          "и в Apple Music, а на Last.fm у него нет списка треков")
    released = re.search(r"Release Date</dt>\s*<dd[^>]*>[^<]*?(\d{4})", page)
    cover = re.search(r'<meta property="og:image" content="([^"]+)"', page)
    album = Album(id=url, name=album_name, artist=artist, release_date=released.group(1) if released else "",
                  cover_url=cover.group(1) if cover else "", tracks=tracks, service="Last.fm")
    return Release(album, tracks)


# Finding a release by name, for links that carry nothing else

def find_album(artist: str, title: str, service: str) -> Release | None:
    """The album on YouTube Music (with audio) or Apple Music (tags only)."""
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
        raise SourceError("В ссылке нет названия трека")
    query = f"{artist} {title}"
    try:
        songs = ytmusic().search(query, filter="songs", limit=10)
    except Exception:
        songs = []
    best = _best(songs, title, artist, lambda r: (r.get("title", ""), _names(r.get("artists"))))
    if best and best.get("videoId"):
        release = _youtube_track(best["videoId"])
        release.album.service = service
        return release

    try:
        songs = _itunes("search", term=query, entity="song", limit=10)
    except SourceError:
        songs = []
    best = _best(songs, title, artist, lambda r: (r.get("trackName", ""), r.get("artistName", "")))
    if best:
        release = _apple_album(best["collectionId"])
        track = next((t for t in release.album.tracks if t.id == str(best["trackId"])), None)
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
            raise SourceError(f"По этой ссылке не нашлось музыки. Подойдут ссылки из {SUPPORTED}") from e
        raise SourceError(f"Не удалось открыть ссылку: {message}") from e

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
        raise SourceError("Плейлист пуст или закрыт")
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
