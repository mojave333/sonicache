"""The library: listing it, deleting, tidying up tags, covers, an album's tracks, lyrics,
the favourites and the albums seen.

One part of gui.Api: the methods the page calls on window.pywebview.api.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .. import libraryscan, logs
from ..engine import folders, lyrics
from ..engine.downloader import read_marker
from ..engine.models import Album, Track
from ..engine.tidy import tidy as tidy_up
from ..libraryscan import (FAVOURITES_FILE, SEEN_FILE, TIDIED_FILE, _embedded_lyrics, _entry_files, _favourites,
                           _files_signature, _forget_in_library_cache, _has_gaps, _library_items, _library_just_read,
                           _load_library_cache, _remember_tidied, _roots_key, _save_library_cache, _seen, _tags)
from ..system import _recycle


class Library:
    def library(self, roots: str | list[str]) -> list[dict]:
        """Album folders and single tracks in the music folder and the other
        folders the library reads, newest first."""
        roots = [roots] if isinstance(roots, str) else [str(root) for root in roots or []]
        # A folder added on its own is read first, so its albums are named as
        # its own and not as the insides of a folder that happens to hold it
        roots.sort(key=lambda root: len(Path(root).expanduser().parts), reverse=True)
        _load_library_cache()
        items, seen = [], set()
        with folders.scanning():
            for root in roots:
                for item in _library_items(Path(root).expanduser()):
                    if item["path"] not in seen:  # one folder inside another is read once
                        seen.add(item["path"])
                        items.append(item)
        items.sort(key=lambda item: item["modified"], reverse=True)
        _save_library_cache(_roots_key(roots), items)
        libraryscan._LIBRARY_LAST["read"] = (_roots_key(roots), time.monotonic(), items)
        return items

    def library_cached(self, roots: str | list[str]) -> list[dict]:
        """What the library held when it was last read, shown at once while
        it is read again; nothing for folders not read before."""
        roots = [roots] if isinstance(roots, str) else [str(root) for root in roots or []]
        _load_library_cache()
        return libraryscan._LIBRARY_CACHE["items"].get(_roots_key(roots), [])

    def delete(self, paths: list[str]) -> dict:
        """Send albums and tracks to the recycle bin; a wrong pick stays undoable."""
        failed, gone = [], set()
        for raw in paths:
            path = Path(raw).expanduser()
            try:
                _recycle(path)
            except OSError:
                failed.append(path.name)
                continue
            gone.add(str(raw))
            # A track's synced lyrics go with it; an album's are inside its folder already
            lrc = path.with_suffix(".lrc")
            if path.suffix.lower() in folders.AUDIO_SUFFIXES and lrc.is_file():
                try:
                    _recycle(lrc)
                except OSError:
                    pass
        _forget_in_library_cache(gone)
        return {"deleted": len(paths) - len(failed), "failed": failed}

    def tidy(self, entries: list[dict], settings: dict) -> bool:
        """Starts filling in what these library entries lack, in the background.

        Each entry is {"path", "artist", "title"} as the library listed it: the
        names are what a folder's name says, for files that say nothing
        themselves. False when a tidy-up is running already; one at a time is
        what MusicBrainz, at a request a second, can take anyway.
        """
        wanted = [(Path(str(entry["path"])).expanduser(), str(entry.get("artist") or ""),
                   str(entry.get("title") or ""))
                  for entry in entries if isinstance(entry, dict) and entry.get("path")]
        with self._lock:
            if self._tidying is not None or not wanted:
                return False
            stop = self._tidying = threading.Event()
        with_lyrics = bool(settings.get("lyrics", True))
        threading.Thread(target=self._tidy, args=(wanted, with_lyrics, stop), daemon=True).start()
        return True

    def stop_tidy(self) -> None:
        with self._lock:
            if self._tidying is not None:
                self._tidying.set()

    def _tidy(self, wanted: list[tuple[Path, str, str]], with_lyrics: bool, stop: threading.Event) -> None:
        result = {"type": "tidy", "state": "done", "total": len(wanted), "entries": 0, "files": 0,
                  "covers": 0, "lyrics": 0, "missed": []}
        logs.log.info("библиотека: привожу в порядок записей: %d", len(wanted))
        try:
            for index, (path, artist, title) in enumerate(wanted):
                if stop.is_set():
                    break
                name = title or path.stem
                self._events.put({"type": "tidy", "state": "running", "done": index, "total": len(wanted),
                                  "title": name})
                try:
                    outcome = tidy_up(path, artist, title, with_lyrics=with_lyrics, stop=stop)
                except Exception:  # one odd folder must not end the rest
                    logs.log.exception("библиотека: %s не приведён в порядок", path)
                    outcome = None
                if not stop.is_set():  # done with, whatever it came to: not offered again as it is
                    _remember_tidied(path)
                if outcome is None or not outcome.found:
                    result["missed"].append(name)
                    continue
                result["entries"] += bool(outcome.files or outcome.cover)
                result["files"] += outcome.files
                result["covers"] += outcome.cover
                result["lyrics"] += outcome.lyrics
        finally:
            with self._lock:
                self._tidying = None
            result["stopped"] = stop.is_set()
            logs.log.info("библиотека: дописаны теги в файлов: %d, обложек: %d, текстов: %d, не найдено: %s",
                          result["files"], result["covers"], result["lyrics"], ", ".join(result["missed"]) or "—")
            self._events.put(result)

    def tag_gaps(self, paths: list[str], settings: dict) -> list[str]:
        """Which of these library entries a tidy-up has something to add to:
        a file without a title, artist, album, genre, year, number, cover or,
        when lyrics are on, lyrics, or an album folder without cover.jpg.
        Entries tidied before and not changed since are left out."""
        with_lyrics = bool(settings.get("lyrics", True))
        tidied = logs.read_json(logs.data_dir() / TIDIED_FILE)
        _load_library_cache()
        found = []
        with folders.scanning():
            for raw in paths or []:
                entry = Path(str(raw))
                files = _entry_files(entry)
                if files and tidied.get(str(entry)) != _files_signature(files) and _has_gaps(entry, files, with_lyrics):
                    found.append(str(raw))
        _save_library_cache()
        return found

    def cover(self, path: str) -> str | None:
        """The address the window loads an album's cover.jpg from, or the
        picture inside a single track's file.

        It used to be the picture itself as a data: string, and the page kept
        every one it had shown until the window closed: a library of a thousand
        albums held hundreds of megabytes of base64. An address is a few dozen
        characters, and the picture behind it lives in the browser's cache,
        which lets go of what is out of sight.
        """
        target = Path(path)
        source = (folders.cover_file(target) or next(iter(folders.album_files(target)[0]), None)
                  if target.is_dir() else target)
        try:
            stat = source.stat()
        except (OSError, AttributeError):
            return None
        # The file's time in the address makes a replaced cover a new picture to the browser
        return f"{self._covers.address(str(target))}?v={stat.st_mtime_ns:x}"

    def covers(self, paths: list[str]) -> dict[str, str]:
        """Where the covers of many entries are, in one call: the window asks
        for the whole library at once, so a card that scrolls into sight has
        its address already and only waits for the picture."""
        found = {}
        with folders.scanning():
            for raw in paths or []:
                address = self.cover(str(raw))
                if address:
                    found[str(raw)] = address
        return found

    def album(self, path: str) -> dict:
        """The tracks of one library entry as their tags tell it, for the album page.

        An album this program downloaded also knows its whole tracklist, so the
        tracks that never arrived are listed too.
        """
        target = Path(path)
        files = folders.album_files(target)[0] if target.is_dir() else [target]
        tracks = [{**_tags(file), "path": str(file)} for file in files]
        tracks.sort(key=lambda track: (track["disc"], track["number"] or 999, track["title"].casefold()))
        marker = read_marker(target) if target.is_dir() else {}
        have = {(track["disc"], track["number"]) for track in tracks}
        missing = [item for item in marker.get("tracklist") or []
                   if isinstance(item, dict) and (item.get("disc", 1), item.get("number")) not in have]
        return {
            "tracks": tracks,
            "missing": missing,
            # Older downloads kept only the count: then the page says how many are missing
            "expected": marker.get("tracks") or 0,
            "link": marker.get("link", ""),
            "service": marker.get("service", ""),
        }

    def tracks(self, roots: str | list[str]) -> list[dict]:
        """Every track in the library's folders with its tags, for the Tracks tab."""
        result = []
        with folders.scanning():
            for item in _library_just_read(roots) or self.library(roots):
                entry = Path(item["path"])
                files = folders.album_files(entry)[0] if item["album"] else [entry]
                for file in files:
                    try:
                        modified = folders.stat(file).st_mtime
                    except OSError:
                        continue
                    result.append({**_tags(file), "path": str(file), "entry": item["path"],
                                   "cover": item["cover"], "modified": modified})
        _save_library_cache()
        return result

    def set_favourite(self, path: str, on: bool) -> list[str]:
        """Puts an album among the favourites or takes it out; the list as it now is."""
        kept = [entry for entry in _favourites() if entry != str(path)]
        if on:
            kept.append(str(path))
        logs.write_json(logs.data_dir() / FAVOURITES_FILE, kept)
        return kept

    def mark_seen(self, path: str) -> None:
        """An album was opened or played: its cover stops saying "New"."""
        seen = _seen()
        seen[str(path)] = time.time()
        logs.write_json(logs.data_dir() / SEEN_FILE, seen)

    def lyrics(self, path: str) -> dict:
        """The words of a track: synced ones from the .lrc beside it, plain ones
        from its tags, or both from LRCLIB when the file has none."""
        target = Path(path)
        if not folders.is_audio(target):
            return {"synced": "", "plain": "", "source": ""}
        try:
            synced = target.with_suffix(".lrc").read_text(encoding="utf-8-sig", errors="replace").strip()
        except OSError:
            synced = ""
        plain = _embedded_lyrics(target)
        if synced or plain:
            return {"synced": synced, "plain": plain, "source": "file"}
        tags = _tags(target)
        found = lyrics.find(Track(id="", title=tags["title"], artists=tags["artists"] or tags["album_artist"],
                                  duration=tags["duration"], track_number=0),
                            Album(id="", name=tags["album"], artist=tags["album_artist"]))
        if not found:
            return {"synced": "", "plain": "", "source": ""}
        return {"synced": found.synced, "plain": found.plain, "source": "lrclib"}
