"use strict";

// Web links with or without https:// and spotify: URIs; the backend decides what it can read
const LINK_RE = /https?:\/\/[^\s"'<>]+|(?:[a-z0-9-]+\.)+(?:com|ru|fm|be|link|fi)\/[^\s"'<>]+|spotify:(?:album|track):[A-Za-z0-9]{22}/gi;

const FORMAT_HINTS = {
  mp3: "mp3 — 320 кбит/с из полной дорожки, до 20 кГц. Играет везде, включая магнитолы",
  m4a: "m4a — AAC 256 кбит/с из полной дорожки, до 20 кГц. Подходит почти всем плеерам",
  opus: "opus — дорожка YouTube как есть, без перекодирования. Понимают его не все плееры",
};

const VIEWS = ["download", "search", "library", "settings"]; // Ctrl+1…4, in the order of the panel; Ctrl+K too

// What the backend accepts as a proxy; anything else is refused there anyway
const PROXY_RE = /^(?:https?|socks4|socks5h?):\/\/[^\s/]+$/i;

// Each step has a sign of its own. The turning arc is left to the waits that
// have no number to show; where a bar counts, the arrow stands still.
const TRACK_UI = {
  waiting: { label: "В очереди", icon: "dot", tone: "muted" },
  search: { label: "Ищем", icon: "search", tone: "muted" },
  download: { label: "Качаем", icon: "download", tone: "primary" },
  done: { label: "Готово", icon: "check", tone: "success" },
  found: { label: "Найден", icon: "check", tone: "success" },
  skip: { label: "Уже есть", icon: "check", tone: "muted" },
  missing: { label: "Не найден", icon: "x", tone: "danger" },
  error: { label: "Ошибка", icon: "alert", tone: "danger" },
  cancel: { label: "Отменён", icon: "stop", tone: "muted" },
  // Held back: the match may be the wrong song, and a person chooses
  doubtful: { label: "Нужен выбор", icon: "alert", tone: "warning" },
  handed: { label: "Докачивается отдельно", icon: "download", tone: "muted" },
  declined: { label: "Пропущен", icon: "stop", tone: "muted" },
};
const TRACK_ACTIVE = new Set(["waiting", "search", "download"]);
// The signs of a step under way: a tick that follows one of them draws itself in
const BUSY_ICONS = new Set(["spinner", "search", "download", "level"]);
const TRACK_FAILED = new Set(["missing", "error"]);


const ACTIVE = new Set(["queued", "running"]);

// Library columns that can be sorted. Text starts ascending, numbers and dates
// descending, because that is the useful end of each.
const COLLATOR = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const LIBRARY_SORTS = {
  modified: { dir: -1, kind: "date", label: "по добавлению", value: (item) => item.modified },
  title: { dir: 1, kind: "text", label: "по названию", value: (item) => `${item.title} ${item.artist}` },
  artist: { dir: 1, kind: "text", label: "по исполнителю", value: (item) => `${item.artist} ${item.year} ${item.title}` },
  year: { dir: -1, kind: "date", label: "по году", value: (item) => Number(item.year) || 0 },
  tracks: { dir: -1, kind: "number", label: "по числу треков", value: (item) => item.tracks },
  size: { dir: -1, kind: "number", label: "по размеру", value: (item) => item.size },
};
const trackPlace = (track) => `${String(track.disc).padStart(2, "0")}${String(track.number).padStart(3, "0")}`;
const TRACK_SORTS = {
  title: { dir: 1, kind: "text", label: "по названию", value: (track) => track.title },
  artist: { dir: 1, kind: "text", label: "по исполнителю", value: (track) => `${track.artists} ${track.album} ${trackPlace(track)}` },
  album: { dir: 1, kind: "text", label: "по альбому", value: (track) => `${track.album} ${trackPlace(track)}` },
  modified: { dir: -1, kind: "date", label: "по добавлению", value: (track) => track.modified },
  duration: { dir: -1, kind: "number", label: "по длительности", value: (track) => track.duration },
  plays: { dir: -1, kind: "number", label: "по прослушиваниям", value: (track) => state.plays.get(track.path) || 0 },
};
const ARTIST_SORTS = {
  name: { dir: 1, kind: "text", label: "по имени", value: (artist) => artist.name },
  count: { dir: -1, kind: "number", label: "по числу альбомов", value: (artist) => artist.albums * 1000 + artist.singles },
  modified: { dir: -1, kind: "date", label: "по добавлению", value: (artist) => artist.modified },
};
const PLAYLIST_SORTS = {
  modified: { dir: -1, kind: "date", label: "по изменению", value: (entry) => entry.modified },
  name: { dir: 1, kind: "text", label: "по названию", value: (entry) => entry.name },
  count: { dir: -1, kind: "number", label: "по числу треков", value: (entry) => entry.count },
};
// What "which way" means depends on what is sorted
const ORDER_LABELS = {
  text: { 1: "А → Я", "-1": "Я → А" },
  date: { "-1": "новые сверху", 1: "старые сверху" },
  number: { "-1": "больше сверху", 1: "меньше сверху" },
};

const state = {
  settings: null,
  problems: [],
  view: "download",
  jobs: new Map(),
  tracks: [], // every track of every job, in the order the releases arrived
  orphans: [],
  dirty: new Set(),
  run: null, // jobs added since the queue was last idle; the line above the downloads sums them up
  library: {
    items: [], folder: null, stale: true, loading: false, token: 0,
    tab: "albums", view: "grid",
    genre: "", // the genre the tabs are narrowed to, by its lower-case name; "" for all
    sorts: { // newest first, the way the folder itself is read
      albums: { key: "modified", dir: -1 },
      tracks: { key: "modified", dir: -1 },
      artists: { key: "name", dir: 1 },
      playlists: { key: "modified", dir: -1 },
    },
    scroll: {}, // where each tab was left
    shown: [], // paths in the order drawn, so Shift-click knows what a range covers
    selected: new Set(),
    anchor: null,
    artists: null, // grouped from the items when first asked for
    trackList: { items: [], folder: null, stale: true, loading: false, token: 0 },
    pages: [], // the album and artist pages open over the grid, the top one last
    pageToken: 0,
  },
  // The catalogue search: what was asked last and what came back
  search: { query: "", token: 0, results: null, loading: false, queued: new Set(), timer: 0, page: null, pages: [] },
  covers: new Map(),
  artistPhotos: new Map(),
  plays: new Map(), // path → how many times it was listened to
  playlists: { items: [], stale: true, loading: false, token: 0 }, // the cards of the Playlists tab
  scrobble: null, // the Last.fm and ListenBrainz accounts, as the settings show them
  trackMenu: null, // the tracks the track menu was opened for
  lastfmWaiting: false,
  tints: new Map(), // picture address → the hue its band is drawn in
  update: null, // { version, url } once a newer release is published
  diagnostics: null, // log path and yt-dlp version, read once at startup
  paused: false,
  // Tidying the library up: which entry of how many, and afterwards what it came to
  tidy: { running: false, done: 0, total: 0, title: "", paths: new Set(), note: "", timer: 0,
          gaps: new Set(), gapsToken: 0 }, // gaps: the entries a tidy-up has something to add to
  reported: "", // the progress last sent to the title and the taskbar button
};
const darkMedia = window.matchMedia("(prefers-color-scheme: dark)");
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => root.querySelectorAll(selector);
const api = () => window.pywebview.api;

// Covers and photos are fetched as they scroll into sight. The viewport is the
// root because the album pages scroll in a box of their own.
const coverObserver = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    coverObserver.unobserve(entry.target);
    showLibraryCover(entry.target);
  }
}, { rootMargin: "700px" });
const artistObserver = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    artistObserver.unobserve(entry.target);
    showArtistPhoto(entry.target);
  }
}, { rootMargin: "200px" });

applyTheme();

let booted = false;
function boot() {
  if (booted) return;
  booted = true;
  init().catch((error) => {
    state.problems = [t("Интерфейс не запустился: {error}", { error })];
    renderProblems();
  });
}
if (window.pywebview?.api?.init) boot();
else window.addEventListener("pywebviewready", boot);

async function init() {
  const data = await api().init();
  state.settings = data.settings;
  state.traySupported = Boolean(data.tray);
  state.library.view = data.settings.library_view === "list" ? "list" : "grid";
  state.problems = data.problems;
  state.watched = data.watched || [];
  setLanguage(data.language); // translates the markup before anything is drawn
  $("#version").textContent = `v${data.version}`;
  renderProblems();
  renderSettings();
  bindUi();
  restoreHistory(data.history || []);
  showView("download");
  renderChrome();
  setInterval(renderStatus, 1000);
  pollLoop();
  checkForUpdate();
  loadDiagnostics();
  // Only now, so the saved panel width is in place before it can be animated
  setTimeout(() => document.documentElement.classList.add("motion-ready"), 0);
  // Notices about finished downloads are for when the window is not in use
  window.addEventListener("focus", () => api().focus(true));
  window.addEventListener("blur", () => api().focus(false));
  if (data.first_run) showWelcome();
}

// The first start: what the program does and where the music goes. Starting
// saves the settings, and a saved settings file means this is not shown again.
function showWelcome() {
  const dialog = $("#welcome");
  const path = () => { $("#welcome-path").textContent = state.settings.folder; };
  path();
  const choose = async () => {
    const folder = await api().choose_folder(state.settings.folder);
    if (folder) {
      updateSettings({ folder });
      path();
    }
  };
  const start = () => {
    $("#welcome-folder").removeEventListener("click", choose);
    document.documentElement.classList.remove("welcoming");
    if (dialog.open) dialog.close();
    api().save_settings(state.settings);
    $("#link")?.focus();
  };
  $("#welcome-folder").addEventListener("click", choose);
  $("#welcome-start").addEventListener("click", start, { once: true });
  dialog.addEventListener("cancel", start, { once: true }); // Escape starts too: nothing here must be decided
  document.documentElement.classList.add("welcoming");
  dialog.showModal();
  $("#welcome-start").focus();
}

// Where the log sits, and how old the yt-dlp inside this build is
async function loadDiagnostics() {
  const info = await api().diagnostics();
  state.diagnostics = info;
  const stale = info.ytdlp_age >= 60;
  $("#ytdlp-title").textContent = `yt-dlp ${info.ytdlp}`;
  $("#ytdlp-desc").textContent = stale
    ? t("Этой сборке {days} {dayWord}, а YouTube за такое время обычно что-то меняет. Если загрузки перестали работать, обновите Trackhound",
        { days: info.ytdlp_age, dayWord: plural(info.ytdlp_age, "день", "дня", "дней") })
    : t("Звук скачивает yt-dlp. Он обновляется вместе с Trackhound");
  $("#ytdlp-desc").classList.toggle("warn", stale);
  $("#log-path").textContent = info.log;
  $("#log-path").title = info.log;
}

// Only asks; installing waits for the button
async function checkForUpdate() {
  const release = await api().latest_release();
  if (!release) return;
  state.update = release;
  $("#update-title").textContent = t("Доступна версия {version}", { version: release.version });
  $("#update").hidden = false;
  $("#update-open").addEventListener("click", () => api().open_url(release.url));
  // Without an installer to fetch — an unusual release, or one still uploading
  // — the page is all that can be offered.
  $("#update-install").hidden = !release.installer || !release.digest;
  $("#update-install").addEventListener("click", startUpdate);
  renderSettingsDot();
}

function startUpdate() {
  if (!state.update) return;
  $("#update-install").disabled = true;
  $("#update-install-label").textContent = t("Скачиваем…");
  api().install_update(state.update);
}

// The download runs in the program, and says how it is going through the same
// queue the music does.
function onUpdateEvent(event) {
  const button = $("#update-install");
  const label = $("#update-install-label");
  const desc = $("#update-desc");
  if (event.state === "downloading") {
    button.disabled = true;
    label.textContent = t("Скачиваем… {percent}%", { percent: event.percent });
  } else if (event.state === "checking") {
    label.textContent = t("Проверяем хеш…");
  } else if (event.state === "starting") {
    label.textContent = t("Запускаем установщик");
    desc.textContent = t("Окно сейчас закроется: установщик заменит файлы и откроет новую версию сам");
  } else if (event.state === "error") {
    button.disabled = false;
    label.textContent = t("Обновить программу");
    desc.textContent = t("Не получилось обновиться: {error}", { error: event.message });
    desc.classList.add("warn");
  }
}

function renderSettingsDot() {
  $("#settings-dot").hidden = !state.problems.length && !state.update;
}

/* Settings */

function renderSettings() {
  const settings = state.settings;
  applyTheme();
  applySidebar(settings.sidebar);
  syncRadios($("#theme"), "data-theme-choice", settings.theme);
  syncSwitch($("#replaygain"), settings.replaygain);
  syncSwitch($("#ask-doubtful"), settings.ask_doubtful);
  syncSwitch($("#lyrics"), settings.lyrics);
  syncSwitch($("#tray"), settings.tray);
  syncSwitch($("#notify"), settings.notify);
  syncSwitch($("#discord"), settings.discord);
  renderPlayerSettings();
  $("#tray-setting").hidden = !state.traySupported; // the notification area is Windows' own
  syncRadios($("#language"), "data-language", settings.language);
  syncRadios($("#formats"), "data-format", settings.format);
  renderProfiles();
  syncRadios($("#mode-menu"), "data-dry-run", String(settings.dry_run));
  const folder = $("#folder");
  $("#folder-name").textContent = settings.folder.split(/[\\/]+/).filter(Boolean).pop() || settings.folder;
  folder.title = t("{folder}\nНажмите, чтобы выбрать другую папку", { folder: settings.folder });
  folder.setAttribute("aria-label", t("Папка для музыки: {folder}", { folder: settings.folder }));
  $("#threads").textContent = settings.threads;
  $("#music-folder").textContent = settings.folder;
  renderLibraryFolders();
  $("#cookies").value = settings.cookies_browser;
  $("#rate").value = String(settings.rate_limit);
  $("#track-name").value = settings.track_name;
  $("#folder-layout").value = settings.folder_name;
  if ($("#proxy") !== document.activeElement) $("#proxy").value = settings.proxy;
  syncSwitch($("#relay"), settings.relay !== "off");
  applyPlayerSettings(settings);
  $("#naming-preview").textContent = namingExample(settings);
  $("#submit-label").textContent = t(settings.dry_run ? "Проверить" : "Скачать");
  $("#submit use").setAttribute("href", settings.dry_run ? "#i-search" : "#i-download");
  renderStatus();
}

function updateSettings(patch) {
  if ((patch.folder && patch.folder !== state.settings.folder) || patch.library_folders) state.library.stale = true;
  const lyricsChanged = "lyrics" in patch && patch.lyrics !== state.settings.lyrics;
  Object.assign(state.settings, patch);
  renderSettings();
  api().save_settings(state.settings);
  if (lyricsChanged && state.library.items.length) refreshGaps(); // missing lyrics count only while they are on
}

// Switching the language repaints the markup and redraws everything the
// script wrote, so nothing is left in the language that was just dropped.
async function changeLanguage(choice) {
  updateSettings({ language: choice });
  setLanguage(await api().set_language(choice));
  renderSettings();
  renderProblems();
  for (const job of state.jobs.values()) renderJob(job);
  for (const track of state.tracks) renderTrack(track);
  renderLibrary();
  renderChrome();
  if (state.diagnostics) loadDiagnostics();
}

async function copyReport() {
  const button = $("#log-copy");
  const info = state.diagnostics || await api().diagnostics();
  const copied = await api().copy(info.report);
  button.textContent = t(copied ? "Скопировано" : "Не удалось скопировать");
  announce(t(copied ? "Отчёт скопирован в буфер обмена" : "Не удалось скопировать отчёт"));
  setTimeout(() => { button.textContent = t("Скопировать отчёт"); }, 2000);
}

// What this network lets through. Where services are blocked the program
// still does what it can — Spotify's preview pages instead of its player,
// SoundCloud instead of YouTube — and a VPN client's proxy, when one is
// running here, is one click away.
const NETWORK_WORDS = {
  spotify: {
    ok: "Spotify: плеер открывается",
    previews: "Spotify: плеер закрыт для этой сети. Альбомы и треки соберутся со страниц-превью, а из плейлиста только первые 30 треков",
    down: "Spotify: не открывается. Вставляйте ссылки из Apple Music, Deezer или YouTube Music либо пишите «Исполнитель - Альбом»",
  },
  youtube: {
    ok: "YouTube: открывается",
    down: "YouTube: не открывается. Звук будет только с SoundCloud, а там есть не всё",
  },
  soundcloud: { ok: "SoundCloud: открывается", down: "SoundCloud: не открывается" },
};
const PROXY_SPOTIFY = { ok: "плеер открывается", previews: "только страницы-превью", down: "не открывается" };
const RELAY_WORDS = {
  ok: "Зеркало Spotify: работает",
  down: "Зеркало Spotify: не отвечает",
  none: "Зеркало Spotify: не задано",
};
const NETWORK_TONES = { ok: "ok", previews: "warn", down: "bad" };
const NETWORK_ICONS = { ok: "check", warn: "alert", bad: "x" };

async function checkNetwork() {
  const button = $("#network-check");
  const desc = $("#network-desc");
  desc.dataset.text ??= desc.textContent;
  button.disabled = true;
  desc.textContent = t("Проверяем…");
  try {
    renderNetwork(await api().check_network());
  } finally {
    button.disabled = false;
    desc.textContent = desc.dataset.text;
  }
}

function renderNetwork(result) {
  const rows = ["spotify", "youtube", "soundcloud"].map((service) =>
    networkRow(NETWORK_TONES[result[service]], t(NETWORK_WORDS[service][result[service]])));
  // Where Spotify is closed, a working relay makes up for it in full
  if (result.relay === "ok" && result.spotify !== "ok") {
    rows[0] = networkRow("ok", t("Spotify: плеер закрыт для этой сети, но релизы целиком приходят через зеркало"));
  }
  if (result.relay !== "none" || result.spotify !== "ok") rows.splice(1, 0, networkRow(
    result.relay === "ok" ? "ok" : "bad", t(RELAY_WORDS[result.relay])));
  for (const proxy of result.proxies) {
    const row = networkRow(NETWORK_TONES[proxy.spotify],
      t("Прокси {client}: {url}. Spotify через него: {state}",
        { client: proxy.client, url: proxy.url, state: t(PROXY_SPOTIFY[proxy.spotify]) }));
    if (proxy.url === state.settings.proxy) {
      row.append(Object.assign(document.createElement("span"), { className: "net-note", textContent: t("включён") }));
    } else {
      const use = Object.assign(document.createElement("button"), {
        type: "button", className: "tool-btn", textContent: t("Использовать"),
      });
      use.addEventListener("click", () => {
        updateSettings({ proxy: proxy.url });
        checkNetwork(); // the same check again, now through the proxy
      });
      row.append(use);
    }
    rows.push(row);
  }
  if (!result.proxies.length) {
    rows.push(networkRow("", t("На этом компьютере не нашлось прокси VPN-клиента. VPN в режиме TUN или «системного прокси» программа использует сама. В других режимах впишите адрес прокси выше")));
  }
  $("#network-list").replaceChildren(...rows);
}

function networkRow(tone, text) {
  const row = document.createElement("li");
  row.className = `net-item${tone ? ` ${tone}` : ""}`;
  row.innerHTML = `<svg class="icon sm" aria-hidden="true"><use href="#i-${NETWORK_ICONS[tone] || "dot"}"/></svg><span></span>`;
  $("span", row).textContent = text;
  return row;
}

function applyTheme() {
  const choice = state.settings ? state.settings.theme : "system";
  const dark = choice === "dark" || (choice === "system" && darkMedia.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

// The panel has two widths and a button at its foot to move between them: an
// icon rail and a labelled column. The setting is still a width in pixels, so
// a file written when the panel was dragged opens at whichever of the two it
// was left nearer.
const SIDEBAR_RAIL = 64;
const SIDEBAR_WIDE = 208;
const SIDEBAR_SNAP = 110;

function applySidebar(width) {
  const size = Math.round(width) >= SIDEBAR_SNAP ? SIDEBAR_WIDE : SIDEBAR_RAIL;
  document.documentElement.style.setProperty("--sidebar", `${size}px`);
  const toggle = $("#sidebar-toggle");
  const label = t(size === SIDEBAR_WIDE ? "Свернуть панель" : "Развернуть панель");
  toggle.setAttribute("aria-expanded", String(size === SIDEBAR_WIDE));
  toggle.setAttribute("aria-label", label);
  toggle.title = `${label} (Ctrl+B)`;
  return size;
}

function toggleSidebar() {
  const wide = $("#sidebar-toggle").getAttribute("aria-expanded") === "true";
  updateSettings({ sidebar: applySidebar(wide ? SIDEBAR_RAIL : SIDEBAR_WIDE) });
}

function bindSidebarToggle() {
  $("#sidebar-toggle").addEventListener("click", toggleSidebar);
}

/* Watching: albums and playlists checked again now and then for new tracks */

function isWatched(link) {
  return (state.watched || []).some((entry) => entry.link === link);
}

function setWatched(list) {
  state.watched = list || [];
  renderWatched();
  renderArtistButtons();
  for (const job of state.jobs.values()) renderJob(job);
}

async function toggleWatch(job) {
  const list = isWatched(job.link) ? await api().unwatch(job.link) : await api().watch(job.id);
  setWatched(list);
  announce(t(isWatched(job.link) ? "Следим за «{title}»" : "Больше не следим за «{title}»",
             { title: job.title }));
}

function watchStatus(entry) {
  const when = entry.checked ? new Date(entry.checked * 1000).toLocaleString(LANGUAGE, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  if (entry.state === "running" || entry.state === "queued") return t("Проверяем…");
  if (entry.added_tracks == null) return t("Проверено {when}", { when });
  if (entry.kind === "artist") {
    return entry.added_tracks
      ? t("Проверено {when}, новых релизов: {count}", { when, count: entry.added_tracks })
      : t("Проверено {when}, новых релизов нет", { when });
  }
  return entry.added_tracks
    ? t("Проверено {when}, новых треков: {count}", { when, count: entry.added_tracks })
    : t("Проверено {when}, новых треков нет", { when });
}

function renderWatched() {
  const group = $("#watch-group");
  for (const row of $$(".watch-row", group)) row.remove();
  const list = state.watched || [];
  $("#watch-empty").hidden = list.length > 0;
  $("#watch-check").disabled = list.length === 0;
  for (const entry of list) {
    const row = document.createElement("div");
    row.className = "setting watch-row";
    const label = document.createElement("div");
    label.className = "setting-label";
    const name = document.createElement("p");
    name.textContent = entry.title;
    name.title = entry.link;
    const desc = document.createElement("p");
    desc.className = "setting-desc";
    desc.textContent = [entry.kind === "artist" ? t("исполнитель") : "", entry.service, watchStatus(entry)]
      .filter(Boolean).join(" · ");
    label.append(name, desc);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-btn danger";
    remove.title = t("Перестать следить");
    remove.setAttribute("aria-label", t("Перестать следить"));
    remove.innerHTML = '<svg class="icon sm" aria-hidden="true"><use href="#i-trash"/></svg>';
    remove.addEventListener("click", async () => setWatched(await api().unwatch(entry.link)));
    row.append(label, remove);
    group.append(row);
  }
}

function bindWatch() {
  $("#watch-check").addEventListener("click", async () => {
    setWatched(await api().check_watched(true));
    announce(t("Проверяем плейлисты под наблюдением"));
  });
  renderWatched();
}

// The folders the library shows besides the music folder, each with a way out
function renderLibraryFolders() {
  const group = $("#library-folders-group");
  for (const row of $$(".library-folder-row", group)) row.remove();
  for (const folder of state.settings.library_folders || []) {
    const row = document.createElement("div");
    row.className = "setting library-folder-row";
    const label = document.createElement("div");
    label.className = "setting-label";
    const path = document.createElement("p");
    path.className = "setting-path";
    path.textContent = folder;
    label.append(path);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-btn danger";
    remove.title = t("Убрать из библиотеки");
    remove.setAttribute("aria-label", remove.title);
    remove.innerHTML = '<svg class="icon sm" aria-hidden="true"><use href="#i-x"/></svg>';
    remove.addEventListener("click", () => updateSettings({
      library_folders: state.settings.library_folders.filter((item) => item !== folder),
    }));
    row.append(label, remove);
    group.append(row);
  }
}

/* Profiles: a folder, a format and the two naming rules under a name */

const PROFILE_KEYS = ["folder", "format", "track_name", "folder_name"];

// Which saved profile the current settings match, if any. Derived rather than
// remembered, so editing a setting by hand simply steps out of the profile
// instead of leaving a stale name lit.
function activeProfile() {
  return (state.settings.profiles || []).find((profile) =>
    PROFILE_KEYS.every((key) => profile[key] === state.settings[key])) || null;
}

function profileSummary(profile) {
  const folder = profile.folder.split(/[\\/]+/).filter(Boolean).pop() || profile.folder;
  return [folder, profile.format, t(FOLDER_LAYOUTS[profile.folder_name] || profile.folder_name)]
    .join(" · ");
}

const FOLDER_LAYOUTS = {
  flat: "Исполнитель - Альбом (Год)",
  nested: "Исполнитель → Альбом (Год)",
  album: "Альбом (Год)",
};

function renderProfiles() {
  const profiles = state.settings.profiles || [];
  const active = activeProfile();
  $("#profile-name").textContent = active ? active.name : t("Профиль");
  $("#profile").classList.toggle("on", Boolean(active));

  const menu = $("#profile-menu");
  menu.replaceChildren();
  if (!profiles.length) {
    const empty = document.createElement("p");
    empty.className = "menu-empty";
    empty.textContent = t("Профилей пока нет. Сохранить текущие настройки можно в разделе «Настройки»");
    menu.append(empty);
  }
  for (const profile of profiles) {
    const item = document.createElement("button");
    item.type = "button";
    item.setAttribute("role", "menuitemradio");
    item.setAttribute("aria-checked", String(profile === active));
    item.innerHTML = '<svg class="icon sm check" aria-hidden="true"><use href="#i-check"/></svg>';
    const name = document.createElement("span");
    name.className = "menu-name";
    name.textContent = profile.name;
    const note = document.createElement("span");
    note.className = "menu-note";
    note.textContent = profileSummary(profile);
    item.append(name, note);
    item.addEventListener("click", () => {
      applyProfile(profile);
      setProfileMenuOpen(false);
    });
    menu.append(item);
  }
  renderProfileSettings(profiles);
}

function renderProfileSettings(profiles) {
  const group = $("#profiles-group");
  for (const row of $$(".profile-row", group)) row.remove();
  const add = $("#profile-add");
  for (const profile of profiles) {
    const row = document.createElement("div");
    row.className = "setting profile-row";
    const label = document.createElement("div");
    label.className = "setting-label";
    const name = document.createElement("p");
    name.textContent = profile.name;
    const desc = document.createElement("p");
    desc.className = "setting-desc";
    desc.textContent = profile.folder + " · " + profileSummary(profile);
    label.append(name, desc);
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "tool-btn";
    apply.textContent = t("Применить");
    apply.addEventListener("click", () => applyProfile(profile));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-btn danger";
    remove.title = t("Удалить профиль");
    remove.setAttribute("aria-label", t("Удалить профиль"));
    remove.innerHTML = '<svg class="icon sm" aria-hidden="true"><use href="#i-trash"/></svg>';
    remove.addEventListener("click", () => deleteProfile(profile.name));
    row.append(label, apply, remove);
    group.insertBefore(row, add);
  }
}

function applyProfile(profile) {
  updateSettings(Object.fromEntries(PROFILE_KEYS.map((key) => [key, profile[key]])));
  announce(t("Профиль «{name}»", { name: profile.name }));
}

function saveProfile() {
  const input = $("#profile-new");
  const name = input.value.trim().slice(0, 40);
  if (!name) return input.focus();
  const profiles = (state.settings.profiles || [])
    .filter((profile) => profile.name.toLowerCase() !== name.toLowerCase());
  profiles.push({ name, ...Object.fromEntries(PROFILE_KEYS.map((key) => [key, state.settings[key]])) });
  input.value = "";
  updateSettings({ profiles });
  announce(t("Профиль «{name}» сохранён", { name }));
}

function deleteProfile(name) {
  updateSettings({
    profiles: (state.settings.profiles || []).filter((profile) => profile.name !== name),
  });
}

function setProfileMenuOpen(open) {
  const menu = $("#profile-menu");
  menu.hidden = !open;
  $("#profile").setAttribute("aria-expanded", String(open));
  if (open) ($("[aria-checked=true]", menu) || $("button", menu) || menu).focus();
}

function bindProfiles() {
  $("#profile").addEventListener("click", () => setProfileMenuOpen($("#profile-menu").hidden));
  $("#profile-save").addEventListener("click", saveProfile);
  $("#profile-new").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      saveProfile();
    }
  });
  document.addEventListener("click", (event) => {
    if (!$("#profile-menu").hidden && !event.target.closest("#profile-menu, #profile")) {
      setProfileMenuOpen(false);
    }
  });
}

