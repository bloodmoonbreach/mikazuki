"""Generic careers pages: sites with no API and no known job system.

For each [[careers]] URL, the cheapest route that works:

1. **ATS fingerprint**: the page links to / embeds a known job system (Greenhouse, Lever, …) → read its API.
2. **JSON-LD**: schema.org JobPosting markup on the page or on the job pages it links to (what Google for Jobs reads).
3. **HTML**: job pages without markup → title + main text via Trafilatura.
4. **Needs a browser**: the page is an empty JavaScript shell → queued for the optional browser helper (Crawl4AI on
   another machine). Never bypasses bot protection: a challenge quarantines the site.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin, urlparse

from . import ats, crawl, http
from .model import Posting, parse_date

ATS_HOSTS = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|smartrecruiters\.com|"
    r"recruitee\.com|personio\.(?:de|com)|teamtailor\.com|breezy\.hr|myworkdayjobs\.com)[^\s\"'<>)]*", re.IGNORECASE)
# Job platforms we have no API adapter for, whose hosted job pages can still be read like any careers page
# (often with JobPosting markup). Spain: TalentClue, Factorial, Hiring Room; others common in the EU.
EXTERNAL_BOARDS = re.compile(
    r"https?://(?:[a-z0-9-]+\.)+(?:talentclue\.com|factorialhr\.(?:es|com)|hiringroom\.com|softgarden\.io|"
    r"jobs\.bamboohr\.com|bamboohr\.com|jobvite\.com|icims\.com|successfactors\.(?:com|eu)|taleo\.net|join\.com|"
    r"homerun\.co|jobs\.personio\.de|recruitcrm\.io|zohorecruit\.(?:com|eu)|jobs\.ashbyhq\.com)[^\s\"'<>)]*",
    re.IGNORECASE)
_LDJSON = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_ANCHOR = re.compile(r"<a\b[^>]*href\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class Recon:
    url: str
    state: str  # ats | jsonld | html | needs_browser | blocked | robots | error | empty
    detail: str = ""
    ats: str | None = None
    slug: str | None = None
    postings: list[Posting] | None = None


# ------------------------------------------------------------------ parsing

def job_postings_jsonld(html: str) -> list[dict]:
    """Every schema.org JobPosting object in the page's JSON-LD (handles @graph, lists, ItemList)."""
    found: list[dict] = []

    def walk(node, depth=0):
        if depth > 6:
            return
        if isinstance(node, list):
            for x in node:
                walk(x, depth + 1)
        elif isinstance(node, dict):
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                found.append(node)
                return
            for key in ("@graph", "mainEntity", "itemListElement", "item"):
                if key in node:
                    walk(node[key], depth + 1)

    for raw in _LDJSON.findall(html)[:20]:
        try:
            walk(json.loads(raw.strip()))
        except (ValueError, RecursionError):
            continue
    return found[:200]


def _names(node) -> list[str]:
    if isinstance(node, list):
        return [n for x in node for n in _names(x)]
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out = []
        for k in ("name", "addressLocality", "addressRegion", "addressCountry"):
            v = node.get(k)
            if isinstance(v, (str, dict, list)):
                out += _names(v)
        if "address" in node:
            out += _names(node["address"])
        return out
    return []


def posting_from_jsonld(jp: dict, page_url: str, site: str) -> Posting:
    """A JobPosting → Posting. Structured location rules are written as plain sentences so remote-check reads them."""
    title = str(jp.get("title") or jp.get("name") or "").strip()
    org = jp.get("hiringOrganization")
    company = (org.get("name") if isinstance(org, dict) else org) or site
    places = ", ".join(dict.fromkeys(_names(jp.get("jobLocation"))))
    lines = []
    if "TELECOMMUTE" in str(jp.get("jobLocationType", "")).upper():
        lines.append("This is a fully remote position.")
    req = ", ".join(dict.fromkeys(_names(jp.get("applicantLocationRequirements"))))
    if req:
        lines.append(f"Applicants must be based in {req}.")
    sal = jp.get("baseSalary")
    if isinstance(sal, dict):
        v = sal.get("value") if isinstance(sal.get("value"), dict) else {}
        lo, hi = v.get("minValue") or v.get("value"), v.get("maxValue")
        if lo:
            lines.append(f"Salary: {lo}{' - ' + str(hi) if hi else ''} {sal.get('currency', '')} per "
                         f"{str(v.get('unitText') or 'year').lower()}")
    desc = "\n".join(lines) + "\n\n" + unescape(str(jp.get("description") or ""))
    url = str(jp.get("url") or page_url)
    if not url.startswith(("http://", "https://")):
        url = urljoin(page_url, url)
    remote = True if "TELECOMMUTE" in str(jp.get("jobLocationType", "")).upper() else None
    return Posting("careers", f"{site} careers", url, title, str(company), places, desc,
                   parse_date(jp.get("datePosted")), [], "", remote, url)


