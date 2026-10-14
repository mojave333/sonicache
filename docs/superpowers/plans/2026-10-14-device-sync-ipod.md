# Device Sync (part 4: classic iPod) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A classic iPod in disk mode shows up under Devices as an iPod, syncs from its page, and its own firmware lists the music because `iPod_Control/iTunes/iTunesDB` is written for it.

**Architecture:** `sonicache/engine/itunesdb.py` reads and writes the database as a tree of records (unknown records kept byte for byte) and signs it with hash58. `sonicache/engine/ipod.py` tells models apart and gives an `IpodTarget` — the `Target` of part 2 — that maps the plan's `Sonicache/<rel>` paths to `iPod_Control/Music/Fnn/SNCxxxxx.ext` and writes the database in hooks `devices.run()` now calls.

**Tech Stack:** Python stdlib (`struct`, `hashlib`, `hmac` in tests, `plistlib`, `ctypes`), mutagen (already a dependency), ffmpeg for transcoding, pytest, plain JS.

**Spec:** `docs/superpowers/specs/2026-10-14-device-sync-ipod-design.md` (format notes also in the session notes folder, `ipod-itunesdb-notes.md`).

## Global Constraints

- No new dependencies. Tests need no iPod: an iPod is a folder in `tmp_path` with `iPod_Control/iTunes`, `iPod_Control/Music/F00..F02`, `iPod_Control/Device/SysInfo`.
- Record sizes as libgpod writes them: mhbd 244, mhsd 96, mhlt/mhlp/mhla/mhli 92, mhit 0x248, mhyp 108, mhip 76, mhia 88, mhii 80, string mhod 24+16+UTF-16LE, mhbd version 0x30, platform 2.
- Dataset order for a new database: 1, 3, 2, 4, 8, 6, 10, 5. mhod 52 sort types: title 3, album 4, artist 5, genre 7, composer 0x12.
- Our files: `iPod_Control/Music/Fnn/SNC` + 5 of `[A-Z0-9]` + `.mp3|.m4a`; manifest at `iPod_Control/iTunes/sonicache-sync.json`; database written through `iTunesDB.sonicache.part` + `os.replace`.
- A manifest entry's `file` is trusted only if it matches `^iPod_Control/Music/F\d{2}/SNC[0-9A-Z]{5}\.(mp3|m4a)$`.
- Parts 1–3 behave exactly as before: `DriveTarget`/`MtpTarget` get the default hooks.
- Engine and window text through `t()`, English in `sonicache/engine/i18n.py` and `sonicache/web/i18n.js`; no new icons; pressed buttons only change shade.
- Commit each task to main with a message that says what changed, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push. Other sessions edit this checkout: `git status` before each commit, add only own files.

## Review Focus

- An iPod already filled by iTunes: its tracks, playlists, play counts and On-The-Go list survive a sync (Task 4 `test_itunes_tracks_and_playlists_survive`, Task 2 `test_play_counts_and_otg_are_carried_over`).
- A manifest someone edited so that `file` points at an iTunes file or outside `Music` → nothing of theirs is deleted (Task 4 `test_a_forged_file_in_the_manifest_deletes_nothing`).
- A database that does not parse (truncated, foreign version) → sync ends with an error before anything is deleted (Task 4 `test_an_unreadable_database_stops_before_deleting`).
- A Classic without a FireWire GUID anywhere → shown with a note, not synced unsigned (Task 3 `test_classic_without_guid_is_not_ready`).
- The cable pulled after some files → next sync lists them in the database without copying them again (Task 4 `test_a_stopped_sync_goes_on`).

---

### Task 1: iTunesDB records, tracks and playlists

**Files:** Create `sonicache/engine/itunesdb.py`, `tests/test_itunesdb.py`

**Interfaces — Produces:**
- `TrackInfo` dataclass: `path` (`:iPod_Control:Music:F07:SNC4KQ7Z.mp3` form), `title`, `artist`, `album`, `albumartist`, `genre`, `composer`, `kind` (`"mp3"`/`"m4a"`), `year`, `track`, `disc`, `length` (ms), `bitrate` (kbit/s), `samplerate`, `size`, `compilation: bool`, `dbid: int`, `added: int` (unix time).
- `Database` with `tracks -> list[Track]` (`id`, `dbid`, `title`, `artist`, `album`, `genre`, `composer`, `path`, `track`, `disc`), `playlists -> list[Playlist]` (`pid`, `name`, `master: bool`, `track_ids: list[int]`), `add_track(info) -> int`, `remove_tracks(dbids: set[int])`, `add_playlist(name, pid, track_ids)`, `remove_playlists(pids: set[int])`, `finish()`, `to_bytes() -> bytes`.
- `parse(data: bytes) -> Database` (raises `ValueError` on anything that is not an mhbd tree), `new_database() -> Database`, `ipod_path(rel: str) -> str` (`iPod_Control/Music/F00/X.mp3` → `:iPod_Control:Music:F00:X.mp3`).

