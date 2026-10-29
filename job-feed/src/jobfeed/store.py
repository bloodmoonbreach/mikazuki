"""SQLite storage for collected jobs, run history and per-source state."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  uid TEXT PRIMARY KEY, dedupe TEXT, search TEXT, source TEXT, source_name TEXT, url TEXT,
  title TEXT, company TEXT, location TEXT, posted TEXT, first_seen TEXT, last_seen TEXT,
  best TEXT, verdicts TEXT, mode TEXT, salary TEXT, languages TEXT, risk TEXT, traps TEXT,
  fit INTEGER, matched TEXT, gaps TEXT, local_match INTEGER DEFAULT 0, good INTEGER DEFAULT 0,
  status TEXT DEFAULT 'new', notified INTEGER DEFAULT 0, also_on TEXT DEFAULT '[]', text TEXT
);
CREATE INDEX IF NOT EXISTS jobs_dedupe ON jobs(dedupe);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, good);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, uid TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL, text TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_uid ON events(uid, at);
CREATE TABLE IF NOT EXISTS blocked (host TEXT PRIMARY KEY, reason TEXT, since TEXT, until TEXT);
CREATE TABLE IF NOT EXISTS careers (
  url TEXT PRIMARY KEY, name TEXT, state TEXT, detail TEXT, ats TEXT, slug TEXT, jobs INTEGER DEFAULT 0, checked TEXT
);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started TEXT, finished TEXT, ok INTEGER, summary TEXT);
CREATE TABLE IF NOT EXISTS sources (key TEXT PRIMARY KEY, last_fetch TEXT, last_error TEXT, last_count INTEGER);
"""
# Pipeline: new → saved → applied → interview → offer; or rejected / withdrawn / dismissed at any point.
STATUSES = ("new", "saved", "applied", "interview", "offer", "rejected", "withdrawn", "dismissed")
TRACKED = ("applied", "interview", "offer", "rejected", "withdrawn")  # statuses that count as an application
EVENT_KINDS = ("note", "applied", "email", "call", "interview", "assessment", "offer", "rejection", "follow-up", "status")
NO_RESPONSE_DAYS = 21

