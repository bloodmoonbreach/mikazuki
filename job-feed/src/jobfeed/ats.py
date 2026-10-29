"""Company career boards: public job APIs of applicant-tracking systems (ATS).

Most companies publish their openings through an ATS that exposes them as JSON/XML for the
company's own career page. We read those directly, two-phase to stay light:

1. list every opening (titles + locations; cheap);
2. fetch full details only for openings whose title matches one of your searches.

Supported: Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio, Teamtailor,
Breezy HR, Workday. `detect(url)` works out the ATS and company id from a careers-page URL.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from urllib.parse import quote, urlparse

from . import http
from .model import Posting, parse_date

ATS_NAMES = {
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workable": "Workable",
    "smartrecruiters": "SmartRecruiters", "recruitee": "Recruitee", "personio": "Personio",
    "teamtailor": "Teamtailor", "breezy": "Breezy HR", "workday": "Workday",
}
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


@dataclass
class Company:
    name: str
    ats: str
    slug: str  # board token / company id; for Workday "host/tenant/site"
    search: str | list[str] = ""  # server-side search text (Workday, SmartRecruiters); a list runs each term
    tags: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.ats not in ATS_NAMES:
            raise ValueError(f"{self.name}: unknown ats {self.ats!r} (supported: {', '.join(ATS_NAMES)})")
        parts = self.slug.split("/") if self.ats == "workday" else [self.slug]
        if self.ats == "workday" and len(parts) != 3:
            raise ValueError(f"{self.name}: workday slug is host/tenant/site, e.g. acme.wd3.myworkdayjobs.com/acme/Careers")
        if not all(_SLUG.match(p) for p in parts):
            raise ValueError(f"{self.name}: invalid slug {self.slug!r}")


@dataclass
class Opening:
    """Phase 1 result: enough to decide whether to fetch details."""
    id: str
    title: str
    location: str
    url: str
    posted: object = None
    remote: bool | None = None
    raw: dict = field(default_factory=dict)


# ----------------------------------------------------------------- helpers

def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _xml(text: str) -> ET.Element:
    if re.search(r"<!(DOCTYPE|ENTITY)", text[:5000], re.IGNORECASE):
        raise http.FetchError("XML with DOCTYPE/ENTITY refused")
    return ET.fromstring(http.html_entities_to_numeric(text))


def _post_json(url: str, body: dict) -> dict:
    """POST helper for APIs that need it (Workday). Same SSRF checks via http.check_public."""
    import urllib.error
    import urllib.request

    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https":
        raise http.FetchError("https only")
    http.check_public(host)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "User-Agent": http.USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read(5_000_000))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise http.FetchError(f"{host}: {e}") from e


# ---------------------------------------------------- per-ATS: list + detail
# Each entry: list(company) -> [Opening]; detail(company, opening) -> description HTML/text

def _gh_list(c: Company) -> list[Opening]:
    d = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{c.slug}/jobs")
    return [Opening(_s(j["id"]), _s(j.get("title")), _s((j.get("location") or {}).get("name")), _s(j.get("absolute_url")),
                    parse_date(j.get("first_published") or j.get("updated_at"))) for j in d.get("jobs", [])]


def _gh_detail(c: Company, o: Opening) -> str:
    d = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{c.slug}/jobs/{quote(o.id)}")
    import html as _html
    return _html.unescape(_s(d.get("content")))  # Greenhouse returns escaped HTML


def _lever_list(c: Company) -> list[Opening]:
    out = []
    for j in http.get_json(f"https://api.lever.co/v0/postings/{c.slug}?mode=json", timeout=60):
        cat = j.get("categories") or {}
        loc = ", ".join(x for x in [_s(cat.get("location")), *[_s(x) for x in (cat.get("allLocations") or [])][1:]] if x)
        wt = _s(j.get("workplaceType"))
        if wt:
            loc = f"{loc} ({wt})" if loc else wt
        desc = "\n\n".join([_s(j.get("descriptionPlain"))] + [f"{_s(l.get('text'))}\n{_s(l.get('content'))}" for l in j.get("lists") or []]
                           + [_s(j.get("additionalPlain"))])
        out.append(Opening(_s(j.get("id")), _s(j.get("text")), loc, _s(j.get("hostedUrl")), parse_date(j.get("createdAt")),
                           wt == "remote" or None, {"description": desc}))
    return out


def _ashby_list(c: Company) -> list[Opening]:
    d = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{c.slug}?includeCompensation=true")
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [_s(j.get("location"))] + [_s(x.get("location")) for x in j.get("secondaryLocations") or []]
        wt = _s(j.get("workplaceType"))
        loc = ", ".join(x for x in locs if x) + (f" ({wt})" if wt else "")
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        desc = _s(j.get("descriptionHtml") or j.get("descriptionPlain")) + (f"<p>Compensation: {comp}</p>" if comp else "")
        out.append(Opening(_s(j.get("id")), _s(j.get("title")), loc, _s(j.get("jobUrl")), parse_date(j.get("publishedAt")),
                           bool(j.get("isRemote")) or None, {"description": desc}))
    return out


def _workable_list(c: Company) -> list[Opening]:
    d = http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{c.slug}?details=true")
    out = []
    for j in d.get("jobs", []):
        loc = ", ".join(x for x in (_s(j.get("city")), _s(j.get("state")), _s(j.get("country"))) if x)
        if j.get("telecommuting"):
            loc = f"{loc} (remote)" if loc else "Remote"
        out.append(Opening(_s(j.get("shortcode")), _s(j.get("title")), loc, _s(j.get("url") or j.get("application_url")),
                           parse_date(j.get("published_on") or j.get("created_at")), bool(j.get("telecommuting")) or None,
                           {"description": _s(j.get("description"))}))
    return out


def _sr_list(c: Company) -> list[Opening]:
    q = f"&q={quote(c.search)}" if c.search else ""
    d = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{c.slug}/postings?limit=100{q}")
    out = []
    for j in d.get("content", []):
        loc = j.get("location") or {}
        text = ", ".join(x for x in (_s(loc.get("city")), _s(loc.get("country")).upper()) if x)
        if loc.get("remote"):
            text = f"{text} (remote)" if text else "Remote"
        out.append(Opening(_s(j.get("id")), _s(j.get("name")), text,
                           f"https://jobs.smartrecruiters.com/{c.slug}/{_s(j.get('id'))}", parse_date(j.get("releasedDate")),
                           bool(loc.get("remote")) or None))
    return out


def _sr_detail(c: Company, o: Opening) -> str:
    d = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{c.slug}/postings/{quote(o.id)}")
    sections = ((d.get("jobAd") or {}).get("sections") or {})
    return "\n".join(f"<h3>{_s(v.get('title'))}</h3>{_s(v.get('text'))}" for v in sections.values() if isinstance(v, dict))


def _recruitee_list(c: Company) -> list[Opening]:
    d = http.get_json(f"https://{c.slug}.recruitee.com/api/offers/")
    out = []
    for j in d.get("offers", []):
        loc = _s(j.get("location")) + (" (remote)" if j.get("remote") else "")
        out.append(Opening(_s(j.get("id")), _s(j.get("title")), loc, _s(j.get("careers_url")), parse_date(j.get("published_at")),
                           bool(j.get("remote")) or None,
                           {"description": _s(j.get("description")) + _s(j.get("requirements"))}))
    return out


def _personio_list(c: Company) -> list[Opening]:
    root = _xml(http.get_text(f"https://{c.slug}.jobs.personio.de/xml"))
    out = []
    for p in root.iter("position"):
        g = lambda tag: _s(p.findtext(tag))  # noqa: E731
        offices = [g("office")] + [_s(o.text) for o in p.findall("additionalOffices/office")]
        desc = "".join(f"<h3>{_s(d.findtext('name'))}</h3>{_s(d.findtext('value'))}" for d in p.findall("jobDescriptions/jobDescription"))
        meta = ", ".join(f"{k}: {g(k)}" for k in ("seniority", "yearsOfExperience", "schedule", "employmentType") if g(k))
        desc = desc or meta
        pid = g("id")
        out.append(Opening(pid, g("name"), ", ".join(x for x in offices if x), f"https://{c.slug}.jobs.personio.de/job/{pid}",
                           parse_date(g("createdAt")), None, {"description": desc}))
    return out


def _teamtailor_list(c: Company) -> list[Opening]:
    host = c.slug if "." in c.slug else f"{c.slug}.teamtailor.com"
    root = _xml(http.get_text(f"https://{host}/jobs.rss"))
    out = []
    for it in root.iter("item"):
        locs = [_s(x.text) for x in it.iter() if x.tag.split("}")[-1] in ("city", "country", "location", "name") and _s(x.text)]
        rm = [_s(x.text) for x in it.iter() if x.tag.split("}")[-1] == "remoteStatus" and _s(x.text) not in ("", "none")]
        loc = ", ".join(dict.fromkeys(locs)) + (f" ({rm[0]})" if rm else "")
        out.append(Opening(_s(it.findtext("guid") or it.findtext("link")), _s(it.findtext("title")), loc, _s(it.findtext("link")),
                           parse_date(it.findtext("pubDate")), ("fully" in rm[0].lower()) if rm else None,
                           {"description": _s(it.findtext("description"))}))
    return out


def _breezy_list(c: Company) -> list[Opening]:
    out = []
    for j in http.get_json(f"https://{c.slug}.breezy.hr/json"):
        loc = j.get("location") or {}
        text = ", ".join(x for x in (_s(loc.get("city")), _s((loc.get("country") or {}).get("name"))) if x)
        if loc.get("is_remote"):
            text = f"{text} (remote)" if text else "Remote"
        out.append(Opening(_s(j.get("id")), _s(j.get("name")), text, _s(j.get("url")), parse_date(j.get("published_date")),
                           bool(loc.get("is_remote")) or None, {"description": _s(j.get("description"))}))
    return out


def _wd_parts(c: Company) -> tuple[str, str, str]:
    host, tenant, site = c.slug.split("/")
    return host, tenant, site


def _workday_list(c: Company) -> list[Opening]:
    host, tenant, site = _wd_parts(c)
    out, offset = [], 0
    while offset < 100:  # the first 100 results for the search text are plenty (Workday is slow)
        d = _post_json(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                       {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": c.search or ""})
        jobs = d.get("jobPostings") or []
        for j in jobs:
            path = _s(j.get("externalPath"))
            out.append(Opening(path, _s(j.get("title")), _s(j.get("locationsText")), f"https://{host}/{site}{path}", None, None,
                               {"path": path}))
        offset += 20
        if len(jobs) < 20:
            break
    return out


def _workday_detail(c: Company, o: Opening) -> str:
    host, tenant, site = _wd_parts(c)
    d = http.get_json(f"https://{host}/wday/cxs/{tenant}/{site}{o.raw['path']}")
    info = d.get("jobPostingInfo") or {}
    o.posted = parse_date(info.get("startDate")) or o.posted
    if info.get("location"):
        o.location = ", ".join(x for x in [_s(info.get("location")), *[_s(x) for x in info.get("additionalLocations") or []]] if x)
    return _s(info.get("jobDescription"))


ADAPTERS: dict[str, tuple[Callable, Callable | None]] = {
    "greenhouse": (_gh_list, _gh_detail),
    "lever": (_lever_list, None),
    "ashby": (_ashby_list, None),
    "workable": (_workable_list, None),
    "smartrecruiters": (_sr_list, _sr_detail),
    "recruitee": (_recruitee_list, None),
    "personio": (_personio_list, None),
    "teamtailor": (_teamtailor_list, None),
    "breezy": (_breezy_list, None),
    "workday": (_workday_list, _workday_detail),
}


def openings(c: Company) -> list[Opening]:
    c.validate()
    if not isinstance(c.search, list):
        return ADAPTERS[c.ats][0](c)
    # Big boards (e.g. a bank's Workday) are searched once per term; results are merged without duplicates.
    seen, out = set(), []
    for term in c.search or [""]:
        for o in ADAPTERS[c.ats][0](replace(c, search=term)):
            if o.id not in seen:
                seen.add(o.id)
                out.append(o)
    return out


def to_posting(c: Company, o: Opening) -> Posting:
    """Phase 2: full description (one extra call for ATSs that need it)."""
    detail = ADAPTERS[c.ats][1]
    desc = o.raw.get("description") or ""
    if detail:
        desc = detail(c, o) or desc
    return Posting("companies", f"{c.name} careers", o.url, o.title, c.name, o.location, desc, o.posted,
                   list(c.tags), "", o.remote, f"{c.ats}:{c.slug}:{o.id}")


# ------------------------------------------------------------------ detect

_PATTERNS = [
    ("greenhouse", r"^(?:job-)?boards(?:-api)?\.greenhouse\.io$", r"^/(?:v1/boards/)?([A-Za-z0-9_-]+)"),
    ("lever", r"^jobs\.(?:eu\.)?lever\.co$", r"^/([A-Za-z0-9_.-]+)"),
    ("ashby", r"^jobs\.ashbyhq\.com$", r"^/([A-Za-z0-9_.%-]+)"),
    ("workable", r"^apply\.workable\.com$", r"^/([A-Za-z0-9_-]+)"),
    ("smartrecruiters", r"^(?:jobs|careers)\.smartrecruiters\.com$", r"^/([A-Za-z0-9_-]+)"),
]
_SUB = [
    ("recruitee", r"^([a-z0-9-]+)\.recruitee\.com$"),
    ("personio", r"^([a-z0-9-]+)\.jobs\.personio\.(?:de|com)$"),
    ("teamtailor", r"^([a-z0-9-]+)\.teamtailor\.com$"),
    ("breezy", r"^([a-z0-9-]+)\.breezy\.hr$"),
]


def detect(url: str) -> tuple[str, str] | None:
    """Careers URL → (ats, slug), or None. E.g. https://job-boards.greenhouse.io/bugcrowd/jobs/1 → (greenhouse, bugcrowd)."""
    u = urlparse(url.strip())
    host, path = (u.hostname or "").lower(), u.path or "/"
    if host.endswith("greenhouse.io"):
        from urllib.parse import parse_qs
        q = parse_qs(u.query)
        token = (q.get("for") or q.get("job_board") or q.get("board") or [None])[0]
        if token and _SLUG.match(token):
            return "greenhouse", token  # e.g. my.greenhouse.io/…?job_board=acme, embed/job_board?for=acme
    for ats, h, p in _PATTERNS:
        if re.match(h, host):
            m = re.match(p, path)
            if m and m.group(1) not in ("embed", "jobs", "v1"):
                return ats, m.group(1)
            if ats == "greenhouse":
                from urllib.parse import parse_qs
                token = (parse_qs(u.query).get("for") or [None])[0]
                if token:
                    return ats, token
    for ats, h in _SUB:
        m = re.match(h, host)
        if m:
            return ats, m.group(1)
    m = re.match(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", host)
    if m:
        parts = [p for p in path.split("/") if p]
        if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
            parts = parts[1:]
        if parts:
            return "workday", f"{host}/{m.group(1)}/{parts[0]}"
    return None
