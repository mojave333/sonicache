"""Previous, play or pause and next under the window's picture on the taskbar (Windows).

Hovering over the program's taskbar button shows a picture of the window, and
Windows can put buttons under it, as media players do (the thumbnail toolbar
of ITaskbarList3). A click reaches the window as WM_COMMAND with THBN_CLICKED
in the high word and the button's number in the low one, so the window's
procedure is wrapped to catch those and hand every other message on.

The icons are files beside the window's own (web/thumb-*.ico), in the shapes
of the player's buttons: dark ones on a light taskbar, light ones on a dark
one. The same ITaskbarList3 fills the taskbar button with the downloads'
progress (progress()). It is plain Win32 and COM through ctypes, as the tray
icon is. Elsewhere none of it does anything.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
import threading
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from .logs import log

SUPPORTED = sys.platform == "win32"

BUTTONS = ("prev", "play", "next")  # numbered 1, 2, 3 in the order shown
# Each icon comes black and white, at the sizes the taskbar asks for as the screen is scaled
ICON_DIR = Path(__file__).with_name("web")

_THB_ICON, _THB_TOOLTIP, _THB_FLAGS = 0x2, 0x4, 0x8
_THBF_ENABLED, _THBF_DISABLED, _THBF_HIDDEN = 0x0, 0x1, 0x8
_THBN_CLICKED = 0x1800
_WM_COMMAND = 0x0111
_GWLP_WNDPROC = -4
_SM_CXSMICON = 49
_IMAGE_ICON, _LR_LOADFROMFILE = 1, 0x10
_CLSID_TASKBAR_LIST = uuid.UUID("56FDF344-FD6D-11d0-958A-006097C9A090").bytes_le
_IID_TASKBAR_LIST3 = uuid.UUID("EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF").bytes_le
# ITaskbarList3's methods by their place in its table
_RELEASE, _HR_INIT, _SET_PROGRESS_VALUE, _SET_PROGRESS_STATE, _ADD_BUTTONS, _UPDATE_BUTTONS = 2, 3, 9, 10, 15, 16


if SUPPORTED:
    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class _ThumbButton(ctypes.Structure):
        _fields_ = [("dwMask", wintypes.DWORD), ("iId", wintypes.UINT), ("iBitmap", wintypes.UINT),
                    ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 260), ("dwFlags", wintypes.DWORD)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                   wintypes.UINT]
    _user32.DestroyIcon.argtypes = [wintypes.HICON]
    _user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.CallWindowProcW.restype = _LRESULT
    _user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                        wintypes.LPARAM]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT
    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]


def _light_taskbar() -> bool:
    """Whether the taskbar, and the pictures above it, are in the light theme."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except OSError:
        return False  # Windows' own default taskbar is dark


def _icon_file(name: str, colour: str) -> Path:
    return ICON_DIR / f"thumb-{name}-{colour}.ico"


def _load_icon(path: Path, size: int) -> int:
    icon = _user32.LoadImageW(None, str(path), _IMAGE_ICON, size, size, _LR_LOADFROMFILE)
    if not icon:
        raise OSError(f"LoadImageW {path.name}")
    return icon


@contextlib.contextmanager
def _taskbar():
    """ITaskbarList3 and its table of methods, with COM set up on this thread
    for it: the window's calls each come on a thread of their own."""
    ole32 = ctypes.oledll.ole32
    try:
        ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        owned = True
    except OSError:  # the thread already has COM in another mode, which serves as well
        owned = False
    try:
        taskbar = ctypes.c_void_p()
        ole32.CoCreateInstance(_CLSID_TASKBAR_LIST, None, 1, _IID_TASKBAR_LIST3, ctypes.byref(taskbar))  # INPROC
        methods = ctypes.cast(taskbar, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        try:
            ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(methods[_HR_INIT])(taskbar)
            yield taskbar, methods
        finally:
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(methods[_RELEASE])(taskbar)
    finally:
        if owned:
            ole32.CoUninitialize()


def progress(hwnd: int, flag: int, percent: int) -> None:
    """Fills the window's taskbar button the way Explorer does while copying;
    flag is a TBPF_ state: 0 none, 1 a sweep, 2 a fill, 4 an error, 8 paused."""
    if not SUPPORTED:
        return
    with _taskbar() as (taskbar, methods):
        ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)(
            methods[_SET_PROGRESS_STATE])(taskbar, hwnd, flag)
        if flag not in (0, 1):  # a value would turn the sweep back into a plain fill
            ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulonglong,
                               ctypes.c_ulonglong)(methods[_SET_PROGRESS_VALUE])(taskbar, hwnd, percent, 100)


