# SYSTEM_SUMMARY.md

A complete description of this system, written to be handed to an AI agent or a
developer who has never seen it. Read this and you can work on it.

---

## 1. What problem it solves

Applying for professional work in Australia is a document-production problem.
A typical university or government vacancy wants:

1. a CV tailored to the role,
2. a cover letter,
3. **a separate statement addressing every selection criterion** — and the
   criteria usually live in a Position Description PDF, not in the job ad.

Eight or nine criteria per job, each needing a specific evidenced answer. Doing
this by hand for dozens of applications is where candidates either give up or
start writing claims they cannot defend in an interview.

**The system's premise:** a person is the source code, and each application is a
compiled build product. Describe yourself once, in a structured and honest way;
compile every application from that description.

## 2. The central invariant

> **A claim may appear in a generated document only if it traces to an evidence
> entry.**

Evidence entries are things that can be checked: a paper, a repository, a
project with real numbers, a URL. Capability units are what the person can do,
and each one *points at* the evidence that proves it.

The compiler has no path by which to produce "extensive experience in X",
because there is nowhere for that sentence to come from. This is the system's
honesty mechanism and every other design decision is subordinate to it.

## 3. Architecture

```
seed_identity.json  →  SQLite  →  compile  →  out/apply/<slug>/
   (who you are)      (queried)   (per job)    (the package)
                                      ↑
                             job ad PDF / paste / URL
```

### Module map — `proteus/proteus/`

| Module | Responsibility |
|---|---|
| `db.py` | SQLite schema and connection. Tables: `profile`, `evidence`, `capability_units`, `calibration`, `jobspecs`, `pipeline`, `events`, `revisions`. |
| `identity.py` | Loads `seed_identity.json` into the DB. The identity graph: profile + evidence + capability units. |
| `calibration.py` | Assigns each capability a level 1–5 with a written rationale. Computes `job_stretch` — how far a job reaches beyond current level. Levels are loaded from `data/calibration_levels.json` (private — they quote real career specifics); **every capability in the seed must have an entry there or it silently fails to load.** |
| `jobspec.py` | Parses a job ad into a structured JobSpec (title, org, seniority, skills, visa, salary, deadline). Heuristic by default; optional LLM path. Prefers a PD's `Position Title:` field over the first line, because a PD is a form whose first line is a field label. |
| `jobdoc.py` | Extracts text from an uploaded PDF/Word/RTF/txt. `tidy()` strips page furniture — page numbers and any line repeating on 3+ pages — otherwise a running header becomes the job title. Refuses scanned PDFs loudly rather than saving an empty entry. |
| `joburl.py` | Reads **one** URL when you clicks. schema.org JobPosting → main content → PDF. Knows which domains hard-block automation (some government portals return 403 to everything) and says so instead of advising a pointless retry. Add domains as you meet them. |
| `compiler.py` | Builds the résumé. Ranks capabilities by relevance to the JobSpec, selects, renders with evidence chains. Four persona build targets. |
| `selection.py` | **The most valuable module.** Extracts criteria from the PD verbatim and drafts a response section per criterion. See §4. |
| `reuse.py` | Matches a new criterion against answers you already wrote and approved in past applications, and offers that prose as the first draft. |
| `coverletter.py` | Evidence-grounded letter drafts, persona-aware. |
| `fit.py` | `fit_score` ranker. Visa-infeasible roles are multiplied by 0.35 and greyed — **scored down, never removed.** The decision stays human. |
| `reviewer.py` | Pre-submission simulation: work rights vs the ad, referees, deadline, criteria statement present. Emits ⛔ blockers and ⚠️ warnings. |
| `revisions.py` | Learns from your edits. Diffs your edited file against the generated snapshot in `.generated/`, stores the edits, replays them on the next build. See §5 for the trap here. |
| `materials.py` | Syncs a master CV `.docx` into the profile. Stdlib docx reader. |
| `webapp.py` | The whole UI: a local dashboard on `:8765`. Stdlib `http.server`, no framework. Add jobs by URL, paste, or **drag-and-drop**. |
| `llm.py` | Optional Claude API path. Dormant without `anthropic` + `ANTHROPIC_API_KEY`. The system is fully functional without it. |
| `minimax_ai.py` | Optional MiniMax assistant: on-demand web search and evidence-bounded selection criteria draft. Requires `MINIMAX_API_KEY`; the key stays in the environment. Search results are review leads, not imported or verified jobs. |

### Scripts — `proteus/scripts/`

`seed.py` (load + calibrate) · `apply.py` (build packages — the orchestrator)
· `ingest.py` (clipboard → package) · `outcome.py` (record results)
· `discover.py` (capability → employer map) · `milestone.py` (demo reports)

## 4. How selection criteria work — read this before touching `selection.py`

This is the part that carries the most hard-won knowledge.

**Extraction is verbatim.** `extract_criteria()` pulls criteria from the PD text
exactly as written, including the source's own typos (`education/ training` with
the stray space, `evidence based` with no hyphen). Paraphrasing loses marks:
panels score against their own wording. The parser handles headings with lead-in
clauses, criteria wrapped across lines, and rejects decoy sentences like
"Applications which do not address the selection criteria…".

**Matching criteria to capabilities** — `_crit_score()`:

1. **Whole-word matching only.** A past bug: plain substring matching meant the
   keyword `"R"` (the language) matched the letter *r* inside any English word,
   so every R-tagged capability scored a false hit on every criterion. This put
   "AI-assisted analysis implementation" at the top of a *data processing and
   reporting* criterion and buried the person's actual data-analysis evidence.
