"""Calibration (T1.5) — evidence-driven level assessment, stretch_score, Gap Report.

Level is NEVER user-supplied. It is assessed from evidence, with a written
rationale, on a 1-5 scale:

  1  Exposure — has encountered the skill, needs guidance for anything real.
  2  Assisted — can deliver within a framework/protocol with support.
  3  Independent — delivers standard work in the domain unsupervised.
  4  Advanced — deep expertise; handles novel/hard cases; mentors others.
  5  Authority — defines direction; recognised contribution to the field.

Calibration is bidirectional: it guards against both over-reach and the
systematic self-underestimation the design doc calls out. Assessments below are
analyst-seeded from the CV evidence; run with use_llm=True to re-derive them
live via the Claude API (see llm.py).
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
from pathlib import Path

from . import identity

RUBRIC = """1 Exposure / 2 Assisted / 3 Independent / 4 Advanced / 5 Authority.
Assess from evidence only. Give a level (fractional allowed) and one sentence of rationale."""

# Analyst-seeded, evidence-grounded assessments (source='analyst').
# Each: level + one-sentence rationale tied to the capability's evidence.
# Calibration levels live in data/calibration_levels.json, not in this file.
# They quote real career specifics — grant numbers, budgets, head counts — so
# they are personal data, not source code, and are git-ignored accordingly.
# Copy data/calibration_levels.template.json to get started.
_LEVELS_FILE = Path(__file__).resolve().parent.parent / "data" / "calibration_levels.json"


def _load_assessments() -> dict[str, tuple[float, str]]:
    if not _LEVELS_FILE.is_file():
        return {}
    raw = json.loads(_LEVELS_FILE.read_text(encoding="utf-8"))
    return {k: (float(v["level"]), str(v["rationale"]))
            for k, v in raw.items() if not k.startswith("_")}


ANALYST_ASSESSMENTS: dict[str, tuple[float, str]] = _load_assessments()


def calibrate(conn: sqlite3.Connection, use_llm: bool = False) -> int:
    """Assess a level + rationale for every capability unit. Returns count."""
    now = _dt.date.today().isoformat()
    caps = identity.get_capabilities(conn)
    n = 0
    for cap in caps:
        if use_llm:
            from . import llm  # optional dependency
            evidence = [identity.get_evidence(conn, e) for e in cap["evidence"]]
            level, rationale = llm.assess_level(cap, [e for e in evidence if e])
            source = "llm"
        else:
            level, rationale = ANALYST_ASSESSMENTS.get(
                cap["id"], (2.0, "No seeded assessment; defaulting to Assisted pending review."))
            source = "analyst"
        conn.execute(
            "INSERT OR REPLACE INTO calibration(capability_id, level, rationale, source, assessed_at) "
            "VALUES(?,?,?,?,?)",
            (cap["id"], float(level), rationale, source, now),
        )
        n += 1
    conn.commit()
    return n


def get_levels(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT c.capability_id, c.level, c.rationale, c.source, u.domain, u.statement, u.keywords "
        "FROM calibration c JOIN capability_units u ON u.id = c.capability_id"
    ).fetchall()
    import json
    out = {}
    for r in rows:
        out[r["capability_id"]] = {
            "level": r["level"], "rationale": r["rationale"], "source": r["source"],
            "domain": r["domain"], "statement": r["statement"], "keywords": json.loads(r["keywords"]),
        }
    return out


# --- stretch_score + Gap Report -------------------------------------------

SENIORITY_LEVEL = {
    "intern": 2.0, "assistant": 2.5, "junior": 2.5, "associate": 3.0,
    "": 3.0, None: 3.0, "mid": 3.5, "senior": 4.0, "lead": 4.5,
    "principal": 4.5, "professor": 5.0, "postdoc": 3.5, "post-doc": 3.5,
    "research fellow": 3.5, "research associate": 3.0,
}


def _required_level(seniority: str | None) -> float:
    if not seniority:
        return 3.0
    key = seniority.strip().lower()
    for k, v in SENIORITY_LEVEL.items():
        if k and k in key:
            return v
    return 3.0


def _best_match(skill: str, levels: dict[str, dict]) -> tuple[str | None, float]:
    """Match a required skill string to the strongest capability covering it."""
    s = skill.lower()
    best_id, best_score, best_level = None, 0.0, 0.0
    for cid, info in levels.items():
        hay = (info["statement"] + " " + " ".join(info["keywords"])).lower()
        score = 0.0
        for kw in info["keywords"]:
            k = kw.lower()
            if k in s or s in k:
                score = max(score, 1.0)
            elif any(tok in hay for tok in s.split() if len(tok) > 3):
                score = max(score, 0.4)
        if s in hay and score < 0.6:
            score = 0.6
        if score > best_score:
            best_id, best_score, best_level = cid, score, info["level"]
    if best_score < 0.4:
        return None, 0.0
    return best_id, best_level


def job_stretch(conn: sqlite3.Connection, jobspec: dict) -> dict:
    """Compute per-skill and overall stretch = required_level - user_level."""
    levels = get_levels(conn)
    req = _required_level(jobspec.get("seniority"))
    per_skill = []
    for skill in jobspec.get("required_skills", []):
        cap_id, user_level = _best_match(skill, levels)
        covered = cap_id is not None
        stretch = round(req - (user_level if covered else 0.0), 2)
        per_skill.append({
            "skill": skill, "required_level": req, "user_level": user_level if covered else None,
            "capability": cap_id, "covered": covered, "stretch": stretch,
        })
    if per_skill:
        overall = round(sum(p["stretch"] for p in per_skill) / len(per_skill), 2)
    else:
        overall = 0.0
    return {"overall": overall, "required_level": req, "per_skill": per_skill}


def gap_report(conn: sqlite3.Connection, jobspec: dict) -> dict:
    """Skills with no evidence or high stretch — the pre-interview cram list."""
    stretch = job_stretch(conn, jobspec)
    no_evidence = [p["skill"] for p in stretch["per_skill"] if not p["covered"]]
    high_stretch = [
        {"skill": p["skill"], "stretch": p["stretch"], "user_level": p["user_level"]}
        for p in stretch["per_skill"] if p["covered"] and p["stretch"] >= 1.5
    ]
    return {
        "overall_stretch": stretch["overall"],
        "required_level": stretch["required_level"],
        "no_evidence": no_evidence,
        "high_stretch": high_stretch,
        "per_skill": stretch["per_skill"],
    }
