"""The lines the window and the command line say, added to the engine's table.

The lookup itself and the engine's own lines live in trackhound.engine.i18n;
importing this module is what teaches that table the rest of the program.
"""

from __future__ import annotations

from .engine.i18n import ENGLISH, LANGUAGES, language, resolve, set_language, t

__all__ = ["ENGLISH", "LANGUAGES", "language", "resolve", "set_language", "t"]

# Russian → English. The window has its own copy of its lines in web/i18n.js.
ENGLISH.update({
    # Command line
    "Скачивание альбомов, синглов и треков по ссылке из Spotify, Apple Music, "
    "Deezer, YouTube, SoundCloud, Last.fm и сайтов вроде Bandcamp.":
        "Downloads albums, singles and tracks by link from Spotify, Apple Music, "
        "Deezer, YouTube, SoundCloud, Last.fm and sites yt-dlp understands, such as Bandcamp.",
    "ссылки на альбомы, плейлисты или треки — либо название: «Исполнитель - Альбом»":
        "links to albums, playlists or tracks — or a name: \"Artist - Album\"",
    "папка для музыки (по умолчанию {path})": "music folder (default {path})",
    "формат файлов: mp3 (по умолчанию, 320 кбит/с), m4a (AAC 256), opus":
        "file format: mp3 (the default, 320 kbit/s), m4a (AAC 256), opus",
    "сколько треков качать одновременно (по умолчанию 3)":
        "how many tracks to download at once (default 3)",
    "только показать найденные совпадения, ничего не скачивать":
        "only show what was matched, download nothing",
    "брать cookies из браузера (chrome, edge, firefox…): нужно для видео "
    "с возрастным ограничением":
        "take cookies from a browser (chrome, edge, firefox…): needed for age-restricted videos",
    "имена файлов: auto — исполнитель только у гостей, artist — всегда, "
    "title — только номер и название":
        "file names: auto — performer only for guests, artist — always, "
        "title — number and title only",
    "папки альбомов: flat — «Исполнитель - Альбом (Год)», "
    "nested — «Исполнитель\\Альбом (Год)», album — «Альбом (Год)»":
        "album folders: flat — \"Artist - Album (Year)\", "
        "nested — \"Artist\\Album (Year)\", album — \"Album (Year)\"",
    "ограничить скорость: 500K, 2M (по умолчанию без ограничения)":
        "limit the speed: 500K, 2M (default: no limit)",
    "прокси для метаданных и загрузки: http://127.0.0.1:1080, socks5://…":
        "proxy for metadata and downloads: http://127.0.0.1:1080, socks5://…",
    "язык интерфейса и сообщений": "language of the interface and the messages",
    "показать версию, какие ffmpeg и Deno нашлись и что открывается из этой сети, ничего не скачивая":
        "print the version, which ffmpeg and Deno were found and what opens from this network, "
        "download nothing",
    "проверяем сеть…": "checking the network…",
    "открывается": "opens",
    "плеер закрыт, релизы — со страниц-превью": "the player is closed, releases come from the preview pages",
    "не открывается": "does not open",
    "прокси {client}: {url} (Spotify через него: {state}) — --proxy {url}":
        "{client} proxy: {url} (Spotify through it: {state}) — --proxy {url}",
    "АДРЕС": "ADDRESS",
    "БРАУЗЕР": "BROWSER",
    "СКОРОСТЬ": "SPEED",
    "ЯЗЫК": "LANGUAGE",
    "непонятная скорость: {value}. Примеры: 500K, 2M":
        "unreadable speed: {value}. For example: 500K, 2M",
    "укажите ссылку или название (или --check, чтобы проверить установку)":
        "give a link or a name (or --check to inspect the installation)",
    "папка для музыки: {path}": "music folder: {path}",
    "журнал: {path}": "log: {path}",
    "не найден": "not found",
    "\nОстановлено.": "\nStopped.",
    "измерить громкость и записать теги ReplayGain; сам звук не меняется":
        "measure the loudness and write ReplayGain tags; the audio itself is not changed",

    # Lists of links read from a file
    "ФАЙЛ": "FILE",
    "взять ссылки и названия из файла: .txt по одной на строку или CSV-выгрузка плейлиста":
        "take links and names from a file: a .txt with one per line, or a playlist exported as CSV",
    "не прочитать {file}: {error}": "cannot read {file}: {error}",
    "{file}: взято {count}, не разобрано строк: {skipped}": "{file}: {count} taken, {skipped} lines not understood",
    "Списки ссылок (*.txt;*.csv)": "Lists of links (*.txt;*.csv)",
    "Все файлы (*.*)": "All files (*.*)",
    "Файл слишком большой для списка ссылок": "The file is too large to be a list of links",

    # Updating from inside the window
    "Ссылка на установщик не с GitHub": "The installer link does not point at GitHub",
    "GitHub не сообщил хеш установщика": "GitHub did not report a hash for the installer",
    "Скачанный установщик не совпал с хешем на GitHub":
        "The downloaded installer does not match the hash GitHub published",

    # The window itself
    "Для окна программы нужен компонент Microsoft Edge WebView2, его нет в системе.\n\n"
    "Скачать и установить его сейчас? Установщик официальный, с сайта Microsoft.":
        "The program's window needs the Microsoft Edge WebView2 component, which this "
        "system does not have.\n\nDownload and install it now? The installer is the "
        "official one, from Microsoft.",
    "Не удалось установить WebView2 ({error}).\n\nСкачайте его вручную: "
    "https://go.microsoft.com/fwlink/p/?LinkId=2124703":
        "WebView2 could not be installed ({error}).\n\nDownload it by hand: "
        "https://go.microsoft.com/fwlink/p/?LinkId=2124703",
    "не удалось удалить {path}": "could not delete {path}",
    "не удалось удалить {path}: {error}": "could not delete {path}: {error}",

    # The log and the report
    "настройки: формат {format}, потоков {threads}, cookies: {cookies}":
        "settings: format {format}, {threads} threads, cookies: {cookies}",
    "не используются": "not used",
    "папка: {folder}": "folder: {folder}",
    "проблема: {problem}": "problem: {problem}",
    "не установлен": "not installed",
    "сборка": "built",
    "из исходников": "from source",
    "\n--- {path} (последние {lines} строк) ---": "\n--- {path} (last {lines} lines) ---",
})
