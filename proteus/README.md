# Proteus — the engine

> Start at the repository README one level up. This file is the module-level detail.

> You are the source code; résumés are compiled build products.
> **New here? Read `GUIDE.md` — 3 steps to your first application package.**

## What's here

| Layer | Module | Status |
|---|---|---|
| L0 Identity Core | `proteus/db.py`, `proteus/identity.py`, `data/seed_identity.json` | ✅ SQLite+JSON, seeded from CV (+ education & experience) |
| Calibration (T1.5) | `proteus/calibration.py` | ✅ evidence-driven levels + rationale, stretch_score, Gap Report |
| L1 JobSpec (T2) | `proteus/jobspec.py` | ✅ deterministic JD parser (+ optional LLM) |
| L4 Compiler (T3+T7) | `proteus/compiler.py` | ✅ 4 persona build targets, education/experience sections, evidence ledger |
| L2 Matching (T4) | `proteus/fit.py` | ✅ fit_score ranker (spray-and-rank mode: sort, don't filter) |
| Sensing (T5, compliance-safe) | `scripts/ingest.py` | ✅ clipboard → package in one command (no scraping, by design) |
| Reviewer simulator (T6) | `proteus/reviewer.py` | ✅ deadline / work-rights / referees / selection-criteria / visa blockers |
| Cover letters (T7) | `proteus/coverletter.py` | ✅ evidence-grounded, persona-aware drafts |
| CRM board (T8) | `proteus/webapp.py`, `proteus/jobdoc.py`, `proteus/joburl.py` | ✅ paste, upload (PDF/Word) **or fetch one pasted link** + status board (`python3 -m proteus.webapp`) |
| Reverse discovery (T9) | `scripts/discover.py` | ✅ capability → employer archetypes, aimed at your city |
| Orchestrator (T10) | `scripts/apply.py` | ✅ one command → full application package per job |
| Learning loop (T11, lite) | `scripts/outcome.py` + `events` table | ✅ record outcomes, response-rate report |
| Claude API | `proteus/llm.py` | ✅ optional (claude-fable-5 + opus-4-8 fallback) |
| MiniMax API | `proteus/minimax_ai.py` | ✅ optional: live web search leads and selection criteria AI draft |

Deliberately *not* built: job-board crawling, scheduled re-fetch,
auto-submit (charter: human-in-the-loop, compliance tiers). Optional MiniMax
web search produces leads for review. `joburl.py` reads
**one** URL, **when you click**, and hands the text back to you for review — it is
assisted paste, not a crawler.

## Run it

```bash
cd proteus
python3 scripts/seed.py            # load Identity Graph + calibrate (no API key needed)
python3 scripts/apply.py           # -> out/apply/<job>/ package for every saved JD
python3 scripts/milestone.py       # -> out/level_report.md, ranking.md (sample-job demo)
```

Add `--llm` to either command to re-derive calibration levels and parse JDs live
via the Claude API (needs `pip install anthropic` and `ANTHROPIC_API_KEY`).

```bash
python3 -m pytest                  # smoke tests
```

## Design invariants honoured (Fable 5 charter §2)

1. **Evidence Ledger** — every résumé claim traces to a seeded evidence item.
2. **Human-in-the-loop** — Proteus generates; it never sends. No outbound path exists.
3. **Visa filter kept** — infeasible roles are scored down and greyed, never dropped.
4. **Backward-compatible schema** — `db.SCHEMA_VERSION`; changes add migrations.
5. **Compliance tiers** — no scraping in the MVP; JD parsing is paste-only (LinkedIn C-tier).

## Adding a real job

Three equivalent ways, all human-initiated:

- **Web UI** — **+ Add a job**: paste a link and press **Fetch the page**
  (`proteus/joburl.py` reads that one page — schema.org `JobPosting` first,
  then main-content HTML, then PDF — and drops the text, title and closing date
  into the review box), paste the ad text, upload the ad/PD as PDF or Word
  (`proteus/jobdoc.py` extracts the text), or any combination. Fetching and
  building are separate steps: you always see the text before a package is
  built. Unreadable links fail loudly (bot check / login wall / JS-only page)
  rather than saving a blank entry.
- **Clipboard** — `python3 scripts/ingest.py <name>`.
- **By hand** — drop a `.txt` into `data/real_jobs/` and run `scripts/apply.py`.

Nothing is auto-submitted, ever.

## MiniMax and the plain-language dashboard

For a double-click launch, create the ignored local file
`my-materials/config/minimax.env` at the project root with one line:

```text
MINIMAX_API_KEY=your-key-here
```

MiniMax has separate international and China API hosts. For a key issued by
the China platform, add `MINIMAX_API_HOST=https://api.minimaxi.com` on a second
line; the default is `https://api.minimax.io`. The host setting accepts only
these two official API addresses. A 401 response can mean a region mismatch.

Keep the file private (`chmod 600 my-materials/config/minimax.env`). You can
instead set `MINIMAX_API_KEY` in the terminal environment; it takes priority
over the file. Do not paste the key into the dashboard or commit it. The default
writing model is `MiniMax-M2.7` (override with `MINIMAX_MODEL`). The search
uses MiniMax-M3 and its server-side `web_search` tool. Both API operations can
consume your MiniMax allowance. Without the key, all existing local tracking
and document compilation still work.

The top of the board now shows jobs added today, open roles above the initial
fit threshold, starred roles, recorded submissions, rejections, and the time
between recorded submission and rejection events. The application table counts
submissions and rejections by role type; title-based types are provisional until
you select a type on the job tracking page. Search leads are displayed
separately under **AI 找岗位**. Add a promising vacancy to the board only after
checking the employer's current page, job ID, closing date and eligibility.
The application timeline groups submitted roles into Monday–Sunday weeks, then
expandable dates. Entries without a known submission date stay in a separate
"date needed" group until the confirmation date is recorded.
Search does not scrape or import jobs automatically. Search results are saved
only under the ignored `out/` directory. An AI selection draft is written as
`selection_criteria_AI_DRAFT.md` inside the existing ignored application
package and never overwrites a final file.

The floating **English / 中文** button switches the dashboard and common workflow
controls. The choice is kept in this browser's local storage. Employer ads,
selection-criteria headings and generated application documents retain their
source language; the interface switch never rewrites them. Daily follow-up
reminders configured in the Codex app are separate from this local web server:
the server itself does not run a background scheduler or send notifications.

## Discretion

The UI is named **Job Board** and its visible chrome (window title, headings,
buttons, status words) avoids job-hunting vocabulary; the how-to text lives in
a collapsed panel. Only the surface strings changed — routes, DB keys, file
names and package contents are untouched.