function renderProblems() {
  const problems = state.problems;
  const items = () => problems.map((text) => Object.assign(document.createElement("li"), { textContent: text }));
  $("#problems-list").replaceChildren(...items());
  $("#problems").hidden = !problems.length;
  renderSettingsDot();
  $("#env-title").textContent = t(problems.length ? "Не хватает компонентов"
                                                  : "Всё необходимое установлено");
  $("#env-list").replaceChildren(...(problems.length ? items()
    : [Object.assign(document.createElement("li"),
                     { textContent: t("ffmpeg, Deno или Node.js, yt-dlp-ejs") })]));
  $("#env-icon use").setAttribute("href", problems.length ? "#i-alert" : "#i-check");
  $("#env-icon").classList.toggle("warn", problems.length > 0);
  renderStatus();
}

function bindUi() {
  for (const button of $$("[data-view]")) {
    button.addEventListener("click", () => showView(button.dataset.view));
  }
  bindSidebarToggle();
  bindProfiles();
  bindWatch();
  for (const button of $$("#formats [data-format]")) button.title = t(FORMAT_HINTS[button.dataset.format]);
  radioGroup($("#theme"), "data-theme-choice", (theme) => updateSettings({ theme }));
  bindSwitch($("#replaygain"), (on) => updateSettings({ replaygain: on }));
  bindSwitch($("#ask-doubtful"), (on) => updateSettings({ ask_doubtful: on }));
  bindSwitch($("#lyrics"), (on) => updateSettings({ lyrics: on }));
  bindSwitch($("#tray"), (on) => updateSettings({ tray: on }));
  bindSwitch($("#notify"), (on) => updateSettings({ notify: on }));
  bindSwitch($("#discord"), (on) => updateSettings({ discord: on }));
  bindSettingsNav();
  radioGroup($("#formats"), "data-format", (format) => updateSettings({ format }));
  darkMedia.addEventListener("change", applyTheme);

  const chooseMusicFolder = async () => {
    const folder = await api().choose_folder(state.settings.folder);
    if (folder) updateSettings({ folder });
  };
  $("#folder").addEventListener("click", chooseMusicFolder);
  $("#music-folder-change").addEventListener("click", chooseMusicFolder);
  $("#library-folder-add").addEventListener("click", async () => {
    const folder = await api().choose_folder("");
    const known = libraryFolders();
    if (folder && !known.includes(folder)) {
      updateSettings({ library_folders: [...(state.settings.library_folders || []), folder] });
    }
  });
  for (const button of $$("[data-step]")) {
    button.addEventListener("click", () => {
      const threads = Math.min(8, Math.max(1, state.settings.threads + Number(button.dataset.step)));
      updateSettings({ threads });
    });
  }

  radioGroup($("#language"), "data-language", changeLanguage);
  $("#cookies").addEventListener("change", (event) => updateSettings({ cookies_browser: event.target.value }));
  $("#rate").addEventListener("change", (event) => updateSettings({ rate_limit: Number(event.target.value) }));
  $("#track-name").addEventListener("change", (event) => updateSettings({ track_name: event.target.value }));
  $("#folder-layout").addEventListener("change", (event) => updateSettings({ folder_name: event.target.value }));
  // A proxy is typed rather than picked, so it is taken once the field is left
  $("#proxy").addEventListener("change", (event) => updateSettings({ proxy: event.target.value.trim() }));
  $("#proxy").addEventListener("input", (event) => {
    const value = event.target.value.trim();
    event.target.closest(".field").classList.toggle("invalid", Boolean(value) && !PROXY_RE.test(value));
  });
  // On is the program's own relay, which is what an empty setting means
  bindSwitch($("#relay"), (on) => updateSettings({ relay: on ? "" : "off" }));
  $("#paste").addEventListener("click", pasteFromClipboard);
  $("#link").addEventListener("input", clearLinkError);
  $("#form").addEventListener("submit", submitLinks);
  bindModeMenu();
  for (const button of $$(".stop")) button.addEventListener("click", stopAll);
  for (const button of $$(".pause")) button.addEventListener("click", togglePause);
  $("#clear").addEventListener("click", clearFinished);
  $("#log-open").addEventListener("click", () => api().open_logs());
  $("#log-copy").addEventListener("click", copyReport);
  $("#network-check").addEventListener("click", checkNetwork);

  $("#library-filter").addEventListener("input", renderLibrary);
  bindSearch();
  bindPlayer();
  $("#library-refresh").addEventListener("click", loadLibrary);
  $("#library-folder").addEventListener("click", () => api().open_folder(state.settings.folder));
  $("#library-shuffle").addEventListener("click", shuffleLibrary);
  $("#library-delete").addEventListener("click", deleteSelected);
  $("#library-again").addEventListener("click", () => downloadAgain(selectedItems().filter(hasMissing)));
  $("#library-tidy").addEventListener("click", () => {
    const picked = selectedItems().filter(hasGaps);
    tidyLibrary(picked.length ? picked : state.library.items.filter(hasGaps), { ask: !picked.length });
  });
  $("#library").addEventListener("click", onLibraryClick);
  $("#library").addEventListener("keydown", onLibraryKey);
  $("#library").addEventListener("contextmenu", onLibraryContextMenu);
  $("#library-cards").addEventListener("click", onCardsClick);
  $("#library-cards").addEventListener("keydown", onCardsKey);
  $("#library-cards").addEventListener("contextmenu", onCardsContextMenu);
  $("#library-artists").addEventListener("click", onArtistsClick);
  $("#library-artists").addEventListener("keydown", onCardsKey);
  $(".artist-albums").addEventListener("click", onCardsClick);
  $(".artist-albums").addEventListener("keydown", onCardsKey);
  $("#library-tracks").addEventListener("dblclick", (event) => {
    const row = event.target.closest(".track-item");
    if (row) openTrack(row);
  });
  $("#library-tracks").addEventListener("keydown", onTracksKey);
  for (const tab of $$("#library-tabs [role=tab]")) {
    tab.addEventListener("click", () => showLibraryTab(tab.dataset.tab));
  }
  radioGroup($("#library-view"), "data-library-view", setLibraryView);
  $("#library-sort").addEventListener("change", onSortPick);
  $("#library-genre").addEventListener("change", (event) => {
    state.library.genre = event.target.value;
    renderLibrary();
  });
  $("#library-order").addEventListener("change", onSortPick);
  $("#page-back").addEventListener("click", () => closePage());
  $("#album-page").addEventListener("click", onPageAction);
  // The mouse's back button, as in a browser
  $("#view-library").addEventListener("mouseup", (event) => {
    if (event.button === 3 && !$("#library-page").hidden) closePage();
  });
  new ResizeObserver(() => placeTabInk(false)).observe($("#library-tabs"));
  $("#library-menu").addEventListener("click", onLibraryMenuClick);
  // Anywhere else — including another window — closes the menu
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#library-menu")) closeLibraryMenu(false);
  });
  window.addEventListener("blur", () => closeLibraryMenu(false));
  for (const button of $$("#view-library .sort")) {
    button.addEventListener("click", () => sortLibraryBy(button.dataset.sort));
  }

  document.addEventListener("keydown", onShortcut);
  document.addEventListener("paste", (event) => {
    if (event.target instanceof Element && event.target.closest("input, textarea")) return;
    const text = event.clipboardData.getData("text/plain").trim();
    if (!text) return;
    event.preventDefault();
    showView("download");
    appendLinks(text);
  });
  // A dropped link would otherwise navigate the whole window away
  document.addEventListener("dragover", (event) => event.preventDefault());
  document.addEventListener("drop", (event) => {
    event.preventDefault();
    const file = [...(event.dataTransfer.files || [])].find((item) => /\.(txt|csv)$/i.test(item.name));
    if (file) {
      file.text().then((text) => api().parse_list(text, file.name)).then(queueList);
      return;
    }
    const text = event.dataTransfer.getData("text/uri-list") || event.dataTransfer.getData("text/plain");
    if (!text.trim()) return;
    showView("download");
    appendLinks(text.trim());
  });
}

function onShortcut(event) {
  const floating = ["#track-menu", "#playlist-menu", "#eq-panel", "#queue-panel"].map((id) => $(id)).find((element) => !element.hidden);
  if (event.key === "Escape" && floating) {
    event.preventDefault();
    if (floating.id === "eq-panel") toggleEqualizer(false);
    else if (floating.id === "queue-panel") toggleQueue(false);
    else closeMenu(floating);
    return;
  }
  if (event.key === "Escape" && !$("#mode-menu").hidden) {
    setMenuOpen(false);
  } else if (event.ctrlKey && !event.altKey && !event.shiftKey && /^[1-4]$/.test(event.key)) {
    event.preventDefault();
    showView(VIEWS[Number(event.key) - 1]);
  } else if (event.key === "F5" || (event.ctrlKey && event.code === "KeyR")) {
    event.preventDefault(); // reloading the page would lose the download list
    if (state.view === "library") loadLibrary();
  } else if (event.ctrlKey && !event.altKey && !event.shiftKey && event.code === "KeyK") {
    event.preventDefault();
    showView("search");
  } else if (event.key === "Escape" && state.view === "now") {
    closeNowPlaying();
  } else if (event.key === "Escape" && state.view === "search" && !$("#search-page").hidden) {
    closeSearchPage();
  } else if (event.ctrlKey && !event.altKey && !event.shiftKey && event.code === "KeyB") {
    event.preventDefault();
    toggleSidebar();
  } else if (event.ctrlKey && !event.altKey && event.code === "KeyF") {
    // the library search box is not focused automatically, so give it a shortcut
    event.preventDefault();
    if (state.view === "library") $("#library-filter").focus();
    if (state.view === "search") $("#search-input").focus();
  } else if (state.view === "library" && !(event.target instanceof Element && event.target.closest("input, textarea"))) {
    onLibraryShortcut(event);
  }
}

function onLibraryShortcut(event) {
  const { selected, shown } = state.library;
  if (event.key === "Escape" && !$("#library-menu").hidden) {
    closeLibraryMenu();
    return;
  }
  const pageOpen = !$("#library-page").hidden;
  if (pageOpen && (event.key === "Escape" || event.key === "Backspace" || (event.altKey && event.key === "ArrowLeft"))) {
    event.preventDefault();
    closePage();
    return;
  }
  if (pageOpen) return;
  if (event.ctrlKey && !event.altKey && event.code === "KeyA") {
    event.preventDefault();
    state.library.selected = new Set(shown);
    renderSelection();
  } else if (event.key === "Delete" && selected.size) {
    event.preventDefault();
    deleteSelected();
  } else if (event.key === "Escape" && selected.size) {
    clearSelection();
  }
}

function showView(name) {
  state.view = name;
  for (const button of $$("[data-view]")) {
    if (button.dataset.view === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  for (const view of $$(".view")) view.hidden = view.id !== `view-${name}`;
  setMenuOpen(false, false);
  if (name === "download") $("#link").focus();
  $("#player-lyrics")?.setAttribute("aria-pressed", String(name === "now"));
  // Now playing covers the whole window; any way out of it gives the window back
  document.documentElement.classList.toggle("now-mode", name === "now");
  // An open album takes the whole window only while the library is on screen:
  // "Fetch what is missing" on its page, a shortcut or the rail itself lead
  // elsewhere with the page still open behind, and the rail has to come back
  document.documentElement.classList.toggle("album-full", albumFillsWindow());
  if (name !== "now" && player.fullscreen) toggleFullscreen();
  if (name === "settings") {
    requestAnimationFrame(spySettings); // the cards have no places until the view shows
    loadScrobbleAccounts();
  }
  if (name === "search") {
    renderSearch();
    $("#search-input").focus();
    // The search marks what the library holds: it is read, if it was not yet
    if (state.library.stale && !state.library.loading) loadLibrary();
  }
  if (name === "library") {
    requestAnimationFrame(() => placeTabInk(false)); // the tabs have no size until the view shows
    if (state.library.stale || state.library.folder !== libraryFolders().join("\n")) loadLibrary();
  } else {
    clearSelection();
  }
  renderStatus();
}

function bindModeMenu() {
  const menu = $("#mode-menu");
  $("#mode").addEventListener("click", () => setMenuOpen(menu.hidden));
  menu.addEventListener("click", (event) => {
    if (event.target.closest("[data-action=import]")) {
      setMenuOpen(false, false);
      api().pick_list().then(queueList);
      return;
    }
    const item = event.target.closest("[data-dry-run]");
    if (!item) return;
    updateSettings({ dry_run: item.dataset.dryRun === "true" });
    setMenuOpen(false, false);
    $("#link").focus();
  });
  menu.addEventListener("keydown", (event) => {
    const step = { ArrowUp: -1, ArrowDown: 1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const items = [...$$("[role=menuitemradio]", menu)];
    items[(items.indexOf(document.activeElement) + step + items.length) % items.length].focus();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!menu.hidden && !event.target.closest(".split")) setMenuOpen(false, false);
  });
}

function setMenuOpen(open, restoreFocus = true) {
  const menu = $("#mode-menu");
  const wasOpen = !menu.hidden;
  menu.hidden = !open;
  $("#mode").setAttribute("aria-expanded", String(open));
  if (open) ($("[aria-checked=true]", menu) || $("button", menu)).focus();
  else if (wasOpen && restoreFocus) $("#mode").focus();
}

function radioGroup(group, attribute, onSelect) {
  group.addEventListener("click", (event) => {
    const button = event.target.closest(`[${attribute}]`);
    if (button) onSelect(button.getAttribute(attribute));
  });
  group.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[event.key];
    if (!step) return;
    const buttons = [...group.querySelectorAll(`[${attribute}]`)];
    const current = buttons.indexOf(document.activeElement);
    if (current < 0) return;
    event.preventDefault();
    const next = buttons[(current + step + buttons.length) % buttons.length];
    next.focus();
    onSelect(next.getAttribute(attribute));
  });
}

// A switch, and the row it sits on, which turns it as well: the words of a
// setting are a bigger target than the switch itself
function bindSwitch(button, onChange) {
  button.closest(".setting").addEventListener("click", (event) => {
    const other = event.target.closest("a, button, input, select, label");
    if (other && other !== button) return;
    onChange(button.getAttribute("aria-checked") !== "true");
  });
}

function syncSwitch(button, on) {
  button.setAttribute("aria-checked", String(Boolean(on)));
}

// What an album comes out as under the naming rules chosen, on an example
function namingExample(settings) {
  const slash = settings.folder.includes("\\") ? "\\" : "/";
  const root = settings.folder.split(/[\\/]+/).filter(Boolean).pop() || settings.folder;
  const album = {
    flat: ["Radiohead - In Rainbows (2007)"],
    nested: ["Radiohead", "In Rainbows (2007)"],
    album: ["In Rainbows (2007)"],
  }[settings.folder_name] || ["Radiohead - In Rainbows (2007)"];
  const track = settings.track_name === "artist" ? "01. Radiohead - 15 Step" : "01. 15 Step";
  return [root, ...album, `${track}.${settings.format}`].join(slash);
}

// The subjects beside the cards: a click scrolls to one, and the one in view is lit
function bindSettingsNav() {
  const scroller = $("#settings-scroll");
  $("#settings-nav").addEventListener("click", (event) => {
    const link = event.target.closest("a[href^='#']");
    if (!link) return;
    event.preventDefault();
    const card = $(link.getAttribute("href"));
    card.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
    markSettingsNav(card.id);
  });
  let queued = false;
  scroller.addEventListener("scroll", () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      spySettings();
    });
  }, { passive: true });
  spySettings();
}

function spySettings() {
  const scroller = $("#settings-scroll");
  const cards = $$(".settings-card", scroller);
  const line = scroller.getBoundingClientRect().top + scroller.clientHeight / 3;
  let current = cards[0];
  for (const card of cards) if (card.getBoundingClientRect().top <= line) current = card;
  // At the bottom the last card counts, however little of it rose past the line
  if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) current = cards[cards.length - 1];
  if (current) markSettingsNav(current.id);
}

function markSettingsNav(id) {
  for (const link of $$("#settings-nav a")) link.setAttribute("aria-current", String(link.getAttribute("href") === `#${id}`));
}

function syncRadios(group, attribute, value) {
  for (const button of group.querySelectorAll(`[${attribute}]`)) {
    const checked = button.getAttribute(attribute) === value;
    button.setAttribute("aria-checked", String(checked));
    button.tabIndex = checked ? 0 : -1;
  }
}

/* Links */

async function pasteFromClipboard() {
  const text = (await api().paste()).trim();
  if (!text) {
    showLinkError(t("В буфере обмена пусто. Скопируйте ссылку на альбом или трек: "
    + "«Поделиться» → «Копировать ссылку»."));
    return;
  }
  appendLinks(text);
}

function appendLinks(text) {
  const input = $("#link");
  const current = input.value.trim();
  input.value = current ? `${current} ${text}` : text;
  clearLinkError();
  input.focus();
}

