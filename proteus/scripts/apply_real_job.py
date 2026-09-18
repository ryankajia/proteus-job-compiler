#!/usr/bin/env python3
"""Compile a résumé + gap report for one real, pasted JD (not a sample).

Usage: python scripts/apply_real_job.py data/real_jobs/<file>.txt [--llm]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proteus import calibration, compiler, db, fit, identity, jobspec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_llm = "--llm" in sys.argv
    if not args:
        print("usage: apply_real_job.py <jd.txt> [--llm]")
        sys.exit(1)
    jd_path = Path(args[0])
    OUT.mkdir(exist_ok=True)
    conn = db.connect()

    if not identity.get_capabilities(conn):
        identity.load_seed(conn)
        calibration.calibrate(conn, use_llm=use_llm)

    spec = jobspec.parse(jd_path.read_text(encoding="utf-8"), source=jd_path.stem, use_llm=use_llm)
    jobspec.save(conn, spec)

    scored = {**spec, **fit.score(conn, spec)}
    print(f"Fit: {scored['fit_score']}  Stretch: {scored['stretch_score']}  "
          f"Zone: {scored['zone']}  Visa: {'feasible' if scored['visa_feasible'] else 'GREYED'}")

    resume_md = compiler.compile_resume(conn, scored, target="academic")
    slug = scored.get("id", jd_path.stem)
    md_path = OUT / f"resume_{slug}.md"
    md_path.write_text(resume_md, encoding="utf-8")
    print(f"Resume -> {md_path}")

    pdf_path = OUT / f"resume_{slug}.pdf"
    if compiler.to_pdf(str(md_path), str(pdf_path)):
        print(f"PDF -> {pdf_path}")
    else:
        print("PDF engine not available — Markdown is the source of truth.")

    gap = calibration.gap_report(conn, scored)
    glines = [f"# Gap report — {scored['title']}", "",
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
    gpath = OUT / f"gap_{slug}.md"
    gpath.write_text("\n".join(glines), encoding="utf-8")
    print(f"Gap report -> {gpath}")

    conn.close()


if __name__ == "__main__":
    main()
