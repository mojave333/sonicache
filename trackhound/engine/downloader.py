"""Downloading matched tracks with yt-dlp and tagging them with mutagen."""

from __future__ import annotations

import base64
import importlib.util
import json
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

from . import loudness, sources
from .i18n import t
from .logs import YtdlpLogger, log
from .matcher import SOURCE_NAMES, Match, Matcher
from .models import Album, Track
from .net import BROWSER_UA

FORMATS = ("m4a", "mp3", "opus")
# How a track is named inside an album folder
TRACK_NAMES = ("auto", "artist", "title")
# How the album folder itself is named, inside the music folder
FOLDER_NAMES = ("flat", "nested", "album")
# Written into every album folder: where the album came from
MARKER_NAME = ".trackhound.json"
DEFAULT_OUTPUT_DIR = Path.home() / "Music" / "Trackhound"
# A 1000x1000 cover is about a megabyte; past this it is not artwork any more
MAX_COVER_BYTES = 8 * 1024 * 1024
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
    audio_format: str = "m4a"
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


@dataclass
class Report:
    ok: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def summary(self, dry_run: bool = False) -> str:
        text = t("{done}: {ok}, уже было: {skipped}, ошибок: {failed}",
                 done=t("найдено") if dry_run else t("скачано"), ok=len(self.ok),
                 skipped=len(self.skipped), failed=len(self.failed))
        return "\n".join([text, *(f"  ✗ {item}" for item in self.failed)])


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
            raise DownloaderError(t("Неизвестный формат: {format}",
                                    format=self.options.audio_format))
        if not self.ffmpeg and self.options.audio_format != "m4a" and not self.options.dry_run:
            raise DownloaderError(t("Для mp3 и opus нужен ffmpeg (winget install Gyan.FFmpeg)"))

        release = sources.resolve(link)
        album, tracks, single = release.album, release.tracks, release.single
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
            self._tag_loudness(album, tracks, single)
        except BaseException:
            self.stop_event.set()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        return report

    def _process(self, album: Album, track: Track, folder: Path, single: bool,
                 cover: bytes | None) -> tuple[str, str] | None:
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
                self.log(t("✗ Не найдено ни на YouTube Music, ни на SoundCloud: {label}",
                           label=label))
                self._track_event(track, "missing")
                return "failed", t("{label}: не найдено ни на YouTube Music, ни на SoundCloud",
                                   label=label)
            source = t(SOURCE_NAMES.get(match.source, "")) or album.service
            if self.options.dry_run:
                self.log(t("? {label}  →  {artists} - {title} [{source}, {got} / {wanted}, "
                           "оценка {score}] {url}", label=label, artists=match.artists,
                           title=match.title, source=source, got=_mmss(match.duration),
                           wanted=_mmss(track.duration), score=match.score, url=match.page_url))
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
                    self.log(t("! {label}: не скачалось с {source} ({error}), "
                               "пробую другой источник", label=label,
                               source=source or t("исходной ссылки"), error=_error_text(e)))
                    _clear_partials(folder, stem)
                    match, source = following, t(SOURCE_NAMES.get(following.source, "")) or album.service
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
                raise DownloadCancelled(t("остановлено пользователем"))
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
            "logger": YtdlpLogger("download"),
            "progress_hooks": [on_progress],
        }
        if self.js_runtimes:
            opts["js_runtimes"] = self.js_runtimes
        if self.options.rate_limit:
            # Shared between threads, so the limit is what the program uses in total
            opts["ratelimit"] = self.options.rate_limit / max(1, self.options.threads)
        if self.options.proxy:
            opts["proxy"] = self.options.proxy
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
            got = ", ".join(p.suffix for p in folder.glob(f"{stem}.*")) or t("ничего")
            raise DownloaderError(t("ожидался файл .{format}, получено: {got}",
                                    format=audio_format, got=got))
        return path

    def _track_event(self, track: Track, state: str, text: str = "", source: str = "", **extra) -> None:
        self.events("track", {"id": track.id, "state": state, "text": text, "source": source, **extra})

    def _fetch_cover(self, url: str) -> bytes | None:
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
            self.log(f"! Обложка не скачалась: {e}")
            return None
        if len(data) > MAX_COVER_BYTES:
            self.log(f"! Обложка больше {MAX_COVER_BYTES // (1024 * 1024)} МБ, пропускаю: {url}")
            return None
        if not data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
            self.log(f"! По адресу обложки не JPEG и не PNG, пропускаю: {url}")
            return None
        return data


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

    The metadata comes through urllib and requests, the audio through yt-dlp;
    all three read these variables, which keeps one setting enough.
    """
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if proxy:
            os.environ[name] = proxy
        else:
            os.environ.pop(name, None)


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
        "tracks": len(album.tracks),
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
        raise DownloaderError(t("скачался фрагмент {got} вместо {expected}",
                                got=_mmss(length), expected=_mmss(track.duration)))


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