async function submitLinks(event) {
  event.preventDefault();
  const input = $("#link");
  const raw = input.value.trim();
  // Spotify adds a tracking ?si= parameter; other services keep ids in the query (?v=, ?list=, ?i=)
  const found = [...new Set((raw.match(LINK_RE) || []).map((link) =>
    link.replace(/^https?:\/\//i, "").replace(/(spotify\.com\/\S*?)\?.*$/, "$1")))];
  // No link in the field: the text is a name to search for, one release at a time
  const links = found.length ? found : (raw ? [raw] : []);
  if (!links.length) {
    showLinkError(explainBadLink(raw));
    input.focus();
    return;
  }
  if (await queueLinks(links)) input.value = "";
}

async function queueLinks(links) {
  const submit = $("#submit");
  submit.disabled = true;
  try {
    const { dry_run: dryRun, format } = state.settings;
    const jobs = await api().download(links, state.settings);
    for (const { job, link } of jobs) addJob(job, link, dryRun, format);
    return true;
  } finally {
    submit.disabled = false;
  }
}

// A file of links: picked from the menu, or dropped on the window. The list is
// read in the program, which knows the playlist exporters' CSV columns.
async function queueList(list) {
  if (!list) return;
  showView("download");
  if (list.error || !list.entries.length) {
    showLinkError(list.error || t("В файле {name} не нашлось ни ссылок, ни названий", { name: list.name }));
    return;
  }
  await queueLinks(list.entries);
  const message = list.skipped
    ? t("Из {name} в очередь: {count}, не разобрано строк: {skipped}",
        { name: list.name, count: list.entries.length, skipped: list.skipped })
    : t("Из {name} в очередь: {count}", { name: list.name, count: list.entries.length });
  announce(message);
  showLinkNote(message);
}

function explainBadLink() {
  return t("Вставьте ссылку на альбом, сингл или трек — или напишите, что искать: "
    + "«Исполнитель - Альбом».");
}

// A passing word under the field — what an imported list turned into — that
// clears itself rather than waiting to be dismissed
let linkNoteTimer = 0;
function showLinkNote(message) {
  $("#link-note-text").textContent = message;
  $("#link-note").hidden = false;
  clearTimeout(linkNoteTimer);
  linkNoteTimer = setTimeout(() => { $("#link-note").hidden = true; }, 8000);
}

function showLinkError(message) {
  $("#link").setAttribute("aria-invalid", "true");
  $("#link").closest(".field").classList.add("invalid");
  $("#link-error-text").textContent = message;
  $("#link-error").hidden = false;
}

function clearLinkError() {
  $("#link").removeAttribute("aria-invalid");
  $("#link").closest(".field").classList.remove("invalid");
  $("#link-error").hidden = true;
}

/* Jobs */

function addJob(id, link, dryRun, format) {
  if (!hasActiveJobs()) state.run = { jobs: new Set(), workStart: 0 };
  state.run.jobs.add(id);

  const node = $("#job-template").content.firstElementChild.cloneNode(true);
  const job = {
    id, link, dryRun, node, format,
    state: "queued", stopping: false, title: prettyLink(link), sub: "", message: "", folder: "",
    done: 0, total: 0, result: null, summary: "", tracks: new Map(), expanded: false,
  };
  $(".tag", node).textContent = dryRun ? t("проверка") : format;
  $(".job-row", node).addEventListener("click", (event) => {
    if (!event.target.closest(".cell-actions") && job.tracks.size) setExpanded(job, !job.expanded);
  });
  $(".open", node).addEventListener("click", () => api().open_folder(job.folder));
  $(".retry", node).addEventListener("click", () => retryJob(job));
  $(".choose-go", node).addEventListener("click", (event) => {
    event.stopPropagation();
    downloadChosen(job);
  });
  $(".offer-use", node).addEventListener("click", (event) => {
    event.stopPropagation();
    updateSettings({ proxy: job.offer.url });
    for (const other of state.jobs.values()) if (other.offer && other !== job) renderJob(other);
    retryJob(job);
  });
  $(".watch", node).addEventListener("click", () => toggleWatch(job));
  state.jobs.set(id, job);
  $("#jobs").prepend(node);
  renderJob(job);

  // Events can arrive before download() has returned the job id
  const early = state.orphans.filter((event) => event.job === id);
  state.orphans = state.orphans.filter((event) => event.job !== id);
  early.forEach(handleEvent);
  flushRender();
}

// What the window showed last time: finished releases stay as a history, and
// anything cut short by closing the window comes back with its retry button.
function restoreHistory(history) {
  for (const entry of history) {
    addJob(entry.job, entry.link, Boolean(entry.dry_run), entry.format || state.settings.format);
    const job = state.jobs.get(entry.job);
    job.restored = true;
    job.state = entry.state === "queued" || entry.state === "running" ? "cancelled" : entry.state;
    job.title = entry.title || prettyLink(entry.link);
    job.folder = entry.folder || "";
    job.single = Boolean(entry.single);
    job.message = entry.message || "";
    job.total = entry.total || 0;
    job.done = entry.ok === undefined ? 0 : entry.ok + entry.skipped + entry.failed;
    if (entry.title) {
      const kind = entry.kind ? entry.kind.charAt(0).toUpperCase() + entry.kind.slice(1) : "";
      job.sub = [entry.artist, kind, entry.year, entry.service].filter(Boolean).join(" · ");
    }
    if (entry.ok !== undefined) {
      job.result = { ok: entry.ok, skipped: entry.skipped, failed: entry.failed, doubtful: entry.doubtful || 0 };
      job.summary = summaryText({ ...job.result, dry_run: job.dryRun });
      if (entry.failed) job.state = "partial";
      if (entry.doubtful) job.state = "choose";
    }
    if (entry.cover) loadCover($(".cover", job.node), entry.cover);
    if (entry.tracks && entry.tracks.length) {
      onRelease(job, {
        folder: job.folder, title: job.title, kind: entry.kind || "", album: entry.album || "",
        artist: entry.artist || "", year: entry.year || "", service: entry.service || "",
        cover: "", note: "", tracks: entry.tracks,
      }, true);
    }
    renderJob(job);
  }
}

function hasActiveJobs() {
  return [...state.jobs.values()].some((job) => ACTIVE.has(job.state));
}

function renderJob(job) {
  const { node } = job;
  const status = jobStatus(job);
  // Derived, not toggled: this line rewrites className, so anything imperative
  // set elsewhere would be wiped on the next event.
  const classes = ["job", `is-${job.state}`];
  if (job.tracks.size) classes.push("has-tracks");
  node.className = classes.join(" ");
  $(".job-row", node).className = `row job-row tone-${status.tone}`;
  const title = $(".title", node);
  title.textContent = job.title;
  title.title = job.title;
  const sub = $(".sub", node);
  sub.textContent = job.state === "error" ? job.message : job.sub;
  const note = $(".note", node);
  note.textContent = job.note || "";
  note.hidden = !job.note || job.state === "error";
  // Tracks held back for a choice: how many are chosen, and the button that sends them
  const waiting = [...job.tracks.values()].filter((track) => track.state === "doubtful");
  const chosen = waiting.filter((track) => track.choice !== undefined).length;
  $(".choose-bar", node).hidden = !waiting.length;
  $(".choose-text", node).textContent = chosen
    ? t("Выбрано {chosen} из {count}", { chosen, count: waiting.length })
    : t("Выберите запись для каждого такого трека или пропустите его");
  $(".choose-go", node).hidden = !chosen;
  const offer = $(".offer:not(.choose-bar)", node);
  offer.hidden = !(job.offer && job.state === "error" && !state.settings.proxy);
  if (!offer.hidden) {
    $(".offer-text", node).textContent =
      t("На компьютере работает {client}: через его прокси Spotify открывается", { client: job.offer.client });
  }
  // The card's arrow drops once for each track that lands in the folder
  const landed = job.done > (job.shownDone ?? job.done);
  job.shownDone = job.done;
  setStatusCell($(".cell-status", node), { ...status, drop: landed });

  const finished = !ACTIVE.has(job.state);
  const retry = $(".retry", node);
  retry.hidden = !["error", "cancelled", "partial"].includes(job.state);
  const retryLabel = t(job.state === "cancelled" ? (job.total ? "Продолжить" : "Запустить")
                                                 : "Повторить");
  retry.title = retryLabel;
  retry.setAttribute("aria-label", retryLabel);
  $(".open", node).hidden = !(finished && !job.dryRun && job.folder && job.state !== "error");
  // A lone track has nothing to add to it, and a dry run downloaded nothing to
  // keep up to date, so only real albums and playlists get the eye.
  const watchButton = $(".watch", node);
  const watching = isWatched(job.link);
  watchButton.hidden = !(watching || (["done", "partial"].includes(job.state) && !job.dryRun && !job.single));
  watchButton.setAttribute("aria-pressed", String(watching));
  const watchLabel = t(watching ? "Перестать следить" : "Следить за новыми треками");
  watchButton.title = watchLabel;
  watchButton.setAttribute("aria-label", watchLabel);
}

function jobStatus(job) {
  switch (job.state) {
    case "running": {
      // Until the page behind the link is read there is nothing to count: the
      // arc turns, and the bar waits empty for the number
      if (!job.total) {
        return { icon: "spinner", tone: "muted", progress: 0,
                 text: t(job.stopping ? "Останавливаем…" : "Читаем ссылку…") };
      }
      const ratio = jobProgress(job);
      const stage = job.stopping ? { icon: "spinner", text: t("Останавливаем…") } : jobStage(job, ratio);
      return { ...stage, tone: "primary", progress: ratio };
    }
    case "done": {
      const { ok, skipped } = job.result;
      const text = t(job.dryRun ? "Всё найдено" : !ok && skipped ? "Уже скачано" : "Готово");
      return { icon: "check", tone: "success", text, tip: job.summary };
    }
    case "choose": {
      const waiting = [...job.tracks.values()].filter((track) => track.state === "doubtful").length;
      if (!waiting) return { icon: "check", tone: "success", text: t("Готово"), tip: job.summary };
      return { icon: "alert", tone: "warning", tip: job.summary,
               text: t("Ждут выбора: {count}", { count: waiting }) };
    }
    case "partial":
      return { icon: "alert", tone: "warning", tip: job.summary,
               text: t(job.dryRun ? "Не найдено: {failed}" : "Не скачано: {failed}",
                       { failed: job.result.failed }) };
    case "error":
      return { icon: "alert", tone: "danger", text: t("Ошибка"), tip: job.message };
    case "cancelled":
      return { icon: "stop", tone: "muted",
               text: job.total ? t("Остановлено · {done} из {total}",
                                   { done: job.done, total: job.total }) : t("Отменено") };
    default:
      return { icon: "dot", tone: "muted", text: t("В очереди") };
  }
}

// Which of the steps the downloader really takes the release is on.
// Measuring the loudness comes last and runs for the album as a whole, so it
// speaks for the card; before the first track arrives there is only the search.
function jobStage(job, ratio) {
  if (job.loudness) return { icon: "level", text: t("Выравниваем громкость") };
  const states = [...job.tracks.values()].map((track) => track.state);
  if (!job.done && !states.includes("download")) {
    return states.includes("search") ? { icon: "search", text: t("Ищем источник") }
                                     : { icon: "dot", text: t("В очереди") };
  }
  const left = formatEta(smoothEta(job, remainingSeconds([...job.tracks.values()])));
  const text = t("{done} из {total} · {percent}%", { done: job.done, total: job.total, percent: Math.floor(ratio * 100) });
  return { icon: "download", text: left ? t("{progress} · ещё {eta}", { progress: text, eta: left }) : text };
}

// How long the tracks still need, the way the downloader works through them:
// as many at once as there are threads, each waiting track taking the thread
// that frees first. A track under way has the downloader's own estimate; one
// still waiting takes as long as those before it did.
function remainingSeconds(tracks) {
  const now = Date.now();
  const active = tracks.filter((track) => track.state === "search" || track.state === "download");
  const waiting = tracks.filter((track) => track.state === "waiting").length;
  if (!active.length && !waiting) return null;
  const mean = (values) => (values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0);
  const took = tracks.map((track) => track.took).filter(Boolean);
  const whole = took.length ? mean(took) : mean(active.filter((track) => track.eta != null && track.startedAt)
    .map((track) => (now - track.startedAt) / 1000 + trackLeft(track)));
  if (!whole) return null;
  const threads = Math.max(1, state.settings?.threads || 1);
  const lanes = active.map((track) => trackLeft(track) ?? Math.max(1, whole - (now - track.startedAt) / 1000));
  while (lanes.length < threads) lanes.push(0);
  for (let i = 0; i < waiting; i++) {
    lanes.sort((a, b) => a - b);
    lanes[0] += whole;
  }
  return Math.max(...lanes);
}

// What the downloader last said this track still needs, counted down since
function trackLeft(track) {
  if (track.eta == null) return null;
  return Math.max(0, track.eta - (Date.now() - track.etaAt) / 1000);
}

// The estimate moves with every tick; shown as it is, it would twitch
function smoothEta(holder, seconds) {
  if (seconds == null) {
    holder.etaShown = null;
    return null;
  }
  holder.etaShown = holder.etaShown == null ? seconds : holder.etaShown + 0.25 * (seconds - holder.etaShown);
  return holder.etaShown;
}

function formatEta(seconds) {
  if (seconds == null) return "";
  if (seconds < 10) return t("несколько секунд");
  if (seconds < 55) return t("~{seconds} сек", { seconds: Math.max(10, Math.round(seconds / 5) * 5) });
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return t("~{minutes} мин", { minutes });
  return t("~{hours} ч {minutes} мин", { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
}

function jobProgress(job) {
  let downloading = 0;
  for (const track of job.tracks.values()) {
    if (track.state === "download") downloading += track.percent / 100;
  }
  return Math.min(1, (job.done + downloading) / job.total);
}

function setStatusCell(cell, { icon, text, tip = "", progress, drop = false }) {
  const statusIcon = $(".status-icon", cell);
  const before = statusIcon.dataset.icon;
  if (before !== icon) {
    drawIcon(statusIcon, icon);
    // A tick after work seen under way draws itself in; one read back from
    // the history is simply there
    if (icon === "check" && BUSY_ICONS.has(before)) drawTick(statusIcon);
  } else if (drop && icon === "download") {
    dropArrow(statusIcon);
  }
  $(".status-text", cell).textContent = text;
  cell.title = tip;
  const bar = $(".bar", cell);
  bar.hidden = progress === undefined;
  if (typeof progress === "number") {
    bar.style.setProperty("--p", progress);
    bar.setAttribute("aria-valuenow", String(Math.round(progress * 100)));
  }
}

// The icon is copied out of its symbol rather than shown through <use>, so
// that its parts can move on their own: the bars level, the arrow drops, the
// tick draws itself in.
function drawIcon(svg, name) {
  const symbol = document.getElementById(`i-${name}`);
  svg.setAttribute("viewBox", symbol.getAttribute("viewBox"));
  svg.replaceChildren(...[...symbol.children].map((node) => node.cloneNode(true)));
  svg.dataset.icon = name;
}

// Once for a piece that has really arrived, and no oftener than the eye can
// follow: a download that stalls leaves the arrow standing, which a loop
// turning on its own could never show.
function dropArrow(svg) {
  const now = performance.now();
  if (reduceMotion() || now - Number(svg.dataset.dropped || 0) < 700) return;
  svg.dataset.dropped = String(now);
  $(".drop", svg).animate(
    [{ transform: "none" }, { transform: "translateY(2.5px)", offset: 0.35 }, { transform: "none" }],
    { duration: 320, easing: "ease-in-out" });
}

function drawTick(svg) {
  if (reduceMotion() || !svg.getClientRects().length) return; // a folded row has nobody to see it
  const path = $("path", svg);
  const length = `${path.getTotalLength()}`;
  path.animate([{ strokeDasharray: length, strokeDashoffset: length },
                { strokeDasharray: length, strokeDashoffset: "0" }],
               { duration: 260, easing: EMPHASIZED });
}

// Signs that start at different moments would each keep a phase of their own;
// set to one clock, the rows turn and level together, as one machine does.
const IN_STEP = new Set(["spin", "sniff", "level-a", "level-b", "level-c"]);
document.addEventListener("animationstart", (event) => {
  if (!IN_STEP.has(event.animationName)) return;
  for (const animation of event.target.getAnimations()) {
    if (animation.animationName === event.animationName) animation.startTime = 0;
  }
});

async function pollLoop() {
  try {
    const events = await api().poll();
    events.forEach(handleEvent);
    if (events.length) flushRender();
  } catch (error) {
    console.error(error);
  }
  setTimeout(pollLoop, 200);
}

function handleEvent(event) {
  if (event.type === "update") return onUpdateEvent(event);
  // Jobs the program started by itself — a watched playlist being checked —
  // arrive without a card, because the window never asked for them.
  if (event.type === "added") return addJob(event.job, event.link, event.dry_run, event.format);
  if (event.type === "watched") return setWatched(event.list);
  if (event.type === "tidy") return onTidyEvent(event);
  const job = state.jobs.get(event.job);
  if (!job) {
    state.orphans.push(event);
    if (state.orphans.length > 2000) state.orphans.splice(0, 1000);
    return;
  }
  if (event.type === "job") onJobEvent(job, event);
  else if (event.type === "release") onRelease(job, event);
  else if (event.type === "track") onTrack(job, event);
  else if (event.type === "progress") Object.assign(job, { done: event.done, total: event.total });
  // Metering the files after the last track: a note, so the card does not look stuck at 100%
  else if (event.type === "loudness") job.loudness = event.state === "running";
  state.dirty.add(job);
}

// Events come in bursts; rows and counters are redrawn once per burst
function flushRender() {
  for (const job of state.dirty) renderJob(job);
  state.dirty.clear();
  renderChrome();
}

// A Spotify refusal a VPN client's proxy may lift: Spotify keeps its player
// from some countries, and a VPN often runs here without the system knowing.
// The ports are looked at once a session, or again after nothing was found.
const PROXY_HELPS = new Set(["unavailable", "not_found"]);
let proxySearch = null;

function offerProxy(job) {
  if (state.settings.proxy || !/spotify|spoti\.fi/i.test(job.link)) return;
  proxySearch ??= api().find_proxies();
  proxySearch.then((found) => {
    if (!found.length) proxySearch = null;
    const proxy = found.find((item) => item.spotify === "ok");
    if (!proxy || job.state !== "error" || state.settings.proxy) return;
    job.offer = proxy;
    renderJob(job);
  });
}

function onJobEvent(job, event) {
  if (event.state === "running") {
    job.state = "running";
    return;
  }
  job.stopping = false;
  if (event.state === "error") {
    job.state = "error";
    job.message = event.message;
    announce(t("Ошибка: {message}", { message: event.message }));
    if (PROXY_HELPS.has(event.code)) offerProxy(job);
  } else if (event.state === "cancelled") {
    job.state = "cancelled";
  } else if (event.state === "done" && event.quiet) {
    // A watched playlist checked and found unchanged: nothing to show for it
    removeJob(job, true);
    renderChrome();
    return;
  } else if (event.state === "done") {
    job.result = event;
    job.summary = summaryText(event);
    // Tracks held back for a choice come first: they wait for the person at the window
    job.state = event.doubtful > 0 ? "choose" : event.failed > 0 ? "partial" : "done";
    if (event.doubtful > 0) setExpanded(job, true);
    else if (!event.failed && !event.dry_run) setExpanded(job, false);
    announce(`${job.title}: ${job.summary}`);
  }
  // Tracks that never started get no event of their own
  for (const track of job.tracks.values()) {
    if (TRACK_ACTIVE.has(track.state)) {
      track.state = "cancel";
      renderTrack(track);
    }
  }
  if (!job.dryRun && job.folder) {
    state.library.stale = true;
    if (state.view === "library") loadLibrary();
  }
}

function summaryText({ ok, skipped, failed, doubtful, dry_run: dryRun }) {
  const waiting = doubtful ? t("ждут выбора: {count}", { count: doubtful }) : "";
  return [summaryCounts({ ok, skipped, failed, dry_run: dryRun }), waiting].filter(Boolean).join(" · ");
}

function summaryCounts({ ok, skipped, failed, dry_run: dryRun }) {
  if (dryRun) {
    return failed
      ? t("Найдено {ok} из {total} · не найдено: {failed}", { ok, total: ok + failed, failed })
      : t("Найдены все треки: {ok}", { ok });
  }
  if (!ok && !failed && skipped) return t("Всё уже было скачано раньше");
  const parts = [t("Скачано {ok} {trackWord}",
    { ok, trackWord: plural(ok, "трек", "трека", "треков") })];
  if (skipped) parts.push(t("уже были: {skipped}", { skipped }));
  if (failed) parts.push(t("не удалось: {failed}", { failed }));
  return parts.join(" · ");
}

// A restored card is filled from what was saved, not from events: its tracks
// belong to a run that is over, so they stay out of the queue and the status
// bar, and the card waits to be opened instead of opening itself.
function onRelease(job, event, restored = false) {
  Object.assign(job, { folder: event.folder, title: event.title, total: event.tracks.length,
                       single: Boolean(event.single) });
  const kind = event.kind.charAt(0).toUpperCase() + event.kind.slice(1);
  const fromAlbum = event.kind === t("трек") && event.album && event.album !== event.title
    && t("из «{album}»", { album: event.album });
  job.sub = [event.artist, kind, fromAlbum, event.year, event.service].filter(Boolean).join(" · ");
  job.note = event.note || ""; // e.g. a playlist page that only gave its first tracks
  if (event.cover) loadCover($(".cover", job.node), event.cover);

  const multiDisc = event.tracks.some((track) => track.disc > 1);
  const rows = [];
  for (const info of event.tracks) {
    const track = {
      job, id: info.id, number: multiDisc ? `${info.disc}-${info.number}` : info.number,
      title: info.title, artists: info.artists, duration: info.duration,
      state: restored ? info.state || "waiting" : "waiting", percent: 0,
      source: restored ? info.source || "" : "", text: restored ? info.text || "" : "",
      candidates: restored ? info.candidates || [] : [],
    };
    track.row = createTrackRow(track, info.artists === event.artist ? "" : info.artists);
    renderTrack(track);
    job.tracks.set(info.id, track);
    rows.push(track.row);
    if (!restored) state.tracks.push(track);
  }
  $(".tracks", job.node).replaceChildren(...rows);
  for (const track of job.tracks.values()) renderChoices(track); // a row has to be in the list first
  setExpanded(job, !restored);
}

function createTrackRow(track, artists, release = "") {
  const row = $("#track-template").content.firstElementChild.cloneNode(true);
  $(".num", row).textContent = track.number;
  const title = $(".title", row);
  title.textContent = track.title;
  title.title = track.title;
  $(".sub", row).dataset.artists = artists;
  const releaseCell = $(".cell-release", row);
  releaseCell.textContent = release;
  releaseCell.title = release;
  $(".cell-dur", row).textContent = track.duration;
  return row;
}

function onTrack(job, event) {
  const track = job.tracks.get(event.id);
  if (!track) return;
  Object.assign(track, { state: event.state, text: event.text || "", percent: event.percent || 0,
                         wide: Boolean(event.wide), retry: Boolean(event.retry),
                         // seconds the downloader still gives this track, and when it said so
                         eta: typeof event.eta === "number" ? event.eta : null, etaAt: Date.now() });
  if ((event.state === "search" || event.state === "download") && !track.startedAt) track.startedAt = Date.now();
  if (event.state === "done" && track.startedAt && !track.took) track.took = (Date.now() - track.startedAt) / 1000;
  if (event.source) track.source = event.source;
  if (event.candidates) track.candidates = event.candidates;
  if (event.state !== "skip" && state.run?.jobs.has(job.id) && !state.run.workStart) state.run.workStart = Date.now();
  renderTrack(track);
}

function renderTrack(track) {
  const ui = TRACK_UI[track.state] || TRACK_UI.waiting;
  const label = trackLabel(track, ui);
  const note = trackNote(track);
  const downloading = track.state === "download";
  const drop = downloading && track.percent > (track.shownPercent || 0);
  track.shownPercent = downloading ? track.percent : 0;
  for (const row of [track.row]) {
    row.className = `row track tone-${ui.tone}`;
    const sub = $(".sub", row);
    sub.textContent = note || sub.dataset.artists;
    sub.classList.toggle("danger", TRACK_FAILED.has(track.state));
    $(".cell-source", row).textContent = track.source;
    setStatusCell($(".cell-status", row), {
      icon: ui.icon,
      text: downloading ? [`${label} ${track.percent}%`, formatEta(trackLeft(track))].filter(Boolean).join(" · ")
        : label,
      tip: track.state === "skip" ? t("Файл уже есть в папке") : "",
      progress: downloading ? track.percent / 100 : undefined,
      drop,
    });
  }
  renderChoices(track);
}

// A track held back because it looks like more than one recording: its
// candidates under its row, each to be listened to and chosen, or the track
// passed over. The choices go out together from the card's own button.
function renderChoices(track) {
  const shown = track.state === "doubtful" && track.candidates?.length && track.row.parentNode;
  if (!shown) {
    track.choicesNode?.remove();
    track.choicesNode = null;
    return;
  }
  if (!track.choicesNode) {
    track.choicesNode = document.createElement("li");
    track.choicesNode.className = "choices";
    track.row.after(track.choicesNode);
  }
  const items = track.candidates.map((candidate, index) => {
    const item = document.createElement("div");
    const chosen = track.choice === index;
    item.className = `choice${chosen ? " is-chosen" : ""}`;
    item.innerHTML = '<button type="button" class="choice-pick" role="radio"><svg class="icon sm" aria-hidden="true">'
      + '<use href="#i-check"/></svg></button><div class="choice-main"><p class="title"></p><p class="sub"></p></div>'
      + '<button type="button" class="tool-btn choice-listen"></button>';
    const pick = $(".choice-pick", item);
    pick.setAttribute("aria-checked", String(chosen));
    pick.setAttribute("aria-label", t("Выбрать эту запись"));
    $(".title", item).textContent = [candidate.artists, candidate.title].filter(Boolean).join(" — ");
    $(".sub", item).textContent = t("{source} · {length}, нужно {wanted} · совпадение {percent}%", {
      source: candidate.source_name || candidate.source, length: formatDuration(candidate.duration),
      wanted: track.duration, percent: Math.min(100, Math.round(candidate.score * 100)),
    });
    $(".choice-listen", item).textContent = t("Послушать");
    $(".choice-listen", item).addEventListener("click", () => api().open_page(candidate.page_url || candidate.url));
    const choose = () => {
      track.choice = chosen ? undefined : index;
      renderChoices(track);
      renderJob(track.job);
    };
    pick.addEventListener("click", choose);
    $(".choice-main", item).addEventListener("click", choose);
    return item;
  });
  const skip = document.createElement("button");
  skip.type = "button";
  skip.className = "tool-btn choice-skip";
  skip.textContent = t("Не скачивать");
  skip.addEventListener("click", () => {
    track.state = "declined";
    delete track.choice;
    renderTrack(track);
    renderJob(track.job);
  });
  track.choicesNode.replaceChildren(...items, skip);
}

async function downloadChosen(job) {
  const chosen = [...job.tracks.values()].filter((track) => track.state === "doubtful" && track.choice !== undefined);
  const choices = Object.fromEntries(chosen.map((track) => [track.id, track.candidates[track.choice]]));
  const queued = await api().download_choices(job.link, state.settings, choices);
  if (!queued) return;
  addJob(queued.job, queued.link, false, job.format);
  for (const track of chosen) {
    track.state = "handed";
    delete track.choice;
    renderTrack(track);
  }
  renderJob(job);
  announce(t("Выбранные треки поставлены в очередь: {count}", { count: chosen.length }));
}

// Which turn the step has taken: the second search, over every source, or a
// source tried after the first one refused
function trackLabel(track, ui) {
  if (track.state === "search" && track.wide) return t("Ищем везде");
  if (track.state === "download" && track.retry && track.source) {
    return t("Пробуем {source}", { source: track.source });
  }
  return t(ui.label);
}

function trackNote({ state: name, text }) {
  switch (name) {
    case "found": return t("Найдено: {text}", { text });
    case "missing": return t("Нет в открытом доступе на YouTube Music и SoundCloud");
    case "doubtful": return t("Похоже сразу на несколько записей — выберите нужную");
    case "error": return text;
    default: return "";
  }
}

const COLLAPSE_MS = 260;

function setExpanded(job, expanded, animate = true) {
  if (job.expanded === expanded) return;
  job.expanded = expanded;
  foldTracks(job, animate);
  renderJob(job);
  const toggle = $(".expander", job.node);
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute("aria-label", expanded ? "Скрыть треки" : "Показать треки");
}

// Only the part of the list that is on screen moves. Growing a 300-track
// playlist to its full height in the same 220 ms would cover the visible part
// in a frame or two and read as a snap; the rows past the bottom edge are out
// of sight either way, so they simply arrive (or leave) with the last frame.
function foldTracks(job, animate) {
  const tracks = $(".tracks", job.node);
  const running = tracks.getAnimations();
  const current = running.length ? tracks.getBoundingClientRect().height : null;
  running.forEach((animation) => animation.cancel());

  const quiet = !animate || matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (job.expanded) tracks.hidden = false;
  // The room between the top of the list and the bottom of the scrolling area;
  // a hidden view measures zero here and gets no animation.
  const room = Math.max(0, tracks.closest(".list-scroll").getBoundingClientRect().bottom
                           - tracks.getBoundingClientRect().top);
  const visible = Math.min(tracks.scrollHeight, room);
  const from = current ?? (job.expanded ? 0 : visible);
  const to = job.expanded ? visible : 0;
  if (quiet || from === to) {
    tracks.hidden = !job.expanded;
    return;
  }
  const style = getComputedStyle(document.documentElement);
  const animation = tracks.animate(
    { height: [`${Math.min(from, room)}px`, `${to}px`] },
    { duration: parseFloat(style.getPropertyValue("--settle")) || 220,
      easing: style.getPropertyValue("--ease").trim() || "ease-out" },
  );
  // Out of the tab order once it is really closed; hidden first, so the
  // cancelled animation never lets the full height flash back.
  animation.finished.then(() => {
    if (!job.expanded) tracks.hidden = true;
    animation.cancel();
  }, () => {});
}

// Paused means "start nothing new": a track already downloading is finished,
// so nothing is thrown away and the queue picks up where it stood.
function togglePause() {
  setPaused(!state.paused);
  announce(t(state.paused ? "Загрузка на паузе" : "Загрузка продолжается"));
}

function setPaused(paused) {
  state.paused = paused;
  api().pause(paused);
  for (const button of $$(".pause")) {
    button.setAttribute("aria-pressed", String(paused));
    $("span", button).textContent = paused ? "Продолжить" : "Пауза";
    $("use", button).setAttribute("href", paused ? "#i-play" : "#i-pause");
  }
  reportProgress();
}

function stopAll() {
  if (state.paused) setPaused(false); // stopping a paused queue must not hang it
  api().stop();
  for (const job of state.jobs.values()) {
    if (job.state === "running") {
      job.stopping = true;
      renderJob(job);
    }
  }
}

function clearFinished() {
  for (const job of [...state.jobs.values()]) {
    if (!ACTIVE.has(job.state)) removeJob(job, true);
  }
  api().forget_history(); // cleared here means cleared next time too
  renderChrome();
}

function removeJob(job, fade = false) {
  // Bookkeeping happens at once; only the row itself lingers to fade out, so a
  // second click can never act on a job that is already gone.
  state.tracks = state.tracks.filter((track) => track.job !== job);
  state.jobs.delete(job.id);
  if (!fade) {
    job.node.remove();
    return;
  }
  job.node.classList.add("leaving");
  setTimeout(() => job.node.remove(), COLLAPSE_MS);
}

async function retryJob(job) {
  const jobs = await api().download([job.link], { ...state.settings, dry_run: job.dryRun });
  removeJob(job);
  for (const { job: id, link } of jobs) addJob(id, link, job.dryRun, job.format);
}

/* Window chrome: counters, badges, the lines above the lists */

function renderChrome() {
  const jobs = [...state.jobs.values()];
  const active = jobs.some((job) => ACTIVE.has(job.state));
  $("#empty").hidden = jobs.length > 0;
  $("#jobs-count").textContent = jobs.length || "";
  for (const button of $$(".stop")) button.hidden = !active;
  for (const button of $$(".pause")) button.hidden = !active;
  if (!active && state.paused) setPaused(false); // nothing left to hold back
  $("#clear").hidden = !jobs.some((job) => !ACTIVE.has(job.state));

  // How many tracks are under way, on the panel, for when another section is open
  const underWay = state.tracks.filter((track) => TRACK_ACTIVE.has(track.state)).length;
  const badge = $("#download-badge");
  badge.textContent = underWay > 99 ? "99+" : underWay;
  badge.hidden = !underWay;
  renderStatus();
  reportProgress();
}

// The run's progress, shown outside the window as well: a download is mostly
// watched with the window minimised, from its title and its taskbar button.
// Green while it goes, yellow on pause, red once something has failed.
function reportProgress() {
  const jobs = state.run ? [...state.run.jobs].map((id) => state.jobs.get(id)).filter(Boolean) : [];
  const tracks = jobs.flatMap((job) => [...job.tracks.values()]);
  let name = "none";
  let ratio = 0;
  if (jobs.some((job) => ACTIVE.has(job.state))) {
    ratio = tracks.reduce((sum, track) => sum + trackShare(track), 0) / (tracks.length || 1);
    const failed = tracks.some((track) => TRACK_FAILED.has(track.state))
      || jobs.some((job) => job.state === "error");
    if (!tracks.length) name = "indeterminate"; // only links being read: no count yet
    else name = state.paused ? "paused" : failed ? "error" : "normal";
  }
  const reported = `${name} ${Math.floor(ratio * 100)}`;
  if (reported === state.reported) return;
  state.reported = reported;
  api().progress(name, ratio);
}

function trackShare(track) {
  if (track.state === "download") return track.percent / 100;
  return track.state === "waiting" || track.state === "search" ? 0 : 1;
}

// The window has no status bar: the downloads say how they are going above
// their list, the library what it holds and what is going on in it above its
// own; problems have their strip in Download and the dot on Settings
function renderStatus() {
  if (!state.settings) return;
  $("#download-status").textContent = statusText();
  renderLibrarySummary();
}

function statusText() {
  const jobs = state.run ? [...state.run.jobs].map((id) => state.jobs.get(id)).filter(Boolean) : [];
  if (!jobs.length) return ""; // the empty list says so itself
  const tracks = jobs.flatMap((job) => [...job.tracks.values()]);
  const finished = tracks.filter((track) => !TRACK_ACTIVE.has(track.state));
  const failed = tracks.filter((track) => TRACK_FAILED.has(track.state)).length;
  const dryRun = jobs.every((job) => job.dryRun);
  const parts = [];

  if (jobs.some((job) => ACTIVE.has(job.state))) {
    const queued = jobs.filter((job) => job.state === "queued").length;
    if (tracks.length) {
      parts.push(t(dryRun ? "Проверено {done} из {total}" : "Загружено {done} из {total}",
        { done: finished.length, total: tracks.length }));
    }
    const eta = formatEta(smoothEta(state.run, remainingSeconds(tracks)));
    if (eta) parts.push(t("осталось {eta}", { eta }));
    if (failed) parts.push(t(dryRun ? "не найдено: {failed}" : "не удалось: {failed}", { failed }));
    if (jobs.some((job) => job.state === "running" && !job.total)) parts.push(t("читаем ссылку…"));
    if (queued) {
      parts.push(t("ещё {queued} {linkWord} в очереди",
        { queued, linkWord: plural(queued, "ссылка", "ссылки", "ссылок") }));
    }
    const text = parts.join(" · ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  const count = (...names) => tracks.filter((track) => names.includes(track.state)).length;
  const errors = jobs.filter((job) => job.state === "error").length;
  if (jobs.some((job) => job.state === "cancelled")) parts.push(t("Остановлено"));
  if (dryRun && tracks.length) {
    parts.push(t("найдено {found} из {total}", { found: count("found"), total: tracks.length }));
  } else if (tracks.length) {
    const ok = count("done");
    const skipped = count("skip");
    parts.push(t("скачано {ok} {trackWord}",
      { ok, trackWord: plural(ok, "трек", "трека", "треков") }));
    if (skipped) parts.push(t("уже были: {skipped}", { skipped }));
    if (failed) parts.push(t("не удалось: {failed}", { failed }));
  }
  if (errors) {
    parts.push(t("{links} с ошибкой: {errors}",
      { links: plural(errors, "ссылка", "ссылки", "ссылок"), errors }));
  }
  const text = parts.join(" · ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// Remaining time from the pace so far; skipped files take no time and would inflate it
/* Library */

// Motion follows Material's emphasized curve: things leave fast and land softly.
const EMPHASIZED = "cubic-bezier(0.2, 0, 0, 1)";
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function librarySorts(tab = state.library.tab) {
  return { albums: LIBRARY_SORTS, tracks: TRACK_SORTS, artists: ARTIST_SORTS, playlists: PLAYLIST_SORTS }[tab];
}

function currentSort(tab = state.library.tab) {
  return state.library.sorts[tab];
}

// The bar above the list: what is going on in the library when something is,
// else what it holds. A folder being read or holding nothing says so in the
// middle of the view instead.
function renderLibrarySummary() {
  $("#library-summary").textContent = libraryNote() || librarySummary();
}

function libraryNote() {
  const { items, selected, shown } = state.library;
  if (state.tidy.running) {
    const { done, total, title } = state.tidy;
    return t("Дописываем теги: {done} из {total} · {title}", { done: Math.min(done + 1, total), total, title });
  }
  if (state.tidy.note && !selected.size) return state.tidy.note;
  if (selected.size) {
    const size = items.filter((item) => selected.has(item.path)).reduce((total, item) => total + item.size, 0);
    return t("Выбрано: {count} · {size}", { count: selected.size, size: formatSize(size) });
  }
  if (state.library.tab === "albums" && items.length && shown.length < items.length) {
    return t("Найдено: {shown} из {total}", { shown: shown.length, total: items.length });
  }
  return "";
}

function librarySummary() {
  const { items, tab } = state.library;
  if (!items.length) return "";
  if (tab === "playlists") {
    const count = state.playlists.items.filter((entry) => !entry.smart).length;
    return count ? t("{count} {playlistWord}", { count, playlistWord: plural(count, "плейлист", "плейлиста", "плейлистов") }) : "";
  }
  if (tab === "artists") {
    const count = libraryArtists().length;
    return t("{count} {artistWord}", { count, artistWord: plural(count, "исполнитель", "исполнителя", "исполнителей") });
  }
  if (tab === "tracks" && state.library.trackList.items.length) {
    const list = state.library.trackList.items;
    const seconds = list.reduce((total, track) => total + track.duration, 0);
    return [t("{tracks} {trackWord}", { tracks: list.length, trackWord: plural(list.length, "трек", "трека", "треков") }),
      formatLength(seconds)].join(" · ");
  }
  const albums = items.filter((item) => item.album).length;
  const tracks = items.reduce((total, item) => total + item.tracks, 0);
  const size = items.reduce((total, item) => total + item.size, 0);
  const parts = albums ? [t("{albums} {albumWord}",
    { albums, albumWord: plural(albums, "альбом", "альбома", "альбомов") })] : [];
  parts.push(t("{tracks} {trackWord}",
    { tracks, trackWord: plural(tracks, "трек", "трека", "треков") }), formatSize(size));
  return parts.join(" · ");
}

// The music folder and the other folders the library shows
function libraryFolders() {
  return [state.settings.folder, ...(state.settings.library_folders || [])];
}

async function loadLibrary() {
  const library = state.library;
  const folders = libraryFolders();
  const folder = folders.join("\n"); // what was read, to tell when it changes
  const token = ++library.token;
  if (library.folder !== folder) library.items = [];
  library.loading = true;
  library.trackList.stale = true;
  renderLibrary();
  // What the folders held last time shows at once, and is replaced when read again
  let early = false;
  if (!library.items.length) {
    const cached = await Promise.resolve().then(() => api().library_cached(folders)).catch(() => []);
    if (token !== library.token) return;
    if (cached?.length) {
      Object.assign(library, { items: cached, folder });
      library.artists = null;
      renderLibrary({ enter: true });
      prefetchCovers(cached);
      early = true;
    }
  }
  let items;
  try {
    items = await api().library(folders);
  } catch (error) {
    console.error(error);
    items = [];
  }
  if (token !== library.token) return;
  Object.assign(library, { items, folder, stale: false, loading: false });
  library.artists = null;
  renderLibrary({ enter: !early });
  markInLibrary();
  prefetchCovers(items);
  if (library.tab === "tracks") loadTracks();
  refreshGaps();
}

// Which entries have tags, a cover or lyrics missing. Every file is read for
// it, so it comes after the library is drawn, and the buttons that offer to
// fill them in show up once it is known.
async function refreshGaps() {
  const tidy = state.tidy;
  const token = ++tidy.gapsToken;
  let found = [];
  try {
    found = await api().tag_gaps(state.library.items.map((item) => item.path), state.settings);
  } catch (error) {
    console.error(error);
  }
  if (token !== tidy.gapsToken) return;
  tidy.gaps = new Set(found);
  renderTidy();
}

function hasGaps(item) {
  return state.tidy.gaps.has(item.path);
}

// Every file's tags are read for this tab, so it is asked for only when opened
async function loadTracks() {
  const list = state.library.trackList;
  const folders = libraryFolders();
  const folder = folders.join("\n");
  if (!list.stale && list.folder === folder) return;
  const token = ++list.token;
  list.loading = true;
  renderLibrary();
  let items;
  try {
    const [tracks, counts] = await Promise.all([api().tracks(folders), api().play_counts().catch(() => ({}))]);
    items = tracks;
    state.plays = new Map(Object.entries(counts || {}));
  } catch (error) {
    console.error(error);
    items = [];
  }
  if (token !== list.token) return;
  Object.assign(list, { items, folder, stale: false, loading: false });
  renderLibrary({ enter: true });
}

function renderLibrary({ enter = false } = {}) {
  const library = state.library;
  const { tab } = library;
  const grid = tab === "albums" && library.view === "grid";
  $("#library").hidden = !(tab === "albums" && !grid);
  $("#library-head").hidden = !(tab === "albums" && !grid);
  $("#library-cards").hidden = !grid;
  $("#library-tracks").hidden = tab !== "tracks";
  $("#library-artists").hidden = tab !== "artists";
  $("#library-playlists").hidden = tab !== "playlists";
  $("#view-library").dataset.tab = tab;
  $("#playlist-new").hidden = tab !== "playlists";
  $("#playlist-import").hidden = tab !== "playlists";
  $("#library-view").hidden = tab !== "albums";
  syncRadios($("#library-view"), "data-library-view", library.view);
  renderSortPickers();
  renderGenrePicker();
  if (tab === "playlists") {
    $("#library-shuffle").hidden = true;
    $("#library-genre").closest(".pick").hidden = true;
  }
  const shownCount = tab === "albums" ? renderAlbums(grid, enter)
    : tab === "tracks" ? renderTracks(enter) : tab === "playlists" ? renderPlaylists(enter) : renderArtists(enter);
  renderSelection(); // and with it the bar above the list

  const empty = $("#library-empty");
  empty.hidden = shownCount > 0;
  const query = $("#library-filter").value.trim();
  let [title, text] = [t("Ничего не найдено"), query ? t("По запросу «{query}»", { query })
    : t("В жанре «{genre}»", { genre: $("#library-genre").selectedOptions[0]?.dataset.name || "" })];
  const loading = library.loading || (tab === "tracks" && library.trackList.loading);
  // The aardvark by an empty crate for an empty folder, among dug-up holes for a
  // search that turned nothing up, and nowhere while the folder is still being read
  let scene = "art/nothing-found.webp";
  if (tab === "playlists") {
    if (!query) {
      const reading = state.playlists.loading && !state.playlists.items.length;
      [title, text] = reading ? [t("Читаем плейлисты…"), ""] : [t("Плейлистов пока нет"),
        t("Создайте свой или добавьте треки правым щелчком: «Добавить в плейлист». Плейлист .m3u8 из другого плеера тоже подойдёт")];
      scene = reading ? "" : "art/empty-library.webp";
    }
  } else if (!library.items.length || (tab === "tracks" && !library.trackList.items.length)) {
    [title, text] = loading ? [t("Читаем папку…"), ""] : [t("В папке пока нет музыки"), libraryFolders().join(", ")];
    scene = loading ? "" : "art/empty-library.webp";
  }
  $(".empty-title", empty).textContent = title;
  $(".empty-text", empty).textContent = text;
  const picture = $(".scene", empty);
  picture.hidden = !scene;
  if (scene && picture.getAttribute("src") !== scene) picture.setAttribute("src", scene);
}

function libraryQuery() {
  return $("#library-filter").value.trim().toLocaleLowerCase();
}

// The genres a file names: "Alternative Rock; Rock" is two, the way this
// program and most taggers join several
function genresOf(text) {
  return String(text || "").split(/[;\0]/).map((genre) => genre.trim()).filter(Boolean);
}

// Whether a tag names the genre the library is narrowed to; any, when it is not
function inGenre(text) {
  const wanted = state.library.genre;
  return !wanted || genresOf(text).some((genre) => genre.toLocaleLowerCase() === wanted);
}

// Every genre the albums and tracks name, each once, with how many name it
function libraryGenres() {
  const found = new Map();
  for (const item of state.library.items) {
    for (const genre of genresOf(item.genre)) {
      const key = genre.toLocaleLowerCase();
      if (!found.has(key)) found.set(key, { key, name: genre, count: 0 });
      found.get(key).count += 1;
    }
  }
  return [...found.values()].sort((a, b) => COLLATOR.compare(a.name, b.name));
}

function renderGenrePicker() {
  const library = state.library;
  const pick = $("#library-genre");
  const shuffle = $("#library-shuffle");
  const genres = libraryGenres();
  // A genre gone from the folders after it was read again lets the library go back to all
  if (library.genre && !genres.some((genre) => genre.key === library.genre)) library.genre = "";
  pick.closest(".pick").hidden = !genres.length;
  pick.replaceChildren(new Option(t("все"), ""), ...genres.map((genre) => {
    const option = new Option(`${genre.name} · ${genre.count}`, genre.key);
    option.dataset.name = genre.name;
    return option;
  }));
  pick.value = library.genre;
  const genre = pick.selectedOptions[0]?.dataset.name;
  shuffle.title = genre ? t("Слушать «{genre}» вперемешку", { genre }) : t("Слушать всю библиотеку вперемешку");
  shuffle.setAttribute("aria-label", shuffle.title);
  shuffle.hidden = !library.items.length;
}

function sortBy(list, sorts, { key, dir }, tieBreak) {
  const { value } = sorts[key];
  return [...list].sort((a, b) => dir * compare(value(a), value(b)) || tieBreak(a, b));
}

function compare(a, b) {
  return typeof a === "number" ? a - b : COLLATOR.compare(a, b);
}

function renderAlbums(grid, enter) {
  const library = state.library;
  const needle = libraryQuery();
  const found = library.items.filter((item) => inGenre(item.genre)
    && (!needle || `${item.artist} ${item.title}`.toLocaleLowerCase().includes(needle)));
  const shown = sortBy(found, LIBRARY_SORTS, library.sorts.albums, (a, b) => COLLATOR.compare(a.title, b.title));
  library.shown = shown.map((item) => item.path);
  // Something the filter hides must not stay selected: a batch delete would
  // then take away something nobody can see.
  const visible = new Set(library.shown);
  for (const path of library.selected) if (!visible.has(path)) library.selected.delete(path);
  if (grid) {
    const cards = $("#library-cards");
    cards.replaceChildren(...shown.map((item, index) => createCard(item, index)));
    cards.querySelector(".card")?.setAttribute("tabindex", "0");
    playEntrance(cards, enter);
    $("#library").replaceChildren();
  } else {
    $("#library").replaceChildren(...shown.map(createLibraryRow));
    $$("#library .library-row").forEach((row, index) => { row.tabIndex = index ? -1 : 0; });
    $("#library-cards").replaceChildren();
    renderSortHeader();
  }
  return shown.length;
}

function renderTracks(enter) {
  const list = state.library.trackList;
  const needle = libraryQuery();
  const found = list.items.filter((track) => inGenre(track.genre)
    && (!needle || `${track.title} ${track.artists} ${track.album}`.toLocaleLowerCase().includes(needle)));
  // Tracks that tie (one album's tracks share a date) keep the album's own order
  const albumOrder = TRACK_SORTS.album.value;
  const shown = sortBy(found, TRACK_SORTS, state.library.sorts.tracks, (a, b) => compare(albumOrder(a), albumOrder(b)));
  const rows = shown.map(createLibraryTrack);
  $("#library-tracks").replaceChildren(...rows);
  markPlayingRows();
  if (rows[0]) rows[0].tabIndex = 0;
  if (enter && rows.length && !reduceMotion()) {
    $("#library-tracks").animate([{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1, transform: "none" }],
                                 { duration: 300, easing: EMPHASIZED });
  }
  return shown.length;
}

// Artists are what the folder names say: every album and single track by the same name
function libraryArtists() {
  const library = state.library;
  if (library.artists) return library.artists;
  const byName = new Map();
  for (const item of library.items) {
    const name = item.artist || t("Без исполнителя");
    const key = name.toLocaleLowerCase();
    if (!byName.has(key)) byName.set(key, { name, key, items: [], albums: 0, singles: 0, modified: 0 });
    const artist = byName.get(key);
    artist.items.push(item);
    if (item.album) artist.albums += 1;
    else artist.singles += 1;
    artist.modified = Math.max(artist.modified, item.modified);
  }
  library.artists = [...byName.values()];
  return library.artists;
}

function renderArtists(enter) {
  const needle = libraryQuery();
  const found = libraryArtists().filter((artist) => (!needle || artist.key.includes(needle))
    && artist.items.some((item) => inGenre(item.genre)));
  const shown = sortBy(found, ARTIST_SORTS, state.library.sorts.artists, (a, b) => COLLATOR.compare(a.name, b.name));
  const box = $("#library-artists");
  box.replaceChildren(...shown.map((artist, index) => createArtistCard(artist, index)));
  box.querySelector(".card")?.setAttribute("tabindex", "0");
  playEntrance(box, enter);
  return shown.length;
}

function artistCounts(artist) {
  const parts = [];
  if (artist.albums) {
    parts.push(t("{count} {albumWord}", { count: artist.albums, albumWord: plural(artist.albums, "альбом", "альбома", "альбомов") }));
  }
  if (artist.singles) {
    parts.push(t("{count} {trackWord}", { count: artist.singles, trackWord: plural(artist.singles, "трек", "трека", "треков") }));
  }
  return parts.join(", ");
}

// Cards rise into place one after another, the first screenful only: a delay
// for card 300 would be a wait, not a flourish
function playEntrance(container, enter) {
  container.classList.remove("entering");
  if (!enter || reduceMotion()) return;
  void container.offsetWidth; // restart the animation on a container that played it before
  container.classList.add("entering");
  setTimeout(() => container.classList.remove("entering"), 900);
}

function renderSortPickers() {
  const sorts = librarySorts();
  const { key, dir } = currentSort();
  const sortPick = $("#library-sort");
  sortPick.replaceChildren(...Object.entries(sorts).map(([name, sort]) => new Option(t(sort.label), name)));
  sortPick.value = key;
  const labels = ORDER_LABELS[sorts[key].kind];
  const orderPick = $("#library-order");
  orderPick.replaceChildren(new Option(t(labels[sorts[key].dir]), String(sorts[key].dir)),
                            new Option(t(labels[-sorts[key].dir]), String(-sorts[key].dir)));
  orderPick.value = String(dir);
}

function sortLibraryBy(key) {
  const sort = state.library.sorts.albums;
  sort.dir = sort.key === key ? -sort.dir : LIBRARY_SORTS[key].dir;
  sort.key = key;
  renderLibrary();
}

function renderSortHeader() {
  const { key, dir } = state.library.sorts.albums;
  for (const button of $$("#view-library .sort")) {
    const active = button.dataset.sort === key;
    const order = dir > 0 ? "ascending" : "descending";
    button.closest("[role=columnheader]").setAttribute("aria-sort", active ? order : "none");
  }
}

function onSortPick() {
  const sort = currentSort();
  const key = $("#library-sort").value;
  if (key !== sort.key) Object.assign(sort, { key, dir: librarySorts()[key].dir });
  else sort.dir = Number($("#library-order").value);
  renderLibrary();
}

/* Tabs, views and the pages drawn over them */

function placeTabInk(animate = true) {
  const tabs = $("#library-tabs");
  const chosen = $("[aria-selected=true]", tabs);
  if (!chosen || !chosen.offsetWidth) return;
  tabs.classList.toggle("ink-ready", animate && !reduceMotion());
  tabs.style.setProperty("--ink-x", `${chosen.offsetLeft + 8}px`);
  tabs.style.setProperty("--ink-w", `${chosen.offsetWidth - 16}px`);
}

// Material's "fade through": what leaves fades out quickly, what arrives fades
// in while growing from just under its size
function fadeThrough(change) {
  const panel = $("#library-panel");
  if (reduceMotion()) {
    change();
    return;
  }
  // The swap waits on a timer, not on the animation: a window in the
  // background may hold animations still, and the tab must change regardless
  const out = panel.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 90, easing: "ease-in", fill: "forwards" });
  setTimeout(() => {
    change();
    out.cancel();
    panel.animate([{ opacity: 0, transform: "scale(0.985)" }, { opacity: 1, transform: "none" }],
                  { duration: 240, easing: EMPHASIZED });
  }, 90);
}

function showLibraryTab(tab) {
  const library = state.library;
  if (tab === library.tab && $("#library-page").hidden) return;
  closePage({ instant: true });
  library.scroll[library.tab] = $("#library-scroll").scrollTop;
  for (const button of $$("#library-tabs [role=tab]")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  }
  placeTabInk();
  clearSelection();
  fadeThrough(() => {
    library.tab = tab;
    renderLibrary({ enter: true });
    $("#library-scroll").scrollTop = library.scroll[tab] || 0;
    if (tab === "tracks") loadTracks();
    if (tab === "playlists") loadPlaylists();
    renderStatus();
  });
}

function setLibraryView(view) {
  if (view === state.library.view) return;
  fadeThrough(() => {
    state.library.view = view;
    renderLibrary({ enter: true });
  });
  updateSettings({ library_view: view });
}

// An animation's end, or its planned end, whichever comes first: a window in
// the background may hold animations still, and nothing may wait on them forever
function settled(animation, duration) {
  return Promise.race([animation.finished.catch(() => {}), new Promise((resolve) => setTimeout(resolve, duration + 80))]);
}

function rectOf(element) {
  const box = element?.getBoundingClientRect();
  return box && box.width ? box : null;
}

// The cover the person clicked grows into the page's cover (and shrinks back
// on the way out): one picture moving, which says where the page came from
function morph(element, from, to, { duration = 440 } = {}) {
  if (!from || !to || reduceMotion()) return Promise.resolve();
  const dx = from.left - to.left;
  const dy = from.top - to.top;
  const sx = from.width / to.width;
  const sy = from.height / to.height;
  const moved = `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`;
  return settled(element.animate([{ transformOrigin: "0 0", transform: moved }, { transformOrigin: "0 0", transform: "none" }],
                                 { duration, easing: EMPHASIZED }), duration);
}

function morphBack(element, to, { duration = 360 } = {}) {
  const from = rectOf(element);
  if (!from || !to || reduceMotion()) return Promise.resolve();
  const moved = `translate(${to.left - from.left}px, ${to.top - from.top}px) scale(${to.width / from.width}, ${to.height / from.height})`;
  return settled(element.animate([{ transformOrigin: "0 0", transform: "none" }, { transformOrigin: "0 0", transform: moved }],
                                 { duration, easing: EMPHASIZED, fill: "forwards" }), duration);
}

// Everything on the page but the moving picture rises in a little after it
function riseIn(elements, delay = 90) {
  if (reduceMotion()) return;
  elements.forEach((element, index) => element?.animate(
    [{ opacity: 0, transform: "translateY(16px)" }, { opacity: 1, transform: "none" }],
    { duration: 320, delay: delay + index * 45, easing: EMPHASIZED, fill: "backwards" }));
}

/* The band at the top of a page takes the colour of the cover, as in Harmonoid */

// The hue that most of the picture's coloured pixels share, and how strong it
// is. White, black and grey carry no hue and are left out, so a red title on
// a white sleeve gives red, and a black-and-white photo gives a grey band.
async function pictureTint(src) {
  if (!state.tints.has(src)) {
    state.tints.set(src, (async () => {
      const image = new Image();
      image.crossOrigin = "anonymous"; // Deezer's photos allow it, and so do the program's covers
      image.src = src;
      await image.decode();
      const size = 48;
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      context.drawImage(image, 0, 0, size, size);
      const { data } = context.getImageData(0, 0, size, size);
      const buckets = Array.from({ length: 36 }, () => ({ weight: 0, x: 0, y: 0, saturation: 0 }));
      for (let index = 0; index < data.length; index += 4) {
        const [hue, saturation, lightness] = toHsl(data[index], data[index + 1], data[index + 2]);
        if (saturation < 0.18 || lightness < 0.1 || lightness > 0.94) continue;
        // Vivid mid-tones speak for the picture more than washed-out corners do
        const weight = saturation * (1 - Math.abs(lightness - 0.5));
        const bucket = buckets[Math.floor(hue / 10) % 36];
        bucket.weight += weight;
        bucket.x += Math.cos((hue * Math.PI) / 180) * weight;
        bucket.y += Math.sin((hue * Math.PI) / 180) * weight;
        bucket.saturation += saturation * weight;
      }
      // Neighbouring buckets are one colour split by the grid, so they count together
      let best = null;
      buckets.forEach((bucket, index) => {
        const joined = bucket.weight + 0.5 * (buckets[(index + 35) % 36].weight + buckets[(index + 1) % 36].weight);
        if (!best || joined > best.joined) best = { joined, bucket };
      });
      const pixels = size * size;
      if (!best || best.bucket.weight < pixels * 0.015) return { hue: 0, saturation: 0 };
      const { bucket } = best;
      const hue = ((Math.atan2(bucket.y, bucket.x) * 180) / Math.PI + 360) % 360;
      return { hue: Math.round(hue), saturation: Math.round((bucket.saturation / bucket.weight) * 100) };
    })().catch(() => null));
  }
  return state.tints.get(src);
}

function toHsl(red, green, blue) {
  const r = red / 255;
  const g = green / 255;
  const b = blue / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const lightness = (max + min) / 2;
  if (max === min) return [0, 0, lightness];
  const delta = max - min;
  const saturation = delta / (1 - Math.abs(2 * lightness - 1));
  let hue = max === r ? ((g - b) / delta) % 6 : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  hue = (hue * 60 + 360) % 360;
  return [hue, saturation, lightness];
}

// The band fades from what it showed to the new colour; null lets it fade away
async function tintPage(src, owner) {
  const page = $("#library-page");
  const tint = src ? await pictureTint(src) : null;
  if (owner !== state.library.pageToken) return; // another page has been opened since
  fadeBand(page);
  page.style.setProperty("--tint-a", tint ? "1" : "0");
  if (!tint) return;
  page.style.setProperty("--tint-h", tint.hue);
  page.style.setProperty("--tint-s", `${tint.saturation}%`);
}

function fadeBand(page) {
  page.classList.add("tinting");
  clearTimeout(page.tintTimer);
  page.tintTimer = setTimeout(() => page.classList.remove("tinting"), 600);
}

function albumFillsWindow() {
  const pages = state.library.pages;
  return state.view === "library" && ["album", "playlist"].includes(pages[pages.length - 1]?.kind) && !$("#library-page").hidden;
}

function showPage(kind) {
  const page = $("#library-page");
  $("#view-library").classList.add("page-open");
  document.documentElement.classList.toggle("album-full", kind !== "artist");
  page.classList.toggle("artist-open", kind === "artist");
  $("#album-page").hidden = kind === "artist";
  $("#artist-page").hidden = kind !== "artist";
  page.hidden = false;
  page.scrollTop = 0;
  $("#library-panel").inert = true;
  if (!reduceMotion()) {
    page.animate([{ backgroundColor: "transparent" }, { backgroundColor: getComputedStyle(page).backgroundColor }],
                 { duration: 260, easing: EMPHASIZED });
  }
}

// The arrow says where it goes back to in its tooltip, since it shows no words
function renderPageBack() {
  const back = $("#page-back");
  $("#page-back-label").textContent = backLabel();
  back.title = t("Назад: {where}", { where: backLabel() });
  back.setAttribute("aria-label", back.title);
}

function backLabel() {
  const stack = state.library.pages;
  const below = stack[stack.length - 2];
  if (below) return below.kind === "artist" ? below.artist.name : below.item.title;
  return t({ albums: "Альбомы", tracks: "Треки", artists: "Исполнители", playlists: "Плейлисты" }[state.library.tab]);
}

async function openAlbum(item, source = null, highlight = "") {
  const library = state.library;
  const from = rectOf(source && $(".cover", source));
  library.pages.push({ kind: "album", item, source, from });
  const token = ++library.pageToken;
  fillAlbumPage(item);
  renderPageBack();
  showPage("album");
  const cover = $(".album-cover", $("#album-page"));
  const info = $(".album-info", $("#album-page"));
  riseIn([$("#page-back"), ...info.children]);
  // The cover appears where it stands. It used to fly there from the card it
  // was opened from, but the page fills the window: the rail and the bars step
  // aside as it opens, and the cover ended up crossing to the rail's own place.
  if (!reduceMotion()) {
    cover.animate([{ opacity: 0, transform: "scale(0.92)" }, { opacity: 1, transform: "none" }], { duration: 300, easing: EMPHASIZED });
  }
  $("#page-back").focus({ preventScroll: true });

  let data;
  try {
    data = await api().album(item.path);
  } catch (error) {
    console.error(error);
    data = { tracks: [], missing: [], expected: 0, link: item.link, service: "" };
  }
  if (token !== library.pageToken) return;
  renderAlbumTracks(item, data, highlight);
}

function fillAlbumPage(item) {
  const page = $("#album-page");
  page.dataset.kind = "album";
  page.playlist = null;
  page.tracks = [];
  $(".album-head .artists", page).textContent = t("Исполнители");
  $("use", $(".album-cover", page)).setAttribute("href", "#i-note");
  $(".album-title", page).textContent = item.title;
  page.dataset.path = item.path;
  $(".album-sub", page).textContent = albumSubline(item);
  const image = $(".album-cover img", page);
  image.hidden = true;
  image.removeAttribute("src");
  const owner = state.library.pageToken;
  if (item.cover) {
    if (!state.covers.has(item.path)) state.covers.set(item.path, api().cover(item.path));
    state.covers.get(item.path).then((src) => {
      if (src && page.dataset.path === item.path) {
        loadCover($(".album-cover", page), src);
        showBackdrop(page, src);
      }
      tintPage(src, owner);
    });
  } else {
    showBackdrop(page, "");
    tintPage(null, owner);
  }
  // The count in the marker says at once whether tracks are missing; the
  // tracklist confirms it once the page is read
  $("[data-page-action=again]", page).hidden = !hasMissing(item);
  const watch = $("[data-page-action=watch]", page);
  watch.hidden = !item.link || !item.album;
  renderWatchButton(item.link);
  renderTidy(); // a page opened while the library is tidied says whether it is among them
  $(".album-list", page).replaceChildren();
}

function albumSubline(item, format = "", genre = "") {
  const parts = [item.artist, genre, item.year,
    t("{tracks} {trackWord}", { tracks: item.tracks, trackWord: plural(item.tracks, "трек", "трека", "треков") }),
    formatSize(item.size), format];
  return parts.filter(Boolean).join(" · ");
}

function renderWatchButton(link) {
  const button = $("[data-page-action=watch]", $("#album-page"));
  const watching = Boolean(link) && isWatched(link);
  button.setAttribute("aria-pressed", String(watching));
  $("span", button).textContent = t(watching ? "Следим" : "Следить");
}

function renderAlbumTracks(item, data, highlight) {
  const page = $("#album-page");
  const formats = data.tracks.map((track) => track.format);
  const format = formats.sort((a, b) => formats.filter((f) => f === b).length - formats.filter((f) => f === a).length)[0] || "";
  // "Alternative Rock; Art Rock" in the tags reads better with commas on the page
  const genre = (data.tracks.map((track) => track.genre).find(Boolean) || "").replace(/\s*;\s*/g, ", ");
  $(".album-sub", page).textContent = albumSubline(item, format, genre);
  const rows = [];
  const all = [...data.tracks.map((track) => ({ ...track, missing: false })),
    ...data.missing.map((track) => ({ ...track, missing: true }))];
  all.sort((a, b) => (a.disc || 1) - (b.disc || 1) || (a.number || 999) - (b.number || 999));
  const discs = new Set(all.map((track) => track.disc || 1)).size;
  let disc = 0;
  for (const track of all) {
    if (discs > 1 && (track.disc || 1) !== disc) {
      disc = track.disc || 1;
      const head = document.createElement("div");
      head.className = "disc-head";
      head.textContent = t("Диск {number}", { number: disc });
      rows.push(head);
    }
    rows.push(createAlbumTrack(track, item));
  }
  const unknown = Math.max(0, data.expected - data.tracks.length - data.missing.length);
  $("[data-page-action=again]", page).hidden = !item.link || !(data.missing.length || unknown);
  if (unknown) {
    const note = document.createElement("div");
    note.className = "album-note";
    note.textContent = t("Ещё {count} {trackWord} нет в папке",
                         { count: unknown, trackWord: plural(unknown, "трек", "трека", "треков") });
    rows.push(note);
  }
  page.tracks = data.tracks.map((track) => ({ ...track, album: track.album || item.title,
                                               album_artist: track.album_artist || item.artist }));
  const list = $(".album-list", page);
  list.replaceChildren(...rows);
  markPlayingRows();
  if (!reduceMotion()) {
    list.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }], { duration: 260, easing: EMPHASIZED });
  }
  const target = highlight && [...$$(".album-track", list)].find((row) => row.dataset.path === highlight);
  if (target) {
    target.scrollIntoView({ block: "center" });
    target.classList.add("highlight");
  }
}

function createAlbumTrack(track, item) {
  const row = document.createElement("div");
  row.className = `album-track${track.missing ? " missing" : ""}`;
  row.setAttribute("role", "row");
  if (track.path) row.dataset.path = track.path;
  const number = document.createElement("span");
  number.className = "n";
  number.textContent = track.number || "";
  const title = document.createElement("span");
  title.textContent = track.title;
  if (track.missing) {
    const gone = document.createElement("span");
    gone.className = "gone";
    gone.textContent = ` · ${t("нет в папке")}`;
    title.append(gone);
  }
  const artists = document.createElement("span");
  artists.className = "artists";
  artists.textContent = track.artists || item.artist;
  const time = document.createElement("span");
  time.className = "num";
  time.textContent = track.duration ? formatDuration(track.duration) : "";
  row.append(number, title, artists, time);
  return row;
}

function openArtist(artist, source = null) {
  const library = state.library;
  const from = rectOf(source && $(".artist-photo", source));
  library.pages.push({ kind: "artist", artist, source, from });
  const owner = ++library.pageToken;
  if (!state.artistPhotos.has(artist.name)) {
    state.artistPhotos.set(artist.name, api().artist_picture(artist.name).catch(() => ""));
  }
  state.artistPhotos.get(artist.name).then((url) => tintPage(url || null, owner));
  const page = $("#artist-page");
  $(".artist-name", page).textContent = artist.name;
  $(".artist-sub", page).textContent = artistCounts(artist);
  fillArtistPhoto($(".artist-photo", page), artist.name);
  const items = sortBy(artist.items, LIBRARY_SORTS, { key: "year", dir: -1 }, (a, b) => COLLATOR.compare(a.title, b.title));
  const cards = $(".artist-albums", page);
  cards.replaceChildren(...items.map((item, index) => createCard(item, index)));
  renderPageBack();
  showPage("artist");
  const photo = $(".artist-photo", page);
  if (source) $(".artist-photo", source).style.visibility = "hidden";
  riseIn([$("#page-back"), $(".artist-head > div:last-child", page)]);
  playEntrance(cards, true);
  morph(photo, from, rectOf(photo)).then(() => { if (source) $(".artist-photo", source).style.visibility = ""; });
  $("#page-back").focus({ preventScroll: true });
}

// Back to where the page was opened from; the picture flies home to its card
async function closePage({ instant = false } = {}) {
  const library = state.library;
  const page = $("#library-page");
  if (page.hidden) return;
  const top = library.pages.pop();
  library.pageToken += 1;
  const below = library.pages[library.pages.length - 1];
  const fly = top.kind !== "artist" ? $(".album-cover", $("#album-page")) : $(".artist-photo", $("#artist-page"));
  const sourcePicture = top.source && $(top.kind !== "artist" ? ".cover" : ".artist-photo", top.source);
  // An artist's photo flies back to its card, so the rail has to be back
  // before it goes. An album's page fades out whole and gives the window back
  // afterwards (finishClose), so the layout is not rebuilt mid-animation.
  const flies = top.kind === "artist";
  if (flies || below) document.documentElement.classList.remove("album-full");
  if (below) {
    // From an album back to the artist whose page it was opened from
    $("#album-page").hidden = true;
    $("#artist-page").hidden = false;
    page.classList.add("artist-open");
    state.artistPhotos.get(below.artist.name)?.then((url) => tintPage(url || null, library.pageToken));
    renderPageBack();
    page.scrollTop = 0;
    if (!instant && sourcePicture && !reduceMotion()) {
      riseIn([$(".artist-head", $("#artist-page"))], 0);
    }
    sourcePicture?.focus?.();
    top.source?.focus();
    return;
  }
  library.pages = [];
  if (instant || reduceMotion() || !top.source || !top.source.isConnected) {
    finishClose(page);
    if (!instant && !reduceMotion()) {
      $("#library-panel").animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, easing: EMPHASIZED });
    }
    top.source?.focus();
    return;
  }
  const to = flies ? rectOf(sourcePicture) : null;
  if (flies && sourcePicture) sourcePicture.style.visibility = "hidden";
  const others = [...page.children].filter((child) => !child.hidden);
  fadeBand(page);
  page.style.setProperty("--tint-a", "0");
  if (flies) {
    for (const element of [$("#page-back"), ...$$(".artist-head > div:last-child, .artist-albums", page)]) {
      element.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 140, easing: "ease-in", fill: "forwards" });
    }
    const background = page.animate([{ backgroundColor: getComputedStyle(page).backgroundColor }, { backgroundColor: "transparent" }],
                                    { duration: 320, easing: EMPHASIZED, fill: "forwards" });
    await Promise.all([morphBack(fly, to), settled(background, 320)]);
  } else {
    // The album's page goes as one picture, cover and all: the cover used to
    // stay behind, lit, while everything else faded from under it
    await settled(page.animate([{ opacity: 1 }, { opacity: 0 }],
                               { duration: 200, easing: "ease-in", fill: "forwards" }), 200);
  }
  finishClose(page);
  for (const element of others) element.getAnimations({ subtree: true }).forEach((animation) => animation.cancel());
  if (flies && sourcePicture) sourcePicture.style.visibility = "";
  top.source.focus({ preventScroll: true });
}

function finishClose(page) {
  page.style.setProperty("--tint-a", "0");
  page.getAnimations().forEach((animation) => animation.cancel());
  for (const element of $$("*", page)) element.getAnimations().forEach((animation) => animation.cancel());
  page.hidden = true;
  $("#library-panel").inert = false;
  $("#view-library").classList.remove("page-open");
  document.documentElement.classList.remove("album-full");
}

/* Cards: a click opens, Ctrl and Shift pick, the right button offers the menu */

function createCard(item, index) {
  const card = $("#card-template").content.firstElementChild.cloneNode(true);
  card.dataset.path = item.path;
  card.style.setProperty("--i", Math.min(index, 24));
  $(".card-title", card).textContent = item.title;
  $(".card-title", card).title = item.title;
  const year = item.album ? item.year : t("трек");
  $(".card-sub", card).textContent = [item.artist, year].filter(Boolean).join(" · ");
  if (state.library.selected.has(item.path)) card.classList.add("is-selected");
  if (item.cover) coverObserver.observe(card);
  return card;
}

function itemByPath(path) {
  return state.library.items.find((item) => item.path === path);
}

function onCardsClick(event) {
  const card = event.target.closest(".card");
  if (!card) return;
  if (event.ctrlKey || event.shiftKey) {
    selectRow(card.dataset.path, event);
    return;
  }
  const item = itemByPath(card.dataset.path);
  if (item) openAlbum(item, card);
}

function onCardsKey(event) {
  const cards = [...event.currentTarget.querySelectorAll(".card")];
  const current = cards.indexOf(document.activeElement.closest(".card"));
  if (current === -1) return;
  const card = cards[current];
  if (event.key === "Enter") {
    event.preventDefault();
    card.click();
    return;
  }
  if (event.key === " " && card.dataset.path && event.currentTarget.id === "library-cards") {
    event.preventDefault();
    selectRow(card.dataset.path, { ctrlKey: true });
    return;
  }
  // Up and down jump a whole row of the grid, however many columns fit
  const columns = getComputedStyle(event.currentTarget).gridTemplateColumns.split(" ").length;
  const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns, ArrowUp: -columns, Home: "first", End: "last" }[event.key];
  if (step === undefined) return;
  event.preventDefault();
  let next = step === "first" ? 0 : step === "last" ? cards.length - 1 : current + step;
  next = Math.max(0, Math.min(cards.length - 1, next));
  for (const other of cards) other.tabIndex = other === cards[next] ? 0 : -1;
  cards[next].focus();
  cards[next].scrollIntoView({ block: "nearest" });
  if (event.shiftKey && cards[next].dataset.path) selectRow(cards[next].dataset.path, { shiftKey: true });
}

