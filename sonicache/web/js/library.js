"use strict";

// The library: reading it, the grid, the table and the artists, sorting and
// filtering, the side list, tidying up tags and deleting

// Remaining time from the pace so far; skipped files take no time and would inflate it
/* Library */

// Motion follows Material's emphasized curve: things leave fast and land softly.
const EMPHASIZED = "cubic-bezier(0.2, 0, 0, 1)";

// What arrives is off the mark at once and slow to land: the stylesheet's --ease-out
const EASE_OUT = "cubic-bezier(0.23, 1, 0.32, 1)";

// A sign that turns into another under the pointer does not blink: the new one fades in over the old place.
// It does not move or grow, so that a button that was pressed stays where it is.
function swapIcon(use, href) {
  if (use.getAttribute("href") === href) return;
  use.setAttribute("href", href);
  const icon = use.ownerSVGElement;
  if (reduceMotion() || !icon.getClientRects().length) return; // nobody sees it change
  icon.animate([{ opacity: 0.25 }, { opacity: 1 }], { duration: 160, easing: "ease-out" });
}

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
  $("#library-title-name").textContent = libraryTitle();
  const summary = $("#library-summary");
  summary.textContent = libraryNote() || librarySummary();
  summary.title = summary.textContent; // a narrow window cuts it short
}

// The name of what the library shows, as its list of views has it
function libraryTitle() {
  const { tab, favouritesOnly } = state.library;
  if (tab === "albums" && favouritesOnly) return t("Избранное");
  return t({ albums: "Альбомы", tracks: "Треки", artists: "Исполнители", playlists: "Плейлисты" }[tab]);
}

