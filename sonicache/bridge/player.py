"""The player: playing a file, the taskbar and media buttons, what the session left, the
listens and scrobbling, and the playlists of one's own.

One part of gui.Api: the methods the page calls on window.pywebview.api.
"""

from __future__ import annotations

import locale
import os
import time
import webbrowser
from pathlib import Path

import webview
from mutagen import File as MutagenFile

from .. import logs, mediacontrols, playlists, prefs, scrobble, thumbbar
from ..engine import catalog, folders, loudness
from ..engine.downloader import _safe_name
from ..i18n import t
from ..libraryscan import _tags
from .downloads import LIST_BYTES

PLAYER_QUEUE_FILE, PLAYER_PLACE_FILE = "player-queue.json", "player-place.json"


class Player:
    # The player: the window streams a file from the local server and reads
    # its lyrics from beside it, from its tags, or from LRCLIB

    def play(self, path: str) -> dict | None:
        """Where the window streams one file from, with its tags and cover."""
        target = Path(path)
        if not folders.is_audio(target):
            return None
        cover = self.cover(str(target.parent if folders.cover_file(target.parent) else target))
        try:  # how the sound is made, for the line under the title in Now playing
            sound = MutagenFile(target).info
        except Exception:
            sound = None
        return {**_tags(target), "path": str(target), "url": self._covers.audio_address(str(target)),
                "cover": cover or "", "sample_rate": getattr(sound, "sample_rate", 0) or 0,
                "channels": getattr(sound, "channels", 0) or 0,
                # ReplayGain the player turns the volume by, from whatever tagged the file
                "gain": loudness.playback_gains(target)}

    def player_buttons(self, buttons: dict | None) -> None:
        """Previous, play or pause and next under the window's picture on the
        taskbar, as the player has them; None while nothing plays hides them.
        The same goes to Windows' media controls, with the title and the cover."""
        if not thumbbar.SUPPORTED or self._window is None:
            return
        with self._extras_lock:
            if self._media is None and buttons and mediacontrols.SUPPORTED:
                self._media = mediacontrols.MediaControls(self._window.native.Handle.ToInt64(), self._media_button)
            if self._thumbbar is None and buttons:
                try:
                    self._thumbbar = thumbbar.ThumbBar(self._window.native.Handle.ToInt64(), self._thumbbar_click)
                except Exception as e:  # a picture on the taskbar is never worth a failed call
                    logs.log.debug("кнопки на панели задач: %s", e)
            media, bar = self._media, self._thumbbar
        if media is not None:
            media.show(buttons)
        if bar is not None:
            bar.show(buttons)

    def now_playing(self, track: dict | None) -> None:
        """What plays, for the Discord profile: the track with how far into it
        the player is, or None while nothing plays or it is paused."""
        if track:
            try:
                position = max(0.0, float(track.get("position") or 0))
            except (TypeError, ValueError):
                position = 0.0
            # The moment the track started, so the bar is right however late it is shown
            track = {**track, "start": time.time() - position}
            track.pop("position", None)
        self._now = track or None
        if self._discord:
            self._presence.show(self._now)
        # Last.fm and ListenBrainz hear of each track once, as it starts; a
        # pause is no news to them
        if track and track.get("path") != self._announced:
            self._announced = str(track.get("path") or "")
            self._scrobbler.now_playing(track)

    def _web_cover(self, artist: str, album: str, title: str) -> str:
        """A picture on the web for what plays, since Discord shows no other:
        the album's cover, else that of a release with the same song, else the
        artist's photo."""
        return ((album and catalog.album_cover(artist, album)) or catalog.track_cover(artist, title)
                or self.artist_picture(artist))

    # What was listened to: kept for the play counts and "Most played", and
    # sent to Last.fm and ListenBrainz when an account is connected

    def listened(self, track: dict) -> None:
        """The player has played enough of a track to count it as listened to."""
        listen = playlists.record(track)
        if listen is not None:
            self._scrobbler.scrobble(listen)

    # The player as it was left, for the next run: the queue in one file,
    # rewritten when the queue changes, and the track and the place in it in
    # another, small one, written every few seconds while it plays

    def save_player(self, session: dict | None) -> None:
        folder = logs.data_dir()
        if not session:
            for name in (PLAYER_QUEUE_FILE, PLAYER_PLACE_FILE):
                try:
                    (folder / name).unlink(missing_ok=True)
                except OSError:
                    pass
            return
        if isinstance(session.get("queue"), list):
            queue = [entry for entry in map(playlists.track, session["queue"][:playlists.TRACKS_LIMIT]) if entry]
            unshuffled = session.get("unshuffled")
            logs.write_json(folder / PLAYER_QUEUE_FILE, {
                "queue": queue, "source": str(session.get("source") or "queue"),
                "unshuffled": [str(path) for path in unshuffled] if isinstance(unshuffled, list) else None})
        try:
            position = max(0.0, float(session.get("position") or 0))
        except (TypeError, ValueError):
            position = 0.0
        logs.write_json(folder / PLAYER_PLACE_FILE, {"index": int(session.get("index") or 0),
                                                 "path": str(session.get("path") or ""), "position": round(position, 1)})

    def player_session(self) -> dict | None:
        """What the player held when the program was last closed, or None:
        the queue must still hold the track it was on, and its file be there."""
        folder = logs.data_dir()
        saved, place = logs.read_json(folder / PLAYER_QUEUE_FILE), logs.read_json(folder / PLAYER_PLACE_FILE)
        queue = [entry for entry in map(playlists.track, saved.get("queue") or []) if entry]
        index = place.get("index") if isinstance(place.get("index"), int) else -1
        if not (0 <= index < len(queue)) or queue[index]["path"] != place.get("path"):
            return None
        if not Path(queue[index]["path"]).is_file():
            return None
        unshuffled = saved.get("unshuffled")
        return {"queue": queue, "index": index, "position": float(place.get("position") or 0),
                "source": str(saved.get("source") or "queue"),
                "unshuffled": unshuffled if isinstance(unshuffled, list) else None}

    def play_counts(self) -> dict[str, int]:
        return playlists.counts()

    def scrobble_accounts(self) -> dict:
        return self._scrobbler.accounts()

    def connect_listenbrainz(self, token: str) -> dict:
        try:
            return {"name": self._scrobbler.connect_listenbrainz(token)}
        except (scrobble.Rejected, scrobble.Invalid):
            return {"error": t("ListenBrainz не принял этот токен")}
        except scrobble.Unavailable as e:
            return {"error": t("ListenBrainz не ответил: {error}", error=e)}

    def connect_lastfm(self) -> dict:
        """Opens Last.fm's page where the person allows the program; the
        window then asks lastfm_connected() until they have."""
        try:
            url = self._scrobbler.lastfm_start()
        except (scrobble.Rejected, scrobble.Unavailable, scrobble.Invalid) as e:
            return {"error": t("Last.fm не ответил: {error}", error=e)}
        webbrowser.open(url)
        return {"url": url}

    def lastfm_connected(self) -> dict:
        try:
            return {"name": self._scrobbler.lastfm_finish()}
        except (scrobble.Unavailable, scrobble.Invalid) as e:
            return {"name": "", "error": str(e)}

    def disconnect_scrobbler(self, service: str) -> dict:
        if service in scrobble.SERVICES:
            self._scrobbler.disconnect(service)
        return self._scrobbler.accounts()

    # Playlists of one's own, and the two made from what was listened to

    def playlists(self) -> list[dict]:
        """Every playlist as its card shows it: name, count, length and cover."""
        listens = playlists.plays()
        cards = [self._playlist_card({"id": kind, "name": "", "tracks": playlists.smart(kind, listens)}, smart=True)
                 for kind in playlists.SMART]
        return cards + [self._playlist_card(entry) for entry in playlists.load()]

    def _playlist_card(self, entry: dict, smart: bool = False) -> dict:
        chosen = None if smart else playlists.cover_file(entry["id"])
        cover = (self.cover(str(chosen)) if chosen else "") or next(
            (address for address in map(self._track_cover, entry["tracks"][:8]) if address), "")
        return {"id": entry["id"], "name": entry["name"], "smart": smart, "count": len(entry["tracks"]),
                "duration": sum(track["duration"] for track in entry["tracks"]), "cover": cover,
                "custom": chosen is not None, "modified": entry.get("modified", 0)}

    def _track_cover(self, entry: dict) -> str:
        path = Path(entry["path"])
        if not path.exists():
            return ""
        return self.cover(str(path.parent if folders.cover_file(path.parent) else path)) or ""

    def playlist(self, playlist_id: str) -> dict | None:
        """A playlist with its tracks, each saying whether its file is still there."""
        if playlist_id in playlists.SMART:
            entry = {"id": playlist_id, "name": "", "tracks": playlists.smart(playlist_id)}
        else:
            entry = next((found for found in playlists.load() if found["id"] == playlist_id), None)
            if entry is None:
                return None
        card = self._playlist_card(entry, smart=playlist_id in playlists.SMART)
        tracks = [{**track, "missing": not os.path.exists(track["path"])} for track in entry["tracks"]]
        return {**card, "tracks": tracks}

    def save_playlist(self, playlist_id: str | None, name: str, tracks: list[dict]) -> dict:
        kept = [entry for entry in map(playlists.track, tracks or []) if entry]
        return self._playlist_card(playlists.save(playlist_id or None, str(name or ""), kept))

    def add_to_playlist(self, playlist_id: str, tracks: list[dict]) -> dict | None:
        entry = playlists.add(playlist_id, tracks or [])
        return self._playlist_card(entry) if entry else None

    def delete_playlist(self, playlist_id: str) -> None:
        playlists.delete(playlist_id)

    def set_playlist_cover(self, playlist_id: str) -> dict | None:
        """Asks for a picture and keeps it as the playlist's cover. Returns the playlist's card; None
        when nothing was chosen; {"error": ...} for a file that cannot be one."""
        if self._window is None or playlist_id in playlists.SMART:
            return None
        chosen = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=str(Path.home()),
            file_types=(t("Картинки (*.jpg;*.jpeg;*.png;*.webp)"), t("Все файлы (*.*)")))
        if not chosen:
            return None
        return self._keep_playlist_cover(playlist_id, Path(chosen[0]))

    def _keep_playlist_cover(self, playlist_id: str, path: Path) -> dict:
        try:
            data = path.read_bytes() if path.stat().st_size <= playlists.COVER_BYTES else b""
        except OSError as e:
            return {"error": str(e)}
        if not playlists.set_cover(playlist_id, data):
            return {"error": t("Нужна картинка JPEG, PNG или WebP не больше 8 МБ")}
        entry = next((found for found in playlists.load() if found["id"] == playlist_id), None)
        return self._playlist_card(entry) if entry else {"error": t("Плейлиста уже нет")}

    def reset_playlist_cover(self, playlist_id: str) -> dict | None:
        """Takes the chosen picture off, and the playlist shows its tracks' cover again."""
        playlists.clear_cover(playlist_id)
        entry = next((found for found in playlists.load() if found["id"] == playlist_id), None)
        return self._playlist_card(entry) if entry else None

    def export_playlist(self, playlist_id: str) -> str | None:
        """Writes the playlist as .m3u8 where the person says, the music
        folder offered first; returns the file's path."""
        entry = self.playlist(playlist_id)
        if entry is None or self._window is None:
            return None
        name = entry["name"] or t({"recent": "Недавно слушали", "top": "Часто слушаю"}.get(playlist_id, "Плейлист"))
        chosen = self._window.create_file_dialog(
            webview.FileDialog.SAVE, directory=prefs._load_settings()["folder"],
            save_filename=f"{_safe_name(name)}.m3u8", file_types=(t("Плейлисты (*.m3u8)"),))
        if not chosen:
            return None
        target = Path(chosen if isinstance(chosen, str) else chosen[0])
        if target.suffix.lower() not in (".m3u8", ".m3u"):
            target = target.with_suffix(".m3u8")
        try:
            target.write_text(playlists.to_m3u8({**entry, "name": name}, target), encoding="utf-8")
        except OSError as e:
            logs.log.warning("плейлист не записан в %s: %s", target, e)
            return None
        return str(target)

    def import_playlist(self) -> dict | None:
        """Reads an .m3u8 or .m3u file into a new playlist; the files it names
        that are there get their names from their own tags."""
        if self._window is None:
            return None
        chosen = self._window.create_file_dialog(
            webview.FileDialog.OPEN, directory=prefs._load_settings()["folder"],
            file_types=(t("Плейлисты (*.m3u8;*.m3u)"), t("Все файлы (*.*)")))
        if not chosen:
            return None
        return self._import_playlist(Path(chosen[0]))

    def _import_playlist(self, path: Path) -> dict | None:
        try:
            raw = path.read_bytes()[:LIST_BYTES]
        except OSError:
            return None
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:  # an old .m3u in the system's own code page
            text = raw.decode(locale.getpreferredencoding(False), errors="replace")
        found = playlists.from_m3u8(path, text)
        tracks = []
        for entry in found["tracks"]:
            file = Path(entry["path"])
            if folders.is_audio(file) and file.exists():
                tags = _tags(file)
                entry = {**entry, **{key: tags[key] for key in ("title", "artists", "album", "album_artist", "duration")
                                     if tags.get(key)}}
            tracks.append(entry)
        return self.save_playlist(None, found["name"], tracks)
