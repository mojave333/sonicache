"""Finding a Spotify track on YouTube Music, SoundCloud or YouTube."""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

import requests
import yt_dlp
from ytmusicapi import YTMusic

from .logs import YtdlpLogger
from .models import Album, Track

MIN_SCORE = 0.62  # below this a candidate is considered a different song
GOOD_SCORE = 0.9  # a candidate this good ends the search, later sources are skipped

# Official audio on YouTube Music is preferred, then artist uploads on
# SoundCloud (small artists often skip YouTube), then any YouTube video.
SOURCE_BONUS = {"song": 0.05, "soundcloud": 0.03, "video": 0.0}
SOURCE_NAMES = {"song": "YouTube Music", "soundcloud": "SoundCloud", "video": "YouTube-видео"}

_NOISE_RE = re.compile(
    r"\s[-–—]\s[^-–—]*remaster[^-–—]*$"
    r"|[(\[][^)\]]*remaster[^)\]]*[)\]]"
    r"|[(\[](?:feat|ft|with)\.?\s[^)\]]*[)\]]"
    r"|\s(?:feat|ft)\.\s.*$"
    r"|[(\[](?:official|lyrics?|audio|video|hd|hq|4k|explicit|clean)\b[^)\]]*[)\]]",
    re.I,
)
# A candidate containing one of these words (absent from the Spotify title
# and album name) is most likely a different version of the song.
_VERSION_WORDS = {
    "live", "remix", "cover", "karaoke", "instrumental", "acoustic", "sped",
    "slowed", "nightcore", "reverb", "8d", "boosted", "tribute", "reaction",
    "mashup", "demo", "edit", "extended", "edition", "drumless",
    "минус", "кавер", "ремикс", "концерт",
}
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h",
    "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "і": "i", "є": "e", "ґ": "g",
})
_MUSIC_HOST_RE = re.compile(r"^https://music\.youtube\.com(?=[/?]|$)")


class SearchError(Exception):
    pass


@dataclass
class Match:
    source: str  # "song" (YouTube Music audio), "soundcloud" or "video"
    url: str  # what yt-dlp downloads
    page_url: str  # what a person opens
    title: str
    artists: str
    duration: float
    score: float


class _FallbackSession(requests.Session):
    """Sends YouTube Music API calls to www.youtube.com when music.youtube.com
    is unreachable. Some networks (DNS filtering, DPI-bypass setups that only
    map www.youtube.com) fail to resolve it, while the same API works there."""

    use_www = False  # shared by all threads once detected

    def request(self, method, url, *args, **kwargs):
        kwargs.setdefault("timeout", 30)
        if not _FallbackSession.use_www:
            try:
                return super().request(method, url, *args, **kwargs)
            except requests.ConnectionError:
                if not _MUSIC_HOST_RE.match(url):
                    raise
                _FallbackSession.use_www = True
        url = _MUSIC_HOST_RE.sub("https://www.youtube.com", url)
        return super().request(method, url, *args, **kwargs)


_clients = threading.local()


def ytmusic() -> YTMusic:
    """A YouTube Music client for the current thread: requests.Session is not thread-safe."""
    if not hasattr(_clients, "ytmusic"):
        _clients.ytmusic = YTMusic(requests_session=_FallbackSession())
    return _clients.ytmusic


class Matcher:
    def find_all(self, track: Track, album: Album, exhaustive: bool = False) -> list[Match]:
        """Every candidate good enough to be this track, best first. The next one
        is worth trying when the best refuses to download: age-gated videos,
        dead links and region blocks all look fine until yt-dlp reaches them.
        An exhaustive search asks every source even when the first one nails it."""
        query = f"{track.artists} {track.title}"
        best: dict[str, Match] = {}  # by url: the same song can appear in two searches
        errors = []
        for search in (self._youtube_music_songs, self._soundcloud, self._youtube_videos):
            try:
                candidates = search(query)
            except SearchError as e:
                errors.append(e)
                continue
            for candidate in candidates:
                match = _score(candidate, track, album)
                if match and match.score >= MIN_SCORE:
                    known = best.get(match.url)
                    if known is None or match.score > known.score:
                        best[match.url] = match
            if not exhaustive and any(match.score >= GOOD_SCORE for match in best.values()):
                break
        if not best and errors:  # "not found" would hide the real reason
            raise errors[0]
        return sorted(best.values(), key=lambda match: match.score, reverse=True)

    def _youtube_music_songs(self, query: str) -> list[dict]:
        return self._youtube_music(query, "songs")

    def _youtube_videos(self, query: str) -> list[dict]:
        return self._youtube_music(query, "videos")

    def _youtube_music(self, query: str, search_filter: str) -> list[dict]:
        results = _retry(lambda: ytmusic().search(query, filter=search_filter, limit=10), "YouTube Music")
        candidates = []
        for result in results:
            video_id = result.get("videoId")
            if not video_id or not result.get("title"):
                continue
            candidates.append({
                "source": "song" if result.get("resultType") == "song" else "video",
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "page_url": f"https://music.youtube.com/watch?v={video_id}",
                "title": result["title"],
                "artists": ", ".join(a.get("name", "") for a in result.get("artists") or []),
                "duration": result.get("duration_seconds") or 0,
                "album": (result.get("album") or {}).get("name") or "",
                "explicit": result.get("isExplicit"),
            })
        return candidates

    def _soundcloud(self, query: str) -> list[dict]:
        opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                "logger": YtdlpLogger("soundcloud")}

        def search() -> list[dict]:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(f"scsearch10:{query}", download=False).get("entries") or []

        return [{
            "source": "soundcloud",
            "url": entry["url"],
            "page_url": entry.get("webpage_url") or entry["url"],
            "title": entry["title"],
            "artists": entry.get("uploader") or "",
            "duration": entry.get("duration") or 0,
            "album": "",
            "explicit": None,
        } for entry in _retry(search, "SoundCloud") if entry.get("url") and entry.get("title")]


