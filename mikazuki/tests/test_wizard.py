"""First-run account creation and the setup wizard."""

import importlib
import json
import re
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient


def fresh_app(tmp_path, monkeypatch):
    for k in ("MIKAZUKI_PASSWORD", "MIKAZUKI_USER", "JOBDESK_PASSWORD", "JOBDESK_USER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MIKAZUKI_DATA", str(tmp_path))
    monkeypatch.setenv("MIKAZUKI_SCHEDULER", "0")
    import mikazuki.app as appmod
    appmod = importlib.reload(appmod)
    return appmod, TestClient(appmod.app)


def tok(c, url):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(url).text).group(1)


def create_account(c, tmp_path, code=None, password="correct horse battery"):
    code = code or (tmp_path / "setup-code.txt").read_text().strip()
    return c.post("/setup/account", data={"csrf": tok(c, "/setup/account"), "code": code, "username": "jane",
                                          "password": password, "password2": password}, follow_redirects=False)


def test_first_run_needs_the_setup_code(tmp_path, monkeypatch):
    appmod, c = fresh_app(tmp_path, monkeypatch)
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup/account"
    assert c.get("/login", follow_redirects=False).headers["location"] == "/setup/account"
    assert c.post("/feed/run", data={}).status_code in (401, 403)
    assert create_account(c, tmp_path, code="AAAA-BBBB-CCCC").status_code == 400
    assert create_account(c, tmp_path, password="short").status_code == 400
    r = create_account(c, tmp_path)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert not (tmp_path / "setup-code.txt").exists()
    acc = json.loads((tmp_path / ".account").read_text())
    assert acc["user"] == "jane" and acc["hash"].startswith("scrypt$") and "correct" not in acc["hash"]
    assert oct((tmp_path / ".account").stat().st_mode & 0o777) == "0o600"
    assert c.get("/settings").status_code == 200  # signed in by the account creation
    # A second account can't be created, and the password works at /login.
    assert c.get("/setup/account", follow_redirects=False).headers["location"] == "/setup"
    c2 = TestClient(appmod.app)
    t = tok(c2, "/login")
    assert c2.post("/login", data={"csrf": t, "username": "jane", "password": "correct horse battery", "next": "/"},
                   follow_redirects=False).status_code == 303


def _post(c, step, data):
    return c.post(f"/setup/{step}", data={"csrf": tok(c, f"/setup/{step}"), **data}, follow_redirects=False)


def test_wizard_without_ai_writes_working_files(tmp_path, monkeypatch):
    appmod, c = fresh_app(tmp_path, monkeypatch)
    create_account(c, tmp_path)
    assert "Start the setup" in c.get("/").text
    assert _post(c, "you", {"name": "Marta López", "email": "marta@example.com", "linkedin": "https://www.linkedin.com/in/marta-l/",
                            "cv_langs": ["en", "es"], "cv_main": "es"}).headers["location"] == "/setup/roles"
    r = _post(c, "roles", {"preset": "business-creative", "role": ["customer", "marketing"], "level": "entry",
                           "custom0_name": "Translator", "custom0_keywords": "translator, traductor"})
    assert r.headers["location"] == "/setup/where"
    assert _post(c, "where", {"countries": ["ES"]}).status_code == 303
    r = _post(c, "where", {"countries": ["ES", "PT"], "citizenships": ["ES"], "lang_es": "native", "lang_en": "B2",
                           "local": "1", "home": "Sevilla, Dos Hermanas"})
    assert r.headers["location"] == "/setup/ai"
    assert _post(c, "ai", {"provider": "none", "action": "save"}).headers["location"] == "/setup/cv"
    r = _post(c, "cv", {"action": "form", "summary": "Customer support specialist.", "jobs": "3", "edus": "2",
                        "job0_role": "Help Desk Technician", "job0_company": "Logística Levante", "job0_start": "2023-03",
                        "job0_bullets": "Supported 300 users\n- Cut password tickets by 40%",
                        "edu0_degree": "CFGM SMR", "edu0_school": "IES Example", "edu0_start": "2017", "edu0_end": "2019",
                        "certs": "CompTIA A+ | CompTIA | 2024", "skills": "Tools: Zendesk, Excel"})
    assert r.headers["location"] == "/setup/sources"
    page = c.get("/setup/sources").text
    assert "Remotive" in page and "InfoJobs" in page and "LinkedIn" in page  # alerts-only boards are named
    assert _post(c, "sources", {"boards": ["remotive", "himalayas", "bogus"], "companies": "1"}).status_code == 303
    r = c.post("/setup/finish", data={"csrf": tok(c, "/setup/done")}, follow_redirects=False)
    assert r.status_code == 303

    from cvtracks.loader import load_master, load_tracks
    from jobfeed.config import parse
    m = load_master(tmp_path / "master.yaml")
    assert m.person.name == "Marta López" and m.person.linkedin == "marta-l"
    assert m.experience[0].ongoing and len(m.experience[0].bullets) == 2
    assert m.languages[0].name == {"es": "Español", "en": "Spanish"}
    t = load_tracks(tmp_path / "tracks")
    assert set(t) == {"customer", "marketing", "translator"} and t["customer"].languages == ["es", "en"]
    cfg = parse((tmp_path / "feed.toml").read_text())
    assert [s.name for s in cfg.searches] == ["Customer Support Specialist", "Marketing Specialist", "Translator"]
    s = cfg.searches[0]
    assert s.sources == ["remotive", "himalayas", "companies"] and not s.remote_only and s.locations == ["Sevilla", "Dos Hermanas"]
    assert "senior" in s.exclude_title and "manager" in s.exclude_title
    assert "manager" not in cfg.searches[1].exclude_title  # "community manager" is a marketing keyword
    assert cfg.companies and not cfg.careers
    prof = tomllib.loads((tmp_path / "profile.toml").read_text())
    assert prof["countries"] == ["ES", "PT"] and prof["languages"] == ["en", "es"] and "llm" not in prof
    assert appmod.profile_and_llm()[1] is None


