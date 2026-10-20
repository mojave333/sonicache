"use strict";

// The settings page, the first run's welcome, the updates, the network check,
// the window's own frame, the watched releases, the profiles and the player's settings

// The first start: what the program does and where the music goes. Starting
// saves the settings, and a saved settings file means this is not shown again.
function showWelcome() {
  const dialog = $("#welcome");
  const path = () => { $("#welcome-path").textContent = state.settings.folder; };
  path();
  const choose = async () => {
    const folder = await api().choose_folder(state.settings.folder);
    if (folder) {
      updateSettings({ folder });
      path();
    }
  };
  const start = () => {
    $("#welcome-folder").removeEventListener("click", choose);
    document.documentElement.classList.remove("welcoming");
    if (dialog.open) dialog.close();
    api().save_settings(state.settings);
    $("#link")?.focus();
  };
  $("#welcome-folder").addEventListener("click", choose);
  $("#welcome-start").addEventListener("click", start, { once: true });
  dialog.addEventListener("cancel", start, { once: true }); // Escape starts too: nothing here must be decided
  document.documentElement.classList.add("welcoming");
  dialog.showModal();
  $("#welcome-start").focus({ preventScroll: true }); // a dialog taller than the window opens at its top
}

// Where the log sits, and how old the yt-dlp inside this build is
async function loadDiagnostics() {
  const info = await api().diagnostics();
  state.diagnostics = info;
  const stale = info.ytdlp_age >= 60;
  $("#ytdlp-title").textContent = `yt-dlp ${info.ytdlp}`;
  $("#ytdlp-desc").textContent = stale
    ? t("Этой сборке {days} {dayWord}, а YouTube за такое время обычно что-то меняет. Если загрузки перестали работать, обновите Sonicache",
        { days: info.ytdlp_age, dayWord: plural(info.ytdlp_age, "день", "дня", "дней") })
    : t("Звук скачивает yt-dlp. Он обновляется вместе с Sonicache");
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
  // Installed by a package manager, it is that manager's to update
  const managers = { scoop: "Установлено через Scoop. Обновите командой scoop update sonicache",
                     aur: "Установлено из AUR. Обновите пакет sonicache-bin, например командой yay -Syu sonicache-bin" };
  if (managers[release.manager]) $("#update-desc").textContent = t(managers[release.manager]);
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
  syncRadios($("#theme"), "data-theme-choice", settings.theme);
  syncRadios($("#font"), "data-font-choice", FONTS.includes(settings.font) ? settings.font : FONTS[0]);
  syncRadios($("#art"), "data-art-choice", ARTS.includes(settings.art) ? settings.art : ARTS[0]);
  syncSwitch($("#replaygain"), settings.replaygain);
  syncSwitch($("#lyrics"), settings.lyrics);
  syncSwitch($("#tray"), settings.tray);
  syncSwitch($("#notify"), settings.notify);
  syncSwitch($("#discord"), settings.discord);
  renderPlayerSettings();
  $("#tray-setting").hidden = !state.traySupported; // the notification area is Windows' own
  syncRadios($("#language"), "data-language", settings.language);
  syncRadios($("#formats"), "data-format", settings.format);
  renderProfiles();
  syncRadios($("#mode-menu"), "data-dry-run", String(settings.dry_run));
  const folder = $("#folder");
  $("#folder-name").textContent = settings.folder.split(/[\\/]+/).filter(Boolean).pop() || settings.folder;
  folder.title = t("{folder}\nНажмите, чтобы выбрать другую папку", { folder: settings.folder });
  folder.setAttribute("aria-label", t("Папка для музыки: {folder}", { folder: settings.folder }));
  $("#threads").textContent = settings.threads;
  $("#music-folder").textContent = settings.folder;
  renderLibraryFolders();
  $("#cookies").value = settings.cookies_browser;
  $("#rate").value = String(settings.rate_limit);
  $("#track-name").value = settings.track_name;
  $("#folder-layout").value = settings.folder_name;
  if ($("#proxy") !== document.activeElement) $("#proxy").value = settings.proxy;
  syncSwitch($("#relay"), settings.relay !== "off");
  applyPlayerSettings(settings);
  $("#naming-preview").textContent = namingExample(settings);
  $("#submit-label").textContent = t(settings.dry_run ? "Проверить" : "Скачать");
  renderStatus();
}