def _retry(func, source: str, attempts: int = 3):
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as e:  # ytmusicapi and yt-dlp raise many unrelated exception types
            if attempt == attempts:
                message = str(e).removeprefix("ERROR: ")
                raise SearchError(f"поиск на {source} не удался: {message}") from e
            time.sleep(2 * attempt)


def _score(candidate: dict, track: Track, album: Album) -> Match | None:
    title = candidate["title"]
    title_score = _similarity(_norm(track.title), _norm(title))
    # Video titles often look like "Artist - Title", so artists are looked up in both.
    artist_score = _artist_score(track.artists, f"{candidate['artists']} {title}")
    duration = candidate["duration"]
    if track.duration and duration:
        diff = abs(duration - track.duration)
        duration_score = 1.0 if diff <= 3 else max(0.0, 1 - (diff - 3) / 25)
    else:
        duration_score = 0.5
    same_album = (bool(candidate["album"])
                  and _similarity(_norm(album.name), _norm(candidate["album"])) > 0.8)
    # Without the artist only a near-exact title, length and album together are enough:
    # a same-named song by someone else often has a similar length.
    if artist_score == 0 and not (title_score >= 0.9 and duration_score >= 0.9 and same_album):
        return None

    score = (0.4 * title_score + 0.25 * artist_score + 0.35 * duration_score
             + SOURCE_BONUS[candidate["source"]])
    if same_album:
        score += 0.1
    if candidate["explicit"] is not None and candidate["explicit"] == track.explicit:
        score += 0.03
    allowed = set(_norm(f"{track.title} {album.name}").split())
    score -= 0.3 * len((set(_norm(title).split()) & _VERSION_WORDS) - allowed)

    return Match(candidate["source"], candidate["url"], candidate["page_url"], title,
                 candidate["artists"], duration, round(score, 3))


def _norm(text: str) -> str:
    text = _NOISE_RE.sub(" ", unicodedata.normalize("NFKC", text))
    text = "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]|_", " ", text.replace("&", " and "))
    return " ".join(text.split())


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ratio = max(SequenceMatcher(None, a, b).ratio(),
                SequenceMatcher(None, a.translate(_TRANSLIT), b.translate(_TRANSLIT)).ratio())
    # One name inside the other is usually the same release with something
    # appended ("one more time" in "daft punk one more time"). It is not when
    # the shorter name is a fraction of the longer: "never" sits inside "never
    # gonna give you up" and is a different song.
    shorter, longer = sorted((f" {a} ", f" {b} "), key=len)
    if shorter in longer and len(shorter) >= 0.5 * len(longer):
        ratio = max(ratio, 0.85)
    return ratio


def _artist_score(track_artists: str, candidate: str) -> float:
    names = [_norm(name) for name in track_artists.split(",")]
    names = [name for name in names if name]
    if not names:
        return 0.5
    haystacks = (f" {_norm(candidate)} ", f" {_norm(candidate).translate(_TRANSLIT)} ")

    def found(name: str) -> bool:
        return any(f" {n} " in h for n in (name, name.translate(_TRANSLIT)) for h in haystacks)

    main = 0.7 if found(names[0]) else 0.0
    rest = names[1:]
    featured = 0.3 * sum(map(found, rest)) / len(rest) if rest else (0.3 if main else 0.0)
    return main + featured
