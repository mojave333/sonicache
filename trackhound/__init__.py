"""Download albums, singles, playlists and tracks by link, tagged and with cover art.

The engine (trackhound.engine) turns a link into files; the window (gui) and
the command line (cli) are built on top of it.

Copyright (C) 2026 mojave333. Trackhound is free software under the terms of
the GNU General Public License, version 2 or (at your option) any later
version; see the LICENSE file. It comes with no warranty.
"""

# The single source of truth for the version: the release workflow refuses to
# build when the pushed tag says something else.
__version__ = "1.5.0"

# The relay Spotify's pages are read through where Spotify refuses them to the
# network, deployed from relay/worker.js and run in Frankfurt (see its README).
# The settings and --relay can put another in its place.
SPOTIFY_RELAY = "https://trackhound-relay.takmakov2006.workers.dev"


def relay_for(setting: str) -> str:
    """The relay a setting asks for: its own address, none for "off", else the program's."""
    setting = setting.strip()
    return "" if setting == "off" else setting or SPOTIFY_RELAY
