"""What the player plays, shown on the person's Discord profile.

Discord's desktop client listens on the computer itself for programs that want
to say what the person is doing (Rich Presence): a named pipe on Windows, a
socket in the temporary folder elsewhere. A program says hello with its
application's number, then sends SET_ACTIVITY with the activity as JSON. Each
message is framed by two little-endian numbers, the kind of message and the
length of what follows. No account or token is involved, and when the program
ends, Discord takes the activity down by itself.

The activity reads "Listening to" the track, with its artist, the album's
cover and a bar that Discord moves along by itself from the times it was
given. Discord shows only pictures on the web, so the cover is the album's on
Deezer or in Apple's catalogue, found by its artist and title; for a record
neither has, the release on Deezer that has the same song, then a photo of
the artist, and the program's logo when there is nothing at all. Discord
takes five updates in twenty seconds, so they are sent from a thread of their
own, the latest one only, a moment apart.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .logs import log

CLIENT_ID = "1552340155522875503"  # the Trackhound application on Discord
HOMEPAGE = "https://github.com/mojave333/trackhound"
LOGO = "https://raw.githubusercontent.com/mojave333/trackhound/main/docs/logo.png"

_HANDSHAKE, _FRAME, _CLOSE, _PING, _PONG = 0, 1, 2, 3, 4
_LISTENING = 2  # the activity's type: "Listening to", as music players have it
_SHOW_STATE = 1  # the status line in the member list names the artist, not the program or the track
_GAP = 2.0  # seconds between two updates
_RETRY = 15.0  # seconds before looking for Discord again
_LIMIT = 128  # Discord's longest text


class Presence:
    """Keeps Discord told what plays. show() is called from anywhere and
    returns at once; the thread connects, finds the cover and sends."""

    def __init__(self, cover_for: Callable[[str, str, str], str] | None = None):
        self._cover_for = cover_for  # (artist, album, title) to a picture's address
        self._covers: dict[tuple[str, str], str] = {}
        self._wanted: dict | None = None
        self._sent: dict | None = None
        self._wake = threading.Event()
        self._closed = False
        self._pipe: _Pipe | None = None
        self._thread: threading.Thread | None = None

    def show(self, track: dict | None) -> None:
        """The track that plays, as the player has it with "start" and "duration"
        in seconds; None takes the activity down."""
        self._wanted = dict(track) if track else None
        if self._thread is None and track:
            self._thread = threading.Thread(target=self._run, name="discord", daemon=True)
            self._thread.start()
        self._wake.set()

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        if self._pipe is not None:
            self._pipe.close()

    def _run(self) -> None:
        while not self._closed:
            self._wake.wait(None if self._pipe is not None or self._wanted is None else _RETRY)
            self._wake.clear()
            if self._closed:
                break
            wanted = self._wanted
            if wanted == self._sent:
                continue
            if self._pipe is None:
                if wanted is None:  # nothing shown, nothing to take down
                    self._sent = None
                    continue
                self._pipe = _connect()
                if self._pipe is None:
                    continue  # Discord is not running; looked for again later
            try:
                self._pipe.request("SET_ACTIVITY", {"pid": os.getpid(),
                                                    "activity": self._activity(wanted) if wanted else None})
                self._sent = wanted
            except (OSError, ValueError) as e:
                log.debug("Discord: связь прервалась: %s", e)
                self._pipe.close()
                self._pipe = None
                self._wake.set()  # tried again at once, then every little while
                time.sleep(1)
                continue
            time.sleep(_GAP)

    def _activity(self, track: dict) -> dict:
        artist = str(track.get("album_artist") or track.get("artists") or "")
        return activity(track, self._cover(artist, str(track.get("album") or ""), str(track.get("title") or "")))

    def _cover(self, artist: str, album: str, title: str) -> str:
        # One look for an album; a track without one is looked for by itself
        key = (artist.casefold(), (album or f"track:{title}").casefold())
        if key not in self._covers:
            cover = ""
            if (album or title) and self._cover_for is not None:
                try:
                    cover = self._cover_for(artist, album, title)
                except Exception as e:  # a picture is never worth a status left unsaid
                    log.debug("Discord: обложка «%s» не нашлась: %s", album or title, e)
                    return ""  # asked again with the next track
            self._covers[key] = cover
        return self._covers[key]


def activity(track: dict, cover: str = "") -> dict:
    """The activity Discord shows for a track: its title, artist and album,
    the cover or the logo, and its start and end for the moving bar."""
    title = _text(track.get("title")) or "Trackhound"
    shown = {
        "type": _LISTENING,
        "status_display_type": _SHOW_STATE,
        "details": title,
        "assets": {"large_image": cover or LOGO},
        "buttons": [{"label": "Trackhound", "url": HOMEPAGE}],
    }
    artists = _text(track.get("artists") or track.get("album_artist"))
    if artists:
        shown["state"] = artists
    album = _text(track.get("album"))
    if album:
        shown["assets"]["large_text"] = album
    try:
        start, duration = float(track.get("start") or 0), float(track.get("duration") or 0)
    except (TypeError, ValueError):
        start = duration = 0
    if start > 0 and duration > 0:
        shown["timestamps"] = {"start": int(start * 1000), "end": int((start + duration) * 1000)}
    return shown


def _text(value) -> str:
    """Discord wants two to 128 characters: one is made two with a blank
    that shows as nothing, a long one is cut with an ellipsis."""
    text = " ".join(str(value or "").split())
    if len(text) > _LIMIT:
        text = text[:_LIMIT - 1].rstrip() + "…"
    if len(text) == 1:
        text += "\u2800"
    return text


# The connection

class _Pipe:
    def __init__(self, write: Callable[[bytes], None], read: Callable[[int], bytes], close: Callable[[], None]):
        self._write, self._read, self._close = write, read, close

    def send(self, op: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self._write(struct.pack("<II", op, len(data)) + data)

    def receive(self) -> tuple[int, dict]:
        op, length = struct.unpack("<II", self._exactly(8))
        data = json.loads(self._exactly(length) or b"{}")
        return op, data if isinstance(data, dict) else {}

    def _exactly(self, size: int) -> bytes:
        chunks, left = [], size
        while left:
            chunk = self._read(left)
            if not chunk:
                raise OSError("Discord closed the connection")
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)

    def answer(self, nonce: str) -> dict:
        """Discord's answer to one request: a ping on the way is answered."""
        while True:
            op, data = self.receive()
            if op == _PING:
                self.send(_PONG, data)
            elif op == _CLOSE:
                raise OSError(f"Discord closed the connection: {data.get('message') or data}")
            elif data.get("nonce") == nonce:
                if data.get("evt") == "ERROR":
                    raise ValueError(f"Discord: {(data.get('data') or {}).get('message') or data}")
                return data

    def request(self, command: str, args: dict) -> dict:
        nonce = uuid.uuid4().hex
        self.send(_FRAME, {"cmd": command, "args": args, "nonce": nonce})
        return self.answer(nonce)

    def hello(self) -> None:
        self.send(_HANDSHAKE, {"v": 1, "client_id": CLIENT_ID})
        op, data = self.receive()
        if op == _CLOSE or data.get("evt") != "READY":
            raise OSError(f"Discord did not accept the program: {data.get('message') or data}")

    def close(self) -> None:
        try:
            self._close()
        except OSError:
            pass


