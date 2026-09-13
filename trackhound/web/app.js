"use strict";

// Web links with or without https:// and spotify: URIs; the backend decides what it can read
const LINK_RE = /https?:\/\/[^\s"'<>]+|(?:[a-z0-9-]+\.)+(?:com|ru|fm|be|link|fi)\/[^\s"'<>]+|spotify:(?:album|track):[A-Za-z0-9]{22}/gi;

const FORMAT_HINTS = {
  m4a: "m4a — звук как есть, без перекодирования. Подходит почти всем плеерам",
  mp3: "mp3 — для старых плееров и магнитол. Перекодируется, файлы чуть больше",
  opus: "opus — компактнее при том же качестве, но понимают его не все плееры",
};

const VIEWS = ["download", "library", "queue", "settings"];

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
const COLLATOR = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });
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
    state.problems = [`Интерфейс не запустился: ${error}`];
    renderProblems();
  });
}
if (window.pywebview?.api?.init) boot();
else window.addEventListener("pywebviewready", boot);

async function init() {
  const data = await api().init();
  state.settings = data.settings;
  state.problems = data.problems;
  $("#version").textContent = `v${data.version}`;
  renderProblems();
  renderSettings();
  bindUi();
  showView("download");
  renderChrome();
  setInterval(renderStatusBar, 1000);
  pollLoop();
  checkForUpdate();
}

// Nothing is downloaded or installed here: it only points at the releases page
async function checkForUpdate() {
  const release = await api().latest_release();
  if (!release) return;
  state.update = release;
  $("#update-title").textContent = `Доступна версия ${release.version}`;
  $("#update").hidden = false;
  $("#update-open").addEventListener("click", () => api().open_url(release.url));
  renderSettingsDot();
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
  syncRadios($("#formats"), "data-format", settings.format);
  syncRadios($("#mode-menu"), "data-dry-run", String(settings.dry_run));
  const folder = $("#folder");
  $("#folder-name").textContent = settings.folder.split(/[\\/]+/).filter(Boolean).pop() || settings.folder;
  folder.title = `${settings.folder}\nНажмите, чтобы выбрать другую папку`;
  folder.setAttribute("aria-label", `Папка для музыки: ${settings.folder}`);
  $("#threads").textContent = settings.threads;
  $("#cookies").value = settings.cookies_browser;
  $("#submit-label").textContent = settings.dry_run ? "Проверить" : "Скачать";
  $("#submit use").setAttribute("href", settings.dry_run ? "#i-search" : "#i-download");
  renderStatusBar();
}

function updateSettings(patch) {
  if (patch.folder && patch.folder !== state.settings.folder) state.library.stale = true;
  Object.assign(state.settings, patch);
  renderSettings();
  api().save_settings(state.settings);
}

function applyTheme() {
  const choice = state.settings ? state.settings.theme : "system";
  const dark = choice === "dark" || (choice === "system" && darkMedia.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

// The rail is dragged wider by its right edge; below SIDEBAR_SNAP it springs back to icons only
const SIDEBAR_RAIL = 64;
const SIDEBAR_SNAP = 110;
const SIDEBAR_MAX = 320;

function applySidebar(width) {
  const limit = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_RAIL, Math.round(window.innerWidth * 0.4)));
  const size = Math.min(limit, Math.max(SIDEBAR_RAIL, Math.round(width)));
  document.documentElement.style.setProperty("--sidebar", `${size}px`);
  $("#resizer").setAttribute("aria-valuenow", String(size));
  return size;
}

function sidebarWidth() {
  return $(".sidebar").getBoundingClientRect().width;
}

function bindSidebarResize() {
  const resizer = $("#resizer");
  let dragging = false;

  resizer.addEventListener("pointerdown", (event) => {
    dragging = true;
    resizer.setPointerCapture(event.pointerId);
    document.documentElement.dataset.resizing = "";
    event.preventDefault();
  });
  resizer.addEventListener("pointermove", (event) => {
    if (dragging) applySidebar(event.clientX);
  });
  resizer.addEventListener("pointerup", (event) => {
    if (!dragging) return;
    dragging = false;
    resizer.releasePointerCapture(event.pointerId);
    delete document.documentElement.dataset.resizing;
    const width = sidebarWidth();
    updateSettings({ sidebar: applySidebar(width < SIDEBAR_SNAP ? SIDEBAR_RAIL : width) });
  });
  // A double click flips between the icon rail and a comfortable labelled width
  resizer.addEventListener("dblclick", () => {
    updateSettings({ sidebar: applySidebar(sidebarWidth() > SIDEBAR_RAIL ? SIDEBAR_RAIL : 208) });
  });
  resizer.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -24, ArrowRight: 24 }[event.key];
    if (!step) return;
    event.preventDefault();
    const width = sidebarWidth() + step;
    updateSettings({ sidebar: applySidebar(width < SIDEBAR_SNAP && step < 0 ? SIDEBAR_RAIL : width) });
  });
}

