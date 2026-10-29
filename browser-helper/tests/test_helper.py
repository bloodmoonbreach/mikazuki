import importlib

import pytest
from fastapi.testclient import TestClient

from browserhelper import guard


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://192.168.1.10:8780/", "http://100.64.0.7/",
                                 "http://169.254.169.254/", "file:///etc/passwd", "http://u:p@example.com/",
                                 "https://jobs.cybersecurityjobs.com/jobs/", "https://www.linkedin.com/jobs/"])
def test_refused(url):
    with pytest.raises(guard.Refused):
        guard.check_url(url)


def test_extra_deny(monkeypatch):
    monkeypatch.setenv("HELPER_DENY", "example.org")
    assert guard.denied("jobs.example.org") and not guard.denied("example.com")


def test_challenge_detection():
    assert guard.looks_challenged("<title>Just a moment...</title>", 200)
    assert guard.looks_challenged("", 429) and not guard.looks_challenged("<h1>Jobs</h1>", 200)
    assert not guard.looks_challenged('<form><div class="g-recaptcha"></div><script src="https://www.google.com/recaptcha/api.js"></script></form>', 200)


@pytest.fixture
def client(monkeypatch):
    import sys
    import types
    fake_c4a = types.ModuleType("crawl4ai")

    class CrawlerRunConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    fake_c4a.CrawlerRunConfig = CrawlerRunConfig
    fake_c4a.CacheMode = types.SimpleNamespace(BYPASS="bypass")
    monkeypatch.setitem(sys.modules, "crawl4ai", fake_c4a)  # the real one lives in the Docker image
    monkeypatch.setenv("HELPER_TOKEN", "t" * 32)
    import browserhelper.app as app
    app = importlib.reload(app)

    class Result:
        success, status_code, html, redirected_url = True, 200, "<h1>Jobs</h1>", None
        markdown = type("M", (), {"raw_markdown": "# Jobs"})()

    class FakeCrawler:
        async def arun(self, url, config=None):
            assert config.check_robots_txt is True
            return Result()

        async def close(self):
            pass

    async def fake():
        return FakeCrawler()
    monkeypatch.setattr(app, "_crawler", fake)
    with TestClient(app.app) as c:
        yield c


def test_auth_and_render(client):
    assert client.post("/render", json={"url": "https://example.com/"}).status_code == 401
    assert client.post("/render", json={"url": "https://example.com/"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.post("/render", json={"url": "https://example.com/"}, headers={"Authorization": "Bearer " + "t" * 32})
    assert r.status_code == 200 and r.json()["markdown"] == "# Jobs"
    r = client.post("/render", json={"url": "http://192.168.0.1/"}, headers={"Authorization": "Bearer " + "t" * 32})
    assert r.status_code == 400
