"""Downloading matched tracks with yt-dlp and tagging them with mutagen."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import yt_dlp
from mutagen import File as MutagenFile
from mutagen.flac import Picture
from mutagen.id3 import (APIC, ID3, TALB, TCMP, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TSRC, USLT,
                        ID3NoHeaderError)
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from yt_dlp.utils import DownloadCancelled

from . import loudness, lyrics, sources
from .i18n import t
from .logs import YtdlpLogger, log
from .matcher import ISRC_SCORE, SOURCE_NAMES, Match, Matcher, doubtful
from .models import Album, Track, _CodedError
from .progress import Clock
from .net import BROWSER_UA

FORMATS = ("mp3", "m4a", "opus")  # in the order the window shows them; mp3 is the default
# How a track is named inside an album folder
TRACK_NAMES = ("auto", "artist", "title")
# How the album folder itself is named, inside the music folder
FOLDER_NAMES = ("flat", "nested", "album")
# Written into every album folder: where the album came from
MARKER_NAME = ".trackhound.json"
DEFAULT_OUTPUT_DIR = Path.home() / "Music" / "Trackhound"
# A 1000x1000 cover is about a megabyte; past this it is not artwork any more
MAX_COVER_BYTES = 8 * 1024 * 1024
# A held-back track offers this many candidates to choose from
DOUBT_CHOICES = 5
KINDS = {"album": "альбом", "single": "сингл", "ep": "EP", "compilation": "сборник", "playlist": "плейлист"}

_UNSAFE_CHARS = str.maketrans({
    ":": " -", '"': "'", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", "<": "(", ">": ")",
})
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


class DownloaderError(_CodedError):
    """A download could not go ahead, or a file came out wrong; `code` and
    `details` as on SourceError."""


def tool_dirs() -> list[Path]:
    """Where a copy of ffmpeg or deno shipped with the program would sit.

    A build made with trackhound.spec keeps them in _internal\\bin; a copy the
    user drops next to the exe wins over it, and a checkout uses vendor\\ so
    that running from source behaves the same way.
    """
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).parent)
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    else:
        roots.append(Path(__file__).resolve().parents[2] / "vendor")  # trackhound/engine/ -> checkout
    return [folder for root in roots for folder in (root / "bin", root)]


def find_tool(name: str) -> str | None:
    """A copy shipped with the program wins over one installed system-wide."""
    filename = f"{name}.exe" if os.name == "nt" else name
    for folder in tool_dirs():
        candidate = folder / filename
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


@dataclass
class Options:
    output_dir: Path
    audio_format: str = "mp3"
    threads: int = 3
    dry_run: bool = False  # only search and print matches
    # Browser to take YouTube cookies from ("chrome", "firefox"...); age-restricted
    # videos are only served to a signed-in account. Empty means no cookies.
    cookies_browser: str = ""
    # "auto" names guests only when they differ from the album artist,
    # "artist" always names them, "title" leaves the number and the title
    track_name: str = "auto"
    # "flat": "Artist - Album (Year)", "nested": "Artist/Album (Year)", "album": "Album (Year)"
    folder_name: str = "flat"
    # Bytes per second for the whole program, 0 for as fast as it goes
    rate_limit: int = 0
    proxy: str = ""  # "http://127.0.0.1:1080", "socks5://…"; empty uses the system settings
    # Measure each file with ffmpeg and write ReplayGain tags; the audio stays as it is
    replaygain: bool = False
    # Hold back a track whose best match may be the wrong song, instead of
    # downloading it: its candidates come in the "doubtful" track event and in
    # Report.doubts, for a person to choose from
    ask: bool = False
    # The tracks chosen that way, by track id: only they are downloaded, each
    # from its own match, with no search
    choices: dict[str, Match] = field(default_factory=dict)
    # Lyrics from LRCLIB: the plain ones into the tags, the synced ones into an
    # .lrc file beside the track
    lyrics: bool = False


@dataclass
class Doubt:
    """A track held back because its match may be the wrong song."""

    track: Track
    candidates: list[Match]  # best first


@dataclass
class Failure:
    """A track that did not arrive, for a program to act on."""

    track: Track
    code: str  # one of ERROR_CODES
    message: str  # the reason as a person reads it, in the language set_language() chose


@dataclass
class Report:
    ok: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # "Artist - Title: reason", one line each
    failures: list[Failure] = field(default_factory=list)  # the same tracks, with their codes
    doubtful: list[str] = field(default_factory=list)  # "Artist - Title" held back for a choice
    doubts: list[Doubt] = field(default_factory=list)  # the same tracks, with their candidates

    def summary(self, dry_run: bool = False) -> str:
        text = t("{done}: {ok}, уже было: {skipped}, ошибок: {failed}",
                 done=t("найдено") if dry_run else t("скачано"), ok=len(self.ok),
                 skipped=len(self.skipped), failed=len(self.failed))
        if self.doubtful:
            text += t(", ждут выбора: {count}", count=len(self.doubtful))
        return "\n".join([text, *(f"  ✗ {item}" for item in self.failed),
                          *(f"  ? {item}" for item in self.doubtful)])


class Downloader:
    def __init__(
        self,
        options: Options,
        log: Callable[[str], None] = print,
        progress: Callable[[int, int], None] | None = None,
        stop_event: threading.Event | None = None,
        events: Callable[[str, dict], None] | None = None,
        resume_event: threading.Event | None = None,
    ):
        self.options = options
        self.log = _tee(log)
        self.progress = progress or (lambda done, total: None)
        # structured updates for the window: "release" once, then "track" on every state change
        self.events = events or (lambda kind, data: None)
        self.stop_event = stop_event or threading.Event()
        # Cleared while paused: tracks already downloading finish, new ones wait.
        # A caller's event is left as it is — a job started during a pause stays paused.
        self.resume_event = resume_event or threading.Event()
        if resume_event is None:
            self.resume_event.set()
        self.matcher = Matcher()
        self._paths: dict[str, Path] = {}
        # The tracks under way, each with its clock, the source shown and whether
        # it is a second try; the lock also keeps their events in order
        self._clocks: dict[str, dict] = {}
        self._clock_lock = threading.Lock()
        self.ffmpeg = find_tool("ffmpeg")
        # yt-dlp needs a JavaScript runtime to solve YouTube challenges; only
        # deno is enabled by default, node has to be passed explicitly.
        self.js_runtimes = {name: {"path": path} for name in ("deno", "node")
                            if (path := find_tool(name))}

    def environment_problems(self) -> list[str]:
        problems = []
        if not self.ffmpeg:
            problems.append(t("Не найден ffmpeg: форматы mp3/opus недоступны, m4a может не "
                              "сохраниться. Установка: winget install Gyan.FFmpeg"))
        if not self.js_runtimes:
            problems.append(t("Не найден Deno или Node.js 22+: YouTube может не отдавать аудио. "
                              "Установка: winget install DenoLand.Deno"))
        if importlib.util.find_spec("yt_dlp_ejs") is None:
            problems.append(t("Не установлен пакет yt-dlp-ejs: pip install -U yt-dlp-ejs"))
        return problems

    def download_link(self, link: str) -> Report:
        if self.options.audio_format not in FORMATS:
            raise DownloaderError.of("unknown_format", "Неизвестный формат: {format}",
                                     format=self.options.audio_format)
        if not self.ffmpeg and self.options.audio_format != "m4a" and not self.options.dry_run:
            raise DownloaderError.of("ffmpeg_missing",
                                     "Для mp3 и opus нужен ffmpeg (winget install Gyan.FFmpeg)")

        release = sources.resolve(link)
        album, tracks, single = release.album, release.tracks, release.single
        if self.options.choices:  # only the tracks chosen for, the album still whole for the numbering
            tracks = [track for track in tracks if track.id in self.options.choices]
        if (album.kind != "playlist" and len(album.tracks) > 1
                and any(not track.isrc and not track.audio_url for track in tracks)):
            # Spotify and Apple name no ISRC: the same album on Deezer does, for all its tracks at once
            try:
                sources.borrow_isrcs(album)
            except Exception as e:  # the codes are a nicety: tracks are still found by name
                log.getChild("download").info("ISRC для «%s» не получены: %s", album.name, e)
        if album.kind != "playlist" and not self.options.dry_run:
            # A playlist mixes genres, so one guessed for its name would be wrong on most tracks
            try:
                album.genre = sources.find_genre(album.artist, album.name, album.genre)
            except Exception as e:  # the genre is a nicety: no lookup failure may stop a download
                log.getChild("download").warning("жанр для «%s» не нашёлся: %s", album.name, e)
        if single:
            folder = self.options.output_dir
            details = [album.name != tracks[0].title and t("из «{album}»", album=album.name),
                       album.year, album.service]
            self.log(t("♪ {artist} — {title} ({details})", artist=tracks[0].artists,
                       title=tracks[0].title, details=", ".join(filter(None, details))))
        else:
            folder = _album_folder(self.options.output_dir, album, self.options.folder_name)
            self.log(t("♪ {artist} — {album} ({kind}, {year}, треков: {tracks}, {service})",
                       artist=album.artist, album=album.name, kind=t(KINDS.get(album.kind, album.kind)),
                       year=album.year or t("год неизвестен"), tracks=len(tracks),
                       service=album.service))
        if album.note:
            self.log(f"! {album.note}")
        self.events("release", {
            "kind": t("трек") if single else t(KINDS.get(album.kind, album.kind)),
            "single": single,  # a lone track has nothing to watch for
            "note": album.note,
            "title": tracks[0].title if single else album.name,
            "artist": tracks[0].artists if single else album.artist,
            "album": album.name,
            "year": album.year,
            "service": album.service,
            "cover": album.cover_url,
            "folder": str(folder),
            "tracks": [{"id": t.id, "number": t.track_number, "disc": t.disc_number, "title": t.title,
                        "artists": t.artists, "duration": _mmss(t.duration)} for t in tracks],
        })
        return self._download_tracks(album, tracks, folder, single, link)

    def _download_tracks(self, album: Album, tracks: list[Track], folder: Path, single: bool,
                         link: str = "") -> Report:
        report = Report()
        cover = None
        if not self.options.dry_run:
            folder.mkdir(parents=True, exist_ok=True)
            if not single:
                _write_marker(folder, album, link)
            cover = self._fetch_cover(album.cover_url)
            if cover and not single and not (folder / "cover.jpg").exists():
                (folder / "cover.jpg").write_bytes(cover)

        lock = threading.Lock()
        done = 0
        self._paths: dict[str, Path] = {}  # where each track of this release is on disk

        def job(track: Track) -> None:
            nonlocal done
            result = self._process(album, track, folder, single, cover)
            with lock:
                if result:
                    kind, line, *more = result
                    getattr(report, kind).append(line)
                    # A failed track carries its Failure, a held-back one its Doubt
                    (report.doubts if kind == "doubtful" else report.failures).extend(more)
                done += 1
                self.progress(done, len(tracks))

        self.progress(0, len(tracks))
        pool = ThreadPoolExecutor(max_workers=max(1, self.options.threads))
        try:
            futures = [pool.submit(job, track) for track in tracks]
            # Short waits keep the main thread responsive to Ctrl+C on Windows,
            # and each one tells the window how far the tracks under way are
            while wait(futures, timeout=0.5).not_done:
                self._tick()
            for future in futures:
                future.result()
            self._tag_loudness(album, tracks, single)
        except BaseException:
            self.stop_event.set()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        return report

    def _process(self, album: Album, track: Track, folder: Path, single: bool,
                 cover: bytes | None) -> tuple[str, str] | tuple[str, str, Failure] | None:
        while not self.resume_event.wait(timeout=0.5):
            if self.stop_event.is_set():
                break  # stopping while paused must not wait for a resume
        if self.stop_event.is_set():
            self._track_event(track, "cancel")
            return None
        label = f"{track.artists} - {track.title}"
        target = folder / f"{_safe_name(_file_stem(album, track, single, self.options.track_name))}.{self.options.audio_format}"
        if not self.options.dry_run:
            candidates = [target]
            if legacy := _legacy_stem(album, track, single, self.options.track_name):
                candidates.append(folder / f"{_safe_name(legacy)}.{self.options.audio_format}")
            existing = next((path for path in candidates if path.exists()), None)
            if existing is not None:
                self._paths[track.id] = existing
                self.log(t("= Уже есть: {name}", name=existing.name))
                self._track_event(track, "skip")
                return "skipped", label

        stem = f"_part_{track.id}"
        direct = _direct_match(track)
        if not self.options.dry_run:
            with self._clock_lock:
                self._clocks[track.id] = {"clock": Clock(track.duration), "track": track, "source": "",
                                          "retry": False}
        announced = False  # the search shown from the ISRC lookup on, not only once it asks YouTube
        if not (direct or track.isrc or self.options.choices.get(track.id)):
            self._track_event(track, "search", wide=False)
            announced = True
            try:
                track.isrc = sources.track_isrc(track)
            except Exception as e:
                log.getChild("download").debug("ISRC для «%s» не получен: %s", label, e)
        tried: set[str] = set()
        pool: list[Match] = []
        searches = 0  # 1: the quick search, which stops at the first great hit; 2: every source

        def next_match() -> Match | None:
            nonlocal pool, searches
            while not pool and searches < 2:
                searches += 1
                if not (searches == 1 and announced):
                    self._track_event(track, "search", wide=searches == 2)
                pool = [found for found in self.matcher.find_all(track, album, searches == 2)
                        if found.url not in tried]
            if not pool:
                return None
            match = pool.pop(0)
            tried.add(match.url)
            return match

        try:
            chosen = self.options.choices.get(track.id)
            match = chosen or direct or next_match()
            if match is not None and self.options.ask and not (chosen or direct or self.options.dry_run):
                candidates = [match, *pool]
                if doubtful(candidates):
                    self.log(t("? {label}: похоже на несколько записей, ждёт выбора", label=label))
                    self._track_event(track, "doubtful", candidates=[
                        {**asdict(candidate), "source_name": t(SOURCE_NAMES.get(candidate.source, ""))}
                        for candidate in candidates[:DOUBT_CHOICES]])
                    return "doubtful", label, Doubt(track, candidates)
            if match is None:
                self.log(t("✗ Не найдено ни на YouTube Music, ни на SoundCloud: {label}",
                           label=label))
                self._track_event(track, "missing", code="no_source")
                reason = t("не найдено ни на YouTube Music, ни на SoundCloud")
                return "failed", f"{label}: {reason}", Failure(track, "no_source", reason)
            source = t(SOURCE_NAMES.get(match.source, "")) or album.service
            if match.score == ISRC_SCORE:
                source = f"{source} · ISRC"  # the very recording, not one of the same name
            if self.options.dry_run:
                self.log(t("? {label}  →  {artists} - {title} [{source}, {got} / {wanted}, "
                           "оценка {score}] {url}", label=label, artists=match.artists,
                           title=match.title, source=source, got=_mmss(match.duration),
                           wanted=_mmss(track.duration), score=match.score, url=match.page_url))
                self._track_event(track, "found", f"{match.artists} — {match.title}", source)
                return "ok", label
            retry = False  # a source after the first one that refused
            while True:
                try:
                    path = self._fetch(match, folder, stem, track, source, retry)
                    break
                except DownloadCancelled:
                    raise
                except Exception as e:
                    # This one refuses (age-gated video, preview only, dead link):
                    # the next candidate is usually the same song somewhere else.
                    following = next_match()
                    if following is None:
                        raise
                    self.log(t("! {label}: не скачалось с {source} ({error}), "
                               "пробую другой источник", label=label,
                               source=source or t("исходной ссылки"), error=_error_text(e)))
                    _clear_partials(folder, stem)
                    match, source = following, t(SOURCE_NAMES.get(following.source, "")) or album.service
                    retry = True
            self._step(track, "finish")
            words = lyrics.find(track, album) if self.options.lyrics else None
            _write_tags(path, album, track, cover, words.plain if words else "")
            os.replace(path, target)
            if words and words.synced:
                _write_lrc(target, words.synced)
        except DownloadCancelled:
            self._track_event(track, "cancel")
            return None
        except Exception as e:  # one broken track must not stop the whole album
            code, message = _describe(e)
            self.log(f"✗ {label}: {message}")
            self._track_event(track, "error", message, code=code)
            return "failed", f"{label}: {message}", Failure(track, code, message)
        finally:
            if not self.options.dry_run:
                _clear_partials(folder, stem)
        self._paths[track.id] = target
        self.log(f"✓ {target.name}" + ("" if match.source == "song" else f"  ({source})"))
        self._track_event(track, "done", source=source)
        return "ok", label

    def _tag_loudness(self, album: Album, tracks: list[Track], single: bool) -> None:
        """ReplayGain for everything of this release that is on disk.

        The tracks skipped as already there take part too: an album gain is
        only right when it is worked out over the whole album, and a track that
        was tagged before is not measured a second time.
        """
        if not self.options.replaygain or self.options.dry_run or self.stop_event.is_set():
            return
        paths = [self._paths[track.id] for track in tracks if track.id in self._paths]
        if not paths:
            return
        if not self.ffmpeg:
            self.log(t("! Громкость не выровнена: для этого нужен ffmpeg"))
            return
        self.events("loudness", {"state": "running"})
        # A playlist gathers songs from many records, so it has no album gain to speak of
        whole_album = not single and album.kind != "playlist"
        measured = loudness.apply(paths, self.ffmpeg, whole_album=whole_album, log=self.log,
                                  stop=self.stop_event, workers=self.options.threads)
        if measured:
            self.log(t("♫ Громкость измерена: {count}", count=measured))
        self.events("loudness", {"state": "done", "measured": measured})

    def _fetch(self, match: Match, folder: Path, stem: str, track: Track, source: str,
               retry: bool = False) -> Path:
        clock = self._step(track, "prepare", match.source, source, retry)
        self._track_event(track, "download", source=source, retry=retry,
                          percent=clock.percent() if clock else 0,
                          **({"eta": round(clock.remaining(), 1)} if clock else {}))
        path = self._download_audio(match, folder, stem, track, source, retry)
        _check_duration(path, track)
        return path

    def _download_audio(self, match: Match, folder: Path, stem: str, track: Track, source: str,
                        retry: bool = False) -> Path:
        audio_format = self.options.audio_format
        clock = self._step(track, "prepare")

        def on_progress(status: dict) -> None:
            if self.stop_event.is_set():
                raise DownloadCancelled(t("остановлено пользователем"))
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            if status.get("status") == "downloading" and clock is not None:
                clock.enter("download")
                if total:
                    clock.report(status.get("downloaded_bytes", 0) / total, status.get("eta"))

        if match.source == "soundcloud":
            format_spec = "bestaudio/best"  # AAC 160k when available; previews rank last
        elif audio_format == "m4a" and self.ffmpeg:
            # YouTube's own AAC is 128 kbit/s and cut off at 16 kHz, while its Opus
            # stream reaches 20 kHz. So m4a is made from the Opus, at 256 kbit/s,
            # unless YouTube offers AAC as good as that itself (Premium accounts).
            format_spec = "bestaudio[ext=m4a][abr>=200]/bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio/best"
        else:
            format_spec = {"m4a": "bestaudio[ext=m4a]/bestaudio/best",
                           "opus": "bestaudio[acodec=opus]/bestaudio/best"}.get(audio_format, "bestaudio/best")
        opts = {
            "format": format_spec,
            "paths": {"home": str(folder), "temp": str(folder)},
            "outtmpl": {"default": f"{stem}.%(ext)s"},
            "noplaylist": True,
            "overwrites": True,
            "retries": 5,
            "fragment_retries": 5,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": YtdlpLogger("download"),
            "progress_hooks": [on_progress],
        }
        # The conversion is not left to yt-dlp: its ffmpeg says nothing until it
        # is done, and for mp3 that is most of a track's time
        if self.js_runtimes:
            opts["js_runtimes"] = self.js_runtimes
        if self.options.rate_limit:
            # Shared between threads, so the limit is what the program uses in total
            opts["ratelimit"] = self.options.rate_limit / max(1, self.options.threads)
        if self.options.proxy:
            opts["proxy"] = _remote_dns(self.options.proxy)
        if self.options.cookies_browser and match.source != "soundcloud":
            opts["cookiesfrombrowser"] = (self.options.cookies_browser,)
        if self.ffmpeg:
            opts["ffmpeg_location"] = self.ffmpeg
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(match.url, download=True)

        fetched = _downloaded_file(info, folder, stem)
        path = folder / f"{stem}.{audio_format}"
        if fetched is None or (not self.ffmpeg and fetched != path):
            got = ", ".join(p.suffix for p in folder.glob(f"{stem}.*")) or t("ничего")
            raise DownloaderError.of("wrong_file", "ожидался файл .{format}, получено: {got}",
                                     format=audio_format, got=got)
        if not self.ffmpeg:
            return path  # m4a as YouTube gives it, the one format that needs no ffmpeg
        copy = _same_codec((info or {}).get("acodec"), fetched, audio_format)
        if clock is not None:
            clock.encode = not copy
            clock.enter("convert")
        _convert(self.ffmpeg, fetched, path, audio_format, copy,
                 track.duration or (info or {}).get("duration") or 0,
                 on_fraction=clock.report if clock else None, stop=self.stop_event)
        return path

    def _track_event(self, track: Track, state: str, text: str = "", source: str = "", **extra) -> None:
        with self._clock_lock:
            if state not in ("search", "download"):  # over, or waiting for a choice: no more ticks
                entry = self._clocks.pop(track.id, None)
                if entry is not None and state == "done":
                    entry["clock"].done()
            self.events("track", {"id": track.id, "state": state, "text": text, "source": source, **extra})

    def _step(self, track: Track, step: str, match_source: str | None = None, shown: str | None = None,
              retry: bool | None = None) -> Clock | None:
        """Moves a track's clock on to the next step; None for a trial run."""
        with self._clock_lock:
            entry = self._clocks.get(track.id)
            if entry is None:
                return None
            entry["clock"].enter(step, match_source)
            if shown is not None:
                entry["source"] = shown
            if retry is not None:
                entry["retry"] = retry
            return entry["clock"]

    def _tick(self) -> None:
        """How far each track under way is, and how long it still needs, told
        twice a second: the steps that report nothing still move the bar."""
        with self._clock_lock:
            for entry in self._clocks.values():
                clock = entry["clock"]
                if clock.step in ("search", "done"):
                    continue
                self.events("track", {"id": entry["track"].id, "state": "download", "text": "",
                                      "source": entry["source"], "retry": entry["retry"],
                                      "percent": clock.percent(), "eta": round(clock.remaining(), 1)})

    def _fetch_cover(self, url: str) -> bytes | None:
        return fetch_cover(url, self.log)


