"""Selection criteria responses — the Australian application ritual.

Many Australian employers (universities, government and adjacent) require a
separate statement addressing each selection criterion; applications that skip
it "may not be considered". This module drafts that statement:

- If the JD text enumerates the criteria, each one gets a section with the
  best-matching capability units + their evidence, and a STAR TODO marker.
- If the JD only *mentions* selection criteria (they usually live in a PD PDF
  the JD links to), it produces a ready-to-fill template plus an evidence bank
  ranked for this job, so pasting the real criteria in is the only manual step.

Same invariant as the résumé/cover-letter compilers: claims trace to L0
evidence; the draft never invents examples — the STAR specifics are yours.
"""
from __future__ import annotations

import re
import sqlite3

from . import calibration, identity, reuse
from .compiler import TARGETS, TARGET_ALIASES, _relevance

_HEAD_RE = re.compile(
    r"^\s*(?:the\s+)?(selection criteria|essential criteria|desirable criteria|"
    r"about you|what you.{0,3}ll bring|to be successful.{0,40}|"
    r"skills and experience|our ideal candidate)"
    # A parenthetical qualifier is part of the heading, not prose:
    # "Selection Criteria (from the official Position Description, P54814):".
    r"(?:\s*\([^)]{0,70}\))?"
    # Ads often trail the heading with a lead-in clause rather than ending it:
    # "Selection Criteria — the successful candidate will have:". Allow that,
    # but only when it ends in a colon, so prose sentences don't match.
    r"(?:\s*[—–:-]\s*.{0,70}:)?\s*:?\s*$", re.I)
_ITEM_RE = re.compile(r"^\s*(?:[-•*]|\d{1,2}[.)])\s+(.{15,})$")
# Prose lines tolerated between the heading and the first list item.
_MAX_LEAD_IN = 3

# Below this a "match" is one or two incidental shared words, not evidence.
_MIN_CRIT_SCORE = 1.0


def mentions_criteria(raw: str) -> bool:
    return "selection criteria" in (raw or "").lower()


def extract_criteria(raw: str) -> list[str]:
    """Pull an enumerated criteria list out of the JD text, if there is one."""
    criteria: list[str] = []
    lines = (raw or "").splitlines()
    in_block = False
    blank_before = False   # a blank line separates items; prose after one ends the list
    kept_last = False      # was the previous item kept (vs. filtered as an instruction)?
    lead_in = 0            # prose lines seen between the heading and the first item
    for ln in lines:
        if _HEAD_RE.match(ln.strip()):
            in_block = True
            blank_before = False
            lead_in = 0
            continue
        if not in_block:
            continue
        if not ln.strip():
            blank_before = True
            continue
        m = _ITEM_RE.match(ln)
        if m:
            item = m.group(1).strip().rstrip(";,")
            # Document-upload instructions are not criteria.
            kept_last = not re.match(
                r"(a |an )?(current |statement|cv|curriculum|cover letter|other document)",
                item, re.I)
            if kept_last:
                criteria.append(item)
        elif criteria and not blank_before:
            # A wrapped criterion: PDFs and long bullets break mid-sentence.
            # Only ever extends the item directly above it, never restarts a list.
            if kept_last:
                criteria[-1] = (criteria[-1] + " " + ln.strip()).rstrip(";,")
        elif criteria:
            break              # blank line then prose: the list is over
        else:
            # A heading is often followed by an instruction sentence before the
            # list starts ("Provide concise evidence of your skills…"). Give it
            # a couple of lines before concluding there is no list here.
            lead_in += 1
            if lead_in > _MAX_LEAD_IN:
                in_block = False
        blank_before = False
    return criteria


