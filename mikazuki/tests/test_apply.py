import json
import re
import time

import pytest

from mikazuki import apply as A
from test_app import client, csrf  # noqa: F401 (fixture)

GH_URL = "https://job-boards.greenhouse.io/acme/jobs/123"
GH_JSON = {
    "title": "SOC Analyst", "ai_disclaimer": None,
    "questions": [
        {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text", "values": []}]},
        {"label": "Resume/CV", "required": True, "fields": [{"name": "resume", "type": "input_file", "values": []},
                                                             {"name": "resume_text", "type": "textarea", "values": []}]},
        {"label": "Why <b>Acme</b>? <script>alert(1)</script>", "required": True,
         "fields": [{"name": "question_1", "type": "textarea", "values": []}]},
        {"label": "Are you legally authorized to work in Spain?", "required": True,
         "fields": [{"name": "question_2", "type": "multi_value_single_select", "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
        {"label": "What are your salary expectations?", "required": False, "fields": [{"name": "question_3", "type": "input_text", "values": []}]},
        {"label": "Which gender do you identify as?", "required": True,
         "fields": [{"name": "question_4", "type": "multi_value_single_select", "values": [{"label": "Female", "value": 1}, {"label": "Male", "value": 2}]}]},
        {"label": "hidden", "required": False, "fields": [{"name": "x", "type": "input_hidden", "values": []}]},
    ],
}
LEVER_HTML = """
<ul><li class="application-question resume"><div class="application-label">Resume/CV<span>✱</span></div>
<input type="file" name="resume"></li>
<li class="application-question"><div class="application-label">Full name<span>✱</span></div><input type="text" name="name"></li>
<li class="application-question custom-question"><div class="application-label"><div class="text">Applicants must have authorization to work in the jurisdiction, without requiring employer sponsorship.</div></div>
<ul><li><label><input type="radio" name="cards[abc][field0]" value="Yes – I confirm">Yes – I confirm</label></li></ul></li>
<li class="application-question"><div class="application-field"><ul><li><label><input type="hidden" name="consent[marketing]" value="0"><input type="checkbox" name="consent[marketing]" value="1"><p>Yes, Acme can contact me</p></label></li></ul></div></li>
</ul>"""


# ---------------------------------------------------------------- units


def test_form_source_only_known_hosts():
    assert A.form_source(GH_URL) == ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123?questions=true")
    assert A.form_source("https://job-boards.eu.greenhouse.io/acme/jobs/9")[0] == "greenhouse"
    assert A.form_source("https://jobs.lever.co/acme/f8f13562-1522-4377-821a-7854239cf36b")[1].endswith("/apply")
    for url in ("https://evil.example/acme/jobs/123", "http://192.168.0.1/acme/jobs/1", "https://greenhouse.io.evil.com/a/jobs/1",
                "https://accenture.wd103.myworkdayjobs.com/x/job/y", "javascript:alert(1)"):
        assert A.form_source(url) is None
        assert A.read_form(url)[0] == "none"  # never fetched


def test_parse_greenhouse_and_lever():
    qs, _ = A.parse_greenhouse(GH_JSON)
    assert [q.kind for q in qs] == ["text", "file", "textarea", "select", "text", "select"]  # hidden field skipped
    assert qs[2].label == "Why Acme ? alert(1)"  # tags stripped
    assert qs[3].options == ["Yes", "No"]
    lq = A.parse_lever(LEVER_HTML)
    assert [(q.label[:20], q.kind, q.required) for q in lq] == [
        ("Resume/CV", "file", True), ("Full name", "text", True),
        ("Applicants must have", "select", False), ("Yes, Acme can contac", "consent", False)]
    assert lq[2].options == ["Yes – I confirm"]


@pytest.mark.parametrize("text", [
    "During this application process I agree to use only my own words.",
    "The use of AI or other generated content will disqualify your application.",
    "Applications written with ChatGPT are not allowed.",
    "Responde sin usar inteligencia artificial.",
])
def test_ai_ban_detected(text):
    assert A.ai_ban(["Some intro.", text])


@pytest.mark.parametrize("text", [
    "You will secure AI systems and LLM pipelines against prompt injection.",
    "Experience with generative AI is a plus. We reject nothing but bad coffee.",
    "Do not hesitate to apply.",
])
def test_ai_ban_not_triggered_by_job_topics(text):
    assert A.ai_ban([text]) == ""


def test_fill_by_code_never_guesses():
    master = {"person": {"name": "Ana Maria Pop", "email": "a@example.com", "phone": "+34 600", "github": "anapop",
                         "website": "ana.dev", "location": {"en": "Madrid, Spain"}},
              "languages": [{"name": {"en": "Romanian"}, "level": {"en": "Native"}}],
              "experience": [{"company": "Shop SL", "start": "2022-01", "end": "2025-01"}]}
    profile = {"countries": ["ES"], "citizenships": ["RO"], "apply": {"availability": "Two weeks"}}
    qs, _ = A.parse_greenhouse(GH_JSON)
    qs += A.parse_pasted("What languages do you speak?\nCurrent company\nLinkedIn profile\nWhen can you start?")
    A.fill_by_code(qs, A.facts(master, profile, {"source_name": "Greenhouse"}, "en", "tailored/cv.pdf"))
    got = {q.label: (q.origin, q.answer) for q in qs}
    assert got["First Name"] == ("code", "Ana Maria")
    assert got["Resume/CV"] == ("code", "tailored/cv.pdf")
    assert got["Why Acme ? alert(1)"] == ("ai", "")
    assert got["Are you legally authorized to work in Spain?"] == ("code", "Yes")
    assert got["What are your salary expectations?"] == ("code", "[NEEDS INPUT: salary]")
    assert got["Which gender do you identify as?"] == ("you", "")
    assert got["What languages do you speak?"] == ("code", "Romanian: Native")
    nat = A.parse_pasted("What is your nationality?\nCurrent location\nIn which country do you currently live?")
    A.fill_by_code(nat, A.facts(master, profile, {}, "en", ""))
    assert all(q.origin == "you" and q.answer == "" for q in nat)  # personal data is never filled in
    assert got["Current company"][1].startswith("None at the moment (last: Shop SL")
    assert got["LinkedIn profile"] == ("code", "[NEEDS INPUT: linkedin]")
    assert got["When can you start?"] == ("code", "Two weeks")


def test_no_work_auth_claim_without_eu_citizenship():
    qs = A.parse_pasted("Do you have the right to work in the EU?")
    A.fill_by_code(qs, A.facts({"person": {}}, {"countries": ["ES"], "citizenships": []}, {}, "en", ""))
    assert "[NEEDS INPUT" in qs[0].answer


# ---------------------------------------------------------------- through the app


@pytest.fixture
def job(client, monkeypatch):
    tok = csrf(client)
    client.post("/init", data={"csrf": tok})
    client.post("/edit", data={"csrf": tok, "file": "profile.toml", "text":
                               'countries = ["ES"]\ncitizenships = ["RO"]\nlanguages = ["en", "es"]\n'
                               '[llm]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "x"\n'})
    r = client.post("/jobs/new", data={"csrf": tok, "title": "SOC Analyst", "company": "Acme", "url": GH_URL,
                                       "text": "We monitor alerts with Splunk and the SIEM.", "status": "saved"})
    uid = re.search(r'/jobs/([0-9a-f]{16})/', r.text).group(1)
    return tok, uid


def _wait(client, uid):
    for _ in range(100):
        r = client.get(f"/jobs/{uid}/apply")
        if "Drafting open answers" not in r.text:
            return r
        time.sleep(0.05)
    raise AssertionError("drafting never finished")


def test_apply_reads_form_tailors_cv_and_drafts(client, job, monkeypatch):
    tok, uid = job
    seen = []
    monkeypatch.setattr(A, "fetch", lambda url, timeout=20: json.dumps(GH_JSON))
    monkeypatch.setattr(A, "ask", lambda llm, facts, line, q, lang="en": seen.append((facts, q.label)) or "Because of the SIEM work.")
    r = client.post(f"/jobs/{uid}/apply", data={"csrf": tok})
    assert r.status_code == 200 and "Form read, CV tailored" in r.text
    r = _wait(client, uid)
    assert "Because of the SIEM work." in r.text
    assert [x[1] for x in seen] == ["Why Acme ? alert(1)", "Cover letter"] and len(seen[0][0]) > 200  # the CV as facts
    assert "<script>alert(1)" not in r.text  # labels from the form are text, never markup
    assert "Tailored CV" in r.text and "1 need" in r.text  # salary
    assert "tailored/" in client.get(f"/jobs/{uid}").text  # CV attached to the tracker job
    # edits are kept and survive a redraft of everything else
    fields = {f"a{i}": "" for i in range(6)}
    fields.update({"csrf": tok, "a2": "My own answer", "a4": "35.000 € gross/year"})
    client.post(f"/jobs/{uid}/apply/save", data=fields)
    client.post(f"/jobs/{uid}/apply/draft", data={"csrf": tok})
    r = _wait(client, uid)
    assert "My own answer" in r.text and "35.000 € gross/year" in r.text
    assert [x[1] for x in seen[2:]] == ["Cover letter"]  # only the unedited AI text was redrafted


def test_apply_respects_ai_ban(client, job, monkeypatch):
    tok, uid = job
    banned = dict(GH_JSON, questions=GH_JSON["questions"] + [
        {"label": "I agree to use only my own words; AI-generated answers will disqualify me.", "required": True,
         "fields": [{"name": "q9", "type": "multi_value_single_select", "values": [{"label": "Yes", "value": 1}]}]}])
    monkeypatch.setattr(A, "fetch", lambda url, timeout=20: json.dumps(banned))
    monkeypatch.setattr(A, "ask", lambda *a: pytest.fail("must not draft when AI is forbidden"))
    r = client.post(f"/jobs/{uid}/apply", data={"csrf": tok})
    assert "forbids AI-written answers" in r.text
    r = client.post(f"/jobs/{uid}/apply/draft", data={"csrf": tok})
    assert "nothing is drafted" in r.text


def test_apply_paste_questions_and_offline_model(client, job, monkeypatch):
    tok, uid = job
    monkeypatch.setattr(A, "read_form", lambda url: ("none", "", [], []))
    r = client.post(f"/jobs/{uid}/apply", data={"csrf": tok})
    assert "Paste its questions" in r.text
    # the real `ask` against a closed port: a clear error, not a crash
    r = client.post(f"/jobs/{uid}/apply/questions", data={"csrf": tok, "questions": "Why us?\n\nEmail *"})
    r = _wait(client, uid)
    assert "reach the local model" in r.text and "Is the model host running?" in r.text
    assert "a@" in r.text or "@" in r.text  # email filled by code from master.yaml


def test_apply_csrf_and_bad_ids(client, job):
    tok, uid = job
    assert client.post(f"/jobs/{uid}/apply", data={}).status_code == 403
    assert "Press Apply first" in client.get(f"/jobs/{uid}/apply").text
    assert client.get("/jobs/..%2f..%2fprofile.toml/apply").status_code in (303, 404, 200)
    with pytest.raises(ValueError):
        A.AppStore(__import__("pathlib").Path("/tmp")).get("../../etc/passwd")


def test_deleting_job_deletes_application(client, job, monkeypatch, tmp_path):
    tok, uid = job
    monkeypatch.setattr(A, "read_form", lambda url: ("none", "", [], []))
    client.post(f"/jobs/{uid}/apply", data={"csrf": tok})
    assert (tmp_path / "applications" / f"{uid}.json").exists()
    client.post(f"/jobs/{uid}/delete", data={"csrf": tok})
    assert not (tmp_path / "applications" / f"{uid}.json").exists()


def test_apply_settings_per_track_and_language():
    profile = {"citizenships": ["RO"], "apply": {
        "salary": {"en": "€26,000–30,000 gross per year", "es": "26.000–30.000 € brutos anuales"},
        "salary_helpdesk": {"en": "€18,000–21,000 gross per year", "es": "18.000–21.000 € brutos anuales"},
        "driving_licence": {"en": "Yes, B", "es": "Sí, B"}, "travel": {"en": "Yes", "es": "Sí"},
        "work_mode": {"en": "Remote, or hybrid in Madrid", "es": "Remoto, o híbrido en Madrid"}}}
    qs = A.parse_pasted("¿Cuál es tu expectativa salarial?\n\n¿Tienes carné de conducir?")
    A.fill_by_code(qs, A.facts({"person": {}}, profile, {}, "es", "", "helpdesk"))
    assert [q.answer for q in qs] == ["18.000–21.000 € brutos anuales", "Sí, B"]
    qs = A.parse_pasted("Salary expectations?")
    A.fill_by_code(qs, A.facts({"person": {}}, profile, {}, "en", "", "soc"))
    assert qs[0].answer == "€26,000–30,000 gross per year"
    travel = A.Question(id="t", label="We require all colleagues to meet in person 2-4 times a year. Is that OK?",
                        kind="select", options=["Yes", "No"])
    mode = A.Question(id="m", label="Are you open to hybrid work?", kind="text")
    essay = A.Question(id="e", label="Tell us how you stay productive when working remote.", kind="textarea")
    A.fill_by_code([travel, mode, essay], A.facts({"person": {}}, profile, {}, "en", "", "soc"))
    assert (travel.answer, mode.answer, essay.origin) == ("Yes", "Remote, or hybrid in Madrid", "ai")


def test_about_md_reaches_the_model(client, job, monkeypatch, tmp_path):
    tok, uid = job
    (tmp_path / "about.md").write_text("---\ntitle: x\n---\nI moved to Spain at 16.", encoding="utf-8")
    seen = []
    monkeypatch.setattr(A, "read_form", lambda url: ("none", "", [], []))
    monkeypatch.setattr(A, "ask", lambda llm, facts, line, q, lang="en": seen.append(facts) or "ok")
    client.post(f"/jobs/{uid}/apply", data={"csrf": tok})
    client.post(f"/jobs/{uid}/apply/questions", data={"csrf": tok, "questions": "Tell us about yourself"})
    _wait(client, uid)
    assert seen and "I moved to Spain at 16." in seen[0] and "title: x" not in seen[0]


@pytest.mark.parametrize("title,track,expected", [
    ("Analista SOC (m/f/d) - Remoto", "soc", "Analista SOC"),
    ("Junior Cybersecurity Analyst, Madrid", "soc", "Junior Cybersecurity Analyst"),
    ("Senior SOC Analyst", "soc", None),                 # never claim seniority
    ("Ubuntu Security Engineer", "soc", None),           # a different role keeps the track headline
    ("Pentester Junior | Hybrid", "pentest", "Pentester Junior"),
    ("Técnico de Soporte Informático", "helpdesk", "Técnico de Soporte Informático"),
    ("SOC Team Lead", "soc", None),
])
def test_headline_mirrors_only_the_same_junior_role(title, track, expected):
    assert A.headline_for(title, track) == expected


def test_cover_letter_respects_ai_ban(client, job, monkeypatch):
    tok, uid = job
    banned = dict(GH_JSON, questions=GH_JSON["questions"] + [
        {"label": "I agree to use only my own words.", "required": True,
         "fields": [{"name": "q9", "type": "multi_value_single_select", "values": [{"label": "Yes", "value": 1}]}]}])
    monkeypatch.setattr(A, "fetch", lambda url, timeout=20: json.dumps(banned))
    monkeypatch.setattr(A, "ask", lambda *a, **k: pytest.fail("must not draft"))
    r = client.post(f"/jobs/{uid}/apply", data={"csrf": tok})
    assert "Cover letter" in r.text and "write it yourself, or skip it" in r.text
