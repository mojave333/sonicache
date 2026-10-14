"""The iPod's own database, read and written without an iPod (sonicache.engine.itunesdb)."""

import struct

import pytest

from sonicache.engine import itunesdb
from sonicache.engine.itunesdb import TrackInfo


def info(title, artist="Artist", album="Album", number=1, disc=1, genre="", dbid=None, path=None, **more):
    return TrackInfo(path=path or f":iPod_Control:Music:F00:{title[:5].upper()}.mp3", title=title, artist=artist,
                     album=album, genre=genre, track=number, disc=disc, length=180_000, bitrate=256,
                     samplerate=44100, size=5_000_000, kind="mp3", dbid=dbid or abs(hash(title)) % 2**63,
                     added=1_700_000_000, **more)


def datasets(db):
    return [record.u32(12) for record in db.root.children]


def test_a_new_database_reads_back():
    data = itunesdb.new_database("IPOD 5G").to_bytes()
    db = itunesdb.parse(data)
    assert data[:4] == b"mhbd" and struct.unpack_from("<I", data, 8)[0] == len(data)
    assert datasets(db) == [1, 3, 2, 4, 8, 6, 10, 5]
    assert db.tracks == []
    [master] = db.playlists
    assert master.master and master.name == "IPOD 5G" and master.track_ids == []


def test_tracks_and_playlists_read_back():
    db = itunesdb.new_database("iPod")
    first = db.add_track(info("Airbag", "Radiohead", "OK Computer", 1, genre="Rock"))
    second = db.add_track(info("Paranoid Android", "Radiohead", "OK Computer", 2, albumartist="Radiohead",
                               composer="Yorke", path=":iPod_Control:Music:F01:SNCAAAAA.m4a"))
    assert (first, second) == (52, 53)
    db.add_playlist("Mix", 777, [second, first])
    db.finish()
    back = itunesdb.parse(db.to_bytes())
    airbag, android = back.tracks
    assert (airbag.title, airbag.artist, airbag.album, airbag.genre, airbag.track) == \
        ("Airbag", "Radiohead", "OK Computer", "Rock", 1)
    assert (android.id, android.composer, android.path) == (53, "Yorke", ":iPod_Control:Music:F01:SNCAAAAA.m4a")
    assert android.dbid == info("Paranoid Android").dbid
    for kind in (2, 3):  # the playlists and the podcasts' copy of them
        master, mix = back.playlists_of(kind)
        assert master.master and master.track_ids == [52, 53]
        assert (mix.name, mix.pid, mix.master, mix.track_ids) == ("Mix", 777, False, [53, 52])


def test_a_track_is_written_as_libgpod_writes_it():
    db = itunesdb.new_database("iPod")
    db.add_track(info("Airbag", path=":iPod_Control:Music:F00:SNCABCDE.mp3"))
    mhit = db.tracks[0].record
    assert len(mhit.header) == 0x248
    assert mhit.u32(0x14) == 1  # visible
    assert mhit.header[0x18:0x1C] == b" 3PM"  # "MP3 " as a big-endian number, written little-endian
    assert struct.unpack_from("<H", mhit.header, 0x3E)[0] == 44100
    assert mhit.u32(0xD0) == 1  # audio
    assert mhit.header[0x70:0x78] == mhit.header[0xA8:0xB0]  # dbid twice
    assert struct.unpack_from("<Q", mhit.header, 0x134)[0] == 0x808080808080
    assert mhit.u32(0x168) == 1
    types = [child.u32(12) for child in mhit.children]
    assert types[:4] == [1, 4, 3, 6] and 2 in types
    assert db.tracks[0].strings[6] == "MPEG audio file"


def test_unknown_records_survive_byte_for_byte():
    db = itunesdb.new_database("iPod")
    db.add_track(info("Airbag"))
    db.add_playlist("Mix", 5, [52])
    db.finish()
    # What a newer iTunes may add: a longer track header, an mhod nobody here knows, a dataset of its own
    mhit = db.tracks[0].record
    mhit.header += bytes(range(16))
    struct.pack_into("<I", mhit.header, 4, len(mhit.header))
    mhit.children.append(itunesdb.Record(b"mhod", bytearray(struct.pack("<4sIIIII", b"mhod", 24, 0, 200, 0, 0)),
                                         body=b"\x01\x02\x03\x04whatever"))
    db.root.children.append(itunesdb.Record(b"mhsd", bytearray(struct.pack("<4sIII", b"mhsd", 96, 0, 9) + bytes(80)),
                                            body=b"a genius id, not records"))
    data = db.to_bytes()
    again = itunesdb.parse(data)
    assert again.to_bytes() == data
    assert again.tracks[0].title == "Airbag"


