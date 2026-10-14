"""The library over Wi-Fi as a Subsonic server (sonicache.subsonic), asked as the phone players ask."""

import hashlib
import json
import xml.etree.ElementTree as ElementTree

import pytest

from sonicache import subsonic

NS = "{http://subsonic.org/restapi}"
PASSWORD = "secret23"


def call(api, method, fmt="json", auth=True, http="GET", **params):
    """A method asked as players ask it, the answer parsed: JSON as a dict, XML as an element."""
    sent = {"v": "1.16.1", "c": "test", "f": fmt, **params}
    if auth:
        sent = {"u": "sonicache", "p": PASSWORD, **sent}
    response = api.handle(http, f"/rest/{method}", {key: str(value) for key, value in sent.items()}, {})
    if fmt == "json":
        return json.loads(response.body)["subsonic-response"]
    return ElementTree.fromstring(response.body)


@pytest.fixture
def api():
    return subsonic.Api(None, PASSWORD)


class TestSigningIn:
    def test_ping_answers_ok_in_xml_and_json(self, api):
        answer = call(api, "ping")
        assert (answer["status"], answer["version"], answer["openSubsonic"], answer["type"]) == (
            "ok", "1.16.1", True, "sonicache")
        root = call(api, "ping", fmt="xml")
        assert root.tag == f"{NS}subsonic-response" and root.get("status") == "ok"

    def test_plain_hex_and_token_passwords_sign_in(self, api):
        assert call(api, "ping", auth=False, u="sonicache", p=PASSWORD)["status"] == "ok"
        assert call(api, "ping", auth=False, u="sonicache", p="enc:" + PASSWORD.encode().hex())["status"] == "ok"
        token = hashlib.md5((PASSWORD + "salt1").encode()).hexdigest()
        assert call(api, "ping", auth=False, u="sonicache", t=token, s="salt1")["status"] == "ok"

    def test_wrong_or_missing_credentials_get_nothing(self, api):
        assert call(api, "ping", auth=False)["error"]["code"] == 10
        assert call(api, "ping", auth=False, u="sonicache")["error"]["code"] == 10
        assert call(api, "ping", auth=False, u="sonicache", p="nope")["error"]["code"] == 40
        assert call(api, "ping", auth=False, u="someone", p=PASSWORD)["error"]["code"] == 40
        assert call(api, "ping", auth=False, u="sonicache", p="enc:zz")["error"]["code"] == 40
        assert call(api, "ping", auth=False, u="sonicache", t="0" * 32, s="salt1")["error"]["code"] == 40
        root = call(api, "ping", fmt="xml", auth=False, u="sonicache", p="nope")
        assert root.get("status") == "failed" and root.find(f"{NS}error").get("code") == "40"

    def test_view_suffix_and_post_form_are_the_same_call(self, api):
        assert call(api, "ping.view")["status"] == "ok"
        assert call(api, "ping", http="POST")["status"] == "ok"

    def test_an_unknown_method_is_not_supported(self, api):
        answer = call(api, "createUser")
        assert answer["status"] == "failed" and answer["error"]["code"] == 0

    def test_a_path_outside_rest_is_not_found(self, api):
        assert api.handle("GET", "/elsewhere", {}, {}).status == 404

    def test_license_user_folders_and_extensions(self, api):
        assert call(api, "getLicense")["license"]["valid"] is True
        user = call(api, "getUser", username="sonicache")["user"]
        assert user["username"] == "sonicache" and user["streamRole"] and user["downloadRole"]
        assert call(api, "getMusicFolders")["musicFolders"]["musicFolder"] == [{"id": 1, "name": "Sonicache"}]
        assert {"name": "formPost", "versions": [1]} in call(api, "getOpenSubsonicExtensions")["openSubsonicExtensions"]

    def test_xml_puts_lists_as_repeated_elements(self, api):
        root = call(api, "getMusicFolders", fmt="xml")
        folders = root.find(f"{NS}musicFolders").findall(f"{NS}musicFolder")
        assert [(folder.get("id"), folder.get("name")) for folder in folders] == [("1", "Sonicache")]
        extension = call(api, "getOpenSubsonicExtensions", fmt="xml").find(f"{NS}openSubsonicExtensions")
        assert extension.get("name") == "formPost" and extension.find(f"{NS}versions").text == "1"


