"""The library over Wi-Fi, as a Subsonic server.

An iPhone lets no program on the computer reach its files, and an Android
phone without a cable is out of reach as well. What both can do is play from a
music server on the home network and keep albums for when there is none: the
Subsonic API is the language of such servers (Navidrome, Airsonic, Gonic), and
free players speak it on both — Amperfy on the iPhone, Ultrasonic on Android.

This module answers that API from the library as the window reads it. It knows
nothing of the window: what it serves comes through a Source of callables
(gui.py makes one). Api answers one request without a socket, so the tests ask
it directly; Listener is the HTTP server in front of it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import __version__

VERSION = "1.16.1"
USER = "sonicache"
NAMESPACE = "http://subsonic.org/restapi"

# Subsonic's error codes
GENERIC, MISSING, WRONG_CREDENTIALS, NOT_FOUND = 0, 10, 40, 70


@dataclass
class Response:
    status: int
    content_type: str
    body: bytes | None = None
    path: Path | None = None  # a file to send whole or by the range asked for
    stream: Iterable[bytes] | None = None  # sent as it comes, with no length
    headers: dict[str, str] = field(default_factory=dict)


class Failure(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def check_credentials(params: dict[str, str], user: str, password: str) -> int | None:
    """Subsonic's error code for the credentials of a request, None when they are right.
    A password comes as it is, as "enc:" and its UTF-8 in hex, or as a token: md5 of it and a salt."""
    name = params.get("u")
    if not name:
        return MISSING
    if params.get("t") and params.get("s"):
        expected = hashlib.md5((password + params["s"]).encode("utf-8")).hexdigest()
        right = hmac.compare_digest(expected.encode(), params["t"].lower().encode("utf-8"))
    elif params.get("p"):
        given = params["p"]
        if given.startswith("enc:"):
            try:
                given = bytes.fromhex(given[4:]).decode("utf-8")
            except ValueError:
                return WRONG_CREDENTIALS
        right = hmac.compare_digest(given.encode("utf-8"), password.encode("utf-8"))
    else:
        return MISSING
    right = hmac.compare_digest(name.encode("utf-8"), user.encode("utf-8")) and right
    return None if right else WRONG_CREDENTIALS


def render(payload: dict, fmt: str, status: str = "ok") -> tuple[bytes, str]:
    """The answer in the envelope Subsonic wraps everything in: XML unless JSON is asked for."""
    head = {"status": status, "version": VERSION, "type": "sonicache", "serverVersion": __version__,
            "openSubsonic": True}
    if fmt == "json":
        return json.dumps({"subsonic-response": {**head, **payload}}, ensure_ascii=False).encode("utf-8"), \
            "application/json; charset=utf-8"
    root = ElementTree.Element("subsonic-response", {"xmlns": NAMESPACE, **_attributes(head)})
    for key, value in payload.items():
        _element(root, key, value)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True), "text/xml; charset=utf-8"


def _attributes(values: dict) -> dict[str, str]:
    return {key: _text(value) for key, value in values.items() if not isinstance(value, (dict, list))}


def _text(value) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def _element(parent, tag: str, value) -> None:
    """A dict as an element with its plain values as attributes; a list as the element repeated."""
    if isinstance(value, list):
        for each in value:
            _element(parent, tag, each)
        return
    if not isinstance(value, dict):
        ElementTree.SubElement(parent, tag, {"value": _text(value)})
        return
    child = ElementTree.SubElement(parent, tag, _attributes(value))
    for key, inner in value.items():
        if isinstance(inner, (dict, list)):
            _element(child, key, inner)


class Api:
    """Answers one Subsonic request from what source gives (gui.py's library)."""

    def __init__(self, source, password: str, user: str = USER):
        self.source = source
        self.password = password
        self.user = user

    def handle(self, method: str, path: str, params: dict[str, str], headers: dict[str, str]) -> Response:
        if not path.startswith("/rest/"):
            return Response(404, "text/plain", b"not found")
        name = path.rsplit("/", 1)[-1].removesuffix(".view")
        fmt = "json" if params.get("f") in ("json", "jsonp") else "xml"
        error = check_credentials(params, self.user, self.password)
        if error is not None:
            message = "Required parameter is missing" if error == MISSING else "Wrong username or password"
            return self._failed(Failure(error, message), fmt)
        answer = getattr(self, f"_m_{name}", None)
        if answer is None:
            return self._failed(Failure(GENERIC, f"{name} is not supported"), fmt)
        try:
            result = answer(params)
        except Failure as failure:
            return self._failed(failure, fmt)
        if isinstance(result, Response):
            return result
        body, kind = render(result, fmt)
        return Response(200, kind, body)

    def _failed(self, failure: Failure, fmt: str) -> Response:
        body, kind = render({"error": {"code": failure.code, "message": str(failure)}}, fmt, status="failed")
        return Response(200, kind, body)

    # Methods with nothing of the library in them

    def _m_ping(self, params):
        return {}

    def _m_getLicense(self, params):
        return {"license": {"valid": True, "email": "", "licenseExpires": "2099-12-31T00:00:00"}}

    def _m_getUser(self, params):
        return {"user": {"username": self.user, "email": "", "scrobblingEnabled": True, "adminRole": False,
                         "settingsRole": False, "downloadRole": True, "uploadRole": False, "playlistRole": False,
                         "coverArtRole": True, "commentRole": False, "podcastRole": False, "streamRole": True,
                         "jukeboxRole": False, "shareRole": False, "videoConversionRole": False, "folder": [1]}}

    def _m_getMusicFolders(self, params):
        return {"musicFolders": {"musicFolder": [{"id": 1, "name": "Sonicache"}]}}

    def _m_getOpenSubsonicExtensions(self, params):
        return {"openSubsonicExtensions": [{"name": "formPost", "versions": [1]}]}


def required(params: dict[str, str], name: str) -> str:
    value = params.get(name)
    if not value:
        raise Failure(MISSING, f"Required parameter is missing: {name}")
    return value
