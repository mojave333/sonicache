"use strict";

// The Sync page: players, phones and the classic iPod, the library over Wi-Fi

/* Devices: the library copied to a player or a phone that shows up as a drive,
   or to a folder chosen as one. Python works out the difference and copies;
   the page here says what the device is to get and what a sync would change. */

const DEVICE_POLL = 3000; // how often the drives are looked for while the synchronisation page is open

const DEVICE_POLL_SIDE = 10000; // and while only the column at the side shows them

// Drives come and go: they are looked for again every few seconds while the page is on screen, and less often
// while they are only rows in the column at the side. A narrow window's column has no rows to keep.
function watchDevices() {
  clearInterval(state.devices.timer);
  state.devices.timer = 0;
  const every = state.view === "sync" ? DEVICE_POLL : sideWide() ? DEVICE_POLL_SIDE : 0;
  if (!every) return;
  if (state.view === "sync" || !state.devices.loaded) loadDevices();
  if (!state.devices.wifi) loadWifi(); // the list says whether it is on
  state.devices.timer = setInterval(() => { if (!document.hidden) loadDevices(); }, every);
}

/* Phones over Wi-Fi: the library as a Subsonic server (subsonic.py), which phone
   players such as Amperfy and Ultrasonic play from and download for later */

function openWifi() {
  state.devices.open = { kind: "wifi" };
  renderWifi();
  showSyncPage();
  loadWifi();
}

// The page of the device or of the Wi-Fi server chosen in the list; with nothing plugged in, what to do about it
function showSyncPage() {
  const open = state.devices.open;
  const page = open?.kind === "wifi" ? $("#wifi-page") : open?.kind === "device" ? $("#device-page") : $("#sync-home");
  const changed = page.hidden;
  for (const other of [$("#sync-home"), $("#device-page"), $("#wifi-page")]) other.hidden = other !== page;
  // Until the drives have been looked for once, "no devices" would only flash up
  $("#sync-home").classList.toggle("waiting", !state.devices.loaded);
  if (changed) riseIn([...page.children]);
  renderDeviceSide();
}

// With nothing chosen the first device is opened, as the library opens on its albums
function openFirstDevice() {
  const { open, items } = state.devices;
  if (open || !items.length || state.view !== "sync") return false;
  openDevice(items[0].id);
  return true;
}

function closeSyncPage() {
  state.devices.open = null;
  if (!openFirstDevice()) showSyncPage();
}

// The name of what is open and where it is, and the buttons for it
function renderSyncHead() {
  const open = state.devices.open;
  const device = open?.kind === "device" ? state.devices.items.find((entry) => entry.id === open.id) : null;
  const wifi = open?.kind === "wifi";
  $("#sync-title-name").textContent = device ? device.name : wifi ? t("Телефон по Wi-Fi") : t("Синхронизация");
  // What keeps a device from syncing is said on its page, beside where its room would be
  $("#sync-summary").textContent = device ? [deviceWhere(device), device.note ? "" : deviceState(device)].filter(Boolean).join(" · ")
    : wifi ? t("iPhone и Android слушают библиотеку по домашней сети") : t("Музыка из библиотеки — на плеер, iPod или телефон");
  $("#device-open").hidden = !device || device.kind === "mtp"; // a phone over a cable has no path to open in Explorer
  $("#device-forget").hidden = !device || device.kind !== "folder";
  $("#device-sync").hidden = !device;
}

// In the column at the side, as the library's is, the list has nothing to open
function setSyncSideOpen(open, restoreFocus = true) {
  const view = $("#view-sync");
  const title = $("#sync-title");
  const wasOpen = view.classList.contains("side-open");
  open = open && !sideWide();
  view.classList.toggle("side-open", open);
  title.setAttribute("aria-expanded", String(open));
  if (open) ($("[aria-current=page]", $("#sync-side")) || $(".side-item", $("#sync-side"))).focus();
  else if (wasOpen && restoreFocus) title.focus();
}