function renderProblems() {
  const problems = state.problems;
  const items = () => problems.map((text) => Object.assign(document.createElement("li"), { textContent: text }));
  $("#problems-list").replaceChildren(...items());
  $("#problems").hidden = !problems.length;
  renderSettingsDot();
  $("#env-title").textContent = problems.length ? "Не хватает компонентов" : "Всё необходимое установлено";
  $("#env-list").replaceChildren(...(problems.length ? items()
    : [Object.assign(document.createElement("li"), { textContent: "ffmpeg, Deno или Node.js, yt-dlp-ejs" })]));
  $("#env-icon use").setAttribute("href", problems.length ? "#i-alert" : "#i-check");
  $("#env-icon").classList.toggle("warn", problems.length > 0);
  renderStatusBar();
}

function bindUi() {
  for (const button of $$("[data-view]")) {
    button.addEventListener("click", () => showView(button.dataset.view));
  }
  bindSidebarResize();
  for (const button of $$("#formats [data-format]")) button.title = FORMAT_HINTS[button.dataset.format];
  radioGroup($("#theme"), "data-theme-choice", (theme) => updateSettings({ theme }));
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

  $("#cookies").addEventListener("change", (event) => updateSettings({ cookies_browser: event.target.value }));
  $("#paste").addEventListener("click", pasteFromClipboard);
  $("#link").addEventListener("input", clearLinkError);
  $("#form").addEventListener("submit", submitLinks);
  bindModeMenu();
  for (const button of $$(".stop")) button.addEventListener("click", stopAll);
  $("#clear").addEventListener("click", clearFinished);
  $("#status-problems").addEventListener("click", () => showView("settings"));

  $("#library-filter").addEventListener("input", renderLibrary);
  $("#library-refresh").addEventListener("click", loadLibrary);
  $("#library-folder").addEventListener("click", () => api().open_folder(state.settings.folder));
  $("#library-delete").addEventListener("click", deleteSelected);
  $("#library").addEventListener("click", onLibraryClick);
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
    showLinkError("В буфере обмена пусто. Скопируйте ссылку на альбом или трек: «Поделиться» → «Копировать ссылку».");
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
  const links = [...new Set((raw.match(LINK_RE) || []).map((link) =>
    link.replace(/^https?:\/\//i, "").replace(/(spotify\.com\/\S*?)\?.*$/, "$1")))];
  if (!links.length) {
    showLinkError(explainBadLink(raw));
    input.focus();
    return;
  }
  const submit = $("#submit");
  submit.disabled = true;
  try {
    const { dry_run: dryRun, format } = state.settings;
    const jobs = await api().download(links, state.settings);
    input.value = "";
    for (const { job, link } of jobs) addJob(job, link, dryRun, format);
  } finally {
    submit.disabled = false;
  }
}

function explainBadLink(raw) {
  if (!raw) return "Вставьте ссылку на альбом, сингл или трек.";
  return "Не похоже на ссылку. Подойдут ссылки из Spotify, Apple Music, YouTube, SoundCloud и Last.fm: "
    + "в приложении нажмите «Поделиться» → «Копировать ссылку» и вставьте её сюда.";
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
    id, link, dryRun, node,
    state: "queued", stopping: false, title: prettyLink(link), sub: "", message: "", folder: "",
    done: 0, total: 0, result: null, summary: "", tracks: new Map(), expanded: false,
  };
  $(".tag", node).textContent = dryRun ? "проверка" : format;
  $(".job-row", node).addEventListener("click", (event) => {
    if (!event.target.closest(".cell-actions") && job.tracks.size) setExpanded(job, !job.expanded);
  });
  $(".open", node).addEventListener("click", () => api().open_folder(job.folder));
  $(".retry", node).addEventListener("click", () => retryJob(job));
  state.jobs.set(id, job);
  $("#jobs").prepend(node);
  renderJob(job);

  // Events can arrive before download() has returned the job id
  const early = state.orphans.filter((event) => event.job === id);
  state.orphans = state.orphans.filter((event) => event.job !== id);
  early.forEach(handleEvent);
  flushRender();
}

function hasActiveJobs() {
  return [...state.jobs.values()].some((job) => ACTIVE.has(job.state));
}

function renderJob(job) {
  const { node } = job;
  const status = jobStatus(job);
  node.className = `job is-${job.state}${job.tracks.size ? " has-tracks" : ""}`;
  $(".job-row", node).className = `row job-row tone-${status.tone}`;
  const title = $(".title", node);
  title.textContent = job.title;
  title.title = job.title;
  $(".sub", node).textContent = job.state === "error" ? job.message : job.sub;
  setStatusCell($(".cell-status", node), status);

  const finished = !ACTIVE.has(job.state);
  const retry = $(".retry", node);
  retry.hidden = !["error", "cancelled", "partial"].includes(job.state);
  const retryLabel = job.state === "cancelled" ? (job.total ? "Продолжить" : "Запустить") : "Повторить";
  retry.title = retryLabel;
  retry.setAttribute("aria-label", retryLabel);
  $(".open", node).hidden = !(finished && !job.dryRun && job.folder && job.state !== "error");
}

function jobStatus(job) {
  switch (job.state) {
    case "running": {
      if (!job.total) {
        return { icon: "spinner", tone: "muted", text: job.stopping ? "Останавливаем…" : "Читаем ссылку…", progress: "indeterminate" };
      }
      const ratio = jobProgress(job);
      const text = job.stopping ? "Останавливаем…" : `${job.done} из ${job.total} · ${Math.floor(ratio * 100)}%`;
      return { icon: "spinner", tone: "primary", text, progress: ratio };
    }
    case "done": {
      const { ok, skipped } = job.result;
      const text = job.dryRun ? "Всё найдено" : !ok && skipped ? "Уже скачано" : "Готово";
      return { icon: "check", tone: "success", text, tip: job.summary };
    }
    case "partial":
      return { icon: "alert", tone: "warning", text: `${job.dryRun ? "Не найдено" : "Не скачано"}: ${job.result.failed}`, tip: job.summary };
    case "error":
      return { icon: "alert", tone: "danger", text: "Ошибка", tip: job.message };
    case "cancelled":
      return { icon: "stop", tone: "muted", text: job.total ? `Остановлено · ${job.done} из ${job.total}` : "Отменено" };
    default:
      return { icon: "dot", tone: "muted", text: "В очереди" };
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
    announce(`Ошибка: ${event.message}`);
  } else if (event.state === "cancelled") {
    job.state = "cancelled";
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
  if (dryRun) return failed ? `Найдено ${ok} из ${ok + failed} · не найдено: ${failed}` : `Найдены все треки: ${ok}`;
  if (!ok && !failed && skipped) return "Всё уже было скачано раньше";
  const parts = [`Скачано ${ok} ${plural(ok, "трек", "трека", "треков")}`];
  if (skipped) parts.push(`уже были: ${skipped}`);
  if (failed) parts.push(`не удалось: ${failed}`);
  return parts.join(" · ");
}

function onRelease(job, event) {
  Object.assign(job, { folder: event.folder, title: event.title, total: event.tracks.length });
  const kind = event.kind.charAt(0).toUpperCase() + event.kind.slice(1);
  const fromAlbum = event.kind === "трек" && event.album && event.album !== event.title && `из «${event.album}»`;
  job.sub = [event.artist, kind, fromAlbum, event.year, event.service].filter(Boolean).join(" · ");
  if (event.cover) loadCover($(".cover", job.node), event.cover);

  const multiDisc = event.tracks.some((track) => track.disc > 1);
  const rows = [];
  const queueRows = [];
  for (const info of event.tracks) {
    const track = {
      job, number: multiDisc ? `${info.disc}-${info.number}` : info.number,
      title: info.title, artists: info.artists, duration: info.duration,
      state: "waiting", percent: 0, source: "", text: "",
    };
    track.row = createTrackRow(track, info.artists === event.artist ? "" : info.artists);
    track.queueRow = createTrackRow(track, info.artists, event.title);
    track.queueRow.addEventListener("dblclick", () => {
      showView("download");
      setExpanded(job, true);
      track.row.scrollIntoView({ block: "center" });
    });
    renderTrack(track);
    job.tracks.set(info.id, track);
    state.tracks.push(track);
    rows.push(track.row);
    queueRows.push(track.queueRow);
  }
  $(".tracks", job.node).replaceChildren(...rows);
  $("#queue").append(...queueRows);
  setExpanded(job, true);
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
      text: downloading ? `${ui.label} ${track.percent}%` : ui.label,
      tip: track.state === "skip" ? "Файл уже есть в папке" : "",
      progress: downloading ? track.percent / 100 : undefined,
    });
  }
  track.queueRow.hidden = !QUEUE_FILTERS[state.queueFilter](track);
}

function trackNote({ state: name, text }) {
  switch (name) {
    case "found": return `Найдено: ${text}`;
    case "missing": return "Нет в открытом доступе на YouTube Music и SoundCloud";
    case "error": return text;
    default: return "";
  }
}

function setExpanded(job, expanded) {
  job.expanded = expanded;
  $(".tracks", job.node).hidden = !(expanded && job.tracks.size);
  const toggle = $(".expander", job.node);
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute("aria-label", expanded ? "Скрыть треки" : "Показать треки");
}

function stopAll() {
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
    if (!ACTIVE.has(job.state)) removeJob(job);
  }
  renderChrome();
}

