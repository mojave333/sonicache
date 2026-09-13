"""Downloading matched tracks with yt-dlp and tagging them with mutagen."""

from __future__ import annotations

import base64
import importlib.util
import os
import re
import shutil
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yt_dlp
from mutagen import File as MutagenFile
from mutagen.flac import Picture
from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from yt_dlp.utils import DownloadCancelled

from . import sources
from .matcher import SOURCE_NAMES, Match, Matcher, SilentLogger
from .models import Album, Track
from .net import BROWSER_UA

FORMATS = ("m4a", "mp3", "opus")
DEFAULT_OUTPUT_DIR = Path.home() / "Music" / "Trackhound"
KINDS = {"album": "альбом", "single": "сингл", "ep": "EP", "compilation": "сборник", "playlist": "плейлист"}

_UNSAFE_CHARS = str.maketrans({
    ":": " -", '"': "'", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", "<": "(", ">": ")",
})
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


class DownloaderError(Exception):
    pass


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
        roots.append(Path(__file__).resolve().parent.parent / "vendor")
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
    audio_format: str = "m4a"
    threads: int = 3
    dry_run: bool = False  # only search and print matches
    # Browser to take YouTube cookies from ("chrome", "firefox"...); age-restricted
    # videos are only served to a signed-in account. Empty means no cookies.
    cookies_browser: str = ""


@dataclass
class Report:
    ok: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def summary(self, dry_run: bool = False) -> str:
        done = "найдено" if dry_run else "скачано"
        text = f"{done}: {len(self.ok)}, уже было: {len(self.skipped)}, ошибок: {len(self.failed)}"
        return "\n".join([text, *(f"  ✗ {item}" for item in self.failed)])