- [ ] Tests: `test_a_new_database_reads_back` (8 datasets in order 1,3,2,4,8,6,10,5; master playlist; no tracks); `test_tracks_and_playlists_read_back` (two `TrackInfo`, one playlist; strings, numbers, dbid, path come back; master lists both in both datasets 2 and 3); `test_unknown_records_survive_byte_for_byte` (a database whose mhit has an extra unknown mhod type 200 and a longer header, plus an unknown dataset 9: `parse(x).to_bytes() == x`); `test_removing_tracks_takes_them_out_of_every_playlist`; `test_library_index_sorts_like_libgpod` (mhod 52 of type artist orders artist → album → disc → track → title, casefolded; mhod 53 letters and counts); `test_albums_and_artists_are_rebuilt` (dataset 4 has one mhia per album+artist, dataset 8 one mhii per artist; tracks carry their ids at 0x120 / 0x1E0); `test_garbage_is_not_a_database` (`ValueError`).
- [ ] RED → implement. Records as a generic node (`tag`, header bytes, children; list records count children, others carry total length) so unknown ones round-trip; typed accessors read/write header fields by offset. mhit fields as in libgpod `mk_mhit` (visible 1, filetype marker `"MP3 "`/`"M4A "` as big-endian int written little-endian, mediatype 1, samplerate at 0x3E, dbid at 0x70 and 0xA8, mark_unplayed 1, `id_0x24` of the mhbd at 0x128, `0x808080808080` at 0x134, 1 at 0x168). Track mhods: 1 title, 4 artist, 3 album, 6 filetype ("MPEG audio file" / "AAC audio file"), 2 path, 5 genre, 12 composer, 22 album artist. Playlists: title mhod + the fixed 0x288 mhod 100; each mhip has a position mhod 100. Mac time = unix + 2082844800 + local UTC offset. → GREEN → commit.

### Task 2: Signing, play counts, On-The-Go

**Files:** Modify `sonicache/engine/itunesdb.py`; Test `tests/test_itunesdb.py`

**Interfaces — Produces:** `hash58_key(guid: bytes) -> bytes` (64 bytes), `sign(data: bytes, guid: bytes) -> bytes` (sets scheme 1 at 0x30 and hash58 at 0x58), `apply_play_counts(db: Database, data: bytes)` (adds plays/skips, takes last played, bookmark, rating by track order; `ValueError` on a bad file), `otg_indexes(data: bytes) -> list[int]`, `Database.track_ids_in_order() -> list[int]`.

- [ ] Tests: `test_hash_tables_are_the_aes_sboxes` (computed S-box starts `63 7C 77 7B F2 6B 6F C5`, inverse `52 09 6A D5 30 36 A5 38`); `test_hash58_is_hmac_sha1_with_the_derived_key` (equals `hmac.new(key[:20]+zeros, zeroed_db, sha1)` with db_id, 0x32..0x45 and hash zeroed; db_id and 0x32 restored after); `test_signing_twice_gives_the_same_hash`; `test_play_counts_and_otg_are_carried_over` (entry len 0x1C: plays 3 added, rating 80, last played set; OTG indexes [1,0] → track ids of positions 1 and 0).
- [ ] RED → implement (S-box generated from GF(2^8) inverse + affine map) → GREEN → commit.

### Task 3: Telling iPods apart

**Files:** Create `sonicache/engine/ipod.py`, `tests/test_ipod.py`; Modify `sonicache/engine/devices.py` (`Device.kind` doc, `find_devices`), `sonicache/engine/i18n.py`

**Interfaces — Produces:** `IpodInfo(model: str, checksum: str, guid: bytes | None, note: str)`; `inspect(root: Path) -> IpodInfo`; `is_ipod(root: Path) -> bool` (has `iPod_Control/iTunes`, no `.rockbox`); `_usb_serial(root: Path) -> str` (Windows `IOCTL_STORAGE_QUERY_PROPERTY`, `""` elsewhere; tests patch it). `find_devices` gives such a drive `kind="ipod"` and `note=info.note`.