def fetch_cover(url: str, say: Callable[[str], None]) -> bytes | None:
    """The picture at a release's cover address, or None, with the reason said."""
    if not url:
        return None
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            # The address comes out of someone else's metadata and these
            # bytes are copied into every track of the album, so read a
            # bounded amount and check that it really is an image.
            data = resp.read(MAX_COVER_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as e:
        say(f"! Обложка не скачалась: {e}")
        return None
    if len(data) > MAX_COVER_BYTES:
        say(f"! Обложка больше {MAX_COVER_BYTES // (1024 * 1024)} МБ, пропускаю: {url}")
        return None
    if not data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        say(f"! По адресу обложки не JPEG и не PNG, пропускаю: {url}")
        return None
    return data


_ENCODERS = {"mp3": ("libmp3lame", "320k"), "m4a": ("aac", "256k"), "opus": ("libopus", "160k")}
_CODECS = {"mp3": "mp3", "m4a": "aac", "opus": "opus"}  # the codec each format holds
_CODEC_BY_SUFFIX = {".mp3": "mp3", ".m4a": "aac", ".mp4": "aac", ".opus": "opus", ".ogg": "vorbis"}


def _downloaded_file(info: dict | None, folder: Path, stem: str) -> Path | None:
    """The file yt-dlp wrote, from its own account or else found by its name."""
    for item in (info or {}).get("requested_downloads") or []:
        path = Path(item.get("filepath") or "")
        if path.name and path.is_file():
            return path
    found = [path for path in folder.glob(f"{stem}.*")
             if path.suffix not in (".part", ".ytdl") and ".converting" not in path.name]
    return found[0] if found else None


def _same_codec(acodec: str | None, path: Path, audio_format: str) -> bool:
    """Whether the stream is already in the codec the format holds, so that it
    is only rewrapped: YouTube's Opus as .opus, SoundCloud's mp3 as .mp3."""
    codec = (acodec or "").lower()
    if codec in ("", "none"):
        codec = _CODEC_BY_SUFFIX.get(path.suffix.lower(), "")
    family = "aac" if codec.startswith("mp4a") or codec == "aac" else codec.split(".")[0]
    return family == _CODECS[audio_format]


def _convert(ffmpeg: str, source: Path, target: Path, audio_format: str, copy: bool, duration: float,
             on_fraction: Callable[[float], None] | None = None, stop: threading.Event | None = None) -> None:
    """Makes the downloaded stream into the format asked for, with ffmpeg
    telling how far it has got.

    A stream in the format's own codec is only rewrapped. Anything else is
    encoded: mp3 at a constant 320 kbit/s and 44.1 kHz, which car stereos and
    DJ software expect of it (YouTube's Opus is 48 kHz); m4a as AAC 256; opus
    at 160. What the stream carried as tags is dropped: the release's own are
    written afterwards.
    """
    partial = target.with_name(f"{target.stem}.converting{target.suffix}")
    command = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(source),
               "-map", "0:a:0", "-vn", "-map_metadata", "-1"]
    if copy:
        command += ["-c:a", "copy"]
        if audio_format == "m4a":
            command += ["-bsf:a", "aac_adtstoasc"]  # AAC from an HLS stream comes framed for broadcast
    else:
        encoder, bitrate = _ENCODERS[audio_format]
        command += ["-c:a", encoder, "-b:a", bitrate]
        if audio_format == "mp3":
            command += ["-ar", "44100"]
    if audio_format == "m4a":
        command += ["-movflags", "+faststart"]
    command += ["-progress", "pipe:1", "-nostats", str(partial)]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # no console flashing up
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", errors="replace", creationflags=flags)
    complaints: list[str] = []
    reader = threading.Thread(target=lambda: complaints.extend(process.stderr), daemon=True)
    reader.start()
    try:
        for line in process.stdout:  # "out_time_us=12345678", twice a second
            if stop is not None and stop.is_set():
                raise DownloadCancelled(t("остановлено пользователем"))
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit() and duration and on_fraction:
                on_fraction(int(value) / 1_000_000 / duration)
        process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=5)
    if process.returncode != 0:
        partial.unlink(missing_ok=True)
        lines = "".join(complaints).strip().splitlines()
        raise DownloaderError.of("convert_failed", "ffmpeg не перекодировал файл: {error}",
                                 error=lines[-1] if lines else process.returncode)
    os.replace(partial, target)
    if source != target:
        source.unlink(missing_ok=True)


