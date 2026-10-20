"use strict";

// What the rest stands on: the constants, the state, the bridge to the program,
// the pictures of the empty screens, the theme, and init(), which fills the window once the
// program has answered. app.js, loaded last, starts it through boot().

// Web links with or without https:// and spotify: URIs; the backend decides what it can read
const LINK_RE = /https?:\/\/[^\s"'<>]+|(?:[a-z0-9-]+\.)+(?:com|ru|fm|be|link|fi)\/[^\s"'<>]+|spotify:(?:album|track):[A-Za-z0-9]{22}/gi;

const FORMAT_HINTS = {
  mp3: "mp3 — 320 кбит/с из полной дорожки, до 20 кГц. Играет везде, включая магнитолы",
  m4a: "m4a — AAC 256 кбит/с из полной дорожки, до 20 кГц. Подходит почти всем плеерам",
  opus: "opus — дорожка YouTube как есть, без перекодирования. Понимают его не все плееры",
};

const VIEWS = ["download", "search", "library", "settings"]; // Ctrl+1…4, in the order of the bar; Ctrl+K too

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
    favouritesOnly: false, // the albums that are favourites, shown as a view of their own in the side list
    genresOpen: undefined, // the side list's genres, open or shut; left to the genre picked while undecided
    genre: "", // the genre the library is narrowed to, by its lower-case name; "" for all
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
    // Downloads into the library's folders, by job: shown grey from the moment
    // the release is read until the library is read again after them
    pending: new Map(),
  },
  // The catalogue search: what was asked last and what came back
  // home: the Catalogue's first screen (the chart and what is new), asked for when it is opened with nothing typed
  search: { home: { data: null, loading: false, at: 0, key: "" }, query: "", filter: "all", token: 0, results: null, loading: false, queued: new Set(), timer: 0, page: null, pages: [] },
  covers: new Map(),
  artistPhotos: new Map(),
  plays: new Map(), // path → how many times it was listened to
  playlists: { items: [], stale: true, loading: false, token: 0 }, // the cards of the Playlists tab
  // Players and phones the library is synced to: those there now, what the open one gets, how its sync goes
  devices: { items: [], loaded: false, open: null, timer: 0, selection: null, plan: null, planToken: 0, planTimer: 0, saving: null, shown: "", sideRows: "",
             tab: "playlists", entries: null, // the list chosen from on a device's page, and what each list offers
             wifi: null, // the library over Wi-Fi: on or off, the address, the login and the password

             sync: { running: false, device: "", done: 0, total: 0, title: "", note: "", timer: 0 } },
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

// The pictures of the empty screens, when typed in ASCII, each have a part that
// moves: a cassette's reels for the downloads, a turntable with no record for
// the library, a radio that finds no station for a search, a mixer for the
// settings. A scene is its lines and the cells drawn in the accent.
const SPOKES = ["|", "/", "-", "\\"];

const HISS = " .:'`,-";

function asciiPut(lines, row, col, text, accents) {
  const line = lines[row].padEnd(col + text.length);
  lines[row] = line.slice(0, col) + text + line.slice(col + text.length);
  if (accents) for (let i = 0; i < text.length; i++) accents.add(`${row}:${col + i}`);
}

const ASCII_SCENES = {
  cassette(tick, accents) {
    const lines = [
      "   ______________________________   ",
      "  /                              \\  ",
      " |   __________________________   | ",
      " |  |   .---.          .---.   |  | ",
      " |  |  /     \\        /     \\  |  | ",
      " |  |  \\     / ______ \\     /  |  | ",
      " |  |   '---'  |____|  '---'   |  | ",
      " |  |__________________________|  | ",
      " |     ______________________     | ",
      " |    /  o    [ ]  [ ]    o  \\    | ",
      " |___/________________________\\___| ",
    ];
    const spoke = SPOKES[tick % 4];
    asciiPut(lines, 4, 9, `(${spoke})`, accents);
    asciiPut(lines, 4, 24, `(${spoke})`, accents);
    asciiPut(lines, 4, 14, tick % 2 ? "~-~-~-~-" : "-~-~-~-~");
    return lines;
  },
  turntable(tick, accents) {
    const lines = [
      " .----------------------------------. ",
      " |      .-''''''''''-.              | ",
      " |    .'     ____     '.      ||    | ",
      " |   /     .'    '.     \\     ||    | ",
      " |  |     |   ()   |     |    ||    | ",
      " |   \\     '.____.'     /    //     | ",
      " |    '.              .'    //      | ",
      " |      '-..........-'     (o)      | ",
      " '----------------------------------' ",
    ];
    // A mark riding round the empty platter
    const angle = tick / 16 * 2 * Math.PI;
    const row = Math.round(4.3 + Math.sin(angle) * 2.1), col = Math.round(13.5 + Math.cos(angle) * 7.2);
    if (lines[row][col] === " ") asciiPut(lines, row, col, "*", accents);
    asciiPut(lines, 4, 14, "()", accents);
    return lines;
  },
  radio(tick, accents) {
    const lines = [
      " .------------------------------------. ",
      " |  88    92    96    100   104   108 | ",
      " |  |.....|.....|.....|.....|.....|   | ",
      " |                                    | ",
      " |  .------------.                    | ",
      " |  |            |     ( )     ( )    | ",
      " |  |            |                    | ",
      " |  |            |     ~  ~  ~  ~     | ",
      " |  '------------'                    | ",
      " '------------------------------------' ",
    ];
    // The needle goes along the dial and back, and the speaker only hisses
    const span = 30, step = Math.floor(tick / 2) % (span * 2);
    asciiPut(lines, 3, 4 + (step < span ? step : span * 2 - step), "^", accents);
    for (let row = 5; row <= 7; row++) {
      let hiss = "";
      for (let i = 0; i < 12; i++) hiss += HISS[Math.floor(Math.random() * HISS.length)];
      asciiPut(lines, row, 5, hiss);
    }
    return lines;
  },
  sync(tick, accents) {
    const lines = [
      " .-------------------.                     .--------. ",
      " |  .-------------.  |                     | .----. | ",
      " |  |  ~  ~  ~  ~ |  |                     | | ~~ | | ",
      " |  |  ~  ~  ~  ~ |  |                     | '----' | ",
      " |  '-------------'  |                     |   (  ) | ",
      " '---------+---------'                     '--------' ",
    ];
    // Notes go over to the player, one after another
    const trail = ">    ".repeat(5).slice(0, 25);
    const shift = tick % 5;
    asciiPut(lines, 3, 24, (trail.slice(shift) + trail.slice(0, shift)).slice(0, 19), accents);
    asciiPut(lines, 4, 52, tick % 2 ? "(o)" : "( )");
    return lines;
  },
};

