"""An Android phone over a cable, in its "File transfer" mode (MTP).

Windows gives such a phone no drive letter: it is reached through Windows
Portable Devices (WPD), a COM API in which a storage holds objects, each with
an id and a parent, and nothing has a path. MtpTarget is the sync's target
(devices.Target) on top of that: it finds an object by walking the names down
from the storage, creates the folders on the way, and, since MTP cannot write
over a file, deletes the old one and writes the new one beside it under a
.part name before giving it the real one.

The program's files on a phone go into Music/Sonicache, where players look and
where they keep the phone's own folders tidy.

_Wpd is the COM itself, through ctypes as the taskbar buttons are; MtpTarget
only asks it for the few things below, so a phone kept in memory stands in for
it in the tests.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .devices import SYNC_DIR, Target
from .logs import log

_log = log.getChild("mtp")

BASE = "Music"
CHUNK = 256 * 1024  # bytes handed to the phone at a time


@dataclass(frozen=True)
class Entry:
    """An object on the phone, as a folder's listing gives it."""
    id: str
    name: str
    folder: bool
    size: int = 0


class MtpTarget(Target):
    """Where a sync writes on a phone's storage. session answers children(),
    make_folder(), write(), rename(), remove(), read() and space() (_Wpd)."""

    def __init__(self, session, storage_id: str, base: str = BASE):
        self.session = session
        self.storage_id = storage_id
        self.base = base
        self._listings: dict[str, dict[str, Entry]] = {}  # folder id -> its objects by lower-case name
        self._renames = True  # until the phone says it cannot rename

    def __str__(self) -> str:
        return f"MTP {self.storage_id}/{self.base}"

    # Finding objects by their path

    def _listing(self, folder_id: str) -> dict[str, Entry]:
        if folder_id not in self._listings:
            self._listings[folder_id] = {entry.name.casefold(): entry for entry in self.session.children(folder_id)}
        return self._listings[folder_id]

    def _forget(self, folder_id: str) -> None:
        self._listings.pop(folder_id, None)

    def _parts(self, dest: str) -> list[str]:
        return [self.base, *[part for part in dest.split("/") if part]]

    def _folder(self, parts: list[str], create: bool = False) -> str | None:
        """The id of the folder these names lead to from the storage; made on the way when asked."""
        folder = self.storage_id
        for name in parts:
            entry = self._listing(folder).get(name.casefold())
            if entry is None:
                if not create:
                    return None
                made = self.session.make_folder(folder, name)
                self._forget(folder)
                entry = Entry(made, name, True)
            elif not entry.folder:
                raise NotADirectoryError(20, "a file where a folder should be", "/".join(parts))
            folder = entry.id
        return folder

    def _find(self, dest: str) -> tuple[str | None, Entry | None]:
        """The folder that would hold dest, and dest's own object, either None when not there."""
        *above, name = self._parts(dest)
        folder = self._folder(above)
        if folder is None:
            return None, None
        return folder, self._listing(folder).get(name.casefold())

    # The target

    def free(self) -> int:
        return self.session.space(self.storage_id)[0]

    def exists(self, dest: str) -> bool:
        return self._find(dest)[1] is not None

    def is_file(self, dest: str) -> bool:
        entry = self._find(dest)[1]
        return entry is not None and not entry.folder

    def read_bytes(self, dest: str) -> bytes:
        entry = self._find(dest)[1]
        if entry is None or entry.folder:
            raise FileNotFoundError(2, "not on the phone", dest)
        return self.session.read(entry.id)

    def write_bytes(self, dest: str, data: bytes) -> None:
        self._store(dest, lambda: [data], len(data))

    def put(self, local: Path, dest: str) -> int:
        size = Path(local).stat().st_size

        def chunks() -> Iterable[bytes]:
            with open(local, "rb") as file:
                while block := file.read(CHUNK):
                    yield block
        # The file is opened before anything is sent: a library file that went away
        # fails here, naming itself, and not halfway through the phone's write
        with open(local, "rb"):
            pass
        self._store(dest, chunks, size)
        return size

    def _store(self, dest: str, chunks, size: int) -> None:
        """The file under its name, in place of the old one: written whole as .part
        first and then renamed, or, on a phone that cannot rename, straight away."""
        *above, name = self._parts(dest)
        folder = self._folder(above, create=True)
        if self._renames:
            partial = f"{name}.part"
            stale = self._listing(folder).get(partial.casefold())
            if stale is not None:
                self.session.remove(stale.id)
            self.session.write(folder, partial, chunks(), size)
            self._forget(folder)
            written = self._listing(folder)[partial.casefold()]
            old = self._listing(folder).get(name.casefold())
            if old is not None:
                self.session.remove(old.id)
            if self.session.rename(written.id, name):
                self._forget(folder)
                return
            self._renames = False
            self.session.remove(written.id)
            self._forget(folder)
        old = self._listing(folder).get(name.casefold())
        if old is not None:
            self.session.remove(old.id)
        self.session.write(folder, name, chunks(), size)
        self._forget(folder)

    def delete(self, dest: str) -> None:
        folder, entry = self._find(dest)
        if entry is not None and not entry.folder:
            self.session.remove(entry.id)
            self._forget(folder)

    def prune(self, folder: str) -> None:
        parts = [part for part in folder.split("/") if part]
        while len(parts) > 1 and parts[0] == SYNC_DIR:  # Sonicache/ itself stays
            above = self._folder([self.base, *parts[:-1]])
            if above is None:
                return
            entry = self._listing(above).get(parts[-1].casefold())
            if entry is None or not entry.folder or self._listing(entry.id):
                return
            self.session.remove(entry.id)
            self._forget(above)
            self._forget(entry.id)
            parts.pop()

    def clean_parts(self) -> None:
        top = self._folder([self.base, SYNC_DIR])
        if top is None:
            return
        waiting = [top]
        while waiting:
            folder = waiting.pop()
            for entry in list(self._listing(folder).values()):
                if entry.folder:
                    waiting.append(entry.id)
                elif entry.name.endswith(".part"):
                    self.session.remove(entry.id)
                    self._forget(folder)


