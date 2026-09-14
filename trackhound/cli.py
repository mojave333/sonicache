"""Command line interface."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import __version__, logs
from .downloader import (DEFAULT_OUTPUT_DIR, FOLDER_NAMES, FORMATS, TRACK_NAMES, Downloader,
                         Options, use_proxy)


def _rate(value: str, parser: argparse.ArgumentParser) -> int:
    """"500K", "2M", "1.5m" — bytes per second; empty means no limit."""
    if not value:
        return 0
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*([kmg]?)b?/?s?\s*", value, re.I)
    if not m:
        parser.error(f"непонятная скорость: {value}. Примеры: 500K, 2M")
    scale = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()]
    return int(float(m.group(1).replace(",", ".")) * scale)


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
    parser.add_argument("links", nargs="*",
                        help="ссылки на альбомы, плейлисты или треки — либо название: «Исполнитель - Альбом»")
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
    parser.add_argument("--names", choices=TRACK_NAMES, default="auto",
                        help="имена файлов: auto — исполнитель только у гостей, artist — всегда, "
                             "title — только номер и название")
    parser.add_argument("--folders", choices=FOLDER_NAMES, default="flat",
                        help="папки альбомов: flat — «Исполнитель - Альбом (Год)», "
                             "nested — «Исполнитель\Альбом (Год)», album — «Альбом (Год)»")
    parser.add_argument("--limit-rate", default="", metavar="СКОРОСТЬ",
                        help="ограничить скорость: 500K, 2M (по умолчанию без ограничения)")
    parser.add_argument("--proxy", default="", metavar="АДРЕС",
                        help="прокси для метаданных и загрузки: http://127.0.0.1:1080, socks5://…")
    parser.add_argument("--check", action="store_true",
                        help="показать версию и какие ffmpeg и Deno нашлись, ничего не скачивая")
    args = parser.parse_args(argv)
    logs.setup(console=False)  # warnings already reach the console as text

    options = Options(args.output.expanduser(), args.format, max(1, args.threads), args.dry_run,
                      args.cookies_from_browser, args.names, args.folders,
                      _rate(args.limit_rate, parser), args.proxy)
    use_proxy(options.proxy)
    downloader = Downloader(options, log=lambda message: print(message, flush=True))

    if args.check:
        print(f"Trackhound {__version__}")
        print(f"ffmpeg: {downloader.ffmpeg or 'не найден'}")
        for name in ("deno", "node"):
            print(f"{name}: {downloader.js_runtimes.get(name, {}).get('path', 'не найден')}")
        print(f"папка для музыки: {options.output_dir}")
        print(f"yt-dlp: {logs.package_version('yt_dlp')}")
        print(f"журнал: {logs.log_file()}")
        for problem in downloader.environment_problems():
            print(f"⚠ {problem}")
        return 0
    if not args.links:
        parser.error("укажите ссылку или название (или --check, чтобы проверить установку)")

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