def library(root):
    """Two artists, three albums and a single, as the window reads them, with real files to send."""
    def track(entry, number, title, artist="Radiohead", album="", year="2007", genre="Rock", disc=1):
        path = root / entry / f"{number:02d} {title}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ID3" + bytes(50 + number))
        return {"title": title, "artists": artist, "album": album, "album_artist": artist, "number": number,
                "disc": disc, "year": year, "genre": genre, "duration": 200 + number, "bitrate": 320, "format": "mp3",
                "path": str(path), "entry": str(root / entry), "cover": True, "modified": 1_700_000_000 + number}
    tracks = [track("In Rainbows", 1, "15 Step", album="In Rainbows"),
              track("In Rainbows", 2, "Bodysnatchers", album="In Rainbows"),
              track("Kid A", 1, "Everything In Its Right Place", album="Kid A", year="2000", genre="Electronic"),
              track("Discovery", 1, "One More Time", artist="Daft Punk", album="Discovery", year="2001",
                    genre="House; Electronic")]
    single = root / "Daft Punk - Aerodynamic.mp3"
    single.write_bytes(b"ID3" + bytes(10))
    tracks.append({"title": "Aerodynamic", "artists": "Daft Punk", "album": "", "album_artist": "", "number": 0,
                   "disc": 1, "year": "", "genre": "", "duration": 212, "bitrate": 256, "format": "mp3",
                   "path": str(single), "entry": str(single), "cover": False, "modified": 1_600_000_000})

    def entry(path, title, artist, year, genre, album=True, modified=1_700_000_000):
        return {"path": str(path), "title": title, "artist": artist, "year": year, "genre": genre, "album": album,
                "tracks": 1, "size": 1, "modified": modified, "cover": album, "link": "", "expected": 0}
    items = [entry(root / "In Rainbows", "In Rainbows", "Radiohead", "2007", "Rock", modified=1_700_000_300),
             entry(root / "Kid A", "Kid A", "Radiohead", "2000", "Electronic", modified=1_600_000_100),
             entry(root / "Discovery", "Discovery", "Daft Punk", "2001", "House; Electronic", modified=1_650_000_000),
             entry(single, "Aerodynamic", "Daft Punk", "", "", album=False, modified=1_500_000_000)]
    return items, tracks


class FakeSource:
    def __init__(self, root):
        self.items, self.tracks = library(root)
        self.lists = [{"id": "mix", "name": "Mix", "tracks": [{"path": self.tracks[3]["path"]},
                                                              {"path": self.tracks[0]["path"]},
                                                              {"path": str(root / "gone.mp3")}]}]
        self.stars = [str(root / "Kid A")]
        self.counts = {self.tracks[3]["path"]: 5, self.tracks[0]["path"]: 2}
        self.heard = []
        self.reads = 0

    def as_source(self):
        def read():
            self.reads += 1
            return self.items, self.tracks

        def set_favourite(path, on):
            self.stars = [entry for entry in self.stars if entry != path] + ([path] if on else [])
        return subsonic.Source(library=read, playlists=lambda: self.lists, favourites=lambda: self.stars,
                               set_favourite=set_favourite, cover=lambda path: b"\xff\xd8\xffcover:" + path.encode(),
                               listened=self.heard.append, plays=lambda: self.counts, ffmpeg=lambda: None)


@pytest.fixture
def source(tmp_path):
    return FakeSource(tmp_path)


@pytest.fixture
def full(source):
    return subsonic.Api(source.as_source(), PASSWORD)


def names(entries, key="name"):
    return [entry[key] for entry in entries]


