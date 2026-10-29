"""Built-in sources: public job APIs and feeds that allow reuse with credit and a link back."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from .. import http
from ..model import Posting, parse_date
from . import Search, source


def _s(v) -> str:
    return "" if v is None else str(v).strip()


@source("remotive", "Remotive", min_interval_h=6, homepage="https://remotive.com")
def remotive(search: Search, opts: dict) -> list[Posting]:
    # Remotive returns a small batch per query and asks for only a few calls a day: query a few categories.
    jobs = []
    for cat in opts.get("categories", ["information-technology", "devops", "customer-service"]):
        if re.fullmatch(r"[a-z0-9-]+", cat):
            jobs += http.get_json(f"https://remotive.com/api/remote-jobs?category={cat}", delay=2).get("jobs", [])
    out = []
    for j in jobs:
        out.append(Posting("remotive", "Remotive", _s(j.get("url")), _s(j.get("title")), _s(j.get("company_name")),
                           _s(j.get("candidate_required_location")), _s(j.get("description")),
                           parse_date(j.get("publication_date")), list(j.get("tags") or []), _s(j.get("salary")), True,
                           _s(j.get("id"))))
    return out


@source("remoteok", "Remote OK", min_interval_h=3, homepage="https://remoteok.com")
def remoteok(search: Search, opts: dict) -> list[Posting]:
    data = http.get_json("https://remoteok.com/api")
    out = []
    for j in data[1:] if isinstance(data, list) else []:  # item 0 is the legal notice
        sal = ""
        if j.get("salary_min") or j.get("salary_max"):
            sal = f"{j.get('salary_min') or ''}-{j.get('salary_max') or ''} USD per year"
        out.append(Posting("remoteok", "Remote OK", _s(j.get("url")), _s(j.get("position")), _s(j.get("company")),
                           _s(j.get("location")), _s(j.get("description")), parse_date(j.get("date") or j.get("epoch")),
                           list(j.get("tags") or []), sal, True, _s(j.get("id"))))
    return out


@source("himalayas", "Himalayas", min_interval_h=3, homepage="https://himalayas.app")
def himalayas(search: Search, opts: dict) -> list[Posting]:
    out, cursor = [], None
    for _ in range(int(opts.get("pages", 5))):
        url = "https://himalayas.app/jobs/api?limit=20" + (f"&cursor={quote(cursor)}" if cursor else "")
        data = http.get_json(url, delay=1.5)
        for j in data.get("jobs", []):
            loc = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
            tz = j.get("timezoneRestrictions") or []
            desc = _s(j.get("description"))
            if tz:
                desc = f"Time zones: {', '.join('UTC%+g' % float(t) for t in tz if _num(t))}\n\n{desc}"
            sal = ""
            if j.get("minSalary") or j.get("maxSalary"):
                sal = f"{j.get('minSalary') or ''}-{j.get('maxSalary') or ''} {j.get('currency') or ''} per {j.get('salaryPeriod') or 'year'}"
            out.append(Posting("himalayas", "Himalayas", _s(j.get("applicationLink")), _s(j.get("title")),
                               _s(j.get("companyName")), loc, desc, parse_date(j.get("pubDate")),
                               list(j.get("categories") or []), sal, True, _s(j.get("guid"))))
        cursor = data.get("nextCursor")
        if not cursor:
            break
    return out


def _num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


@source("jobicy", "Jobicy", min_interval_h=1, homepage="https://jobicy.com")
def jobicy(search: Search, opts: dict) -> list[Posting]:
    geo = opts.get("geo", "europe")
    data = http.get_json(f"https://jobicy.com/api/v2/remote-jobs?count=100&geo={quote(geo)}")
    out = []
    for j in data.get("jobs", []):
        sal = ""
        if j.get("salaryMin"):
            sal = f"{j.get('salaryMin')}-{j.get('salaryMax') or ''} {j.get('salaryCurrency') or ''} per {j.get('salaryPeriod') or 'year'}"
        out.append(Posting("jobicy", "Jobicy", _s(j.get("url")), _s(j.get("jobTitle")), _s(j.get("companyName")),
                           _s(j.get("jobGeo")), _s(j.get("jobDescription")), parse_date(j.get("pubDate")),
                           [_s(x) for x in (j.get("jobIndustry") or [])], sal, True, _s(j.get("id"))))
    return out


@source("arbeitnow", "Arbeitnow", min_interval_h=3, homepage="https://www.arbeitnow.com")
def arbeitnow(search: Search, opts: dict) -> list[Posting]:
    out = []
    for page in range(1, int(opts.get("pages", 3)) + 1):
        data = http.get_json(f"https://www.arbeitnow.com/api/job-board-api?page={page}", delay=1.5)
        for j in data.get("data", []):
            out.append(Posting("arbeitnow", "Arbeitnow", _s(j.get("url")), _s(j.get("title")), _s(j.get("company_name")),
                               _s(j.get("location")), _s(j.get("description")), parse_date(j.get("created_at")),
                               list(j.get("tags") or []), "", bool(j.get("remote")), _s(j.get("slug"))))
        if not (data.get("links") or {}).get("next"):
            break
    return out


@source("workingnomads", "Working Nomads", min_interval_h=3, homepage="https://www.workingnomads.com")
def workingnomads(search: Search, opts: dict) -> list[Posting]:
    data = http.get_json("https://www.workingnomads.com/api/exposed_jobs/")
    out = []
    for j in data if isinstance(data, list) else []:
        tags = [t for t in _s(j.get("tags")).split(",") if t]
        out.append(Posting("workingnomads", "Working Nomads", _s(j.get("url")), _s(j.get("title")),
                           _s(j.get("company_name")), _s(j.get("location")), _s(j.get("description")),
                           parse_date(j.get("pub_date")), tags, "", True, _s(j.get("url"))))
    return out


# ------------------------------------------------------------------- RSS

def _parse_rss(xml: str) -> list[dict]:
    # Third-party XML: refuse DTDs/entities outright (XML bombs, external entities).
    if re.search(r"<!(DOCTYPE|ENTITY)", xml[:5000], re.IGNORECASE):
        raise http.FetchError("feed declares a DOCTYPE/ENTITY; refusing to parse")
    root = ET.fromstring(http.html_entities_to_numeric(xml))
    items = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        d: dict = {}
        for child in it:
            name = child.tag.split("}")[-1]
            if name == "link" and child.get("href"):
                d["link"] = child.get("href")
            else:
                d[name] = (child.text or "").strip()
        items.append(d)
    return items


@source("weworkremotely", "We Work Remotely", min_interval_h=2, homepage="https://weworkremotely.com")
def weworkremotely(search: Search, opts: dict) -> list[Posting]:
    out = []
    for cat in opts.get("categories", ["remote-devops-sysadmin-jobs", "remote-customer-support-jobs"]):
        if not re.fullmatch(r"[a-z0-9-]+", cat):
            continue
        for it in _parse_rss(http.get_text(f"https://weworkremotely.com/categories/{cat}.rss")):
            company, _, title = _s(it.get("title")).partition(": ")
            if not title:
                company, title = "", company
            out.append(Posting("weworkremotely", "We Work Remotely", _s(it.get("link")), title.strip(), company.strip(),
                               _s(it.get("region")) or "Anywhere", _s(it.get("description")), parse_date(it.get("pubDate")),
                               [_s(it.get("category"))], "", True, _s(it.get("guid") or it.get("link"))))
    return out


@source("rss", "RSS", min_interval_h=1, shared=False)
def rss(search: Search, opts: dict) -> list[Posting]:
    """Any RSS/Atom feed: options = {"urls": [...], "name": "My board"}."""
    out = []
    name = opts.get("name", "RSS")
    for url in opts.get("urls", []):
        for it in _parse_rss(http.get_text(url)):
            out.append(Posting("rss", name, _s(it.get("link")), _s(it.get("title")), _s(it.get("author") or it.get("creator")),
                               _s(it.get("location") or it.get("region")),
                               _s(it.get("description") or it.get("summary") or it.get("content")),
                               parse_date(it.get("pubDate") or it.get("published") or it.get("updated")), [], "", None,
                               _s(it.get("guid") or it.get("id") or it.get("link"))))
    return out


# --------------------------------------------------------- with credentials

@source("infojobs", "InfoJobs", min_interval_h=2, env=("INFOJOBS_CLIENT_ID", "INFOJOBS_CLIENT_SECRET"),
        homepage="https://www.infojobs.net", shared=False)
def infojobs(search: Search, opts: dict) -> list[Posting]:
    """Official InfoJobs API (register an app at developer.infojobs.net). Experimental."""
    import base64

    auth = base64.b64encode(f"{os.environ['INFOJOBS_CLIENT_ID']}:{os.environ['INFOJOBS_CLIENT_SECRET']}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    out = []
    for kw in search.keywords[: int(opts.get("max_keywords", 3))]:
        q = f"q={quote(kw)}&maxResults=50&order=updated-desc"
        if opts.get("province"):
            q += f"&province={quote(opts['province'])}"
        data = http.get_json(f"https://api.infojobs.net/api/9/offer?{q}", headers=headers)
        for j in data.get("items", []):
            city = _s(j.get("city"))
            prov = _s((j.get("province") or {}).get("value"))
            remote = _s((j.get("teleworking") or {}).get("value"))
            out.append(Posting("infojobs", "InfoJobs", _s(j.get("link")), _s(j.get("title")),
                               _s((j.get("author") or {}).get("name")), ", ".join(x for x in (city, prov, "España") if x),
                               f"Teletrabajo: {remote}\n\n{_s(j.get('requirementMin'))}", parse_date(j.get("published")),
                               [], _s((j.get("salaryDescription") or "")), None, _s(j.get("id"))))
    return out


# Countries with an Adzuna site. Adzuna's docs don't list them; a country that isn't there just fails on its own.
ADZUNA_COUNTRIES = ("AT", "AU", "BE", "BR", "CA", "CH", "DE", "ES", "FR", "GB", "IN", "IT", "MX", "NL", "NZ", "PL", "SG", "US", "ZA")


@source("adzuna", "Adzuna", min_interval_h=6, env=("ADZUNA_APP_ID", "ADZUNA_APP_KEY"), homepage="https://www.adzuna.com",
        shared=False, countries=ADZUNA_COUNTRIES, signup="https://developer.adzuna.com/signup")
def adzuna(search: Search, opts: dict) -> list[Posting]:
    """Official Adzuna API: one call per country and keyword. Each person registers a free key; Adzuna's terms
    allow it for personal job search. Options: countries = ["IN", ...] (required), max_keywords, results.

    Adzuna returns a shortened description, so the location label carries the country for remote-check.
    """
    from urllib.parse import urlencode

    countries = [c for c in (str(x).upper() for x in opts.get("countries", [])) if c in ADZUNA_COUNTRIES]
    out, failed = [], []
    for cc in countries:
        for kw in search.keywords[: int(opts.get("max_keywords", 3))]:
            params = {"app_id": os.environ["ADZUNA_APP_ID"], "app_key": os.environ["ADZUNA_APP_KEY"], "what": kw,
                      "results_per_page": min(int(opts.get("results", 50)), 50), "max_days_old": search.max_age_days,
                      "sort_by": "date", "content-type": "application/json"}
            try:
                data = http.get_json(f"https://api.adzuna.com/v1/api/jobs/{cc.lower()}/search/1?{urlencode(params)}")
            except http.FetchError as e:  # the message names the host, never the key
                failed.append(f"{cc}: {e}")
                break
            for j in data.get("results", []):
                loc = j.get("location") or {}
                area = loc.get("area") or []
                place = _s(loc.get("display_name"))
                if area and _s(area[0]) and _s(area[0]).lower() not in place.lower():
                    place = f"{place}, {_s(area[0])}" if place else _s(area[0])
                lo, hi = j.get("salary_min"), j.get("salary_max")
                salary = ""
                if str(j.get("salary_is_predicted", "1")) == "0" and isinstance(lo, (int, float)):
                    salary = f"{lo:,.0f}" + (f"–{hi:,.0f}" if isinstance(hi, (int, float)) and hi != lo else "") + " a year"
                out.append(Posting("adzuna", "Adzuna", _s(j.get("redirect_url")), _s(j.get("title")),
                                   _s((j.get("company") or {}).get("display_name")), place, _s(j.get("description")),
                                   parse_date(j.get("created")), [_s((j.get("category") or {}).get("label"))] if j.get("category") else [],
                                   salary, None, _s(j.get("id"))))
    if failed and not out:
        raise http.FetchError("; ".join(failed[:3]))
    opts.setdefault("_warnings", []).extend(failed)  # shown as "adzuna (partial)" on the Feed page
    return out


@source("usajobs", "USAJOBS", min_interval_h=6, env=("USAJOBS_API_KEY", "USAJOBS_EMAIL"), homepage="https://www.usajobs.gov",
        shared=False, countries=("US",), signup="https://developer.usajobs.gov/")
def usajobs(search: Search, opts: dict) -> list[Posting]:
    """Official USAJOBS API (US federal jobs; most need US citizenship). Free key; the email it was requested with
    goes in the User-Agent header, as the API requires. Its terms allow any lawful use and ask to credit USAJOBS."""
    from urllib.parse import urlencode

    headers = {"User-Agent": os.environ["USAJOBS_EMAIL"], "Authorization-Key": os.environ["USAJOBS_API_KEY"]}
    out = []
    for kw in search.keywords[: int(opts.get("max_keywords", 3))]:
        params = {"Keyword": kw, "DatePosted": min(search.max_age_days, 60), "ResultsPerPage": int(opts.get("results", 100))}
        if search.remote_only:
            params["RemoteIndicator"] = "True"
        data = http.get_json(f"https://data.usajobs.gov/api/search?{urlencode(params)}", headers=headers)
        for item in ((data.get("SearchResult") or {}).get("SearchResultItems") or []):
            d = item.get("MatchedObjectDescriptor") or {}
            place = _s(d.get("PositionLocationDisplay")) or "; ".join(
                _s(x.get("LocationName")) for x in d.get("PositionLocation") or [] if x.get("LocationName"))
            if "united states" not in place.lower():
                place = f"{place}, United States" if place else "United States"
            pay = (d.get("PositionRemuneration") or [{}])[0]
            salary = ""
            if pay.get("MinimumRange"):
                salary = f"{_s(pay.get('MinimumRange'))}–{_s(pay.get('MaximumRange'))} USD {_s(pay.get('Description') or pay.get('RateIntervalCode'))}".strip()
            details = ((d.get("UserArea") or {}).get("Details") or {})
            text = "\n\n".join(x for x in (_s(details.get("JobSummary")), _s(d.get("QualificationSummary"))) if x)
            org = _s(d.get("OrganizationName")) or _s(d.get("DepartmentName"))
            out.append(Posting("usajobs", "USAJOBS", _s(d.get("PositionURI")), _s(d.get("PositionTitle")), org, place, text,
                               parse_date(d.get("PublicationStartDate")), [], salary,
                               True if search.remote_only else None, _s(d.get("PositionID") or item.get("MatchedObjectId"))))
    return out


# ------------------------------------------------------- company career boards

def _company_postings(c, searches: list, max_details: int) -> list[Posting]:
    """Openings of one ATS company whose title matches a search, with full details."""
    from .. import ats
    from ..pipeline import relevant

    out = []
    matches = [o for o in ats.openings(c)
               if any(relevant(Posting("companies", c.name, o.url, o.title, c.name, o.location, "", o.posted), s)
                      for s in searches)]
    for o in matches[:max_details]:
        try:
            out.append(ats.to_posting(c, o))
        except http.FetchError:
            continue
    return out


@source("companies", "Company career pages", min_interval_h=6, homepage="")
def companies(search: Search, opts: dict) -> list[Posting]:
    """Every [[company]] in feed.toml, via its ATS API. Details are fetched only for openings whose title
    matches one of the searches that use this source, so dozens of companies stay cheap."""
    searches = [s for s in opts.get("_searches", []) if "companies" in s.sources]
    max_details = int(opts.get("max_details", 15))
    out, failed = [], []
    for c in opts.get("_companies", []):
        try:
            out += _company_postings(c, searches, max_details)
        except (http.FetchError, ValueError, KeyError, TypeError) as e:
            failed.append(f"{c.name}: {e}"[:120])
    if failed and not out:
        raise http.FetchError("; ".join(failed[:5]))
    opts.setdefault("_warnings", []).extend(failed)
    return out


# ------------------------------------------------- boards with usable feeds

@source("cybersecurityjobsite", "CyberSecurityJobsite", min_interval_h=3, homepage="https://www.cybersecurityjobsite.com",
        shared=False)
def cybersecurityjobsite(search: Search, opts: dict) -> list[Posting]:
    """Madgex job board RSS with keyword search (opts: base_url to use another Madgex board)."""
    base = opts.get("base_url", "https://www.cybersecurityjobsite.com").rstrip("/")
    name = opts.get("name", "CyberSecurityJobsite")
    out = []
    for kw in search.keywords[: int(opts.get("max_keywords", 4))]:
        for it in _parse_rss(http.get_text(f"{base}/jobsrss/?Keywords={quote(kw)}", delay=2)):
            company, _, title = _s(it.get("title")).partition(": ")
            if not title:
                company, title = "", company
            out.append(Posting("cybersecurityjobsite", name, _s(it.get("link")), title.strip(), company.strip().rstrip("."),
                               _s(it.get("location")), _s(it.get("description")), parse_date(it.get("pubDate")), [], "", None,
                               _s(it.get("guid") or it.get("link"))))
    return out


@source("nodesk", "NoDesk", min_interval_h=3, homepage="https://nodesk.co")
def nodesk(search: Search, opts: dict) -> list[Posting]:
    out = []
    for it in _parse_rss(http.get_text("https://nodesk.co/remote-jobs/index.xml")):
        title, _, company = _s(it.get("title")).rpartition(" at ")
        if not title:
            title, company = company, ""
        out.append(Posting("nodesk", "NoDesk", _s(it.get("link")), title.strip(), company.strip(), "", _s(it.get("description")),
                           parse_date(it.get("pubDate")), [], "", True, _s(it.get("guid") or it.get("link"))))
    return out


# ------------------------------------------------ Foorilla (formerly isecjobs.com)

_FOORILLA = "https://foorilla.com/api/v1"
_foorilla_topics: dict[str, int] = {}


def _foorilla_get(path: str, params: dict) -> dict:
    from urllib.parse import urlencode

    q = urlencode([(k, v) for k, vs in params.items() for v in (vs if isinstance(vs, list) else [vs]) if v not in (None, "")])
    return http.get_json(f"{_FOORILLA}{path}?{q}", headers={"Api-Key": os.environ["FOORILLA_API_KEY"]}, delay=1.5)


def _foorilla_topic_ids(names: list[str]) -> list[int]:
    if not _foorilla_topics:
        page = 1
        while page <= 10:
            d = _foorilla_get("/core/topic/", {"page": page, "page_size": 100})
            for t in d.get("results", []):
                _foorilla_topics[_s(t.get("name")).lower()] = int(t["id"])
            if page >= int(d.get("pages") or 1):
                break
            page += 1
    wanted = [n.lower() for n in names]
    return sorted({tid for name, tid in _foorilla_topics.items() if any(w in name for w in wanted)})


@source("foorilla", "Foorilla", min_interval_h=3, env=("FOORILLA_API_KEY",), homepage="https://foorilla.com", shared=False)
def foorilla(search: Search, opts: dict) -> list[Posting]:
    """Foorilla job API (isecjobs.com now redirects here). Free API key from your Foorilla account.

    opts: topics = ["InfoSec"] (topic name fragments; default: filter by the search keywords in the title),
          remote_only (default: the search's remote_only), pages (default 2).
    """
    import datetime as _dt

    since = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=search.max_age_days)).isoformat(timespec="seconds")
    base = {"published_after": since, "page_size": 100}
    if opts.get("remote_only", search.remote_only):
        base["has_remote"] = "true"
    queries = []
    if opts.get("topics"):
        ids = _foorilla_topic_ids(list(opts["topics"]))
        if not ids:
            raise http.FetchError(f"no Foorilla topic matches {opts['topics']}")
        queries.append({**base, "topic": ids})
    else:
        queries += [{**base, "title": kw} for kw in search.keywords[: int(opts.get("max_keywords", 4))]]
    out = []
    for q in queries:
        for page in range(1, int(opts.get("pages", 2)) + 1):
            d = _foorilla_get("/hiring/job/", {**q, "page": page})
            for j in d.get("results", []):
                if j.get("expired"):
                    continue
                company = j.get("company") or {}
                countries = ", ".join(_s(c.get("name")) for c in j.get("countries") or [])
                regions = ", ".join(_s(r.get("name")) for r in j.get("regions") or [])
                lines = [f"Countries: {countries}" if countries else "", f"Regions: {regions}" if regions else "",
                         "Remote work possible." if j.get("has_remote") else "",
                         f"Experience level: {_s(j.get('experience_level'))}" if j.get("experience_level") else "",
                         f"Language: {_s(j.get('language'))}" if j.get("language") else "",
                         "Topics: " + ", ".join(_s(t.get("name")) for t in j.get("topics") or []) if j.get("topics") else ""]
                lo, hi = j.get("salary_min_eur"), j.get("salary_max_eur")
                sal = f"Salary: €{lo:,.0f} - €{hi:,.0f} per year" if lo and hi else ""
                out.append(Posting("foorilla", "Foorilla", _s(j.get("apply_url")), _s(j.get("title")), _s(company.get("name")),
                                   _s(j.get("location")), "\n".join(x for x in lines + [sal] if x), parse_date(j.get("published")),
                                   [_s(t.get("name")) for t in j.get("tags") or [] if isinstance(t, dict)], sal,
                                   j.get("has_remote") if isinstance(j.get("has_remote"), bool) else None, _s(j.get("id"))))
            if page >= int(d.get("pages") or 1):
                break
    return out



@source("careers", "Careers pages (no API)", min_interval_h=12, homepage="")
def careers_pages(search: Search, opts: dict) -> list[Posting]:
    """Every [[careers]] page in feed.toml: known job system → its API; else JobPosting markup; else page text.
    JavaScript-only pages are handed to the browser helper if one is configured and reachable."""
    from .. import ats, careers as cr

    searches = [s for s in opts.get("_searches", []) if "careers" in s.sources]
    keywords = list(dict.fromkeys(k for s in searches for k in s.keywords))
    store = opts.get("_store")
    helper = opts.get("_browser_helper")
    out, problems = [], []
    for entry in opts.get("_careers", []):
        r = cr.recon(entry["name"], entry["url"], keywords, max_pages=int(entry.get("max_pages", 12)), store=store)
        if r.state == "ats":
            try:
                found = _company_postings(ats.Company(entry["name"], r.ats, r.slug), searches, 15)
                out += found
                r.detail += f"; {len(found)} matching job(s)"
            except (http.FetchError, ValueError) as e:
                r.state, r.detail = "error", f"{r.detail}; {e}"
        elif r.state == "needs_browser" and helper is not None:
            try:
                found = helper.render_jobs(entry["name"], entry["url"], keywords)
                out += found
                r.state, r.detail = "browser", f"read with the browser helper: {len(found)} job(s)"
            except http.FetchError as e:
                r.detail += f" (browser helper: {e})"
        else:
            out += r.postings or []
        if store is not None:
            store.careers_state(entry["url"], entry["name"], r.state, r.detail, r.ats, r.slug,
                                len(r.postings or []) if r.state != "ats" else 0)
        if r.state in ("blocked", "error", "robots"):
            problems.append(f"{entry['name']}: {r.state}")
    opts.setdefault("_warnings", []).extend(problems)
    return out


# ------------------------------------------------ Hipo.ro (Romania)
# Terms allow viewing and storing listings for personal purposes; robots.txt allows the job pages.
# Search: /locuri-de-munca/cautajob/<domain>/<city>/<words> (GET). Job pages carry schema.org JobPosting.

_HIPO = "https://www.hipo.ro"
_HIPO_CARD = re.compile(r'<a\b(?=[^>]*\bclass="job-title")(?=[^>]*\btitle="([^"]*)")(?=[^>]*\bhref="([^"]+)")[^>]*>.*?'
                        r'class="company-name">\s*<span>([^<]*)</span>.*?fa-calendar-alt[^>]*></i>\s*([\d-]{10})?'
                        r'(?:.*?fa-map-marker-alt[^>]*></i>\s*([^<]+))?', re.S)


def _demojibake(s: str) -> str:
    """Some Hipo pages are UTF-8 text decoded as Latin-1 upstream ("â€“" for "–"); undo it when that's the case."""
    if not re.search("[ÂÃâ][\x80-\xbf€™œ“”–—•]", s):
        return s
    for enc in ("latin-1", "cp1252"):
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return s


def _hipo_slug(term: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", term)
    return re.sub(r"[^A-Za-z0-9]+", "-", "".join(c for c in t if not unicodedata.combining(c))).strip("-")


@source("hipo", "Hipo.ro", min_interval_h=12, homepage="https://www.hipo.ro", shared=False)
def hipo(search: Search, opts: dict) -> list[Posting]:
    """Romanian job board. opts: terms (default: the search keywords), max_details (job pages fetched per run)."""
    from html import unescape

    from .. import crawl
    from ..careers import job_postings_jsonld, posting_from_jsonld
    from ..pipeline import _has, fold

    terms = [str(t) for t in (opts.get("terms") or search.keywords)][: int(opts.get("max_terms", 8))]
    cards: dict[str, tuple] = {}
    for term in terms:
        html = crawl.fetch_page(f"{_HIPO}/locuri-de-munca/cautajob/Toate-Domeniile/Toate-Orasele/{_hipo_slug(term)}")
        for title, href, company, date, city in _HIPO_CARD.findall(html):
            cards.setdefault(href, (unescape(title).strip(), unescape(company).strip(), date, (city or "").strip()))
    out, budget = [], int(opts.get("max_details", 15))
    for href, (title, company, date, city) in cards.items():
        url = href if href.startswith("http") else _HIPO + href
        posted = parse_date("-".join(reversed(date.split("-")))) if date else None  # dd-mm-yyyy
        p = Posting("hipo", "Hipo.ro", url, title, company, f"{city.title()}, Romania" if city else "Romania", "",
                    posted, [], "", None, href)
        # Job pages only for titles that name one of the searched roles (keeps requests low).
        if budget > 0 and any(_has(fold(title), k) for k in search.keywords):
            budget -= 1
            try:
                jps = job_postings_jsonld(crawl.fetch_page(url))
            except http.FetchError:
                jps = []
            if jps:
                jp = jps[0]
                full = posting_from_jsonld(jp, url, "Hipo.ro")
                extra = "\n\n".join(str(jp.get(k) or "") for k in ("responsibilities", "qualifications", "jobBenefits"))
                p.description = _demojibake(unescape(full.description + "\n\n" + extra)).strip()
                p.remote = full.remote
                p.location = full.location or p.location
        out.append(p)
    return out
