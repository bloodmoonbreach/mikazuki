import pytest

from jobfeed import http
from jobfeed.sources import SOURCES, Search
from jobfeed.sources import builtin

RESULT = {"SearchResult": {"SearchResultCount": 1, "SearchResultItems": [{"MatchedObjectId": "801",
    "MatchedObjectDescriptor": {"PositionID": "CISA-26-801", "PositionTitle": "IT Specialist (INFOSEC)",
        "PositionURI": "https://www.usajobs.gov/job/801", "PositionLocationDisplay": "Arlington, Virginia",
        "OrganizationName": "Cybersecurity and Infrastructure Security Agency",
        "QualificationSummary": "One year of specialized experience…",
        "PositionRemuneration": [{"MinimumRange": "86962", "MaximumRange": "113047", "RateIntervalCode": "PA", "Description": "Per Year"}],
        "PublicationStartDate": "2026-10-27T00:00:00.0000", "UserArea": {"Details": {"JobSummary": "Protect federal networks."}}}}]}}


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "usa-secret")
    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")


def test_registered_for_the_us():
    s = SOURCES["usajobs"]
    assert s.countries == frozenset({"US"}) and s.env == ("USAJOBS_API_KEY", "USAJOBS_EMAIL")


def test_request_and_mapping(monkeypatch, keys):
    calls = []
    monkeypatch.setattr(http, "get_json", lambda url, **kw: calls.append((url, kw)) or RESULT)
    out = builtin.usajobs(Search("s", ["information security", "cyber"], ["usajobs"], max_age_days=90), {"max_keywords": 1})
    url, kw = calls[0]
    assert url.startswith("https://data.usajobs.gov/api/search?") and "Keyword=information+security" in url
    assert "DatePosted=60" in url and "RemoteIndicator=True" in url  # 60 is the API's maximum; remote_only by default
    assert kw["headers"] == {"User-Agent": "me@example.com", "Authorization-Key": "usa-secret"}
    p = out[0]
    assert (p.source, p.title, p.source_id) == ("usajobs", "IT Specialist (INFOSEC)", "CISA-26-801")
    assert p.location == "Arlington, Virginia, United States" and p.remote is True
    assert p.salary_text == "86962–113047 USD Per Year" and p.description.startswith("Protect federal networks.")


def test_not_remote_only_sends_no_remote_filter(monkeypatch, keys):
    calls = []
    monkeypatch.setattr(http, "get_json", lambda url, **kw: calls.append(url) or {"SearchResult": {"SearchResultItems": []}})
    builtin.usajobs(Search("s", ["x"], ["usajobs"], remote_only=False), {})
    assert "RemoteIndicator" not in calls[0]
