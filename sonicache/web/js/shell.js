"use strict";

// What holds the pages together: the bindings of the page, the keys, Home, the side
// list, the switch between the sections, menus, toggles and the toast

function bindUi() {
  for (const button of $$("[data-view]")) {
    button.addEventListener("click", () => showView(button.dataset.view));
  }
  bindSide();
  bindToast();
  bindProfiles();
  bindWatch();
  for (const button of $$("#formats [data-format]")) button.title = t(FORMAT_HINTS[button.dataset.format]);
  radioGroup($("#theme"), "data-theme-choice", (theme) => updateSettings({ theme }));
  radioGroup($("#font"), "data-font-choice", (font) => updateSettings({ font }));
  radioGroup($("#art"), "data-art-choice", (art) => updateSettings({ art }));
  bindSwitch($("#replaygain"), (on) => updateSettings({ replaygain: on }));
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
  $("#link").addEventListener("input", onLinkInput);
  $("#link").addEventListener("keydown", onLinkKey);
  $("#link").addEventListener("blur", hideSuggest);
  $("#suggest").addEventListener("mousedown", (event) => event.preventDefault()); // the field keeps the focus
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
  $("#library-refresh").addEventListener("click", () => {
    setMoreMenuOpen(false);
    loadLibrary();
  });
  $("#library-folder").addEventListener("click", () => {
    setMoreMenuOpen(false);
    api().open_folder(state.settings.folder);
  });
  $("#library-more").addEventListener("click", () => setMoreMenuOpen($("#library-more-menu").hidden));
  $("#library-more-menu").addEventListener("keydown", onMenuKey);
  $("#library-shuffle").addEventListener("click", shuffleLibrary);
  $("#library-delete").addEventListener("click", deleteSelected);
  $("#library-again").addEventListener("click", () => downloadAgain(selectedItems().filter(hasMissing)));
  $("#library-tidy").addEventListener("click", () => {
    setMoreMenuOpen(false);
    const picked = selectedItems().filter(hasGaps);
    tidyLibrary(picked.length ? picked : state.library.items.filter(hasGaps), { ask: !picked.length });
  });
  $("#library").addEventListener("click", onLibraryClick);
  $("#library").addEventListener("keydown", onLibraryKey);
  $("#library").addEventListener("contextmenu", onLibraryContextMenu);
  $("#view-home").addEventListener("click", onHomeClick);
  bindHomeBar();
  $("#search-library").addEventListener("click", onHomeClick);
  $(".album-more").addEventListener("click", onCardsClick);
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
  bindLibrarySide();
  bindDevices();
  bindSyncSide();
  bindWifi();
  radioGroup($("#library-view"), "data-library-view", setLibraryView);
  $("#library-sort").addEventListener("change", onSortPick);
  $("#library-order").addEventListener("change", onSortPick);
  $("#library-sort-button").addEventListener("click", () => setSortMenuOpen($("#library-sort-menu").hidden));
  $("#library-sort-menu").addEventListener("click", onSortMenuClick);
  $("#library-sort-menu").addEventListener("keydown", onMenuKey);
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest(".sort-wrap")) setSortMenuOpen(false, false);
    if (!event.target.closest(".more-wrap")) setMoreMenuOpen(false, false);
  });
  $("#page-back").addEventListener("click", () => closePage());
  $("#album-page").addEventListener("click", onPageAction);
  // The mouse's back button, as in a browser
  $("#view-library").addEventListener("mouseup", (event) => {
    if (event.button === 3 && !$("#library-page").hidden) closePage();
  });
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
  } else if (event.key === "Escape" && !$("#profile-menu").hidden) {
    setProfileMenuOpen(false);
    $("#profile").focus();
  } else if (event.ctrlKey && !event.altKey && !event.shiftKey && /^[1-4]$/.test(event.key)) {
    event.preventDefault();
    showView(VIEWS[Number(event.key) - 1]);
  } else if (event.key === "F5" || (event.ctrlKey && event.code === "KeyR")) {
    event.preventDefault(); // reloading the page would lose the download list
    if (state.view === "library") loadLibrary();
    if (state.view === "search") refreshCatalogue();
  } else if (event.ctrlKey && !event.altKey && !event.shiftKey && event.code === "KeyK") {
    event.preventDefault();
    showView("search");
  } else if (event.key === "Escape" && state.view === "sync" && $("#view-sync").classList.contains("side-open")) {
    setSyncSideOpen(false);
  } else if (event.key === "Escape" && state.view === "now") {
    closeNowPlaying();
  } else if (event.key === "Escape" && state.view === "search" && !$("#search-page").hidden) {
    closeSearchPage();
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
  if (event.key === "Escape" && $("#view-library").classList.contains("side-open")) {
    setLibrarySideOpen(false);
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

// The home page: three rows of albums out of what the library and the listens
// already know. Tracks played are shown as the albums they belong to.
function albumOf(path) {
  return state.library.items.find((item) => item.path === path
    || (item.album && (path.startsWith(`${item.path}\\`) || path.startsWith(`${item.path}/`))));
}

function albumsOf(tracks) {
  const found = new Set();
  for (const track of tracks || []) {
    const item = albumOf(track.path);
    if (item) found.add(item);
  }
  return [...found];
}

let homeToken = 0;

async function renderHome() {
  const token = ++homeToken;
  const items = state.library.items;
  const lists = await Promise.all(["recent", "top"].map((kind) => api().playlist(kind).catch(() => null)));
  if (token !== homeToken) return;
  const rows = {
    added: [...items].sort((a, b) => (b.modified || 0) - (a.modified || 0)),
    favourites: items.filter(isFavourite),
    recent: albumsOf(lists[0]?.tracks),
    top: albumsOf(lists[1]?.tracks),
  };
  // The album at the head is not shown again first thing in the row it was taken from
  const from = rows.recent[0] ? "recent" : rows.favourites[0] ? "favourites" : "added";
  const lead = rows[from][0];
  let shown = 0;
  for (const section of $$("#view-home .home-row")) {
    const key = section.dataset.row;
    const row = rows[key].filter((item) => key !== from || item !== lead).slice(0, 16);
    section.hidden = !row.length;
    shown += row.length;
    $(".cards", section).replaceChildren(...row.map((item, index) => createCard(item, index)));
  }
  // The greeting opens the page, and stands small in its bar once scrolled away, whatever the library holds
  for (const greeting of $$("#view-home .home-greeting")) greeting.textContent = homeGreeting();
  renderHomeTop(rows);
  $(".home-empty").hidden = shown > 0 || state.library.loading;
  drawAscii();
}

function homeGreeting() {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return t("Доброе утро");
  if (hour >= 12 && hour < 18) return t("Добрый день");
  if (hour >= 18 && hour < 23) return t("Добрый вечер");
  return t("Доброй ночи");
}

// Over the rows: a greeting, the album to go on with (the one played last, or
// else a favourite, or else the newest) in the colours of its cover, and six
// more albums to start from
function renderHomeTop(rows) {
  const top = $("#view-home .home-top");
  const lead = rows.recent[0] || rows.favourites[0] || rows.added[0];
  top.hidden = !lead;
  if (!lead) return;
  const feature = $(".home-feature", top);
  const changed = feature.dataset.path !== lead.path;
  feature.dataset.path = lead.path;
  $(".home-feature-kicker", feature).textContent = t(rows.recent[0] ? "Продолжить слушать"
    : rows.favourites[0] ? "Из избранного" : "Новое в библиотеке"); // not the name of the row of the newest below
  $(".home-feature-title", feature).textContent = lead.title;
  const tracks = lead.tracks ? t("{tracks} {trackWord}", { tracks: lead.tracks, trackWord: plural(lead.tracks, "трек", "трека", "треков") }) : "";
  $(".home-feature-sub", feature).textContent = [lead.artist, lead.album ? lead.year : t("трек"), tracks].filter(Boolean).join(" · ");
  $("[data-home-action=open] span", feature).textContent = t(lead.album ? "Открыть альбом" : "Открыть");
  if (changed) {
    const image = $("img", feature);
    image.hidden = true;
    image.removeAttribute("src");
    feature.style.setProperty("--tint-a", "0");
    if (lead.cover) tintHomeFeature(feature, lead);
  }
  const quick = [];
  for (const item of [...rows.favourites, ...rows.recent, ...rows.added]) {
    if (item !== lead && !quick.includes(item)) quick.push(item);
    if (quick.length === 6) break;
  }
  // One tile alone under the album at the head looked left over: they come in twos, so their rows are whole
  quick.length -= quick.length % 2;
  $(".home-quick", top).replaceChildren(...quick.map((item, index) => {
    const card = createCard(item, index);
    card.classList.add("quick");
    return card;
  }));
}

async function tintHomeFeature(feature, item) {
  showLibraryCover(feature);
  const src = await state.covers.get(item.path);
  const tint = src ? await pictureTint(src) : null;
  if (!tint || feature.dataset.path !== item.path) return;
  feature.style.setProperty("--tint-h", tint.hue);
  feature.style.setProperty("--tint-s", `${tint.saturation}%`);
  feature.style.setProperty("--tint-a", "1");
}

// The Home's bar is bare while the greeting is in sight. Its line comes once the greeting starts under it, and the
// greeting small in it once the large one has gone
function bindHomeBar() {
  const bar = $("#view-home .home-head");
  new IntersectionObserver(([entry]) => {
    bar.classList.toggle("lined", entry.intersectionRatio < 1);
    bar.classList.toggle("titled", !entry.isIntersecting);
  }, { root: $("#view-home .home"), threshold: [0, 1] }).observe($("#view-home .home-hello"));
}

function onHomeClick(event) {
  const action = event.target.closest("[data-home-action]");
  if (action) {
    const lead = itemByPath(action.closest(".home-feature").dataset.path);
    if (!lead) return;
    if (action.dataset.homeAction === "play") {
      playItem(lead);
    } else {
      showView("library");
      openAlbum(lead);
    }
    return;
  }
  const card = event.target.closest(".card");
  const item = card && itemByPath(card.dataset.path);
  if (!item) return;
  if (event.target.closest(".play-btn")) {
    playItem(item);
    $(".cover-mark.new", card)?.remove();
    return;
  }
  showView("library");
  openAlbum(item);
}

/* The column at the side: the sections, and in a wide window the library's list and the devices under them. A
   narrower window keeps the sections' icons only, and the lists go back into their pages, where they are the menus
   under the names in the bars. The settings take the column over with their subjects and a way back. */

const railMedia = matchMedia("(max-width: 999px)"); // the stylesheet's own figure

const sideWide = () => !railMedia.matches;

function bindSide() {
  railMedia.addEventListener("change", () => placeSideLists());
  placeSideLists(false); // the first section looks for the devices
  $("#settings-back").addEventListener("click", () => showView(state.settingsFrom || "home"));
}

// The lists go where the window's width puts them: into the column, or back to the head of their pages
function placeSideLists(watch = true) {
  const wide = sideWide();
  setLibrarySideOpen(false, false);
  setSyncSideOpen(false, false);
  if (wide) {
    $("#side-library").append($("#library-side"));
    $("#side-device-list").append($("#sync-side"));
    $("#side-settings").append($("#settings-nav"));
  } else {
    $("#view-library").prepend($("#library-side"));
    $("#view-sync").prepend($("#sync-side"));
    $("#view-settings").prepend($("#settings-nav"));
  }
  // Beside the lists the names in the bars are only headings; without them they open the lists as menus
  $("#library-title").disabled = wide;
  $("#sync-title").disabled = wide;
  renderSidePane();
  if (watch) watchDevices();
}

// The sections, or the settings' subjects while the settings are on screen; which page is up is told to the
// stylesheet, which lights the library's and the devices' rows only while theirs is
function renderSidePane() {
  const side = $("#side");
  const settings = state.view === "settings" && sideWide();
  const pane = settings ? $("#side-settings") : $("#side-main");
  const left = document.activeElement?.closest(".side-pane") && !pane.contains(document.activeElement);
  $("#side-main").hidden = settings;
  $("#side-settings").hidden = !settings;
  side.dataset.view = state.view;
  foldDevices();
  // A key that changed the pane leaves the focus in it, on what is now in use
  if (left) [...$$("#settings-back, [aria-current=page]", pane)].find((item) => item.offsetParent)?.focus();
}

// Synchronisation's row in the column, and its list unfolded under it while a device is plugged in, the phone
// server is on or the page is on screen. The row is lit while the page says what to plug in; a device's or the
// phone's own page lights its row in the list instead.
function foldDevices() {
  const { items, wifi, open } = state.devices;
  const page = state.view === "sync";
  const list = $("#side-device-list");
  const shown = items.length > 0 || Boolean(wifi?.on) || page;
  // At the start it is as it is, with no movement; after that it opens and closes as the genres do
  if (shown === list.hidden) unfold(list, shown, list.dataset.placed === "true");
  list.dataset.placed = "true";
  const row = $("#side-sync");
  if (page && !open) row.setAttribute("aria-current", "page");
  else row.removeAttribute("aria-current");
}

function showView(name) {
  // The way back from the settings goes to where they were opened from
  if (name === "settings" && state.view !== "settings") state.settingsFrom = state.view === "now" ? player.previousView : state.view;
  state.view = name;
  for (const button of $$("[data-view]")) {
    if (button.dataset.view === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  renderSidePane();
  for (const view of $$(".view")) view.hidden = view.id !== `view-${name}`;
  setMenuOpen(false, false);
  setLibrarySideOpen(false, false);
  if (name === "download") $("#link").focus();
  $("#player-lyrics")?.setAttribute("aria-pressed", String(name === "now"));
  // Now playing covers the whole window; any way out of it gives the window back
  document.documentElement.classList.toggle("now-mode", name === "now");
  // An open album takes the whole window below the top bar only while the library is
  // on screen: "Fetch what is missing" on its page or a shortcut lead elsewhere with
  // the page still open behind, and the library's own bars have to come back
  document.documentElement.classList.toggle("album-full", albumFillsWindow());
  if (name !== "now" && player.fullscreen) toggleFullscreen();
  if (name === "settings") {
    requestAnimationFrame(spySettings); // the cards have no places until the view shows
    loadScrobbleAccounts();
  }
  if (name === "search") {
    renderSearch();
    $("#search-input").focus();
    if (!state.search.query) loadCatalogueHome();
    // The search marks what the library holds: it is read, if it was not yet
    if (state.library.stale && !state.library.loading) loadLibrary();
  }
  if (name === "home") {
    if (state.library.stale && !state.library.loading) loadLibrary();
    renderHome();
  }
  if (name === "library") {
    if (state.library.stale || state.library.folder !== libraryFolders().join("\n")) loadLibrary();
    if (state.playlists.stale && !state.playlists.loading) loadPlaylists();
  } else {
    clearSelection();
  }
  if (name === "sync") {
    // The lists a device is chosen from are made of the library and the playlists
    if (state.library.stale && !state.library.loading) loadLibrary();
    if (state.playlists.stale && !state.playlists.loading) loadPlaylists();
    showSyncPage();
  }
  watchDevices();
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

// The queue and the equaliser float over the page, just above the player
// A word from the program wherever the window is, with at most one thing to do
// about it; it goes by itself, but not while the pointer rests on it
const toast = { timer: 0, act: null };

function showToast(text, action = "", act = null) {
  const box = $("#toast");
  $(".toast-text", box).textContent = text;
  $(".toast-text", box).title = text;
  const button = $(".toast-action", box);
  button.textContent = action;
  button.hidden = !action;
  toast.act = act;
  placeFloat(box);
  box.hidden = false;
  holdToast(false);
}

function hideToast() {
  clearTimeout(toast.timer);
  $("#toast").hidden = true;
  toast.act = null;
}

function holdToast(held) {
  clearTimeout(toast.timer);
  if (!held) toast.timer = setTimeout(hideToast, 8000);
}

function bindToast() {
  const box = $("#toast");
  $(".toast-action", box).addEventListener("click", () => {
    const act = toast.act;
    hideToast();
    act?.();
  });
  $(".toast-close", box).addEventListener("click", hideToast);
  box.addEventListener("mouseenter", () => holdToast(true));
  box.addEventListener("mouseleave", () => holdToast(false));
}

// Above the player wherever it stands: a plate at the foot of the window, or the dock that floats over Now playing
function placeFloat(panel) {
  const bar = $("#player");
  let edge = 0;
  if (!bar.hidden) {
    const style = getComputedStyle(bar);
    edge = (style.position === "fixed" ? parseFloat(style.bottom) : parseFloat(style.marginBottom)) + bar.offsetHeight;
  }
  panel.style.bottom = `${edge + 8}px`;
}