# The phones plugged in

KEEP_FOR = 15  # seconds a phone's storages are trusted: the window asks every 3, and opening one is slow


@dataclass(frozen=True)
class Storage:
    id: str  # the object id of the storage on its phone
    description: str  # "Internal shared storage", "SD card"
    free: int
    total: int
    persistent_id: str  # the same each time the phone is plugged in


@dataclass(frozen=True)
class Phone:
    pnp_id: str
    name: str
    storages: list[Storage]  # none while it is locked or only charging


_FOUND: dict[str, tuple[float, Phone]] = {}


def find_phones() -> list[Phone]:
    """The phones plugged in by cable in "File transfer" mode, with their storages.
    WPD lists USB sticks (MSC) and iPhones (PTP, photos only) too: only MTP is a phone to sync."""
    if sys.platform != "win32":
        return []
    now = time.monotonic()
    found = []
    try:
        listed = _list_devices()
    except OSError as e:
        _log.warning("устройства MTP не перечислились: %s", e)
        return []
    for pnp_id, name in listed:
        kept = _FOUND.get(pnp_id)
        if kept is not None and now - kept[0] < KEEP_FOR:
            if kept[1] is not None:
                found.append(kept[1])
            continue
        try:
            protocol, storages = _probe(pnp_id)
        except OSError as e:  # busy with another program, or gone while it was asked
            _log.info("устройство %s не открылось: %s", name, e)
            continue
        phone = Phone(pnp_id, name, storages) if protocol.upper().startswith("MTP") else None
        _FOUND[pnp_id] = (now, phone)
        if phone is not None:
            found.append(phone)
    return found


def _list_devices() -> list[tuple[str, str]]:
    """(PnP id, friendly name) of every portable device Windows knows of now."""
    with _com():
        manager = _create(_CLSID_MANAGER, _IID_MANAGER)
        try:
            _method(manager, 4)()  # RefreshDeviceList: a phone plugged in a moment ago
            count = ctypes.c_ulong()
            _method(manager, 3, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))(None, ctypes.byref(count))
            if not count.value:
                return []
            ids = (ctypes.c_void_p * count.value)()
            _method(manager, 3, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))(ids, ctypes.byref(count))
            found = []
            for address in ids[:count.value]:
                pnp_id = _take_string(address)
                found.append((pnp_id, _friendly_name(manager, pnp_id)))
            return found
        finally:
            _release(manager)


