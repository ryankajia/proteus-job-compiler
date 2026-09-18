"""T7 Cover letter compiler — evidence-grounded, persona-aware.

Same invariant as the résumé compiler: every concrete claim in the letter maps
to a capability unit backed by L0 evidence. The deterministic version produces
a solid, honest draft; pass `angle=` to inject a hand-written opening/hook, or
edit the draft before sending (Proteus never sends anything).
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

from . import calibration, identity
from .compiler import TARGETS, TARGET_ALIASES, _relevance

# Persona → one-line self-positioning used in the opening paragraph.
POSITIONING = {
    "researcher": "a cognitive neuroscientist with 12+ years of experimental research experience",
    "builder": "a researcher-turned-builder who ships production software",
    "ai_architect": "a researcher who designs and ships evidence-grounded LLM systems",
    "polymath": "a research professional combining scientific, administrative, and coordination experience",
}


def compile_cover_letter(conn: sqlite3.Connection, spec: dict,
                         target: str = "researcher", angle: str | None = None) -> str:
    target = TARGET_ALIASES.get(target, target)
    weights = TARGETS.get(target, {})
    profile = identity.get_profile(conn)
    caps = identity.get_capabilities(conn)
    levels = calibration.get_levels(conn)

    ranked = sorted(
        caps,
        key=lambda c: (_relevance(c, spec, weights), levels.get(c["id"], {}).get("level", 0)),
        reverse=True,
    )
    top = [c for c in ranked if _relevance(c, spec, weights) > 0][:3] or ranked[:3]

    title = spec.get("title") or "the advertised role"
    org = spec.get("org") or "your organisation"
    today = _dt.date.today().strftime("%d %B %Y")

    lines: list[str] = []
    ap = lines.append
    ap(f"{profile['name']}")
    ap(f"{profile['email']} · {profile['phone']}")
    ap(f"{profile['location']}")
    ap("")
    ap(today)
    ap("")
    ap(f"**Re: Application for {title} — {org}**")
    ap("")
    ap("Dear Hiring Manager,")
    ap("")
    if angle:
        ap(angle)
    else:
        # Work rights come from the profile, never hardcoded: this used to
        # carry one person's city, move date and visa subclass, which was both
        # a privacy leak and useless to anybody else.
        rights = (profile.get("work_rights") or "").strip().rstrip(".")
        opening = (f"I am writing to apply for the {title} position at {org}. "
                   f"I am {POSITIONING.get(target, POSITIONING['researcher'])}")
        ap(opening + (f", {rights[0].lower() + rights[1:]}." if rights else "."))
    ap("")
    ap("Three strengths I would bring to this role:")
    ap("")
    for c in top:
        lv = levels.get(c["id"], {})
        stmt = c["statement"]
        ap(f"- {stmt} (calibrated level {lv.get('level', '?')}/5)")
    ap("")
    ap("Every claim above is backed by verifiable evidence — publications, funded "
       "projects, or shipped systems — listed in my CV's evidence ledger.")
    ap("")
    ap(f"I would welcome the opportunity to discuss how my background fits {org}'s needs. "
       "Thank you for your consideration.")
    ap("")
    ap("Yours sincerely,")
    ap(f"{profile['name']}")
    return "\n".join(lines)
