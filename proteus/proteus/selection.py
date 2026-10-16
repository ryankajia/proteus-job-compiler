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
# Instruction lines tolerated between the heading and the first item. Three was
# too few: one real PD has five ("Your suitability ... will be assessed ... To
# apply, write a two-page supporting statement ... by addressing ...", wrapped
# over four lines, plus a "Skills" label) and came out with no criteria at all.
# Still bounded, so prose that merely mentions "selection criteria" cannot
# reach a bullet list far below and adopt it.
_MAX_LEAD_IN = 10

# Below this a "match" is one or two incidental shared words, not evidence.
_MIN_CRIT_SCORE = 1.0


# ------------------------------------------------- how the PD wants a reply ----
# Two different documents are both loosely called "addressing the selection
# criteria", and they are written differently:
#
#   itemised   a heading per criterion, a short answer under each. Assessors
#              score the criteria one at a time, so length follows the number of
#              criteria and every criterion needs its own answer.
#   narrative  a single continuous "supporting statement / pitch", often capped
#              at a page count. Assessors read the whole piece and then check
#              that every criterion was covered, so one strong example can (and
#              should) answer several criteria at once.
#
# Guidance for one is bad advice for the other: nine separate answers squeezed
# into two pages read as a list, and a flowing pitch handed to a panel that
# scores line by line leaves criteria unanswered.
#
# The style comes from the PD's own instructions, not from the employer's name.
# In practice the PD often says nothing at all (the wording lives on the advert
# page), so itemised is the default and narrative needs an explicit signal.

WORDS_PER_PAGE = 425          # a page of 11pt prose with headings; an estimate
_DEFAULT_PAGES = 2            # assumed when a narrative PD names no limit
_MAX_EXAMPLES = 4             # a pitch that tells more than four stories has none

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})"

_NARRATIVE_RE = re.compile(
    r"\bpitch\b"
    r"|\bsupporting statement\b"
    r"|\b" + _NUM + r"[- ]pages?\b[^.]{0,40}\b(?:statement|response|application|pitch|letter)\b"
    r"|\b(?:statement|response|application|pitch|letter)\b[^.]{0,40}\b" + _NUM + r"[- ]pages?\b",
    re.I)

# Wording that asks for a reply criterion by criterion, even if a page limit
# is also given ("a two-page statement addressing each criterion").
_ITEMISED_RE = re.compile(
    r"\b(?:each|every)\s+(?:of\s+the\s+)?(?:selection\s+)?criteri(?:on|a)\b"
    r"|\bcriteri(?:on|a)\s+(?:by|one\s+by)\s+(?:criteri(?:on|a)|one)\b"
    r"|\b(?:separately|individually|point[- ]by[- ]point)\b",
    re.I)

_PAGES_RE = re.compile(
    r"(?:no\s+more\s+than|maximum\s+of|max\.?|up\s+to|limit(?:ed)?\s+to|not\s+exceed(?:ing)?)?"
    r"\s*(" + _NUM + r")[- ]pages?\b", re.I)
_WORDS_RE = re.compile(
    r"(?:no\s+more\s+than|maximum\s+of|max\.?|up\s+to|limit(?:ed)?\s+to|not\s+exceed(?:ing)?)?"
    r"\s*(\d{3,4})[- ]words?\b", re.I)
_OVERRIDE_RE = re.compile(r"^\s*Response\s+style\s*:\s*(narrative|itemi[sz]ed)\s*$", re.I | re.M)


def _to_int(token: str) -> int:
    t = token.lower()
    return _NUMBER_WORDS.get(t) or int(t)


def response_style(raw: str) -> dict:
    """How this PD wants the reply written.

    Returns {"style": "itemised" | "narrative", "pages": int | None,
             "words": int | None, "signal": str}. `signal` quotes what decided
    it, so a wrong call is visible rather than silent. A line
    `Response style: narrative|itemised` in the saved job text overrides the
    guess, since this is a heuristic and the person reading the ad knows best.
    """
    text = raw or ""
    pages = words = None
    m = _PAGES_RE.search(text)
    if m:
        pages = _to_int(m.group(1))
    m = _WORDS_RE.search(text)
    if m:
        words = int(m.group(1))

    forced = _OVERRIDE_RE.search(text)
    if forced:
        style = "itemised" if forced.group(1).lower().startswith("itemi") else "narrative"
        return {"style": style, "pages": pages, "words": words,
                "signal": f"set by hand: {forced.group(0).strip()}"}

    narrative = _NARRATIVE_RE.search(text)
    itemised = _ITEMISED_RE.search(text)
    if narrative and not itemised:
        a, b = max(0, narrative.start() - 40), min(len(text), narrative.end() + 40)
        return {"style": "narrative", "pages": pages, "words": words,
                "signal": "…" + " ".join(text[a:b].split()) + "…"}
    signal = ""
    if itemised:
        a, b = max(0, itemised.start() - 40), min(len(text), itemised.end() + 40)
        signal = "…" + " ".join(text[a:b].split()) + "…"
    return {"style": "itemised", "pages": pages, "words": words, "signal": signal}


