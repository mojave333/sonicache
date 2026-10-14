"""A phone over a cable (sonicache.engine.mtp): the paths on top of WPD, tried on a phone kept in memory."""

import contextlib
import itertools

import pytest

from sonicache.engine import devices, mtp
from sonicache.engine.devices import PlaylistSpec, Source
from sonicache.engine.mtp import Entry, MtpTarget


class FakeWpd:
    """What a phone's storage answers over MTP: objects with ids under parents, no paths,
    no overwriting in place, and renaming only if the phone allows it."""

    def __init__(self, renames: bool = True, free: int = 10**9):
        self.ids = itertools.count(1)
        self.entries = {"s1": Entry("s1", "Internal shared storage", True)}
        self.parents: dict[str, str] = {}
        self.data: dict[str, bytes] = {}
        self.renames = renames
        self.free = free
        self.fail_write = None  # the name a write fails on, as an unplugged phone does

    def children(self, parent):
        return [entry for key, entry in self.entries.items() if self.parents.get(key) == parent]

    def _add(self, parent, name, folder, data=b""):
        if any(entry.name == name for entry in self.children(parent)):
            raise AssertionError(f"two objects named {name}: MTP would keep both")
        key = f"o{next(self.ids)}"
        self.entries[key] = Entry(key, name, folder, len(data))
        self.parents[key] = parent
        if not folder:
            self.data[key] = data
        return key

    def make_folder(self, parent, name):
        return self._add(parent, name, True)

    def write(self, parent, name, chunks, size):
        if self.fail_write and name.startswith(self.fail_write):
            raise OSError(0x8007048F, "the device is not connected")
        data = b"".join(chunks)
        assert len(data) == size
        self._add(parent, name, False, data)

    def rename(self, key, name):
        if not self.renames:
            return False
        old = self.entries[key]
        self.entries[key] = Entry(key, name, old.folder, old.size)
        return True

    def remove(self, key):
        if self.children(key):
            raise AssertionError("a folder with something in it was removed")
        del self.entries[key]
        self.parents.pop(key, None)
        self.data.pop(key, None)

    def read(self, key):
        return self.data[key]

    def space(self, storage):
        return self.free, 10**10

    def paths(self):
        """Every object as a path from the storage, folders with a trailing slash."""
        def path(key):
            parts = []
            while key in self.parents:
                parts.append(self.entries[key].name)
                key = self.parents[key]
            return "/".join(reversed(parts))
        return sorted(path(key) + ("/" if entry.folder else "") for key, entry in self.entries.items() if key != "s1")

    def content(self, path):
        for key in self.data:
            if "/".join(self._chain(key)) == path:
                return self.data[key]
        raise KeyError(path)

    def _chain(self, key):
        parts = []
        while key in self.parents:
            parts.append(self.entries[key].name)
            key = self.parents[key]
        return list(reversed(parts))


@pytest.fixture
def phone():
    return FakeWpd()


@pytest.fixture
def target(phone):
    return MtpTarget(phone, "s1")


