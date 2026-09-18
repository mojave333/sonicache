"""Download albums, singles, playlists and tracks by link, tagged and with cover art.

The engine (trackhound.engine) turns a link into files; the window (gui) and
the command line (cli) are built on top of it.

Copyright (C) 2026 mojave333. Trackhound is free software under the terms of
the GNU General Public License, version 2 or (at your option) any later
version; see the LICENSE file. It comes with no warranty.
"""

# The single source of truth for the version: the release workflow refuses to
# build when the pushed tag says something else.
__version__ = "1.2.1"