class TestLibrary:
    def test_ids_are_stable_and_distinct(self, full, source):
        first = call(full, "getArtists")
        again = call(subsonic.Api(source.as_source(), PASSWORD), "getArtists")
        assert first == again
        artists = [artist for index in first["artists"]["index"] for artist in index["artist"]]
        assert len({artist["id"] for artist in artists}) == 2
        assert all(artist["id"].startswith("ar-") for artist in artists)

    def test_artists_and_indexes_group_by_first_letter(self, full):
        index = call(full, "getArtists")["artists"]["index"]
        assert [(group["name"], names(group["artist"])) for group in index] == [("D", ["Daft Punk"]), ("R", ["Radiohead"])]
        assert [group["artist"][0]["albumCount"] for group in index] == [2, 2]
        classic = call(full, "getIndexes")["indexes"]["index"]
        assert [(group["name"], names(group["artist"])) for group in classic] == [("D", ["Daft Punk"]), ("R", ["Radiohead"])]

    def test_get_artist_and_album_list_their_children(self, full):
        radiohead = call(full, "getArtists")["artists"]["index"][1]["artist"][0]
        artist = call(full, "getArtist", id=radiohead["id"])["artist"]
        assert names(artist["album"]) == ["In Rainbows", "Kid A"]  # newest year first
        album = call(full, "getAlbum", id=artist["album"][0]["id"])["album"]
        assert (album["songCount"], album["duration"], album["year"], album["artistId"]) == (2, 403, 2007, radiohead["id"])
        assert [(song["track"], song["title"], song["albumId"], song["suffix"], song["contentType"])
                for song in album["song"]] == [(1, "15 Step", album["id"], "mp3", "audio/mpeg"),
                                               (2, "Bodysnatchers", album["id"], "mp3", "audio/mpeg")]
        assert album["song"][0]["size"] == 54 and album["song"][0]["coverArt"] == album["id"]
        assert call(full, "getSong", id=album["song"][1]["id"])["song"]["title"] == "Bodysnatchers"

    def test_a_single_is_an_album_of_one(self, full):
        found = call(full, "search3", query="aerodynamic")["searchResult3"]
        assert names(found["album"]) == ["Aerodynamic"] and names(found["song"], "title") == ["Aerodynamic"]
        assert call(full, "getAlbum", id=found["album"][0]["id"])["album"]["songCount"] == 1

    def test_album_lists_by_type(self, full):
        def listed(kind, **more):
            return names(call(full, "getAlbumList2", type=kind, size=10, **more)["albumList2"]["album"])
        assert listed("newest") == ["In Rainbows", "Discovery", "Kid A", "Aerodynamic"]
        assert listed("alphabeticalByName") == ["Aerodynamic", "Discovery", "In Rainbows", "Kid A"]
        assert listed("alphabeticalByArtist") == ["Aerodynamic", "Discovery", "In Rainbows", "Kid A"]
        assert listed("frequent") == ["Discovery", "In Rainbows"]
        assert listed("starred") == ["Kid A"]
        assert listed("byYear", fromYear=2000, toYear=2001) == ["Kid A", "Discovery"]
        assert listed("byGenre", genre="Electronic") == ["Discovery", "Kid A"]
        assert sorted(listed("random")) == ["Aerodynamic", "Discovery", "In Rainbows", "Kid A"]
        assert names(call(full, "getAlbumList2", type="newest", size=2, offset=1)["albumList2"]["album"]) == [
            "Discovery", "Kid A"]
        old = call(full, "getAlbumList", type="alphabeticalByName")["albumList"]["album"]
        assert names(old, "title")[0] == "Aerodynamic" and old[0]["isDir"] is True

    def test_search_finds_artists_albums_and_songs(self, full):
        found = call(full, "search3", query="radio")["searchResult3"]
        assert names(found["artist"]) == ["Radiohead"] and found.get("song", []) == []
        found = call(full, "search3", query="step")["searchResult3"]
        assert names(found["song"], "title") == ["15 Step"]
        everything = call(full, "search3", query="", songCount=100)["searchResult3"]
        assert len(everything["song"]) == 5 and len(everything["album"]) == 4
        assert names(call(full, "search2", query="kid")["searchResult2"]["album"], "title") == ["Kid A"]

    def test_genres_and_songs_by_genre(self, full):
        genres = {genre["value"]: (genre["songCount"], genre["albumCount"])
                  for genre in call(full, "getGenres")["genres"]["genre"]}
        assert genres == {"Rock": (2, 1), "Electronic": (2, 2), "House": (1, 1)}
        songs = call(full, "getSongsByGenre", genre="House")["songsByGenre"]["song"]
        assert names(songs, "title") == ["One More Time"]

    def test_playlists_and_starred(self, full):
        [playlist] = call(full, "getPlaylists")["playlists"]["playlist"]
        assert (playlist["name"], playlist["songCount"]) == ("Mix", 2)  # the file that is gone is left out
        entries = call(full, "getPlaylist", id=playlist["id"])["playlist"]["entry"]
        assert names(entries, "title") == ["One More Time", "15 Step"]
        assert names(call(full, "getStarred2")["starred2"]["album"]) == ["Kid A"]
        assert names(call(full, "getStarred")["starred"]["album"], "title") == ["Kid A"]

    def test_music_directory_walks_artist_album_song(self, full):
        daft = call(full, "getIndexes")["indexes"]["index"][0]["artist"][0]
        folder = call(full, "getMusicDirectory", id=daft["id"])["directory"]
        assert folder["name"] == "Daft Punk" and names(folder["child"], "title") == ["Discovery", "Aerodynamic"]
        assert all(child["isDir"] for child in folder["child"])
        album = call(full, "getMusicDirectory", id=folder["child"][0]["id"])["directory"]
        assert names(album["child"], "title") == ["One More Time"] and album["child"][0]["isDir"] is False

    def test_random_songs(self, full):
        assert len(call(full, "getRandomSongs", size=3)["randomSongs"]["song"]) == 3
        assert names(call(full, "getRandomSongs", genre="House")["randomSongs"]["song"], "title") == ["One More Time"]

    def test_an_unknown_id_is_not_found(self, full):
        assert call(full, "getAlbum", id="al-000000000000")["error"]["code"] == 70
        assert call(full, "getAlbum")["error"]["code"] == 10

    def test_what_there_is_none_of_is_empty(self, full):
        for method, key in [("getNowPlaying", "nowPlaying"), ("getPodcasts", "podcasts"), ("getBookmarks", "bookmarks"),
                            ("getInternetRadioStations", "internetRadioStations"), ("getShares", "shares")]:
            assert call(full, method)[key] == {}
        assert call(full, "getPlayQueue")["status"] == "ok"
        assert call(full, "savePlayQueue", id="x")["status"] == "ok"
        assert call(full, "getScanStatus")["scanStatus"] == {"scanning": False, "count": 5}

    def test_the_index_is_kept_for_a_while(self, full, source, monkeypatch):
        now = [0.0]
        monkeypatch.setattr(subsonic.time, "monotonic", lambda: now[0])
        call(full, "getArtists")
        call(full, "getArtists")
        assert source.reads == 1
        now[0] += subsonic.INDEX_FOR + 1
        call(full, "getArtists")
        assert source.reads == 2


