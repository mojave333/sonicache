"""Syncing to a classic iPod, with an iPod that is a folder (sonicache.engine.ipod)."""

import json
import plistlib
import re
import struct
import subprocess
import threading

import pytest

from sonicache.engine import devices, ipod, itunesdb
from sonicache.engine.devices import PlaylistSpec, Source
from sonicache.engine.downloader import find_tool

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


# Syncing: the plan and the run of part 1, onto an iPod

OUR_FILE = re.compile(r"^iPod_Control/Music/F\d{2}/SNC[0-9A-Z]{5}\.(mp3|m4a)$")


@pytest.fixture
def lib(tmp_path):
    return tmp_path / "lib"


@pytest.fixture
def pod(tmp_path):
    return make_ipod(tmp_path / "pod")


def song(lib, rel, data=b"music"):
    path = lib / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return Source(path, rel, 1.0)


def target(root):
    return ipod.IpodTarget(root, ipod.inspect(root), "IPOD")


def sync(root, sources, playlists=(), stop=None, progress=None, **options):
    aim = target(root)
    work = devices.plan(aim, sources, list(playlists), **options)
    return devices.run(aim, work, progress=progress, stop=stop), work


def database(root):
    return itunesdb.parse((root / ipod.DATABASE).read_bytes())


def manifest(root):
    return json.loads((root / ipod.MANIFEST_FILE).read_text(encoding="utf-8"))["files"]


def test_sync_puts_files_in_f_folders_and_lists_them(pod, lib):
    one, two = song(lib, "A/X/01 One.mp3"), song(lib, "A/X/02 Two.mp3")
    three = song(lib, "B/Y/Three.m4a")
    report, _ = sync(pod, [one, two, three], [PlaylistSpec("Mix", [two.path, one.path])])
    assert (report.copied, report.error, report.failed) == (3, "", [])
    assert not (pod / "Sonicache").exists()
    files = {dest: entry["file"] for dest, entry in manifest(pod).items() if "file" in entry}
    assert sorted(files) == ["Sonicache/A/X/01 One.mp3", "Sonicache/A/X/02 Two.mp3", "Sonicache/B/Y/Three.m4a"]
    assert all(OUR_FILE.match(file) and (pod / file).read_bytes() == b"music" for file in files.values())
    assert files["Sonicache/B/Y/Three.m4a"].endswith(".m4a")
    db = database(pod)
    by_title = {track.title: track for track in db.tracks}
    assert sorted(by_title) == ["01 One", "02 Two", "Three"]
    assert by_title["01 One"].path == itunesdb.ipod_path(files["Sonicache/A/X/01 One.mp3"])
    master, mix = db.playlists
    assert master.name == "IPOD" and sorted(master.track_ids) == sorted(track.id for track in db.tracks)
    assert (mix.name, mix.track_ids) == ("Mix", [by_title["02 Two"].id, by_title["01 One"].id])


def test_second_sync_writes_nothing(pod, lib):
    sources = [song(lib, "A/X/01 One.mp3"), song(lib, "A/X/02 Two.mp3")]
    sync(pod, sources, [PlaylistSpec("Mix", [sources[0].path])])
    before = manifest(pod)
    work = devices.plan(target(pod), sources, [PlaylistSpec("Mix", [sources[0].path])])
    assert (work.write, work.delete, work.keep, work.conflicts) == ([], [], 2, [])
    devices.run(target(pod), work)
    assert manifest(pod) == before
    assert [playlist.name for playlist in database(pod).playlists] == ["IPOD", "Mix"]
    assert len(database(pod).tracks) == 2


def test_unchosen_tracks_leave_the_database_and_the_disk(pod, lib):
    one, two = song(lib, "A/X/01 One.mp3"), song(lib, "A/X/02 Two.mp3")
    sync(pod, [one, two], [PlaylistSpec("Mix", [two.path])])
    gone = manifest(pod)["Sonicache/A/X/02 Two.mp3"]["file"]
    report, _ = sync(pod, [one])
    assert report.deleted == 2  # the track and its playlist
    assert not (pod / gone).exists()
    db = database(pod)
    assert [track.title for track in db.tracks] == ["01 One"]
    assert [playlist.name for playlist in db.playlists] == ["IPOD"]


def foreign_ipod(pod):
    """An iPod iTunes filled: one song of its own, in a playlist of its own."""
    db = itunesdb.new_database("IPOD")
    theirs = db.add_track(itunesdb.TrackInfo(path=":iPod_Control:Music:F00:ABCD.mp3", title="Theirs", dbid=42))
    db.add_playlist("Their list", 4242, [theirs])
    db.finish()
    (pod / ipod.DATABASE).write_bytes(db.to_bytes())
    (pod / "iPod_Control/Music/F00/ABCD.mp3").write_bytes(b"theirs")
    return theirs


def test_itunes_tracks_and_playlists_survive(pod, lib):
    theirs = foreign_ipod(pod)
    sync(pod, [song(lib, "A/X/01 One.mp3")])
    db = database(pod)
    their_track = next(track for track in db.tracks if track.dbid == 42)
    assert (their_track.id, their_track.title) == (theirs, "Theirs")
    their_list = next(playlist for playlist in db.playlists if playlist.name == "Their list")
    assert their_list.track_ids == [theirs]
    assert len(db.tracks) == 2
    sync(pod, [])
    db = database(pod)
    assert [track.title for track in db.tracks] == ["Theirs"]
    assert [playlist.name for playlist in db.playlists] == ["IPOD", "Their list"]
    assert (pod / "iPod_Control/Music/F00/ABCD.mp3").read_bytes() == b"theirs"


