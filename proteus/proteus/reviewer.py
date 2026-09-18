"""T6 Reviewer simulator (lite) — deterministic pre-submission checks.

Simulates the first-pass screener: does the application package cover the JD's
requirements, state work rights, handle referees, and beat the deadline? Each
finding has a severity: 'block' (fix before sending), 'warn', or 'info'.
"""
from __future__ import annotations

import datetime as _dt
import re
import sqlite3

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}


def _parse_deadline(text: str) -> _dt.date | None:
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", text or "")
    if not m:
        return None
    month = MONTHS.get(m.group(2).lower()[:20])
    if not month:
        # try 3-letter prefix
        for name, num in MONTHS.items():
            if name.startswith(m.group(2).lower()[:3]):
                month = num
                break
    if not month:
        return None
    try:
        return _dt.date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def review(conn: sqlite3.Connection, spec: dict, resume_md: str,
           cover_md: str | None = None) -> list[dict]:
    findings: list[dict] = []
    raw = (spec.get("raw_text") or "").lower()
    body = resume_md + ("\n" + cover_md if cover_md else "")
    body_low = body.lower()

    # 1. Deadline check.
    deadline = _parse_deadline(spec.get("deadline") or "") or _parse_deadline(raw[:2000])
    if deadline:
        today = _dt.date.today()
        if deadline < today:
            findings.append({"severity": "block", "check": "deadline",
                             "msg": f"Deadline {deadline.isoformat()} has PASSED ({(today - deadline).days} days ago)."})
        elif (deadline - today).days <= 7:
            findings.append({"severity": "warn", "check": "deadline",
                             "msg": f"Deadline {deadline.isoformat()} is in {(deadline - today).days} day(s) — submit soon."})

    # 2. Required-skill coverage in the package text.
    missing = [s for s in spec.get("required_skills", []) if s.lower() not in body_low]
    if missing:
        findings.append({"severity": "warn", "check": "keyword-coverage",
                         "msg": "JD keywords absent from the package: " + ", ".join(missing)})

    # 3. Work rights stated (Australian employers screen on this immediately).
    #    Match on the words any status would use, not one particular visa.
    _RIGHTS = ("work rights", "citizen", "permanent resident", "visa", "subclass")
    if not any(w in body_low for w in _RIGHTS):
        findings.append({"severity": "block", "check": "work-rights",
                         "msg": "Package does not state your right to work in Australia — "
                                "add a line giving your citizenship, residency or visa."})

    # 4. Referee requirement.
    if "refere" in raw:
        if "refere" not in body_low:
            findings.append({"severity": "warn", "check": "referees",
                             "msg": "JD asks for referees but the package never mentions them — "
                                    "add referee details or an 'available on request' line, and "
                                    "line up your referees' consent."})
    else:
        findings.append({"severity": "info", "check": "referees",
                         "msg": "JD does not request referee details at application."})

    # 5. Selection criteria statement (APS / university pattern).
    if "selection criteria" in raw:
        findings.append({"severity": "block", "check": "selection-criteria",
                         "msg": "This employer requires a separate statement addressing the "
                                "selection criteria — applications without one may not be considered."})

    # 6. Citizenship / clearance blockers.
    if not spec.get("visa_feasible", True) or spec.get("security_clearance"):
        findings.append({"severity": "block", "check": "visa",
                         "msg": "Role requires citizenship, permanent residency or a "
                                "security clearance — likely ineligible on a temporary visa."})
    if re.search(r"must hold australian citizen", raw):
        findings.append({"severity": "block", "check": "visa",
                         "msg": "JD explicitly requires Australian citizenship."})

    # 7. Length sanity.
    words = len(resume_md.split())
    if words > 900:
        findings.append({"severity": "warn", "check": "length",
                         "msg": f"Résumé is {words} words — consider trimming below ~2 pages."})
    return findings


def render_md(spec: dict, findings: list[dict]) -> str:
    icon = {"block": "⛔", "warn": "⚠️", "info": "ℹ️"}
    lines = [f"# Reviewer report — {spec.get('title', 'role')}", ""]
    blocks = [f for f in findings if f["severity"] == "block"]
    lines.append(f"**Verdict: {'NOT READY — fix blockers first' if blocks else 'ready to submit (review warnings)'}**")
    lines.append("")
    for f in findings:
        lines.append(f"- {icon[f['severity']]} **{f['check']}** — {f['msg']}")
    if not findings:
        lines.append("- ✅ No findings — package looks clean.")
    return "\n".join(lines)
