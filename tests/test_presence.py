"""What plays, on the Discord profile: the activity, and the talk with Discord."""

import json
import socket
import struct
import threading
import time

import pytest

from trackhound import gui, presence

TRACK = {"title": "Teen Age Riot", "artists": "Sonic Youth", "album": "Daydream Nation",
         "album_artist": "Sonic Youth", "start": 1000.0, "duration": 417.0}


class TestActivity:
    def test_a_track_is_listened_to_with_its_cover_and_bar(self):
        shown = presence.activity(TRACK, "https://covers.test/daydream.jpg")
        assert (shown["type"], shown["details"], shown["state"]) == (2, "Teen Age Riot", "Sonic Youth")
        assert shown["assets"] == {"large_image": "https://covers.test/daydream.jpg", "large_text": "Daydream Nation"}
        assert shown["timestamps"] == {"start": 1_000_000, "end": 1_417_000}
        assert shown["buttons"] == [{"label": "Trackhound", "url": presence.HOMEPAGE}]

    def test_without_a_cover_the_logo_shows(self):
        assert presence.activity(TRACK)["assets"]["large_image"] == presence.LOGO

    def test_a_track_of_unknown_length_has_no_bar(self):
        assert "timestamps" not in presence.activity({**TRACK, "duration": 0})

    def test_texts_are_kept_within_what_discord_takes(self):
        shown = presence.activity({**TRACK, "title": "x" * 300, "artists": "M", "album": ""})
        assert len(shown["details"]) == 128 and shown["details"].endswith("…")
        assert len(shown["state"]) == 2  # one letter is refused, so a blank is added
        assert "large_text" not in shown["assets"]


class FakeDiscord:
    """The Discord client's end of the pipe: says READY and answers each request."""

    def __init__(self):
        self.ours, theirs = socket.socketpair()
        self.ours.settimeout(5)
        self.received = []
        self.arrived = threading.Condition()
        threading.Thread(target=self._serve, args=(theirs,), daemon=True).start()

    def pipe(self, place):
        return presence._Pipe(self.ours.sendall, self.ours.recv, self.ours.close)

    def _serve(self, connection):
        with connection:
            while True:
                try:
                    head = connection.recv(8)
                except OSError:  # the program closed its end
                    return
                if len(head) < 8:
                    return
                op, length = struct.unpack("<II", head)
                data = b""
                while len(data) < length:
                    data += connection.recv(length - len(data))
                message = json.loads(data)
                with self.arrived:
                    self.received.append((op, message))
                    self.arrived.notify_all()
                answer = ({"cmd": "DISPATCH", "evt": "READY", "data": {}} if op == 0
                          else {"cmd": message["cmd"], "evt": None, "nonce": message["nonce"], "data": {}})
                body = json.dumps(answer).encode()
                connection.sendall(struct.pack("<II", 1, len(body)) + body)

    def wait_for(self, count):
        with self.arrived:
            assert self.arrived.wait_for(lambda: len(self.received) >= count, timeout=10)
        return self.received[count - 1]


@pytest.fixture
def discord(monkeypatch):
    fake = FakeDiscord()
    monkeypatch.setattr(presence, "_places", lambda: ["discord-ipc-0"])
    monkeypatch.setattr(presence, "_open", fake.pipe)
    monkeypatch.setattr(presence, "_GAP", 0)
    return fake


class TestPresence:
    def test_the_program_says_hello_then_what_plays_then_that_it_stopped(self, discord):
        covers = []
        shown = presence.Presence(lambda artist, album, title: covers.append((artist, album)) or "https://covers.test/a.jpg")
        shown.show(TRACK)
        assert discord.wait_for(1) == (0, {"v": 1, "client_id": presence.CLIENT_ID})
        op, message = discord.wait_for(2)
        assert (op, message["cmd"]) == (1, "SET_ACTIVITY")
        assert message["args"]["activity"]["details"] == "Teen Age Riot"
        assert message["args"]["activity"]["assets"]["large_image"] == "https://covers.test/a.jpg"
        shown.show({**TRACK, "title": "Silver Rocket"})
        assert discord.wait_for(3)[1]["args"]["activity"]["details"] == "Silver Rocket"
        assert covers == [("Sonic Youth", "Daydream Nation")]  # the album's cover is found once
        shown.show(None)
        assert discord.wait_for(4)[1]["args"]["activity"] is None
        shown.close()

    def test_without_discord_nothing_is_sent_and_nothing_breaks(self, monkeypatch):
        monkeypatch.setattr(presence, "_places", lambda: [])
        shown = presence.Presence()
        shown.show(TRACK)
        time.sleep(0.2)
        assert shown._pipe is None and shown._sent is None
        shown.close()


class FakePresence:
    def __init__(self):
        self.shown = []

    def show(self, track):
        self.shown.append(track)


class TestApi:
    @pytest.fixture
    def api(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gui, "SETTINGS_FILE", tmp_path / ".trackhound.json")
        # Saving the settings sets the proxy, the relay and the language for the
        # whole process, which the other tests must not inherit
        for name in ("use_proxy", "use_relay", "set_language"):
            monkeypatch.setattr(gui, name, lambda *args: None)
        api = gui.Api()
        api._presence = FakePresence()
        return api

    def test_what_plays_goes_to_discord_only_when_asked_for(self, api):
        api.now_playing({"title": "Song", "position": 30, "duration": 200})
        assert api._presence.shown == []
        api.save_settings({**gui._normalize({}), "discord": True})
        (track,) = api._presence.shown  # what already plays is shown at once
        assert track["title"] == "Song" and "position" not in track
        assert abs(track["start"] - (time.time() - 30)) < 5  # the moment it started, for the bar
        api.now_playing(None)
        assert api._presence.shown[-1] is None
        api.now_playing({"title": "Next", "position": 0, "duration": 100})
        api.save_settings({**gui._normalize({}), "discord": False})
        assert api._presence.shown[-1] is None  # turned off: taken down
