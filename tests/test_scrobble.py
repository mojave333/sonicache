"""Listens sent to Last.fm and ListenBrainz, with the services faked."""

import hashlib
import io
import json
import urllib.error
import urllib.parse

import pytest

from trackhound import scrobble

LISTEN = {"artists": "Sonic Youth", "album_artist": "Sonic Youth", "title": "Teen Age Riot",
          "album": "Daydream Nation", "duration": 417, "time": 1_700_000_000}


class Service:
    """Answers what each request asks, and keeps what was asked."""

    def __init__(self):
        self.asked = []
        self.answers = []  # what to answer next: a dict, or an exception

    def __call__(self, request, timeout):
        body = request.data.decode() if request.data else ""
        self.asked.append((request.get_method(), request.full_url, dict(request.header_items()), body))
        answer = self.answers.pop(0) if self.answers else {}
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


def http_error(code, answer=None):
    return urllib.error.HTTPError("https://service.test", code, "error", {}, io.BytesIO(json.dumps(answer or {}).encode()))


@pytest.fixture
def service():
    return Service()


@pytest.fixture
def lastfm(monkeypatch):
    monkeypatch.setattr(scrobble, "LASTFM_KEY", "key")
    monkeypatch.setattr(scrobble, "LASTFM_SECRET", "secret")


class TestListenBrainz:
    def test_a_token_is_checked_then_listens_go_with_it(self, service):
        service.answers = [{"valid": True, "user_name": "mojave"}]
        scrobbler = scrobble.Scrobbler(service)
        assert scrobbler.connect_listenbrainz(" token ") == "mojave"
        assert scrobbler.accounts()["listenbrainz"] == "mojave"
        method, url, headers, _ = service.asked[0]
        assert (method, url, headers["Authorization"]) == ("GET", scrobble.LISTENBRAINZ_API + "validate-token", "Token token")
        scrobbler._data["pending"]["listenbrainz"].append(scrobble._listen(LISTEN))
        scrobbler._flush("listenbrainz")
        payload = json.loads(service.asked[1][3])
        assert payload["listen_type"] == "single"
        (listen,) = payload["payload"]
        assert listen["listened_at"] == LISTEN["time"]
        assert listen["track_metadata"]["artist_name"] == "Sonic Youth"
        assert listen["track_metadata"]["release_name"] == "Daydream Nation"
        assert listen["track_metadata"]["additional_info"]["duration_ms"] == 417_000
        assert scrobbler.accounts()["waiting"] == 0

    def test_a_token_turned_down_is_not_kept(self, service):
        service.answers = [{"valid": False, "message": "Invalid token"}]
        scrobbler = scrobble.Scrobbler(service)
        with pytest.raises(scrobble.Rejected):
            scrobbler.connect_listenbrainz("wrong")
        assert scrobbler.accounts()["listenbrainz"] == ""

    def test_now_playing_carries_no_time(self, service):
        scrobbler = scrobble.Scrobbler(service)
        scrobbler._data["listenbrainz"] = {"token": "t", "name": "m"}
        scrobbler._send_now("listenbrainz", LISTEN)
        payload = json.loads(service.asked[0][3])
        assert payload["listen_type"] == "playing_now" and "listened_at" not in payload["payload"][0]


