#!/usr/bin/env python3
"""T12 Revision learning — harvest your edits without regenerating anything.

Diffs every material file (and *_FINAL.md) in out/apply/*/ against its
.generated/ snapshot, records your line edits as reusable rules, and writes
out/learning_report.md. Future runs of scripts/apply.py re-apply these rules,
so regenerated drafts follow your wording instead of reverting it.

Usage: python3 scripts/learn.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proteus import db, revisions  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    conn = db.connect()
    n = revisions.learn_all(conn)
    report = revisions.report_md(conn)
    out = ROOT / "out" / "learning_report.md"
    out.write_text(report, encoding="utf-8")
    print(f"Harvested {n} new edit observation(s).")
    print(f"Report -> {out}")
    conn.close()


if __name__ == "__main__":
    main()
