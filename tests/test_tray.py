"""The icon by the clock and the notices it gives (Windows)."""

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

from trackhound import tray

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the notification area is Windows'")
ICON = Path(tray.__file__).with_name("web") / "icon.ico"


class IconInfo(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", ctypes.c_void_p), ("hbmColor", ctypes.c_void_p)]


class Bitmap(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


def width(icon: int) -> int:
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    user32.GetIconInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(IconInfo)]
    gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    info, bitmap = IconInfo(), Bitmap()
    assert user32.GetIconInfo(icon, ctypes.byref(info))
    try:
        gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(Bitmap), ctypes.byref(bitmap))
    finally:
        gdi32.DeleteObject(info.hbmColor)
        gdi32.DeleteObject(info.hbmMask)
    return bitmap.bmWidth


def test_the_notices_picture_is_the_icons_largest():
    """Windows shows it bigger than SM_CXICON, stretching a smaller one out of
    focus, and refuses one smaller than SM_CXICON (ERROR_INCORRECT_SIZE) once
    the window has made the process DPI-aware, which it does only after the
    icon by the clock is made."""
    icon = tray.Tray(ICON, "Trackhound", lambda: {}, on_open=lambda: None, on_check=lambda: None,
                     on_quit=lambda: None)
    if not icon.show():
        pytest.skip("no notification area here")
    try:
        assert width(icon._picture) == 256
    finally:
        icon.close()
