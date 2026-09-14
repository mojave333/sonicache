"""HTTP helpers for the metadata sources."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .i18n import t
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
                raise SourceError(t("{service} не нашёл страницу: {url}", service=service, url=url)) from e
            if attempt == retries or e.code not in (429, 500, 502, 503, 504):
                raise SourceError(t("{service} ответил HTTP {code}: {url}",
                                    service=service, code=e.code, url=url)) from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries:
                raise SourceError(t("Нет связи с {service}: {error}", service=service, error=e)) from e
        time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def fetch_json(url: str, *, service: str) -> dict:
    try:
        return json.loads(fetch_text(url, service=service))
    except ValueError as e:
        raise SourceError(t("{service} вернул непонятный ответ: {url}",
                            service=service, url=url)) from e
