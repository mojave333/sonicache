"""Writes what the package managers install a release from, for one version.

    python scripts/package-managers.py 2.0.0

Every file names the release's downloads by address and by hash, and the
hashes are the ones GitHub computed for the files as uploaded, read from its
API, so nothing is downloaded here. The release workflow runs this once all
the files are up, and commits the result:

  bucket/trackhound.json                  Scoop: scoop bucket add trackhound <repo>
  Casks/trackhound.rb                     Homebrew: brew tap mojave333/trackhound <repo>
  packaging/aur/PKGBUILD, .SRCINFO        the AUR package trackhound-bin
  packaging/winget/<version>/*.yaml       winget, sent to microsoft/winget-pkgs

Scoop and Homebrew read the first two straight from this repository. The AUR
and winget files are what gets published there, by hand or by the workflow.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO = "mojave333/trackhound"
HOMEPAGE = f"https://github.com/{REPO}"
ROOT = Path(__file__).resolve().parent.parent
SUMMARY = "Music downloader, library and player"
DESCRIPTION = ("Downloads albums, playlists and tracks from Spotify, Apple Music, Deezer, YouTube, "
               "SoundCloud and Bandcamp links, or by name, with tags, covers and lyrics; "
               "also a library and player for the whole collection")
WINGET_ID = "mojave333.Trackhound"
INNO_GUID = "{FA88A291-13E9-40D1-9496-B10A55139A16}"  # AppGuid in trackhound.iss


def digests(version: str) -> dict[str, str]:
    """The SHA-256 of every file of the release, by the kind of file."""
    request = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/tags/v{version}",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "trackhound"})
    with urllib.request.urlopen(request, timeout=30) as response:
        assets = json.loads(response.read())["assets"]
    found = {}
    for asset in assets:
        digest = str(asset.get("digest") or "")
        if digest.startswith("sha256:"):
            for kind, ending in KINDS.items():
                if asset["name"] == f"Trackhound-v{version}-{ending}":
                    found[kind] = digest.removeprefix("sha256:")
    missing = set(KINDS) - set(found)
    if missing:
        raise SystemExit(f"v{version} has no hash for: {', '.join(sorted(missing))}")
    return found


KINDS = {"zip": "windows-x64.zip", "setup": "windows-x64-setup.exe", "arm64": "macos-arm64.dmg",
         "x64": "macos-x64.dmg", "linux": "linux-x64.tar.xz"}


def download(version: str, kind: str) -> str:
    return f"{HOMEPAGE}/releases/download/v{version}/Trackhound-v{version}-{KINDS[kind]}"


def scoop(version: str, sums: dict[str, str]) -> str:
    return json.dumps({
        "version": version,
        "description": DESCRIPTION,
        "homepage": HOMEPAGE,
        "license": "GPL-2.0-or-later",
        "notes": "The program updates itself only when installed from its own installer; "
                 "here, run: scoop update trackhound",
        "architecture": {"64bit": {"url": download(version, "zip"), "hash": sums["zip"]}},
        "bin": [["Trackhound-cli.exe", "trackhound"]],
        "shortcuts": [["Trackhound.exe", "Trackhound"]],
        "checkver": {"github": HOMEPAGE},
        "autoupdate": {"architecture": {"64bit": {
            "url": f"{HOMEPAGE}/releases/download/v$version/Trackhound-v$version-windows-x64.zip"}}},
    }, indent=4) + "\n"


def cask(version: str, sums: dict[str, str]) -> str:
    return f'''cask "trackhound" do
  arch arm: "arm64", intel: "x64"

  version "{version}"
  sha256 arm:   "{sums["arm64"]}",
         intel: "{sums["x64"]}"

  url "{HOMEPAGE}/releases/download/v#{{version}}/Trackhound-v#{{version}}-macos-#{{arch}}.dmg"
  name "Trackhound"
  desc "{SUMMARY}"
  homepage "{HOMEPAGE}"

  livecheck do
    url :url
    strategy :github_latest
  end

  app "Trackhound.app"
  binary "#{{appdir}}/Trackhound.app/Contents/MacOS/Trackhound-cli", target: "trackhound"

  zap trash: [
    "~/.trackhound.json",
    "~/Library/Application Support/Trackhound",
  ]

  caveats <<~EOS
    Trackhound is not signed by Apple, so macOS refuses to open it the first time.
    Either right-click Trackhound in Applications and choose Open, or run:
      xattr -dr com.apple.quarantine /Applications/Trackhound.app
  EOS
end
'''


# What the bundled Qt WebEngine needs of the system: the Linux build installs
# the same on Ubuntu (see release.yml), here under Arch's names
AUR_DEPENDS = ("alsa-lib", "libglvnd", "libxcomposite", "libxdamage", "libxkbcommon-x11", "libxkbfile",
               "libxrandr", "libxtst", "nss", "xcb-util-cursor", "xcb-util-image", "xcb-util-keysyms",
               "xcb-util-renderutil", "xcb-util-wm")
LOGO_SHA256 = "2bebe0ae274a0841e879f234699cbdc86f2f44edef51c163c7b44dfe19c8060d"  # docs/logo.png


def pkgbuild(version: str, sums: dict[str, str]) -> str:
    depends = " ".join(f"'{name}'" for name in AUR_DEPENDS)
    return f'''# Maintainer: mojave333 <https://github.com/mojave333>
pkgname=trackhound-bin
pkgver={version}
pkgrel=1
pkgdesc="{SUMMARY}: albums, playlists and tracks by link or by name, with tags, covers and lyrics"
arch=('x86_64')
url="{HOMEPAGE}"
license=('GPL-2.0-or-later')
provides=('trackhound')
conflicts=('trackhound')
depends=({depends})
options=('!strip' '!debug')
source=("Trackhound-v${{pkgver}}-linux-x64.tar.xz::${{url}}/releases/download/v${{pkgver}}/Trackhound-v${{pkgver}}-linux-x64.tar.xz"
        "trackhound.png::https://raw.githubusercontent.com/{REPO}/v${{pkgver}}/docs/logo.png")
sha256sums=('{sums["linux"]}'
            '{LOGO_SHA256}')

package() {{
  install -d "$pkgdir/opt"
  cp -a Trackhound "$pkgdir/opt/trackhound"
  install -d "$pkgdir/usr/bin"
  ln -s /opt/trackhound/Trackhound-cli "$pkgdir/usr/bin/trackhound"
  ln -s /opt/trackhound/Trackhound "$pkgdir/usr/bin/trackhound-window"
  install -Dm644 trackhound.png "$pkgdir/usr/share/pixmaps/trackhound.png"
  install -Dm644 /dev/stdin "$pkgdir/usr/share/applications/trackhound.desktop" <<END
[Desktop Entry]
Type=Application
Name=Trackhound
Comment={SUMMARY}
Exec=/opt/trackhound/Trackhound
Icon=trackhound
Categories=AudioVideo;Audio;Player;Network;
Terminal=false
END
}}
'''


def srcinfo(version: str, sums: dict[str, str]) -> str:
    lines = [
        "pkgbase = trackhound-bin",
        f"\tpkgdesc = {SUMMARY}: albums, playlists and tracks by link or by name, with tags, covers and lyrics",
        f"\tpkgver = {version}",
        "\tpkgrel = 1",
        f"\turl = {HOMEPAGE}",
        "\tarch = x86_64",
        "\tlicense = GPL-2.0-or-later",
        *(f"\tdepends = {name}" for name in AUR_DEPENDS),
        "\tprovides = trackhound",
        "\tconflicts = trackhound",
        "\toptions = !strip",
        "\toptions = !debug",
        f"\tsource = Trackhound-v{version}-linux-x64.tar.xz::{download(version, 'linux')}",
        f"\tsource = trackhound.png::https://raw.githubusercontent.com/{REPO}/v{version}/docs/logo.png",
        f"\tsha256sums = {sums['linux']}",
        f"\tsha256sums = {LOGO_SHA256}",
        "",
        "pkgname = trackhound-bin",
        "",
    ]
    return "\n".join(lines)


WINGET_SCHEMA = "1.10.0"


def winget(version: str, sums: dict[str, str]) -> dict[str, str]:
    header = f"# yaml-language-server: $schema=https://aka.ms/winget-manifest.{{kind}}.{WINGET_SCHEMA}.schema.json\n\n"
    common = f"PackageIdentifier: {WINGET_ID}\nPackageVersion: {version}\n"
    return {
        f"{WINGET_ID}.yaml": header.format(kind="version") + common + (
            "DefaultLocale: en-US\n"
            "ManifestType: version\n"
            f"ManifestVersion: {WINGET_SCHEMA}\n"),
        f"{WINGET_ID}.installer.yaml": header.format(kind="installer") + common + (
            "InstallerType: inno\n"
            "Scope: user\n"
            "UpgradeBehavior: install\n"
            f"ProductCode: '{INNO_GUID}_is1'\n"
            "Installers:\n"
            "- Architecture: x64\n"
            f"  InstallerUrl: {download(version, 'setup')}\n"
            f"  InstallerSha256: {sums['setup'].upper()}\n"
            "ManifestType: installer\n"
            f"ManifestVersion: {WINGET_SCHEMA}\n"),
        f"{WINGET_ID}.locale.en-US.yaml": header.format(kind="defaultLocale") + common + (
            "PackageLocale: en-US\n"
            "Publisher: mojave333\n"
            f"PublisherUrl: https://github.com/mojave333\n"
            f"PublisherSupportUrl: {HOMEPAGE}/issues\n"
            "PackageName: Trackhound\n"
            f"PackageUrl: {HOMEPAGE}\n"
            "License: GPL-2.0-or-later\n"
            f"LicenseUrl: {HOMEPAGE}/blob/main/LICENSE\n"
            f"ShortDescription: {SUMMARY}\n"
            f"Description: {DESCRIPTION}.\n"
            "Tags:\n- music\n- downloader\n- music-player\n- yt-dlp\n- lyrics\n- tagging\n"
            f"ReleaseNotesUrl: {HOMEPAGE}/releases/tag/v{version}\n"
            "ManifestType: defaultLocale\n"
            f"ManifestVersion: {WINGET_SCHEMA}\n"),
    }


def main() -> None:
    version = sys.argv[1].lstrip("vV") if len(sys.argv) > 1 else ""
    if not version:
        raise SystemExit(__doc__)
    sums = digests(version)
    files = {
        ROOT / "bucket" / "trackhound.json": scoop(version, sums),
        ROOT / "Casks" / "trackhound.rb": cask(version, sums),
        ROOT / "packaging" / "aur" / "PKGBUILD": pkgbuild(version, sums),
        ROOT / "packaging" / "aur" / ".SRCINFO": srcinfo(version, sums),
    }
    winget_folder = ROOT / "packaging" / "winget"
    for old in winget_folder.glob("*/"):  # only the current version is kept
        for file in old.glob("*.yaml"):
            file.unlink()
        old.rmdir()
    files.update({winget_folder / version / name: text for name, text in winget(version, sums).items()})
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(path.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
