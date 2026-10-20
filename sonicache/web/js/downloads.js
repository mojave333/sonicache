"use strict";

// The Download page: links and names in, the suggestions, the jobs and their
// tracks as the program reports them, the progress in the window and on the taskbar

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

// The links in a text as the program keeps them: Spotify's tracking ?si= goes,
// while other services keep their ids in the query (?v=, ?list=, ?i=)
function linksIn(text) {
  return [...new Set((text.match(LINK_RE) || []).map((link) =>
    link.replace(/^https?:\/\//i, "").replace(/(spotify\.com\/\S*?)\?.*$/, "$1")))];
}

// A music link copied elsewhere, offered once, when the window comes back into
// use; not one already in the field, in the queue or in the library
let clipboardOffered = "";

async function offerClipboard() {
  let text = "";
  try {
    text = (await api().paste()) || "";
  } catch {
    return;
  }
  const [link] = linksIn(text);
  if (!link || link === clipboardOffered) return;
  clipboardOffered = link;
  const known = [$("#link").value, ...[...state.jobs.values()].map((job) => job.link),
                 ...state.library.items.map((item) => item.link || "")];
  if (known.some((where) => linksIn(where).includes(link))) return;
  showToast(t("В буфере ссылка: {link}", { link: prettyLink(link) }), t("Скачать"), () => queueLinks([link]));
}

async function submitLinks(event) {
  event.preventDefault();
  hideSuggest();
  const input = $("#link");
  const raw = input.value.trim();
  const found = linksIn(raw);
  // No link in the field: the text is a name to search for, one release at a time
  const links = found.length ? found : (raw ? [raw] : []);
  if (!links.length) {
    showLinkError(explainBadLink(raw));
    input.focus();
    return;
  }
  if (await queueLinks(links)) input.value = "";
}

/* The link field searches too: a name typed there brings the catalogue's
   finds under it, to download or open; Enter with none picked still downloads
   the best match, as it always did */

const suggest = { timer: 0, token: 0, items: [], active: -1, query: "" };

function onLinkInput() {
  clearTimeout(suggest.timer);
  const query = $("#link").value.trim();
  if (query.length < 2 || (query.match(LINK_RE) || []).length) {
    hideSuggest();
    return;
  }
  suggest.timer = setTimeout(() => findSuggestions(query), 300);
}

async function findSuggestions(query) {
  const token = ++suggest.token;
  let results;
  try {
    results = await api().search(query);
  } catch (error) {
    console.error(error);
    return;
  }
  if (token !== suggest.token || $("#link").value.trim() !== query || document.activeElement !== $("#link")) return;
  Object.assign(suggest, { query, active: -1, items: [
    ...(results.albums || []).slice(0, 3).map((item) => ({ kind: "album", item })),
    ...(results.tracks || []).slice(0, 3).map((item) => ({ kind: "track", item })),
    ...(results.artists || []).slice(0, 2).map((item) => ({ kind: "artist", item })),
  ] });
  renderSuggestions();
}

function renderSuggestions() {
  const rows = suggest.items.map(({ kind, item }, index) => {
    const row = document.createElement("button");
    row.type = "button";
    row.id = `suggest-${index}`;
    row.className = "suggest-item";
    row.dataset.kind = kind;
    row.setAttribute("role", "option");
    row.innerHTML = '<span class="cover"><img alt="" hidden><svg class="icon sm" aria-hidden="true"><use href="#i-note"/></svg></span>'
      + '<span class="suggest-main"><span class="suggest-title"></span><span class="suggest-sub"></span></span>'
      + '<span class="suggest-kind"></span>';
    $(".suggest-title", row).textContent = kind === "artist" ? item.name : item.title;
    $(".suggest-sub", row).textContent = kind === "album" ? [item.artist, item.year].filter(Boolean).join(" · ")
      : kind === "track" ? [item.artist, item.album].filter(Boolean).join(" · ") : "";
    const where = kind !== "artist" && inLibrary(item, kind) ? t("в библиотеке") : "";
    $(".suggest-kind", row).textContent = where || t(kind === "album" ? RELEASE_KINDS[item.type] || "Альбом"
      : kind === "track" ? "Трек" : "Исполнитель");
    loadCover($(".cover", row), kind === "artist" ? item.picture : item.cover);
    row.addEventListener("click", () => pickSuggestion(index));
    return row;
  });
  if (!rows.length) {
    const none = document.createElement("p");
    none.className = "menu-empty";
    none.textContent = t("Ничего не найдено");
    rows.push(none);
  }
  const more = document.createElement("button");
  more.type = "button";
  more.id = `suggest-${suggest.items.length}`;
  more.className = "suggest-more";
  more.setAttribute("role", "option");
  more.textContent = t("Все результаты в поиске");
  more.addEventListener("click", () => pickSuggestion(suggest.items.length));
  $("#suggest").replaceChildren(...rows, more);
  $("#suggest").hidden = false;
  $("#link").setAttribute("aria-expanded", "true");
  markSuggestion();
}

function markSuggestion() {
  const rows = [...$$("#suggest [role=option]")];
  rows.forEach((row, index) => row.classList.toggle("is-active", index === suggest.active));
  const active = rows[suggest.active];
  if (active) {
    $("#link").setAttribute("aria-activedescendant", active.id);
    active.scrollIntoView({ block: "nearest" });
  } else {
    $("#link").removeAttribute("aria-activedescendant");
  }
}

function onLinkKey(event) {
  if ($("#suggest").hidden) return;
  const count = suggest.items.length + 1; // and the line that leads to the Search view
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const next = suggest.active + (event.key === "ArrowDown" ? 1 : -1);
    suggest.active = next >= count ? -1 : next < -1 ? count - 1 : next;
    markSuggestion();
  } else if (event.key === "Enter" && suggest.active >= 0) {
    event.preventDefault();
    pickSuggestion(suggest.active);
  } else if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    hideSuggest();
  }
}

function hideSuggest() {
  clearTimeout(suggest.timer);
  suggest.token++;
  suggest.active = -1;
  $("#suggest").hidden = true;
  $("#link").setAttribute("aria-expanded", "false");
  $("#link").removeAttribute("aria-activedescendant");
}

async function pickSuggestion(index) {
  const { query } = suggest;
  const picked = suggest.items[index];
  hideSuggest();
  if (!picked) { // every find, on the Search view
    showView("search");
    $("#search-input").value = query;
    runSearch(query);
  } else if (picked.kind === "artist") {
    showView("search");
    openSearchPage("artist", picked.item);
  } else if (await queueLinks([picked.item.link])) {
    $("#link").value = "";
  }
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
  $(".tag", node).classList.toggle("trial", dryRun);
  $(".job-row", node).addEventListener("click", (event) => {
    if (!event.target.closest(".cell-actions") && job.tracks.size) setExpanded(job, !job.expanded);
  });
  $(".open", node).addEventListener("click", () => api().open_folder(job.folder));
  $(".retry", node).addEventListener("click", () => retryJob(job));
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
      // Tracks an earlier version held back for a choice of recording never arrived
      job.result = { ok: entry.ok, skipped: entry.skipped, failed: entry.failed + (entry.doubtful || 0) };
      job.summary = summaryText({ ...job.result, dry_run: job.dryRun });
      if (job.result.failed) job.state = "partial";
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
  // Folded while it runs, the card still names the tracks on their way
  const busy = job.state === "running" && !job.expanded
    ? [...job.tracks.values()].filter((track) => track.state === "search" || track.state === "download") : [];
  const now = $(".now", node);
  now.textContent = busy.length ? t("Сейчас: {tracks}", { tracks: busy.map((track) => track.title).join(", ") }) : "";
  now.title = now.textContent;
  now.hidden = !busy.length;
  const offer = $(".offer", node);
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
  if (event.type === "sync") return onSyncEvent(event);
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
  for (const job of state.dirty) {
    renderJob(job);
    if (state.library.pending.has(job.id)) renderDownloading(job);
  }
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
    job.state = event.failed > 0 ? "partial" : "done";
    // What failed, or what a trial run found, is what the card is opened for
    setExpanded(job, event.failed > 0 || Boolean(event.dry_run));
    announce(`${job.title}: ${job.summary}`);
  }
  sinkFinished(job);
  // The card says it on the Download view; anywhere else the toast does
  if (["done", "partial", "error"].includes(job.state) && !job.dryRun && state.view !== "download") toastFinished(job);
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

// The downloads under way stay at the head of the list: one that ends goes down below them, to the head of those
// that are over. A short single added later used to finish above a long album and push it out of sight.
function sinkFinished(job) {
  const active = new Set([...state.jobs.values()].filter((other) => ACTIVE.has(other.state)).map((other) => other.node));
  const last = [...$("#jobs").children].filter((node) => active.has(node)).pop();
  if (last && job.node.compareDocumentPosition(last) & Node.DOCUMENT_POSITION_FOLLOWING) last.after(job.node);
}

function toastFinished(job) {
  if (job.state === "done") {
    showToast(t("Скачано: {title}", { title: job.title }), t("Открыть"), () => openDownloaded(job));
  } else {
    const text = job.state === "partial" ? "Скачано не всё: {title}" : "Не скачалось: {title}";
    showToast(t(text, { title: job.title }), t("Показать"), () => showJob(job));
  }
}

// A finished album on its page in the library, once the library has read it
async function openDownloaded(job) {
  showView("library");
  for (let wait = 0; wait < 100 && (state.library.loading || state.library.stale); wait++) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const item = !job.single && state.library.items.find((entry) => entry.album && pathKey(entry.path) === pathKey(job.folder));
  if (item && state.view === "library") openAlbum(item);
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
// bar. No card opens by itself while it runs, as a whole album of rows buried
// the rest of the queue: it names the tracks under way instead (renderJob), and
// opens at the end when there is something to look at (onJobEvent).
function onRelease(job, event, restored = false) {
  Object.assign(job, { folder: event.folder, title: event.title, total: event.tracks.length,
                       single: Boolean(event.single), artist: event.artist, year: event.year, cover: event.cover });
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
      job, number: multiDisc ? `${info.disc}-${info.number}` : info.number,
      title: info.title, artists: info.artists, duration: info.duration,
      // A state an earlier version saved and this one does not know: the track did not arrive here
      state: restored ? (TRACK_UI[info.state] ? info.state : "cancel") : "waiting", percent: 0,
      source: restored ? info.source || "" : "", text: restored ? info.text || "" : "",
    };
    track.row = createTrackRow(track, info.artists === event.artist ? "" : info.artists);
    renderTrack(track);
    job.tracks.set(info.id, track);
    rows.push(track.row);
    if (!restored) state.tracks.push(track);
  }
  $(".tracks", job.node).replaceChildren(...rows);
  // A download into the library shows there at once, grey until it lands
  if (!restored && !job.dryRun && inLibraryFolders(job.folder)) {
    state.library.pending.set(job.id, job);
    redrawAlbums();
  }
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

function foldTracks(job, animate) {
  unfold($(".tracks", job.node), job.expanded, animate);
}

// What opens under a row, the same way everywhere: a download's tracks, the genres and the devices in the column
// at the side. Its height grows or draws in over --settle on the window's own curve, its contents cut at the edge
// as it goes, and closed it is [hidden], out of the tab order. Only the part of the list that is on screen moves.
// Growing a 300-track playlist to its full height in the same 220 ms would cover the visible part in a frame or
// two and read as a snap; the rows past the bottom edge are out of sight either way, so they simply arrive (or
// leave) with the last frame.
function unfold(element, open, animate = true) {
  const running = element.getAnimations();
  const current = running.length ? element.getBoundingClientRect().height : null;
  running.forEach((animation) => animation.cancel());

  const quiet = !animate || reduceMotion();
  element.hidden = false; // measured at its own height, a max-height included; closed again below if it is to be
  const full = element.getBoundingClientRect().height;
  // The room between the top of the list and the bottom of the scrolling area;
  // a hidden view or a closed menu measures zero here and gets no animation.
  const scroller = element.closest(".list-scroll, .side-lists");
  const room = scroller ? Math.max(0, scroller.getBoundingClientRect().bottom - element.getBoundingClientRect().top) : full;
  const visible = Math.min(full, room);
  const from = current ?? (open ? 0 : visible);
  const to = open ? visible : 0;
  if (quiet || from === to) {
    element.hidden = !open;
    return;
  }
  const style = getComputedStyle(document.documentElement);
  const animation = element.animate(
    { height: [`${Math.min(from, room)}px`, `${to}px`], overflow: ["hidden", "hidden"] },
    { duration: parseFloat(style.getPropertyValue("--settle")) || 220,
      easing: style.getPropertyValue("--ease").trim() || "ease-out" },
  );
  // Out of the tab order once it is really closed; hidden first, so the
  // cancelled animation never lets the full height flash back.
  animation.finished.then(() => {
    if (!open) element.hidden = true;
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
    swapIcon($("use", button), paused ? "#i-play" : "#i-pause");
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
  if (state.library.pending.delete(job.id)) redrawAlbums();
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
  $("#jobs").classList.toggle("one-format", new Set(jobs.filter((job) => !job.dryRun).map((job) => job.format)).size < 2);
  for (const button of $$(".stop")) button.hidden = !active;
  for (const button of $$(".pause")) button.hidden = !active;
  if (!active && state.paused) setPaused(false); // nothing left to hold back
  $("#clear").hidden = !jobs.some((job) => !ACTIVE.has(job.state));

  // How many tracks are under way, said by the bar's button; its line shows how far (reportProgress)
  const underWay = state.tracks.filter((track) => TRACK_ACTIVE.has(track.state)).length;
  const button = $('.side-view[data-view="download"]');
  const count = underWay ? t("в работе: {count}", { count: underWay }) : "";
  button.title = [t("Загрузка (Ctrl+1)"), count].filter(Boolean).join(" · ");
  button.setAttribute("aria-label", [t("Загрузка"), count].filter(Boolean).join(", "));
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
  const ring = $("#download-ring");
  ring.hidden = name === "none";
  ring.dataset.state = name;
  ring.style.setProperty("--ring-p", name === "indeterminate" ? 0.25 : ratio);
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
