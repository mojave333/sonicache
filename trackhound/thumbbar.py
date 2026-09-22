"""Previous, play or pause and next under the window's picture on the taskbar (Windows).

Hovering over the program's taskbar button shows a picture of the window, and
Windows can put buttons under it, as media players do (the thumbnail toolbar
of ITaskbarList3). A click reaches the window as WM_COMMAND with THBN_CLICKED
in the high word and the button's number in the low one, so the window's
procedure is wrapped to catch those and hand every other message on.

The icons are drawn here in the shapes of the player's own buttons, so no
image files are needed: dark on a light taskbar, light on a dark one. It is
plain Win32 and COM through ctypes, as the taskbar progress and the tray icon
are. Elsewhere ThumbBar does nothing.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Callable

from .logs import log

SUPPORTED = sys.platform == "win32"

BUTTONS = ("prev", "play", "next")  # numbered 1, 2, 3 in the order shown
# The shapes on the 24-unit square Material Icons draws in, as polygons
SHAPES = {
    "prev": [[(6, 6), (8, 6), (8, 18), (6, 18)], [(9.5, 12), (18, 6), (18, 18)]],
    "next": [[(6, 6), (14.5, 12), (6, 18)], [(16, 6), (18, 6), (18, 18), (16, 18)]],
    "play": [[(8, 5), (19, 12), (8, 19)]],
    "pause": [[(6, 5), (10, 5), (10, 19), (6, 19)], [(14, 5), (18, 5), (18, 19), (14, 19)]],
}
_SAMPLES = 4  # per pixel side: the edges come out smooth

_THB_ICON, _THB_TOOLTIP, _THB_FLAGS = 0x2, 0x4, 0x8
_THBF_ENABLED, _THBF_DISABLED, _THBF_HIDDEN = 0x0, 0x1, 0x8
_THBN_CLICKED = 0x1800
_WM_COMMAND = 0x0111
_GWLP_WNDPROC = -4
_SM_CXSMICON = 49
_CLSID_TASKBAR_LIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
_IID_TASKBAR_LIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
# ITaskbarList3's methods by their place in its table
_RELEASE, _HR_INIT, _ADD_BUTTONS, _UPDATE_BUTTONS = 2, 3, 15, 16


def coverage(shapes: list[list[tuple[float, float]]], size: int) -> list[float]:
    """How much of each pixel of a size × size icon the shapes cover, 0 to 1, row by row."""
    scale = 24 / size
    result = []
    for y in range(size):
        for x in range(size):
            hits = 0
            for sy in range(_SAMPLES):
                for sx in range(_SAMPLES):
                    px = (x + (sx + 0.5) / _SAMPLES) * scale
                    py = (y + (sy + 0.5) / _SAMPLES) * scale
                    hits += any(_inside(shape, px, py) for shape in shapes)
            result.append(hits / _SAMPLES ** 2)
    return result


def _inside(shape: list[tuple[float, float]], x: float, y: float) -> bool:
    """Whether a point is inside a polygon: a ray to the right crosses its edges an odd number of times."""
    inside = False
    for (x1, y1), (x2, y2) in zip(shape, shape[1:] + shape[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


if SUPPORTED:
    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class _ThumbButton(ctypes.Structure):
        _fields_ = [("dwMask", wintypes.DWORD), ("iId", wintypes.UINT), ("iBitmap", wintypes.UINT),
                    ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 260), ("dwFlags", wintypes.DWORD)]

    class _BitmapInfoHeader(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class _IconInfo(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                    ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]

    class _Guid(ctypes.Structure):
        _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16), ("data3", ctypes.c_uint16),
                    ("data4", ctypes.c_ubyte * 8)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _user32.GetDC.restype = wintypes.HDC
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _user32.CreateIconIndirect.restype = wintypes.HICON
    _user32.CreateIconIndirect.argtypes = [ctypes.POINTER(_IconInfo)]
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
    _gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    _gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BitmapInfoHeader), wintypes.UINT,
                                        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    _gdi32.CreateBitmap.restype = wintypes.HBITMAP
    _gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


def _light_taskbar() -> bool:
    """Whether the taskbar, and the pictures above it, are in the light theme."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except OSError:
        return False  # Windows' own default taskbar is dark


def _make_icon(shapes, size: int, rgb: tuple[int, int, int]) -> int:
    """A size × size icon: the shapes in one colour, their edges faded out in the alpha."""
    header = _BitmapInfoHeader(biSize=ctypes.sizeof(_BitmapInfoHeader), biWidth=size, biHeight=-size,  # top down
                               biPlanes=1, biBitCount=32, biCompression=0)
    bits = ctypes.c_void_p()
    screen = _user32.GetDC(None)
    color = _gdi32.CreateDIBSection(screen, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    _user32.ReleaseDC(None, screen)
    if not color or not bits.value:
        raise OSError("CreateDIBSection")
    pixels = (ctypes.c_uint32 * (size * size)).from_address(bits.value)
    red, green, blue = rgb
    for i, share in enumerate(coverage(shapes, size)):
        pixels[i] = (round(share * 255) << 24) | (red << 16) | (green << 8) | blue
    mask = _gdi32.CreateBitmap(size, size, 1, 1, None)  # unused beside the alpha, but an icon must have one
    try:
        icon = _user32.CreateIconIndirect(ctypes.byref(_IconInfo(fIcon=True, hbmMask=mask, hbmColor=color)))
    finally:
        _gdi32.DeleteObject(mask)
        _gdi32.DeleteObject(color)
    if not icon:
        raise OSError("CreateIconIndirect")
    return icon


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
                    rgb = (0x23, 0x21, 0x2C) if _light_taskbar() else (0xFF, 0xFF, 0xFF)
                    self._icons = {name: _make_icon(shapes, size, rgb) for name, shapes in SHAPES.items()}
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
        """One ITaskbarList3 call, with COM set up on this thread for it."""
        ole32 = ctypes.oledll.ole32
        try:
            ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
            owned = True
        except OSError:  # the thread already has COM in another mode, which serves as well
            owned = False
        try:
            clsid, iid = _Guid(), _Guid()
            ole32.CLSIDFromString(_CLSID_TASKBAR_LIST, ctypes.byref(clsid))
            ole32.CLSIDFromString(_IID_TASKBAR_LIST3, ctypes.byref(iid))
            taskbar = ctypes.c_void_p()
            ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(taskbar))
            methods = ctypes.cast(taskbar, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(methods[_RELEASE])
            init = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(methods[_HR_INIT])
            buttons_call = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                              ctypes.c_void_p)(methods[method])
            try:
                init(taskbar)
                buttons_call(taskbar, self._hwnd, len(buttons), ctypes.cast(buttons, ctypes.c_void_p))
            finally:
                release(taskbar)
        finally:
            if owned:
                ole32.CoUninitialize()

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
