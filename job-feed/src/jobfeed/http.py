"""Polite, SSRF-safe HTTP for job sources.

* Only http(s); hosts must resolve to public addresses (checked on every redirect hop), so a
  user-configured feed URL can't reach the LAN, Tailscale, localhost or cloud metadata.
* Identifies itself, waits between requests to the same host, caps response size.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

from . import __version__

USER_AGENT = f"job-feed/{__version__} (+https://github.com/bloodmoonbreach/mikazuki)"
MAX_BYTES = 8_000_000
MAX_REDIRECTS = 4
_last_hit: dict[str, float] = {}


class FetchError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class Blocked(FetchError):
    """The site said no: a bot challenge, CAPTCHA, or 403/429. Never retried around; the site is quarantined."""

    def __init__(self, msg: str, status: int | None = None, challenge: bool = False):
        super().__init__(msg, status)
        self.challenge = challenge  # a real bot-protection page (vs a plain 403/429)


_CHALLENGE = (b"cf-chl", b"challenge-platform", b"just a moment...", b"captcha", b"hcaptcha", b"recaptcha",
              b"attention required! | cloudflare", b"ddos-guard", b"perimeterx", b"px-captcha", b"datadome",
              b"verify you are human", b"are you a robot")


def check_public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FetchError(f"can't resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or ip in ipaddress.ip_network("100.64.0.0/10")
                or (ip.version == 6 and ip.ipv4_mapped and not ip.ipv4_mapped.is_global)):
            raise FetchError(f"{host} points to a non-public address ({ip}); refusing")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def get(url: str, *, timeout: float = 20.0, accept: str = "*/*", headers: dict | None = None,
        delay: float = 1.0, max_bytes: int = MAX_BYTES) -> tuple[bytes, str]:
    """GET with checks. Returns (body, content type)."""
    for _ in range(MAX_REDIRECTS + 1):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            raise FetchError(f"refusing URL {url!r}")
        check_public(u.hostname)
        wait = _last_hit.get(u.hostname, 0) + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_hit[u.hostname] = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept, **(headers or {})})
        try:
            resp = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urljoin(url, e.headers["Location"])
                continue
            if e.code in (401, 403, 429, 503):
                try:
                    body = e.read(30_000).lower()
                except OSError:
                    body = b""
                challenged = bool(e.headers.get("cf-mitigated")) or any(m in body for m in _CHALLENGE)
                if challenged or e.code in (403, 429):
                    what = "bot challenge" if challenged else f"HTTP {e.code}"
                    raise Blocked(f"{u.hostname}: {what} (site refuses automated access; not bypassed)", e.code,
                                  challenge=challenged) from e
            raise FetchError(f"HTTP {e.code} from {u.hostname}", e.code) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(f"{u.hostname}: {e}") from e
        with resp:
            try:
                data = resp.read(max_bytes + 1)
            except (TimeoutError, OSError) as e:
                raise FetchError(f"{u.hostname}: read failed ({e})") from e
            if len(data) > max_bytes:
                raise FetchError(f"{u.hostname}: response too large")
            return data, resp.headers.get_content_type()
    raise FetchError("too many redirects")


def get_json(url: str, **kw):
    data, _ = get(url, accept="application/json", **kw)
    try:
        return json.loads(data)
    except ValueError as e:
        raise FetchError(f"{urlparse(url).hostname}: invalid JSON") from e


_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def html_entities_to_numeric(xml: str) -> str:
    """Feeds that use HTML entities (&nbsp; &eacute;) aren't valid XML; turn them into numeric references.
    (Feeds that *declare* entities are refused elsewhere: that's the XML-bomb vector.)"""
    import re
    from html.entities import name2codepoint

    def sub(m):
        name = m.group(1)
        if name in _XML_ENTITIES or name not in name2codepoint:
            return m.group(0)
        return f"&#{name2codepoint[name]};"
    return re.sub(r"&([A-Za-z][A-Za-z0-9]{1,31});", sub, xml)


def get_text(url: str, **kw) -> str:
    data, _ = get(url, **kw)
    return data.decode("utf-8", errors="replace")
