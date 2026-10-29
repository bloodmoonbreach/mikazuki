"""feed.toml: saved searches, schedule and notifications."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .sources import SOURCES, Search

VERDICTS = ("no", "unlikely", "unclear", "likely", "yes")

EXAMPLE = """\
# job-feed configuration. Searches run every `schedule_hours` (mikazuki) or when you run `job-feed run`.
schedule_hours = 6
use_llm = false            # ask the LLM about unclear posts (slower; needs the LLM host from profile.toml)

[notify]
min_verdict = "likely"     # notify when the best country verdict is at least this…
local_matches = true       # …or when a non-remote search finds a job in one of its `locations`
max_per_run = 10
# Telegram: create a bot with @BotFather, put the token in an env var, and your chat id here.
# telegram_token_env = "TELEGRAM_BOT_TOKEN"
# telegram_chat_id = "123456789"
# ntfy: any topic URL, e.g. "https://ntfy.sh/your-secret-topic"
# ntfy_url = ""
# Webhook (e.g. n8n): receives a JSON list of new matches
# webhook_url = ""

[[search]]
name = "Cybersecurity (remote)"
track = "security"         # cvtracks track used for the fit score (optional)
keywords = ["security analyst", "SOC analyst", "cybersecurity", "cyber security", "penetration tester",
            "pentester", "pentest", "incident response", "blue team", "ciberseguridad", "analista de seguridad"]
exclude_title = ["senior", "sr", "lead", "principal", "staff", "head", "director", "manager", "architect",
                 "vp", "chief", "officer", "sales", "account executive"]
sources = ["remotive", "remoteok", "himalayas", "jobicy", "workingnomads", "weworkremotely", "arbeitnow"]
remote_only = true
max_age_days = 21

[[search]]
name = "IT support (remote or local)"
track = "support"
keywords = ["help desk", "helpdesk", "service desk", "IT support", "technical support", "desktop support",
            "support technician", "soporte técnico", "técnico de soporte", "técnico informático"]
exclude_title = ["senior", "sr", "lead", "manager", "director", "head", "sales"]
sources = ["remotive", "remoteok", "himalayas", "workingnomads", "weworkremotely", "arbeitnow"]
remote_only = false
locations = ["Madrid"]   # on-site/hybrid jobs here count as matches
max_age_days = 21
"""


class ConfigError(ValueError):
    pass


@dataclass
class Notify:
    min_verdict: str = "likely"
    local_matches: bool = True
    max_per_run: int = 10
    telegram_token_env: str | None = None
    telegram_chat_id: str | None = None
    ntfy_url: str | None = None
    webhook_url: str | None = None


@dataclass
class FeedConfig:
    searches: list[Search]
    notify: Notify = field(default_factory=Notify)
    schedule_hours: float = 6.0
    source_options: dict = field(default_factory=dict)
    use_llm: bool = False  # LLM pass for unclear posts during scheduled runs (slower; needs the LLM host up)
    companies: list = field(default_factory=list)  # [[company]] blocks → jobfeed.ats.Company
    careers: list = field(default_factory=list)  # [[careers]] blocks: {name, url, max_pages}
    browser_helper: dict = field(default_factory=dict)  # [browser_helper] url, token_env
    include_internships: bool = False  # student internships (prácticas formativas…); courses and bootcamps are always out


def parse(text: str) -> FeedConfig:
    try:
        d = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"feed.toml: {e}") from e
    n = d.get("notify") or {}
    if "telegram_token" in n:
        raise ConfigError("feed.toml: put the Telegram token in an environment variable and set telegram_token_env")
    unknown_n = set(n) - set(Notify.__dataclass_fields__)
    if unknown_n:
        raise ConfigError(f"[notify]: unknown key(s) {sorted(unknown_n)}")
    notify = Notify(**n)
    if notify.min_verdict not in VERDICTS:
        raise ConfigError(f"[notify] min_verdict must be one of {VERDICTS}")
    searches = []
    for i, s in enumerate(d.get("search") or []):
        w = f"[[search]] #{i + 1}"
        if not s.get("name") or not s.get("keywords"):
            raise ConfigError(f"{w}: needs name and keywords")
        allowed = {"name", "track", "keywords", "exclude_title", "sources", "remote_only", "locations", "max_age_days",
                   "options", "min_description_hits"}
        if set(s) - allowed:
            raise ConfigError(f"{w}: unknown key(s) {sorted(set(s) - allowed)}")
        srcs = s.get("sources") or ["remotive", "remoteok", "himalayas", "jobicy", "workingnomads", "weworkremotely"]
        bad = [x for x in srcs if x not in SOURCES]
        if bad:
            raise ConfigError(f"{w}: unknown source(s) {bad}; available: {', '.join(sorted(SOURCES))}")
        searches.append(Search(name=str(s["name"]), keywords=[str(k) for k in s["keywords"]], sources=srcs,
                               track=s.get("track"), exclude_title=[str(x) for x in s.get("exclude_title", [])],
                               remote_only=bool(s.get("remote_only", True)), locations=[str(x) for x in s.get("locations", [])],
                               max_age_days=int(s.get("max_age_days", 21)), options=dict(s.get("options") or {}),
                               min_description_hits=int(s.get("min_description_hits", 3))))
    names = [s.name for s in searches]
    if len(names) != len(set(names)):
        raise ConfigError("search names must be unique")
    from .ats import Company

    companies = []
    for i, c in enumerate(d.get("company") or []):
        w = f"[[company]] #{i + 1}"
        if set(c) - {"name", "ats", "slug", "search", "tags"}:
            raise ConfigError(f"{w}: unknown key(s) {sorted(set(c) - {'name', 'ats', 'slug', 'search', 'tags'})}")
        if not c.get("ats") or not c.get("slug"):
            raise ConfigError(f"{w}: needs ats and slug")
        raw_search = c.get("search", "")
        search = [str(t) for t in raw_search if str(t).strip()] if isinstance(raw_search, list) else str(raw_search)
        comp = Company(str(c.get("name") or c["slug"]), str(c["ats"]), str(c["slug"]), search,
                       [str(t) for t in c.get("tags", [])])
        try:
            comp.validate()
        except ValueError as e:
            raise ConfigError(f"{w}: {e}") from e
        companies.append(comp)
    careers = []
    for i, c in enumerate(d.get("careers") or []):
        w = f"[[careers]] #{i + 1}"
        if set(c) - {"name", "url", "max_pages"}:
            raise ConfigError(f"{w}: unknown key(s) {sorted(set(c) - {'name', 'url', 'max_pages'})}")
        url = str(c.get("url", ""))
        if not url.lower().startswith(("http://", "https://")):
            raise ConfigError(f"{w}: url must start with http:// or https://")
        careers.append({"name": str(c.get("name") or url), "url": url, "max_pages": min(int(c.get("max_pages", 12)), 40)})
    helper = dict(d.get("browser_helper") or {})
    if "token" in helper:
        raise ConfigError("[browser_helper]: put the token in an environment variable and set token_env")
    return FeedConfig(searches, notify, float(d.get("schedule_hours", 6)), dict(d.get("sources") or {}),
                      bool(d.get("use_llm", False)), companies, careers, helper, bool(d.get("include_internships", False)))


def load(path: str | Path) -> FeedConfig:
    return parse(Path(path).read_text(encoding="utf-8"))
