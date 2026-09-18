"""Hard thresholds, checked when a job is added rather than before submitting.

`reviewer.py` runs just before submission. But the reasons an application is
screened out — citizenship, a security clearance, a professional registration,
a stated minimum number of years — are already decided the moment the ad is
read. Finding out at the end means the résumé, the cover letter and eight
selection-criterion answers were written for nothing.

So this runs at intake and says, in one line, why a job might not be worth
starting.

**A gate hit never removes a job.** It marks it and lifts it to the top of the
"to assess" pile. This follows the position already taken in `fit.py`, where a
visa-blocked role is scored down and greyed but never hidden: the system is not
entitled to decide that something is impossible. Ads overstate requirements,
"registration required" often means "or eligible for", and a panel that likes a
candidate finds a way. The judgement stays with the person.

Patterns live in `data/gate_rules.json` and are meant to be extended by hand.
They are re-read on each call, so editing the file takes effect immediately —
a verdict stored at intake would go stale the moment a rule changed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RULES_FILE = Path(__file__).resolve().parent.parent / "data" / "gate_rules.json"

# Only the first few hits are worth showing; a wall of red chips is the same as
# no signal at all.
MAX_FINDINGS = 4


def load_rules(path: Path | None = None) -> dict:
    """Read the rule file. A missing or broken file disables the gate rather
    than breaking the board."""
    try:
        raw = json.loads((path or RULES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# Ads state requirements they do NOT have as often as ones they do:
# "no security clearance required", "this role does not require a Security
# Clearance". Both were flagged on the first real run, and two false alarms out
# of two is how a warning becomes something you learn to ignore.
_NEGATION = re.compile(
    r"\b(?:no|not|without|never|neither|nor|exempt from|free from|"
    r"does\s+not\s+require|do\s+not\s+require|is\s+not\s+required|"
    r"are\s+not\s+required|not\s+necessary|unnecessary)\b", re.I)

# How far back to look for that negation. One clause, not one paragraph.
_NEGATION_WINDOW = 45


def _is_negated(text: str, m: re.Match) -> bool:
    """True when the phrase is being ruled out rather than required."""
    before = text[max(0, m.start() - _NEGATION_WINDOW):m.start()]
    # Do not read across a sentence boundary: "Citizenship is not relevant.
    # A security clearance is required." negates only the first clause.
    before = re.split(r"[.;!?\n]", before)[-1]
    if _NEGATION.search(before):
        return True
    # "... security clearance is not required" — negation follows the phrase.
    after = re.split(r"[.;!?\n]", text[m.end():m.end() + _NEGATION_WINDOW])[0]
    return bool(re.match(r"\s*(?:is|are|will)?\s*not\s+(?:be\s+)?"
                         r"(?:required|necessary|needed)", after, re.I))


def _first_match(text: str, patterns: list[str]) -> re.Match | None:
    for p in patterns:
        try:
            for m in re.finditer(p, text, re.I):
                if not _is_negated(text, m):
                    return m
        except re.error:          # a hand-written rule with a typo in it
            continue
    return None


def _quote(text: str, m: re.Match, width: int = 70) -> str:
    """The phrase in the ad that triggered this, so the claim can be checked."""
    start = max(0, m.start() - 12)
    end = min(len(text), m.end() + 12)
    snippet = " ".join(text[start:end].split())
    return (snippet[:width] + "…") if len(snippet) > width else snippet


def gate(text: str, rules: dict | None = None) -> list[dict]:
    """Hard-threshold findings for one ad. Empty list means nothing detected.

    Each finding: {check, label, why, evidence, detail}
    """
    rules = load_rules() if rules is None else rules
    body = text or ""
    findings: list[dict] = []

    for key in ("citizenship", "clearance", "registration"):
        rule = rules.get(key)
        if not rule:
            continue
        m = _first_match(body, rule.get("patterns", []))
        if m:
            findings.append({"check": key, "label": rule["label"],
                             "why": rule["why"], "evidence": _quote(body, m),
                             "detail": ""})

    rule = rules.get("experience_years")
    if rule:
        m = _first_match(body, rule.get("patterns", []))
        if m:
            years = next((g for g in m.groups() if g and g.isdigit()), "")
            role = next((g for g in m.groups() if g and not g.isdigit()), "")
            # The capture runs on past the title into the sentence ("... as a
            # Senior Project Officer is essential"). Cut at the first word that
            # cannot be part of a job title.
            role = (role or "").strip(" .,;")
            role = re.split(r"\s+(?:is|are|was|will|would|and|or|with|within|for|to|in|at|on)\b",
                            role, maxsplit=1)[0].strip(" .,;-")
            detail = f"{years}+ years as {role}" if years and role else ""
            findings.append({"check": "experience_years", "label": rule["label"],
                             "why": rule["why"], "evidence": _quote(body, m),
                             "detail": detail})

    rule = rules.get("location")
    if rule and rule.get("restricted_patterns"):
        # A role that can be done remotely is not gated on where it is based.
        if not _first_match(body, rule.get("remote_patterns", [])):
            m = _first_match(body, rule["restricted_patterns"])
            if m:
                findings.append({"check": "location", "label": rule["label"],
                                 "why": rule["why"], "evidence": _quote(body, m),
                                 "detail": ""})

    return findings[:MAX_FINDINGS]


def summary(findings: list[dict]) -> str:
    """One line for the board: what might stop this application."""
    if not findings:
        return ""
    parts = [f["detail"] or f["label"] for f in findings]
    return "May not be eligible: " + "; ".join(parts)
