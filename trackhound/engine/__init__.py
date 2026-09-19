"""The engine: a link or a name in, tagged music files out.

    from pathlib import Path
    from trackhound.engine import Downloader, Options, resolve

    release = resolve("https://www.deezer.com/album/302127")
    print(release.album.name, [track.title for track in release.tracks])

    report = Downloader(Options(output_dir=Path("Music"))).download_link(
        "https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc")
    print(report.summary())

What is importable from here is the public interface, and it keeps working
across minor versions. The modules behind it (trackhound.engine.sources,
.matcher and the rest) may change in any release.

The engine knows nothing about the window or the command line, and sets up no
logging of its own: it writes to the "trackhound" logger and reports progress
through the callbacks given to Downloader. Its messages speak Russian or
English, the system's language unless set_language() says otherwise.
ffmpeg and a JavaScript runtime (Deno, or Node.js 22+) are looked for on PATH.
"""

from . import batch
from .downloader import (DEFAULT_OUTPUT_DIR, FOLDER_NAMES, FORMATS, TRACK_NAMES, Downloader,
                         DownloaderError, Options, Report, extension, find_tool, use_proxy)
from .i18n import set_language
from .models import Album, Release, SourceError, Track
from .sources import resolve, search, search_track

__all__ = [
    "DEFAULT_OUTPUT_DIR", "FOLDER_NAMES", "FORMATS", "TRACK_NAMES",
    "Album", "Downloader", "DownloaderError", "Options", "Release", "Report", "SourceError", "Track",
    "batch", "extension", "find_tool", "resolve", "search", "search_track", "set_language", "use_proxy",
]