class FakeAI(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAI.seen.append((self.path, self.headers.get("Authorization"), body))
        prompt = body["messages"][-1]["content"]
        if "Reply with the single word OK" in prompt:
            text = "OK"
        else:
            text = ("```yaml\nperson: {name: MARTA LOPEZ, email: m@example.com, nationality: Spanish}\n"
                    "summaries: {main: Support specialist.}\n"
                    "experience:\n- {id: lev, role: Help Desk Technician, company: Logistica Levante, start: 2023-03, ongoing: true,"
                    " bullets: [Supported 300 users]}\n- {id: x, role: CEO, company: Globex, start: 2010}\n```")
        out = json.dumps({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_ai():
    srv = HTTPServer(("127.0.0.1", 0), FakeAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeAI.seen.clear()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_ai_service_key_and_cv_draft(tmp_path, monkeypatch, fake_ai):
    appmod, c = fresh_app(tmp_path, monkeypatch)
    create_account(c, tmp_path)
    _post(c, "you", {"name": "Marta López", "email": "marta@example.com", "cv_langs": ["en"], "cv_main": "en"})
    _post(c, "roles", {"preset": "it-security", "role": ["support"], "level": "entry"})
    _post(c, "where", {"countries": ["ES"], "lang_en": "C1"})
    r = _post(c, "ai", {"provider": "openai", "base_url": fake_ai, "model": "small", "api_key": "sk-test-123",
                        "action": "test"})
    assert "It works" in r.text
    assert FakeAI.seen[-1][1] == "Bearer sk-test-123"
    assert (tmp_path / ".llm-key").read_text().strip() == "sk-test-123"
    assert oct((tmp_path / ".llm-key").stat().st_mode & 0o777) == "0o600"
    assert "sk-test-123" not in c.get("/setup/ai").text  # the key is never shown again
    assert _post(c, "ai", {"provider": "openai", "base_url": fake_ai, "model": "small", "action": "save"}).status_code == 303
    r = _post(c, "cv", {"action": "draft", "cv_text": "MARTA LOPEZ. Help Desk Technician at Logistica Levante since 2023. " * 5})
    assert "Read every line" in r.text and "Globex" in r.text and "not in the CV text" in r.text
    assert "nationality" not in re.search(r'<textarea name="draft"[^>]*>(.*?)</textarea>', r.text, re.S).group(1)
    draft = re.search(r'<textarea name="draft"[^>]*>(.*?)</textarea>', r.text, re.S).group(1)
    import html
    assert _post(c, "cv", {"action": "save_draft", "draft": html.unescape(draft)}).headers["location"] == "/setup/sources"
    _post(c, "sources", {"boards": ["remotive"], "companies": "1", "careers": "1"})
    c.post("/setup/finish", data={"csrf": tok(c, "/setup/done")})
    prof = tomllib.loads((tmp_path / "profile.toml").read_text())
    assert prof["llm"] == {"provider": "openai", "base_url": fake_ai, "model": "small", "api_key_file": ".llm-key"}
    assert "sk-test" not in (tmp_path / "profile.toml").read_text()
    assert appmod.profile_and_llm()[1].api_key == "sk-test-123"
    from mikazuki import apply
    llm = apply.LLM.from_profile(prof, appmod.llm_key())
    assert llm.openai and llm.api_key == "sk-test-123"
    q = apply.Question(id="why", label="Why do you want this job?", kind="textarea")
    assert apply.ask(llm, "FACTS", "Job at X", q)
    path, auth_header, body = FakeAI.seen[-1]
    assert path == "/v1/chat/completions" and auth_header == "Bearer sk-test-123"
    assert body["model"] == "small" and body["messages"][0]["role"] == "system" and "Why do you want" in body["messages"][1]["content"]
    m = (tmp_path / "master.yaml").read_text()
    assert "Marta López" in m and "support:" in m  # contact from the wizard, one summary per role


def test_change_password(tmp_path, monkeypatch):
    appmod, c = fresh_app(tmp_path, monkeypatch)
    create_account(c, tmp_path)
    t = tok(c, "/settings")
    r = c.post("/settings/password", data={"csrf": t, "current": "wrong", "password": "another long one", "password2": "another long one"},
               follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = c.post("/settings/password", data={"csrf": t, "current": "correct horse battery", "password": "another long one",
                                           "password2": "another long one"}, follow_redirects=False)
    assert r.headers["location"].startswith("/login")
    assert c.get("/settings", follow_redirects=False).status_code == 303  # old session signed out


def test_running_the_setup_again_keeps_hand_tuned_files(tmp_path, monkeypatch):
    from importlib.resources import files
    appmod, c = fresh_app(tmp_path, monkeypatch)
    create_account(c, tmp_path)
    master = files("cvtracks").joinpath("examples/master.yaml").read_text(encoding="utf-8")
    feed = ('[[search]]\nname = "Tuned"\ntrack = "support"\nkeywords = ["help desk"]\n'
            'locations = ["Toledo"]   # my own comment\n')
    (tmp_path / "master.yaml").write_text(master, encoding="utf-8")
    (tmp_path / "feed.toml").write_text(feed, encoding="utf-8")
    (tmp_path / "tracks").mkdir(exist_ok=True)
    (tmp_path / "tracks" / "support.yaml").write_text("id: support\ntitle: Mine\nlanguages: [es, en]\n", encoding="utf-8")
    (tmp_path / "profile.toml").write_text('countries = ["ES"]\nlanguages = ["en", "es"]\n', encoding="utf-8")

    # prefilled from the existing files
    assert re.search(r'name="lang_es">.*?<option value="native" selected', c.get("/setup/where").text, re.S)
    assert re.search(r'name="lang_en">.*?<option value="C1" selected', c.get("/setup/where").text, re.S)
    assert re.search(r'value="es" checked', c.get("/setup/you").text)

    _post(c, "you", {"name": "Ana", "cv_langs": ["es", "en"], "cv_main": "es"})
    _post(c, "roles", {"preset": "it-security", "role": ["support", "security"], "level": "entry"})
    _post(c, "where", {"countries": ["ES"], "lang_es": "native", "lang_en": "C1"})
    _post(c, "ai", {"provider": "none", "action": "save"})
    r = _post(c, "cv", {"action": "form", "summary": "x", "jobs": "3", "edus": "2"})
    assert r.status_code == 200 and "Replace my current master.yaml" in r.text
    assert (tmp_path / "master.yaml").read_text(encoding="utf-8") == master
    assert _post(c, "cv", {"action": "keep"}).headers["location"] == "/setup/sources"
    _post(c, "sources", {"boards": ["remotive"]})
    page = c.get("/setup/done").text
    assert "Replace my current searches" in page and "tracks/support.yaml" in page
    assert c.post("/setup/finish", data={"csrf": tok(c, "/setup/done")}, follow_redirects=False).status_code == 303
    assert (tmp_path / "feed.toml").read_text(encoding="utf-8") == feed
    assert (tmp_path / "tracks" / "support.yaml").read_text(encoding="utf-8").startswith("id: support\ntitle: Mine")
    assert (tmp_path / "tracks" / "security.yaml").exists()
    assert set(tomllib.loads((tmp_path / "profile.toml").read_text())["languages"]) == {"es", "en"}

    c.post("/setup/finish", data={"csrf": tok(c, "/setup/done"), "replace_feed": "1"}, follow_redirects=False)
    assert "Tuned" not in (tmp_path / "feed.toml").read_text(encoding="utf-8")


def test_country_scoped_source_is_offered_with_its_key_note(monkeypatch):
    from mikazuki import packs
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    boards = {k: (label, on) for k, label, on in packs.available_boards({"where": {"countries": ["IN"]}})}
    label, on = boards["adzuna"]
    assert "developer.adzuna.com" in label and "enter it below" in label and on is False
    assert "adzuna" not in {k for k, _, _ in packs.available_boards({"where": {"countries": ["RO"]}})}  # no Adzuna site
    monkeypatch.setenv("ADZUNA_APP_ID", "x")
    monkeypatch.setenv("ADZUNA_APP_KEY", "y")
    assert dict((k, on) for k, _, on in packs.available_boards({"where": {"countries": ["IN"]}}))["adzuna"] is True
    assert "Naukri" in packs.alerts_only({"where": {"countries": ["IN"]}})


def test_feed_toml_gives_adzuna_the_chosen_countries_it_covers():
    import tomllib
    from mikazuki import packs
    ans = {"roles": {"preset": "", "custom": [{"title": "SOC analyst", "keywords": ["soc analyst"]}]},
           "where": {"countries": ["IN", "RO", "GB"]}, "sources": {"boards": ["remotive", "adzuna"], "companies": False, "careers": False}}
    feed = tomllib.loads(packs.feed_toml(ans, '[sources.adzuna]\nmax_keywords = 2\n'))
    assert feed["sources"]["adzuna"] == {"max_keywords": 2, "countries": ["GB", "IN"]}  # RO has no Adzuna site; old options kept


def test_source_keys_entered_in_the_setup(tmp_path, monkeypatch):
    import os
    from mikazuki import keys
    for name in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "USAJOBS_API_KEY", "USAJOBS_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USAJOBS_API_KEY", "from-dotenv")
    keys._ours.clear()
    appmod, c = fresh_app(tmp_path, monkeypatch)
    create_account(c, tmp_path)
    assert _post(c, "where", {"countries": ["IN", "US"]}).status_code == 303
    page = c.get("/setup/sources").text
    assert 'name="key_ADZUNA_APP_KEY"' in page and "developer.adzuna.com" in page
    assert 'name="key_USAJOBS_API_KEY"' not in page and "set in the environment" in page  # .env wins, not editable
    r = _post(c, "sources", {"boards": ["adzuna"], "key_ADZUNA_APP_ID": "id-1", "key_ADZUNA_APP_KEY": "secret-1",
                             "key_USAJOBS_API_KEY": "from-form", "key_PATH": "/evil", "companies": "1"})
    assert r.status_code == 303
    stored = json.loads((tmp_path / ".source-keys.json").read_text())
    assert stored == {"ADZUNA_APP_ID": "id-1", "ADZUNA_APP_KEY": "secret-1"}  # no PATH, no override of .env
    assert oct((tmp_path / ".source-keys.json").stat().st_mode & 0o777) == "0o600"
    assert os.environ["ADZUNA_APP_KEY"] == "secret-1" and os.environ["USAJOBS_API_KEY"] == "from-dotenv"
    page = c.get("/setup/sources").text
    assert "secret-1" not in page and "Saved. Leave empty to keep it" in page
    _post(c, "sources", {"boards": ["adzuna"], "key_ADZUNA_APP_KEY": ""})  # empty keeps the saved key
    assert json.loads((tmp_path / ".source-keys.json").read_text())["ADZUNA_APP_KEY"] == "secret-1"
    for name in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"):
        os.environ.pop(name, None)
    keys._ours.clear()
