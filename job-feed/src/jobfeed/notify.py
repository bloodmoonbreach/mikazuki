"""Notifications for new matches: Telegram, ntfy, webhook. Plain text only (job titles are untrusted)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

ICON = {"yes": "✅", "likely": "🟢", "unclear": "❔", "unlikely": "🟠", "no": "❌"}


def _line(r: dict) -> str:
    v = " ".join(f"{c} {ICON.get(x, x)}" for c, x in (r.get("verdicts") or {}).items())
    bits = [v]
    if r.get("fit") is not None:
        bits.append(f"fit {r['fit']}%")
    if r.get("local_match"):
        bits.append("📍 local")
    s = r.get("salary")
    if s:
        bits.append(f"{s['min']:,.0f}–{s['max']:,.0f} {s['currency']}/{s['period']}")
    if r.get("traps"):
        bits.append("⚠ AI trap")
    head = f"{r['title']}" + (f" @ {r['company']}" if r.get("company") else "")
    return f"{head}\n{' · '.join(bits)}\n{r.get('location') or ''}\nvia {r['source_name']}: {r['url']}"


def message(rows: list[dict], extra: int = 0) -> str:
    out = [f"job-feed: {len(rows) + extra} new match(es)"] + [_line(r) for r in rows]
    if extra:
        out.append(f"…and {extra} more in mikazuki")
    return "\n\n".join(out)[:3900]  # Telegram's limit is 4096


def _post(url: str, body: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "job-feed", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read(10_000)


def send(cfg, rows: list[dict], extra: int = 0) -> tuple[bool, list[str]]:
    text = message(rows, extra)
    sent, errors = False, []
    if cfg.telegram_token_env and cfg.telegram_chat_id:
        token = os.environ.get(cfg.telegram_token_env)
        if not token:
            errors.append(f"telegram: env var {cfg.telegram_token_env} is not set")
        else:
            try:
                _post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json.dumps({"chat_id": cfg.telegram_chat_id, "text": text, "disable_web_page_preview": True}).encode(),
                      {"Content-Type": "application/json"})
                sent = True
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                errors.append(f"telegram: {str(e).replace(token, '***')}")
    if cfg.ntfy_url:
        try:
            _post(cfg.ntfy_url, text.encode(), {"Title": "job-feed: new matches", "Tags": "briefcase"})
            sent = True
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            errors.append(f"ntfy: {e}")
    if cfg.webhook_url:
        try:
            payload = [{k: r.get(k) for k in ("title", "company", "location", "url", "source_name", "verdicts", "best",
                                              "mode", "fit", "salary", "local_match", "traps", "search")} for r in rows]
            _post(cfg.webhook_url, json.dumps({"new": payload, "more": extra}, ensure_ascii=False).encode(),
                  {"Content-Type": "application/json"})
            sent = True
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            errors.append(f"webhook: {e}")
    return sent, errors
