"use strict";

// Russian and English for the window. The Russian line is the key, so the rest
// of the code reads as it always did and an English build looks it up here;
// anything missing falls back to Russian rather than to an empty box.
//
// Placeholders are named: {count}, {name}. The two languages are free to put
// them in different places.

const TRANSLATIONS = {
  // Sections and chrome
  "Разделы": "Sections",
  "Загрузка": "Download",
  "Библиотека": "Library",
  "Очередь": "Queue",
  "Настройки": "Settings",
  "Загрузка (Ctrl+1)": "Download (Ctrl+1)",
  "Библиотека (Ctrl+2)": "Library (Ctrl+2)",
  "Очередь (Ctrl+3)": "Queue (Ctrl+3)",
  "Настройки (Ctrl+4)": "Settings (Ctrl+4)",
  "Свернуть панель": "Collapse the panel",
  "Развернуть панель": "Expand the panel",
  "Развернуть панель (Ctrl+B)": "Expand the panel (Ctrl+B)",
  "Свернуть": "Collapse",

  "Нужны для треков с возрастным ограничением: YouTube отдаёт их только тем, кто вошёл в аккаунт. Браузер при загрузке лучше закрыть":
    "For age-restricted tracks: YouTube serves them only to a signed-in account. Close the browser while downloading",

  // Loudness
  "Выравнивание громкости": "Loudness levelling",
  "Теги ReplayGain: плеер сам сделает треки одинаково громкими. Звук не меняется; нужен плеер, который их читает — foobar2000, MusicBee, AIMP, VLC":
    "ReplayGain tags: the player makes the tracks equally loud. The audio is not changed; it takes a player that reads them — foobar2000, MusicBee, AIMP, VLC",
  "Выкл": "Off",
  "Вкл": "On",
  "Измеряем громкость…": "Measuring the loudness…",

  // Lists from a file
  "Скачать список из файла…": "Download a list from a file…",
  "В файле {name} не нашлось ни ссылок, ни названий": "{name} has neither links nor names in it",
  "Из {name} в очередь: {count}": "From {name} to the queue: {count}",
  "Из {name} в очередь: {count}, не разобрано строк: {skipped}":
    "From {name} to the queue: {count}, lines not understood: {skipped}",

  // Watching
  "Слежение": "Watching",
  "Плейлисты и альбомы под наблюдением": "Playlists and albums being watched",
  "Раз в 12 часов программа заново открывает ссылку и докачивает в ту же папку только появившиеся треки. Включается глазом на карточке загрузки":
    "Every 12 hours the link is opened again and only the tracks added since are downloaded, into the same folder. Turned on with the eye on a download card",
  "Проверить сейчас": "Check now",
  "Пока ни за чем не следим": "Nothing is being watched yet",
  "Следить за новыми треками": "Watch for new tracks",
  "Перестать следить": "Stop watching",
  "Следим за «{title}»": "Watching \"{title}\"",
  "Больше не следим за «{title}»": "No longer watching \"{title}\"",
  "Проверяем…": "Checking…",
  "Проверено {when}": "Checked {when}",
  "Проверено {when}, новых треков: {count}": "Checked {when}, new tracks: {count}",
  "Проверено {when}, новых треков нет": "Checked {when}, nothing new",
  "Проверяем плейлисты под наблюдением": "Checking the watched playlists",

  // Profiles
  "Профиль": "Profile",
  "Профили: папка, формат и имена одним нажатием": "Profiles: folder, format and naming in one click",
  "Профили": "Profiles",
  "Сохранить текущие настройки": "Save the current settings",
  "Папка, формат и правила имён под одним названием: потом возвращаться к ним одним нажатием в панели загрузки":
    "The folder, the format and the naming rules under one name, to come back to from the download toolbar in one click",
  "Например: на диск D, mp3": "For example: D drive, mp3",
  "Название профиля": "Profile name",
  "Сохранить": "Save",
  "Применить": "Apply",
  "Удалить профиль": "Delete the profile",
  "Профилей пока нет. Сохранить текущие настройки можно в разделе «Настройки»":
    "No profiles yet. The settings section saves the current ones",
  "Профиль «{name}»": "Profile \"{name}\"",
  "Профиль «{name}» сохранён": "Profile \"{name}\" saved",
  "Исполнитель - Альбом (Год)": "Artist - Album (Year)",
  "Исполнитель → Альбом (Год)": "Artist → Album (Year)",
  "Альбом (Год)": "Album (Year)",

  // Updating
  "Страница релиза": "Release page",
  "Обновить программу": "Update the program",
  "Скачается установщик с GitHub, программа сверит его хеш, закроется, обновится и откроется снова":
    "The installer is fetched from GitHub and checked against its hash; the program closes, updates and opens again",
  "Скачается установщик с GitHub, программа сверит его хеш и запустит; окно закроется, чтобы файлы можно было заменить":
    "The installer is fetched from GitHub, checked against its hash and started; the window closes so the files can be replaced",
  "Скачиваем…": "Downloading…",
  "Скачиваем… {percent}%": "Downloading… {percent}%",
  "Проверяем хеш…": "Checking the hash…",
  "Запускаем установщик": "Starting the installer",
  "Окно сейчас закроется: установщик заменит файлы и откроет новую версию сам":
    "The window is about to close: the installer replaces the files and opens the new version itself",
  "Окно сейчас закроется, чтобы установщик мог заменить файлы":
    "The window is about to close so the installer can replace the files",
  "Не получилось обновиться: {error}": "The update did not go through: {error}",

  // Download view
  "Ссылка": "Link",
  "Ссылка на трек, альбом или плейлист — или «Исполнитель - Альбом»":
    "A link to a track, album or playlist — or \"Artist - Album\"",
  "Вставить из буфера обмена": "Paste from the clipboard",
  "Формат": "Format",
  "Открыть папку": "Open the folder",
  "Скачать": "Download",
  "Проверить": "Check",
  "Режим": "Mode",
  "Только проверить, что найдётся": "Only check what would be found",
  "Загрузки": "Downloads",
  "Убрать завершённые": "Clear the finished ones",
  "Пауза": "Pause",
  "Продолжить": "Resume",
  "Остановить": "Stop",
  "Запустить": "Start",
  "Повторить": "Try again",
  "Загрузок нет": "No downloads",
  "Вставьте ссылку (Ctrl+V) или перетащите её в окно":
    "Paste a link (Ctrl+V) or drag one into the window",
  "Показать треки": "Show the tracks",
  "Скрыть треки": "Hide the tracks",
  "Прогресс": "Progress",
  "проверка": "check",
  "трек": "track",

  // Library view
  "Поиск": "Search",
  "Поиск по исполнителю или названию": "Search by artist or title",
  "Открыть папку в проводнике": "Open the folder in the file manager",
  "Обновить": "Refresh",
  "Обновить (F5)": "Refresh (F5)",
  "Скачать снова": "Download again",
  "Скачать снова: докачает недостающие треки":
    "Download again: fills in the tracks that are missing",
  "Удалить": "Delete",
  "Показать в проводнике": "Show in the file manager",
  "Альбомы и треки": "Albums and tracks",
  "Действия с выделенным": "Actions for the selection",
  "Название": "Title",
  "Год": "Year",
  "Треков": "Tracks",
  "Размер": "Size",
  "Изменён": "Changed",
  "В папке пока нет музыки": "No music in this folder yet",
  "Ничего не найдено": "Nothing found",
  "По запросу «{query}»": "For \"{query}\"",
  "Читаем папку…": "Reading the folder…",
  "Удалить {what}?": "Delete {what}?",
  "{size} уйдёт в корзину — оттуда файлы можно вернуть.":
    "{size} goes to the trash — files can be brought back from there.",
  "Отмена": "Cancel",
  "Удалено: {count}": "Deleted: {count}",
  "Не удалось удалить: {names}": "Could not delete: {names}",
  "Добавлено в очередь: {count}": "Added to the queue: {count}",
  "Выбрано: {count} · {size}": "Selected: {count} · {size}",
  "Найдено: {shown} из {total}": "Found: {shown} of {total}",
  "{albums} {albumWord}": "{albums} {albumWord}",
  "{tracks} {trackWord}": "{tracks} {trackWord}",
  "{count} {recordWord}": "{count} {recordWord}",

  // Queue view
  "Показать": "Show",
  "Все": "All",
  "В работе": "Running",
  "Готово": "Done",
  "Проблемы": "Problems",
  "Трек": "Track",
  "Релиз": "Release",
  "Источник": "Source",
  "Длит.": "Length",
  "Статус": "Status",
  "Очередь пуста": "The queue is empty",
  "Сейчас ничего не качается": "Nothing is downloading right now",
  "Готовых треков пока нет": "No finished tracks yet",
  "Проблемных треков нет": "No tracks in trouble",

  // Track and job states
  "В очереди": "Queued",
  "Ищем": "Searching",
  "Качаем": "Downloading",
  "Найден": "Found",
  "Уже есть": "Already here",
  "Не найден": "Not found",
  "Ошибка": "Error",
  "Отменён": "Cancelled",
  "Отменено": "Cancelled",
  "Остановлено": "Stopped",
  "Останавливаем…": "Stopping…",
  "Читаем ссылку…": "Reading the link…",
  "читаем ссылку…": "reading the link…",
  "Всё найдено": "Everything was found",
  "Уже скачано": "Already downloaded",
  "Всё уже было скачано раньше": "All of it had been downloaded before",
  "Файл уже есть в папке": "The file is already in the folder",
  "Нет в открытом доступе на YouTube Music и SoundCloud":
    "Not openly available on YouTube Music or SoundCloud",
  "Ошибка: {message}": "Error: {message}",
  "Остановлено · {done} из {total}": "Stopped · {done} of {total}",
  "{done} из {total} · {percent}%": "{done} of {total} · {percent}%",
  "Не найдено: {failed}": "Not found: {failed}",
  "Не скачано: {failed}": "Not downloaded: {failed}",
  "Найдено {ok} из {total} · не найдено: {failed}":
    "Found {ok} of {total} · not found: {failed}",
  "Найдены все треки: {ok}": "Every track was found: {ok}",
  "Скачано {ok} {trackWord}": "Downloaded {ok} {trackWord}",
  "уже были: {skipped}": "already here: {skipped}",
  "не удалось: {failed}": "failed: {failed}",
  "из «{album}»": "from \"{album}\"",

  // Status bar
  "Нет загрузок": "No downloads",
  "Проверено {done} из {total}": "Checked {done} of {total}",
  "Загружено {done} из {total}": "Downloaded {done} of {total}",
  "найдено {found} из {total}": "found {found} of {total}",
  "скачано {ok} {trackWord}": "downloaded {ok} {trackWord}",
  "осталось {eta}": "{eta} left",
  "ещё {queued} {linkWord} в очереди": "{queued} more {linkWord} queued",
  "{links} с ошибкой: {errors}": "{links} with an error: {errors}",
  "только проверка": "check only",
  "{mode} · {threads} {threadWord}": "{mode} · {threads} {threadWord}",
  "~{seconds} сек": "~{seconds} sec",
  "~{minutes} мин": "~{minutes} min",
  "~{hours} ч {minutes} мин": "~{hours} h {minutes} min",
  // Library tabs, sorting and pages
  "Альбомы": "Albums",
  "Треки": "Tracks",
  "Исполнители": "Artists",
  "Поиск в библиотеке": "Search the library",
  "Сортировка:": "Sort:",
  "Порядок:": "Order:",
  "Сортировка": "Sort",
  "Порядок": "Order",
  "Вид": "View",
  "Сетка обложек": "Cover grid",
  "Список": "List",
  "Докачать недостающее": "Fetch what is missing",
  "Следить": "Watch",
  "Следим": "Watching",
  "Время": "Time",
  "Треки альбома": "Album tracks",
  "по добавлению": "by date added",
  "по названию": "by title",
  "по исполнителю": "by artist",
  "по году": "by year",
  "по числу треков": "by track count",
  "по размеру": "by size",
  "по альбому": "by album",
  "по длительности": "by length",
  "по имени": "by name",
  "по числу альбомов": "by album count",
  "А → Я": "A → Z",
  "Я → А": "Z → A",
  "новые сверху": "newest first",
  "старые сверху": "oldest first",
  "больше сверху": "largest first",
  "меньше сверху": "smallest first",
  "Без исполнителя": "No artist",
  "Диск {number}": "Disc {number}",
  "нет в папке": "not in the folder",
  "Ещё {count} {trackWord} нет в папке": "{count} more {trackWord} not in the folder",
  "{count} {albumWord}": "{count} {albumWord}",
  "{count} {trackWord}": "{count} {trackWord}",
  "{count} {artistWord}": "{count} {artistWord}",
  "{minutes} мин": "{minutes} min",
  "{hours} ч {minutes} мин": "{hours} h {minutes} min",
  "{value} КБ": "{value} KB",
  "{value} МБ": "{value} MB",
  "{value} ГБ": "{value} GB",

  // Settings
  "Внешний вид": "Appearance",
  "Тема": "Theme",
  "Как в системе": "As in the system",
  "Светлая": "Light",
  "Тёмная": "Dark",
  "Язык": "Language",
  "Русский": "Русский",
  "English": "English",
  "Треков одновременно": "Tracks at once",
  "Больше — быстрее, но YouTube может начать ограничивать скорость":
    "More is faster, but YouTube may start throttling",
  "Больше": "More",
  "Меньше": "Fewer",
  "Папки альбомов": "Album folders",
  "Как называть папку альбома внутри папки для музыки":
    "How to name an album's folder inside the music folder",
  "Исполнитель - Альбом (Год)": "Artist - Album (Year)",
  "Исполнитель → Альбом (Год)": "Artist → Album (Year)",
  "Альбом (Год)": "Album (Year)",
  "Имена файлов": "File names",
  "Номер и название всегда на месте; различается только исполнитель":
    "The number and the title are always there; only the performer differs",
  "01. Название (гости — отдельно)": "01. Title (guests named separately)",
  "01. Исполнитель - Название": "01. Artist - Title",
  "01. Название": "01. Title",
  "Ограничение скорости": "Speed limit",
  "Чтобы загрузка не занимала весь канал": "So a download does not take the whole line",
  "Без ограничения": "No limit",
  "500 КБ/с": "500 KB/s",
  "1 МБ/с": "1 MB/s",
  "2 МБ/с": "2 MB/s",
  "5 МБ/с": "5 MB/s",
  "10 МБ/с": "10 MB/s",
  "Прокси": "Proxy",
  "Для метаданных и загрузки:": "For metadata and downloads:",
  "или": "or",
  ". Пусто — как настроено в системе": ". Empty means the system's own setting",
  "Cookies из браузера": "Cookies from a browser",
  "Не использовать": "Do not use any",
  "Компоненты": "Components",
  "В буфере обмена пусто. Скопируйте ссылку на альбом или трек: «Поделиться» → «Копировать ссылку».":
    "The clipboard is empty. Copy a link to an album or a track: \"Share\" → \"Copy link\".",
  "Всё необходимое установлено": "Everything needed is installed",
  "Не хватает компонентов": "Components are missing",
  "ffmpeg, Deno или Node.js, yt-dlp-ejs": "ffmpeg, Deno or Node.js, yt-dlp-ejs",
  "Диагностика": "Diagnostics",
  "YouTube меняется часто: если загрузки перестали работать, сначала обновите Trackhound":
    "YouTube changes often: if downloads stop working, update Trackhound first",
  "Скачиванием занимается yt-dlp; он обновляется вместе с Trackhound":
    "The downloading is done by yt-dlp, which is updated together with Trackhound",
  "Сборке {days} {dayWord} — YouTube за это время обычно успевает смениться. Если загрузки перестали работать, обновите Trackhound":
    "This build is {days} {dayWord} old — YouTube usually changes within that. "
    + "If downloads stopped working, update Trackhound",
  "Журнал работы": "Log",
  "Подробности загрузок и ошибок — здесь. Никуда не отправляется":
    "The detail of downloads and errors lives here. It is never sent anywhere",
  "Открыть": "Open",
  "Скопировать отчёт": "Copy the report",
  "Скопировано": "Copied",
  "Не удалось скопировать": "Could not copy",
  "Отчёт скопирован в буфер обмена": "The report is on the clipboard",
  "Не удалось скопировать отчёт": "The report could not be copied",
  "О программе": "About",
  "Доступна версия {version}": "Version {version} is available",
  "Скачайте новую версию со страницы релизов и распакуйте поверх старой":
    "Download the new version from the releases page and unpack it over the old one",
  "Открыть страницу": "Open the page",
  "Названия, обложки и номера треков берутся со страницы по ссылке; звук — оттуда же, если он открыт, или с YouTube Music и SoundCloud":
    "Titles, covers and track numbers come from the page behind the link; the audio comes "
    + "from there too when it is open, or from YouTube Music and SoundCloud",
  "Папка для музыки: {folder}": "Music folder: {folder}",
  "{folder}\nНажмите, чтобы выбрать другую папку": "{folder}\nClick to choose another folder",
  "Проблемы: {count}": "Problems: {count}",

  // Messages
  "Вставьте ссылку на альбом, сингл или трек — или напишите, что искать: «Исполнитель - Альбом».":
    "Paste a link to an album, a single or a track — or write what to look for: \"Artist - Album\".",
  "В буфере обмена пусто. Скопируйте ссылку на альбом или трек: «Поделиться» → «Копировать ссылку».":
    "The clipboard is empty. Copy a link to an album or a track: \"Share\" → \"Copy link\".",
  "Интерфейс не запустился: {error}": "The interface did not start: {error}",
  "Загрузка на паузе": "The download is paused",
  "Загрузка продолжается": "The download continues",
  "m4a — AAC 256 кбит/с из полной дорожки, до 20 кГц. Подходит почти всем плеерам":
    "m4a — AAC 256 kbit/s from the full-band stream, up to 20 kHz. Almost every player takes it",
  "mp3 — 320 кбит/с из полной дорожки, до 20 кГц. Играет везде, включая магнитолы":
    "mp3 — 320 kbit/s from the full-band stream, up to 20 kHz. Plays anywhere, car stereos included",
  "opus — дорожка YouTube как есть, без перекодирования. Понимают его не все плееры":
    "opus — YouTube's stream as it is, no re-encoding. Not every player understands it",
};

