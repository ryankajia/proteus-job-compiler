#!/usr/bin/env python3
"""T5 Sensing (compliance-safe) — ingest a JD from the clipboard in one step.

No scraping (charter compliance tier): YOU copy the job ad text in the browser,
then run this. It saves the JD to data/real_jobs/ and immediately builds the
full application package via scripts/apply.py.

Usage:
    1. Select + copy the whole job ad in your browser (Cmd+A on the ad, Cmd+C)
    2. python3 scripts/ingest.py my_job_name
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "data" / "real_jobs"


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else None
    text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
    if len(text) < 80:
        print("Clipboard looks empty (or too short to be a JD). Copy the job ad first.")
        sys.exit(1)
    if not name:
        first = next((ln for ln in text.splitlines() if ln.strip()), "job")
        name = re.sub(r"[^a-z0-9]+", "_", first.lower()).strip("_")[:48] or "job"
    JOBS.mkdir(parents=True, exist_ok=True)
    path = JOBS / f"{name}.txt"
    path.write_text(text, encoding="utf-8")
    print(f"Saved JD -> {path}")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "apply.py"), name], check=False)


if __name__ == "__main__":
    main()
