"""Hard thresholds, checked at intake instead of before submitting.

Citizenship, a clearance, a registration, a stated minimum number of years —
all knowable the moment the ad is read. Finding out afterwards means the
résumé, the letter and eight criterion answers were written for nothing.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import gate, webapp  # noqa: E402


# ------------------------------------------------- the brief's two cases ----

def test_ahpra_registration_is_detected():
    found = gate.gate("The successful applicant will hold Full registration with "
                      "AHPRA as a psychologist.")
    assert [f["check"] for f in found] == ["registration"]


def test_years_requirement_extracts_the_title():
    found = gate.gate("We are seeking someone with 1+ year of experience as a "
                      "Clinical Research Associate.")
    assert len(found) == 1
    f = found[0]
    assert f["check"] == "experience_years"
    assert "1+ years" in f["detail"]
    assert "Clinical Research Associate" in f["detail"]


@pytest.mark.parametrize("text,title", [
    ("Minimum of 5 years experience as a Senior Project Officer is essential.",
     "Senior Project Officer"),
    ("At least 3 years experience in Data Analysis and reporting required.",
     "Data Analysis"),
    ("2+ years of demonstrated experience as a Research Officer.",
     "Research Officer"),
])
def test_the_title_stops_at_the_end_of_the_title(text, title):
    """The capture used to run on into the sentence: '... Officer is essential'."""
    assert gate.gate(text)[0]["detail"].endswith(title)


# ------------------------------------------------------------ negation ----
# Both flags on the first real run were false positives: ads state the
# requirements they do NOT have as often as the ones they do.

@pytest.mark.parametrize("text", [
    "Salary as stated; no security clearance required.",
    "This role does not require a Security Clearance.",
    "Australian citizenship is not required for this position.",
    "A security clearance is not required.",
    "Applicants without AHPRA registration are welcome to apply.",
])
def test_a_requirement_being_ruled_out_is_not_a_hit(text):
    assert gate.gate(text) == [], f"false positive on: {text}"


@pytest.mark.parametrize("text,check", [
    ("Applicants must be able to obtain and maintain a Baseline security clearance.", "clearance"),
    ("Eligibility: Australian citizenship is required for this role.", "citizenship"),
    ("You must hold Australian permanent residency.", "citizenship"),
    ("Current professional registration is essential.", "registration"),
])
def test_real_requirements_are_still_caught(text, check):
    assert [f["check"] for f in gate.gate(text)] == [check]


def test_negation_does_not_read_across_a_sentence_boundary():
    """'Citizenship is not relevant. A clearance is required.' negates only
    the first clause."""
    found = gate.gate("Citizenship is not relevant. A security clearance is required.")
    assert [f["check"] for f in found] == ["clearance"]


# --------------------------------------------------------- robustness ----

def test_a_clean_ad_produces_nothing():
    assert gate.gate("A friendly team seeks a coordinator. Applicants must have "
                     "the right to work in Australia.") == []


@pytest.mark.parametrize("text", ["", None, "   "])
def test_empty_input_is_safe(text):
    assert gate.gate(text) == []


def test_a_broken_rules_file_disables_the_gate_rather_than_the_board(tmp_path):
    bad = tmp_path / "rules.json"
    bad.write_text("{ not json at all", encoding="utf-8")
    assert gate.load_rules(bad) == {}


def test_a_malformed_pattern_is_skipped_not_raised(tmp_path):
    rules = {"citizenship": {"label": "L", "why": "W", "patterns": ["([unclosed", "must be an australian citizen"]}}
    found = gate.gate("You must be an australian citizen.", rules=rules)
    assert [f["check"] for f in found] == ["citizenship"]


def test_findings_are_capped():
    """A wall of red chips is the same as no signal."""
    text = ("Australian citizenship is required. A security clearance is required. "
            "Full registration with AHPRA is required. Minimum of 5 years "
            "experience as a Senior Officer is required.")
    assert len(gate.gate(text)) <= gate.MAX_FINDINGS


def test_every_shipped_rule_compiles():
    import re
    rules = gate.load_rules()
    assert rules, "the shipped rules file must load"
    for name, rule in rules.items():
        for key in ("patterns", "remote_patterns", "restricted_patterns"):
            for p in rule.get(key, []):
                re.compile(p)     # must not raise


def test_evidence_quotes_the_ad():
    found = gate.gate("Applicants need a Baseline security clearance to start.")
    assert "security clearance" in found[0]["evidence"].lower()


# ------------------------------------------------------------- board ----

def _row(slug="a", **kw):
    base = {"id": "job_" + slug, "slug": slug, "title": slug.title(), "org": "",
            "salary": "", "status": "sensed", "fit_score": 0.7,
            "visa_requirement": "", "visa_feasible": True, "deadline": "",
            "closes_on": None, "closed": False, "url": "", "has_package": False,
            "polished": False, "criteria_done": 0, "criteria_total": 0,
            "started_on_portal": "", "gate_flags": []}
    base.update(kw)
    base["group"] = webapp._group_of(base)
    return base


def test_a_gated_job_is_never_removed(monkeypatch):
    """fit.py's position: score down and mark, never hide. The system is not
    entitled to decide something is impossible."""
    flags = gate.gate("Australian citizenship is required for this role.")
    rows = [_row("gated", gate_flags=flags), _row("clean")]
    monkeypatch.setattr(webapp, "_board_rows", lambda conn: rows)
    out = webapp.render_board(None).decode("utf-8")
    assert "Gated" in out and "Clean" in out


def test_a_gated_job_shows_a_chip_and_a_reason(monkeypatch):
    flags = gate.gate("Full registration with AHPRA is essential.")
    rows = [_row("gated", gate_flags=flags)]
    monkeypatch.setattr(webapp, "_board_rows", lambda conn: rows)
    out = webapp.render_board(None).decode("utf-8")
    assert "pill crit chip" in out
    assert "May not be eligible" in out
    assert "Professional registration" in out


def test_gated_jobs_sort_to_the_top_of_their_group():
    import datetime as dt
    soon = dt.date.today() + dt.timedelta(days=1)
    later = dt.date.today() + dt.timedelta(days=30)
    rows = [_row("urgent_clean", closes_on=soon),
            _row("distant_gated", closes_on=later,
                 gate_flags=gate.gate("Australian citizenship is required."))]
    assert sorted(rows, key=webapp._sort_key)[0]["slug"] == "distant_gated"