def raw(api, method, headers=None, **params):
    """A method whose answer is a file or a picture, not parsed."""
    sent = {"u": "sonicache", "p": PASSWORD, "v": "1.16.1", "c": "test", **params}
    return api.handle("GET", f"/rest/{method}", {key: str(value) for key, value in sent.items()}, headers or {})


def first_album(api):
    return call(api, "getAlbumList2", type="alphabeticalByName", size=10)["albumList2"]["album"]


class TestFiles:
    def in_rainbows(self, api):
        album = next(album for album in first_album(api) if album["name"] == "In Rainbows")
        return call(api, "getAlbum", id=album["id"])["album"]

    def test_stream_sends_the_file(self, full, source):
        song = self.in_rainbows(full)["song"][0]
        response = raw(full, "stream", id=song["id"])
        assert (response.status, response.content_type, response.path, response.start, response.length) == (
            200, "audio/mpeg", subsonic.Path(source.tracks[0]["path"]), 0, 54)
        assert response.headers["Accept-Ranges"] == "bytes"
        assert raw(full, "download", id=song["id"]).path == response.path

    def test_stream_answers_a_range(self, full):
        song = self.in_rainbows(full)["song"][0]
        response = raw(full, "stream", {"Range": "bytes=2-5"}, id=song["id"])
        assert (response.status, response.start, response.length) == (206, 2, 4)
        assert response.headers["Content-Range"] == "bytes 2-5/54"
        tail = raw(full, "stream", {"Range": "bytes=50-"}, id=song["id"])
        assert (tail.start, tail.length, tail.headers["Content-Range"]) == (50, 4, "bytes 50-53/54")
        assert raw(full, "stream", {"Range": "bytes=90-"}, id=song["id"]).status == 416

    def test_an_unknown_id_reads_no_file(self, full):
        for wanted in ("tr-000000000000", "C:\\Windows\\win.ini", "../../etc/passwd"):
            response = raw(full, "stream", id=wanted, f="json")
            assert response.path is None and json.loads(response.body)["subsonic-response"]["error"]["code"] == 70
        assert raw(full, "getCoverArt", id="C:\\Windows").status == 404

    def test_a_file_gone_since_the_index_was_read_is_not_found(self, full, source):
        song = self.in_rainbows(full)["song"][0]
        subsonic.Path(source.tracks[0]["path"]).unlink()
        response = raw(full, "stream", id=song["id"], f="json")
        assert json.loads(response.body)["subsonic-response"]["error"]["code"] == 70

    def test_cover_art_of_an_album_and_of_a_song(self, full, source):
        album = self.in_rainbows(full)
        picture = raw(full, "getCoverArt", id=album["id"], size=300)
        assert picture.status == 200 and picture.content_type == "image/jpeg"
        assert picture.body.endswith(source.items[0]["path"].encode())
        assert raw(full, "getCoverArt", id=album["song"][1]["id"]).body == picture.body

    def test_star_and_unstar_change_the_favourites(self, full, source):
        album = self.in_rainbows(full)
        assert call(full, "star", albumId=album["id"])["status"] == "ok"
        assert source.items[0]["path"] in source.stars
        assert "In Rainbows" in names(call(full, "getStarred2")["starred2"]["album"])
        call(full, "unstar", id=album["id"])
        assert source.items[0]["path"] not in source.stars
        assert call(full, "star", id=album["song"][0]["id"])["status"] == "ok"  # a song's star is taken, not kept

    def test_scrobble_counts_a_listen(self, full, source):
        song = self.in_rainbows(full)["song"][1]
        call(full, "scrobble", id=song["id"], submission="false")
        assert source.heard == []
        call(full, "scrobble", id=song["id"])
        assert [(heard["path"], heard["title"], heard["album"]) for heard in source.heard] == [
            (source.tracks[1]["path"], "Bodysnatchers", "In Rainbows")]

    def test_start_scan_builds_the_index_again(self, full, source):
        call(full, "getArtists")
        assert call(full, "startScan")["scanStatus"]["count"] == 5
        assert source.reads == 2