function onCardsContextMenu(event) {
  const card = event.target.closest(".card");
  if (!card) return;
  event.preventDefault();
  if (!state.library.selected.has(card.dataset.path)) selectRow(card.dataset.path, {});
  openLibraryMenu(event.clientX, event.clientY);
}

/* Tracks */

function createLibraryTrack(track) {
  const row = $("#library-track-template").content.firstElementChild.cloneNode(true);
  row.dataset.path = track.path;
  row.dataset.entry = track.entry;
  $(".title", row).textContent = track.title;
  $(".title", row).title = track.path;
  $(".cell-artists", row).textContent = track.artists;
  $(".cell-album", row).textContent = track.album;
  $(".cell-time", row).textContent = track.duration ? formatDuration(track.duration) : "";
  if (track.cover) {
    row.dataset.cover = track.entry;
    coverObserver.observe(row);
  }
  return row;
}

// A track in the Tracks tab plays from its cover; the list as shown is the queue
function onTrackCoverClick(event) {
  const cover = event.target.closest("#library-tracks .track-item .cover");
  if (!cover) return;
  event.stopPropagation();
  const rows = [...$$("#library-tracks .track-item")];
  playQueue(rows.map((row) => row.dataset.path), rows.indexOf(cover.closest(".track-item")), "tracks",
            trackArtists(state.library.trackList.items));
}

