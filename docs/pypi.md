# sonicache

Your personal music archive.

Sonicache reads a link or a name from a music service for what the release is (the titles,
the artists, the year, the cover), finds the audio in open sources, and saves it tagged, with
the cover, the genre and the lyrics. A name works too: `"Daft Punk - Discovery"`. It runs on
your own machine, with no accounts and no API keys.

The service's page only says what the release is. When a service streams its audio openly
(YouTube, SoundCloud, Bandcamp), the track is taken from there; otherwise the same track is
looked for on YouTube Music, SoundCloud and among YouTube videos. yt-dlp fetches it, and
mutagen writes the tags and the cover art.

This package is the engine and the command line. Windows users who want the program with its
window and everything bundled should take the installer from
[Releases](https://github.com/mojave333/sonicache/releases) instead.

## Installing

```sh
pip install sonicache          # the engine and the `sonicache` command
pip install "sonicache[gui]"   # adds the window, started with `sonicache-gui`
```

ffmpeg and a JavaScript runtime (Deno, or Node.js 22+) have to be on `PATH`. yt-dlp needs the
runtime to get audio from YouTube, and ffmpeg is needed for mp3, opus and ReplayGain.
`sonicache --check` says what it found.

## Command line

```sh
sonicache https://www.deezer.com/album/302127
sonicache "Daft Punk - Discovery" -f mp3 -o ~/Music
sonicache --dry-run https://www.deezer.com/playlist/908622995
sonicache --help
```

## As a library

```python
from pathlib import Path
from sonicache.engine import Downloader, Options, resolve

release = resolve("https://www.deezer.com/album/302127")
print(release.album.name, [track.title for track in release.tracks])

downloader = Downloader(Options(output_dir=Path("Music"), audio_format="m4a"))
report = downloader.download_link("https://www.deezer.com/album/302127")
print(report.summary())
```

The names importable from `sonicache.engine` are the public interface and keep working across
minor versions; the modules behind it may change. Messages come in the system's language,
Russian or English; `set_language("en")` fixes it. The engine writes to the `sonicache` logger
and sets up no handlers of its own. Where a network keeps back a service's pages,
`use_relay(address)` reads them through a relay abroad; the one-file relay is
[in the repository](https://github.com/mojave333/sonicache/tree/main/relay).

### Errors

A message is for a person and may be reworded in any release. A program should look at the code
instead, which is the same in either language and across minor versions:

```python
from sonicache.engine import SourceError

try:
    release = resolve(link)
except SourceError as error:
    print(error.code, error.details)  # http_error {'service': 'Deezer', 'code': 503, 'url': ...}

for failure in report.failures:  # the tracks that did not arrive
    print(failure.track.title, failure.code, failure.message)
```

`DownloaderError` has the same `.code` and `.details`. `ERROR_CODES` holds the list:

| Code | Meaning |
| --- | --- |
| `unsupported_link` | the link leads to no music, or to a kind of page the service is not read from |
| `login_required` | the service shows its music only to a signed-in account |
| `not_found` | the service has nothing under this link or id, or not in this country |
| `not_in_album` | the link names a track its album does not have |
| `empty` | the release or playlist has no tracks that can be read, or it is private |
| `unreadable` | the page or the service's answer is not in the shape it is read in |
| `short_link_broken` | a short link could not be followed to the page behind it |
| `empty_query` | neither a link nor a name to search for was given |
| `no_title` | the link carries no track title to search for |
| `link_failed` | the link could not be opened |
| `http_error` | the service answered with an HTTP error |
| `offline` | the service could not be reached |
| `service_error` | the service answered with an error of its own |
| `unknown_format` | the audio format asked for is not one of FORMATS |
| `ffmpeg_missing` | the format asked for needs ffmpeg, which was not found |
| `no_source` | the track was found neither on YouTube Music nor on SoundCloud |
| `age_restricted` | the video is age-restricted: it takes a signed-in YouTube account |
| `cookies_locked` | the browser's cookies could not be copied while it is open |
| `cookies_unreadable` | the browser's cookies could not be decrypted |
| `no_cookies` | the browser holds no YouTube cookies |
| `preview_only` | only a preview of the track could be fetched |
| `wrong_file` | the transfer did not end in a file of the format asked for |
| `download_failed` | the transfer failed for any other reason |
| `unknown` | an error raised without a code |

## Licence

GPL-2.0-or-later. Use it for music you have the rights to, and respect copyright law and the
terms of service of the services involved.
