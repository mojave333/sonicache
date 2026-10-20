"""The preview stand: the window's page in a browser, with a stub in place of the program behind it.

    python scripts/preview.py            # builds build/preview and serves it on http://127.0.0.1:8765
    python scripts/preview.py --build    # only builds it

The page comes from sonicache/web as it is, so the stand never falls behind the program. What the program would
answer comes from scripts/preview/stub.js; scripts/preview/scenarios.js adds the scenes the screenshots are taken
from (?scene=library, ?scene=player and the rest). The cover and the track are made up, so nothing here belongs to
anyone else. Files put into build/preview by hand stay there.
"""

import argparse
import functools
import http.server
import math
import shutil
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "sonicache" / "web"
STUB = ROOT / "scripts" / "preview"
OUT = ROOT / "build" / "preview"
PORT = 8765


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WEB, OUT, dirs_exist_ok=True)
    for name in ("stub.js", "scenarios.js", "cover.svg"):
        shutil.copy2(STUB / name, OUT / name)
    # The stub and the scenes go in after the page's own scripts, which they call into
    page = (WEB / "index.html").read_text(encoding="utf-8")
    end = page.rindex("</script>") + len("</script>")
    indent = page[page.rindex("\n", 0, end) + 1:].split("<", 1)[0]
    extra = "".join(f'\n{indent}<script src="{name}"></script>' for name in ("stub.js", "scenarios.js"))
    (OUT / "index.html").write_text(page[:end] + extra + page[end:], encoding="utf-8", newline="")
    track = OUT / "track.wav"
    if not track.exists():
        write_tone(track)
    return OUT


def write_tone(path, seconds=30, rate=8000):
    """A quiet hum for the player to play: half a minute of 220 Hz, fading in and out."""
    frames = bytearray()
    for i in range(seconds * rate):
        fade = min(1.0, i / rate, (seconds * rate - i) / rate)
        frames += struct.pack("<h", int(3000 * fade * math.sin(2 * math.pi * 220 * i / rate)))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the stand with no caching, so a page reloaded after a rebuild is the new one."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--build", action="store_true", help="only build build/preview, serve nothing")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    out = build()
    print(f"built {out.relative_to(ROOT)}")
    if args.build:
        return
    handler = functools.partial(Handler, directory=str(out))
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"http://127.0.0.1:{args.port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    sys.exit(main())