function openTrack(row) {
  const item = itemByPath(row.dataset.entry);
  if (item) openAlbum(item, null, row.dataset.path);
}

function onTracksKey(event) {
  const rows = [...$$("#library-tracks .track-item")];
  const current = rows.indexOf(document.activeElement.closest(".track-item"));
  if (current === -1) return;
  if (event.key === "Enter") {
    event.preventDefault();
    openTrack(rows[current]);
    return;
  }
  const step = { ArrowDown: 1, ArrowUp: -1, Home: "first", End: "last" }[event.key];
  if (!step) return;
  event.preventDefault();
  let next = step === "first" ? 0 : step === "last" ? rows.length - 1 : current + step;
  next = Math.max(0, Math.min(rows.length - 1, next));
  for (const other of rows) other.tabIndex = other === rows[next] ? 0 : -1;
  rows[next].focus();
}

/* Artists */

function createArtistCard(artist, index) {
  const card = $("#artist-template").content.firstElementChild.cloneNode(true);
  card.dataset.artist = artist.key;
  card.style.setProperty("--i", Math.min(index, 24));
  $(".card-title", card).textContent = artist.name;
  $(".card-sub", card).textContent = artistCounts(artist);
  fillArtistPhoto($(".artist-photo", card), artist.name, true);
  return card;
}

// Hues far enough apart that neighbouring artists do not look alike
const ARTIST_HUES = [12, 35, 150, 175, 200, 222, 252, 282, 318, 345];

// FNV-1a: the same name always gets the same colour, and similar names do not
function nameHash(text) {
  let hash = 0x811c9dc5;
  for (const char of text.toLocaleLowerCase()) hash = Math.imul(hash ^ char.codePointAt(0), 0x01000193) >>> 0;
  return hash;
}

// Initials on a colour of their own until (and unless) Deezer has a photo
function fillArtistPhoto(photo, name, lazy = false) {
  const words = name.split(/\s+/).filter(Boolean);
  $(".initials", photo).textContent = (words.length > 1 ? words[0][0] + words[1][0] : name.slice(0, 1)).toUpperCase();
  photo.style.setProperty("--hue", ARTIST_HUES[nameHash(name) % ARTIST_HUES.length]);
  photo.dataset.name = name;
  const image = $("img", photo);
  image.hidden = true;
  image.removeAttribute("src");
  if (lazy) artistObserver.observe(photo);
  else showArtistPhoto(photo);
}

async function showArtistPhoto(photo) {
  const { name } = photo.dataset;
  if (!state.artistPhotos.has(name)) state.artistPhotos.set(name, api().artist_picture(name).catch(() => ""));
  const url = await state.artistPhotos.get(name);
  if (!url || photo.dataset.name !== name) return;
  const image = $("img", photo);
  image.addEventListener("load", () => { image.hidden = false; }, { once: true });
  image.src = url;
}

function onArtistsClick(event) {
  const card = event.target.closest(".artist-card");
  if (!card) return;
  const artist = libraryArtists().find((entry) => entry.key === card.dataset.artist);
  if (artist) openArtist(artist, card);
}

/* Selection: click picks one row, Ctrl adds, Shift takes the range — as in Explorer */

function onLibraryClick(event) {
  const row = event.target.closest(".library-row");
  if (row) selectRow(row.dataset.path, event);
}

function selectRow(path, { ctrlKey = false, shiftKey = false } = {}) {
  const library = state.library;
  const { selected, shown, anchor } = library;
  if (shiftKey && anchor && shown.includes(anchor)) {
    const from = shown.indexOf(anchor);
    const to = shown.indexOf(path);
    const range = shown.slice(Math.min(from, to), Math.max(from, to) + 1);
    library.selected = new Set(ctrlKey ? [...selected, ...range] : range);
  } else if (ctrlKey) {
    if (selected.has(path)) selected.delete(path);
    else selected.add(path);
    library.anchor = path;
  } else {
    library.selected = new Set([path]);
    library.anchor = path;
  }
  renderSelection();
}

// Arrows walk the list the way they walk a folder in Explorer: the focused row
// is the selected one, Ctrl moves focus alone, Shift drags the selection along.
function onLibraryKey(event) {
  const step = { ArrowDown: 1, ArrowUp: -1, Home: "first", End: "last" }[event.key];
  if (!step) return;
  const rows = [...$$("#library .library-row")];
  if (!rows.length) return;
  const current = rows.indexOf(document.activeElement.closest(".library-row"));
  let next = step === "first" ? 0 : step === "last" ? rows.length - 1 : current + step;
  next = Math.max(0, Math.min(rows.length - 1, next));
  if (next === current && current !== -1) return;
  event.preventDefault();
  const row = rows[next === -1 ? 0 : next];
  focusRow(row);
  if (!event.ctrlKey) selectRow(row.dataset.path, { shiftKey: event.shiftKey });
}

function focusRow(row) {
  for (const other of $$("#library .library-row")) other.tabIndex = other === row ? 0 : -1;
  row.focus();
}

function onLibraryContextMenu(event) {
  const row = event.target.closest(".library-row");
  if (!row) return;
  event.preventDefault();
  if (!state.library.selected.has(row.dataset.path)) selectRow(row.dataset.path, {});
  openLibraryMenu(event.clientX, event.clientY);
}

function openLibraryMenu(x, y) {
  const menu = $("#library-menu");
  const items = selectedItems();
  $("[data-action=again]", menu).hidden = !items.some(hasMissing);
  $("[data-action=open]", menu).hidden = items.length !== 1;
  $("[data-action=tidy]", menu).hidden = state.tidy.running || !items.some(hasGaps);
  menu.hidden = false;
  // Placed after it is measurable, so a menu near the edge turns back inwards
  const box = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(x, window.innerWidth - box.width - 8)}px`;
  menu.style.top = `${Math.min(y, window.innerHeight - box.height - 8)}px`;
  $("button:not([hidden])", menu).focus();
}

function closeLibraryMenu(restoreFocus = true) {
  const menu = $("#library-menu");
  if (menu.hidden) return;
  menu.hidden = true;
  if (restoreFocus) $("#library .library-row[data-path], #library-cards .card[tabindex='0']")?.focus();
}

function onLibraryMenuClick(event) {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  closeLibraryMenu(false);
  const items = selectedItems();
  if (action === "again") downloadAgain(items.filter(hasMissing));
  else if (action === "open" && items.length === 1) api().open_folder(items[0].path);
  else if (action === "tidy") tidyLibrary(items.filter(hasGaps));
  else if (action === "delete") deleteSelected();
  else if (action === "next" || action === "queue") itemsTracks(items).then((tracks) => enqueue(tracks, action === "next"));
  else if (action === "playlist") {
    const { clientX: x, clientY: y } = event;
    itemsTracks(items).then((tracks) => openPlaylistMenu(x, y, tracks));
  }
}

function clearSelection() {
  if (!state.library.selected.size) return;
  state.library.selected.clear();
  state.library.anchor = null;
  renderSelection();
}

function renderSelection() {
  const { selected } = state.library;
  for (const row of $$("#library .library-row, #library-cards .card")) {
    const picked = selected.has(row.dataset.path);
    row.classList.toggle("is-selected", picked);
    row.setAttribute("aria-selected", String(picked));
  }
  $("#library-delete").hidden = !selected.size;
  $("#library-again").hidden = !selectedItems().some(hasMissing);
  renderStatus();
}

// Only an album this program downloaded knows its tracklist, and only one
// with tracks still missing has anything to fetch. The row, the menu, the
// toolbar and the album page all ask this, so a whole album offers nothing.
function hasMissing(item) {
  return Boolean(item.link) && item.expected > item.tracks;
}

function selectedItems() {
  const { items, selected } = state.library;
  return items.filter((item) => selected.has(item.path));
}

// Albums keep the link they came from, so the folder itself can ask for the
// rest: tracks that failed last time are downloaded, the ones on disk skipped.
async function downloadAgain(items) {
  const links = [...new Set(items.map((item) => item.link).filter(Boolean))];
  if (!links.length) return;
  const { dry_run: dryRun, format } = state.settings;
  const jobs = await api().download(links, state.settings);
  for (const { job, link } of jobs) addJob(job, link, dryRun, format);
  showView("download");
  announce(t("Добавлено в очередь: {count}", { count: links.length }));
}

async function onPageAction(event) {
  const action = event.target.closest("[data-page-action]")?.dataset.pageAction;
  const top = state.library.pages[state.library.pages.length - 1];
  if (action && top?.kind === "playlist") {
    onPlaylistAction(action);
    return;
  }
  if (!action || !top || top.kind !== "album") return;
  const { item } = top;
  if (action === "open") api().open_folder(item.path);
  else if (action === "again") downloadAgain([item]);
  else if (action === "tidy") tidyLibrary([item]);
  else if (action === "play") playAlbumPage(0);
  else if (action === "watch") {
    const list = isWatched(item.link) ? await api().unwatch(item.link) : await api().watch_album(item.path);
    setWatched(list);
    renderWatchButton(item.link);
    announce(t(isWatched(item.link) ? "Следим за «{title}»" : "Больше не следим за «{title}»", { title: item.title }));
  }
}

/* Search */

// The catalogues are asked as the person types, once the typing pauses; Enter
// asks at once. Only the answer to the last question is drawn.
function bindSearch() {
  const input = $("#search-input");
  input.addEventListener("input", () => {
    clearTimeout(state.search.timer);
    state.search.timer = setTimeout(() => runSearch(input.value), 450);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    clearTimeout(state.search.timer);
    runSearch(input.value, true);
  });
  $("#search-scroll").addEventListener("click", onSearchClick);
  $("#search-scroll").addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && event.target.matches(".search-card")) {
      event.preventDefault();
      event.target.click();
    }
  });
  $("#search-back").addEventListener("click", closeSearchPage);
  $("#search-page").addEventListener("click", onSearchPageClick);
}

async function runSearch(text, again = false) {
  const search = state.search;
  const query = text.trim();
  if (query === search.query && !again && (search.results || search.loading)) return;
  const token = ++search.token;
  Object.assign(search, { query, loading: Boolean(query), results: null });
  if (search.pages.length) {
    search.pages = [];
    drawSearchPage();
  }
  renderSearch();
  if (!query) return;
  let results;
  try {
    results = await api().search(query);
  } catch (error) {
    console.error(error);
    results = { error: String(error), albums: [], tracks: [], artists: [] };
  }
  if (token !== search.token) return; // a newer question is on its way
  Object.assign(search, { results, loading: false });
  renderSearch({ enter: true });
}

function renderSearch({ enter = false } = {}) {
  const { query, results, loading } = state.search;
  const found = results || { albums: [], tracks: [], artists: [] };
  const any = found.albums.length || found.tracks.length || found.artists.length;
  const empty = $("#search-empty");
  empty.hidden = Boolean(any);
  $(".empty-title", empty).textContent = !query ? t("Найдите, что скачать")
    : loading ? t("Ищем…")
    : results?.error ? t("Поиск не удался") : t("Ничего не найдено");
  $(".empty-text", empty).textContent = !query
    ? t("Исполнитель, альбом или трек — с обложками из каталога. Щелчок по альбому покажет его треки, стрелка сразу поставит в очередь")
    : loading ? "" : results?.error || t("По запросу «{query}»", { query });
  $("#search-service").textContent = results?.service ? t("Каталог: {service}", { service: results.service }) : "";
  fillSearchSection("artists", found.artists.map(createSearchArtist), enter);
  fillSearchSection("albums", found.albums.map(createSearchAlbum), enter);
  fillSearchSection("tracks", found.tracks.map(createSearchTrack), false);
}

function fillSearchSection(kind, nodes, enter) {
  $(`#search-${kind}-section`).hidden = !nodes.length;
  const list = $(`#search-${kind}`);
  list.replaceChildren(...nodes);
  if (enter && !reduceMotion() && list.classList.contains("cards")) {
    list.classList.remove("entering");
    void list.offsetWidth; // the animation starts over for the new cards
    list.classList.add("entering");
  }
}

const RELEASE_KINDS = { album: "Альбом", single: "Сингл", ep: "EP", compilation: "Сборник" };

// What the library holds, to be marked in the search: an album by the link it
// was downloaded from, or else by its artist and title as the catalogues spell
// them, editions and remasters aside; a single track by its artist and title
function libraryIndex() {
  const library = state.library;
  if (library.index?.items === library.items) return library.index;
  const index = { items: library.items, links: new Map(), albums: new Map(), tracks: new Map() };
  for (const item of library.items) {
    if (item.link) index.links.set(item.link, item);
    (item.album ? index.albums : index.tracks).set(releaseKey(item.artist, item.title), item);
  }
  library.index = index;
  return index;
}

function releaseKey(artist, title) {
  const clean = (text) => String(text || "").toLocaleLowerCase()
    .replace(/[([][^)\]]*(remaster|deluxe|edition|expanded|anniversary|version|bonus|explicit)[^)\]]*[)\]]/g, " ")
    .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const first = String(artist || "").split(/,|&|\s+(?:feat|ft)\.?\s/i)[0];
  return `${clean(first)}|${clean(title)}`;
}

// The library's album or track a search result is, or null
function inLibrary(item, kind = "album") {
  if (!state.library.items.length) return null;
  const index = libraryIndex();
  const key = releaseKey(item.artist, item.title);
  return index.links.get(item.link) || (kind === "album" ? index.albums.get(key) || index.tracks.get(key)
    : index.tracks.get(key)) || null;
}

// The marks drawn again once the library has been read
function markInLibrary() {
  for (const card of $$("#view-search .search-card")) {
    if (card._item) $(".in-library", card).hidden = !inLibrary(card._item);
  }
  for (const row of $$("#view-search .search-track")) {
    if (row._item) $(".in-library-tag", row).hidden = !inLibrary(row._item, "track");
  }
  renderSearchPageButton();
}

function createSearchAlbum(item, index) {
  const card = $("#search-card-template").content.firstElementChild.cloneNode(true);
  card.style.setProperty("--i", Math.min(index, 24));
  card.dataset.link = item.link;
  $(".card-title", card).textContent = item.title;
  $(".card-title", card).title = item.title;
  const tracks = item.tracks
    ? t("{tracks} {trackWord}", { tracks: item.tracks, trackWord: plural(item.tracks, "трек", "трека", "треков") }) : "";
  $(".card-sub", card).textContent = [item.artist, t(RELEASE_KINDS[item.type] || "Альбом"), item.year, tracks]
    .filter(Boolean).join(" · ");
  showRemotePicture($(".card-cover", card), item.cover);
  markQueued($(".queue-btn", card), item.link);
  $(".in-library", card).hidden = !inLibrary(item);
  card._item = item;
  return card;
}

function createSearchTrack(item) {
  const row = $("#search-track-template").content.firstElementChild.cloneNode(true);
  row.dataset.link = item.link;
  $(".title-text", row).textContent = item.title;
  $(".title", row).title = item.title;
  $(".in-library-tag", row).hidden = !inLibrary(item, "track");
  row._item = item;
  $(".cell-artists", row).textContent = item.artist;
  $(".cell-album", row).textContent = item.album;
  $(".cell-time", row).textContent = item.duration ? formatDuration(item.duration) : "";
  showRemotePicture($(".cover", row), item.cover);
  markQueued($(".queue-btn", row), item.link);
  return row;
}

function createSearchArtist(item, index) {
  const card = $("#artist-template").content.firstElementChild.cloneNode(true);
  card.classList.add("search-artist");
  card.style.setProperty("--i", Math.min(index, 24));
  card.dataset.link = item.link;
  $(".card-title", card).textContent = item.name;
  $(".card-sub", card).textContent = item.albums
    ? t("{albums} {albumWord}", { albums: item.albums, albumWord: plural(item.albums, "альбом", "альбома", "альбомов") }) : "";
  const photo = $(".artist-photo", card);
  fillArtistPhoto(photo, item.name || "?", true);
  artistObserver.unobserve(photo); // the catalogue sent its own picture: no lookup by name
  if (item.picture) {
    const image = $("img", photo);
    image.addEventListener("load", () => { image.hidden = false; }, { once: true });
    image.src = item.picture;
  }
  card._item = item;
  return card;
}

// A catalogue's picture, straight from its own address; the note stays where it does not load
function showRemotePicture(box, url) {
  const image = $("img", box);
  if (!url) return;
  image.addEventListener("load", () => {
    image.hidden = false;
    box.classList.add("has-image");
  }, { once: true });
  image.src = url; // not lazy: a hidden picture is never in view, so it would never load
}

function markQueued(button, link) {
  const queued = state.search.queued.has(link);
  button.classList.toggle("is-queued", queued);
  $("use", button).setAttribute("href", queued ? "#i-check" : "#i-download");
  button.title = queued ? t("В очереди") : t("Скачать");
  button.setAttribute("aria-label", button.title);
}

function onSearchClick(event) {
  const queue = event.target.closest(".queue-btn");
  const holder = event.target.closest("[data-link]");
  if (!holder) return;
  if (queue) {
    event.stopPropagation();
    queueFromSearch([holder.dataset.link]);
    return;
  }
  if (holder.classList.contains("search-artist")) openSearchPage("artist", holder._item);
  else if (holder.classList.contains("search-card")) openSearchPage("album", holder._item);
}

// What is chosen here goes into the queue the way a pasted link would; the
// view stays, so more can be picked, and the arrow turns into a tick.
async function queueFromSearch(links) {
  const { dry_run: dryRun, format } = state.settings;
  const jobs = await api().download(links, state.settings);
  for (const { job, link } of jobs) {
    addJob(job, link, dryRun, format);
    state.search.queued.add(link);
  }
  for (const button of $$("#view-search [data-link] .queue-btn, #view-search .queue-btn[data-link]")) {
    markQueued(button, button.dataset.link || button.closest("[data-link]").dataset.link);
  }
  renderSearchPageButton();
  renderChrome();
  announce(t("Добавлено в очередь: {count}", { count: jobs.length }));
}

// The album and artist pages open over the results, one on top of another:
// an artist's album opens over the artist, and Back returns there.
function openSearchPage(kind, item) {
  state.search.pages.push({ kind, item, data: null });
  drawSearchPage();
}

function closeSearchPage() {
  if (!state.search.pages.length) return;
  state.search.pages.pop();
  drawSearchPage();
}

function drawSearchPage() {
  const page = $("#search-page");
  const pages = state.search.pages;
  const top = pages[pages.length - 1] || null;
  state.search.page = top;
  if (!top) {
    page.hidden = true;
    $("#search-input").focus({ preventScroll: true });
    return;
  }
  const below = pages[pages.length - 2];
  $("#search-back-label").textContent = below ? (below.item.name || below.item.title) : t("Поиск");
  $(".album-page", page).hidden = top.kind !== "album";
  $(".search-artist-page", page).hidden = top.kind !== "artist";
  page.hidden = false;
  page.scrollTop = 0;
  $("#search-back").focus({ preventScroll: true });
  if (top.kind === "album") fillSearchAlbum(top);
  else fillSearchArtist(top);
}

async function fillSearchAlbum(entry) {
  const { item } = entry;
  const page = $("#search-page");
  $(".album-title", page).textContent = item.title;
  $(".album-sub", page).textContent = [item.artist, t(RELEASE_KINDS[item.type] || "Альбом"), item.year]
    .filter(Boolean).join(" · ");
  const cover = $(".album-cover", page);
  $("img", cover).hidden = true;
  $("img", cover).removeAttribute("src");
  showRemotePicture(cover, item.cover.replace("/250x250-", "/500x500-"));
  renderSearchPageButton();
  riseIn([...$(".album-info", page).children]);
  if (!entry.data) {
    $(".album-list", page).replaceChildren(searchNote(t("Читаем список треков…")));
    try {
      entry.data = await api().release(item.link);
    } catch (error) {
      entry.data = { error: String(error) };
    }
  }
  if (state.search.page !== entry) return; // another page was opened meanwhile
  const data = entry.data;
  if (data.error) {
    $(".album-list", page).replaceChildren(searchNote(data.error));
    return;
  }
  const count = data.tracks.length;
  $(".album-sub", page).textContent = [data.artist, t(RELEASE_KINDS[data.kind] || "Альбом"), data.year,
    t("{tracks} {trackWord}", { tracks: count, trackWord: plural(count, "трек", "трека", "треков") })]
    .filter(Boolean).join(" · ");
  $(".album-list", page).replaceChildren(...data.tracks.map((track) => {
    const row = createAlbumTrack(track, { artist: data.artist });
    row.classList.add("search-album-track");
    const button = $("#search-track-template").content.querySelector(".queue-btn").cloneNode(true);
    if (track.link) {
      button.dataset.link = track.link;
      markQueued(button, track.link);
    } else {
      button.hidden = true;
    }
    row.append(button);
    return row;
  }));
}

const RELEASE_GROUPS = [
  ["Альбомы", ["album"]],
  ["Синглы и EP", ["single", "ep"]],
  ["Сборники", ["compilation"]],
];

async function fillSearchArtist(entry) {
  const { item } = entry;
  const page = $(".search-artist-page", $("#search-page"));
  $(".artist-name", page).textContent = item.name;
  $(".artist-sub", page).textContent = "";
  const photo = $(".artist-photo", page);
  fillArtistPhoto(photo, item.name || "?", true);
  artistObserver.unobserve(photo);
  if (item.picture) {
    const image = $("img", photo);
    image.addEventListener("load", () => { image.hidden = false; }, { once: true });
    image.src = item.picture;
  }
  riseIn([...$(".artist-info", page).children]);
  if (!entry.data) {
    $(".artist-releases", page).replaceChildren(searchNote(t("Читаем релизы…")));
    try {
      entry.data = await api().artist(item.link);
    } catch (error) {
      entry.data = { error: String(error) };
    }
  }
  if (state.search.page !== entry) return;
  const data = entry.data;
  if (data.error) {
    $(".artist-releases", page).replaceChildren(searchNote(data.error));
    return;
  }
  const count = data.releases.length;
  $(".artist-sub", page).textContent = [
    t("{count} {releaseWord}", { count, releaseWord: plural(count, "релиз", "релиза", "релизов") }),
    data.fans ? t("поклонников: {count}", { count: data.fans.toLocaleString(LANGUAGE) }) : "",
    data.service,
  ].filter(Boolean).join(" · ");
  const sections = [];
  let index = 0;
  for (const [title, kinds] of RELEASE_GROUPS) {
    const releases = data.releases.filter((release) => kinds.includes(release.type));
    if (!releases.length) continue;
    const head = document.createElement("h3");
    head.className = "search-head";
    head.textContent = t(title);
    const cards = document.createElement("div");
    cards.className = "cards";
    cards.append(...releases.map((release) => createSearchAlbum(release, index++)));
    sections.push(head, cards);
  }
  $(".artist-releases", page).replaceChildren(...sections);
  renderArtistButtons();
}

// The library's own page of an album found in the search
function openFromSearch(item) {
  if (!item) return;
  showView("library");
  if (item.album) openAlbum(item);
}

function discographyLinks(data) {
  return data.releases.filter((release) => release.type === "album" || release.type === "ep")
    .map((release) => release.link).reverse(); // oldest first, the order they came out in
}