def _connect() -> _Pipe | None:
    """The first of Discord's ten places that answers, or None without Discord."""
    for place in _places():
        try:
            pipe = _open(place)
        except OSError:
            continue
        try:
            pipe.hello()
            log.info("Discord: подключено (%s)", place)
            return pipe
        except (OSError, ValueError) as e:
            log.debug("Discord: %s не ответил: %s", place, e)
            pipe.close()
    return None


def _places() -> list[str]:
    if sys.platform == "win32":
        return [rf"\\?\pipe\discord-ipc-{number}" for number in range(10)]
    # The temporary folders, and where the Flatpak and Snap builds keep theirs
    roots = [os.environ.get(name) for name in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")] + ["/tmp"]
    folders = []
    for root in dict.fromkeys(filter(None, roots)):
        folders += [Path(root), Path(root, "app", "com.discordapp.Discord"), Path(root, "snap.discord"),
                    Path(root, ".flatpak", "dev.vencord.Vesktop", "xdg-run")]
    return [str(folder / f"discord-ipc-{number}") for number in range(10) for folder in folders]


def _open(place: str) -> _Pipe:
    if sys.platform == "win32":
        handle = open(place, "r+b", buffering=0)  # noqa: SIM115 - held as long as Discord answers
        return _Pipe(handle.write, handle.read, handle.close)
    if not os.path.exists(place):
        raise FileNotFoundError(place)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(10)
    try:
        connection.connect(place)
    except OSError:
        connection.close()
        raise
    return _Pipe(connection.sendall, connection.recv, connection.close)
