"""End-to-end smoke test on a throwaway DB — no API key, no network."""
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import calibration, compiler, db, fit, identity, jobspec  # noqa: E402

JOBS = Path(__file__).resolve().parent.parent / "data" / "sample_jobs"


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    identity.load_seed(c)
    calibration.calibrate(c)
    yield c
    c.close()


def test_seed_loaded(conn):
    caps = identity.get_capabilities(conn)
    # Assert shape, not a count: the number of capabilities is a property of
    # whoever's seed is loaded, not of the code.
    assert caps and all(c["id"] and c["statement"] and c["keywords"] for c in caps)
    # The seed is your own private file, so assert shape, not identity.
    assert identity.get_profile(conn)["name"]


def test_every_capability_calibrated(conn):
    levels = calibration.get_levels(conn)
    caps = {c["id"] for c in identity.get_capabilities(conn)}
    # The real bug this guards: a capability with no entry in
    # calibration_levels.json loads silently as nothing, and every criterion
    # that needed it draws no evidence.
    assert caps and set(levels) == caps
    assert all(1.0 <= v["level"] <= 5.0 and v["rationale"] for v in levels.values())


def test_jd_parse_and_visa_flag(conn):
    gov = jobspec.parse((JOBS / "data_analyst_government.txt").read_text())
    assert gov["security_clearance"] == 1
    assert not fit._visa_feasible(gov)
    uni = jobspec.parse((JOBS / "research_officer_university.txt").read_text())
    assert fit._visa_feasible(uni)
    assert "Python" in uni["required_skills"]


def test_ranker_sorts_and_keeps_infeasible(conn):
    specs = [jobspec.parse(p.read_text(), source=p.stem) for p in sorted(JOBS.glob("*.txt"))]
    ranked = fit.rank(conn, specs)
    assert len(ranked) == len(specs)                       # nothing filtered out
    fits = [r["fit_score"] for r in ranked]
    assert fits == sorted(fits, reverse=True)              # sorted best-first
    assert ranked[-1]["visa_feasible"] is False            # infeasible sinks to the bottom


def test_resume_claims_are_all_backed(conn):
    spec = jobspec.parse((JOBS / "research_officer_university.txt").read_text(), source="uni")
    md = compiler.compile_resume(conn, spec)
    assert "Evidence ledger" in md
    # Every [n] citation in the body must have a matching ledger entry.
    import re
    cites = {int(x) for x in re.findall(r"\[(\d+)\]", md)}
    ledger = {int(x) for x in re.findall(r"^(\d+)\. ", md, re.M)}
    assert cites and cites.issubset(ledger)


def test_resume_has_experience_and_education(conn):
    spec = jobspec.parse((JOBS / "research_officer_university.txt").read_text(), source="uni")
    md = compiler.compile_resume(conn, spec, target="researcher")
    assert "## Experience" in md and "## Education" in md
    # Phrasing follows the master CV ("Ph.D. in X" vs "Ph.D., X"), so assert
    # the substance rather than one wording.
    profile = identity.get_profile(conn)
    assert profile["education"][0]["degree"].split(",")[0] in md


def test_cover_letter_grounded_and_persona_aware(conn):
    from proteus import coverletter
    spec = jobspec.parse((JOBS / "research_officer_university.txt").read_text(), source="uni")
    md = coverletter.compile_cover_letter(conn, spec, target="researcher")
    # The letter must state work rights from the profile, whatever they are.
    assert "Dear" in md
    rights = identity.get_profile(conn)["work_rights"]
    assert rights.split()[0].lower() in md.lower()
    assert "calibrated level" in md


def test_reviewer_flags_blockers(conn):
    from proteus import reviewer
    spec = {"title": "X", "raw_text": "Selection criteria required. Closes: 1 January 2020. referees needed.",
            "required_skills": ["Python"], "deadline": "1 January 2020", "visa_feasible": True}
    findings = reviewer.review(conn, spec, "resume text without the magic words")
    checks = {f["check"] for f in findings if f["severity"] == "block"}
    assert "deadline" in checks and "work-rights" in checks and "selection-criteria" in checks


def test_pick_target_personas(conn):
    assert compiler.pick_target({"title": "Research Fellow", "raw_text": ""}) == "researcher"
    assert compiler.pick_target({"title": "Program Coordinator", "raw_text": ""}) == "polymath"
    assert compiler.pick_target({"title": "AI Engineer", "raw_text": ""}) == "ai_architect"