function bindSyncSide() {
  const title = $("#sync-title");
  title.addEventListener("click", () => setSyncSideOpen(title.getAttribute("aria-expanded") !== "true"));
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#sync-side, #sync-title")) setSyncSideOpen(false, false);
  });
  $("#sync-side").addEventListener("click", async (event) => {
    const target = event.target;
    const device = target.closest("[data-device]");
    const open = state.devices.open;
    // From another section in the column at the side, the heading, "nothing connected" and every row lead here
    if (state.view !== "sync" && target.closest("button, .side-link")) showView("sync");
    if (target.closest(".side-link, #sync-none-side")) {
      setSyncSideOpen(false);
    } else if (device) {
      setSyncSideOpen(false);
      if (open?.kind !== "device" || open.id !== device.dataset.device) openDevice(device.dataset.device);
    } else if (target.closest("#sync-wifi")) {
      setSyncSideOpen(false);
      if (open?.kind !== "wifi") openWifi();
    } else if (target.closest("#sync-folder")) {
      setSyncSideOpen(false);
      const added = await api().add_device_folder();
      if (!added) return;
      await loadDevices();
      openDevice(added.id);
    }
  });
  // The heading is a way to the page, and answers the keys of a button
  $("#sync-side .side-link").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    event.currentTarget.click();
  });
}

async function loadWifi() {
  try {
    state.devices.wifi = await api().wifi();
  } catch (error) {
    console.error(error);
    return;
  }
  renderWifi();
  renderDeviceSide();
}

function wifiAddress(wifi) {
  return wifi.addresses.length ? `http://${wifi.addresses[0]}:${wifi.port}` : "";
}

function renderWifi() {
  const wifi = state.devices.wifi;
  if (!wifi) return;
  syncSwitch($("#wifi-on"), wifi.on);
  // A computer on several networks shows each way in; the first is the one its traffic goes out by
  $("#wifi-address").textContent = wifi.addresses.length
    ? wifi.addresses.map((address) => `http://${address}:${wifi.port}`).join("  ·  ")
    : t("Компьютер не подключён к сети");
  $("#wifi-user").textContent = wifi.user;
  $("#wifi-password").textContent = wifi.password;
  $("[data-wifi-copy=address]").disabled = !wifi.addresses.length;
  const error = $("#wifi-error");
  error.hidden = !wifi.error;
  error.textContent = wifi.error;
}

function bindWifi() {
  bindSwitch($("#wifi-on"), async (on) => {
    syncSwitch($("#wifi-on"), on);
    try {
      state.devices.wifi = await api().set_wifi(on);
    } catch (error) {
      console.error(error);
    }
    renderWifi();
    renderDeviceSide();
  });
  $("#wifi-new").addEventListener("click", async () => {
    const ok = await askConfirm({
      title: t("Сменить пароль?"),
      text: t("Телефоны, подключённые со старым паролем, перестанут слушать, пока в плеере не введут новый."),
      action: t("Сменить"),
      danger: false,
    });
    if (!ok) return;
    state.devices.wifi = await api().new_wifi_password();
    renderWifi();
  });
  $("#wifi-page").addEventListener("click", (event) => {
    const button = event.target.closest("[data-wifi-copy]");
    const wifi = state.devices.wifi;
    if (!button || !wifi) return;
    const text = { address: wifiAddress(wifi), user: wifi.user, password: wifi.password }[button.dataset.wifiCopy];
    if (!text) return;
    api().copy(text);
    showToast(t("Скопировано: {text}", { text }));
  });
}

async function loadDevices() {
  let items = [];
  try {
    items = await api().devices() || [];
  } catch (error) {
    console.error(error);
  }
  state.devices.items = items;
  const first = !state.devices.loaded;
  state.devices.loaded = true;
  if (openFirstDevice()) return; // the drives were read for the first time, or one was plugged in while there were none
  if (first && state.view === "sync") showSyncPage();
  renderDeviceSide();
  const id = openDeviceId();
  if (!id) return;
  const device = items.find((entry) => entry.id === id);
  if (device) renderDeviceSpace(device);
  else if (!state.devices.sync.running) closeSyncPage(); // unplugged while its page was open
}

