"""One feed run: fetch → filter → de-duplicate → check (remote-check) → fit (cvtracks) → store → notify."""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field

from remote_eligibility import Profile, assess, prepare_job

from .config import VERDICTS, FeedConfig
from .model import Posting
from .sources import SOURCES, Search
from .store import Store, now
from .training import not_a_job

log = logging.getLogger("jobfeed")
TEXT_LIMIT = 20_000


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _has(folded: str, term: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(fold(term))}(?![\w])", folded) is not None


def relevant(p: Posting, s: Search) -> bool:
    """A keyword in the title, or two different keywords in the text; and no excluded word in the title."""
    title = fold(p.title)
    if any(_has(title, x) for x in s.exclude_title):
        return False
    if any(_has(title, k) for k in s.keywords):
        return True
    body = fold(p.description[:15000] + " " + " ".join(p.tags))
    return sum(1 for k in s.keywords if _has(body, k)) >= s.min_description_hits


def fresh(p: Posting, s: Search) -> bool:
    # A company's own job system (and its careers pages) only lists open roles: their age doesn't matter.
    if p.posted is None or p.source in ("companies", "careers"):
        return True
    return dt.datetime.now(dt.timezone.utc) - p.posted <= dt.timedelta(days=s.max_age_days)


@dataclass
class RunResult:
    fetched: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)  # sources not due yet
    needs_setup: dict[str, str] = field(default_factory=dict)  # sources missing a key: not a failed run
    new: list[dict] = field(default_factory=list)
    seen: int = 0
    filtered: int = 0
    not_jobs: int = 0  # course/bootcamp adverts and (unless allowed) student internships
    notified: int = 0
    notify_errors: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"fetched": self.fetched, "errors": self.errors, "skipped": self.skipped,
                "needs_setup": self.needs_setup, "new": len(self.new),
                "good": sum(1 for j in self.new if j["good"]), "seen": self.seen, "filtered": self.filtered,
                "not_jobs": self.not_jobs,
                "notified": self.notified, "notify_errors": self.notify_errors}


def _fit(master, track, text: str):
    if master is None or track is None:
        return None, [], []
    from cvtracks import tailor

    _, report = tailor(master, track, text)
    m, g = len(report.matched), len(report.gaps)
    return (round(100 * m / (m + g)) if m + g else None), report.matched[:15], report.gaps[:15]


def evaluate(p: Posting, s: Search, profile: Profile, master=None, tracks=None, llm=None) -> dict:
    """Check one posting. Returns a row for the store."""
    job = {"title": p.title, "company": p.company, "location": p.location, "description": p.text_for_check(),
           "job_url": p.url, "is_remote": p.remote}
    a = assess(job, profile, llm=llm)
    clean, _ = prepare_job(dict(job))  # sanitised text: what we store and later tailor from
    folded_loc = fold(f"{p.location} {clean.description[:3000]}")
    local = any(_has(folded_loc, loc) for loc in s.locations)
    track = (tracks or {}).get(s.track) if s.track else None
    fit, matched, gaps = _fit(master, track, clean.description)
    # trap words only (e.g. "mango"); other injection findings are summarised by `risk`
    traps = [f.get("trap_word") or "?" for f in a.safety["findings"] if f["category"] == "ai_trap"]
    return {
        "uid": p.uid, "dedupe": p.dedupe_key, "search": s.name, "source": p.source, "source_name": p.source_name,
        "url": p.url, "title": p.title[:300], "company": p.company[:200], "location": p.location[:300],
        "posted": p.posted.isoformat() if p.posted else None, "best": a.best,
        "verdicts": {c.country: c.verdict for c in a.countries}, "mode": a.mode, "salary": a.salary,
        "languages": a.languages, "risk": a.safety["risk"], "traps": traps, "fit": fit, "matched": matched,
        "gaps": gaps, "local_match": int(local), "text": clean.description[:TEXT_LIMIT],
    }


def hide_not_jobs(store: Store, internships_ok: bool = False) -> int:
    """Dismiss stored, still-new posts that turn out to be courses or (unless allowed) student internships."""
    n = 0
    for j in store.jobs(status="new", limit=5000):
        why = not_a_job(j.get("title") or "", j.get("text") or "", internships_ok=internships_ok)
        if why:
            store.set_status(j["uid"], "dismissed", log=False)
            store.add_event(j["uid"], "note", f"Hidden automatically: {why}, not a job offer")
            n += 1
    return n


# When a post doesn't say whether it's remote ("unknown" mode), remote-check can only judge the text, which
# rarely restricts anything; so the location must be plausible: one of your countries (by name, city or
# code), a region that includes one, or a remote word.
_REMOTE_PLACE = re.compile(r"\b(worldwide|anywhere|global|remote|remoto|teletrabajo|telemunca|la distanta|"
                           r"home[\s-]based|work from home|wfh)\b")


def plausible_place(location: str, countries: set[str]) -> bool:
    from remote_eligibility.geo import find_places, label_countries
    if not location:
        return False
    if _REMOTE_PLACE.search(fold(location)):
        return True
    for p in find_places(location):
        if p.kind == "anywhere" or (p.kind == "region" and p.countries & countries):
            return True
    return bool(label_countries(location) & countries)