function libraryNote() {
  const { items, selected, shown } = state.library;
  const sync = state.devices.sync;
  if (sync.running) {
    return t("Синхронизация: {done} из {total} · {title} · не отключайте устройство",
             { done: Math.min(sync.done + 1, sync.total), total: sync.total, title: sync.title });
  }
  if (sync.note && !selected.size) return sync.note;
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
  const held = state.library.favouritesOnly ? items.filter(isFavourite) : items;
  const albums = held.filter((item) => item.album).length;
  const tracks = held.reduce((total, item) => total + item.tracks, 0);
  const size = held.reduce((total, item) => total + item.size, 0);
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
      if (state.view === "home") renderHome();
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
  for (const [id, job] of library.pending) if (!ACTIVE.has(job.state)) library.pending.delete(id);
  library.artists = null;
  renderLibrary({ enter: !early });
  if (state.view === "home") renderHome();
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
  $("#tracks-head").hidden = tab !== "tracks";
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
  renderGenre();
  if (tab === "playlists") $("#library-shuffle").hidden = true;
  const shownCount = tab === "albums" ? renderAlbums(grid, enter)
    : tab === "tracks" ? renderTracks(enter) : tab === "playlists" ? renderPlaylists(enter) : renderArtists(enter);
  renderSelection(); // and with it the bar above the list
  renderLibrarySide();

  const empty = $("#library-empty");
  empty.hidden = shownCount > 0;
  const query = $("#library-filter").value.trim();
  let [title, text] = [t("Ничего не найдено"), query ? t("По запросу «{query}»", { query })
    : t("В жанре «{genre}»", { genre: genreName() })];
  if (library.favouritesOnly && tab === "albums" && !query && !library.genre) {
    [title, text] = [t("Избранного пока нет"), t("Откройте альбом и нажмите на сердечко: он появится здесь")];
  }
  const loading = library.loading || (tab === "tracks" && library.trackList.loading);
  // A turntable with no record for an empty folder, a radio finding no station for
  // a search that turned nothing up, and no picture while the folder is still being read
  let scene = "radio";
  if (tab === "playlists") {
    if (!query) {
      const reading = state.playlists.loading && !state.playlists.items.length;
      [title, text] = reading ? [t("Читаем плейлисты…"), ""] : [t("Плейлистов пока нет"),
        t("Создайте свой или добавьте треки правым щелчком: «Добавить в плейлист». Плейлист .m3u8 из другого плеера тоже подойдёт")];
      scene = reading ? "" : "turntable";
    }
  } else if (!library.items.length || (tab === "tracks" && !library.trackList.items.length)) {
    [title, text] = loading ? [t("Читаем папку…"), ""] : [t("В папке пока нет музыки"), libraryFolders().join(", ")];
    scene = loading ? "" : "turntable";
  }
  $(".empty-title", empty).textContent = title;
  $(".empty-text", empty).textContent = text;
  const picture = $(".scene", empty);
  picture.hidden = !scene;
  if (scene) picture.dataset.scene = scene;
  drawAscii();
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

// The genre the library is narrowed to, as the tags spell it; nothing when it is not
function genreName() {
  const { genre } = state.library;
  return (genre && libraryGenres().find((entry) => entry.key === genre)?.name) || "";
}

// The genre is chosen in the list of views. Here it is checked against the tags, shown beside the name of the
// view as something a click takes off, and the shuffle is named after it
function renderGenre() {
  const library = state.library;
  const shuffle = $("#library-shuffle");
  // A genre gone from the folders after it was read again lets the library go back to all
  if (library.genre && !genreName()) library.genre = "";
  const genre = genreName();
  const chip = $("#library-genre");
  chip.hidden = !genre || library.tab === "playlists"; // the playlists are not narrowed by it
  $("span", chip).textContent = genre;
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
  const matches = (artist, title) => !needle || `${artist} ${title}`.toLocaleLowerCase().includes(needle);
  const found = library.items.filter((item) => inGenre(item.genre) && matches(item.artist, item.title)
    && (!library.favouritesOnly || isFavourite(item)));
  const shown = sortBy(found, LIBRARY_SORTS, library.sorts.albums, (a, b) => COLLATOR.compare(a.title, b.title));
  library.shown = shown.map((item) => item.path);
  // Something the filter hides must not stay selected: a batch delete would
  // then take away something nobody can see.
  const visible = new Set(library.shown);
  for (const path of library.selected) if (!visible.has(path)) library.selected.delete(path);
  // Downloads under way: an album already in the folder turns grey where it
  // stands, and one with nothing there yet comes first as a grey card of its
  // own, with no genre to be filtered by until its files are read
  const fresh = [];
  const downloading = new Map();
  if (library.pending.size) {
    const present = new Set(library.items.map((item) => pathKey(item.path)));
    for (const job of library.pending.values()) {
      if (!job.single && present.has(pathKey(job.folder))) downloading.set(pathKey(job.folder), job);
      else if (!library.genre && !library.favouritesOnly && matches(job.artist, job.title)) fresh.push(job);
    }
  }
  const downloadOf = (item) => (downloading.size ? downloading.get(pathKey(item.path)) : undefined);
  if (grid) {
    const cards = $("#library-cards");
    cards.replaceChildren(...fresh.map(createDownloadingCard),
      ...shown.map((item, index) => createCard(item, fresh.length + index, downloadOf(item))));
    cards.querySelector(".card")?.setAttribute("tabindex", "0");
    playEntrance(cards, enter);
    $("#library").replaceChildren();
  } else {
    $("#library").replaceChildren(...fresh.map(createDownloadingRow),
      ...shown.map((item) => createLibraryRow(item, downloadOf(item))));
    $$("#library .library-row").forEach((row, index) => { row.tabIndex = index ? -1 : 0; });
    $("#library-cards").replaceChildren();
    renderSortHeader();
  }
  return fresh.length + shown.length;
}

// A path the way the library compares them: one kind of slash, none at the
// end, and one case, as Windows does not tell them apart
function pathKey(path) {
  return String(path).replace(/[\\/]+/g, "/").replace(/\/$/, "").toLocaleLowerCase();
}

// The albums tab drawn again for a download that came or went: at once when
// it is on screen, otherwise when the library is next opened
function redrawAlbums() {
  if (state.view !== "library") state.library.stale = true;
  else if (state.library.tab === "albums") renderLibrary();
}

function inLibraryFolders(folder) {
  const key = pathKey(folder);
  return libraryFolders().some((root) => key === pathKey(root) || key.startsWith(`${pathKey(root)}/`));
}

// A download under way, on its card or row in the library: grey, with a bar
// for how far it has got
function markDownloading(element, job) {
  element.classList.add("is-downloading");
  element.dataset.job = job.id;
  element.setAttribute("aria-busy", "true");
  const bar = document.createElement("span");
  bar.className = "bar download-bar";
  bar.innerHTML = '<span class="bar-fill"></span>';
  $(".cover", element).append(bar);
  fillDownloading(element, job);
}

function renderDownloading(job) {
  for (const element of $$(`#library-cards [data-job="${job.id}"], #library [data-job="${job.id}"]`)) {
    fillDownloading(element, job);
  }
}

function fillDownloading(element, job) {
  const count = job.single ? t("трек") : t("{done} из {total}", { done: job.done, total: job.total });
  $(".card-sub, .sub", element).textContent = [job.artist, count].filter(Boolean).join(" · ");
  $(".bar-fill", element).style.setProperty("--p", job.total ? jobProgress(job) : 0);
}

// Grey cards of downloads with nothing in the folder yet. They are not
// library entries: a click leads to the download, and there is nothing to
// select or to offer in a menu
function createDownloadingCard(job, index) {
  const card = $("#card-template").content.firstElementChild.cloneNode(true);
  card.style.setProperty("--i", Math.min(index, 24));
  $(".card-title", card).textContent = job.title;
  $(".card-title", card).title = job.title;
  if (job.cover) loadCover($(".cover", card), job.cover);
  markDownloading(card, job);
  return card;
}

function createDownloadingRow(job) {
  const row = $("#library-template").content.firstElementChild.cloneNode(true);
  const title = $(".title", row);
  title.textContent = job.title;
  title.title = job.title;
  $(".cell-year", row).textContent = job.single ? "" : job.year || "";
  $(".cell-count", row).textContent = job.total;
  $(".open", row).hidden = true;
  row.addEventListener("dblclick", () => showJob(job));
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter") showJob(job);
  });
  if (job.cover) loadCover($(".cover", row), job.cover);
  markDownloading(row, job);
  return row;
}

