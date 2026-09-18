"""Closing dates are stored, and fit is a band on the list.

Two separate problems this covers:

  * A closing date parsed out of prose on every render could never be
    corrected, and most Australian PDs do not state one at all. It is now a
    real column that can be typed in by hand.
  * A four-decimal fit score on a list is false precision. The number stays in
    the database and on the detail page, where calibrating it against real
    outcomes needs it; the list shows a word.
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import db, jobspec, webapp  # noqa: E402


# ------------------------------------------------------ date parsing ----

@pytest.mark.parametrize("text,expected", [
    ("15 Sep 2026", dt.date(2026, 9, 15)),
    ("2 August 2026", dt.date(2026, 8, 2)),
    ("2 Aug. 2026", dt.date(2026, 8, 2)),
    ("Sep 15, 2026", dt.date(2026, 9, 15)),
    ("September 15 2026", dt.date(2026, 9, 15)),
    ("2026-09-15", dt.date(2026, 9, 15)),
    ("15/09/2026", dt.date(2026, 9, 15)),      # Australian day/month order
])
def test_formats_that_must_parse(text, expected):
    assert jobspec.parse_closing_date(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "rolling", "open until filled", "ASAP",
    "31 Feb 2026",        # regex-shaped but not a real date
    "40 Sep 2026",
    "15 Smarch 2026",
    None,
])
def test_nothing_is_guessed(text):
    """A wrong date either hides a live job or makes a dead one look open."""
    assert jobspec.parse_closing_date(text) is None


def test_extracts_from_the_closes_line_the_ingest_writes():
    jd = "Some Role\nBlah blah\n\n-----\nCloses: 15 Sep 2026, 23:55 AEST\n(added 2026-09-01)"
    assert jobspec.extract_closing_date(jd) == "2026-09-15"


def test_a_hand_typed_date_beats_the_ad_text():
    jd = "Closes: 15 Sep 2026"
    assert jobspec.extract_closing_date(jd, deadline_field="2 August 2026") == "2026-08-02"


def test_no_date_in_the_ad_yields_empty_not_a_guess():
    assert jobspec.extract_closing_date("No closing date is stated anywhere.") == ""


# --------------------------------------------------------- storage ----

def test_closes_at_survives_a_reparse(tmp_path):
    """Most PDs never state a date, so a hand-typed one is often the only copy."""
    conn = db.connect(tmp_path / "t.db")
    spec = jobspec.parse("A Role\nNo dates here.", source="x")
    job_id = jobspec.save(conn, spec)
    conn.execute("UPDATE jobspecs SET closes_at=? WHERE id=?", ("2026-12-01", job_id))
    conn.commit()

    jobspec.save(conn, jobspec.parse("A Role\nNo dates here.", source="x"))
    kept = conn.execute("SELECT closes_at FROM jobspecs WHERE id=?", (job_id,)).fetchone()[0]
    assert kept == "2026-12-01"
    conn.close()


def test_migration_adds_the_column_without_losing_rows(tmp_path):
    """Existing installs predate closes_at; recreating the table would throw
    away a real job hunt."""
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE jobspecs (id TEXT PRIMARY KEY, title TEXT, deadline TEXT)")
    old.execute("CREATE TABLE pipeline (job_id TEXT PRIMARY KEY, status TEXT)")
    old.execute("INSERT INTO jobspecs VALUES ('job_1', 'Kept Role', '15 Sep 2026')")
    old.commit()
    old.close()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db.migrate(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobspecs)")}
    assert "closes_at" in cols
    row = conn.execute("SELECT title FROM jobspecs WHERE id='job_1'").fetchone()
    assert row["title"] == "Kept Role", "migration must not drop existing rows"
    db.migrate(conn)          # must be safe to re-run
    conn.close()


# ------------------------------------------------------------- UI ----

def _row(**kw):
    base = {"id": "job_x", "slug": "x", "title": "X", "org": "", "salary": "",
            "status": "sensed", "fit_score": 0.7, "visa_requirement": "",
            "visa_feasible": True, "deadline": "", "closes_on": None,
            "closed": False, "url": "", "has_package": False, "polished": False,
            "criteria_done": 0, "criteria_total": 0}
    base.update(kw)
    base["group"] = webapp._group_of(base)
    return base


@pytest.mark.parametrize("score,word,band", [
    (1.00, "Strong", "live"),
    (0.80, "Strong", "live"),        # boundary, inclusive
    (0.7999, "Possible", "warn"),
    (0.55, "Possible", "warn"),      # boundary, inclusive
    (0.5499, "Stretch", "plain"),
    (0.00, "Stretch", "plain"),
])
def test_band_boundaries_are_unambiguous(score, word, band):
    assert webapp._fit_band(score) == (word, band)


def test_bands_do_not_share_a_colour():
    """All-green pills carried no information."""
    colours = {webapp._fit_band(v)[1] for v in (0.9, 0.7, 0.3)}
    assert len(colours) == 3


@pytest.mark.parametrize("score", [None, "", "abc", float("nan")])
def test_unscorable_values_do_not_crash(score):
    word, _ = webapp._fit_band(score)
    assert isinstance(word, str)


def test_no_closing_date_says_unknown_and_offers_an_entry():
    cell = webapp._deadline_cell(_row(closes_on=None))
    assert "Date unknown" in cell
    assert 'action="/deadline"' in cell


def test_a_missing_date_does_not_crash_the_row():
    html_out = webapp._board_row_html(_row(closes_on=None))
    assert "Date unknown" in html_out


@pytest.mark.parametrize("days,cls", [(0, "crit"), (3, "crit"), (4, "warn"),
                                      (7, "warn"), (8, "plain"), (60, "plain")])
def test_urgency_colours(days, cls):
    cell = webapp._deadline_cell(_row(closes_on=dt.date.today() + dt.timedelta(days=days)))
    assert f'pill {cls}' in cell


def test_expired_row_is_greyed_and_labelled():
    row = _row(closed=True, closes_on=dt.date.today() - dt.timedelta(days=5))
    assert "Closed" in webapp._deadline_cell(row)
    assert 'class="row dead"' in webapp._board_row_html(row)


def test_the_entry_prefills_the_current_date():
    row = _row(closes_on=dt.date(2026, 12, 1))
    assert 'value="2026-12-01"' in webapp._deadline_cell(row)
