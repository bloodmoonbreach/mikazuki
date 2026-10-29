# AGENTS.md: setting up mikazuki for a new user

Instructions for an AI coding assistant (Claude Code, Cursor, Copilot, Codex…) helping someone adapt mikazuki
to their own job search. Read [README.md](README.md) first for what the parts do.

Your job is to turn the user's real situation into five data files, and to adapt the few places in the code
that are specific to one field. Work with the user, one topic at a time. **Never invent a fact.**

## Ground rules

1. **Facts come from the user only.** Don't guess dates, grades, employers, tools, numbers or achievements.
   If something is missing, write `TODO` in the file (cvtracks lint lists them) and ask.
2. **Prove, don't claim.** A CV summary describes the person as a professional (role, how they work, what
   they bring) in 2–4 sentences. No list of projects, no "passionate team player". Every soft skill in the
   summary must be backed by a bullet somewhere in the CV.
3. **Contact data only.** Name, email, phone, links. No address or city, no nationality or citizenship, no
   age, photo or marital status. In the EU this is GDPR data minimisation and anti-discrimination practice,
   and some employers' policies reject CVs that include it. Work mode (remote/hybrid) is agreed with each
   employer and doesn't go in the CV header.
4. **Describe AI-assisted work honestly.** If the user built something with an AI coding assistant, the CV
   says so ("designed and built with AI-assisted development"), not "wrote it in Python".
5. **Respect websites' rules.** Only add job sources whose terms and robots.txt allow automated reading. If a
   site's terms forbid bots or scraping, don't add it; suggest the site's own email alerts instead.
6. **Don't commit personal data.** `data/`, `.env` and `compose.yaml` are git-ignored; keep it that way.

## Step 1: interview

Ask, in this order, and keep notes in the user's language:

- **Target roles** (one to three), seniority, and a fallback role if any. Each becomes a CV track.
- **Countries** they can legally work from now and later; citizenships (for work-permit questions only,
  never for the CV); languages with levels (CEFR A1–C2 where possible).
- **Work modes** they accept (remote, hybrid near home, on-site) and the towns that count as "near home".
- **Experience**: every job with dates, role, employer, and 2–4 results with numbers where real. Help them
  find the transferable part of unrelated jobs, without disguising the job as something it wasn't.
- **Education**, **certifications** (date, ID, verification link), **projects** (what, tools, outcome,
  link if public), **skills** they could explain in an interview.
- **Application facts**: LinkedIn and GitHub handles, salary expectation per role (help them research the
  middle of published ranges for their country and level), availability, driving licence, travel.
- **In their own words**: why this field, what they're proud of, a weakness they're working on.

## Step 2: fill the files

All files live in `data/` (or edit them on the **Edit** page; every save keeps the previous version).
Start from the example workspace (**Create example workspace** on the welcome page) and replace its content.

- **`master.yaml`** (format: the cvtracks README and `cvtracks/src/cvtracks/examples/master.yaml` in the
  cvtracks repository). Texts can be per language: `{en: "...", es: "..."}`. Tag items with the tracks they
  belong to; give bullets `keywords` that screeners look for, only when true.
- **`tracks/<role>.yaml`**, one per target role: `title` (headline), `summary` (key into `summaries`),
  `include_tags`, `sections` order, `limits` (one page for junior profiles), `keywords` to lint against.
- **`profile.toml`**: `countries`, `citizenships`, `languages`; `[llm]` for the local model; `[apply]` for
  form facts (`linkedin`, `salary` or `salary_<track>`, `availability`, `work_mode`, `relocate`,
  `travel`, `driving_licence`; values can be `{en = "...", es = "..."}`). Never put API keys in this file;
  use `api_key_env` with the name of an environment variable.
- **`feed.toml`**: one `[[search]]` per role with `keywords` (in every language the user's job market
  uses), `exclude_title` (seniority and roles they don't want), `sources`, `remote_only`, `locations`.
  Add employers as `[[company]]` (use **Add company** on the Feed page: it detects the job system and
  checks it answers) and careers pages as `[[careers]]`. Big Workday/SmartRecruiters boards take a list of
  search terms. Set `include_internships = true` only if the user needs a work placement.
- **`about.md`** (optional): facts the CV doesn't hold, the user's own words, and how they want answers
  written. Apply gives it to the model with the CV.

Run **Lint** after every change and fix errors; warnings about keywords mean "add only if true".

## Step 3: adapt the field-specific parts

The code ships tuned for cybersecurity and IT support. For another field:

- **Skills vocabulary**: `cvtracks/src/cvtracks/skills/*.txt` (in the cvtracks repository, one file per
  field: `it-security.txt`, `software-data.txt`, `business-creative.txt`) lists the terms that can be reported
  as "missing" from a CV. Add the tools, methods and certifications of the user's field to the matching file,
  or a new `<field>.txt`, one per line, in every language their market uses. Short acronyms only match in
  capitals; avoid terms that are also ordinary words.
- **Not-a-job filter**: `job-feed/src/jobfeed/training.py` recognises course adverts, bootcamps, student
  internships and job fairs in English, Spanish and Romanian. Add phrases for other languages if needed,
  with tests.
- **Plausible places**: a job of unknown work mode counts as a match only if its location names one of the
  user's countries (`countries` in profile.toml), a region that includes one, or a remote word
  (`plausible_place` in `job-feed/src/jobfeed/pipeline.py`). Cities are recognised through remote-check's
  GeoNames list (every city of 50,000+ people, 252 countries), and mikazuki downloads the detailed town list of
  each country in profile.toml by itself (`places/` in the data folder). If a town is still placed wrong or
  left unclear, add it to remote-check's `src/remote_eligibility/cities-overrides.txt` (`Town | CC`) with a test.
- **New job boards**: write a source in `job-feed/src/jobfeed/sources/builtin.py` only after checking the
  site's terms and robots.txt (rule 5). Use `crawl.fetch_page` (it respects robots.txt and crawl delays)
  and prefer structured data (JSON-LD JobPosting, RSS, a public API) over page scraping.

Keep the tests passing (`pytest` in each part) and add tests for anything you change.

## Step 4: try it

Run the feed (**Run now** on the Feed page), open a few matches, press **Apply** on one, and read the drafts
with the user. If a draft claims something untrue, fix the facts or `about.md`, not the draft.
