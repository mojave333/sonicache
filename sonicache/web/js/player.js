"use strict";

// The player: the queue it plays, the sound and the equalizer, crossfades, the listens
// counted, the queue panel and the menu of a track

/* Player */

// Two audio elements take turns: while one plays, the other has the next
// track of the queue loaded, so an album goes on from track to track without
// the pause of fetching the next one. The queue is a list of paths: an album
// page, or the Tracks tab as it was shown when a track was started.
const player = {
  audio: new Audio(),
  spare: new Audio(),
  ahead: null, // { path, info } of the track the spare element holds
  aheadToken: 0,
  queue: [],
  unshuffled: null, // the queue in its own order while it plays shuffled
  artists: new Map(), // path to artist, so a shuffle can keep one band's tracks apart
  source: "album", // "album": an album page in its order; "tracks": the Tracks tab
  index: -1,
  info: null,
  lines: [], // synced lyrics: { time, text, node }
  current: -1,
  token: 0,
  failures: 0, // files in a row that would not play
  seeking: false,
  previousView: "library",
  fullscreen: false,
  volume: 0.8, // the slider's; ReplayGain turns the element down from it
  muted: false,
  shuffle: false,
  repeat: "off", // "all" goes round the queue, "one" plays the track again
  taskbar: "",
  presence: "", // what Discord was last told
  meta: new Map(), // path → title, artists and length of what was queued, for the queue's rows
  handover: null, // what starts the next track on the dot, with a cancel() of its own
  fading: 0, // the timer that frees the spare element once the last track has faded out
  fadeUntil: 0, // the graph's time a crossfade's rise ends at
  listen: null, // how much of this track has played, for counting it as listened to
};

const REPEATS = ["off", "all", "one"];

// How far the arrow keys move: seconds through the track, and the volume
const SEEK_STEP = 5;

const VOLUME_STEP = 0.05;

// How the player was left last time: the volume, the shuffle and the repeat
// come from the settings file, which outlives the window's own storage
function applyPlayerSettings(settings) {
  const volume = Number(settings.volume);
  player.volume = Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : player.volume;
  player.shuffle = Boolean(settings.shuffle);
  player.repeat = REPEATS.includes(settings.repeat) ? settings.repeat : "off";
  $("#player-volume").value = Math.round(player.volume * 100);
  applyVolume();
  renderPlayerModes();
}

// The settings file is written once the slider comes to rest, not on every
// step of a drag
let playerSaveTimer = 0;

function rememberPlayer(patch) {
  Object.assign(state.settings, patch);
  clearTimeout(playerSaveTimer);
  playerSaveTimer = setTimeout(() => api().save_settings(state.settings), 400);
}

function bindPlayer() {
  $("#player-volume").value = Math.round(player.volume * 100);
  for (const element of [player.audio, player.spare]) {
    element.preload = "auto";
    element.crossOrigin = "anonymous"; // Web Audio hears a file from the local server only so
    // Only the element that plays speaks; the other is loading what comes next
    const own = (handler) => (event) => { if (event.target === player.audio) handler(event); };
    element.addEventListener("timeupdate", own(renderPlayerTime));
    element.addEventListener("durationchange", own(renderPlayerTime));
    element.addEventListener("durationchange", own(() => syncPresence()));
    element.addEventListener("seeked", own(() => syncPresence(true)));
    element.addEventListener("timeupdate", own(() => {
      countListen();
      watchHandover();
      if (Date.now() - session.saved > 5000) rememberSession(); // the place, every few seconds
    }));
    element.addEventListener("pause", own(() => rememberSession()));
    element.addEventListener("seeked", own(() => rememberSession()));
    element.addEventListener("seeking", own(cancelHandover));
    element.addEventListener("pause", own(cancelHandover));
    element.addEventListener("play", own(renderPlayerButton));
    element.addEventListener("pause", own(renderPlayerButton));
    element.addEventListener("playing", own(() => { player.failures = 0; }));
    element.addEventListener("ended", own(onTrackEnded));
    element.addEventListener("error", (event) => {
      if (event.target !== player.audio) {
        // The next track is fetched again when its turn comes
        if (player.ahead && event.target.src === player.ahead.info.url) player.ahead = null;
        return;
      }
      if (!player.info) return;
      announce(t("Не получилось сыграть «{title}»", { title: player.info.title }));
      skipBroken();
    });
  }
  $("#player-play").addEventListener("click", togglePlay);
  $("#player-prev").addEventListener("click", () => stepTrack(-1));
  $("#player-next").addEventListener("click", () => stepTrack(1));
  $("#player-shuffle").addEventListener("click", toggleShuffle);
  $("#player-repeat").addEventListener("click", cycleRepeat);
  $("#player-stop").addEventListener("click", stopPlayer);
  $("#player-open").addEventListener("click", toggleNowPlaying);
  $("#player-lyrics").addEventListener("click", toggleNowPlaying);
  $("#now-close").addEventListener("click", closeNowPlaying);
  $("#player-full").addEventListener("click", toggleFullscreen);
  const position = $("#player-position");
  position.addEventListener("input", () => {
    player.seeking = true;
    fillRange(position);
    const length = player.audio.duration;
    if (length) $("#player-time").textContent = formatDuration(position.value / 1000 * length);
  });
  position.addEventListener("change", () => {
    if (player.audio.duration) player.audio.currentTime = position.value / 1000 * player.audio.duration;
    player.seeking = false;
  });
  $("#player-volume").addEventListener("input", (event) => setVolume(event.target.value / 100));
  $("#player-mute").addEventListener("click", () => setMuted(!player.muted));
  $("#now-lyrics").addEventListener("scroll", (event) => {
    event.currentTarget.classList.toggle("scrolled", event.currentTarget.scrollTop > 4);
  });
  $("#now-lyrics").addEventListener("click", (event) => {
    const line = event.target.closest("[data-time]");
    if (line) player.audio.currentTime = Number(line.dataset.time);
  });
  $("#library-tracks").addEventListener("click", onTrackCoverClick, true);
  $(".album-list", $("#album-page")).addEventListener("click", (event) => {
    const number = event.target.closest(".album-track[data-path] .n");
    if (number) playAlbumPage(number.closest(".album-track"));
  });
  $(".album-list", $("#album-page")).addEventListener("dblclick", (event) => {
    const row = event.target.closest(".album-track[data-path]");
    if (row) playAlbumPage(row);
  });
  document.addEventListener("keydown", onPlayerKey);
  if ("mediaSession" in navigator) {
    // The keyboard's media keys and the system's own media controls
    const handlers = {
      play: () => {
        wakeSound();
        player.audio.play();
      },
      pause: () => player.audio.pause(),
      stop: stopPlayer,
      previoustrack: () => stepTrack(-1),
      nexttrack: () => stepTrack(1),
      seekto: (details) => { player.audio.currentTime = details.seekTime; },
      seekbackward: (details) => { player.audio.currentTime = Math.max(0, player.audio.currentTime - (details.seekOffset || 10)); },
      seekforward: (details) => { player.audio.currentTime += details.seekOffset || 10; },
    };
    for (const [action, handler] of Object.entries(handlers)) {
      try {
        navigator.mediaSession.setActionHandler(action, handler);
      } catch {
        // an engine that knows no such action simply goes without it
      }
    }
  }
  bindEqualizer();
  bindQueue();
  bindTrackMenu();
  bindPlaylists();
  bindPlayerSettings();
  renderPlayerModes();
  renderPlayerVolume();
}