function renderArtistButtons() {
  const entry = state.search.page;
  if (!entry || entry.kind !== "artist" || !entry.data || entry.data.error) return;
  const page = $(".search-artist-page", $("#search-page"));
  const links = discographyLinks(entry.data);
  const download = $("[data-search-action=discography]", page);
  const queued = links.length > 0 && links.every((link) => state.search.queued.has(link));
  download.disabled = queued || !links.length;
  $("span", download).textContent = queued ? t("В очереди")
    : t("Скачать дискографию: {count}", { count: links.length });
  const watching = isWatched(entry.data.link);
  const watchButton = $("[data-search-action=watch-artist]", page);
  watchButton.setAttribute("aria-pressed", String(watching));
  $("span", watchButton).textContent = t(watching ? "Следим за новыми релизами" : "Следить за новыми релизами");
}

function searchNote(text) {
  const note = document.createElement("div");
  note.className = "album-note";
  note.textContent = text;
  return note;
}

function renderSearchPageButton() {
  const open = state.search.page;
  if (!open) return;
  if (open.kind === "artist") {
    renderArtistButtons();
    return;
  }
  const button = $("[data-search-action=download]", $("#search-page"));
  const queued = state.search.queued.has(open.item.link);
  const owned = inLibrary(open.item);
  button.disabled = queued;
  $("span", button).textContent = queued ? t("В очереди") : owned ? t("Скачать ещё раз") : t("Скачать");
  button.className = owned ? "tool-btn" : "btn primary"; // what is here already asks for no second download
  $("[data-search-action=open-library]", $("#search-page")).hidden = !owned;
}

async function onSearchPageClick(event) {
  const open = state.search.page;
  const queue = event.target.closest(".queue-btn");
  if (queue) {
    event.stopPropagation();
    const link = queue.dataset.link || queue.closest("[data-link]")?.dataset.link;
    if (link) queueFromSearch([link]);
    return;
  }
  const card = event.target.closest(".search-card");
  if (card && card._item) {
    openSearchPage("album", card._item);
    return;
  }
  const action = event.target.closest("[data-search-action]")?.dataset.searchAction;
  if (!action || !open) return;
  if (action === "download") queueFromSearch([open.item.link]);
  else if (action === "open-library") openFromSearch(inLibrary(open.item));
  else if (action === "discography" && open.data) queueFromSearch(discographyLinks(open.data));
  else if (action === "watch-artist" && open.data) {
    const { link, name } = open.data;
    const watching = isWatched(link);
    setWatched(watching ? await api().unwatch(link) : await api().watch_artist(link));
    renderArtistButtons();
    announce(t(watching ? "Больше не следим за «{title}»" : "Следим за «{title}»", { title: name }));
  }
}

/* Player */

// Two audio elements take turns: while one plays, the other has the next
// track of the queue loaded, so an album goes on from track to track without
// the pause of fetching the next one. The queue is a list of paths: an album
// page, or the Tracks tab as it was shown when a track was started.
const player = {
  audio: new Audio(),
  spare: new Audio(),
  ahead: null, // { path, info } of the track the spare element holds
  aheadToken: 0,
  queue: [],
  unshuffled: null, // the queue in its own order while it plays shuffled
  artists: new Map(), // path to artist, so a shuffle can keep one band's tracks apart
  source: "album", // "album": an album page in its order; "tracks": the Tracks tab
  index: -1,
  info: null,
  lines: [], // synced lyrics: { time, text, node }
  current: -1,
  token: 0,
  failures: 0, // files in a row that would not play
  seeking: false,
  previousView: "library",
  fullscreen: false,
  volume: 0.8, // the slider's; ReplayGain turns the element down from it
  muted: false,
  shuffle: false,
  repeat: "off", // "all" goes round the queue, "one" plays the track again
  taskbar: "",
  presence: "", // what Discord was last told
  meta: new Map(), // path → title, artists and length of what was queued, for the queue's rows
  handover: null, // what starts the next track on the dot, with a cancel() of its own
  fading: 0, // the timer that frees the spare element once the last track has faded out
  fadeUntil: 0, // the graph's time a crossfade's rise ends at
  listen: null, // how much of this track has played, for counting it as listened to
};
const REPEATS = ["off", "all", "one"];
// How far the arrow keys move: seconds through the track, and the volume
const SEEK_STEP = 5;
const VOLUME_STEP = 0.05;

// How the player was left last time: the volume, the shuffle and the repeat
// come from the settings file, which outlives the window's own storage
function applyPlayerSettings(settings) {
  const volume = Number(settings.volume);
  player.volume = Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : player.volume;
  player.shuffle = Boolean(settings.shuffle);
  player.repeat = REPEATS.includes(settings.repeat) ? settings.repeat : "off";
  $("#player-volume").value = Math.round(player.volume * 100);
  applyVolume();
  renderPlayerModes();
}

// The settings file is written once the slider comes to rest, not on every
// step of a drag
let playerSaveTimer = 0;
function rememberPlayer(patch) {
  Object.assign(state.settings, patch);
  clearTimeout(playerSaveTimer);
  playerSaveTimer = setTimeout(() => api().save_settings(state.settings), 400);
}

function bindPlayer() {
  $("#player-volume").value = Math.round(player.volume * 100);
  for (const element of [player.audio, player.spare]) {
    element.preload = "auto";
    element.crossOrigin = "anonymous"; // Web Audio hears a file from the local server only so
    // Only the element that plays speaks; the other is loading what comes next
    const own = (handler) => (event) => { if (event.target === player.audio) handler(event); };
    element.addEventListener("timeupdate", own(renderPlayerTime));
    element.addEventListener("durationchange", own(renderPlayerTime));
    element.addEventListener("durationchange", own(() => syncPresence()));
    element.addEventListener("seeked", own(() => syncPresence(true)));
    element.addEventListener("timeupdate", own(() => {
      countListen();
      watchHandover();
    }));
    element.addEventListener("seeking", own(cancelHandover));
    element.addEventListener("pause", own(cancelHandover));
    element.addEventListener("play", own(renderPlayerButton));
    element.addEventListener("pause", own(renderPlayerButton));
    element.addEventListener("playing", own(() => { player.failures = 0; }));
    element.addEventListener("ended", own(onTrackEnded));
    element.addEventListener("error", (event) => {
      if (event.target !== player.audio) {
        // The next track is fetched again when its turn comes
        if (player.ahead && event.target.src === player.ahead.info.url) player.ahead = null;
        return;
      }
      if (!player.info) return;
      announce(t("Не получилось сыграть «{title}»", { title: player.info.title }));
      skipBroken();
    });
  }
  $("#player-play").addEventListener("click", togglePlay);
  $("#player-prev").addEventListener("click", () => stepTrack(-1));
  $("#player-next").addEventListener("click", () => stepTrack(1));
  $("#player-shuffle").addEventListener("click", toggleShuffle);
  $("#player-repeat").addEventListener("click", cycleRepeat);
  $("#player-stop").addEventListener("click", stopPlayer);
  $("#player-open").addEventListener("click", toggleNowPlaying);
  $("#player-lyrics").addEventListener("click", toggleNowPlaying);
  $("#now-close").addEventListener("click", closeNowPlaying);
  $("#player-full").addEventListener("click", toggleFullscreen);
  const position = $("#player-position");
  position.addEventListener("input", () => {
    player.seeking = true;
    fillRange(position);
    const length = player.audio.duration;
    if (length) $("#player-time").textContent = formatDuration(position.value / 1000 * length);
  });
  position.addEventListener("change", () => {
    if (player.audio.duration) player.audio.currentTime = position.value / 1000 * player.audio.duration;
    player.seeking = false;
  });
  $("#player-volume").addEventListener("input", (event) => setVolume(event.target.value / 100));
  $("#player-mute").addEventListener("click", () => setMuted(!player.muted));
  $("#now-lyrics").addEventListener("scroll", (event) => {
    event.currentTarget.classList.toggle("scrolled", event.currentTarget.scrollTop > 4);
  });
  $("#now-lyrics").addEventListener("click", (event) => {
    const line = event.target.closest("[data-time]");
    if (line) player.audio.currentTime = Number(line.dataset.time);
  });
  $("#library-tracks").addEventListener("click", onTrackCoverClick, true);
  $(".album-list", $("#album-page")).addEventListener("click", (event) => {
    const number = event.target.closest(".album-track[data-path] .n");
    if (number) playAlbumPage(number.closest(".album-track"));
  });
  $(".album-list", $("#album-page")).addEventListener("dblclick", (event) => {
    const row = event.target.closest(".album-track[data-path]");
    if (row) playAlbumPage(row);
  });
  document.addEventListener("keydown", onPlayerKey);
  if ("mediaSession" in navigator) {
    // The keyboard's media keys and the system's own media controls
    const handlers = {
      play: () => {
        wakeSound();
        player.audio.play();
      },
      pause: () => player.audio.pause(),
      stop: stopPlayer,
      previoustrack: () => stepTrack(-1),
      nexttrack: () => stepTrack(1),
      seekto: (details) => { player.audio.currentTime = details.seekTime; },
      seekbackward: (details) => { player.audio.currentTime = Math.max(0, player.audio.currentTime - (details.seekOffset || 10)); },
      seekforward: (details) => { player.audio.currentTime += details.seekOffset || 10; },
    };
    for (const [action, handler] of Object.entries(handlers)) {
      try {
        navigator.mediaSession.setActionHandler(action, handler);
      } catch {
        // an engine that knows no such action simply goes without it
      }
    }
  }
  bindEqualizer();
  bindQueue();
  bindTrackMenu();
  bindPlaylists();
  bindPlayerSettings();
  renderPlayerModes();
  renderPlayerVolume();
}

// The player's keys, as media players have them: Space, the arrows for the
// position and the volume, M for the sound, L for the words, F for the whole
// screen. Elsewhere than in Now playing the arrows move through lists and
// cards, so there they are the player's only when nothing else has the focus.
function onPlayerKey(event) {
  if (!player.info || event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
  const target = event.target instanceof Element ? event.target : null;
  if (target?.closest("input:not([type=range]), textarea, select, [contenteditable]")) return;
  const now = state.view === "now";
  const free = now || !target || target === document.body || Boolean(target.closest("#player"));
  const slider = Boolean(target?.closest("input[type=range]")); // a slider moves itself with the arrows
  const arrow = { ArrowLeft: -SEEK_STEP, ArrowRight: SEEK_STEP }[event.key];
  const volume = { ArrowUp: VOLUME_STEP, ArrowDown: -VOLUME_STEP }[event.key];
  if (event.code === "Space") {
    if (!now || target?.closest("button, input")) return; // elsewhere Space picks rows and presses buttons
    togglePlay();
  } else if (arrow && free && !slider) {
    const length = player.audio.duration || 0;
    player.audio.currentTime = Math.min(Math.max(0, player.audio.currentTime + arrow), length || Infinity);
  } else if (volume && free && !slider) {
    setVolume(player.volume + volume);
  } else if (event.code === "KeyM") {
    setMuted(!player.muted);
  } else if (event.code === "KeyL") {
    toggleNowPlaying();
  } else if (event.code === "KeyQ") {
    toggleQueue();
  } else if (event.code === "KeyF" && now) {
    toggleFullscreen();
  } else {
    return;
  }
  event.preventDefault();
}

// The album page's own files, in the order shown; starting at a row or a position
function playAlbumPage(start) {
  if ($("#album-page").dataset.kind === "playlist") {
    playPlaylistPage(typeof start === "number" ? null : start.dataset.path);
    return;
  }
  rememberTracks($("#album-page").tracks || []);
  const rows = [...$$(".album-list .album-track[data-path]", $("#album-page"))];
  if (!rows.length) return;
  const index = typeof start === "number" ? start : Math.max(0, rows.indexOf(start));
  const artists = new Map(rows.map((row) => [row.dataset.path, $(".artists", row)?.textContent || ""]));
  playQueue(rows.map((row) => row.dataset.path), index, "album", artists);
}

function playQueue(paths, index, source, artists = new Map()) {
  if (!paths.length) return;
  const start = Math.min(Math.max(0, index), paths.length - 1);
  Object.assign(player, { source, artists, failures: 0, unshuffled: null, queue: paths, index: start });
  if (player.shuffle) shuffleQueue();
  loadTrack();
}

// The whole library, or the genre it is narrowed to, shuffled from a random track
async function shuffleLibrary() {
  await loadTracks();
  const tracks = state.library.trackList.items.filter((track) => inGenre(track.genre));
  if (!tracks.length) return;
  if (!player.shuffle) {
    player.shuffle = true;
    rememberPlayer({ shuffle: true });
    renderPlayerModes();
  }
  playQueue(tracks.map((track) => track.path), Math.floor(Math.random() * tracks.length), "tracks", trackArtists(tracks));
}

function trackArtists(tracks) {
  return new Map(tracks.map((track) => [track.path, track.album_artist || track.artists || ""]));
}

async function loadTrack({ fade = 0, outgoing = null } = {}) {
  cancelHandover();
  const token = ++player.token;
  const path = player.queue[player.index];
  let info = null;
  if (player.ahead?.path === path) {
    // Loaded while the last one played: the elements swap and it starts at once
    info = player.ahead.info;
    [player.audio, player.spare] = [player.spare, player.audio];
  } else {
    try {
      info = await api().play(path);
    } catch (error) {
      console.error(error);
    }
    if (token !== player.token) return;
    if (!info) {
      skipBroken();
      return;
    }
    player.audio.src = info.url;
  }
  if (outgoing && outgoing === player.spare) releaseOutgoing(outgoing, fade);
  else clearSpare();
  if (!fade) player.fadeUntil = 0; // a skip during a crossfade takes the new track at its own level
  player.info = info;
  resetListen();
  applyVolume(fade);
  wakeSound();
  player.audio.play().catch(() => renderPlayerButton());
  renderPlayer();
  loadLyrics(path, token);
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: info.title, artist: info.artists, album: info.album,
      artwork: info.cover ? [{ src: info.cover, sizes: "512x512" }] : [],
    });
  }
  if (!outgoing) loadAhead(); // after a handover it loads once the last track has let the spare go
}

// The track after this one is fetched and loaded while this one plays
async function loadAhead() {
  const token = ++player.aheadToken;
  const next = player.repeat === "one" ? -1 : stepIndex(1);
  const path = next >= 0 && next !== player.index ? player.queue[next] : "";
  if (!path) return;
  let info = null;
  try {
    info = await api().play(path);
  } catch {
    // fetched when its turn comes
  }
  if (token !== player.aheadToken || !info) return;
  player.ahead = { path, info };
  player.spare.src = info.url;
  player.spare.load();
}

function clearSpare() {
  clearTimeout(player.fading);
  player.fading = 0;
  player.ahead = null;
  player.aheadToken += 1;
  player.spare.pause();
  player.spare.removeAttribute("src");
  player.spare.load();
}

// Where a step lands in the queue: round it while it repeats, -1 past its ends
function stepIndex(step) {
  const { queue, index } = player;
  if (player.repeat === "all" && queue.length) return (index + step + queue.length) % queue.length;
  return index + step >= 0 && index + step < queue.length ? index + step : -1;
}

// Forward or back through the queue. At the end the player stops unless it
// repeats; "back" in the first seconds of a track goes to the one before,
// later it starts over.
function stepTrack(step, automatic = false) {
  if (!player.queue.length) return;
  if (step < 0 && player.audio.currentTime > 3) {
    player.audio.currentTime = 0;
    return;
  }
  // Round the queue again shuffled: a new order, not the last one once more
  if (step > 0 && player.shuffle && player.repeat === "all" && player.index + step >= player.queue.length
      && player.queue.length > 2) {
    const last = player.queue[player.index];
    player.queue = spreadShuffle(player.queue);
    if (player.queue[0] === last) player.queue.push(player.queue.shift());
    player.index = 0;
    loadTrack();
    return;
  }
  const next = stepIndex(step);
  if (next < 0) {
    if (automatic) {
      player.audio.pause();
      player.audio.currentTime = 0;
      renderPlayerButton();
    }
    return;
  }
  player.index = next;
  loadTrack();
}

function onTrackEnded() {
  if (player.repeat === "one") {
    player.audio.currentTime = 0;
    resetListen(); // played again, it counts again
    player.audio.play();
    return;
  }
  stepTrack(1, true);
}

// A file that will not play is passed over; a queue of nothing but such files stops
function skipBroken() {
  player.failures += 1;
  if (player.failures >= player.queue.length) {
    player.failures = 0;
    player.audio.pause();
    renderPlayerButton();
    return;
  }
  stepTrack(1, true);
}

function togglePlay() {
  if (!player.info) return;
  wakeSound();
  if (player.audio.paused) player.audio.play();
  else player.audio.pause();
}

// Shuffled, the track that plays stays first and the rest follow in any
// order; back in order, the queue is the album or the list as it was
function toggleShuffle() {
  player.shuffle = !player.shuffle;
  rememberPlayer({ shuffle: player.shuffle });
  if (player.queue.length) {
    if (player.shuffle) {
      shuffleQueue();
    } else if (player.unshuffled) {
      const current = player.queue[player.index];
      player.queue = player.unshuffled;
      player.index = Math.max(0, player.queue.indexOf(current));
      player.unshuffled = null;
    }
    clearSpare();
    loadAhead();
    applyVolume(); // shuffled, each track takes its own ReplayGain
  }
  renderPlayerModes();
  if (player.info) renderPlayer();
}

function shuffleQueue() {
  const { queue, index } = player;
  const order = spreadShuffle(queue);
  const at = order.indexOf(queue[index]);
  player.unshuffled = queue;
  player.queue = [...order.slice(at), ...order.slice(0, at)]; // turned round, the gaps stay as they were
  player.index = 0;
}

// Shuffled the way listeners hear as random. Plain random order clumps: a
// band with a tenth of the tracks comes back every few songs and now and then
// twice in a row. So each artist's tracks are spread evenly through the whole
// queue, each at a random place in its own stretch, and the artists'
// stretches start at random, which keeps them from following one another in
// step (Spotify's way since 2014).
function spreadShuffle(paths) {
  const groups = new Map();
  for (const path of paths) {
    const artist = artistKey(player.artists.get(path));
    if (!groups.has(artist)) groups.set(artist, []);
    groups.get(artist).push(path);
  }
  const placed = [];
  for (const group of groups.values()) {
    for (let i = group.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [group[i], group[j]] = [group[j], group[i]];
    }
    const stretch = 1 / group.length;
    const start = Math.random() * stretch;
    group.forEach((path, i) => placed.push({ path, at: start + i * stretch + (Math.random() - 0.5) * stretch * 0.3 }));
  }
  return placed.sort((a, b) => a.at - b.at).map((entry) => entry.path);
}

// "Sonic Youth, Lydia Lunch" and "sonic youth" are one band to keep apart
function artistKey(text) {
  return String(text || "").split(/,|;|&|\s+(?:feat|ft)\.?\s/i)[0].trim().toLocaleLowerCase();
}

function cycleRepeat() {
  player.repeat = REPEATS[(REPEATS.indexOf(player.repeat) + 1) % REPEATS.length];
  rememberPlayer({ repeat: player.repeat });
  if (player.queue.length) {
    clearSpare();
    loadAhead();
  }
  renderPlayerModes();
  if (player.info) renderPlayer();
}

function renderPlayerModes() {
  const shuffle = $("#player-shuffle");
  shuffle.setAttribute("aria-pressed", String(player.shuffle));
  shuffle.title = t(player.shuffle ? "Играть по порядку" : "Играть вперемешку");
  shuffle.setAttribute("aria-label", shuffle.title);
  const repeat = $("#player-repeat");
  repeat.setAttribute("aria-pressed", String(player.repeat !== "off"));
  $("use", repeat).setAttribute("href", player.repeat === "one" ? "#i-mi-repeat-one" : "#i-mi-repeat");
  repeat.title = t({ off: "Повторять очередь", all: "Повторять этот трек", one: "Не повторять" }[player.repeat]);
  repeat.setAttribute("aria-label", repeat.title);
}

function setVolume(volume) {
  player.volume = Math.min(1, Math.max(0, Math.round(volume * 100) / 100));
  player.muted = false;
  $("#player-volume").value = Math.round(player.volume * 100);
  rememberPlayer({ volume: player.volume });
  applyVolume();
}

function setMuted(muted) {
  player.muted = muted;
  applyVolume();
}

// ReplayGain: an album page played in its order takes the album's gain, so a
// quiet interlude stays quiet beside a loud single; shuffled, or from the
// Tracks tab, each track takes its own. The element cannot play louder than
// the file, so a track quieter than the reference stays at the slider's volume.
function applyVolume(fadeIn = 0) {
  const gain = player.info?.gain || {};
  const inOrder = player.source === "album" && !player.shuffle;
  const decibels = (inOrder ? gain.album ?? gain.track : gain.track ?? gain.album) ?? 0;
  const replayGain = Math.min(1, 10 ** (decibels / 20));
  if (sound.context) {
    for (const element of [player.audio, player.spare]) {
      element.volume = 1;
      element.muted = false;
    }
    const now = sound.context.currentTime;
    if (fadeIn > 0) {
      setElementGain(player.audio, replayGain, fadeIn, 0);
      player.fadeUntil = now + fadeIn;
    } else if (now >= player.fadeUntil) {
      setElementGain(player.audio, replayGain); // a crossfade's rise is left to finish
    }
    sound.master.gain.setTargetAtTime(player.muted ? 0 : player.volume, now, 0.015);
  } else {
    player.audio.volume = player.volume * replayGain;
    player.audio.muted = player.muted;
  }
  renderPlayerVolume();
}

function stopPlayer() {
  cancelHandover();
  player.token += 1;
  player.audio.pause();
  player.audio.removeAttribute("src");
  player.audio.load();
  clearSpare();
  Object.assign(player, { queue: [], unshuffled: null, index: -1, info: null, lines: [], current: -1 });
  if (state.view === "now") closeNowPlaying();
  renderPlayer();
}

function renderPlayer() {
  const bar = $("#player");
  const info = player.info;
  bar.hidden = !info;
  markPlayingRows();
  if (!info) {
    syncTaskbar();
    renderQueue();
    return;
  }
  $(".player-title", bar).textContent = info.title;
  $(".player-title", bar).title = info.title;
  $(".player-artist", bar).textContent = info.artists || info.album_artist;
  setPlayerPicture($(".player-cover", bar), info.cover);
  const round = player.repeat === "all";
  $("#player-prev").disabled = !round && player.index <= 0 && player.audio.currentTime <= 3;
  $("#player-next").disabled = !round && player.index >= player.queue.length - 1;
  renderPlayerButton();
  renderPlayerTime();
  renderNowPlaying();
  renderQueue();
}

function setPlayerPicture(box, url) {
  const image = $("img", box);
  if (image.dataset.src === url) return;
  image.dataset.src = url || "";
  if (!url) {
    image.hidden = true;
    image.removeAttribute("src");
    return;
  }
  // The picture is fetched beside the one on screen and takes its place when
  // it is there; hiding it first showed the empty note for a moment
  const next = new Image();
  next.addEventListener("load", () => {
    if (image.dataset.src !== url) return; // another track came meanwhile
    image.src = url;
    image.hidden = false;
  }, { once: true });
  next.src = url;
}

function renderPlayerButton() {
  const playing = !player.audio.paused;
  const button = $("#player-play");
  $("use", button).setAttribute("href", playing ? "#i-mi-pause" : "#i-mi-play");
  button.title = t(playing ? "Пауза" : "Слушать");
  button.setAttribute("aria-label", button.title);
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = playing ? "playing" : "paused";
  syncTaskbar();
  syncPresence();
}

function renderPlayerTime() {
  const { audio } = player;
  const length = audio.duration || player.info?.duration || 0;
  if (!player.seeking) {
    $("#player-time").textContent = formatDuration(audio.currentTime || 0);
    $("#player-position").value = length ? Math.round((audio.currentTime / length) * 1000) : 0;
    fillRange($("#player-position"));
  }
  $("#player-length").textContent = formatDuration(length);
  highlightLyric();
  if ("mediaSession" in navigator && navigator.mediaSession.setPositionState && length && Number.isFinite(audio.duration)) {
    try {
      navigator.mediaSession.setPositionState({ duration: audio.duration, position: Math.min(audio.currentTime, audio.duration),
                                                playbackRate: audio.playbackRate || 1 });
    } catch {
      // a position past the end while a new file loads
    }
  }
}

function renderPlayerVolume() {
  const silent = player.muted || player.volume === 0;
  $("use", $("#player-mute")).setAttribute("href", silent ? "#i-mi-mute" : player.volume < 0.5 ? "#i-mi-volume-low" : "#i-mi-volume");
  $("#player-mute").title = t(silent ? "Со звуком" : "Без звука");
  fillRange($("#player-volume"));
}

// The buttons under the window's picture on the taskbar follow the player
function syncTaskbar() {
  const { info } = player;
  const buttons = info
    ? { playing: !player.audio.paused, prev: !$("#player-prev").disabled, next: !$("#player-next").disabled,
        words: { prev: t("Предыдущий"), play: t("Слушать"), pause: t("Пауза"), next: t("Следующий") },
        // and for Windows' media controls, what plays
        title: info.title, artists: info.artists || "", album: info.album || "", album_artist: info.album_artist || "",
        cover: info.cover || "" }
    : null;
  const key = JSON.stringify(buttons);
  if (key === player.taskbar) return;
  player.taskbar = key;
  Promise.resolve().then(() => api().player_buttons(buttons)).catch(() => {});
}

// The Discord profile shows the track while it plays, with a bar Discord moves
// by itself: it is told again when the track, the pause or the place changes
function syncPresence(moved = false) {
  const { info, audio } = player;
  const length = audio.duration || info?.duration || 0;
  const track = info && !audio.paused
    ? { path: info.path, title: info.title, artists: info.artists || info.album_artist || "", album: info.album || "",
        album_artist: info.album_artist || "", duration: length, position: audio.currentTime || 0 }
    : null;
  const key = track ? `${info.path}|${Math.round(length)}` : "";
  if (key === player.presence && !(moved && track)) return;
  player.presence = key;
  Promise.resolve().then(() => api().now_playing(track)).catch(() => {});
}

// A button of Windows' media controls or a media key, passed on by the program
function mediaAction(name) {
  if (!player.info) return;
  if (name === "play" && player.audio.paused) togglePlay();
  else if (name === "pause" && !player.audio.paused) togglePlay();
  else if (name === "next") stepTrack(1);
  else if (name === "previous") stepTrack(-1);
  else if (name === "stop") stopPlayer();
}

// A click on one of those buttons, passed on by the program
function taskbarAction(name) {
  if (name === "prev") stepTrack(-1);
  else if (name === "next") stepTrack(1);
  else if (name === "play") togglePlay();
}

// A slider's line is filled up to its knob, as the share --p that .bar-fill uses too
function fillRange(input) {
  const min = Number(input.min) || 0;
  const max = Number(input.max) || 100;
  input.style.setProperty("--p", (Number(input.value) - min) / (max - min));
}

// The row of the track that plays, wherever it is on screen
function markPlayingRows() {
  const path = player.info?.path;
  for (const row of $$(".album-track.is-playing, .track-item.is-playing")) row.classList.remove("is-playing");
  if (!path) return;
  for (const row of $$(".album-list .album-track[data-path], #library-tracks .track-item")) {
    if (row.dataset.path === path) row.classList.add("is-playing");
  }
}

/* Sound: the equaliser and the crossfade */

