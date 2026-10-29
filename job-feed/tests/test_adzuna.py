import pytest

from jobfeed import http
from jobfeed.sources import SOURCES, Search
from jobfeed.sources import builtin

RESULT = {"results": [
    {"id": 4711, "title": "SOC Analyst L1", "redirect_url": "https://www.adzuna.in/details/4711",
     "company": {"display_name": "Acme Security"}, "location": {"display_name": "Pune, Maharashtra", "area": ["India", "Maharashtra", "Pune"]},
     "description": "Monitor SIEM alerts…", "created": "2026-10-28T09:00:00Z", "category": {"label": "IT Jobs"},
     "salary_min": 600000, "salary_max": 900000, "salary_is_predicted": "0"},
    {"id": 4712, "title": "Security Engineer", "redirect_url": "https://www.adzuna.in/details/4712",
     "company": {"display_name": "Beta"}, "location": {"display_name": "India", "area": ["India"]},
     "description": "…", "created": "2026-10-27T09:00:00Z", "salary_min": 1000000, "salary_is_predicted": "1"},
]}


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "app-id-123")
    monkeypatch.setenv("ADZUNA_APP_KEY", "secret-key-456")


def test_registered_with_countries_and_key():
    s = SOURCES["adzuna"]
    assert "IN" in s.countries and s.env == ("ADZUNA_APP_ID", "ADZUNA_APP_KEY") and s.signup.startswith("https://")


def test_one_call_per_country_and_keyword(monkeypatch, keys):
    urls = []
    monkeypatch.setattr(http, "get_json", lambda url, **kw: urls.append(url) or RESULT)
    search = Search("soc", ["soc analyst", "security analyst", "siem", "fourth"], ["adzuna"], max_age_days=14)
    out = builtin.adzuna(search, {"countries": ["in", "XX", "GB"], "max_keywords": 2})
    assert len(urls) == 4  # IN and GB (XX skipped) x 2 keywords
    assert urls[0].startswith("https://api.adzuna.com/v1/api/jobs/in/search/1?")
    assert "what=soc+analyst" in urls[0] and "max_days_old=14" in urls[0] and "app_key=secret-key-456" in urls[0]
    first = out[0]
    assert (first.source, first.title, first.company) == ("adzuna", "SOC Analyst L1", "Acme Security")
    assert first.location == "Pune, Maharashtra, India"  # country added so remote-check can place it
    assert first.salary_text == "600,000–900,000 a year" and first.tags == ["IT Jobs"]
    assert out[1].location == "India" and out[1].salary_text == ""  # Adzuna's own estimate is not a salary


def test_no_countries_means_no_calls(monkeypatch, keys):
    monkeypatch.setattr(http, "get_json", lambda url, **kw: pytest.fail("no call expected"))
    assert builtin.adzuna(Search("s", ["x"], ["adzuna"]), {}) == []


def test_a_failing_country_does_not_stop_the_others_and_never_shows_the_key(monkeypatch, keys):
    def fake(url, **kw):
        if "/jobs/za/" in url:
            raise http.FetchError("HTTP 400 from api.adzuna.com", 400)
        return RESULT
    monkeypatch.setattr(http, "get_json", fake)
    opts = {"countries": ["ZA", "IN"]}
    assert len(builtin.adzuna(Search("s", ["x"], ["adzuna"]), opts)) == 2
    assert opts["_warnings"] == ["ZA: HTTP 400 from api.adzuna.com"]
    monkeypatch.setattr(http, "get_json", lambda url, **kw: (_ for _ in ()).throw(http.FetchError("HTTP 401 from api.adzuna.com", 401)))
    with pytest.raises(http.FetchError) as e:
        builtin.adzuna(Search("s", ["x"], ["adzuna"]), {"countries": ["IN"]})
    assert "secret-key-456" not in str(e.value) and "IN: HTTP 401" in str(e.value)