function updateSettings(patch) {
  if ((patch.folder && patch.folder !== state.settings.folder) || patch.library_folders) state.library.stale = true;
  const lyricsChanged = "lyrics" in patch && patch.lyrics !== state.settings.lyrics;
  Object.assign(state.settings, patch);
  renderSettings();
  api().save_settings(state.settings);
  if (lyricsChanged && state.library.items.length) refreshGaps(); // missing lyrics count only while they are on
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

// What this network lets through. Where services are blocked the program
// still does what it can — Spotify's preview pages instead of its player,
// SoundCloud instead of YouTube — and a VPN client's proxy, when one is
// running here, is one click away.
const NETWORK_WORDS = {
  spotify: {
    ok: "Spotify: плеер открывается",
    previews: "Spotify: плеер закрыт для этой сети. Альбомы и треки соберутся со страниц-превью, а из плейлиста только первые 30 треков",
    down: "Spotify: не открывается. Вставляйте ссылки из Apple Music, Deezer или YouTube Music либо пишите «Исполнитель - Альбом»",
  },
  youtube: {
    ok: "YouTube: открывается",
    down: "YouTube: не открывается. Звук будет только с SoundCloud, а там есть не всё",
  },
  soundcloud: { ok: "SoundCloud: открывается", down: "SoundCloud: не открывается" },
};

const PROXY_SPOTIFY = { ok: "плеер открывается", previews: "только страницы-превью", down: "не открывается" };

const RELAY_WORDS = {
  ok: "Зеркало Spotify: работает",
  down: "Зеркало Spotify: не отвечает",
  none: "Зеркало Spotify: не задано",
};

const NETWORK_TONES = { ok: "ok", previews: "warn", down: "bad" };

const NETWORK_ICONS = { ok: "check", warn: "alert", bad: "x" };

async function checkNetwork() {
  const button = $("#network-check");
  const desc = $("#network-desc");
  desc.dataset.text ??= desc.textContent;
  button.disabled = true;
  desc.textContent = t("Проверяем…");
  try {
    renderNetwork(await api().check_network());
  } finally {
    button.disabled = false;
    desc.textContent = desc.dataset.text;
  }
}

function renderNetwork(result) {
  const rows = ["spotify", "youtube", "soundcloud"].map((service) =>
    networkRow(NETWORK_TONES[result[service]], t(NETWORK_WORDS[service][result[service]])));
  // Where Spotify is closed, a working relay makes up for it in full
  if (result.relay === "ok" && result.spotify !== "ok") {
    rows[0] = networkRow("ok", t("Spotify: плеер закрыт для этой сети, но релизы целиком приходят через зеркало"));
  }
  if (result.relay !== "none" || result.spotify !== "ok") rows.splice(1, 0, networkRow(
    result.relay === "ok" ? "ok" : "bad", t(RELAY_WORDS[result.relay])));
  for (const proxy of result.proxies) {
    const row = networkRow(NETWORK_TONES[proxy.spotify],
      t("Прокси {client}: {url}. Spotify через него: {state}",
        { client: proxy.client, url: proxy.url, state: t(PROXY_SPOTIFY[proxy.spotify]) }));
    if (proxy.url === state.settings.proxy) {
      row.append(Object.assign(document.createElement("span"), { className: "net-note", textContent: t("включён") }));
    } else {
      const use = Object.assign(document.createElement("button"), {
        type: "button", className: "tool-btn", textContent: t("Использовать"),
      });
      use.addEventListener("click", () => {
        updateSettings({ proxy: proxy.url });
        checkNetwork(); // the same check again, now through the proxy
      });
      row.append(use);
    }
    rows.push(row);
  }
  if (!result.proxies.length) {
    rows.push(networkRow("", t("На этом компьютере не нашлось прокси VPN-клиента. VPN в режиме TUN или «системного прокси» программа использует сама. В других режимах впишите адрес прокси выше")));
  }
  $("#network-list").replaceChildren(...rows);
}

function networkRow(tone, text) {
  const row = document.createElement("li");
  row.className = `net-item${tone ? ` ${tone}` : ""}`;
  row.innerHTML = `<svg class="icon sm" aria-hidden="true"><use href="#i-${NETWORK_ICONS[tone] || "dot"}"/></svg><span></span>`;
  $("span", row).textContent = text;
  return row;
}

/* The window's own frame: with no system title bar the top bar is the way to move the window,
   its edges the way to size it, and its three buttons are drawn here */

const FRAME_EDGES = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

function setMaximized(on) {
  document.documentElement.classList.toggle("maximized", on);
  const button = $("#win-max");
  $("use", button).setAttribute("href", on ? "#i-win-restore" : "#i-win-max");
  button.title = t(on ? "Восстановить" : "Развернуть");
  button.setAttribute("aria-label", button.title);
}

function refreshMaximized() {
  Promise.resolve(api().window_state()).then((on) => setMaximized(Boolean(on))).catch(() => {});
}

function toggleMaximized() {
  if (player.fullscreen) return; // the whole screen is left by its own button, or by leaving Now playing
  Promise.resolve(api().window_maximize()).then((on) => setMaximized(Boolean(on))).catch(() => {});
}

// The band across the top of the window, as tall as the pages' bars, is where a title bar would be, as in Apple
// Music: a press on its bare ground (the column's head, a page's bar, the head of a page without one, the top of
// Now playing) takes the window by it, and a second one fills the screen. What is clicked, typed in or dragged
// keeps its own press; a click on bare ground still lands, the window moves only once the pointer does.
function onFrameDown(event) {
  const band = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--bar-h")) || 55;
  if (event.button !== 0 || event.clientY > band || player.fullscreen) return;
  if (event.target.closest("button, input, select, textarea, a, label, img, dialog, [role=button], [role=radio], "
    + "[role=option], .field, .menu, .card, .job, .track-item, .album-track, .library-row, .win-edge")) return;
  if (event.detail === 2) {
    toggleMaximized();
    return;
  }
  api().window_drag();
}

function bindWindowFrame(frameless) {
  if (!frameless) return;
  document.documentElement.classList.add("frameless");
  $("#window-controls").hidden = false;
  $("#win-min").addEventListener("click", () => api().window_minimize());
  $("#win-max").addEventListener("click", toggleMaximized);
  $("#win-close").addEventListener("click", () => api().window_close());
  for (const edge of FRAME_EDGES) {
    const handle = document.createElement("div");
    handle.className = "win-edge";
    handle.dataset.edge = edge;
    handle.addEventListener("mousedown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      api().window_resize(edge);
    });
    document.body.append(handle);
  }
  document.addEventListener("mousedown", onFrameDown);
  let settle = 0;
  window.addEventListener("resize", () => {
    clearTimeout(settle);
    settle = setTimeout(refreshMaximized, 120);
  });
  refreshMaximized();
}

