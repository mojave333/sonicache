"""The Download page: links and lists in, the queue of jobs and the thread that
runs them, the events the page polls for, and the watched releases.

One part of gui.Api: the methods the page calls on window.pywebview.api.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import webview

from .. import logs, prefs, thumbbar, tray, watch
from ..engine import batch, catalog, folders
from ..engine.downloader import Downloader, Options, read_marker
from ..engine.models import SourceError
from ..history import HISTORY_LIMIT, _save_history
from ..i18n import t
from ..libraryscan import _remember_downloaded
from ..prefs import _normalize
from ..system import _clipboard_text, _reveal
from .window import TITLE

# Track states that mean "not finished": a job that ends while a track is in
# one of them was interrupted, and the saved card should say so.
UNFINISHED = {"waiting", "search", "download"}

# How the taskbar button shows the downloads, as ITaskbarList3 numbers them:
# nothing, a sweep while no count is known yet, green, red, yellow
TASKBAR_STATES = {"none": 0, "indeterminate": 1, "normal": 2, "error": 4, "paused": 8}

LIST_BYTES = 2_000_000  # a list of links this long is already hundreds of thousands of lines

def _expand_artists(links: list[str]) -> list[str]:
    """Each artist link stands for their albums and EPs, oldest first. One
    that cannot be read stays as it is, and its card says why."""
    expanded = []
    for link in links:
        if not catalog.is_artist_link(link):
            expanded.append(link)
            continue
        try:
            releases = catalog.discography(link)
        except SourceError as e:
            logs.log.warning("дискография %s не прочиталась: %s", link, e)
            expanded.append(link)
            continue
        logs.log.info("дискография %s: альбомов и EP: %d", link, len(releases))
        expanded += [release["link"] for release in releases] or [link]
    return expanded

def _notice(finished: list[dict]) -> tuple[str, str]:
    """The title and text of the notice about these finished releases."""
    def counts(items: list[dict]) -> str:
        total = {key: sum(item.get(key) or 0 for item in items) for key in ("ok", "skipped", "failed")}
        parts = [t("скачано: {count}", count=total["ok"]) if total["ok"] else "",
                 t("уже были: {count}", count=total["skipped"]) if total["skipped"] else "",
                 t("не скачалось: {count}", count=total["failed"]) if total["failed"] else ""]
        return ", ".join(filter(None, parts))

    if len(finished) == 1:
        item = finished[0]
        if item["state"] == "error":
            return t("Не скачалось: {title}", title=item["title"]), item.get("message", "")
        whole = not item.get("failed")
        title = t("Скачано: {title}", title=item["title"]) if whole else t("Скачано не всё: {title}",
                                                                          title=item["title"])
        return title, counts([item])
    errors = sum(item["state"] == "error" for item in finished)
    text = counts(finished)
    if errors:
        text = ", ".join(filter(None, [text, t("ссылок с ошибкой: {count}", count=errors)]))
    return t("Загрузки завершены: {count}", count=len(finished)), text


class Downloads:
    def forget_history(self) -> None:
        """Clearing the finished cards clears what is remembered about them."""
        with self._lock:
            self._history = [entry for entry in self._history
                             if entry.get("state") in ("queued", "running")]
            _save_history(self._history)

    def _announce(self) -> None:
        """Tells how the downloads went, once the queue has run empty, when
        nobody is looking at the window: it is in the tray, minimized or behind others."""
        with self._lock:
            finished, self._finished = self._finished, []
        if not finished or (self._focused and not self._hidden) or not prefs._load_settings()["notify"]:
            return
        title, text = _notice(finished)
        if self._tray is not None and self._tray.shown:
            self._tray.notify(title, text)
        elif not tray.SUPPORTED:
            tray.notify_elsewhere(title, text)

    def choose_folder(self, current: str) -> str | None:
        start = current if current and Path(current).is_dir() else str(Path.home())
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        return os.path.normpath(result[0]) if result else None

    def paste(self) -> str:
        return _clipboard_text()

    def pick_list(self) -> dict | None:
        """Asks for a text or CSV file of links and says what is in it."""
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=str(Path.home()),
            file_types=(t("Списки ссылок (*.txt;*.csv)"), t("Все файлы (*.*)")))
        if not result:
            return None
        path = Path(result[0])
        try:
            if path.stat().st_size > LIST_BYTES:
                return {"name": path.name, "entries": [], "skipped": 0,
                        "error": t("Файл слишком большой для списка ссылок")}
            entries, skipped = batch.read(path.read_bytes(), path.name)
        except OSError as e:
            return {"name": path.name, "entries": [], "skipped": 0, "error": str(e)}
        return {"name": path.name, "entries": entries, "skipped": skipped}

    def parse_list(self, text: str, name: str = "") -> dict:
        """The same, for a file dropped on the window, which arrives as text."""
        entries, skipped = batch.parse(str(text or "")[:LIST_BYTES], str(name or ""))
        return {"name": name, "entries": entries, "skipped": skipped}

    def download(self, links: list[str], settings: dict) -> list[dict]:
        settings = _normalize(settings)
        prefs._save_settings(settings)
        return [self._enqueue(link, settings) for link in _expand_artists(links)]

    def artist(self, link: str) -> dict:
        """An artist's page for Search: picture, name and every release, newest first."""
        try:
            page = catalog.artist(link)
        except SourceError as e:
            return {"error": str(e)}
        return {**page, "watched": any(entry["link"] == page["link"] for entry in self._watched)}

    def watch_artist(self, link: str) -> list[dict]:
        """Starts watching an artist for new releases, into the music folder.
        What the artist has today is remembered, so none of it downloads."""
        try:
            page = catalog.artist(link)
        except SourceError as e:
            logs.log.warning("слежение: исполнитель %s не открылся: %s", link, e)
            return self.watched()
        with self._lock:
            self._watched = watch.add(self._watched, page["link"], page["name"], page["service"],
                                      prefs._load_settings(), known=[release["link"] for release in page["releases"]])
            watch.save(self._watched)
        return self.watched()

    def _enqueue(self, link: str, settings: dict) -> dict:
        options = Options(Path(settings["folder"]).expanduser(), settings["format"],
                          settings["threads"], settings["dry_run"], settings["cookies_browser"],
                          settings["track_name"], settings["folder_name"],
                          settings["rate_limit"], settings["proxy"], settings["replaygain"],
                          lyrics=settings.get("lyrics", True))
        with self._lock:
            self._job_counter += 1
            job = self._job_counter
            self._jobs.put((job, link, options))
            # The music folder and the naming rules are kept so the release can be
            # watched later and its new tracks sent to the same place. Not under
            # "folder": the release event fills that with the album's own folder,
            # and a check started from there nests the album inside itself.
            self._remember({"job": job, "link": link, "state": "queued",
                            "format": settings["format"], "dry_run": settings["dry_run"],
                            "music_folder": settings["folder"],
                            "track_name": settings["track_name"],
                            "folder_name": settings["folder_name"], "time": time.time()})
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, daemon=True)
                self._worker.start()
        return {"job": job, "link": link}

    def pause(self, paused: bool) -> bool:
        """Pauses between tracks: what is downloading finishes, the rest waits."""
        if paused:
            self._resume.clear()
        else:
            self._resume.set()
        logs.log.info("пауза" if paused else "продолжаем")
        return paused

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            self._resume.set()  # a paused queue still has to be able to stop
            while not self._jobs.empty():
                job, _, _ = self._jobs.get_nowait()
                self._events.put({"type": "job", "job": job, "state": "cancelled"})

    def progress(self, state: str, ratio: float = 0.0) -> None:
        """How far the downloads have come, where it is seen with the window
        minimised: in the title, and on Windows on the taskbar button too."""
        window = self._window
        if window is None or state not in TASKBAR_STATES:
            return
        percent = min(100, max(0, int(ratio * 100)))
        counted = state in ("normal", "error", "paused")
        window.set_title(f"{percent}% · {TITLE}" if counted else TITLE)
        if sys.platform != "win32":
            return
        try:
            thumbbar.progress(window.native.Handle.ToInt64(), TASKBAR_STATES[state], percent)
        except Exception as e:  # only a picture of the progress: never worth a failed call
            logs.log.debug("панель задач: %s", e)

    def open_folder(self, path: str) -> bool:
        folder = Path(path).expanduser()
        if folder.is_file() and _reveal(folder):
            return True
        while not folder.is_dir() and folder.parent != folder:
            folder = folder.parent  # a dry run never creates the album folder
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
        except OSError:
            return False
        return True

    # Watching: a release downloaded again now and then for what was added since

    def watched(self) -> list[dict]:
        """The watched links, each with how its last check went."""
        with self._lock:
            running = {entry.get("job") for entry in self._history
                       if entry.get("state") in ("queued", "running")}
            result = []
            for entry in self._watched:
                last = entry.get("last") or {}
                result.append({
                    "kind": entry.get("kind") or "release",
                    "link": entry["link"], "title": entry.get("title") or entry["link"],
                    "service": entry.get("service") or "", "checked": entry.get("checked") or 0,
                    "state": "running" if entry.get("job") in running else last.get("state", ""),
                    "added_tracks": last.get("ok"),
                })
            return result

    def _finish_watch_check(self, job: int, state: str, counts: dict) -> bool:
        """Records how a check went; answers whether its card should go quietly.

        A check that found nothing is the usual case — twice a day for every
        watched playlist — and a card saying "already downloaded" each time
        would bury the downloads that matter. So an empty check leaves no card
        and no history, only the line in the watch list.
        """
        with self._lock:
            entry = next((item for item in self._watched if item.get("job") == job), None)
            if entry is None:
                return False
            entry["last"] = {"state": state, "ok": counts["ok"], "failed": counts["failed"]}
            watch.save(self._watched)
            quiet = state == "done" and not counts["ok"] and not counts["failed"]
            if quiet:
                self._history = [item for item in self._history if item.get("job") != job]
                _save_history(self._history)
        self._events.put({"type": "watched", "list": self.watched()})
        return quiet

    def watch(self, job: int) -> list[dict]:
        """Starts watching the release a finished card came from."""
        with self._lock:
            entry = next((item for item in self._history if item.get("job") == job), None)
            if entry and entry.get("link") and not entry.get("dry_run"):
                used = {"folder": entry.get("music_folder"), "format": entry.get("format"),
                        "track_name": entry.get("track_name"),
                        "folder_name": entry.get("folder_name")}
                settings = {**prefs._load_settings(), **{key: value for key, value in used.items() if value}}
                self._watched = watch.add(self._watched, entry["link"], entry.get("title", ""),
                                          entry.get("service", ""), settings)
                watch.save(self._watched)
        return self.watched()

    def unwatch(self, link: str) -> list[dict]:
        with self._lock:
            self._watched = watch.remove(self._watched, link)
            watch.save(self._watched)
        return self.watched()

    def check_watched(self, force: bool = False) -> list[dict]:
        """Queues every watched link that is due, or all of them when asked.

        A check is an ordinary download of the same link into the same folder:
        the downloader skips what is already there, so only new tracks arrive.
        """
        now = time.time()
        artists = []
        with self._lock:
            entries = list(self._watched) if force else watch.due(self._watched, now)
            if not entries:
                return self.watched()
            settings = prefs._load_settings()
            for entry in entries:
                if entry.get("kind") == "artist":  # read outside the lock: it asks the catalogue
                    entry["checked"] = now
                    artists.append(entry)
                    continue
                queued = self._enqueue(entry["link"], {**settings, **entry["settings"], "dry_run": False})
                entry["checked"], entry["job"] = now, queued["job"]
                # The window did not start this job, so it is told to draw a card
                self._events.put({"type": "added", **queued, "dry_run": False, "watched": True,
                                  "format": entry["settings"].get("format", settings["format"])})
            watch.save(self._watched)
        for entry in artists:
            self._check_artist(entry, settings)
        logs.log.info("слежение: проверяю %d", len(entries))
        return self.watched()

    def _check_artist(self, entry: dict, settings: dict) -> None:
        """Queues the releases a watched artist has put out since the last look."""
        try:
            releases = catalog.artist(entry["link"])["releases"]
        except SourceError as e:
            logs.log.warning("слежение: %s не открылся: %s", entry["link"], e)
            with self._lock:
                entry["last"] = {"state": "error", "ok": None, "failed": 0}
                watch.save(self._watched)
            return
        with self._lock:
            fresh = watch.new_releases(entry, releases)
            entry["last"] = {"state": "done", "ok": len(fresh), "failed": 0}
            for release in fresh:
                queued = self._enqueue(release["link"], {**settings, **entry["settings"], "dry_run": False})
                self._events.put({"type": "added", **queued, "dry_run": False, "watched": True,
                                  "format": entry["settings"].get("format", settings["format"])})
            watch.save(self._watched)
        if fresh:
            logs.log.info("слежение: у «%s» новых релизов: %d", entry.get("title"), len(fresh))
        self._events.put({"type": "watched", "list": self.watched()})

    def _watch_loop(self) -> None:
        time.sleep(30)  # let the window settle before the first check
        while True:
            try:
                if watch.due(self._watched):
                    self.check_watched()
                    self._events.put({"type": "watched", "list": self.watched()})
            except Exception:
                logs.log.exception("слежение: проверка не удалась")
            time.sleep(15 * 60)

    def watch_album(self, path: str) -> list[dict]:
        """Starts watching an album from its page in the library.

        New tracks go to the music folder the album sits in, in the format its
        files already have, named by the current rules.
        """
        target = Path(path)
        marker = read_marker(target)
        link = marker.get("link")
        if not link:
            return self.watched()
        try:
            formats = [file.suffix[1:].lower() for file in target.iterdir() if folders.is_audio(file)]
        except OSError:
            formats = []
        settings = {**prefs._load_settings(), "folder": str(target.parent)}
        if formats:
            settings["format"] = max(set(formats), key=formats.count)
        with self._lock:
            self._watched = watch.add(self._watched, link, marker.get("album") or target.name,
                                      marker.get("service", ""), settings)
            watch.save(self._watched)
        return self.watched()

    def poll(self) -> list[dict]:
        events = []
        while len(events) < 500:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def _work(self) -> None:
        while True:
            with self._lock:
                if self._jobs.empty():
                    self._worker = None
                    threading.Thread(target=self._announce, daemon=True).start()
                    return
                job, link, options = self._jobs.get_nowait()
                self._stop.clear()  # a stop cancels only what was started or queued before it
            self._run(job, link, options)

    def _run(self, job: int, link: str, options: Options) -> None:
        # The tracklist and how each track ended. Without it a card restored on
        # the next run has nothing to unfold and hides its arrow. Track events
        # are far too many to write the file on each one, so the states are kept
        # here and saved once, when the job is over.
        tracks: dict[str, dict] = {}
        names = {"title": link}  # the release's own name once it is known, for the notice

        def finished_tracks() -> list[dict]:
            return [dict(item, state="cancel") if item["state"] in UNFINISHED else item
                    for item in tracks.values()]

        def emit(**event) -> None:
            self._events.put({"job": job, **event})
            kind = event.get("type")
            if kind == "release":  # the card's title and tracklist, kept for next time
                names.update(title=event["title"], folder=event["folder"], single=bool(event.get("single")))
                tracks.clear()
                for item in event["tracks"]:
                    tracks[item["id"]] = {**item, "state": "waiting", "source": "", "text": ""}
                self._remember({"job": job, "link": link, "state": "running", "title": event["title"],
                                "artist": event["artist"], "kind": event["kind"], "year": event["year"],
                                "service": event["service"], "album": event["album"],
                                "cover": event["cover"], "folder": event["folder"],
                                "total": len(event["tracks"]), "tracks": list(tracks.values()),
                                "single": bool(event.get("single"))})
            elif kind == "track":
                known = tracks.get(event.get("id"))
                if known is not None:
                    known["state"] = event.get("state", known["state"])
                    known["text"] = event.get("text", "")
                    if event.get("source"):
                        known["source"] = event["source"]

        emit(type="job", state="running")
        self._remember({"job": job, "state": "running"})
        downloader = Downloader(
            options,
            log=lambda message: None,
            progress=lambda done, total: emit(type="progress", done=done, total=total),
            events=lambda kind, data: emit(type=kind, **data),
            stop_event=self._stop,
            resume_event=self._resume,
        )
        logs.log.info("ссылка: %s", link)
        try:
            report = downloader.download_link(link)
        except Exception as e:  # shown on the card, the next link still runs
            logs.log.exception("ссылка не скачалась: %s", link)
            message = str(e) or type(e).__name__
            emit(type="job", state="error", message=message, code=getattr(e, "code", ""))
            self._remember({"job": job, "state": "error", "message": message,
                            "tracks": finished_tracks()})
            if not self._stop.is_set():
                with self._lock:
                    self._finished.append({"title": names["title"], "state": "error", "message": message})
            return
        state = "cancelled" if self._stop.is_set() else "done"
        counts = {"ok": len(report.ok), "skipped": len(report.skipped), "failed": len(report.failed)}
        self._remember({"job": job, "state": state, "dry_run": options.dry_run,
                        "tracks": finished_tracks(), **counts})
        quiet = self._finish_watch_check(job, state, counts)
        if state == "done" and not options.dry_run and (report.ok or report.skipped):
            _remember_downloaded(names, report.paths.values())
        emit(type="job", state=state, dry_run=options.dry_run, quiet=quiet, **counts)
        # A check that found nothing new, a trial run and a stopped one are not news
        if state == "done" and not quiet and not options.dry_run:
            with self._lock:
                self._finished.append({"title": names["title"], "state": state, **counts})

    def _remember(self, entry: dict) -> None:
        """Adds to, or updates, what is known about a job, and saves it."""
        with self._lock:
            known = next((item for item in self._history if item.get("job") == entry.get("job")), None)
            if known is None:
                self._history.append(entry)
            else:
                known.update(entry)
            del self._history[:-HISTORY_LIMIT]
            _save_history(self._history)