def page_text(html: str, url: str) -> tuple[str, str]:
    """(title, main text) of an HTML job page via Trafilatura; falls back to <title> + stripped text."""
    title = ""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL) or \
        re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = " ".join(unescape(_TAGS.sub(" ", m.group(1))).split())[:200]
    try:
        import trafilatura
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_precision=True) or ""
    except Exception:  # extraction is best-effort
        text = ""
    if not text:
        text = " ".join(unescape(_TAGS.sub(" ", re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I))).split())
    return title, text[:20000]


_APPLY = re.compile(r"\b(apply|apply now|submit (your )?application|aplicar|inscr[ií]bete|inscribirse|env[ií]a tu (cv|candidatura)|"
                    r"post[uú]late|candidatura|aplic[aă]|trimite cv|candideaz[aă])\b", re.IGNORECASE)
_JOBBODY = re.compile(r"\b(requirements|qualifications|responsibilities|what you.ll do|what we offer|about the role|"
                      r"requisitos|funciones|responsabilidades|qu[eé] ofrecemos|experiencia (m[ií]nima|requerida)|"
                      r"cerin[tț]e|responsabilit[aă][tț]i|oferim)\b", re.IGNORECASE)
_CAREERS_LINK = re.compile(r"(careers?|jobs?|trabaja|empleo|join[- ]?us|vacantes|work[- ]with[- ]us|ofertas|cariere|locuri de munc)",
                           re.IGNORECASE)


def looks_like_job_page(html: str, text: str) -> bool:
    """A job ad, not a marketing page: it invites you to apply and talks about the role's requirements or tasks."""
    visible = unescape(_TAGS.sub(" ", re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)))
    return bool(_APPLY.search(visible) and (_JOBBODY.search(text) or _JOBBODY.search(visible)))


def find_careers_url(site_url: str) -> str | None:
    """From a company homepage, the most likely careers page (same site)."""
    try:
        html = crawl.fetch_page(site_url, max_bytes=6_000_000)
    except http.FetchError:
        return None
    host = urlparse(site_url).netloc.removeprefix("www.")
    best = None
    for href, inner in _ANCHOR.findall(html):
        url = urljoin(site_url, href.strip())
        u = urlparse(url)
        text = " ".join(unescape(_TAGS.sub(" ", inner)).split())
        if u.scheme in ("http", "https") and u.netloc.removeprefix("www.").endswith(host) and \
                (_CAREERS_LINK.search(u.path) or _CAREERS_LINK.search(text)):
            score = (0 if _CAREERS_LINK.search(u.path) else 1, len(u.path))
            if best is None or score < best[0]:
                best = (score, url.split("#")[0])
    if not best:
        systems = ats_links(html, site_url)
        return site_url if systems else None
    return best[1]


def looks_like_js_shell(html: str) -> bool:
    """Little visible text, but app-root markers: the content is rendered by JavaScript."""
    body = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", html, flags=re.S | re.I)
    visible = " ".join(unescape(_TAGS.sub(" ", body)).split())
    markers = re.search(r"id=[\"'](root|app|__next|__nuxt|svelte)[\"']|ng-version|data-reactroot|window\.__INITIAL_STATE__",
                        html, re.I)
    return len(visible) < 400 and bool(markers)


def ats_links(html: str, page_url: str) -> list[tuple[str, str]]:
    """Known job systems linked or embedded on the page → [(ats, slug)]."""
    out = []
    for m in ATS_HOSTS.finditer(unescape(html)):
        found = ats.detect(urljoin(page_url, m.group(0)))
        if found and found not in out:
            out.append(found)
    return out[:3]


def job_links(page_url: str, html: str, keywords: list[str]) -> list[str]:
    """Same-site links whose path looks like a job page; links whose text matches a search keyword first.
    (Keywords only rank: "security" in a marketing URL doesn't make it a job.)"""
    host = urlparse(page_url).netloc
    scored = []
    for href, inner in _ANCHOR.findall(html):
        url = urljoin(page_url, href.strip()).split("#")[0]
        u = urlparse(url)
        if u.scheme not in ("http", "https") or u.netloc != host or url.rstrip("/") == page_url.rstrip("/"):
            continue
        # On a hosted job platform (TalentClue, Factorial, …) nearly every link is a job: skip the path test there.
        if not crawl.JOBLIKE.search(u.path) and not EXTERNAL_BOARDS.match(url):
            continue
        text = " ".join(unescape(_TAGS.sub(" ", inner)).split()).lower()
        hit = any(k.lower() in text for k in keywords)
        scored.append((0 if hit else 1, url))
    return list(dict.fromkeys(u for _, u in sorted(scored)))


