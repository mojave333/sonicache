# Device Sync (part 1: device as a drive) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sync chosen playlists, albums and artists (or the whole library) from Sonicache to a removable drive or any chosen folder, with a preview and one button.

**Architecture:** A UI-free engine module `sonicache/engine/devices.py` finds drives, plans the difference against a manifest kept on the device and carries the plan out (copy or ffmpeg to mp3). `sonicache/gui.py` keeps the per-device selection in `devices.json`, turns it into the list of source files and exposes thin `Api` methods; the window gets a "Устройства" section in the library's side list and a device page in `#library-page`.

**Tech Stack:** Python 3.12 (stdlib, ctypes on Windows, mutagen via existing helpers), ffmpeg (existing `find_tool`), pywebview page in plain JS/CSS, pytest.

**Spec:** `docs/superpowers/specs/2026-10-14-device-sync-design.md`

## Global Constraints

- Program writes and deletes only under `<root>/Sonicache/`; deletes only paths listed in `<root>/Sonicache/.sonicache-sync.json`.
- Manifest: `{"version": 1, "files": {<posix rel from root>: {"source", "size", "mtime_ns", "mode", "bytes"}}}`; `mode` is `copy`, `mp3:<bitrate>` or `playlist`.
- Unreadable manifest or other `version` → treated as empty, warning logged, nothing deleted.
- Names: `<>:"/\|?*` and control characters → `_`, trailing dots/spaces stripped, each part ≤ 120 chars, whole rel path ≤ 240.
- Bitrates offered: 128, 192, 256, 320; default 256. mp3 sources are copied even with transcoding on.
- Size after transcoding: `duration * bitrate * 1000 / 8 + 65536`.
- Playlists: `Sonicache/Playlists/<name>.m3u8`, relative paths with `/`.
- Manifest saved after deletions, every 20 files and in `finally`, atomically via `.tmp`.
- Window texts through `t()`; every new Russian line gets its English in `sonicache/web/i18n.js` (window) or `sonicache/i18n.py` (Python-side messages).
- Device icon: existing `i-folder` (new Phosphor icons need a download, not done here).
- Commit messages describe the change in a sentence (repo habit), end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; commit to `main`, files added by name.

## Review Focus

- The device is unplugged mid-run → `run` stops with `error`, the manifest lists exactly what was finished, the next run continues (Task 3 test `test_device_gone_mid_run_keeps_what_was_done`).
- A library file is renamed or moved → its old copy is deleted and the new one written (Task 2 test `test_a_moved_source_is_deleted_and_copied_anew`).
- The same album is selected both as an album and through its artist → it is synced once (Task 6 test `test_an_album_chosen_twice_is_synced_once`).
- A playlist holds a file outside every library folder → it lands under `Sonicache/Other/<parent folder>/<file>` (Task 6 test `test_a_track_outside_the_library_goes_under_other`).
- A drive with an empty volume label → named `Диск E:` (Task 5 test `test_a_drive_without_a_label_is_named_by_its_letter`).

---

### Task 1: Names and the manifest

**Files:**
- Create: `sonicache/engine/devices.py`
- Test: `tests/test_devices.py`

**Interfaces:**
- Produces: `SYNC_DIR = "Sonicache"`, `MANIFEST = ".sonicache-sync.json"`, `BITRATES = (128, 192, 256, 320)`, `DEFAULT_BITRATE = 256`;
  `safe_rel(rel: str) -> str` (posix, cleaned per Global Constraints);
  `load_manifest(root: Path) -> dict[str, dict]` (the `files` map; `{}` on any problem);
  `save_manifest(root: Path, files: dict[str, dict]) -> None` (atomic, creates `Sonicache/`).

- [ ] **Step 1: Write the failing tests**

