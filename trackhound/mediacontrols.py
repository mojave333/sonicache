"""The track that plays, in Windows' own media controls (Windows).

Windows shows what a program plays in the box that comes up with the volume
keys, on the lock screen and in the quick settings, and sends it the keyboard's
media keys: the System Media Transport Controls. The window's browser engine
takes part in it by itself, but it says only "a site is playing media", with
neither the title nor the cover, so the program speaks for itself instead and
the engine's own part is switched off (browser_arguments()).

It is plain WinRT through ctypes, as the taskbar buttons are plain COM: the
controls of a window come from ISystemMediaTransportControlsInterop, the title,
artists and album go into its display updater, the cover by its address on the
program's own little server, and the buttons come back through a delegate
written here. Everything is done on one thread of its own. Elsewhere, and if
any of it fails, MediaControls does nothing.
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
from ctypes import wintypes
from typing import Callable

from .logs import log

SUPPORTED = sys.platform == "win32"

# The buttons, by their number in SystemMediaTransportControlsButton
BUTTONS = {0: "play", 1: "pause", 2: "stop", 6: "next", 7: "previous"}

_PLAYING, _PAUSED, _STOPPED = 3, 4, 2  # MediaPlaybackStatus
_MUSIC = 1  # MediaPlaybackType
_E_NOINTERFACE = 0x80004002 - (1 << 32)
_IID_INTEROP = "{ddb0472d-c911-4a1f-86d9-dc3d71a95f5a}"
_IID_CONTROLS = "{99fa3ff4-1742-42a6-902e-087d41f965ec}"
_IID_MUSIC2 = "{00368462-97d3-44b9-b00f-008afcefaf18}"
_IID_STREAM_REFERENCE = "{857309dc-3fbf-4e7d-986f-ef3b1a07a964}"
_IID_URI_FACTORY = "{44a9796f-723e-4fdf-a218-033e75b0c084}"
_IID_HANDLER = "{0557e996-7b23-5bae-aa81-ea0d671143a4}"  # TypedEventHandler<controls, ButtonPressedEventArgs>
_IID_UNKNOWN = "{00000000-0000-0000-c000-000000000046}"
_IID_AGILE = "{94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90}"

# Places in the tables of methods; 0-5 are IUnknown's and IInspectable's
_RELEASE, _QUERY = 2, 0
_INTEROP_GET_FOR_WINDOW = 6
_PUT_STATUS, _GET_UPDATER, _PUT_ENABLED, _PUT_PLAY, _PUT_STOP, _PUT_PAUSE = 7, 8, 11, 13, 15, 17
_PUT_PREVIOUS, _PUT_NEXT, _ADD_PRESSED, _REMOVE_PRESSED = 25, 27, 32, 33
_UPDATER_PUT_TYPE, _UPDATER_PUT_THUMBNAIL, _UPDATER_GET_MUSIC, _UPDATER_CLEAR, _UPDATER_UPDATE = 7, 11, 12, 16, 17
_MUSIC_PUT_TITLE, _MUSIC_PUT_ALBUM_ARTIST, _MUSIC_PUT_ARTIST, _MUSIC2_PUT_ALBUM = 7, 9, 11, 7
_ARGS_GET_BUTTON = 6
_REFERENCE_FROM_URI, _URI_CREATE = 7, 6

if SUPPORTED:
    class _Guid(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

        def __eq__(self, other):
            return bytes(self) == bytes(other)

    _QUERY_TYPE = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p))
    _COUNT_TYPE = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    _INVOKE_TYPE = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

    class _HandlerTable(ctypes.Structure):
        _fields_ = [("QueryInterface", _QUERY_TYPE), ("AddRef", _COUNT_TYPE), ("Release", _COUNT_TYPE),
                    ("Invoke", _INVOKE_TYPE)]

    class _Handler(ctypes.Structure):
        _fields_ = [("table", ctypes.POINTER(_HandlerTable))]


def browser_arguments() -> str:
    """What the window's engine is started with when the program shows its
    own media controls: the engine's are switched off, else Windows shows
    both, the engine's without a title. The elastic overscroll stays off as
    pywebview has it, since a second --disable-features replaces the first."""
    return "--disable-features=ElasticOverscroll,HardwareMediaKeyHandling" if _available() else ""


def _available() -> bool:
    """Whether this Windows has the controls at all (Windows 8.1 on). Asked
    on a thread of its own: WinRT has to be started on the thread that asks,
    and the main thread is left for the window, which wants another kind."""
    if not SUPPORTED:
        return False
    found = []

    def probe():
        ctypes.windll.combase.RoInitialize(1)
        try:
            _release(_factory("Windows.Media.SystemMediaTransportControls", _IID_INTEROP))
            found.append(True)
        except OSError:
            pass
        finally:
            ctypes.windll.combase.RoUninitialize()

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(5)
    return bool(found)


class MediaControls:
    """The window's entry in Windows' media controls. show() hands over what
    plays (or None) and returns at once; on_button gets "play", "pause",
    "stop", "next" or "previous"."""

    def __init__(self, hwnd: int, on_button: Callable[[str], None]):
        self._hwnd = hwnd
        self._on_button = on_button
        self._jobs: queue.Queue = queue.Queue()
        self._controls = None
        self._token = ctypes.c_int64(0) if SUPPORTED else None
        self._handler = None  # kept alive as long as Windows may call it
        self._shown: dict | None = None
        self._thread = threading.Thread(target=self._run, name="media-controls", daemon=True)
        self._thread.start()

    def show(self, state: dict | None) -> None:
        self._jobs.put(("show", state))

    def close(self) -> None:
        self._jobs.put(("close", None))

    def _run(self) -> None:
        combase = ctypes.windll.combase
        combase.RoInitialize(1)  # the multithreaded apartment: the buttons arrive on Windows' own threads
        try:
            self._controls = self._open()
        except OSError as e:
            log.debug("медиа-кнопки Windows: %s", e)
            self._controls = None
        while True:
            kind, state = self._jobs.get()
            if kind == "close":
                break
            if self._controls is None:
                continue
            try:
                self._show(state)
            except OSError as e:
                log.debug("медиа-кнопки Windows: %s", e)
        if self._controls is not None:
            try:
                _call(self._controls, _PUT_ENABLED, ctypes.c_int)(self._controls, 0)
                _call(self._controls, _REMOVE_PRESSED, ctypes.c_int64)(self._controls, self._token)
            except OSError:
                pass
            _release(self._controls)
            self._controls = None

    def _open(self):
        interop = _factory("Windows.Media.SystemMediaTransportControls", _IID_INTEROP)
        try:
            controls = ctypes.c_void_p()
            get = _call(interop, _INTEROP_GET_FOR_WINDOW, wintypes.HWND, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p))
            get(interop, self._hwnd, ctypes.byref(_guid(_IID_CONTROLS)), ctypes.byref(controls))
        finally:
            _release(interop)
        for method in (_PUT_PLAY, _PUT_PAUSE, _PUT_STOP):
            _call(controls, method, ctypes.c_int)(controls, 1)
        self._handler = _handler(self._pressed)
        _call(controls, _ADD_PRESSED, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int64))(
            controls, ctypes.addressof(self._handler[0]), ctypes.byref(self._token))
        return controls

    def _pressed(self, args) -> None:
        button = ctypes.c_int()
        _call(args, _ARGS_GET_BUTTON, ctypes.POINTER(ctypes.c_int))(args, ctypes.byref(button))
        name = BUTTONS.get(button.value)
        if name:
            self._on_button(name)

    def _show(self, state: dict | None) -> None:
        controls = self._controls
        enabled = _call(controls, _PUT_ENABLED, ctypes.c_int)
        if not state:
            enabled(controls, 0)
            self._shown = None
            return
        enabled(controls, 1)
        _call(controls, _PUT_STATUS, ctypes.c_int)(controls, _PLAYING if state.get("playing") else _PAUSED)
        _call(controls, _PUT_PREVIOUS, ctypes.c_int)(controls, int(bool(state.get("prev", True))))
        _call(controls, _PUT_NEXT, ctypes.c_int)(controls, int(bool(state.get("next", True))))
        track = {key: str(state.get(key) or "") for key in ("title", "artists", "album", "album_artist", "cover")}
        if track == self._shown:
            return
        self._shown = track
        updater = ctypes.c_void_p()
        _call(controls, _GET_UPDATER, ctypes.POINTER(ctypes.c_void_p))(controls, ctypes.byref(updater))
        try:
            _call(updater, _UPDATER_CLEAR)(updater)
            _call(updater, _UPDATER_PUT_TYPE, ctypes.c_int)(updater, _MUSIC)
            music = ctypes.c_void_p()
            _call(updater, _UPDATER_GET_MUSIC, ctypes.POINTER(ctypes.c_void_p))(updater, ctypes.byref(music))
            try:
                _put_text(music, _MUSIC_PUT_TITLE, track["title"])
                _put_text(music, _MUSIC_PUT_ARTIST, track["artists"] or track["album_artist"])
                _put_text(music, _MUSIC_PUT_ALBUM_ARTIST, track["album_artist"])
                music2 = _query(music, _IID_MUSIC2)
                if music2:
                    try:
                        _put_text(music2, _MUSIC2_PUT_ALBUM, track["album"])
                    finally:
                        _release(music2)
            finally:
                _release(music)
            if track["cover"]:
                try:
                    reference = _stream_reference(track["cover"])
                    try:
                        _call(updater, _UPDATER_PUT_THUMBNAIL, ctypes.c_void_p)(updater, reference)
                    finally:
                        _release(reference)
                except OSError as e:
                    log.debug("медиа-кнопки Windows: обложка: %s", e)
            _call(updater, _UPDATER_UPDATE)(updater)
        finally:
            _release(updater)


# WinRT through ctypes

def _guid(text: str):
    guid = _Guid()
    ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(guid))
    return guid


def _call(pointer, index: int, *arguments):
    """Method number index of a WinRT object, callable with the object first."""
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *arguments)(table[index])


def _release(pointer) -> None:
    if pointer:
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[_RELEASE])(pointer)


def _query(pointer, iid: str):
    found = ctypes.c_void_p()
    try:
        _call(pointer, _QUERY, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p))(
            pointer, ctypes.byref(_guid(iid)), ctypes.byref(found))
    except OSError:
        return None
    return found


class _String:
    """An HSTRING for the length of a with block."""

    def __init__(self, text: str):
        self.handle = ctypes.c_void_p()
        units = len(text.encode("utf-16-le")) // 2  # the length in UTF-16, as Windows counts it
        ctypes.windll.combase.WindowsCreateString(ctypes.c_wchar_p(text), units, ctypes.byref(self.handle))

    def __enter__(self):
        return self.handle

    def __exit__(self, *exc):
        ctypes.windll.combase.WindowsDeleteString(self.handle)


def _factory(name: str, iid: str):
    factory = ctypes.c_void_p()
    with _String(name) as class_name:
        result = ctypes.windll.combase.RoGetActivationFactory(class_name, ctypes.byref(_guid(iid)), ctypes.byref(factory))
    if result < 0 or not factory:
        raise OSError(f"no {name}: {result & 0xFFFFFFFF:#x}")
    return factory


def _put_text(pointer, index: int, text: str) -> None:
    with _String(text) as value:
        _call(pointer, index, ctypes.c_void_p)(pointer, value)


def _stream_reference(address: str):
    """A RandomAccessStreamReference to the picture at an address."""
    uris = _factory("Windows.Foundation.Uri", _IID_URI_FACTORY)
    try:
        uri = ctypes.c_void_p()
        with _String(address) as text:
            _call(uris, _URI_CREATE, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(uris, text, ctypes.byref(uri))
    finally:
        _release(uris)
    references = _factory("Windows.Storage.Streams.RandomAccessStreamReference", _IID_STREAM_REFERENCE)
    try:
        reference = ctypes.c_void_p()
        _call(references, _REFERENCE_FROM_URI, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
            references, uri, ctypes.byref(reference))
    finally:
        _release(references)
        _release(uri)
    return reference


def _handler(callback: Callable):
    """A COM object Windows calls with each button press: its table of four
    methods is made of Python functions, kept alive in the returned list."""
    accepted = [_guid(_IID_HANDLER), _guid(_IID_UNKNOWN), _guid(_IID_AGILE)]
    count = [1]

    def query(this, iid, found):
        if any(iid.contents == known for known in accepted):
            found[0] = this
            count[0] += 1
            return 0
        found[0] = None
        return _E_NOINTERFACE

    def add_ref(this):
        count[0] += 1
        return count[0]

    def release(this):
        count[0] -= 1
        return count[0]

    def invoke(this, sender, args):
        try:
            callback(args)
        except Exception as e:  # a press that fails must not take Windows' thread with it
            log.debug("медиа-кнопки Windows: %s", e)
        return 0

    table = _HandlerTable(_QUERY_TYPE(query), _COUNT_TYPE(add_ref), _COUNT_TYPE(release), _INVOKE_TYPE(invoke))
    handler = _Handler(ctypes.pointer(table))
    return [handler, table]
