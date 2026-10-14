"""Syncing to a classic iPod, with an iPod that is a folder (sonicache.engine.ipod)."""

import plistlib
import struct

import pytest

from sonicache.engine import devices, ipod, itunesdb

GUID = "000A27001A2B3C4D"


def make_ipod(root, model="xA002", guid="", extended=None, folders=3):
    """The folders a classic iPod has in disk mode, with what its SysInfo says."""
    control = root / "iPod_Control"
    (control / "iTunes").mkdir(parents=True)
    for number in range(folders):
        (control / "Music" / f"F{number:02d}").mkdir(parents=True)
    (control / "Device").mkdir()
    lines = [f"ModelNumStr: {model}"] if model else []
    if guid:
        lines.append(f"FirewireGuid: 0x{guid}")
    (control / "Device" / "SysInfo").write_text("\n".join(lines) + "\n", encoding="ascii")
    if extended is not None:
        (control / "Device" / "SysInfoExtended").write_bytes(plistlib.dumps(extended))
    return root


@pytest.fixture(autouse=True)
def no_usb(monkeypatch):
    monkeypatch.setattr(ipod, "_usb_serial", lambda root: "")


def test_video_5g_needs_no_hash(tmp_path):
    found = ipod.inspect(make_ipod(tmp_path, "xA002"))
    assert (found.checksum, found.note) == ("none", "")
    assert "video" in found.model.lower()


def test_classic_from_sysinfo_takes_its_guid(tmp_path):
    found = ipod.inspect(make_ipod(tmp_path, "xB147", guid=GUID))
    assert (found.checksum, found.guid, found.note) == ("hash58", bytes.fromhex(GUID), "")
    assert "classic" in found.model.lower()


def test_sysinfo_extended_db_version(tmp_path):
    found = ipod.inspect(make_ipod(tmp_path / "a", "", extended={"DBVersion": 3, "FireWireGUID": GUID}))
    assert (found.checksum, found.guid, found.note) == ("hash58", bytes.fromhex(GUID), "")
    newer = ipod.inspect(make_ipod(tmp_path / "b", "", extended={"DBVersion": 4, "FireWireGUID": GUID}))
    assert newer.note


def test_an_itunes_signed_database_means_hash58(tmp_path, monkeypatch):
    root = make_ipod(tmp_path, "")
    signed = itunesdb.sign(itunesdb.new_database("iPod").to_bytes(), bytes.fromhex(GUID))
    (root / "iPod_Control/iTunes/iTunesDB").write_bytes(signed)
    monkeypatch.setattr(ipod, "_usb_serial", lambda root: GUID)
    found = ipod.inspect(root)
    assert (found.checksum, found.guid, found.note) == ("hash58", bytes.fromhex(GUID), "")


def test_a_hash72_database_is_not_supported(tmp_path):
    root = make_ipod(tmp_path, "")
    data = bytearray(itunesdb.new_database("iPod").to_bytes())
    struct.pack_into("<H", data, 0x70, 2)
    data[0x72:0x80] = b"\x01" * 14
    (root / "iPod_Control/iTunes/iTunesDB").write_bytes(bytes(data))
    assert ipod.inspect(root).note


def test_classic_without_guid_is_not_ready(tmp_path):
    found = ipod.inspect(make_ipod(tmp_path, "xB147"))
    assert found.checksum == "hash58" and found.guid is None and found.note


def test_nano_5g_and_newer_are_not_supported(tmp_path):
    assert ipod.inspect(make_ipod(tmp_path / "a", "xC027")).note
    newer = make_ipod(tmp_path / "b", "")
    (newer / "iPod_Control/iTunes/iTunesCDB").write_bytes(b"compressed")
    assert ipod.inspect(newer).note


def test_shuffle_is_not_supported(tmp_path):
    assert ipod.inspect(make_ipod(tmp_path, "xC584")).note


def test_an_unknown_model_without_signs_is_taken_as_old(tmp_path):
    found = ipod.inspect(make_ipod(tmp_path, ""))
    assert (found.checksum, found.note) == ("none", "")


def test_find_devices_tells_an_ipod_from_a_drive(tmp_path, monkeypatch):
    stock = make_ipod(tmp_path / "stock", "xA002")
    rockbox = make_ipod(tmp_path / "rockbox", "xA002")
    (rockbox / ".rockbox").mkdir()
    nano = make_ipod(tmp_path / "nano", "xC027")
    plain = tmp_path / "card"
    plain.mkdir()
    monkeypatch.setattr(devices, "_drives", lambda: [(str(stock), "IPOD 5G", 1), (str(rockbox), "ROCK", 2),
                                                     (str(nano), "NANO", 3), (str(plain), "CARD", 4)])
    monkeypatch.setattr(devices, "_phones", lambda: [])
    found = {device.name: device for device in devices.find_devices([])}
    assert (found["IPOD 5G"].kind, found["IPOD 5G"].note) == ("ipod", "")
    assert found["ROCK"].kind == "drive" and found["CARD"].kind == "drive"
    assert found["NANO"].kind == "ipod" and found["NANO"].note


def test_usb_serial_of_a_folder_is_nothing(tmp_path, monkeypatch):
    monkeypatch.undo()  # the real one: a folder is no USB drive
    assert ipod._usb_serial(tmp_path) == ""
