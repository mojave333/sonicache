"""A proxy set in the program reaches everything that goes out, SOCKS ones included."""

import http.server
import os
import select
import socket
import socketserver
import threading
import urllib.request

import pytest
import requests

from trackhound.engine import use_proxy


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"through"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class _Socks5(socketserver.BaseRequestHandler):
    """Just enough of SOCKS5 to relay one connection: no login, CONNECT only."""

    def exact(self, size):
        data = b""
        while len(data) < size:
            data += self.request.recv(size - len(data))
        return data

    def handle(self):
        methods = self.exact(2)[1]
        self.exact(methods)
        self.request.sendall(b"\x05\x00")
        _, _, _, kind = self.exact(4)
        host = self.exact(self.exact(1)[0]).decode() if kind == 3 else socket.inet_ntoa(self.exact(4))
        port = int.from_bytes(self.exact(2), "big")
        self.server.seen.append((kind, host, port))
        remote = socket.create_connection(("127.0.0.1" if host == "localhost" else host, port))
        self.request.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + port.to_bytes(2, "big"))
        with remote:
            while True:
                readable, _, _ = select.select([self.request, remote], [], [], 5)
                if not readable:
                    return
                for side in readable:
                    data = side.recv(65536)
                    if not data:
                        return
                    (remote if side is self.request else self.request).sendall(data)


@pytest.fixture
def servers(monkeypatch):
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    page = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    proxy = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Socks5)
    proxy.daemon_threads = True
    proxy.seen = []
    for server in (page, proxy):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://localhost:{page.server_port}/", proxy
    use_proxy("")
    for server in (page, proxy):
        server.shutdown()
        server.server_close()


def test_urllib_goes_through_a_socks_proxy(servers):
    url, proxy = servers
    use_proxy(f"socks5://127.0.0.1:{proxy.server_address[1]}")
    with urllib.request.urlopen(url, timeout=5) as response:
        assert response.read() == b"through"
    kind, host, _ = proxy.seen[0]
    assert (kind, host) == (3, "localhost")  # the name went to the proxy, not the local DNS


def test_requests_goes_through_it_too(servers):
    url, proxy = servers
    use_proxy(f"socks5://127.0.0.1:{proxy.server_address[1]}")
    assert os.environ["HTTPS_PROXY"].startswith("socks5h://")
    assert requests.get(url, timeout=5).text == "through"
    assert proxy.seen


def test_clearing_the_proxy_goes_direct_again(servers):
    url, proxy = servers
    use_proxy(f"socks5://127.0.0.1:{proxy.server_address[1]}")
    use_proxy("")
    with urllib.request.urlopen(url, timeout=5) as response:
        assert response.read() == b"through"
    assert proxy.seen == []
