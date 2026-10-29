# mikazuki

A small self-hosted web interface for two command-line tools:

- **[cvtracks](https://github.com/bloodmoonbreach/cvtracks)**: one master file of facts → every CV you need (tracks × languages, PDF/HTML/Markdown/JSON Resume), with lint and honest per-job tailoring.
- **[remote-check](https://github.com/bloodmoonbreach/remote-check)**: where can this "remote" job actually be done from? (EN/ES/RO, prompt-injection defences.)

- **[job-feed](https://github.com/bloodmoonbreach/mikazuki)**: collects jobs from public job APIs/feeds on a schedule, checks and scores them, and notifies you.

Pages: **Dashboard** · **Feed** (new matches from your saved searches; save / applied / dismiss; one click to check and tailor) · (tracks, lint status, latest files) · **Check a job** (paste a post or URL → per-country verdicts, salary, required languages, AI-trap warnings, and a CV tailored to it) · **CVs** (preview/download) · **Edit** (master.yaml, tracks, profile; validated before saving, every version kept) · **Lint** · **Settings**.

## Apply

**Apply** on a Feed job prepares a manual application; nothing is ever submitted for you.

- Reads the form's questions: Greenhouse (public API) and Lever (`/apply` page). For anything else, paste the questions.
- Tailors a CV to the job (cvtracks) and attaches it to the tracker entry.
- Fills factual answers by code from `master.yaml` / `profile.toml` (contact, links, work rights, country, nationality, languages, salary, availability). Missing facts become `[NEEDS INPUT]`, never guesses.
- Leaves consent boxes, demographics and AI pledges to you.
- Drafts open questions with a **local** Ollama model (default `gemma4:12b`), using only the CV as facts. Every draft is editable, with Copy buttons.
- If the form forbids AI-written answers, nothing is drafted.

Optional settings: the `[apply]` section of `profile.toml`.

## Run with Docker

```bash
mkdir mikazuki && cd mikazuki
mkdir src data
git clone https://github.com/bloodmoonbreach/remote-check src/remote-eligibility
git clone https://github.com/bloodmoonbreach/cvtracks src/cvtracks
git clone https://github.com/bloodmoonbreach/mikazuki src/job-feed
git clone https://github.com/bloodmoonbreach/mikazuki src/mikazuki
cp src/mikazuki/compose.example.yaml compose.yaml
echo "MIKAZUKI_PASSWORD=$(openssl rand -base64 18)" > .env && chmod 600 .env
sudo chown 1000:1000 data        # or change `user:` in compose.yaml to the owner of ./data
docker compose up -d --build
```

Open `http://<host>:8780`, sign in as `admin` with the password from `.env`, and create the example workspace (or put your own `master.yaml` and `tracks/` in `data/`).

To use the optional LLM pass for unclear job posts, add an `[llm]` section to **Settings** (any OpenAI-compatible endpoint, e.g. Ollama at `http://<ollama-host>:11434/v1`). API keys go in environment variables (`api_key_env`), never in the file.

## Without Docker

```bash
pip install ./remote-eligibility "./cvtracks[pdf]" ./mikazuki
MIKAZUKI_DATA=~/cv MIKAZUKI_PASSWORD=change-me mikazuki     # http://127.0.0.1:8780
```

## Security

It holds personal data, so:

- **Login page** when `MIKAZUKI_PASSWORD` is set: signed session cookie (HttpOnly, SameSite=Lax, 12 h or 30 days with "keep me signed in"), lockout after 5 wrong passwords, same-site redirects only after sign-in. Changing the password signs every session out. The signing secret lives in `data/.secret` (mode 600) or `MIKAZUKI_SECRET`. Use it on your LAN/VPN, or behind HTTPS.
- **CSRF tokens** on every form; strict Content-Security-Policy (no inline scripts), `X-Frame-Options`, `nosniff`.
- **No path traversal:** the editor only touches `master.yaml`, `profile.toml` and `tracks/*.yaml`; downloads only come from `out/`.
- **No SSRF:** "check a job by URL" refuses anything that resolves to a private, loopback, link-local, CGNAT/Tailscale or metadata address, checked again on every redirect.
- **Apply:** form questions are fetched only from Greenhouse and Lever hosts (through the same SSRF-safe fetch) and shown as escaped text; the local model gets the CV and the question, has no tools, and its drafts are never sent anywhere.
- **Untrusted job posts:** hidden text and prompt injections are removed before analysis (remote-check); job text is always HTML-escaped; a job post can never add anything to your CV.
- **Container:** non-root, read-only filesystem, no capabilities, `no-new-privileges`, memory-capped.

Not meant to be exposed to the internet as-is.

## License

MIT