// The player's keys, as media players have them: Space, the arrows for the
// position and the volume, M for the sound, L for the words, F for the whole
// screen. Elsewhere than in Now playing the arrows move through lists and
// cards, so there they are the player's only when nothing else has the focus.
function onPlayerKey(event) {
  if (!player.info || event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
  const target = event.target instanceof Element ? event.target : null;
  if (target?.closest("input:not([type=range]), textarea, select, [contenteditable]")) return;
  const now = state.view === "now";
  const free = now || !target || target === document.body || Boolean(target.closest("#player"));
  const slider = Boolean(target?.closest("input[type=range]")); // a slider moves itself with the arrows
  const arrow = { ArrowLeft: -SEEK_STEP, ArrowRight: SEEK_STEP }[event.key];
  const volume = { ArrowUp: VOLUME_STEP, ArrowDown: -VOLUME_STEP }[event.key];
  if (event.code === "Space") {
    if (!now || target?.closest("button, input")) return; // elsewhere Space picks rows and presses buttons
    togglePlay();
  } else if (arrow && free && !slider) {
    const length = player.audio.duration || 0;
    player.audio.currentTime = Math.min(Math.max(0, player.audio.currentTime + arrow), length || Infinity);
  } else if (volume && free && !slider) {
    setVolume(player.volume + volume);
  } else if (event.code === "KeyM") {
    setMuted(!player.muted);
  } else if (event.code === "KeyL") {
    toggleNowPlaying();
  } else if (event.code === "KeyQ") {
    toggleQueue();
  } else if (event.code === "KeyF" && now) {
    toggleFullscreen();
  } else {
    return;
  }
  event.preventDefault();
}

// The album page's own files, in the order shown; starting at a row or a position
function playAlbumPage(start) {
  if ($("#album-page").dataset.kind === "playlist") {
    playPlaylistPage(typeof start === "number" ? null : start.dataset.path);
    return;
  }
  rememberTracks($("#album-page").tracks || []);
  const rows = [...$$(".album-list .album-track[data-path]", $("#album-page"))];
  if (!rows.length) return;
  const index = typeof start === "number" ? start : Math.max(0, rows.indexOf(start));
  const artists = new Map(rows.map((row) => [row.dataset.path, $(".artists", row)?.textContent || ""]));
  playQueue(rows.map((row) => row.dataset.path), index, "album", artists);
}

function playQueue(paths, index, source, artists = new Map()) {
  if (!paths.length) return;
  const start = Math.min(Math.max(0, index), paths.length - 1);
  Object.assign(player, { source, artists, failures: 0, unshuffled: null, queue: paths, index: start });
  if (player.shuffle) shuffleQueue();
  session.queueChanged = true;
  loadTrack();
}

// The player as it was left: the queue, the track and the place in it are
// kept between runs, and the next start opens with that track, paused
const session = { timer: 0, queueChanged: false, saved: 0 };

function rememberSession(queueChanged = false) {
  if (queueChanged) session.queueChanged = true;
  clearTimeout(session.timer);
  session.timer = setTimeout(saveSession, 800);
}

function saveSession() {
  session.saved = Date.now();
  if (!player.info || !player.queue.length) {
    Promise.resolve().then(() => api().save_player(null)).catch(() => {});
    return;
  }
  const data = { index: player.index, path: player.queue[player.index], position: player.audio.currentTime || 0 };
  if (session.queueChanged) {
    session.queueChanged = false;
    Object.assign(data, { queue: player.queue.map(trackMeta), unshuffled: player.unshuffled, source: player.source });
  }
  Promise.resolve().then(() => api().save_player(data)).catch(() => {});
}

async function restorePlayer() {
  let saved = null;
  try {
    saved = await api().player_session();
  } catch (error) {
    console.error(error);
  }
  if (!saved || player.info) return; // something started meanwhile
  rememberTracks(saved.queue);
  const paths = saved.queue.map((track) => track.path);
  Object.assign(player, {
    queue: paths, index: saved.index, source: saved.source, failures: 0,
    unshuffled: saved.unshuffled?.length ? saved.unshuffled : null,
    artists: new Map(saved.queue.map((track) => [track.path, track.album_artist || track.artists || ""])),
  });
  loadTrack({ paused: true, at: saved.position });
}

// The whole library, or the genre it is narrowed to, shuffled from a random track
async function shuffleLibrary() {
  await loadTracks();
  const tracks = state.library.trackList.items.filter((track) => inGenre(track.genre));
  if (!tracks.length) return;
  if (!player.shuffle) {
    player.shuffle = true;
    rememberPlayer({ shuffle: true });
    renderPlayerModes();
  }
  playQueue(tracks.map((track) => track.path), Math.floor(Math.random() * tracks.length), "tracks", trackArtists(tracks));
}

function trackArtists(tracks) {
  return new Map(tracks.map((track) => [track.path, track.album_artist || track.artists || ""]));
}

async function loadTrack({ fade = 0, outgoing = null, paused = false, at = 0 } = {}) {
  cancelHandover();
  const token = ++player.token;
  const path = player.queue[player.index];
  let info = null;
  if (player.ahead?.path === path) {
    // Loaded while the last one played: the elements swap and it starts at once
    info = player.ahead.info;
    [player.audio, player.spare] = [player.spare, player.audio];
  } else {
    try {
      info = await api().play(path);
    } catch (error) {
      console.error(error);
    }
    if (token !== player.token) return;
    if (!info) {
      skipBroken();
      return;
    }
    player.audio.src = info.url;
    if (at > 0) {
      const element = player.audio;
      element.addEventListener("loadedmetadata", () => { element.currentTime = Math.min(at, (element.duration || at) - 1); },
                               { once: true });
    }
  }
  if (outgoing && outgoing === player.spare) releaseOutgoing(outgoing, fade);
  else clearSpare();
  if (!fade) player.fadeUntil = 0; // a skip during a crossfade takes the new track at its own level
  player.info = info;
  resetListen();
  applyVolume(fade);
  if (paused) {
    player.listen.at = at;
    renderPlayerButton();
  } else {
    wakeSound();
    player.audio.play().catch(() => renderPlayerButton());
  }
  rememberSession();
  renderPlayer();
  loadLyrics(path, token);
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: info.title, artist: info.artists, album: info.album,
      artwork: info.cover ? [{ src: info.cover, sizes: "512x512" }] : [],
    });
  }
  if (!outgoing) loadAhead(); // after a handover it loads once the last track has let the spare go
}

