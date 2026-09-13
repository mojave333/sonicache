"""Command line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .downloader import DEFAULT_OUTPUT_DIR, FORMATS, Downloader, Options


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trackhound",
        description="Скачивание альбомов, синглов и треков по ссылке из Spotify, Apple Music, "
                    "YouTube, SoundCloud, Last.fm и сайтов вроде Bandcamp.",
    )
    parser.add_argument("links", nargs="+", help="ссылки на альбомы, плейлисты или треки")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=f"папка для музыки (по умолчанию {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("-f", "--format", choices=FORMATS, default="m4a",
                        help="формат файлов (по умолчанию m4a — без перекодирования)")
    parser.add_argument("-t", "--threads", type=int, default=3,
                        help="сколько треков качать одновременно (по умолчанию 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать найденные совпадения, ничего не скачивать")
    parser.add_argument("--cookies-from-browser", default="", metavar="БРАУЗЕР",
                        help="брать cookies из браузера (chrome, edge, firefox…): нужно для видео "
                             "с возрастным ограничением")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    options = Options(args.output.expanduser(), args.format, max(1, args.threads), args.dry_run,
                      args.cookies_from_browser)
    downloader = Downloader(options, log=lambda message: print(message, flush=True))
    for problem in downloader.environment_problems():
        print(f"⚠ {problem}", file=sys.stderr)

    failed = 0
    try:
        for link in args.links:
            try:
                report = downloader.download_link(link)
            except Exception as e:
                print(f"✗ {e}", file=sys.stderr)
                failed += 1
                continue
            print(report.summary(args.dry_run), end="\n\n")
            failed += len(report.failed)
    except KeyboardInterrupt:
        print("\nОстановлено.", file=sys.stderr)
        return 130
    return 1 if failed else 0
