"use strict";

// Now playing: the whole window given to the track, and its lyrics

/* Now playing */

function openNowPlaying() {
  if (!player.info) return;
  if (state.view !== "now") player.previousView = state.view;
  // The back button is named after the section it goes back to, as the column at the side names it
  const back = $(`.side-view[data-view="${player.previousView}"]`);
  $("#now-close-label").textContent = back ? ($(".side-name", back)?.textContent || back.getAttribute("aria-label")) : t("Назад");
  showView("now");
  renderNowPlaying();
  scrollToLyric(true);
}

function closeNowPlaying() {
  showView(player.previousView || "library");
}

function toggleNowPlaying() {
  if (state.view === "now") closeNowPlaying();
  else openNowPlaying();
}

function toggleFullscreen() {
  player.fullscreen = !player.fullscreen;
  document.documentElement.classList.toggle("window-full", player.fullscreen);
  api().fullscreen();
  const button = $("#player-full");
  swapIcon($("use", button), player.fullscreen ? "#i-full-exit" : "#i-full");
  button.title = t(player.fullscreen ? "Выйти из полноэкранного режима" : "Во весь экран");
  button.setAttribute("aria-label", button.title);
}

function renderNowPlaying() {
  const view = $("#view-now");
  const info = player.info;
  $("#player-lyrics").setAttribute("aria-pressed", String(state.view === "now"));
  if (!info) return;
  $(".now-title", view).textContent = info.title;
  $(".now-artist", view).textContent = info.artists || info.album_artist || "";
  $(".now-sub", view).textContent = [info.album, info.year].filter(Boolean).join(" · ");
  // The file's make on one line: the format first, then how it sounds; stereo goes without saying, mono does not
  $(".now-format", view).textContent = [
    (info.format || "").toUpperCase(),
    info.bitrate ? t("{kbps} кбит/с", { kbps: info.bitrate }) : "",
    info.sample_rate ? t("{khz} кГц", { khz: (info.sample_rate / 1000).toLocaleString(LANGUAGE, { maximumFractionDigits: 1 }) }) : "",
    info.channels === 1 ? t("Моно") : "",
  ].filter(Boolean).join(" · ");
  setPlayerPicture($(".now-cover", view), info.cover);
  const backdrop = $(".now-backdrop", view);
  if (backdrop.dataset.src !== info.cover) {
    const cover = info.cover || "";
    backdrop.dataset.src = cover;
    backdrop.hidden = true;
    if (cover) {
      backdrop.addEventListener("load", () => { backdrop.hidden = false; }, { once: true });
      backdrop.src = cover;
    }
    // The wash in the cover's own colour, worked out as the album page's band is
    (cover ? pictureTint(cover).catch(() => null) : Promise.resolve(null)).then((tint) => {
      if (backdrop.dataset.src !== cover) return; // the next track came meanwhile
      // A grey or white cover has no colour to give: the wash is a neutral one then
      view.style.setProperty("--tint-a", "1");
      view.style.setProperty("--tint-h", tint ? tint.hue : 0);
      view.style.setProperty("--tint-s", tint ? `${tint.saturation}%` : "0%");
      view.style.setProperty("--backdrop-a", backdropAlpha(tint));
    });
  }
}

async function loadLyrics(path, token) {
  const box = $("#now-lyrics");
  player.lines = [];
  player.current = -1;
  box.replaceChildren(lyricsNote(t("Ищем текст…")));
  let words;
  try {
    words = await api().lyrics(path);
  } catch (error) {
    words = { synced: "", plain: "" };
  }
  if (token !== player.token) return;
  const lines = parseLrc(words.synced || "");
  if (lines.length) {
    player.lines = lines.map((line) => {
      const node = document.createElement("button");
      node.type = "button";
      node.className = "lyric-line";
      node.dataset.time = line.time;
      node.textContent = line.text || "♪";
      return { ...line, node };
    });
    box.replaceChildren(...player.lines.map((line) => line.node));
    highlightLyric();
    scrollToLyric(true);
  } else if (words.plain) {
    const text = document.createElement("p");
    text.className = "lyrics-plain";
    text.textContent = words.plain;
    box.replaceChildren(text);
    box.scrollTop = 0;
  } else {
    box.replaceChildren(lyricsNote(t("Текста для этой песни не нашлось")));
  }
}

function lyricsNote(text) {
  const note = document.createElement("p");
  note.className = "lyrics-none";
  note.textContent = text;
  return note;
}

// "[01:23.45] words", a line may carry several times; "[ar: …]" and the like are skipped
function parseLrc(text) {
  const lines = [];
  for (const raw of text.split(/\r?\n/)) {
    const times = [...raw.matchAll(/\[(\d+):(\d+(?:[.:]\d+)?)\]/g)];
    if (!times.length) continue;
    const words = raw.replace(/\[[^\]]*\]/g, "").trim();
    for (const [, minutes, seconds] of times) {
      lines.push({ time: Number(minutes) * 60 + Number(seconds.replace(":", ".")), text: words });
    }
  }
  return lines.sort((a, b) => a.time - b.time);
}

function highlightLyric() {
  const { lines, audio } = player;
  if (!lines.length) return;
  const now = audio.currentTime + 0.25; // a line lights up as it is sung, not after
  let index = -1;
  for (let i = 0; i < lines.length && lines[i].time <= now; i++) index = i;
  if (index === player.current) return;
  player.current = index;
  lines.forEach((line, i) => {
    line.node.classList.toggle("is-current", i === index);
    line.node.classList.toggle("is-past", i < index);
  });
  scrollToLyric(false);
}

// The line being sung sits a third of the way down, and only the words move:
// scrolling the line into view would drag the whole screen, cover and all, with it
function scrollToLyric(instant) {
  if (state.view !== "now") return;
  const box = $("#now-lyrics");
  const line = player.lines[player.current];
  const top = line ? line.node.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop : 0;
  box.scrollTo({ top: Math.max(0, top - box.clientHeight / 3),
                 behavior: instant || reduceMotion() ? "auto" : "smooth" });
}