FFMPEG = __import__("sonicache.engine.downloader", fromlist=["find_tool"]).find_tool("ffmpeg")


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
def test_stream_as_mp3_is_converted(tmp_path, source):
    import subprocess
    from mutagen.mp3 import MP3

    song_file = tmp_path / "Kid A" / "01 Everything In Its Right Place.mp3"
    opus = song_file.with_suffix(".opus")
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-c:a", "libopus", str(opus)], check=True)
    source.tracks[2]["path"] = str(opus)
    base = source.as_source()
    api = subsonic.Api(subsonic.Source(**{**base.__dict__, "ffmpeg": lambda: FFMPEG}), PASSWORD)
    album = next(album for album in first_album(api) if album["name"] == "Kid A")
    song = call(api, "getAlbum", id=album["id"])["album"]["song"][0]
    assert song["suffix"] == "opus"
    response = raw(api, "stream", id=song["id"], format="mp3", maxBitRate=128)
    assert response.content_type == "audio/mpeg" and response.path is None
    made = tmp_path / "made.mp3"
    made.write_bytes(b"".join(response.stream))
    assert abs(MP3(made).info.bitrate - 128_000) <= 16_000
    assert raw(api, "stream", id=song["id"], format="raw").path == opus


def free_port():
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def fetch(port, method, password=PASSWORD, headers=None, **params):
    import urllib.parse
    import urllib.request

    query = urllib.parse.urlencode({"u": "sonicache", "p": password, "v": "1.16.1", "c": "test", "f": "json", **params})
    request = urllib.request.Request(f"http://127.0.0.1:{port}/rest/{method}?{query}", headers=headers or {})
    with urllib.request.urlopen(request, timeout=10) as answer:
        return answer.status, dict(answer.headers), answer.read()


class TestListener:
    def test_a_real_request_reaches_the_api(self, full):
        listener = subsonic.Listener(full, free_port())
        port = listener.start()
        try:
            status, _, body = fetch(port, "ping.view")
            assert status == 200 and json.loads(body)["subsonic-response"]["status"] == "ok"
            album = json.loads(fetch(port, "getAlbumList2", type="alphabeticalByName")[2])
            song_album = next(entry for entry in album["subsonic-response"]["albumList2"]["album"]
                              if entry["name"] == "In Rainbows")
            song = json.loads(fetch(port, "getAlbum", id=song_album["id"])[2])["subsonic-response"]["album"]["song"][0]
            status, headers, body = fetch(port, "stream", headers={"Range": "bytes=0-2"}, id=song["id"])
            assert (status, body, headers["Content-Range"]) == (206, b"ID3", "bytes 0-2/54")
        finally:
            listener.stop()

    def test_a_post_form_is_read_like_a_query(self, full):
        import urllib.parse
        import urllib.request

        listener = subsonic.Listener(full, free_port())
        port = listener.start()
        try:
            form = urllib.parse.urlencode({"u": "sonicache", "p": PASSWORD, "f": "json", "c": "t", "v": "1.16.1"})
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/rest/ping", data=form.encode(), timeout=10) as answer:
                assert json.loads(answer.read())["subsonic-response"]["status"] == "ok"
        finally:
            listener.stop()

    def test_a_taken_port_moves_to_the_next(self, full):
        import socket

        taken = free_port()
        holder = socket.socket()
        holder.bind(("0.0.0.0", taken))
        holder.listen()
        listener = subsonic.Listener(full, taken)
        try:
            assert listener.start() != taken
        finally:
            listener.stop()
            holder.close()

    def test_local_addresses_are_private_ipv4(self):
        import ipaddress

        for address in subsonic.local_addresses():
            parsed = ipaddress.ip_address(address)
            assert parsed.version == 4 and parsed.is_private and not parsed.is_loopback and not parsed.is_link_local
