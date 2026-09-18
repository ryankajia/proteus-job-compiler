# Proteus — 3-step quick start

You are the source; résumés are build products. Nothing is ever sent anywhere
automatically — you always press submit yourself.

---

## First run (once)

```bash
cd proteus
cp data/seed_identity.template.json data/seed_identity.json
cp data/calibration_levels.template.json data/calibration_levels.json
# Edit both files — this is the real work. See "Describing yourself" below.
python3 scripts/seed.py
```

## The everyday loop

**1. Open the dashboard**

```bash
python3 -m proteus.webapp        # → http://localhost:8765
```

On a Mac, double-click `Open-Dashboard.command` instead.

**2. Add the job.** Press **+ Add entry**. Three ways in — use any, or mix them:

| Way | When to use it | Notes |
|---|---|---|
| **Paste a link** | The ad is a normal web page | Press *Fetch the page*. It reads **only** that one page and shows you the text before anything is built. University, government and ATS pages usually work; SEEK and Indeed block scripts; LinkedIn is not attempted, by their terms. |
| **Paste text** | Anything else | Copy the whole ad — title, employer, requirements. No need to tidy it. |
| **Upload a document** | Most Australian vacancies | Drag the PD onto the box. PDF, .docx, .doc, .rtf, .txt, .md. Several files merge into one entry — e.g. the ad text plus the official PD. |

Optionally set a **name** (becomes the folder name), a **link** (becomes the
"go to the employer's page" button) and a **closing date** (becomes the ⏰ badge).

Fetching and building are deliberately two steps: a link alone never silently
produces an application package.

**3. Open the package** at `out/apply/<name>/`:

| File | What it is |
|---|---|
| `checklist.md` | **Read first** — what to upload where, blockers, deadline |
| `resume.md` | Tailored résumé, every claim chained to evidence |
| `cover_letter.md` | Draft letter — personalise the opening |
| `selection_criteria.md` | One section per criterion, headings verbatim from the PD |
| `review.md` | ⛔ blockers / ⚠️ warnings before you submit |
| `gap.md` | What to cram before the interview |

Edit anything in the browser (✏️ **Edit**), then **Print / Save as PDF** or
**Download Word**, and submit on the employer's own site.

**4. Mark it Submitted** on the board once the confirmation email arrives.

> **An application counts only when the "Application received" email arrives.**
> Finishing the documents is not applying. Applications are routinely lost
> with the files finished and the Submit button never pressed.

---

## Describing yourself — the part that decides everything

Draft quality depends entirely on `seed_identity.json`. An hour spent here is
worth more than any amount of tuning.

- **Evidence** = things that can be checked: a paper, a repo, a project with
  real numbers, a URL. Scale beats adjectives.
- **Capability units** = what you can do, each *pointing at* the evidence that
  proves it.
- **Calibration levels** (`calibration_levels.json`) = 1–5 plus a written
  rationale for each capability. Say what you *cannot* yet do as plainly as
  what you can. Every capability needs an entry, or it silently fails to load.

Nothing appears in a generated document unless it traces back to an evidence
entry. That is the whole point: the compiler cannot write "extensive experience
in X", because there is nowhere for that sentence to come from.

Start small — six evidence entries and four capabilities is enough for a first
package.

---

## Useful commands

```bash
python3 scripts/apply.py                  # rebuild every package
python3 scripts/apply.py my_job_name      # rebuild one (any filename substring)
python3 scripts/ingest.py my_job_name     # clipboard → package, from the terminal
python3 scripts/outcome.py set my_job_name applied    # record a submission
python3 scripts/outcome.py report         # response rates by positioning
python3 -m pytest                         # 64 tests
```

## Conventions worth knowing

- `*_FINAL.md` is hand-polished and **never overwritten** by a rebuild. Bare
  drafts are regenerated freely.
- A line starting with `>` is a note to yourself: shown as a yellow sticky in
  the browser, stripped from print and Word output.
- Editing a draft teaches the system your wording; the next rebuild keeps it.
- `.generated/` holds the snapshot each rebuild diffs against — don't edit it.
- Keep your real CVs, referees and applications outside the repo. See
  `my-materials/README.md`; everything there is git-ignored.
