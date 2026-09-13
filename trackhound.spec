# PyInstaller build: one folder holding two entry points into the same code.
#
#     pyinstaller --noconfirm trackhound.spec
#
# Trackhound.exe      double-clicked, no console window
# Trackhound-cli.exe  run from a terminal, prints its progress
#
# ffmpeg.exe and deno.exe are picked up from vendor\ when they are there and
# land in bin\ next to the exe, where downloader.find_tool() looks first. The
# build works without them; the program then falls back to whatever is on PATH.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

datas = [("trackhound/web", "trackhound/web")]
binaries = [(str(tool), "bin") for tool in sorted(Path("vendor").glob("*.exe"))]
hiddenimports = ["trackhound.cli", "trackhound.gui", "tkinter", "tkinter.messagebox"]

# Extractors, JavaScript payloads, locale files and the WebView2 glue are all
# loaded dynamically, so PyInstaller cannot see them by reading the imports.
for package in ("webview", "yt_dlp", "yt_dlp_ejs", "ytmusicapi", "clr_loader", "pythonnet"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

analysis = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "matplotlib", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

icon = ["trackhound/web/icon.ico"] if Path("trackhound/web/icon.ico").is_file() else None

window = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Trackhound",
    console=False,
    icon=icon,
)
console = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Trackhound-cli",
    console=True,
    icon=icon,
)
COLLECT(
    window,
    console,
    analysis.binaries,
    analysis.datas,
    name="Trackhound",
)