def _friendly_name(manager, pnp_id: str) -> str:
    size = ctypes.c_ulong()
    name = _method(manager, 5, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong))
    try:
        name(pnp_id, None, ctypes.byref(size))
        buffer = ctypes.create_unicode_buffer(size.value + 1)
        name(pnp_id, buffer, ctypes.byref(size))
        return buffer.value
    except OSError:
        return pnp_id


def _probe(pnp_id: str) -> tuple[str, list[Storage]]:
    """What a device speaks, and, for a phone, its storages."""
    with _Wpd(pnp_id) as wpd:
        protocol = wpd.string(_DEVICE, PROTOCOL) or ""
        if not protocol.upper().startswith("MTP"):
            return protocol, []
        storages = []
        for object_id in wpd.ids(_DEVICE):
            values = wpd.values(object_id, [CONTENT_TYPE, CATEGORY, DESCRIPTION, FREE, CAPACITY, PERSISTENT_ID])
            try:
                if (values.get_guid(CONTENT_TYPE) != TYPE_FUNCTIONAL
                        or values.get_guid(CATEGORY) != CATEGORY_STORAGE):
                    continue
                storages.append(Storage(object_id, values.get_string(DESCRIPTION) or object_id,
                                        values.get_ulonglong(FREE) or 0, values.get_ulonglong(CAPACITY) or 0,
                                        values.get_string(PERSISTENT_ID) or object_id))
            finally:
                values.release()
        return protocol, storages


# Windows Portable Devices through ctypes. The places of the methods in their
# tables were read from Windows' own type libraries (PortableDeviceApi.dll,
# PortableDeviceTypes.dll), and the GUIDs and property keys checked against
# the bytes of its WPD DLLs.

def _guid(text: str) -> bytes:
    return uuid.UUID(text).bytes_le


_CLSID_MANAGER = _guid("0af10cec-2ecd-4b92-9581-34f6ae0637f3")
_IID_MANAGER = _guid("a1567595-4c2f-4574-a6fa-ecef917b9a40")
_CLSID_DEVICE = _guid("f7c0039a-4762-488a-b4b3-760ef9a1ba9b")  # PortableDeviceFTM: usable from any thread
_IID_DEVICE = _guid("625e2df8-6392-4cf0-9ad1-3cfa5f17775c")
_CLSID_VALUES = _guid("0c15d503-d017-47ce-9016-7b3f978721cc")
_IID_VALUES = _guid("6848f6f2-3155-4f86-b6f5-263eeeab3143")
_CLSID_KEYS = _guid("de2d022d-2480-43be-97f0-d1fa2cf98f4f")
_IID_KEYS = _guid("dada2357-e0ad-492e-98db-dd61c53ba353")
_CLSID_VARIANTS = _guid("08a99e2f-6d6d-4b80-af5a-baf2bcbe4cb9")
_IID_VARIANTS = _guid("89b2e422-4f1b-4316-bcef-a44afea83eb3")

_DEVICE = "DEVICE"  # the object id of the device itself, above its storages
_OBJECT = "EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C"
_STORAGE = "01A3057A-74D6-4E80-BEA7-DC4C212CE50A"
_CLIENT = "204D9F0C-2292-4080-9F42-40664E70F859"
PARENT_ID, NAME, PERSISTENT_ID = (_OBJECT, 3), (_OBJECT, 4), (_OBJECT, 5)
FORMAT, CONTENT_TYPE, SIZE, ORIGINAL_FILE_NAME = (_OBJECT, 6), (_OBJECT, 7), (_OBJECT, 11), (_OBJECT, 12)
CATEGORY = ("8F052D93-ABCA-4FC5-A5AC-B01DF4DBE598", 2)
CAPACITY, FREE, DESCRIPTION = (_STORAGE, 4), (_STORAGE, 5), (_STORAGE, 7)
PROTOCOL = ("26D4979A-E643-4626-9E2B-736DC0C92FDC", 6)
CLIENT_NAME, CLIENT_MAJOR, CLIENT_MINOR, CLIENT_REVISION = (_CLIENT, 2), (_CLIENT, 3), (_CLIENT, 4), (_CLIENT, 5)
RESOURCE_DEFAULT = ("E81E79BE-34F0-41BF-B53F-F1A06AE87842", 0)
TYPE_FOLDER = uuid.UUID("27E2E392-A111-48E0-AB0C-E17705A05F85")
TYPE_FUNCTIONAL = uuid.UUID("99ED0160-17FF-4C44-9D98-1D7A6F941921")
TYPE_FILE = uuid.UUID("0085E0A6-8D34-45D7-BC5C-447E59C73D48")
CATEGORY_STORAGE = uuid.UUID("23F05BBC-15DE-4C2A-A55B-A9AF5CE412EF")
FORMAT_FOLDER = uuid.UUID("30010000-AE6C-4804-98BA-C57B46965FE7")  # WPD_OBJECT_FORMAT_PROPERTIES_ONLY
FORMAT_FILE = uuid.UUID("30000000-AE6C-4804-98BA-C57B46965FE7")  # WPD_OBJECT_FORMAT_UNSPECIFIED
_VT_LPWSTR = 31
_STGM_READ = 0


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", ctypes.c_ubyte * 16), ("pid", ctypes.c_ulong)]


