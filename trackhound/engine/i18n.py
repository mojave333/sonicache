"""Russian and English text, and the lines the engine itself says.

The Russian string is the key: code reads as it always did, and an English
build simply looks the line up here. Anything missing from the table falls
back to Russian rather than to a key nobody can read.

Placeholders are named, so the two languages can put them in different places:
    t("Не найден альбом {name}", name=album.name)
"""

from __future__ import annotations

import locale
import os

LANGUAGES = ("system", "ru", "en")

_current = "ru"

# Russian → English, for what the engine says. The program adds its own lines
# to this table in trackhound/i18n.py.
ENGLISH = {
    # Network and services
    "{service} не нашёл страницу: {url}": "{service} has no such page: {url}",
    "{service} ответил HTTP {code}: {url}": "{service} answered HTTP {code}: {url}",
    "Нет связи с {service}: {error}": "Cannot reach {service}: {error}",
    "{service} вернул непонятный ответ: {url}":
        "{service} answered with something unreadable: {url}",
    "поиск на {source} не удался: {error}": "the search on {source} failed: {error}",
    "YouTube-видео": "YouTube video",

    # Spotify
    "Не похоже на ссылку на альбом, трек или плейлист Spotify: {link}":
        "This does not look like a Spotify album, track or playlist link: {link}",
    "Не удалось получить данные Spotify для {kind}/{id}: ссылка неверна или "
    "Spotify изменил формат страницы":
        "Could not read Spotify data for {kind}/{id}: the link is wrong, or Spotify "
        "changed the shape of its page",
    "В альбоме {id} не найдено треков": "No tracks in album {id}",
    "В плейлисте {id} не найдено треков или он закрыт":
        "Playlist {id} has no tracks, or it is private",
    "Не удалось открыть короткую ссылку {link}: {error}":
        "Could not open the short link {link}: {error}",
    "Spotify отдаёт по ссылке первые {limit} треков плейлиста. Если их больше, "
    "остальные придётся добавить отдельно":
        "Spotify hands over the first {limit} tracks of a playlist. If it holds more, "
        "the rest have to be added separately",
    "Разные исполнители": "Various artists",

    # Apple Music, YouTube, Last.fm and the rest
    "Из Apple Music поддерживаются ссылки на альбомы, песни и плейлисты":
        "From Apple Music, links to albums, songs and playlists are supported",
    "Apple Music не нашёл песню {id}: возможно, её нет в регионе {country}":
        "Apple Music has no song {id}: it may not exist in the {country} store",
    "Apple Music не нашёл альбом {id}: возможно, его нет в регионе {country}":
        "Apple Music has no album {id}: it may not exist in the {country} store",
    "Песни {id} нет в альбоме «{album}»": "Song {id} is not part of \"{album}\"",
    "В альбоме Apple Music {id} нет доступных песен":
        "Apple Music album {id} has no songs available",
    "В плейлисте Apple Music нет доступных песен":
        "This Apple Music playlist has no songs available",
    "Не удалось прочитать страницу Apple Music: ссылка неверна или страница изменила формат":
        "Could not read the Apple Music page: the link is wrong, or the page changed shape",
    "Страница Apple Music отдаёт первые {shown} треков из {total}. "
    "Остальные придётся добавить отдельно":
        "The Apple Music page hands over the first {shown} tracks of {total}. "
        "The rest have to be added separately",
    "Из Deezer поддерживаются ссылки на альбомы, треки и плейлисты":
        "From Deezer, links to albums, tracks and playlists are supported",
    "Короткая ссылка Deezer никуда не ведёт: {link}": "This Deezer short link leads nowhere: {link}",
    "Deezer не нашёл альбом {id}": "Deezer has no album {id}",
    "Deezer не нашёл трек {id}": "Deezer has no track {id}",
    "Deezer не нашёл плейлист {id}: он удалён или закрыт":
        "Deezer has no playlist {id}: it was deleted or is private",
    "В альбоме Deezer {id} нет треков": "Deezer album {id} has no tracks",
    "Трека {id} нет в альбоме «{album}»": "Track {id} is not part of \"{album}\"",
    "Deezer ответил ошибкой: {error}": "Deezer answered with an error: {error}",
    "Из YouTube поддерживаются ссылки на видео, альбомы и плейлисты":
        "From YouTube, links to videos, albums and playlists are supported",
    "В альбоме YouTube Music нет треков": "This YouTube Music album has no tracks",
    "Плейлист пуст или закрыт": "The playlist is empty or private",
    "YouTube не нашёл видео {id}": "YouTube has no video {id}",
    "YouTube Music не отдал {what}: {error}": "YouTube Music would not give the {what}: {error}",
    "альбом": "album",
    "плейлист": "playlist",
    "видео": "video",
    "сингл": "single",
    "сборник": "compilation",
    "трек": "track",
    "поиск": "search",
    "Из Last.fm поддерживаются ссылки на альбомы и треки, а не на исполнителей":
        "From Last.fm, links to albums and tracks are supported, not to artists",
    "Не нашёл треки альбома «{album}» ({artist}): его нет на YouTube Music и в Apple Music, "
    "а на Last.fm у него нет списка треков":
        "Found no tracks for \"{album}\" ({artist}): it is on neither YouTube Music nor "
        "Apple Music, and its Last.fm page carries no track list",
    "В ссылке нет названия трека": "The link carries no track name",
    "Вставьте ссылку или напишите, что искать: «Исполнитель - Альбом»":
        "Paste a link, or write what to look for: \"Artist - Album\"",
    "Не похоже на ссылку: {link}": "This does not look like a link: {link}",
    "По этой ссылке не нашлось музыки. Подойдут ссылки из {services}":
        "No music behind that link. Links from {services} will do",
    "Не удалось открыть ссылку: {error}": "Could not open the link: {error}",
    "Spotify, Apple Music, Deezer, YouTube, SoundCloud, Last.fm и сайтов вроде Bandcamp":
        "Spotify, Apple Music, Deezer, YouTube, SoundCloud, Last.fm and sites such as Bandcamp",
    "VK показывает музыку только после входа в аккаунт, поэтому альбом по этой ссылке "
    "не прочитать. Найдите этот релиз в Spotify, Apple Music, Deezer, YouTube или "
    "SoundCloud и вставьте ссылку оттуда.":
        "VK only shows music to a signed-in account, so an album cannot be read from that "
        "link. Find the release on Spotify, Apple Music, Deezer, YouTube or SoundCloud and paste "
        "that link instead.",
    "Плейлист": "Playlist",

    # Downloading
    "Неизвестный формат: {format}": "Unknown format: {format}",
    "Для mp3 и opus нужен ffmpeg (winget install Gyan.FFmpeg)":
        "mp3 and opus need ffmpeg (winget install Gyan.FFmpeg)",

    # Loudness
    "! Громкость не выровнена: для этого нужен ffmpeg": "! Loudness not measured: that needs ffmpeg",
    "♫ Громкость измерена: {count}": "♫ Loudness measured: {count}",

    # Progress and results of a download
    "Не найден ffmpeg: форматы mp3/opus недоступны, m4a может не сохраниться. "
    "Установка: winget install Gyan.FFmpeg":
        "ffmpeg not found: mp3 and opus are unavailable and m4a may fail to save. "
        "Install it with: winget install Gyan.FFmpeg",
    "Не найден Deno или Node.js 22+: YouTube может не отдавать аудио. "
    "Установка: winget install DenoLand.Deno":
        "Neither Deno nor Node.js 22+ found: YouTube may refuse to hand over audio. "
        "Install it with: winget install DenoLand.Deno",
    "Не установлен пакет yt-dlp-ejs: pip install -U yt-dlp-ejs":
        "The yt-dlp-ejs package is missing: pip install -U yt-dlp-ejs",
    "год неизвестен": "year unknown",
    "из «{album}»": "from \"{album}\"",
    "♪ {artist} — {title} ({details})": "♪ {artist} — {title} ({details})",
    "♪ {artist} — {album} ({kind}, {year}, треков: {tracks}, {service})":
        "♪ {artist} — {album} ({kind}, {year}, tracks: {tracks}, {service})",
    "= Уже есть: {name}": "= Already here: {name}",
    "✗ Не найдено ни на YouTube Music, ни на SoundCloud: {label}":
        "✗ Found on neither YouTube Music nor SoundCloud: {label}",
    "не найдено ни на YouTube Music, ни на SoundCloud": "found on neither YouTube Music nor SoundCloud",
    "! {label}: не скачалось с {source} ({error}), пробую другой источник":
        "! {label}: {source} would not hand it over ({error}), trying another source",
    "исходной ссылки": "the original link",
    "! Обложка не скачалась: {error}": "! The cover did not download: {error}",
    "! Обложка больше {limit} МБ, пропускаю: {url}":
        "! The cover is larger than {limit} MB, skipping it: {url}",
    "! По адресу обложки не JPEG и не PNG, пропускаю: {url}":
        "! What is at the cover address is neither JPEG nor PNG, skipping it: {url}",
    "ожидался файл .{format}, получено: {got}": "expected a .{format} file, got: {got}",
    "ничего": "nothing",
    "скачался фрагмент {got} вместо {expected}":
        "only {got} of {expected} was downloaded",
    "остановлено пользователем": "stopped by the person at the keyboard",
    "видео с возрастным ограничением: нужен вход в аккаунт YouTube "
    "(cookies браузера в настройках)":
        "age-restricted video: it needs a signed-in YouTube account "
        "(browser cookies, in the settings)",
    "не удалось прочитать cookies: закройте браузер и повторите":
        "could not read the cookies: close the browser and try again",
    "не удалось расшифровать cookies браузера; в Firefox они читаются надёжнее":
        "could not decrypt the browser cookies; Firefox's are read more reliably",
    "в браузере нет cookies YouTube: войдите в аккаунт в этом браузере":
        "the browser holds no YouTube cookies: sign in with that browser first",
    "скачано": "downloaded",
    "найдено": "found",
    "{done}: {ok}, уже было: {skipped}, ошибок: {failed}":
        "{done}: {ok}, already here: {skipped}, failed: {failed}",
    "? {label}  →  {artists} - {title} [{source}, {got} / {wanted}, оценка {score}] {url}":
        "? {label}  →  {artists} - {title} [{source}, {got} / {wanted}, score {score}] {url}",
}


def resolve(setting: str) -> str:
    """Turns the setting ("system", "ru", "en") into a language to speak."""
    if setting in ("ru", "en"):
        return setting
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(name)
        if value:
            return "ru" if value.lower().startswith("ru") else "en"
    try:
        code = locale.getlocale()[0] or ""
    except ValueError:
        code = ""
    if not code:
        try:
            code = locale.getdefaultlocale()[0] or ""  # noqa: DEP004 — the fallback path
        except (AttributeError, ValueError):
            code = ""
    return "ru" if code.lower().startswith(("ru", "russian")) else "en"


def set_language(setting: str) -> str:
    global _current
    _current = resolve(setting)
    return _current


def language() -> str:
    return _current


def t(text: str, **values) -> str:
    """The line in the current language, with its placeholders filled in."""
    line = ENGLISH.get(text, text) if _current == "en" else text
    return line.format(**values) if values else line


# A script that never chooses gets the system's language; the program sets its
# own from the settings before it says anything.
_current = resolve("system")
