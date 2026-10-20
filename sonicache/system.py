"""What the window asks of the operating system: the file manager, the recycle bin, the clipboard,
the system's theme, and where an update comes from.
"""

from __future__ import annotations

import ctypes
import datetime
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

from . import logs
from .i18n import t

def _package_manager(executable: str | None = None) -> str:
    """"scoop" or "aur" when the program was installed by one of them, "" else."""
    if not getattr(sys, "frozen", False) and executable is None:
        return ""
    # Split by either slash, so a Windows path reads the same on any system
    text = str(executable or sys.executable)
    parts = [part.casefold() for part in re.split(r"[\\/]+", text) if part]
    if "scoop" in parts and "apps" in parts[parts.index("scoop"):]:
        return "scoop"
    if text.replace("\\", "/").startswith("/opt/sonicache/"):
        return "aur"
    return ""

def _release_age(version: str) -> int:
    """Days since a yt-dlp release (its version is the date it was built).

    YouTube breaks yt-dlp every few weeks, so an old copy is the likeliest
    reason downloads stop working; -1 means the version said nothing.
    """
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})", version or "")
    if not m:
        return -1
    try:
        built = datetime.date(*(int(piece) for piece in m.groups()))
    except ValueError:
        return -1
    return max(0, (datetime.date.today() - built).days)

def _newer(candidate: str, current: str) -> bool:
    def parts(version: str) -> list[int]:
        return [int(piece) for piece in re.findall(r"\d+", version)] or [0]

    return parts(candidate) > parts(current)

class _FileOperation(ctypes.Structure):
    """SHFILEOPSTRUCTW: the shell call that knows about the recycle bin."""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]

_FO_DELETE = 3

# Undoable, and quiet: the window asks for confirmation itself
_FOF_FLAGS = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI

def _reveal(path: Path) -> bool:
    """Opens the file manager with the file itself picked out."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{path}"')
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
            return True
        # Nautilus, Dolphin, Nemo and the rest answer this; the caller falls
        # back to opening the folder itself when they do not.
        subprocess.run(["dbus-send", "--session", "--print-reply",
                        "--dest=org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                        "org.freedesktop.FileManager1.ShowItems",
                        f"array:string:{path.as_uri()}", "string:"],
                       check=True, capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False

def _recycle(path: Path) -> None:
    """Moves a file or folder to the desktop's trash, wherever that is.

    Nothing here deletes outright: a wrong click in the library has to stay
    undoable, so a desktop with no trash of its own raises instead.
    """
    if sys.platform == "win32":
        operation = _FileOperation(
            wFunc=_FO_DELETE,
            pFrom=f"{path.resolve()}\0\0",  # the shell wants a double-null terminated list
            fFlags=_FOF_FLAGS,
        )
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation)):
            raise OSError(t("не удалось удалить {path}", path=path))
        return

    if sys.platform == "darwin":
        script = f'tell application "Finder" to delete POSIX file "{path.resolve()}"'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode:
            raise OSError(t("не удалось удалить {path}: {error}",
                            path=path, error=result.stderr.strip()))
        return

    if shutil.which("gio"):
        result = subprocess.run(["gio", "trash", str(path.resolve())],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return
        logs.log.warning("gio trash не справился: %s", result.stderr.strip())
    _trash_by_hand(path)

def _trash_by_hand(path: Path) -> None:
    """The freedesktop.org trash, for a desktop without gio.

    A trashed file goes to ~/.local/share/Trash/files with a .trashinfo note
    beside it saying where it came from, which is what restores it.
    """
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
    files, info = root / "files", root / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)
    source = path.resolve()
    name = source.name
    for attempt in range(1, 1000):  # the trash may already hold this name
        if not (files / name).exists() and not (info / f"{name}.trashinfo").exists():
            break
        name = f"{source.stem}.{attempt}{source.suffix}"
    note = info / f"{name}.trashinfo"
    note.write_text(
        "[Trash Info]\n"
        f"Path={urllib.parse.quote(str(source))}\n"
        f"DeletionDate={datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n",
        encoding="utf-8")
    try:
        shutil.move(str(source), str(files / name))
    except OSError:
        note.unlink(missing_ok=True)
        raise

def installer_command(setup: Path, pid: int) -> list[str]:
    """How the downloaded installer is started for an update.

    Silently, because the person has already said yes by pressing the button;
    told the process id to wait for, so nothing is locked when files are
    replaced; and marked as an update, so the installer opens the new version
    when it is done. The installer also closes any copy it still finds running
    from its folder, which is what saves an update started by an older release
    that passes none of this.
    """
    return [str(setup), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1", f"/WAITPID={pid}"]

def _exit_now() -> None:
    logging.shutdown()
    os._exit(0)

def _windows_clipboard():
    """user32 and kernel32 with the clipboard's calls typed for 64-bit handles
    and pointers: left untyped, one above 2 GB came back cut to 32 bits and
    the copy wrote to an address that was not there."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    return user32, kernel32