2. **A synonym bridge** (`_SYNONYMS`). Australian criteria are written in
   administrative English ("data processing", "large datasets", "competing
   deadlines"); an evidence ledger is written in the person's own professional
   English ("data analysis", "pandas", "statistics", "coordination"). Literally
   they never meet. Bridged hits score 0.6 against a literal hit's 1.0.
3. **Ties break on calibrated level** — where two capabilities answer equally
   well, lead with the stronger one.
4. **A second claim appears only if it scores ≥50% of the first.** A distant
   runner-up is noise you have to delete.
5. **An absolute floor (`_MIN_CRIT_SCORE = 1.0`).** Below it the section says
   *no direct match — answer honestly* instead of attaching the least-bad
   capability. Before this, a criterion about equal opportunity and fire-warden
   duties attracted *web development*. Saying "nothing here" is more useful than
   a fabricated fit.

**Two kinds of reply (`response_style()`).** "Addressing the selection criteria"
names two different documents. An *itemised* statement has a heading per criterion
and is scored one criterion at a time; a *narrative* supporting statement / pitch
(often page-limited) is read whole and then checked for coverage, so one strong
example should answer several criteria. Advice for one is wrong for the other, so
the generator branches. The style comes from the PD's own instructions — a "pitch"
or a page-limited "supporting statement" — never from the employer's name, and the
PD frequently says nothing at all (the wording lives on the advert page), so
itemised is the default and narrative needs an explicit signal. The detected
phrase is quoted in the output so a wrong call is visible, and a line
`Response style: narrative|itemised` in the saved job text overrides it. The
narrative worksheet is a coverage map, a greedy choice of at most four examples
that between them cover the criteria, a word budget, and a tick-box checklist the
board reads as progress.

**Answer reuse (`reuse.py`).** Australian criteria repeat across roles. The
module scans finalised criteria files from past applications, strips scaffolding,
and matches by content-word Jaccard similarity (threshold 0.16). A hit is
inserted as a labelled draft showing its source package and score. It **excludes
the job's own answers** so it cannot match itself. Reused prose remains a
draft that must be checked against the new role and its official PD.

## 5. Traps that have already caused real damage

**The learning loop can eat the scaffolding.** `selection_criteria.md` is a
*worksheet*: you deletes the generated `**Claim:**` and evidence lines while
writing their real answer. `revisions.learn_package()` diffed that and learned
"never emit these lines again", storing 127 global drop rules. Every subsequent
package had its evidence stripped line by line, and criteria came out blank.
Fixed by `_is_scaffold()` — **scoped to the selection-criteria files only**,
because a résumé bullet like `- **Runs field surveys**` is authored content and
must stay learnable. If you change the scaffolding format, update that regex.

**A capability missing from `calibration_levels.json` silently vanishes.** It sits in
the seed, fails to load, and the criteria that needed it draw no evidence. This
can happen if an office-software capability lacks a calibration entry while
criteria explicitly ask for advanced Excel.

**JavaScript inside an f-string.** `webapp.py` renders HTML from f-strings. Every
literal `{` and `}` in embedded JS must be doubled or the module will not parse.

**A PD is a form, not an ad.** Its first line is a field label. Extract the title
from `Position Title:`, and strip page furniture before parsing, or packages get
named things like `24/08/2026 HR125 Page 1 of 3`.

## 6. Compliance posture — do not "improve" this away

Deliberately absent: job-board crawling, scheduled re-fetching and auto-submit.
Optional MiniMax web search returns leads for human verification. `joburl.py`
reads one URL per click and returns the text for review
— assisted paste, not a crawler. LinkedIn is refused outright without a request.
SEEK and Indeed block scripts and the error message says so rather than
suggesting a retry.

**Every application is submitted by a human who has read it.** Automating the
Submit button would be the single most tempting change and the wrong one: the
declaration being signed is a legal statement about a real person.

## 7. Privacy

`my-materials/`, `proteus/databases/`, `proteus/out/`, `proteus/data/seed_identity.json`
and `proteus/data/calibration_levels.json` are git-ignored. They contain real CVs, **third-party
referees' names**, submitted applications and personal identifiers. Only the
engine, the template and the sample jobs are public. Check `.gitignore` before
pushing.

## 8. Repository layout

```
├── README.md                public landing page and quick start
├── SYSTEM_SUMMARY.md        this file
├── Open-Dashboard.command   macOS double-click launcher
├── proteus/                 the engine
│   ├── proteus/             modules (see §3)
│   ├── scripts/             orchestration entry points
│   ├── data/                *.template.json, sample_jobs/
│   ├── tests/               offline tests (pytest)
│   ├── GUIDE.md             three-step quick start
└── my-materials/            private data, git-ignored (see its own README)
```

## 9. Conventions

- **One slug per job, everywhere.** `job-ads/<slug>_<jobID>.pdf`,
  `applications/<slug>/`, `proteus/out/apply/<slug>/`.
- **No job IDs or employer names inside your own documents**, or their
  filenames. The folder path is the record. (Downloaded PDs are the exception —
  they keep the ID so the right vacancy can be verified before uploading.)
- `*_FINAL.md` is hand-polished and **never overwritten** by a rebuild. Bare
  drafts are regenerated freely.
- `.generated/` holds the snapshot each build diffs against. Do not edit it.
- Every autonomous change is logged in `my-materials/reference/DECISIONS.md`
  with date, what, and why. It is private: it names real vacancies and outcomes.
- Runtime code uses the standard library; run tests with `python3 -m pytest`.

## 10. Running it

```bash
cd proteus
cp data/seed_identity.template.json data/seed_identity.json
# edit that file — this is the real work
python3 scripts/seed.py              # load + calibrate
python3 -m proteus.webapp            # dashboard at localhost:8765
python3 scripts/apply.py [slug]      # rebuild packages from the terminal
python3 -m pytest                    # offline test suite
```

Python 3.11+, standard library only. `anthropic` is optional and unused by
default.