class ThumbBar:
    """The buttons of one window's thumbnail. show() puts them up or updates
    them, show(None) hides them; on_click gets "prev", "play" or "next", on a
    thread of its own, so it may call back into the window."""

    def __init__(self, hwnd: int, on_click: Callable[[str], None]):
        self._hwnd = hwnd
        self._on_click = on_click
        self._lock = threading.Lock()
        self._icons: dict[str, int] = {}
        self._added = False
        self._last: dict | None = None
        self._old_proc = None  # None once the window has its own procedure back
        self._chain = None
        self._proc = None
        if not SUPPORTED:
            return
        # Explorer sends this when it starts again: the buttons have to be put up anew
        self._restarted = _user32.RegisterWindowMessageW("TaskbarButtonCreated")
        self._proc = _WNDPROC(self._window_proc)
        self._chain = _user32.SetWindowLongPtrW(hwnd, _GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))
        self._old_proc = self._chain

    def show(self, state: dict | None) -> None:
        if not SUPPORTED or self._old_proc is None:
            return
        with self._lock:
            if state is None and not self._added:
                return
            self._last = state
            try:
                if not self._icons:
                    size = _user32.GetSystemMetrics(_SM_CXSMICON) or 16
                    colour = "black" if _light_taskbar() else "white"
                    self._icons = {name: _load_icon(_icon_file(name, colour), size)
                                   for name in ("prev", "play", "pause", "next")}
                self._call(_UPDATE_BUTTONS if self._added else _ADD_BUTTONS, self._buttons(state))
                self._added = True
            except Exception as e:  # a picture on the taskbar is never worth a failed call
                log.debug("кнопки на панели задач: %s", e)

    def close(self) -> None:
        """Gives the window its own procedure back, before the window goes."""
        if not SUPPORTED or self._old_proc is None:
            return
        current = _user32.GetWindowLongPtrW(self._hwnd, _GWLP_WNDPROC)
        if current == ctypes.cast(self._proc, ctypes.c_void_p).value:
            _user32.SetWindowLongPtrW(self._hwnd, _GWLP_WNDPROC, self._old_proc)
        with self._lock:
            for icon in self._icons.values():
                _user32.DestroyIcon(icon)
            self._icons = {}
        self._old_proc = None

    def _buttons(self, state: dict | None):
        state = state or {}
        words = state.get("words") or {}
        buttons = (_ThumbButton * len(BUTTONS))()
        for number, (button, name) in enumerate(zip(buttons, BUTTONS), start=1):
            shown = "pause" if name == "play" and state.get("playing") else name
            enabled = name == "play" or state.get(name, False)
            button.dwMask = _THB_ICON | _THB_TOOLTIP | _THB_FLAGS
            button.iId = number
            button.hIcon = self._icons[shown]
            button.szTip = str(words.get(shown, ""))[:259]
            button.dwFlags = _THBF_HIDDEN if not state else _THBF_ENABLED if enabled else _THBF_DISABLED
        return buttons

    def _call(self, method: int, buttons) -> None:
        with _taskbar() as (taskbar, methods):
            ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, wintypes.UINT, ctypes.c_void_p)(
                methods[method])(taskbar, self._hwnd, len(buttons), ctypes.cast(buttons, ctypes.c_void_p))

    def _window_proc(self, hwnd, message, wparam, lparam):
        try:
            if message == _WM_COMMAND and (wparam >> 16) & 0xFFFF == _THBN_CLICKED:
                number = wparam & 0xFFFF
                if 1 <= number <= len(BUTTONS):
                    threading.Thread(target=self._on_click, args=(BUTTONS[number - 1],), daemon=True).start()
                    return 0
            elif message == self._restarted and self._added:
                self._added = False
                threading.Thread(target=self.show, args=(self._last,), daemon=True).start()
        except Exception as e:  # the window's own messages must go on whatever happens here
            log.debug("кнопки на панели задач: %s", e)
        return _user32.CallWindowProcW(self._chain, hwnd, message, wparam, lparam)
