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
