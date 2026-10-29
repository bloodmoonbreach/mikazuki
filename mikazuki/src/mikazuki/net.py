"""Fetching job pages from the server, without letting a URL reach internal services (SSRF).

Every hop is checked: the host must resolve only to public addresses, so a job URL can't
point at this server, the LAN, Tailscale (100.64.0.0/10) or cloud metadata endpoints, even via redirects.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

MAX_BYTES = 3_000_000
MAX_REDIRECTS = 4
_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json", "application/ld+json")


class FetchError(RuntimeError):
    pass


def _public(host: str) -> None:
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
    def redirect_request(self, *args, **kwargs):  # we follow redirects ourselves, checking each hop
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def fetch(url: str, timeout: float = 15.0) -> str:
    for _ in range(MAX_REDIRECTS + 1):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise FetchError("only http(s) URLs")
        if u.username or u.password:
            raise FetchError("URLs with credentials are not allowed")
        _public(u.hostname)
        req = urllib.request.Request(url, headers={
            "User-Agent": "mikazuki/0.1 (+https://github.com/bloodmoonbreach/mikazuki)",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "en,es;q=0.8,ro;q=0.7",
        })
        try:
            resp = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urljoin(url, e.headers["Location"])
                continue
            raise FetchError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(str(e)) from e
        with resp:
            ctype = resp.headers.get_content_type()
            if ctype not in _TYPES:
                raise FetchError(f"unsupported content type {ctype}")
            data = resp.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise FetchError("page too large")
            return data.decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    raise FetchError("too many redirects")
