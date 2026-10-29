"""URL safety and politeness checks (no browser needed, unit-testable)."""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from urllib.parse import urlparse

UA = "job-feed/0.1 (+https://github.com/bloodmoonbreach/mikazuki)"
UA_TOKEN = "job-feed"
# Only real challenge pages. (A reCAPTCHA widget on an application form is normal and is not a challenge.)
CHALLENGE = re.compile(r"cf-chl|challenge-platform|<title>just a moment\.\.\.|verify you are human|are you a robot|"
                       r"ddos-guard|px-captcha|captcha-delivery\.com|geo\.captcha-delivery|attention required! \| cloudflare",
                       re.IGNORECASE)


class Refused(ValueError):
    pass


# Sites known to put bot protection in front of automated clients. A real browser may pass such a challenge
# automatically, but the site has said it doesn't want bots, so they are refused outright. Add more with
# HELPER_DENY="host1,host2".
DENY = {"jobs.cybersecurityjobs.com", "linkedin.com", "www.linkedin.com"}


def denied(host: str) -> bool:
    import os
    extra = {h.strip().lower() for h in os.environ.get("HELPER_DENY", "").split(",") if h.strip()}
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in DENY | extra)


def _bad_ip(ip: ipaddress._BaseAddress) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
            or ip in ipaddress.ip_network("100.64.0.0/10")
            or (ip.version == 6 and bool(ip.ipv4_mapped) and not ip.ipv4_mapped.is_global))


_cache: dict[str, float] = {}


def host_public(host: str) -> bool:
    """True if the host resolves only to public addresses. Positive answers are cached for 5 minutes;
    failures are not cached (a transient DNS hiccup must not block a host)."""
    host = host.lower().strip("[]")
    if _cache.get(host, 0) > time.time():
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    ok = bool(infos) and not any(_bad_ip(ipaddress.ip_address(i[4][0])) for i in infos)
    if ok:
        _cache[host] = time.time() + 300
    return ok


def check_url(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise Refused("only http(s) URLs")
    if u.username or u.password:
        raise Refused("URLs with credentials are not allowed")
    if denied(u.hostname):
        raise Refused(f"{u.hostname} refuses automated access (bot protection); not rendered")
    if not host_public(u.hostname):
        raise Refused(f"{u.hostname} does not resolve to a public address")
    return url.strip()


def looks_challenged(html: str, status: int | None) -> bool:
    return status in (403, 429) or bool(CHALLENGE.search(html[:30000] if html else ""))
