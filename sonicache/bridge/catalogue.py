"""The Catalogue: the search, its first screen, a release and an artist's picture.

One part of gui.Api: the methods the page calls on window.pywebview.api.
"""

from __future__ import annotations

import threading
import time

from .. import logs
from ..engine import catalog, sources
from ..engine.models import SourceError
from ..libraryscan import _ARTIST_LOCK

ARTISTS_FILE = "artists.json"  # names already looked up on Deezer, beside the history

# The Catalogue's first screen and the Deezer ids found for it: kept for a while, so that opening it
# is not a run of requests each time, and shown as it was when Deezer does not answer
CATALOGUE_FILE = "catalogue.json"

CATALOGUE_FOR = 6 * 3600

ARTIST_RETRY = 7 * 86400  # an artist Deezer did not know is asked about again after a week

_ARTIST_QUERIES = threading.BoundedSemaphore(4)  # Deezer allows 50 requests in 5 seconds

def _track_link(album, track) -> str:
    """A link to one track of a found album, to download it alone; "" where the
    catalogue gives none."""
    if album.service == "Deezer" and track.id.isdigit():
        return f"https://www.deezer.com/track/{track.id}"
    if track.audio_url.startswith("https://www.youtube.com/watch?v="):
        return track.audio_url.replace("https://www.youtube.com/", "https://music.youtube.com/", 1)
    return ""


class Catalogue:
    def search(self, query: str) -> dict:
        """Albums, tracks and artists from the catalogues, for the Search view."""
        try:
            return catalog.search(query)
        except SourceError as e:
            return {"error": str(e), "service": "", "albums": [], "tracks": [], "artists": []}

    def catalogue_home(self, artists: list[str], refresh: bool = False) -> dict:
        """The Catalogue before anything is asked: the chart, and what is new from `artists`.

        Kept for six hours; an answer older than that is shown still, marked, when
        Deezer cannot be reached.
        """
        names = [str(name) for name in artists or [] if str(name).strip()][:catalog.HOME_ARTISTS]
        # "2": the first screen has the chart's tracks and artists and the editors' picks too, and an
        # answer kept from before them is asked again rather than shown without them
        key = "|".join(["2", *(name.casefold() for name in names)])
        path = logs.data_dir() / CATALOGUE_FILE
        with _ARTIST_LOCK:
            kept = logs.read_json(path)
        home = kept.get("home") if isinstance(kept.get("home"), dict) else {}
        if home.get("key") == key and not refresh and time.time() - home.get("at", 0) < CATALOGUE_FOR:
            return home["data"]
        known = dict(kept.get("ids") or {})
        try:
            data = catalog.home(names, known)
        except SourceError as e:
            if home.get("data"):
                return {**home["data"], "stale": True}
            return {"error": str(e), "service": "", "popular": [], "fresh": []}
        with _ARTIST_LOCK:
            logs.write_json(path, {"ids": known, "home": {"key": key, "at": time.time(), "data": data}})
        return data

    def release(self, link: str) -> dict:
        """A found album's tracklist, for its page in Search, before anything is downloaded."""
        try:
            album = sources.resolve(link).album
        except Exception as e:  # shown on the page instead of the tracks
            logs.log.warning("поиск: %s не открылся: %s", link, e)
            return {"error": str(e) or type(e).__name__}
        return {
            "title": album.name, "artist": album.artist, "year": album.year, "kind": album.kind,
            "cover": album.cover_url, "service": album.service,
            "tracks": [{"number": track.track_number, "disc": track.disc_number, "title": track.title,
                        "artists": track.artists, "duration": round(track.duration),
                        "link": _track_link(album, track)} for track in album.tracks],
        }

    def artist_picture(self, name: str) -> str:
        """A photo of the artist, looked up on Deezer once and remembered."""
        key = " ".join(str(name or "").split()).casefold()
        if not key:
            return ""
        path = logs.data_dir() / ARTISTS_FILE
        with _ARTIST_LOCK:
            known = logs.read_json(path)
        entry = known.get(key)
        if isinstance(entry, dict) and (entry.get("url") or time.time() - entry.get("asked", 0) < ARTIST_RETRY):
            return entry.get("url", "")
        with _ARTIST_QUERIES:
            try:
                url = sources.artist_picture(name)
            except SourceError:
                return ""  # offline or refused: asked again next time, nothing remembered
        with _ARTIST_LOCK:
            known = logs.read_json(path)
            known[key] = {"url": url, "asked": int(time.time())}
            logs.write_json(path, known)
        return url
