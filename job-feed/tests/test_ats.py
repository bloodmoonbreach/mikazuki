import pytest

from jobfeed import ats, http
from jobfeed.config import ConfigError, parse
from jobfeed.sources import SOURCES
from jobfeed.store import Store
from jobfeed.pipeline import run
from remote_eligibility import Profile


@pytest.mark.parametrize("url,expected", [
    ("https://job-boards.greenhouse.io/bugcrowd/jobs/8007216?gh_jid=8007216", ("greenhouse", "bugcrowd")),
    ("https://boards.greenhouse.io/embed/job_board?for=gitlab", ("greenhouse", "gitlab")),
    ("https://jobs.lever.co/sophos/abc-123", ("lever", "sophos")),
    ("https://jobs.eu.lever.co/acme", ("lever", "acme")),
    ("https://jobs.ashbyhq.com/1password/xyz", ("ashby", "1password")),
    ("https://apply.workable.com/trailofbits/j/ABC/", ("workable", "trailofbits")),
    ("https://jobs.smartrecruiters.com/BoschGroup/744000", ("smartrecruiters", "BoschGroup")),
    ("https://intigriti.recruitee.com/o/x", ("recruitee", "intigriti")),
    ("https://acme.jobs.personio.de/job/1", ("personio", "acme")),
    ("https://acme.teamtailor.com/jobs/1", ("teamtailor", "acme")),
    ("https://acme.breezy.hr/p/1", ("breezy", "acme")),
    ("https://accenture.wd103.myworkdayjobs.com/en-US/AccentureCareers/job/X", ("workday", "accenture.wd103.myworkdayjobs.com/accenture/AccentureCareers")),
    ("https://my.greenhouse.io/users/sign_in?job_board=scbitdefendersrl&source=job_alert_post", ("greenhouse", "scbitdefendersrl")),
    ("https://www.linkedin.com/jobs/view/1", None),
    ("https://evil.example/boards.greenhouse.io/x", None),
])
def test_detect(url, expected):
    assert ats.detect(url) == expected


def test_company_config_validation():
    cfg = parse('[[company]]\nname="Acme"\nats="greenhouse"\nslug="acme"\n')
    assert cfg.companies[0].slug == "acme"
    for bad in ('ats="nope"\nslug="x"', 'ats="greenhouse"\nslug="../../etc"', 'ats="workday"\nslug="host/only"',
                'ats="greenhouse"\nslug="a b"', 'ats="greenhouse"'):
        with pytest.raises(ConfigError):
            parse(f'[[company]]\nname="X"\n{bad}\n')


def test_companies_source_fetches_details_only_for_matching_titles(tmp_path, monkeypatch):
    listed = [ats.Opening("1", "Junior Security Analyst", "Remote - EMEA", "https://acme.example/1"),
              ats.Opening("2", "Account Executive", "Remote - EMEA", "https://acme.example/2"),
              ats.Opening("3", "Senior Security Analyst", "Remote - EMEA", "https://acme.example/3")]
    details = []
    monkeypatch.setitem(ats.ADAPTERS, "greenhouse",
                        (lambda c: listed, lambda c, o: details.append(o.id) or "Fully remote. Must be based in the EU."))
    cfg = parse('''
[[search]]
name = "Cybersecurity"
keywords = ["security analyst"]
exclude_title = ["senior"]
sources = ["companies"]
[[company]]
name = "Acme"
ats = "greenhouse"
slug = "acme"
''')
    store = Store(tmp_path / "feed.db")
    res = run(cfg, store, Profile.parse("ES,RO", "RO"), notify=False, force=True)
    assert details == ["1"]  # only the matching, non-senior title got a detail call
    row = res.new[0]
    assert row["title"] == "Junior Security Analyst" and row["source_name"] == "Acme careers"
    assert row["verdicts"] == {"ES": "likely", "RO": "likely"} and row["good"]


def test_one_broken_company_does_not_stop_the_rest(tmp_path, monkeypatch):
    def lst(c):
        if c.slug == "broken":
            raise http.FetchError("HTTP 404")
        return [ats.Opening("1", "Security Analyst", "Remote", "https://ok.example/1", raw={"description": "Remote, EU."})]
    monkeypatch.setitem(ats.ADAPTERS, "lever", (lst, None))
    cfg = parse('[[search]]\nname="c"\nkeywords=["security analyst"]\nsources=["companies"]\n'
                '[[company]]\nname="Broken"\nats="lever"\nslug="broken"\n[[company]]\nname="Ok"\nats="lever"\nslug="ok"\n')
    res = run(cfg, Store(tmp_path / "f.db"), Profile.parse("ES"), notify=False, force=True)
    assert len(res.new) == 1 and "Broken" in res.errors.get("companies (partial)", "")


