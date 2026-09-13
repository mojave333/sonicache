"""Command line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .downloader import DEFAULT_OUTPUT_DIR, FORMATS, Downloader, Options


def main(argv: list[str] | None = None) -> int:
    # Every message here is in Russian, which a console or a redirect on an
    # English Windows cannot encode; --help must not die over that.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        prog="trackhound",
        description="Скачивание альбомов, синглов и треков по ссылке из Spotify, Apple Music, "
                    "YouTube, SoundCloud, Last.fm и сайтов вроде Bandcamp.",
    )
    parser.add_argument("links", nargs="*", help="ссылки на альбомы, плейлисты или треки")
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
    parser.add_argument("--check", action="store_true",
                        help="показать версию и какие ffmpeg и Deno нашлись, ничего не скачивая")
    args = parser.parse_args(argv)

    options = Options(args.output.expanduser(), args.format, max(1, args.threads), args.dry_run,
                      args.cookies_from_browser)
    downloader = Downloader(options, log=lambda message: print(message, flush=True))

    if args.check:
        print(f"Trackhound {__version__}")
        print(f"ffmpeg: {downloader.ffmpeg or 'не найден'}")
        for name in ("deno", "node"):
            print(f"{name}: {downloader.js_runtimes.get(name, {}).get('path', 'не найден')}")
        print(f"папка для музыки: {options.output_dir}")
        for problem in downloader.environment_problems():
            print(f"⚠ {problem}")
        return 0
    if not args.links:
        parser.error("укажите хотя бы одну ссылку (или --check, чтобы проверить установку)")

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