def word_budget(style: dict) -> int | None:
    """Words available for the whole reply, when the PD gives a limit."""
    if style.get("words"):
        return style["words"]
    if style.get("pages"):
        return style["pages"] * WORDS_PER_PAGE
    return None


def document_name(raw: str) -> str:
    """What to call the document, in the PD's own terms, for checklists."""
    st = response_style(raw)
    if st["style"] == "narrative":
        n = st["pages"] or _DEFAULT_PAGES
        return f"{n}-page supporting statement / pitch"
    return "Statement addressing the selection criteria"



def _is_group_label(line: str) -> bool:
    """A short heading that groups the items below it ("Skills", "Behaviour",
    "Knowledge") rather than continuing the criterion above it."""
    t = line.strip().rstrip(":")
    words = t.split()
    return (1 <= len(words) <= 4 and t[:1].isupper()
            and not re.search(r"[.!?;,]$", t) and not _ITEM_RE.match(line))


def _next_is_item(lines: list[str], idx: int) -> bool:
    for nxt in lines[idx + 1:]:
        if nxt.strip():
            return bool(_ITEM_RE.match(nxt))
    return False


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
    last_line = ""         # last physical line of the current item
    for idx, ln in enumerate(lines):
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
            # Do NOT strip trailing punctuation here: if this line wraps, the
            # comma at its end belongs inside the criterion ("Word, Excel,\n
            # PowerPoint"). It is trimmed once, after the whole item is built.
            item = m.group(1).strip()
            # Document-upload instructions are not criteria.
            # "A current CV", "Statement addressing ...", "Cover letter". The
            # word "current" alone is not enough: it also opens real criteria
            # ("Current registration with AHPRA", "Current driver's licence"),
            # which used to be dropped without a word.
            kept_last = not re.match(
                r"(?:an? )?(?:current )?(?:statement\b|cv\b|curriculum\b|r[eé]sum[eé]\b|"
                r"cover letter\b|other documents?\b)",
                item, re.I)
            if kept_last:
                criteria.append(item)
            last_line = ln
        elif (criteria and not blank_before and _is_group_label(ln)
              and last_line.rstrip().endswith((".", ";", ":"))
              and _next_is_item(lines, idx)):
            # "Behaviour" / "Knowledge" directly under a finished item. Without
            # this they were glued onto that item's text, breaking the rule that
            # criteria are copied verbatim. Only when the previous item is
            # complete and an item follows, so a wrapped fragment such as
            # "PowerPoint and Outlook" is still treated as a continuation.
            pass
        elif criteria and not blank_before:
            # A wrapped criterion: PDFs and long bullets break mid-sentence.
            # Only ever extends the item directly above it, never restarts a list.
            if kept_last:
                criteria[-1] = criteria[-1] + " " + ln.strip()
            last_line = ln
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
    # A list separator left on the end of an item is not part of the criterion.
    return [c.rstrip(";,") for c in criteria]


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
    "organisation": ("coordination", "project management", "research administration"),
    "priorities": ("coordination", "project management"),
    "timelines": ("coordination", "project management", "grant management"),
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


def _top_capabilities(crit: str, caps: list[dict], levels: dict,
                      spec: dict, weights: dict) -> list[dict]:
    """The capabilities that genuinely answer one criterion: none, one, or two.

    Shared by the itemised and the narrative reply so both see the same
    evidence.
    """
    # Ties break on calibrated level: where two capabilities answer a
    # criterion equally well, lead with the one you are strongest at.
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
    return top


