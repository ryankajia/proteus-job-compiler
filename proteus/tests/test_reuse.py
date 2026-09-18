"""Reuse of previously approved criterion answers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proteus import reuse  # noqa: E402

PAST = """# Statement addressing the selection criteria — Laboratory Support Officer

## 3. Proven organisational skills and attention to detail, with a demonstrated ability to prioritise competing deadlines

I run several programme cycles at once — annual reporting, mid-term review and
budget schedules — each with its own deadline and its own contributors. I break
each funder requirement into defined tasks and hold the master schedule, which
keeps the workload controllable and puts responsibility with the person who owns
the work.

## 4. Understanding of equal opportunity principles and work health and safety

I have worked in laboratory environments for twenty years, where safety
procedures and incident reporting are part of daily operations. I support equal
opportunity principles and have worked in culturally mixed teams across three
countries, and I am willing to undertake First Aid Officer duties.
"""

# The same requirement, worded the public-sector way.
NEW_CRIT = ("Proven organisational skills and ability to prioritise own workload and to work "
            "effectively both independently and as part of a team, meeting demanding and "
            "competing deadlines while delivering high quality outcomes.")

UNRELATED = ("Current registration as a medical radiation practitioner with AHPRA and "
             "demonstrated experience operating linear accelerator equipment.")


def test_sections_strip_scaffolding_and_keep_prose():
    md = PAST + "\n**Claim:** something generated (L3.0/5)\n- ✓ (repo) evidence line\n"
    secs = reuse._sections(md)
    assert len(secs) == 2
    joined = " ".join(body for _, body in secs)
    assert "Claim:" not in joined and "✓ (repo)" not in joined
    # Leading numbering is stripped from the heading.
    assert secs[0][0].startswith("Proven organisational skills")


def test_matches_the_same_criterion_worded_differently(tmp_path):
    bank = [{"criterion": h, "answer": b, "package": "example_support_officer"}
            for h, b in reuse._sections(PAST)]
    hit = reuse.best_match(NEW_CRIT, bank)
    assert hit is not None
    assert "master schedule" in hit["answer"]
    assert hit["score"] > reuse.best_match.__defaults__[0]


def test_does_not_match_an_unrelated_criterion():
    bank = [{"criterion": h, "answer": b, "package": "p"} for h, b in reuse._sections(PAST)]
    assert reuse.best_match(UNRELATED, bank) is None


def test_short_fragments_are_not_banked():
    md = "## Some criterion\n\nToo short to be a real answer.\n"
    assert reuse._sections(md) == []


def test_load_bank_can_exclude_a_package():
    everything = reuse.load_bank()
    if not everything:
        return  # no packages on disk in this checkout
    pkg = everything[0]["package"]
    assert all(item["package"] != pkg
               for item in reuse.load_bank(exclude_package=pkg))
