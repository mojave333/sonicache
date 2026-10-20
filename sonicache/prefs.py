"""What the settings may hold, and the file they are kept in, ~/.sonicache.json.

_normalize() is the gate: whatever the file or the page says, what comes out is something the
program can use, a default in place of anything it does not know.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import logs
from .engine.downloader import DEFAULT_OUTPUT_DIR, FOLDER_NAMES, FORMATS, TRACK_NAMES
from .i18n import resolve

SETTINGS_FILE = Path.home() / ".sonicache.json"

# The settings as the program wrote them under its former name, before 3.0:
# the first start under the new name takes them over (_adopt_former_settings)
FORMER_SETTINGS_FILE = Path.home() / ".trackhound.json"

THEMES = ("system", "light", "dark")

# The faces the names can be set in (web/style.css); the first is a new installation's
FONTS = ("onest", "source-serif", "comic")

# What an empty screen shows: no picture, a crayon drawing or ASCII art; the first is a new installation's
ARTS = ("none", "crayon", "ascii")

# Browsers yt-dlp can read cookies from; "" means it takes none
COOKIE_BROWSERS = ("", "chrome", "edge", "firefox", "brave", "chromium", "opera", "vivaldi")

# Speeds offered in the settings, bytes per second; 0 is no limit
RATE_LIMITS = (0, 512_000, 1_048_576, 2_097_152, 5_242_880, 10_485_760)

_PROXY_RE = re.compile(r"^(?:https?|socks4|socks5h?)://[^\s/]+$", re.I)

# A Spotify relay's address, or "off"; empty is the program's own (SPOTIFY_RELAY)
_RELAY_RE = re.compile(r"^https?://[^\s?#]+(?:\?\S*)?$", re.I)

# What a profile remembers: where the music goes and in what shape. The rest of
# the settings — theme, language, proxy — are about the program, not the music,
# and stay the same whichever profile is picked.
PROFILE_KEYS = ("folder", "format", "track_name", "folder_name")

PROFILE_LIMIT = 12

LIBRARY_VIEWS = ("grid", "list")

# What the player's repeat button steps through: the queue, one track, neither
REPEAT_MODES = ("off", "all", "one")

CROSSFADE_MAX = 12

EQ_BANDS, EQ_LIMIT = 10, 12  # 31 Hz to 16 kHz an octave apart, each up or down 12 dB

def _normalize(settings: dict) -> dict:
    try:
        threads = min(8, max(1, int(settings.get("threads", 3))))
    except (TypeError, ValueError):
        threads = 3
    try:  # the player's volume, from silent to as loud as the file is
        volume = min(1.0, max(0.0, float(settings.get("volume", 0.8))))
    except (TypeError, ValueError):
        volume = 0.8
    try:  # seconds one track fades into the next; 0 for none
        crossfade = min(CROSSFADE_MAX, max(0, int(settings.get("crossfade", 0))))
    except (TypeError, ValueError):
        crossfade = 0
    try:
        rate_limit = int(settings.get("rate_limit") or 0)
    except (TypeError, ValueError):
        rate_limit = 0
    if rate_limit not in RATE_LIMITS:
        rate_limit = 0
    proxy = str(settings.get("proxy") or "").strip()
    if not _PROXY_RE.match(proxy):
        proxy = ""
    # Empty is the program's own relay, "off" none at all
    relay = str(settings.get("relay") or "").strip()
    if relay != "off" and not _RELAY_RE.match(relay):
        relay = ""
    return {
        "folder": str(settings.get("folder") or DEFAULT_OUTPUT_DIR),
        "format": settings.get("format") if settings.get("format") in FORMATS else "mp3",
        "threads": threads,
        # A new installation opens on paper; the dark theme and the system's are a choice
        "theme": settings.get("theme") if settings.get("theme") in THEMES else "light",
        "font": settings.get("font") if settings.get("font") in FONTS else FONTS[0],
        "art": settings.get("art") if settings.get("art") in ARTS else ARTS[0],
        "dry_run": bool(settings.get("dry_run", False)),
        "cookies_browser": (settings.get("cookies_browser")
                            if settings.get("cookies_browser") in COOKIE_BROWSERS else ""),
        "rate_limit": rate_limit,
        "proxy": proxy,
        "relay": relay,
        # The window offers Russian and English and nothing else, so the
        # language is settled here: "system" from an older settings file, or a
        # first run with nothing saved, becomes whichever of the two the
        # computer asks for.
        "language": (settings.get("language")
                     if settings.get("language") in ("ru", "en") else resolve("system")),
        "track_name": (settings.get("track_name")
                       if settings.get("track_name") in TRACK_NAMES else "auto"),
        "folder_name": (settings.get("folder_name")
                        if settings.get("folder_name") in FOLDER_NAMES else "flat"),
        "replaygain": bool(settings.get("replaygain", False)),
        # Lyrics from LRCLIB into the tags, synced ones into an .lrc beside the track
        "lyrics": bool(settings.get("lyrics", True)),
        # Closing the window leaves the program in the tray while it has work (Windows)
        "tray": bool(settings.get("tray", True)),
        # A notice when downloads finish and the window is not in use
        "notify": bool(settings.get("notify", True)),
        "profiles": _profiles(settings.get("profiles")),
        # Folders besides the music folder that the library shows, left where they are
        "library_folders": _library_folders(settings.get("library_folders"),
                                            str(settings.get("folder") or DEFAULT_OUTPUT_DIR)),
        "library_view": settings.get("library_view") if settings.get("library_view") in LIBRARY_VIEWS else "grid",
        # How the player was left. These used to live in the window's own
        # storage, which belongs to the address the page was opened from — and
        # that is a new port on every start, so they were lost each time.
        "volume": round(volume, 3),
        "shuffle": bool(settings.get("shuffle", False)),
        "repeat": settings.get("repeat") if settings.get("repeat") in REPEAT_MODES else "off",
        "crossfade": crossfade,
        # The next track of an album starts the moment the last one ends
        "gapless": bool(settings.get("gapless", True)),
        "eq": _equalizer(settings.get("eq")),
        # What plays, shown on the person's Discord profile
        "discord": bool(settings.get("discord", False)),
    }

def _equalizer(value) -> dict:
    """The equaliser: whether it is on, and the gain of each band in decibels."""
    value = value if isinstance(value, dict) else {}
    bands = value.get("bands") if isinstance(value.get("bands"), list) else []
    gains = []
    for number in range(EQ_BANDS):
        try:
            gain = float(bands[number])
        except (IndexError, TypeError, ValueError):
            gain = 0.0
        gains.append(round(min(EQ_LIMIT, max(-EQ_LIMIT, gain)), 1))
    preset = str(value.get("preset") or "flat")
    return {"on": bool(value.get("on", False)), "bands": gains, "preset": preset[:32]}

def _library_folders(raw, folder: str) -> list[str]:
    result = []
    for item in raw if isinstance(raw, list) else []:
        text = str(item or "").strip()
        if text and text != folder and text not in result:
            result.append(text)
    return result[:20]

def _profiles(raw) -> list[dict]:
    """The saved folder-and-format sets, cleaned the way the settings are.

    A profile is only a name and the keys in PROFILE_KEYS, each checked against
    the same rules as the live setting, so a hand-edited file cannot put the
    window into a state its own controls could not reach.
    """
    profiles, seen = [], set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()[:40]
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        clean = _normalize(entry)
        profiles.append({"name": name, **{key: clean[key] for key in PROFILE_KEYS}})
        if len(profiles) >= PROFILE_LIMIT:
            break
    return profiles

def _load_settings() -> dict:
    return _normalize(logs.read_json(SETTINGS_FILE))

def _adopt_former_settings() -> None:
    """The settings of the program under its former name become this one's,
    on the first start that finds none of its own. A copy: an older version
    still installed beside this one keeps the file it reads."""
    if SETTINGS_FILE.exists() or not FORMER_SETTINGS_FILE.is_file():
        return
    try:
        shutil.copyfile(FORMER_SETTINGS_FILE, SETTINGS_FILE)
        logs.log.info("настройки прежней версии скопированы из %s", FORMER_SETTINGS_FILE)
    except OSError as e:
        logs.log.warning("не скопировал настройки из %s: %s", FORMER_SETTINGS_FILE, e)

def _save_settings(settings: dict) -> None:
    # "check only" is not remembered: forgetting it on would silently skip downloads
    logs.write_json(SETTINGS_FILE, {key: value for key, value in settings.items() if key != "dry_run"}, indent=2)