def test_foorilla_source(monkeypatch):
    from jobfeed.sources import builtin
    from jobfeed.sources import Search
    monkeypatch.setenv("FOORILLA_API_KEY", "k")
    calls = []

    def fake_get_json(url, headers=None, **kw):
        calls.append((url, headers))
        if "/core/topic/" in url:
            return {"results": [{"id": 7, "name": "InfoSec & Privacy"}, {"id": 9, "name": "Web Development"}], "pages": 1}
        return {"pages": 1, "results": [{
            "id": 42, "title": "Junior SOC Analyst", "company": {"name": "Acme"}, "location": "Madrid, Spain",
            "published": "2026-10-07T10:00:00Z", "expired": False, "apply_url": "https://acme.example/jobs/42",
            "has_remote": True, "experience_level": "Entry", "language": "English",
            "countries": [{"name": "Spain"}], "regions": [{"name": "Europe"}], "topics": [{"name": "InfoSec & Privacy"}],
            "salary_min_eur": 30000, "salary_max_eur": 36000, "tags": []}]}
    monkeypatch.setattr(builtin.http, "get_json", fake_get_json)
    builtin._foorilla_topics.clear()
    out = builtin.foorilla(Search("c", ["soc analyst"], ["foorilla"]), {"topics": ["infosec"]})
    assert calls[0][1] == {"Api-Key": "k"} and "topic=7" in calls[-1][0] and "has_remote=true" in calls[-1][0]
    p = out[0]
    assert (p.title, p.company, p.remote) == ("Junior SOC Analyst", "Acme", True)
    assert "Countries: Spain" in p.description and "€30,000 - €36,000" in p.description


def test_foorilla_needs_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FOORILLA_API_KEY", raising=False)
    cfg = parse('[[search]]\nname="c"\nkeywords=["soc analyst"]\nsources=["foorilla"]\n')
    res = run(cfg, Store(tmp_path / "f.db"), Profile.parse("ES"), notify=False, force=True)
    assert "FOORILLA_API_KEY" in res.needs_setup["foorilla"] and not res.errors


def test_search_list_runs_each_term_and_merges(monkeypatch):
    from jobfeed import ats
    calls = []

    def fake(c):
        calls.append(c.search)
        return {"security": [ats.Opening("1", "SOC Analyst", "Madrid", "u1"), ats.Opening("2", "Security Engineer", "", "u2")],
                "help desk": [ats.Opening("2", "Security Engineer", "", "u2"), ats.Opening("3", "Help Desk Technician", "", "u3")]}[c.search]
    monkeypatch.setitem(ats.ADAPTERS, "workday", (fake, None))
    c = ats.Company("Bank", "workday", "bank.wd3.myworkdayjobs.com/bank/Careers", search=["security", "help desk"])
    assert [o.id for o in ats.openings(c)] == ["1", "2", "3"] and calls == ["security", "help desk"]


def test_config_accepts_search_list():
    from jobfeed.config import parse
    cfg = parse('[[company]]\nname = "Bank"\nats = "workday"\nslug = "bank.wd3.myworkdayjobs.com/bank/Careers"\nsearch = ["security", "help desk"]\n')
    assert cfg.companies[0].search == ["security", "help desk"]


def test_teamtailor_rss_locations(monkeypatch):
    """Custom-domain boards (jobs.example.com) work; empty location tags and remoteStatus "none" add no noise."""
    rss = """<?xml version="1.0"?><rss xmlns:tt="https://teamtailor.com/locations"><channel>
    <item><title>SOC Analyst</title><link>https://jobs.example.com/jobs/1-soc</link><guid>g1</guid>
      <pubDate>Mon, 12 Oct 2026 11:48:37 +0100</pubDate><remoteStatus>none</remoteStatus>
      <tt:locations><tt:location><tt:name> </tt:name><tt:city>Madrid</tt:city><tt:country>Spain</tt:country></tt:location></tt:locations>
    </item></channel></rss>"""
    seen = {}

    def fake_get_text(url, **kw):
        seen["url"] = url
        return rss

    monkeypatch.setattr(ats.http, "get_text", fake_get_text)
    (o,) = ats._teamtailor_list(ats.Company(name="Acme", ats="teamtailor", slug="jobs.example.com"))
    assert seen["url"] == "https://jobs.example.com/jobs.rss"
    assert o.location == "Madrid, Spain"
    assert o.remote is None
