# browser-helper

A small, hardened service that renders JavaScript-only web pages for [job-feed](https://github.com/bloodmoonbreach/mikazuki), running on a machine with RAM to spare (e.g. your desktop) so a small NAS doesn't need a browser. It wraps [Crawl4AI](https://github.com/unclecode/crawl4ai) (headless Chromium via Playwright).

job-feed sends it only pages that answered normally but are empty without JavaScript. All extraction stays in job-feed; this service only renders.

## API

- `GET /healthz` → `{"ok": true}`
- `POST /render` `{"url": "https://…"}` with `Authorization: Bearer <HELPER_TOKEN>` → `{"html", "markdown", "final_url", "status"}`, or `{"blocked": true, "reason"}` when the site refuses automated access.

## Rules it enforces

- **Token required** (`HELPER_TOKEN`, at least 24 characters).
- **Public URLs only.** Checked for the URL and for **every request the browser makes**, so a page can't make it reach your LAN, NAS, Tailscale network or cloud metadata. Only http(s), no credentials in URLs.
- **robots.txt obeyed** for the `job-feed` user agent; honest user agent.
- **Bot protection is never bypassed.** Challenge pages, CAPTCHAs, 403 and 429 come back as `blocked`. Sites known to challenge bots are refused outright, even though a real browser could sometimes pass the challenge (`DENY` in `guard.py`, plus `HELPER_DENY`). No stealth plugins, no CAPTCHA solving, no proxies.
- **One page at a time**; images, fonts and media are not loaded; 30 s page timeout; the browser restarts every 100 pages (`HELPER_RESTART_EVERY`) to contain long-run memory growth.

## Run

```bash
umask 077; printf 'HELPER_TOKEN=%s\n' "$(head -c 33 /dev/urandom | base64 | tr -d '/+=')" > .env
# edit compose.yaml: bind the port to an address only your job-feed host can reach (e.g. a Tailscale IP)
docker compose up -d --build
```

Docker-published ports bypass host firewalls like ufw, so **bind the port to a private address** (a VPN or Tailscale IP), never `0.0.0.0` on a public network.

In job-feed's `feed.toml`:

```toml
[browser_helper]
url = "http://<helper-address>:8790"
token_env = "BROWSER_HELPER_TOKEN"   # set this variable to the same token in job-feed's environment
```

## License

MIT. Crawl4AI is Apache-2.0; Playwright/Chromium have their own licenses.
