"""Job sources. Each source turns one search into a list of Postings. Add one with @source."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..model import Posting


@dataclass
class Search:
    name: str
    keywords: list[str]
    sources: list[str]
    track: str | None = None  # cvtracks track for the fit score
    exclude_title: list[str] = field(default_factory=list)
    remote_only: bool = True
    locations: list[str] = field(default_factory=list)  # on-site/hybrid OK if the location mentions one of these
    max_age_days: int = 21
    min_description_hits: int = 3  # without a keyword in the title, this many different keywords in the text
    options: dict = field(default_factory=dict)  # per-source options, e.g. {"jobicy": {"geo": "europe"}}


@dataclass
class Source:
    key: str
    name: str  # shown as "via <name>" (most sources' terms require crediting them)
    fetch: Callable[[Search, dict], list[Posting]]
    min_interval_h: float = 1.0  # don't call more often than this
    env: tuple[str, ...] = ()  # required environment variables
    homepage: str = ""
    shared: bool = True  # one call serves every search (results filtered locally)
    countries: frozenset[str] = frozenset()  # ISO codes it covers when it is country-scoped (empty: not country-scoped)
    signup: str = ""  # where to get the key named in env


SOURCES: dict[str, Source] = {}


def source(key: str, name: str, *, min_interval_h: float = 1.0, env: tuple[str, ...] = (), homepage: str = "",
           shared: bool = True, countries: tuple[str, ...] = (), signup: str = ""):
    def deco(func):
        SOURCES[key] = Source(key, name, func, min_interval_h, env, homepage, shared, frozenset(countries), signup)
        return func
    return deco


from . import builtin  # noqa: E402,F401