class Downloader:
    def __init__(
        self,
        options: Options,
        log: Callable[[str], None] = print,
        progress: Callable[[int, int], None] | None = None,
        stop_event: threading.Event | None = None,
        events: Callable[[str, dict], None] | None = None,
    ):
        self.options = options
        self.log = log
        self.progress = progress or (lambda done, total: None)
        # structured updates for the window: "release" once, then "track" on every state change
        self.events = events or (lambda kind, data: None)
        self.stop_event = stop_event or threading.Event()
        self.matcher = Matcher()
        self.ffmpeg = find_tool("ffmpeg")
        # yt-dlp needs a JavaScript runtime to solve YouTube challenges; only
        # deno is enabled by default, node has to be passed explicitly.
        self.js_runtimes = {name: {"path": path} for name in ("deno", "node")
                            if (path := find_tool(name))}

    def environment_problems(self) -> list[str]:
        problems = []
        if not self.ffmpeg:
            problems.append("Не найден ffmpeg: форматы mp3/opus недоступны, m4a может не "
                            "сохраниться. Установка: winget install Gyan.FFmpeg")
        if not self.js_runtimes:
            problems.append("Не найден Deno или Node.js 22+: YouTube может не отдавать аудио. "
                            "Установка: winget install DenoLand.Deno")
        if importlib.util.find_spec("yt_dlp_ejs") is None:
            problems.append("Не установлен пакет yt-dlp-ejs: pip install -U yt-dlp-ejs")
        return problems

    def download_link(self, link: str) -> Report:
        if self.options.audio_format not in FORMATS:
            raise DownloaderError(f"Неизвестный формат: {self.options.audio_format}")
        if not self.ffmpeg and self.options.audio_format != "m4a" and not self.options.dry_run:
            raise DownloaderError("Для mp3 и opus нужен ffmpeg (winget install Gyan.FFmpeg)")

        release = sources.resolve(link)
        album, tracks, single = release.album, release.tracks, release.single
        if single:
            folder = self.options.output_dir
            details = [album.name != tracks[0].title and f"из «{album.name}»", album.year, album.service]
            self.log(f"♪ {tracks[0].artists} — {tracks[0].title} ({', '.join(filter(None, details))})")
        else:
            title = f"{album.artist} - {album.name}" + (f" ({album.year})" if album.year else "")
            folder = self.options.output_dir / _safe_name(title)
            self.log(f"♪ {album.artist} — {album.name} ({KINDS.get(album.kind, album.kind)}, "
                     f"{album.year or 'год неизвестен'}, треков: {len(tracks)}, {album.service})")
        self.events("release", {
            "kind": "трек" if single else KINDS.get(album.kind, album.kind),
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
        return self._download_tracks(album, tracks, folder, single)

    def _download_tracks(self, album: Album, tracks: list[Track], folder: Path, single: bool) -> Report:
        report = Report()
        cover = None
        if not self.options.dry_run:
            folder.mkdir(parents=True, exist_ok=True)
            cover = self._fetch_cover(album.cover_url)
            if cover and not single and not (folder / "cover.jpg").exists():
                (folder / "cover.jpg").write_bytes(cover)

        lock = threading.Lock()
        done = 0

        def job(track: Track) -> None:
            nonlocal done
            result = self._process(album, track, folder, single, cover)
            with lock:
                if result:
                    getattr(report, result[0]).append(result[1])
                done += 1
                self.progress(done, len(tracks))

        self.progress(0, len(tracks))
        pool = ThreadPoolExecutor(max_workers=max(1, self.options.threads))
        try:
            futures = [pool.submit(job, track) for track in tracks]
            # Short waits keep the main thread responsive to Ctrl+C on Windows.
            while wait(futures, timeout=0.5).not_done:
                pass
            for future in futures:
                future.result()
        except BaseException:
            self.stop_event.set()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        return report

    def _process(self, album: Album, track: Track, folder: Path, single: bool,
                 cover: bytes | None) -> tuple[str, str] | None:
        if self.stop_event.is_set():
            self._track_event(track, "cancel")
            return None
        label = f"{track.artists} - {track.title}"
        target = folder / f"{_safe_name(self._file_stem(album, track, single))}.{self.options.audio_format}"
        if target.exists() and not self.options.dry_run:
            self.log(f"= Уже есть: {target.name}")
            self._track_event(track, "skip")
            return "skipped", label

        stem = f"_part_{track.id}"
        direct = _direct_match(track)
        tried: set[str] = set()
        pool: list[Match] = []
        searches = 0  # 1: the quick search, which stops at the first great hit; 2: every source

        def next_match() -> Match | None:
            nonlocal pool, searches
            while not pool and searches < 2:
                searches += 1
                self._track_event(track, "search")
                pool = [found for found in self.matcher.find_all(track, album, searches == 2)
                        if found.url not in tried]
            if not pool:
                return None
            match = pool.pop(0)
            tried.add(match.url)
            return match

        try:
            match = direct or next_match()
            if match is None:
                self.log(f"✗ Не найдено ни на YouTube Music, ни на SoundCloud: {label}")
                self._track_event(track, "missing")
                return "failed", f"{label}: не найдено ни на YouTube Music, ни на SoundCloud"
            source = SOURCE_NAMES.get(match.source) or album.service
            if self.options.dry_run:
                self.log(f"? {label}  →  {match.artists} - {match.title} [{source}, "
                         f"{_mmss(match.duration)} / {_mmss(track.duration)}, оценка {match.score}] "
                         f"{match.page_url}")
                self._track_event(track, "found", f"{match.artists} — {match.title}", source)
                return "ok", label
            while True:
                try:
                    path = self._fetch(match, folder, stem, track, source)
                    break
                except DownloadCancelled:
                    raise
                except Exception as e:
                    # This one refuses (age-gated video, preview only, dead link):
                    # the next candidate is usually the same song somewhere else.
                    following = next_match()
                    if following is None:
                        raise
                    self.log(f"! {label}: не скачалось с {source or 'исходной ссылки'} "
                             f"({_error_text(e)}), пробую другой источник")
                    _clear_partials(folder, stem)
                    match, source = following, SOURCE_NAMES.get(following.source) or album.service
            _write_tags(path, album, track, cover)
            os.replace(path, target)
        except DownloadCancelled:
            self._track_event(track, "cancel")
            return None
        except Exception as e:  # one broken track must not stop the whole album
            message = _error_text(e)
            self.log(f"✗ {label}: {message}")
            self._track_event(track, "error", message)
            return "failed", f"{label}: {message}"
        finally:
            if not self.options.dry_run:
                _clear_partials(folder, stem)
        self.log(f"✓ {target.name}" + ("" if match.source == "song" else f"  ({source})"))
        self._track_event(track, "done", source=source)
        return "ok", label

    def _file_stem(self, album: Album, track: Track, single: bool) -> str:
        if single:
            return f"{track.artists} - {track.title}"
        number = f"{track.track_number:02d}"
        if album.total_discs > 1:
            number = f"{track.disc_number}-{number}"
        if album.artist.casefold() in track.artists.casefold():
            return f"{number}. {track.title}"
        return f"{number}. {track.artists} - {track.title}"

    def _fetch(self, match: Match, folder: Path, stem: str, track: Track, source: str) -> Path:
        self._track_event(track, "download", source=source, percent=0)
        path = self._download_audio(match, folder, stem, track, source)
        _check_duration(path, track)
        return path

    def _download_audio(self, match: Match, folder: Path, stem: str, track: Track, source: str) -> Path:
        audio_format = self.options.audio_format
        reported = -1

        def on_progress(status: dict) -> None:
            nonlocal reported
            if self.stop_event.is_set():
                raise DownloadCancelled("остановлено пользователем")
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            if status.get("status") == "downloading" and total:
                percent = min(99, int(status.get("downloaded_bytes", 0) * 100 / total))
                if percent >= reported + 5:  # the window does not need every chunk
                    reported = percent
                    self._track_event(track, "download", source=source, percent=percent)

        if match.source == "soundcloud":
            format_spec = "bestaudio/best"  # AAC 160k when available; previews rank last
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
            "logger": SilentLogger(),
            "progress_hooks": [on_progress],
        }
        if self.js_runtimes:
            opts["js_runtimes"] = self.js_runtimes
        if self.options.cookies_browser and match.source != "soundcloud":
            opts["cookiesfrombrowser"] = (self.options.cookies_browser,)
        if self.ffmpeg:
            opts["ffmpeg_location"] = self.ffmpeg
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                # applies only when re-encoding: best VBR for mp3, kbit/s otherwise
                "preferredquality": {"mp3": "0", "m4a": "192", "opus": "160"}[audio_format],
            }]
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([match.url])

        path = folder / f"{stem}.{audio_format}"
        if not path.exists():
            got = ", ".join(p.suffix for p in folder.glob(f"{stem}.*")) or "ничего"
            raise DownloaderError(f"ожидался файл .{audio_format}, получено: {got}")
        return path

    def _track_event(self, track: Track, state: str, text: str = "", source: str = "", **extra) -> None:
        self.events("track", {"id": track.id, "state": state, "text": text, "source": source, **extra})

    def _fetch_cover(self, url: str) -> bytes | None:
        if not url:
            return None
        request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as e:
            self.log(f"! Обложка не скачалась: {e}")
            return None


