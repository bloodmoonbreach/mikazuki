# mikazuki 三日月

A self-hosted job-search desk. It collects job offers from company job systems and job boards, checks
where a "remote" job can really be done from, keeps one fact file of your experience and builds every CV
from it, tracks your applications, and prepares each application for you to send yourself.

It runs on your own machine (a NAS, a home server, a laptop). Your CV and your applications never leave it.

> Built for one person's job hunt (junior cybersecurity and IT support) and published as a working example.
> Places work worldwide (252 countries; mikazuki downloads the town list of each country you pick), but job posts
> are read in English, Spanish and Romanian, and the built-in job sources lean European. For anything else, expect
> to adapt it. The [AGENTS.md](AGENTS.md) file helps an AI coding assistant do that adaptation with you.

## What it does

- **Feed.** Pulls offers from company job systems (Greenhouse, Lever, Ashby, Workable, SmartRecruiters,
  Recruitee, Personio, Teamtailor, Breezy, Workday), from public job APIs and RSS feeds, from official APIs
  that need your own free key (Adzuna in 19 countries, USAJOBS, InfoJobs), from careers pages, and from Hipo.ro.
  The setup suggests the sources that cover the countries you pick. Keeps only the roles you search for, and drops course adverts, bootcamps, student
  internships and job-fair sign-ups that are dressed up as vacancies.
