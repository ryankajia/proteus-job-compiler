"""The board is one list, grouped by stage, sorted by closing date.

It used to render every job twice — once as a "package" card and once as a
"tracker" row — with no shared key, so a job's real state was never in one
place and the table overflowed sideways.
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import webapp  # noqa: E402

TODAY = dt.date.today()


def _row(slug, **kw):
    base = {
        "id": "job_" + slug, "slug": slug, "title": kw.pop("title", slug.title()),
        "org": "Somewhere", "salary": "", "status": "sensed", "fit_score": 0.7,
        "visa_requirement": "", "visa_feasible": True, "deadline": "",
        "closes_on": None, "closed": False, "url": "", "has_package": False,
        "polished": False, "criteria_done": 0, "criteria_total": 0,
    }
    base.update(kw)
    base["group"] = webapp._group_of(base)
    return base


# ----------------------------------------------------------- sorting ----

def test_sorting_puts_the_soonest_deadline_first():
    rows = [
        _row("late", closes_on=TODAY + dt.timedelta(days=30)),
        _row("soon", closes_on=TODAY + dt.timedelta(days=2)),
        _row("mid", closes_on=TODAY + dt.timedelta(days=9)),
    ]
    assert [r["slug"] for r in sorted(rows, key=webapp._sort_key)] == ["soon", "mid", "late"]


def test_undated_jobs_sort_last_and_do_not_raise():
    """An undated job must not blow up the whole page."""
    rows = [_row("undated"), _row("dated", closes_on=TODAY + dt.timedelta(days=5))]
    ordered = sorted(rows, key=webapp._sort_key)   # must not raise
    assert [r["slug"] for r in ordered] == ["dated", "undated"]


def test_sorting_is_not_by_fit():
    """Fit used to drive the order; a closing date matters more."""
    rows = [
        _row("weak_but_urgent", fit_score=0.2, closes_on=TODAY + dt.timedelta(days=1)),
        _row("strong_but_distant", fit_score=0.99, closes_on=TODAY + dt.timedelta(days=60)),
    ]
    assert sorted(rows, key=webapp._sort_key)[0]["slug"] == "weak_but_urgent"


@pytest.mark.parametrize("text,expected", [
    ("2 August 2026", dt.date(2026, 8, 2)),
    ("15 Sep 2026", dt.date(2026, 9, 15)),
    ("2 Aug. 2026", dt.date(2026, 8, 2)),
    ("2026-09-15", dt.date(2026, 9, 15)),
    ("", None),
    ("rolling", None),
    ("31 Feb 2026", None),       # a real date parse, not a regex match
    ("2 Smarch 2026", None),
])
def test_deadline_parsing(text, expected):
    assert webapp._deadline_date(text) == expected


# ---------------------------------------------------------- grouping ----

def test_each_job_lands_in_exactly_one_group():
    rows = [
        _row("a", status="sensed"),
        _row("b", status="drafted", has_package=True),
        _row("c", status="drafted", has_package=True, polished=True),
        _row("d", status="applied"),
        _row("e", status="rejected"),
    ]
    groups = [r["group"] for r in rows]
    assert groups == [webapp.GROUP_INBOX, webapp.GROUP_DRAFTING, webapp.GROUP_READY,
                      webapp.GROUP_SUBMITTED, webapp.GROUP_CLOSED]
    assert len(set(groups)) == len(groups)


def test_polished_but_sent_is_not_in_the_ready_group():
    """Ready-not-submitted must mean unsent, or the alarm is meaningless."""
    sent = _row("x", status="applied", polished=True, has_package=True)
    assert sent["group"] == webapp.GROUP_SUBMITTED


def test_every_group_has_a_title():
    assert set(webapp.GROUP_ORDER) == set(webapp.GROUP_TITLE)


# ------------------------------------------------------------ render ----

def _render(rows, monkeypatch):
    monkeypatch.setattr(webapp, "_board_rows", lambda conn: rows)
    return webapp.render_board(None).decode("utf-8")


def test_a_job_is_rendered_only_once(monkeypatch):
    """The detail board has one row per job; the summary may link to it too."""
    rows = [_row("alpha_officer", has_package=True),
            _row("beta_fellow", status="applied")]
    html_out = _render(rows, monkeypatch)
    assert html_out.count('<p class="title"><a href=') == len(rows)


def test_slug_is_a_subtitle_not_the_heading(monkeypatch):
    rows = [_row("example_facilities_coordinator", title="Facilities Coordinator")]
    html_out = _render(rows, monkeypatch)
    assert '<p class="title"><a href="/job/job_example_facilities_coordinator">Facilities Coordinator</a>' in html_out
    assert '<div class="slug">example_facilities_coordinator</div>' in html_out


def test_slug_is_the_fallback_when_there_is_no_title(monkeypatch):
    rows = [_row("some_slug", title="")]
    html_out = _render(rows, monkeypatch)
    assert "some_slug" in html_out


def test_fit_is_a_word_not_a_number(monkeypatch):
    rows = [_row("a", fit_score=0.9542)]
    html_out = _render(rows, monkeypatch)
    assert "0.9542" not in html_out
    assert "Strong" in html_out


@pytest.mark.parametrize("score,word", [
    (0.95, "Strong"), (0.80, "Strong"),
    (0.79, "Possible"), (0.55, "Possible"),
    (0.54, "Stretch"), (0.0, "Stretch"),
    (None, "Not scored"),
])
def test_fit_bands_at_the_boundaries(score, word):
    assert webapp._fit_band(score)[0] == word


def test_urgency_colours_the_deadline(monkeypatch):
    soon = _row("s", closes_on=TODAY + dt.timedelta(days=2))
    week = _row("w", closes_on=TODAY + dt.timedelta(days=6))
    far = _row("f", closes_on=TODAY + dt.timedelta(days=40))
    assert 'pill crit' in webapp._deadline_cell(soon)
    assert 'pill warn' in webapp._deadline_cell(week)
    assert 'pill plain' in webapp._deadline_cell(far)


def test_a_closed_job_says_so_and_greys_the_row(monkeypatch):
    rows = [_row("old", closed=True)]
    html_out = _render(rows, monkeypatch)
    assert 'class="row dead"' in html_out
    assert "Closed" in html_out


def test_closed_group_is_collapsed(monkeypatch):
    rows = [_row("gone", status="rejected")]
    html_out = _render(rows, monkeypatch)
    assert "<details class=\"past\">" in html_out


def test_stage_is_a_menu_not_an_inline_select(monkeypatch):
    rows = [_row("a"), _row("b"), _row("c")]
    html_out = _render(rows, monkeypatch)
    assert "<select" not in html_out, "inline selects were the heaviest thing on the page"
    assert html_out.count('<details class="stage">') == 3


def test_empty_board_says_so(monkeypatch):
    assert "还没有岗位" in _render([], monkeypatch)


def test_criteria_progress_renders_as_a_fraction(monkeypatch):
    rows = [_row("a", criteria_done=4, criteria_total=9)]
    html_out = _render(rows, monkeypatch)
    assert "4/9" in html_out
    assert 'width:44%' in html_out


def test_visa_restricted_is_marked_but_still_listed(monkeypatch):
    """fit.py's stance: scored down and greyed, never removed."""
    rows = [_row("restricted", visa_feasible=False, fit_score=0.2)]
    html_out = _render(rows, monkeypatch)
    assert "Visa-restricted" in html_out
    assert "restricted" in html_out
