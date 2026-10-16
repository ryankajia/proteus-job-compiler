#!/usr/bin/env python3
"""T10 Orchestrator — one command per application (or all of them).

For every JD in data/real_jobs/ (or the one you name), this produces a complete
application package under out/apply/<name>/:

    resume.md         evidence-chained résumé (persona auto-selected)
    cover_letter.md   evidence-grounded cover letter draft
    review.md         T6 reviewer report — blockers/warnings before you send
    gap.md            what to cram before an interview
    checklist.md      exactly what to upload where, per employer

Usage:
    python3 scripts/apply.py                    # all JDs in data/real_jobs/
    python3 scripts/apply.py my_job_name        # any filename substring
    python3 scripts/apply.py --llm              # re-parse JDs via Claude API

Proteus generates; it never sends. Submitting is always your click.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proteus import calibration, compiler, coverletter, db, fit, identity, jobspec, reviewer, revisions, selection  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "apply"
JOBS = ROOT / "data" / "real_jobs"


def checklist_md(spec: dict, scored: dict, target: str, findings: list, pkg: Path) -> str:
    raw = (spec.get("raw_text") or "").lower()

    def doc(fname: str, label: str, draft_hint: str) -> str:
        """Point at the FINAL when one exists — that is the file you sends."""
        final = revisions.FINAL_OF[fname]
        if (pkg / final).exists():
            return f"- [ ] {label} — **`{final}` is the final version**; export it to PDF"
        return f"- [ ] {label} (start from `{fname}`, {draft_hint})"

    lines = [f"# Application checklist — {spec.get('title')}", ""]
    lines.append(f"- Persona used: **{target}**  ·  Fit {scored['fit_score']}  ·  Zone {scored['zone']}")
    if spec.get("deadline"):
        lines.append(f"- **Deadline: {spec['deadline']}**")
    lines.append("")
    lines.append("## Documents to prepare")
    lines.append(doc("resume.md", "Résumé", "export to PDF"))
    lines.append(doc("cover_letter.md", "Cover letter", "personalise the opening"))
    if "selection criteria" in raw:
        # Named in the PD's own terms: a two-page pitch and a criterion-by-
        # criterion statement are different documents.
        lines.append(doc("selection_criteria.md",
                         f"**{selection.document_name(raw)}** (required)",
                         "check against the official PD"))
    if "refere" in raw:
        lines.append("- [ ] Referees — skip if already recorded in this "
                     "employer's system; otherwise line up 3 (the usual number) "
                     "and get their consent first")
    else:
        lines.append("- [x] No referee details required at application 🎉")
    lines.append("")
    lines.append("## Before you click submit")
    for f in findings:
        mark = "⛔" if f["severity"] == "block" else "⚠️" if f["severity"] == "warn" else "·"
        lines.append(f"- {mark} {f['msg']}")
    return "\n".join(lines)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_llm = "--llm" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    conn = db.connect()

    if not identity.get_capabilities(conn):
        identity.load_seed(conn)
        calibration.calibrate(conn, use_llm=use_llm)

    files = sorted(JOBS.glob("*.txt"))
    if args:
        files = [f for f in files if any(a.lower() in f.stem.lower() for a in args)]
    if not files:
        print(f"No JD files matched in {JOBS}. Drop a .txt there (or run scripts/ingest.py).")
        sys.exit(1)

    summary = []
    for jd in files:
        spec = jobspec.parse(jd.read_text(encoding="utf-8"), source=jd.stem, use_llm=use_llm)
        jobspec.save(conn, spec)
        scored = {**spec, **fit.score(conn, spec)}
        target = compiler.pick_target(spec)

        pkg = OUT / jd.stem
        pkg.mkdir(exist_ok=True)

        raw = {"resume.md": compiler.compile_resume(conn, scored, target=target),
               "cover_letter.md": coverletter.compile_cover_letter(conn, scored, target=target)}
        # AU-style selection criteria statement (drafted when the JD lists the
        # criteria; a fill-in template when they live in the PD PDF).
        if selection.mentions_criteria(spec.get("raw_text") or ""):
            raw["selection_criteria.md"] = selection.compile_selection_criteria(conn, scored, target=target)

        # T12: harvest your edits BEFORE overwriting anything. Packages
        # from before snapshotting existed get today's compile as baseline, so
        # whatever differs in your files is learned rather than lost.
        for fname, text in raw.items():
            if (pkg / fname).exists() and not (pkg / revisions.SNAP_DIR / fname).exists():
                revisions.snapshot(pkg, fname, text)
        learned = revisions.learn_package(conn, pkg)
        if learned:
            print(f"  learned {learned} edit(s) from your changes in {pkg.name}")

        for fname in raw:
            raw[fname], applied = revisions.apply_rules(conn, raw[fname], fname, package=pkg.name)
            if applied:
                print(f"  re-applied {len(applied)} learned edit(s) -> {fname}")
            # The snapshot always updates — it is what your edits are diffed
            # against. The visible draft is only kept while no hand-polished
            # FINAL exists; once it does, the draft is noise in the folder.
            revisions.snapshot(pkg, fname, raw[fname])
            final = pkg / revisions.FINAL_OF[fname]
            if final.exists():
                (pkg / fname).unlink(missing_ok=True)
            else:
                (pkg / fname).write_text(raw[fname], encoding="utf-8")
        # Review what you will actually send: the hand-polished FINAL when it
        # exists, otherwise the auto-draft.
        def sendable(fname: str) -> str:
            final = pkg / revisions.FINAL_OF[fname]
            return final.read_text(encoding="utf-8") if final.exists() else raw[fname]

        resume_md, cover_md = sendable("resume.md"), sendable("cover_letter.md")

        findings = reviewer.review(conn, scored, resume_md, cover_md)
        (pkg / "review.md").write_text(reviewer.render_md(scored, findings), encoding="utf-8")

        gap = calibration.gap_report(conn, scored)
        glines = [f"# Gap report — {scored['title']}", "",
                  f"Overall stretch: **{gap['overall_stretch']}** (required level {gap['required_level']}).", ""]
        if gap["no_evidence"]:
            glines.append("**No evidence on file:** " + ", ".join(gap["no_evidence"]))
        for h in gap.get("high_stretch", []):
            glines.append(f"- {h['skill']} (your level {h['user_level']}, stretch {h['stretch']})")
        (pkg / "gap.md").write_text("\n".join(glines), encoding="utf-8")

        (pkg / "checklist.md").write_text(checklist_md(spec, scored, target, findings, pkg), encoding="utf-8")

        # Track it: package drafted. Regenerating materials must never undo
        # progress you recorded — an applied/interviewing job stays where it is.
        import datetime as _dt
        now = _dt.datetime.now().isoformat(timespec="seconds")
        prev = conn.execute("SELECT status FROM pipeline WHERE job_id=?", (spec["id"],)).fetchone()
        status = prev["status"] if prev and prev["status"] not in ("sensed", "drafted") else "drafted"
        conn.execute(
            "INSERT OR REPLACE INTO pipeline(job_id, status, positioning_used, fit_score, stretch_score, deadline, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (spec["id"], status, target, scored["fit_score"], scored["stretch_score"], spec.get("deadline"), now))
        conn.execute("INSERT INTO events(job_id, event, note, created_at) VALUES(?,?,?,?)",
                     (spec["id"], "drafted", f"package -> {pkg}", now))
        conn.commit()

        blocks = sum(1 for f in findings if f["severity"] == "block")
        summary.append((jd.stem, scored["fit_score"], target, blocks))
        print(f"✓ {jd.stem}: fit {scored['fit_score']}, persona {target}, "
              f"{blocks} blocker(s) -> {pkg}")

    (ROOT / "out" / "learning_report.md").write_text(revisions.report_md(conn), encoding="utf-8")
    print("\nPackages ready under out/apply/. Read each checklist.md before submitting.")
    print("Learned-edit rules -> out/learning_report.md")
    conn.close()


if __name__ == "__main__":
    main()
