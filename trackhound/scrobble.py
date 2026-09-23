"""Listens sent to Last.fm and ListenBrainz ("scrobbling").

ListenBrainz takes a user token, which the person copies from their profile
page. Last.fm is asked through the program's own API account: the program gets
a token, opens Last.fm's page where the person allows it, and exchanges the
token for a session that does not expire. The keys are kept in scrobble.json
beside the library's cache, never in the settings the window sees.

A listen that could not be sent (no network, a service down) waits in the
same file and goes with the next one, up to a few thousand of them. "Now
playing" is sent as a track starts and is not kept: it is only true for a
few minutes.
"""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__, logs

# The program's Last.fm API account (last.fm/api/account/create). Desktop
# programs cannot hide the secret: it only proves which program asks, and
# every open source scrobbler ships its own.
LASTFM_KEY = "e3778254bdcd50832fb94f7ac8528017"
LASTFM_SECRET = "10cad8159ab797b937e53b928c931ccf"
LASTFM_API = "https://ws.audioscrobbler.com/2.0/"
LASTFM_AUTH = "https://www.last.fm/api/auth/"
LISTENBRAINZ_API = "https://api.listenbrainz.org/1/"

SERVICES = ("lastfm", "listenbrainz")
PENDING_LIMIT = 5000
BATCH = 50  # Last.fm takes fifty listens a request; ListenBrainz more, but fifty is plenty
_TIMEOUT = 15


class Rejected(Exception):
    """The service turned the key down: the account is disconnected."""


class Unavailable(Exception):
    """The service could not be reached or failed: tried again later."""


class Invalid(Exception):
    """The service refused what was sent as malformed: sending it again would not help."""


def accounts_file() -> Path:
    return logs.data_dir() / "scrobble.json"


def lastfm_ready() -> bool:
    return bool(LASTFM_KEY and LASTFM_SECRET)


