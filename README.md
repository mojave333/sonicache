<div align="center">

<img src="docs/logo.png" width="112" alt="">

# Trackhound

**Paste a link, get tagged music.**

Albums, singles, playlists and single tracks from Spotify, Apple Music, Deezer, YouTube,
SoundCloud, Last.fm and sites such as Bandcamp.

[![Latest release](https://img.shields.io/github/v/release/mojave333/trackhound?label=release&color=8FBF3F)](https://github.com/mojave333/trackhound/releases/latest)
[![Build](https://github.com/mojave333/trackhound/actions/workflows/ci.yml/badge.svg)](https://github.com/mojave333/trackhound/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/badge/licence-GPL--2.0--or--later-blue)](LICENSE)
![Windows, macOS, Linux](https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-informational)

### [⬇ Download for Windows, macOS or Linux](https://github.com/mojave333/trackhound/releases/latest)

<img src="docs/demo.gif" width="880" alt="An album name is typed in, the tracks download, and the finished album appears in the library">

<sub>A real run: a name typed instead of a link, ten tracks downloaded, the album on disk in the library, and the settings. Only the waiting is sped up.</sub>

</div>

---

Titles, artists, track numbers, the year and the cover are read from the page behind the
link. The genres come from [MusicBrainz](https://musicbrainz.org/), where listeners vote on
them much as on Rate Your Music: up to three of the most voted, such as "Alternative Rock;
Rock; Art Rock". An album nobody has voted on keeps the genre its service names (Deezer and
Apple Music do); for Spotify, YouTube and Last.fm it is looked up by name in Apple's catalogue
and then on Deezer. Playlists get no genre, since their songs come from many. When the service streams the audio openly (YouTube, SoundCloud, Bandcamp), the track
is taken from there. When it does not (Spotify, Apple Music, Deezer, Last.fm), the same
track is looked for on YouTube Music, SoundCloud and among YouTube videos. yt-dlp does the
downloading, after which the files get their tags and cover art.

- Python, ffmpeg and Deno come inside the installer, so there is nothing else to set up.
- No accounts or API keys; everything runs on your own machine.
- A name works when you have no link: write "Daft Punk - Discovery".
- Every file gets tags, the genre and cover art, and multi-disc albums are numbered properly.
- Optional ReplayGain tags make a shuffled library play at one volume.
- Lyrics from LRCLIB go into the tags, and synced ones into an `.lrc` file beside the track.
- A text file of links or a playlist exported as CSV can be queued in one go.
- Watched playlists are checked twice a day, and only the tracks added since are downloaded.
- A folder, a format and the naming rules can be saved as a profile and switched with one click.
- The program updates itself: it fetches the next installer, checks its hash against GitHub
  and runs it.
- The window and the terminal version are the same program.
- The interface is in Russian and English.

**Contents**
[Installing](#installing) ·
[Using it](#using-it) ·
[Command line](#command-line) ·
[Links it understands](#links-it-understands) ·
[Where the files go](#where-the-files-go) ·
[Formats](#formats-and-quality) ·
[How a track is chosen](#how-a-track-is-chosen) ·
[When something breaks](#when-something-breaks) ·
[macOS and Linux](#macos-and-linux) ·
[Python package](#python-package) ·
[From source](#running-from-source) ·
[Building](#building) ·
[Licence](#licence)

## Installing

1. Download `Trackhound-vX.Y.Z-windows-x64-setup.exe` from the
   [Releases](https://github.com/mojave333/trackhound/releases/latest) page.
2. Run it. The installer asks where to put the program, offers tick boxes for a desktop
   shortcut and for `Trackhound-cli` on the `PATH`, and finishes by offering to start it.

No administrator rights are needed: by default the program is installed for you alone, in
`%LOCALAPPDATA%\Programs\Trackhound`. An administrator is also offered the machine-wide
install. It uninstalls like any other program, through Settings → Apps, and asks on the way
out whether to delete the settings and the log file. Downloaded music is left alone either
way.

If you would rather not use an installer, the `Trackhound-vX.Y.Z-windows-x64.zip` archive
sits next to it: unpack it anywhere and run `Trackhound.exe`.

Nothing else has to be installed: Python, ffmpeg and Deno are already inside. Windows 10 or
11, 64-bit; the installed folder takes about 320 MB because of the bundled ffmpeg and Deno.
For macOS and Linux there are builds of their own (see [macOS and Linux](#macos-and-linux)).

`Trackhound-cli.exe --check` prints the version and the paths to the ffmpeg and Deno it
found.

### Three notes about the first run

> [!NOTE]
> **SmartScreen.** Neither the installer nor the program itself is signed with a
> certificate, so Windows may show its blue "Windows protected your PC" window. Click
> "More info" → "Run anyway". Only a certificate from an authority removes that window (a
> self-signed one does nothing for SmartScreen), and a new signature still has to earn its
> reputation through downloads. The cheapest option today is Azure Trusted Signing, about
> $10 a month plus verification of an organisation; a plain OV certificate starts at $200 a
> year, and EV costs more but starts with a reputation.

> [!WARNING]
> **Antivirus.** Windows Defender sometimes flags `Trackhound.exe` as
> `Trojan:Win32/Sabsik.EN.D!ml`. The `!ml` suffix means the file matched no known virus
> signature; a machine-learning model flagged it on circumstantial evidence. For this build
> the evidence is an unsigned exe that unpacks a bundled Python of several hundred files as
> it starts, and nearly everything built with PyInstaller gets caught the same way. It is a
> false positive, but while the file sits in quarantine the archive will not finish
> unpacking, and `Trackhound.exe` disappears from the folder right after extraction.
>
> To fix it, open Windows Security → Virus & threat protection → Protection history, find
> the Trackhound entry, expand it, choose "Allow on device", then unpack the archive again.
> If you would rather check the program than allow it, upload the archive to
> [VirusTotal](https://www.virustotal.com/), where the other engines stay quiet, and compare
> its SHA-256 with the one printed in the release notes using `Get-FileHash <file>`. The
> detection is worth reporting to Microsoft through their
> [file submission form](https://www.microsoft.com/en-us/wdsi/filesubmission); a report
> usually gets it cleared for everyone within a day or two. Signing the program, as in the
> note above, would stop it for good.

> [!NOTE]
> **WebView2.** The window is drawn by the Microsoft Edge WebView2 component. Windows 11
> always has it and Windows 10 usually does; if it is missing, the program offers to
> download and install it from Microsoft.

## Using it

The sections are on the left: Download, Search (Ctrl+K), Library, Queue and Settings
(Ctrl+1…4 for all but Search). A button at
the foot of the panel opens it into a labelled column and folds it back to icons alone;
Ctrl+B does the same. The program remembers which way it was left.

### Download

Paste a link with the button, with Ctrl+V, or drag it into the window; several at once is
fine. Without a link, write a name instead, "Artist - Album" or "Artist - Track", and the
release is looked up on YouTube Music or Apple Music. The format and the folder sit next to
the field. The arrow beside "Download" switches to "only check what would be found", which
downloads nothing.

Each link becomes a row with its cover and overall progress. Clicking it opens the tracks
and their state: searching (and searching everywhere, when the quick search found nothing),
downloading with percentages, trying another source when the first one refused, done, already in the folder, not found or failed, and where the audio came from. Links added while a download runs join the
queue. "Pause" holds the queue between tracks, so what is downloading finishes and nothing
new starts. "Stop" interrupts, and the retry button on a row picks up what is left. Closing
the window loses nothing: unfinished links and the list of what was downloaded come back on
the next run. The status bar at the bottom counts the tracks and estimates what is left; the window
title shows the percentage, and on Windows the taskbar button fills with it, yellow while
paused and red once something has failed.

The percentages count a track's whole way, not only its bytes. Most of the time goes where
nothing is downloaded: yt-dlp preparing YouTube's stream takes several seconds, and turning
it into mp3 takes longer than the download itself. So each step's remaining time is
estimated, from ffmpeg's own report during the conversion and from how long the step took on
the tracks before where nothing reports, and the bar moves at an even pace instead of
standing at 0, leaping to 99 and standing again. Each track and each link say roughly how
long they still need ("~20 sec", "~3 min"), worked out for the number of tracks downloaded at
once; the first estimates are rough and settle after a track or two.

### Search

For when there is no link at hand. Type an artist, an album or a track, and the catalogue
answers as the typing pauses: artists with their photos, albums as a grid of covers, and
tracks as a list with their album and length. The results come from Deezer, which needs no
account; where Deezer does not answer, YouTube Music is asked instead, and the corner says
which one is shown.

The arrow on a cover or a track puts it into the queue at once and turns into a tick; the
view stays, so more can be picked. A click on an album opens its page with the tracklist,
where the whole album or any one track can be queued. Whatever is chosen downloads exactly
as its link pasted by hand would, with the same matching, tags and folders.

A click on an artist opens their page: the photo, and every release newest first, split
into albums, singles and EPs, and compilations. "Download the discography" queues their
albums and EPs, oldest first; singles are left out, since they are mostly songs the albums
have as well, and each one can still be queued from its cover. "Watch for new releases"
adds the artist to Settings → Watching: from then on the checks every 12 hours download
whatever comes out, singles included, into the music folder. What the artist had when the
watch began is never downloaded by it, and neither is an old record a catalogue adds late.

A link to an artist pasted into the download field, or given on the command line, stands
for the same discography: each album and EP becomes a card of its own. Deezer and YouTube
Music list an artist's releases openly; Spotify and Apple Music do not, so their artist
links are followed to the artist of the same name on Deezer.

### Library

The library shows what is already in the music folder, on three tabs: Albums, Tracks and
Artists. Albums sitting in a folder per artist, which the "nested" naming makes, are found
too, and take their artist from the folder above them. Each has a search, a sort order and a direction. Albums come as a grid of covers
or, with the switch beside the sorting, as a table with sortable columns.

A click on a cover opens the album's page: the cover grows out of the grid into a large one
beside the title, the year, the size and the tracks, read from the files' own tags. The top of
the page takes the cover's main colour, a pastel in the light theme and a deep shade in the
dark one. For an
album this program downloaded, the tracks that never arrived are listed too, greyed out,
since the `.trackhound.json` file in the album's folder keeps the link and the whole
tracklist. From the page the album can be opened in the file manager, completed ("Fetch what
is missing" downloads the same link again and skips what is on disk) or watched for new
tracks. The same button sits on the album's row, in its right-click menu and above the list,
and everywhere it shows only while tracks are missing. Escape, Backspace or the mouse's back button returns to the grid, and the cover
flies back to its place.

Tracks lists every file with its artists, album and length; a double click opens its album
with the track picked out, and a click on its cover plays the list from there. Artists groups the albums and single tracks by the name in their
folder, each with a round photo that Deezer's public catalogue provides (looked up once per
name and remembered) or initials on a colour of their own when it has none. An artist's page
shows their albums.

In the grid and in the table, albums are selected the way files are in a file manager: Ctrl
and Shift with a click, Ctrl+A, the arrows, Home and End (a plain click on a cover opens it).
The right button opens a menu of actions, and the selection goes to the trash, from where it
can be restored.

### Listening

The library plays what it holds. "Play" on an album's page starts it from the top; a click
on a track's number, or a double click on its row, starts from that track, and the album is
the queue. A bar above the status bar then shows the track, with previous, pause and next,
a position to drag, and the volume. The keyboard's media keys and Windows' own media
controls work too. The file is streamed from the program itself, so nothing is copied.

The cover or the lines button in the bar opens Now playing: the cover large, on a blur of
its colours, and the words beside it. Synced lyrics, from the `.lrc` next to the file,
scroll with the song, the line being sung lit up; a click on a line jumps there. A file with
only plain lyrics in its tags shows those, and one with none has its lyrics looked up in
LRCLIB for the screen, without anything being written to it. Space pauses there, and Back
or the bar returns to where you were.

### Filling in tags

Albums downloaded by an older version, or put in the folder by another program, may lack a
genre, a year, a cover or lyrics. "Fill in tags" adds what is missing without downloading
anything again: on the album's page and in the right-click menu it works on that album or the
selection, and above the list, with nothing selected, on the whole library after a question.

Each album is looked up again: by the link in its `.trackhound.json` when this program
downloaded it, otherwise by the album and artist in its files' tags or, failing those, in its
folder's name. Each file is matched to a track of the release by its title and length, or by
its number and length when the title says nothing ("Track 01"). A release found by name is
taken only when most of the folder's files are its tracks, so a wrong guess changes nothing.

A file then gets only the tags it does not have: the title, artists, album, album artist,
number, disc, date, genre, cover and, when Settings → Download has lyrics on, the words from
LRCLIB with an `.lrc` for synced ones. Whatever it already says stays as it is, including
anything corrected by hand, ReplayGain tags and the ID3 version of an mp3. The folder gets a
`cover.jpg` if it has none. The status bar shows which album is in hand, and the button stops
the run after the current album. Albums that no catalogue knows are named at the end.

### Queue

Every track of the current downloads in one list, filtered by running, done or problems.

### Loudness levelling

Off by default; Settings → Download turns it on. Every file is measured with ffmpeg's EBU
R128 meter and gets ReplayGain 2.0 tags: track gain and peak, plus one album gain shared by
the tracks of an album, so a quiet interlude stays quiet beside a loud single. Opus gets the
`R128_*` tags its specification asks for instead. The audio itself is not changed. The
player turns each track up or down, so this helps only in a player that reads the tags,
such as foobar2000, MusicBee, AIMP, VLC or Poweramp.

A playlist gets no album gain, since its songs come from different records. A file that
already has the tags is never measured twice, so a watched playlist does not re-measure
itself on every check. Measuring takes a few seconds a track, on as many threads as the
downloads use.

### Lyrics

On by default; Settings → Download turns it off. Each track's lyrics are looked up in
[LRCLIB](https://lrclib.net), a free open database, by artist, title, album and length, so a
live take or a remix of another length does not lend its words. The plain text goes into the
tags (`USLT` in mp3, `©lyr` in m4a, `LYRICS` in opus), where phones and most players show it.
When LRCLIB also has the timings, they go into an `.lrc` file under the track's name, which
players with a lyrics pane, such as foobar2000, MusicBee or AIMP, scroll along with the song.

An instrumental gets nothing, and a song LRCLIB does not know or cannot answer for costs only
its lyrics: the track itself is downloaded as usual. Deleting a track from the library also
deletes its `.lrc`.

### Lists from a file

"Download a list from a file…" under the arrow beside "Download" queues everything in a
file; dropping the file on the window does the same. A `.txt` holds one link or name per
line, and blank lines and lines starting with `#` are skipped. A `.csv` is read the way
playlist exporters (Exportify, TuneMyMusic, Soundiiz) write it: from a link or URI column
when there is one, otherwise from the track and artist columns. A row from such an export
is always looked for as a single song, so a playlist holding an album's title track does not
bring the whole album with it. A file can hold up to 500 entries, and repeats are taken
once. The note under the field says how many lines could not be read.

### Watching

A finished album or playlist has an eye on its card. Press it, and the program opens that
link again every 12 hours and downloads whatever was added since into the same folder, in
the same format. Tracks already on disk are skipped, so a check that finds nothing costs
one page read and leaves no card behind. Settings → Watching lists what is watched, when
each was last checked and how many tracks it brought, with a button to check everything
now.

### In the background

On Windows the program keeps an icon by the clock. While something is downloading, being
filled in or watched, closing the window only hides it there: downloads run to the end and
watched playlists keep their schedule. A click on the icon brings the window back; its menu
also checks the watched ones at once and quits. With nothing left to do, closing the window
ends the program as before. Settings → Watching turns this off.

When downloads finish while the window is minimized, hidden or behind others, a notification
says what came of them: one for the whole queue, not one per album, and none for a watched
playlist that had nothing new. It comes from the icon on Windows, from `osascript` on macOS
and from `notify-send` on Linux, and Settings → Watching turns it off too.

The very first start shows a short welcome with the music folder, which can be changed
there and then; everything else is in Settings.

### Profiles

A folder, a format and the two naming rules can be saved under a name in the settings, and
the button beside the folder in the download toolbar switches between them: "Music, m4a"
for everyday listening, "D drive, mp3" for the car. The button stays lit while the settings
still match the profile and goes quiet as soon as one of them is changed by hand.

### Settings

The settings cover the theme (as in the system, light or dark), the language (Russian or
English; the first run takes the system's, and any other system language gets English), how
many tracks to download at once, a speed limit, a proxy, cookies from a browser, and a check
that every component is in place. They also show the version of yt-dlp inside the build,
the path to the log and a "Copy the report" button. The report holds the version, the
component paths, the settings and the end of the log, which is what a bug report needs.

The program asks GitHub about new versions once per run, and a line appears in the settings
when one is published. "Update the program" fetches that release's installer, checks it
against the SHA-256 GitHub publishes beside it, closes, and lets the installer replace the
files and open the new version. Nothing is downloaded until the button is pressed, and
nothing is run if the hash disagrees. The installer waits for the program to exit before it
touches a file and closes any copy still running from its folder, so an update cannot stop
halfway. Settings live in `%USERPROFILE%\.trackhound.json`.

YouTube serves age-restricted tracks only to a signed-in account. Usually the program takes
such a song from another source, but if there is no other, choose in the settings the
browser you are signed into YouTube with. Close that browser first, because it holds its
cookie file open. Firefox's cookies are read the most reliably.

## Command line

`Trackhound-cli.exe` is the same program for a terminal: it prints the progress and opens no
window.

```bat
Trackhound-cli.exe https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc
Trackhound-cli.exe "Daft Punk - Discovery"
Trackhound-cli.exe LINK1 LINK2 -f mp3 -o D:\Music -t 4
Trackhound-cli.exe --dry-run LINK
```

| Option | Meaning |
|---|---|
| `-o`, `--output` | folder (default `%USERPROFILE%\Music\Trackhound`) |
| `-f`, `--format` | `mp3` (default), `m4a`, `opus` |
| `-t`, `--threads` | how many tracks to download at once, default 3 |
| `--dry-run` | only show what was matched |
| `--cookies-from-browser` | take cookies from a browser (`chrome`, `edge`, `firefox`…) for age-restricted tracks |
| `--limit-rate` | limit the speed: `500K`, `2M` |
| `--proxy` | proxy for metadata and downloads: `http://127.0.0.1:1080`, `socks5://…` |
| `--names` | file names: `auto` (default), `artist`, `title` |
| `--folders` | album folders: `flat` (default), `nested`, `album` |
| `--replaygain` | measure the loudness and write ReplayGain tags; the audio is not changed |
| `--lyrics` | write lyrics from LRCLIB into the tags, and synced ones into an `.lrc` beside the track |
| `--from-file` | take links and names from a `.txt` or a playlist exported as `.csv`; can be repeated |
| `--lang` | language of the messages: `system` (default), `ru`, `en` |
| `--check` | version, paths to ffmpeg and Deno, the music folder |

## Links it understands

| Service | Which links | Where the audio comes from |
|---|---|---|
| Spotify | album, single, track, playlist, `spotify:…`, `spotify.link/…` | searched on YouTube Music and SoundCloud |
| Apple Music | album, song, playlist | searched |
| Deezer | album, track, playlist, `link.deezer.com/…` | searched |
| YouTube, YouTube Music | video, album, playlist | from the link |
| Artists on Deezer, YouTube Music, Spotify, Apple Music | the artist's page | their albums and EPs, each downloaded as above |
| SoundCloud | track, set | from the link; DRM-protected tracks are searched elsewhere |
| Last.fm | album, track | the album is found on YouTube Music or Apple Music, the audio comes from there or from a search |
| Bandcamp and other sites yt-dlp understands | album, track | from the link |

Spotify and Apple Music playlists are read off the page, and the page does not hand over the
whole list: Spotify gives the first 100 tracks, Apple Music the first 50. When a playlist
holds more, the program says so on the release line, and the rest have to be added
separately. Deezer playlists come whole, through Deezer's public API. VK is not supported;
it shows music only to a signed-in account.

A playlist's tracks keep their own artists, and are tagged as a compilation: the playlist's
name as the album, "Various artists" as the album artist, and the compilation flag, so
players group them the way they group a soundtrack. The folder is named after whoever put
the playlist together. No genre is written, since one genre would be wrong for most of the
list.

Where Spotify does not work, Russia among those places, it keeps its player page back but
still gives out the pages it makes for link previews in messengers. The release is read off
those instead: an album or a track comes whole, a playlist only with its first 30 tracks,
and the release line says so. The audio never comes from Spotify, so nothing else changes.
A longer playlist comes whole through a proxy (up to 100 tracks, as anywhere), or exported
to CSV with Exportify, TuneMyMusic or Soundiiz and opened with "Download a list from a
file…". More on blocked networks in [Where services are blocked](#where-services-are-blocked).

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

Multi-disc albums are numbered `1-01`, `2-01`. A track credited to someone besides the album
artist keeps those names in its file name. The settings change both layouts: file names as
"01. Title" (guests named separately), "01. Artist - Title" or "01. Title"; album folders as
"Artist - Album (Year)", "Artist → Album (Year)" (nested) or "Album (Year)". On the command
line these are `--names` and `--folders`.

Files that are already there are skipped, so an interrupted download can simply be started
again.

## Formats and quality

YouTube keeps two versions of every track: AAC at 128 kbit/s, cut off above 16 kHz, and
Opus at 130 to 160 kbit/s, which reaches 20 kHz. Trackhound starts from the Opus for every
format except a Premium account's AAC 256, which is kept as it is.

- `mp3`, the default, is a constant 320 kbit/s at 44.1 kHz. It plays anywhere, car
  stereos and DJ software included.
- `m4a` is the Opus turned into AAC at 256 kbit/s. Almost every player takes it.
- `opus` is the Opus as it is, with no re-encoding. Not every player understands it.

No format makes the sound better than the stream it came from, and none of the free
sources is lossless. SoundCloud hands over AAC at 160 kbit/s. Without ffmpeg `m4a` falls
back to YouTube's own AAC 128, and the other formats are not available.

## How a track is chosen

First the recording itself is looked for by its ISRC, the international code every release
of a song carries and every catalogue shares. Deezer names it for its own tracks; for a
Spotify or Apple Music link the same album is found on Deezer by name (two requests for the
whole album), and a playlist's tracks are looked up one by one, only when title, version and
length agree, so a live take or a remaster never lends its code to the original. YouTube
Music then finds the official audio filed under that code: the very recording, not another
one of the same name, and the queue shows "YouTube Music · ISRC" as its source. The code is
also written into the file's tags. Where Deezer does not answer, the lookups rest for five
minutes and tracks are matched by name as before.

Without a code, or when YouTube Music has nothing under it, the search runs on
"artist + title" in this order:

1. official audio on YouTube Music;
2. SoundCloud, since smaller artists often skip YouTube Music;
3. ordinary YouTube videos.

Once a confident match is found, the remaining sources are not asked. Candidates are scored
on title (transliterated Cyrillic included), artists, length and album. Live, remix, cover,
demo and similar versions are dropped unless those words appear in the title on the source
service. When the match refuses to download (an age-gated video, say), the next candidate
is tried, widening the search to the other sources if needed. With no suitable candidate the
track is reported as failed, and nothing random is downloaded in its place. A file that
turns out too short, such as a 30-second SoundCloud Go+ preview, counts as a failure too.

A track whose best match may be the wrong song is not downloaded by itself: its score is
weak, or a different song, a live take or another length, scores about the same. The card
lists its candidates with their source, length against the wanted one, and how well they
match; each can be listened to in the browser, and "Download the chosen" fetches the picked
ones into the same folder as a card of their own. The rest of the album downloads as usual,
and a watched playlist, with nobody to ask, takes the best candidate as before. Settings →
"Ask about doubtful matches" turns this off.

## Where services are blocked

The program talks to Spotify, YouTube and SoundCloud from your own computer, not from a
server abroad, so it sees what your network lets through. Settings → Diagnostics →
"Check" says what that is: whether Spotify's player opens or only its preview pages, whether
YouTube and SoundCloud open, and whether a VPN client runs a proxy on this computer.
`trackhound --check` prints the same.

- **A VPN in TUN or "system proxy" mode** is used by the program by itself, with nothing to
  set.
- **A VPN client that only opens a local proxy** (v2rayN, NekoBox, Clash, Hiddify, v2rayA,
  Shadowsocks, on their default ports) is found by the check, and one click puts it to use.
  A card Spotify refused looks for it too and offers "Use it and try again".
- **Any other proxy** goes into Settings → Proxy, `http://…` or `socks5://…`. A SOCKS proxy
  is asked to look names up itself, since a blocked service's name may resolve wrongly here.
- **A Spotify relay** reads the pages Spotify refuses to this network from abroad, so
  albums, tracks and playlists of up to 100 tracks come whole with no proxy at all. It is a
  free Cloudflare Worker, one file; [relay/README.md](relay/README.md) says how to put it
  up and point the program at it (Settings → Spotify relay, `--relay`).
- **Without a proxy or a relay**, a Spotify album or track still downloads, read off the
  preview pages where they come, and a playlist gives its first 30 tracks. When YouTube does not answer, the search leaves
  it out for five minutes at a time and goes to SoundCloud, which has much but not all.

## When something breaks

- **Download errors from YouTube.** YouTube changes often, and yt-dlp with it. Update to the
  newest Trackhound release; running from source, run `install.bat` again. The Settings
  section shows how old the bundled yt-dlp is.
- **"Spotify changed the shape of its page".** The parsing in `trackhound/engine/spotify.py` needs
  fixing.
- **"Spotify did not hand over … and answered “Page not found”".** Spotify will not show the
  release to this network, and its link-preview pages did not come either. A proxy helps, or
  a link to the same release from Deezer, Apple Music or YouTube Music.
- **`music.youtube.com` is unreachable on this network.** The program switches to
  `www.youtube.com` by itself.
- **A proxy is needed.** Settings → Diagnostics → "Check" finds a VPN client's proxy on this
  computer. Otherwise write the address in Settings → Proxy, or start with
  `--proxy socks5://127.0.0.1:10808`. `HTTP_PROXY`/`HTTPS_PROXY` work too.
- **Anything else.** Settings → Diagnostics → "Copy the report", and attach that to an
  [issue](https://github.com/mojave333/trackhound/issues).

## macOS and Linux

Each release also carries builds for macOS, one for Apple Silicon (M1 and newer) and one
for Intel, and one for 64-bit Linux. Both have ffmpeg and Deno inside, like the Windows one, and the command line
program sits beside the window in the same folder. They do not update themselves: when a
new version is out, the settings show a line about it with a link to the release page.

**macOS.** Open `Trackhound-vX.Y.Z-macos-arm64.dmg` on a Mac with Apple Silicon, or
`Trackhound-vX.Y.Z-macos-x64.dmg` on one with an Intel processor (Apple menu → About This Mac
says which), and drag Trackhound to Applications.
The app is not signed with an Apple developer certificate, so the first launch is refused
with "Apple could not verify…". Click "Done", open System Settings → Privacy & Security,
scroll to the line about Trackhound and click "Open Anyway". The same can be done in a
terminal:

```sh
xattr -dr com.apple.quarantine /Applications/Trackhound.app
```

The command line is `/Applications/Trackhound.app/Contents/MacOS/Trackhound-cli`.

**Linux.** Unpack `Trackhound-vX.Y.Z-linux-x64.tar.xz` anywhere and start `Trackhound` in the
folder; `Trackhound-cli` is the command line. It needs glibc 2.35 or newer (Ubuntu 22.04,
Debian 12, Fedora 36 and later). The window is drawn by Qt WebEngine, which is inside, so
nothing has to be installed. If the window does not open, start `./Trackhound` from a
terminal: it names the library it could not find.

```sh
tar -xJf Trackhound-vX.Y.Z-linux-x64.tar.xz
./Trackhound/Trackhound
```

### From source

The program also runs from source on both. The window is then drawn by WebKit (macOS) or
GTK (Linux) through pywebview. ffmpeg and Deno have to be installed separately:

```sh
brew install ffmpeg deno            # macOS
sudo apt install ffmpeg && curl -fsSL https://deno.land/install.sh | sh   # Debian/Ubuntu
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py            # the window
.venv/bin/python main.py LINK       # the command line
```

Either way, deleting from the library goes to the trash (Finder, `gio trash`, or
`~/.local/share/Trash` per the freedesktop.org specification), "Show in the file manager"
opens Finder or the desktop's file manager, and the theme follows the system. Settings live
in `~/.trackhound.json`; the log and the history in `~/Library/Application Support/Trackhound`
(macOS) or `$XDG_DATA_HOME/trackhound` (Linux).

## Python package

The engine and the command line are also a package on PyPI, for any system with Python
3.10 or newer. ffmpeg and Deno (or Node.js 22+) have to be installed separately and be on
`PATH`.

```sh
pip install trackhound          # the engine and the `trackhound` command
pip install "trackhound[gui]"   # adds the window, started with `trackhound-gui`
```

The `trackhound` command takes the same options as `Trackhound-cli.exe`. From Python:

```python
from pathlib import Path
from trackhound.engine import Downloader, Options, resolve

release = resolve("https://www.deezer.com/album/302127")
print(release.album.name, [track.title for track in release.tracks])

report = Downloader(Options(output_dir=Path("Music"))).download_link("https://www.deezer.com/album/302127")
print(report.summary())
```

What `trackhound.engine` exports is the public interface and keeps working across minor
versions; the modules behind it may change in any release. The engine writes to the
`trackhound` logger without setting up handlers, and speaks the system's language, Russian
or English, until `set_language()` says otherwise. Errors carry a code beside the sentence,
for a program to act on: `SourceError` and `DownloaderError` have `.code` and `.details`, and
`report.failures` lists each failed track with its code. The codes stay the same in either
language and across minor versions; `ERROR_CODES` says what each one means.

A new version reaches PyPI together with the Windows release: the same `release.yml` run
builds the package and publishes it through Trusted Publishing.

## Running from source

Windows and Python 3.10+. ffmpeg and Deno are not included: put `ffmpeg.exe` and `deno.exe`
into a `vendor\` folder next to the project
(`powershell -ExecutionPolicy Bypass -File scripts\fetch-vendor.ps1` downloads them) or
install them system-wide:

```bat
winget install Gyan.FFmpeg
winget install DenoLand.Deno
```

Then:

1. `install.bat` creates `.venv` and installs the dependencies.
2. `run.bat` opens the program's window.

From source, the command line is `.venv\Scripts\python.exe main.py LINK`.

### Tests

```bat
.venv\Scripts\python.exe -m pytest
```

The tests are offline: the Spotify, Apple Music and Last.fm pages come from `tests/fixtures`,
the Deezer answers are written into the tests, and no request leaves the machine. They cover
what rots by itself: the parsing of other people's markup, the match scoring on YouTube Music
and SoundCloud, file names and the settings. [`ci.yml`](.github/workflows/ci.yml) runs the
same tests on every push, on Windows, Linux and macOS.

## Building

```bat
pip install -r requirements.txt -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File scripts\fetch-vendor.ps1
pyinstaller --noconfirm trackhound.spec
powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
```

The last line is optional: it wraps the finished folder into
`dist\Trackhound-X.Y.Z-windows-x64-setup.exe`.
[Inno Setup 6.3 or newer](https://jrsoftware.org/isdl.php) does the wrapping, following
[`trackhound.iss`](trackhound.iss). When it is not installed, the script says which command
installs it (`winget install --id JRSoftware.InnoSetup`). The version comes from
`trackhound/__init__.py`, so there is nothing to keep in step by hand.

The finished folder is `dist\Trackhound`, holding two exes (`Trackhound.exe` and
`Trackhound-cli.exe`) built from the same code: the first has no console, the second has
one. Everything else lives in `_internal`, including `bin\ffmpeg.exe` and `bin\deno.exe`
from `vendor\`. The program looks there first and only then in `PATH`.

Building without `vendor\` works too and produces a lighter variant that takes ffmpeg and
Deno from the system.

On macOS and Linux the same spec file makes `dist/Trackhound.app` or `dist/Trackhound`, and
a script does the rest: it fetches ffmpeg and Deno, builds, checks that the command line
and the window start, and packs a `.dmg` or a `.tar.xz`. The Linux build needs
`pip install "pywebview[qt]"` (it is in `requirements-dev.txt`) and, for the check, `xvfb`.

```sh
pip install -r requirements.txt -r requirements-dev.txt
scripts/fetch-vendor.sh
scripts/build-unix.sh v1.2.3
```

### Releasing

Releases are made on GitHub, with nothing to do locally. Open
**Actions → Release → Run workflow** and type the version (`1.2.3`).
[`release.yml`](.github/workflows/release.yml) writes it into `trackhound/__init__.py`,
commits it, tags the commit, runs the tests, builds the exe and the installer, and publishes
the release with both files and their SHA-256. The macOS and Linux builds are attached to
the same release a few minutes later, and the Python package goes to PyPI.

Tagging by hand also works: `git tag v1.2.3 && git push origin v1.2.3` starts the same
workflow, which then checks the tag against `__version__` and stops if they disagree. A
version that has already been released is refused before anything is built.

## How it is put together

Everything that turns a link into tagged files lives in `trackhound/engine/`, which knows
nothing about the window or the command line; `tests/test_engine.py` keeps it that way.
The rest of `trackhound/` is the program built on top of it.

| File | What it does |
|---|---|
| `trackhound/engine/__init__.py` | the engine's public interface, the part a script imports |
| `trackhound/engine/sources.py` | parsing the links of every service, and finding a release by name |
| `trackhound/engine/spotify.py` | Spotify metadata |
| `trackhound/engine/models.py`, `trackhound/engine/net.py` | shared data structures and HTTP |
| `trackhound/engine/matcher.py` | searching and picking a match on YouTube Music, SoundCloud and YouTube |
| `trackhound/engine/downloader.py` | downloading (yt-dlp), converting (ffmpeg), tagging (mutagen) |
| `trackhound/engine/loudness.py` | measuring the loudness and writing ReplayGain tags |
| `trackhound/engine/lyrics.py` | looking up lyrics in LRCLIB |
| `trackhound/engine/catalog.py` | the catalogue search behind the Search view |
| `trackhound/engine/tidy.py` | filling in the tags older files lack, without downloading them again |
| `trackhound/engine/batch.py` | reading a list of links from a `.txt` or a playlist export |
| `trackhound/engine/i18n.py`, `trackhound/i18n.py`, `trackhound/web/i18n.js` | the Russian and English text: the engine's, the program's, the window's |
| `trackhound/logs.py` | the log file and the report |
| `trackhound/watch.py` | the list of watched playlists |
| `trackhound/gui.py` | the window (pywebview) and the bridge to the downloader |
| `trackhound/tray.py` | the icon by the clock and the notifications (Win32 through ctypes) |
| `trackhound/web/` | the interface: `index.html`, `style.css`, `app.js`, `i18n.js`, `icon.ico` |
| `trackhound/web/art/`, `trackhound/web/fonts/` | the drawing on the empty screen and the Commissioner font |
| `trackhound/cli.py`, `main.py` | the command line and the entry point |
| `tests/` | offline tests of the link parsing, the matching and the file names |
| `trackhound.spec`, `scripts/fetch-vendor.ps1` | building the exe |
| `scripts/fetch-vendor.sh`, `scripts/build-unix.sh` | building for macOS and Linux |
| `trackhound.iss`, `scripts/build-installer.ps1` | building the installer |
| `pyproject.toml`, `docs/pypi.md` | the Python package and its page on PyPI |

## Licence

[GPL-2.0-or-later](LICENSE). The program imports mutagen (GPL-2.0-or-later), which counts as
linking rather than mere aggregation, so the copyleft covers the whole project.

Other dependencies: yt-dlp and yt-dlp-ejs are under the Unlicense, ytmusicapi under MIT,
pywebview under BSD-3-Clause. The window writes its names and headings in Commissioner, which
travels with the program in `trackhound/web/fonts/` under the SIL Open Font License; the
licence text sits beside the files.

The release builds carry two separate programs, each under its own licence: ffmpeg (GPL,
sources at [ffmpeg.org](https://ffmpeg.org/download.html)) and Deno from
[denoland/deno](https://github.com/denoland/deno) (MIT). ffmpeg comes from the
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) builds on Windows and Linux and
from [Martin Riedl's builds](https://ffmpeg.martin-riedl.de/) on macOS. Trackhound runs both
as external processes.

The Linux build also contains Qt (LGPL-3.0) and PyQt6 (GPL-3.0), which draw its window.
Because PyQt6 is GPL-3.0 and Trackhound allows any later version of the GPL, the Linux
build as a whole is distributed under GPL-3.0.

> [!IMPORTANT]
> Use the program for music you have the rights to, and respect copyright law and the terms
> of service of YouTube and Spotify.
