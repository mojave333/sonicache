"""The icon in the taskbar's notification area, and the notices it shows (Windows).

While something is downloading or watched, closing the window only hides it:
the icon stays by the clock, downloads run to the end and the watched
playlists are checked on schedule. A click on the icon brings the window back,
and its menu can check the watched ones at once or quit. The same icon carries
the notice that downloads have finished, which Windows shows as one of its own.

It is plain Win32 through ctypes, on a thread of its own with its own message
loop, so the program needs no extra package for it. Elsewhere Tray() does
nothing and notices go through the system's own command, when there is one.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from .logs import log

SUPPORTED = sys.platform == "win32"

_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_NULL = 0x0000
_WM_CONTEXTMENU = 0x007B
_WM_LBUTTONUP = 0x0202
_WM_APP = 0x8000
_CALLBACK = _WM_APP + 1  # what the icon's clicks arrive as
_NIN_BALLOONUSERCLICK = 0x0405
_NIM_ADD, _NIM_MODIFY, _NIM_DELETE, _NIM_SETVERSION = 0, 1, 2, 4
_NIF_MESSAGE, _NIF_ICON, _NIF_TIP, _NIF_INFO, _NIF_SHOWTIP = 0x1, 0x2, 0x4, 0x10, 0x80
_NIIF_USER, _NIIF_LARGE_ICON = 0x4, 0x20
_NOTIFYICON_VERSION_4 = 4
_IMAGE_ICON, _LR_LOADFROMFILE = 1, 0x10
_SM_CXICON, _SM_CXSMICON = 11, 49
_MF_STRING, _MF_SEPARATOR = 0x0, 0x800
_TPM_RIGHTBUTTON, _TPM_RETURNCMD = 0x2, 0x100
_MENU_OPEN, _MENU_CHECK, _MENU_QUIT = 1, 2, 3


class _Guid(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _NotifyIconData(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD), ("guidItem", _Guid), ("hBalloonIcon", wintypes.HICON),
    ]


if SUPPORTED:
    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class _WndClass(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(_WndClass)]
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                        wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int,
                                   ctypes.c_int, wintypes.UINT]
    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CreatePopupMenu.restype = wintypes.HMENU
    _user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    _user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.LPVOID]
    _user32.DestroyMenu.argtypes = [wintypes.HMENU]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT
    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NotifyIconData)]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class Tray:
    """The icon, from show() until close(). Callbacks run on the icon's thread."""

    def __init__(self, icon: Path, tip: str, menu: Callable[[], dict[str, str]],
                 on_open: Callable[[], None], on_check: Callable[[], None], on_quit: Callable[[], None]):
        self._icon_path = icon
        self._tip = tip[:127]
        self._menu = menu  # the menu's words, asked for each time it opens: the language may change
        self._on_open, self._on_check, self._on_quit = on_open, on_check, on_quit
        self._hwnd = None
        self._icon = None  # small, for the taskbar
        self._picture = None  # large, for the notices
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._procedure = None  # kept alive: Windows calls it for as long as the window lives

    @property
    def shown(self) -> bool:
        return self._hwnd is not None

    def show(self) -> bool:
        """Puts the icon by the clock; False where there is none to put."""
        if not SUPPORTED:
            return False
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="tray", daemon=True)
            self._thread.start()
        self._ready.wait(5)
        return self.shown

    def notify(self, title: str, text: str) -> bool:
        """A notice from the icon; Windows shows it as a notification of its own."""
        if not self.shown:
            return False
        data = self._data(_NIF_INFO)
        data.szInfoTitle = title[:63]
        data.szInfo = text[:255] or " "
        data.dwInfoFlags = _NIIF_USER | _NIIF_LARGE_ICON
        data.hBalloonIcon = self._picture or self._icon
        return bool(_shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(data)))

    def close(self) -> None:
        if self.shown:
            _user32.PostMessageW(self._hwnd, _WM_CLOSE, 0, 0)

    def _data(self, flags: int) -> _NotifyIconData:
        data = _NotifyIconData()
        data.cbSize = ctypes.sizeof(_NotifyIconData)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = flags
        return data

    def _add(self) -> bool:
        data = self._data(_NIF_MESSAGE | _NIF_ICON | _NIF_TIP | _NIF_SHOWTIP)
        data.uCallbackMessage = _CALLBACK
        data.hIcon = self._icon
        data.szTip = self._tip
        if not _shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(data)):
            return False
        data.uVersion = _NOTIFYICON_VERSION_4  # clicks arrive with what was clicked in the low word
        _shell32.Shell_NotifyIconW(_NIM_SETVERSION, ctypes.byref(data))
        return True

    def _loop(self) -> None:
        try:
            instance = _kernel32.GetModuleHandleW(None)
            # Explorer restarted after a crash forgets every icon; it says so with this message
            taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")
            self._procedure = _WNDPROC(lambda *args: self._message(taskbar_created, *args))
            window_class = _WndClass(lpfnWndProc=self._procedure, hInstance=instance,
                                     lpszClassName="TrackhoundTray")
            if not _user32.RegisterClassW(ctypes.byref(window_class)):
                raise OSError(ctypes.get_last_error(), "RegisterClassW")
            hwnd = _user32.CreateWindowExW(0, "TrackhoundTray", "Trackhound", 0, 0, 0, 0, 0,
                                           None, None, instance, None)
            if not hwnd:
                raise OSError(ctypes.get_last_error(), "CreateWindowExW")
            # The sizes this screen's scaling asks for, picked out of icon.ico
            small, large = _user32.GetSystemMetrics(_SM_CXSMICON), _user32.GetSystemMetrics(_SM_CXICON)
            self._icon = _user32.LoadImageW(None, str(self._icon_path), _IMAGE_ICON, small, small, _LR_LOADFROMFILE)
            self._picture = _user32.LoadImageW(None, str(self._icon_path), _IMAGE_ICON, large, large,
                                               _LR_LOADFROMFILE)
            self._hwnd = hwnd
            if not self._add():
                raise OSError(ctypes.get_last_error(), "Shell_NotifyIconW")
        except OSError as e:
            log.warning("значок в трее не появился: %s", e)
            self._hwnd = None
            self._ready.set()
            return
        self._ready.set()
        message = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(message))
            _user32.DispatchMessageW(ctypes.byref(message))
        # The class points at this thread's procedure: gone with it, before a later show() makes another
        _user32.UnregisterClassW("TrackhoundTray", instance)
        self._ready.clear()  # closed: show() may put it back with a new thread
        self._thread = None

    def _message(self, taskbar_created: int, hwnd, message, wparam, lparam):
        if message == _CALLBACK:
            event = lparam & 0xFFFF
            if event in (_WM_LBUTTONUP, _NIN_BALLOONUSERCLICK):
                self._call(self._on_open)
            elif event == _WM_CONTEXTMENU:
                self._popup(hwnd)
            return 0
        if message == taskbar_created and taskbar_created:
            self._add()
            return 0
        if message == _WM_CLOSE:
            _shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(self._data(0)))
            _user32.DestroyWindow(hwnd)
            self._hwnd = None
            return 0
        if message == _WM_DESTROY:
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _popup(self, hwnd) -> None:
        words = self._menu()
        menu = _user32.CreatePopupMenu()
        _user32.AppendMenuW(menu, _MF_STRING, _MENU_OPEN, words["open"])
        _user32.AppendMenuW(menu, _MF_STRING, _MENU_CHECK, words["check"])
        _user32.AppendMenuW(menu, _MF_SEPARATOR, 0, None)
        _user32.AppendMenuW(menu, _MF_STRING, _MENU_QUIT, words["quit"])
        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        # Without this the menu stays open after a click elsewhere
        _user32.SetForegroundWindow(hwnd)
        chosen = _user32.TrackPopupMenu(menu, _TPM_RIGHTBUTTON | _TPM_RETURNCMD, point.x, point.y, 0, hwnd, None)
        _user32.PostMessageW(hwnd, _WM_NULL, 0, 0)
        _user32.DestroyMenu(menu)
        action = {_MENU_OPEN: self._on_open, _MENU_CHECK: self._on_check, _MENU_QUIT: self._on_quit}.get(chosen)
        if action:
            self._call(action)

    @staticmethod
    def _call(action: Callable[[], None]) -> None:
        # The window calls back into this thread's messages; run the action beside it
        threading.Thread(target=action, daemon=True).start()


def notify_elsewhere(title: str, text: str) -> bool:
    """A notification on macOS and Linux, through the system's own command."""
    if sys.platform == "darwin":
        script = f"display notification {_applescript(text)} with title {_applescript(title)}"
        command = ["osascript", "-e", script]
    elif shutil.which("notify-send"):
        command = ["notify-send", "--app-name=Trackhound", title, text]
    else:
        return False
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True


def _applescript(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
