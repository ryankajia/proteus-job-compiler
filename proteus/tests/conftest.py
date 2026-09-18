"""Tests run against an invented person, never the real user's data.

`data/seed_identity.json` and `data/calibration_levels.json` are private and
git-ignored, so on a fresh clone they do not exist and fourteen tests errored
with FileNotFoundError. Pointing the loaders at `tests/fixtures/` fixes that
and also stops a change to somebody's real CV from breaking a test.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import calibration, identity  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _use_fixture_identity(monkeypatch):
    monkeypatch.setattr(identity, "SEED", FIXTURES / "seed_identity.json")
    monkeypatch.setattr(calibration, "_LEVELS_FILE", FIXTURES / "calibration_levels.json")
    monkeypatch.setattr(
        calibration, "ANALYST_ASSESSMENTS", calibration._load_assessments())
    yield
