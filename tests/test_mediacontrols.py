"""Windows' own media controls: the engine's part switched off, and the
button handler Windows calls."""

import ctypes
import sys

import pytest

from trackhound import mediacontrols

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows' own controls")


def test_the_engine_leaves_the_controls_to_the_program():
    arguments = mediacontrols.browser_arguments()
    # The engine's media keys go, and pywebview's own switch stays in the same list
    assert arguments == "--disable-features=ElasticOverscroll,HardwareMediaKeyHandling"


class TestHandler:
    def call(self, handler, index, *types):
        table = ctypes.cast(ctypes.addressof(handler[0]), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.HRESULT if index != 2 else ctypes.c_ulong, ctypes.c_void_p, *types)(table[index])

    def test_it_answers_as_the_handler_windows_asks_for_and_nothing_else(self):
        handler = mediacontrols._handler(lambda args: None)
        this = ctypes.addressof(handler[0])
        query = self.call(handler, 0, ctypes.POINTER(mediacontrols._Guid), ctypes.POINTER(ctypes.c_void_p))
        found = ctypes.c_void_p()
        assert query(this, ctypes.byref(mediacontrols._guid(mediacontrols._IID_HANDLER)), ctypes.byref(found)) == 0
        assert found.value == this
        with pytest.raises(OSError):  # IMarshal and the like are not pretended
            query(this, ctypes.byref(mediacontrols._guid("{00000003-0000-0000-c000-000000000046}")), ctypes.byref(found))

    def test_a_press_reaches_the_callback(self):
        pressed = []
        handler = mediacontrols._handler(pressed.append)
        invoke = self.call(handler, 3, ctypes.c_void_p, ctypes.c_void_p)
        assert invoke(ctypes.addressof(handler[0]), None, 1234) == 0
        assert pressed == [1234]


def test_the_buttons_are_named_as_the_player_names_them():
    assert set(mediacontrols.BUTTONS.values()) == {"play", "pause", "stop", "next", "previous"}
