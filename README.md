# Proteus — an application compiler for the Australian job market

Australian job applications want a separate statement addressing eight or
nine selection criteria. This compiles them from a structured description of
yourself.

The criteria usually live in a Position Description PDF rather than in the ad,
and the convention is national — the APS, every state and territory government,
every university, most of the health and community sector. Writing that by hand
for forty applications is where people give up, or start claiming things they
cannot defend in the interview.

You describe yourself **once**: evidence (things that can be checked) and
capabilities (what you can do, each pointing at its evidence). Every
application is compiled from that, and nothing reaches the page unless it
traces back to something real.

Nothing in it is specific to a city or an employer: selection criteria are a
national convention. Use it for APS roles in Melbourne, a university in Perth,
a health service in Brisbane, or anything else in Australia.

---

## Start here — three steps

```bash
cd proteus
cp data/seed_identity.template.json data/seed_identity.json   # 1. describe yourself
python3 scripts/seed.py                                       # 2. load and calibrate
python3 -m proteus.webapp                                     # 3. open the dashboard
```

Then open **http://localhost:8765**, drag a job-ad PDF onto the page, and a
full application package appears: tailored résumé, cover letter, a statement
addressing each selection criterion, a pre-submission review, and a gap report.

On a Mac, double-clicking **`Open-Dashboard.command`** does step 3 for you.

Python 3.11+. **No dependencies, no API key, no account.** Everything runs
locally on your machine.

---

## What you get per job

| File | What it is |
|---|---|
| `checklist.md` | **Read first.** What to upload where, blockers, deadline. |
| `resume.md` | Tailored résumé — every claim chained to evidence. |
| `cover_letter.md` | Draft letter, positioned for this employer. |
| `selection_criteria.md` | One section per criterion, **headings verbatim from the PD**, with your matching evidence under each. |
| `review.md` | Pre-submission check: ⛔ blockers, ⚠️ warnings. Catches visa conflicts, missing referees, passed deadlines. |
| `gap.md` | What to read up on before the interview. |

---

## The ideas worth stealing

**Evidence ledger.** A claim exists only if it points at an evidence entry — a
paper, a repo, a project with real numbers. The compiler cannot write "extensive
experience in X" because there is nowhere for that to come from. This is a
honesty mechanism, not a formatting one.

**Calibration.** Each capability carries a level from 1–5 with a written
rationale, including what you *cannot* yet do. It guards in both directions:
against overclaiming, and against the systematic self-underestimation that stops
people applying for jobs they would get.

**Selection criteria, verbatim.** Criteria are extracted from the PD PDF exactly
as written — the odd spacing and missing commas included. Paraphrasing a
criterion is a real way to lose marks, since panels score against their own
wording.

**Answer reuse.** Australian criteria repeat: nearly every APS/university role
asks the same handful of things in different words. The system matches a new
criterion against answers you already wrote and approved, and offers your own
prose as the first draft — labelled with where it came from, never applied
silently.

**A reviewer that says no.** Before you submit, it checks work rights against
the ad, referees, deadlines, and whether the criteria statement is present at
all. Visa-infeasible roles are scored down and greyed, **never hidden** — that
call stays yours.

**Learning from your edits.** When you rewrite a draft, the diff is stored, and
the next package reproduces your wording instead of reverting to the machine's.

## What it deliberately will not do

No job-board scraping. No scheduled re-fetching. No auto-submit. It reads
**one** URL when you click, and hands the text back for you to check — assisted
paste, not a crawler. Every application is submitted by a human who has read it.

---

## Layout

```
├── Open-Dashboard.command   double-click to start (macOS)
├── SYSTEM_SUMMARY.md        full system description — point an AI agent here
├── proteus/                 the engine
│   ├── proteus/             modules: identity, calibration, jobspec, compiler,
│   │                        fit, gate, selection, reuse, reviewer, revisions,
│   │                        webapp
│   ├── scripts/             apply.py, seed.py, ingest.py, outcome.py, discover.py
│   ├── data/                seed_identity.template.json, sample_jobs/
│   ├── tests/               270 tests — python3 -m pytest
│   └── GUIDE.md             three-step quick start
└── my-materials/            YOUR private data — git-ignored, never published
    ├── cv/  job-ads/  applications/  tracking/  reference/
```

## Before you commit anything

A pre-commit hook refuses commits that would publish personal data. Turn it on
once, per clone:

```bash
git config core.hooksPath .githooks
cp .githooks/secrets.example.txt .githooks/secrets.txt   # then add your own
```

It works in two layers. **Path rules are built into the hook** — the private
materials folder, the identity and calibration files, any `.docx` or `.pdf` —
so they cannot be lost. **Literal strings come from `.githooks/secrets.txt`**:
your name, email, phone, employers, referees. That file is git-ignored, and the
hook also refuses to commit it: a list of exactly what you are hiding is not
something to publish.

Only lines the commit *adds* are scanned, so an existing mention will not block
every later commit. `git commit --no-verify` overrides it — a guard, not a cage.

Keep short or ambiguous strings out of the list. A rule that fires on every
commit gets bypassed, and then protects nothing.

**Private data is excluded from GitHub.** `my-materials/`, the database and the
identity file are git-ignored. Check `.gitignore` before pushing anywhere.
If you choose the optional MiniMax writing button, the selected job's evidence
worksheet is sent to MiniMax to generate a draft. Review its contents first.

---

## Honest limits

- The **JD parser is heuristic.** Australian PDs follow a common shape — a
  form with a `Position Title:` field and a bulleted *Selection Criteria*
  section — and that is what it reads. It has been tested against university
  and state-government documents; other employers using the same shape should
  work. A PD laid out differently may need its
  criteria pasted in by hand, and `selection.py` is where you would teach it a
  new shape.
- **Draft quality depends entirely on your evidence ledger.** A thin ledger
  produces thin résumés. The hour spent writing honest evidence entries is the
  hour that decides whether any of this is useful.
- It **will not write your best sentences for you.** It assembles the right
  material against the right criterion; the STAR examples are yours.
- Built for **Australia**. The selection-criteria machinery is the whole point
  here and is probably wasted elsewhere, though the evidence ledger and the
  résumé compiler are not country-specific.
- **Visa scoring is conservative by default.** Roles demanding citizenship,
  permanent residency or a security clearance are scored down for review,
  not removed. Verify eligibility against the official advert and your own
  status before applying.

MIT licensed. Take it, fork it, cut out the parts you do not need.
