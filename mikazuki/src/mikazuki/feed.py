"""Feed integration: background scheduler + helpers. One run at a time, in a worker thread."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

from jobfeed.config import ConfigError, parse
from jobfeed.store import Store as FeedStore

log = logging.getLogger("mikazuki.feed")
_lock = threading.Lock()
_state = {"running": False, "last_error": None, "started": None}


def feed_store(store) -> FeedStore:
    return FeedStore(store.path("feed.db"))


def state() -> dict:
    return dict(_state)


def run_now(store, *, force: bool = False) -> bool:
    """Start a run in the background. False if one is already running."""
    if not _lock.acquire(blocking=False):
        return False
    threading.Thread(target=_run, args=(store, force), daemon=True, name="job-feed").start()
    return True


def _run(store, force: bool) -> None:
    from jobfeed.pipeline import run

    from .app import load, profile_and_llm  # late import: app imports this module

    _state.update(running=True, started=dt.datetime.now(), last_error=None)
    try:
        cfg = parse(store.read("feed.toml"))
        profile, llm, _ = profile_and_llm()
        try:
            master, tracks = load()
        except Exception:
            master, tracks = None, None
        run(cfg, feed_store(store), profile, master=master, tracks=tracks, llm=llm if cfg.use_llm else None, force=force)
    except (ConfigError, Exception) as e:  # never let the scheduler thread die
        _state["last_error"] = f"{type(e).__name__}: {e}"[:500]
        log.exception("feed run failed")
    finally:
        _state["running"] = False
        _lock.release()


def scheduler(store, check_every_s: int = 300) -> None:
    """Runs forever in a daemon thread: starts a feed run whenever the last one is older than schedule_hours."""
    time.sleep(30)  # let the app start first
    while True:
        try:
            cfg = parse(store.read("feed.toml"))
            last = feed_store(store).last_run()
            due = last is None or (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last["started"])
                                   >= dt.timedelta(hours=cfg.schedule_hours))
            if cfg.searches and due:
                run_now(store)
        except Exception as e:
            _state["last_error"] = f"scheduler: {e}"[:500]
        time.sleep(check_every_s)


def start_scheduler(store) -> None:
    threading.Thread(target=scheduler, args=(store,), daemon=True, name="job-feed-scheduler").start()