def regrade(store: Store, cfg: FeedConfig, countries: set[str]) -> int:
    """Re-apply is_good() to stored new jobs, so rule changes also fix what's already in the feed."""
    searches = {s.name: s for s in cfg.searches}
    changed = 0
    for j in store.jobs(status="new", limit=5000):
        s = searches.get(j.get("search"))
        if s is None or not j.get("best"):
            continue
        row = dict(j, languages=j.get("languages") or {})
        good = int(is_good(row, s, cfg.notify.min_verdict, cfg.notify.local_matches, countries))
        if good != int(j.get("good") or 0):
            store.set_good(j["uid"], good)
            changed += 1
    return changed


def is_good(row: dict, s: Search, min_verdict: str, local_ok: bool, countries: set[str] = frozenset()) -> bool:
    if row["languages"].get("missing"):
        return False
    if row.get("mode") in (None, "", "unknown") and not row.get("local_match") \
            and not plausible_place(row.get("location") or "", set(countries)):
        return False
    # A "yes" on an office or hybrid job means "the office is in your country", not "you can do it from home":
    # those match only when they are near home.
    remote_ok = VERDICTS.index(row["best"]) >= VERDICTS.index(min_verdict) and row["mode"] not in ("onsite", "hybrid")
    if s.remote_only:
        return remote_ok
    return remote_ok or (local_ok and bool(row["local_match"]))


def _helper(cfg: FeedConfig):
    """The optional browser helper (e.g. Crawl4AI on another machine), if configured."""
    if not cfg.browser_helper.get("url"):
        return None
    from .browser_helper import BrowserHelper
    return BrowserHelper.from_config(cfg.browser_helper)


def run(cfg: FeedConfig, store: Store, profile: Profile, *, master=None, tracks=None, llm=None,
        force: bool = False, notify: bool = True) -> RunResult:
    started = now()
    res = RunResult()
    # Fetch each source once per run (shared sources serve every search), honouring min intervals.
    cache: dict[tuple, list[Posting]] = {}
    needed = {k for s in cfg.searches for k in s.sources}
    for key in sorted(needed):
        src = SOURCES[key]
        missing = [e for e in src.env if not os.environ.get(e)]
        if missing:
            res.needs_setup[key] = f"needs environment variable(s) {', '.join(missing)}"
            continue
        if not force and not store.source_due(key, src.min_interval_h):
            res.skipped.append(key)
            continue
        searches = [None] if src.shared else [s for s in cfg.searches if key in s.sources]
        total, err = 0, None
        for s in searches:
            opts = {**cfg.source_options.get(key, {}), **((s.options.get(key) if s else None) or {}),
                    "_searches": cfg.searches, "_companies": cfg.companies, "_careers": cfg.careers,
                    "_store": store, "_browser_helper": _helper(cfg)}
            try:
                items = src.fetch(s or cfg.searches[0], opts)
            except Exception as e:  # one broken source must not stop the run
                err = f"{type(e).__name__}: {e}"[:300]
                log.warning("source %s failed: %s", key, err)
                continue
            cache[(key, s.name if s else None)] = items
            total += len(items)
            if opts.get("_warnings"):
                res.errors[f"{key} (partial)"] = "; ".join(opts["_warnings"][:5])[:300]
        store.source_result(key, total, err)
        res.fetched[key] = total
        if err:
            res.errors[key] = err

    res.not_jobs += hide_not_jobs(store, cfg.include_internships)
    countries = set(profile.countries)
    regrade(store, cfg, countries)
    for s in cfg.searches:
        for key in s.sources:
            for p in cache.get((key, None), []) + cache.get((key, s.name), []):
                if not p.url.startswith(("http://", "https://")) or not p.title:
                    continue
                if not fresh(p, s) or not relevant(p, s):
                    res.filtered += 1
                    continue
                if not_a_job(p.title, p.description, internships_ok=cfg.include_internships):
                    res.not_jobs += 1
                    continue
                known = store.known(p.uid, p.dedupe_key)
                if known:
                    store.touch(known["uid"], also=None if known["uid"] == p.uid else p.source_name)
                    res.seen += 1
                    continue
                try:
                    row = evaluate(p, s, profile, master, tracks, llm)
                except Exception as e:
                    log.warning("could not evaluate %s: %s", p.url, e)
                    continue
                row["good"] = int(is_good(row, s, cfg.notify.min_verdict, cfg.notify.local_matches, countries))
                store.insert(row)
                res.new.append(row)

    if notify:
        from .notify import send

        good = [r for r in res.new if r["good"]][: cfg.notify.max_per_run]
        if good:
            sent, errors = send(cfg.notify, good, extra=max(0, sum(r["good"] for r in res.new) - len(good)))
            res.notify_errors = errors
            if sent:
                store.mark_notified([r["uid"] for r in good])
                res.notified = len(good)
    store.log_run(started, not res.errors, res.summary())
    return res