function showJob(job) {
  if (!state.jobs.has(job.id)) return; // its card was cleared away since
  showView("download");
  job.node.scrollIntoView({ block: "nearest", behavior: reduceMotion() ? "auto" : "smooth" });
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
  renderSortMenu(sorts, key, dir, labels);
}

// What the library is sorted by and which way, in one button; its menu lists the
// sorts of the tab being shown and the two directions that go with the one chosen
function renderSortMenu(sorts, key, dir, labels) {
  const order = `${t(sorts[key].label)} · ${t(labels[dir])}`;
  $("#library-sort-label").textContent = order;
  // In a narrow window the button is its icon alone, and says the order when asked
  $("#library-sort-button").title = `${t("Сортировка")}: ${order}`;
  const menu = $("#library-sort-menu");
  const item = (attribute, value, text, checked) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "menuitemradio");
    button.setAttribute("aria-checked", String(checked));
    button.dataset[attribute] = value;
    button.innerHTML = '<svg class="icon sm check" aria-hidden="true"><use href="#i-check"/></svg><span class="menu-name"></span>';
    $(".menu-name", button).textContent = text;
    return button;
  };
  const label = (text) => Object.assign(document.createElement("p"), { className: "menu-label", textContent: t(text) });
  const divider = Object.assign(document.createElement("div"), { className: "menu-divider" });
  divider.setAttribute("role", "separator");
  menu.replaceChildren(
    label("Сортировка"),
    ...Object.entries(sorts).map(([name, sort]) => item("sortKey", name, t(sort.label), name === key)),
    divider,
    label("Порядок"),
    ...[sorts[key].dir, -sorts[key].dir].map((value) => item("sortDir", String(value), t(labels[value]), value === dir)));
}