class Scrobbler:
    """Holds the accounts and sends to them from a thread of its own."""

    def __init__(self, opener: Callable = None):
        self._open = opener or urllib.request.urlopen
        self._lock = threading.Lock()
        self._data = self._load()
        self._wake = threading.Event()
        self._now: dict | None = None
        self._thread: threading.Thread | None = None
        self._pending_token = ""  # a Last.fm token waiting for the person to allow it

    # What the window shows

    def accounts(self) -> dict:
        with self._lock:
            return {
                "lastfm": self._data.get("lastfm", {}).get("name", ""),
                "listenbrainz": self._data.get("listenbrainz", {}).get("name", ""),
                "lastfm_ready": lastfm_ready(),
                "waiting": sum(len(self._data.get("pending", {}).get(service, [])) for service in SERVICES),
            }

    # Connecting

    def connect_listenbrainz(self, token: str) -> str:
        """Checks the token and keeps it; returns the user's name."""
        token = str(token or "").strip()
        answer = self._listenbrainz("GET", "validate-token", token)
        if not answer.get("valid"):
            raise Rejected(answer.get("message") or "invalid token")
        name = str(answer.get("user_name") or "")
        with self._lock:
            self._data["listenbrainz"] = {"token": token, "name": name}
            self._data.setdefault("pending", {})["listenbrainz"] = []
            self._save()
        return name

    def lastfm_start(self) -> str:
        """A token for the person to allow on Last.fm; returns the page to open."""
        if not lastfm_ready():
            raise Rejected("no Last.fm API key")
        answer = self._lastfm({"method": "auth.getToken"}, sign=True)
        self._pending_token = str(answer.get("token") or "")
        if not self._pending_token:
            raise Unavailable("Last.fm gave no token")
        return f"{LASTFM_AUTH}?{urllib.parse.urlencode({'api_key': LASTFM_KEY, 'token': self._pending_token})}"

    def lastfm_finish(self) -> str:
        """The name once the person has allowed the program, "" while they have not."""
        if not self._pending_token:
            return ""
        try:
            answer = self._lastfm({"method": "auth.getSession", "token": self._pending_token}, sign=True)
        except Rejected:
            return ""  # error 14: not allowed yet
        session = answer.get("session") or {}
        if not session.get("key"):
            return ""
        self._pending_token = ""
        name = str(session.get("name") or "")
        with self._lock:
            self._data["lastfm"] = {"key": str(session["key"]), "name": name}
            self._data.setdefault("pending", {})["lastfm"] = []
            self._save()
        return name

    def disconnect(self, service: str) -> None:
        with self._lock:
            self._data.pop(service, None)
            self._data.get("pending", {}).pop(service, None)
            self._save()

    # Listening

    def now_playing(self, track: dict | None) -> None:
        self._now = dict(track) if track else None
        if track and self._connected():
            self._start()
            self._wake.set()

    def scrobble(self, listen: dict) -> None:
        """Queues one listen for every connected service and sends it."""
        with self._lock:
            connected = [service for service in SERVICES if service in self._data]
            if not connected or not _listen(listen)["artist"] or not _listen(listen)["title"]:
                return  # neither service takes a listen without an artist and a title
            pending = self._data.setdefault("pending", {})
            for service in connected:
                pending[service] = (pending.get(service, []) + [_listen(listen)])[-PENDING_LIMIT:]
            self._save()
        self._start()
        self._wake.set()

    def _connected(self) -> bool:
        with self._lock:
            return any(service in self._data for service in SERVICES)

    def _start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="scrobble", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        backoff = 0
        while True:
            self._wake.wait(backoff or None)
            self._wake.clear()
            now, self._now = self._now, None
            failed = False
            for service in SERVICES:
                try:
                    if now:
                        try:
                            self._send_now(service, now)
                        except Invalid as e:
                            logs.log.info("%s: «сейчас играет» не принято: %s", service, e)
                    self._flush(service)
                except Rejected as e:
                    logs.log.warning("%s: ключ отклонён, аккаунт отключён: %s", service, e)
                    self.disconnect(service)
                except Unavailable as e:
                    logs.log.info("%s: не отправлено, повторю позже: %s", service, e)
                    failed = True
            # Unsent listens are tried again after a minute, then less and less often
            backoff = min(max(60, backoff * 2), 3600) if failed else 0

    def _send_now(self, service: str, track: dict) -> None:
        with self._lock:
            account = dict(self._data.get(service) or {})
        if not account:
            return
        listen = _listen({**track, "time": 0})
        if service == "lastfm":
            self._lastfm({"method": "track.updateNowPlaying", "sk": account["key"], **_lastfm_fields(listen)},
                         sign=True, post=True)
        else:
            self._listenbrainz("POST", "submit-listens", account["token"],
                               {"listen_type": "playing_now", "payload": [_listenbrainz_listen(listen, False)]})

    def _flush(self, service: str) -> None:
        while True:
            with self._lock:
                account = dict(self._data.get(service) or {})
                batch = list(self._data.get("pending", {}).get(service, [])[:BATCH])
            if not account or not batch:
                return
            try:
                if service == "lastfm":
                    fields = {"method": "track.scrobble", "sk": account["key"]}
                    for number, listen in enumerate(batch):
                        fields.update({f"{key}[{number}]": value for key, value in _lastfm_fields(listen).items()})
                        fields[f"timestamp[{number}]"] = str(listen["time"])
                    self._lastfm(fields, sign=True, post=True)
                else:
                    self._listenbrainz("POST", "submit-listens", account["token"], {
                        "listen_type": "single" if len(batch) == 1 else "import",
                        "payload": [_listenbrainz_listen(listen, True) for listen in batch]})
            except Invalid as e:
                logs.log.warning("%s: не принял %d прослушиваний, они отброшены: %s", service, len(batch), e)
            with self._lock:
                pending = self._data.get("pending", {}).get(service, [])
                del pending[:len(batch)]
                self._save()

    # The two services

    def _lastfm(self, fields: dict, sign: bool = False, post: bool = False) -> dict:
        fields = {**fields, "api_key": LASTFM_KEY}
        if sign:
            fields["api_sig"] = _signature(fields)
        fields["format"] = "json"
        body = urllib.parse.urlencode(fields).encode()
        request = (urllib.request.Request(LASTFM_API, data=body) if post
                   else urllib.request.Request(f"{LASTFM_API}?{body.decode()}"))
        answer = self._ask(request)
        error = answer.get("error")
        if error in (4, 9, 10, 14, 26):  # the key, the session or the token is not (or not yet) good
            raise Rejected(answer.get("message") or error)
        if error:
            raise Unavailable(answer.get("message") or error)
        return answer

    def _listenbrainz(self, method: str, path: str, token: str, payload: dict | None = None) -> dict:
        request = urllib.request.Request(
            LISTENBRAINZ_API + path, method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Token {token}", "Content-Type": "application/json"})
        return self._ask(request)

    def _ask(self, request: urllib.request.Request) -> dict:
        request.add_header("User-Agent", f"Trackhound/{__version__}")
        try:
            with self._open(request, timeout=_TIMEOUT) as response:
                data = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read() or b"{}")
            except ValueError:
                data = {}
            if isinstance(data, dict) and isinstance(data.get("error"), int):
                return data  # Last.fm's numbered error, which _lastfm reads
            said = (data.get("error") if isinstance(data, dict) else "") or e.reason
            if e.code in (401, 403):
                raise Rejected(said) from e
            if e.code == 400:
                raise Invalid(said) from e
            raise Unavailable(f"HTTP {e.code}: {said}") from e
        except (OSError, ValueError) as e:
            raise Unavailable(str(e)) from e
        return data if isinstance(data, dict) else {}

    # The file

    def _load(self) -> dict:
        try:
            data = json.loads(accounts_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        path = accounts_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            logs.log.warning("не записал %s: %s", path.name, e)


def _listen(track: dict) -> dict:
    return {"artist": str(track.get("artists") or track.get("album_artist") or ""),
            "title": str(track.get("title") or ""), "album": str(track.get("album") or ""),
            "album_artist": str(track.get("album_artist") or ""),
            "duration": int(track.get("duration") or 0), "time": int(track.get("time") or 0)}


def _lastfm_fields(listen: dict) -> dict:
    fields = {"artist": listen["artist"], "track": listen["title"]}
    if listen["album"]:
        fields["album"] = listen["album"]
    if listen["album_artist"] and listen["album_artist"] != listen["artist"]:
        fields["albumArtist"] = listen["album_artist"]
    if listen["duration"]:
        fields["duration"] = str(listen["duration"])
    return fields


def _listenbrainz_listen(listen: dict, dated: bool) -> dict:
    info = {"media_player": "Trackhound", "submission_client": "Trackhound",
            "submission_client_version": __version__}
    if listen["duration"]:
        info["duration_ms"] = listen["duration"] * 1000
    metadata = {"artist_name": listen["artist"], "track_name": listen["title"], "additional_info": info}
    if listen["album"]:
        metadata["release_name"] = listen["album"]
    shown = {"track_metadata": metadata}
    if dated:
        shown["listened_at"] = listen["time"]
    return shown


def _signature(fields: dict) -> str:
    """Last.fm's api_sig: every field but format, sorted, name then value, and the secret."""
    text = "".join(f"{name}{fields[name]}" for name in sorted(fields) if name not in ("format", "callback"))
    return hashlib.md5((text + LASTFM_SECRET).encode()).hexdigest()
