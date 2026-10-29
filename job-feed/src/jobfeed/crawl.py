"""Polite crawling: robots.txt (Protego), crawl-delay, sitemap discovery with hard caps, block quarantine.

Rules (also for anyone reusing this code):
* identify honestly (job-feed User-Agent), obey robots.txt for that agent, honour Crawl-delay;
* a bot challenge / CAPTCHA / 403 / 429 is the site saying no: stop, quarantine the host, never work around it;
* sitemaps are untrusted input: capped in size (also after gunzip), nesting depth and URL count.
"""

from __future__ import annotations

import datetime as dt
import gzip
import re
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from protego import Protego

from . import http

UA_TOKEN = "job-feed"
MAX_CRAWL_DELAY = 30.0  # a site asking for more than this between requests is skipped
SITEMAP_MAX_BYTES = 10_000_000  # after decompression
SITEMAP_MAX_FILES = 6
SITEMAP_MAX_DEPTH = 2
SITEMAP_MAX_URLS = 3000

JOBLIKE = re.compile(r"/(jobs?|careers?|vacanc(y|ies)|vacantes?|empleos?|ofertas?|positions?|openings?|stellen|"
                     r"oportunidades|trabaja-con-nosotros|join-us|work-with-us|joinus|posting|requisition|locuri-de-munca)"
                     r"(/|-|$|\?)", re.IGNORECASE)


@dataclass
class SitePolicy:
    host: str
    robots: Protego | None
    delay: float
    sitemaps: list[str] = field(default_factory=list)

    def allowed(self, url: str) -> bool:
        return self.robots is None or self.robots.can_fetch(url, UA_TOKEN)


class Disallowed(http.FetchError):
    """robots.txt disallows this URL for job-feed."""


_policies: dict[str, SitePolicy] = {}


def policy(url: str) -> SitePolicy:
    """robots.txt for the URL's host (cached per run). RFC 9309: 4xx → no rules; 5xx/unreachable → assume disallow."""
    u = urlparse(url)
    host = f"{u.scheme}://{u.netloc}"
    if host in _policies:
        return _policies[host]
    robots = None
    try:
        text = http.get_text(f"{host}/robots.txt", max_bytes=500_000)
        robots = Protego.parse(text)
    except http.Blocked as e:
        if e.challenge:
            raise  # bot protection on the whole site: stop here
        robots = None  # RFC 9309: robots.txt "unavailable" (4xx) → no rules
    except http.FetchError as e:
        if e.status is None or e.status >= 500:
            robots = Protego.parse("User-agent: *\nDisallow: /")  # unreachable: be conservative
    delay = 1.0
    if robots is not None:
        cd = robots.crawl_delay(UA_TOKEN)
        if cd:
            delay = float(cd)
        rr = robots.request_rate(UA_TOKEN)
        if rr and rr.requests:
            delay = max(delay, rr.seconds / rr.requests)
    p = SitePolicy(host, robots, delay, list(robots.sitemaps) if robots is not None else [])
    _policies[host] = p
    return p


def fetch_page(url: str, *, max_bytes: int = 3_000_000) -> str:
    """GET a page if robots.txt allows it, at the site's crawl-delay."""
    p = policy(url)
    if p.delay > MAX_CRAWL_DELAY:
        raise Disallowed(f"{p.host} asks for {p.delay:g}s between requests; skipped")
    if not p.allowed(url):
        raise Disallowed(f"robots.txt disallows {url} for {UA_TOKEN}")
    data, _ = http.get(url, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", delay=max(1.0, p.delay),
                       max_bytes=max_bytes)
    return data.decode("utf-8", errors="replace")


def _gunzip_capped(data: bytes) -> bytes:
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(data, SITEMAP_MAX_BYTES + 1)
    if len(out) > SITEMAP_MAX_BYTES or d.unconsumed_tail:
        raise http.FetchError("sitemap too large after decompression")
    return out


def _sitemap_xml(url: str) -> ET.Element:
    p = policy(url)
    if not p.allowed(url):
        raise Disallowed(f"robots.txt disallows {url}")
    data, ctype = http.get(url, accept="application/xml,text/xml;q=0.9,*/*;q=0.5", delay=max(1.0, p.delay),
                           max_bytes=SITEMAP_MAX_BYTES)
    if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
        data = _gunzip_capped(data)
    text = data.decode("utf-8", errors="replace")
    if re.search(r"<!(DOCTYPE|ENTITY)", text[:5000], re.IGNORECASE):
        raise http.FetchError("sitemap declares DOCTYPE/ENTITY; refused")
    return ET.fromstring(text)


def sitemap_urls(site_url: str, *, max_age_days: int | None = None, joblike: bool = True) -> list[tuple[str, dt.datetime | None]]:
    """Job-looking URLs from a site's sitemaps (robots.txt Sitemap: lines, else /sitemap.xml), newest first."""
    from .model import parse_date

    p = policy(site_url)
    queue = [(u, 0) for u in (p.sitemaps or [f"{p.host}/sitemap.xml"])]
    seen_files, out = set(), {}
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max_age_days)) if max_age_days else None
    while queue and len(seen_files) < SITEMAP_MAX_FILES and len(out) < SITEMAP_MAX_URLS:
        sm, depth = queue.pop(0)
        if sm in seen_files or urlparse(sm).netloc != urlparse(p.host).netloc:
            continue  # other hosts' sitemaps are out of scope
        seen_files.add(sm)
        try:
            root = _sitemap_xml(sm)
        except (http.FetchError, ET.ParseError):
            continue
        tag = root.tag.split("}")[-1]
        for node in root:
            loc = next((c.text.strip() for c in node if c.tag.split("}")[-1] == "loc" and c.text), None)
            if not loc:
                continue
            if tag == "sitemapindex":
                # only follow child sitemaps that look relevant, to stay small
                if depth + 1 <= SITEMAP_MAX_DEPTH and (not joblike or JOBLIKE.search(loc) or "job" in loc.lower() or len(queue) < 2):
                    queue.append((loc, depth + 1))
                continue
            if joblike and not JOBLIKE.search(urlparse(loc).path):
                continue
            lm = next((parse_date(c.text) for c in node if c.tag.split("}")[-1] == "lastmod" and c.text), None)
            if cutoff and lm and lm < cutoff:
                continue
            out[loc] = lm
            if len(out) >= SITEMAP_MAX_URLS:
                break
    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return sorted(out.items(), key=lambda kv: kv[1] or epoch, reverse=True)


def links_on(page_url: str, html: str, *, same_site: bool = True) -> list[str]:
    """Job-looking links on a page (absolute, de-duplicated, same site by default)."""
    host = urlparse(page_url).netloc
    out = []
    for m in re.finditer(r"""href\\s*=\\s*["']([^"'#]+)""".replace("\\\\", "\\"), html, re.IGNORECASE):
        href = urljoin(page_url, m.group(1).strip())
        u = urlparse(href)
        if u.scheme not in ("http", "https") or (same_site and u.netloc != host):
            continue
        if JOBLIKE.search(u.path) and href.rstrip("/") != page_url.rstrip("/"):
            out.append(href.split("#")[0])
    return list(dict.fromkeys(out))