/* Watching: albums and playlists checked again now and then for new tracks */

function isWatched(link) {
  return (state.watched || []).some((entry) => entry.link === link);
}

function setWatched(list) {
  state.watched = list || [];
  renderWatched();
  renderArtistButtons();
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
  if (entry.kind === "artist") {
    return entry.added_tracks
      ? t("Проверено {when}, новых релизов: {count}", { when, count: entry.added_tracks })
      : t("Проверено {when}, новых релизов нет", { when });
  }
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
    desc.textContent = [entry.kind === "artist" ? t("исполнитель") : "", entry.service, watchStatus(entry)]
      .filter(Boolean).join(" · ");
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

// The folders the library shows besides the music folder, each with a way out
function renderLibraryFolders() {
  const group = $("#library-folders-group");
  for (const row of $$(".library-folder-row", group)) row.remove();
  for (const folder of state.settings.library_folders || []) {
    const row = document.createElement("div");
    row.className = "setting library-folder-row";
    const label = document.createElement("div");
    label.className = "setting-label";
    const path = document.createElement("p");
    path.className = "setting-path";
    path.textContent = folder;
    label.append(path);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-btn danger";
    remove.title = t("Убрать из библиотеки");
    remove.setAttribute("aria-label", remove.title);
    remove.innerHTML = '<svg class="icon sm" aria-hidden="true"><use href="#i-x"/></svg>';
    remove.addEventListener("click", () => updateSettings({
      library_folders: state.settings.library_folders.filter((item) => item !== folder),
    }));
    row.append(label, remove);
    group.append(row);
  }
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
  // The button says where and in what the next download goes, or the profile that set it
  const folder = state.settings.folder.split(/[\\/]+/).filter(Boolean).pop() || state.settings.folder;
  $("#profile-name").textContent = active ? active.name : `${state.settings.format} · ${folder}`;
  $("#profile").classList.toggle("on", Boolean(active));

  const menu = $("#profile-list");
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
  renderStatus();
}

/* Settings: the player's card */

function bindPlayerSettings() {
  bindSwitch($("#gapless"), (on) => updateSettings({ gapless: on }));
  const crossfade = $("#crossfade");
  crossfade.addEventListener("input", () => {
    fillRange(crossfade);
    renderCrossfade(Number(crossfade.value));
  });
  crossfade.addEventListener("change", () => updateSettings({ crossfade: Number(crossfade.value) }));
  $("#lastfm-connect").addEventListener("click", onLastfmClick);
  $("#listenbrainz-connect").addEventListener("click", onListenbrainzClick);
  $("#listenbrainz-token").addEventListener("keydown", (event) => {
    if (event.key === "Enter") onListenbrainzClick();
  });
}

function renderPlayerSettings() {
  const settings = state.settings;
  syncSwitch($("#gapless"), settings.gapless !== false);
  const crossfade = $("#crossfade");
  if (document.activeElement !== crossfade) crossfade.value = settings.crossfade || 0;
  fillRange(crossfade);
  renderCrossfade(Number(crossfade.value));
  renderEqualizer();
}

function renderCrossfade(seconds) {
  $("#crossfade-value").textContent = seconds ? t("{seconds} с", { seconds }) : t("нет");
}

async function loadScrobbleAccounts() {
  try {
    state.scrobble = await api().scrobble_accounts();
  } catch (error) {
    console.error(error);
  }
  renderScrobbleAccounts();
}

function renderScrobbleAccounts() {
  const accounts = state.scrobble || {};
  $("#lastfm-setting").hidden = !accounts.lastfm_ready;
  const waiting = accounts.waiting ? t(" · ждут отправки: {count}", { count: accounts.waiting }) : "";
  const lastfm = $("#lastfm-connect");
  if (!state.lastfmWaiting) {
    $("span", lastfm).textContent = t(accounts.lastfm ? "Отключить" : "Подключить");
    lastfm.disabled = false;
  }
  $("#lastfm-state").textContent = accounts.lastfm
    ? t("Прослушивания уходят в профиль {name}", { name: accounts.lastfm }) + waiting
    : t("Отправляет прослушанные треки в ваш профиль Last.fm. Подключение откроет страницу Last.fm, где нужно разрешить доступ");
  const listenbrainz = $("#listenbrainz-connect");
  $("span", listenbrainz).textContent = t(accounts.listenbrainz ? "Отключить" : "Подключить");
  $("#listenbrainz-field").hidden = Boolean(accounts.listenbrainz);
  $("#listenbrainz-state").textContent = accounts.listenbrainz
    ? t("Прослушивания уходят в профиль {name}", { name: accounts.listenbrainz }) + waiting
    : t("Открытая замена Last.fm. Токен — на странице listenbrainz.org/settings");
}

async function onLastfmClick() {
  const button = $("#lastfm-connect");
  if (state.scrobble?.lastfm) {
    state.scrobble = await api().disconnect_scrobbler("lastfm");
    renderScrobbleAccounts();
    return;
  }
  const started = await api().connect_lastfm();
  if (started.error) {
    announce(started.error);
    return;
  }
  // Last.fm's page is open in the browser; the program asks until access is allowed
  state.lastfmWaiting = true;
  button.disabled = true;
  $("span", button).textContent = t("Жду разрешения на Last.fm…");
  for (let tries = 0; tries < 60 && state.lastfmWaiting; tries += 1) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const answer = await api().lastfm_connected().catch(() => ({ name: "" }));
    if (answer.name) {
      announce(t("Last.fm подключён: {name}", { name: answer.name }));
      break;
    }
  }
  state.lastfmWaiting = false;
  loadScrobbleAccounts();
}

async function onListenbrainzClick() {
  if (state.scrobble?.listenbrainz) {
    state.scrobble = await api().disconnect_scrobbler("listenbrainz");
    renderScrobbleAccounts();
    return;
  }
  const input = $("#listenbrainz-token");
  const token = input.value.trim();
  if (!token) {
    input.focus();
    return;
  }
  const button = $("#listenbrainz-connect");
  button.disabled = true;
  const answer = await api().connect_listenbrainz(token).catch((error) => ({ error: String(error) }));
  button.disabled = false;
  if (answer.error) {
    announce(answer.error);
    input.closest(".field").classList.add("invalid");
    return;
  }
  input.value = "";
  input.closest(".field").classList.remove("invalid");
  announce(t("ListenBrainz подключён: {name}", { name: answer.name }));
  loadScrobbleAccounts();
}