def _tee(report: Callable[[str], None]) -> Callable[[str], None]:
    """Everything the person is told is also written to the log file."""
    def say(message: str) -> None:
        log.getChild("download").info("%s", message)
        report(message)

    return say


def _album_folder(root: Path, album: Album, style: str = "flat") -> Path:
    """Where an album lands: one folder, an artist folder, or just the album."""
    year = f" ({album.year})" if album.year else ""
    if style == "nested" and album.artist:
        return root / _safe_name(album.artist) / _safe_name(f"{album.name}{year}")
    if style == "album" or not album.artist:
        return root / _safe_name(f"{album.name}{year}")
    return root / _safe_name(f"{album.artist} - {album.name}{year}")


def _file_stem(album: Album, track: Track, single: bool, style: str = "auto") -> str:
    if single:
        return f"{track.artists} - {track.title}"
    number = f"{track.track_number:02d}"
    if album.total_discs > 1:
        number = f"{track.disc_number}-{number}"
    if style == "title":
        return f"{number}. {track.title}"
    # Guests are named: on an album by one artist only their own tracks go unlabelled
    own = not track.artists or track.artists.casefold() == album.artist.casefold()
    if style == "auto" and own:
        return f"{number}. {track.title}"
    if not track.artists:
        return f"{number}. {track.title}"
    return f"{number}. {track.artists} - {track.title}"


