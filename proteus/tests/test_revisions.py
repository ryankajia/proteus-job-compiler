"""T12 revision learning — edits harvested from packages get re-applied."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import db, revisions  # noqa: E402

GENERATED = ("# Test Person\n\n26 July 2026\n\n"
             "- **Runs field surveys** (L3.5) [1]\n- old bullet to delete\n")


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def _package(tmp_path, name, edited):
    pkg = tmp_path / name
    pkg.mkdir()
    revisions.snapshot(pkg, "resume.md", GENERATED)
    (pkg / "resume.md").write_text(edited, encoding="utf-8")
    return pkg


def test_own_package_edit_always_sticks(conn, tmp_path):
    edited = GENERATED.replace("Runs field surveys", "Leads regional field surveys")
    pkg = _package(tmp_path, "job_a", edited)
    assert revisions.learn_package(conn, pkg) > 0

    # Regenerating job_a reproduces your wording…
    fresh, applied = revisions.apply_rules(conn, GENERATED, "resume.md", package="job_a")
    assert "Leads regional field surveys" in fresh and applied
    # …even when evidence refs were renumbered ([1] -> [2]).
    renumbered = GENERATED.replace("[1]", "[2]")
    fresh2, _ = revisions.apply_rules(conn, renumbered, "resume.md", package="job_a")
    assert "Leads regional field surveys" in fresh2
    # But one edit is job tailoring, not yet a global preference.
    other, _ = revisions.apply_rules(conn, GENERATED, "resume.md", package="job_x")
    assert "Runs field surveys" in other


def test_same_edit_twice_goes_global(conn, tmp_path):
    edited = GENERATED.replace("Runs field surveys", "Leads regional field surveys")
    revisions.learn_package(conn, _package(tmp_path, "job_a", edited))
    revisions.learn_package(conn, _package(tmp_path, "job_b", edited))
    fresh, _ = revisions.apply_rules(conn, GENERATED, "resume.md", package="job_x")
    assert "Leads regional field surveys" in fresh


def test_drop_needs_two_packages(conn, tmp_path):
    edited = GENERATED.replace("- old bullet to delete\n", "")
    revisions.learn_package(conn, _package(tmp_path, "job_a", edited))
    kept, _ = revisions.apply_rules(conn, GENERATED, "resume.md", package="job_x")
    assert "old bullet to delete" in kept          # one deletion: job-specific

    revisions.learn_package(conn, _package(tmp_path, "job_b", edited))
    dropped, _ = revisions.apply_rules(conn, GENERATED, "resume.md", package="job_x")
    assert "old bullet to delete" not in dropped   # two packages: your rule


def test_date_lines_never_learned(conn, tmp_path):
    edited = GENERATED.replace("26 July 2026", "25 July 2026")
    pkg = _package(tmp_path, "job_a", edited)
    assert revisions.learn_package(conn, pkg) == 0


def test_final_file_is_learned(conn, tmp_path):
    pkg = tmp_path / "job_c"
    pkg.mkdir()
    revisions.snapshot(pkg, "resume.md", GENERATED)
    (pkg / "resume.md").write_text(GENERATED, encoding="utf-8")
    (pkg / "resume_FINAL.md").write_text(
        GENERATED.replace("# Test Person", "# Test Person, Ph.D."), encoding="utf-8")
    revisions.learn_package(conn, pkg)
    fresh, _ = revisions.apply_rules(conn, GENERATED, "resume.md", package="job_c")
    assert "# Test Person, Ph.D." in fresh


def test_unedited_package_learns_nothing(conn, tmp_path):
    pkg = _package(tmp_path, "job_d", GENERATED)
    assert revisions.learn_package(conn, pkg) == 0
    assert revisions.apply_rules(conn, GENERATED, "resume.md")[1] == []