class _Guid(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]


class _PropVariant(ctypes.Structure):
    """VT and three reserved words, then a union as wide as two pointers."""
    _fields_ = [("vt", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3), ("value", ctypes.c_void_p),
                ("more", ctypes.c_void_p)]


def _key(key: tuple[str, int]) -> _PropertyKey:
    fmtid, pid = key
    return _PropertyKey((ctypes.c_ubyte * 16)(*_guid(fmtid)), pid)


def _method(obj, index: int, *argtypes):
    """Method number index of a COM object, callable with the rest of its arguments;
    a failing HRESULT comes back as an OSError."""
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(table[index])
    return lambda *args: function(obj, *args)


def _release(obj) -> None:
    if obj:
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(obj)


def _create(clsid: bytes, iid: bytes) -> ctypes.c_void_p:
    obj = ctypes.c_void_p()
    ctypes.oledll.ole32.CoCreateInstance(clsid, None, 1, iid, ctypes.byref(obj))  # CLSCTX_INPROC_SERVER
    return obj


def _take_string(address) -> str:
    """A string COM allocated for the caller, read and given back."""
    if not address:
        return ""
    text = ctypes.wstring_at(address)
    ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(address))
    return text


@contextlib.contextmanager
def _com():
    """COM on this thread for as long as it is needed: the window's calls and the
    sync each run on a thread of their own."""
    ole32 = ctypes.oledll.ole32
    try:
        ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        owned = True
    except OSError:  # the thread has COM in the other mode already, which serves as well
        owned = False
    try:
        yield
    finally:
        if owned:
            ole32.CoUninitialize()


def _count(collection) -> int:
    count = ctypes.c_ulong()
    _method(collection, 3, ctypes.POINTER(ctypes.c_ulong))(ctypes.byref(count))
    return count.value


def _keys(keys: list[tuple[str, int]]):
    collection = _create(_CLSID_KEYS, _IID_KEYS)
    add = _method(collection, 5, ctypes.POINTER(_PropertyKey))
    for key in keys:
        add(ctypes.byref(_key(key)))
    return collection


def _ids_collection(ids: list[str]):
    collection = _create(_CLSID_VARIANTS, _IID_VARIANTS)
    add = _method(collection, 5, ctypes.POINTER(_PropVariant))
    for object_id in ids:
        text = ctypes.create_unicode_buffer(object_id)
        add(ctypes.byref(_PropVariant(_VT_LPWSTR, (ctypes.c_ushort * 3)(), ctypes.cast(text, ctypes.c_void_p), None)))
    return collection