def _legacy_stem(album: Album, track: Track, single: bool, style: str = "auto") -> str:
    """How releases before the guest-artist fix were named, so that an album
    downloaded back then is still recognised as already downloaded."""
    if single or not album.artist or album.artist.casefold() not in track.artists.casefold():
        return ""
    number = f"{track.track_number:02d}"
    if album.total_discs > 1:
        number = f"{track.disc_number}-{number}"
    legacy = f"{number}. {track.title}"
    return "" if legacy == _file_stem(album, track, single, style) else legacy


def _direct_match(track: Track) -> Match | None:
    """The track's own audio, when the link's service streams it openly."""
    if not track.audio_url:
        return None
    return Match(track.audio_source or "web", track.audio_url, track.audio_url,
                 track.title, track.artists, track.duration, 1.0)


def use_proxy(proxy: str) -> None:
    """Points everything that speaks HTTP at the proxy, or back at the system.

    The metadata comes through urllib and requests, the audio through yt-dlp.
    requests and yt-dlp read these variables, so one setting is enough for
    them. urllib reads them too but speaks only to HTTP proxies: sent to a
    SOCKS port, which is what most VPN clients open, it failed, so a SOCKS
    proxy gets an opener of its own.
    """
    proxy = _remote_dns(proxy)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if proxy:
            os.environ[name] = proxy
        else:
            os.environ.pop(name, None)
    parts = urllib.parse.urlsplit(proxy)
    if parts.scheme.startswith("socks"):
        import socks
        from sockshandler import SocksiPyHandler

        kind = socks.SOCKS4 if parts.scheme.startswith("socks4") else socks.SOCKS5
        handler = SocksiPyHandler(kind, parts.hostname, parts.port or 1080, True,
                                  parts.username, parts.password)
        # Without an empty ProxyHandler urllib adds its own, which takes the
        # socks5h:// above from the variables and knows no such scheme
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({}), handler))
    else:
        # Built again from the variables on the next request: an opener made
        # earlier would keep the proxy it was made with
        urllib.request.install_opener(None)


