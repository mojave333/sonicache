# PyInstaller build: one folder holding two entry points into the same code.
#
#     pyinstaller --noconfirm trackhound.spec
#
# Trackhound(.exe)      double-clicked, no console window
# Trackhound-cli(.exe)  run from a terminal, prints its progress
#
# The same file builds on all three systems. Windows gets dist\Trackhound, Linux
# gets dist/Trackhound, macOS gets dist/Trackhound.app with both programs in
# Contents/MacOS.
#
# ffmpeg and deno are picked up from vendor/ when they are there (fetch-vendor.ps1
# on Windows, fetch-vendor.sh elsewhere) and land in bin/, where
# downloader.find_tool() looks first. The build works without them; the program
# then falls back to whatever is on PATH.
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")

datas = [("trackhound/web", "trackhound/web")]
tools = {"ffmpeg.exe", "deno.exe"} if WINDOWS else {"ffmpeg", "deno"}
binaries = [(str(tool), "bin") for tool in sorted(Path("vendor").glob("*")) if tool.name in tools]
hiddenimports = ["trackhound.cli", "trackhound.gui", "tkinter", "tkinter.messagebox"]

# Extractors, JavaScript payloads, locale files and the window's glue are all
# loaded dynamically, so PyInstaller cannot see them by reading the imports.
packages = ["webview", "yt_dlp", "yt_dlp_ejs", "ytmusicapi"]
if WINDOWS:
    packages += ["clr_loader", "pythonnet"]  # WebView2 through .NET
for package in packages:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# The window is drawn by the system's own web view: WebView2 on Windows, WebKit
# through pyobjc on macOS. Linux has no such thing that can be bundled sanely
# (WebKitGTK expects its helper programs at fixed system paths), so the Linux
# build carries Qt WebEngine and pywebview's Qt backend instead.
excludes = ["PyQt5", "PySide2", "PySide6", "gi", "matplotlib", "numpy", "pytest"]
if LINUX:
    hiddenimports += ["qtpy", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineCore", "PyQt6.QtWebChannel",
                      "PyQt6.QtNetwork"]
    # The rest of Qt, which would otherwise come along with its QML plugins
    # and more than double the archive
    excludes += [f"PyQt6.{module}" for module in (
        "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic", "Qt3DRender",
        "QtBluetooth", "QtCharts", "QtDataVisualization", "QtDesigner", "QtHelp", "QtMultimedia",
        "QtMultimediaWidgets", "QtNfc", "QtPdf", "QtPdfWidgets", "QtQml", "QtQuick", "QtQuick3D",
        "QtQuickWidgets", "QtRemoteObjects", "QtSensors", "QtSerialPort", "QtSpatialAudio", "QtSql",
        "QtStateMachine", "QtTest", "QtTextToSpeech", "QtWebEngineQuick", "QtWebSockets",
    )]
else:
    excludes.append("PyQt6")

analysis = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(analysis.pure)

icon = ["trackhound/web/icon.ico"] if Path("trackhound/web/icon.ico").is_file() else None
version = re.search(r'__version__ = "([^"]+)"', Path("trackhound/__init__.py").read_text(encoding="utf-8"))[1]


def version_resource(filename, description):
    """Only Windows reads it.

    An unsigned exe with no version resource at all is what Windows Defender's
    machine-learning heuristics like least: a bare PyInstaller build regularly
    comes back as Trojan:Win32/Sabsik. Filling the resource in does not replace
    a certificate, but it costs nothing and gives the scanners something to read.
    """
    if not WINDOWS:
        return None
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = tuple(int(part) for part in version.split(".")) + (0,) * (4 - len(version.split(".")))
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([
                StringTable("040904B0", [  # English (US), Unicode
                    StringStruct("CompanyName", "Trackhound"),
                    StringStruct("FileDescription", description),
                    StringStruct("FileVersion", version),
                    StringStruct("InternalName", filename),
                    StringStruct("LegalCopyright", "GPL-2.0-or-later"),
                    StringStruct("OriginalFilename", filename),
                    StringStruct("ProductName", "Trackhound"),
                    StringStruct("ProductVersion", version),
                ]),
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


window = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Trackhound",
    console=False,
    icon=icon,
    version=version_resource("Trackhound.exe", "Trackhound music downloader"),
)
console = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Trackhound-cli",
    console=True,
    icon=icon,
    version=version_resource("Trackhound-cli.exe", "Trackhound music downloader (command line)"),
)
folder = COLLECT(
    window,
    console,
    analysis.binaries,
    analysis.datas,
    name="Trackhound",
)
if MAC:
    BUNDLE(
        folder,
        name="Trackhound.app",
        icon=icon[0] if icon else None,  # converted to .icns, which needs Pillow at build time
        bundle_identifier="io.github.mojave333.trackhound",
        version=version,
        info_plist={
            "CFBundleDisplayName": "Trackhound",
            "CFBundleShortVersionString": version,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # follow the system's dark mode
        },
    )
