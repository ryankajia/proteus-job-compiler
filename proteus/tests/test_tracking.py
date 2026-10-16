"""Checklist tests use an invented applicant and employer only."""
from proteus import db, jobspec, tracking, webapp


def _job(conn):
    job_id = jobspec.save(conn, {
        "title": "Example Services Assistant",
        "org": "Example Civic Office",
        "source": "synthetic_services_assistant",
        "raw_text": "Example Services Assistant at Example Civic Office. https://example.com/job",
        "deadline": "31 December 2099",
        "closes_at": "2099-12-31",
    })
    conn.execute("INSERT INTO pipeline(job_id,status,updated_at) VALUES(?,?,?)",
                 (job_id, "sensed", "2099-01-01T00:00:00"))
    conn.commit()
    return job_id


def test_checklist_tracks_uploads_before_submission(tmp_path):
    conn = db.connect(tmp_path / "tracker.db")
    job_id = _job(conn)
    values = {key: {"required": key in ("cv", "statement"),
                    "done": key == "cv", "detail": ""} for key in tracking.ITEM_KEYS}
    values["cv"]["detail"] = "resume.docx"
    tracking.save(conn, job_id, values)

    items = tracking.get(conn, job_id)
    assert items["cv"]["done"] == 1
    assert items["cv"]["detail"] == "resume.docx"
    assert items["statement"]["required"] == 1
    assert tracking.progress(conn, job_id) == (1, 4)
    assert conn.execute("SELECT status FROM pipeline WHERE job_id=?", (job_id,)).fetchone()[0] == "sensed"


def test_confirmation_records_application_once(tmp_path):
    conn = db.connect(tmp_path / "tracker.db")
    job_id = _job(conn)
    values = {key: {"required": key == "cv", "done": key in ("portal_submitted", "confirmation_received"),
                    "detail": ""} for key in tracking.ITEM_KEYS}
    tracking.save(conn, job_id, values)
    tracking.save(conn, job_id, values)
    assert conn.execute("SELECT status FROM pipeline WHERE job_id=?", (job_id,)).fetchone()[0] == "applied"
    assert conn.execute("SELECT count(*) FROM events WHERE job_id=? AND event='applied'", (job_id,)).fetchone()[0] == 1


def test_track_page_shows_separate_submit_and_confirmation(tmp_path):
    conn = db.connect(tmp_path / "tracker.db")
    job_id = _job(conn)
    page = webapp.render_track(conn, job_id).decode()
    assert "Pressed Submit on employer site" in page
    assert "Application confirmation received" in page
    assert "Selection statement / pitch" in page


def test_actual_outcome_dates_are_editable_without_changing_status(tmp_path):
    conn = db.connect(tmp_path / "tracker.db")
    job_id = _job(conn)
    conn.execute("UPDATE pipeline SET submitted_at=?,rejected_at=? WHERE job_id=?",
                 ("2026-09-01", "2026-09-12", job_id))
    conn.commit()
    page = webapp.render_track(conn, job_id).decode()
    assert 'name="submitted_at" value="2026-09-01"' in page
    assert 'name="rejected_at" value="2026-09-12"' in page
    assert conn.execute("SELECT status FROM pipeline WHERE job_id=?", (job_id,)).fetchone()[0] == "sensed"


def test_applied_category_counts_use_manual_choice(tmp_path):
    conn = db.connect(tmp_path / "tracker.db")
    job_id = _job(conn)
    conn.execute("UPDATE pipeline SET status='rejected',role_category='临床' WHERE job_id=?", (job_id,))
    conn.commit()
    rows = webapp._board_rows(conn)
    overview = webapp._plain_overview(conn, rows)
    assert '<th scope="row">临床</th><td>1</td><td>1</td>' in overview
    assert 'name="role_category"' in webapp.render_track(conn, job_id).decode()
    assert webapp._role_category({"title": "Clinical Assistant", "role_category": "研究"}) == "研究"
