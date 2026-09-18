#!/usr/bin/env python3
"""Reverse discovery — from your capabilities to the employers worth searching.

Instead of only reacting to job ads, this maps your strongest calibrated
capabilities to employer *archetypes* and ready-made search queries.

The archetypes are national: every Australian capital has universities, a
health service, state and federal departments, research institutes and NFPs.
Your city is read from your profile `location`, so the queries come out aimed
where you actually are. Output: out/discovery.md.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proteus import calibration, db, identity  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Employer archetypes by capability domain. Deliberately generic: these exist
# in every Australian capital, so {city} is filled in from your profile.
# Add your own — this is a starting point, not a directory.
TARGETS = {
    "research": [
        ("Universities — the research schools and institutes in your field",
         "seek: research officer OR research fellow {field} {city}"),
        ("Medical research institutes (WEHI, Garvan, QIMR, Telethon Kids, Florey, "
         "Menzies, JCSMR and the rest — most capitals have one)",
         "seek: research assistant OR postdoctoral {field} {city}"),
        ("Hospital and health-service research units, and clinical trial sites",
         "seek: clinical trial coordinator OR research assistant {city}"),
        ("CSIRO and the national science agencies",
         "seek: research scientist CSIRO {city}"),
    ],
    "stats": [
        ("University statistical consulting and data science units",
         "seek: statistician OR data analyst research {city}"),
        ("State health departments and primary health networks",
         "seek: data analyst health {city}"),
        ("ABS, and state government analytics teams",
         "seek: data analyst government {city}"),
    ],
    "ai": [
        ("Australian AI and software companies — note that defence and federal "
         "work is usually gated on a security clearance, which needs citizenship",
         "seek: AI engineer OR machine learning {city}"),
        ("Remote-friendly Australian startups (no clearance, location flexible)",
         "seek: LLM engineer remote australia"),
    ],
    "management": [
        ("University professional services — research offices, graduate schools",
         "seek: research administration officer university {city}"),
        ("Learned academies and scientific NFPs",
         "seek: program coordinator OR project officer science {city}"),
        ("Medical research funders and peak bodies (NHMRC, MRFF-funded programmes)",
         "seek: grants officer OR research program manager {city}"),
    ],
    "web": [
        ("SaaS companies, digital agencies and university web teams",
         "seek: full stack developer {city}"),
    ],
}


def _city(conn) -> str:
    """The user's own city, so queries point where they actually are.

    `location` is free text and in practice collects whatever was on the CV
    header line — phone numbers, separators, a state, a start date. Pull out
    the most city-like piece rather than trusting the whole field.
    """
    loc = (identity.get_profile(conn).get("location") or "").strip()
    # Split on the separators CV headers use, and drop anything with a digit
    # (phone numbers, postcodes, "from October 2026").
    parts = [p.strip(" ()") for p in re.split(r"[·|,;/]| - ", loc)]
    words = [p for p in parts if p and not any(ch.isdigit() for ch in p)]
    # Prefer a known capital; otherwise the first clean fragment.
    for w in words:
        for cap in ("Canberra", "Sydney", "Melbourne", "Brisbane", "Perth",
                    "Adelaide", "Hobart", "Darwin", "Newcastle", "Wollongong",
                    "Gold Coast", "Geelong"):
            if cap.lower() in w.lower():
                return cap
    return words[0] if words else "australia"


def main() -> None:
    conn = db.connect()
    if not identity.get_capabilities(conn):
        identity.load_seed(conn)
        calibration.calibrate(conn)
    levels = calibration.get_levels(conn)

    by_domain: dict[str, float] = {}
    for info in levels.values():
        d = info["domain"]
        by_domain[d] = max(by_domain.get(d, 0), info["level"])

    city = _city(conn)
    lines = [f"# Reverse discovery — where your capabilities point ({city})", "",
             "*Ordered by your strongest calibrated domains. Searches are aimed at "
             "your profile location; swap the city to look elsewhere.*",
             "", "*Roles requiring citizenship or a security clearance are scored "
             "down elsewhere in the system — see `VISA_BLOCKERS` in `proteus/fit.py` "
             "if that does not apply to you.*", ""]
    for domain, lvl in sorted(by_domain.items(), key=lambda kv: kv[1], reverse=True):
        rows = TARGETS.get(domain)
        if not rows:
            continue
        lines.append(f"## {domain} (your peak level: {lvl})")
        lines.append("")
        for org, query in rows:
            lines.append(f"- **{org}**")
            lines.append(f"  - search: `{query.format(city=city, field=domain)}`")
        lines.append("")
    out = ROOT / "out" / "discovery.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Discovery map -> {out}")
    conn.close()


if __name__ == "__main__":
    main()