// The track after this one is fetched and loaded while this one plays
async function loadAhead() {
  const token = ++player.aheadToken;
  const next = player.repeat === "one" ? -1 : stepIndex(1);
  const path = next >= 0 && next !== player.index ? player.queue[next] : "";
  if (!path) return;
  let info = null;
  try {
    info = await api().play(path);
  } catch {
    // fetched when its turn comes
  }
  if (token !== player.aheadToken || !info) return;
  player.ahead = { path, info };
  player.spare.src = info.url;
  player.spare.load();
}

function clearSpare() {
  clearTimeout(player.fading);
  player.fading = 0;
  player.ahead = null;
  player.aheadToken += 1;
  player.spare.pause();
  player.spare.removeAttribute("src");
  player.spare.load();
}

// Where a step lands in the queue: round it while it repeats, -1 past its ends
function stepIndex(step) {
  const { queue, index } = player;
  if (player.repeat === "all" && queue.length) return (index + step + queue.length) % queue.length;
  return index + step >= 0 && index + step < queue.length ? index + step : -1;
}

// Forward or back through the queue. At the end the player stops unless it
// repeats; "back" in the first seconds of a track goes to the one before,
// later it starts over.
function stepTrack(step, automatic = false) {
  if (!player.queue.length) return;
  if (step < 0 && player.audio.currentTime > 3) {
    player.audio.currentTime = 0;
    return;
  }
  // Round the queue again shuffled: a new order, not the last one once more
  if (step > 0 && player.shuffle && player.repeat === "all" && player.index + step >= player.queue.length
      && player.queue.length > 2) {
    const last = player.queue[player.index];
    player.queue = spreadShuffle(player.queue);
    session.queueChanged = true;
    if (player.queue[0] === last) player.queue.push(player.queue.shift());
    player.index = 0;
    loadTrack();
    return;
  }
  const next = stepIndex(step);
  if (next < 0) {
    if (automatic) {
      player.audio.pause();
      player.audio.currentTime = 0;
      renderPlayerButton();
    }
    return;
  }
  player.index = next;
  loadTrack();
}

function onTrackEnded() {
  if (player.repeat === "one") {
    player.audio.currentTime = 0;
    resetListen(); // played again, it counts again
    player.audio.play();
    return;
  }
  stepTrack(1, true);
}

// A file that will not play is passed over; a queue of nothing but such files stops
function skipBroken() {
  player.failures += 1;
  if (player.failures >= player.queue.length) {
    player.failures = 0;
    player.audio.pause();
    renderPlayerButton();
    return;
  }
  stepTrack(1, true);
}

function togglePlay() {
  if (!player.info) return;
  wakeSound();
  if (player.audio.paused) player.audio.play();
  else player.audio.pause();
}

// Shuffled, the track that plays stays first and the rest follow in any
// order; back in order, the queue is the album or the list as it was
function toggleShuffle() {
  session.queueChanged = true;
  player.shuffle = !player.shuffle;
  rememberPlayer({ shuffle: player.shuffle });
  if (player.queue.length) {
    if (player.shuffle) {
      shuffleQueue();
    } else if (player.unshuffled) {
      const current = player.queue[player.index];
      player.queue = player.unshuffled;
      player.index = Math.max(0, player.queue.indexOf(current));
      player.unshuffled = null;
    }
    clearSpare();
    loadAhead();
    applyVolume(); // shuffled, each track takes its own ReplayGain
  }
  renderPlayerModes();
  if (player.info) renderPlayer();
}

function shuffleQueue() {
  const { queue, index } = player;
  const order = spreadShuffle(queue);
  const at = order.indexOf(queue[index]);
  player.unshuffled = queue;
  player.queue = [...order.slice(at), ...order.slice(0, at)]; // turned round, the gaps stay as they were
  player.index = 0;
}

// Shuffled the way listeners hear as random. Plain random order clumps: a
// band with a tenth of the tracks comes back every few songs and now and then
// twice in a row. So each artist's tracks are spread evenly through the whole
// queue, each at a random place in its own stretch, and the artists'
// stretches start at random, which keeps them from following one another in
// step (Spotify's way since 2014).
function spreadShuffle(paths) {
  const groups = new Map();
  for (const path of paths) {
    const artist = artistKey(player.artists.get(path));
    if (!groups.has(artist)) groups.set(artist, []);
    groups.get(artist).push(path);
  }
  const placed = [];
  for (const group of groups.values()) {
    for (let i = group.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [group[i], group[j]] = [group[j], group[i]];
    }
    const stretch = 1 / group.length;
    const start = Math.random() * stretch;
    group.forEach((path, i) => placed.push({ path, at: start + i * stretch + (Math.random() - 0.5) * stretch * 0.3 }));
  }
  return placed.sort((a, b) => a.at - b.at).map((entry) => entry.path);
}

// "Sonic Youth, Lydia Lunch" and "sonic youth" are one band to keep apart
function artistKey(text) {
  return String(text || "").split(/,|;|&|\s+(?:feat|ft)\.?\s/i)[0].trim().toLocaleLowerCase();
}

function cycleRepeat() {
  player.repeat = REPEATS[(REPEATS.indexOf(player.repeat) + 1) % REPEATS.length];
  rememberPlayer({ repeat: player.repeat });
  if (player.queue.length) {
    clearSpare();
    loadAhead();
  }
  renderPlayerModes();
  if (player.info) renderPlayer();
}

function renderPlayerModes() {
  const shuffle = $("#player-shuffle");
  shuffle.setAttribute("aria-pressed", String(player.shuffle));
  shuffle.title = t(player.shuffle ? "Играть по порядку" : "Играть вперемешку");
  shuffle.setAttribute("aria-label", shuffle.title);
  const repeat = $("#player-repeat");
  repeat.setAttribute("aria-pressed", String(player.repeat !== "off"));
  swapIcon($("use", repeat), player.repeat === "one" ? "#i-repeat-one" : "#i-repeat");
  repeat.title = t({ off: "Повторять очередь", all: "Повторять этот трек", one: "Не повторять" }[player.repeat]);
  repeat.setAttribute("aria-label", repeat.title);
}

function setVolume(volume) {
  player.volume = Math.min(1, Math.max(0, Math.round(volume * 100) / 100));
  player.muted = false;
  $("#player-volume").value = Math.round(player.volume * 100);
  rememberPlayer({ volume: player.volume });
  applyVolume();
}

function setMuted(muted) {
  player.muted = muted;
  applyVolume();
}

