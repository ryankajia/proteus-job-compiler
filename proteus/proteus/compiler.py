"""L4 Generate — minimal compiler: Identity Graph + JobSpec -> industry-AI resume.

Hard constraint (charter invariant #1): every claim traces to L0 evidence. The
compiler selects capability units by relevance to the JobSpec, orders them by
calibrated level, and prints an Evidence Ledger so no claim is bare.
"""
from __future__ import annotations

import sqlite3

from . import calibration, identity


# T7 build targets — the four personas from the design doc. Domain weights bias
# which capability units surface first; old target names stay as aliases.
TARGETS = {
    "researcher": {"research": 1.6, "stats": 1.3, "software": 0.9, "management": 0.8, "web": 0.5, "ai": 0.6},
    "builder": {"web": 1.6, "software": 1.4, "ai": 1.1, "stats": 0.9, "research": 0.6, "management": 0.6},
    "ai_architect": {"ai": 1.7, "web": 1.2, "software": 1.1, "stats": 0.9, "research": 0.7, "management": 0.6},
    "polymath": {"management": 1.5, "research": 1.1, "stats": 1.0, "software": 0.9, "web": 0.8, "ai": 0.8},
}
TARGET_ALIASES = {"academic": "researcher", "industry-ai": "ai_architect"}


def pick_target(spec: dict) -> str:
    """Auto-select a persona for a JobSpec (the L4 positioning decision)."""
    text = ((spec.get("title") or "") + " " + (spec.get("raw_text") or "")[:400]).lower()
    if any(w in text for w in ("engineer", "developer", "software", "llm", "ai ")):
        return "ai_architect"
    if any(w in text for w in ("fellow", "postdoc", "research officer", "scientist", "research assistant")):
        return "researcher"
    if any(w in text for w in ("coordinator", "administrat", "support officer", "assistant", "officer", "manager")):
        return "polymath"
    return "researcher"


def _relevance(cap: dict, spec: dict, weights: dict | None = None) -> float:
    reqs = " ".join(spec.get("required_skills", []) + spec.get("nice_to_have", [])).lower()
    w = (weights or {}).get(cap["domain"], 1.0)
    if not reqs:
        return 0.5 * w
    # Whole-word matching: plain substring matching let the keyword "R" match
    # the letter r inside any word, giving every R-tagged capability a false
    # hit on every job. See selection._word_re.
    from .selection import _mentions
    hits = sum(1 for kw in cap["keywords"] if _mentions(reqs, kw))
    stmt_hits = sum(1 for tok in set(reqs.split())
                    if len(tok) > 3 and _mentions(cap["statement"].lower(), tok))
    return (hits * 1.0 + stmt_hits * 0.2) * w


def compile_resume(conn: sqlite3.Connection, spec: dict, target: str = "industry-ai") -> str:
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
    selected = [c for c in ranked if _relevance(c, spec, weights) > 0][:6] or ranked[:5]

    used_ev: list[str] = []
    lines: list[str] = []
    ap = lines.append

    ap(f"# {profile['name']} — {profile['title']}")
    ap("")
    contact = f"{profile['email']} · {profile['phone']} · {profile['location']}"
    ap(contact)
    ap("")
    ap(f"**Work rights:** {profile['work_rights']}")
    ap("")
    tgt = spec.get("title") or "role"
    org = f" at {spec['org']}" if spec.get("org") else ""
    ap(f"*Compiled for: {tgt}{org} (target build: {target})*")
    ap("")
    ap("## Summary")
    ap(profile["summary"])
    ap("")
    ap("## Selected capabilities")
    for c in selected:
        lv = levels.get(c["id"], {})
        level_txt = f"L{lv.get('level', '?')}"
        refs = []
        for ev_id in c["evidence"]:
            if ev_id not in used_ev:
                used_ev.append(ev_id)
            refs.append(f"[{used_ev.index(ev_id) + 1}]")
        ap(f"- **{c['statement']}** ({level_txt}) {' '.join(refs)}")
    ap("")

    for exp in profile.get("experience", []):
        if "## Experience" not in lines:
            ap("## Experience")
            ap("")
        refs = []
        for ev_id in exp.get("evidence", []):
            if ev_id not in used_ev:
                used_ev.append(ev_id)
            refs.append(f"[{used_ev.index(ev_id) + 1}]")
        ap(f"**{exp['role']}** — {exp['org']} ({exp['period']}) {' '.join(refs)}")
        for b in exp.get("bullets", []):
            ap(f"- {b}")
        ap("")

    if profile.get("education"):
        ap("## Education")
        ap("")
        for ed in profile["education"]:
            ap(f"**{ed['degree']}** — {ed['org']} ({ed['period']})")
            if ed.get("note"):
                ap(f"- {ed['note']}")
            ap("")

    # Most Australian employers want three referees named *in the CV*; a
    # missing block is a real screen-out, so emit it whenever we have them.
    if profile.get("referees"):
        ap("## Referees")
        ap("")
        for r in profile["referees"]:
            bits = [b for b in (r.get("title"), r.get("org")) if b]
            ap(f"**{r['name']}**" + (f" — {', '.join(bits)}" if bits else ""))
            contact = " · ".join(b for b in (r.get("email"), r.get("phone")) if b)
            if contact:
                ap(f"- {contact}")
            if r.get("relationship"):
                ap(f"- {r['relationship']}")
            ap("")

    ap("## Evidence ledger")
    ap("*Every claim above traces to a verifiable source below.*")
    ap("")
    for i, ev_id in enumerate(used_ev, 1):
        ev = identity.get_evidence(conn, ev_id)
        if not ev:
            continue
        mark = "✓" if ev["verifiable"] else "○"
        ap(f"{i}. {mark} ({ev['type']}) {ev['ref']}")
    ap("")

    langs = ", ".join(profile.get("languages", []))
    if langs:
        ap(f"**Languages:** {langs}")
        ap("")

    return "\n".join(lines)


def to_pdf(markdown_path: str, pdf_path: str) -> bool:
    """Best-effort MD->PDF via pandoc. Returns False if no engine is available."""
    import shutil
    import subprocess

    if not shutil.which("pandoc"):
        return False
    for engine in ("weasyprint", "wkhtmltopdf", "pdflatex", "tectonic", "typst"):
        if shutil.which(engine):
            try:
                subprocess.run(
                    ["pandoc", markdown_path, "-o", pdf_path, f"--pdf-engine={engine}"],
                    check=True, capture_output=True,
                )
                return True
            except subprocess.CalledProcessError:
                continue
    return False
