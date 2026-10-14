"""The library over Wi-Fi, as a Subsonic server.

An iPhone lets no program on the computer reach its files, and an Android
phone without a cable is out of reach as well. What both can do is play from a
music server on the home network and keep albums for when there is none: the
Subsonic API is the language of such servers (Navidrome, Airsonic, Gonic), and
free players speak it on both — Amperfy on the iPhone, Ultrasonic on Android.

This module answers that API from the library as the window reads it. It knows
nothing of the window: what it serves comes through a Source of callables
(gui.py makes one). Api answers one request without a socket, so the tests ask
it directly; Listener is the HTTP server in front of it.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import random
import threading
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import __version__
from .i18n import t

VERSION = "1.16.1"
INDEX_FOR = 60  # seconds the library's snapshot answers before it is read again
IGNORED_ARTICLES = "The El La Los Las Le Les"
CONTENT_TYPES = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "opus": "audio/ogg", "flac": "audio/flac"}
USER = "sonicache"
NAMESPACE = "http://subsonic.org/restapi"

# Subsonic's error codes
GENERIC, MISSING, WRONG_CREDENTIALS, NOT_FOUND = 0, 10, 40, 70


@dataclass
class Response:
    status: int
    content_type: str
    body: bytes | None = None
    path: Path | None = None  # a file to send whole or by the range asked for
    stream: Iterable[bytes] | None = None  # sent as it comes, with no length
    headers: dict[str, str] = field(default_factory=dict)


class Failure(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def check_credentials(params: dict[str, str], user: str, password: str) -> int | None:
    """Subsonic's error code for the credentials of a request, None when they are right.
    A password comes as it is, as "enc:" and its UTF-8 in hex, or as a token: md5 of it and a salt."""
    name = params.get("u")
    if not name:
        return MISSING
    if params.get("t") and params.get("s"):
        expected = hashlib.md5((password + params["s"]).encode("utf-8")).hexdigest()
        right = hmac.compare_digest(expected.encode(), params["t"].lower().encode("utf-8"))
    elif params.get("p"):
        given = params["p"]
        if given.startswith("enc:"):
            try:
                given = bytes.fromhex(given[4:]).decode("utf-8")
            except ValueError:
                return WRONG_CREDENTIALS
        right = hmac.compare_digest(given.encode("utf-8"), password.encode("utf-8"))
    else:
        return MISSING
    right = hmac.compare_digest(name.encode("utf-8"), user.encode("utf-8")) and right
    return None if right else WRONG_CREDENTIALS


def render(payload: dict, fmt: str, status: str = "ok") -> tuple[bytes, str]:
    """The answer in the envelope Subsonic wraps everything in: XML unless JSON is asked for."""
    head = {"status": status, "version": VERSION, "type": "sonicache", "serverVersion": __version__,
            "openSubsonic": True}
    if fmt == "json":
        return json.dumps({"subsonic-response": {**head, **payload}}, ensure_ascii=False).encode("utf-8"), \
            "application/json; charset=utf-8"
    root = ElementTree.Element("subsonic-response", {"xmlns": NAMESPACE, **_attributes(head)})
    for key, value in payload.items():
        _element(root, key, value)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True), "text/xml; charset=utf-8"


def _attributes(values: dict) -> dict[str, str]:
    return {key: _text(value) for key, value in values.items() if not isinstance(value, (dict, list))}


def _text(value) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def _element(parent, tag: str, value) -> None:
    """A dict as an element with its plain values as attributes; a list as the element repeated."""
    if isinstance(value, list):
        for each in value:
            _element(parent, tag, each)
        return
    if not isinstance(value, dict):  # a plain value in a list: <versions>1</versions>
        ElementTree.SubElement(parent, tag).text = _text(value)
        return
    child = ElementTree.SubElement(parent, tag, _attributes(value))
    for key, inner in value.items():
        if isinstance(inner, (dict, list)):
            _element(child, key, inner)


@dataclass
class Source:
    """What the server serves, as gui.py hands it over."""
    library: Callable[[], tuple[list[dict], list[dict]]]  # the entries (Api.library) and the tracks (Api.tracks)
    playlists: Callable[[], list[dict]]  # {"id", "name", "tracks": [{"path", ...}]}
    favourites: Callable[[], list[str]]  # the entries' paths
    set_favourite: Callable[[str, bool], object]
    cover: Callable[[str], bytes | None]  # an entry's or a file's cover
    listened: Callable[[dict], object]  # a track played to the end on the phone
    plays: Callable[[], dict[str, int]]  # how often each file was listened to
    ffmpeg: Callable[[], str | None]


def _id(prefix: str, key: str) -> str:
    return f"{prefix}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _iso(seconds: float) -> str:
    moment = datetime.datetime.fromtimestamp(seconds or 0, tz=datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _year(text) -> int:
    digits = str(text or "")[:4]
    return int(digits) if digits.isdigit() else 0


def _genres(text: str) -> list[str]:
    """"House; Electronic" is two genres, as the library's own filter splits them."""
    return [genre.strip() for genre in str(text or "").replace("\0", ";").split(";") if genre.strip()]