// ReplayGain: an album page played in its order takes the album's gain, so a
// quiet interlude stays quiet beside a loud single; shuffled, or from the
// Tracks tab, each track takes its own. The element cannot play louder than
// the file, so a track quieter than the reference stays at the slider's volume.
function applyVolume(fadeIn = 0) {
  const gain = player.info?.gain || {};
  const inOrder = player.source === "album" && !player.shuffle;
  const decibels = (inOrder ? gain.album ?? gain.track : gain.track ?? gain.album) ?? 0;
  const replayGain = Math.min(1, 10 ** (decibels / 20));
  if (sound.context) {
    for (const element of [player.audio, player.spare]) {
      element.volume = 1;
      element.muted = false;
    }
    const now = sound.context.currentTime;
    if (fadeIn > 0) {
      setElementGain(player.audio, replayGain, fadeIn, 0);
      player.fadeUntil = now + fadeIn;
    } else if (now >= player.fadeUntil) {
      setElementGain(player.audio, replayGain); // a crossfade's rise is left to finish
    }
    sound.master.gain.setTargetAtTime(player.muted ? 0 : player.volume, now, 0.015);
  } else {
    player.audio.volume = player.volume * replayGain;
    player.audio.muted = player.muted;
  }
  renderPlayerVolume();
}

function stopPlayer() {
  cancelHandover();
  clearTimeout(session.timer);
  Promise.resolve().then(() => api().save_player(null)).catch(() => {}); // closed on purpose: nothing to come back to
  player.token += 1;
  player.audio.pause();
  player.audio.removeAttribute("src");
  player.audio.load();
  clearSpare();
  Object.assign(player, { queue: [], unshuffled: null, index: -1, info: null, lines: [], current: -1 });
  if (state.view === "now") closeNowPlaying();
  renderPlayer();
}

function renderPlayer() {
  const bar = $("#player");
  const info = player.info;
  bar.hidden = !info;
  markPlayingRows();
  if (!info) {
    syncTaskbar();
    renderQueue();
    return;
  }
  $(".player-title", bar).textContent = info.title;
  $(".player-title", bar).title = info.title;
  $(".player-artist", bar).textContent = info.artists || info.album_artist;
  setPlayerPicture($(".player-cover", bar), info.cover);
  const round = player.repeat === "all";
  $("#player-prev").disabled = !round && player.index <= 0 && player.audio.currentTime <= 3;
  $("#player-next").disabled = !round && player.index >= player.queue.length - 1;
  renderPlayerButton();
  renderPlayerTime();
  renderNowPlaying();
  renderQueue();
}

function setPlayerPicture(box, url) {
  const image = $("img", box);
  if (image.dataset.src === url) return;
  image.dataset.src = url || "";
  if (!url) {
    image.hidden = true;
    image.removeAttribute("src");
    return;
  }
  // The picture is fetched beside the one on screen and takes its place when
  // it is there; hiding it first showed the empty note for a moment
  const next = new Image();
  next.addEventListener("load", () => {
    if (image.dataset.src !== url) return; // another track came meanwhile
    image.src = url;
    image.hidden = false;
  }, { once: true });
  next.src = url;
}

function renderPlayerButton() {
  const playing = !player.audio.paused;
  const button = $("#player-play");
  swapIcon($("use", button), playing ? "#i-pause" : "#i-play");
  renderPagePlay();
  button.title = t(playing ? "Пауза" : "Слушать");
  button.setAttribute("aria-label", button.title);
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = playing ? "playing" : "paused";
  syncTaskbar();
  syncPresence();
}

function renderPlayerTime() {
  const { audio } = player;
  const length = audio.duration || player.info?.duration || 0;
  if (!player.seeking) {
    $("#player-time").textContent = formatDuration(audio.currentTime || 0);
    $("#player-position").value = length ? Math.round((audio.currentTime / length) * 1000) : 0;
    fillRange($("#player-position"));
  }
  $("#player-length").textContent = formatDuration(length);
  highlightLyric();
  if ("mediaSession" in navigator && navigator.mediaSession.setPositionState && length && Number.isFinite(audio.duration)) {
    try {
      navigator.mediaSession.setPositionState({ duration: audio.duration, position: Math.min(audio.currentTime, audio.duration),
                                                playbackRate: audio.playbackRate || 1 });
    } catch {
      // a position past the end while a new file loads
    }
  }
}

function renderPlayerVolume() {
  const silent = player.muted || player.volume === 0;
  swapIcon($("use", $("#player-mute")), silent ? "#i-mute" : player.volume < 0.5 ? "#i-volume-low" : "#i-volume");
  $("#player-mute").title = t(silent ? "Со звуком" : "Без звука");
  fillRange($("#player-volume"));
}

// The buttons under the window's picture on the taskbar follow the player
function syncTaskbar() {
  const { info } = player;
  const buttons = info
    ? { playing: !player.audio.paused, prev: !$("#player-prev").disabled, next: !$("#player-next").disabled,
        words: { prev: t("Предыдущий"), play: t("Слушать"), pause: t("Пауза"), next: t("Следующий") },
        // and for Windows' media controls, what plays
        title: info.title, artists: info.artists || "", album: info.album || "", album_artist: info.album_artist || "",
        cover: info.cover || "" }
    : null;
  const key = JSON.stringify(buttons);
  if (key === player.taskbar) return;
  player.taskbar = key;
  Promise.resolve().then(() => api().player_buttons(buttons)).catch(() => {});
}

// The Discord profile shows the track while it plays, with a bar Discord moves
// by itself: it is told again when the track, the pause or the place changes
function syncPresence(moved = false) {
  const { info, audio } = player;
  const length = audio.duration || info?.duration || 0;
  const track = info && !audio.paused
    ? { path: info.path, title: info.title, artists: info.artists || info.album_artist || "", album: info.album || "",
        album_artist: info.album_artist || "", duration: length, position: audio.currentTime || 0 }
    : null;
  const key = track ? `${info.path}|${Math.round(length)}` : "";
  if (key === player.presence && !(moved && track)) return;
  player.presence = key;
  Promise.resolve().then(() => api().now_playing(track)).catch(() => {});
}

// A button of Windows' media controls or a media key, passed on by the program
function mediaAction(name) {
  if (!player.info) return;
  if (name === "play" && player.audio.paused) togglePlay();
  else if (name === "pause" && !player.audio.paused) togglePlay();
  else if (name === "next") stepTrack(1);
  else if (name === "previous") stepTrack(-1);
  else if (name === "stop") stopPlayer();
}

// A click on one of those buttons, passed on by the program
function taskbarAction(name) {
  if (name === "prev") stepTrack(-1);
  else if (name === "next") stepTrack(1);
  else if (name === "play") togglePlay();
}

// A slider's line is filled up to its knob, as the share --p that .bar-fill uses too
function fillRange(input) {
  const min = Number(input.min) || 0;
  const max = Number(input.max) || 100;
  input.style.setProperty("--p", (Number(input.value) - min) / (max - min));
}

// The row of the track that plays, wherever it is on screen
function markPlayingRows() {
  renderPagePlay();
  const path = player.info?.path;
  for (const row of $$(".album-track.is-playing, .track-item.is-playing")) row.classList.remove("is-playing");
  if (!path) return;
  for (const row of $$(".album-list .album-track[data-path], #library-tracks .track-item")) {
    if (row.dataset.path === path) row.classList.add("is-playing");
  }
}

