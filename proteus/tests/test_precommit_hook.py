"""The pre-commit hook must actually refuse a commit.

Driven through real `git commit` calls in a throwaway repository rather than by
calling the script directly: a hook that works when invoked by hand but not
when git runs it protects nothing.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
HOOK = REPO / ".githooks" / "pre-commit"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or not HOOK.is_file(),
                                reason="needs git and the hook")


def _git(repo: Path, *args, **kw):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, errors="replace", **kw)


@pytest.fixture()
def repo(tmp_path):
    """A real repository with the hook installed exactly as the README says."""
    r = tmp_path / "r"
    (r / ".githooks").mkdir(parents=True)
    shutil.copy(HOOK, r / ".githooks" / "pre-commit")
    os.chmod(r / ".githooks" / "pre-commit", 0o755)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    _git(r, "config", "core.hooksPath", ".githooks")
    (r / ".githooks" / "secrets.txt").write_text(
        "# comment line\nsecret.person@example.com\nAcme Grant 0001\n\n",
        encoding="utf-8")
    return r


def _commit(repo: Path, message="m"):
    return _git(repo, "commit", "-m", message)


# ------------------------------------------------------------ refusals ----

def test_a_secret_string_is_refused(repo):
    (repo / "notes.md").write_text("Contact secret.person@example.com for access.\n",
                                   encoding="utf-8")
    _git(repo, "add", "notes.md")
    out = _commit(repo)
    assert out.returncode != 0, "the commit should have been refused"
    assert "secret.person@example.com" in (out.stdout + out.stderr)
    assert "notes.md" in (out.stdout + out.stderr)
    # and nothing was recorded
    assert _git(repo, "log", "--oneline").returncode != 0 or \
        _git(repo, "log", "--oneline").stdout.strip() == ""


def test_the_match_is_case_insensitive(repo):
    (repo / "a.md").write_text("Funded by ACME GRANT 0001.\n", encoding="utf-8")
    _git(repo, "add", "a.md")
    assert _commit(repo).returncode != 0


def test_a_private_path_is_refused_even_with_no_secrets_file(repo):
    """Structural rules live in the hook, so they survive a missing list."""
    (repo / ".githooks" / "secrets.txt").unlink()
    (repo / "my-materials").mkdir()
    (repo / "my-materials" / "cv.txt").write_text("anything at all\n", encoding="utf-8")
    _git(repo, "add", "my-materials/cv.txt")
    out = _commit(repo)
    assert out.returncode != 0
    assert "my-materials" in (out.stdout + out.stderr)


@pytest.mark.parametrize("path", [
    "seed_identity.json", "calibration_levels.json",
    "proteus/out/apply/x/resume.md", "Hui_CV.docx", "notes.pdf",
    "proteus/data/real_jobs/a_job.txt",
])
def test_paths_that_must_never_be_committed(repo, path):
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-f", path)
    out = _commit(repo)
    assert out.returncode != 0, f"{path} should be blocked"


# ------------------------------------------------------- what may pass ----

def test_a_clean_commit_is_allowed(repo):
    (repo / "code.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "code.py")
    out = _commit(repo)
    assert out.returncode == 0, out.stdout + out.stderr
    assert _git(repo, "log", "--oneline").stdout.strip()


def test_test_fixtures_are_allowed_through(repo):
    """They are named like the private files but are invented data."""
    f = repo / "proteus" / "tests" / "fixtures" / "seed_identity.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('{"profile": {"name": "Test Person"}}\n', encoding="utf-8")
    _git(repo, "add", "proteus/tests/fixtures/seed_identity.json")
    assert _commit(repo).returncode == 0


def test_templates_are_allowed_through(repo):
    f = repo / "data" / "seed_identity.template.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('{"profile": {"name": "Your Name"}}\n', encoding="utf-8")
    _git(repo, "add", "data/seed_identity.template.json")
    assert _commit(repo).returncode == 0


def test_only_added_lines_are_scanned(repo):
    """An existing mention must not block every future commit."""
    (repo / "old.md").write_text("secret.person@example.com was here\n", encoding="utf-8")
    _git(repo, "add", "old.md")
    _git(repo, "commit", "-m", "seed", "--no-verify")

    (repo / "old.md").write_text("secret.person@example.com was here\nA new clean line.\n",
                                 encoding="utf-8")
    _git(repo, "add", "old.md")
    assert _commit(repo).returncode == 0, "editing elsewhere in the file must not be blocked"


def test_comments_in_the_list_are_not_treated_as_terms(repo):
    (repo / "c.md").write_text("This mentions a comment line, harmlessly.\n", encoding="utf-8")
    _git(repo, "add", "c.md")
    assert _commit(repo).returncode == 0


def test_bypass_is_possible_for_a_deliberate_override(repo):
    (repo / "n.md").write_text("secret.person@example.com\n", encoding="utf-8")
    _git(repo, "add", "n.md")
    assert _commit(repo).returncode != 0
    out = _git(repo, "commit", "-m", "deliberate", "--no-verify")
    assert out.returncode == 0, "--no-verify must still work; the hook is a guard, not a cage"


def test_the_shipped_hook_compiles():
    subprocess.run([sys.executable, "-m", "py_compile", str(HOOK)], check=True)


def test_the_secret_list_itself_cannot_be_committed(repo):
    """Committing it would publish an index of exactly what is hidden."""
    _git(repo, "add", "-f", ".githooks/secrets.txt")
    out = _commit(repo)
    assert out.returncode != 0
    assert "secret list" in (out.stdout + out.stderr)
