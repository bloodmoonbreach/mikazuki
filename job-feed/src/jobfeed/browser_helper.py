"""Client for an optional browser helper: a small service on another machine (e.g. a desktop running Crawl4AI)
that renders JavaScript-only pages and returns their HTML.

The helper only renders; all extraction (JSON-LD, page text, link following) stays here, so its output is treated
exactly like any other untrusted page. The helper URL is a trusted, configured address (often a private/Tailscale IP),
so this client talks only to that one address, with a bearer token from an environment variable.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import http
from .model import Posting


@dataclass
class BrowserHelper:
    url: str
    token: str | None = None
    timeout: float = 90.0
    max_pages: int = 8

    @classmethod
    def from_config(cls, cfg: dict) -> "BrowserHelper":
        token = os.environ.get(cfg.get("token_env") or "BROWSER_HELPER_TOKEN")
        return cls(str(cfg["url"]).rstrip("/"), token, float(cfg.get("timeout", 90)), int(cfg.get("max_pages", 8)))

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(f"{self.url}{path}", data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "User-Agent": http.USER_AGENT,
            **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read(8_000_000))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read(2000)).get("detail", "")
            except ValueError:
                detail = ""
            raise http.FetchError(f"browser helper: HTTP {e.code} {detail}".strip(), e.code) from e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise http.FetchError(f"browser helper unreachable ({e})") from e

    def available(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.url}/healthz", headers={"User-Agent": http.USER_AGENT})
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def render(self, url: str) -> str:
        """Rendered HTML of one page. The helper enforces robots.txt, public-only URLs and timeouts itself."""
        d = self._post("/render", {"url": url})
        if d.get("blocked"):
            raise http.Blocked(f"browser helper: {d.get('reason', 'blocked')}", d.get("status"), challenge=True)
        return str(d.get("html") or "")

    def render_jobs(self, name: str, url: str, keywords: list[str]) -> list[Posting]:
        """Render a JavaScript careers page and the job pages it links to; extract like any other page."""
        from . import careers as cr

        if not self.available():
            raise http.FetchError("browser helper is offline")
        html = self.render(url)
        postings = [cr.posting_from_jsonld(jp, url, name) for jp in cr.job_postings_jsonld(html) if jp.get("title")]
        if postings:
            return postings
        board = cr.external_board(html, url)  # rendered page links to a hosted job platform: read that instead
        if board:
            r = cr.recon(name, board, keywords, max_pages=self.max_pages, _depth=1)
            if r.postings:
                return r.postings
            if r.state == "needs_browser":
                url, html = board, self.render(board)
        candidates = cr.job_links(url, html, keywords)[: self.max_pages]
        seen = set(candidates)
        for job_url in candidates:
            try:
                page = self.render(job_url)
            except http.Blocked:
                raise
            except http.FetchError:
                continue  # one broken page doesn't stop the rest
            found = cr.job_postings_jsonld(page)
            more = [u for u in cr.job_links(job_url, page, keywords) if u not in seen]
            if not found and len(more) >= 3 and len(candidates) < self.max_pages * 2:  # listing page
                candidates += more[: self.max_pages]
                seen.update(more)
                continue
            if found:
                postings += [cr.posting_from_jsonld(jp, job_url, name) for jp in found if jp.get("title")]
            else:
                title, text = cr.page_text(page, job_url)
                if title and len(text) > 300 and cr.looks_like_job_page(page, text):
                    postings.append(Posting("careers", f"{name} careers", job_url, title, name, "", text, None, [], "",
                                            None, job_url))
        return postings
