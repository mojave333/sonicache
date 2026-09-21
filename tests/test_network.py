"""Telling what the network lets through, and finding a VPN client's proxy."""

import contextlib
import io
import socket
import socketserver
import threading
import urllib.error
import urllib.request

import pytest

from trackhound.engine import network

EMBED = f"https://open.spotify.com/embed/album/{network.PROBE_ALBUM}"
PREVIEW = f"https://open.spotify.com/album/{network.PROBE_ALBUM}"
OPEN = ('<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"state":{"data":{"entity":{"name":"Discovery"}}}}}}</script>')
CLOSED = ('<script id="__NEXT_DATA__" type="application/json">'
          '{"props":{"pageProps":{"status":404,"title":"Page not found"}}}</script>')
SONGS = '<meta name="music:song" content="https://open.spotify.com/track/x">'


def opener(pages):
    """urlopen over a dict of pages; a missing one fails like a blocked site."""
    def open_url(request, timeout):
        url = request.full_url
        if url not in pages:
            raise urllib.error.URLError("connection reset")
        return contextlib.closing(io.BytesIO(pages[url].encode()))

    return open_url


@pytest.mark.parametrize("pages, expected", [
    ({EMBED: OPEN}, "ok"),
    ({EMBED: CLOSED, PREVIEW: SONGS}, "previews"),
    ({PREVIEW: SONGS}, "previews"),
    ({EMBED: CLOSED, PREVIEW: "<html></html>"}, "down"),
    ({}, "down"),
])
def test_spotify_is_told_apart_by_what_comes(pages, expected):
    assert network.spotify_state_via(opener(pages), 1) == expected


class _Answer(socketserver.BaseRequestHandler):
    def handle(self):
        first = self.request.recv(64)
        if self.server.kind == "socks5" and first.startswith(b"\x05"):
            self.request.sendall(b"\x05\x00")
        elif self.server.kind == "http":
            self.request.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n" if first.startswith(b"\x05")
                                 else b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            self.request.sendall(b"SSH-2.0-OpenSSH\r\n")  # something else entirely


@pytest.fixture
def listener():
    servers = []

    def start(kind):
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Answer)
        server.daemon_threads = True
        server.kind = kind
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("kind", ["socks5", "http", ""])
def test_a_proxy_is_known_by_its_answer(listener, kind):
    assert network.proxy_kind(listener(kind)) == kind


def test_a_closed_port_is_no_proxy():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # free, and nobody listening once closed
    assert network.proxy_kind(port) == ""


def test_found_proxies_are_tried_with_spotify(listener, monkeypatch):
    port = listener("socks5")
    monkeypatch.setattr(network, "PROXY_PORTS", {port: "v2rayN"})
    monkeypatch.setattr(network, "spotify_state_via", lambda open_url, timeout: "ok")
    assert network.find_proxies() == [{"url": f"socks5://127.0.0.1:{port}", "client": "v2rayN", "spotify": "ok"}]


def test_the_check_names_every_service(monkeypatch):
    pages = {EMBED: CLOSED, PREVIEW: SONGS, "https://www.youtube.com/": "<html>",
             f"https://relay.test?kind=album&id={network.PROBE_ALBUM}": '{"entity": {"name": "Discovery"}}'}
    monkeypatch.setattr(urllib.request, "urlopen", opener(pages))
    monkeypatch.setattr(network, "find_proxies", lambda timeout: [])
    assert network.check(1, relay="https://relay.test") == {
        "spotify": "previews", "relay": "ok", "youtube": "ok", "soundcloud": "down", "proxies": []}


@pytest.mark.parametrize("relay, pages, expected", [
    ("", {}, "none"),
    ("https://relay.test", {}, "down"),
    ("https://relay.test", {f"https://relay.test?kind=album&id={network.PROBE_ALBUM}": '{"entity": null}'}, "down"),
    ("https://relay.test/?key=1", {f"https://relay.test/?key=1&kind=album&id={network.PROBE_ALBUM}":
                                   '{"entity": {"name": "Discovery"}}'}, "ok"),
])
def test_the_relay_is_tried_with_the_probe_album(monkeypatch, relay, pages, expected):
    monkeypatch.setattr(urllib.request, "urlopen", opener(pages))
    assert network.relay_state(relay, 1) == expected
