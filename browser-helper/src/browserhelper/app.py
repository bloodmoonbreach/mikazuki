"""The service: POST /render {url} → {html, markdown, final_url, status} (or {blocked: true}).

Hardening: bearer token required; only public URLs, also for every sub-request the browser makes (so a page
can't make it reach the LAN/tailnet); robots.txt obeyed for the job-feed user agent; one page at a time;
images/fonts/media blocked; browser restarted every N pages (Crawl4AI has known long-run memory growth);
bot challenges are reported, never bypassed.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from . import __version__, guard

TOKEN = os.environ.get("HELPER_TOKEN", "")
RESTART_EVERY = int(os.environ.get("HELPER_RESTART_EVERY", "100"))
PAGE_TIMEOUT_MS = int(os.environ.get("HELPER_PAGE_TIMEOUT_MS", "30000"))
MAX_HTML = 6_000_000

_state = {"crawler": None, "pages": 0}
_lock = asyncio.Lock()


async def _route_guard(route, request):
    host = urlparse(request.url).hostname or ""
    if request.resource_type in ("image", "media", "font"):
        return await route.abort()
    if request.url.startswith(("data:", "blob:")):
        return await route.continue_()
    if not request.url.startswith(("http://", "https://")) or not guard.host_public(host):
        return await route.abort("blockedbyclient")
    return await route.continue_()


async def _new_crawler():
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, user_agent=guard.UA, verbose=False,
                                                   extra_args=["--disable-dev-shm-usage"]))

    async def on_context(page, context, **kwargs):
        await context.route("**/*", _route_guard)
        return page

    crawler.crawler_strategy.set_hook("on_page_context_created", on_context)
    await crawler.start()
    return crawler


async def _crawler():
    if _state["crawler"] is None or _state["pages"] >= RESTART_EVERY:
        if _state["crawler"] is not None:
            await _state["crawler"].close()
        _state["crawler"], _state["pages"] = await _new_crawler(), 0
    return _state["crawler"]


@asynccontextmanager
async def lifespan(app):
    if len(TOKEN) < 24:
        raise RuntimeError("set HELPER_TOKEN (at least 24 characters)")
    yield
    if _state["crawler"] is not None:
        await _state["crawler"].close()


app = FastAPI(title="browser-helper", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url=None)


def auth(request: Request) -> None:
    sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not sent or not hmac.compare_digest(sent, TOKEN):
        raise HTTPException(401, "missing or wrong token")


class RenderIn(BaseModel):
    url: str


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__}


@app.post("/render", dependencies=[Depends(auth)])
async def render(body: RenderIn):
    try:
        url = guard.check_url(body.url)
    except guard.Refused as e:
        raise HTTPException(400, str(e)) from None
    from crawl4ai import CacheMode, CrawlerRunConfig

    cfg = CrawlerRunConfig(check_robots_txt=True, cache_mode=CacheMode.BYPASS, wait_until="domcontentloaded",
                           page_timeout=PAGE_TIMEOUT_MS, delay_before_return_html=2.0, verbose=False)
    async with _lock:  # one page at a time
        crawler = await _crawler()
        result = await crawler.arun(url, config=cfg)
        _state["pages"] += 1
    status = getattr(result, "status_code", None)
    html = (getattr(result, "html", "") or "")[:MAX_HTML]
    if not result.success:
        msg = str(getattr(result, "error_message", "") or "")
        if "robots" in msg.lower():
            raise HTTPException(403, "robots.txt disallows this URL for job-feed")
        if guard.looks_challenged(html, status):
            return {"blocked": True, "status": status, "reason": "bot challenge / access refused (not bypassed)"}
        raise HTTPException(502, f"render failed: {msg[:200]}")
    if guard.looks_challenged(html, status) and len(html) < 60000:
        return {"blocked": True, "status": status, "reason": "bot challenge / access refused (not bypassed)"}
    md = getattr(result, "markdown", None)
    markdown = getattr(md, "raw_markdown", None) or (md if isinstance(md, str) else "")
    return {"html": html, "markdown": (markdown or "")[:400_000], "final_url": getattr(result, "redirected_url", None) or url,
            "status": status}