def _direct_match(track: Track) -> Match | None:
    """The track's own audio, when the link's service streams it openly."""
    if not track.audio_url:
        return None
    return Match(track.audio_source or "web", track.audio_url, track.audio_url,
                 track.title, track.artists, track.duration, 1.0)


def _clear_partials(folder: Path, stem: str) -> None:
    for leftover in folder.glob(f"{stem}.*"):
        leftover.unlink(missing_ok=True)


# yt-dlp explains itself in English and at length; these cases have a short answer
_KNOWN_ERRORS = (
    ("confirm your age", "видео с возрастным ограничением: нужен вход в аккаунт YouTube "
                         "(cookies браузера в настройках)"),
    ("age-restricted", "видео с возрастным ограничением: нужен вход в аккаунт YouTube "
                       "(cookies браузера в настройках)"),
    ("could not copy", "не удалось прочитать cookies: закройте браузер и повторите"),
    ("failed to decrypt", "не удалось расшифровать cookies браузера; в Firefox они читаются надёжнее"),
    ("no cookies found", "в браузере нет cookies YouTube: войдите в аккаунт в этом браузере"),
)


def _error_text(error: Exception) -> str:
    text = re.sub(r"^ERROR:\s*", "", str(error)).strip()
    lowered = text.lower()
    for needle, message in _KNOWN_ERRORS:
        if needle in lowered:
            return message
    return text or type(error).__name__


