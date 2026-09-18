# Trackhound

Paste a link, get tagged music. Trackhound downloads albums, singles, playlists and
single tracks by link from Spotify, Apple Music, Deezer, YouTube, SoundCloud, Last.fm and
sites such as Bandcamp. A name works too: `"Daft Punk - Discovery"`.

Titles, artists, track numbers, the year and the cover are read from the page behind the
link, with no accounts or API keys. When the service streams the audio openly (YouTube,
SoundCloud, Bandcamp), the track is taken from there; otherwise the same track is looked
for on YouTube Music, SoundCloud and among YouTube videos. yt-dlp downloads it, and
mutagen writes the tags and the cover art.

This package is the engine and the command line. Windows users who want the program with
its window and everything bundled should take the installer from
[Releases](https://github.com/mojave333/trackhound/releases/latest) instead.

## Installing

```sh
pip install trackhound          # the engine and the `trackhound` command
pip install "trackhound[gui]"   # adds the window, started with `trackhound-gui`
```

ffmpeg and a JavaScript runtime (Deno, or Node.js 22+) have to be on `PATH`. yt-dlp needs
the runtime to get audio from YouTube, and ffmpeg is needed for mp3, opus and ReplayGain.
`trackhound --check` says what it found.

## Command line

```sh
trackhound https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc
trackhound "Daft Punk - Discovery" -f mp3 -o ~/Music
trackhound --dry-run https://www.deezer.com/playlist/908622995
trackhound --help
```

## As a library

```python
from pathlib import Path
from trackhound.engine import Downloader, Options, resolve

release = resolve("https://www.deezer.com/album/302127")
print(release.album.name, [track.title for track in release.tracks])

downloader = Downloader(Options(output_dir=Path("Music"), audio_format="m4a"))
report = downloader.download_link("https://www.deezer.com/album/302127")
print(report.summary())
```

The names importable from `trackhound.engine` are the public interface and keep working
across minor versions; the modules behind it may change. Messages come in the system's
language, Russian or English; `set_language("en")` fixes it. The engine writes to the
`trackhound` logger and sets up no handlers of its own.

## Licence

GPL-2.0-or-later. Use it for music you have the rights to, and respect copyright law and
the terms of service of the services involved.
