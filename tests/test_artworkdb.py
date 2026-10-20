"""Covers on a classic iPod: ArtworkDB and the .ithmb files (sonicache.engine.artworkdb)."""

import json
import struct
import subprocess

import pytest

from sonicache.engine import artworkdb, ipod, itunesdb
from sonicache.engine.artworkdb import Artwork, Cover
from sonicache.engine.devices import Source
from sonicache.engine.downloader import find_tool

FFMPEG = find_tool("ffmpeg")


@pytest.fixture
def drawn(monkeypatch):
    """Draws each cover as its key's first letter repeated, and counts the drawings."""
    calls = []

    def draw(data, formats, ffmpeg):
        calls.append(data)
        return {format_id: data[:1] * (width * height * 2) for format_id, width, height in formats}

    monkeypatch.setattr(artworkdb, "draw", draw)
    return calls


def tracks(*dbids):
    db = itunesdb.new_database("iPod")
    for dbid in dbids:
        db.add_track(itunesdb.TrackInfo(path=f":iPod_Control:Music:F00:{dbid}.mp3", title=str(dbid), dbid=dbid))
    db.finish()
    return db


def pictures(root):
    """Each picture in ArtworkDB: its id, whose it is, and where each size lies (file, offset)."""
    _, datasets = artworkdb.parse((root / artworkdb.DATABASE).read_bytes())
    found = []
    for item in datasets[1][1]:
        image_id, song_id = struct.unpack_from("<IQ", item, 16)
        name = item.decode("utf-16-le", errors="replace")
        places = []
        offset = struct.unpack_from("<I", item, 4)[0]
        while offset < len(item):
            mhni = offset + 24
            format_id, at = struct.unpack_from("<II", item, mhni + 16)
            places.append((format_id, at))
            offset += struct.unpack_from("<I", item, offset + 8)[0]
        found.append((image_id, song_id, places))
    return found


def links(db):
    """What each track says of its picture: (artwork count, size, has artwork, picture id)."""
    return {track.dbid: (*struct.unpack_from("<HxxI", track.record.header, 0x7C), track.record.header[0xA4],
                         struct.unpack_from("<I", track.record.header, 0x160)[0]) for track in db.tracks}


def test_an_album_shares_one_picture_on_a_classic(tmp_path, drawn):
    db = tracks(11, 12, 13)
    album, single = Cover("a" * 40, 500, b"A"), Cover("b" * 40, 700, b"B")
    Artwork(tmp_path, "classic").write(db, {11: album, 12: album, 13: single}, {11, 12, 13}, "ffmpeg")
    assert len(drawn) == 2
    found = pictures(tmp_path)
    assert [(image_id, song_id) for image_id, song_id, _ in found] == [(0x64, 11), (0x65, 13)]
    # Every size of a picture, at its place in the file of that size
    assert found[1][2] == [(1061, 56 * 56 * 2), (1055, 128 * 128 * 2), (1068, 128 * 128 * 2), (1060, 320 * 320 * 2)]
    assert links(db) == {11: (1, 500, 1, 0x64), 12: (1, 500, 1, 0x64), 13: (1, 700, 1, 0x65)}
    big = (tmp_path / artworkdb.ARTWORK / "F1060_1.ithmb").read_bytes()
    assert big == b"A" * (320 * 320 * 2) + b"B" * (320 * 320 * 2)
    _, datasets = artworkdb.parse((tmp_path / artworkdb.DATABASE).read_bytes())
    assert sorted(struct.unpack_from("<I", item, 16)[0] for item in datasets[3][1]) == [1055, 1060, 1061, 1068]


def test_older_models_list_a_picture_per_track_drawn_once(tmp_path, drawn):
    db = tracks(11, 12)
    album = Cover("a" * 40, 500, b"A")
    Artwork(tmp_path, "video").write(db, {11: album, 12: album}, {11, 12}, "ffmpeg")
    found = pictures(tmp_path)
    assert [(image_id, song_id) for image_id, song_id, _ in found] == [(0x64, 11), (0x65, 12)]
    assert found[0][2] == found[1][2] == [(1028, 0), (1029, 0)]
    assert len(drawn) == 1


def test_a_cover_drawn_once_is_not_drawn_again(tmp_path, drawn):
    album = Cover("a" * 40, 500, b"A")
    Artwork(tmp_path, "classic").write(tracks(11), {11: album}, {11}, "ffmpeg")
    db = tracks(11, 12)
    Artwork(tmp_path, "classic").write(db, {11: album, 12: Cover("b" * 40, 9, b"B")}, {11, 12}, "ffmpeg")
    assert drawn == [b"A", b"B"]
    assert json.loads((tmp_path / artworkdb.STATE).read_text())["slots"] == {"a" * 40: 0, "b" * 40: 1}


def test_the_place_of_a_cover_that_went_is_taken_by_the_next(tmp_path, drawn):
    one, two, three = (Cover(letter * 40, 1, letter.encode()) for letter in "abc")
    Artwork(tmp_path, "classic").write(tracks(1, 2), {1: one, 2: two}, {1, 2}, "ffmpeg")
    Artwork(tmp_path, "classic").write(tracks(2, 3), {2: two, 3: three}, {1, 2, 3}, "ffmpeg")
    slots = json.loads((tmp_path / artworkdb.STATE).read_text())["slots"]
    assert slots == {"b" * 40: 1, "c" * 40: 0}
    assert (tmp_path / artworkdb.ARTWORK / "F1061_1.ithmb").stat().st_size == 2 * 56 * 56 * 2