def _plan_examples(covers: dict[str, set[int]], levels: dict, n_criteria: int
                   ) -> tuple[list[tuple[str, list[int], list[int]]], list[int], list[int]]:
    """Choose the few examples that between them answer the most criteria.

    A pitch is a handful of stories, each carrying several criteria — the
    opposite of one answer per criterion. This is a greedy set cover: take the
    capability that answers the most criteria not yet covered, repeat.

    Returns (examples, unplaced, no_evidence) where each example is
    (capability_id, newly_covered, also_relevant); `unplaced` are criteria the
    ledger can answer but that did not fit in the example budget; `no_evidence`
    are criteria nothing in the ledger answers.
    """
    answerable = set().union(*covers.values()) if covers else set()
    no_evidence = [i for i in range(1, n_criteria + 1) if i not in answerable]
    remaining = set(answerable)
    examples: list[tuple[str, list[int], list[int]]] = []
    while remaining and len(examples) < _MAX_EXAMPLES:
        # Most new criteria first; then the stronger capability; then whichever
        # reaches the earliest criterion, so the examples read C1, C2, ... in
        # order. Iterating sorted ids makes any remaining tie deterministic.
        best = max(sorted(covers), key=lambda cid: (
            len(covers[cid] & remaining),
            levels.get(cid, {}).get("level", 0),
            -min(covers[cid] & remaining, default=10 ** 6)))
        gain = covers[best] & remaining
        if not gain:
            break
        examples.append((best, sorted(gain), sorted(covers[best] - gain)))
        remaining -= gain
    return examples, sorted(remaining), no_evidence


def _ids(nums: list[int]) -> str:
    return ", ".join(f"C{n}" for n in nums)


