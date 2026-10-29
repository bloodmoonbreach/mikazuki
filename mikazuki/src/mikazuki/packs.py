"""Presets for the setup wizard, and the code that turns wizard answers into the data files.

The answers (a plain dict, kept in `<data>/.setup.json` while the wizard runs) become:
profile.toml, feed.toml, tracks/<role>.yaml and, from the CV step, master.yaml.
The presets live in packs/*.toml so they can be extended without touching code.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from cvtracks.model import SECTIONS
from cvtracks.setup import NATIVE, dump, master_yaml, slug
from jobfeed.sources import SOURCES
from remote_eligibility.geo import COUNTRIES

HERE = Path(__file__).parent / "packs"
REMOTE_BOARDS = ["remotive", "remoteok", "himalayas", "jobicy", "workingnomads", "weworkremotely", "arbeitnow", "nodesk"]
BOARD_NAMES = {
    "remotive": "Remotive", "remoteok": "Remote OK", "himalayas": "Himalayas", "jobicy": "Jobicy",
    "workingnomads": "Working Nomads", "weworkremotely": "We Work Remotely", "arbeitnow": "Arbeitnow",
    "nodesk": "NoDesk", "cybersecurityjobsite": "Cyber Security Jobsite", "hipo": "Hipo.ro",
}
CV_LANGS = {"en": "English", "es": "Spanish", "ro": "Romanian", "fr": "French", "de": "German", "it": "Italian",
            "pt": "Portuguese", "nl": "Dutch", "pl": "Polish"}
LEVELS = {"entry": "Entry level or junior", "mid": "Some experience (2-5 years)", "senior": "Senior"}


@lru_cache
def fields() -> dict:
    return tomllib.loads((HERE / "fields.toml").read_text(encoding="utf-8"))


@lru_cache
def employers() -> dict:
    return tomllib.loads((HERE / "employers.toml").read_text(encoding="utf-8"))


@lru_cache
def countries_pack() -> dict:
    return tomllib.loads((HERE / "countries.toml").read_text(encoding="utf-8"))


def presets() -> list[dict]:
    return fields()["preset"]


def preset(pid: str) -> dict | None:
    return next((p for p in presets() if p["id"] == pid), None)


def country_choices() -> list[tuple[str, str]]:
    return sorted(((c, COUNTRIES[c].display) for c in COUNTRIES), key=lambda x: x[1])


# ---------------------------------------------------------------- answers → roles


def roles(ans: dict) -> list[dict]:
    """The chosen roles: preset roles plus custom ones, each with id, title, keywords, workday terms."""
    r = ans.get("roles") or {}
    p = preset(r.get("preset", "")) or {"role": []}
    chosen = [dict(x) for x in p["role"] if x["id"] in (r.get("ids") or [])]
    for c in r.get("custom") or []:
        name, kws = c.get("name", "").strip(), [k.strip() for k in c.get("keywords") or [] if k.strip()]
        if name:
            chosen.append({"id": slug(name), "title": {"en": name}, "keywords": kws or [name], "workday": (kws or [name])[:2],
                           "custom": True})
    seen, out = set(), []
    for x in chosen:
        while x["id"] in seen:
            x["id"] += "-2"
        seen.add(x["id"])
        out.append(x)
    return out


def _title(role: dict, lang: str) -> str:
    t = role["title"]
    return t.get(lang) or t.get("en") or next(iter(t.values()))


def exclude_words(level: str, keywords: list[str]) -> list[str]:
    """Seniority words for the level, minus any that the role's own keywords use ("community manager")."""
    words = fields().get("exclude", {}).get(level or "entry", {}).get("words", [])
    kw = " ".join(keywords).lower()
    return [w for w in words if not re.search(rf"\b{re.escape(w)}\b", kw)]


# ---------------------------------------------------------------- files


def track_files(ans: dict) -> dict[str, str]:
    langs = (ans.get("you") or {}).get("cv_langs") or ["en"]
    out = {}
    for r in roles(ans):
        title = {l: _title(r, l) if l in r["title"] or len(langs) == 1 else "TODO: translate" for l in langs}
        if len(langs) > 1 and r.get("custom"):
            title = {langs[0]: _title(r, "en"), **{l: "TODO: translate" for l in langs[1:]}}
        track = {"id": r["id"], "title": title if len(langs) > 1 else title[langs[0]], "languages": langs,
                 "summary": r["id"], "include_tags": [], "exclude_tags": ["unpublished"], "sections": list(SECTIONS),
                 "limits": {"pages": 1, "bullets": 4, "projects": 4}, "keywords": []}
        out[f"tracks/{r['id']}.yaml"] = (
            "# One CV version per target role. include_tags: [] shows everything; tag items in master.yaml\n"
            "# and list the tags here to make this version more focused.\n" + dump(track))
    return out


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items()) + "}"
    raise TypeError(type(v))