def external_board(html: str, page_url: str = "") -> str | None:
    """A link to a hosted job platform without an adapter (TalentClue, Factorial, …), if the page has one.
    None when the page is already on such a platform (don't hop from board to board)."""
    if page_url and EXTERNAL_BOARDS.match(page_url):
        return None
    m = EXTERNAL_BOARDS.search(unescape(html))
    if not m:
        return None
    url = m.group(0).rstrip(".,;")
    return url if not re.search(r"/(complaints|denuncias|whistle|privacy|login)", url, re.I) else None


# -------------------------------------------------------------------- recon

def recon(name: str, url: str, keywords: list[str], *, max_pages: int = 12, store=None, _depth: int = 0) -> Recon:
    host = urlparse(url).netloc
    if store is not None and (why := store.blocked(host)):
        return Recon(url, "blocked", why)
    try:
        html = crawl.fetch_page(url, max_bytes=6_000_000)
    except http.Blocked as e:
        if store is not None and e.challenge:
            store.block(host, str(e))
        return Recon(url, "blocked", str(e))
    except crawl.Disallowed as e:
        return Recon(url, "robots", str(e))
    except http.FetchError as e:
        if e.status == 404 or urlparse(url).path in ("", "/"):
            found = find_careers_url(f"{urlparse(url).scheme}://{urlparse(url).netloc}/")
            if found and found.rstrip("/") != url.rstrip("/"):
                r = recon(name, found, keywords, max_pages=max_pages, store=store)
                r.detail = f"careers page found at {found}; {r.detail}"
                return r
        return Recon(url, "error", str(e))

    # a homepage was given: look for the careers page first
    if urlparse(url).path in ("", "/") and not job_postings_jsonld(html) and not ats_links(html, url):
        found = find_careers_url(url)
        if found and found.rstrip("/") != url.rstrip("/"):
            r = recon(name, found, keywords, max_pages=max_pages, store=store)
            r.detail = f"careers page found at {found}; {r.detail}"
            return r

    # 1. a known job system behind the page
    systems = ats_links(html, url)
    if systems:
        kind, slug = systems[0]
        return Recon(url, "ats", f"uses {ats.ATS_NAMES[kind]} ({slug})", kind, slug)

    # 1b. a hosted job platform we have no API for (TalentClue, Factorial, …): read its pages instead
    board = external_board(html, url) if _depth == 0 else None
    if board and urlparse(board).netloc != host:
        r = recon(name, board, keywords, max_pages=max_pages, store=store, _depth=1)
        r.detail = f"job board at {board}; {r.detail}"
        return r

    # 2. JobPosting markup on the careers page itself
    site = name
    direct = job_postings_jsonld(html)
    postings = [posting_from_jsonld(jp, url, site) for jp in direct if jp.get("title")]

    # 3. follow job links (page links first, then the sitemap), reading markup or main text
    if not postings:
        candidates = job_links(url, html, keywords)
        if len(candidates) < max_pages:
            try:
                candidates += [u for u, _ in crawl.sitemap_urls(url, max_age_days=45)]
            except http.FetchError:
                pass
        candidates = list(dict.fromkeys(candidates))[:max_pages]
        from_markup = from_text = 0
        seen = set(candidates)
        for job_url in candidates:
            try:
                page = crawl.fetch_page(job_url)
            except http.Blocked as e:
                if store is not None and e.challenge:
                    store.block(host, str(e))
                return Recon(url, "blocked", str(e), postings=postings)
            except http.FetchError:
                continue
            found = job_postings_jsonld(page)
            if found:
                postings += [posting_from_jsonld(jp, job_url, site) for jp in found if jp.get("title")]
                from_markup += 1
                continue
            more = [u for u in job_links(job_url, page, keywords) if u not in seen]
            if len(more) >= 3 and len(candidates) < max_pages * 2:  # a listing page: go one level deeper
                candidates += more[:max_pages]
                seen.update(more)
                continue
            if True:
                title, text = page_text(page, job_url)
                if title and len(text) > 300 and looks_like_job_page(page, text):
                    postings.append(Posting("careers", f"{site} careers", job_url, title, site, "", text, None, [], "",
                                            None, job_url))
                    from_text += 1
        if postings:
            state = "jsonld" if from_markup >= from_text else "html"
            return Recon(url, state, f"{len(postings)} job(s) from {from_markup + from_text} page(s) "
                                     f"({from_markup} with job markup, {from_text} read as text)", postings=postings)
        if looks_like_js_shell(html):
            return Recon(url, "needs_browser", "page is rendered by JavaScript; needs a browser")
        # Lots of page, no jobs visible: the list is probably loaded by JavaScript or a widget.
        return Recon(url, "needs_browser", "no jobs visible without a browser (list probably loaded by JavaScript)")
    return Recon(url, "jsonld", f"{len(postings)} job(s) in the page's markup", postings=postings)