// Whether the queue is the album or the playlist whose page is open, in any order (a shuffle changes it)
function pagePlaying() {
  const page = $("#album-page");
  if (!player.info || $("#library-page").hidden) return false;
  const paths = new Set(page.dataset.kind === "playlist" // a playlist may hold a track twice
    ? (page.playlist?.tracks || []).filter((track) => !track.missing).map((track) => track.path)
    : [...$$(".album-list .album-track[data-path]", page)].map((row) => row.dataset.path));
  const queued = new Set(player.queue);
  return paths.size > 0 && paths.size === queued.size && [...paths].every((path) => queued.has(path));
}

// The page's own button pauses what it started, as the player's does, and plays it on from where it stopped
function renderPagePlay() {
  const button = $('#album-page [data-page-action="play"]');
  const playing = pagePlaying() && !player.audio.paused;
  swapIcon($("use", button), playing ? "#i-pause" : "#i-play");
  $("span", button).textContent = playing ? t("Пауза") : t("Слушать");
}

/* Sound: the equaliser and the crossfade */

// Both elements play into one Web Audio graph: each through a gain of its own
// (its ReplayGain, and its share of a crossfade), then the equaliser's ten
// bands, then the volume. The graph is made at the first play the person asks
// for, since a browser keeps sound shut until then; before it, and wherever Web
// Audio fails, the elements play straight out as they used to.
const EQ_FREQUENCIES = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];

const EQ_LIMIT = 12;

const EQ_PRESETS = {
  flat: { label: "Ровно", bands: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
  bass: { label: "Больше баса", bands: [6, 5, 4, 2, 0, 0, 0, 0, 0, 0] },
  treble: { label: "Больше высоких", bands: [0, 0, 0, 0, 0, 1, 2, 4, 5, 6] },
  vocal: { label: "Голос", bands: [-2, -2, -1, 1, 3, 4, 3, 1, 0, -1] },
  rock: { label: "Рок", bands: [4, 3, 2, 0, -1, -1, 1, 2, 3, 4] },
  electronic: { label: "Электроника", bands: [5, 4, 1, 0, -2, 1, 0, 1, 4, 5] },
  acoustic: { label: "Акустика", bands: [3, 3, 2, 1, 1, 1, 2, 2, 2, 1] },
  quiet: { label: "Тихо, но разборчиво", bands: [5, 4, 2, 0, -1, 0, 1, 2, 4, 4] },
};

const sound = { context: null, failed: false, gains: new Map(), filters: [], preamp: null, master: null };

function soundGraph() {
  if (sound.context || sound.failed) return sound.context;
  try {
    const context = new AudioContext({ latencyHint: "playback" });
    sound.preamp = context.createGain();
    sound.master = context.createGain();
    sound.filters = EQ_FREQUENCIES.map((frequency, index) => {
      const filter = context.createBiquadFilter();
      filter.type = index === 0 ? "lowshelf" : index === EQ_FREQUENCIES.length - 1 ? "highshelf" : "peaking";
      filter.frequency.value = frequency;
      filter.Q.value = 1.1; // an octave wide: neighbouring bands meet without a dip
      return filter;
    });
    let chain = sound.preamp;
    for (const filter of sound.filters) chain = chain.connect(filter);
    chain.connect(sound.master).connect(context.destination);
    for (const element of [player.audio, player.spare]) {
      const gain = context.createGain();
      context.createMediaElementSource(element).connect(gain).connect(sound.preamp);
      sound.gains.set(element, gain);
    }
    sound.context = context;
    applyEqualizer();
    applyVolume();
  } catch (error) {
    console.error(error);
    sound.failed = true;
  }
  return sound.context;
}

// A browser starts a graph suspended until the page has been used
function wakeSound() {
  if (!sound.context && navigator.userActivation?.hasBeenActive) soundGraph();
  if (sound.context?.state === "suspended") sound.context.resume().catch(() => {});
}

// One element's own gain, at once or along a curve of so many seconds. The
// curve is a quarter of a sine: two tracks crossing keep the loudness of one,
// where straight lines would dip in the middle.
function setElementGain(element, value, seconds = 0, from = null) {
  const gain = sound.gains.get(element)?.gain;
  if (!gain) return;
  const now = sound.context.currentTime;
  const start = from ?? gain.value;
  gain.cancelScheduledValues(now);
  if (seconds > 0) {
    const steps = 64;
    const curve = new Float32Array(steps);
    for (let i = 0; i < steps; i++) {
      const x = i / (steps - 1);
      curve[i] = value > start ? start + (value - start) * Math.sin(x * Math.PI / 2)
        : value + (start - value) * Math.cos(x * Math.PI / 2);
    }
    gain.setValueCurveAtTime(curve, now, seconds);
  } else {
    gain.setValueAtTime(value, now);
  }
}

function applyEqualizer() {
  if (!sound.context) return;
  const { on, bands } = equalizer();
  const now = sound.context.currentTime;
  sound.filters.forEach((filter, index) => filter.gain.setTargetAtTime(on ? bands[index] : 0, now, 0.03));
  // Raised bands would push a loud master over the top: the whole is lowered
  // by the highest of them, so the equaliser shapes the sound and never clips it
  const peak = on ? Math.max(0, ...bands) : 0;
  sound.preamp.gain.setTargetAtTime(10 ** (-peak / 20), now, 0.03);
}

function equalizer() {
  const eq = state.settings.eq || {};
  const bands = EQ_FREQUENCIES.map((_, index) => Number(eq.bands?.[index]) || 0);
  return { on: Boolean(eq.on), bands, preset: eq.preset || "flat" };
}

function setEqualizer(patch) {
  const eq = { ...equalizer(), ...patch };
  rememberPlayer({ eq });
  soundGraph();
  applyEqualizer();
  renderEqualizer();
}

function bindEqualizer() {
  const bands = $("#eq-bands");
  bands.replaceChildren(...EQ_FREQUENCIES.map((frequency, index) => {
    const band = document.createElement("div");
    band.className = "eq-band";
    const value = document.createElement("output");
    const slider = document.createElement("input");
    Object.assign(slider, { type: "range", min: -EQ_LIMIT, max: EQ_LIMIT, step: 0.5, value: 0 });
    slider.dataset.band = index;
    const label = document.createElement("span");
    label.textContent = frequency >= 1000 ? `${frequency / 1000}k` : String(frequency);
    slider.setAttribute("aria-label", t("{hertz} Гц", { hertz: frequency }));
    const slot = document.createElement("div");
    slot.className = "eq-slot";
    slot.append(slider);
    band.append(value, slot, label);
    return band;
  }));
  bands.addEventListener("input", (event) => {
    const index = Number(event.target.dataset.band);
    if (Number.isNaN(index)) return;
    const values = equalizer().bands;
    values[index] = Number(event.target.value);
    // Moving a band means wanting to hear it: the equaliser comes on
    setEqualizer({ bands: values, preset: "custom", on: true });
  });
  // A double click puts a band back to the middle
  bands.addEventListener("dblclick", (event) => {
    const index = Number(event.target.dataset?.band);
    if (Number.isNaN(index)) return;
    const values = equalizer().bands;
    values[index] = 0;
    setEqualizer({ bands: values, preset: "custom" });
  });
  $("#eq-on").addEventListener("click", () => setEqualizer({ on: !equalizer().on }));
  $("#eq-preset").addEventListener("change", (event) => {
    const preset = EQ_PRESETS[event.target.value];
    if (preset) setEqualizer({ bands: [...preset.bands], preset: event.target.value, on: true });
  });
  $("#eq-close").addEventListener("click", () => toggleEqualizer(false));
  $("#player-eq").addEventListener("click", () => toggleEqualizer());
  $("#eq-open").addEventListener("click", () => toggleEqualizer(true));
  // Anywhere else closes it, as a menu is closed
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#eq-panel, #player-eq, #eq-open")) toggleEqualizer(false);
  });
}