function setSortMenuOpen(open, restoreFocus = true) {
  const menu = $("#library-sort-menu");
  const wasOpen = !menu.hidden;
  menu.hidden = !open;
  $("#library-sort-button").setAttribute("aria-expanded", String(open));
  if (open) ($("[aria-checked=true]", menu) || $("button", menu)).focus();
  else if (wasOpen && restoreFocus) $("#library-sort-button").focus();
}

// The library's "more": opening its folder and reading it again, wanted now and then
function setMoreMenuOpen(open, restoreFocus = true) {
  const menu = $("#library-more-menu");
  const wasOpen = !menu.hidden;
  menu.hidden = !open;
  $("#library-more").setAttribute("aria-expanded", String(open));
  if (open) $("button", menu).focus();
  else if (wasOpen && restoreFocus) $("#library-more").focus();
}

function onSortMenuClick(event) {
  const choice = event.target.closest("[data-sort-key], [data-sort-dir]");
  if (!choice) return;
  if (choice.dataset.sortKey) $("#library-sort").value = choice.dataset.sortKey;
  else $("#library-order").value = choice.dataset.sortDir;
  setSortMenuOpen(false);
  onSortPick();
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

/* Views and the pages drawn over them */

// Another of the library's views is there at once and fades in: it used to wait for the last one to fade
// out first, which every click in the side list paid for
function fadeThrough(change) {
  change();
  if (reduceMotion()) return;
  $("#library-panel").animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160, easing: "ease-out" });
}

/* The library's list of views, genres and playlists: in the column at the side in a wide window, and in a
   narrower one the menu of the name in the bar */

function setLibrarySideOpen(open, restoreFocus = true) {
  const view = $("#view-library");
  const title = $("#library-title");
  const side = $("#library-side");
  const wasOpen = view.classList.contains("side-open");
  open = open && !sideWide(); // in the column it has nothing to open
  view.classList.toggle("side-open", open);
  title.setAttribute("aria-expanded", String(open));
  if (open) ($("[aria-pressed=true]", side) || $("[aria-current=page]", side) || $(".side-item", side)).focus();
  else if (wasOpen && restoreFocus) title.focus();
}

// One of the library's views: the albums, the tracks, the artists, the playlists, or the albums that are favourites
async function chooseLibraryView(tab, favourites = false) {
  const library = state.library;
  while (!$("#library-page").hidden) await closePage({ instant: true });
  const changed = library.favouritesOnly !== favourites;
  library.favouritesOnly = favourites;
  if (tab !== library.tab) showLibraryTab(tab);
  else if (changed) renderLibrary({ enter: true });
  else renderLibrarySide();
}

function renderLibrarySide() {
  const library = state.library;
  const side = $("#library-side");
  const top = library.pages[library.pages.length - 1];
  const view = top?.kind === "playlist" ? ""
    : library.favouritesOnly && library.tab === "albums" ? "favourites" : library.tab;
  const counts = {
    albums: library.items.filter((item) => item.album).length,
    favourites: library.items.filter(isFavourite).length,
    tracks: library.items.reduce((total, item) => total + item.tracks, 0),
    artists: libraryArtists().length,
  };
  for (const entry of $$("[data-side]", side)) {
    const on = entry.dataset.side === view;
    if (on) entry.setAttribute("aria-current", "page");
    else entry.removeAttribute("aria-current");
    const number = $(".side-n", entry);
    if (number) number.textContent = counts[entry.dataset.side] || "";
  }
  const genres = libraryGenres();
  const open = (library.genresOpen ?? Boolean(library.genre)) && genres.length > 0;
  const group = $("#side-genres");
  group.hidden = !genres.length;
  group.setAttribute("aria-expanded", String(open));
  $(".side-n", group).textContent = genres.length || "";
  const list = $("#side-genre-list");
  if (open) {
    list.replaceChildren(...genres.map((genre) => sideItem({
      name: genre.name, count: genre.count,
      attributes: { "data-genre": genre.key, "aria-pressed": String(library.genre === genre.key) },
    })));
  }
  if (open === list.hidden) unfold(list, open); // filled first, so it opens to its own height
  const entries = state.playlists.items || [];
  const rows = [...entries.filter((entry) => entry.smart && entry.count),
    ...entries.filter((entry) => !entry.smart).sort((a, b) => COLLATOR.compare(a.name, b.name))];
  $("#side-playlists").replaceChildren(...rows.map((entry) => sideItem({
    name: playlistName(entry), count: entry.smart ? 0 : entry.count,
    attributes: { "data-playlist": entry.id, ...(top?.kind === "playlist" && top.id === entry.id ? { "aria-current": "page" } : {}) },
  })));
  renderDeviceSide();
}