# Criterion vocabulary -> capability vocabulary. AU selection criteria are
# written in administrative English ("data processing and reporting",
# "large datasets"); the evidence ledger is written in research English
# ("data analysis", "pandas", "statistics"). Without this bridge the two
# never meet and a criterion draws no evidence at all, or draws whatever
# happened to share a stray word.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "data processing": ("data analysis", "data pipeline", "pandas", "statistics"),
    "data": ("data analysis", "data pipeline", "pandas"),
    "datasets": ("data analysis", "data pipeline", "statistics"),
    "dataset": ("data analysis", "data pipeline", "statistics"),
    "analyse": ("data analysis", "statistics"),
    "analysing": ("data analysis", "statistics"),
    "analytical": ("data analysis", "statistics", "experimental design"),
    "analysis": ("data analysis", "statistics"),
    "statistical": ("statistics",),
    "reporting": ("research administration", "grant management", "scientific writing"),
    "reports": ("research administration", "grant management", "scientific writing"),
    "projects": ("project management", "coordination"),
    "project": ("project management", "coordination"),
    "stakeholders": ("coordination", "research administration"),
    "supervise": ("supervision", "teaching", "mentoring"),
    "mentor": ("supervision", "teaching", "mentoring"),
    "train": ("teaching", "mentoring"),
    "policies": ("research administration", "compliance"),
    "procedures": ("research administration", "compliance"),
    "systems": ("data pipeline", "software development", "web application"),
    "database": ("SQLite", "web application", "data pipeline"),
    "spreadsheet": ("Excel", "data analysis"),
    "excel": ("Excel", "data analysis"),
    "organisational": ("coordination", "project management", "research administration"),
    "organise": ("coordination", "project management"),
    "prioritise": ("coordination", "project management"),
    "workload": ("coordination", "project management", "research administration"),
    "deadlines": ("coordination", "project management", "grant management"),
    "deadline": ("coordination", "project management", "grant management"),
    "team": ("coordination", "supervision"),
    "administrative": ("research administration", "coordination"),
    "communication": ("scientific writing", "coordination"),
    "liaise": ("coordination", "research administration"),
    "budget": ("grant management", "research administration"),
    "committee": ("research administration", "coordination"),
}


def _word_re(term: str) -> re.Pattern:
    """Whole-word/phrase matcher. Plain substring matching made the keyword
    'R' (the language) match the letter r inside any word, so every capability
    tagged R scored a false hit on every criterion — that is how criterion 1
    of one real draft drew 'AI-assisted analysis
    implementation' instead of your actual data-analysis evidence."""
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term.lower()) + r"(?![A-Za-z0-9])")


def _mentions(haystack: str, term: str) -> bool:
    return bool(_word_re(term).search(haystack))


def _crit_score(cap: dict, crit: str) -> float:
    t = crit.lower()
    cap_text = (cap["statement"] + " " + " ".join(cap["keywords"])).lower()

    hits = sum(1 for kw in cap["keywords"] if _mentions(t, kw))

    # Words of the criterion that appear in the capability statement.
    stmt_hits = sum(1 for tok in set(re.findall(r"[a-z]{4,}", t))
                    if _mentions(cap["statement"].lower(), tok))

    # Bridged vocabulary: a criterion term whose research-side equivalent the
    # capability actually carries. Weighted below a literal keyword hit.
    bridged = 0
    for term, equivalents in _SYNONYMS.items():
        if _mentions(t, term):
            bridged += sum(1 for eq in equivalents if _mentions(cap_text, eq))

    return hits * 1.0 + bridged * 0.6 + stmt_hits * 0.2


def _evidence_lines(conn: sqlite3.Connection, cap: dict) -> list[str]:
    out = []
    for ev_id in cap["evidence"]:
        ev = identity.get_evidence(conn, ev_id)
        if ev:
            mark = "✓" if ev["verifiable"] else "○"
            out.append(f"  - {mark} ({ev['type']}) {ev['ref']}")
    return out