def _letter(name: str) -> str:
    words = name.split()
    if len(words) > 1 and words[0] in IGNORED_ARTICLES.split():
        name = " ".join(words[1:])
    first = name[:1].upper()
    return first if first.isalpha() else "#"


class Index:
    """The library at one moment, by the ids the phone asks with."""

    def __init__(self):
        self.artists: dict[str, dict] = {}
        self.albums: dict[str, dict] = {}
        self.songs: dict[str, dict] = {}
        self.playlists: dict[str, dict] = {}
        self.starred: dict[str, float] = {}  # album id -> when it is said to have been starred
        self.album_by_path: dict[str, str] = {}

    @classmethod
    def build(cls, items: list[dict], tracks: list[dict], playlists: list[dict], favourites: list[str],
              plays: dict[str, int]) -> "Index":
        index = cls()
        by_entry: dict[str, list[dict]] = {}
        for track in tracks:
            by_entry.setdefault(os.path.normcase(str(track.get("entry") or track["path"])), []).append(track)
        for item in items:
            name = item.get("artist") or t("Без исполнителя")
            artist_id = _id("ar", name.casefold())
            artist = index.artists.setdefault(artist_id, {"id": artist_id, "name": name, "albums": []})
            album_id = _id("al", item["path"])
            album = {"id": album_id, "name": item.get("title") or Path(item["path"]).stem, "artist": name,
                     "artistId": artist_id, "year": _year(item.get("year")), "genres": _genres(item.get("genre")),
                     "created": _iso(item.get("modified") or 0), "modified": item.get("modified") or 0,
                     "path": item["path"], "songs": []}
            own = sorted(by_entry.get(os.path.normcase(item["path"]), []),
                         key=lambda track: (track.get("disc") or 1, track.get("number") or 999,
                                            str(track.get("title") or "").casefold()))
            for track in own:
                song = index._song(track, album)
                album["songs"].append(song["id"])
            album["plays"] = sum(plays.get(index.songs[song]["path"], 0) for song in album["songs"])
            index.albums[album_id] = album
            index.album_by_path[os.path.normcase(item["path"])] = album_id
            artist["albums"].append(album_id)
        now = time.time()
        for path in favourites:
            album_id = index.album_by_path.get(os.path.normcase(path))
            if album_id:
                index.starred[album_id] = now
        by_path = {os.path.normcase(song["path"]): song_id for song_id, song in index.songs.items()}
        for playlist in playlists:
            songs = [by_path[key] for key in (os.path.normcase(str(entry.get("path") or ""))
                                              for entry in playlist.get("tracks") or []) if key in by_path]
            if songs or not str(playlist.get("id")) in ("recent", "top"):
                playlist_id = _id("pl", str(playlist["id"]))
                index.playlists[playlist_id] = {"id": playlist_id, "key": str(playlist["id"]),
                                                "name": playlist.get("name") or "", "songs": songs}
        return index

    def _song(self, track: dict, album: dict) -> dict:
        path = str(track["path"])
        suffix = Path(path).suffix.lower().lstrip(".")
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        song_id = _id("tr", path)
        song = {"id": song_id, "parent": album["id"], "isDir": False,
                "title": track.get("title") or Path(path).stem, "album": album["name"],
                "artist": track.get("artists") or album["artist"], "track": track.get("number") or 0,
                "discNumber": track.get("disc") or 1, "year": _year(track.get("year")) or album["year"],
                "genre": (_genres(track.get("genre")) or [""])[0], "coverArt": album["id"], "size": size,
                "contentType": CONTENT_TYPES.get(suffix, "application/octet-stream"), "suffix": suffix,
                "duration": round(track.get("duration") or 0), "bitRate": track.get("bitrate") or 0,
                "path": f"{album['artist']}/{album['name']}/{Path(path).name}", "isVideo": False,
                "albumId": album["id"], "artistId": album["artistId"], "type": "music",
                "created": _iso(track.get("modified") or 0)}
        if not song["genre"]:
            del song["genre"]
        self.songs[song_id] = {"view": song, "path": path, "genres": _genres(track.get("genre"))}
        return song

    # What the methods answer with

    def song(self, song_id: str) -> dict:
        found = self.songs.get(song_id)
        if found is None:
            raise Failure(NOT_FOUND, "Song not found")
        return found["view"]

    def album(self, album_id: str) -> dict:
        found = self.albums.get(album_id)
        if found is None:
            raise Failure(NOT_FOUND, "Album not found")
        return found

    def album_view(self, album: dict) -> dict:
        """AlbumID3, as getAlbum and the lists of albums give it."""
        view = {"id": album["id"], "name": album["name"], "artist": album["artist"], "artistId": album["artistId"],
                "coverArt": album["id"], "songCount": len(album["songs"]),
                "duration": sum(self.songs[song]["view"]["duration"] for song in album["songs"]),
                "created": album["created"], "playCount": album["plays"]}
        if album["year"]:
            view["year"] = album["year"]
        if album["genres"]:
            view["genre"] = album["genres"][0]
        if album["id"] in self.starred:
            view["starred"] = _iso(self.starred[album["id"]])
        return view

    def album_child(self, album: dict) -> dict:
        """The same album as a folder, for the methods older players browse with."""
        view = {"id": album["id"], "parent": album["artistId"], "isDir": True, "title": album["name"],
                "album": album["name"], "artist": album["artist"], "coverArt": album["id"], "created": album["created"]}
        if album["year"]:
            view["year"] = album["year"]
        if album["genres"]:
            view["genre"] = album["genres"][0]
        if album["id"] in self.starred:
            view["starred"] = _iso(self.starred[album["id"]])
        return view

    def artist_view(self, artist: dict) -> dict:
        return {"id": artist["id"], "name": artist["name"], "albumCount": len(artist["albums"]),
                "coverArt": artist["albums"][0] if artist["albums"] else ""}

    def artist_albums(self, artist: dict) -> list[dict]:
        """Newest first, as an artist's page shows them."""
        return sorted((self.albums[album] for album in artist["albums"]),
                      key=lambda album: (-album["year"], album["name"].casefold()))

    def sorted_artists(self) -> list[dict]:
        return sorted(self.artists.values(), key=lambda artist: artist["name"].casefold())

    def letters(self) -> list[tuple[str, list[dict]]]:
        groups: dict[str, list[dict]] = {}
        for artist in self.sorted_artists():
            groups.setdefault(_letter(artist["name"]), []).append(artist)
        return sorted(groups.items())