- [ ] Tests: `test_video_5g_needs_no_hash` (SysInfo `ModelNumStr: xA002`); `test_classic_from_sysinfo_takes_its_guid` (`xB147`, `FirewireGuid: 0x000A27001A2B3C4D`); `test_sysinfo_extended_db_version` (3 → hash58 with `FireWireGUID`; 4 → note); `test_an_itunes_signed_database_means_hash58` (no SysInfo, iTunesDB with non-zero hash58, GUID from patched `_usb_serial`); `test_classic_without_guid_is_not_ready`; `test_nano_5g_and_newer_are_not_supported` (`iTunesCDB` present, or `xC027`); `test_shuffle_is_not_supported` (`xC584`); `test_find_devices_tells_an_ipod_from_a_drive` (patched `_drives()`: iPod → `ipod`, same with `.rockbox` → `drive`).
- [ ] RED → implement (model table: hash58 = Classic B029 B147 B145 B150 B562 B565 C293 C297, Nano 3G A978 A980 B261 B249 B253 B257, Nano 4G B480 B651 B654 B657 B660 B663 B666 B598 B732 B735 B739 B742 B745 B748 B751 B754 B903 B905 B907 B909 B911 B913 B915 B917 B918; unsupported = Nano 5G C027…C075, Nano 6G C525…C699, Shuffles, touch; notes «Этот iPod не поддерживается: его база защищена подписью, которую нельзя сделать без iTunes» / «iPod shuffle не поддерживается» / «Не удалось узнать FireWire ID этого iPod: без него он не примет базу») → GREEN → commit.

### Task 4: IpodTarget and the hooks in devices

**Files:** Modify `sonicache/engine/ipod.py`, `sonicache/engine/devices.py`; Test `tests/test_ipod.py`, `tests/test_devices.py` (unchanged tests must pass)

**Interfaces — Consumes:** Tasks 1–3. **Produces:** `Target.plays`, `Target.companions`, `Target.begin(plan)`, `Target.describe(dest) -> dict`, `Target.finish(files: dict[str, dict])`; `Plan.unplayable: int`; `IpodTarget(root: Path, info: IpodInfo)`; `open_target(device)` yields it for `kind == "ipod"`.

- [ ] Tests (fake iPod, Video 5G unless said): `test_sync_puts_files_in_f_folders_and_lists_them` (files match the `SNC` pattern, database parses, titles/paths/playlist names present, master lists all); `test_second_sync_writes_nothing`; `test_unchosen_tracks_leave_the_database_and_the_disk`; `test_itunes_tracks_and_playlists_survive` (database made with a foreign track and playlist first: same ids and members after sync and after removing ours); `test_a_forged_file_in_the_manifest_deletes_nothing`; `test_an_unreadable_database_stops_before_deleting`; `test_a_stopped_sync_goes_on`; `test_opus_without_ffmpeg_is_unplayable` (and with `transcode=False`, an `.opus` source planned as `mp3:` mode when `can_transcode`); `test_no_covers_or_lyrics_beside_tracks`; `test_classic_database_is_signed` (hash verifies with `sign` recomputation); `test_play_counts_file_is_removed_after_writing`.
- [ ] RED → implement: `plan()` uses `target.plays`/`target.companions`; `run()` calls `begin` after `clean_parts`, merges `describe(dest)` into entries (files and playlists), and in `finally` saves the manifest then calls `finish(files)` (an `OSError`/`ValueError` there sets `report.error` if empty, «База iPod не записалась: {error}»). `IpodTarget.begin` writes the database without `plan.delete` and raises before any deletion if it cannot read it. Track tags via `downloader.read_tags` + mutagen `info` of the local file put. → GREEN, full `pytest` → commit.

### Task 5: The window, README

**Files:** Modify `sonicache/gui.py` (`devices()` synced check through `open_target` for non-mtp, `device_plan` adds `unplayable`), `sonicache/web/app.js` (`fillDevicePage`, `renderDevicePreview`), `sonicache/web/i18n.js`, `README.md`; Test `tests/test_gui.py`

- [ ] Test `test_device_plan_counts_unplayable_for_an_ipod` (patched `find_devices` returning an ipod `Device` on a fake iPod) → RED → implement.
- [ ] Window: root line «iPod · {root}»; transcode description for iPod «iPod играет только mp3 и m4a: остальное пережимается всегда. Включите, чтобы пережимать и m4a»; preview appends «iPod не сыграет без ffmpeg: {count}». English for each.
- [ ] README: the devices section gets the iPod part — models that work, those that do not and why, Rockbox stays a drive, covers not written, and a manual check (sync three albums and a playlist, eject safely, see Music → Artists/Playlists on the iPod, play, sync again with one album unticked).
- [ ] Full `pytest` green, `node --check sonicache/web/app.js` → commit.
