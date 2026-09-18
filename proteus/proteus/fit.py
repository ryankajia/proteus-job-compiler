"""L2 Matching — fit_score v1. A RANKER, not a filter.

fit_score blends skill coverage, seniority fit, and visa feasibility. Every job
gets a score; visa-infeasible roles are pushed down and greyed, never dropped
(the visa-feasibility signal must never be removed — charter invariant #3).
"""
from __future__ import annotations

import sqlite3

from . import calibration

# Requirements that put a role out of reach for anyone without citizenship or
# permanent residency. Australian ads phrase this a dozen ways, and defence and
# federal roles gate on a clearance that needs citizenship.
#
# This is the right default for a visa holder. If you ARE a citizen or PR,
# empty this tuple — nothing else needs changing.
VISA_BLOCKERS = (
    "citizenship required", "must be an australian citizen", "australian citizen only",
    "australian citizenship", "pr/citizenship", "permanent resident only",
    "citizens or permanent residents", "security clearance", "baseline clearance",
    "nv1", "nv2", "negative vetting", "agsva",
)


def _skill_coverage(conn: sqlite3.Connection, spec: dict) -> tuple[float, list[dict]]:
    levels = calibration.get_levels(conn)
    reqs = spec.get("required_skills", []) or []
    if not reqs:
        return 0.5, []
    detail = []
    covered = 0.0
    for skill in reqs:
        cap_id, user_level = calibration._best_match(skill, levels)
        if cap_id:
            covered += min(1.0, user_level / 4.0)  # level 4 == full credit
            detail.append({"skill": skill, "capability": cap_id, "user_level": user_level})
        else:
            detail.append({"skill": skill, "capability": None, "user_level": None})
    return covered / len(reqs), detail


def _visa_feasible(spec: dict) -> bool:
    v = (spec.get("visa_requirement") or "").lower()
    if spec.get("security_clearance"):
        return False
    return not any(b.lower() in v for b in VISA_BLOCKERS)


def score(conn: sqlite3.Connection, spec: dict) -> dict:
    coverage, detail = _skill_coverage(conn, spec)
    stretch = calibration.job_stretch(conn, spec)
    feasible = _visa_feasible(spec)

    # Seniority fit: closest to the best target zone (stretch 0.5-1.0) scores highest.
    s = stretch["overall"]
    if 0.5 <= s <= 1.0:
        seniority_fit = 1.0
    elif s < 0.5:
        seniority_fit = max(0.5, 1.0 - (0.5 - s))      # over-qualified: still fine
    else:
        seniority_fit = max(0.0, 1.0 - (s - 1.0) * 0.4)  # stretch decays the score

    base = 0.55 * coverage + 0.30 * seniority_fit + 0.15 * (1.0 if feasible else 0.0)
    fit = round(base * (1.0 if feasible else 0.35), 4)  # infeasible → greyed & sunk

    zone = ("Below your level (safe)" if s < 0 else
            "Best target zone (reach + grow)" if 0.5 <= s <= 1.0 else
            "Comfortable" if 0 <= s < 0.5 else
            "High risk, high reward (a free skills test)" if s > 2 else "Stretch")

    return {
        "fit_score": fit, "coverage": round(coverage, 3),
        "stretch_score": s, "seniority_fit": round(seniority_fit, 3),
        "visa_feasible": feasible, "zone": zone, "skill_detail": detail,
    }


def rank(conn: sqlite3.Connection, specs: list[dict]) -> list[dict]:
    """Score all specs and return them sorted best-first. Nothing is dropped."""
    scored = []
    for spec in specs:
        result = score(conn, spec)
        scored.append({**spec, **result})
    scored.sort(key=lambda x: (x["visa_feasible"], x["fit_score"]), reverse=True)
    return scored