// A row of the list: its name and, beside it, a count; no icon, the name says it
function sideItem({ name, count, attributes }) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "side-item";
  for (const [key, value] of Object.entries(attributes)) button.setAttribute(key, value);
  const label = document.createElement("span");
  label.className = "side-name";
  label.textContent = name;
  const number = document.createElement("span");
  number.className = "side-n";
  number.textContent = count || "";
  button.append(label, number);
  return button;
}

function bindLibrarySide() {
  const side = $("#library-side");
  const title = $("#library-title");
  title.addEventListener("click", () => setLibrarySideOpen(title.getAttribute("aria-expanded") !== "true"));
  $("#library-genre").addEventListener("click", () => {
    state.library.genre = "";
    renderLibrary({ enter: true });
  });
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#library-side, #library-title")) setLibrarySideOpen(false, false);
  });
  side.addEventListener("click", async (event) => {
    const library = state.library;
    const target = event.target;
    // Opening the genres is a step on the way; anything else chosen closes the menu, and from another section in
    // the column at the side it opens the library at what was chosen. A new playlist asks its name where it is,
    // and goes to the library once it has one.
    if (target.closest("button, [data-side]") && !target.closest("#side-genres")) {
      setLibrarySideOpen(false);
      if (state.view !== "library" && !target.closest("#side-playlist-new")) showView("library");
    }
    const genre = target.closest("[data-genre]");
    if (genre) {
      while (!$("#library-page").hidden) await closePage({ instant: true });
      if (library.tab === "playlists") await chooseLibraryView("albums");
      library.genre = library.genre === genre.dataset.genre ? "" : genre.dataset.genre;
      renderLibrary({ enter: true });
      return;
    }
    if (target.closest("#side-genres")) {
      library.genresOpen = !(library.genresOpen ?? Boolean(library.genre));
      renderLibrarySide();
      return;
    }
    const playlist = target.closest("[data-playlist]");
    if (playlist) {
      while (!$("#library-page").hidden) await closePage({ instant: true });
      openPlaylist(playlist.dataset.playlist);
      return;
    }
    if (target.closest("#side-playlist-new")) {
      $("#playlist-new").click();
      return;
    }
    const view = target.closest("[data-side]");
    if (view) chooseLibraryView(view.dataset.side === "favourites" ? "albums" : view.dataset.side, view.dataset.side === "favourites");
  });
  // The playlists' heading is a link to all of them, and answers the keys of a button
  $(".side-link", side).addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    event.currentTarget.click();
  });
}

function showLibraryTab(tab) {
  const library = state.library;
  if (tab === library.tab && $("#library-page").hidden) return;
  closePage({ instant: true });
  library.scroll[library.tab] = $("#library-scroll").scrollTop;
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
  // How far it has come is said above the list; the button says what a click does
  $("span", button).textContent = running ? t("Остановить") : t("Дописать теги");
  button.title = running
    ? t("Остановить после текущей записи · {done} из {total}", { done: Math.min(done + 1, total), total })
    : t("Дописать жанры, обложки, тексты и недостающие теги, ничего не скачивая заново");
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

function createLibraryRow(item, download = null) {
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
  if (download) markDownloading(row, download);
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
  if (!src) return;
  const image = $("img", cover);
  image.addEventListener("load", () => { image.hidden = false; }, { once: true });
  image.src = src;
}
