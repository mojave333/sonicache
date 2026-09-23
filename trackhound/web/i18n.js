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
  "Библиотека (Ctrl+3)": "Library (Ctrl+3)",
  "Настройки (Ctrl+4)": "Settings (Ctrl+4)",
  "Свернуть панель": "Collapse the panel",
  "Развернуть панель": "Expand the panel",
  "Развернуть панель (Ctrl+B)": "Expand the panel (Ctrl+B)",
  "Свернуть": "Collapse",

  "Нужны для треков с возрастным ограничением: YouTube отдаёт их только после входа в аккаунт. Пока идёт загрузка, браузер лучше закрыть":
    "Needed for age-restricted tracks: YouTube only serves them to a signed-in account. Close the browser while downloading",

  // Loudness
  "Выравнивание громкости": "Loudness levelling",
  "Записывает теги ReplayGain, по которым плеер делает треки одинаково громкими. Сам звук не меняется. Такие теги читают foobar2000, MusicBee, AIMP и VLC":
    "Writes ReplayGain tags, which a player uses to play every track at the same volume. The audio itself is not changed. foobar2000, MusicBee, AIMP and VLC read these tags",
  "Выкл": "Off",
  "Вкл": "On",
  "Ищем источник": "Looking for a source",
  "Выравниваем громкость": "Levelling the loudness",

  // Lists from a file
  "Скачать список из файла…": "Download a list from a file…",
  "В файле {name} не нашлось ни ссылок, ни названий": "{name} has neither links nor names in it",
  "Из {name} в очередь: {count}": "From {name} to the queue: {count}",
  "Из {name} в очередь: {count}, не разобрано строк: {skipped}":
    "From {name} to the queue: {count}, lines not understood: {skipped}",

  // Watching
  "Слежение": "Watching",
  "Плейлисты, альбомы и исполнители под наблюдением":
    "Watched playlists, albums and artists",
  "Раз в 12 часов программа снова открывает ссылку и докачивает в ту же папку только новое. Чтобы следить за релизом, нажмите на глаз на его карточке загрузки. За исполнителем можно следить с его страницы в поиске":
    "Every 12 hours the program opens the link again and downloads only what is new, into the same folder. To watch a release, press the eye on its download card. An artist can be watched from their page in Search",
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
  "Запоминает папку, формат и правила имён под одним названием. Вернуть их можно одним нажатием в панели загрузки":
    "Keeps the folder, the format and the naming rules under one name, to bring back with one click in the download toolbar",
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
  "Программа скачает установщик с GitHub и сверит его хеш. Потом она закроется, обновится и откроется снова":
    "The program downloads the installer from GitHub and checks its hash. Then it closes, updates and opens again",
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
  "Ищем везде": "Searching everywhere",
  "Качаем": "Downloading",
  "Пробуем {source}": "Trying {source}",
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
  "Проверено {done} из {total}": "Checked {done} of {total}",
  "Загружено {done} из {total}": "Downloaded {done} of {total}",
  "найдено {found} из {total}": "found {found} of {total}",
  "скачано {ok} {trackWord}": "downloaded {ok} {trackWord}",
  "осталось {eta}": "{eta} left",
  "ещё {queued} {linkWord} в очереди": "{queued} more {linkWord} queued",
  "{links} с ошибкой: {errors}": "{links} with an error: {errors}",
  "~{seconds} сек": "~{seconds} sec",
  "~{minutes} мин": "~{minutes} min",
  "~{hours} ч {minutes} мин": "~{hours} h {minutes} min",
  // Library tabs, sorting and pages
  "Альбомы": "Albums",
  "Треки": "Tracks",
  "Исполнители": "Artists",
  "Поиск в библиотеке": "Search the library",
  "Сортировка:": "Sort:",
  "Назад: {where}": "Back: {where}",
  "Всё сохраняется сразу, отдельная кнопка не нужна": "Everything is saved at once, there is no button for it",
  "Разделы настроек": "Settings sections",
  "Куда сохранять": "Where music goes",
  "Звук и тексты": "Sound and lyrics",
  "Слежение и фон": "Watching and background",
  "Сеть": "Network",
  "Если что-то не работает": "If something does not work",
  "Как выглядит окно": "How the window looks",
  "Папка для музыки и то, как в ней называются альбомы и треки": "The music folder, and how albums and tracks are named in it",
  "Скорость и то, как программа выбирает запись": "Speed, and how the program picks a recording",
  "Что дописывается к каждому скачанному треку": "What every downloaded track gets besides its sound",
  "Где ещё лежит ваша музыка и кому видно, что играет": "Where else your music is, and who sees what plays",
  "Показывать в Discord, что играет": "Show on Discord what plays",
  "В профиле появится «Слушает» с названием трека, исполнителем и обложкой альбома. На паузе статус пропадает. Нужен запущенный Discord на этом компьютере":
    "Your profile shows \"Listening to\" with the track, the artist and the album's cover. It goes away while paused. Discord must be running on this computer",
  "Новые треки приходят сами, без вашего участия": "New tracks arrive by themselves",
  "Папка, формат и имена под одним названием, на один щелчок": "A folder, a format and names under one name, a click away",
  "Если сервисы открываются плохо или не открываются совсем": "When the services open slowly or not at all",
  "Что установлено и где искать подробности": "What is installed, and where to find the details",
  "Так будет выглядеть альбом:": "An album will look like this:",
  "Остановить после текущей записи · {done} из {total}": "Stop after the current item · {done} of {total}",
  "Удалить (Delete)": "Delete (Delete)",
  "Уже в библиотеке": "Already in the library",
  "В библиотеке": "In the library",
  "в библиотеке": "in the library",
  "Скачать ещё раз": "Download again",
  "Жанр:": "Genre:",
  "Жанр": "Genre",
  "все": "all",
  "В жанре «{genre}»": "In the genre “{genre}”",
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
  "Чем больше, тем быстрее, но YouTube может начать ограничивать скорость":
    "More is faster, but YouTube may start to slow the downloads",
  "Больше": "More",
  "Меньше": "Fewer",
  "Папки альбомов": "Album folders",
  "Как называть папку альбома внутри папки для музыки":
    "How to name an album's folder inside the music folder",
  "Исполнитель - Альбом (Год)": "Artist - Album (Year)",
  "Исполнитель → Альбом (Год)": "Artist → Album (Year)",
  "Альбом (Год)": "Album (Year)",
  "Имена файлов": "File names",
  "Номер и название есть в имени всегда. Варианты отличаются тем, когда в него попадает исполнитель":
    "The number and title are always in the name. The choices differ in when the artist is added",
  "01. Название (исполнитель только у гостей)":
    "01. Title (artist only for guests)",
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
  "Через него идут поиск и загрузка. Например:":
    "Searches and downloads go through it. For example:",
  "или": "or",
  ". Если поле пустое, работают настройки системы":
    ". When it is empty, the system's settings apply",
  "Cookies из браузера": "Cookies from a browser",
  "Не использовать": "Do not use any",
  "Компоненты": "Components",
  "В буфере обмена пусто. Скопируйте ссылку на альбом или трек: «Поделиться» → «Копировать ссылку».":
    "The clipboard is empty. Copy a link to an album or a track: \"Share\" → \"Copy link\".",
  "Всё необходимое установлено": "Everything needed is installed",
  "Не хватает компонентов": "Components are missing",
  "ffmpeg, Deno или Node.js, yt-dlp-ejs": "ffmpeg, Deno or Node.js, yt-dlp-ejs",
  "Диагностика": "Diagnostics",
  "YouTube часто меняется. Если загрузки перестали работать, сначала обновите Trackhound":
    "YouTube changes often. If downloads stop working, update Trackhound first",
  "Звук скачивает yt-dlp. Он обновляется вместе с Trackhound":
    "yt-dlp downloads the audio. It is updated together with Trackhound",
  "Этой сборке {days} {dayWord}, а YouTube за такое время обычно что-то меняет. Если загрузки перестали работать, обновите Trackhound":
    "This build is {days} {dayWord} old, and YouTube usually changes something in that time. If downloads stop working, update Trackhound",
  "Доступ к сервисам": "Access to the services",
  "Проверяет, открываются ли из этой сети Spotify, YouTube и SoundCloud и есть ли на компьютере прокси VPN-клиента":
    "Checks whether Spotify, YouTube and SoundCloud open from this network, and whether a VPN client runs a proxy on this computer",
  "Spotify: плеер открывается":
    "Spotify: the player opens",
  "Spotify: плеер закрыт для этой сети. Альбомы и треки соберутся со страниц-превью, а из плейлиста только первые 30 треков":
    "Spotify: the player is closed to this network. Albums and tracks are read from the preview pages, but only the first 30 tracks of a playlist",
  "Spotify: не открывается. Вставляйте ссылки из Apple Music, Deezer или YouTube Music либо пишите «Исполнитель - Альбом»":
    "Spotify: does not open. Paste links from Apple Music, Deezer or YouTube Music, or write \"Artist - Album\"",
  "YouTube: открывается":
    "YouTube: opens",
  "YouTube: не открывается. Звук будет только с SoundCloud, а там есть не всё":
    "YouTube: does not open. The audio can only come from SoundCloud, which does not have everything",
  "SoundCloud: открывается":
    "SoundCloud: opens",
  "SoundCloud: не открывается":
    "SoundCloud: does not open",
  "плеер открывается": "the player opens",
  "только страницы-превью": "only the preview pages",
  "не открывается": "does not open",
  "Прокси {client}: {url}. Spotify через него: {state}":
    "{client} proxy: {url}. Spotify through it: {state}",
  "включён": "in use",
  "Использовать": "Use it",
  "Использовать и повторить": "Use it and try again",
  "Спрашивать при сомнительных совпадениях": "Ask about doubtful matches",
  "Тексты песен": "Lyrics",
  "Тексты берутся из LRCLIB. Обычный записывается в теги, его покажут плеер и телефон. Синхронный сохраняется в файл .lrc рядом с треком, и плееры прокручивают его вместе с песней":
    "Lyrics come from LRCLIB. Plain lyrics go into the tags, where a player or a phone shows them. Synced lyrics are saved to an .lrc file beside the track, and players scroll them along with the song",
  "Если трек похож на несколько разных записей, программа покажет варианты на карточке и подождёт, пока вы выберете нужную. Каждый вариант можно послушать":
    "When a track looks like more than one recording, the card shows the candidates and waits for you to pick one. Each of them can be played first",
  "Нужен выбор": "Choose",
  "Дописать теги": "Fill in tags",
  "несколько секунд": "a few seconds",
  "{progress} · ещё {eta}": "{progress} · {eta} left",
  "Поиск (Ctrl+2)": "Search (Ctrl+2)",
  "Папка для музыки": "Music folder",
  "Другие папки с музыкой": "Other music folders",
  "Библиотека покажет музыку и из этих папок. Файлы остаются там, где лежат":
    "The library shows the music in these folders too. The files stay where they are",
  "Добавить папку…": "Add a folder…",
  "Убрать из библиотеки": "Remove from the library",
  "Исполнитель, альбом или трек": "Artist, album or track",
  "Что найти": "What to find",
  "Найдите, что скачать": "Find something to download",
  "Ищем…": "Searching…",
  "Поиск не удался": "The search failed",
  "Исполнитель, альбом или трек — с обложками из каталога. Щелчок по альбому покажет его треки, стрелка сразу поставит в очередь":
    "An artist, an album or a track, with covers from the catalogue. A click on an album shows its tracks; the arrow queues it at once",
  "Каталог: {service}": "Catalogue: {service}",
  "Альбом": "Album",
  "Сингл": "Single",
  "Сборник": "Compilation",
  "В очереди": "Queued",
  "Читаем список треков…": "Reading the tracklist…",
  "Сейчас играет": "Now playing",
  "Назад (Esc)": "Back (Esc)",
  "Во весь экран": "Full screen",
  "Выйти из полноэкранного режима": "Leave full screen",
  "{kbps} кбит/с": "{kbps} kbit/s",
  "{khz} кГц": "{khz} kHz",
  "Моно": "Mono",
  "Стерео": "Stereo",
  "Назад": "Back",
  "Предыдущий": "Previous",
  "Следующий": "Next",
  "Играть вперемешку": "Shuffle",
  "Слушать всю библиотеку вперемешку": "Shuffle the whole library",
  "Слушать «{genre}» вперемешку": "Shuffle “{genre}”",
  "Играть по порядку": "Play in order",
  "Повторять очередь": "Repeat the queue",
  "Повторять этот трек": "Repeat this track",
  "Не повторять": "Don't repeat",
  "Позиция": "Position",
  "Текст песни": "Lyrics",
  "Без звука": "Mute",
  "Со звуком": "Unmute",
  "Громкость": "Volume",
  "Закрыть плеер": "Close the player",
  "Слушать": "Play",
  "Не получилось сыграть «{title}»": "Could not play \"{title}\"",
  "Ищем текст…": "Looking for the lyrics…",
  "Текста для этой песни не нашлось": "No lyrics found for this song",
  "Скачать дискографию": "Download the discography",
  "Скачать дискографию: {count}": "Download the discography: {count}",
  "Следить за новыми релизами": "Watch for new releases",
  "Следим за новыми релизами": "Watching for new releases",
  "Новые альбомы, EP и синглы скачаются сами: раз в 12 часов программа смотрит, что вышло":
    "New albums, EPs and singles download by themselves: every 12 hours the program looks at what came out",
  "Читаем релизы…": "Reading the releases…",
  "{count} {releaseWord}": "{count} {releaseWord}",
  "поклонников: {count}": "fans: {count}",
  "Синглы и EP": "Singles and EPs",
  "Сборники": "Compilations",
  "Проверено {when}, новых релизов: {count}": "Checked {when}, new releases: {count}",
  "Проверено {when}, новых релизов нет": "Checked {when}, no new releases",
  "исполнитель": "artist",
  "Работать в фоне после закрытия окна": "Keep running when the window is closed",
  "Если идут загрузки или что-то под наблюдением, закрытое окно сворачивается в значок у часов. Загрузки доходят до конца, проверки идут по расписанию. Чтобы выйти, выберите «Выход» в меню значка":
    "While something downloads or is watched, closing the window leaves an icon by the clock. Downloads run to the end and checks keep their schedule. To quit, choose Quit in the icon's menu",
  "Уведомления о завершении": "Notify when done",
  "Сообщает, что загрузки закончились, если окно свёрнуто, закрыто или спрятано под другими":
    "Says the downloads have finished when the window is minimized, closed or behind other windows",
  "Добро пожаловать в Trackhound": "Welcome to Trackhound",
  "Вставьте ссылку на альбом, плейлист или трек из Spotify, Apple Music, Deezer, YouTube или SoundCloud. Можно и просто написать «Исполнитель - Альбом». Программа найдёт эту запись в открытом доступе, скачает её и заполнит теги: название, обложку, жанр и текст песни.":
    "Paste a link to an album, playlist or track from Spotify, Apple Music, Deezer, YouTube or SoundCloud, or just write \"Artist - Album\". The program finds the same recording where it is openly available, downloads it and fills in the tags: title, cover, genre and lyrics.",
  "Музыка будет здесь": "The music goes here",
  "Изменить…": "Change…",
  "Формат, названия файлов, прокси и остальное можно поменять в настройках в любой момент.":
    "The format, file names, proxy and the rest can be changed in Settings at any time.",
  "Начать": "Start",
  "Дописать жанры, обложки, тексты и недостающие теги, ничего не скачивая заново":
    "Fill in genres, covers, lyrics and missing tags, downloading nothing again",
  "Дописываем теги: {done} из {total} · {title}": "Filling in tags: {done} of {total} · {title}",
  "Дописать теги во всю библиотеку?": "Fill in tags across the whole library?",
  "Жанры, годы, обложки и тексты допишутся туда, где их нет: {count} {recordWord}. Уже записанное не меняется, ничего не скачивается заново.":
    "Genres, years, covers and lyrics go where they are missing: {count} {recordWord}. Nothing already written is changed, and nothing is downloaded again.",
  "Дописать": "Fill in",
  "Дописано: файлов {files}, обложек {covers}, текстов {lyrics}":
    "Filled in: {files} files, {covers} covers, {lyrics} lyrics",
  "не нашлись в каталогах: {names}": "not found in the catalogues: {names}",
  "Дописывать нечего: всё уже на месте": "Nothing to fill in: everything is there already",
  "Остановлено. {text}": "Stopped. {text}",
  "Дописываем…": "Filling in…",
  "Докачивается отдельно": "Downloading separately",
  "Пропущен": "Passed over",
  "Ждут выбора: {count}": "Waiting for a choice: {count}",
  "ждут выбора: {count}": "waiting for a choice: {count}",
  "Выбрано {chosen} из {count}": "Chosen {chosen} of {count}",
  "Выберите запись для каждого такого трека или пропустите его": "Choose a recording for each such track, or pass it over",
  "Скачать выбранные": "Download the chosen",
  "Выбрать эту запись": "Choose this recording",
  "{source} · {length}, нужно {wanted} · совпадение {percent}%": "{source} · {length}, wanted {wanted} · match {percent}%",
  "Послушать": "Listen",
  "Не скачивать": "Do not download",
  "Выбранные треки поставлены в очередь: {count}": "Chosen tracks queued: {count}",
  "Похоже сразу на несколько записей — выберите нужную": "Looks like more than one recording — choose the right one",
  "Зеркало Spotify": "Spotify relay",
  "Помогает открыть релизы, которые Spotify не показывает в вашей стране. Запрос к Spotify отправляется с сервера во Франкфурте":
    "Opens releases that Spotify does not show in your country. The request to Spotify is sent from a server in Frankfurt",
  "Зеркало Spotify: работает":
    "Spotify relay: works",
  "Зеркало Spotify: не отвечает":
    "Spotify relay: does not answer",
  "Зеркало Spotify: не задано":
    "Spotify relay: not set",
  "Spotify: плеер закрыт для этой сети, но релизы целиком приходят через зеркало":
    "Spotify: the player is closed to this network, but whole releases come through the relay",
  "На компьютере работает {client}: через его прокси Spotify открывается":
    "{client} is running on this computer, and Spotify opens through its proxy",
  "На этом компьютере не нашлось прокси VPN-клиента. VPN в режиме TUN или «системного прокси» программа использует сама. В других режимах впишите адрес прокси выше":
    "No VPN client's proxy was found on this computer. The program uses a VPN in TUN or system-proxy mode by itself. In other modes, enter the proxy address above",
  "Журнал работы": "Log",
  "Здесь записаны подробности загрузок и ошибок. Журнал никуда не отправляется":
    "Details of downloads and errors are written here. The log is not sent anywhere",
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
  "Названия, обложки и номера треков программа берёт со страницы по ссылке. Звук берётся оттуда же, если он там в открытом доступе, а иначе с YouTube Music или SoundCloud":
    "Titles, covers and track numbers come from the page behind the link. The audio comes from there too when it is openly available, and otherwise from YouTube Music or SoundCloud",
  "Папка для музыки: {folder}": "Music folder: {folder}",
  "{folder}\nНажмите, чтобы выбрать другую папку": "{folder}\nClick to choose another folder",

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
  "релиз": ["release", "releases"],
  "ссылка": ["link", "links"],
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
