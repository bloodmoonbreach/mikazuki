"""mikazuki (三日月): self-hosted job-hunt desk around cvtracks, remote-check and job-feed. Formerly job-desk."""

import os as _os

__version__ = "0.2.0"

# Settings were called JOBDESK_* before the rename (2026-10-09); keep old .env files working.
for _k, _v in list(_os.environ.items()):
    if _k.startswith("JOBDESK_"):
        _os.environ.setdefault("MIKAZUKI_" + _k[len("JOBDESK_"):], _v)