function openDeviceId() {
  const open = state.devices.open;
  return open?.kind === "device" && state.view === "sync" ? open.id : "";
}

function renderDeviceSide() {
  const id = openDeviceId();
  const { open, wifi, items, sync } = state.devices;
  const wifiState = $("#sync-wifi-state");
  wifiState.textContent = wifi?.running ? t("Включено") : "";
  wifiState.classList.toggle("ok", Boolean(wifi?.running));
  // The drives are looked for every few seconds: rows drawn again for nothing would take the focus away
  const rows = JSON.stringify([language(), items.map((device) => [device.id, device.name, device.note, device.free,
    device.synced, sync.running && sync.device === device.id ? sync.done : -1])]);
  if (rows !== state.devices.sideRows) {
    state.devices.sideRows = rows;
    renderDeviceRows();
  }
  for (const item of $$("#sync-side .side-item")) {
    // With nothing plugged in, the page says what to plug in, and "nothing connected" is its row
    const current = item.id === "sync-wifi" ? open?.kind === "wifi"
      : item.id === "sync-none-side" ? !open
      : open?.kind === "device" && item.dataset.device === open.id;
    if (current) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
  foldDevices();
  renderSyncHead();
  // What the lists offer comes from the library and the playlists, which may be read only now
  const shown = `${state.library.items.length}|${state.playlists.items.length}`;
  if (id && shown !== state.devices.shown) renderDeviceChoice();
}

// Where a device is, as its page names it: a phone over a cable has no path to show
function deviceWhere(device) {
  return device.kind === "mtp" ? t("По кабелю · папка Music/Sonicache")
    : device.kind === "ipod" ? t("iPod · {root}", { root: device.root }) : device.root;
}

// What there is to know before opening it: what keeps it from syncing, how its sync goes, or whether it had one
function deviceState(device) {
  const sync = state.devices.sync;
  if (device.note) return device.note;
  if (sync.running && sync.device === device.id) {
    return t("Синхронизация: {done} из {total}", { done: Math.min(sync.done + 1, sync.total), total: sync.total });
  }
  return device.synced ? t("Уже синхронизировалось") : t("Ещё не синхронизировалось");
}

// The devices in the list, each with the room left on it, as the library's list has its counts
function renderDeviceRows() {
  const { items, sync } = state.devices;
  $("#sync-none-side").hidden = items.length > 0;
  $("#sync-devices").replaceChildren(...items.map((device) => {
    const working = sync.running && sync.device === device.id;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "side-item" + (device.note ? " not-ready" : "") + (working ? " working" : "");
    row.dataset.device = device.id;
    row.innerHTML = '<span class="side-name"></span><span class="side-n"></span>';
    $(".side-name", row).textContent = device.name;
    const count = $(".side-n", row);
    const free = device.note ? "" : t("Свободно {free} из {total}", { free: formatSize(device.free), total: formatSize(device.total) });
    row.title = [device.name, deviceWhere(device), free, deviceState(device)].filter(Boolean).join("\n");
    if (device.note) {
      count.innerHTML = '<svg class="icon sm" aria-hidden="true"><use href="#i-alert"/></svg>';
    } else if (working) {
      count.textContent = `${Math.min(sync.done + 1, sync.total)}/${sync.total}`;
    } else {
      count.textContent = formatSize(device.free);
    }
    return row;
  }));
}

function openDevice(id) {
  const device = state.devices.items.find((entry) => entry.id === id);
  if (!device) return;
  const library = state.library;
  state.devices.open = { kind: "device", id };
  state.devices.selection = structuredClone(device.selection);
  state.devices.plan = null;
  $("#device-filter").value = "";
  fillDevicePage(device);
  showSyncPage(); // its row in the list is marked once the page is there
  // The lists are made of the library and the playlists: they are read if they were not yet
  if (library.stale && !library.loading) loadLibrary();
  if (state.playlists.stale && !state.playlists.loading) loadPlaylists();
  previewDevice();
}

function fillDevicePage(device) {
  $("#device-transcode").disabled = !device.ffmpeg;
  // An iPod plays mp3 and m4a only: anything else is made mp3 whether this is on or not
  $("#device-transcode-desc").textContent = !device.ffmpeg ? t("Для пережатия нужен ffmpeg")
    : device.kind === "ipod" ? t("iPod играет только mp3 и m4a: остальное пережимается всегда. Включите, чтобы пережимать и m4a")
    : t("Для плееров, которые не играют opus и m4a, и чтобы больше влезло. Файлы в библиотеке не меняются");
  renderDeviceSpace(device);
  renderDeviceChoice();
  renderDevicePreview();
}

// How full the device is now, and how full it will be after the sync
function renderDeviceSpace(device) {
  const page = $("#device-page");
  // A locked phone shows no storage yet: what to do about it is said in its place
  $(".device-space", page).hidden = Boolean(device.note);
  $("#device-warn").hidden = !device.note;
  $("#device-warn span").textContent = device.note;
  if (device.note) return;
  const total = Math.max(1, device.total);
  const taken = device.total - device.free;
  const plan = state.devices.plan;
  const ready = plan && !plan.error;
  const added = ready ? Math.max(0, Math.min(plan.need, device.free)) : 0;
  $(".space-taken", page).style.width = `${(100 * taken) / total}%`;
  $(".space-after", page).style.width = `${(100 * added) / total}%`;
  $("#space-taken-n").textContent = formatSize(taken);
  $("#space-added").hidden = !added;
  $("#space-added-n").textContent = formatSize(added);
  $("#space-free-n").textContent = formatSize(ready ? Math.max(0, device.free - plan.need) : device.free);
}

// What can be chosen for a device, each as the library and the side list name it
function deviceEntries(kind) {
  if (kind === "playlists") {
    return state.playlists.items.filter((entry) => !entry.smart || entry.count)
      .map((entry) => ({ key: entry.id, name: playlistName(entry), sub: String(entry.count || "") }));
  }
  if (kind === "artists") {
    const unknown = t("Без исполнителя");
    return libraryArtists().filter((artist) => artist.name !== unknown)
      .sort((a, b) => COLLATOR.compare(a.name, b.name))
      .map((artist) => ({ key: artist.key, name: artist.name, sub: artistCounts(artist) }));
  }
  return [...state.library.items].sort((a, b) => COLLATOR.compare(a.artist, b.artist) || COLLATOR.compare(a.title, b.title))
    .map((item) => ({ key: item.path, name: item.title, sub: [item.artist, item.album ? item.year : t("трек")].filter(Boolean).join(" · ") }));
}

const DEVICE_LISTS = ["playlists", "artists", "albums"];

function renderDeviceChoice() {
  const selection = state.devices.selection;
  if (!selection) return;
  state.devices.shown = `${state.library.items.length}|${state.playlists.items.length}`;
  state.devices.entries = Object.fromEntries(DEVICE_LISTS.map((kind) => [kind, deviceEntries(kind)]));
  const transcode = $("#device-transcode");
  syncRadios($("#device-scope"), "data-scope", selection.all ? "all" : "pick");
  syncSwitch(transcode, selection.transcode && !transcode.disabled);
  $("#device-bitrate").value = String(selection.bitrate);
  $("#device-bitrate").disabled = transcode.disabled || !selection.transcode;
  // The whole library needs no lists: the place says so instead
  $("#device-page").classList.toggle("whole", selection.all);
  renderDeviceCounts();
  renderDeviceItems();
}

// Each tab says how much of its list is chosen
function renderDeviceCounts() {
  const { selection, entries, tab } = state.devices;
  for (const button of $$("#device-tabs [data-list]")) {
    const kind = button.dataset.list;
    const chosen = new Set(selection[kind]);
    const total = entries[kind].length;
    const picked = entries[kind].filter((entry) => chosen.has(entry.key)).length;
    $(".count", button).textContent = picked ? t("{chosen} из {total}", { chosen: picked, total }) : String(total || "");
    button.setAttribute("aria-selected", String(kind === tab));
    button.tabIndex = kind === tab ? 0 : -1;
  }
}

// The list of the tab open, narrowed by the filter
function renderDeviceItems() {
  const { selection, entries, tab } = state.devices;
  const chosen = new Set(selection[tab]);
  const needle = $("#device-filter").value.trim().toLocaleLowerCase();
  const shown = needle ? entries[tab].filter((entry) => `${entry.name} ${entry.sub}`.toLocaleLowerCase().includes(needle)) : entries[tab];
  $("#device-lists").replaceChildren(...shown.map((entry) => {
    const row = document.createElement("label");
    row.className = "device-item";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.dataset.key = entry.key;
    check.checked = chosen.has(entry.key);
    const name = document.createElement("span");
    name.className = "device-item-name";
    name.textContent = entry.name;
    name.title = entry.name;
    const sub = document.createElement("span");
    sub.className = "device-item-sub";
    sub.textContent = entry.sub;
    row.append(check, name, sub);
    return row;
  }));
}

// Every change is kept at once, and what a sync would do is worked out again a moment later
function changeDevice() {
  const id = openDeviceId();
  if (!id) return;
  const selection = state.devices.selection;
  state.devices.saving = api().save_device(id, selection).catch((error) => console.error(error));
  const device = state.devices.items.find((entry) => entry.id === id);
  if (device) device.selection = structuredClone(selection);
  clearTimeout(state.devices.planTimer);
  state.devices.planTimer = setTimeout(previewDevice, 300);
}

async function previewDevice() {
  const id = openDeviceId();
  if (!id) return;
  const token = ++state.devices.planToken;
  state.devices.plan = null;
  renderDevicePreview();
  await state.devices.saving;
  let plan;
  try {
    plan = await api().device_plan(id);
  } catch (error) {
    plan = { error: String(error) };
  }
  if (token !== state.devices.planToken || id !== openDeviceId()) return;
  state.devices.plan = plan;
  renderDevicePreview();
  const device = state.devices.items.find((entry) => entry.id === id);
  if (device) renderDeviceSpace(device);
}

function nothingChosen(selection) {
  return !selection.all && !selection.playlists.length && !selection.albums.length && !selection.artists.length;
}

function renderDevicePreview() {
  const { plan, selection, sync } = state.devices;
  const id = openDeviceId();
  const running = sync.running && sync.device === id;
  let lead = "";
  const notes = [];
  if (running) {
    lead = t("Синхронизация: {done} из {total}", { done: Math.min(sync.done + 1, sync.total), total: sync.total });
    notes.push(sync.title, t("не отключайте устройство"));
  } else if (!plan) {
    lead = t("Считаем…");
  } else if (plan.error) {
    lead = plan.error;
  } else if (!plan.fits) {
    lead = t("Не хватает {size}", { size: formatSize(plan.need - plan.free) });
  } else if (!plan.tracks && !plan.deleted) {
    lead = selection && nothingChosen(selection) ? t("Отметьте, что взять на устройство") : t("Всё уже на устройстве");
  } else {
    if (plan.tracks) {
      lead = t("Добавится {tracks} {trackWord} ({size})", { tracks: plan.tracks,
        trackWord: plural(plan.tracks, "трек", "трека", "треков"), size: formatSize(plan.bytes) });
    }
    if (plan.deleted) {
      const gone = t("уберётся {deleted}", { deleted: plan.deleted });
      if (lead) notes.push(gone);
      else lead = gone;
    }
    notes.push(t("после синхронизации свободно {free}", { free: formatSize(Math.max(0, plan.free - plan.need)) }));
  }
  if (!running && plan && !plan.error) {
    if (plan.conflicts) notes.push(t("чужих файлов на месте: {conflicts}", { conflicts: plan.conflicts }));
    if (plan.missing) notes.push(t("нет на диске: {missing}", { missing: plan.missing }));
    if (plan.unplayable) notes.push(t("iPod не сыграет без ffmpeg: {count}", { count: plan.unplayable }));
  }
  $("#device-preview").textContent = lead.charAt(0).toUpperCase() + lead.slice(1);
  $("#device-notes").textContent = notes.filter(Boolean).join(" · ");
  // How the last sync of this device went stays where it was asked for
  const result = $("#device-result");
  result.hidden = running || !sync.note || sync.device !== id;
  result.textContent = sync.note;
  const progress = $("#device-progress");
  progress.hidden = !running;
  if (running) $("span", progress).style.width = `${(100 * Math.min(sync.done, sync.total)) / Math.max(1, sync.total)}%`;
  const button = $("#device-sync");
  button.disabled = !running && (sync.running || !plan || Boolean(plan.error) || !plan.fits);
  $("span", button).textContent = running ? t("Остановить") : t("Синхронизировать");
}

async function syncDevice() {
  const id = openDeviceId();
  if (!id) return;
  if (state.devices.sync.running) {
    api().stop_sync(); // the file in hand is finished, the rest wait for the next time
    return;
  }
  if (!await api().sync_device(id)) return;
  clearTimeout(state.devices.sync.timer);
  Object.assign(state.devices.sync, { running: true, device: id, done: 0, total: 0, title: "", note: "" });
  renderDevicePreview();
  renderStatus();
}

function onSyncEvent(event) {
  const sync = state.devices.sync;
  if (event.state === "running") {
    Object.assign(sync, { running: true, device: event.device, done: event.done, total: event.total, title: event.title });
  } else {
    sync.running = false;
    sync.note = syncSummary(event);
    announce(sync.note);
    clearTimeout(sync.timer);
    sync.timer = setTimeout(() => { sync.note = ""; renderStatus(); }, 30000);
    loadDevices();
    if (openDeviceId() === event.device) previewDevice();
  }
  renderDeviceSide(); // the row of the device, in the column at the side, and the bar count the files too
  renderDevicePreview();
  renderStatus();
}

function syncSummary({ name, copied, deleted, failed, conflicts, error, state: how }) {
  if (error && !copied && !deleted) return error;
  const parts = [t("На «{name}» записано {copied}, удалено {deleted}", { name, copied, deleted })];
  if (failed.length) parts.push(t("не удалось: {failed}", { failed: failed.length }));
  if (conflicts) parts.push(t("чужих файлов на месте: {conflicts}", { conflicts }));
  if (error) parts.push(error);
  const text = parts.join(" · ");
  return how === "stopped" ? t("Остановлено. {text}", { text }) : text;
}

function bindDevices() {
  const page = $("#device-page");
  const transcode = $("#device-transcode");
  radioGroup($("#device-scope"), "data-scope", (scope) => {
    state.devices.selection.all = scope === "all";
    renderDeviceChoice();
    changeDevice();
  });
  bindSwitch(transcode, (on) => {
    if (transcode.disabled) return;
    state.devices.selection.transcode = on;
    renderDeviceChoice();
    changeDevice();
  });
  $("#device-bitrate").addEventListener("change", (event) => {
    state.devices.selection.bitrate = Number(event.target.value);
    changeDevice();
  });
  $("#device-lists").addEventListener("change", (event) => {
    const box = event.target.closest("input[type=checkbox]");
    if (!box) return;
    const kind = state.devices.tab;
    const chosen = new Set(state.devices.selection[kind]);
    if (box.checked) chosen.add(box.dataset.key);
    else chosen.delete(box.dataset.key);
    state.devices.selection[kind] = [...chosen];
    renderDeviceCounts();
    changeDevice();
  });
  radioGroup($("#device-tabs"), "data-list", (kind) => {
    if (kind === state.devices.tab) return;
    state.devices.tab = kind;
    $("#device-lists").scrollTop = 0;
    renderDeviceCounts();
    renderDeviceItems();
  });
  $("#device-filter").addEventListener("input", renderDeviceItems);
  $("#device-sync").addEventListener("click", syncDevice);
  $("#device-open").addEventListener("click", () => {
    const device = state.devices.items.find((entry) => entry.id === openDeviceId());
    if (device) api().open_folder(device.root);
  });
  $("#device-forget").addEventListener("click", async () => {
    const id = openDeviceId();
    if (!id) return;
    await api().forget_device_folder(id);
    closeSyncPage();
    loadDevices();
  });
  page.hidden = true;
}