# Columns added after v0.1, with their SQL type (added to existing databases on open).
_EXTRA_COLUMNS = {
    "origin": "TEXT DEFAULT 'feed'",  # feed | manual | import
    "profile": "TEXT",  # e.g. Cybersecurity / Help desk (free text; feed jobs use the search name)
    "platform": "TEXT",  # where you applied / found it, for manual entries (feed jobs: source_name)
    "applied_at": "TEXT",
    "response": "TEXT",  # free text of the last response ("Denied", "Interview 1", …)
    "response_at": "TEXT",
    "cv_file": "TEXT",  # CV sent (path under out/)
    "notes": "TEXT",
    "search_filter": "TEXT",
    "next_action": "TEXT",
    "next_at": "TEXT",
    "updated_at": "TEXT",
}
_JSON = ("verdicts", "salary", "languages", "traps", "matched", "gaps", "also_on")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with self.db() as c:
            c.executescript(SCHEMA)
            have = {row[1] for row in c.execute("PRAGMA table_info(jobs)")}
            for col, typ in _EXTRA_COLUMNS.items():
                if col not in have:
                    c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def known(self, uid: str, dedupe: str) -> sqlite3.Row | None:
        with self.db() as c:
            return c.execute("SELECT uid, source, also_on FROM jobs WHERE uid=? OR dedupe=? LIMIT 1", (uid, dedupe)).fetchone()

    def touch(self, uid: str, also: str | None = None) -> None:
        with self.db() as c:
            if also:
                row = c.execute("SELECT also_on FROM jobs WHERE uid=?", (uid,)).fetchone()
                lst = json.loads(row["also_on"] or "[]") if row else []
                if also not in lst:
                    lst.append(also)
                c.execute("UPDATE jobs SET last_seen=?, also_on=? WHERE uid=?", (now(), json.dumps(lst), uid))
            else:
                c.execute("UPDATE jobs SET last_seen=? WHERE uid=?", (now(), uid))

    def insert(self, row: dict) -> None:
        row = {k: (json.dumps(v, ensure_ascii=False) if k in _JSON else v) for k, v in row.items()}
        row.setdefault("first_seen", now())
        row.setdefault("last_seen", row["first_seen"])
        cols = ", ".join(row)
        with self.db() as c:
            c.execute(f"INSERT OR IGNORE INTO jobs ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))

    def jobs(self, status: str | None = None, search: str | None = None, good_only: bool = False, limit: int = 200) -> list[dict]:
        q, args = "SELECT * FROM jobs WHERE 1=1", []
        if status:
            q += " AND status=?"
            args.append(status)
        if search:
            q += " AND search=?"
            args.append(search)
        if good_only:
            q += " AND good=1"
        q += " ORDER BY good DESC, first_seen DESC LIMIT ?"
        args.append(limit)
        with self.db() as c:
            return [self._decode(r) for r in c.execute(q, args)]

    def job(self, uid: str) -> dict | None:
        with self.db() as c:
            r = c.execute("SELECT * FROM jobs WHERE uid=?", (uid,)).fetchone()
        return self._decode(r) if r else None

    def set_status(self, uid: str, status: str, *, log: bool = True) -> bool:
        """Change status; applying sets applied_at (today) and a rejection/offer sets the response date."""
        if status not in STATUSES:
            raise ValueError(status)
        today = now()[:10]
        with self.db() as c:
            row = c.execute("SELECT status, applied_at FROM jobs WHERE uid=?", (uid,)).fetchone()
            if not row:
                return False
            sets = {"status": status, "updated_at": now()}
            if status in TRACKED and not row["applied_at"]:
                sets["applied_at"] = today
            if status in ("rejected", "offer", "interview"):
                sets["response"] = {"rejected": "Denied", "offer": "Offer", "interview": "Interview"}[status]
                sets["response_at"] = today
            c.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in sets)} WHERE uid=?", [*sets.values(), uid])
            if log and row["status"] != status:
                c.execute("INSERT INTO events(uid, at, kind, text) VALUES (?,?,?,?)",
                          (uid, now(), "applied" if status == "applied" else "status", f"{row['status']} → {status}"))
            return True

    # ------------------------------------------------------------ tracker

    EDITABLE = ("title", "company", "location", "url", "profile", "platform", "posted", "applied_at", "response",
                "response_at", "cv_file", "notes", "search_filter", "next_action", "next_at", "text")

    def set_good(self, uid: str, good: int) -> None:
        with self.db() as c:
            c.execute("UPDATE jobs SET good=? WHERE uid=?", (int(good), uid))

    def update(self, uid: str, fields: dict) -> bool:
        fields = {k: (v if v not in ("",) else None) for k, v in fields.items() if k in self.EDITABLE}
        if not fields:
            return False
        fields["updated_at"] = now()
        with self.db() as c:
            return c.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in fields)} WHERE uid=?",
                             [*fields.values(), uid]).rowcount == 1

    def add_manual(self, fields: dict, origin: str = "manual") -> str:
        """Add a job you found yourself (or an imported one). Returns its uid (existing jobs are left alone)."""
        return self.add_manual_ex(fields, origin)[0]

    def add_manual_ex(self, fields: dict, origin: str = "manual") -> tuple[str, bool]:
        """Like add_manual, also says whether a new row was inserted."""
        import hashlib

        key = fields.get("url") or f"{fields.get('company')}|{fields.get('title')}|{fields.get('applied_at') or fields.get('posted')}"
        uid = hashlib.sha1(f"{origin}|{key}".encode()).hexdigest()[:16]
        status = fields.get("status") or ("applied" if fields.get("applied_at") else "saved")
        if status not in STATUSES:
            raise ValueError(f"bad status {status!r}")
        row = {"uid": uid, "dedupe": uid, "search": fields.get("profile") or "", "source": origin,
               "source_name": fields.get("platform") or origin, "status": status, "origin": origin, "good": 1,
               "verdicts": {}, "languages": {}, "traps": [], "matched": [], "gaps": [], "also_on": []}
        row.update({k: fields.get(k) for k in self.EDITABLE if fields.get(k) not in (None, "")})
        row.setdefault("title", "(untitled)")
        if self.job(uid):
            return uid, False
        self.insert(row)
        return uid, True

    def delete(self, uid: str) -> bool:
        with self.db() as c:
            c.execute("DELETE FROM events WHERE uid=?", (uid,))
            return c.execute("DELETE FROM jobs WHERE uid=?", (uid,)).rowcount == 1

    def add_event(self, uid: str, kind: str, text: str, at: str | None = None) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(kind)
        with self.db() as c:
            c.execute("INSERT INTO events(uid, at, kind, text) VALUES (?,?,?,?)", (uid, at or now(), kind, text[:4000]))

    def events(self, uid: str) -> list[dict]:
        with self.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM events WHERE uid=? ORDER BY at DESC, id DESC", (uid,))]

    def delete_event(self, uid: str, event_id: int) -> None:
        with self.db() as c:
            c.execute("DELETE FROM events WHERE uid=? AND id=?", (uid, event_id))

    def tracked(self, statuses: tuple[str, ...] = ("saved",) + TRACKED, profile: str | None = None) -> list[dict]:
        q = f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(statuses))})"
        args: list = list(statuses)
        if profile:
            q += " AND COALESCE(profile, search)=?"
            args.append(profile)
        q += " ORDER BY COALESCE(applied_at, response_at, substr(first_seen, 1, 10)) DESC"
        with self.db() as c:
            rows = [self._decode(r) for r in c.execute(q, args)]
        for r in rows:
            r["waiting_days"] = _days_since(r.get("applied_at")) if r["status"] == "applied" and not r.get("response") else None
            r["no_response"] = r["waiting_days"] is not None and r["waiting_days"] >= NO_RESPONSE_DAYS
        return rows

    def funnel(self) -> dict:
        """Counts per profile: found, saved, applied (ever), interviews, offers, rejected, waiting, no response."""
        with self.db() as c:
            rows = [dict(r) for r in c.execute("SELECT status, applied_at, response, COALESCE(profile, search) p, good FROM jobs")]
        out: dict = {}
        for r in rows:
            for key in ("All", r["p"] or "Other"):
                f = out.setdefault(key, {"found": 0, "matches": 0, "saved": 0, "applied": 0, "interview": 0, "offer": 0,
                                         "rejected": 0, "waiting": 0, "no_response": 0})
                f["found"] += 1
                f["matches"] += r["good"] or 0
                if r["status"] == "saved":
                    f["saved"] += 1
                if r["status"] in TRACKED or r["applied_at"]:
                    f["applied"] += 1
                if r["status"] in ("interview", "offer"):
                    f["interview"] += 1
                if r["status"] == "offer":
                    f["offer"] += 1
                if r["status"] == "rejected":
                    f["rejected"] += 1
                if r["status"] == "applied" and not r["response"]:
                    days = _days_since(r["applied_at"])
                    f["no_response" if days is not None and days >= NO_RESPONSE_DAYS else "waiting"] += 1
        for f in out.values():
            answered = f["interview"] + f["rejected"]
            # "answered" includes rejections; "interview_rate" is the one that matters
            f["response_rate"] = round(100 * answered / f["applied"]) if f["applied"] else None
            f["interview_rate"] = round(100 * f["interview"] / f["applied"]) if f["applied"] else None
        return out

    def mark_notified(self, uids: list[str]) -> None:
        with self.db() as c:
            c.executemany("UPDATE jobs SET notified=1 WHERE uid=?", [(u,) for u in uids])

    def counts(self) -> dict:
        with self.db() as c:
            rows = c.execute("SELECT status, SUM(good) g, COUNT(*) n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: {"good": r["g"] or 0, "all": r["n"]} for r in rows}

    def source_due(self, key: str, min_interval_h: float) -> bool:
        with self.db() as c:
            r = c.execute("SELECT last_fetch FROM sources WHERE key=?", (key,)).fetchone()
        if not r or not r["last_fetch"]:
            return True
        last = dt.datetime.fromisoformat(r["last_fetch"])
        return dt.datetime.now(dt.timezone.utc) - last >= dt.timedelta(hours=min_interval_h) - dt.timedelta(minutes=5)

    def source_result(self, key: str, count: int | None, error: str | None) -> None:
        with self.db() as c:
            c.execute("INSERT INTO sources(key, last_fetch, last_error, last_count) VALUES (?,?,?,?) "
                      "ON CONFLICT(key) DO UPDATE SET last_fetch=excluded.last_fetch, last_error=excluded.last_error, "
                      "last_count=excluded.last_count", (key, now(), error, count))

    def source_states(self) -> list[dict]:
        with self.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM sources ORDER BY key")]

    # ------------------------------------------------------- polite crawling

    def block(self, host: str, reason: str, days: int = 7) -> None:
        until = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).isoformat(timespec="seconds")
        with self.db() as c:
            c.execute("INSERT INTO blocked(host, reason, since, until) VALUES (?,?,?,?) ON CONFLICT(host) DO UPDATE "
                      "SET reason=excluded.reason, since=excluded.since, until=excluded.until", (host, reason[:300], now(), until))

    def blocked(self, host: str) -> str | None:
        with self.db() as c:
            r = c.execute("SELECT reason, until FROM blocked WHERE host=?", (host,)).fetchone()
        if r and r["until"] > now():
            return r["reason"]
        return None

    def blocked_hosts(self) -> list[dict]:
        with self.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM blocked WHERE until > ? ORDER BY since DESC", (now(),))]

    def careers_state(self, url: str, name: str, state: str, detail: str = "", ats: str | None = None,
                      slug: str | None = None, jobs: int = 0) -> None:
        with self.db() as c:
            c.execute("INSERT INTO careers(url, name, state, detail, ats, slug, jobs, checked) VALUES (?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(url) DO UPDATE SET name=excluded.name, state=excluded.state, detail=excluded.detail, "
                      "ats=excluded.ats, slug=excluded.slug, jobs=excluded.jobs, checked=excluded.checked",
                      (url, name, state, detail[:500], ats, slug, jobs, now()))

    def careers_states(self) -> list[dict]:
        with self.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM careers ORDER BY name")]

    def log_run(self, started: str, ok: bool, summary: dict) -> None:
        with self.db() as c:
            c.execute("INSERT INTO runs(started, finished, ok, summary) VALUES (?,?,?,?)",
                      (started, now(), int(ok), json.dumps(summary, ensure_ascii=False)))
            c.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 100)")

    def last_run(self) -> dict | None:
        with self.db() as c:
            r = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        d = dict(r)
        d["summary"] = json.loads(d["summary"] or "{}")
        return d

    @staticmethod
    def _decode(r: sqlite3.Row) -> dict:  # noqa: D401
        d = dict(r)
        for k in _JSON:
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        return d


def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        d = dt.date.fromisoformat(iso[:10])
    except ValueError:
        return None
    return (dt.date.today() - d).days