def _table(name: str, d: dict) -> str:
    return f"[{name}]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in d.items() if v is not None) + "\n"


def profile_toml(ans: dict, old: dict | None = None) -> str:
    """profile.toml from the answers; keeps the [apply] facts and other [llm] options already there."""
    old = old or {}
    w, you, ai = ans.get("where") or {}, ans.get("you") or {}, ans.get("ai") or {}
    head = {"countries": w.get("countries") or ["ES"], "citizenships": w.get("citizenships") or [],
            "languages": w.get("languages") or ["en"]}
    text = ("# Profile for job checks and applications. Edit here or run the setup again (Settings).\n"
            "# countries: where you can work from; citizenships: only used to answer right-to-work questions.\n"
            + "".join(f"{k} = {_toml_value(v)}\n" for k, v in head.items()) + "\n")
    provider = ai.get("provider") or "none"
    if provider != "none":
        llm = {k: v for k, v in (old.get("llm") or {}).items() if k not in ("base_url", "model", "provider", "api_key_file", "api_key_env")}
        llm = {"provider": provider, "base_url": ai.get("base_url"), "model": ai.get("model"), **llm}
        if provider == "openai":
            llm["api_key_file"] = ".llm-key"
        text += "# AI: drafts application answers and reads CVs. 'ollama' stays on your machine; 'openai' is any\n" \
                "# OpenAI-compatible API (your CV and job posts are sent to it). The key is in .llm-key, not here.\n"
        text += _table("llm", llm)
    apply = dict(old.get("apply") or {})
    for k in ("linkedin", "github", "website"):
        if you.get(k):
            apply.setdefault(k, you[k])
    if provider == "ollama":
        apply.pop("ollama_url", None)
    if apply:
        text += ("# Apply: facts that application forms ask for and the CV doesn't hold (salary, availability,\n"
                 "# notice period...). Anything missing shows up as [NEEDS INPUT] instead of being guessed.\n")
        text += _table("apply", apply)
    else:
        text += ("# Apply: facts that application forms ask for and the CV doesn't hold. Examples:\n"
                 "# [apply]\n# salary = \"30.000-35.000 € gross/year\"\n# availability = \"Two weeks\"\n")
    return text