def test_itunes_pictures_and_files_are_left_alone(tmp_path, drawn):
    """A picture iTunes listed for its own track stays, and the program's go into a file of another number."""
    folder = tmp_path / artworkdb.ARTWORK
    folder.mkdir(parents=True)
    (folder / "F1055_1.ithmb").write_bytes(b"itunes")
    theirs = artworkdb._mhii(0x70, 999, 10, [(1055, 1, 0, 128, 128)])
    mhli = artworkdb._listed(b"mhli", [theirs])
    body = b"".join(artworkdb._sized(artworkdb._header(b"mhsd", {12: ("H", kind)}), inner)
                    for kind, inner in ((1, mhli), (2, artworkdb._listed(b"mhla", [])),
                                        (3, artworkdb._listed(b"mhlf", []))))
    head = artworkdb._header(b"mhfd", {16: ("I", 2), 20: ("I", 3), 28: ("I", 0x70)})
    (folder / "ArtworkDB").write_bytes(artworkdb._sized(head, body))

    db = tracks(11)
    Artwork(tmp_path, "classic").write(db, {11: Cover("a" * 40, 5, b"A")}, {11}, "ffmpeg")
    found = pictures(tmp_path)
    assert [(image_id, song_id) for image_id, song_id, _ in found] == [(0x70, 999), (0x71, 11)]
    assert (folder / "F1055_1.ithmb").read_bytes() == b"itunes"
    assert (folder / "F1055_2.ithmb").is_file()
    assert ":F1055_2.ithmb" in (folder / "ArtworkDB").read_bytes().decode("utf-16-le", errors="replace")


def test_a_track_without_a_cover_says_so(tmp_path, drawn):
    db = tracks(11, 12)
    Artwork(tmp_path, "classic").write(db, {11: Cover("a" * 40, 5, b"A"), 12: None}, {11, 12}, "ffmpeg")
    assert links(db)[12] == (0, 0, 2, 0)


def test_models_without_a_colour_screen_get_nothing(tmp_path, drawn):
    Artwork(tmp_path, "mini").write(tracks(11), {11: Cover("a" * 40, 5, b"A")}, {11}, "ffmpeg")
    assert not (tmp_path / artworkdb.ARTWORK).exists()


def test_an_unreadable_artworkdb_is_left_as_it_is(tmp_path, drawn):
    folder = tmp_path / artworkdb.ARTWORK
    folder.mkdir(parents=True)
    (folder / "ArtworkDB").write_bytes(b"mhfd garbage")
    Artwork(tmp_path, "classic").write(tracks(11), {11: Cover("a" * 40, 5, b"A")}, {11}, "ffmpeg")
    assert (folder / "ArtworkDB").read_bytes() == b"mhfd garbage"


def test_a_synced_album_brings_its_cover(tmp_path, drawn, monkeypatch):
    """The whole way: the picture beside the album in the library reaches the iPod's database."""
    monkeypatch.setattr(ipod, "_usb_serial", lambda root: "")
    root = tmp_path / "pod"
    (root / "iPod_Control" / "iTunes").mkdir(parents=True)
    (root / "iPod_Control" / "Device").mkdir()
    (root / "iPod_Control" / "Device" / "SysInfo").write_text("ModelNumStr: xA002\n", encoding="ascii")
    album = tmp_path / "lib" / "A" / "X"
    album.mkdir(parents=True)
    (album / "cover.jpg").write_bytes(b"Picture")
    sources = []
    for name in ("01 One.mp3", "02 Two.mp3"):
        (album / name).write_bytes(b"music")
        sources.append(Source(album / name, f"A/X/{name}", 1.0))
    aim = ipod.IpodTarget(root, ipod.inspect(root), "IPOD")
    from sonicache.engine import devices

    devices.run(aim, devices.plan(aim, sources, []))
    db = itunesdb.parse((root / ipod.DATABASE).read_bytes())
    assert [links(db)[track.dbid][2] for track in db.tracks] == [1, 1]
    assert drawn == [b"Picture"]


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
def test_ffmpeg_draws_every_size_fitted_into_its_square(tmp_path):
    picture = tmp_path / "wide.png"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:size=300x150",
                    "-frames:v", "1", str(picture)], check=True)
    sizes = artworkdb.draw(picture.read_bytes(), artworkdb.FORMATS["video"], FFMPEG)
    assert {format_id: len(data) for format_id, data in sizes.items()} == {1028: 100 * 100 * 2, 1029: 200 * 200 * 2}
    small = sizes[1028]
    pixel = lambda x, y: struct.unpack_from("<H", small, (y * 100 + x) * 2)[0]  # noqa: E731
    assert pixel(50, 0) == 0 and pixel(50, 99) == 0  # black above and below a picture wider than tall
    assert pixel(50, 50) >> 11 > 28  # red in the middle
    assert artworkdb.draw(b"not a picture", artworkdb.FORMATS["video"], FFMPEG) is None
