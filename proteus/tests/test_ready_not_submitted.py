"""The written-but-never-sent alarm.

The one failure mode where the cost is fully paid and the return is exactly
zero. Two packages reached "final ready", were never submitted, and expired.
Six more were lost the same way in August. Every other feature raises the
chance of success; this one stops a chance already in hand from being dropped.
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import webapp  # noqa: E402

TODAY = dt.date.today()
SOON = TODAY + dt.timedelta(days=4)
GONE = TODAY - dt.timedelta(days=10)


def _row(slug="a", **kw):
    base = {"id": "job_" + slug, "slug": slug, "title": kw.pop("title", slug.title()),
            "org": "", "salary": "", "status": "drafted", "fit_score": 0.7,
            "visa_requirement": "", "visa_feasible": True, "deadline": "",
            "closes_on": None, "closed": False, "url": "", "has_package": True,
            "polished": False, "criteria_done": 0, "criteria_total": 0,
            "started_on_portal": ""}
    base.update(kw)
    base["group"] = webapp._group_of(base)
    return base


# ------------------------------- the four combinations the brief asked for --

def test_ready_and_still_open_is_the_alarm_state():
    row = _row(polished=True, closes_on=SOON, closed=False)
    assert webapp.is_ready_not_submitted(row) is True
    assert webapp.is_expired_unsubmitted(row) is False
    assert row["group"] == webapp.GROUP_READY


def test_ready_and_expired_is_wasted_work():
    row = _row(polished=True, closes_on=GONE, closed=True)
    assert webapp.is_ready_not_submitted(row) is False
    assert webapp.is_expired_unsubmitted(row) is True
    assert row["group"] == webapp.GROUP_CLOSED


def test_not_ready_and_still_open_is_neither():
    row = _row(polished=False, closes_on=SOON, closed=False)
    assert webapp.is_ready_not_submitted(row) is False
    assert webapp.is_expired_unsubmitted(row) is False


def test_not_ready_and_expired_is_neither():
    row = _row(polished=False, closes_on=GONE, closed=True)
    assert webapp.is_ready_not_submitted(row) is False
    assert webapp.is_expired_unsubmitted(row) is False


@pytest.mark.parametrize("status", ["applied", "interviewing", "offer"])
def test_a_submitted_application_is_never_the_alarm(status):
    """Otherwise the alarm cries wolf and gets ignored."""
    row = _row(polished=True, closes_on=SOON, status=status)
    assert webapp.is_ready_not_submitted(row) is False


def test_a_rejected_application_is_not_wasted_work():
    """It was sent. Losing is not the same as never trying."""
    row = _row(polished=True, closes_on=GONE, closed=True, status="rejected")
    assert webapp.is_expired_unsubmitted(row) is False


# ------------------------------------------------------------- banner ----

def _render(rows, monkeypatch):
    monkeypatch.setattr(webapp, "_board_rows", lambda conn: rows)
    return webapp.render_board(None).decode("utf-8")


def test_banner_names_each_one_and_its_days_left(monkeypatch):
    rows = [
        _row("alpha", title="Alpha Officer", polished=True,
             closes_on=TODAY + dt.timedelta(days=4)),
        _row("beta", title="Beta Coordinator", polished=True,
             closes_on=TODAY + dt.timedelta(days=1)),
        _row("gamma", title="Gamma Analyst", status="applied"),
    ]
    out = _render(rows, monkeypatch)
    assert "written but not submitted" in out
    assert "Alpha Officer" in out and "4 days left" in out
    assert "Beta Coordinator" in out and "1 day left" in out
    assert "Gamma Analyst" not in out.split('class="alert"')[1].split("</div>")[0]


def test_no_banner_when_nothing_is_waiting(monkeypatch):
    rows = [_row("a", status="applied", polished=True, closes_on=SOON)]
    assert 'class="alert"' not in _render(rows, monkeypatch)


def test_strip_counts_ready_not_submitted(monkeypatch):
    rows = [_row("a", polished=True, closes_on=SOON),
            _row("b", polished=True, closes_on=SOON),
            _row("c", status="applied")]
    out = _render(rows, monkeypatch)
    strip = out.split('class="strip"')[1].split("</dl>")[0]
    assert "Ready, NOT submitted" in strip
    assert "<dd>2</dd>" in strip


def test_expired_unsubmitted_is_labelled_not_hidden(monkeypatch):
    rows = [_row("lost", title="Lost Chance", polished=True, closes_on=GONE, closed=True)]
    out = _render(rows, monkeypatch)
    assert "Written, never sent" in out
    assert "Lost Chance" in out, "wasted work must stay visible to be learned from"


def test_an_empty_board_says_nothing_is_open(monkeypatch):
    """Compiling more documents is motion without progress."""
    rows = [_row("a", closed=True, closes_on=GONE, status="rejected")]
    out = _render(rows, monkeypatch)
    assert "Nothing on this board is still open" in out


# -------------------------------------------------------------- portal ----

def test_portal_application_goes_stale_after_five_days():
    """An incomplete portal application is deleted after about a week."""
    fresh = _row(status="started_portal",
                 started_on_portal=(TODAY - dt.timedelta(days=2)).isoformat())
    stale = _row(status="started_portal",
                 started_on_portal=(TODAY - dt.timedelta(days=6)).isoformat())
    assert webapp.is_portal_stale(fresh) is False
    assert webapp.is_portal_stale(stale) is True


def test_portal_staleness_needs_a_date():
    assert webapp.is_portal_stale(_row(status="started_portal")) is False
    assert webapp.portal_days_open(_row(started_on_portal="not a date")) is None


def test_submitting_clears_the_portal_warning():
    row = _row(status="applied",
               started_on_portal=(TODAY - dt.timedelta(days=30)).isoformat())
    assert webapp.is_portal_stale(row) is False


def test_stale_portal_application_raises_its_own_banner(monkeypatch):
    rows = [_row("p", title="Portal Job", status="started_portal", closes_on=SOON,
                 started_on_portal=(TODAY - dt.timedelta(days=6)).isoformat())]
    out = _render(rows, monkeypatch)
    assert "left" in out and "unfinished" in out
    assert "Portal Job" in out and "started 6 days ago" in out