function toggleEqualizer(open = $("#eq-panel").hidden) {
  const panel = $("#eq-panel");
  if (open === !panel.hidden) return;
  panel.hidden = !open;
  $("#player-eq").setAttribute("aria-pressed", String(open));
  if (!open) return;
  toggleQueue(false);
  soundGraph();
  placeFloat(panel);
  renderEqualizer();
  $("#eq-preset").focus();
}

function renderEqualizer() {
  const { on, bands, preset } = equalizer();
  syncSwitch($("#eq-on"), on);
  $("#eq-panel").classList.toggle("off", !on);
  const pick = $("#eq-preset");
  const options = Object.entries(EQ_PRESETS).map(([key, entry]) => new Option(t(entry.label), key));
  if (preset === "custom") options.push(new Option(t("Своя"), "custom"));
  pick.replaceChildren(...options);
  pick.value = preset in EQ_PRESETS || preset === "custom" ? preset : "flat";
  for (const slider of $$("#eq-bands input")) {
    const value = bands[Number(slider.dataset.band)];
    if (document.activeElement !== slider) slider.value = value;
    slider.style.setProperty("--p", (value + EQ_LIMIT) / (EQ_LIMIT * 2));
    $("output", slider.closest(".eq-band")).textContent = value > 0 ? `+${value}` : String(value);
  }
  $("#player-eq").classList.toggle("on", on);
  const name = preset === "custom" ? t("своя") : t(EQ_PRESETS[preset]?.label || "Ровно").toLocaleLowerCase(LANGUAGE);
  $("#eq-state").textContent = on ? t("Включён: {preset}", { preset: name }) : t("Выключен");
}

/* Handing over from one track to the next */

// The next track of the same record starts the moment this one ends, and one
// from another record fades in over this one's end (if a crossfade is set).
// The spare element holds the next track already, so either is a matter of
// starting it at the right time rather than waiting for "ended".
function crossfadeFor(ahead) {
  const seconds = Number(state.settings.crossfade) || 0;
  if (!seconds || !sound.context || !player.info || !ahead) return 0;
  // Only an album heard in its own order goes on without one: shuffled, its
  // tracks are no longer each other's continuation
  const sameRecord = !player.shuffle && Boolean(ahead.info.album) && ahead.info.album === player.info.album
    && (ahead.info.album_artist || "") === (player.info.album_artist || "");
  return sameRecord ? 0 : seconds;
}

function watchHandover() {
  const { audio, ahead } = player;
  if (player.handover || player.seeking || audio.paused || !player.info || player.repeat === "one") return;
  const next = stepIndex(1);
  // Round a shuffled queue the order is dealt again, so the track held is not the next one
  if (next < 0 || (next === 0 && player.shuffle) || ahead?.path !== player.queue[next]) return;
  if (player.spare.readyState < HTMLMediaElement.HAVE_FUTURE_DATA) return;
  const length = audio.duration || 0;
  const left = length - audio.currentTime;
  if (!Number.isFinite(left) || left <= 0) return;
  const fade = Math.min(crossfadeFor(ahead), length / 3, (player.spare.duration || 0) / 3);
  if (fade >= 1 && left <= fade) {
    handOver(fade);
  } else if (fade < 1 && state.settings.gapless !== false && left <= 1) {
    // Started a hair before the end: the one that ends is cut, not waited for
    player.handover = atSoundTime(Math.max(0, left / (audio.playbackRate || 1) - 0.025), () => handOver(0));
  }
}

// Something done so many seconds from now on the sound's own clock. A timer
// will not do: in a window out of sight the browser lets timers run a second
// late, and the track would end before the next one started. A silent source
// stopped at that moment says so on time, since the sound is never held back.
function atSoundTime(seconds, callback) {
  const context = sound.context;
  if (!context || context.state !== "running") {
    const timer = setTimeout(callback, seconds * 1000);
    return { cancel: () => clearTimeout(timer) };
  }
  const tick = context.createConstantSource();
  const silence = context.createGain();
  silence.gain.value = 0;
  tick.connect(silence).connect(context.destination);
  let live = true;
  tick.onended = () => {
    tick.disconnect();
    silence.disconnect();
    if (live) callback();
  };
  tick.start();
  tick.stop(context.currentTime + seconds);
  return { cancel: () => { live = false; } };
}

function cancelHandover() {
  player.handover?.cancel?.();
  player.handover = null;
}

function handOver(fade) {
  cancelHandover();
  const next = stepIndex(1);
  if (next < 0 || player.ahead?.path !== player.queue[next]) return;
  const outgoing = player.audio;
  player.index = next;
  loadTrack({ fade, outgoing });
}

// The track that has handed over fades out (or stops at once), and only then
// is the spare element free to load the track after the new one
function releaseOutgoing(element, fade) {
  player.ahead = null;
  player.aheadToken += 1;
  clearTimeout(player.fading);
  const done = () => {
    player.fading = 0;
    if (element !== player.spare) return; // taken up again meanwhile
    element.pause();
    element.removeAttribute("src");
    element.load();
    setElementGain(element, 1);
    loadAhead();
  };
  if (fade > 0 && sound.context) {
    setElementGain(element, 0, fade);
    player.fading = setTimeout(done, fade * 1000 + 60);
  } else {
    done();
  }
}

/* Listening: what counts, for the play counts, Last.fm and ListenBrainz */

// A track counts once half of it, or four minutes, has played (the rule
// Last.fm and ListenBrainz share), and only what really played: a jump
// forward adds nothing
function resetListen() {
  player.listen = { at: player.audio.currentTime || 0, played: 0, counted: false };
}

function countListen() {
  const { audio, info, listen } = player;
  if (!info || !listen || listen.counted) return;
  const step = audio.currentTime - listen.at;
  listen.at = audio.currentTime;
  if (step > 0 && step < 2) listen.played += step;
  const length = audio.duration || info.duration || 0;
  if (length < 30 || listen.played < Math.min(length / 2, 240)) return;
  listen.counted = true;
  const track = { path: info.path, title: info.title, artists: info.artists || "", album: info.album || "",
                  album_artist: info.album_artist || "", duration: Math.round(length) };
  Promise.resolve().then(() => api().listened(track)).catch(() => {});
  state.plays.set(info.path, (state.plays.get(info.path) || 0) + 1);
  state.playlists.stale = true; // "Recently played" and "Most played" have changed
}