```python
def test_safe_rel_cleans_each_part_for_fat():
    assert devices.safe_rel('AC/DC: Live?/01 "Hi" .flac.') == "AC/DC_ Live_/01 _Hi_ .flac"
def test_safe_rel_limits_a_part_and_the_path():
    rel = devices.safe_rel("/".join(["x" * 200] * 3))
    assert all(len(part) <= 120 for part in rel.split("/")) and len(rel) <= 240
def test_manifest_round_trips(tmp_path):
    devices.save_manifest(tmp_path, {"Sonicache/a.mp3": {"source": "s", "size": 1, "mtime_ns": 2, "mode": "copy", "bytes": 1}})
    assert devices.load_manifest(tmp_path)["Sonicache/a.mp3"]["mode"] == "copy"
def test_a_broken_manifest_is_empty(tmp_path):
    (tmp_path / "Sonicache").mkdir(); (tmp_path / "Sonicache" / devices.MANIFEST).write_text("{nope")
    assert devices.load_manifest(tmp_path) == {}
def test_a_manifest_of_another_version_is_empty(tmp_path): ...  # {"version": 2, "files": {...}} -> {}
```

Note: in `safe_rel` the `/` is the separator, so `AC/DC` is two parts; the test pins that.

- [ ] **Step 2: Run** `.venv\Scripts\python -m pytest tests/test_devices.py -q` → FAIL (no module).
- [ ] **Step 3: Implement** in `devices.py`; `save_manifest` writes `{"version": 1, "files": ...}` to `MANIFEST + ".tmp"` then `os.replace`.
- [ ] **Step 4: Run** the same command → PASS.
- [ ] **Step 5: Commit** `tests/test_devices.py sonicache/engine/devices.py`.

### Task 2: The plan

**Files:** Modify `sonicache/engine/devices.py`; Test `tests/test_devices.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `@dataclass Source: path: Path; rel: str; duration: float = 0.0`
  - `@dataclass PlaylistSpec: name: str; sources: list[Path]`
  - `@dataclass Item: source: Path; dest: str; mode: str; size: int; audio: bool` (`dest` posix rel from root, starts with `Sonicache/`)
  - `@dataclass Plan: write: list[Item]; delete: list[str]; keep: int; conflicts: list[str]; playlists: dict[str, str]; free: int; need: int; fits: bool` (`playlists` maps dest → m3u8 text)
  - `plan(root: Path, sources: list[Source], playlists: list[PlaylistSpec], transcode: bool = False, bitrate: int = DEFAULT_BITRATE, can_transcode: bool = True) -> Plan`

Sidecars: for each audio source, its `.lrc` (same stem) when it exists, and once per source folder `folders.cover_file(folder)`; both mode `copy`, `audio=False`, dest beside the track's dest. Mode of audio: `mp3:<bitrate>` when `transcode and can_transcode and suffix != ".mp3"`, else `copy`. Duplicate dest → second gets ` (2)` before the suffix. Free space from `shutil.disk_usage(root).free`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_new_source_is_written(tmp_path): # plan(device, [Source(lib/"a.flac", "R/A/a.flac")], []) -> write[0].dest == "Sonicache/R/A/a.flac", mode "copy", keep 0
def test_an_unchanged_source_is_kept(...): # manifest entry with the source's size, mtime_ns, mode, dest file present -> write == [], keep == 1
def test_a_changed_source_is_written_again(...): # mtime_ns differs -> in write
def test_a_change_of_mode_writes_it_again(...): # manifest mode "copy", transcode=True -> dest ".mp3", old dest in delete
def test_what_is_no_longer_chosen_is_deleted(...): # manifest has "Sonicache/x.mp3", not wanted -> delete == ["Sonicache/x.mp3"]
def test_a_moved_source_is_deleted_and_copied_anew(...): # manifest rel "Sonicache/old/a.flac", source now at rel "new/a.flac" -> both
def test_a_file_of_someone_else_is_a_conflict(...): # "Sonicache/R/A/a.flac" exists, not in manifest -> conflicts == [dest], not in write, not in delete
def test_mp3_is_copied_even_when_transcoding(...): # a.mp3, transcode=True -> mode "copy"
def test_without_ffmpeg_nothing_is_transcoded(...): # can_transcode=False -> mode "copy"
def test_two_sources_on_one_name_get_a_number(...): # a.flac + a.mp3, transcode -> "a.mp3", "a (2).mp3"
def test_the_cover_and_the_lyrics_go_along(...): # cover.jpg and a.lrc beside -> their dests in write, audio False
def test_the_size_after_transcoding_is_estimated(...): # duration 100, bitrate 256 -> size == 100*256*1000//8 + 65536
def test_need_counts_what_is_freed_and_fits_compares_with_free(..., monkeypatch): # patch devices.shutil.disk_usage -> free 1000; need = written - freed; fits
def test_a_playlist_lists_its_tracks_relative_to_its_file(...): # playlists["Sonicache/Playlists/Mix.m3u8"] contains "../R/A/a.flac" and "#PLAYLIST:Mix"
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement `plan`.** Playlist text: build `{"name", "tracks": [{"path": <dest path of the source on the device>, ...}]}` and reuse `playlists.to_m3u8`? It lives in `sonicache/playlists.py` (outside the engine) and writes OS separators, so write the m3u8 lines here: `#EXTM3U`, `#PLAYLIST:<name>`, then for each source in the set `posixpath.relpath(dest, "Sonicache/Playlists")`; sources not in the set are left out. Playlist file name via `safe_rel`.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit.**

