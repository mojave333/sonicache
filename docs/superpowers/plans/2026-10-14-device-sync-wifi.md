# Device Sync (part 3: Wi-Fi via Subsonic) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phones reach the Sonicache library over the home network through the Subsonic API, so Amperfy (iPhone) or Ultrasonic/Symfonium (Android) can play it and keep albums offline.

**Architecture:** `sonicache/subsonic.py` holds an `Index` built from plain library dicts, an `Api` that answers a request without sockets, and a `Listener` (ThreadingHTTPServer). `gui.py` feeds it through a `Source` of callables and keeps the server's switch, port and password in `wifi.json`; the window gets a "Телефон по Wi-Fi" page under Devices.

**Tech Stack:** Python stdlib (http.server, hashlib, xml.etree, json), mutagen-free (tags come from `Api.tracks`), ffmpeg for `format=mp3`, pytest, plain JS/CSS.

**Spec:** `docs/superpowers/specs/2026-10-14-device-sync-wifi-design.md`

## Global Constraints

- Subsonic version `1.16.1`; JSON envelope `{"subsonic-response": {"status", "version", "type": "sonicache", "serverVersion", "openSubsonic": true, ...}}`; XML root `subsonic-response` with `xmlns="http://subsonic.org/restapi"`.
- Errors: 0 generic / not supported, 10 missing parameter, 40 wrong user or password, 70 not found.
- ids: `ar-`, `al-`, `tr-`, `pl-` + `sha1(key)[:12]`; artist key = artist name casefolded, album key = entry path, track key = file path, playlist key = playlist id.
- Index lives 60 s; port 4533, up to +10 when taken; user `sonicache`; password 10 chars from `abcdefghijkmnpqrstuvwxyz23456789`.
- `wifi.json` in `logs.data_dir()`: `{"on": bool, "port": int, "password": str}`.
- Window text through `t()`, English in `sonicache/web/i18n.js`; icons only existing ones (`i-link`); pressed buttons only change shade.
- Commit to main with descriptive messages and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A request without credentials or with a wrong token → error 10 / 40, never data (Task 1 `test_wrong_or_missing_credentials_get_nothing`).
- An id not in the index (or a path smuggled in as an id) → error 70, no file read (Task 3 `test_an_unknown_id_reads_no_file`).
- A player seeking → `Range` answered with 206 and the right bytes (Task 3 `test_stream_answers_a_range`).
- Two tracks with the same title on different albums, or an album without tags → distinct ids, album still listed (Task 2 `test_ids_are_stable_and_distinct`).
- The port is taken by another program → the next one is used and the page shows it (Task 4 `test_a_taken_port_moves_to_the_next`).

---

### Task 1: The envelope and signing in

**Files:** Create `sonicache/subsonic.py`, `tests/test_subsonic.py`

**Interfaces — Produces:** `Response(status: int, content_type: str, body: bytes | None = None, path: Path | None = None, stream: Iterable[bytes] | None = None, headers: dict)`; `Api(source, password: str, user: str = "sonicache")` with `handle(method: str, path: str, params: dict[str, str], headers: dict[str, str]) -> Response`; `render(payload: dict, fmt: str) -> tuple[bytes, str]` (XML or JSON); `check_credentials(params, user, password) -> int | None` (error code or None).

- [ ] Tests: `test_ping_answers_ok_in_xml_and_json`, `test_plain_hex_and_token_passwords_sign_in`, `test_wrong_or_missing_credentials_get_nothing`, `test_view_suffix_and_post_form_are_the_same_call`, `test_an_unknown_method_is_not_supported`, `test_license_user_folders_and_extensions`.
- [ ] RED → implement → GREEN → commit.

### Task 2: The index of the library

**Files:** Modify `sonicache/subsonic.py`; Test `tests/test_subsonic.py`

**Interfaces — Produces:** `Index.build(items: list[dict], tracks: list[dict], playlists: list[dict], favourites: list[str], plays: dict[str, int]) -> Index` with `artists`, `albums`, `songs`, `playlists` dicts by id and helpers used by the methods. The `Source` protocol (spec table) as a dataclass of callables `Source(library, playlists, favourites, set_favourite, cover, listened, ffmpeg, plays)`.

- [ ] Tests on a synthetic library (two artists, three albums, a single, a playlist): `test_ids_are_stable_and_distinct`, `test_artists_and_indexes_group_by_first_letter`, `test_get_artist_and_album_list_their_children`, `test_album_lists_by_type`, `test_search_finds_artists_albums_and_songs`, `test_genres_and_songs_by_genre`, `test_playlists_and_starred`, `test_music_directory_walks_artist_album_song`, `test_random_songs_and_song`.
- [ ] RED → implement the methods → GREEN → commit.

### Task 3: Files: stream, download, covers, stars and listens

**Files:** Modify `sonicache/subsonic.py`; Test `tests/test_subsonic.py`

- [ ] Tests: `test_stream_sends_the_file`, `test_stream_answers_a_range`, `test_stream_as_mp3_is_converted` (skip without ffmpeg), `test_an_unknown_id_reads_no_file`, `test_cover_art_of_an_album_and_of_a_song`, `test_star_and_unstar_change_the_favourites`, `test_scrobble_counts_a_listen`, `test_start_scan_builds_the_index_again`.
- [ ] RED → implement → GREEN → commit.

### Task 4: The listener and the window's side

**Files:** Modify `sonicache/subsonic.py`, `sonicache/gui.py`, `sonicache/i18n.py`; Test `tests/test_subsonic.py`, `tests/test_gui.py`

**Interfaces — Produces:** `Listener(api, port) .start() -> int (port) / .stop()`; `local_addresses() -> list[str]`; gui `Api.wifi() -> dict`, `Api.set_wifi(on: bool) -> dict`, `Api.new_wifi_password() -> dict`, `WIFI_FILE = "wifi.json"`; server started in `Api.init` when `on`.

- [ ] Tests: `test_a_real_request_reaches_the_api` (Listener on a free port, urllib `ping` with credentials), `test_a_taken_port_moves_to_the_next`, gui: `test_wifi_is_off_until_switched_on`, `test_switching_wifi_on_starts_the_server_and_keeps_it`, `test_a_new_password_replaces_the_old`.
- [ ] RED → implement → GREEN (whole suite) → commit.

### Task 5: The window and the README

**Files:** Modify `sonicache/web/index.html`, `sonicache/web/app.js`, `sonicache/web/style.css`, `sonicache/web/i18n.js`, `README.md`

- [ ] A fixed row "Телефон по Wi-Fi" (`data-wifi`) under Devices; the page `#wifi-page` in `#library-page` (kind `"wifi"`): switch row, address/login/password rows with copy buttons, "Новый пароль", how to connect for iPhone and Android, notes on the network, the running program and the firewall; error line.
- [ ] Verify in the stand (off, on with addresses, copy, new password, error), both themes, English; `node --check`.
- [ ] README: a "Phones over Wi-Fi" paragraph under Players and phones, the module in the table. Commit.
