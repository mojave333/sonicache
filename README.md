<div align="center">

# Sonicache

Your personal music archive.

[![Build](https://img.shields.io/github/actions/workflow/status/mojave333/sonicache/ci.yml?label=build&color=BE3237)](https://github.com/mojave333/sonicache/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/badge/licence-GPL--2.0--or--later-BE3237)](LICENSE)
![Platforms](https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-BE3237)

</div>

Sonicache saves music to your own folder. Give it a link from a music service or just a name, like
"Daft Punk - Discovery", and the release arrives tagged, with its cover, genre and lyrics. It also keeps a
library of everything in your music folders and plays it. Everything runs on your computer, with no
accounts or API keys, and the interface is in Russian and English.

## What it does

- Reads links from Spotify, Apple Music, Deezer and Last.fm to learn what the release is, then finds the
  audio on YouTube Music, SoundCloud or YouTube. Links to YouTube, SoundCloud and Bandcamp are downloaded
  directly.
- Tags every file with its title, artists, album, track number, year and cover. Genres come from
  MusicBrainz and lyrics from LRCLIB; synced lyrics go into an `.lrc` beside the track.
- Shows every folder you add as one library, including music it did not download, and fills in the tags
  older albums lack.
- Plays the library with a queue, gapless albums, crossfade, a ten-band equalizer, ReplayGain and synced lyrics on
  screen.
- Searches the Deezer catalogue, lists new releases from the artists you have and marks the albums you own.
- Checks watched playlists and artists every 12 hours and downloads only what is new.
- Syncs to a player, an SD card, an Android phone over a cable or a classic iPod. A phone can also play the
  library over Wi-Fi in a Subsonic app such as Amperfy or Ultrasonic.
- Sends your listens to Last.fm or ListenBrainz, and can show the current track on Discord.

## Install

> [!NOTE]
> The Releases page has no builds at the moment. Until it does, run Sonicache [from source](#from-source).

### Windows

Download `Sonicache-vX.Y.Z-windows-x64-setup.exe` from
[Releases](https://github.com/mojave333/sonicache/releases) and run it. It needs no administrator rights and
can put `Sonicache-cli` on `PATH`. The `.zip` from the same page runs without installing, and Scoop works
too:

```powershell
scoop bucket add sonicache https://github.com/mojave333/sonicache
scoop install sonicache/sonicache
```

Python, ffmpeg and Deno are bundled, about 320 MB in all. The program updates itself after checking the
installer's SHA-256; under Scoop, run `scoop update sonicache` instead.

The builds are unsigned, so SmartScreen may say "Windows protected your PC": click "More info", then "Run
anyway". Windows Defender sometimes wrongly flags `Sonicache.exe` as `Trojan:Win32/Sabsik.EN.D!ml`. The
`!ml` means a machine-learning model guessed, and it guesses the same about most PyInstaller builds. Open
Windows Security → Protection history, choose "Allow on device" and unpack the archive again.

The window needs Microsoft Edge WebView2. Windows 11 has it; on Windows 10 the program offers to install it
if it is missing.

### macOS

Open the `.dmg` for your Mac (`macos-arm64` for Apple Silicon, `macos-x64` for Intel) and drag Sonicache to
Applications. Apple has not signed the app, so the first launch is blocked. Allow it in System Settings →
Privacy & Security → "Open Anyway", or run:

```sh
xattr -dr com.apple.quarantine /Applications/Sonicache.app
```

Homebrew picks the right build and adds a `sonicache` command (the first launch still needs the step
above):

```sh
brew tap mojave333/sonicache https://github.com/mojave333/sonicache
brew install --cask mojave333/sonicache/sonicache
```

### Linux

Unpack `Sonicache-vX.Y.Z-linux-x64.tar.xz` and run `./Sonicache/Sonicache`. It needs glibc 2.35 or newer
(Ubuntu 22.04, Debian 12, Fedora 36 and later). If the window does not open, start it from a terminal to see
which library is missing.

The macOS and Linux builds do not update themselves. The settings tell you when a new version is out.

## Command line

`Sonicache-cli` is the same program without the window.

```bat
Sonicache-cli.exe https://www.deezer.com/album/302127
Sonicache-cli.exe "Daft Punk - Discovery" -f m4a -o D:\Music
Sonicache-cli.exe --dry-run LINK
```

| Option | Meaning |
|---|---|
| `-o`, `--output` | folder (default `%USERPROFILE%\Music\Sonicache`) |
| `-f`, `--format` | `mp3` (default), `m4a`, `opus` |
| `-t`, `--threads` | how many tracks at once, default 3 |
| `--dry-run` | only show what was matched |
| `--cookies-from-browser` | take cookies from a browser (`chrome`, `edge`, `firefox`…) for age-restricted tracks |
| `--limit-rate` | limit the speed: `500K`, `2M` |
| `--proxy` | proxy for metadata and transfers: `http://127.0.0.1:1080`, `socks5://…` |
| `--names` | file names: `auto` (default), `artist`, `title` |
| `--folders` | album folders: `flat` (default), `nested`, `album` |
| `--replaygain` | measure the loudness and write ReplayGain tags; the audio is not changed |
| `--lyrics` | write lyrics from LRCLIB into the tags, and synced ones into an `.lrc` beside the track |
| `--from-file` | take links and names from a `.txt` or a playlist exported as `.csv`; can be repeated |
| `--lang` | language of the messages: `system` (default), `ru`, `en` |
| `--check` | version, paths to ffmpeg and Deno, the music folder |

## Where the music comes from

Each track is looked up first by its ISRC, the code every release of a recording carries, so YouTube Music
returns that exact recording. Without a code, Sonicache searches YouTube Music, then SoundCloud, then ordinary
YouTube videos, and skips live versions, remixes and covers unless the title asks for one. A track with no
good match is reported as failed, and nothing random is saved in its place.

| Format | What you get |
|---|---|
| `mp3` (default) | 320 kbit/s, plays anywhere |
| `m4a` | AAC at 256 kbit/s |
| `opus` | YouTube's Opus stream, not re-encoded |

None of the free sources is lossless, so no format can sound better than the stream it came from.

Playlists from Spotify, Apple Music and Deezer come whole, up to 1000 tracks. A Spotify playlist read through
the relay stops at its first 100.

## Where the files go

```
Sonicache\
  Daft Punk - Discovery (2001)\
    01. One More Time.mp3
    02. Aerodynamic.mp3
    cover.jpg
```

Files already on disk are skipped, so an interrupted run can simply be started again. The settings, or
`--names` and `--folders`, change how files and folders are named.

## If a service is blocked

Sonicache connects from your own computer, so it sees what your network lets through. Settings → Network →
"Check" shows what opens and finds the local proxy of a VPN client such as v2rayN, Clash or Hiddify. Any
other proxy goes into Settings → Network → Proxy, or `--proxy`. For Spotify there is also a one-file relay
on Cloudflare Workers, described in [relay/README.md](relay/README.md).

## Reporting a problem

Settings → If something does not work → "Copy the report", then attach it to an
[issue](https://github.com/mojave333/sonicache/issues). Errors from YouTube usually go away in the newest
version, which carries a fresh yt-dlp.

## From source

You need Python 3.10 or newer, ffmpeg and Deno. On Windows:

```bat
winget install Gyan.FFmpeg
winget install DenoLand.Deno
install.bat
run.bat
```

`install.bat` creates `.venv` with the dependencies and `run.bat` opens the window. The command line is
`.venv\Scripts\python.exe main.py LINK`. Instead of winget, `scripts\fetch-vendor.ps1` puts `ffmpeg.exe` and
`deno.exe` into `vendor\`.

On macOS and Linux:

```sh
brew install ffmpeg deno            # macOS
sudo apt install ffmpeg && curl -fsSL https://deno.land/install.sh | sh   # Debian and Ubuntu
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py            # the window
.venv/bin/python main.py LINK       # the command line
```

The tests run offline, on service pages saved in `tests/fixtures`:

```bat
.venv\Scripts\python.exe -m pytest
```

`python scripts/preview.py` serves the window's page at http://127.0.0.1:8765 with a stub in place of the
program, for checking the interface in a browser.

## Building

```bat
pip install -r requirements.txt -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File scripts\fetch-vendor.ps1
pyinstaller --noconfirm sonicache.spec
powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
```

This makes `dist\Sonicache` with `Sonicache.exe` and `Sonicache-cli.exe`. The last line wraps it into an
installer and needs [Inno Setup](https://jrsoftware.org/isdl.php) 6.3 or newer. On macOS and Linux,
`scripts/fetch-vendor.sh` and then `scripts/build-unix.sh v1.2.3` make a `.dmg` or a `.tar.xz`.

Releases are made on GitHub: Actions → Release → Run workflow, then type the version.
[`release.yml`](.github/workflows/release.yml) tags the commit, builds for every system and publishes the
files with their SHA-256.

The engine that turns a link into tagged files is `sonicache/engine/`, and it knows nothing about the window.
The window is `sonicache/gui.py` and `sonicache/web/`.

## Licence

[GPL-2.0-or-later](LICENSE), because the program links mutagen, which is under the same licence. yt-dlp is
under the Unlicense, ytmusicapi under MIT and pywebview under BSD-3-Clause. The heading fonts, Onest and
Source Serif 4, are under the SIL Open Font License, and the [Phosphor](https://phosphoricons.com) icons under
MIT.

The release builds also carry ffmpeg (GPL, sources at [ffmpeg.org](https://ffmpeg.org/download.html)) and
Deno (MIT) as separate programs. ffmpeg comes from [BtbN's builds](https://github.com/BtbN/FFmpeg-Builds) on
Windows and Linux and from [Martin Riedl's](https://ffmpeg.martin-riedl.de/) on macOS. The Linux build also
contains Qt (LGPL-3.0) and PyQt6 (GPL-3.0), so that build as a whole is GPL-3.0.

Use Sonicache for music you have the rights to, and respect copyright law and the terms of the services
involved.