def compile_selection_criteria(conn: sqlite3.Connection, spec: dict,
                               target: str = "researcher") -> str:
    target = TARGET_ALIASES.get(target, target)
    weights = TARGETS.get(target, {})
    profile = identity.get_profile(conn)
    caps = identity.get_capabilities(conn)
    levels = calibration.get_levels(conn)
    criteria = extract_criteria(spec.get("raw_text") or "")
    # Never reuse this job's own answers back into itself.
    bank = reuse.load_bank(exclude_package=spec.get("source"))

    title = spec.get("title") or "the advertised role"
    org = spec.get("org") or ""

    lines: list[str] = []
    ap = lines.append
    ap(f"# Statement addressing the selection criteria — {title}"
       + (f", {org}" if org else ""))
    ap("")
    ap(f"{profile['name']} · {profile['email']} · {profile['phone']}")
    ap("")
    ap("> ✍️ **2–3 sentences (45–80 words) per criterion.** First sentence: say "
       "plainly why you meet it. Then one or two giving your hardest evidence "
       "(numbers, title, project). Then stop — no preamble, no summing up, no "
       "adjectives, and do not reuse an example from another criterion. Give a "
       "STAR example (situation → task → action → measurable result) only for "
       "the one or two criteria that matter most. Paragraphs marked ♻️ are "
       "reused from your own earlier final answers — check them before use. "
       "Sticky-note lines are stripped from print and Word output.")
    ap("")

    def respond_to(crit: str, idx: int) -> None:
        ap(f"## Criterion {idx}: {crit}")
        ap("")
        # An approved answer to the same criterion from a past application
        # beats anything generated: the words are yours and were already judged
        # fit to send. Offered as a draft, labelled, never applied silently.
        prior = reuse.best_match(crit, bank)
        if prior:
            ap(f"> ♻️ **Reused from your own final answer** ({prior['package']}, "
               f"similarity {prior['score']}) — check line by line that it still "
               "fits this criterion and this job, then delete this note.")
            ap("")
            ap(prior["answer"])
            ap("")
        # Ties break on calibrated level: where two capabilities answer a
        # criterion equally well, lead with the one you is strongest at.
        ranked = sorted(caps, key=lambda c: (_crit_score(c, crit),
                                             levels.get(c["id"], {}).get("level", 0),
                                             _relevance(c, spec, weights)), reverse=True)
        # The second claim only earns its place if it answers the criterion
        # comparably well; a distant runner-up is noise you have to delete.
        # An absolute floor, not just a relative one. A criterion nothing in
        # the ledger really answers (equal opportunity, WHS) used to attract
        # whatever scored highest — web development under a fire-warden
        # criterion. Saying "no direct match" is more useful and more honest.
        hits = [c for c in ranked if _crit_score(c, crit) >= _MIN_CRIT_SCORE]
        top = hits[:1]
        if len(hits) > 1 and _crit_score(hits[1], crit) >= 0.5 * _crit_score(hits[0], crit):
            top.append(hits[1])
        if top:
            for c in top:
                lv = levels.get(c["id"], {})
                ap(f"**Claim:** {c['statement']} (calibrated L{lv.get('level', '?')}/5)")
                lines.extend(_evidence_lines(conn, c))
                ap("")
            ap("> ✍️ Turn the claims above into **2–3 sentences** in the first "
               "person: say you meet it, give the evidence, stop.")
        else:
            ap("> ✍️ Nothing in the evidence ledger answers this one directly. "
               "Answer honestly: give the closest transferable experience, or say "
               "how you would pick it up. Do not manufacture a fit.")
        ap("")

    if criteria:
        for i, crit in enumerate(criteria, 1):
            respond_to(crit, i)
    else:
        ap("> ⚠️ This ad only says a statement addressing the selection criteria "
           "is required — the criteria themselves are in the **Position "
           "Description (PD) PDF** linked from the vacancy. Download the PD, "
           "paste each criterion into a slot below, then answer it using the "
           "evidence bank at the bottom.")
        ap("")
        for i in range(1, 4):
            ap(f"## Criterion {i}: [paste the criterion from the PD here]")
            ap("")
            ap("> ✍️ 2–3 sentences (45–80 words). Answer only this criterion, "
               "and only with facts.")
            ap("")
        ap("---")
        ap("")
        ap("## Evidence bank — your strongest capabilities for this job "
           "(draw on these, then delete this section before submitting)")
        ap("")
        ranked = sorted(caps, key=lambda c: _relevance(c, spec, weights), reverse=True)
        for c in ranked[:6]:
            lv = levels.get(c["id"], {})
            ap(f"- **{c['statement']}** (L{lv.get('level', '?')}/5)")
            lines.extend(_evidence_lines(conn, c))
        ap("")

    ap("---")
    ap("")
    ap("> ✍️ Before submitting: is every criterion answered (skipping one can "
       "disqualify you)? Is each within 2–3 sentences? Any empty phrases, "
       "preamble or summing-up left to cut?")
    return "\n".join(lines)
