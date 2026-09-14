# Trackhound

[По-русски](README.md)

Downloads albums, singles, playlists and single tracks by link from Spotify, Apple Music, YouTube, SoundCloud, Last.fm and sites such as Bandcamp.

Titles, artists, track numbers, the year and the cover come from the page behind the link — no accounts, no API keys. When the service streams the audio openly (YouTube, SoundCloud, Bandcamp), the track is taken from there. When it does not (Spotify, Apple Music, Last.fm), the same track is looked for on YouTube Music, SoundCloud and among YouTube videos. yt-dlp does the downloading, after which the files get their tags and cover art.

## Installing

1. Download `Trackhound-vX.Y.Z-windows-x64.zip` from the [Releases](https://github.com/mojave333/trackhound/releases/latest) page.
2. Unpack it anywhere — `%LOCALAPPDATA%\Trackhound`, for example.
3. Run `Trackhound.exe`.

Nothing else has to be installed: Python, ffmpeg and Deno are inside the archive. Windows 10 or 11, 64-bit; the unpacked folder takes about 320 MB because of the bundled ffmpeg and Deno. On macOS and Linux the program runs from source — see [macOS and Linux](#macos-and-linux).

Two notes about the first run:

- **SmartScreen.** The build is not signed with a certificate, so Windows may show its blue "Windows protected your PC" window. Click "More info" → "Run anyway". Only a certificate from an authority removes that window — a self-signed one does nothing for SmartScreen — and a new signature earns its reputation through downloads. The cheapest option today is Azure Trusted Signing, about $10 a month plus verification of an organisation; a plain OV certificate starts at $200 a year, EV costs more but starts with a reputation.
- **WebView2.** The window is drawn by the Microsoft Edge WebView2 component. Windows 11 always has it and Windows 10 usually does; if it is missing, the program offers to download and install it from Microsoft.

`Trackhound-cli.exe --check` prints the version and the paths to the ffmpeg and Deno it found.

## Using it

The sections are on the left: Download, Library, Queue and Settings (Ctrl+1…4). The panel is dragged by its right edge: the wider it is, the sooner the labels appear; narrow, it goes back to icons alone, and a double click on the edge switches between the two. The width is remembered.

**Download.** Paste a link with the button, with Ctrl+V, or drag it into the window — several at once is fine. Without a link, write a name instead: "Artist - Album" or "Artist - Track", and the release is looked up on YouTube Music or Apple Music. The format and the folder sit next to the field; the arrow beside "Download" switches to "only check what would be found", which downloads nothing. Each link becomes a row with its cover and overall progress; clicking it opens the tracks: searching, downloading (with percentages), done, already in the folder, not found or failed, and where the audio came from. Links can be added while a download runs — they queue up. "Pause" holds the queue between tracks: what is downloading finishes, nothing new starts. "Stop" interrupts, and the retry button on a row picks up what is left. Closing the window loses nothing: unfinished links and the list of what was downloaded come back on the next run. The status bar at the bottom counts the tracks and estimates what is left.

**Library** — what is already in the folder: albums and single tracks, with a search and sortable columns. A double click opens the folder in the file manager; rows are selected the way they are in a file manager (click, Ctrl, Shift, Ctrl+A, arrows, Home and End), the right button opens a menu of actions, and the selection goes to the trash, from where it can be restored. Albums downloaded by this program have a "Download again" button: the link is remembered in a `.trackhound.json` file inside the album's folder, so missing tracks are filled in without hunting for the link again.

**Queue** — every track of the current downloads in one list, filtered by running, done or problems.

**Settings** — theme (as in the system, light or dark), language (as in the system, Russian or English), how many tracks to download at once, a speed limit, a proxy, cookies from a browser, and a check that every component is in place. The version of yt-dlp inside the build is there too, together with the path to the log and a "Copy the report" button: the report holds the version, the component paths, the settings and the end of the log — what belongs in a bug report. A line about a new version appears there when one is published: the program asks GitHub once per run and shows a link, downloading nothing by itself. Settings live in `%USERPROFILE%\.trackhound.json`.

Age-restricted tracks are only served by YouTube to a signed-in account. Usually the program simply takes such a song from another source, but if there is no other, choose in the settings the browser you are signed into YouTube with. Close that browser first — it holds its cookie file open; Firefox's cookies are read the most reliably.

## Command line

`Trackhound-cli.exe` is the same program for a terminal: it prints the progress and opens no window.

```bat
Trackhound-cli.exe https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc
Trackhound-cli.exe "Daft Punk - Discovery"
Trackhound-cli.exe LINK1 LINK2 -f mp3 -o D:\Music -t 4
Trackhound-cli.exe --dry-run LINK
```

| Option | Meaning |
|---|---|
| `-o`, `--output` | folder (default `%USERPROFILE%\Music\Trackhound`) |
| `-f`, `--format` | `m4a` (default), `mp3`, `opus` |
| `-t`, `--threads` | how many tracks to download at once, default 3 |
| `--dry-run` | only show what was matched |
| `--cookies-from-browser` | take cookies from a browser (`chrome`, `edge`, `firefox`…) — for age-restricted tracks |
| `--limit-rate` | limit the speed: `500K`, `2M` |
| `--proxy` | proxy for metadata and downloads: `http://127.0.0.1:1080`, `socks5://…` |
| `--names` | file names: `auto` (default), `artist`, `title` |
| `--folders` | album folders: `flat` (default), `nested`, `album` |
| `--lang` | language of the messages: `system` (default), `ru`, `en` |
| `--check` | version, paths to ffmpeg and Deno, the music folder |

## Links

| Service | Which links | Where the audio comes from |
|---|---|---|
| Spotify | album, single, track, playlist, `spotify:…`, `spotify.link/…` | searched on YouTube Music and SoundCloud |
| Apple Music | album, song, playlist | searched |
| YouTube, YouTube Music | video, album, playlist | from the link |
| SoundCloud | track, set | from the link; DRM-protected tracks are searched elsewhere |
| Last.fm | album, track | the album is found on YouTube Music or Apple Music, the audio comes from there or from a search |
| Bandcamp and other sites yt-dlp understands | album, track | from the link |

Spotify and Apple Music playlists are read off the page, and the page does not hand over the whole list: Spotify gives the first 100 tracks, Apple Music the first 50. When a playlist holds more, the program says so on the release line, and the rest have to be added separately. Artist pages and VK are not supported: VK shows music only to a signed-in account.

## Where the files go

```
Trackhound\
  Daft Punk - Discovery (2001)\
    01. One More Time.m4a
    02. Aerodynamic.m4a
    cover.jpg
    .trackhound.json
  Rick Astley - Never Gonna Give You Up.m4a     (a link to a single track)
```

Multi-disc albums are numbered `1-01`, `2-01`. A track credited to someone besides the album artist keeps those names in its file name. The settings change both layouts: file names as "01. Title" (guests named separately), "01. Artist - Title" or "01. Title"; album folders as "Artist - Album (Year)", "Artist → Album (Year)" (nested) or "Album (Year)". On the command line: `--names` and `--folders`.

Files that are already there are skipped, so an interrupted download can simply be started again.

## Formats and quality

- **m4a** — AAC as it is, no re-encoding. Almost every player takes it.
- **opus** — Opus as it is: a little better at the same size, but not every player understands it.
- **mp3** — re-encoded (VBR V0) for older devices. That does not improve the source.

YouTube hands over roughly 130–160 kbit/s, SoundCloud AAC 160 kbit/s. There is no lossless to be had.

## How a track is chosen

The search runs on "artist + title" in this order:

1. official audio on YouTube Music;
2. SoundCloud — smaller artists often skip YouTube Music;
3. ordinary YouTube videos.

Once a confident match is found, the remaining sources are not asked. Candidates are scored on title (transliterated Cyrillic included), artists, length and album. Live, remix, cover, demo and similar versions are dropped unless those words appear in the title on the source service. When the match refuses to download — an age-gated video, say — the next candidate is tried, widening the search to the other sources if needed. With no suitable candidate the track is reported as failed: nothing random is downloaded in its place. A file that turns out too short (a 30-second SoundCloud Go+ preview, for example) counts as a failure too.

## When something breaks

- **Download errors from YouTube.** YouTube changes often, and yt-dlp with it. Update to the newest Trackhound release; running from source, run `install.bat` again. The Settings section shows how old the bundled yt-dlp is.
- **"Spotify changed the shape of its page".** The parsing in `trackhound/spotify.py` needs fixing.
- **`music.youtube.com` is unreachable on this network.** The program switches to `www.youtube.com` by itself.
- **A proxy is needed.** Write the address in Settings → Proxy, or start with `--proxy http://127.0.0.1:1080`. `HTTP_PROXY`/`HTTPS_PROXY` work too.
- **Anything else.** Settings → Diagnostics → "Copy the report", and attach that to the issue.

## macOS and Linux

There are no ready-made builds — they are made on Windows — but the program runs from source: the window is drawn by WebKit (macOS) or GTK (Linux) through pywebview, deleting from the library goes to the trash (Finder, `gio trash`, or `~/.local/share/Trash` by the freedesktop.org specification), "Show in the file manager" opens Finder or the desktop's file manager, and the theme follows the system. ffmpeg and Deno have to be installed separately:

```sh
brew install ffmpeg deno            # macOS
sudo apt install ffmpeg && curl -fsSL https://deno.land/install.sh | sh   # Debian/Ubuntu
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py            # the window
.venv/bin/python main.py LINK       # the command line
```

Settings live in `~/.trackhound.json`; the log and the history in `~/Library/Application Support/Trackhound` (macOS) or `$XDG_DATA_HOME/trackhound` (Linux).

## Running from source

Windows and Python 3.10+. ffmpeg and Deno are not included: put `ffmpeg.exe` and `deno.exe` into a `vendor\` folder next to the project (`powershell -ExecutionPolicy Bypass -File scripts\fetch-vendor.ps1` downloads them) or install them system-wide:

```bat
winget install Gyan.FFmpeg
winget install DenoLand.Deno
```

Then:

1. `install.bat` — creates `.venv` and installs the dependencies.
2. `run.bat` — opens the program's window.

From source, the command line is `.venv\Scripts\python.exe main.py LINK`.

## Tests

```bat
.venv\Scripts\python.exe -m pytest
```

The tests are offline: the Spotify, Apple Music and Last.fm pages come from `tests/fixtures` and no request leaves the machine. They cover what rots by itself — the parsing of other people's markup, the match scoring on YouTube Music and SoundCloud, file names and the settings. [`ci.yml`](.github/workflows/ci.yml) runs the same on every push, on Windows, Linux and macOS.

## Building the exe

```bat
pip install -r requirements.txt -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File scripts\fetch-vendor.ps1
pyinstaller --noconfirm trackhound.spec
```

The finished folder is `dist\Trackhound`, holding two exes (`Trackhound.exe` and `Trackhound-cli.exe`) built from the same code: the first has no console, the second has one. Everything else lives in `_internal`, including `bin\ffmpeg.exe` and `bin\deno.exe` from `vendor\`; the program looks there first and only then in `PATH`.

Building without `vendor\` works too and produces a lighter variant that takes ffmpeg and Deno from the system.

Releases build themselves: a `v1.2.3` tag starts [`release.yml`](.github/workflows/release.yml), which checks that the tag matches `__version__` in `trackhound/__init__.py`, builds the archive and creates a draft release.

## How it is put together

- `trackhound/sources.py` — parsing the links of every service and finding a release by name
- `trackhound/spotify.py` — Spotify metadata
- `trackhound/models.py`, `trackhound/net.py` — shared data structures and HTTP
- `trackhound/matcher.py` — searching and picking a match on YouTube Music, SoundCloud and YouTube
- `trackhound/downloader.py` — downloading (yt-dlp), converting (ffmpeg), tagging (mutagen)
- `trackhound/i18n.py`, `trackhound/web/i18n.js` — the Russian and English text
- `trackhound/logs.py` — the log file and the report
- `trackhound/gui.py` — the window (pywebview) and the bridge between the interface and the downloader
- `trackhound/web/` — the interface: `index.html`, `style.css`, `app.js`, `i18n.js`, the `icon.ico`
- `trackhound/cli.py`, `main.py` — the command line and the entry point
- `tests/` — offline tests of the link parsing, the matching and the file names
- `trackhound.spec`, `scripts/fetch-vendor.ps1` — building the exe

## Licence

[GPL-2.0-or-later](LICENSE). The program imports mutagen (GPL-2.0-or-later), and that is linking rather than mere aggregation, so the copyleft covers the whole project.

The other dependencies: yt-dlp and yt-dlp-ejs — Unlicense, ytmusicapi — MIT, pywebview — BSD-3-Clause.

The release archive carries two separate programs, each under its own licence: `ffmpeg.exe` from the [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) builds (GPL, sources at [ffmpeg.org](https://ffmpeg.org/download.html)) and `deno.exe` from [denoland/deno](https://github.com/denoland/deno) (MIT). Trackhound runs both as external processes.

## Important

Use the program for music you have the rights to, and respect copyright law and the terms of service of YouTube and Spotify.