// Russian has three plural forms and English two. The Russian singular is the
// key here, the same word the code passes to plural().
const PLURALS = {
  "трек": ["track", "tracks"],
  "альбом": ["album", "albums"],
  "исполнитель": ["artist", "artists"],
  "запись": ["item", "items"],
  "ссылка": ["link", "links"],
  "поток": ["thread", "threads"],
  "день": ["day", "days"],
};

// Two Russian lines can share one English word ("Загрузка" and "Скачать" are
// both "Download"), so the first one wins here and the markup is remembered
// word for word below — the reverse map is only a fallback.
const RUSSIAN_BY_ENGLISH = {};
for (const [russian, english] of Object.entries(TRANSLATIONS)) {
  if (!(english in RUSSIAN_BY_ENGLISH)) RUSSIAN_BY_ENGLISH[english] = russian;
}

// What the markup said before any translation: nodes and attributes as written
const ORIGINAL_TEXT = new WeakMap();
const ORIGINAL_ATTRS = new WeakMap();

let LANGUAGE = "ru";

function setLanguage(code) {
  LANGUAGE = code === "en" ? "en" : "ru";
  applyLanguage();
  return LANGUAGE;
}

function language() {
  return LANGUAGE;
}

// The line in the current language, with {named} placeholders filled in
function t(text, values) {
  const russian = RUSSIAN_BY_ENGLISH[text] || text;
  const line = LANGUAGE === "en" ? (TRANSLATIONS[russian] || russian) : russian;
  if (!values) return line;
  return line.replace(/\{(\w+)\}/g, (whole, name) => (name in values ? values[name] : whole));
}