class Api:
    """Answers one Subsonic request from what source gives (gui.py's library)."""

    def __init__(self, source: Source | None, password: str, user: str = USER):
        self.source = source
        self.password = password
        self.user = user
        self._index: Index | None = None
        self._built = 0.0
        self._lock = threading.Lock()

    def index(self, fresh: bool = False) -> Index:
        """The library's snapshot, read again when it is a minute old or when asked to."""
        with self._lock:
            now = time.monotonic()
            if fresh or self._index is None or now - self._built > INDEX_FOR:
                items, tracks = self.source.library()
                self._index = Index.build(items, tracks, self.source.playlists(), self.source.favourites(),
                                          self.source.plays())
                self._built = now
            return self._index

    def forget(self) -> None:
        """The next request reads the library again: something in it changed."""
        with self._lock:
            self._index = None

    def handle(self, method: str, path: str, params: dict[str, str], headers: dict[str, str]) -> Response:
        if not path.startswith("/rest/"):
            return Response(404, "text/plain", b"not found")
        name = path.rsplit("/", 1)[-1].removesuffix(".view")
        fmt = "json" if params.get("f") in ("json", "jsonp") else "xml"
        error = check_credentials(params, self.user, self.password)
        if error is not None:
            message = "Required parameter is missing" if error == MISSING else "Wrong username or password"
            return self._failed(Failure(error, message), fmt)
        answer = getattr(self, f"_m_{name}", None)
        if answer is None:
            return self._failed(Failure(GENERIC, f"{name} is not supported"), fmt)
        try:
            result = answer(params)
        except Failure as failure:
            return self._failed(failure, fmt)
        if isinstance(result, Response):
            return result
        body, kind = render(result, fmt)
        return Response(200, kind, body)

    def _failed(self, failure: Failure, fmt: str) -> Response:
        body, kind = render({"error": {"code": failure.code, "message": str(failure)}}, fmt, status="failed")
        return Response(200, kind, body)

    # Methods with nothing of the library in them

    def _m_ping(self, params):
        return {}

    def _m_getLicense(self, params):
        return {"license": {"valid": True, "email": "", "licenseExpires": "2099-12-31T00:00:00"}}

    def _m_getUser(self, params):
        return {"user": {"username": self.user, "email": "", "scrobblingEnabled": True, "adminRole": False,
                         "settingsRole": False, "downloadRole": True, "uploadRole": False, "playlistRole": False,
                         "coverArtRole": True, "commentRole": False, "podcastRole": False, "streamRole": True,
                         "jukeboxRole": False, "shareRole": False, "videoConversionRole": False, "folder": [1]}}

    def _m_getMusicFolders(self, params):
        return {"musicFolders": {"musicFolder": [{"id": 1, "name": "Sonicache"}]}}

    def _m_getOpenSubsonicExtensions(self, params):
        return {"openSubsonicExtensions": [{"name": "formPost", "versions": [1]}]}

    # Browsing by tags (ID3), as the newer players do

    def _m_getArtists(self, params):
        index = self.index()
        return {"artists": {"ignoredArticles": IGNORED_ARTICLES, "index": [
            {"name": letter, "artist": [index.artist_view(artist) for artist in artists]}
            for letter, artists in index.letters()]}}

    def _m_getArtist(self, params):
        index = self.index()
        artist = index.artists.get(required(params, "id"))
        if artist is None:
            raise Failure(NOT_FOUND, "Artist not found")
        return {"artist": {**index.artist_view(artist),
                           "album": [index.album_view(album) for album in index.artist_albums(artist)]}}

    def _m_getAlbum(self, params):
        index = self.index()
        album = index.album(required(params, "id"))
        return {"album": {**index.album_view(album), "song": [index.song(song) for song in album["songs"]]}}

    def _m_getSong(self, params):
        return {"song": self.index().song(required(params, "id"))}

    def _albums(self, params) -> list[dict]:
        """The albums a list of a given type holds, in its order, the page asked for."""
        index = self.index()
        kind = params.get("type") or "alphabeticalByName"
        albums = list(index.albums.values())
        if kind == "newest":
            albums.sort(key=lambda album: -album["modified"])
        elif kind == "random":
            random.shuffle(albums)
        elif kind == "frequent":
            albums = sorted((album for album in albums if album["plays"]), key=lambda album: -album["plays"])
        elif kind == "recent":
            heard = next((playlist for playlist in index.playlists.values() if playlist["key"] == "recent"), None)
            order = []
            for song in heard["songs"] if heard else []:
                album = index.songs[song]["view"]["albumId"]
                if album not in order:
                    order.append(album)
            albums = [index.albums[album] for album in order]
        elif kind == "starred":
            albums = sorted((album for album in albums if album["id"] in index.starred),
                            key=lambda album: album["name"].casefold())
        elif kind == "byYear":
            low, high = _number(params, "fromYear", 0), _number(params, "toYear", 9999)
            albums = sorted((album for album in albums if min(low, high) <= album["year"] <= max(low, high)),
                            key=lambda album: album["year"], reverse=low > high)
        elif kind == "byGenre":
            wanted = required(params, "genre").casefold()
            albums = sorted((album for album in albums if wanted in (genre.casefold() for genre in album["genres"])),
                            key=lambda album: album["name"].casefold())
        elif kind == "alphabeticalByArtist":
            albums.sort(key=lambda album: (album["artist"].casefold(), album["name"].casefold()))
        else:  # alphabeticalByName, and anything a newer player may ask for
            albums.sort(key=lambda album: album["name"].casefold())
        offset, size = _number(params, "offset", 0), min(500, _number(params, "size", 10))
        return albums[offset:offset + size]

    def _m_getAlbumList2(self, params):
        index = self.index()
        return {"albumList2": {"album": [index.album_view(album) for album in self._albums(params)]}}

    def _m_getAlbumList(self, params):
        index = self.index()
        return {"albumList": {"album": [index.album_child(album) for album in self._albums(params)]}}

    def _songs_matching(self, params) -> list[dict]:
        index = self.index()
        genre = (params.get("genre") or "").casefold()
        low, high = _number(params, "fromYear", 0), _number(params, "toYear", 9999)
        return [song["view"] for song in index.songs.values()
                if (not genre or genre in (each.casefold() for each in song["genres"]))
                and (not (params.get("fromYear") or params.get("toYear")) or low <= song["view"]["year"] <= high)]

    def _m_getRandomSongs(self, params):
        songs = self._songs_matching(params)
        random.shuffle(songs)
        return {"randomSongs": {"song": songs[:min(500, _number(params, "size", 10))]}}

    def _m_getGenres(self, params):
        index = self.index()
        counts: dict[str, list] = {}
        for song in index.songs.values():
            for genre in song["genres"]:
                counts.setdefault(genre.casefold(), [genre, 0, set()])
                counts[genre.casefold()][1] += 1
                counts[genre.casefold()][2].add(song["view"]["albumId"])
        return {"genres": {"genre": [{"value": name, "songCount": songs, "albumCount": len(albums)}
                                     for name, songs, albums in sorted(counts.values(),
                                                                       key=lambda entry: entry[0].casefold())]}}

    def _m_getSongsByGenre(self, params):
        required(params, "genre")
        songs = self._songs_matching({"genre": params["genre"]})
        offset = _number(params, "offset", 0)
        return {"songsByGenre": {"song": songs[offset:offset + min(500, _number(params, "count", 10))]}}

    def _search(self, params) -> tuple[list[dict], list[dict], list[dict]]:
        """Artists, albums and songs whose names hold the words; everything for an empty query,
        which is how some players copy the whole library to the phone."""
        index = self.index()
        needle = (params.get("query") or "").strip().strip('"').casefold()

        def page(found: list, kind: str) -> list:
            offset = _number(params, f"{kind}Offset", 0)
            return found[offset:offset + min(5000, _number(params, f"{kind}Count", 20))]
        artists = [artist for artist in index.sorted_artists() if needle in artist["name"].casefold()]
        albums = sorted((album for album in index.albums.values() if needle in album["name"].casefold()),
                        key=lambda album: album["name"].casefold())
        songs = sorted((song["view"] for song in index.songs.values() if needle in song["view"]["title"].casefold()),
                       key=lambda song: (song["artist"].casefold(), song["album"].casefold(), song["discNumber"],
                                         song["track"]))
        return page(artists, "artist"), page(albums, "album"), page(songs, "song")

    def _m_search3(self, params):
        index = self.index()
        artists, albums, songs = self._search(params)
        return {"searchResult3": {"artist": [index.artist_view(artist) for artist in artists],
                                  "album": [index.album_view(album) for album in albums], "song": songs}}

    def _m_search2(self, params):
        index = self.index()
        artists, albums, songs = self._search(params)
        return {"searchResult2": {"artist": [{"id": artist["id"], "name": artist["name"]} for artist in artists],
                                  "album": [index.album_child(album) for album in albums], "song": songs}}

    def _m_getPlaylists(self, params):
        index = self.index()
        return {"playlists": {"playlist": [self._playlist_view(index, playlist)
                                           for playlist in index.playlists.values()]}}

    def _m_getPlaylist(self, params):
        index = self.index()
        playlist = index.playlists.get(required(params, "id"))
        if playlist is None:
            raise Failure(NOT_FOUND, "Playlist not found")
        return {"playlist": {**self._playlist_view(index, playlist),
                             "entry": [index.song(song) for song in playlist["songs"]]}}

    def _playlist_view(self, index: Index, playlist: dict) -> dict:
        songs = [index.songs[song]["view"] for song in playlist["songs"]]
        view = {"id": playlist["id"], "name": playlist["name"], "songCount": len(songs),
                "duration": sum(song["duration"] for song in songs), "public": False, "owner": self.user,
                "created": _iso(0), "changed": _iso(0)}
        if songs:
            view["coverArt"] = songs[0]["albumId"]
        return view

    def _m_getStarred2(self, params):
        index = self.index()
        albums = sorted((index.albums[album] for album in index.starred), key=lambda album: album["name"].casefold())
        return {"starred2": {"artist": [], "album": [index.album_view(album) for album in albums], "song": []}}

    def _m_getStarred(self, params):
        index = self.index()
        albums = sorted((index.albums[album] for album in index.starred), key=lambda album: album["name"].casefold())
        return {"starred": {"artist": [], "album": [index.album_child(album) for album in albums], "song": []}}

    # Browsing by folders, as the older players do: an artist holds albums, an album songs

    def _m_getIndexes(self, params):
        index = self.index()
        newest = max((album["modified"] for album in index.albums.values()), default=0)
        return {"indexes": {"lastModified": int(newest * 1000), "ignoredArticles": IGNORED_ARTICLES, "index": [
            {"name": letter, "artist": [{"id": artist["id"], "name": artist["name"]} for artist in artists]}
            for letter, artists in index.letters()]}}

    def _m_getMusicDirectory(self, params):
        index = self.index()
        folder = required(params, "id")
        artist = index.artists.get(folder)
        if artist is not None:
            return {"directory": {"id": artist["id"], "name": artist["name"],
                                  "child": [index.album_child(album) for album in index.artist_albums(artist)]}}
        album = index.album(folder)
        return {"directory": {"id": album["id"], "parent": album["artistId"], "name": album["name"],
                              "child": [index.song(song) for song in album["songs"]]}}

    # What the program has none of

    def _m_getNowPlaying(self, params):
        return {"nowPlaying": {}}

    def _m_getPodcasts(self, params):
        return {"podcasts": {}}

    def _m_getInternetRadioStations(self, params):
        return {"internetRadioStations": {}}

    def _m_getBookmarks(self, params):
        return {"bookmarks": {}}

    def _m_getShares(self, params):
        return {"shares": {}}

    def _m_getPlayQueue(self, params):
        return {}

    def _m_savePlayQueue(self, params):
        return {}

    def _m_getScanStatus(self, params):
        return {"scanStatus": {"scanning": False, "count": len(self.index().songs)}}


def _number(params: dict[str, str], name: str, default: int) -> int:
    try:
        return max(0, int(params.get(name) or default))
    except ValueError:
        return default


def required(params: dict[str, str], name: str) -> str:
    value = params.get(name)
    if not value:
        raise Failure(MISSING, f"Required parameter is missing: {name}")
    return value
