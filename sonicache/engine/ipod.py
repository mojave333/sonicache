"""A classic iPod in disk mode: Classic, Video, nano, mini.

It shows up as a drive, but its own firmware plays only what its database
lists (itunesdb.py). This module tells the models apart, by what the iPod
says of itself and by the database already on it, and syncs to the ones
whose database can be written: the older ones need nothing, a Classic and a
nano of the 3rd and 4th generation need it signed with their FireWire GUID.
An iPod with Rockbox plays from folders and is synced as a plain drive.
"""

from __future__ import annotations

import plistlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .i18n import t
from .logs import log

CONTROL = "iPod_Control"
DATABASE = f"{CONTROL}/iTunes/iTunesDB"
_log = log.getChild("ipod")

# Model numbers (ModelNumStr in SysInfo, without its first letter), from libgpod's itdb_device.c
_GENERATIONS = {
    "classic": ("iPod Classic", "B029 B147 B145 B150 B562 B565 C293 C297"),
    "nano3": ("iPod nano 3G", "A978 A980 B261 B249 B253 B257"),
    "nano4": ("iPod nano 4G", "B480 B651 B654 B657 B660 B663 B666 B598 B732 B735 B739 B742 B745 B748 B751 "
                              "B754 B903 B905 B907 B909 B911 B913 B915 B917 B918"),
    "nano5": ("iPod nano 5G", "C027 C031 C034 C037 C040 C043 C046 C049 C050 C060 C062 C064 C066 C068 C070 "
                              "C072 C074 C075"),
    "nano6": ("iPod nano 6G", "C525 C688 C689 C690 C691 C692 C693 C526 C694 C695 C696 C697 C698 C699"),
    "shuffle": ("iPod shuffle", "9724 9725 A546 A947 A949 A951 A953 C167 B225 B233 B231 B227 B228 B229 B518 "
                                "B520 B522 B524 B526 C306 C323 C381 C384 C387 B867 C164 C303 C307 C328 C331 "
                                "C584 C585 C749 C750 C751"),
    "touch": ("iPod touch", "A623 A627 B376 B528 B531 B533 C086 C008 C011 C540 C544 C547"),
    "video": ("iPod video", "A002 A146 A003 A147 A452 A444 A446 A664 A448 A450"),
    "nano": ("iPod nano", "A350 A352 A004 A099 A005 A107 A477 A426 A428 A487 A489 A725 A726 A497"),
    "mini": ("iPod mini", "9160 9436 9435 9434 9437 9800 9802 9804 9806 9801 9803 9805 9807"),
    "photo": ("iPod photo", "A079 A127 9829 9585 9830 9586 S492"),
    "old": ("iPod", "8513 8541 8697 8709 8737 8740 8738 8741 8976 8946 9460 9244 8948 9245 9282 9787 9268 E436"),
}
MODELS = {number: generation for generation, (_, numbers) in _GENERATIONS.items() for number in numbers.split()}
HASH58 = {"classic", "nano3", "nano4"}
LOCKED = {"nano5", "nano6", "touch"}


@dataclass
class IpodInfo:
    model: str  # what to call it
    checksum: str  # "none" or "hash58"
    guid: bytes | None  # the FireWire GUID hash58 is keyed by
    note: str = ""  # why it cannot be synced; nothing when it can


def is_ipod(root: Path) -> bool:
    """An iPod whose own firmware plays: one with Rockbox plays from folders, like any player."""
    return (Path(root) / CONTROL / "iTunes").is_dir() and not (Path(root) / ".rockbox").exists()


def _sysinfo(root: Path) -> dict[str, str]:
    try:
        text = (root / CONTROL / "Device" / "SysInfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {key.strip(): value.strip() for key, _, value in (line.partition(":") for line in text.splitlines())
            if value}


def _extended(root: Path) -> dict:
    try:
        found = plistlib.loads((root / CONTROL / "Device" / "SysInfoExtended").read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}
    return found if isinstance(found, dict) else {}


