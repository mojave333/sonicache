// Scenario helpers for checking the window in the preview stand (not shipped)
window.__wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
window.__job = (id, title, total, { done = 0, folder = `C:/Music/Radiohead - ${title} (1997)`, finish = null, single = false } = {}) => {
  addJob(id, `https://open.spotify.com/album/${id}`, false, "mp3");
  handleEvent({ job: id, type: "job", state: "running" });
  handleEvent({ job: id, type: "release", kind: single ? "трек" : "альбом", single, note: "", title, artist: "Radiohead",
    album: title, year: "1997", service: "Spotify", cover: "cover.svg", folder,
    tracks: Array.from({ length: total }, (_, i) => ({ id: `${id}-${i}`, number: i + 1, disc: 1,
      title: ["Airbag", "Paranoid Android", "Subterranean Homesick Alien", "Exit Music", "Let Down", "Karma Police",
              "Fitter Happier", "Electioneering", "Climbing Up the Walls", "No Surprises", "Lucky", "The Tourist"][i % 12],
      artists: "Radiohead", duration: "4:00" })) });
  for (let i = 0; i < done; i++) handleEvent({ job: id, type: "track", id: `${id}-${i}`, state: "done", source: "YouTube Music" });
  if (done < total && !finish) {
    handleEvent({ job: id, type: "track", id: `${id}-${done}`, state: "download", percent: 46, source: "YouTube Music", eta: 12 });
    if (done + 1 < total) handleEvent({ job: id, type: "track", id: `${id}-${done + 1}`, state: "search" });
  }
  handleEvent({ job: id, type: "progress", done, total });
  if (finish) {
    const failed = finish === "partial" ? 2 : 0;
    handleEvent({ job: id, type: "job", state: finish === "error" ? "error" : "done", message: "нет сети",
      ok: total - failed, skipped: 0, failed, dry_run: false });
  }
  flushRender();
  return state.jobs.get(id);
};
window.__finish = (id, how = "done") => {
  const job = state.jobs.get(id);
  const failed = how === "partial" ? 2 : 0;
  handleEvent({ job: id, type: "progress", done: job.total, total: job.total });
  handleEvent({ job: id, type: "job", state: how === "error" ? "error" : "done", message: "нет сети",
    ok: job.total - failed, skipped: 0, failed, dry_run: false });
  flushRender();
};
window.__toast = () => {
  const box = document.querySelector("#toast");
  return box ? { shown: !box.hidden, text: box.querySelector(".toast-text")?.textContent,
    action: box.querySelector(".toast-action")?.hidden ? "" : box.querySelector(".toast-action")?.textContent } : "no toast element";
};

// Scenes for headless screenshots: ?scene=library|player|downloads|catalogue|menu (&weight=4)
(() => {
  const query = new URLSearchParams(location.search);
  const scene = query.get("scene");
  if (query.get("weight")) document.documentElement.style.setProperty("--icon-weight", `${query.get("weight")}px`);
  if (!scene) return;
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  setTimeout(async () => {
    if (scene === "downloads") {
      showView("download");
      window.__job(1, "OK Computer", 12, { done: 4 });
      window.__job(2, "Kid A", 10, { done: 10, finish: "done" });
      window.__job(3, "Amnesiac", 11, { done: 9, finish: "partial" });
      return;
    }
    if (scene === "catalogue") { showView("search"); return; }
    showView("library");
    await wait(900);
    if (scene === "menu") document.querySelector("#library-title").click();
    if (scene === "player" || scene === "album") {
      document.querySelector("#view-library .card").click();
      await wait(900);
      if (scene === "player") document.querySelector("#view-library .card .play-btn, [data-page-action=play]")?.click();
    }
  }, 700);
})();

// ?run=<script> for a one-off scene (the preview stand only)
(() => {
  const run = new URLSearchParams(location.search).get("run");
  if (run) setTimeout(() => (0, eval)(`(async () => { const wait = (ms) => new Promise((r) => setTimeout(r, ms)); ${run} })()`), 700);
})();
