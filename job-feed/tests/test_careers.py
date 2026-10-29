import json

import pytest

from jobfeed import careers as cr, crawl, http
from jobfeed.browser_helper import BrowserHelper
from jobfeed.config import ConfigError, parse
from jobfeed.pipeline import run
from jobfeed.store import Store
from remote_eligibility import Profile

LD = lambda obj: f'<script type="application/ld+json">{json.dumps(obj)}</script>'  # noqa: E731
JP = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Junior SOC Analyst",
      "hiringOrganization": {"@type": "Organization", "name": "Acme Security"},
      "jobLocationType": "TELECOMMUTE",
      "applicantLocationRequirements": {"@type": "Country", "name": "Spain"},
      "datePosted": "2026-10-01", "description": "<p>Monitor alerts in our SIEM.</p>",
      "baseSalary": {"@type": "MonetaryAmount", "currency": "EUR", "value": {"minValue": 28000, "maxValue": 34000, "unitText": "YEAR"}}}
LONG = "<p>" + "We are hiring a junior analyst to monitor alerts and investigate incidents. " * 10 + "</p>"


@pytest.fixture
def web(monkeypatch):
    pages: dict = {}

    def fetch_page(url, **kw):
        v = pages.get(url)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise http.FetchError("HTTP 404", 404)
        return v
    monkeypatch.setattr(crawl, "fetch_page", fetch_page)
    monkeypatch.setattr(crawl, "sitemap_urls", lambda *a, **k: [])
    return pages


def test_ats_fingerprint(web):
    web["https://acme.example/careers"] = '<iframe src="https://job-boards.greenhouse.io/embed/job_board?for=acmesec"></iframe>'
    r = cr.recon("Acme", "https://acme.example/careers", ["soc analyst"])
    assert (r.state, r.ats, r.slug) == ("ats", "greenhouse", "acmesec")


def test_jsonld_on_careers_page(web):
    web["https://acme.example/careers"] = "<html>" + LD({"@graph": [JP, {"@type": "Organization"}]}) + "</html>"
    r = cr.recon("Acme", "https://acme.example/careers", ["soc analyst"])
    p = r.postings[0]
    assert r.state == "jsonld" and p.title == "Junior SOC Analyst" and p.company == "Acme Security"
    assert "fully remote" in p.description and "Applicants must be based in Spain" in p.description and "28000" in p.description


def test_follows_job_links_markup_and_text(web):
    web["https://acme.example/careers"] = ('<a href="/careers/junior-soc-analyst">Junior SOC Analyst</a>'
                                           '<a href="/careers/it-support">IT Support</a><a href="/about">About</a>')
    web["https://acme.example/careers/junior-soc-analyst"] = LD(JP)
    web["https://acme.example/careers/it-support"] = "<html><h1>IT Support Technician</h1>" + LONG + "<h2>Requirements</h2><p>Windows</p><a>Apply now</a></html>"
    r = cr.recon("Acme", "https://acme.example/careers", ["soc analyst"])
    titles = [p.title for p in r.postings]
    assert titles[0] == "Junior SOC Analyst" and "IT Support Technician" in titles and r.state == "jsonld"


def test_js_shell_needs_browser(web):
    web["https://spa.example/careers"] = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    r = cr.recon("Spa", "https://spa.example/careers", ["soc analyst"])
    assert r.state == "needs_browser"


def test_challenge_quarantines_site(web, tmp_path):
    store = Store(tmp_path / "f.db")
    web["https://cf.example/careers"] = http.Blocked("cf.example: bot challenge", 403, challenge=True)
    assert cr.recon("CF", "https://cf.example/careers", [], store=store).state == "blocked"
    assert store.blocked("cf.example")
    web["https://cf.example/careers"] = "<html>would be fine now</html>"
    assert cr.recon("CF", "https://cf.example/careers", [], store=store).state == "blocked"  # stays quarantined


def test_config():
    cfg = parse('[[careers]]\nname="Acme"\nurl="https://acme.example/careers"\n[browser_helper]\nurl="http://100.1.2.3:8790"\ntoken_env="T"\n')
    assert cfg.careers[0]["url"].endswith("/careers") and cfg.browser_helper["url"]
    with pytest.raises(ConfigError):
        parse('[[careers]]\nname="x"\nurl="file:///etc/passwd"\n')
    with pytest.raises(ConfigError):
        parse('[browser_helper]\nurl="http://x"\ntoken="secret"\n')


def test_pipeline_with_careers_and_helper(web, tmp_path, monkeypatch):
    web["https://acme.example/careers"] = LD(JP)
    web["https://spa.example/jobs"] = '<div id="app"></div>'
    rendered = {"https://spa.example/jobs": '<a href="/jobs/blue-team">Blue Team Analyst</a>',
                "https://spa.example/jobs/blue-team": "<h1>Blue Team Analyst</h1>" + LONG + "<p>Remote within the EU.</p><h2>Responsibilities</h2><button>Apply</button>"}
    monkeypatch.setattr(BrowserHelper, "available", lambda self: True)
    monkeypatch.setattr(BrowserHelper, "render", lambda self, url: rendered[url])
    cfg = parse('''
[[search]]
name = "Cybersecurity"
keywords = ["soc analyst", "blue team"]
sources = ["careers"]
[[careers]]
name = "Acme"
url = "https://acme.example/careers"
[[careers]]
name = "Spa"
url = "https://spa.example/jobs"
[browser_helper]
url = "http://helper.example:8790"
''')
    store = Store(tmp_path / "f.db")
    res = run(cfg, store, Profile.parse("ES,RO", "RO"), notify=False, force=True)
    rows = {r["title"]: r for r in res.new}
    assert rows["Junior SOC Analyst"]["verdicts"] == {"ES": "yes", "RO": "no"} and rows["Junior SOC Analyst"]["good"]
    assert rows["Junior SOC Analyst"]["salary"]["min"] == 28000
    assert "Blue Team Analyst" in rows
    states = {s["name"]: s["state"] for s in store.careers_states()}
    assert states == {"Acme": "jsonld", "Spa": "browser"}


def test_helper_offline_keeps_needs_browser(web, tmp_path, monkeypatch):
    web["https://spa.example/jobs"] = '<div id="app"></div>'
    monkeypatch.setattr(BrowserHelper, "available", lambda self: False)
    cfg = parse('[[search]]\nname="c"\nkeywords=["soc analyst"]\nsources=["careers"]\n'
                '[[careers]]\nname="Spa"\nurl="https://spa.example/jobs"\n[browser_helper]\nurl="http://helper.example:8790"\n')
    store = Store(tmp_path / "f.db")
    run(cfg, store, Profile.parse("ES"), notify=False, force=True)
    s = store.careers_states()[0]
    assert s["state"] == "needs_browser" and "offline" in s["detail"]


def test_marketing_page_is_not_a_job(web):
    web["https://m.example/careers"] = '<a href="/careers/why-cyber">Why cybersecurity</a>'
    web["https://m.example/careers/why-cyber"] = "<h1>Make cybersecurity your advantage</h1>" + LONG + "<a>Contact us</a>"
    assert cr.recon("M", "https://m.example/careers", ["cybersecurity"]).state == "needs_browser"


def test_homepage_finds_careers_page(web):
    web["https://h.example/"] = '<a href="/about">About</a><a href="/trabaja-con-nosotros/">Trabaja con nosotros</a>'
    web["https://h.example/trabaja-con-nosotros/"] = LD(JP)
    r = cr.recon("H", "https://h.example/", ["soc analyst"])
    assert r.state == "jsonld" and "careers page found at https://h.example/trabaja-con-nosotros/" in r.detail
