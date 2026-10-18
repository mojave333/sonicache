"""The window without its system frame: how it is moved, sized and kept off the taskbar.

Windows draws a title bar and a border for a window; without them the window has
to be moved and sized by the program. Both are done here in the way the system
does them, by following the cursor for as long as the button is held: the page
says where the button went down (the top bar, an edge) and one call here carries
it through to the release. The arithmetic is plain functions, so it can be tested
without a window.
"""

from __future__ import annotations

import sys
import threading
import time

EDGES = ("n", "s", "e", "w", "ne", "nw", "se", "sw")
GRAB = 4          # pixels the cursor must travel before a press becomes a drag
STEP = 0.008      # seconds between one look at the cursor and the next
GRAB_HEIGHT = 28  # how far under the top edge a window taken out of maximized is held


def resized(origin: tuple[int, int, int, int], delta: tuple[int, int], edge: str,
            minimum: tuple[int, int]) -> tuple[int, int, int, int]:
    """The window's rectangle after an edge or a corner is dragged by `delta`.

    The opposite side stays where it is, and the window stops shrinking at its
    minimum size rather than being pushed along by the side being dragged.
    """
    left, top, right, bottom = origin
    dx, dy = delta
    min_width, min_height = minimum
    if "w" in edge:
        left = min(left + dx, right - min_width)
    if "e" in edge:
        right = max(right + dx, left + min_width)
    if "n" in edge:
        top = min(top + dy, bottom - min_height)
    if "s" in edge:
        bottom = max(bottom + dy, top + min_height)
    return left, top, right, bottom


