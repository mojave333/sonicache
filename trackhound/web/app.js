"use strict";

// Web links with or without https:// and spotify: URIs; the backend decides what it can read
const LINK_RE = /https?:\/\/[^\s"'<>]+|(?:[a-z0-9-]+\.)+(?:com|ru|fm|be|link|fi)\/[^\s"'<>]+|spotify:(?:album|track):[A-Za-z0-9]{22}/gi;

const FORMAT_HINTS = {
  mp3: "mp3 — 320 кбит/с из полной дорожки, до 20 кГц. Играет везде, включая магнитолы",
  m4a: "m4a — AAC 256 кбит/с из полной дорожки, до 20 кГц. Подходит почти всем плеерам",
  opus: "opus — дорожка YouTube как есть, без перекодирования. Понимают его не все плееры",
};

const VIEWS = ["download", "library", "queue", "settings"];

// What the backend accepts as a proxy; anything else is refused there anyway
const PROXY_RE = /^(?:https?|socks4|socks5h?):\/\/[^\s/]+$/i;

const TRACK_UI = {
  waiting: { label: "В очереди", icon: "dot", tone: "muted" },
  search: { label: "Ищем", icon: "spinner", tone: "muted" },
  download: { label: "Качаем", icon: "spinner", tone: "primary" },
  done: { label: "Готово", icon: "check", tone: "success" },
  found: { label: "Найден", icon: "check", tone: "success" },
  skip: { label: "Уже есть", icon: "check", tone: "muted" },
  missing: { label: "Не найден", icon: "x", tone: "danger" },
  error: { label: "Ошибка", icon: "alert", tone: "danger" },
  cancel: { label: "Отменён", icon: "stop", tone: "muted" },
};
const TRACK_ACTIVE = new Set(["waiting", "search", "download"]);
const TRACK_FAILED = new Set(["missing", "error"]);

const QUEUE_FILTERS = {
  all: () => true,
  active: (track) => TRACK_ACTIVE.has(track.state),
  done: (track) => ["done", "found", "skip"].includes(track.state),
  problems: (track) => TRACK_FAILED.has(track.state),
};
const QUEUE_EMPTY = {
  all: "Очередь пуста",
  active: "Сейчас ничего не качается",
  done: "Готовых треков пока нет",
  problems: "Проблемных треков нет",
};

const ACTIVE = new Set(["queued", "running"]);

// Library columns that can be sorted. Text starts ascending, numbers and dates
// descending, because that is the useful end of each.
const COLLATOR = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const LIBRARY_SORTS = {
  title: { dir: 1, value: (item) => `${item.title} ${item.artist}` },
  year: { dir: -1, value: (item) => Number(item.year) || 0 },
  tracks: { dir: -1, value: (item) => item.tracks },
  size: { dir: -1, value: (item) => item.size },
  modified: { dir: -1, value: (item) => item.modified },
};

const state = {
  settings: null,
  problems: [],
  view: "download",
  jobs: new Map(),
  tracks: [], // every track of every job, in the order the releases arrived
  orphans: [],
  dirty: new Set(),
  queueFilter: "all",
  run: null, // jobs added since the queue was last idle; the status bar sums them up
  library: {
    items: [], folder: null, stale: true, loading: false, token: 0,
    sort: { key: "modified", dir: -1 }, // newest first, the way the folder itself is read
    shown: [], // paths in the order drawn, so Shift-click knows what a range covers
    selected: new Set(),
    anchor: null,
  },
  covers: new Map(),
  update: null, // { version, url } once a newer release is published
  diagnostics: null, // log path and yt-dlp version, read once at startup
  paused: false,
};
const darkMedia = window.matchMedia("(prefers-color-scheme: dark)");
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => root.querySelectorAll(selector);
const api = () => window.pywebview.api;

const coverObserver = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    coverObserver.unobserve(entry.target);
    showLibraryCover(entry.target);
  }
}, { root: $("#library-scroll"), rootMargin: "200px" });

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
  setInterval(renderStatusBar, 1000);
  pollLoop();
  checkForUpdate();
  loadDiagnostics();
  // Only now, so the saved panel width is in place before it can be animated
  setTimeout(() => document.documentElement.classList.add("motion-ready"), 0);
}