def _compile_narrative(conn: sqlite3.Connection, spec: dict, criteria: list[str],
                       style: dict, profile: dict, caps: list[dict], levels: dict,
                       weights: dict, bank: list[dict]) -> list[str]:
    """A worksheet for a continuous supporting statement / pitch.

    Not a reply per criterion: a coverage map (which of your strengths can carry
    which criteria), a short list of examples that between them cover the
    criteria, and a checklist to tick once the finished draft is read back.
    """
    lines: list[str] = []
    ap = lines.append
    title = spec.get("title") or "the advertised role"
    org = spec.get("org") or ""
    n = len(criteria)
    budget = word_budget(style)
    assumed = budget is None
    if assumed:
        budget = _DEFAULT_PAGES * WORDS_PER_PAGE
    limit_txt = (f"{style['pages']} pages" if style.get("pages") and not style.get("words")
                 else f"{style['words']} words" if style.get("words")
                 else f"no limit stated — assuming {_DEFAULT_PAGES} pages")

    ap(f"# Supporting statement — {title}" + (f", {org}" if org else ""))
    ap("")
    ap(f"{profile['name']} · {profile['email']} · {profile['phone']}")
    ap("")
    ap(f"> ✍️ **This PD asks for a continuous supporting statement / pitch "
       f"({limit_txt}), not a reply to each criterion.** Assessors read it "
       f"whole and then check that every one of the {n} criteria was covered. "
       f"So write a few strong examples that each answer several criteria — "
       f"not {n} separate answers squeezed into a page. Budget: about {budget} "
       f"words{' (an estimate: ~' + str(WORDS_PER_PAGE) + ' words a page)' if not style.get('words') else ''}. "
       f"Sticky-note lines are stripped from print and Word output.")
    if style.get("signal"):
        ap(f"> Detected from the PD: {style['signal']}  — if that is wrong, add a "
           f"line `Response style: itemised` to the saved job text.")
    ap("")

    ap("## Criteria to cover — word for word from the PD")
    ap("")
    for i, crit in enumerate(criteria, 1):
        ap(f"- [ ] **C{i}** {crit}")
    ap("")
    ap("> ✍️ Tick each box once your finished draft clearly covers that criterion. "
       "Cover it in the criterion's own words where you can — a panel checking a "
       "list is looking for the words, not for a number.")
    ap("")

    # Which capability answers which criterion.
    covers: dict[str, set[int]] = {}
    for i, crit in enumerate(criteria, 1):
        for c in _top_capabilities(crit, caps, levels, spec, weights):
            covers.setdefault(c["id"], set()).add(i)
    by_id = {c["id"]: c for c in caps}
    examples, unplaced, no_evidence = _plan_examples(covers, levels, n)

    ap("## Coverage map")
    ap("")
    ap("Which of your strengths can carry which criteria, from your evidence ledger:")
    ap("")
    for cid in sorted(covers, key=lambda k: (-len(covers[k]), k)):
        lv = levels.get(cid, {}).get("level", "?")
        ap(f"- **{by_id[cid]['statement'].split(':')[0]}** (L{lv}/5) — {_ids(sorted(covers[cid]))}")
    # A criterion no capability answers may still have earlier wording of yours
    # (an answer to a similar criterion in a past application). Saying "no
    # evidence" about those would be wrong, and would send you to invent a
    # sentence you already have.
    prior_for = {i: reuse.best_match(criteria[i - 1], bank) for i in no_evidence}
    has_prior = [i for i in no_evidence if prior_for[i]]
    no_evidence = [i for i in no_evidence if not prior_for[i]]
    if has_prior:
        ap(f"- **No capability matches, but you have earlier wording for:** {_ids(has_prior)} "
           f"(see the raw material at the end)")
    if no_evidence:
        ap(f"- **Nothing in your ledger answers directly:** {_ids(no_evidence)}")
    ap("")

    ap("## Suggested structure")
    ap("")
    ap("**Opening — 2 or 3 sentences.** Why this role and why you, in plain words.")
    ap("")
    ap(f"> ✍️ About {round(budget * 0.10 / 10) * 10} words. No life story: the role, "
       f"who you are professionally, and the one thing you most want the panel to remember.")
    ap("")
    per = max(80, round(budget * 0.85 / max(1, len(examples)) / 10) * 10)
    for k, (cid, new, also) in enumerate(examples, 1):
        c = by_id[cid]
        lv = levels.get(cid, {})
        ap(f"### Example {k} — {c['statement'].split(':')[0]} · answers {_ids(new)}")
        ap("")
        ap(f"**Claim:** {c['statement']} (calibrated L{lv.get('level', '?')}/5)")
        lines.extend(_evidence_lines(conn, c))
        if also:
            ap(f"  - Can also speak to: {_ids(also)}")
        ap("")
        ap(f"> ✍️ About {per} words. One real story: situation → task → what you did → "
           f"the measurable result. Choose the story that lets you use the wording of "
           f"{_ids(new)} naturally, and stop when it has done that.")
        ap("")
    ap("**Close — 1 or 2 sentences.** Availability and what you would do first.")
    ap("")

    if unplaced:
        ap(f"## Answerable, but not yet placed in an example: {_ids(unplaced)}")
        ap("")
        ap("> ✍️ Your ledger has evidence for these, but the examples above did not "
           "reach them. Weave each into the nearest example with a clause or a "
           "sentence — do not add a fifth story.")
        ap("")
    if no_evidence:
        ap(f"## Not covered by any evidence: {_ids(no_evidence)}")
        ap("")
        one = len(no_evidence) == 1
        ap(f"> ✍️ Nothing in the evidence ledger answers {'this one' if one else 'these'} "
           f"directly. Cover {'it' if one else 'each'} with one honest sentence: the "
           "closest transferable experience, or how you would pick it up. Do not "
           "manufacture a fit.")
        ap("")

    # Earlier wording she already approved, as raw material rather than as text.
    material = []
    for i, crit in enumerate(criteria, 1):
        prior = reuse.best_match(crit, bank)
        if prior:
            material.append((i, prior))
    if material:
        ap("## Raw material from your own earlier answers")
        ap("")
        ap("> ♻️ These answered a similar criterion in an earlier application. They are "
           "notes, not draft text — a pitch reads as one voice, so adapt the "
           "substance into your examples rather than pasting paragraphs.")
        ap("")
        for i, prior in material:
            ap(f"> **C{i}** — from {prior['package']} (similarity {prior['score']}):")
            for ln in prior["answer"].splitlines():
                if ln.strip():
                    ap(f"> {ln.strip()}")
            ap(">")
        ap("")

    ap("---")
    ap("")
    ap(f"> ✍️ Before submitting: read it once as a panel would. Is every one of the "
       f"{n} boxes above honestly ticked? Is it within about {budget} words? Does each "
       f"example end on a result? Any empty phrases, preamble or summing-up left to cut?")
    return lines


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

    # A PD that asks for a continuous pitch needs different advice from one
    # that is scored criterion by criterion. Only when criteria were actually
    # found: with none, the paste-them-in template below is the right output
    # whichever style it turns out to be.
    style = response_style(spec.get("raw_text") or "")
    if criteria and style["style"] == "narrative":
        return "\n".join(_compile_narrative(conn, spec, criteria, style, profile,
                                            caps, levels, weights, bank))

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
    if criteria and word_budget(style):
        budget = word_budget(style)
        ap(f"> ✍️ This PD limits the reply to about {budget} words in total, which "
           f"is roughly {budget // len(criteria)} words per criterion for {len(criteria)} "
           f"criteria — plan the lengths before you write.")
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
        top = _top_capabilities(crit, caps, levels, spec, weights)
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
