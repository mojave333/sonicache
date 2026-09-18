"""Download Spotify albums, singles and tracks by link.

Metadata (titles, track numbers, cover, release date) comes from public
Spotify pages; audio is matched on YouTube Music, SoundCloud or YouTube.

Copyright (C) 2026 mojave333. Trackhound is free software under the terms of
the GNU General Public License, version 2 or (at your option) any later
version; see the LICENSE file. It comes with no warranty.
"""

# The single source of truth for the version: the release workflow refuses to
# build when the pushed tag says something else.
__version__ = "1.2.1"