// Where the log sits, and how old the yt-dlp inside this build is
async function loadDiagnostics() {
  const info = await api().diagnostics();
  state.diagnostics = info;
  const stale = info.ytdlp_age >= 60;
  $("#ytdlp-title").textContent = `yt-dlp ${info.ytdlp}`;
  $("#ytdlp-desc").textContent = stale
    ? t("Сборке {days} {dayWord} — YouTube за это время обычно успевает смениться. "
        + "Если загрузки перестали работать, обновите Trackhound",
        { days: info.ytdlp_age, dayWord: plural(info.ytdlp_age, "день", "дня", "дней") })
    : t("Скачиванием занимается yt-dlp; он обновляется вместе с Trackhound");
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
  syncRadios($("#replaygain"), "data-replaygain", String(settings.replaygain));
  syncRadios($("#language"), "data-language", settings.language);
  syncRadios($("#formats"), "data-format", settings.format);
  renderProfiles();
  syncRadios($("#mode-menu"), "data-dry-run", String(settings.dry_run));
  const folder = $("#folder");
  $("#folder-name").textContent = settings.folder.split(/[\\/]+/).filter(Boolean).pop() || settings.folder;
  folder.title = t("{folder}\nНажмите, чтобы выбрать другую папку", { folder: settings.folder });
  folder.setAttribute("aria-label", t("Папка для музыки: {folder}", { folder: settings.folder }));
  $("#threads").textContent = settings.threads;
  $("#cookies").value = settings.cookies_browser;
  $("#rate").value = String(settings.rate_limit);
  $("#track-name").value = settings.track_name;
  $("#folder-layout").value = settings.folder_name;
  if ($("#proxy") !== document.activeElement) $("#proxy").value = settings.proxy;
  $("#submit-label").textContent = t(settings.dry_run ? "Проверить" : "Скачать");
  $("#submit use").setAttribute("href", settings.dry_run ? "#i-search" : "#i-download");
  renderStatusBar();
}

