# Device Sync (part 2: Android over MTP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An Android phone plugged in by cable in "File transfer" mode shows up under Devices and syncs like a drive.

**Architecture:** `plan`/`run` in `sonicache/engine/devices.py` move onto a small `Target` interface; `DriveTarget` keeps part 1's behaviour. A new `sonicache/engine/mtp.py` holds a ctypes COM layer over Windows Portable Devices (`_Wpd`) and `MtpTarget`, the path logic on top of it, testable with a fake `_Wpd`. Devices gain `kind="mtp"` and a `note` for a phone that is not ready.

**Tech Stack:** Python 3.12 stdlib + ctypes COM (no new dependency), pytest, plain JS/CSS.

**Spec:** `docs/superpowers/specs/2026-10-14-device-sync-mtp-design.md` (and part 1's spec it continues)

## Global Constraints

- No new dependencies; COM through ctypes vtables as in `sonicache/thumbbar.py`.
- Vtable indexes (from the system typelibs): IPortableDeviceManager GetDevices 3, RefreshDeviceList 4, GetDeviceFriendlyName 5; IPortableDevice Open 3, Content 5, Close 8; IPortableDeviceContent EnumObjects 3, Properties 4, Transfer 5, CreateObjectWithPropertiesOnly 6, CreateObjectWithPropertiesAndData 7, Delete 8; IEnumPortableDeviceObjectIDs Next 3; IPortableDeviceProperties GetValues 5, SetValues 6; IPortableDeviceResources GetStream 5; IPortableDeviceValues SetValue 5, SetStringValue 7, GetStringValue 8, SetUnsignedIntegerValue 9, SetUnsignedLargeIntegerValue 13, GetUnsignedLargeIntegerValue 14, SetGuidValue 27, GetGuidValue 28; IPortableDeviceKeyCollection Add 5; IPortableDevicePropVariantCollection Add 5; ISequentialStream Read 3, Write 4; IStream Commit 8; IUnknown Release 2.
- CLSIDs: PortableDeviceManager `0af10cec-2ecd-4b92-9581-34f6ae0637f3`, PortableDeviceFTM `f7c0039a-4762-488a-b4b3-760ef9a1ba9b`, PortableDeviceValues `0c15d503-d017-47ce-9016-7b3f978721cc`, PortableDeviceKeyCollection `de2d022d-2480-43be-97f0-d1fa2cf98f4f`, PortableDevicePropVariantCollection `08a99e2f-6d6d-4b80-af5a-baf2bcbe4cb9`. IIDs as in the typelib dump (spec).
- Property keys (GUID, pid), verified in the DLLs: object `EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C` ID 2, PARENT_ID 3, NAME 4, PERSISTENT_UNIQUE_ID 5, FORMAT 6, CONTENT_TYPE 7, SIZE 11, ORIGINAL_FILE_NAME 12; functional `8F052D93-ABCA-4FC5-A5AC-B01DF4DBE598` CATEGORY 2; storage `01A3057A-74D6-4E80-BEA7-DC4C212CE50A` CAPACITY 4, FREE_SPACE_IN_BYTES 5, DESCRIPTION 7; device `26D4979A-E643-4626-9E2B-736DC0C92FDC` PROTOCOL 6, FRIENDLY_NAME 12; client `204D9F0C-2292-4080-9F42-40664E70F859` NAME 2, MAJOR 3, MINOR 4, REVISION 5; resource default `E81E79BE-34F0-41BF-B53F-F1A06AE87842` pid 0. Content types: folder `27E2E392-A111-48E0-AB0C-E17705A05F85`, functional object `99ED0160-17FF-4C44-9D98-1D7A6F941921`, generic file `0085E0A6-8D34-45D7-BC5C-447E59C73D48`; storage category `23F05BBC-15DE-4C2A-A55B-A9AF5CE412EF`; formats properties-only `30010000-AE6C-4804-98BA-C57B46965FE7`, unspecified `30000000-AE6C-4804-98BA-C57B46965FE7`.
- Only devices whose protocol starts with `MTP` are listed. Files live under `Music/Sonicache` of each storage. Device list cached 15 s per PnP id.
- Part 1's rules hold: deletes only manifest paths inside `Sonicache/`; `.part` then rename; manifest saved after deletions, every 20 files and in `finally`.
- Window text through `t()` with English in `sonicache/web/i18n.js` / `sonicache/engine/i18n.py`. Commit to main, descriptive messages, `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- The phone is locked or set to "Charging only" → it is listed with a note and cannot be synced (Task 4 `test_a_phone_without_storages_is_listed_as_not_ready`).
- The phone cannot rename objects → the file is still written under its final name (Task 2 `test_put_without_rename_writes_under_the_final_name`).
- A file is replaced on the phone (MTP cannot overwrite) → the old object is deleted first, no duplicate names (Task 2 `test_put_replaces_a_file`).
- The phone is unplugged mid-run → COM error ends the run as a device error with the manifest saved as far as possible (Task 2 `test_a_device_error_on_mtp_ends_the_run`).
- A USB stick or an iPhone also seen by WPD → not listed as MTP (Task 3 `test_only_mtp_devices_are_listed`).

---

### Task 1: The Target interface, and the drive behind it

**Files:** Modify `sonicache/engine/devices.py`; Test `tests/test_devices.py`

**Interfaces — Produces:** `class Target` (methods per spec table: `free`, `exists`, `is_file`, `read_bytes`, `write_bytes`, `put(local: Path, dest: str) -> int`, `delete`, `prune(folder: str)`, `clean_parts`), `class DriveTarget(Target)` with `root: Path`; `as_target(root: Path | Target) -> Target`. `plan`, `run`, `load_manifest`, `save_manifest` accept `Path | Target`. `_to_mp3` writes to a local temp file that `put()` copies.

- [ ] Step 1: failing test `test_plan_and_run_take_a_target_as_well_as_a_path` (DriveTarget(dev) passed to plan and run → file written, manifest readable via `load_manifest(dev)`).
- [ ] Step 2: run → FAIL (no DriveTarget). Step 3: refactor. Step 4: whole `tests/test_devices.py` green (all part 1 tests unchanged). Step 5: commit.

### Task 2: MtpTarget over a fake WPD session

**Files:** Create `sonicache/engine/mtp.py` (MtpTarget part), `tests/test_mtp.py`

**Interfaces — Consumes:** Task 1 `Target`. **Produces:** session protocol used by MtpTarget: `children(parent_id) -> list[Entry]` (`Entry(id, name, folder: bool, size: int)`), `make_folder(parent_id, name) -> str`, `write(parent_id, name, data: Iterable[bytes], size: int) -> None`, `rename(object_id, name) -> bool` (False = not supported), `remove(object_id) -> None`, `read(object_id) -> bytes`, `space(storage_id) -> tuple[int, int]` (free, total). `MtpTarget(session, storage_id: str, base: str = "Music")`.

- [ ] Step 1: tests in `tests/test_mtp.py` with `FakeWpd` (dict tree): `test_exists_and_read_follow_the_path`, `test_put_creates_the_folders_on_the_way`, `test_put_replaces_a_file`, `test_put_without_rename_writes_under_the_final_name`, `test_delete_and_prune_stop_at_sonicache`, `test_clean_parts_removes_half_written_files`, `test_names_are_matched_without_regard_to_case`, `test_free_is_the_storage_s`; plus a sync on it: `test_a_sync_onto_a_phone_writes_and_a_second_one_keeps` (devices.plan/run with `MtpTarget(FakeWpd)`), `test_a_device_error_on_mtp_ends_the_run` (fake raises OSError on write → report.error, manifest kept).
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS. Step 5: commit.

### Task 3: The WPD COM layer and the list of phones

**Files:** Modify `sonicache/engine/mtp.py`; Test `tests/test_mtp.py`

**Interfaces — Produces:** `_Wpd(pnp_id)` implementing Task 2's session over COM (context manager: opens/closes the device), `Storage(pnp_id, device_name, storage_id, description, free, total, persistent_id)`, `Phone(pnp_id, name, storages: list[Storage])`, `find_phones() -> list[Phone]` (cached 15 s), `_list_devices() -> list[tuple[str, str, str]]` (pnp id, friendly name, protocol). Not on Windows: `find_phones()` → `[]`.

- [ ] Step 1: tests: `test_values_round_trip_a_string_a_guid_and_a_number` (Windows only), `test_collections_take_keys_and_strings` (Windows only), `test_only_mtp_devices_are_listed` (monkeypatch `_list_devices` and `_storages_of`), `test_the_list_is_kept_for_a_while` (monkeypatch time), `test_find_phones_on_this_machine_is_a_list`.
- [ ] Step 2–4. Step 5: commit.

### Task 4: Phones as devices, in the engine and the window's side

**Files:** Modify `sonicache/engine/devices.py`, `sonicache/gui.py`, `sonicache/engine/i18n.py`; Test `tests/test_devices.py`, `tests/test_gui.py`

**Interfaces — Produces:** `Device.note: str = ""`, `kind` `"mtp"`, root `"mtp:<pnp>|<storage>"`; `devices.target(device) -> Target`; `find_devices` adds phones (ready storages, and a phone with none as a not-ready device with note `t("Разблокируйте телефон и выберите в уведомлении «Передача файлов»")`). gui: `devices()` uses `devices.target(device)` for the manifest; `_device_work` and `_sync` plan/run on `devices.target(device)`; `device_plan` of a not-ready device → `{"error": device.note}`.

- [ ] Step 1: tests `test_a_phone_s_storages_are_devices`, `test_a_phone_without_storages_is_listed_as_not_ready`, `test_the_target_of_a_phone_is_an_mtp_target` (devices), `test_a_not_ready_phone_cannot_be_synced` (gui, monkeypatched `mtp.find_phones`).
- [ ] Step 2–4; whole suite green. Step 5: commit.

### Task 5: The window and the README

**Files:** Modify `sonicache/web/app.js`, `sonicache/web/i18n.js`, `README.md`

- [ ] Device page: for `kind === "mtp"` the line under the name reads `t("По кабелю · папка Music/Sonicache")`, `#device-open` hidden; a device with `note` shows the note as the preview and disables the button; side row of a not-ready phone shows no free space.
- [ ] Verify in the stand (a phone, a not-ready phone, a drive) in both themes and English; `node --check`.
- [ ] README: the "Players and phones" section gains phones by cable and the manual check with a real phone (plug in, choose "File transfer", it appears, sync, look in Music/Sonicache on the phone). Commit.
