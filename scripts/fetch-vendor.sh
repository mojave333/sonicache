#!/usr/bin/env bash
# The macOS and Linux counterpart of fetch-vendor.ps1: downloads ffmpeg and
# deno into vendor/, where trackhound.spec picks them up.
#
#     scripts/fetch-vendor.sh
#
# Linux gets x86-64 builds; macOS gets arm64 or x86-64 ones, whichever this Mac is.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
vendor="$root/vendor"
mkdir -p "$vendor"
temp="$(mktemp -d)"
trap 'rm -rf "$temp"' EXIT

case "$(uname -s)" in
  Linux)
    # GPL build from the same project as the Windows one
    ffmpeg_url="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
    deno_url="https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip"
    ;;
  Darwin)
    # A static GPL build; BtbN makes none for macOS
    if [ "$(uname -m)" = arm64 ]; then
      ffmpeg_arch=arm64 deno_arch=aarch64
    else
      ffmpeg_arch=amd64 deno_arch=x86_64
    fi
    ffmpeg_url="https://ffmpeg.martin-riedl.de/redirect/latest/macos/$ffmpeg_arch/release/ffmpeg.zip"
    deno_url="https://github.com/denoland/deno/releases/latest/download/deno-$deno_arch-apple-darwin.zip"
    ;;
  *)
    echo "No builds of ffmpeg and deno are known for $(uname -s)" >&2
    exit 1
    ;;
esac

fetch() {
  local name="$1" url="$2"
  if [ -x "$vendor/$name" ]; then
    echo "$name is already there, skipping"
    return
  fi
  echo "Downloading $name from $url"
  local archive="$temp/$name-archive"
  curl -fsSL --retry 3 -o "$archive" "$url"
  mkdir -p "$temp/$name"
  case "$url" in
    *.tar.xz) tar -xJf "$archive" -C "$temp/$name" ;;
    *) unzip -q "$archive" -d "$temp/$name" ;;
  esac
  local found
  found="$(find "$temp/$name" -type f -name "$name" | head -n 1)"
  if [ -z "$found" ]; then
    echo "$name not found in $url" >&2
    exit 1
  fi
  cp "$found" "$vendor/$name"
  chmod +x "$vendor/$name"
  echo "  $vendor/$name -> $(du -h "$vendor/$name" | cut -f1)"
}

fetch ffmpeg "$ffmpeg_url"
fetch deno "$deno_url"
"$vendor/ffmpeg" -hide_banner -version | head -n 1
"$vendor/deno" --version | head -n 1