// Both elements play into one Web Audio graph: each through a gain of its own
// (its ReplayGain, and its share of a crossfade), then the equaliser's ten
// bands, then the volume. The graph is made at the first play the person asks
// for, since a browser keeps sound shut until then; before it, and wherever Web
// Audio fails, the elements play straight out as they used to.
const EQ_FREQUENCIES = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
const EQ_LIMIT = 12;
const EQ_PRESETS = {
  flat: { label: "Ровно", bands: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
  bass: { label: "Больше баса", bands: [6, 5, 4, 2, 0, 0, 0, 0, 0, 0] },
  treble: { label: "Больше высоких", bands: [0, 0, 0, 0, 0, 1, 2, 4, 5, 6] },
  vocal: { label: "Голос", bands: [-2, -2, -1, 1, 3, 4, 3, 1, 0, -1] },
  rock: { label: "Рок", bands: [4, 3, 2, 0, -1, -1, 1, 2, 3, 4] },
  electronic: { label: "Электроника", bands: [5, 4, 1, 0, -2, 1, 0, 1, 4, 5] },
  acoustic: { label: "Акустика", bands: [3, 3, 2, 1, 1, 1, 2, 2, 2, 1] },
  quiet: { label: "Тихо, но разборчиво", bands: [5, 4, 2, 0, -1, 0, 1, 2, 4, 4] },
};
const sound = { context: null, failed: false, gains: new Map(), filters: [], preamp: null, master: null };

function soundGraph() {
  if (sound.context || sound.failed) return sound.context;
  try {
    const context = new AudioContext({ latencyHint: "playback" });
    sound.preamp = context.createGain();
    sound.master = context.createGain();
    sound.filters = EQ_FREQUENCIES.map((frequency, index) => {
      const filter = context.createBiquadFilter();
      filter.type = index === 0 ? "lowshelf" : index === EQ_FREQUENCIES.length - 1 ? "highshelf" : "peaking";
      filter.frequency.value = frequency;
      filter.Q.value = 1.1; // an octave wide: neighbouring bands meet without a dip
      return filter;
    });
    let chain = sound.preamp;
    for (const filter of sound.filters) chain = chain.connect(filter);
    chain.connect(sound.master).connect(context.destination);
    for (const element of [player.audio, player.spare]) {
      const gain = context.createGain();
      context.createMediaElementSource(element).connect(gain).connect(sound.preamp);
      sound.gains.set(element, gain);
    }
    sound.context = context;
    applyEqualizer();
    applyVolume();
  } catch (error) {
    console.error(error);
    sound.failed = true;
  }
  return sound.context;
}

// A browser starts a graph suspended until the page has been used
function wakeSound() {
  if (!sound.context && navigator.userActivation?.hasBeenActive) soundGraph();
  if (sound.context?.state === "suspended") sound.context.resume().catch(() => {});
}

// One element's own gain, at once or along a curve of so many seconds. The
// curve is a quarter of a sine: two tracks crossing keep the loudness of one,
// where straight lines would dip in the middle.
function setElementGain(element, value, seconds = 0, from = null) {
  const gain = sound.gains.get(element)?.gain;
  if (!gain) return;
  const now = sound.context.currentTime;
  const start = from ?? gain.value;
  gain.cancelScheduledValues(now);
  if (seconds > 0) {
    const steps = 64;
    const curve = new Float32Array(steps);
    for (let i = 0; i < steps; i++) {
      const x = i / (steps - 1);
      curve[i] = value > start ? start + (value - start) * Math.sin(x * Math.PI / 2)
        : value + (start - value) * Math.cos(x * Math.PI / 2);
    }
    gain.setValueCurveAtTime(curve, now, seconds);
  } else {
    gain.setValueAtTime(value, now);
  }
}

function applyEqualizer() {
  if (!sound.context) return;
  const { on, bands } = equalizer();
  const now = sound.context.currentTime;
  sound.filters.forEach((filter, index) => filter.gain.setTargetAtTime(on ? bands[index] : 0, now, 0.03));
  // Raised bands would push a loud master over the top: the whole is lowered
  // by the highest of them, so the equaliser shapes the sound and never clips it
  const peak = on ? Math.max(0, ...bands) : 0;
  sound.preamp.gain.setTargetAtTime(10 ** (-peak / 20), now, 0.03);
}

function equalizer() {
  const eq = state.settings.eq || {};
  const bands = EQ_FREQUENCIES.map((_, index) => Number(eq.bands?.[index]) || 0);
  return { on: Boolean(eq.on), bands, preset: eq.preset || "flat" };
}

function setEqualizer(patch) {
  const eq = { ...equalizer(), ...patch };
  rememberPlayer({ eq });
  soundGraph();
  applyEqualizer();
  renderEqualizer();
}

function bindEqualizer() {
  const bands = $("#eq-bands");
  bands.replaceChildren(...EQ_FREQUENCIES.map((frequency, index) => {
    const band = document.createElement("div");
    band.className = "eq-band";
    const value = document.createElement("output");
    const slider = document.createElement("input");
    Object.assign(slider, { type: "range", min: -EQ_LIMIT, max: EQ_LIMIT, step: 0.5, value: 0 });
    slider.dataset.band = index;
    const label = document.createElement("span");
    label.textContent = frequency >= 1000 ? `${frequency / 1000}k` : String(frequency);
    slider.setAttribute("aria-label", t("{hertz} Гц", { hertz: frequency }));
    const slot = document.createElement("div");
    slot.className = "eq-slot";
    slot.append(slider);
    band.append(value, slot, label);
    return band;
  }));
  bands.addEventListener("input", (event) => {
    const index = Number(event.target.dataset.band);
    if (Number.isNaN(index)) return;
    const values = equalizer().bands;
    values[index] = Number(event.target.value);
    // Moving a band means wanting to hear it: the equaliser comes on
    setEqualizer({ bands: values, preset: "custom", on: true });
  });
  // A double click puts a band back to the middle
  bands.addEventListener("dblclick", (event) => {
    const index = Number(event.target.dataset?.band);
    if (Number.isNaN(index)) return;
    const values = equalizer().bands;
    values[index] = 0;
    setEqualizer({ bands: values, preset: "custom" });
  });
  $("#eq-on").addEventListener("click", () => setEqualizer({ on: !equalizer().on }));
  $("#eq-preset").addEventListener("change", (event) => {
    const preset = EQ_PRESETS[event.target.value];
    if (preset) setEqualizer({ bands: [...preset.bands], preset: event.target.value, on: true });
  });
  $("#eq-close").addEventListener("click", () => toggleEqualizer(false));
  $("#player-eq").addEventListener("click", () => toggleEqualizer());
  $("#eq-open").addEventListener("click", () => toggleEqualizer(true));
  // Anywhere else closes it, as a menu is closed
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#eq-panel, #player-eq, #eq-open")) toggleEqualizer(false);
  });
}

function toggleEqualizer(open = $("#eq-panel").hidden) {
  const panel = $("#eq-panel");
  if (open === !panel.hidden) return;
  panel.hidden = !open;
  $("#player-eq").setAttribute("aria-pressed", String(open));
  if (!open) return;
  toggleQueue(false);
  soundGraph();
  placeFloat(panel);
  renderEqualizer();
  $("#eq-preset").focus();
}

function renderEqualizer() {
  const { on, bands, preset } = equalizer();
  syncSwitch($("#eq-on"), on);
  $("#eq-panel").classList.toggle("off", !on);
  const pick = $("#eq-preset");
  const options = Object.entries(EQ_PRESETS).map(([key, entry]) => new Option(t(entry.label), key));
  if (preset === "custom") options.push(new Option(t("Своя"), "custom"));
  pick.replaceChildren(...options);
  pick.value = preset in EQ_PRESETS || preset === "custom" ? preset : "flat";
  for (const slider of $$("#eq-bands input")) {
    const value = bands[Number(slider.dataset.band)];
    if (document.activeElement !== slider) slider.value = value;
    slider.style.setProperty("--p", (value + EQ_LIMIT) / (EQ_LIMIT * 2));
    $("output", slider.closest(".eq-band")).textContent = value > 0 ? `+${value}` : String(value);
  }
  $("#player-eq").classList.toggle("on", on);
  const name = preset === "custom" ? t("своя") : t(EQ_PRESETS[preset]?.label || "Ровно").toLocaleLowerCase(LANGUAGE);
  $("#eq-state").textContent = on ? t("Включён: {preset}", { preset: name }) : t("Выключен");
}

// The queue and the equaliser float over the page, just above the player
function placeFloat(panel) {
  const bar = $("#player");
  panel.style.bottom = `${(bar.hidden ? 0 : bar.offsetHeight) + 12}px`;
}

/* Handing over from one track to the next */

// The next track of the same record starts the moment this one ends, and one
// from another record fades in over this one's end (if a crossfade is set).
// The spare element holds the next track already, so either is a matter of
// starting it at the right time rather than waiting for "ended".
function crossfadeFor(ahead) {
  const seconds = Number(state.settings.crossfade) || 0;
  if (!seconds || !sound.context || !player.info || !ahead) return 0;
  // Only an album heard in its own order goes on without one: shuffled, its
  // tracks are no longer each other's continuation
  const sameRecord = !player.shuffle && Boolean(ahead.info.album) && ahead.info.album === player.info.album
    && (ahead.info.album_artist || "") === (player.info.album_artist || "");
  return sameRecord ? 0 : seconds;
}

function watchHandover() {
  const { audio, ahead } = player;
  if (player.handover || player.seeking || audio.paused || !player.info || player.repeat === "one") return;
  const next = stepIndex(1);
  // Round a shuffled queue the order is dealt again, so the track held is not the next one
  if (next < 0 || (next === 0 && player.shuffle) || ahead?.path !== player.queue[next]) return;
  if (player.spare.readyState < HTMLMediaElement.HAVE_FUTURE_DATA) return;
  const length = audio.duration || 0;
  const left = length - audio.currentTime;
  if (!Number.isFinite(left) || left <= 0) return;
  const fade = Math.min(crossfadeFor(ahead), length / 3, (player.spare.duration || 0) / 3);
  if (fade >= 1 && left <= fade) {
    handOver(fade);
  } else if (fade < 1 && state.settings.gapless !== false && left <= 1) {
    // Started a hair before the end: the one that ends is cut, not waited for
    player.handover = atSoundTime(Math.max(0, left / (audio.playbackRate || 1) - 0.025), () => handOver(0));
  }
}

// Something done so many seconds from now on the sound's own clock. A timer
// will not do: in a window out of sight the browser lets timers run a second
// late, and the track would end before the next one started. A silent source
// stopped at that moment says so on time, since the sound is never held back.
function atSoundTime(seconds, callback) {
  const context = sound.context;
  if (!context || context.state !== "running") {
    const timer = setTimeout(callback, seconds * 1000);
    return { cancel: () => clearTimeout(timer) };
  }
  const tick = context.createConstantSource();
  const silence = context.createGain();
  silence.gain.value = 0;
  tick.connect(silence).connect(context.destination);
  let live = true;
  tick.onended = () => {
    tick.disconnect();
    silence.disconnect();
    if (live) callback();
  };
  tick.start();
  tick.stop(context.currentTime + seconds);
  return { cancel: () => { live = false; } };
}

function cancelHandover() {
  player.handover?.cancel?.();
  player.handover = null;
}

function handOver(fade) {
  cancelHandover();
  const next = stepIndex(1);
  if (next < 0 || player.ahead?.path !== player.queue[next]) return;
  const outgoing = player.audio;
  player.index = next;
  loadTrack({ fade, outgoing });
}

// The track that has handed over fades out (or stops at once), and only then
// is the spare element free to load the track after the new one
function releaseOutgoing(element, fade) {
  player.ahead = null;
  player.aheadToken += 1;
  clearTimeout(player.fading);
  const done = () => {
    player.fading = 0;
    if (element !== player.spare) return; // taken up again meanwhile
    element.pause();
    element.removeAttribute("src");
    element.load();
    setElementGain(element, 1);
    loadAhead();
  };
  if (fade > 0 && sound.context) {
    setElementGain(element, 0, fade);
    player.fading = setTimeout(done, fade * 1000 + 60);
  } else {
    done();
  }
}

/* Listening: what counts, for the play counts, Last.fm and ListenBrainz */

// A track counts once half of it, or four minutes, has played (the rule
// Last.fm and ListenBrainz share), and only what really played: a jump
// forward adds nothing
function resetListen() {
  player.listen = { at: player.audio.currentTime || 0, played: 0, counted: false };
}

function countListen() {
  const { audio, info, listen } = player;
  if (!info || !listen || listen.counted) return;
  const step = audio.currentTime - listen.at;
  listen.at = audio.currentTime;
  if (step > 0 && step < 2) listen.played += step;
  const length = audio.duration || info.duration || 0;
  if (length < 30 || listen.played < Math.min(length / 2, 240)) return;
  listen.counted = true;
  const track = { path: info.path, title: info.title, artists: info.artists || "", album: info.album || "",
                  album_artist: info.album_artist || "", duration: Math.round(length) };
  Promise.resolve().then(() => api().listened(track)).catch(() => {});
  state.plays.set(info.path, (state.plays.get(info.path) || 0) + 1);
  state.playlists.stale = true; // "Recently played" and "Most played" have changed
}

/* The queue */

// What a queued path shows: remembered from wherever it was queued from, else
// from the Tracks tab, else its file name
function rememberTracks(tracks) {
  for (const track of tracks) {
    if (!track?.path) continue;
    player.meta.set(track.path, { path: track.path, title: track.title || "", artists: track.artists || track.album_artist || "",
                                  album: track.album || "", album_artist: track.album_artist || "", duration: track.duration || 0 });
  }
}

let tracksIndex = { items: null, byPath: new Map() };
function libraryTrack(path) {
  const items = state.library.trackList.items;
  if (tracksIndex.items !== items) tracksIndex = { items, byPath: new Map(items.map((track) => [track.path, track])) };
  return tracksIndex.byPath.get(path);
}

function trackMeta(path) {
  const known = player.meta.get(path) || libraryTrack(path);
  if (known) return known;
  const name = path.split(/[\\/]/).pop() || path;
  return { path, title: name.replace(/\.[^.]+$/, ""), artists: "", album: "", duration: 0 };
}

// Tracks put in the queue: after the one that plays, or at the end. With
// nothing playing they simply start.
function enqueue(tracks, next = false) {
  const playable = tracks.filter((track) => track?.path && !track.missing);
  if (!playable.length) return;
  rememberTracks(playable);
  const paths = playable.map((track) => track.path);
  for (const track of playable) player.artists.set(track.path, track.album_artist || track.artists || "");
  if (!player.info) {
    playQueue(paths, 0, "queue", player.artists);
  } else {
    const at = next ? player.index + 1 : player.queue.length;
    player.queue.splice(at, 0, ...paths);
    if (player.unshuffled) {
      // Back in order, they stay where they were put: after this track, or at the end
      const place = next ? player.unshuffled.indexOf(player.queue[player.index]) + 1 : player.unshuffled.length;
      player.unshuffled.splice(place || player.unshuffled.length, 0, ...paths);
    }
    queueChanged();
  }
  const title = playable[0].title || trackMeta(paths[0]).title;
  announce(playable.length === 1
    ? t(next ? "«{title}» заиграет следующим" : "«{title}» в очереди", { title })
    : t(next ? "Заиграют следующими: {count}" : "Добавлено в очередь: {count}", { count: playable.length }));
}

// After any change to the queue the next track is loaded again, unless the
// last one is still fading out of the spare element (it loads when it is done)
function queueChanged() {
  cancelHandover();
  if (!player.fading) {
    clearSpare();
    loadAhead();
  }
  renderPlayer();
  renderQueue();
}

function moveInQueue(from, to) {
  const [path] = player.queue.splice(from, 1);
  player.queue.splice(to, 0, path);
  if (from === player.index) player.index = to;
  else if (from < player.index && to >= player.index) player.index -= 1;
  else if (from > player.index && to <= player.index) player.index += 1;
  queueChanged();
}

function removeFromQueue(index) {
  if (index === player.index) return;
  const [path] = player.queue.splice(index, 1);
  if (index < player.index) player.index -= 1;
  const kept = player.unshuffled?.indexOf(path) ?? -1;
  if (kept >= 0) player.unshuffled.splice(kept, 1);
  queueChanged();
}

function clearUpcoming() {
  const later = new Set(player.queue.splice(player.index + 1));
  if (player.unshuffled) player.unshuffled = player.unshuffled.filter((path) => !later.has(path) || player.queue.includes(path));
  queueChanged();
}

function bindQueue() {
  const list = $("#queue-list");
  $("#player-queue").addEventListener("click", () => toggleQueue());
  $("#queue-close").addEventListener("click", () => toggleQueue(false));
  $("#queue-clear").addEventListener("click", clearUpcoming);
  $("#queue-keep").addEventListener("click", () => {
    keepAsPlaylist(player.queue.map(trackMeta), t("Очередь от {date}", { date: new Date().toLocaleDateString(LANGUAGE) }));
  });
  list.addEventListener("click", (event) => {
    const row = event.target.closest(".queue-row");
    if (!row) return;
    const index = [...list.children].indexOf(row);
    if (event.target.closest(".queue-remove")) {
      removeFromQueue(index);
    } else if (index !== player.index) {
      player.index = index;
      loadTrack();
    }
  });
  sortable(list, ".queue-row", moveInQueue);
}

function toggleQueue(open = $("#queue-panel").hidden) {
  const panel = $("#queue-panel");
  if (open === !panel.hidden) return;
  panel.hidden = !open;
  $("#player-queue").setAttribute("aria-pressed", String(open));
  if (!open) return;
  toggleEqualizer(false);
  placeFloat(panel);
  renderQueue();
  $(".queue-row.current", panel)?.scrollIntoView({ block: "center" });
}

function renderQueue() {
  const panel = $("#queue-panel");
  if (panel.hidden) return;
  if (!player.info) {
    toggleQueue(false);
    return;
  }
  const rows = player.queue.map((path, index) => {
    const track = trackMeta(path);
    const row = document.createElement("li");
    row.className = `queue-row${index < player.index ? " played" : index === player.index ? " current" : ""}`;
    row.draggable = true;
    row.innerHTML = `<svg class="icon sm grip" aria-hidden="true"><use href="#i-grip"/></svg>
      <span class="queue-names"><span class="queue-title"></span><span class="queue-artist"></span></span>
      <span class="queue-time"></span>`;
    $(".queue-title", row).textContent = track.title;
    $(".queue-artist", row).textContent = track.artists || track.album_artist || "";
    $(".queue-time", row).textContent = track.duration ? formatDuration(track.duration) : "";
    if (index !== player.index) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-btn queue-remove";
      remove.title = t("Убрать из очереди");
      remove.setAttribute("aria-label", remove.title);
      remove.innerHTML = `<svg class="icon sm" aria-hidden="true"><use href="#i-x"/></svg>`;
      row.append(remove);
    }
    return row;
  });
  $("#queue-list").replaceChildren(...rows);
  const upcoming = player.queue.slice(player.index + 1);
  const seconds = upcoming.reduce((total, path) => total + (trackMeta(path).duration || 0), 0);
  $("#queue-count").textContent = upcoming.length
    ? [t("дальше {count} {trackWord}", { count: upcoming.length, trackWord: plural(upcoming.length, "трек", "трека", "треков") }),
      formatLength(seconds)].join(" · ")
    : t("дальше ничего");
  $("#queue-clear").disabled = !upcoming.length;
}

// Rows put in another order by dragging them: the row goes where the line shows
function sortable(list, selector, move) {
  let dragged = null;
  const unmark = () => { for (const row of $$(".drop-before, .drop-after", list)) row.classList.remove("drop-before", "drop-after"); };
  list.addEventListener("dragstart", (event) => {
    const row = event.target.closest?.(selector);
    if (!row?.draggable) return;
    dragged = row;
    row.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", "");
  });
  list.addEventListener("dragend", () => {
    dragged?.classList.remove("dragging");
    dragged = null;
    unmark();
  });
  list.addEventListener("dragover", (event) => {
    if (!dragged) return;
    event.preventDefault();
    const row = event.target.closest?.(selector);
    if (!row || row === dragged) return;
    const box = row.getBoundingClientRect();
    unmark();
    row.classList.add(event.clientY > box.top + box.height / 2 ? "drop-after" : "drop-before");
  });
  list.addEventListener("drop", (event) => {
    if (!dragged) return;
    event.preventDefault();
    event.stopPropagation(); // the window's own drop takes links and lists of links
    const target = $(".drop-before, .drop-after", list);
    const after = Boolean(target?.classList.contains("drop-after"));
    unmark();
    if (!target) return;
    const rows = [...$$(selector, list)];
    const from = rows.indexOf(dragged);
    let to = rows.indexOf(target) + (after ? 1 : 0);
    if (from < to) to -= 1;
    if (from >= 0 && from !== to) move(from, to);
  });
}

/* A track's own menu: the queue, the playlists, its album */

function bindTrackMenu() {
  for (const list of [$(".album-list", $("#album-page")), $("#library-tracks")]) {
    list.addEventListener("contextmenu", onTrackContextMenu);
  }
  $("#track-menu").addEventListener("click", onTrackMenuClick);
  $("#playlist-menu").addEventListener("click", onPlaylistMenuClick);
  for (const menu of [$("#track-menu"), $("#playlist-menu"), $("#library-menu")]) {
    menu.addEventListener("keydown", onMenuKey);
  }
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#track-menu")) closeMenu($("#track-menu"));
    if (!event.target.closest("#playlist-menu")) closeMenu($("#playlist-menu"));
  });
  window.addEventListener("blur", () => {
    closeMenu($("#track-menu"));
    closeMenu($("#playlist-menu"));
  });
}

// Up and down through a menu's buttons, Escape out of it
function onMenuKey(event) {
  const menu = event.currentTarget;
  if (event.key === "Escape") {
    event.preventDefault();
    if (menu.id === "library-menu") closeLibraryMenu();
    else closeMenu(menu);
    return;
  }
  const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
  if (!step) return;
  event.preventDefault();
  const buttons = [...$$("button:not([hidden])", menu)];
  buttons[(buttons.indexOf(document.activeElement) + step + buttons.length) % buttons.length]?.focus();
}

