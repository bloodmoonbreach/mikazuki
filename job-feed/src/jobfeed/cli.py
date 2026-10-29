"""job-feed command line.

    job-feed init [DIR]          write an example feed.toml
    job-feed run  [--force]      collect, check and store new jobs (and notify)
    job-feed list [--good]       show stored jobs
    job-feed sources             list available sources
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import EXAMPLE, ConfigError, load
from .sources import SOURCES
from .store import Store


def _profile(data: Path):
    import tomllib

    from remote_eligibility import LLMConfig, Profile

    p = data / "profile.toml"
    prof = tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return (Profile.parse(prof.get("countries") or ["ES"], prof.get("citizenships") or [], prof.get("languages")),
            LLMConfig.from_env(prof.get("llm")))


def _cv(data: Path):
    if not (data / "master.yaml").exists():
        return None, None
    from cvtracks import load_master, load_tracks

    return load_master(data / "master.yaml"), load_tracks(data / "tracks")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="job-feed", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("-d", "--data", default=os.environ.get("MIKAZUKI_DATA") or os.environ.get("JOBDESK_DATA", "."),
                    help="folder with feed.toml, profile.toml, master.yaml + tracks/ (default . or $MIKAZUKI_DATA)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("run")
    p.add_argument("--force", action="store_true", help="ignore source minimum intervals")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--llm", action="store_true", help="use the LLM pass for unclear posts (slower)")
    p = sub.add_parser("list")
    p.add_argument("--good", action="store_true")
    p.add_argument("--status", choices=("new", "saved", "applied", "dismissed"))
    sub.add_parser("sources")
    args = ap.parse_args(argv)
    data = Path(args.data).expanduser()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.cmd == "sources":
        for s in SOURCES.values():
            extra = f" (needs {', '.join(s.env)})" if s.env else ""
            print(f"{s.key:<15} {s.name:<18} every ≥{s.min_interval_h:g}h{extra}")
        return 0
    if args.cmd == "init":
        f = data / "feed.toml"
        if f.exists() and not args.force:
            print(f"{f} exists (use --force)", file=sys.stderr)
            return 1
        f.write_text(EXAMPLE, encoding="utf-8")
        print(f"wrote {f}")
        return 0

    store = Store(data / "feed.db")
    if args.cmd == "list":
        for j in store.jobs(status=args.status, good_only=args.good):
            v = " ".join(f"{c}:{x}" for c, x in (j["verdicts"] or {}).items())
            print(f"{'★' if j['good'] else ' '} {v:<24} fit {str(j['fit'] or '-'):>3}  {j['title']} @ {j['company']}  [{j['source_name']}]\n      {j['url']}")
        return 0

    try:
        cfg = load(data / "feed.toml")
    except (OSError, ConfigError) as e:
        print(f"job-feed: {e}", file=sys.stderr)
        return 2
    profile, llm = _profile(data)
    master, tracks = _cv(data)
    from .pipeline import run

    res = run(cfg, store, profile, master=master, tracks=tracks, llm=llm if args.llm else None,
              force=args.force, notify=not args.no_notify)
    s = res.summary()
    print(f"fetched {s['fetched']}  skipped {s['skipped'] or '-'}  errors {s['errors'] or '-'}")
    if s["needs_setup"]:
        print(f"needs setup {s['needs_setup']}")
    print(f"new {s['new']} (good {s['good']}), already seen {s['seen']}, filtered out {s['filtered']}, notified {s['notified']}")
    for r in res.new:
        if r["good"]:
            print(f"  ★ {r['title']} @ {r['company']} {r['verdicts']} fit {r['fit']}  {r['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
