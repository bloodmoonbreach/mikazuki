"""First run: create the account, then a setup wizard that writes the data files for any field.

Steps: you → roles → where → ai → cv → sources → done. Answers are kept in `<data>/.setup.json` so the
wizard can be left and resumed, or run again later from Settings. Nothing is written to profile.toml,
feed.toml or tracks/ until the last step; every file it replaces keeps its previous version in History.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
import urllib.error
import urllib.request

import yaml
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import keys, packs

router = APIRouter()
STEPS = [("you", "You"), ("roles", "Roles"), ("where", "Where"), ("ai", "AI"), ("cv", "CV"), ("sources", "Sources"),
         ("done", "Finish")]
STEP_IDS = [s for s, _ in STEPS]
STATE = ".setup.json"


def _a():
    from . import app  # late import: app includes this router
    return app


def load_answers() -> dict:
    store = _a().store
    f = store.root / STATE
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return _from_existing()


def save_answers(ans: dict) -> None:
    f = _a().store.root / STATE
    old = os.umask(0o077)
    try:
        f.write_text(json.dumps(ans, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        os.umask(old)


def _from_existing() -> dict:
    """Prefill from files that already exist (running the wizard again on a set-up install)."""
    a = _a()
    ans: dict = {}
    try:
        prof = a.store.profile()
    except Exception:
        prof = {}
    levels: dict = {}
    cv_langs: list = []
    if a.store.has_master:
        try:
            m = a.load()[0]
            for lang in getattr(m, "languages", None) or []:
                code = _lang_code(lang)
                if code:
                    levels[code] = _lang_level(lang)
        except Exception:
            pass
    for t in sorted((a.store.root / "tracks").glob("*.yaml")) if (a.store.root / "tracks").is_dir() else []:
        try:
            cv_langs += [l for l in (yaml.safe_load(t.read_text(encoding="utf-8")) or {}).get("languages") or []
                         if l in packs.CV_LANGS and l not in cv_langs]
        except Exception:
            pass
    if prof.get("countries"):
        langs = prof.get("languages") or []
        ans["where"] = {"countries": prof.get("countries"), "citizenships": prof.get("citizenships") or [],
                        "languages": langs,
                        "language_levels": {c: levels.get(c, "C1") for c in langs if c in packs.CV_LANGS}}
    llm = prof.get("llm") or {}
    if llm.get("base_url"):
        ans["ai"] = {"provider": llm.get("provider") or "ollama", "base_url": llm.get("base_url"), "model": llm.get("model")}
    if a.store.has_master:
        try:
            m = a.load()[0]
            p = m.person
            ans["you"] = {k: getattr(p, k) for k in ("name", "email", "phone", "linkedin", "github", "website") if getattr(p, k)}
            if cv_langs:
                ans["you"]["cv_langs"] = cv_langs
        except Exception:
            pass
    return ans


_LANG_NAMES = {"english": "en", "spanish": "es", "romanian": "ro", "french": "fr", "german": "de", "italian": "it",
               "portuguese": "pt", "dutch": "nl", "polish": "pl"}


def _lang_code(lang) -> str | None:
    name = getattr(lang, "name", None) if not isinstance(lang, dict) else lang.get("name")
    if isinstance(name, dict):
        name = name.get("en") or next(iter(name.values()), "")
    return _LANG_NAMES.get(str(name or "").strip().lower())


def _lang_level(lang) -> str:
    """CEFR level of a master.yaml language entry; native speakers → 'native', unknown → 'C1'."""
    get = (lambda k: lang.get(k)) if isinstance(lang, dict) else (lambda k: getattr(lang, k, None))
    cefr = str(get("cefr") or "").upper()
    if cefr in ("A1", "A2", "B1", "B2", "C1", "C2"):
        return cefr
    level = get("level")
    text = " ".join(level.values()) if isinstance(level, dict) else str(level or "")
    if re.search(r"native|nativ|matern", text, re.I):
        return "native"
    m = re.search(r"\b([ABC][12])\b", text)
    return m.group(1) if m else "C1"


def _page(request: Request, step: str, **ctx) -> HTMLResponse:
    ans = ctx.pop("ans", None) or load_answers()
    done = set(ans.get("_done") or [])
    return _a().page(request, "setup.html", step=step, steps=STEPS, done=done, ans=ans, packs=packs,
                     keys=keys, saved_keys=set(keys.load(_a().store)),
                     has_master=_a().store.has_master, has_feed=_has_searches(_read(_a().store, "feed.toml")),
                     existing_tracks=[r for r in packs.track_files(ans) if _a().store.path(r).exists()], **ctx)


def _read(store, rel: str) -> str:
    return store.read(rel) if store.path(rel).exists() else ""


def _next(step: str) -> RedirectResponse:
    return RedirectResponse(f"/setup/{STEP_IDS[STEP_IDS.index(step) + 1]}", status_code=303)


def _mark(ans: dict, step: str) -> None:
    ans["_done"] = sorted(set(ans.get("_done") or []) | {step})
    save_answers(ans)


def _list(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;\n]", text or "") if x.strip()]


# ---------------------------------------------------------------- account


@router.get("/setup/account", response_class=HTMLResponse)
def account_form(request: Request):
    a = _a()
    if a.auth.configured:
        return RedirectResponse("/setup", status_code=303)
    return a.templates.TemplateResponse(request, "account.html", {"csrf": request.state.csrf, "error": None, "user": ""})


@router.post("/setup/account", response_class=HTMLResponse)
def account_create(request: Request, code: str = Form(""), username: str = Form(""), password: str = Form(""),
                   password2: str = Form("")):
    from . import security
    a = _a()
    if a.auth.configured:
        return RedirectResponse("/login", status_code=303)
    ip = request.client.host if request.client else "?"
    user = username.strip()
    error = None
    if a.auth.locked_for(ip):
        error = "Too many wrong setup codes. Wait a few minutes."
    elif code.strip().upper().replace(" ", "") != a.auth.setup_code().replace(" ", ""):
        a.auth._fails.setdefault(ip, []).append(__import__("time").time())
        error = "That setup code is wrong. It's in the server log, and in setup-code.txt in the data folder."
    elif not re.fullmatch(r"[A-Za-z0-9._@-]{2,40}", user):
        error = "Usernames are 2-40 letters, digits, dots, dashes or underscores."
    elif password != password2:
        error = "The two passwords don't match."
    elif problem := security.password_problem(password, user):
        error = problem
    if error:
        return a.templates.TemplateResponse(request, "account.html", {"csrf": request.state.csrf, "error": error,
                                                                      "user": user[:40]}, status_code=400)
    a.auth.create_account(user, password)
    resp = RedirectResponse("/setup", status_code=303)
    token, ttl = a.auth.make(long=False)
    resp.set_cookie(security.SESSION_COOKIE, token, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https", path="/")
    return resp


@router.post("/settings/password")
def change_password(current: str = Form(""), password: str = Form(""), password2: str = Form("")):
    from . import security
    a = _a()
    if a.auth.from_env:
        return a.back("/settings", err="The password is set with MIKAZUKI_PASSWORD; change it there.")
    if not a.auth._check(current):
        return a.back("/settings", err="Your current password is wrong.")
    if password != password2:
        return a.back("/settings", err="The two new passwords don't match.")
    if problem := security.password_problem(password, a.auth.user):
        return a.back("/settings", err=problem)
    a.auth.change_password(password)
    return RedirectResponse("/login?next=/settings", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    return _a().page(request, "settings.html", user=_a().auth.user, from_env=_a().auth.from_env)


# ---------------------------------------------------------------- wizard


@router.get("/setup")
def setup_start():
    ans = load_answers()
    done = set(ans.get("_done") or [])
    for s in STEP_IDS:
        if s not in done:
            return RedirectResponse(f"/setup/{s}", status_code=303)
    return RedirectResponse("/setup/done", status_code=303)


@router.get("/setup/{step}", response_class=HTMLResponse)
def setup_step(request: Request, step: str):
    if step not in STEP_IDS:
        return RedirectResponse("/setup", status_code=303)
    return _page(request, step)


@router.post("/setup/you")
async def post_you(request: Request):
    f = await request.form()
    ans = load_answers()
    you = {k: str(f.get(k, "")).strip()[:200] for k in ("name", "email", "phone", "linkedin", "github", "website")}
    for k in ("linkedin", "github"):  # a pasted profile URL → handle
        you[k] = re.sub(r"^https?://(www\.)?(linkedin\.com/in|github\.com)/", "", you[k]).strip("/")
    langs = [l for l in f.getlist("cv_langs") if l in packs.CV_LANGS]
    you["cv_langs"] = sorted(langs, key=lambda l: (l != f.get("cv_main"), list(packs.CV_LANGS).index(l))) or ["en"]
    ans["you"] = {k: v for k, v in you.items() if v}
    _mark(ans, "you")
    return _next("you")


@router.post("/setup/roles")
async def post_roles(request: Request):
    f = await request.form()
    ans = load_answers()
    pid = str(f.get("preset", ""))
    p = packs.preset(pid)
    ids = [i for i in f.getlist("role") if p and i in {r["id"] for r in p["role"]}]
    custom = []
    for i in range(3):
        name = str(f.get(f"custom{i}_name", "")).strip()[:80]
        if name:
            custom.append({"name": name, "keywords": _list(str(f.get(f"custom{i}_keywords", "")))[:30]})
    level = str(f.get("level", "entry"))
    ans["roles"] = {"preset": pid if p else "", "ids": ids, "custom": custom,
                    "level": level if level in packs.LEVELS else "entry"}
    save_answers(ans)
    if not packs.roles(ans):
        return _page(request, "roles", ans=ans, form_error="Pick at least one role, or name your own.")
    _mark(ans, "roles")
    return _next("roles")


@router.post("/setup/where")
async def post_where(request: Request):
    from remote_eligibility.geo import COUNTRIES
    f = await request.form()
    ans = load_answers()
    countries = [c for c in f.getlist("countries") if c in COUNTRIES]
    levels = {}
    for code in packs.CV_LANGS:
        lvl = str(f.get(f"lang_{code}", ""))
        if lvl in ("A1", "A2", "B1", "B2", "C1", "C2", "native"):
            levels[code] = lvl
    ans["where"] = {"countries": countries, "citizenships": [c for c in f.getlist("citizenships") if c in COUNTRIES],
                    "languages": list(levels) or ["en"], "language_levels": levels,
                    "local": bool(f.get("local")), "home": _list(str(f.get("home", "")))[:10],
                    "internships": bool(f.get("internships"))}
    save_answers(ans)
    if not countries:
        return _page(request, "where", ans=ans, form_error="Pick at least one country you can work from.")
    _mark(ans, "where")
    return _next("where")


def _llm_settings(form) -> dict:
    provider = str(form.get("provider", "none"))
    if provider not in ("none", "ollama", "openai"):
        provider = "none"
    base = str(form.get("base_url", "")).strip().rstrip("/")
    if provider == "ollama" and base:
        base = re.sub(r"/v1$", "", base) + "/v1"
    return {"provider": provider, "base_url": base or None, "model": str(form.get("model", "")).strip() or None}


def _test_llm(ai: dict, key: str | None) -> tuple[bool, str]:
    """A tiny request to the configured model. (The admin chooses this address, so LAN hosts are allowed.)"""
    from cvtracks.draft import LLMConfig, chat
    if ai["provider"] == "none":
        return True, "No AI: Apply fills facts only, and CV import uses the form."
    if not ai.get("base_url") or not ai.get("model"):
        return False, "Enter the address and the model name."
    if not ai["base_url"].lower().startswith(("http://", "https://")):
        return False, "The address must start with http:// or https://."
    if ai["provider"] == "ollama":
        root = re.sub(r"/v1$", "", ai["base_url"])
        try:
            with urllib.request.urlopen(f"{root}/api/tags", timeout=10) as r:
                names = [m["name"] for m in json.load(r).get("models", [])]
        except (urllib.error.URLError, OSError, ValueError) as e:
            return False, f"Couldn't reach Ollama at {root} ({getattr(e, 'reason', e)}). Is it running, and listening on the network (OLLAMA_HOST=0.0.0.0)?"
        if ai["model"] not in names and f"{ai['model']}:latest" not in names:
            return False, f"Ollama is running but has no model '{ai['model']}'. It has: {', '.join(names) or 'none'}. Run `ollama pull {ai['model']}` on that machine."
    try:
        reply = chat(LLMConfig(ai["base_url"], ai["model"], key, timeout=120), "Reply with the single word OK.")
    except (RuntimeError, ValueError) as e:
        return False, str(e)
    return True, f"It works: {ai['model']} answered “{reply[:40]}”."


@router.post("/setup/ai", response_class=HTMLResponse)
async def post_ai(request: Request):
    f = await request.form()
    ans = load_answers()
    ai = _llm_settings(f)
    key_file = _a().store.root / ".llm-key"
    new_key = str(f.get("api_key", "")).strip()
    if ai["provider"] == "openai" and new_key:
        old = os.umask(0o077)
        try:
            key_file.write_text(new_key + "\n", encoding="utf-8")
        finally:
            os.umask(old)
    elif ai["provider"] != "openai" and key_file.exists():
        key_file.unlink()
    ans["ai"] = ai
    save_answers(ans)
    if f.get("action") == "test" or (ai["provider"] != "none" and f.get("action") != "save_anyway"):
        ok, msg = _test_llm(ai, _a().llm_key() if ai["provider"] == "openai" else None)
        if f.get("action") == "test" or not ok:
            return _page(request, "ai", ans=ans, test_ok=ok, test_msg=msg)
    _mark(ans, "ai")
    return _next("ai")


@router.post("/setup/cv", response_class=HTMLResponse)
async def post_cv(request: Request):
    a = _a()
    f = await request.form()
    ans = load_answers()
    action = str(f.get("action", ""))
    if action == "keep" and a.store.has_master:
        _mark(ans, "cv")
        return _next("cv")
    if action == "draft":
        cv_text = str(f.get("cv_text", ""))
        if len(cv_text.strip()) < 200:
            return _page(request, "cv", ans=ans, form_error="Paste the whole text of your CV (it looks too short).",
                         cv_text=cv_text)
        ai = ans.get("ai") or {}
        if ai.get("provider") in (None, "none"):
            return _page(request, "cv", ans=ans, form_error="Set up an AI first (previous step), or use the form.")
        from cvtracks.draft import LLMConfig, draft_master
        try:
            d = draft_master(cv_text, LLMConfig(ai["base_url"], ai["model"], a.llm_key() if ai["provider"] == "openai" else None,
                                                timeout=600), (ans.get("you") or {}).get("cv_langs") or ["en"])
        except (RuntimeError, ValueError) as e:
            return _page(request, "cv", ans=ans, form_error=f"The AI draft failed: {e}", cv_text=cv_text)
        return _page(request, "cv", ans=ans, draft=packs.master_from_draft(ans, d.yaml), warnings=d.warnings)
    if action == "save_draft":
        text = str(f.get("draft", ""))
        try:
            a._validate("master.yaml", text)
        except Exception as e:
            return _page(request, "cv", ans=ans, draft=text, form_error=f"master.yaml doesn't load yet: {e}")
        a.store.write("master.yaml", text)
        _mark(ans, "cv")
        return _next("cv")
    if action in ("form", "save_draft") and a.store.has_master and not f.get("replace"):
        return _page(request, "cv", ans=ans, draft=str(f.get("draft", "")) or None,
                     form_error="You already have a master.yaml. Tick “Replace my current master.yaml” to overwrite it "
                                "(the old one stays in History), or keep it.")
    if action == "form":
        text = packs.master_from_form(ans, {k: str(v) for k, v in f.items()})
        a.store.write("master.yaml", text)
        _mark(ans, "cv")
        return _next("cv")
    return RedirectResponse("/setup/cv", status_code=303)


@router.post("/setup/sources")
async def post_sources(request: Request):
    f = await request.form()
    ans = load_answers()
    valid = {k for k, _, _ in packs.available_boards(ans)}
    keys.save(_a().store, {name: str(f.get(f"key_{name}", "")) for name in keys.allowed()})
    ans["sources"] = {"boards": [b for b in f.getlist("boards") if b in valid],
                      "companies": bool(f.get("companies")), "careers": bool(f.get("careers"))}
    _mark(ans, "sources")
    return _next("sources")


@router.post("/setup/finish")
def finish(run: str = Form(""), replace_feed: str = Form(""), replace_tracks: str = Form("")):
    a = _a()
    ans = load_answers()
    missing = [label for s, label in STEPS[:-1] if s not in set(ans.get("_done") or [])]
    if missing:
        return a.back("/setup/done", err="Finish these steps first: " + ", ".join(missing))
    try:
        old_profile = a.store.profile()
    except Exception:
        old_profile = {}
    old_feed = a.store.read("feed.toml") if a.store.path("feed.toml").exists() else ""
    a.store.write("profile.toml", packs.profile_toml(ans, old_profile))
    from . import places
    places.ensure(a.store)
    if replace_feed or not _has_searches(old_feed):
        a.store.write("feed.toml", packs.feed_toml(ans, old_feed))
    for rel, text in packs.track_files(ans).items():
        if replace_tracks or not a.store.path(rel).exists():
            a.store.write(rel, text)
    _mark(ans, "done")
    if run:
        from . import feed
        feed.run_now(a.store, force=True)
        return a.back("/feed", msg="Setup saved. The first feed run has started; it takes a few minutes, "
                                   "then matches appear here.")
    return a.back("/", msg="Setup saved.")


def _has_searches(feed_text: str) -> bool:
    """An existing feed.toml with searches of its own: a set-up install whose searches may be hand-tuned.
    The starter file written on first start doesn't count."""
    from jobfeed.config import EXAMPLE
    if feed_text.strip() == EXAMPLE.strip():
        return False
    try:
        return bool(tomllib.loads(feed_text).get("search")) if feed_text.strip() else False
    except Exception:
        return True  # unreadable: don't overwrite it silently
