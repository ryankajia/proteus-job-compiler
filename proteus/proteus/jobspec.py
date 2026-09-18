"""L1 JobSpec — parse a pasted JD into a normalized JobSpec.

Deterministic parser (no API key needed). A richer LLM parse is available via
llm.parse_jd() when a key is present; both produce the same JobSpec shape.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
import sqlite3
from pathlib import Path

from . import db

# Skill vocabulary the ranker cares about — matched case-insensitively.
SKILL_VOCAB = [
    "fMRI", "MRI", "EEG", "MEG", "tDCS", "TMS", "eye-tracking", "psychophysics",
    "MATLAB", "Psychtoolbox", "Python", "R", "Julia", "statistics", "linear mixed",
    "mixed models", "machine learning", "deep learning", "LLM", "multi-agent",
    "data analysis", "data pipeline", "SQL", "FastAPI", "React", "JavaScript",
    "TypeScript", "full-stack", "REST", "API", "cloud", "AWS", "Docker",
    "Kubernetes", "K8s", "scientific writing", "grant", "project management",
    "teaching", "tutoring", "mentoring", "vision", "perception", "consciousness",
    "neuroscience", "cognitive", "clinical", "patients", "experimental design",
    "signal processing", "computational", "modelling", "visualization", "pandas",
    "numpy", "scipy", "git", "prompt engineering",
]

SENIORITY_PATTERNS = [
    (r"\bprofessor\b|\bchair\b", "professor"),
    (r"\bprincipal\b|\bstaff\b", "principal"),
    (r"\bpost[- ]?doc(toral)?\b|\bresearch fellow\b", "postdoc"),
    (r"\blead\b|\bhead of\b", "lead"),
    (r"\bsenior\b|\bsnr\b", "senior"),
    (r"\bjunior\b|\bassistant\b|\bgraduate\b|\bentry[- ]level\b", "junior"),
    (r"\bresearch associate\b", "research associate"),
    (r"\bmid[- ]?level\b", "mid"),
]

VISA_PATTERNS = [
    (r"citizen(ship)?\b.*(required|only|must)", "citizenship required"),
    (r"security clearance|baseline clearance|nv1|nv2|agsva", "security clearance"),
    (r"permanent resident|\bPR\b\b|australian citizen", "PR/citizenship"),
    (r"work rights|full working rights|eligible to work", "work rights required"),
    (r"visa sponsorship|will sponsor", "sponsorship available"),
]


def parse(raw: str, source: str = "manual", use_llm: bool = False) -> dict:
    if use_llm:
        from . import llm
        return llm.parse_jd(raw, source=source)
    return _heuristic_parse(raw, source)



# --------------------------------------------------- closing dates ----
# One canonical parser, used both at intake (to store closes_at) and by the
# board (to sort and colour). Deliberately forgiving about format and
# deliberately silent on failure: guessing a closing date is worse than
# admitting there isn't one, because a wrong date either hides a live job or
# makes a dead one look open.

_MONTH_NAMES = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def parse_closing_date(text: str) -> _dt.date | None:
    """A real date from free text, or None. Never raises."""
    t = (text or "").strip()
    if not t:
        return None

    m = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})", t)              # 2026-09-15
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", t)  # 15 Sep 2026
    if m:
        mon = _month_number(m.group(2))
        if mon:
            return _safe_date(int(m.group(3)), mon, int(m.group(1)))

    m = re.match(r"\s*([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", t)  # Sep 15, 2026
    if m:
        mon = _month_number(m.group(1))
        if mon:
            return _safe_date(int(m.group(3)), mon, int(m.group(2)))

    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", t)               # 15/09/2026 (AU order)
    if m:
        return _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))

    return None


def _month_number(name: str) -> int | None:
    key = name[:3].lower()
    return _MONTH_NAMES.index(key) + 1 if key in _MONTH_NAMES else None


def _safe_date(year: int, month: int, day: int) -> _dt.date | None:
    """31 February is a regex match but not a date."""
    try:
        return _dt.date(year, month, day)
    except ValueError:
        return None


def extract_closing_date(text: str, deadline_field: str = "") -> str:
    """ISO closing date for storage, or "" when the ad does not give one.

    Checked in order of trustworthiness: a date typed into the add form, then
    the "Closes:" line the ingest writes, then anything date-shaped next to a
    deadline word.
    """
    d = parse_closing_date(deadline_field)
    if d:
        return d.isoformat()
    m = re.search(r"^\s*Closes?:\s*(.+)$", text or "", re.M | re.I)
    if m:
        d = parse_closing_date(m.group(1).split("(")[0].strip())
        if d:
            return d.isoformat()
    m = re.search(r"(?:deadline|closes?|closing date|apply by|due)\s*[:\-]?\s*"
                  r"([0-9]{1,2}\s+[A-Za-z]{3,9}\.?,?\s+[0-9]{4}"
                  r"|[A-Za-z]{3,9}\.?\s+[0-9]{1,2},?\s+[0-9]{4}"
                  r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}"
                  r"|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4})", text or "", re.I)
    if m:
        d = parse_closing_date(m.group(1))
        if d:
            return d.isoformat()
    return ""


def _heuristic_parse(raw: str, source: str) -> dict:
    text = raw.strip()
    low = text.lower()

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # A Position Description is a form, not an ad: its first line is a field
    # label ("College/Division: ..."), not the job. Australian PDs carry an
    # explicit "Position Title:" field — prefer it over the first line.
    title = ""
    m_title = re.search(r"^\s*Position\s+Title\s*[:\t]\s*(.+)$", text, re.I | re.M)
    if m_title:
        title = m_title.group(1).strip().strip("|").strip()
    if not title and lines:
        # Otherwise skip leading form-label lines before falling back.
        for ln in lines[:6]:
            if re.match(r"^(college|division|faculty|school|centre|department|unit|"
                        r"classification|position no|responsible to|delegation|"
                        r"number of positions|reference no)\b", ln, re.I):
                continue
            title = ln
            break
        title = title or lines[0]
    title = title[:120]

    org = ""
    m = re.search(r"(?:at|@|company|organisation|organization|employer)[:\s]+([A-Z][\w&.,'\- ]{2,60})", text)
    if m:
        org = m.group(1).strip()

    seniority = ""
    for pat, label in SENIORITY_PATTERNS:
        if re.search(pat, low):
            seniority = label
            break

    skills = []
    for kw in SKILL_VOCAB:
        if re.search(r"(?<![a-z])" + re.escape(kw.lower()) + r"(?![a-z])", low):
            skills.append(kw)
    # De-dup while preserving order.
    skills = list(dict.fromkeys(skills))

    visa = ""
    clearance = 0
    for pat, label in VISA_PATTERNS:
        if re.search(pat, low):
            visa = label
            if "clearance" in label:
                clearance = 1
            break
    if re.search(r"security clearance|clearance", low):
        clearance = 1

    salary = ""
    m = re.search(r"(\$|AUD|A\$|USD|¥|RMB|£)\s?[\d,]+(?:\s?[-–to]+\s?(\$|AUD|A\$)?\s?[\d,]+)?(?:\s?(k|per annum|p\.?a\.?|/year|/hour|/hr))?", text, re.I)
    if m:
        salary = m.group(0).strip()

    deadline = ""
    m = re.search(r"(?:deadline|closes?|apply by|due)[:\s]+([0-9]{1,2}[/\- ][A-Za-z0-9]{1,9}[/\- ][0-9]{2,4}|[A-Za-z]+ [0-9]{1,2},? [0-9]{4})", text, re.I)
    if m:
        deadline = m.group(1).strip()

    nice = []
    m = re.search(r"(nice to have|desirable|preferred|bonus)[:\s]+(.{0,400})", low, re.S)
    if m:
        for kw in SKILL_VOCAB:
            if kw.lower() in m.group(2) and kw not in skills:
                nice.append(kw)

    return {
        "title": title, "org": org, "seniority": seniority,
        "required_skills": skills, "nice_to_have": nice,
        "visa_requirement": visa, "security_clearance": clearance,
        "salary": salary, "deadline": deadline,
        "closes_at": extract_closing_date(text, deadline), "apply_channel": "",
        "source": source, "raw_text": text,
    }


def hidden_sources() -> set[str]:
    """JD sources that are development data or superseded duplicates: demo ads in
    data/sample_jobs and anything moved to data/real_jobs/archived. They stay in
    the DB but must not appear on the board or in the application log."""
    root = Path(__file__).resolve().parent.parent / "data"
    return ({p.stem for p in (root / "sample_jobs").glob("*.txt")}
            | {p.stem for p in (root / "real_jobs" / "archived").glob("*.txt")})


def save(conn: sqlite3.Connection, spec: dict) -> str:
    job_id = spec.get("id")
    if not job_id and spec.get("source"):
        # The source file IS the job's identity. Without this, correcting a
        # typo in a saved JD would fork a duplicate job (the id hashes the
        # ad text) and orphan its pipeline status.
        row = conn.execute("SELECT id FROM jobspecs WHERE source=? ORDER BY created_at",
                           (spec["source"],)).fetchone()
        if row:
            job_id = row["id"]
    job_id = job_id or "job_" + hashlib.sha1(
        (spec.get("title", "") + spec.get("org", "") + spec.get("raw_text", "")[:200]).encode()
    ).hexdigest()[:12]
    now = _dt.datetime.now().isoformat(timespec="seconds")
    # A deadline researched by hand (not present in the JD text) must survive
    # re-parsing: keep the stored one whenever the parse found none.
    if not spec.get("deadline") or not spec.get("closes_at"):
        row = conn.execute(
            "SELECT deadline, closes_at FROM jobspecs WHERE id=?", (job_id,)).fetchone()
        if row:
            if not spec.get("deadline") and row["deadline"]:
                spec["deadline"] = row["deadline"]
            # A closing date typed in by hand must survive re-parsing too —
            # most PDFs do not state one, so it is often the only copy.
            if not spec.get("closes_at") and row["closes_at"]:
                spec["closes_at"] = row["closes_at"]
    # UPSERT, never INSERT OR REPLACE: REPLACE deletes the old row first, and
    # pipeline/events reference jobspecs ON DELETE CASCADE — so re-saving a JD
    # used to silently wipe its application status and entire event history.
    conn.execute(
        "INSERT INTO jobspecs(id, title, org, seniority, required_skills, nice_to_have, "
        "visa_requirement, security_clearance, salary, deadline, closes_at, apply_channel, "
        "source, raw_text, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "title=excluded.title, org=excluded.org, seniority=excluded.seniority, "
        "required_skills=excluded.required_skills, nice_to_have=excluded.nice_to_have, "
        "visa_requirement=excluded.visa_requirement, security_clearance=excluded.security_clearance, "
        "salary=excluded.salary, deadline=excluded.deadline, closes_at=excluded.closes_at, "
        "apply_channel=excluded.apply_channel, "
        "source=excluded.source, raw_text=excluded.raw_text",
        (
            job_id, spec.get("title"), spec.get("org"), spec.get("seniority"),
            db.dumps(spec.get("required_skills", [])), db.dumps(spec.get("nice_to_have", [])),
            spec.get("visa_requirement"), int(spec.get("security_clearance", 0)),
            spec.get("salary"), spec.get("deadline"), spec.get("closes_at") or "",
            spec.get("apply_channel"),
            spec.get("source"), spec.get("raw_text"), now,
        ),
    )
    conn.commit()
    spec["id"] = job_id
    return job_id