### Task 3: Carrying the plan out (copy, delete, resume)

**Files:** Modify `sonicache/engine/devices.py`; Test `tests/test_devices.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces:
  - `@dataclass Report: copied: int = 0; deleted: int = 0; failed: list[str]; conflicts: list[str]; stopped: bool = False; error: str = ""`
  - `run(root: Path, plan: Plan, ffmpeg: str | None = None, progress: Callable[[int, int, str], None] | None = None, stop: threading.Event | None = None) -> Report`
  - `class DeviceGone(Exception)` not exported; `error` carries the text.

Order per spec: remove `*.part` under `Sonicache/`; delete (and drop from the manifest), prune empty folders inside `Sonicache/`, save; write each item to `<dest>.part` then `os.replace`, record `{"source", "size", "mtime_ns", "mode", "bytes"}`, save every 20; write playlists whose text differs (mode `playlist`); delete manifest playlists no longer in `plan.playlists`; save in `finally`. A source that cannot be read → `failed` (its title), continue. An `OSError` while writing to the device or a vanished root → stop, `error = t("Устройство отключено или заполнено: {error}")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_run_copies_and_records(tmp_path): # after run: dest exists with same bytes; manifest[dest]["source"] == str(source); report.copied == 1
def test_run_deletes_only_what_the_manifest_lists(...): # an unlisted file in Sonicache/ survives; listed one gone; empty folders pruned
def test_a_second_run_writes_nothing(...): # plan again after run -> write == []
def test_stop_keeps_what_was_done(...): # progress callback sets stop after the 1st of 3 -> report.stopped; manifest has 1; next plan writes 2
def test_device_gone_mid_run_keeps_what_was_done(..., monkeypatch): # patch devices.shutil.copyfile to raise OSError on the 2nd -> report.error, manifest has 1
def test_leftover_part_files_are_removed(...): # Sonicache/x.mp3.part before -> gone after
def test_an_unreadable_source_is_reported_and_the_rest_go_on(...): # source deleted after plan -> failed == [name], others copied
def test_playlists_are_written_and_dropped(...): # first run writes Mix.m3u8; next plan without it -> file deleted
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement `run`.** **Step 4: Run** → PASS. **Step 5: Commit.**

### Task 4: Transcoding to mp3

**Files:** Modify `sonicache/engine/devices.py`; Test `tests/test_devices.py`

**Interfaces:** Produces `_to_mp3(ffmpeg: str, source: Path, target: Path, bitrate: int, stop: threading.Event | None) -> None`, used by `run` for items with mode `mp3:*`.

Command (spec): `[ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", src, "-map", "0:a:0", "-map", "0:v?", "-c:a", "libmp3lame", "-b:a", f"{bitrate}k", "-ar", "44100", "-c:v", "copy", "-map_metadata", "0", "-id3v2_version", "3", "-f", "mp3", target]`, `CREATE_NO_WINDOW` on Windows, process killed on stop, non-zero exit → `RuntimeError` with ffmpeg's last line (the item goes to `failed`). Without ffmpeg an `mp3:*` item is `failed`.

