"use strict";

// The Catalogue: the search, its first screen, and the album and artist pages it opens

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
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (input.value) {
      input.value = "";
      runSearch("");
    } else {
      input.blur();
    }
  });
  $("#search-refresh").addEventListener("click", refreshCatalogue);
  $("#search-scroll").addEventListener("click", onSearchClick);
  $("#catalogue-home").addEventListener("click", (event) => {
    const more = event.target.closest("[data-more]");
    if (!more) return;
    const section = more.closest(".search-section");
    const open = section.classList.toggle("expanded");
    more.textContent = t(open ? "Свернуть" : "Все");
  });
  radioGroup($("#search-chips"), "data-search-filter", (filter) => {
    state.search.filter = filter;
    renderSearch({ enter: true });
    $("#search-scroll").scrollTop = 0;
  });
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
  const home = state.search.home;
  const found = results || { albums: [], tracks: [], artists: [] };
  const any = renderSearchLibrary() + found.albums.length + found.tracks.length + found.artists.length;
  // With nothing typed the Catalogue shows its first screen; the empty page is for when it has none
  const onShow = query ? 0 : renderCatalogueHome();
  $("#catalogue-home").hidden = !onShow;
  const empty = $("#search-empty");
  empty.hidden = Boolean(any) || onShow > 0;
  const waiting = loading || (!query && home.loading);
  $(".scene", empty).hidden = waiting; // no picture while the answer is still on its way
  $(".empty-title", empty).textContent = !query
    ? (home.loading ? t("Загружаем каталог…") : home.data?.error ? t("Каталог сейчас недоступен") : t("Найдите, что скачать"))
    : loading ? t("Ищем…")
    : results?.error ? t("Поиск не удался") : t("Ничего не найдено");
  $(".empty-text", empty).textContent = !query
    ? (home.loading ? "" : home.data?.error || "")
    : loading ? "" : results?.error || t("По запросу «{query}»", { query });
  $("#search-service").textContent = results?.service ? t("Каталог: {service}", { service: results.service }) : "";
  const { filter } = state.search;
  $("#search-bar").hidden = !query || !(found.albums.length + found.tracks.length + found.artists.length);
  syncRadios($("#search-chips"), "data-search-filter", filter);
  const best = filter === "all" && !loading ? bestSearchResult(found, query) : null;
  $("#search-best-section").hidden = !best;
  $("#search-best").replaceChildren(...(best ? [best] : []));
  const wanted = (kind) => filter === "all" || filter === kind;
  fillSearchSection("artists", wanted("artists") ? found.artists.map(createSearchArtist) : [], enter);
  fillSearchSection("albums", wanted("albums") ? found.albums.map(createSearchAlbum) : [], enter);
  fillSearchSection("tracks", wanted("tracks") ? found.tracks.map(createSearchTrack) : [], false);
}

/* The Catalogue's first screen: the albums new from the library's artists, and the chart */

// The artists the library has most albums of, the ones the catalogue is asked about
function catalogueArtists() {
  const unknown = t("Без исполнителя");
  return libraryArtists().filter((artist) => artist.name !== unknown)
    .sort((a, b) => b.albums - a.albums || b.modified - a.modified).slice(0, 12).map((artist) => artist.name);
}

async function loadCatalogueHome({ force = false } = {}) {
  const home = state.search.home;
  if (home.loading) return;
  const artists = catalogueArtists();
  const key = artists.join("|");
  if (!force && home.data && !home.data.error && home.key === key && Date.now() - home.at < 3600 * 1000) return;
  home.loading = true;
  if (state.view === "search") renderSearch();
  try {
    home.data = await api().catalogue_home(artists, force);
  } catch (error) {
    console.error(error);
    home.data = { error: String(error), popular: [], fresh: [] };
  }
  Object.assign(home, { loading: false, key, at: home.data.error ? 0 : Date.now() });
  if (state.view === "search") renderSearch();
}

// What is on the page asked for again: the answer to what is typed, or else the first screen, past what is kept
function refreshCatalogue() {
  clearTimeout(state.search.timer);
  const text = $("#search-input").value;
  if (text.trim()) runSearch(text, true);
  else loadCatalogueHome({ force: true });
}

// The rows are cards and rows like the search's own, so a click on one does what a click there does; what
// the library holds already is left out of what is new. Under a cover here only whose it is and the year:
// the kind and the tracks are on the album's page. In order: what is new from one's own artists, the chart's
// songs, the editors' picks, the chart's artists and its albums.
function renderCatalogueHome() {
  const data = state.search.home.data;
  const albums = { fresh: (data?.fresh || []).filter((item) => !inLibrary(item)), picks: data?.picks || [], popular: data?.popular || [] };
  const lists = {
    ...Object.fromEntries(Object.entries(albums).map(([kind, items]) => [kind, items.map((item, index) => {
      const card = createSearchAlbum(item, index);
      $(".card-sub", card).textContent = [item.artist, item.year].filter(Boolean).join(" · ");
      return card;
    })])),
    tracks: (data?.tracks || []).map(createSearchTrack),
    artists: (data?.artists || []).map(createSearchArtist),
  };
  let shown = 0;
  for (const [kind, nodes] of Object.entries(lists)) {
    $(`#home-${kind}-section`).hidden = !nodes.length;
    $(`#home-${kind}`).replaceChildren(...nodes);
    shown += nodes.length;
  }
  return shown;
}