/* The queue */

// What a queued path shows: remembered from wherever it was queued from, else
// from the Tracks tab, else its file name
function rememberTracks(tracks) {
  for (const track of tracks) {
    if (!track?.path) continue;
    player.meta.set(track.path, { path: track.path, title: track.title || "", artists: track.artists || track.album_artist || "",
                                  album: track.album || "", album_artist: track.album_artist || "", duration: track.duration || 0 });
  }
}

let tracksIndex = { items: null, byPath: new Map() };

function libraryTrack(path) {
  const items = state.library.trackList.items;
  if (tracksIndex.items !== items) tracksIndex = { items, byPath: new Map(items.map((track) => [track.path, track])) };
  return tracksIndex.byPath.get(path);
}

function trackMeta(path) {
  const known = player.meta.get(path) || libraryTrack(path);
  if (known) return known;
  const name = path.split(/[\\/]/).pop() || path;
  return { path, title: name.replace(/\.[^.]+$/, ""), artists: "", album: "", duration: 0 };
}

// Tracks put in the queue: after the one that plays, or at the end. With
// nothing playing they simply start.
function enqueue(tracks, next = false) {
  const playable = tracks.filter((track) => track?.path && !track.missing);
  if (!playable.length) return;
  rememberTracks(playable);
  const paths = playable.map((track) => track.path);
  for (const track of playable) player.artists.set(track.path, track.album_artist || track.artists || "");
  if (!player.info) {
    playQueue(paths, 0, "queue", player.artists);
  } else {
    const at = next ? player.index + 1 : player.queue.length;
    player.queue.splice(at, 0, ...paths);
    if (player.unshuffled) {
      // Back in order, they stay where they were put: after this track, or at the end
      const place = next ? player.unshuffled.indexOf(player.queue[player.index]) + 1 : player.unshuffled.length;
      player.unshuffled.splice(place || player.unshuffled.length, 0, ...paths);
    }
    queueChanged();
  }
  const title = playable[0].title || trackMeta(paths[0]).title;
  announce(playable.length === 1
    ? t(next ? "«{title}» заиграет следующим" : "«{title}» в очереди", { title })
    : t(next ? "Заиграют следующими: {count}" : "Добавлено в очередь: {count}", { count: playable.length }));
}

// After any change to the queue the next track is loaded again, unless the
// last one is still fading out of the spare element (it loads when it is done)
function queueChanged() {
  session.queueChanged = true;
  cancelHandover();
  if (!player.fading) {
    clearSpare();
    loadAhead();
  }
  renderPlayer();
  renderQueue();
}

function moveInQueue(from, to) {
  const [path] = player.queue.splice(from, 1);
  player.queue.splice(to, 0, path);
  if (from === player.index) player.index = to;
  else if (from < player.index && to >= player.index) player.index -= 1;
  else if (from > player.index && to <= player.index) player.index += 1;
  queueChanged();
}

function removeFromQueue(index) {
  if (index === player.index) return;
  const [path] = player.queue.splice(index, 1);
  if (index < player.index) player.index -= 1;
  const kept = player.unshuffled?.indexOf(path) ?? -1;
  if (kept >= 0) player.unshuffled.splice(kept, 1);
  queueChanged();
}

function clearUpcoming() {
  const later = new Set(player.queue.splice(player.index + 1));
  if (player.unshuffled) player.unshuffled = player.unshuffled.filter((path) => !later.has(path) || player.queue.includes(path));
  queueChanged();
}

function bindQueue() {
  const list = $("#queue-list");
  $("#player-queue").addEventListener("click", () => toggleQueue());
  $("#queue-close").addEventListener("click", () => toggleQueue(false));
  $("#queue-clear").addEventListener("click", clearUpcoming);
  $("#queue-keep").addEventListener("click", () => {
    keepAsPlaylist(player.queue.map(trackMeta), t("Очередь от {date}", { date: new Date().toLocaleDateString(LANGUAGE) }));
  });
  list.addEventListener("click", (event) => {
    const row = event.target.closest(".queue-row");
    if (!row) return;
    const index = [...list.children].indexOf(row);
    if (event.target.closest(".queue-remove")) {
      removeFromQueue(index);
    } else if (index !== player.index) {
      player.index = index;
      loadTrack();
    }
  });
  sortable(list, ".queue-row", moveInQueue);
}

function toggleQueue(open = $("#queue-panel").hidden) {
  const panel = $("#queue-panel");
  if (open === !panel.hidden) return;
  panel.hidden = !open;
  $("#player-queue").setAttribute("aria-pressed", String(open));
  if (!open) return;
  toggleEqualizer(false);
  placeFloat(panel);
  renderQueue();
  $(".queue-row.current", panel)?.scrollIntoView({ block: "center" });
}

function renderQueue() {
  const panel = $("#queue-panel");
  if (panel.hidden) return;
  if (!player.info) {
    toggleQueue(false);
    return;
  }
  const rows = player.queue.map((path, index) => {
    const track = trackMeta(path);
    const row = document.createElement("li");
    row.className = `queue-row${index < player.index ? " played" : index === player.index ? " current" : ""}`;
    row.draggable = true;
    row.innerHTML = `<svg class="icon sm grip" aria-hidden="true"><use href="#i-grip"/></svg>
      <span class="queue-names"><span class="queue-title"></span><span class="queue-artist"></span></span>
      <span class="queue-time"></span>`;
    $(".queue-title", row).textContent = track.title;
    $(".queue-artist", row).textContent = track.artists || track.album_artist || "";
    $(".queue-time", row).textContent = track.duration ? formatDuration(track.duration) : "";
    if (index !== player.index) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-btn queue-remove";
      remove.title = t("Убрать из очереди");
      remove.setAttribute("aria-label", remove.title);
      remove.innerHTML = `<svg class="icon sm" aria-hidden="true"><use href="#i-x"/></svg>`;
      row.append(remove);
    }
    return row;
  });
  $("#queue-list").replaceChildren(...rows);
  const upcoming = player.queue.slice(player.index + 1);
  const seconds = upcoming.reduce((total, path) => total + (trackMeta(path).duration || 0), 0);
  $("#queue-count").textContent = upcoming.length
    ? [t("дальше {count} {trackWord}", { count: upcoming.length, trackWord: plural(upcoming.length, "трек", "трека", "треков") }),
      formatLength(seconds)].join(" · ")
    : t("дальше ничего");
  $("#queue-clear").disabled = !upcoming.length;
}