- [ ] **Step 1: Write the failing test** (`@pytest.mark.skipif(not FFMPEG, ...)` with `FFMPEG = find_tool("ffmpeg")` as in `tests/test_library.py`): make a 1 s flac with ffmpeg (`sine=frequency=440:duration=1`, tagged `-metadata title=Hi`), plan with `transcode=True, bitrate=128`, run → dest ends `.mp3`, `mutagen.File(dest).info.bitrate` ≈ 128000 (±16000), tag title `Hi`, source untouched.
- [ ] **Step 2–4:** fail, implement, pass. **Step 5: Commit.**

### Task 5: Finding the devices

**Files:** Modify `sonicache/engine/devices.py`; Test `tests/test_devices.py`

**Interfaces:**
- Produces: `@dataclass Device: id: str; name: str; root: str; free: int; total: int; kind: str` (+ `to_dict()` via `dataclasses.asdict`);
  `_drives() -> list[tuple[str, str, int]]` — `(root, label, serial)` of removable drives with a medium (Windows ctypes; elsewhere mounts under `/Volumes`, `/media/<user>`, `/run/media/<user>` with `serial = os.stat(path).st_dev`);
  `find_devices(folders: list[str]) -> list[Device]`; `folder_id(path: str) -> str` = `"folder:" + sha1(os.path.normcase(os.path.abspath(path)))[:12]`.

Drive `id = f"vol:{serial:08X}"`; name = label or `t("Диск {letter}", letter="E:")`; a folder that does not exist is left out; usage from `shutil.disk_usage`.

- [ ] **Step 1: Write the failing tests** (monkeypatch `devices._drives`): a drive and a folder both listed with ids `vol:1A2B3C4D` and `folder:…`; `test_a_drive_without_a_label_is_named_by_its_letter` (label "" root "E:\\" → name "Диск E:"); a missing folder is skipped.
- [ ] **Step 2–4:** fail, implement, pass (also a smoke call of the real `_drives()` that only asserts it returns a list). Add `"Диск {letter}": "Drive {letter}"` and the error line of Task 3 to `sonicache/i18n.py`. **Step 5: Commit.**

### Task 6: The window's side (selection, sources, Api)