def centred(work: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """A window of `size` (smaller of it and the screen's working area) in the middle of the area."""
    left, top, right, bottom = work
    width, height = min(size[0], right - left), min(size[1], bottom - top)
    x, y = left + (right - left - width) // 2, top + (bottom - top - height) // 2
    return x, y, x + width, y + height


def restored_corner(maximized: tuple[int, int, int, int], cursor: tuple[int, int],
                    size: tuple[int, int]) -> tuple[int, int]:
    """Where the top left of a window goes when it is taken out of maximized by its
    top bar: held at the same share of its width under the cursor, a little below
    its upper edge, as Windows does it."""
    left, top, right, _ = maximized
    cursor_x, cursor_y = cursor
    width = max(1, right - left)
    share = min(1.0, max(0.0, (cursor_x - left) / width))
    return cursor_x - round(share * size[0]), cursor_y - min(GRAB_HEIGHT, max(0, cursor_y - top))


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.IsZoomed.argtypes = [wintypes.HWND]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

    SW_RESTORE = 9
    SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
    VK_LBUTTON = 0x01

    def _cursor() -> tuple[int, int]:
        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def _rect(hwnd: int) -> tuple[int, int, int, int]:
        rect = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def _held() -> bool:
        return bool(_user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)

    def _scale(hwnd: int) -> float:
        """The screen's scaling, so a minimum size written in 96-dpi pixels holds on a sharper one."""
        try:
            return _user32.GetDpiForWindow(hwnd) / 96
        except (AttributeError, OSError):  # before Windows 10 1607
            return 1.0

    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _user32.CallWindowProcW.restype = _LRESULT
    _user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                        wintypes.LPARAM]

    class _CalcSize(ctypes.Structure):
        """NCCALCSIZE_PARAMS: the window's proposed rectangle first, then two the system keeps for itself."""
        _fields_ = [("rects", wintypes.RECT * 3), ("position", ctypes.c_void_p)]

    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92
    GWL_STYLE, GWLP_WNDPROC = -16, -4
    WM_NCCALCSIZE = 0x0083
    WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
    WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000
    SWP_NOMOVE, SWP_FRAMECHANGED = 0x0002, 0x0020
    _hooks: dict[int, tuple] = {}  # a procedure the system calls into must be kept alive

    class _Margins(ctypes.Structure):
        """MARGINS: how far the system's frame is drawn into the window, side by side."""
        _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int), ("top", ctypes.c_int), ("bottom", ctypes.c_int)]

    def _frame_style(hwnd: int) -> None:
        """The marks of a framed window, and a frame one pixel into the page.

        The system draws a window's shadow, and on Windows 11 its thin edge and round
        corners, only round a frame. With none, a window that did not fill the screen
        had no edge at all and was lost against what lay behind it. The pixel is
        under the page, which covers it."""
        style = _user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        wanted = style | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
        _user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(wanted).value)
        _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        try:
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(1, 1, 1, 1)))
        except (AttributeError, OSError):  # no desktop composition: the window goes without
            pass

    def restore_frame_style(hwnd: int) -> None:
        """Puts the frame's marks back after something took them off: pywebview's whole screen does."""
        _frame_style(hwnd)

    def keep_system_frame_style(hwnd: int) -> None:
        """Gives a window that has no frame the style of one that has, and draws none of it.

        The system animates a window's minimizing, maximizing and restoring, and offers
        the taskbar's button and its menu, only for a window with a caption, a sizing
        frame and the three buttons' marks; one made without them (no frame) jumps
        from state to state. The marks are put back and the window is told, as it is
        asked how much of its area the frame takes, that it takes none, so the page
        has all of it and the frame draws nothing. A maximized window of this style is
        laid out by the system a frame's width past every edge of the screen, so the
        width is taken off again there.
        """
        if hwnd in _hooks:
            return
        chain = [None]

        def window_proc(handle, message, wparam, lparam):
            if message == WM_NCCALCSIZE:
                # Maximized past the screen's edges by a frame's width, it is taken back in; the whole screen
                # (pywebview's, which drops the caption) is the screen itself and keeps every pixel
                captioned = (_user32.GetWindowLongW(handle, GWL_STYLE) & WS_CAPTION) == WS_CAPTION
                if wparam and _user32.IsZoomed(handle) and captioned:
                    rect = ctypes.cast(lparam, ctypes.POINTER(_CalcSize)).contents.rects[0]
                    across = _user32.GetSystemMetrics(SM_CXSIZEFRAME) + _user32.GetSystemMetrics(SM_CXPADDEDBORDER)
                    down = _user32.GetSystemMetrics(SM_CYSIZEFRAME) + _user32.GetSystemMetrics(SM_CXPADDEDBORDER)
                    rect.left += across
                    rect.right -= across
                    rect.top += down
                    rect.bottom -= down
                return 0  # the client area is the whole window
            return _user32.CallWindowProcW(chain[0], handle, message, wparam, lparam)

        proc = _WNDPROC(window_proc)
        chain[0] = _user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(proc, ctypes.c_void_p))
        _hooks[hwnd] = (proc, chain)
        _frame_style(hwnd)

    def is_maximized(hwnd: int) -> bool:
        return bool(_user32.IsZoomed(hwnd))

    def centre(hwnd: int, size: tuple[int, int]) -> None:
        """Sets the window, as it is when not maximized, in the middle of the screen's working area; `size` is in 96-dpi pixels."""
        area = wintypes.RECT()
        _user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        _user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0)  # SPI_GETWORKAREA
        scale = _scale(hwnd)
        left, top, right, bottom = centred((area.left, area.top, area.right, area.bottom),
                                           (round(size[0] * scale), round(size[1] * scale)))
        _user32.SetWindowPos(hwnd, None, left, top, right - left, bottom - top, SWP_NOZORDER | SWP_NOACTIVATE)

    def drag(hwnd: int) -> None:
        """Moves the window with the cursor until the button is let go. A press that
        goes nowhere is left alone, so a double click still reaches the page; a
        maximized window is taken out of it once the cursor really moves."""
        start = _cursor()
        while _held():
            x, y = _cursor()
            if abs(x - start[0]) + abs(y - start[1]) >= GRAB:
                break
            time.sleep(STEP)
        else:
            return
        origin = _rect(hwnd)
        if is_maximized(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
            now = _rect(hwnd)
            corner = restored_corner(origin, _cursor(), (now[2] - now[0], now[3] - now[1]))
            _user32.SetWindowPos(hwnd, None, corner[0], corner[1], 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            origin = _rect(hwnd)
            start = _cursor()
        while _held():
            x, y = _cursor()
            _user32.SetWindowPos(hwnd, None, origin[0] + x - start[0], origin[1] + y - start[1], 0, 0,
                                 SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            time.sleep(STEP)

    def resize(hwnd: int, edge: str, minimum: tuple[int, int]) -> None:
        """Sizes the window from one of its edges or corners until the button is let go."""
        if edge not in EDGES or is_maximized(hwnd):
            return
        scale = _scale(hwnd)
        floor = (round(minimum[0] * scale), round(minimum[1] * scale))
        origin = _rect(hwnd)
        start = _cursor()
        while _held():
            x, y = _cursor()
            left, top, right, bottom = resized(origin, (x - start[0], y - start[1]), edge, floor)
            _user32.SetWindowPos(hwnd, None, left, top, right - left, bottom - top, SWP_NOZORDER | SWP_NOACTIVATE)
            time.sleep(STEP)

else:  # a window with a frame of its own needs none of this
    def keep_system_frame_style(hwnd: int) -> None:
        return None

    def restore_frame_style(hwnd: int) -> None:
        return None

    def is_maximized(hwnd: int) -> bool:
        return False

    def centre(hwnd: int, size: tuple[int, int]) -> None:
        return None

    def drag(hwnd: int) -> None:
        return None

    def resize(hwnd: int, edge: str, minimum: tuple[int, int]) -> None:
        return None


def in_background(function, *args) -> None:
    """Runs a drag or a resize beside the page's call, which must not wait for the button to come up."""
    threading.Thread(target=function, args=args, daemon=True).start()
