#!/usr/bin/env python3
"""First milestone: produce a level report + an evidence-chained resume + a
ranked job queue from the sample JDs.

Usage: python scripts/milestone.py [--llm]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proteus import calibration, compiler, db, fit, identity, jobspec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
JOBS = ROOT / "data" / "sample_jobs"


def write_level_report(conn) -> Path:
    levels = calibration.get_levels(conn)
    profile = identity.get_profile(conn)
    lines = [f"# Level report — {profile['name']}", ""]
    lines.append("*Levels are assessed from evidence, not self-reported. "
                 "Scale: 1 Exposure · 2 Assisted · 3 Independent · 4 Advanced · 5 Authority.*")
    lines.append("")
    ordered = sorted(levels.items(), key=lambda kv: kv[1]["level"], reverse=True)
    lines.append("| Level | Domain | Capability | Rationale |")
    lines.append("|---|---|---|---|")
    for cid, info in ordered:
        stmt = info["statement"].split(":")[0]
        lines.append(f"| **{info['level']}** | {info['domain']} | {stmt} | {info['rationale']} |")
    lines.append("")
    src = next(iter(levels.values()))["source"] if levels else "n/a"
    lines.append(f"*Source: {src}. Re-run with `--llm` to re-derive live via the Claude API.*")
    path = OUT / "level_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_ranking(conn, ranked) -> Path:
    lines = ["# Job queue (ranked — sorted, never filtered)", ""]
    lines.append("| # | Fit | Stretch | Zone | Visa | Role |")
    lines.append("|---|---|---|---|---|---|")
    for i, r in enumerate(ranked, 1):
        visa = "✓ feasible" if r["visa_feasible"] else "⚠ greyed"
        role = f"{r['title']}" + (f" — {r['org']}" if r.get("org") else "")
        lines.append(f"| {i} | {r['fit_score']} | {r['stretch_score']} | {r['zone']} | {visa} | {role} |")
    lines.append("")
    lines.append("*Visa-infeasible roles (citizenship / PR / clearance) are scored down and greyed, "
                 "not removed — you may still watch the role or requalify later.*")
    path = OUT / "ranking.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    use_llm = "--llm" in sys.argv
    OUT.mkdir(exist_ok=True)
    conn = db.connect()

    if not identity.get_capabilities(conn):
        identity.load_seed(conn)
        calibration.calibrate(conn, use_llm=use_llm)

    lr = write_level_report(conn)
    print(f"Level report -> {lr}")

    specs = []
    for jd in sorted(JOBS.glob("*.txt")):
        spec = jobspec.parse(jd.read_text(encoding="utf-8"), source=jd.stem, use_llm=use_llm)
        jobspec.save(conn, spec)
        specs.append(spec)
    print(f"Parsed {len(specs)} JDs.")

    ranked = fit.rank(conn, specs)
    rk = write_ranking(conn, ranked)
    print(f"Ranking -> {rk}")

    top = ranked[0]
    resume_md = compiler.compile_resume(conn, top, target="industry-ai")
    slug = top.get("id", "top")
    md_path = OUT / f"resume_{slug}.md"
    md_path.write_text(resume_md, encoding="utf-8")
    print(f"Top-ranked resume ({top['title']}) -> {md_path}")

    pdf_path = OUT / f"resume_{slug}.pdf"
    if compiler.to_pdf(str(md_path), str(pdf_path)):
        print(f"PDF -> {pdf_path}")
    else:
        print("PDF engine not available — Markdown is the source of truth "
              "(install weasyprint or a LaTeX engine for pandoc PDF).")

    # Gap report for the highest-stretch feasible role.
    feasible = [r for r in ranked if r["visa_feasible"]]
    if feasible:
        stretchiest = max(feasible, key=lambda r: r["stretch_score"])
        gap = calibration.gap_report(conn, stretchiest)
        glines = [f"# Gap report — {stretchiest['title']}", "",
                  f"Overall stretch: **{gap['overall_stretch']}** "
                  f"(required level {gap['required_level']}).", ""]
        if gap["no_evidence"]:
            glines.append("**No evidence on file (investigate before interview):** "
                          + ", ".join(gap["no_evidence"]))
        if gap["high_stretch"]:
            glines.append("")
            glines.append("**High-stretch (cram list):**")
            for h in gap["high_stretch"]:
                glines.append(f"- {h['skill']} (your level {h['user_level']}, stretch {h['stretch']})")
        if not gap["no_evidence"] and not gap["high_stretch"]:
            glines.append("No material gaps — all required skills are evidenced and within reach.")
        gpath = OUT / f"gap_{stretchiest.get('id', 'top')}.md"
        gpath.write_text("\n".join(glines), encoding="utf-8")
        print(f"Gap report ({stretchiest['title']}) -> {gpath}")

    conn.close()


if __name__ == "__main__":
    main()
