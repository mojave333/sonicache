"""Command line interface."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import __version__, logs
from .i18n import LANGUAGES, set_language, t
from .engine import batch
from .engine.downloader import (DEFAULT_OUTPUT_DIR, FOLDER_NAMES, FORMATS, TRACK_NAMES, Downloader,
                                Options, use_proxy)


def _language(argv: list[str] | None) -> str:
    """--lang, read before argparse so that even the help speaks it."""
    words = list(sys.argv[1:] if argv is None else argv)
    for index, word in enumerate(words):
        if word == "--lang" and index + 1 < len(words):
            return words[index + 1]
        if word.startswith("--lang="):
            return word.split("=", 1)[1]
    return "system"


def _rate(value: str, parser: argparse.ArgumentParser) -> int:
    """"500K", "2M", "1.5m" — bytes per second; empty means no limit."""
    if not value:
        return 0
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*([kmg]?)b?/?s?\s*", value, re.I)
    if not m:
        parser.error(t("непонятная скорость: {value}. Примеры: 500K, 2M", value=value))
    scale = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()]
    return int(float(m.group(1).replace(",", ".")) * scale)


def main(argv: list[str] | None = None) -> int:
    # Every message here is in Russian, which a console or a redirect on an
    # English Windows cannot encode; --help must not die over that.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    # The language is settled before argparse builds its help
    set_language(_language(argv))
    parser = argparse.ArgumentParser(
        prog="trackhound",
        description=t("Скачивание альбомов, синглов и треков по ссылке из Spotify, Apple Music, "
                      "Deezer, YouTube, SoundCloud, Last.fm и сайтов вроде Bandcamp."),
    )
    parser.add_argument("links", nargs="*",
                        help=t("ссылки на альбомы, плейлисты или треки — либо название: "
                               "«Исполнитель - Альбом»"))
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=t("папка для музыки (по умолчанию {path})", path=DEFAULT_OUTPUT_DIR))
    parser.add_argument("-f", "--format", choices=FORMATS, default="m4a",
                        help=t("формат файлов: m4a (по умолчанию, AAC 256), mp3 (VBR V0), mp3-320, opus"))
    parser.add_argument("-t", "--threads", type=int, default=3,
                        help=t("сколько треков качать одновременно (по умолчанию 3)"))
    parser.add_argument("--dry-run", action="store_true",
                        help=t("только показать найденные совпадения, ничего не скачивать"))
    parser.add_argument("--cookies-from-browser", default="", metavar=t("БРАУЗЕР"),
                        help=t("брать cookies из браузера (chrome, edge, firefox…): нужно для видео "
                               "с возрастным ограничением"))
    parser.add_argument("--names", choices=TRACK_NAMES, default="auto",
                        help=t("имена файлов: auto — исполнитель только у гостей, artist — всегда, "
                               "title — только номер и название"))
    parser.add_argument("--folders", choices=FOLDER_NAMES, default="flat",
                        help=t("папки альбомов: flat — «Исполнитель - Альбом (Год)», "
                               "nested — «Исполнитель\Альбом (Год)», album — «Альбом (Год)»"))
    parser.add_argument("--limit-rate", default="", metavar=t("СКОРОСТЬ"),
                        help=t("ограничить скорость: 500K, 2M (по умолчанию без ограничения)"))
    parser.add_argument("--proxy", default="", metavar=t("АДРЕС"),
                        help=t("прокси для метаданных и загрузки: http://127.0.0.1:1080, socks5://…"))
    parser.add_argument("--lang", choices=LANGUAGES, default="system", metavar=t("ЯЗЫК"),
                        help=t("язык интерфейса и сообщений"))
    parser.add_argument("--from-file", action="append", default=[], type=Path, metavar=t("ФАЙЛ"),
                        help=t("взять ссылки и названия из файла: .txt по одной на строку или CSV-выгрузка плейлиста"))
    parser.add_argument("--replaygain", action="store_true",
                        help=t("измерить громкость и записать теги ReplayGain; сам звук не меняется"))
    parser.add_argument("--check", action="store_true",
                        help=t("показать версию и какие ffmpeg и Deno нашлись, ничего не скачивая"))
    args = parser.parse_args(argv)
    logs.setup(console=False)  # warnings already reach the console as text

    options = Options(args.output.expanduser(), args.format, max(1, args.threads), args.dry_run,
                      args.cookies_from_browser, args.names, args.folders,
                      _rate(args.limit_rate, parser), args.proxy, args.replaygain)
    use_proxy(options.proxy)
    downloader = Downloader(options, log=lambda message: print(message, flush=True))

    if args.check:
        print(f"Trackhound {__version__}")
        print(f"ffmpeg: {downloader.ffmpeg or t('не найден')}")
        for name in ("deno", "node"):
            print(f"{name}: {downloader.js_runtimes.get(name, {}).get('path', t('не найден'))}")
        print(t("папка для музыки: {path}", path=options.output_dir))
        print(f"yt-dlp: {logs.package_version('yt_dlp')}")
        print(t("журнал: {path}", path=logs.log_file()))
        for problem in downloader.environment_problems():
            print(f"⚠ {problem}")
        return 0
    for list_file in args.from_file:
        try:
            entries, skipped = batch.read(list_file.read_bytes(), list_file.name)
        except OSError as e:
            parser.error(t("не прочитать {file}: {error}", file=list_file, error=e))
        print(t("{file}: взято {count}, не разобрано строк: {skipped}",
                file=list_file.name, count=len(entries), skipped=skipped))
        args.links.extend(entries)
    if not args.links:
        parser.error(t("укажите ссылку или название (или --check, чтобы проверить установку)"))

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
        print(t("\nОстановлено."), file=sys.stderr)
        return 130
    return 1 if failed else 0