function removeJob(job) {
  job.node.remove();
  for (const track of job.tracks.values()) track.queueRow.remove();
  state.tracks = state.tracks.filter((track) => track.job !== job);
  state.jobs.delete(job.id);
}

async function retryJob(job) {
  const jobs = await api().download([job.link], { ...state.settings, dry_run: job.dryRun });
  const format = $(".tag", job.node).textContent;
  removeJob(job);
  for (const { job: id, link } of jobs) addJob(id, link, job.dryRun, format);
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
  $("#queue-empty .empty-title").textContent = state.tracks.length ? QUEUE_EMPTY[state.queueFilter] : QUEUE_EMPTY.all;
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
  $("#status-mode").textContent = `${dryRun ? "только проверка" : format} · ${threads} ${plural(threads, "поток", "потока", "потоков")}`;
  const problems = $("#status-problems");
  problems.hidden = !state.problems.length;
  $("span", problems).textContent = `Проблемы: ${state.problems.length}`;
}

function statusText() {
  const jobs = state.run ? [...state.run.jobs].map((id) => state.jobs.get(id)).filter(Boolean) : [];
  if (!jobs.length) return "Нет загрузок";
  const tracks = jobs.flatMap((job) => [...job.tracks.values()]);
  const finished = tracks.filter((track) => !TRACK_ACTIVE.has(track.state));
  const failed = tracks.filter((track) => TRACK_FAILED.has(track.state)).length;
  const dryRun = jobs.every((job) => job.dryRun);
  const parts = [];

  if (jobs.some((job) => ACTIVE.has(job.state))) {
    const queued = jobs.filter((job) => job.state === "queued").length;
    if (tracks.length) parts.push(`${dryRun ? "Проверено" : "Загружено"} ${finished.length} из ${tracks.length}`);
    const eta = estimate(tracks, finished);
    if (eta) parts.push(`осталось ${eta}`);
    if (failed) parts.push(`${dryRun ? "не найдено" : "не удалось"}: ${failed}`);
    if (jobs.some((job) => job.state === "running" && !job.total)) parts.push("читаем ссылку…");
    if (queued) parts.push(`ещё ${queued} ${plural(queued, "ссылка", "ссылки", "ссылок")} в очереди`);
    const text = parts.join(" · ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  const count = (...names) => tracks.filter((track) => names.includes(track.state)).length;
  const errors = jobs.filter((job) => job.state === "error").length;
  if (jobs.some((job) => job.state === "cancelled")) parts.push("Остановлено");
  if (dryRun && tracks.length) {
    parts.push(`найдено ${count("found")} из ${tracks.length}`);
  } else if (tracks.length) {
    const ok = count("done");
    const skipped = count("skip");
    parts.push(`скачано ${ok} ${plural(ok, "трек", "трека", "треков")}`);
    if (skipped) parts.push(`уже были: ${skipped}`);
    if (failed) parts.push(`не удалось: ${failed}`);
  }
  if (errors) parts.push(`${plural(errors, "ссылка", "ссылки", "ссылок")} с ошибкой: ${errors}`);
  const text = parts.join(" · ") || "Нет загрузок";
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// Remaining time from the pace so far; skipped files take no time and would inflate it
function estimate(tracks, finished) {
  const remaining = tracks.length - finished.length;
  const worked = finished.filter((track) => track.state !== "skip" && track.state !== "cancel").length;
  const elapsed = (Date.now() - state.run.workStart) / 1000;
  if (!state.run.workStart || !remaining || worked < 2 || elapsed < 5) return "";
  const seconds = (remaining * elapsed) / worked;
  if (seconds < 60) return `~${Math.max(5, Math.round(seconds / 5) * 5)} сек`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `~${minutes} мин`;
  return `~${Math.floor(minutes / 60)} ч ${minutes % 60} мин`;
}

/* Library */

// The status bar talks about downloads everywhere else; on this tab it counts
// what the folder holds, and what is picked out of it.
function libraryStatus() {
  const { items, selected, shown, loading } = state.library;
  if (loading && !items.length) return "Читаем папку…";
  if (selected.size) {
    const size = items.filter((item) => selected.has(item.path)).reduce((total, item) => total + item.size, 0);
    return `Выбрано: ${selected.size} · ${formatSize(size)}`;
  }
  if (!items.length) return "В папке пока нет музыки";
  if (shown.length < items.length) return `Найдено: ${shown.length} из ${items.length}`;
  const albums = items.filter((item) => item.album).length;
  const tracks = items.reduce((total, item) => total + item.tracks, 0);
  const size = items.reduce((total, item) => total + item.size, 0);
  const parts = albums ? [`${albums} ${plural(albums, "альбом", "альбома", "альбомов")}`] : [];
  parts.push(`${tracks} ${plural(tracks, "трек", "трека", "треков")}`, formatSize(size));
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
  const needle = query.toLocaleLowerCase("ru");
  const found = needle
    ? items.filter((item) => `${item.artist} ${item.title}`.toLocaleLowerCase("ru").includes(needle))
    : items;
  const shown = sortLibrary(found);
  library.shown = shown.map((item) => item.path);
  // A row that the filter hides must not stay selected: a batch delete would
  // then take away something nobody can see.
  const visible = new Set(library.shown);
  for (const path of selected) if (!visible.has(path)) selected.delete(path);
  $("#library").replaceChildren(...shown.map(createLibraryRow));
  renderSortHeader();
  renderSelection();

  const empty = $("#library-empty");
  empty.hidden = shown.length > 0;
  let [title, text] = ["Ничего не найдено", `По запросу «${query}»`];
  if (!items.length) [title, text] = loading ? ["Читаем папку…", ""] : ["В папке пока нет музыки", state.settings.folder];
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
  renderStatusBar();
}

async function deleteSelected() {
  const { items, selected } = state.library;
  const chosen = items.filter((item) => selected.has(item.path));
  if (!chosen.length) return;
  const size = formatSize(chosen.reduce((total, item) => total + item.size, 0));
  const what = chosen.length === 1
    ? `«${chosen[0].title}»`
    : `${chosen.length} ${plural(chosen.length, "запись", "записи", "записей")}`;
  const ok = await askConfirm({
    title: `Удалить ${what}?`,
    text: `${size} уйдёт в корзину — оттуда файлы можно вернуть.`,
    action: "Удалить",
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
    ? `Не удалось удалить: ${result.failed.join(", ")}`
    : `Удалено: ${result.deleted}`);
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
  $(".sub", row).textContent = item.album ? item.artist : [item.artist, "трек"].filter(Boolean).join(" · ");
  $(".cell-year", row).textContent = item.year;
  $(".cell-count", row).textContent = item.tracks;
  $(".cell-size", row).textContent = formatSize(item.size);
  $(".cell-date", row).textContent = new Date(item.modified * 1000).toLocaleDateString("ru-RU");
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
  if (megabytes < 1) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  if (megabytes < 1024) return `${Math.round(megabytes)} МБ`;
  return `${(megabytes / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ГБ`;
}

function prettyLink(link) {
  return link.replace(/^https?:\/\//, "").replace(/\?si=[^&]*$/, "");
}

function plural(count, one, few, many) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function announce(text) {
  $("#live").textContent = text;
}
