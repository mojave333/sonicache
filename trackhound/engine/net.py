"""HTTP helpers for the metadata sources."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from .models import SourceError

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


def fetch_text(url: str, *, service: str, user_agent: str = BROWSER_UA, retries: int = 3) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Language": "en"})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SourceError.of("not_found",
                                     "{service} не нашёл страницу: {url}", service=service, url=url) from e
            if attempt == retries or e.code not in (429, 500, 502, 503, 504):
                raise SourceError.of("http_error", "{service} ответил HTTP {code}: {url}",
                                     service=service, code=e.code, url=url) from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries:
                raise SourceError.of("offline",
                                     "Нет связи с {service}: {error}", service=service, error=e) from e
        time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def follow_short_link(link: str, pattern: re.Pattern) -> str | None:
    """Where a short link leads: what the pattern finds in the address it
    redirects to, or else in the page it serves; None when neither has it."""
    request = urllib.request.Request(link, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            found = pattern.search(resp.geturl()) or pattern.search(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise SourceError.of("short_link_broken", "Не удалось открыть короткую ссылку {link}: {error}",
                             link=link, error=e) from e
    return found.group(0) if found else None


def fetch_json(url: str, *, service: str, user_agent: str = BROWSER_UA) -> dict:
    try:
        return json.loads(fetch_text(url, service=service, user_agent=user_agent))
    except ValueError as e:
        raise SourceError.of("unreadable", "{service} вернул непонятный ответ: {url}",
                             service=service, url=url) from e