def feed_toml(ans: dict, old_text: str = "") -> str:
    """feed.toml: one search per role, the chosen boards, employers of the preset, careers pages of your countries.
    Keeps [notify], [browser_helper] and [sources] options from an existing file."""
    r, w, src = ans.get("roles") or {}, ans.get("where") or {}, ans.get("sources") or {}
    level = r.get("level") or "entry"
    p = preset(r.get("preset", "")) or {"id": "", "sources": []}
    countries = set(w.get("countries") or [])
    boards = src["boards"] if "boards" in src else [k for k, _, on in available_boards(ans) if on]
    sources = list(dict.fromkeys(boards + (["companies"] if src.get("companies", True) else [])
                                 + (["careers"] if src.get("careers", True) else [])))
    local = bool(w.get("local"))
    old = tomllib.loads(old_text) if old_text.strip() else {}

    out = ["# Feed: what to search, where, and how often. Written by the setup; edit freely.\n",
           f"schedule_hours = {_toml_value(old.get('schedule_hours', 6))}\n",
           f"include_internships = {_toml_value(bool(w.get('internships')))}\n",
           f"use_llm = {_toml_value(bool(old.get('use_llm', False)))}\n\n"]
    notify = old.get("notify") or {"min_verdict": "likely", "local_matches": True, "max_per_run": 10}
    out.append("# Notifications: telegram_token_env + telegram_chat_id, ntfy_url or webhook_url (see the README).\n")
    out.append(_table("notify", notify))
    if old.get("browser_helper"):
        out.append(_table("browser_helper", old["browser_helper"]))
    source_opts = dict(old.get("sources") or {})
    for key in sources:  # country-scoped sources read the chosen countries they cover
        if key in SOURCES and SOURCES[key].countries:
            source_opts[key] = {**source_opts.get(key, {}), "countries": sorted(countries & SOURCES[key].countries)}
    for key, opts in source_opts.items():
        out.append(_table(f"sources.{key}", opts))

    workday_terms: list[str] = []
    for role in roles(ans):
        kws = role["keywords"]
        workday_terms += role.get("workday") or kws[:2]
        search = {"name": _title(role, "en"), "track": role["id"], "keywords": kws,
                  "exclude_title": exclude_words(level, kws), "sources": sources, "remote_only": not local,
                  "max_age_days": 21}
        if local and w.get("home"):
            search["locations"] = w["home"]
        out.append("[[search]]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in search.items()) + "\n")

    if src.get("companies", True):
        workday_terms = list(dict.fromkeys(workday_terms))[:8]
        for c in employers().get("company", []):
            if p["id"] and p["id"] not in c.get("fields", []):
                continue
            block = {"name": c["name"], "ats": c["ats"], "slug": c["slug"]}
            if c["ats"] in ("workday", "smartrecruiters"):
                block["search"] = workday_terms
            out.append("[[company]]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in block.items()) + "\n")
    if src.get("careers", True):
        for c in employers().get("careers", []):
            if (p["id"] and p["id"] not in c.get("fields", [])) or not countries & set(c.get("countries", [])):
                continue
            out.append("[[careers]]\n" + f"name = {_toml_value(c['name'])}\nurl = {_toml_value(c['url'])}\n\n")
    return "".join(out)


def available_boards(ans: dict) -> list[tuple[str, str, bool]]:
    """(key, name, on by default) for the sources step."""
    p = preset((ans.get("roles") or {}).get("preset", "")) or {"sources": []}
    countries = set((ans.get("where") or {}).get("countries") or [])
    cpack = countries_pack().get("country", {})
    extra = list(dict.fromkeys(p.get("sources", []) + [s for c in countries for s in cpack.get(c, {}).get("sources", [])]))
    scoped = [s.key for s in SOURCES.values() if s.countries & countries]  # e.g. Adzuna for India
    keys = list(dict.fromkeys(REMOTE_BOARDS + extra + scoped))
    return [(k, _board_label(k), _ready(k)) for k in keys]


def _ready(key: str) -> bool:
    """On by default unless it needs a key that isn't set yet (then it's offered, unticked)."""
    s = SOURCES.get(key)
    return not (s and s.env) or all(os.environ.get(e) for e in s.env)


def _board_label(key: str) -> str:
    s = SOURCES.get(key)
    name = BOARD_NAMES.get(key) or (s.name if s else key)
    if s and s.env and s.signup:
        name += f" (needs a free key from {urlparse(s.signup).hostname}; enter it below)"
    return name


def key_fields(ans: dict) -> list[tuple[str, str, str]]:
    """(env name, source name, signup URL) for the keys the offered boards need."""
    out = []
    for key, _, _ in available_boards(ans):
        s = SOURCES.get(key)
        for name in (s.env if s else ()):
            out.append((name, s.name, s.signup))
    return out


def alerts_only(ans: dict) -> list[str]:
    cp = countries_pack()
    countries = (ans.get("where") or {}).get("countries") or []
    return list(dict.fromkeys(cp.get("alerts_only_everywhere", [])
                              + [b for c in countries for b in cp.get("country", {}).get(c, {}).get("alerts_only", [])]))


def employer_count(ans: dict) -> tuple[int, int]:
    r, w = ans.get("roles") or {}, ans.get("where") or {}
    pid, countries = r.get("preset", ""), set(w.get("countries") or [])
    comp = sum(1 for c in employers().get("company", []) if not pid or pid in c.get("fields", []))
    car = sum(1 for c in employers().get("careers", []) if (not pid or pid in c.get("fields", []))
              and countries & set(c.get("countries", [])))
    return comp, car


# ---------------------------------------------------------------- CV from the form


def _lines(text: str) -> list[str]:
    return [x.strip(" -•\t") for x in (text or "").splitlines() if x.strip(" -•\t")]


def master_from_form(ans: dict, form: dict) -> str:
    """master.yaml from the CV form: contact from the 'you' step, then jobs, education, skills, languages."""
    you = ans.get("you") or {}
    langs = you.get("cv_langs") or ["en"]
    t = (lambda v: v) if len(langs) == 1 else (lambda v: {langs[0]: v, **{l: "TODO: translate" for l in langs[1:]}})
    person = {k: you[k] for k in ("name", "email", "phone", "linkedin", "github", "website") if you.get(k)}
    person.setdefault("name", "TODO")
    summary = (form.get("summary") or "").strip()
    master: dict = {"person": person,
                    "summaries": {r["id"]: t(summary) if summary else t("TODO: write a 2-4 sentence summary")
                                  for r in roles(ans)} or {"main": t(summary or "TODO")},
                    "experience": [], "education": [], "projects": [], "certifications": [], "skills": [], "languages": []}
    used: set[str] = set()

    def uid(base: str) -> str:
        base, n = slug(base) or "item", 2
        out = base
        while out in used:
            out, n = f"{base}-{n}", n + 1
        used.add(out)
        return out

    for i in range(int(form.get("jobs", 0) or 0)):
        role, company = (form.get(f"job{i}_role") or "").strip(), (form.get(f"job{i}_company") or "").strip()
        if not role and not company:
            continue
        job = {"id": uid(f"{company}-{role}"), "role": t(role or "TODO"), "company": company or "TODO"}
        if s := (form.get(f"job{i}_start") or "").strip():
            job["start"] = s
        end = (form.get(f"job{i}_end") or "").strip()
        job.update({"end": end} if end else {"ongoing": True})
        job["bullets"] = [{"text": t(b)} for b in _lines(form.get(f"job{i}_bullets", ""))] or \
                         [{"text": t("TODO: 2-4 results or responsibilities")}]
        master["experience"].append(job)
    for i in range(int(form.get("edus", 0) or 0)):
        degree, school = (form.get(f"edu{i}_degree") or "").strip(), (form.get(f"edu{i}_school") or "").strip()
        if not degree and not school:
            continue
        ed = {"id": uid(school or degree), "degree": t(degree or "TODO"), "school": school or "TODO"}
        if s := (form.get(f"edu{i}_start") or "").strip():
            ed["start"] = s
        end = (form.get(f"edu{i}_end") or "").strip()
        ed.update({"end": end} if end else {"ongoing": True})
        master["education"].append(ed)
    for line in _lines(form.get("certs", "")):
        parts = [x.strip() for x in line.split("|")]
        c = {"id": uid(parts[0]), "name": parts[0]}
        if len(parts) > 1 and parts[1]:
            c["issuer"] = parts[1]
        if len(parts) > 2 and re.fullmatch(r"\d{4}(-\d{1,2})?", parts[2]):
            c["date"] = parts[2]
        if len(parts) > 3 and parts[3].startswith(("http://", "https://")):
            c["url"] = parts[3]
        master["certifications"].append(c)
    for line in _lines(form.get("skills", "")):
        label, _, items = line.partition(":")
        if not items:
            label, items = "Skills", line
        master["skills"].append({"id": uid(label), "label": t(label.strip()),
                                 "items": [x.strip() for x in re.split(r"[,;]", items) if x.strip()]})
    order = ["native", "C2", "C1", "B2", "B1", "A2", "A1"]
    levels = (ans.get("where") or {}).get("language_levels", {})
    for code, level in sorted(levels.items(), key=lambda kv: order.index(kv[1]) if kv[1] in order else 9):
        name = CV_LANGS.get(code, code)
        from cvtracks.setup import LANGUAGE_NAMES
        known = LANGUAGE_NAMES.get(name.lower(), {})
        entry = {"name": {l: known.get(l, name) for l in langs} if len(langs) > 1 else known.get(langs[0], name)}
        if level == "native":
            entry["level"] = {l: NATIVE.get(l, "Native") for l in langs} if len(langs) > 1 else NATIVE.get(langs[0], "Native")
        else:
            entry["level"] = entry["cefr"] = level
        master["languages"].append(entry)
    return master_yaml(master)


def master_from_draft(ans: dict, draft_yaml: str) -> str:
    """Use an AI draft, with contact details from the wizard and one summary key per role."""
    import yaml
    from cvtracks.setup import HEADER
    body = draft_yaml.split("\n\n", 1)[1] if draft_yaml.startswith("#") else draft_yaml
    data = yaml.safe_load(body) or {}
    you = ans.get("you") or {}
    data.setdefault("person", {})
    for k in ("name", "email", "phone", "linkedin", "github", "website"):
        if you.get(k):
            data["person"][k] = you[k]
    summaries = data.get("summaries") or {}
    main = summaries.pop("main", None) or next(iter(summaries.values()), None)
    for r in roles(ans):
        summaries.setdefault(r["id"], main or "TODO: write a 2-4 sentence summary")
    data["summaries"] = summaries
    order = ["person", "summaries", "experience", "education", "projects", "certifications", "skills", "languages"]
    return (HEADER + "# Drafted by a language model from your CV. Read every line before using it.\n\n"
            + dump({k: data[k] for k in order if data.get(k)}))
