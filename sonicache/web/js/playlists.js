"use strict";

// Playlists of one's own: the list, a playlist's page, its cover, and the menu

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
  else renderLibrarySide(); // the playlists are in the column at the side, whatever is on screen
  return items;
}

function playlistName(entry) {
  if (!entry.smart) return entry.name;
  return t({ recent: "Недавно слушали", top: "Часто слушаю" }[entry.id] || entry.name);
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
    if (state.view !== "library") showView("library"); // asked from the column at the side, over another section
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
  $(".cover-edit", page).hidden = data.smart; // what is made from the listens has the cover of its first track
  $("[data-page-action=uncover]", page).hidden = !data.custom;
  $("[data-page-action=delete]", page).hidden = data.smart;
  $("[data-page-action=keep]", page).hidden = !data.smart || !data.tracks.length;
  $(".album-head .artists", page).textContent = t("Исполнитель · альбом");
  $(".album-tracks", page).classList.remove("no-artists");
  const rows = data.tracks.map((track, index) => {
    const row = createAlbumTrack({ ...track, number: index + 1 }, { artist: "" });
    const artists = $(".artists", row);
    artists.textContent = [track.artists || track.album_artist, track.album].filter(Boolean).join(" · ");
    if (data.id === "top") {
      artists.textContent = [t("{count} {timesWord}", { count: track.plays, timesWord: plural(track.plays, "раз", "раза", "раз") }),
        artists.textContent].filter(Boolean).join(" · ");
    }
    row.draggable = !data.smart;
    return addRowMore(row);
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
  Object.assign(data, { count: saved.count, duration: saved.duration, cover: saved.cover, custom: saved.custom });
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

// A picture of one's own for the playlist, asked for in the system's dialog, or the tracks' cover again
async function changePlaylistCover(data, reset) {
  const result = await (reset ? api().reset_playlist_cover(data.id) : api().set_playlist_cover(data.id));
  if (!result) return; // nothing was chosen
  if (result.error) {
    showToast(result.error);
    return;
  }
  Object.assign(data, { cover: result.cover, custom: result.custom });
  state.playlists.stale = true;
  loadPlaylists(true);
  if ($("#album-page").playlist === data) fillPlaylistPage(data); // unless the page has been closed meanwhile
  announce(t(reset ? "Обложка плейлиста возвращена" : "Обложка плейлиста изменена"));
}

async function onPlaylistAction(action) {
  const data = $("#album-page").playlist;
  if (!data) return;
  if (action === "play") pagePlaying() ? togglePlay() : playPlaylistPage(null);
  else if (action === "shuffle") playPlaylistPage(null, true);
  else if (action === "cover" || action === "uncover") changePlaylistCover(data, action === "uncover");
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
    button.innerHTML = '<span class="menu-name"></span><span class="menu-note"></span>';
    $(".menu-name", button).textContent = entry.name;
    $(".menu-note", button).textContent = String(entry.count);
    return button;
  });
  const create = document.createElement("button");
  create.type = "button";
  create.setAttribute("role", "menuitem");
  create.dataset.playlist = "";
  create.innerHTML = "<span></span>";
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