def test_a_forged_file_in_the_manifest_deletes_nothing(pod, lib):
    foreign_ipod(pod)
    sync(pod, [song(lib, "A/X/01 One.mp3")])
    path = pod / ipod.MANIFEST_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"]["Sonicache/A/X/01 One.mp3"]["file"] = "iPod_Control/Music/F00/ABCD.mp3"
    data["files"]["Sonicache/forged.mp3"] = {"source": "", "size": 0, "mtime_ns": 0, "mode": "copy", "bytes": 1,
                                             "file": "../outside.mp3", "dbid": 42}
    path.write_text(json.dumps(data), encoding="utf-8")
    (pod.parent / "outside.mp3").write_bytes(b"keep")
    sync(pod, [])
    assert (pod / "iPod_Control/Music/F00/ABCD.mp3").read_bytes() == b"theirs"
    assert (pod.parent / "outside.mp3").exists()


def test_an_unreadable_database_stops_before_deleting(pod, lib):
    sync(pod, [song(lib, "A/X/01 One.mp3")])
    ours = pod / manifest(pod)["Sonicache/A/X/01 One.mp3"]["file"]
    (pod / ipod.DATABASE).write_bytes(b"garbage")
    report, _ = sync(pod, [])
    assert report.error and report.deleted == 0
    assert ours.exists() and (pod / ipod.DATABASE).read_bytes() == b"garbage"


def test_a_stopped_sync_goes_on(pod, lib):
    sources = [song(lib, f"A/X/0{n} Song.mp3") for n in range(1, 4)]
    stop = threading.Event()

    def progress(done, total, title):
        if done == 1:
            stop.set()

    report, _ = sync(pod, sources, stop=stop, progress=progress)
    assert report.stopped and report.copied == 1
    assert len(database(pod).tracks) == 1  # what was written is listed
    first = manifest(pod)["Sonicache/A/X/01 Song.mp3"]["file"]
    report, work = sync(pod, sources)
    assert (report.copied, work.keep) == (2, 1)
    assert manifest(pod)["Sonicache/A/X/01 Song.mp3"]["file"] == first
    assert len(database(pod).tracks) == 3


def test_opus_without_ffmpeg_is_unplayable(pod, lib):
    opus, mp3 = song(lib, "A/X/01.opus"), song(lib, "A/X/02.mp3")
    work = devices.plan(target(pod), [opus, mp3], [], can_transcode=False)
    assert work.unplayable == 1 and [item.source for item in work.write] == [mp3.path]
    work = devices.plan(target(pod), [opus, mp3], [], can_transcode=True)
    assert work.unplayable == 0
    assert [(item.dest, item.mode) for item in work.write] == [("Sonicache/A/X/01.mp3", "mp3:256"),
                                                                ("Sonicache/A/X/02.mp3", "copy")]


def test_no_covers_or_lyrics_beside_tracks(pod, lib):
    track = song(lib, "A/X/01 One.mp3")
    (lib / "A/X/cover.jpg").write_bytes(b"jpeg")
    (lib / "A/X/01 One.lrc").write_text("[00:01]la", encoding="utf-8")
    work = devices.plan(target(pod), [track], [])
    assert [item.source for item in work.write] == [track.path]


def test_classic_database_is_signed(tmp_path, lib):
    pod = make_ipod(tmp_path / "classic", "xB147", guid=GUID)
    sync(pod, [song(lib, "A/X/01 One.mp3")])
    data = (pod / ipod.DATABASE).read_bytes()
    assert any(data[0x58:0x6C]) and itunesdb.sign(data, bytes.fromhex(GUID)) == data
    assert [track.title for track in itunesdb.parse(data).tracks] == ["01 One"]


def test_play_counts_and_on_the_go_are_kept(pod, lib):
    sync(pod, [song(lib, "A/X/01 One.mp3")])
    itunes = pod / "iPod_Control/iTunes"
    (itunes / "Play Counts").write_bytes(struct.pack("<4sIII", b"mhdp", 0x60, 0x10, 1).ljust(0x60, b"\0")
                                         + struct.pack("<4I", 5, 0, 0, 60))
    (itunes / "OTGPlaylistInfo").write_bytes(struct.pack("<4sIII", b"mhpo", 0x14, 4, 1).ljust(0x14, b"\0")
                                             + struct.pack("<I", 0))
    sync(pod, [song(lib, "A/X/01 One.mp3")])
    db = database(pod)
    assert db.tracks[0].record.u32(0x50) == 5 and db.tracks[0].record.header[0x1F] == 60
    otg = next(playlist for playlist in db.playlists if playlist.name == "On-The-Go 1")
    assert otg.track_ids == [db.tracks[0].id]
    assert not (itunes / "Play Counts").exists() and not (itunes / "OTGPlaylistInfo").exists()


FFMPEG = find_tool("ffmpeg")


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
def test_tags_of_a_real_mp3_reach_the_database(pod, lib):
    path = lib / "A/X/song.mp3"
    path.parent.mkdir(parents=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-b:a", "128k",
                    "-metadata", "title=Airbag", "-metadata", "artist=Radiohead", "-metadata", "album=OK Computer",
                    "-metadata", "track=1/12", "-metadata", "date=1997", str(path)], check=True)
    sync(pod, [Source(path, "A/X/song.mp3", 1.0)])
    track = database(pod).tracks[0]
    assert (track.title, track.artist, track.album, track.track) == ("Airbag", "Radiohead", "OK Computer", 1)
    mhit = track.record
    assert mhit.u32(0x34) == 1997 and 900 <= mhit.u32(0x28) <= 1200 and mhit.u32(0x38) > 0