// Rows put in another order by dragging them: the row goes where the line shows
function sortable(list, selector, move) {
  let dragged = null;
  const unmark = () => { for (const row of $$(".drop-before, .drop-after", list)) row.classList.remove("drop-before", "drop-after"); };
  list.addEventListener("dragstart", (event) => {
    const row = event.target.closest?.(selector);
    if (!row?.draggable) return;
    dragged = row;
    row.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", "");
  });
  list.addEventListener("dragend", () => {
    dragged?.classList.remove("dragging");
    dragged = null;
    unmark();
  });
  list.addEventListener("dragover", (event) => {
    if (!dragged) return;
    event.preventDefault();
    const row = event.target.closest?.(selector);
    if (!row || row === dragged) return;
    const box = row.getBoundingClientRect();
    unmark();
    row.classList.add(event.clientY > box.top + box.height / 2 ? "drop-after" : "drop-before");
  });
  list.addEventListener("drop", (event) => {
    if (!dragged) return;
    event.preventDefault();
    event.stopPropagation(); // the window's own drop takes links and lists of links
    const target = $(".drop-before, .drop-after", list);
    const after = Boolean(target?.classList.contains("drop-after"));
    unmark();
    if (!target) return;
    const rows = [...$$(selector, list)];
    const from = rows.indexOf(dragged);
    let to = rows.indexOf(target) + (after ? 1 : 0);
    if (from < to) to -= 1;
    if (from >= 0 && from !== to) move(from, to);
  });
}

/* A track's own menu: the queue, the playlists, its album */

function bindTrackMenu() {
  for (const list of [$(".album-list", $("#album-page")), $("#library-tracks")]) {
    list.addEventListener("contextmenu", onTrackContextMenu);
    list.addEventListener("click", (event) => {
      const more = event.target.closest(".row-more");
      if (!more) return;
      event.stopPropagation();
      const box = more.getBoundingClientRect();
      more.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: box.right, clientY: box.bottom }));
    });
  }
  $("#track-menu").addEventListener("click", onTrackMenuClick);
  $("#playlist-menu").addEventListener("click", onPlaylistMenuClick);
  for (const menu of [$("#track-menu"), $("#playlist-menu"), $("#library-menu")]) {
    menu.addEventListener("keydown", onMenuKey);
  }
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#track-menu")) closeMenu($("#track-menu"));
    if (!event.target.closest("#playlist-menu")) closeMenu($("#playlist-menu"));
  });
  window.addEventListener("blur", () => {
    closeMenu($("#track-menu"));
    closeMenu($("#playlist-menu"));
  });
}

// Up and down through a menu's buttons, Escape out of it
function onMenuKey(event) {
  const menu = event.currentTarget;
  if (event.key === "Escape") {
    event.preventDefault();
    if (menu.id === "library-menu") closeLibraryMenu();
    else if (menu.id === "library-sort-menu") setSortMenuOpen(false);
    else if (menu.id === "library-more-menu") setMoreMenuOpen(false);
    else closeMenu(menu);
    return;
  }
  const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
  if (!step) return;
  event.preventDefault();
  const buttons = [...$$("button:not([hidden])", menu)];
  buttons[(buttons.indexOf(document.activeElement) + step + buttons.length) % buttons.length]?.focus();
}

// Placed after it is measurable, so a menu near the edge turns back inwards. It is measured by its own
// size, not by its box on the screen, which is still growing; and it grows out of where the pointer is.
function placeMenu(menu, x, y) {
  const left = Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8));
  const top = Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8));
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
  menu.style.transformOrigin = `${Math.max(0, x - left)}px ${Math.max(0, y - top)}px`;
}

function openMenuAt(menu, x, y) {
  menu.hidden = false;
  placeMenu(menu, x, y);
  $("button:not([hidden])", menu)?.focus();
}

function closeMenu(menu) {
  menu.hidden = true;
}

// The track a row stands for, with what its page knows of it
function rowTrack(row, playlist, index) {
  const path = row.dataset.path;
  if (playlist) return playlist.tracks[index];
  if (row.closest("#album-page")) return ($("#album-page").tracks || []).find((track) => track.path === path) || trackMeta(path);
  return libraryTrack(path) || trackMeta(path);
}

function onTrackContextMenu(event) {
  const row = event.target.closest(".album-track, .track-item");
  if (!row) return;
  const page = $("#album-page");
  const onPage = Boolean(row.closest("#album-page"));
  const playlist = onPage && page.dataset.kind === "playlist" ? page.playlist : null;
  if (!row.dataset.path && !playlist) return; // an album's missing track has no file to act on
  event.preventDefault();
  const index = [...$$(".album-list .album-track", page)].indexOf(row);
  const track = rowTrack(row, playlist, index);
  if (!track) return;
  state.trackMenu = { tracks: [track], row, playlist, index };
  const menu = $("#track-menu");
  const gone = Boolean(track.missing);
  for (const action of ["next", "queue", "playlist", "open"]) $(`[data-action=${action}]`, menu).hidden = gone;
  $("[data-action=album]", menu).hidden = gone || (onPage && !playlist);
  $("[data-action=remove]", menu).hidden = !playlist || playlist.smart;
  $(".menu-divider", menu).hidden = gone;
  openMenuAt(menu, event.clientX, event.clientY);
}

function onTrackMenuClick(event) {
  const action = event.target.closest("[data-action]")?.dataset.action;
  const menu = state.trackMenu;
  if (!action || !menu) return;
  closeMenu($("#track-menu"));
  const [track] = menu.tracks;
  if (action === "next" || action === "queue") enqueue(menu.tracks, action === "next");
  else if (action === "playlist") openPlaylistMenu(event.clientX, event.clientY, menu.tracks);
  else if (action === "open") api().open_folder(track.path);
  else if (action === "remove") removeFromPlaylist(menu.index);
  else if (action === "album") openTrackAlbum(track);
}

// The album a track belongs to: the library's entry for its folder, or for the file itself
async function openTrackAlbum(track) {
  if (state.library.stale && !state.library.items.length) await loadLibrary();
  const folder = track.path.replace(/[\\/][^\\/]*$/, "");
  const item = itemByPath(track.entry || "") || itemByPath(folder) || itemByPath(track.path)
    || state.library.items.find((entry) => track.path.startsWith(`${entry.path}\\`) || track.path.startsWith(`${entry.path}/`));
  if (!item) {
    announce(t("Альбома этого трека нет в библиотеке"));
    return;
  }
  if (state.view !== "library") showView("library");
  if (state.library.pages.length) await closePage({ instant: true });
  openAlbum(item, null, track.path);
}

// Several albums picked in the library: their tracks, album by album, in order
async function itemsTracks(items) {
  const tracks = [];
  for (const item of items) {
    let data;
    try {
      data = await api().album(item.path);
    } catch {
      continue;
    }
    const sorted = [...data.tracks].sort((a, b) => (a.disc || 1) - (b.disc || 1) || (a.number || 999) - (b.number || 999));
    tracks.push(...sorted.map((track) => ({ ...track, album: track.album || item.title,
                                            album_artist: track.album_artist || item.artist })));
  }
  return tracks;
}
