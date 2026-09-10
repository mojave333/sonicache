"use strict";

// Web links with or without https:// and spotify: URIs; the backend decides what it can read
const LINK_RE = /https?:\/\/[^\s"'<>]+|(?:[a-z0-9-]+\.)+(?:com|ru|fm|be|link|fi)\/[^\s"'<>]+|spotify:(?:album|track):[A-Za-z0-9]{22}/gi;

const FORMAT_HINTS = {
  m4a: "m4a — звук как есть, без перекодирования. Подходит почти всем плеерам.",
  mp3: "mp3 — для старых плееров и магнитол. Перекодируется, файлы получаются чуть больше.",
  opus: "opus — компактнее при том же качестве, но понимают его не все плееры.",
};

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

const JOB_STATES = ["queued", "running", "done", "partial", "error", "cancelled"];
const ACTIVE = new Set(["queued", "running"]);

const state = { settings: null, jobs: new Map(), orphans: [] };
const darkMedia = window.matchMedia("(prefers-color-scheme: dark)");
const $ = (selector, root = document) => root.querySelector(selector);
const api = () => window.pywebview.api;

applyTheme();

let booted = false;
function boot() {
  if (booted) return;
  booted = true;
  init().catch((error) => renderProblems([`Интерфейс не запустился: ${error}`]));
}
if (window.pywebview?.api?.init) boot();
else window.addEventListener("pywebviewready", boot);

async function init() {
  const data = await api().init();
  state.settings = data.settings;
  $("#version").textContent = `v${data.version}`;
  renderProblems(data.problems);
  renderSettings();
  bindUi();
  $("#link").focus();
  pollLoop();
}

/* Settings */

function renderSettings() {
  const settings = state.settings;
  applyTheme();
  syncRadios($("#theme"), "data-theme-choice", settings.theme);
  syncRadios($("#formats"), "data-format", settings.format);
  $("#format-hint").textContent = FORMAT_HINTS[settings.format];
  $("#folder-path").textContent = shortPath(settings.folder);
  $("#folder").title = settings.folder;
  $("#threads").textContent = settings.threads;
  $("#dry-run").checked = settings.dry_run;
  $("#submit-label").textContent = settings.dry_run ? "Проверить" : "Скачать";
  $("#submit use").setAttribute("href", settings.dry_run ? "#i-search" : "#i-download");
}

function updateSettings(patch) {
  Object.assign(state.settings, patch);
  renderSettings();
  api().save_settings(state.settings);
}

function applyTheme() {
  const choice = state.settings ? state.settings.theme : "system";
  const dark = choice === "dark" || (choice === "system" && darkMedia.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

function bindUi() {
  radioGroup($("#theme"), "data-theme-choice", (theme) => updateSettings({ theme }));
  radioGroup($("#formats"), "data-format", (format) => updateSettings({ format }));
  darkMedia.addEventListener("change", applyTheme);

  $("#folder").addEventListener("click", async () => {
    const folder = await api().choose_folder(state.settings.folder);
    if (folder) updateSettings({ folder });
  });
  $("#dry-run").addEventListener("change", (event) => updateSettings({ dry_run: event.target.checked }));
  for (const button of document.querySelectorAll("[data-step]")) {
    button.addEventListener("click", () => {
      const threads = Math.min(8, Math.max(1, state.settings.threads + Number(button.dataset.step)));
      updateSettings({ threads });
    });
  }

  $("#paste").addEventListener("click", pasteFromClipboard);
  $("#link").addEventListener("input", clearLinkError);
  $("#form").addEventListener("submit", submitLinks);
  $("#stop").addEventListener("click", stopAll);
  $("#clear").addEventListener("click", clearFinished);

  // A dropped link would otherwise navigate the whole window away
  document.addEventListener("dragover", (event) => event.preventDefault());
  document.addEventListener("drop", (event) => {
    event.preventDefault();
    const text = event.dataTransfer.getData("text/uri-list") || event.dataTransfer.getData("text/plain");
    if (text) setLinkInput(text.trim());
  });
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
  const current = $("#link").value.trim();
  setLinkInput(current ? `${current} ${text}` : text);
}

function setLinkInput(value) {
  const input = $("#link");
  input.value = value;
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
    const dryRun = state.settings.dry_run;
    const jobs = await api().download(links, state.settings);
    input.value = "";
    for (const { job, link } of jobs) addJob(job, link, dryRun);
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
  $("#link-error-text").textContent = message;
  $("#link-error").hidden = false;
}

function clearLinkError() {
  $("#link").removeAttribute("aria-invalid");
  $("#link-error").hidden = true;
}

/* Jobs */

function addJob(id, link, dryRun) {
  const node = $("#job-template").content.firstElementChild.cloneNode(true);
  const job = {
    id, link, dryRun, node,
    state: "queued", title: prettyLink(link), folder: "", done: 0, total: 0,
    tracks: new Map(), expanded: false,
  };
  $(".job-title", node).textContent = job.title;
  $(".job-kind", node).textContent = dryRun ? "Проверка" : "Загрузка";
  $(".toggle", node).addEventListener("click", () => setExpanded(job, !job.expanded));
  $(".open", node).addEventListener("click", () => api().open_folder(job.folder));
  $(".retry", node).addEventListener("click", () => retryJob(job));
  state.jobs.set(id, job);
  $("#jobs").prepend(node);
  setJobState(job, "queued", "В очереди");

  // Events can arrive before download() has returned the job id
  const early = state.orphans.filter((event) => event.job === id);
  state.orphans = state.orphans.filter((event) => event.job !== id);
  early.forEach(handleEvent);
}

function setJobState(job, value, text) {
  job.state = value;
  job.node.classList.remove(...JOB_STATES.map((name) => `is-${name}`));
  job.node.classList.add(`is-${value}`);
  if (text !== undefined) setStatus(job, text);
  const finished = !ACTIVE.has(value);
  $(".retry", job.node).hidden = !["error", "cancelled", "partial"].includes(value);
  $(".retry-label", job.node).textContent =
    value === "cancelled" ? (job.total ? "Продолжить" : "Запустить") : "Повторить";
  $(".open", job.node).hidden = !(finished && !job.dryRun && job.folder && value !== "error");
  updateQueueChrome();
}

function setStatus(job, text) {
  $(".status-text", job.node).textContent = text;
}

async function pollLoop() {
  try {
    for (const event of await api().poll()) handleEvent(event);
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
  else if (event.type === "progress") onProgress(job, event);
}

function onJobEvent(job, event) {
  switch (event.state) {
    case "running":
      job.node.classList.add("loading");
      $(".progress", job.node).classList.add("indeterminate");
      setJobState(job, "running", "Читаем ссылку…");
      break;
    case "error":
      stopLoading(job);
      $(".job-kind", job.node).textContent = "Не получилось";
      setJobState(job, "error", event.message);
      announce(`Ошибка: ${event.message}`);
      break;
    case "cancelled":
      stopLoading(job);
      setJobState(job, "cancelled", job.total ? `Остановлено · ${job.done} из ${job.total}` : "Отменено");
      break;
    case "done": {
      stopLoading(job);
      const text = summaryText(event);
      const partial = event.failed > 0;
      setJobState(job, partial ? "partial" : "done", text);
      if (!partial && !event.dry_run) setExpanded(job, false);
      announce(`${job.title}: ${text}`);
      break;
    }
  }
}

function stopLoading(job) {
  job.node.classList.remove("loading");
  $(".progress", job.node).classList.remove("indeterminate");
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
  stopLoading(job);
  const kind = event.kind.charAt(0).toUpperCase() + event.kind.slice(1);
  const fromAlbum = event.kind === "трек" && event.album && event.album !== event.title && `из «${event.album}»`;
  const details = [kind, fromAlbum, event.year, event.service];
  $(".job-kind", job.node).textContent = details.filter(Boolean).join(" · ");
  const title = $(".job-title", job.node);
  title.textContent = event.title;
  title.title = event.title;
  $(".job-sub", job.node).textContent = event.artist;

  if (event.cover) {
    const image = $(".cover img", job.node);
    image.addEventListener("load", () => {
      image.hidden = false;
      $(".cover-placeholder", job.node).setAttribute("hidden", "");
    }, { once: true });
    image.src = event.cover;
  }

  const multiDisc = event.tracks.some((track) => track.disc > 1);
  const rows = event.tracks.map((track) => {
    const row = createTrackRow(track, event.artist, multiDisc);
    job.tracks.set(track.id, row);
    return row;
  });
  $(".tracks", job.node).replaceChildren(...rows);
  $(".toggle", job.node).hidden = false;
  setExpanded(job, true);
  onProgress(job, { done: 0, total: job.total });
}

function createTrackRow(track, releaseArtist, multiDisc) {
  const row = $("#track-template").content.firstElementChild.cloneNode(true);
  $(".track-num", row).textContent = multiDisc ? `${track.disc}-${track.number}` : track.number;
  const title = $(".track-title", row);
  title.textContent = track.title;
  title.title = track.title;
  $(".track-meta", row).textContent = track.artists === releaseArtist ? "" : track.artists;
  $(".track-dur", row).textContent = track.duration;
  setTrackState(row, "waiting");
  return row;
}

function onTrack(job, event) {
  const row = job.tracks.get(event.id);
  if (row) setTrackState(row, event.state, event);
}

function setTrackState(row, name, { text = "", source = "", percent = 0 } = {}) {
  const ui = TRACK_UI[name] || TRACK_UI.waiting;
  row.className = `track tone-${ui.tone}`;
  const icon = $(".badge .icon", row);
  $("use", icon).setAttribute("href", `#i-${ui.icon}`);
  icon.classList.toggle("spin", ui.icon === "spinner");
  $(".badge-text", row).textContent = name === "download" ? `${ui.label} ${percent}%` : ui.label;
  $(".track-note", row).textContent = trackNote(name, text, source);
}

function trackNote(name, text, source) {
  switch (name) {
    case "download": return source && `Источник: ${source}`;
    case "done": return source;
    case "found": return `${text} · ${source}`;
    case "skip": return "Файл уже есть в папке";
    case "missing": return "Нет в открытом доступе на YouTube Music и SoundCloud";
    case "error": return text;
    default: return "";
  }
}

function onProgress(job, { done, total }) {
  Object.assign(job, { done, total });
  const ratio = total ? done / total : 0;
  const bar = $(".progress", job.node);
  bar.style.setProperty("--p", ratio);
  bar.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
  if (job.state === "running" && total) {
    const verb = job.dryRun ? "Проверено" : "Готово";
    setStatus(job, `${verb} ${done} из ${total} ${plural(total, "трека", "треков", "треков")}`);
  }
}

function setExpanded(job, expanded) {
  job.expanded = expanded;
  $(".tracks", job.node).hidden = !(expanded && job.tracks.size);
  const toggle = $(".toggle", job.node);
  const label = expanded ? "Скрыть треки" : "Показать треки";
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute("aria-label", label);
  toggle.title = label;
}

function updateQueueChrome() {
  const jobs = [...state.jobs.values()];
  $("#empty").hidden = jobs.length > 0;
  $("#stop").hidden = !jobs.some((job) => ACTIVE.has(job.state));
  $("#clear").hidden = !jobs.some((job) => !ACTIVE.has(job.state));
}

function stopAll() {
  api().stop();
  for (const job of state.jobs.values()) {
    if (job.state === "running") setStatus(job, "Останавливаем…");
  }
}

function clearFinished() {
  for (const [id, job] of state.jobs) {
    if (!ACTIVE.has(job.state)) {
      job.node.remove();
      state.jobs.delete(id);
    }
  }
  updateQueueChrome();
}

async function retryJob(job) {
  const jobs = await api().download([job.link], { ...state.settings, dry_run: job.dryRun });
  job.node.remove();
  state.jobs.delete(job.id);
  for (const { job: id, link } of jobs) addJob(id, link, job.dryRun);
}

/* Helpers */

function renderProblems(problems) {
  if (!problems.length) return;
  const items = problems.map((text) => Object.assign(document.createElement("li"), { textContent: text }));
  $("#problems-list").append(...items);
  $("#problems").hidden = false;
}

function shortPath(path) {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts.length > 3 ? `…\\${parts.slice(-2).join("\\")}` : path;
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
