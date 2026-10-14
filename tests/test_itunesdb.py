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


# Signing (hash58: iPod Classic, nano 3G and 4G), and what the firmware itself records

FIXED = bytes.fromhex("6723FE304533F890992107C1D012B2A10781")


def test_hash_tables_are_the_aes_sboxes():
    forward, inverse = itunesdb.SBOX, itunesdb.INVERSE_SBOX
    assert forward[:8] == bytes.fromhex("637C777BF26B6FC5") and forward[-1] == 0x16
    assert inverse[:8] == bytes.fromhex("52096AD53036A538") and inverse[-1] == 0x7D
    assert all(inverse[forward[n]] == n for n in range(256))


def test_the_key_comes_from_the_firewire_guid():
    import hashlib

    # Pairs with a zero count as 1: S-box and inverse of 0 and of 1. lcm(0x0A, 0x27) = 0x0186
    plain = bytes([0x63, 0x52, 0x7C, 0x09])
    assert itunesdb.hash58_key(bytes.fromhex("0001000200030004")) == \
        hashlib.sha1(FIXED + plain * 4).digest() + bytes(44)
    assert itunesdb.hash58_key(bytes.fromhex("0A27000100010001")) == \
        hashlib.sha1(FIXED + bytes([0x7C, 0x09, 0x44, 0xDC]) + plain * 3).digest() + bytes(44)


def zeroed(data):
    data = bytearray(data)
    data[0x18:0x20] = bytes(8)
    data[0x32:0x46] = bytes(20)
    data[0x58:0x6C] = bytes(20)
    return bytes(data)


def test_hash58_is_hmac_sha1_with_the_derived_key():
    import hashlib
    import hmac

    guid = bytes.fromhex("000A27001A2B3C4D")
    db = itunesdb.new_database("Classic")
    db.add_track(info("Airbag"))
    db.finish()
    data = bytearray(db.to_bytes())
    data[0x32:0x46] = b"x" * 20  # whatever iTunes left there stays
    signed = itunesdb.sign(bytes(data), guid)
    assert struct.unpack_from("<H", signed, 0x30)[0] == 1
    assert signed[0x18:0x20] == data[0x18:0x20] and signed[0x32:0x46] == b"x" * 20
    expected = hmac.new(itunesdb.hash58_key(guid)[:20], zeroed(signed), hashlib.sha1).digest()
    assert signed[0x58:0x6C] == expected
    assert itunesdb.sign(signed, guid) == signed


def test_play_counts_and_otg_are_carried_over():
    db = itunesdb.new_database("iPod")
    db.add_track(info("First"))
    db.add_track(info("Second"))
    entries = [(3, 3_700_000_000, 1500, 80, 0, 2, 3_700_000_100), (0, 0, 0, 0, 0, 0, 0)]
    counts = struct.pack("<4sIII", b"mhdp", 0x60, 0x1C, 2).ljust(0x60, b"\0")
    counts += b"".join(struct.pack("<7I", *entry) for entry in entries)
    first = db.tracks[0].record
    first.set_u32(0x50, 4)
    itunesdb.apply_play_counts(db, counts)
    assert first.u32(0x50) == 7 and first.header[0x1F] == 80
    assert (first.u32(0x58), first.u32(0x6C), first.u32(0x9C), first.u32(0xA0)) == \
        (3_700_000_000, 1500, 2, 3_700_000_100)
    assert db.tracks[1].record.u32(0x50) == 0
    otg = struct.pack("<4sIII", b"mhpo", 0x14, 4, 2).ljust(0x14, b"\0") + struct.pack("<II", 1, 0)
    assert itunesdb.otg_indexes(otg) == [1, 0]
    assert [db.track_ids_in_order()[n] for n in itunesdb.otg_indexes(otg)] == [53, 52]
    with pytest.raises(ValueError):
        itunesdb.apply_play_counts(db, b"garbage")


def test_a_track_written_again_keeps_its_place_id_and_listens():
    db = itunesdb.new_database("iPod")
    first = db.put_track(info("Old title", dbid=7))
    other = db.put_track(info("Other", dbid=8))
    db.add_playlist("Made on the iPod", 3, [first])
    db.tracks[0].record.set_u32(0x50, 9)  # listened to nine times
    again = db.put_track(info("New title", dbid=7))
    db.finish()
    back = itunesdb.parse(db.to_bytes())
    assert again == first and [track.title for track in back.tracks] == ["New title", "Other"]
    assert back.tracks[0].record.u32(0x50) == 9 and other == 53
    assert back.playlists[1].track_ids == [first]


def test_tracks_outside_the_main_list_stay_outside():
    """iTunes keeps podcasts out of the main list: they must not turn up among the songs."""
    db = itunesdb.new_database("iPod")
    podcast = db.add_track(info("Episode", dbid=1))
    song = db.add_track(info("Song", dbid=2))
    db.finish()
    for kind in (2, 3):
        master = db.playlists_of(kind)[0].record
        master.children = [child for child in master.children if child.tag != b"mhip" or child.u32(0x18) != podcast]
    back = itunesdb.parse(db.to_bytes())  # as iTunes would have left it
    added = back.add_track(info("New", dbid=3))
    back.finish()
    again = itunesdb.parse(back.to_bytes())
    assert [track.id for track in again.tracks] == [song, added, podcast]  # the main list's first, as libgpod has it
    for kind in (2, 3):
        assert again.playlists_of(kind)[0].track_ids == [song, added]
    index, _ = mhod52(again, 3)
    assert struct.unpack_from("<I", index.body, 4)[0] == 2
