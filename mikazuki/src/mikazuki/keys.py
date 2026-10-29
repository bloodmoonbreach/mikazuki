"""API keys for job sources (Adzuna, USAJOBS…) entered in the setup instead of .env.

Kept in data/.source-keys.json, readable only by the app, and put into the process environment where
job-feed looks for them. Only names that a job source declares can be set (no PATH or PYTHONPATH from a
form), and a value set in the real environment (.env, compose) always wins and is never overwritten.
"""
from __future__ import annotations

import json
import os

from jobfeed.sources import SOURCES

from .store import Store

FILE = ".source-keys.json"
_ours: set[str] = set()  # names this module put into os.environ (so a later save can replace them)


def allowed() -> set[str]:
    return {name for s in SOURCES.values() for name in s.env}


def load(store: Store) -> dict[str, str]:
    try:
        data = json.loads((store.root / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: str(v) for k, v in data.items() if k in allowed() and str(v).strip()} if isinstance(data, dict) else {}


def from_environment(name: str) -> bool:
    """Set outside mikazuki (.env, compose): shown as such and not editable in the setup."""
    return bool(os.environ.get(name)) and name not in _ours


def save(store: Store, updates: dict[str, str]) -> None:
    """Store new values (empty ones keep what's there) and apply them."""
    keys = load(store)
    keys.update({k: v.strip() for k, v in updates.items() if k in allowed() and v.strip() and not from_environment(k)})
    old = os.umask(0o077)
    try:
        (store.root / FILE).write_text(json.dumps(keys, indent=2) + "\n", encoding="utf-8")
    finally:
        os.umask(old)
    apply(store)


def apply(store: Store) -> None:
    for name, value in load(store).items():
        if not from_environment(name):
            os.environ[name] = value
            _ours.add(name)