// The artist the words name, or else the first album the catalogue sent: made
// as the cards below are, so a click on it does what a click on them does
function bestSearchResult(found, query) {
  const needle = (query || "").toLocaleLowerCase();
  if (!needle) return null;
  const named = (artist) => (artist.name || "").toLocaleLowerCase();
  const artist = found.artists.find((entry) => named(entry) === needle) || found.artists.find((entry) => named(entry).startsWith(needle));
  const card = artist ? createSearchArtist(artist, 0) : found.albums[0] ? createSearchAlbum(found.albums[0], 0)
    : found.artists[0] ? createSearchArtist(found.artists[0], 0) : null;
  if (!card) return null;
  card.classList.add("best");
  // What kind of answer it is, since it stands alone above the rest
  const sub = $(".card-sub", card);
  sub.textContent = [card.classList.contains("search-artist") ? t("Исполнитель") : "", sub.textContent].filter(Boolean).join(" · ");
  return card;
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
  if (state.view === "search" && !state.search.query) loadCatalogueHome();
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
  loadCover($(".card-cover", card), item.cover);
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
  loadCover($(".cover", row), item.cover);
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
function markQueued(button, link) {
  const queued = state.search.queued.has(link);
  button.classList.toggle("is-queued", queued);
  swapIcon($("use", button), queued ? "#i-check" : "#i-download");
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
    document.documentElement.classList.toggle("album-full", albumFillsWindow());
    $("#search-input").focus({ preventScroll: true });
    return;
  }
  const below = pages[pages.length - 2];
  $("#search-back-label").textContent = below ? (below.item.name || below.item.title) : t(state.search.query ? "Поиск" : "Каталог");
  $(".album-page", page).hidden = top.kind !== "album";
  $(".search-artist-page", page).hidden = top.kind !== "artist";
  page.hidden = false;
  document.documentElement.classList.toggle("album-full", albumFillsWindow());
  page.scrollTop = 0;
  $("#search-back").focus({ preventScroll: true });
  if (top.kind === "album") fillSearchAlbum(top);
  else fillSearchArtist(top);
}

async function fillSearchAlbum(entry) {
  const { item } = entry;
  const page = $("#search-page");
  // The artist's page stands first in the markup, with a title and a subtitle of its own: this one is asked for
  // by its own box, or the album's name would be written there, out of sight, and the page left without one
  const view = $(".album-page", page);
  const kind = (value) => t(RELEASE_KINDS[value] || "Альбом");
  $(".album-title", view).textContent = item.title;
  $(".album-title", view).title = item.title;
  $(".album-sub", view).textContent = [item.artist, kind(item.type), item.year].filter(Boolean).join(" · ");
  $(".album-stats", view).textContent = "";
  $(".album-tracks", view).classList.remove("no-artists");
  const cover = $(".album-cover", view);
  $("img", cover).hidden = true;
  $("img", cover).removeAttribute("src");
  // The panel shows it large, so the larger picture is asked for; the wash behind it takes its colours
  const src = (item.cover || "").replace("/250x250-", "/1000x1000-");
  loadCover(cover, src);
  showBackdrop(view, src);
  tintElement(page, src, () => state.search.page === entry);
  renderSearchPageButton();
  riseIn([...$(".album-info", view).children]);
  if (!entry.data) {
    $(".album-list", view).replaceChildren(searchNote(t("Читаем список треков…")));
    try {
      entry.data = await api().release(item.link);
    } catch (error) {
      entry.data = { error: String(error) };
    }
  }
  if (state.search.page !== entry) return; // another page was opened meanwhile
  const data = entry.data;
  if (data.error) {
    $(".album-list", view).replaceChildren(searchNote(data.error));
    return;
  }
  // Under the title who and when, as in the library; how many and how long stand under the tracks
  const count = data.tracks.length;
  const length = data.tracks.reduce((sum, track) => sum + (Number(track.duration) || 0), 0);
  $(".album-sub", view).textContent = [data.artist, kind(data.kind), data.year].filter(Boolean).join(" · ");
  $(".album-stats", view).textContent = [
    t("{tracks} {trackWord}", { tracks: count, trackWord: plural(count, "трек", "трека", "треков") }),
    length ? formatLength(length) : "", data.service].filter(Boolean).join(" · ");
  // As in the library, the column of artists is left out where it would only say the album's artist over again
  $(".album-tracks", view).classList.toggle("no-artists", data.tracks.every((track) => !track.artists || track.artists === data.artist));
  $(".album-list", view).replaceChildren(...data.tracks.map((track) => {
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