function openMenuAt(menu, x, y) {
  menu.hidden = false;
  const box = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - box.width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - box.height - 8))}px`;
  $("button:not([hidden])", menu)?.focus();
}

function closeMenu(menu) {
  menu.hidden = true;
}

// The track a row stands for, with what its page knows of it
function rowTrack(row, playlist, index) {
  const path = row.dataset.path;
  if (playlist) return playlist.tracks[index];
  if (row.closest("#album-page")) return ($("#album-page").tracks || []).find((track) => track.path === path) || trackMeta(path);
  return libraryTrack(path) || trackMeta(path);
}

function onTrackContextMenu(event) {
  const row = event.target.closest(".album-track, .track-item");
  if (!row) return;
  const page = $("#album-page");
  const onPage = Boolean(row.closest("#album-page"));
  const playlist = onPage && page.dataset.kind === "playlist" ? page.playlist : null;
  if (!row.dataset.path && !playlist) return; // an album's missing track has no file to act on
  event.preventDefault();
  const index = [...$$(".album-list .album-track", page)].indexOf(row);
  const track = rowTrack(row, playlist, index);
  if (!track) return;
  state.trackMenu = { tracks: [track], row, playlist, index };
  const menu = $("#track-menu");
  const gone = Boolean(track.missing);
  for (const action of ["next", "queue", "playlist", "open"]) $(`[data-action=${action}]`, menu).hidden = gone;
  $("[data-action=album]", menu).hidden = gone || (onPage && !playlist);
  $("[data-action=remove]", menu).hidden = !playlist || playlist.smart;
  $(".menu-divider", menu).hidden = gone;
  openMenuAt(menu, event.clientX, event.clientY);
}

function onTrackMenuClick(event) {
  const action = event.target.closest("[data-action]")?.dataset.action;
  const menu = state.trackMenu;
  if (!action || !menu) return;
  closeMenu($("#track-menu"));
  const [track] = menu.tracks;
  if (action === "next" || action === "queue") enqueue(menu.tracks, action === "next");
  else if (action === "playlist") openPlaylistMenu(event.clientX, event.clientY, menu.tracks);
  else if (action === "open") api().open_folder(track.path);
  else if (action === "remove") removeFromPlaylist(menu.index);
  else if (action === "album") openTrackAlbum(track);
}

// The album a track belongs to: the library's entry for its folder, or for the file itself
async function openTrackAlbum(track) {
  if (state.library.stale && !state.library.items.length) await loadLibrary();
  const folder = track.path.replace(/[\\/][^\\/]*$/, "");
  const item = itemByPath(track.entry || "") || itemByPath(folder) || itemByPath(track.path)
    || state.library.items.find((entry) => track.path.startsWith(`${entry.path}\\`) || track.path.startsWith(`${entry.path}/`));
  if (!item) {
    announce(t("Альбома этого трека нет в библиотеке"));
    return;
  }
  if (state.view !== "library") showView("library");
  if (state.library.pages.length) await closePage({ instant: true });
  openAlbum(item, null, track.path);
}

// Several albums picked in the library: their tracks, album by album, in order
async function itemsTracks(items) {
  const tracks = [];
  for (const item of items) {
    let data;
    try {
      data = await api().album(item.path);
    } catch {
      continue;
    }
    const sorted = [...data.tracks].sort((a, b) => (a.disc || 1) - (b.disc || 1) || (a.number || 999) - (b.number || 999));
    tracks.push(...sorted.map((track) => ({ ...track, album: track.album || item.title,
                                            album_artist: track.album_artist || item.artist })));
  }
  return tracks;
}

/* Playlists */

async function loadPlaylists(force = false) {
  const list = state.playlists;
  if (!force && !list.stale && list.items.length) return list.items;
  const token = ++list.token;
  list.loading = true;
  let items;
  try {
    items = await api().playlists();
  } catch (error) {
    console.error(error);
    items = list.items;
  }
  if (token !== list.token) return list.items;
  Object.assign(list, { items, stale: false, loading: false });
  if (state.view === "library" && state.library.tab === "playlists") renderLibrary();
  return items;
}

function playlistName(entry) {
  if (!entry.smart) return entry.name;
  return t({ recent: "Недавно играло", top: "Часто слушаю" }[entry.id] || entry.name);
}

function playlistCounts(entry) {
  const count = entry.count ?? entry.tracks?.length ?? 0;
  return [t("{tracks} {trackWord}", { tracks: count, trackWord: plural(count, "трек", "трека", "треков") }),
    entry.duration ? formatLength(entry.duration) : ""].filter(Boolean).join(" · ");
}

function renderPlaylists(enter) {
  const list = state.playlists;
  if (list.stale && !list.loading) loadPlaylists();
  const needle = libraryQuery();
  const matches = (entry) => !needle || playlistName(entry).toLocaleLowerCase().includes(needle);
  // What was listened to comes first, once there is some of it
  const smart = list.items.filter((entry) => entry.smart && entry.count && matches(entry));
  const own = sortBy(list.items.filter((entry) => !entry.smart && matches(entry)), PLAYLIST_SORTS,
                     state.library.sorts.playlists, (a, b) => COLLATOR.compare(a.name, b.name));
  const box = $("#library-playlists");
  box.replaceChildren(...[...smart, ...own].map((entry, index) => createPlaylistCard(entry, index)));
  box.querySelector(".card")?.setAttribute("tabindex", "0");
  playEntrance(box, enter);
  return smart.length + own.length;
}

function createPlaylistCard(entry, index) {
  const card = $("#card-template").content.firstElementChild.cloneNode(true);
  card.classList.add("playlist-card");
  card.classList.toggle("smart", entry.smart);
  card.removeAttribute("aria-selected");
  card.setAttribute("role", "button");
  card.dataset.playlist = entry.id;
  card.style.setProperty("--i", Math.min(index, 24));
  $(".card-title", card).textContent = playlistName(entry);
  $(".card-title", card).title = playlistName(entry);
  $(".card-sub", card).textContent = playlistCounts(entry);
  $(".card-cover use", card).setAttribute("href", entry.id === "recent" ? "#i-history" : entry.id === "top" ? "#i-trend" : "#i-playlist");
  if (entry.cover) loadCover($(".card-cover", card), entry.cover);
  return card;
}

function bindPlaylists() {
  const box = $("#library-playlists");
  box.addEventListener("click", (event) => {
    const card = event.target.closest(".card");
    if (card) openPlaylist(card.dataset.playlist, card);
  });
  box.addEventListener("keydown", onCardsKey);
  $("#playlist-new").addEventListener("click", async () => {
    const name = await askName({ title: t("Новый плейлист"), value: nextPlaylistName(), action: t("Создать") });
    if (!name) return;
    const entry = await api().save_playlist(null, name, []);
    state.playlists.stale = true;
    await loadPlaylists(true);
    openPlaylist(entry.id);
  });
  $("#playlist-import").addEventListener("click", async () => {
    const entry = await api().import_playlist();
    if (!entry) return;
    state.playlists.stale = true;
    await loadPlaylists(true);
    announce(t("Плейлист «{name}»: {count}", { name: entry.name, count: entry.count }));
    openPlaylist(entry.id);
  });
  sortable($(".album-list", $("#album-page")), ".album-track", (from, to) => {
    const data = $("#album-page").playlist;
    if (!data || data.smart || $("#album-page").dataset.kind !== "playlist") return;
    const [track] = data.tracks.splice(from, 1);
    data.tracks.splice(to, 0, track);
    savePlaylist(data);
  });
}

function nextPlaylistName() {
  const taken = new Set(state.playlists.items.map((entry) => entry.name));
  for (let number = 1; ; number += 1) {
    const name = t("Плейлист {number}", { number });
    if (!taken.has(name)) return name;
  }
}

async function openPlaylist(id, source = null) {
  const library = state.library;
  const card = state.playlists.items.find((entry) => entry.id === id);
  library.pages.push({ kind: "playlist", id, source, item: { title: card ? playlistName(card) : "" } });
  const token = ++library.pageToken;
  const page = $("#album-page");
  page.dataset.kind = "playlist";
  page.playlist = null;
  page.tracks = [];
  $(".album-title", page).textContent = card ? playlistName(card) : "";
  $(".album-sub", page).textContent = card ? playlistCounts(card) : "";
  $(".album-list", page).replaceChildren();
  fillPageCover(page, card?.cover || "", "#i-playlist");
  renderPageBack();
  showPage("playlist");
  riseIn([$("#page-back"), ...$(".album-info", page).children]);
  if (!reduceMotion()) {
    $(".album-cover", page).animate([{ opacity: 0, transform: "scale(0.92)" }, { opacity: 1, transform: "none" }],
                                    { duration: 300, easing: EMPHASIZED });
  }
  $("#page-back").focus({ preventScroll: true });
  let data = null;
  try {
    data = await api().playlist(id);
  } catch (error) {
    console.error(error);
  }
  if (token !== library.pageToken) return;
  if (!data) {
    closePage();
    return;
  }
  fillPlaylistPage(data);
}

// The album page's cover box, for a page that may have no picture: its icon then
function fillPageCover(page, src, icon) {
  const cover = $(".album-cover", page);
  $("use", cover).setAttribute("href", icon);
  const image = $("img", cover);
  image.hidden = true;
  image.removeAttribute("src");
  const owner = state.library.pageToken;
  if (src) {
    loadCover(cover, src);
    showBackdrop(page, src);
    tintPage(src, owner);
  } else {
    showBackdrop(page, "");
    tintPage(null, owner);
  }
}

function fillPlaylistPage(data) {
  const page = $("#album-page");
  const top = state.library.pages[state.library.pages.length - 1];
  if (top?.kind === "playlist") top.item.title = playlistName(data);
  page.playlist = data;
  page.tracks = data.tracks;
  page.dataset.path = `playlist:${data.id}`;
  $(".album-title", page).textContent = playlistName(data);
  const what = data.smart ? t(data.id === "recent" ? "По последнему прослушиванию" : "По числу прослушиваний") : t("Плейлист");
  $(".album-sub", page).textContent = [what, playlistCounts(data)].join(" · ");
  if ($("img", $(".album-cover", page)).getAttribute("src") !== data.cover) {
    fillPageCover(page, data.cover, data.id === "recent" ? "#i-history" : data.id === "top" ? "#i-trend" : "#i-playlist");
  }
  const playable = data.tracks.some((track) => !track.missing);
  for (const action of ["play", "shuffle", "export"]) $(`[data-page-action=${action}]`, page).disabled = !playable;
  $("[data-page-action=rename]", page).hidden = data.smart;
  $("[data-page-action=delete]", page).hidden = data.smart;
  $("[data-page-action=keep]", page).hidden = !data.smart || !data.tracks.length;
  $(".album-head .artists", page).textContent = t("Исполнитель · альбом");
  const rows = data.tracks.map((track, index) => {
    const row = createAlbumTrack({ ...track, number: index + 1 }, { artist: "" });
    const artists = $(".artists", row);
    artists.textContent = [track.artists || track.album_artist, track.album].filter(Boolean).join(" · ");
    if (data.id === "top") {
      artists.textContent = [t("{count} {timesWord}", { count: track.plays, timesWord: plural(track.plays, "раз", "раза", "раз") }),
        artists.textContent].filter(Boolean).join(" · ");
    }
    row.draggable = !data.smart;
    return row;
  });
  if (!rows.length) {
    const note = document.createElement("div");
    note.className = "album-note";
    note.textContent = t(data.smart ? "Здесь появятся треки, когда вы их послушаете"
      : "Пока пусто. Треки добавляются правым щелчком в альбоме, во вкладке «Треки» или в библиотеке");
    rows.push(note);
  }
  $(".album-list", page).replaceChildren(...rows);
  markPlayingRows();
}

async function savePlaylist(data) {
  const saved = await api().save_playlist(data.id, data.name, data.tracks);
  Object.assign(data, { count: saved.count, duration: saved.duration, cover: saved.cover });
  state.playlists.stale = true;
  loadPlaylists(true);
  if ($("#album-page").playlist === data) fillPlaylistPage(data);
}

function removeFromPlaylist(index) {
  const data = $("#album-page").playlist;
  if (!data || data.smart || index < 0 || index >= data.tracks.length) return;
  data.tracks.splice(index, 1);
  savePlaylist(data);
}

function playPlaylistPage(start, shuffled = false) {
  const data = $("#album-page").playlist;
  if (!data) return;
  const tracks = data.tracks.filter((track) => !track.missing);
  if (!tracks.length) return;
  rememberTracks(tracks);
  if (shuffled && !player.shuffle) {
    player.shuffle = true;
    rememberPlayer({ shuffle: true });
    renderPlayerModes();
  }
  const index = shuffled ? Math.floor(Math.random() * tracks.length)
    : Math.max(0, tracks.findIndex((track) => track.path === start));
  playQueue(tracks.map((track) => track.path), index, "playlist", trackArtists(tracks));
}

async function onPlaylistAction(action) {
  const data = $("#album-page").playlist;
  if (!data) return;
  if (action === "play") playPlaylistPage(null);
  else if (action === "shuffle") playPlaylistPage(null, true);
  else if (action === "export") {
    const path = await api().export_playlist(data.id);
    if (path) announce(t("Сохранено: {path}", { path }));
  } else if (action === "keep") {
    keepAsPlaylist(data.tracks, playlistName(data));
  } else if (action === "rename") {
    const name = await askName({ title: t("Название плейлиста"), value: data.name, action: t("Сохранить") });
    if (!name || name === data.name) return;
    data.name = name;
    savePlaylist(data);
  } else if (action === "delete") {
    const sure = await askConfirm({ title: t("Удалить плейлист «{name}»?", { name: data.name }),
                                    text: t("Сами треки останутся в библиотеке"), action: t("Удалить") });
    if (!sure) return;
    await api().delete_playlist(data.id);
    state.playlists.stale = true;
    await loadPlaylists(true);
    closePage();
  }
}

async function keepAsPlaylist(tracks, name) {
  const chosen = await askName({ title: t("Сохранить как плейлист"), value: name, action: t("Сохранить") });
  if (!chosen) return;
  const entry = await api().save_playlist(null, chosen, tracks.filter((track) => track?.path));
  state.playlists.stale = true;
  loadPlaylists(true);
  announce(t("Плейлист «{name}»: {count}", { name: entry.name, count: entry.count }));
}

// Which playlist the tracks go to: one of one's own, or a new one
async function openPlaylistMenu(x, y, tracks) {
  const menu = $("#playlist-menu");
  menu.tracks = tracks;
  const own = (await loadPlaylists()).filter((entry) => !entry.smart);
  const buttons = own.map((entry) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "menuitem");
    button.dataset.playlist = entry.id;
    button.innerHTML = `<svg class="icon sm" aria-hidden="true"><use href="#i-playlist"/></svg><span class="menu-name"></span><span class="menu-note"></span>`;
    $(".menu-name", button).textContent = entry.name;
    $(".menu-note", button).textContent = String(entry.count);
    return button;
  });
  const create = document.createElement("button");
  create.type = "button";
  create.setAttribute("role", "menuitem");
  create.dataset.playlist = "";
  create.innerHTML = `<svg class="icon sm" aria-hidden="true"><use href="#i-plus"/></svg><span></span>`;
  $("span", create).textContent = t("Новый плейлист…");
  const divider = document.createElement("div");
  divider.className = "menu-divider";
  menu.replaceChildren(...buttons, ...(buttons.length ? [divider] : []), create);
  openMenuAt(menu, x, y);
}

async function onPlaylistMenuClick(event) {
  const button = event.target.closest("[data-playlist]");
  const menu = $("#playlist-menu");
  if (!button) return;
  closeMenu(menu);
  const tracks = (menu.tracks || []).filter((track) => track?.path);
  if (!tracks.length) return;
  let entry;
  if (button.dataset.playlist) {
    entry = await api().add_to_playlist(button.dataset.playlist, tracks);
  } else {
    const name = await askName({ title: t("Новый плейлист"), value: nextPlaylistName(), action: t("Создать") });
    if (!name) return;
    entry = await api().save_playlist(null, name, tracks);
  }
  if (!entry) return;
  state.playlists.stale = true;
  loadPlaylists(true);
  const shown = $("#album-page").playlist;
  if (shown?.id === entry.id && $("#album-page").dataset.kind === "playlist") {
    api().playlist(entry.id).then((data) => { if (data && $("#album-page").playlist === shown) fillPlaylistPage(data); });
  }
  announce(t("Добавлено в «{name}»: {count}", { name: entry.name, count: tracks.length }));
}

// A name asked for in the dialog the confirmations use, with a field in it
async function askName({ title, value = "", action }) {
  const field = $(".confirm-field", $("#confirm"));
  const input = $("#confirm-input");
  field.hidden = false;
  input.value = value;
  const onEnter = (event) => {
    if (event.key === "Enter" && input.value.trim()) {
      event.preventDefault();
      $("#confirm-ok").click();
    }
  };
  input.addEventListener("keydown", onEnter);
  const asked = askConfirm({ title, text: "", action, danger: false });
  input.focus();
  input.select();
  const agreed = await asked;
  input.removeEventListener("keydown", onEnter);
  field.hidden = true;
  const name = input.value.replace(/\s+/g, " ").trim();
  return agreed && name ? name : null;
}

/* Settings: the player's card */

function bindPlayerSettings() {
  bindSwitch($("#gapless"), (on) => updateSettings({ gapless: on }));
  const crossfade = $("#crossfade");
  crossfade.addEventListener("input", () => {
    fillRange(crossfade);
    renderCrossfade(Number(crossfade.value));
  });
  crossfade.addEventListener("change", () => updateSettings({ crossfade: Number(crossfade.value) }));
  $("#lastfm-connect").addEventListener("click", onLastfmClick);
  $("#listenbrainz-connect").addEventListener("click", onListenbrainzClick);
  $("#listenbrainz-token").addEventListener("keydown", (event) => {
    if (event.key === "Enter") onListenbrainzClick();
  });
}

function renderPlayerSettings() {
  const settings = state.settings;
  syncSwitch($("#gapless"), settings.gapless !== false);
  const crossfade = $("#crossfade");
  if (document.activeElement !== crossfade) crossfade.value = settings.crossfade || 0;
  fillRange(crossfade);
  renderCrossfade(Number(crossfade.value));
  renderEqualizer();
}

function renderCrossfade(seconds) {
  $("#crossfade-value").textContent = seconds ? t("{seconds} с", { seconds }) : t("нет");
}

async function loadScrobbleAccounts() {
  try {
    state.scrobble = await api().scrobble_accounts();
  } catch (error) {
    console.error(error);
  }
  renderScrobbleAccounts();
}

function renderScrobbleAccounts() {
  const accounts = state.scrobble || {};
  $("#lastfm-setting").hidden = !accounts.lastfm_ready;
  const waiting = accounts.waiting ? t(" · ждут отправки: {count}", { count: accounts.waiting }) : "";
  const lastfm = $("#lastfm-connect");
  if (!state.lastfmWaiting) {
    $("span", lastfm).textContent = t(accounts.lastfm ? "Отключить" : "Подключить");
    lastfm.disabled = false;
  }
  $("#lastfm-state").textContent = accounts.lastfm
    ? t("Прослушивания уходят в профиль {name}", { name: accounts.lastfm }) + waiting
    : t("Отправляет прослушанные треки в ваш профиль Last.fm. Подключение откроет страницу Last.fm, где нужно разрешить доступ");
  const listenbrainz = $("#listenbrainz-connect");
  $("span", listenbrainz).textContent = t(accounts.listenbrainz ? "Отключить" : "Подключить");
  $("#listenbrainz-field").hidden = Boolean(accounts.listenbrainz);
  $("#listenbrainz-state").textContent = accounts.listenbrainz
    ? t("Прослушивания уходят в профиль {name}", { name: accounts.listenbrainz }) + waiting
    : t("Открытая замена Last.fm. Токен — на странице listenbrainz.org/settings");
}

async function onLastfmClick() {
  const button = $("#lastfm-connect");
  if (state.scrobble?.lastfm) {
    state.scrobble = await api().disconnect_scrobbler("lastfm");
    renderScrobbleAccounts();
    return;
  }
  const started = await api().connect_lastfm();
  if (started.error) {
    announce(started.error);
    return;
  }
  // Last.fm's page is open in the browser; the program asks until access is allowed
  state.lastfmWaiting = true;
  button.disabled = true;
  $("span", button).textContent = t("Жду разрешения на Last.fm…");
  for (let tries = 0; tries < 60 && state.lastfmWaiting; tries += 1) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const answer = await api().lastfm_connected().catch(() => ({ name: "" }));
    if (answer.name) {
      announce(t("Last.fm подключён: {name}", { name: answer.name }));
      break;
    }
  }
  state.lastfmWaiting = false;
  loadScrobbleAccounts();
}

async function onListenbrainzClick() {
  if (state.scrobble?.listenbrainz) {
    state.scrobble = await api().disconnect_scrobbler("listenbrainz");
    renderScrobbleAccounts();
    return;
  }
  const input = $("#listenbrainz-token");
  const token = input.value.trim();
  if (!token) {
    input.focus();
    return;
  }
  const button = $("#listenbrainz-connect");
  button.disabled = true;
  const answer = await api().connect_listenbrainz(token).catch((error) => ({ error: String(error) }));
  button.disabled = false;
  if (answer.error) {
    announce(answer.error);
    input.closest(".field").classList.add("invalid");
    return;
  }
  input.value = "";
  input.closest(".field").classList.remove("invalid");
  announce(t("ListenBrainz подключён: {name}", { name: answer.name }));
  loadScrobbleAccounts();
}

/* Now playing */

function openNowPlaying() {
  if (!player.info) return;
  if (state.view !== "now") player.previousView = state.view;
  showView("now");
  renderNowPlaying();
  scrollToLyric(true);
}

function closeNowPlaying() {
  showView(player.previousView || "library");
}

function toggleNowPlaying() {
  if (state.view === "now") closeNowPlaying();
  else openNowPlaying();
}

function toggleFullscreen() {
  player.fullscreen = !player.fullscreen;
  api().fullscreen();
  const button = $("#player-full");
  $("use", button).setAttribute("href", player.fullscreen ? "#i-mi-full-exit" : "#i-mi-full");
  button.title = t(player.fullscreen ? "Выйти из полноэкранного режима" : "Во весь экран");
  button.setAttribute("aria-label", button.title);
}

function renderNowPlaying() {
  const view = $("#view-now");
  const info = player.info;
  $("#player-lyrics").setAttribute("aria-pressed", String(state.view === "now"));
  if (!info) return;
  $(".now-title", view).textContent = info.title;
  $(".now-artist", view).textContent = info.artists || info.album_artist || "";
  $(".now-sub", view).textContent = [info.album, info.year].filter(Boolean).join(" · ");
  // The file's make, a label per fact: the format first, then how it sounds
  $(".now-format", view).replaceChildren(...[
    (info.format || "").toUpperCase(),
    info.bitrate ? t("{kbps} кбит/с", { kbps: info.bitrate }) : "",
    info.sample_rate ? t("{khz} кГц", { khz: (info.sample_rate / 1000).toLocaleString(LANGUAGE, { maximumFractionDigits: 1 }) }) : "",
    { 1: t("Моно"), 2: t("Стерео") }[info.channels] || "",
  ].filter(Boolean).map((fact) => {
    const label = document.createElement("span");
    label.textContent = fact;
    return label;
  }));
  setPlayerPicture($(".now-cover", view), info.cover);
  const backdrop = $(".now-backdrop", view);
  if (backdrop.dataset.src !== info.cover) {
    const cover = info.cover || "";
    backdrop.dataset.src = cover;
    backdrop.hidden = true;
    if (cover) {
      backdrop.addEventListener("load", () => { backdrop.hidden = false; }, { once: true });
      backdrop.src = cover;
    }
    // The wash in the cover's own colour, worked out as the album page's band is
    (cover ? pictureTint(cover).catch(() => null) : Promise.resolve(null)).then((tint) => {
      if (backdrop.dataset.src !== cover) return; // the next track came meanwhile
      // A grey or white cover has no colour to give: the wash is a neutral one then
      view.style.setProperty("--tint-a", "1");
      view.style.setProperty("--tint-h", tint ? tint.hue : 0);
      view.style.setProperty("--tint-s", tint ? `${tint.saturation}%` : "0%");
    });
  }
}

async function loadLyrics(path, token) {
  const box = $("#now-lyrics");
  player.lines = [];
  player.current = -1;
  box.replaceChildren(lyricsNote(t("Ищем текст…")));
  let words;
  try {
    words = await api().lyrics(path);
  } catch (error) {
    words = { synced: "", plain: "" };
  }
  if (token !== player.token) return;
  const lines = parseLrc(words.synced || "");
  if (lines.length) {
    player.lines = lines.map((line) => {
      const node = document.createElement("button");
      node.type = "button";
      node.className = "lyric-line";
      node.dataset.time = line.time;
      node.textContent = line.text || "♪";
      return { ...line, node };
    });
    box.replaceChildren(...player.lines.map((line) => line.node));
    highlightLyric();
    scrollToLyric(true);
  } else if (words.plain) {
    const text = document.createElement("p");
    text.className = "lyrics-plain";
    text.textContent = words.plain;
    box.replaceChildren(text);
    box.scrollTop = 0;
  } else {
    box.replaceChildren(lyricsNote(t("Текста для этой песни не нашлось")));
  }
}

function lyricsNote(text) {
  const note = document.createElement("p");
  note.className = "lyrics-none";
  note.textContent = text;
  return note;
}

// "[01:23.45] words", a line may carry several times; "[ar: …]" and the like are skipped
function parseLrc(text) {
  const lines = [];
  for (const raw of text.split(/\r?\n/)) {
    const times = [...raw.matchAll(/\[(\d+):(\d+(?:[.:]\d+)?)\]/g)];
    if (!times.length) continue;
    const words = raw.replace(/\[[^\]]*\]/g, "").trim();
    for (const [, minutes, seconds] of times) {
      lines.push({ time: Number(minutes) * 60 + Number(seconds.replace(":", ".")), text: words });
    }
  }
  return lines.sort((a, b) => a.time - b.time);
}

function highlightLyric() {
  const { lines, audio } = player;
  if (!lines.length) return;
  const now = audio.currentTime + 0.25; // a line lights up as it is sung, not after
  let index = -1;
  for (let i = 0; i < lines.length && lines[i].time <= now; i++) index = i;
  if (index === player.current) return;
  player.current = index;
  lines.forEach((line, i) => {
    line.node.classList.toggle("is-current", i === index);
    line.node.classList.toggle("is-past", i < index);
  });
  scrollToLyric(false);
}

// The line being sung sits a third of the way down, and only the words move:
// scrolling the line into view would drag the whole screen, cover and all, with it
function scrollToLyric(instant) {
  if (state.view !== "now") return;
  const box = $("#now-lyrics");
  const line = player.lines[player.current];
  const top = line ? line.node.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop : 0;
  box.scrollTo({ top: Math.max(0, top - box.clientHeight / 3),
                 behavior: instant || reduceMotion() ? "auto" : "smooth" });
}

// Tidying up: older files get the genre, year, cover, lyrics and tags they
// lack, looked up again by the link an album keeps or by its names. Nothing is
// downloaded again, and nothing a file already says is changed.
async function tidyLibrary(items, { ask = false } = {}) {
  if (state.tidy.running) {
    api().stop_tidy(); // the entry in hand is finished, the rest are left
    return;
  }
  const entries = items.map(({ path, artist, title }) => ({ path, artist, title }));
  if (!entries.length) return;
  if (ask) {
    const ok = await askConfirm({
      title: t("Дописать теги во всю библиотеку?"),
      text: t("Жанры, годы, обложки и тексты допишутся туда, где их нет: {count} {recordWord}. "
              + "Уже записанное не меняется, ничего не скачивается заново.",
              { count: entries.length, recordWord: plural(entries.length, "запись", "записи", "записей") }),
      action: t("Дописать"),
      danger: false,
    });
    if (!ok) return;
  }
  if (!await api().tidy(entries, state.settings)) return;
  clearTimeout(state.tidy.timer);
  Object.assign(state.tidy, { running: true, done: 0, total: entries.length, title: entries[0].title,
                              paths: new Set(entries.map((entry) => entry.path)), note: "" });
  renderTidy();
}

function onTidyEvent(event) {
  const tidy = state.tidy;
  if (event.state === "running") {
    Object.assign(tidy, { running: true, done: event.done, total: event.total, title: event.title });
  } else {
    tidy.running = false;
    tidy.note = tidySummary(event);
    announce(tidy.note);
    clearTimeout(tidy.timer);
    tidy.timer = setTimeout(() => { tidy.note = ""; renderStatus(); }, 30000);
    loadLibrary().then(refreshAlbumPage);
  }
  renderTidy();
}

function tidySummary({ files, covers, lyrics, missed, stopped }) {
  const parts = [];
  if (files || covers) {
    parts.push(t("Дописано: файлов {files}, обложек {covers}, текстов {lyrics}", { files, covers, lyrics }));
  }
  if (missed.length) {
    const names = missed.slice(0, 3).map((name) => `«${name}»`).join(", ") + (missed.length > 3 ? "…" : "");
    parts.push(t("не нашлись в каталогах: {names}", { names }));
  }
  const text = parts.join(" · ") || t("Дописывать нечего: всё уже на месте");
  return stopped ? t("Остановлено. {text}", { text }) : text;
}

function renderTidy() {
  const { running, done, total, paths, gaps } = state.tidy;
  const button = $("#library-tidy");
  button.hidden = !running && !gaps.size; // nothing to fill in, nothing to offer
  // An icon alone: how far it has come is said above the list
  $("use", button).setAttribute("href", running ? "#i-stop" : "#i-tag");
  button.title = running
    ? t("Остановить после текущей записи · {done} из {total}", { done: Math.min(done + 1, total), total })
    : t("Дописать жанры, обложки, тексты и недостающие теги, ничего не скачивая заново");
  button.setAttribute("aria-label", running ? t("Остановить") : t("Дописать теги"));
  const page = $("#album-page");
  const onPage = $("[data-page-action=tidy]", page);
  onPage.hidden = !gaps.has(page.dataset.path) && !(running && paths.has(page.dataset.path));
  onPage.disabled = running;
  onPage.title = running && paths.has(page.dataset.path) ? t("Дописываем…")
    : t("Дописать жанры, обложки, тексты и недостающие теги, ничего не скачивая заново");
  renderStatus();
}

// The album page shows the tags, so after a tidy-up it is read again
async function refreshAlbumPage() {
  const library = state.library;
  const top = library.pages[library.pages.length - 1];
  if (!top || top.kind !== "album") return;
  const token = library.pageToken;
  let data;
  try {
    data = await api().album(top.item.path);
  } catch (error) {
    console.error(error);
    return;
  }
  if (token !== library.pageToken) return;
  const fresh = library.items.find((item) => item.path === top.item.path);
  if (fresh && fresh.cover && !top.item.cover) {
    top.item = fresh; // the tidy-up brought a cover.jpg along
    fillAlbumPage(fresh);
  }
  renderAlbumTracks(top.item, data, "");
}

async function deleteSelected() {
  const { items, selected } = state.library;
  const chosen = items.filter((item) => selected.has(item.path));
  if (!chosen.length) return;
  const size = formatSize(chosen.reduce((total, item) => total + item.size, 0));
  const what = chosen.length === 1
    ? `«${chosen[0].title}»`
    : t("{count} {recordWord}",
        { count: chosen.length, recordWord: plural(chosen.length, "запись", "записи", "записей") });
  const ok = await askConfirm({
    title: t("Удалить {what}?", { what }),
    text: t("{size} уйдёт в корзину — оттуда файлы можно вернуть.", { size }),
    action: t("Удалить"),
  });
  if (!ok) return;
  // The cards go at once, and the files follow into the trash behind them:
  // a large folder takes the shell a while, and the folders read again longer
  const library = state.library;
  const gone = new Set(chosen.map((item) => item.path));
  const inside = (path) => [...gone].some((root) => path === root || path.startsWith(`${root}\\`) || path.startsWith(`${root}/`));
  if (player.info && inside(player.info.path)) stopPlayer(); // a file being played could not be moved
  library.items = library.items.filter((item) => !gone.has(item.path));
  library.trackList.items = library.trackList.items.filter((track) => !gone.has(track.entry));
  library.artists = null;
  clearSelection();
  renderLibrary();
  markInLibrary();
  let result;
  try {
    result = await api().delete([...gone]);
  } catch (error) {
    console.error(error);
    result = { deleted: 0, failed: chosen.map((item) => item.title) };
  }
  announce(result.failed.length
    ? t("Не удалось удалить: {names}", { names: result.failed.join(", ") })
    : t("Удалено: {count}", { count: result.deleted }));
  if (result.failed.length) loadLibrary(); // what stayed comes back
}

// A window-level confirm() would be a bare system box, so the dialog is ours.
// The buttons answer directly: WebView2 does not always raise the close event.
function askConfirm({ title, text, action, danger = true }) {
  const dialog = $("#confirm");
  const yes = $("#confirm-ok");
  const no = $("#confirm-cancel");
  yes.classList.toggle("danger", danger);
  yes.classList.toggle("primary", !danger);
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  yes.textContent = action;
  return new Promise((resolve) => {
    const answer = (agreed) => {
      yes.removeEventListener("click", onYes);
      no.removeEventListener("click", onNo);
      dialog.removeEventListener("cancel", onNo);
      if (dialog.open) dialog.close();
      resolve(agreed);
    };
    const onYes = () => answer(true);
    const onNo = () => answer(false);
    yes.addEventListener("click", onYes);
    no.addEventListener("click", onNo);
    dialog.addEventListener("cancel", onNo); // Escape
    dialog.showModal();
    no.focus(); // the safe button is the one under the finger
  });
}

function createLibraryRow(item) {
  const row = $("#library-template").content.firstElementChild.cloneNode(true);
  const title = $(".title", row);
  title.textContent = item.title;
  title.title = item.path;
  $(".sub", row).textContent = item.album ? item.artist
    : [item.artist, t("трек")].filter(Boolean).join(" · ");
  $(".cell-year", row).textContent = item.year;
  $(".cell-count", row).textContent = item.tracks;
  $(".cell-size", row).textContent = formatSize(item.size);
  $(".cell-date", row).textContent = new Date(item.modified * 1000).toLocaleDateString();
  row.dataset.path = item.path;
  const open = () => openAlbum(item, row);
  row.addEventListener("dblclick", open);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter") open();
    if (event.key === " ") {
      event.preventDefault(); // the list must not scroll under the pressed row
      selectRow(item.path, { ctrlKey: true });
    }
  });
  $(".open", row).addEventListener("click", (event) => {
    event.stopPropagation();
    api().open_folder(item.path);
  });
  const again = $(".again", row);
  again.hidden = !hasMissing(item);
  again.addEventListener("click", (event) => {
    event.stopPropagation();
    downloadAgain([item]);
  });
  if (item.cover) coverObserver.observe(row);
  return row;
}

async function showLibraryCover(element) {
  const path = element.dataset.cover || element.dataset.path;
  if (!state.covers.has(path)) state.covers.set(path, api().cover(path));
  const src = await state.covers.get(path);
  if (src) loadCover($(".cover", element), src);
}

// Where every cover of the library is, asked for in one call rather than one
// per card as it scrolls into sight: the answers are what took the longest,
// and a card whose address is known already only waits for the picture itself.
async function prefetchCovers(items) {
  const wanted = items.filter((item) => item.cover && !state.covers.has(item.path)).map((item) => item.path);
  if (!wanted.length) return;
  let found = null;
  try {
    found = await api().covers(wanted);
  } catch (error) {
    return; // each card will ask for its own
  }
  if (!found) return;
  for (const path of wanted) {
    if (!state.covers.has(path)) state.covers.set(path, Promise.resolve(found[path] || ""));
  }
}

// The cover blurred behind the whole page, under the wash of its colours
function showBackdrop(page, src) {
  const backdrop = $(".album-backdrop", page);
  if (backdrop.dataset.src === src) return;
  backdrop.dataset.src = src;
  backdrop.hidden = true;
  backdrop.removeAttribute("src");
  if (!src) return;
  backdrop.addEventListener("load", () => { backdrop.hidden = false; }, { once: true });
  backdrop.src = src;
}

function loadCover(cover, src) {
  const image = $("img", cover);
  image.addEventListener("load", () => { image.hidden = false; }, { once: true });
  image.src = src;
}

function formatDuration(seconds) {
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function formatLength(seconds) {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return t("{minutes} мин", { minutes });
  return t("{hours} ч {minutes} мин", { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
}

/* Helpers */

function formatSize(bytes) {
  const megabytes = bytes / 1048576;
  if (megabytes < 1) return t("{value} КБ", { value: Math.max(1, Math.round(bytes / 1024)) });
  if (megabytes < 1024) return t("{value} МБ", { value: Math.round(megabytes) });
  return t("{value} ГБ",
    { value: (megabytes / 1024).toLocaleString(LANGUAGE, { maximumFractionDigits: 1 }) }); // 13.2 in English, 13,2 in Russian
}

function prettyLink(link) {
  // "track:" marks a song searched by name from an imported list; it says
  // nothing a person needs to read on the card
  return link.replace(/^track:/i, "").replace(/^https?:\/\//, "").replace(/\?si=[^&]*$/, "");
}

// plural() and t() come from i18n.js, which is loaded before this file

function announce(text) {
  $("#live").textContent = text;
}
