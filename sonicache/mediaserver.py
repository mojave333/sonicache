"""The address on this computer the page reads covers and audio from, so that a cover is a URL
and not megabytes of base64, and the player can seek in a file.
"""

from __future__ import annotations

import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .libraryscan import _cover_bytes

class _CoverServer:
    """Serves the library's covers to the window on 127.0.0.1.

    Only a path the window has asked a cover for gets an address, and the
    address names it by a random token, so nothing else on the computer can
    read files through it. The server starts with the first cover asked for.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._paths: dict[str, str] = {}  # token → path
        self._tokens: dict[str, str] = {}  # (kind, path) → token
        self._port = 0

    def address(self, path: str) -> str:
        return self._address("cover", path)

    def audio_address(self, path: str) -> str:
        """The player's address for a music file, streamed with seeking."""
        return self._address("audio", path)

    def _address(self, kind: str, path: str) -> str:
        with self._lock:
            if not self._port:
                self._port = self._start()
            token = self._tokens.get(f"{kind}:{path}")
            if token is None:
                token = secrets.token_urlsafe(16)
                self._tokens[f"{kind}:{path}"] = token
                self._paths[token] = f"{kind}:{path}"
            return f"http://127.0.0.1:{self._port}/{kind}/{token}"

    def path(self, token: str, kind: str = "cover") -> str | None:
        with self._lock:
            known = self._paths.get(token) or ""
        return known.removeprefix(f"{kind}:") if known.startswith(f"{kind}:") else None

    def _start(self) -> int:
        covers = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # one connection for a whole screen of covers

            def do_GET(self):
                route = urllib.parse.urlsplit(self.path).path
                if route.startswith("/audio/"):
                    path = covers.path(route.removeprefix("/audio/"), "audio")
                    try:
                        if path:
                            _send_audio(self, Path(path))
                        else:
                            self.send_error(404)
                    except (ConnectionError, OSError):
                        pass  # the player dropped the request to seek elsewhere
                    return
                path = covers.path(route.removeprefix("/cover/")) if route.startswith("/cover/") else None
                data = _cover_bytes(Path(path)) if path else None
                if not data:
                    self.send_error(404)
                    return
                self.send_response(200)
                kind = "png" if data.startswith(b"\x89PNG") else "webp" if data.startswith(b"RIFF") else "jpeg"
                self.send_header("Content-Type", f"image/{kind}")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=86400")
                # The album page reads the cover's colour through a canvas, which needs this
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self):
                # Asked before a request whose Range the browser does not let through unasked
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET")
                self.send_header("Access-Control-Allow-Headers", "Range")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format, *args):
                pass  # a line for every cover would bury the log

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server.server_port

AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".opus": "audio/ogg"}

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")

def _send_audio(handler: BaseHTTPRequestHandler, path: Path) -> None:
    """A music file, whole or the byte range asked for: the player seeks by
    asking for the part of the file it jumps to."""
    try:
        size = path.stat().st_size
        source = path.open("rb")
    except OSError:
        handler.send_error(404)
        return
    with source:
        start, end = 0, size - 1
        asked = _RANGE_RE.match(handler.headers.get("Range") or "")
        if asked and (asked[1] or asked[2]):
            if asked[1]:
                start = int(asked[1])
                end = min(int(asked[2]), size - 1) if asked[2] else size - 1
            else:  # "bytes=-500": the last 500 bytes
                start = max(0, size - int(asked[2]))
            if start > end:
                handler.send_response(416)
                handler.send_header("Access-Control-Allow-Origin", "*")
                handler.send_header("Content-Range", f"bytes */{size}")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return
        handler.send_response(206 if asked else 200)
        # The player's sound goes through Web Audio (the equaliser, the
        # crossfade), which hears a file from another address only when the
        # server allows it; without this it plays silence
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Content-Type", AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream"))
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Length", str(end - start + 1))
        if asked:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        handler.end_headers()
        source.seek(start)
        left = end - start + 1
        while left > 0:
            chunk = source.read(min(256 * 1024, left))
            if not chunk:
                break
            handler.wfile.write(chunk)
            left -= len(chunk)
