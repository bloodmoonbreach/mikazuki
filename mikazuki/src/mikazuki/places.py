"""Detailed town lists for the countries in profile.toml, so posts that name only a small town are placed.

remote-check knows every city of 50,000+ people; `geo.install_country_data` adds a country's smaller towns
(GeoNames, a few seconds and ~200 KB per country). Lists live in the data folder (`places/`), downloaded
in the background; offline, small towns just stay "unclear".
"""
from __future__ import annotations

import logging
import os
import threading

from remote_eligibility import geo

from .store import Store

log = logging.getLogger("mikazuki.places")
_lock = threading.Lock()


def use_data_folder(store: Store) -> None:
    """Keep the lists next to the user's other data unless REMOTE_CHECK_DATA says otherwise."""
    if not os.environ.get("REMOTE_CHECK_DATA"):
        os.environ["REMOTE_CHECK_DATA"] = str(store.root / "places")
        geo.reload_cities()


def missing(store: Store) -> list[str]:
    try:
        countries = store.profile().get("countries") or []
    except Exception:  # an unreadable profile is reported elsewhere
        return []
    return [c for c in dict.fromkeys(countries) if c in geo.COUNTRIES and not (geo.data_dir() / f"cities-{c}.txt").exists()]


def ensure(store: Store, background: bool = True) -> None:
    """Download the town lists the profile's countries don't have yet.

    Background downloads follow MIKAZUKI_SCHEDULER: 0 turns off all background work (tests, one-off runs).
    """
    if background and os.environ.get("MIKAZUKI_SCHEDULER", "1") == "0":
        return
    codes = missing(store)
    if not codes:
        return
    if background:
        threading.Thread(target=_download, args=(codes,), name="places", daemon=True).start()
    else:
        _download(codes)


def _download(codes: list[str]) -> None:
    with _lock:
        for code in codes:
            if (geo.data_dir() / f"cities-{code}.txt").exists():
                continue
            try:
                n = geo.install_country_data([code])[code]
                log.info("town list for %s: %d names", code, n)
            except Exception as e:  # noqa: BLE001 (offline or GeoNames down: try again next start)
                log.warning("town list for %s not downloaded: %s", code, e)