def _guid(text: str) -> bytes | None:
    text = str(text or "").strip()
    text = text[2:] if text[:2].lower() == "0x" else text
    return bytes.fromhex(text) if re.fullmatch(r"[0-9A-Fa-f]{16}", text) else None


def _database_signs(root: Path) -> str:
    """What the database already on the iPod says of its signing: "hash58", "locked" or ""."""
    try:
        with open(root / DATABASE, "rb") as file:
            head = file.read(0xA0)
    except OSError:
        return ""
    if len(head) < 0xA0 or head[:4] != b"mhbd":
        return ""
    if int.from_bytes(head[0x70:0x72], "little") in (2, 4) or any(head[0x72:0xA0]):
        return "locked"
    return "hash58" if any(head[0x58:0x6C]) else ""


def inspect(root: Path) -> IpodInfo:
    """Which iPod this is, and whether and how its database can be written."""
    root = Path(root)
    info, extended = _sysinfo(root), _extended(root)
    number = re.sub(r"^[A-Za-z](?=[A-Z0-9]{4})", "", info.get("ModelNumStr", ""))[:4].upper()
    generation = MODELS.get(number, "")
    model = _GENERATIONS[generation][0] if generation else "iPod"
    locked = t("Этот iPod не поддерживается: его база защищена подписью, которую нельзя сделать без iTunes")
    itunes = root / CONTROL / "iTunes"
    if generation == "shuffle" or (itunes / "iTunesSD").exists():
        return IpodInfo(_GENERATIONS["shuffle"][0], "none", None, t("iPod shuffle не поддерживается"))
    if generation in LOCKED or (itunes / "iTunesCDB").exists() or (itunes / "iTunes Library.itlp").exists():
        return IpodInfo(model, "none", None, locked)
    version = extended.get("DBVersion")
    signs = _database_signs(root)
    if isinstance(version, int) and version > 3 or signs == "locked":
        return IpodInfo(model, "none", None, locked)
    if not (generation in HASH58 or version == 3 or signs == "hash58"):
        return IpodInfo(model, "none", None)
    guid = _guid(extended.get("FireWireGUID")) or _guid(info.get("FirewireGuid")) or _guid(_usb_serial(root))
    if guid is None:
        return IpodInfo(model, "hash58", None, t("Не удалось узнать FireWire ID этого iPod: без него он не примет базу"))
    return IpodInfo(model, "hash58", guid)


def _usb_serial(root: Path) -> str:
    """The serial number Windows reads from the drive's USB side: an iPod's is its FireWire GUID."""
    match = re.match(r"^([A-Za-z]):[\\/]?$", str(root))
    if sys.platform != "win32" or not match:
        return ""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                       wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                       wintypes.LPVOID]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # No access asked for: the descriptor is readable without opening the volume for reading
    handle = kernel.CreateFileW(f"\\\\.\\{match.group(1)}:", 0, 3, None, 3, 0, None)  # share read+write, open existing
    if handle in (None, wintypes.HANDLE(-1).value):
        return ""
    try:
        query = (ctypes.c_uint32 * 3)(0, 0, 0)  # StorageDeviceProperty, PropertyStandardQuery
        buffer = ctypes.create_string_buffer(1024)
        returned = wintypes.DWORD()
        if not kernel.DeviceIoControl(handle, 0x2D1400, query, ctypes.sizeof(query), buffer, len(buffer),
                                      ctypes.byref(returned), None):  # IOCTL_STORAGE_QUERY_PROPERTY
            return ""
        offset = int.from_bytes(buffer.raw[24:28], "little")  # STORAGE_DEVICE_DESCRIPTOR.SerialNumberOffset
        if not 0 < offset < returned.value:
            return ""
        return buffer.raw[offset:].split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
    finally:
        kernel.CloseHandle(handle)