def test_events_table_exists(conn):
    conn.execute("INSERT INTO jobspecs(id, created_at) VALUES('j1', 'now')")
    conn.execute("INSERT INTO events(job_id, event, note, created_at) VALUES('j1','drafted',NULL,'now')")
    assert conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 1


def test_gap_report_flags_missing_and_stretch(conn):
    ai = jobspec.parse((JOBS / "ai_engineer_remote.txt").read_text(), source="ai")
    gap = calibration.gap_report(conn, ai)
    assert gap["overall_stretch"] > 0
    assert "AWS" in gap["no_evidence"] or "cloud" in gap["no_evidence"]


def test_resaving_a_jd_preserves_pipeline_status_and_events(conn):
    """Regenerating materials must never wipe recorded progress (jobspecs used
    INSERT OR REPLACE, which cascade-deleted pipeline rows and event history)."""
    spec = jobspec.parse((JOBS / "research_officer_university.txt").read_text(), source="uni_ro")
    jobspec.save(conn, spec)
    conn.execute("INSERT INTO pipeline(job_id, status, updated_at) VALUES(?,?,?)",
                 (spec["id"], "applied", "2026-07-27T10:00:00"))
    conn.execute("INSERT INTO events(job_id, event, note, created_at) VALUES(?,?,?,?)",
                 (spec["id"], "applied", "submitted", "2026-07-27T10:00:00"))
    conn.commit()

    again = jobspec.parse((JOBS / "research_officer_university.txt").read_text(), source="uni_ro")
    jobspec.save(conn, again)
    assert again["id"] == spec["id"]                                   # stable identity
    row = conn.execute("SELECT status FROM pipeline WHERE job_id=?", (spec["id"],)).fetchone()
    assert row and row["status"] == "applied"                          # status survived
    assert conn.execute("SELECT COUNT(*) c FROM events WHERE job_id=?",
                        (spec["id"],)).fetchone()["c"] == 1            # history survived


def test_edited_jd_text_does_not_fork_a_duplicate_job(conn):
    spec = jobspec.parse((JOBS / "research_officer_university.txt").read_text(), source="uni_ro2")
    jobspec.save(conn, spec)
    edited = jobspec.parse("Corrected heading\n" + (JOBS / "research_officer_university.txt").read_text(),
                           source="uni_ro2")
    jobspec.save(conn, edited)
    assert edited["id"] == spec["id"]
    assert conn.execute("SELECT COUNT(*) c FROM jobspecs WHERE source='uni_ro2'").fetchone()["c"] == 1


def test_lapsed_deadline_greys_out_without_a_manual_marker(tmp_path, monkeypatch):
    """A closing date that has gone by must render as Closed, not as a live
    badge — it only greyed before if someone hand-typed "PASSED" into the file."""
    from proteus import webapp

    monkeypatch.setattr(webapp, "REAL_JOBS", tmp_path)
    (tmp_path / "old.txt").write_text("Some ad\n\n-----\nCloses: 2 August 2026\n",
                                      encoding="utf-8")
    (tmp_path / "live.txt").write_text("Some ad\n\n-----\nCloses: 30 August 2026\n",
                                       encoding="utf-8")
    (tmp_path / "rolling.txt").write_text("Some ad\n\n-----\nCloses: rolling\n",
                                          encoding="utf-8")

    class FixedDate(_dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 5)

    monkeypatch.setattr(webapp._dt, "date", FixedDate)
    assert webapp._job_meta("old")["closed"] is True
    assert webapp._job_meta("live")["closed"] is False
    # Unparseable dates stay open — never grey out a live job on a bad parse.
    assert webapp._job_meta("rolling")["closed"] is False


def test_apply_link_tells_the_truth_about_dead_ads():
    """Two ways the apply link link misled your: a closed job still offering a live
    Apply link, and a job-board ad taken down while the job is still open."""
    from proteus import webapp

    closed = {"url": "https://au.seek.com/job/1", "closed": True}
    assert "Ad has been taken down" in webapp._apply_link(closed)
    assert "Go to the employer" not in webapp._apply_link(closed)

    board = {"url": "https://au.seek.com/job/1", "closed": False}
    assert "may have expired" in webapp._apply_link(board)

    employer = {"url": "https://careers.example.edu.au/job/12345/x",
                "closed": False}
    assert "Go to the employer" in webapp._apply_link(employer)
    assert "may have expired" not in webapp._apply_link(employer)

    assert webapp._apply_link({"url": "", "closed": False}) == ""
    # Suffix matching must not be fooled by a lookalike host.
    assert not webapp._is_expiring_board("https://seek.com.evil.example/job/1")
    assert webapp._is_expiring_board("https://www.seek.com.au/job/1")
