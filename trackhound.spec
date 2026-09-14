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
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

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

# An unsigned exe with no version resource at all is what Windows Defender's
# machine-learning heuristics like least: a bare PyInstaller build regularly
# comes back as Trojan:Win32/Sabsik. Filling the resource in does not replace a
# certificate, but it costs nothing and gives the scanners something to read.
version = re.search(r'__version__ = "([^"]+)"', Path("trackhound/__init__.py").read_text(encoding="utf-8"))[1]
numbers = tuple(int(part) for part in version.split(".")) + (0,) * (4 - len(version.split(".")))


def version_resource(filename, description):
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([
                StringTable("040904B0", [  # English (US), Unicode
                    StringStruct("CompanyName", "Trackhound"),
                    StringStruct("FileDescription", description),
                    StringStruct("FileVersion", version),
                    StringStruct("InternalName", filename),
                    StringStruct("LegalCopyright", "MIT License"),
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
COLLECT(
    window,
    console,
    analysis.binaries,
    analysis.datas,
    name="Trackhound",
)
