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

# The command line starts and finds what it needs
"$programs/Trackhound-cli" --help > /dev/null
"$programs/Trackhound-cli" --lang en --check
bin="$(find dist -type d -name bin -path '*Trackhound*' | head -n 1)"
if [ -n "$bin" ]; then
  "$bin/ffmpeg" -hide_banner -version | head -n 1
  "$bin/deno" --version | head -n 1
fi

# The window opens and stays open: a missing web view backend or a library the
# build left out ends the program within a second or two
if [ "$(uname -s)" = Linux ] && [ -z "${DISPLAY:-}" ]; then
  launcher=(xvfb-run -a)
else
  launcher=()
fi
${launcher[@]+"${launcher[@]}"} "$programs/Trackhound" > window.log 2>&1 &
pid=$!
sleep 20
if ! kill -0 "$pid" 2> /dev/null; then
  echo "The window closed by itself:" >&2
  cat window.log >&2
  exit 1
fi
pkill -P "$pid" 2> /dev/null || true
kill "$pid" 2> /dev/null || true
wait "$pid" 2> /dev/null || true
rm -f window.log
echo "The window stayed open"

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
