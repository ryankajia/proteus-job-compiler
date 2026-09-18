#!/usr/bin/env python3
"""Seed the Identity Graph from the CV and run Calibration.

Usage: python scripts/seed.py [--llm]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proteus import calibration, db, identity  # noqa: E402


def main() -> None:
    use_llm = "--llm" in sys.argv
    conn = db.connect()
    counts = identity.load_seed(conn)
    print(f"Loaded {counts['capabilities']} capability units, {counts['evidence']} evidence items.")
    n = calibration.calibrate(conn, use_llm=use_llm)
    src = "Claude (live)" if use_llm else "analyst seed"
    print(f"Calibrated {n} capabilities via {src}.")
    conn.close()


if __name__ == "__main__":
    main()
