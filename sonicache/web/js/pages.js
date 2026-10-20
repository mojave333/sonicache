"use strict";

// The pages that open over the library: an album's and an artist's, their colours
// and the moves between them, the cards, the selection and the right-click menu

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
// Pages are opened all day long: what is on them comes up at once and only settles into place, a short rise with
// no wait before it. It took over half a second for the last part of an album's page to show.
function riseIn(elements, delay = 0) {
  if (reduceMotion()) return;
  elements.forEach((element, index) => element?.animate(
    [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }],
    { duration: 220, delay: delay + Math.min(index, 4) * 25, easing: EASE_OUT, fill: "backwards" }));
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
      let lightSum = 0;
      for (let index = 0; index < data.length; index += 4) {
        const [hue, saturation, lightness] = toHsl(data[index], data[index + 1], data[index + 2]);
        lightSum += lightness;
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
      // A pale sleeve with a small coloured thing on it still gives that colour
      const lightness = Math.round((lightSum / pixels) * 100) / 100; // how pale the whole picture is
      if (!best || best.bucket.weight < pixels * 0.006) return { hue: 0, saturation: 0, lightness };
      const { bucket } = best;
      const hue = ((Math.atan2(bucket.y, bucket.x) * 180) / Math.PI + 360) % 360;
      return { hue: Math.round(hue), saturation: Math.round((bucket.saturation / bucket.weight) * 100), lightness };
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
  return tintElement($("#library-page"), src, () => owner === state.library.pageToken);
}

// What a page takes of its cover's colours, set on the page itself: the hue and the saturation the panel's wash
// and the band are drawn in. `current` says whether the page is still the one that was asked for.
async function tintElement(page, src, current) {
  const tint = src ? await pictureTint(src) : null;
  if (!current()) return; // another page has been opened since
  fadeBand(page);
  page.style.setProperty("--tint-a", tint ? "1" : "0");
  if (!tint) return;
  page.style.setProperty("--tint-h", tint.hue);
  page.style.setProperty("--tint-s", `${tint.saturation}%`);
  page.style.setProperty("--backdrop-a", backdropAlpha(tint));
}

// How much of the blurred cover shows on a dark page: the paler the picture, the
// less of it, or a white sleeve lays a grey fog where a dark one gives colour
function backdropAlpha(tint) {
  return Math.min(0.6, Math.max(0.16, 0.6 - Math.max(0, (tint?.lightness ?? 0.4) - 0.4) * 1.1)).toFixed(2);
}

function fadeBand(page) {
  page.classList.add("tinting");
  clearTimeout(page.tintTimer);
  page.tintTimer = setTimeout(() => page.classList.remove("tinting"), 600);
}

function albumFillsWindow() {
  // The Catalogue's album page is the same panel, below its field
  if (state.view === "search") return state.search.page?.kind === "album" && !$("#search-page").hidden;
  const pages = state.library.pages;
  return state.view === "library" && ["album", "playlist"].includes(pages[pages.length - 1]?.kind) && !$("#library-page").hidden;
}