function updateSettings(patch) {
  if (patch.folder && patch.folder !== state.settings.folder) state.library.stale = true;
  Object.assign(state.settings, patch);
  renderSettings();
  api().save_settings(state.settings);
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
    desc.textContent = [entry.service, watchStatus(entry)].filter(Boolean).join(" · ");
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
  renderStatusBar();
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
  radioGroup($("#replaygain"), "data-replaygain", (value) => updateSettings({ replaygain: value === "true" }));
  radioGroup($("#formats"), "data-format", (format) => updateSettings({ format }));
  radioGroup($("#queue-filter"), "data-filter", setQueueFilter);
  darkMedia.addEventListener("change", applyTheme);

  $("#folder").addEventListener("click", async () => {
    const folder = await api().choose_folder(state.settings.folder);
    if (folder) updateSettings({ folder });
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
  $("#paste").addEventListener("click", pasteFromClipboard);
  $("#link").addEventListener("input", clearLinkError);
  $("#form").addEventListener("submit", submitLinks);
  bindModeMenu();
  for (const button of $$(".stop")) button.addEventListener("click", stopAll);
  for (const button of $$(".pause")) button.addEventListener("click", togglePause);
  $("#clear").addEventListener("click", clearFinished);
  $("#status-problems").addEventListener("click", () => showView("settings"));
  $("#log-open").addEventListener("click", () => api().open_logs());
  $("#log-copy").addEventListener("click", copyReport);

  $("#library-filter").addEventListener("input", renderLibrary);
  $("#library-refresh").addEventListener("click", loadLibrary);
  $("#library-folder").addEventListener("click", () => api().open_folder(state.settings.folder));
  $("#library-delete").addEventListener("click", deleteSelected);
  $("#library-again").addEventListener("click", () => downloadAgain(selectedItems()));
  $("#library").addEventListener("click", onLibraryClick);
  $("#library").addEventListener("keydown", onLibraryKey);
  $("#library").addEventListener("contextmenu", onLibraryContextMenu);
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
  if (event.key === "Escape" && !$("#mode-menu").hidden) {
    setMenuOpen(false);
  } else if (event.ctrlKey && !event.altKey && !event.shiftKey && /^[1-4]$/.test(event.key)) {
    event.preventDefault();
    showView(VIEWS[Number(event.key) - 1]);
  } else if (event.key === "F5" || (event.ctrlKey && event.code === "KeyR")) {
    event.preventDefault(); // reloading the page would lose the download list
    if (state.view === "library") loadLibrary();
  } else if (event.ctrlKey && !event.altKey && !event.shiftKey && event.code === "KeyB") {
    event.preventDefault();
    toggleSidebar();
  } else if (event.ctrlKey && !event.altKey && event.code === "KeyF") {
    // the library search box is not focused automatically, so give it a shortcut
    event.preventDefault();
    if (state.view === "library") $("#library-filter").focus();
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
  if (name === "library") {
    if (state.library.stale || state.library.folder !== state.settings.folder) loadLibrary();
  } else {
    clearSelection();
  }
  renderStatusBar(); // the library speaks about itself, the other views about downloads
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
      job.result = { ok: entry.ok, skipped: entry.skipped, failed: entry.failed };
      job.summary = summaryText({ ...job.result, dry_run: job.dryRun });
      if (entry.failed) job.state = "partial";
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
  setStatusCell($(".cell-status", node), status);

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
      if (!job.total) {
        return { icon: "spinner", tone: "muted", progress: "indeterminate",
                 text: t(job.stopping ? "Останавливаем…" : "Читаем ссылку…") };
      }
      const ratio = jobProgress(job);
      const text = job.stopping ? t("Останавливаем…")
        : t("{done} из {total} · {percent}%",
            { done: job.done, total: job.total, percent: Math.floor(ratio * 100) });
      return { icon: "spinner", tone: "primary", text, progress: ratio };
    }
    case "done": {
      const { ok, skipped } = job.result;
      const text = t(job.dryRun ? "Всё найдено" : !ok && skipped ? "Уже скачано" : "Готово");
      return { icon: "check", tone: "success", text, tip: job.summary };
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

function jobProgress(job) {
  let downloading = 0;
  for (const track of job.tracks.values()) {
    if (track.state === "download") downloading += track.percent / 100;
  }
  return Math.min(1, (job.done + downloading) / job.total);
}

function setStatusCell(cell, { icon, text, tip = "", progress }) {
  const statusIcon = $(".status-icon", cell);
  $("use", statusIcon).setAttribute("href", `#i-${icon}`);
  statusIcon.classList.toggle("spin", icon === "spinner");
  $(".status-text", cell).textContent = text;
  cell.title = tip;
  const bar = $(".bar", cell);
  bar.hidden = progress === undefined;
  bar.classList.toggle("indeterminate", progress === "indeterminate");
  if (typeof progress === "number") {
    bar.style.setProperty("--p", progress);
    bar.setAttribute("aria-valuenow", String(Math.round(progress * 100)));
  }
}

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
  else if (event.type === "loudness") job.note = event.state === "running" ? t("Измеряем громкость…") : "";
  state.dirty.add(job);
}

// Events come in bursts; rows and counters are redrawn once per burst
function flushRender() {
  for (const job of state.dirty) renderJob(job);
  state.dirty.clear();
  renderChrome();
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
    job.state = event.failed > 0 ? "partial" : "done";
    if (!event.failed && !event.dry_run) setExpanded(job, false);
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

function summaryText({ ok, skipped, failed, dry_run: dryRun }) {
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
  const queueRows = [];
  for (const info of event.tracks) {
    const track = {
      job, number: multiDisc ? `${info.disc}-${info.number}` : info.number,
      title: info.title, artists: info.artists, duration: info.duration,
      state: restored ? info.state || "waiting" : "waiting", percent: 0,
      source: restored ? info.source || "" : "", text: restored ? info.text || "" : "",
    };
    track.row = createTrackRow(track, info.artists === event.artist ? "" : info.artists);
    track.queueRow = createTrackRow(track, info.artists, event.title);
    track.queueRow.addEventListener("dblclick", () => {
      showView("download");
      setExpanded(job, true, false); // no fold: the row must already be in place to scroll to
      track.row.scrollIntoView({ block: "center" });
    });
    renderTrack(track);
    job.tracks.set(info.id, track);
    rows.push(track.row);
    if (!restored) {
      state.tracks.push(track);
      queueRows.push(track.queueRow);
    }
  }
  $(".tracks", job.node).replaceChildren(...rows);
  if (!restored) $("#queue").append(...queueRows);
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
  Object.assign(track, { state: event.state, text: event.text || "", percent: event.percent || 0 });
  if (event.source) track.source = event.source;
  if (event.state !== "skip" && state.run?.jobs.has(job.id) && !state.run.workStart) state.run.workStart = Date.now();
  renderTrack(track);
}

function renderTrack(track) {
  const ui = TRACK_UI[track.state] || TRACK_UI.waiting;
  const label = t(ui.label);
  const note = trackNote(track);
  const downloading = track.state === "download";
  for (const row of [track.row, track.queueRow]) {
    row.className = `row track tone-${ui.tone}`;
    const sub = $(".sub", row);
    sub.textContent = note || sub.dataset.artists;
    sub.classList.toggle("danger", TRACK_FAILED.has(track.state));
    $(".cell-source", row).textContent = track.source;
    setStatusCell($(".cell-status", row), {
      icon: ui.icon,
      text: downloading ? `${label} ${track.percent}%` : label,
      tip: track.state === "skip" ? t("Файл уже есть в папке") : "",
      progress: downloading ? track.percent / 100 : undefined,
    });
  }
  track.queueRow.hidden = !QUEUE_FILTERS[state.queueFilter](track);
}

function trackNote({ state: name, text }) {
  switch (name) {
    case "found": return t("Найдено: {text}", { text });
    case "missing": return t("Нет в открытом доступе на YouTube Music и SoundCloud");
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
  for (const track of job.tracks.values()) track.queueRow.remove();
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

/* Queue */

function setQueueFilter(name) {
  state.queueFilter = name;
  syncRadios($("#queue-filter"), "data-filter", name);
  for (const track of state.tracks) track.queueRow.hidden = !QUEUE_FILTERS[name](track);
  renderChrome();
}

/* Window chrome: counters, badges, status bar */

function renderChrome() {
  const jobs = [...state.jobs.values()];
  const active = jobs.some((job) => ACTIVE.has(job.state));
  $("#empty").hidden = jobs.length > 0;
  $("#jobs-count").textContent = jobs.length || "";
  for (const button of $$(".stop")) button.hidden = !active;
  for (const button of $$(".pause")) button.hidden = !active;
  if (!active && state.paused) setPaused(false); // nothing left to hold back
  $("#clear").hidden = !jobs.some((job) => !ACTIVE.has(job.state));

  const counts = Object.fromEntries(Object.keys(QUEUE_FILTERS).map((name) => [name, 0]));
  for (const track of state.tracks) {
    for (const [name, test] of Object.entries(QUEUE_FILTERS)) if (test(track)) counts[name] += 1;
  }
  syncRadios($("#queue-filter"), "data-filter", state.queueFilter);
  for (const button of $$("#queue-filter [data-filter]")) {
    $(".count", button).textContent = counts[button.dataset.filter] || "";
  }
  $("#queue-empty").hidden = counts[state.queueFilter] > 0;
  $("#queue-empty .empty-title").textContent =
    t(state.tracks.length ? QUEUE_EMPTY[state.queueFilter] : QUEUE_EMPTY.all);
  const badge = $("#queue-badge");
  badge.textContent = counts.active > 99 ? "99+" : counts.active;
  badge.hidden = !counts.active;
  renderStatusBar();
}

function renderStatusBar() {
  if (!state.settings) return;
  const library = state.view === "library";
  const status = $("#status-text");
  status.textContent = library ? libraryStatus() : statusText();
  status.title = library ? state.settings.folder : "";
  const { format, threads, dry_run: dryRun } = state.settings;
  $("#status-mode").textContent = t("{mode} · {threads} {threadWord}", {
    mode: dryRun ? t("только проверка") : format,
    threads,
    threadWord: plural(threads, "поток", "потока", "потоков"),
  });
  const problems = $("#status-problems");
  problems.hidden = !state.problems.length;
  $("span", problems).textContent = t("Проблемы: {count}", { count: state.problems.length });
}

function statusText() {
  const jobs = state.run ? [...state.run.jobs].map((id) => state.jobs.get(id)).filter(Boolean) : [];
  if (!jobs.length) return t("Нет загрузок");
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
    const eta = estimate(tracks, finished);
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
  const text = parts.join(" · ") || t("Нет загрузок");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// Remaining time from the pace so far; skipped files take no time and would inflate it
function estimate(tracks, finished) {
  const remaining = tracks.length - finished.length;
  const worked = finished.filter((track) => track.state !== "skip" && track.state !== "cancel").length;
  const elapsed = (Date.now() - state.run.workStart) / 1000;
  if (!state.run.workStart || !remaining || worked < 2 || elapsed < 5) return "";
  const seconds = (remaining * elapsed) / worked;
  if (seconds < 60) return t("~{seconds} сек", { seconds: Math.max(5, Math.round(seconds / 5) * 5) });
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return t("~{minutes} мин", { minutes });
  return t("~{hours} ч {minutes} мин",
    { hours: Math.floor(minutes / 60), minutes: minutes % 60 });
}

/* Library */

// The status bar talks about downloads everywhere else; on this tab it counts
// what the folder holds, and what is picked out of it.
function libraryStatus() {
  const { items, selected, shown, loading } = state.library;
  if (loading && !items.length) return t("Читаем папку…");
  if (selected.size) {
    const size = items.filter((item) => selected.has(item.path)).reduce((total, item) => total + item.size, 0);
    return t("Выбрано: {count} · {size}", { count: selected.size, size: formatSize(size) });
  }
  if (!items.length) return t("В папке пока нет музыки");
  if (shown.length < items.length) {
    return t("Найдено: {shown} из {total}", { shown: shown.length, total: items.length });
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

async function loadLibrary() {
  const library = state.library;
  const folder = state.settings.folder;
  const token = ++library.token;
  if (library.folder !== folder) library.items = [];
  library.loading = true;
  renderLibrary();
  let items;
  try {
    items = await api().library(folder);
  } catch (error) {
    console.error(error);
    items = [];
  }
  if (token !== library.token) return;
  Object.assign(library, { items, folder, stale: false, loading: false });
  renderLibrary();
}

function renderLibrary() {
  const library = state.library;
  const { items, loading, selected } = library;
  const query = $("#library-filter").value.trim();
  const needle = query.toLocaleLowerCase();
  const found = needle
    ? items.filter((item) => `${item.artist} ${item.title}`.toLocaleLowerCase().includes(needle))
    : items;
  const shown = sortLibrary(found);
  library.shown = shown.map((item) => item.path);
  // A row that the filter hides must not stay selected: a batch delete would
  // then take away something nobody can see.
  const visible = new Set(library.shown);
  for (const path of selected) if (!visible.has(path)) selected.delete(path);
  $("#library").replaceChildren(...shown.map(createLibraryRow));
  const rows = $$("#library .library-row");
  rows.forEach((row, index) => { row.tabIndex = index ? -1 : 0; }); // one tab stop, arrows do the rest
  renderSortHeader();
  renderSelection();

  const empty = $("#library-empty");
  empty.hidden = shown.length > 0;
  let [title, text] = [t("Ничего не найдено"), t("По запросу «{query}»", { query })];
  if (!items.length) {
    [title, text] = loading ? [t("Читаем папку…"), ""]
                            : [t("В папке пока нет музыки"), state.settings.folder];
  }
  $(".empty-title", empty).textContent = title;
  $(".empty-text", empty).textContent = text;
}

function sortLibrary(items) {
  const { key, dir } = state.library.sort;
  const { value } = LIBRARY_SORTS[key];
  return [...items].sort((a, b) => dir * compare(value(a), value(b)) || COLLATOR.compare(a.title, b.title));
}

function compare(a, b) {
  return typeof a === "number" ? a - b : COLLATOR.compare(a, b);
}

function sortLibraryBy(key) {
  const sort = state.library.sort;
  sort.dir = sort.key === key ? -sort.dir : LIBRARY_SORTS[key].dir;
  sort.key = key;
  renderLibrary();
}

function renderSortHeader() {
  const { key, dir } = state.library.sort;
  for (const button of $$("#view-library .sort")) {
    const active = button.dataset.sort === key;
    const order = dir > 0 ? "ascending" : "descending";
    button.closest("[role=columnheader]").setAttribute("aria-sort", active ? order : "none");
  }
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
  $("[data-action=again]", menu).hidden = !items.some((item) => item.link);
  $("[data-action=open]", menu).hidden = items.length !== 1;
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
  if (restoreFocus) $(`#library .library-row[data-path]`)?.focus();
}

function onLibraryMenuClick(event) {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  closeLibraryMenu(false);
  const items = selectedItems();
  if (action === "again") downloadAgain(items);
  else if (action === "open" && items.length === 1) api().open_folder(items[0].path);
  else if (action === "delete") deleteSelected();
}

function clearSelection() {
  if (!state.library.selected.size) return;
  state.library.selected.clear();
  state.library.anchor = null;
  renderSelection();
}

function renderSelection() {
  const { selected } = state.library;
  for (const row of $$("#library .library-row")) {
    const picked = selected.has(row.dataset.path);
    row.classList.toggle("is-selected", picked);
    row.setAttribute("aria-selected", String(picked));
  }
  $("#library-delete").hidden = !selected.size;
  $("#library-again").hidden = !selectedItems().some((item) => item.link);
  renderStatusBar();
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
  let result;
  try {
    result = await api().delete(chosen.map((item) => item.path));
  } catch (error) {
    console.error(error);
    result = { deleted: 0, failed: chosen.map((item) => item.title) };
  }
  clearSelection();
  announce(result.failed.length
    ? t("Не удалось удалить: {names}", { names: result.failed.join(", ") })
    : t("Удалено: {count}", { count: result.deleted }));
  loadLibrary();
}

// A window-level confirm() would be a bare system box, so the dialog is ours.
// The buttons answer directly: WebView2 does not always raise the close event.
function askConfirm({ title, text, action }) {
  const dialog = $("#confirm");
  const yes = $("#confirm-ok");
  const no = $("#confirm-cancel");
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
  const open = () => api().open_folder(item.path);
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
    open();
  });
  const again = $(".again", row);
  again.hidden = !item.link; // only albums this program downloaded know their link
  again.addEventListener("click", (event) => {
    event.stopPropagation();
    downloadAgain([item]);
  });
  if (item.cover) coverObserver.observe(row);
  return row;
}

async function showLibraryCover(row) {
  const { path } = row.dataset;
  if (!state.covers.has(path)) state.covers.set(path, api().cover(path));
  const src = await state.covers.get(path);
  if (src) loadCover($(".cover", row), src);
}

function loadCover(cover, src) {
  const image = $("img", cover);
  image.addEventListener("load", () => { image.hidden = false; }, { once: true });
  image.src = src;
}

/* Helpers */

function formatSize(bytes) {
  const megabytes = bytes / 1048576;
  if (megabytes < 1) return t("{value} КБ", { value: Math.max(1, Math.round(bytes / 1024)) });
  if (megabytes < 1024) return t("{value} МБ", { value: Math.round(megabytes) });
  return t("{value} ГБ",
    { value: (megabytes / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 }) });
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