let asciiTick = 0;

// Draws the scenes on screen where the settings ask for ASCII, and the settings' own sample of it whatever they
// ask. One out of sight is drawn once, so that it shows a picture even where nothing moves, and then left alone.
function drawAscii() {
  const ascii = document.documentElement.dataset.art === "ascii";
  for (const node of document.querySelectorAll("[data-scene] > .ascii")) {
    if (!ascii && !node.closest(".art-preview")) continue;
    if (!node.offsetParent && node.innerHTML) continue;
    const scene = ASCII_SCENES[node.parentElement.dataset.scene];
    if (!scene) continue;
    const accents = new Set();
    node.innerHTML = scene(asciiTick, accents).map((line, row) => [...line].map((ch, col) => {
      const safe = ch === "<" ? "&lt;" : ch === ">" ? "&gt;" : ch === "&" ? "&amp;" : ch;
      return accents.has(`${row}:${col}`) && ch !== " " ? `<b>${safe}</b>` : safe;
    }).join("")).join("\n");
  }
}

// Eight frames a second; whoever asked the system for less motion gets the picture standing still
if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
  setInterval(() => {
    if (document.hidden) return;
    asciiTick++;
    drawAscii();
  }, 125);
}

// The faces the names can be set in; the first is the one a new installation has
const FONTS = ["onest", "source-serif", "comic"];

// What an empty screen shows: no picture, a crayon drawing or ASCII; the first is a new installation's
const ARTS = ["none", "crayon", "ascii"];

applyTheme();

// What goes wrong in the script is kept in the program's log, so that a window that stopped answering can be traced
window.addEventListener("error", (event) => {
  window.pywebview?.api?.page_error?.(`${event.message} (${event.filename}:${event.lineno})`);
});

window.addEventListener("unhandledrejection", (event) => {
  window.pywebview?.api?.page_error?.(`${event.reason?.stack || event.reason}`);
});

let booted = false;

function boot() {
  if (booted) return;
  booted = true;
  init().catch((error) => {
    state.problems = [t("Интерфейс не запустился: {error}", { error })];
    renderProblems();
  });
}

async function init() {
  const data = await api().init();
  state.settings = data.settings;
  state.traySupported = Boolean(data.tray);
  state.library.view = data.settings.library_view === "list" ? "list" : "grid";
  state.problems = data.problems;
  state.watched = data.watched || [];
  setLanguage(data.language); // translates the markup before anything is drawn
  bindWindowFrame(Boolean(data.frameless));
  $("#version").textContent = `v${data.version}`;
  renderProblems();
  renderSettings();
  bindUi();
  state.seen = new Set(data.seen || []);
  state.favourites = new Set(data.favourites || []);
  restoreHistory(data.history || []);
  showView("home");
  loadPlaylists(); // they are in the column at the side from the start
  restorePlayer();
  renderChrome();
  setInterval(renderStatus, 1000);
  pollLoop();
  checkForUpdate();
  loadDiagnostics();
  // Notices about finished downloads are for when the window is not in use
  window.addEventListener("focus", () => {
    api().focus(true);
    offerClipboard();
  });
  window.addEventListener("blur", () => api().focus(false));
  if (data.first_run) showWelcome();
  else offerClipboard(); // a link copied before the program was started
}

// The theme, the face the names are set in and the pictures of the empty screens
function applyTheme() {
  const settings = state.settings || {};
  const choice = settings.theme || "light";
  const dark = choice === "dark" || (choice === "system" && darkMedia.matches);
  const root = document.documentElement;
  root.dataset.theme = dark ? "dark" : "light";
  root.dataset.font = FONTS.includes(settings.font) ? settings.font : FONTS[0];
  root.dataset.art = ARTS.includes(settings.art) ? settings.art : ARTS[0];
  drawAscii();
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