function showPage(kind) {
  const page = $("#library-page");
  $("#view-library").classList.add("page-open");
  document.documentElement.classList.toggle("album-full", kind === "album" || kind === "playlist");
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

// The arrow says where it goes back to, in words and again in its tooltip
function renderPageBack() {
  renderLibrarySide();
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
  markSeen(item);
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
  // was opened from, but the page fills the window: the library's bars step
  // aside as it opens, and the cover ended up crossing to where they were.
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

// Under an album's tracks: the artist's other albums in the library, newest first
function renderAlbumMore(item) {
  const more = $(".album-more", $("#album-page"));
  const key = (item.artist || "").toLocaleLowerCase();
  const others = key ? state.library.items
    .filter((other) => other !== item && (other.artist || "").toLocaleLowerCase() === key)
    .sort((a, b) => (Number(b.year) || 0) - (Number(a.year) || 0)) : [];
  more.hidden = !others.length;
  $(".album-more-heading", more).textContent = t("Ещё от {artist}", { artist: item.artist });
  $(".cards", more).replaceChildren(...others.slice(0, 16).map((other, index) => createCard(other, index)));
}

// The line under the title, with the artist's name as the way to their page
function fillAlbumSub(item, text) {
  const sub = $(".album-sub", $("#album-page"));
  const artist = item.artist && libraryArtists().find((entry) => entry.name === item.artist);
  if (!artist || !text.startsWith(item.artist)) {
    sub.textContent = text;
    return;
  }
  const link = document.createElement("button");
  link.type = "button";
  link.className = "link";
  link.textContent = item.artist;
  link.addEventListener("click", () => openArtist(artist));
  sub.replaceChildren(link, text.slice(item.artist.length));
}

// Favourite albums: a heart on the album's page puts it in a row of its own on
// the home page and at the foot of its cover everywhere
function isFavourite(item) {
  return Boolean(item && state.favourites && state.favourites.has(item.path));
}

function renderFavourite(item) {
  const button = $("[data-page-action=favourite]", $("#album-page"));
  const on = isFavourite(item);
  button.setAttribute("aria-pressed", String(on));
  button.title = t(on ? "Убрать из избранного" : "В избранное");
  button.setAttribute("aria-label", button.title);
}

async function toggleFavourite(item) {
  const on = !isFavourite(item);
  if (on) state.favourites.add(item.path);
  else state.favourites.delete(item.path);
  renderFavourite(item);
  // The heart that has just been filled grows into its place, on the page and on the cover
  const grow = (node) => !reduceMotion() && node?.animate([{ transform: "scale(0.5)" }, { transform: "none" }],
                                                          { duration: 240, easing: EASE_OUT });
  if (on) grow($("#album-page [data-page-action=favourite] .icon"));
  for (const card of $$(".card")) {
    if (card.dataset.path !== item.path) continue;
    $(".cover-mark.fav", card)?.remove();
    if (on) grow(markFavourite(card));
  }
  await api().set_favourite(item.path, on);
}

function markFavourite(card) {
  const mark = document.createElement("span");
  mark.className = "cover-mark fav";
  mark.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-heart"/></svg>';
  $(".card-cover", card).append(mark);
  return mark;
}

// What the library already holds of what is being searched for: by title or artist
function renderSearchLibrary() {
  const words = (state.search.query || "").toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const found = words.length ? state.library.items.filter((item) => {
    const text = `${item.title} ${item.artist || ""}`.toLocaleLowerCase();
    return words.every((word) => text.includes(word));
  }).slice(0, 16) : [];
  const shown = state.search.filter === "all" ? found.slice(0, 6) : [];
  $("#search-library-section").hidden = !shown.length;
  $("#search-library").replaceChildren(...shown.map((item, index) => {
    const card = createCard(item, index);
    card.classList.add("quick");
    return card;
  }));
  return shown.length;
}

function fillAlbumPage(item) {
  const page = $("#album-page");
  page.dataset.kind = "album";
  page.playlist = null;
  page.tracks = [];
  $(".album-head .artists", page).textContent = t("Исполнители");
  $(".album-tracks", page).classList.remove("no-artists");
  $("use", $(".album-cover", page)).setAttribute("href", "#i-note");
  $(".album-title", page).textContent = item.title;
  $(".album-title", page).title = item.title;
  page.dataset.path = item.path;
  fillAlbumSub(item, albumSubline(item));
  renderFavourite(item);
  $(".album-stats", page).textContent = "";
  renderAlbumMore(item);
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

// Under the title, what an album is known by; the counts stand under its tracks
function albumSubline(item, genre = "") {
  return [item.artist, item.year, genre].filter(Boolean).join(" · ");
}

function albumStats(item, format = "", length = 0) {
  const parts = [t("{tracks} {trackWord}", { tracks: item.tracks, trackWord: plural(item.tracks, "трек", "трека", "треков") }),
    length ? formatLength(length) : "", formatSize(item.size), format];
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
  // Of "Alternative Rock; Rock; Art Rock" in the tags the first says enough here
  const genre = (data.tracks.map((track) => track.genre).find(Boolean) || "").split(";")[0].trim();
  const length = data.tracks.reduce((sum, track) => sum + (Number(track.duration) || 0), 0);
  fillAlbumSub(item, albumSubline(item, genre));
  $(".album-stats", page).textContent = albumStats(item, format, length);
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
    rows.push(addRowMore(createAlbumTrack(track, item)));
  }
  // Every track by the album's own artist: the column that says so is left out
  $(".album-tracks", page).classList.toggle("no-artists", all.every((track) => !track.artists || track.artists === item.artist));
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

// A track row's "more" button; a track with no file in the folder has none, but
// keeps the cell so the columns line up
function addRowMore(row) {
  if (!row.dataset.path) {
    row.append(document.createElement("span"));
    return row;
  }
  const more = $("#library-track-template").content.querySelector(".row-more").cloneNode(true);
  row.append(more);
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
  // An artist's photo flies back to its card, so the library's bars have to be back
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
  renderLibrarySide();
  $("#library-panel").inert = false;
  $("#view-library").classList.remove("page-open");
  document.documentElement.classList.remove("album-full");
}

/* Cards: a click opens, Ctrl and Shift pick, the right button offers the menu */

// What a cover says of its album at a glance: downloaded within the week, and
// how many of its tracks are still missing
const NEW_FOR = 7 * 24 * 3600;

function isNew(item, now = Date.now() / 1000) {
  return Boolean(item.modified) && now - item.modified < NEW_FOR && !(state.seen && state.seen.has(item.path));
}

// Right after the first scan, or a big import, every album is new, and a mark on every cover tells nothing:
// the new ones are marked only while they are fewer than half the library. Counted once per library read.
let newCount = { items: null, seen: -1, few: false };

function fewNew() {
  const { items } = state.library;
  const seen = state.seen ? state.seen.size : 0;
  if (newCount.items !== items || newCount.seen !== seen) {
    const now = Date.now() / 1000;
    newCount = { items, seen, few: items.filter((item) => isNew(item, now)).length * 2 < items.length };
  }
  return newCount.few;
}

function markCover(card, item) {
  const cover = $(".card-cover", card);
  if (isNew(item) && fewNew()) {
    const mark = document.createElement("span");
    mark.className = "cover-mark new";
    mark.textContent = t("Новое");
    cover.append(mark);
  }
  const missing = item.album && item.link ? (item.expected || 0) - (item.tracks || 0) : 0;
  if (missing > 0) {
    const mark = document.createElement("span");
    mark.className = "cover-mark gap";
    mark.title = t("Не хватает треков: {count}", { count: missing });
    mark.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-download"/></svg>';
    mark.append(String(missing));
    cover.append(mark);
  }
}

// Opened or played, an album is new no longer, in this run and the next
function markSeen(item) {
  if (!item || !item.path || !state.seen || state.seen.has(item.path)) return;
  state.seen.add(item.path);
  api().mark_seen(item.path);
  for (const card of $$("#library-cards .card")) {
    if (card.dataset.path === item.path) $(".cover-mark.new", card)?.remove();
  }
}

function createCard(item, index, download = null) {
  const card = $("#card-template").content.firstElementChild.cloneNode(true);
  card.dataset.path = item.path;
  card.style.setProperty("--i", Math.min(index, 24));
  $(".card-title", card).textContent = item.title;
  $(".card-title", card).title = item.title;
  const year = item.album ? item.year : t("трек");
  $(".card-sub", card).textContent = [item.artist, year].filter(Boolean).join(" · ");
  if (state.library.selected.has(item.path)) card.classList.add("is-selected");
  markCover(card, item);
  if (isFavourite(item)) markFavourite(card);
  if (item.cover) coverObserver.observe(card);
  if (download) markDownloading(card, download);
  return card;
}

// A cover's own play button: the album from its first track, its page left closed
async function playItem(item) {
  markSeen(item);
  const tracks = (await itemsTracks([item])).filter((track) => track.path && !track.missing);
  if (!tracks.length) return;
  rememberTracks(tracks);
  playQueue(tracks.map((track) => track.path), 0, "album", trackArtists(tracks));
}

function itemByPath(path) {
  return state.library.items.find((item) => item.path === path);
}

function onCardsClick(event) {
  const card = event.target.closest(".card");
  if (!card) return;
  if (event.target.closest(".play-btn") && card.dataset.path) {
    const item = itemByPath(card.dataset.path);
    if (item) playItem(item);
    return;
  }
  if (!card.dataset.path) { // a download with nothing in the folder yet
    const job = state.jobs.get(Number(card.dataset.job));
    if (job) showJob(job);
    return;
  }
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
  if (!card.dataset.path) return;
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
  if (url && photo.dataset.name === name) loadCover(photo, url);
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
  if (row?.dataset.path) selectRow(row.dataset.path, event);
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
  if (!event.ctrlKey && row.dataset.path) selectRow(row.dataset.path, { shiftKey: event.shiftKey });
}

function focusRow(row) {
  for (const other of $$("#library .library-row")) other.tabIndex = other === row ? 0 : -1;
  row.focus();
}

function onLibraryContextMenu(event) {
  const row = event.target.closest(".library-row");
  if (!row) return;
  event.preventDefault();
  if (!row.dataset.path) return;
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
  placeMenu(menu, x, y);
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
// An album still downloading lacks its tracks only for now: fetching them
// again would start a second download into the same folder
function hasMissing(item) {
  return Boolean(item.link) && item.expected > item.tracks
    && ![...state.library.pending.values()].some((job) => pathKey(job.folder) === pathKey(item.path));
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
  else if (action === "favourite") toggleFavourite(item);
  else if (action === "again") downloadAgain([item]);
  else if (action === "tidy") tidyLibrary([item]);
  else if (action === "play") pagePlaying() ? togglePlay() : playAlbumPage(0);
  else if (action === "watch") {
    const list = isWatched(item.link) ? await api().unwatch(item.link) : await api().watch_album(item.path);
    setWatched(list);
    renderWatchButton(item.link);
    announce(t(isWatched(item.link) ? "Следим за «{title}»" : "Больше не следим за «{title}»", { title: item.title }));
  }
}
