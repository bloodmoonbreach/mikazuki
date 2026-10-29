"""The data folder: master.yaml, tracks/, out/, profile.toml and a history of every saved version."""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

_EDITABLE = re.compile(r"^(master\.yaml|profile\.toml|feed\.toml|tracks/[A-Za-z0-9_-]+\.ya?ml)$")
_TRACK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

DEFAULT_PROFILE = """\
# Profile for job checks (remote-check)
countries = ["ES"]        # where you want to work from, in order
citizenships = []         # e.g. ["RO"]: EU citizenship covers EEA right-to-work rules
languages = ["en", "es"]

# Optional LLM for posts the rules can't decide. Any OpenAI-compatible endpoint.
# [llm]
# base_url = "http://ollama-host:11434/v1"
# model = "gemma4:12b"
# api_key_env = "OPENROUTER_API_KEY"   # name of an environment variable, never the key itself

# Apply: facts that application forms ask for and the CV doesn't hold. Anything left out
# shows up as [NEEDS INPUT] instead of being guessed. Open answers are drafted by a local
# Ollama model (default gemma4:12b, at [llm] base_url unless ollama_url is set).
# [apply]
# linkedin = "your-handle"
# salary = "30.000-35.000 € gross/year"
# availability = "Two weeks"
# relocate = "No, remote from Spain"
# model = "gemma4:12b"
"""


class StoreError(ValueError):
    pass


@dataclass
class OutFile:
    rel: str
    size: int
    mtime: dt.datetime


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        (self.root / "tracks").mkdir(parents=True, exist_ok=True)
        (self.root / "out").mkdir(exist_ok=True)
        (self.root / ".history").mkdir(exist_ok=True)
        if not (self.root / "profile.toml").exists():
            (self.root / "profile.toml").write_text(DEFAULT_PROFILE, encoding="utf-8")
        if not (self.root / "feed.toml").exists():
            from jobfeed.config import EXAMPLE
            (self.root / "feed.toml").write_text(EXAMPLE, encoding="utf-8")

    # -------------------------------------------------------------- paths

    def path(self, rel: str) -> Path:
        """Resolve a path inside the data folder; refuse anything that escapes it."""
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise StoreError("path outside the data folder")
        return p

    def editable(self, rel: str) -> Path:
        if not _EDITABLE.match(rel):
            raise StoreError(f"not an editable file: {rel}")
        return self.path(rel)

    @property
    def has_master(self) -> bool:
        return (self.root / "master.yaml").exists()

    def files(self) -> list[str]:
        out = ["master.yaml"] if self.has_master else []
        out += [f"tracks/{p.name}" for p in sorted((self.root / "tracks").glob("*.y*ml"))]
        return out + ["feed.toml", "profile.toml"]

    # -------------------------------------------------------------- read/write with history

    def read(self, rel: str) -> str:
        p = self.editable(rel)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def write(self, rel: str, text: str) -> None:
        p = self.editable(rel)
        if p.exists():
            self.snapshot(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
        os.replace(tmp, p)

    def snapshot(self, rel: str) -> None:
        p = self.editable(rel)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = self.root / ".history" / f"{stamp}__{rel.replace('/', '__')}"
        shutil.copy2(p, dest)
        # keep the last 50 versions per file
        versions = sorted((self.root / ".history").glob(f"*__{rel.replace('/', '__')}"))
        for old in versions[:-50]:
            old.unlink()

    def history(self, rel: str | None = None) -> list[tuple[str, str, dt.datetime]]:
        """(snapshot name, file it belongs to, time), newest first."""
        out = []
        for f in sorted((self.root / ".history").iterdir(), reverse=True):
            stamp, _, name = f.name.partition("__")
            target = name.replace("__", "/")
            if rel and target != rel:
                continue
            try:
                when = dt.datetime.strptime(stamp, "%Y%m%d-%H%M%S")
            except ValueError:
                continue
            out.append((f.name, target, when))
        return out

    def read_snapshot(self, name: str) -> tuple[str, str]:
        if "/" in name or name.startswith("."):
            raise StoreError("bad snapshot name")
        p = self.path(f".history/{name}")
        target = name.partition("__")[2].replace("__", "/")
        self.editable(target)
        return target, p.read_text(encoding="utf-8")

    def delete_track(self, track_id: str) -> None:
        if not _TRACK_ID.match(track_id):
            raise StoreError("bad track id")
        for ext in ("yaml", "yml"):
            rel = f"tracks/{track_id}.{ext}"
            p = self.editable(rel)
            if p.exists():
                self.snapshot(rel)
                p.unlink()

    # -------------------------------------------------------------- outputs

    def outputs(self) -> list[OutFile]:
        out = []
        for p in (self.root / "out").rglob("*"):
            if p.is_file():
                st = p.stat()
                out.append(OutFile(str(p.relative_to(self.root / "out")), st.st_size, dt.datetime.fromtimestamp(st.st_mtime)))
        return sorted(out, key=lambda f: f.mtime, reverse=True)

    def output_path(self, rel: str) -> Path:
        out = (self.root / "out").resolve()
        p = (out / rel).resolve()
        if out not in p.parents or not p.is_file():  # must stay inside out/, not just the data folder
            raise StoreError("no such file")
        return p

    def profile(self) -> dict:
        try:
            return tomllib.loads(self.read("profile.toml"))
        except tomllib.TOMLDecodeError as e:
            raise StoreError(f"profile.toml: {e}") from e


def valid_track_id(s: str) -> bool:
    return bool(_TRACK_ID.match(s))