def _remote_dns(proxy: str) -> str:
    """A SOCKS proxy is asked to look the names up itself: where a service is
    blocked, the local DNS may be lying about its address as well."""
    return re.sub(r"^socks5://", "socks5h://", re.sub(r"^socks4://", "socks4a://", proxy))


def _write_marker(folder: Path, album: Album, link: str) -> None:
    """Leaves the link the album came from inside its folder.

    The library reads it to offer the album again — to fill in tracks that
    failed the first time, or to fetch it in another format — without anybody
    having to find the link a second time.
    """
    if not link:
        return
    marker = {
        "link": link,
        "artist": album.artist,
        "album": album.name,
        "year": album.year,
        "kind": album.kind,
        "service": album.service,
        "genre": album.genre,
        "tracks": len(album.tracks),
        # The whole tracklist, so the library can name the tracks that are missing
        "tracklist": [{"disc": t.disc_number, "number": t.track_number, "title": t.title,
                       "artists": t.artists, "duration": round(t.duration)} for t in album.tracks],
    }
    try:
        (folder / MARKER_NAME).write_text(json.dumps(marker, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    except OSError as e:
        log.getChild("download").warning("не записал %s: %s", MARKER_NAME, e)


def _clear_partials(folder: Path, stem: str) -> None:
    for leftover in folder.glob(f"{stem}.*"):
        leftover.unlink(missing_ok=True)


# yt-dlp explains itself in English and at length; these cases have a short answer
_KNOWN_ERRORS = (
    ("confirm your age", "age_restricted", "видео с возрастным ограничением: нужен вход в аккаунт YouTube "
                                           "(cookies браузера в настройках)"),
    ("age-restricted", "age_restricted", "видео с возрастным ограничением: нужен вход в аккаунт YouTube "
                                         "(cookies браузера в настройках)"),
    ("could not copy", "cookies_locked", "не удалось прочитать cookies: закройте браузер и повторите"),
    ("failed to decrypt", "cookies_unreadable",
     "не удалось расшифровать cookies браузера; в Firefox они читаются надёжнее"),
    ("no cookies found", "no_cookies", "в браузере нет cookies YouTube: войдите в аккаунт в этом браузере"),
)


def _describe(error: Exception) -> tuple[str, str]:
    """What went wrong with a track: a code for a program, a sentence for a person."""
    text = re.sub(r"^ERROR:\s*", "", str(error)).strip()
    lowered = text.lower()
    for needle, code, message in _KNOWN_ERRORS:
        if needle in lowered:
            return code, t(message)
    code = error.code if isinstance(error, _CodedError) else "download_failed"
    return code, text or type(error).__name__


def _error_text(error: Exception) -> str:
    return _describe(error)[1]


def _check_duration(path: Path, track: Track) -> None:
    """Rejects truncated audio, e.g. 30-second previews of SoundCloud Go+ tracks."""
    audio = MutagenFile(path)
    length = audio.info.length if audio is not None else 0
    if track.duration and 0 < length < track.duration * 0.9 - 2:
        raise DownloaderError.of("preview_only", "скачался фрагмент {got} вместо {expected}",
                                 got=_mmss(length), expected=_mmss(track.duration))


def _write_lrc(track_path: Path, synced: str) -> None:
    """Synced lyrics beside the track, under its name: players look for them there."""
    lrc = track_path.with_suffix(".lrc")
    try:
        lrc.write_text(synced + "\n", encoding="utf-8")
    except OSError as e:
        log.getChild("lyrics").warning("не записал %s: %s", lrc.name, e)


# Where each format keeps each tag. The tidy-up reads through the same table
# the download writes through, so the two never disagree about a name.
TAG_FRAMES = {
    ".m4a": {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "albumartist": "aART",
             "compilation": "cpil", "track": "trkn", "disc": "disk", "date": "\xa9day",
             "genre": "\xa9gen", "lyrics": "\xa9lyr", "cover": "covr", "isrc": "----:com.apple.iTunes:ISRC"},
    ".mp3": {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2",
             "compilation": "TCMP", "track": "TRCK", "disc": "TPOS", "date": "TDRC",
             "genre": "TCON", "lyrics": "USLT", "cover": "APIC", "isrc": "TSRC"},
    ".opus": {"title": "title", "artist": "artist", "album": "album", "albumartist": "albumartist",
              "compilation": "compilation", "track": "tracknumber", "disc": "discnumber", "date": "date",
              "genre": "genre", "lyrics": "lyrics", "cover": "metadata_block_picture", "isrc": "isrc"},
}
_ID3_TEXT = {"TIT2": TIT2, "TPE1": TPE1, "TALB": TALB, "TPE2": TPE2, "TRCK": TRCK, "TPOS": TPOS,
             "TDRC": TDRC, "TCON": TCON, "TSRC": TSRC}


def _write_tags(path: Path, album: Album, track: Track, cover: bytes | None, words: str = "",
                fill: bool = False) -> list[str]:
    """Tags the file as this track of this release; answers with what was written.

    A download starts from clean tags. With fill the file keeps every tag it
    has and gets only the ones it lacks: tidying up older files must never
    overwrite what somebody may have corrected by hand.
    """
    track_total = sum(1 for t in album.tracks if t.disc_number == track.disc_number)
    # A playlist gathers many artists' songs: players group it as a compilation
    # of various artists, the way they do a soundtrack, rather than as an album
    # by whoever put the list together. The folder keeps the curator's name.
    compilation = album.kind == "playlist"
    values = {
        "title": track.title,
        "artist": track.artists,
        "album": album.name,
        "albumartist": t("Разные исполнители") if compilation else album.artist,
        "compilation": compilation,
        # A file the tidy-up could not find in the tracklist has no number to get
        "track": track.track_number and (track.track_number, track_total),
        "disc": track.track_number and (track.disc_number, album.total_discs),
        "date": album.release_date,
        "genre": album.genre,
        "lyrics": words,
        "cover": cover,
        "isrc": track.isrc,
    }
    values = {key: value for key, value in values.items() if value}
    mime = "image/png" if cover and cover.startswith(b"\x89PNG") else "image/jpeg"
    ext = path.suffix.lower()
    frames = TAG_FRAMES.get(ext, {})
    have = {key for key, value in read_tags(path).items() if value} if fill else set()
    values = {key: value for key, value in values.items() if key not in have}
    if fill and not values:
        return []

    if ext == ".m4a":
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        for key, value in values.items():
            if key in ("track", "disc"):
                value = [value]
            elif key == "cover":
                image_format = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
                value = [MP4Cover(value, imageformat=image_format)]
            elif key == "isrc":
                value = [MP4FreeForm(value.encode("ascii", "ignore"))]
            audio.tags[frames[key]] = value
        audio.save()

    elif ext == ".mp3":
        tags, version = ID3(), 3  # ID3v2.3 for Windows Explorer and old players
        if fill:
            try:
                tags = ID3(path)
                # A v2.4 file stays one: saving it as v2.3 would drop the frames v2.3 lacks
                version = 4 if tags.version >= (2, 4, 0) else 3
            except ID3NoHeaderError:
                pass
        for key, value in values.items():
            if key == "lyrics":
                tags.add(USLT(encoding=3, lang="und", desc="", text=value))
            elif key == "cover":
                tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=value))
            elif key == "compilation":
                tags.add(TCMP(encoding=3, text="1"))
            elif key in ("track", "disc"):
                tags.add(_ID3_TEXT[frames[key]](encoding=3, text="{}/{}".format(*value)))
            else:
                tags.add(_ID3_TEXT[frames[key]](encoding=3, text=value))
        tags.save(path, v2_version=version)

    elif ext == ".opus":
        audio = OggOpus(path)
        for key, value in values.items():
            if key in ("track", "disc"):
                audio[frames[key]] = str(value[0])
                audio["tracktotal" if key == "track" else "disctotal"] = str(value[1])
            elif key == "compilation":
                audio[frames[key]] = "1"
            elif key == "cover":
                picture = Picture()
                picture.type, picture.mime, picture.desc, picture.data = 3, mime, "Cover", value
                audio[frames[key]] = base64.b64encode(picture.write()).decode("ascii")
            else:
                audio[frames[key]] = value
        audio.save()

    else:
        return []
    return list(values)