- **"Can I do this job from here?"** Every offer goes through [remote-check](https://github.com/bloodmoonbreach/remote-check):
  a per-country verdict (yes / likely / unclear / unlikely / no), salary, required languages, and a warning
  when the post contains text aimed at AI tools.
- **Keyword fit.** How much of the post your CV covers, shown as a waxing moon, and which real skills the post
  asks for that your CV doesn't show (from a curated vocabulary, not every capitalised word on the page).
- **CVs from one fact file.** [cvtracks](https://github.com/bloodmoonbreach/cvtracks) builds every CV (tracks ×
  languages × PDF/HTML/Markdown/JSON Resume) from `master.yaml`, lints it, and tailors it to a job by
  reordering, never by writing new claims.
- **Tracker.** Statuses, timeline, funnel and interview rate per profile; import and export as .xlsx/.csv.
- **Apply.** Reads the application form (Greenhouse and Lever directly; paste the questions for anything
  else), fills the factual answers from your profile, drafts the open questions and a short cover letter with a
  **local** model through Ollama (or an OpenAI-compatible API with your own key, if you prefer), and shows
  everything with Copy buttons. **Nothing is ever submitted for you.**

## What it deliberately doesn't do

- **Apply automatically.** Mass applying is spam; employers answer it with bot protection and AI bans.
- **Invent facts.** Anything your files don't say becomes `[NEEDS INPUT: …]`. Drafts are drafts.
- **Draft when AI is forbidden.** If a form says answers must be your own words, it drafts nothing.
- **Fill in personal data.** Nationality, location and country of residence are always left to you.
- **Get around websites' rules.** It respects robots.txt and crawl delays, identifies itself, never works
  around a bot challenge, and only reads sites whose terms allow it (see [Lawful use](#lawful-use)).

## The parts

| Part | Repository | What it is |
|---|---|---|
| **remote-check** | [bloodmoonbreach/remote-check](https://github.com/bloodmoonbreach/remote-check) | Job-post analyser: where can it be done from; strips hidden text and prompt injections first |
| **cvtracks** | [bloodmoonbreach/cvtracks](https://github.com/bloodmoonbreach/cvtracks) | One fact file → every CV; lint; honest tailoring |
| **job-feed** | [`job-feed/`](job-feed) | Collects, filters and scores offers; stores them with the tracker (SQLite) |
| **mikazuki** | [`mikazuki/`](mikazuki) | The web app (FastAPI): Feed, Tracker, Check a job, CVs, Edit, Lint, Apply |
| **browser-helper** | [`browser-helper/`](browser-helper) | Optional: renders JavaScript-only careers pages with Crawl4AI, on a machine with enough RAM |

## Quick start (Docker)

```bash
git clone https://github.com/bloodmoonbreach/mikazuki && cd mikazuki
cp compose.example.yaml compose.yaml
mkdir data && sudo chown 1000:1000 data
touch .env && chmod 600 .env    # optional settings (Telegram token…) go here
docker compose up -d --build
```

Open `http://<host>:8780` and create your account with the one-time setup code (printed by
`docker compose logs mikazuki`, also saved in `data/setup-code.txt`). The setup then asks, in about ten minutes,
for the jobs you want, the countries you can work from, your CV, where to look (with the free API keys some
job sources need) and an optional AI model. To look around first, press **Create example workspace**: a
fictional person you can replace later, on the **Edit** page or with an AI assistant (next section).

Optional pieces:

- **Local model** for Apply drafts and for unclear job posts: [Ollama](https://ollama.com) on any machine
  the server can reach, then `[llm] base_url = "http://<host>:11434/v1"` in Settings (`profile.toml`).
  A 12B model on a 12 GB GPU is enough; test a few on your own questions first and keep the most honest.
- **Browser helper** for careers pages that need JavaScript: see [browser-helper/README.md](browser-helper/README.md).
- **Notifications** (Telegram, ntfy or a webhook): see the `[notify]` section of `feed.toml`.

## Make it yours, with an AI assistant

Your data lives in five files in `data/`:

| File | What goes in it |
|---|---|
| `master.yaml` | Every fact about you, once: contact, experience, education, projects, certifications, skills, languages |
| `tracks/*.yaml` | One CV per target role: headline, summary, which items to include, order, page limit, keywords |
| `profile.toml` | Countries you can work from, citizenships, languages; the local model; facts for application forms |
| `feed.toml` | Searches (keywords, sources, remote or local), companies, careers pages, notifications |
| `about.md` | Optional: facts the CV doesn't hold (background, goals, in your own words) for application drafts |

[AGENTS.md](AGENTS.md) tells an AI coding assistant (Claude Code, Cursor, Copilot…) how to interview you
and fill these files without inventing anything, how to adapt the skills vocabulary to your field, and
which rules to keep. Open the repository in your assistant and ask it to "set up mikazuki for me".

## Lawful use

This is a personal tool. Use it for your own job search, on your own machine.

- Sources are read the way their owners allow: public job APIs, the public job-board APIs of company job
  systems, RSS feeds, and pages that robots.txt permits, at a slow pace, with an honest user agent.
- A site that answers with a bot challenge or a 403/429 is quarantined, never worked around.
- Some job boards forbid automated access in their terms, and mikazuki doesn't read them: LinkedIn, Indeed,
  Google Jobs, Naukri, Jooble, BestJobs.eu and eJobs.ro among them. The setup lists them for your countries so
  you can use their own email alerts. Check a site's terms before adding a source.
- Job posts and CVs contain personal data. Keep `data/` private and don't republish job posts.

## Security

It holds personal data, so it's built to be careful with it:

- One account per installation, created with a one-time setup code from the server log. Login page with
  signed session cookies, lockout after repeated wrong passwords, CSRF tokens on every
  form, a strict Content-Security-Policy (no inline scripts) and the usual security headers.
- No SSRF: URLs you give it (a job post, a careers page) may not resolve to private, loopback, link-local,
  CGNAT/Tailscale or cloud-metadata addresses, checked again on every redirect.
- Job posts are untrusted input: hidden text and prompt injections are stripped before any rule or model
  reads them, and everything is HTML-escaped. A job post can never add anything to your CV.
- API keys entered in the setup are kept in a file only the app can read and are never shown again; keys in
  `.env` take precedence. Only key names that a job source declares can be set.
- The local model only gets your CV, your notes and the question; it has no tools.
- The container runs as non-root, with a read-only filesystem, no capabilities and a memory cap.

Run it on your LAN or a VPN. It isn't meant to face the internet as it is.

## Status

Working, tested (about 320 tests across the five parts), and used daily by its author. No support is
promised. Issues and pull requests are welcome.

## Credits and licence

MIT licence (see [LICENSE](LICENSE)). Fonts: Cormorant Garamond and IBM Plex Sans, under the SIL Open Font
License (included). The browser helper uses [Crawl4AI](https://github.com/unclecode/crawl4ai) (Apache-2.0).
City and country data: [GeoNames](https://www.geonames.org), CC BY 4.0.
Built by [bloodmoonbreach](https://github.com/bloodmoonbreach) with AI-assisted development (Claude Code).
