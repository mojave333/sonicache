"""What this network lets through, and a VPN client's proxy on this computer.

Where services are blocked, Russia for one, the program works as far as the
network lets it: Spotify's player may be closed while its preview pages come,
YouTube may not open while SoundCloud does. The check says which is which, so
the person at the window does not have to guess. Most VPN clients open a proxy
on a fixed local port without telling the rest of the system, and the program
goes through a proxy only when it is set; so the check also looks for one on
the ports the common clients use, and tries Spotify through it.
"""

from __future__ import annotations

import json
import socket
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import spotify
from .net import BROWSER_UA

# An album Spotify has in every country it works in: its player page says
# whether Spotify shows releases to this network at all
PROBE_ALBUM = "2noRn2Aes5aoNVsU6iWThc"  # Daft Punk, Discovery
# The ports VPN clients open their local proxies on, by default
PROXY_PORTS = {
    1080: "Shadowsocks", 2080: "NekoBox", 7890: "Clash", 7891: "Clash", 7897: "Clash Verge",
    10808: "v2rayN", 10809: "v2rayN", 12334: "Hiddify", 20170: "v2rayA", 20171: "v2rayA", 20172: "v2rayA",
}
_SERVICES = {
    "youtube": ("https://music.youtube.com/", "https://www.youtube.com/"),
    "soundcloud": ("https://soundcloud.com/",),
}

Opener = Callable[..., object]


def check(timeout: float = 8, relay: str = "") -> dict:
    """Each service as it answers through the proxy now in use, and the local
    proxies found. Spotify is "ok" when its player opens, "previews" when only
    its preview pages come, "down" when neither; the relay is "ok", "down", or
    "none" when there is none; the rest are "ok" or "down".
    """
    with ThreadPoolExecutor(max_workers=5) as pool:
        spotify_state = pool.submit(spotify_state_via, urllib.request.urlopen, timeout)
        relay_state_ = pool.submit(relay_state, relay, timeout)
        services = {name: pool.submit(_opens, urls, urllib.request.urlopen, timeout)
                    for name, urls in _SERVICES.items()}
        proxies = pool.submit(find_proxies, timeout)
        return {"spotify": spotify_state.result(), "relay": relay_state_.result(),
                **{name: "ok" if answer.result() else "down" for name, answer in services.items()},
                "proxies": proxies.result()}


def relay_state(relay: str, timeout: float) -> str:
    """Whether the Spotify relay hands over the probe album (relay/worker.js)."""
    if not relay:
        return "none"
    joiner = "&" if "?" in relay else "?"
    try:
        answer = json.loads(_get(urllib.request.urlopen, f"{relay}{joiner}kind=album&id={PROBE_ALBUM}", timeout))
    except (OSError, ValueError):
        return "down"
    return "ok" if isinstance(answer, dict) and answer.get("entity") else "down"


def find_proxies(timeout: float = 8) -> list[dict]:
    """Local proxies of VPN clients, each with how Spotify answers through it."""
    with ThreadPoolExecutor(max_workers=len(PROXY_PORTS)) as pool:
        kinds = dict(zip(PROXY_PORTS, pool.map(proxy_kind, PROXY_PORTS)))
        found = [(port, kind) for port, kind in kinds.items() if kind]
        states = list(pool.map(lambda item: spotify_state_via(opener(*item).open, timeout), found))
    return [{"url": f"{kind}://127.0.0.1:{port}", "client": PROXY_PORTS[port], "spotify": state}
            for (port, kind), state in zip(found, states)]


def proxy_kind(port: int, host: str = "127.0.0.1") -> str:
    """"socks5" or "http" for a proxy listening on the port, "" for anything else.

    A SOCKS5 greeting is answered in two bytes; a port that takes both, as
    Clash's and NekoBox's do, is taken as SOCKS. An HTTP proxy answers a
    CONNECT with a status line, whatever the status.
    """
    try:
        with socket.create_connection((host, port), timeout=0.3) as sock:
            sock.settimeout(1)
            sock.sendall(b"\x05\x01\x00")
            if sock.recv(2) == b"\x05\x00":
                return "socks5"
    except OSError:
        return ""  # nothing listening, or it hung up at once
    try:
        with socket.create_connection((host, port), timeout=0.3) as sock:
            sock.settimeout(2)
            sock.sendall(b"CONNECT open.spotify.com:443 HTTP/1.1\r\nHost: open.spotify.com:443\r\n\r\n")
            return "http" if sock.recv(12).startswith(b"HTTP/1.") else ""
    except OSError:
        return ""


def opener(port: int, kind: str, host: str = "127.0.0.1") -> urllib.request.OpenerDirector:
    """An opener that goes through one proxy, whatever the program is set to."""
    if kind == "socks5":
        import socks
        from sockshandler import SocksiPyHandler

        return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                           SocksiPyHandler(socks.SOCKS5, host, port, True))
    address = f"http://{host}:{port}"
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": address, "https": address}))


def spotify_state_via(open_url: Opener, timeout: float) -> str:
    try:
        page = _get(open_url, f"https://open.spotify.com/embed/album/{PROBE_ALBUM}", timeout)
        m = spotify._NEXT_DATA_RE.search(page)
        props = json.loads(m.group(1))["props"]["pageProps"] if m else {}
        if ((props.get("state") or {}).get("data") or {}).get("entity"):
            return "ok"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        page = _get(open_url, f"https://open.spotify.com/album/{PROBE_ALBUM}", timeout, spotify.PREVIEW_UA)
    except OSError:
        return "down"
    return "previews" if '"music:song"' in page else "down"


def _opens(urls: tuple[str, ...], open_url: Opener, timeout: float) -> bool:
    for url in urls:
        try:
            _get(open_url, url, timeout)
            return True
        except OSError:
            continue
    return False


def _get(open_url: Opener, url: str, timeout: float, user_agent: str = BROWSER_UA) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Language": "en"})
    with open_url(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")