def _check_duration(path: Path, track: Track) -> None:
    """Rejects truncated audio, e.g. 30-second previews of SoundCloud Go+ tracks."""
    audio = MutagenFile(path)
    length = audio.info.length if audio is not None else 0
    if track.duration and 0 < length < track.duration * 0.9 - 2:
        raise DownloaderError(f"скачался фрагмент {_mmss(length)} вместо {_mmss(track.duration)}")


def _write_tags(path: Path, album: Album, track: Track, cover: bytes | None) -> None:
    track_total = sum(1 for t in album.tracks if t.disc_number == track.disc_number)
    mime = "image/png" if cover and cover.startswith(b"\x89PNG") else "image/jpeg"
    ext = path.suffix.lower()

    if ext == ".m4a":
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        tags["\xa9nam"] = track.title
        tags["\xa9ART"] = track.artists
        tags["\xa9alb"] = album.name
        tags["aART"] = album.artist
        tags["trkn"] = [(track.track_number, track_total)]
        tags["disk"] = [(track.disc_number, album.total_discs)]
        if album.release_date:
            tags["\xa9day"] = album.release_date
        if cover:
            image_format = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            tags["covr"] = [MP4Cover(cover, imageformat=image_format)]
        audio.save()

    elif ext == ".mp3":
        tags = ID3()
        tags.add(TIT2(encoding=3, text=track.title))
        tags.add(TPE1(encoding=3, text=track.artists))
        tags.add(TALB(encoding=3, text=album.name))
        tags.add(TPE2(encoding=3, text=album.artist))
        tags.add(TRCK(encoding=3, text=f"{track.track_number}/{track_total}"))
        tags.add(TPOS(encoding=3, text=f"{track.disc_number}/{album.total_discs}"))
        if album.release_date:
            tags.add(TDRC(encoding=3, text=album.release_date))
        if cover:
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
        tags.save(path, v2_version=3)  # ID3v2.3 for Windows Explorer and old players

    elif ext == ".opus":
        audio = OggOpus(path)
        audio["title"] = track.title
        audio["artist"] = track.artists
        audio["album"] = album.name
        audio["albumartist"] = album.artist
        audio["tracknumber"] = str(track.track_number)
        audio["tracktotal"] = str(track_total)
        audio["discnumber"] = str(track.disc_number)
        audio["disctotal"] = str(album.total_discs)
        if album.release_date:
            audio["date"] = album.release_date
        if cover:
            picture = Picture()
            picture.type, picture.mime, picture.desc, picture.data = 3, mime, "Cover", cover
            audio["metadata_block_picture"] = base64.b64encode(picture.write()).decode("ascii")
        audio.save()


def _safe_name(name: str, limit: int = 120) -> str:
    name = re.sub(r"[\x00-\x1f]", "", name.translate(_UNSAFE_CHARS))
    name = " ".join(name.split())[:limit].rstrip(". ")
    if name.split(".")[0].upper() in _RESERVED_NAMES:
        name = f"_{name}"
    return name or "_"


def _mmss(seconds: float) -> str:
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"