function plural(count, one, few, many) {
  if (LANGUAGE === "en") {
    const forms = PLURALS[one];
    return forms ? forms[count === 1 ? 0 : 1] : one;
  }
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

const TRANSLATED_ATTRIBUTES = ["placeholder", "title", "aria-label"];

// Everything written in the markup, translated in place. Text the script
// writes goes through t() instead, so both directions of a switch work.
function applyLanguage(root = document) {
  document.documentElement.lang = LANGUAGE;
  for (const template of root.querySelectorAll("template")) applyLanguage(template.content);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const shown = node.nodeValue.trim();
    if (!shown) continue;
    if (!ORIGINAL_TEXT.has(node)) ORIGINAL_TEXT.set(node, shown);
    // Long sentences are wrapped in the markup, so the text node carries the
    // newline and the indent with it. The keys here are single lines, so the
    // lookup is done on a single-spaced copy; without it every wrapped
    // description stayed in Russian.
    const original = ORIGINAL_TEXT.get(node).replace(/\s+/g, " ");
    node.nodeValue = node.nodeValue.replace(shown, t(original));
  }
  for (const element of root.querySelectorAll("*")) {
    let originals = ORIGINAL_ATTRS.get(element);
    if (!originals) ORIGINAL_ATTRS.set(element, originals = {});
    for (const name of TRANSLATED_ATTRIBUTES) {
      const value = element.getAttribute(name);
      if (!value) continue;
      if (!(name in originals)) originals[name] = value;
      element.setAttribute(name, t(originals[name]));
    }
  }
}