def _copy_to_clipboard(text: str) -> bool:
    """Windows keeps clipboard data after the program that put it there exits,
    which Tk on its own does not: hence the shell call."""
    if sys.platform == "win32":
        user32, kernel32 = _windows_clipboard()
        buffer = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buffer)
        handle = kernel32.GlobalAlloc(0x2000, size)  # GMEM_MOVEABLE
        if not handle or not user32.OpenClipboard(None):
            return False
        try:
            ctypes.memmove(kernel32.GlobalLock(handle), buffer, size)
            kernel32.GlobalUnlock(handle)
            user32.EmptyClipboard()
            return bool(user32.SetClipboardData(13, handle))  # CF_UNICODETEXT
        finally:
            user32.CloseClipboard()

    # macOS and the Linux desktops keep what a command line tool pipes in
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.run(command, input=text.encode("utf-8"), check=True, timeout=5)
            return True
        except (OSError, subprocess.SubprocessError):
            continue

    root = None
    try:
        from tkinter import Tk

        root = Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        return True
    except Exception:  # a Python built without Tcl/Tk, or no display to talk to
        return False
    finally:
        if root is not None:
            root.destroy()

def _clipboard_text() -> str:
    """The text on the clipboard, "" when there is none. The window asks each
    time it comes back into use, so the system is asked directly where it
    answers at once: Windows itself, and the command line tools that macOS and
    the Linux desktops have. Tk is the last resort: it takes half a second to
    start, and here it owns a hidden root of its own, as the window is a web
    view that gives Python no clipboard to ask.

    Everything is guarded, including the import and the root: a Python built
    without Tcl/Tk must leave the button quiet, not raise into the window,
    where the js_api call would come back as a rejected promise and lose the
    message about the clipboard being empty.
    """
    if sys.platform == "win32":
        user32, kernel32 = _windows_clipboard()
        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
            pointer = kernel32.GlobalLock(handle) if handle else None
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    for command in (["pbpaste"], ["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-ob"]):
        if not shutil.which(command[0]):
            continue
        try:
            return subprocess.run(command, capture_output=True, check=True, timeout=5).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            continue

    root = None
    try:
        from tkinter import Tk

        root = Tk()
        root.withdraw()  # created and hidden before it can ever be drawn
        return root.clipboard_get()
    except Exception:  # no Tcl/Tk, no display, empty, or not text
        return ""
    finally:
        if root is not None:
            root.destroy()

def _system_dark() -> bool:
    """Whether the desktop is in its dark theme; light when nothing says so."""
    if sys.platform == "darwin":
        # The key exists only while the dark theme is on
        return "dark" in _ask(["defaults", "read", "-g", "AppleInterfaceStyle"]).lower()
    if sys.platform != "win32":
        scheme = (_ask(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
                  or _ask(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"]))
        return "dark" in scheme.lower()
    import winreg

    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False

def _ask(command: list[str]) -> str:
    """Runs a small command and returns its output, or "" when it is not there."""
    if not shutil.which(command[0]):
        return ""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()
