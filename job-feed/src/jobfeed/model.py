"""A job posting as collected from a source, before checks."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class Posting:
    source: str  # source key, e.g. "remotive"
    source_name: str  # display name for credit, e.g. "Remotive"
    url: str  # original posting URL (always link here; sources require it)
    title: str
    company: str = ""
    location: str = ""  # free text from the source ("Worldwide", "EMEA", "Madrid, Spain")
    description: str = ""  # may be HTML; sanitised later
    posted: dt.datetime | None = None
    tags: list[str] = field(default_factory=list)
    salary_text: str = ""
    remote: bool | None = None
    source_id: str = ""

    @property
    def uid(self) -> str:
        """Stable id for this source's posting."""
        return hashlib.sha1(f"{self.source}|{self.source_id or self.url}".encode()).hexdigest()[:16]

    @property
    def dedupe_key(self) -> str:
        """Same job on several boards → same key (company + title, normalised)."""
        def norm(s: str) -> str:
            s = unicodedata.normalize("NFKD", s.lower())
            s = "".join(c for c in s if not unicodedata.combining(c))
            s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)  # "(m/f/d)", "[Remote]"
            s = re.sub(r"\b(remote|remoto|hybrid|m/f/d|m/w/d|h/m|f/m|x/f/m)\b", " ", s)
            return re.sub(r"[^a-z0-9]+", " ", s).strip()
        return hashlib.sha1(f"{norm(self.company)}|{norm(self.title)}".encode()).hexdigest()[:16]

    def text_for_check(self) -> str:
        """What remote-check reads: the board's location label goes first, as a hint."""
        parts = [self.description]
        if self.location:
            parts.insert(0, f"Location: {self.location}")
        return "\n\n".join(p for p in parts if p)


def parse_date(value) -> dt.datetime | None:
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            v = float(value)
            if v > 1e12:  # milliseconds
                v /= 1000
            return dt.datetime.fromtimestamp(v, dt.timezone.utc)
        s = str(value).strip().replace("Z", "+00:00")
        try:
            d = dt.datetime.fromisoformat(s)
        except ValueError:
            from email.utils import parsedate_to_datetime
            d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None