def make(path, data=b"music"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class TestPaths:
    def test_put_creates_the_folders_on_the_way(self, phone, target, tmp_path):
        size = target.put(make(tmp_path / "a.mp3", b"abc"), "Sonicache/R/A/a.mp3")
        assert size == 3
        assert phone.paths() == ["Music/", "Music/Sonicache/", "Music/Sonicache/R/", "Music/Sonicache/R/A/",
                                 "Music/Sonicache/R/A/a.mp3"]

    def test_exists_and_read_follow_the_path(self, target, tmp_path):
        target.write_bytes("Sonicache/x.json", b"{}")
        assert target.exists("Sonicache/x.json") and target.is_file("Sonicache/x.json")
        assert target.exists("Sonicache") and not target.is_file("Sonicache")
        assert not target.exists("Sonicache/y.json")
        assert target.read_bytes("Sonicache/x.json") == b"{}"
        with pytest.raises(FileNotFoundError):
            target.read_bytes("Sonicache/y.json")

    def test_names_are_matched_without_regard_to_case(self, target):
        target.write_bytes("Sonicache/A.mp3", b"1")
        assert target.exists("sonicache/a.MP3")

    def test_put_replaces_a_file(self, phone, target):
        target.write_bytes("Sonicache/a.mp3", b"old")
        target.write_bytes("Sonicache/a.mp3", b"new")
        assert phone.paths() == ["Music/", "Music/Sonicache/", "Music/Sonicache/a.mp3"]
        assert target.read_bytes("Sonicache/a.mp3") == b"new"

    def test_put_without_rename_writes_under_the_final_name(self, tmp_path):
        phone = FakeWpd(renames=False)
        target = MtpTarget(phone, "s1")
        target.put(make(tmp_path / "a.mp3", b"abc"), "Sonicache/a.mp3")
        target.put(make(tmp_path / "b.mp3", b"de"), "Sonicache/b.mp3")
        assert phone.paths() == ["Music/", "Music/Sonicache/", "Music/Sonicache/a.mp3", "Music/Sonicache/b.mp3"]
        assert target.read_bytes("Sonicache/a.mp3") == b"abc"

    def test_delete_and_prune_stop_at_sonicache(self, phone, target):
        target.write_bytes("Sonicache/R/A/a.mp3", b"1")
        target.delete("Sonicache/R/A/a.mp3")
        target.delete("Sonicache/R/A/gone.mp3")  # nothing there: nothing to do
        target.prune("Sonicache/R/A")
        assert phone.paths() == ["Music/", "Music/Sonicache/"]

    def test_prune_keeps_a_folder_with_something_in_it(self, phone, target):
        target.write_bytes("Sonicache/R/A/a.mp3", b"1")
        target.write_bytes("Sonicache/R/b.mp3", b"2")
        target.delete("Sonicache/R/A/a.mp3")
        target.prune("Sonicache/R/A")
        assert phone.paths() == ["Music/", "Music/Sonicache/", "Music/Sonicache/R/", "Music/Sonicache/R/b.mp3"]

    def test_clean_parts_removes_half_written_files(self, phone, target):
        target.write_bytes("Sonicache/R/a.mp3", b"1")
        folder = next(entry.id for entry in phone.entries.values() if entry.name == "R")
        phone.write(folder, "b.mp3.part", [b"half"], 4)
        target.clean_parts()
        assert phone.paths() == ["Music/", "Music/Sonicache/", "Music/Sonicache/R/", "Music/Sonicache/R/a.mp3"]

    def test_free_is_the_storage_s(self, phone, target):
        phone.free = 1234
        assert target.free() == 1234

    def test_a_file_already_in_music_is_left_alone(self, phone, target):
        music = phone.make_folder("s1", "Music")
        phone.write(music, "mine.mp3", [b"1"], 1)
        target.write_bytes("Sonicache/a.mp3", b"2")
        assert "Music/mine.mp3" in phone.paths()


class TestSyncOntoAPhone:
    def test_a_sync_onto_a_phone_writes_and_a_second_one_keeps(self, phone, target, tmp_path):
        source = make(tmp_path / "lib" / "R" / "a.mp3", b"abc")
        report = devices.run(target, devices.plan(target, [Source(source, "R/a.mp3")],
                                                  [PlaylistSpec("Mix", [source])]))
        assert report.copied == 1 and report.error == ""
        assert phone.content("Music/Sonicache/R/a.mp3") == b"abc"
        assert phone.content("Music/Sonicache/Playlists/Mix.m3u8").decode().splitlines()[-1] == "../R/a.mp3"
        again = devices.plan(MtpTarget(phone, "s1"), [Source(source, "R/a.mp3")], [PlaylistSpec("Mix", [source])])
        assert again.write == [] and again.keep == 1 and again.delete == []

    def test_what_is_no_longer_chosen_leaves_the_phone(self, phone, target, tmp_path):
        source = make(tmp_path / "lib" / "R" / "a.mp3")
        devices.run(target, devices.plan(target, [Source(source, "R/a.mp3")], []))
        devices.run(MtpTarget(phone, "s1"), devices.plan(MtpTarget(phone, "s1"), [], []))
        assert phone.paths() == ["Music/", "Music/Sonicache/", "Music/Sonicache/.sonicache-sync.json"]

    def test_a_device_error_on_mtp_ends_the_run(self, phone, target, tmp_path):
        sources = [Source(make(tmp_path / "lib" / f"{n}.mp3"), f"{n}.mp3") for n in "abc"]
        work = devices.plan(target, sources, [])
        phone.fail_write = "b.mp3"
        report = devices.run(target, work)
        assert report.error and report.copied == 1
        assert list(devices.load_manifest(MtpTarget(phone, "s1"))) == ["Sonicache/a.mp3"]

    def test_a_vanished_source_is_one_failed_file(self, phone, target, tmp_path):
        gone = make(tmp_path / "lib" / "a.mp3")
        sources = [Source(gone, "a.mp3"), Source(make(tmp_path / "lib" / "b.mp3"), "b.mp3")]
        work = devices.plan(target, sources, [])
        gone.unlink()
        report = devices.run(target, work)
        assert report.failed == ["a.mp3"] and report.copied == 1 and report.error == ""


def test_not_on_windows_there_are_no_phones(monkeypatch):
    monkeypatch.setattr(mtp.sys, "platform", "linux")
    assert mtp.find_phones() == []


windows = pytest.mark.skipif(mtp.sys.platform != "win32", reason="Windows Portable Devices")


@windows
class TestCom:
    """The COM wrapping, on what Windows has without a phone plugged in."""

    def test_values_round_trip_a_string_a_guid_and_a_number(self):
        with mtp._com():
            values = mtp._Values()
            try:
                values.set_string(mtp.NAME, "Песни")
                values.set_guid(mtp.CONTENT_TYPE, mtp.TYPE_FOLDER)
                values.set_ulonglong(mtp.SIZE, 5_000_000_000)
                assert values.get_string(mtp.NAME) == "Песни"
                assert values.get_guid(mtp.CONTENT_TYPE) == mtp.TYPE_FOLDER
                assert values.get_ulonglong(mtp.SIZE) == 5_000_000_000
                assert values.get_string(mtp.ORIGINAL_FILE_NAME) is None
            finally:
                values.release()

    def test_collections_take_keys_and_strings(self):
        with mtp._com():
            keys = mtp._keys([mtp.NAME, mtp.SIZE])
            ids = mtp._ids_collection(["o1", "o2", "o3"])
            try:
                assert mtp._count(keys) == 2 and mtp._count(ids) == 3
            finally:
                mtp._release(keys)
                mtp._release(ids)

    def test_find_phones_on_this_machine_is_a_list(self):
        assert isinstance(mtp.find_phones(), list)


class TestFindPhones:
    @pytest.fixture(autouse=True)
    def fresh(self, monkeypatch):
        monkeypatch.setattr(mtp.sys, "platform", "win32")
        monkeypatch.setattr(mtp, "_FOUND", {})

    def storage(self, name="Internal shared storage"):
        return mtp.Storage("s1", name, 6, 10, "PUID1")

    def test_only_mtp_devices_are_listed(self, monkeypatch):
        monkeypatch.setattr(mtp, "_list_devices", lambda: [("p1", "Redmi Note"), ("p2", "D:\\"), ("p3", "Apple iPhone")])
        answers = {"p1": ("MTP: 1.00", [self.storage()]), "p2": ("MSC:", []), "p3": ("PTP: 1.0", [self.storage("DCIM")])}
        monkeypatch.setattr(mtp, "_probe", answers.__getitem__)
        [phone] = mtp.find_phones()
        assert (phone.pnp_id, phone.name, [storage.description for storage in phone.storages]) == (
            "p1", "Redmi Note", ["Internal shared storage"])

    def test_the_list_is_kept_for_a_while(self, monkeypatch):
        calls, now = [], [100.0]
        monkeypatch.setattr(mtp, "_list_devices", lambda: [("p1", "Redmi Note")])
        monkeypatch.setattr(mtp, "_probe", lambda pnp: calls.append(pnp) or ("MTP: 1.00", [self.storage()]))
        monkeypatch.setattr(mtp.time, "monotonic", lambda: now[0])
        mtp.find_phones()
        now[0] += 10
        mtp.find_phones()
        assert calls == ["p1"]
        now[0] += mtp.KEEP_FOR
        mtp.find_phones()
        assert calls == ["p1", "p1"]

    def test_a_device_that_cannot_be_opened_is_left_out(self, monkeypatch):
        monkeypatch.setattr(mtp, "_list_devices", lambda: [("p1", "Redmi Note")])

        def refuse(pnp):
            raise OSError(-2147024891, "Access is denied")
        monkeypatch.setattr(mtp, "_probe", refuse)
        assert mtp.find_phones() == []

    def test_a_locked_phone_is_listed_without_storages(self, monkeypatch):
        monkeypatch.setattr(mtp, "_list_devices", lambda: [("p1", "Redmi Note")])
        monkeypatch.setattr(mtp, "_probe", lambda pnp: ("MTP: 1.00", []))
        [phone] = mtp.find_phones()
        assert phone.storages == []


class TestPhonesAsDevices:
    """A phone's storages in the list of devices, beside the drives, and synced from the window's side."""

    NOT_READY = "Разблокируйте телефон и выберите в уведомлении «Передача файлов»"

    @pytest.fixture
    def phones(self, monkeypatch):
        listed = []
        monkeypatch.setattr(devices, "_drives", lambda: [])
        monkeypatch.setattr(mtp, "find_phones", lambda: listed)
        return listed

    def test_a_phone_s_storages_are_devices(self, phones):
        phones.append(mtp.Phone("p1", "Redmi Note", [mtp.Storage("s1", "Internal shared storage", 6, 10, "P1"),
                                                     mtp.Storage("s2", "SD card", 1, 2, "P2")]))
        found = devices.find_devices([])
        assert [(device.name, device.kind, device.free, device.total, device.note) for device in found] == [
            ("Redmi Note · Internal shared storage", "mtp", 6, 10, ""), ("Redmi Note · SD card", "mtp", 1, 2, "")]
        assert all(device.id.startswith("mtp:") for device in found) and found[0].id != found[1].id
        assert [device.id for device in devices.find_devices([])] == [device.id for device in found]

    def test_a_phone_without_storages_is_listed_as_not_ready(self, phones):
        phones.append(mtp.Phone("p1", "Redmi Note", []))
        [device] = devices.find_devices([])
        assert (device.name, device.kind, device.note) == ("Redmi Note", "mtp", self.NOT_READY)

    def test_the_target_of_a_phone_is_an_mtp_target(self, phones, monkeypatch):
        phones.append(mtp.Phone("p1", "Redmi Note", [mtp.Storage("s1", "Internal shared storage", 6, 10, "P1")]))
        opened = []

        @contextlib.contextmanager
        def session(pnp_id):
            opened.append(pnp_id)
            yield FakeWpd()
        monkeypatch.setattr(mtp, "_session", session)
        [device] = devices.find_devices([])
        with devices.open_target(device) as target:
            assert isinstance(target, MtpTarget) and target.storage_id == "s1"
        assert opened == ["p1"]

    def test_the_target_of_a_drive_is_a_drive_target(self, tmp_path):
        device = devices.Device("folder:x", "x", str(tmp_path), 1, 2, "folder")
        with devices.open_target(device) as target:
            assert isinstance(target, devices.DriveTarget) and target.root == tmp_path

    def test_a_not_ready_phone_cannot_be_synced(self, phones):
        from sonicache import gui

        phones.append(mtp.Phone("p1", "Redmi Note", []))
        api = gui.Api()
        [device] = api.devices()
        assert device["note"] == self.NOT_READY
        assert api.device_plan(device["id"]) == {"error": self.NOT_READY}

    def test_the_window_syncs_onto_a_phone(self, phones, monkeypatch, tmp_path):
        from sonicache import gui

        music = tmp_path / "Music"
        make(music / "R - First (2000)" / "01. a.mp3", b"abc")
        phone = FakeWpd()
        phones.append(mtp.Phone("p1", "Redmi Note", [mtp.Storage("s1", "Internal shared storage", 6, 10, "P1")]))

        @contextlib.contextmanager
        def session(pnp_id):
            yield phone
        monkeypatch.setattr(mtp, "_session", session)
        monkeypatch.setattr(gui, "_load_settings", lambda: {"folder": str(music), "library_folders": []})
        api = gui.Api()
        [device] = api.devices()
        api.save_device(device["id"], {"all": True})
        assert api.device_plan(device["id"])["tracks"] == 1
        assert api.sync_device(device["id"]) is True
        while (event := api._events.get(timeout=10))["type"] != "sync" or event["state"] == "running":
            pass
        assert (event["state"], event["copied"]) == ("done", 1)
        assert phone.content("Music/Sonicache/R - First (2000)/01. a.mp3") == b"abc"


def test_a_phone_that_keeps_a_file_under_another_name_is_a_device_error(tmp_path):
    class Renaming(FakeWpd):
        def write(self, parent, name, chunks, size):
            super().write(parent, f"~{name}", chunks, size)
    target = MtpTarget(Renaming(), "s1")
    with pytest.raises(OSError):
        target.put(make(tmp_path / "a.mp3"), "Sonicache/a.mp3")
