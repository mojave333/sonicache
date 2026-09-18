#!/usr/bin/env bash
# Builds, checks and packs Trackhound on macOS or Linux.
#
#     scripts/fetch-vendor.sh          # optional: bundle ffmpeg and deno
#     scripts/build-unix.sh v1.3.0     # the tag goes into the file name
#
# macOS:  Trackhound-v1.3.0-macos-arm64.dmg, holding Trackhound.app
# Linux:  Trackhound-v1.3.0-linux-x64.tar.xz, holding the Trackhound folder
#
# The name of the packed file is printed last, for the workflow to pick up.
set -euo pipefail

tag="${1:-dev}"
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

pyinstaller --noconfirm trackhound.spec

# Qt WebEngine brings Chromium's interface text in every language it has and its
# developer tools; the window speaks Russian or English and has no use for either
qt="dist/Trackhound/_internal/PyQt6/Qt6"
if [ -d "$qt/translations/qtwebengine_locales" ]; then
  find "$qt/translations/qtwebengine_locales" -name '*.pak' ! -name 'en-US.pak' ! -name 'ru.pak' -delete
  rm -f "$qt/resources/qtwebengine_devtools_resources.pak"
fi

case "$(uname -s)" in
  Darwin)
    programs="dist/Trackhound.app/Contents/MacOS"
    package="Trackhound-$tag-macos-arm64.dmg"
    ;;
  Linux)
    programs="dist/Trackhound"
    package="Trackhound-$tag-linux-x64.tar.xz"
    ;;
  *)
    echo "Unknown system $(uname -s)" >&2
    exit 1
    ;;
esac

# Nobody may have this system at hand to try the build on, so the checks below
# stand in for a person: each one runs a part the way a user would.
scratch="$(mktemp -d)"

# The command line starts and finds what it needs
"$programs/Trackhound-cli" --help > /dev/null
check="$("$programs/Trackhound-cli" --lang en --check)"
echo "$check"
log="$(printf '%s\n' "$check" | sed -n 's/^log: //p')"

# The bundled tools do their work, not just print a version: every encoder the
# formats need, and JavaScript for YouTube
bin="$(find dist -type d -name bin -path '*Trackhound*' | head -n 1)"
if [ -n "$bin" ]; then
  for codec in aac libmp3lame libopus; do
    "$bin/ffmpeg" -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:duration=1" \
      -c:a "$codec" -f null -
  done
  [ "$("$bin/deno" eval 'console.log(6 * 7)')" = 42 ]
  echo "ffmpeg encodes aac, mp3 and opus; deno runs JavaScript"
fi

# A real download: yt-dlp's own ten-second test song, re-encoded to opus and
# measured for ReplayGain, which puts ffmpeg, mutagen and the file names (the
# title is full of quotes and non-Latin letters) through their paces
"$programs/Trackhound-cli" --lang en -f opus --replaygain -o "$scratch/music" \
  "https://youtube-dl.bandcamp.com/track/youtube-dl-test-song"
find "$scratch/music" -name '*.opus' -size +10k | grep -q .
# Deezer's metadata and the search on YouTube Music, without downloading
"$programs/Trackhound-cli" --lang en --dry-run "https://www.deezer.com/track/3135553"

# The window opens, its page loads and reaches Python (the program logs that),
# and it stays open. A missing web view backend or a library the build left
# out ends the program within seconds; a blank page never logs the line.
if [ "$(uname -s)" = Linux ] && [ -z "${DISPLAY:-}" ]; then
  launcher=(xvfb-run -a)
else
  launcher=()
fi
marker="окно: интерфейс загружен"
before="$(grep -cF "$marker" "$log" 2> /dev/null || true)"
${launcher[@]+"${launcher[@]}"} "$programs/Trackhound" > "$scratch/window.log" 2>&1 &
pid=$!
loaded=""
for _ in $(seq 90); do
  sleep 1
  if ! kill -0 "$pid" 2> /dev/null; then
    break
  fi
  if [ "$(grep -cF "$marker" "$log" 2> /dev/null || true)" -gt "${before:-0}" ]; then
    loaded=yes
    break
  fi
done
[ -n "$loaded" ] && sleep 5
if [ -z "$loaded" ] || ! kill -0 "$pid" 2> /dev/null; then
  echo "The window did not load, or closed by itself:" >&2
  cat "$scratch/window.log" >&2
  tail -n 30 "$log" >&2 || true
  exit 1
fi
pkill -P "$pid" 2> /dev/null || true
kill "$pid" 2> /dev/null || true
wait "$pid" 2> /dev/null || true
echo "The window opened, loaded its page and stayed open"
rm -rf "$scratch"

rm -f "$package"
if [ "$(uname -s)" = Darwin ]; then
  # The usual disk image: the app beside a link to Applications, to drag it onto
  staging="$(mktemp -d)"
  cp -R dist/Trackhound.app "$staging/"
  ln -s /Applications "$staging/Applications"
  hdiutil create -volname Trackhound -srcfolder "$staging" -ov -format UDZO "$package" > /dev/null
  rm -rf "$staging"
else
  # xz packs the Qt and ffmpeg libraries noticeably tighter than gzip
  tar -cf - -C dist Trackhound | xz -T0 -6 > "$package"
fi
du -h "$package" >&2
# What the size is made of, for when it grows
find dist -type f -size +5M -exec du -h {} + | sort -rh | head -n 15 >&2
echo "$package"