class _Values:
    """IPortableDeviceValues: properties by key, to send or as they came back."""

    def __init__(self, obj=None):
        self.obj = obj if obj is not None else _create(_CLSID_VALUES, _IID_VALUES)

    def release(self) -> None:
        _release(self.obj)
        self.obj = None

    def set_string(self, key, text: str) -> None:
        _method(self.obj, 7, ctypes.POINTER(_PropertyKey), ctypes.c_wchar_p)(ctypes.byref(_key(key)), text)

    def set_uint(self, key, number: int) -> None:
        _method(self.obj, 9, ctypes.POINTER(_PropertyKey), ctypes.c_ulong)(ctypes.byref(_key(key)), number)

    def set_ulonglong(self, key, number: int) -> None:
        _method(self.obj, 13, ctypes.POINTER(_PropertyKey), ctypes.c_ulonglong)(ctypes.byref(_key(key)), number)

    def set_guid(self, key, value: uuid.UUID) -> None:
        _method(self.obj, 27, ctypes.POINTER(_PropertyKey), ctypes.POINTER(_Guid))(
            ctypes.byref(_key(key)), ctypes.byref(_Guid((ctypes.c_ubyte * 16)(*value.bytes_le))))

    def get_string(self, key) -> str | None:
        out = ctypes.c_void_p()
        try:
            _method(self.obj, 8, ctypes.POINTER(_PropertyKey), ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(_key(key)), ctypes.byref(out))
        except OSError:
            return None
        return _take_string(out.value)

    def get_ulonglong(self, key) -> int | None:
        out = ctypes.c_ulonglong()
        try:
            _method(self.obj, 14, ctypes.POINTER(_PropertyKey), ctypes.POINTER(ctypes.c_ulonglong))(
                ctypes.byref(_key(key)), ctypes.byref(out))
        except OSError:
            return None
        return out.value

    def get_guid(self, key) -> uuid.UUID | None:
        out = _Guid()
        try:
            _method(self.obj, 28, ctypes.POINTER(_PropertyKey), ctypes.POINTER(_Guid))(
                ctypes.byref(_key(key)), ctypes.byref(out))
        except OSError:
            return None
        return uuid.UUID(bytes_le=bytes(out.data))

    def error(self, key) -> int:
        """The HRESULT a device answered for one property of a SetValues; 0 for none."""
        out = ctypes.c_long()
        try:
            _method(self.obj, 20, ctypes.POINTER(_PropertyKey), ctypes.POINTER(ctypes.c_long))(
                ctypes.byref(_key(key)), ctypes.byref(out))
        except OSError:
            return 0
        return out.value