def read_tags(path: Path) -> dict:
    """What a file already says about itself, by the names of TAG_FRAMES: text
    as text, the track and disc as numbers, the compilation flag, lyrics and
    cover as whether they are there; its length as "duration". Empty for a
    file that cannot be read."""
    frames = TAG_FRAMES.get(path.suffix.lower())
    try:
        audio = MutagenFile(path) if frames else None
    except Exception:  # a broken or half-written file
        return {}
    if audio is None:
        return {}
    found: dict = {"duration": getattr(audio.info, "length", 0) or 0}
    tags = audio.tags
    for key, name in frames.items():
        value = None
        if tags is None:
            pass
        elif isinstance(tags, ID3):
            present = tags.getall(name)
            # A picture frame has no text: that it is there is all there is to know
            value = getattr(present[0], "text", True) if present else None
        else:
            value = tags.get(name)
        if isinstance(value, (list, tuple)) and key not in ("track", "disc"):
            value = value[0] if value else None
        if key in ("track", "disc"):
            if isinstance(value, list):
                value = value[0] if value else None
            number = value[0] if isinstance(value, tuple) else re.match(r"\s*(\d*)", str(value or "")).group(1)
            found[key] = int(number or 0)
        elif key in ("lyrics", "cover", "compilation"):
            # A picture is megabytes of bytes, not worth turning into text to look at
            found[key] = bool(value) if isinstance(value, (bytes, bool)) else str(value or "").strip() not in ("", "0")
        else:
            if isinstance(value, bytes):  # MP4's own atoms for what it has no name for, ISRC among them
                value = value.decode("utf-8", "replace")
            found[key] = str(value or "").strip()
    return found


def _safe_name(name: str, limit: int = 120) -> str:
    name = re.sub(r"[\x00-\x1f]", "", name.translate(_UNSAFE_CHARS))
    name = " ".join(name.split())[:limit].rstrip(". ")
    if name.split(".")[0].upper() in _RESERVED_NAMES:
        name = f"_{name}"
    return name or "_"


def _mmss(seconds: float) -> str:
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"
