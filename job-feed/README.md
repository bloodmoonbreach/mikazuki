# job-feed

Collects job offers on a schedule, keeps the ones that match your searches, checks and scores them, and
stores them in SQLite together with your application tracker. Used by the mikazuki web app; also runs on its
own:

```bash
pip install "remote-eligibility @ git+https://github.com/bloodmoonbreach/remote-check" \
            "cvtracks[pdf] @ git+https://github.com/bloodmoonbreach/cvtracks" ./job-feed
job-feed -d ~/job-data run          # reads feed.toml, profile.toml, master.yaml + tracks/ from that folder
```

## Pipeline

1. **Fetch** from the sources each search names, respecting each source's minimum interval.
2. **Keep relevant offers**: a search keyword in the title (or two in the text), no excluded word in the
   title, not too old.
3. **Drop what isn't a job** (`training.py`): course and bootcamp adverts, student internships (unless
   `include_internships = true`), job-fair and event sign-ups.
4. **Check** each new offer with remote-check (per-country verdict, work mode, salary, languages, AI traps)
   and score it against your CV track with cvtracks (keyword fit, matched and missing skills).
5. **Store** it; mark it a match when it can be done from one of your countries (or is near home for
   searches that allow local jobs). Offers whose work mode is unknown also need a plausible location: one of your countries (by name, city or
   country code), a region that includes one, or a remote word (`plausible_place` in `pipeline.py`).
6. **Notify** (Telegram, ntfy or a webhook) about new matches.

## Sources

| Key | What |
|---|---|
| `companies` | Company job systems: Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio, Teamtailor, Breezy, Workday (`[[company]]` blocks) |
| `careers` | Careers pages without a supported job system: recon finds the job system, JSON-LD markup or page text; JavaScript-only pages go to the optional browser helper |
| `remotive`, `remoteok`, `himalayas`, `jobicy`, `workingnomads`, `weworkremotely`, `arbeitnow`, `nodesk` | Public remote-job APIs and feeds |
| `cybersecurityjobsite` | Madgex board RSS with keyword search |
| `hipo` | Hipo.ro (Romania): search pages and JobPosting data on job pages |
| `foorilla` | Foorilla API (needs `FOORILLA_API_KEY`) |
| `infojobs` | InfoJobs API (needs developer keys; experimental) |
| `adzuna` | Adzuna API in 19 countries (Europe, Americas, India, Australia…; needs a free personal key from developer.adzuna.com in `ADZUNA_APP_ID` / `ADZUNA_APP_KEY`; its terms allow personal job search). Set `countries = ["IN"]` under `[sources.adzuna]` |
| `usajobs` | USAJOBS API, US federal jobs (most need US citizenship; free key in `USAJOBS_API_KEY`, the email you requested it with in `USAJOBS_EMAIL`) |
| `rss` | Any RSS/Atom feed you're allowed to read |

Not included on purpose: scrapers for sites whose terms forbid automated access (LinkedIn, Indeed, Google Jobs, Naukri…). Use their own email alerts.

Every request goes through an SSRF-safe client (public addresses only, checked on every redirect),
identifies itself, waits between requests to the same host, and treats bot challenges as "stop", never as
something to get around.

Configuration: see the example `feed.toml` created with the example workspace, and `src/jobfeed/config.py`.
