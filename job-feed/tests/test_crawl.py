import gzip

import pytest

from jobfeed import crawl, http


@pytest.fixture(autouse=True)
def fresh_policies():
    crawl._policies.clear()
    yield
    crawl._policies.clear()


def fake_web(monkeypatch, pages: dict):
    """pages: url → bytes | Exception"""
    calls = []

    def get(url, **kw):
        calls.append((url, kw.get("delay")))
        v = pages.get(url)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise http.FetchError("HTTP 404", 404)
        return v, "text/html"
    monkeypatch.setattr(http, "get", get)
    return calls


def test_robots_rules_and_crawl_delay(monkeypatch):
    calls = fake_web(monkeypatch, {
        "https://acme.example/robots.txt": b"User-agent: *\nDisallow: /private/\nCrawl-delay: 7\n\nUser-agent: job-feed\nDisallow: /nojobfeed/\nCrawl-delay: 7\n",
        "https://acme.example/careers": b"<a href='/jobs/1'>x</a>",
    })
    assert crawl.fetch_page("https://acme.example/careers").startswith("<a")
    assert calls[-1] == ("https://acme.example/careers", 7.0)
    with pytest.raises(crawl.Disallowed):
        crawl.fetch_page("https://acme.example/nojobfeed/x")


def test_robots_4xx_means_no_rules_5xx_means_disallow(monkeypatch):
    fake_web(monkeypatch, {"https://a.example/robots.txt": http.Blocked("403", 403, challenge=False),
                           "https://a.example/jobs": b"ok",
                           "https://b.example/robots.txt": http.FetchError("HTTP 503", 503)})
    assert crawl.fetch_page("https://a.example/jobs") == "ok"
    with pytest.raises(crawl.Disallowed):
        crawl.fetch_page("https://b.example/jobs")


def test_challenge_on_robots_stops_the_site(monkeypatch):
    fake_web(monkeypatch, {"https://c.example/robots.txt": http.Blocked("challenge", 403, challenge=True)})
    with pytest.raises(http.Blocked):
        crawl.fetch_page("https://c.example/jobs")


def test_too_slow_site_is_skipped(monkeypatch):
    fake_web(monkeypatch, {"https://slow.example/robots.txt": b"User-agent: *\nCrawl-delay: 120\n"})
    with pytest.raises(crawl.Disallowed, match="120"):
        crawl.fetch_page("https://slow.example/jobs")


def test_sitemaps_capped_and_filtered(monkeypatch):
    idx = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://d.example/sitemap-jobs.xml</loc></sitemap>
      <sitemap><loc>https://evil.example/sitemap.xml</loc></sitemap></sitemapindex>"""
    jobs = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://d.example/jobs/soc-analyst</loc><lastmod>2026-10-01</lastmod></url>
      <url><loc>https://d.example/blog/post</loc></url>
      <url><loc>https://d.example/careers/old-role</loc><lastmod>2020-01-01</lastmod></url></urlset>"""
    fake_web(monkeypatch, {"https://d.example/robots.txt": b"Sitemap: https://d.example/sitemap.xml\n",
                           "https://d.example/sitemap.xml": idx, "https://d.example/sitemap-jobs.xml": jobs})
    urls = [u for u, _ in crawl.sitemap_urls("https://d.example/", max_age_days=60)]
    assert urls == ["https://d.example/jobs/soc-analyst"]


def test_sitemap_bombs_refused(monkeypatch):
    bomb = gzip.compress(b"<urlset>" + b" " * (crawl.SITEMAP_MAX_BYTES + 10) + b"</urlset>")
    entity = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>'
    fake_web(monkeypatch, {"https://e.example/robots.txt": b"Sitemap: https://e.example/s.xml.gz\nSitemap: https://e.example/s2.xml\n",
                           "https://e.example/s.xml.gz": bomb, "https://e.example/s2.xml": entity})
    assert crawl.sitemap_urls("https://e.example/") == []  # both skipped, no crash, no expansion


def test_links_on_page():
    html = '<a href="/careers/soc-analyst-1">a</a><a href="https://other.example/jobs/2">b</a><a href="/team">c</a><a href="javascript:x">d</a>'
    assert crawl.links_on("https://f.example/careers", html) == ["https://f.example/careers/soc-analyst-1"]


def test_challenge_detection_in_http(monkeypatch):
    import io
    import urllib.error
    from email.message import Message

    def opener(req, timeout=0):
        h = Message()
        h["cf-mitigated"] = "challenge"
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", h, io.BytesIO(b"<title>Just a moment...</title>"))
    monkeypatch.setattr(http, "check_public", lambda host: None)
    monkeypatch.setattr(http._opener, "open", opener)
    with pytest.raises(http.Blocked) as e:
        http.get("https://g.example/jobs", delay=0)
    assert e.value.challenge and e.value.status == 403
