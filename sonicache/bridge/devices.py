"""The Sync page: devices, their plans and syncs, and the library over Wi-Fi.

One part of gui.Api: the methods the page calls on window.pywebview.api.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .. import logs, playlists, prefs, subsonic
from ..devicestate import (DEVICES_FILE, SMART_NAMES, WIFI_FILE, _DEVICES_LOCK, _devices_data, _find_device,
                           _new_wifi_password, _selection, _sync_sources, _synced, _wifi_settings)
from ..engine import devices
from ..engine.downloader import find_tool
from ..i18n import t
from ..libraryscan import _cover_bytes, _favourites, _library_just_read


class Devices:
    # Devices: the library copied to a player or a phone that shows up as a
    # drive, or to any folder chosen as one (engine/devices.py does the work)

    def devices(self) -> list[dict]:
        """The devices there now, each with what it is to get and whether it was synced before."""
        data = _devices_data()
        ffmpeg = bool(find_tool("ffmpeg"))
        return [{**device.to_dict(), "selection": _selection(data["devices"].get(device.id, {}).get("selection")),
                 "synced": _synced(device), "ffmpeg": ffmpeg}
                for device in devices.find_devices(data["folders"])]

    def add_device_folder(self) -> dict | None:
        """A folder chosen as a device: a phone's music folder, a mounted card, a share."""
        chosen = self.choose_folder("")
        if not chosen:
            return None
        with _DEVICES_LOCK:
            data = _devices_data()
            if devices.folder_id(chosen) not in map(devices.folder_id, data["folders"]):
                data["folders"].append(chosen)
                logs.write_json(logs.data_dir() / DEVICES_FILE, data)
        return next((device for device in self.devices() if device["id"] == devices.folder_id(chosen)), None)

    def forget_device_folder(self, device_id: str) -> None:
        with _DEVICES_LOCK:
            data = _devices_data()
            data["folders"] = [path for path in data["folders"] if devices.folder_id(path) != device_id]
            data["devices"].pop(device_id, None)
            logs.write_json(logs.data_dir() / DEVICES_FILE, data)

    def save_device(self, device_id: str, selection: dict) -> None:
        with _DEVICES_LOCK:
            data = _devices_data()
            data["devices"].setdefault(str(device_id), {})["selection"] = _selection(selection)
            logs.write_json(logs.data_dir() / DEVICES_FILE, data)

    def device_plan(self, device_id: str) -> dict:
        """What a sync would do now, for the line above its button."""
        device = _find_device(device_id)
        if device is None:
            return {"error": t("Устройство не найдено")}
        if device.note:
            return {"error": device.note}
        try:
            with devices.open_target(device) as target:
                work, missing = self._device_work(device, target)
        except OSError as e:  # a phone that went away or stopped answering
            return {"error": t("Устройство не отвечает: {error}", error=e.strerror or e)}
        audio = [item for item in work.write if item.audio]
        return {"tracks": len(audio), "bytes": sum(item.size for item in audio), "deleted": len(work.delete),
                "keep": work.keep, "conflicts": len(work.conflicts), "missing": missing, "need": work.need,
                "free": work.free, "fits": work.fits, "unplayable": work.unplayable}

    def sync_device(self, device_id: str) -> bool:
        """Starts a sync in the background; False when one is running already."""
        with self._lock:
            if self._syncing is not None:
                return False
            stop = self._syncing = threading.Event()
        threading.Thread(target=self._sync, args=(str(device_id), stop), daemon=True).start()
        return True

    def stop_sync(self) -> None:
        with self._lock:
            if self._syncing is not None:
                self._syncing.set()

    def _device_work(self, device: devices.Device, target: devices.Target,
                     fresh: bool = False) -> tuple[devices.Plan, int]:
        """The plan for a device. A preview, asked for at every tick of a box, takes the library
        as it was last read; the sync itself reads the folders again unless that was just done."""
        selection = _selection(_devices_data()["devices"].get(device.id, {}).get("selection"))
        settings = prefs._load_settings()
        roots = [settings["folder"], *(settings.get("library_folders") or [])]
        library = _library_just_read(roots) or (None if fresh else self.library_cached(roots)) or self.library(roots)
        found, specs, missing = _sync_sources(selection, roots, library)
        work = devices.plan(target, found, specs, transcode=selection["transcode"],
                            bitrate=selection["bitrate"], can_transcode=bool(find_tool("ffmpeg")))
        return work, missing

    def _sync(self, device_id: str, stop: threading.Event) -> None:
        result = {"type": "sync", "state": "error", "device": device_id, "name": "", "copied": 0, "deleted": 0,
                  "failed": [], "conflicts": 0, "error": ""}
        try:
            device = _find_device(device_id)
            if device is None:
                result["error"] = t("Устройство не найдено")
                return
            result["name"] = device.name
            if device.note:
                result["error"] = device.note
                return

            def progress(done: int, total: int, title: str) -> None:
                self._events.put({"type": "sync", "state": "running", "device": device_id, "done": done,
                                  "total": total, "title": title})
            with devices.open_target(device) as target:
                work, _ = self._device_work(device, target, fresh=True)
                if not work.fits:
                    result["error"] = t("На устройстве не хватает места")
                    return
                report = devices.run(target, work, find_tool("ffmpeg"), progress, stop)
            result.update(state="error" if report.error else "stopped" if report.stopped else "done",
                          copied=report.copied, deleted=report.deleted, failed=report.failed[:20],
                          conflicts=len(report.conflicts), error=report.error)
            logs.log.info("синхронизация с %s: записано %d, удалено %d, не удалось %d%s", device.name,
                          report.copied, report.deleted, len(report.failed),
                          f", ошибка: {report.error}" if report.error else "")
        except OSError as e:  # a phone that went away before the sync could begin
            logs.log.warning("синхронизация с %s не началась: %s", device_id, e)
            result["error"] = t("Устройство не отвечает: {error}", error=e.strerror or e)
        except Exception as e:  # whatever it was, the window has to hear that the sync is over
            logs.log.exception("синхронизация с %s не удалась", device_id)
            result["error"] = str(e) or type(e).__name__
        finally:
            with self._lock:
                self._syncing = None
            self._events.put(result)

    # The library over Wi-Fi: a Subsonic server phone players (Amperfy, Ultrasonic) play from

    def wifi(self) -> dict:
        """The switch, where a phone reaches the server, and how it signs in."""
        kept = _wifi_settings()
        return {"on": kept["on"], "running": self._wifi is not None, "port": self._wifi_port or kept["port"],
                "addresses": subsonic.local_addresses(), "user": subsonic.USER, "password": kept["password"],
                "error": self._wifi_error}

    def set_wifi(self, on: bool) -> dict:
        kept = _wifi_settings()
        kept["on"] = bool(on)
        logs.write_json(logs.data_dir() / WIFI_FILE, kept)
        if on:
            self._start_wifi(kept)
        else:
            self._stop_wifi()
        return self.wifi()

    def new_wifi_password(self) -> dict:
        """A new password; a phone signed in with the old one has to be told it."""
        kept = _wifi_settings()
        kept["password"] = _new_wifi_password()
        logs.write_json(logs.data_dir() / WIFI_FILE, kept)
        if self._wifi is not None:
            self._wifi.api.password = kept["password"]
        return self.wifi()

    def start_wifi_if_on(self) -> None:
        """At start: the server is back if it was left on."""
        kept = _wifi_settings()
        if kept["on"] and self._wifi is None:
            self._start_wifi(kept)

    def _start_wifi(self, kept: dict) -> None:
        with self._lock:
            if self._wifi is not None:
                return
            listener = subsonic.Listener(subsonic.Api(self._subsonic_source(), kept["password"]), kept["port"])
            try:
                self._wifi_port = listener.start()
            except OSError as e:
                self._wifi_error = t("Не удалось открыть порт {port}: {error}", port=kept["port"], error=e.strerror or e)
                logs.log.warning("wi-fi: %s", self._wifi_error)
                return
            self._wifi, self._wifi_error = listener, ""
        logs.log.info("wi-fi: библиотека открыта на порту %d", self._wifi_port)

    def _stop_wifi(self) -> None:
        with self._lock:
            listener, self._wifi, self._wifi_port, self._wifi_error = self._wifi, None, 0, ""
        if listener is not None:
            listener.stop()
            logs.log.info("wi-fi: библиотека закрыта")

    def _subsonic_source(self) -> subsonic.Source:
        def library():
            settings = prefs._load_settings()
            roots = [settings["folder"], *(settings.get("library_folders") or [])]
            items = _library_just_read(roots) or self.library_cached(roots) or self.library(roots)
            return items, self.tracks(roots)

        def lists():
            return ([{"id": kind, "name": t(SMART_NAMES[kind]), "tracks": playlists.smart(kind)}
                     for kind in playlists.SMART] + playlists.load())
        return subsonic.Source(library=library, playlists=lists, favourites=_favourites,
                               set_favourite=self.set_favourite, cover=lambda path: _cover_bytes(Path(path)),
                               listened=self.listened, plays=playlists.counts,
                               ffmpeg=lambda: find_tool("ffmpeg"))