class TestWaiting:
    def test_a_listen_waits_while_the_service_is_down_and_goes_later(self, service):
        scrobbler = scrobble.Scrobbler(service)
        scrobbler._data["listenbrainz"] = {"token": "t", "name": "m"}
        scrobbler._data["pending"] = {"listenbrainz": [scrobble._listen(LISTEN)]}
        service.answers = [http_error(503)]
        with pytest.raises(scrobble.Unavailable):
            scrobbler._flush("listenbrainz")
        assert scrobbler.accounts()["waiting"] == 1
        scrobbler._flush("listenbrainz")  # up again
        assert scrobbler.accounts()["waiting"] == 0

    def test_what_the_service_calls_malformed_is_dropped(self, service):
        scrobbler = scrobble.Scrobbler(service)
        scrobbler._data["listenbrainz"] = {"token": "t", "name": "m"}
        scrobbler._data["pending"] = {"listenbrainz": [scrobble._listen(LISTEN)]}
        service.answers = [http_error(400, {"error": "bad listen"})]
        scrobbler._flush("listenbrainz")
        assert scrobbler.accounts()["waiting"] == 0

    def test_a_listen_without_an_artist_is_not_queued(self, service):
        scrobbler = scrobble.Scrobbler(service)
        scrobbler._data["listenbrainz"] = {"token": "t", "name": "m"}
        scrobbler.scrobble({**LISTEN, "artists": "", "album_artist": ""})
        assert scrobbler.accounts()["waiting"] == 0

    def test_listens_are_kept_between_runs(self, service, monkeypatch):
        monkeypatch.setattr(scrobble, "LASTFM_KEY", "")
        scrobbler = scrobble.Scrobbler(service)
        scrobbler._data["listenbrainz"] = {"token": "t", "name": "m"}
        scrobbler._start = lambda: None  # no thread: the file is what is looked at
        scrobbler.scrobble(LISTEN)
        assert scrobble.Scrobbler(service).accounts() == {"lastfm": "", "listenbrainz": "m", "lastfm_ready": False,
                                                          "waiting": 1}


class TestLastfm:
    def test_the_signature_is_last_fms_own(self, lastfm):
        fields = {"method": "track.scrobble", "api_key": "key", "sk": "s", "artist[0]": "A", "format": "json"}
        # Every field but format, sorted by name, each name then its value, and the secret last
        text = b"api_keykey" + b"artist[0]A" + b"methodtrack.scrobble" + b"sks" + b"secret"
        assert scrobble._signature(fields) == hashlib.md5(text).hexdigest()

    def test_the_person_allows_the_program_then_listens_go_in_batches(self, service, lastfm):
        scrobbler = scrobble.Scrobbler(service)
        service.answers = [{"token": "tok"}]
        page = scrobbler.lastfm_start()
        assert page == f"{scrobble.LASTFM_AUTH}?api_key=key&token=tok"
        service.answers = [{"error": 14, "message": "not authorized"}]
        assert scrobbler.lastfm_finish() == ""  # not allowed yet
        service.answers = [{"session": {"name": "mojave", "key": "sk"}}]
        assert scrobbler.lastfm_finish() == "mojave"
        scrobbler._data["pending"]["lastfm"] = [scrobble._listen(LISTEN), scrobble._listen({**LISTEN, "title": "Silver Rocket"})]
        scrobbler._flush("lastfm")
        sent = dict(urllib.parse.parse_qsl(service.asked[-1][3]))
        assert sent["method"] == "track.scrobble" and sent["sk"] == "sk"
        assert (sent["track[0]"], sent["track[1]"], sent["timestamp[0]"]) == ("Teen Age Riot", "Silver Rocket", str(LISTEN["time"]))
        signed = {name: value for name, value in sent.items() if name not in ("api_sig", "format")}
        assert sent["api_sig"] == scrobble._signature(signed)
        assert scrobbler.accounts()["waiting"] == 0

    def test_a_session_turned_down_disconnects(self, service, lastfm):
        scrobbler = scrobble.Scrobbler(service)
        scrobbler._data["lastfm"] = {"key": "sk", "name": "m"}
        scrobbler._data["pending"] = {"lastfm": [scrobble._listen(LISTEN)]}
        service.answers = [http_error(403, {"error": 9, "message": "Invalid session key"})]
        with pytest.raises(scrobble.Rejected):
            scrobbler._flush("lastfm")

    def test_without_the_programs_key_there_is_no_last_fm(self, service, monkeypatch):
        monkeypatch.setattr(scrobble, "LASTFM_KEY", "")
        scrobbler = scrobble.Scrobbler(service)
        assert scrobbler.accounts()["lastfm_ready"] is False
        with pytest.raises(scrobble.Rejected):
            scrobbler.lastfm_start()