class _Wpd:
    """One phone opened for a while: with _Wpd(pnp_id) as session. The session
    MtpTarget asks for (children, make_folder, write, rename, remove, read, space)."""

    def __init__(self, pnp_id: str):
        self.pnp_id = pnp_id
        self._com = _com()
        self.device = self.content = self.properties = self.resources = None

    def __enter__(self) -> "_Wpd":
        self._com.__enter__()
        try:
            self.device = _create(_CLSID_DEVICE, _IID_DEVICE)
            client = _Values()
            try:
                client.set_string(CLIENT_NAME, "Sonicache")
                client.set_uint(CLIENT_MAJOR, 1)
                client.set_uint(CLIENT_MINOR, 0)
                client.set_uint(CLIENT_REVISION, 0)
                _method(self.device, 3, ctypes.c_wchar_p, ctypes.c_void_p)(self.pnp_id, client.obj)  # Open
            finally:
                client.release()
            self.content = self._out(self.device, 5)  # Content
            self.properties = self._out(self.content, 4)  # Properties
            self.resources = self._out(self.content, 5)  # Transfer
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc) -> None:
        for name in ("resources", "properties", "content"):
            _release(getattr(self, name))
            setattr(self, name, None)
        if self.device:
            try:
                _method(self.device, 8)()  # Close
            except OSError:
                pass
            _release(self.device)
            self.device = None
        self._com.__exit__(None, None, None)

    @staticmethod
    def _out(obj, index: int):
        out = ctypes.c_void_p()
        _method(obj, index, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(out))
        return out

    # What a device holds

    def ids(self, parent: str) -> list[str]:
        """The ids of the objects in a folder, a storage or the device."""
        found = []
        listing = ctypes.c_void_p()
        _method(self.content, 3, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
            0, parent, None, ctypes.byref(listing))
        try:
            batch = (ctypes.c_void_p * 64)()
            fetched = ctypes.c_ulong()
            following = _method(listing, 3, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p * 64),
                                 ctypes.POINTER(ctypes.c_ulong))
            while True:
                following(64, ctypes.byref(batch), ctypes.byref(fetched))
                if not fetched.value:
                    return found
                found += [_take_string(address) for address in batch[:fetched.value]]
        finally:
            _release(listing)

    def values(self, object_id: str, keys: list[tuple[str, int]]) -> _Values:
        collection = _keys(keys)
        out = ctypes.c_void_p()
        try:
            _method(self.properties, 5, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                object_id, collection, ctypes.byref(out))
        finally:
            _release(collection)
        return _Values(out)

    def string(self, object_id: str, key) -> str | None:
        values = self.values(object_id, [key])
        try:
            return values.get_string(key)
        finally:
            values.release()

    def children(self, parent: str) -> list[Entry]:
        found = []
        for object_id in self.ids(parent):
            values = self.values(object_id, [ORIGINAL_FILE_NAME, NAME, CONTENT_TYPE, SIZE])
            try:
                kind = values.get_guid(CONTENT_TYPE)
                found.append(Entry(object_id, values.get_string(ORIGINAL_FILE_NAME) or values.get_string(NAME) or "",
                                   kind in (TYPE_FOLDER, TYPE_FUNCTIONAL), values.get_ulonglong(SIZE) or 0))
            finally:
                values.release()
        return found

    def space(self, storage_id: str) -> tuple[int, int]:
        values = self.values(storage_id, [FREE, CAPACITY])
        try:
            return values.get_ulonglong(FREE) or 0, values.get_ulonglong(CAPACITY) or 0
        finally:
            values.release()

    # Changing it

    def _new_object(self, parent: str, name: str, kind: uuid.UUID, form: uuid.UUID, size: int | None) -> _Values:
        values = _Values()
        values.set_string(PARENT_ID, parent)
        values.set_string(NAME, name)
        values.set_string(ORIGINAL_FILE_NAME, name)
        values.set_guid(CONTENT_TYPE, kind)
        values.set_guid(FORMAT, form)
        if size is not None:
            values.set_ulonglong(SIZE, size)
        return values

    def make_folder(self, parent: str, name: str) -> str:
        values = self._new_object(parent, name, TYPE_FOLDER, FORMAT_FOLDER, None)
        made = ctypes.c_void_p()
        try:
            _method(self.content, 6, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(values.obj, ctypes.byref(made))
        finally:
            values.release()
        return _take_string(made.value)

    def write(self, parent: str, name: str, chunks: Iterable[bytes], size: int) -> None:
        """A new file, sent in pieces; it is there once the stream is committed."""
        values = self._new_object(parent, name, TYPE_FILE, FORMAT_FILE, size)
        stream = ctypes.c_void_p()
        optimal = ctypes.c_ulong()
        try:
            _method(self.content, 7, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_ulong),
                    ctypes.c_void_p)(values.obj, ctypes.byref(stream), ctypes.byref(optimal), None)
        finally:
            values.release()
        try:
            send = _method(stream, 4, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
            written = ctypes.c_ulong()
            for chunk in chunks:
                view = memoryview(chunk)
                while view:
                    buffer = (ctypes.c_char * len(view)).from_buffer_copy(view)
                    send(buffer, len(view), ctypes.byref(written))
                    if not written.value:
                        raise OSError(0x8007001D, "the device took nothing")  # ERROR_WRITE_FAULT
                    view = view[written.value:]
            _method(stream, 8, ctypes.c_ulong)(0)  # Commit: STGC_DEFAULT
        finally:
            _release(stream)

    def rename(self, object_id: str, name: str) -> bool:
        """False when the phone does not let its files be renamed."""
        values = _Values()
        results = ctypes.c_void_p()
        try:
            values.set_string(ORIGINAL_FILE_NAME, name)
            values.set_string(NAME, name)
            _method(self.properties, 6, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                object_id, values.obj, ctypes.byref(results))
        except OSError:
            return False
        finally:
            values.release()
        answers = _Values(results)
        try:
            return answers.error(ORIGINAL_FILE_NAME) >= 0
        finally:
            answers.release()

    def remove(self, object_id: str) -> None:
        collection = _ids_collection([object_id])
        try:
            _method(self.content, 8, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(0, collection, None)
        finally:
            _release(collection)

    def read(self, object_id: str) -> bytes:
        stream = ctypes.c_void_p()
        optimal = ctypes.c_ulong()
        _method(self.resources, 5, ctypes.c_wchar_p, ctypes.POINTER(_PropertyKey), ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p))(
            object_id, ctypes.byref(_key(RESOURCE_DEFAULT)), _STGM_READ, ctypes.byref(optimal), ctypes.byref(stream))
        try:
            receive = _method(stream, 3, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
            buffer = ctypes.create_string_buffer(max(optimal.value, CHUNK))
            got = ctypes.c_ulong()
            parts = []
            while True:
                receive(buffer, len(buffer), ctypes.byref(got))
                if not got.value:
                    return b"".join(parts)
                parts.append(buffer.raw[:got.value])
        finally:
            _release(stream)