**Files:** Modify `sonicache/gui.py` (new section near the library methods; constants near `FAVOURITES_FILE`); Test `tests/test_gui.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces (`Api` methods, all JSON-able):
  - `devices() -> list[dict]` — `Device.to_dict()` + `selection` (default `{"all": False, "playlists": [], "albums": [], "artists": [], "transcode": False, "bitrate": 256}`) + `synced: bool` (manifest non-empty) + `ffmpeg: bool`
  - `add_device_folder() -> dict | None` (folder dialog like `choose_folder`), `forget_device_folder(device_id: str) -> None`
  - `save_device(device_id: str, selection: dict) -> None` (normalised: known keys only, bitrate in `BITRATES`, artists lower-cased)
  - `device_plan(device_id: str) -> dict` → `{"tracks", "bytes", "deleted", "keep", "conflicts", "missing", "need", "free", "fits"}` (`tracks`/`bytes` over audio items of `write`)
  - `sync_device(device_id: str) -> bool`, `stop_sync() -> None`; events per spec.
  - module function `_sync_sources(selection: dict, roots: list[str], library: list[dict]) -> tuple[list[Source], list[PlaylistSpec], int]` (third value: missing count)
- `DEVICES_FILE = "devices.json"` in `logs.data_dir()`; read/written with `logs.read_json` / `logs.write_json` under a lock.

`_sync_sources`: entries from `library` (the items `library()` returns): all, or those whose `path` is in `albums`, or whose `artist.casefold()` is in `artists`; files of an album entry via `folders.album_files(Path(path))[0]`, a single track is itself; playlists from `playlists.load()` plus `playlists.smart("recent"|"top")` when chosen (all playlists when `all`); dedupe by `os.path.normcase(path)`; missing files counted, not returned; `rel` = path relative to the first root containing it, else `Other/<parent name>/<file name>`; `duration` from `_tags(path)["duration"]`. The library list: `_library_just_read(roots) or self.library(roots)`.

- [ ] **Step 1: Write the failing tests** (`tmp_path` library with plain files; `monkeypatch` `gui._tags` to return `{"duration": 60}` where needed and `logs.data_dir` to a tmp folder):
  `test_an_album_chosen_twice_is_synced_once`, `test_choosing_an_artist_takes_all_their_entries`, `test_all_takes_every_entry_and_every_playlist`, `test_a_track_outside_the_library_goes_under_other`, `test_a_missing_file_is_counted_not_synced`, `test_save_device_keeps_only_known_keys` (bitrate 999 → 256), `test_sync_device_runs_and_reports` (folder device in tmp, `sync_device` then wait on the events queue for `state == "done"`, file present on the "device").
- [ ] **Step 2–4:** fail, implement, pass. Run the whole suite: `.venv\Scripts\python -m pytest -q` → all pass. **Step 5: Commit.**

### Task 7: The window (side list, device page, progress)

**Files:** Modify `sonicache/web/index.html`, `sonicache/web/app.js`, `sonicache/web/style.css`, `sonicache/web/i18n.js`, `README.md` (a short "Devices" paragraph in the library section)

**Interfaces:** Consumes Task 6 `Api` methods and `sync` events.

- Side list: after `#side-playlist-new`, `<h4 class="side-head">Устройства</h4><div id="side-devices"></div>` and `<button class="side-item side-add" id="side-device-folder">+ Выбрать папку</button>`; rows via `sideItem({icon: "i-folder", name, count: free formatted})` with `data-device`.
- `state.devices = { items: [], timer: 0, sync: { running: false, device: "", done: 0, total: 0, title: "", note: "" }, planToken: 0 }`; `loadDevices()` on entering the library and every 3000 ms while `state.view === "library"` (cleared otherwise).
- Device page `#device-page` inside `#library-page` beside `#album-page`/`#artist-page`; `showPage("device")` hides the other two; `library.pages` entry `{ kind: "device", id }`; `backLabel()` handles it; `albumFillsWindow()` unchanged (device page is not full-window).
- Page contents per spec (header with name, root, space bar; "Вся библиотека" checkbox; three `<details>` lists with checkboxes and filter fields for albums and artists; transcode checkbox + bitrate select disabled without ffmpeg with title "Для пережатия нужен ffmpeg"; preview line; primary button toggling "Синхронизировать"/"Остановить"; "Убрать из списка" for `kind === "folder"`).
- Every change: `save_device`, then after 300 ms `device_plan` → preview line: `"Добавится {tracks} {trackWord} ({size}) · уберётся {deleted} · после синхронизации свободно {free}"` or `"Не хватает {size}"` (button disabled); conflicts add `" · чужих файлов на месте: {conflicts}"`.
- `handleEvent`: `type === "sync"` → `onSyncEvent`; `libraryNote()` shows `"Синхронизация: {done} из {total} · {title} · не отключайте устройство"` while running, then the summary `"На «{name}» записано {copied}, удалено {deleted}"` (+ `" · не удалось: {failed}"`, + error text) for 30 s, and `announce()`.
- CSS: `.device-page` layout reusing `.album-info` paddings, `.space-bar` (track `var(--divider)`, fill `var(--accent)`), checkbox lists with `max-height: 320px; overflow-y: auto`; no new tokens; pressed buttons only change shade.
- i18n.js: every Russian string above and the markup's new texts, plus `PLURALS["устройство"]` if used.

- [ ] **Step 1:** Markup + CSS + JS as above.
- [ ] **Step 2: Verify in the preview stand** (copy `sonicache/web` to `build/perf`, stub `devices`, `device_plan`, `save_device`, `sync_device` and emit `sync` events): the side list shows the device; the page opens and goes back; toggling "Вся библиотека" hides the lists; the preview line updates; a "running" event shows progress above the library, "done" shows the summary; English (`?lang=en` or `setLanguage("en")`) has no Russian left on the page; light and dark themes.
- [ ] **Step 3:** `node --check sonicache/web/app.js` (if node exists) and the full pytest suite pass.
- [ ] **Step 4: Commit.**