def test_removing_tracks_takes_them_out_of_every_playlist():
    db = itunesdb.new_database("iPod")
    keep = db.add_track(info("Keep", dbid=1))
    gone = db.add_track(info("Gone", dbid=2))
    db.add_playlist("Both", 9, [gone, keep])
    db.finish()
    db.remove_tracks({2})
    db.finish()
    back = itunesdb.parse(db.to_bytes())
    assert [track.title for track in back.tracks] == ["Keep"]
    for kind in (2, 3):
        master, both = back.playlists_of(kind)
        assert master.track_ids == [keep] and both.track_ids == [keep]


def test_removing_playlists():
    db = itunesdb.new_database("iPod")
    track = db.add_track(info("One"))
    db.add_playlist("Ours", 1, [track])
    db.add_playlist("Theirs", 2, [track])
    db.remove_playlists({1})
    db.finish()
    assert [playlist.name for playlist in itunesdb.parse(db.to_bytes()).playlists] == ["iPod", "Theirs"]


def mhod52(db, sort):
    master = db.playlists_of(2)[0].record
    for child in master.children:
        if child.tag == b"mhod" and child.u32(12) in (52, 53) and struct.unpack_from("<I", child.body, 0)[0] == sort:
            yield child


def test_library_index_sorts_like_libgpod():
    db = itunesdb.new_database("iPod")
    db.add_track(info("Zeta", "beta", "B", 2))    # 52, position 0
    db.add_track(info("alpha", "Beta", "A", 1))   # 53, position 1
    db.add_track(info("Gamma", "Alpha", "C", 1))  # 54, position 2
    db.add_track(info("9 Lives", "The Band", "X", 1))  # 55, position 3: sorted as "Band, The"
    db.finish()
    index, jumps = mhod52(itunesdb.parse(db.to_bytes()), 5)  # by artist
    count = struct.unpack_from("<I", index.body, 4)[0]
    assert count == 4
    positions = list(struct.unpack_from(f"<{count}I", index.body, 48))
    # artist, then album, then disc and number, then title; case does not count
    assert positions == [2, 3, 1, 0]
    entries = struct.unpack_from("<I", jumps.body, 4)[0]
    letters = [struct.unpack_from("<HHII", jumps.body, 16 + 12 * n) for n in range(entries)]
    assert letters == [(ord("A"), 0, 0, 1), (ord("B"), 0, 1, 3)]
    title_index, _ = mhod52(db, 3)
    count = struct.unpack_from("<I", title_index.body, 4)[0]
    assert list(struct.unpack_from(f"<{count}I", title_index.body, 48)) == [3, 1, 2, 0]


def test_albums_and_artists_are_rebuilt():
    db = itunesdb.new_database("iPod")
    db.add_track(info("One", "Radiohead", "OK Computer"))
    db.add_track(info("Two", "Radiohead", "OK Computer"))
    db.add_track(info("Three", "Björk", "Post"))
    db.finish()
    back = itunesdb.parse(db.to_bytes())
    [albums] = [record for record in back.root.children if record.u32(12) == 4]
    [artists] = [record for record in back.root.children if record.u32(12) == 8]
    mhla, mhli = albums.children[0], artists.children[0]
    assert (mhla.tag, len(mhla.children), mhli.tag, len(mhli.children)) == (b"mhla", 2, b"mhli", 2)
    album_ids = [track.record.u32(0x120) for track in back.tracks]
    artist_ids = [track.record.u32(0x1E0) for track in back.tracks]
    assert album_ids[0] == album_ids[1] != album_ids[2] and artist_ids[0] == artist_ids[1] != artist_ids[2]
    names = {mhia.u32(16): itunesdb.string_of(mhia.children[0]) for mhia in mhla.children}
    assert names == {album_ids[0]: "OK Computer", album_ids[2]: "Post"}
    assert {itunesdb.string_of(mhii.children[0]) for mhii in mhli.children} == {"Radiohead", "Björk"}


def test_garbage_is_not_a_database():
    for data in (b"", b"not a database at all", b"mhbd" + struct.pack("<II", 244, 10_000) + bytes(100)):
        with pytest.raises(ValueError):
            itunesdb.parse(data)
