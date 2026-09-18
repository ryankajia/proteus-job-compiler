#!/usr/bin/env python3
"""T11 Learning loop (lite) — record what happened, learn what works.

Usage:
    python3 scripts/outcome.py list                        # all tracked jobs + status
    python3 scripts/outcome.py set <substr> <status> [note]  # applied/interviewing/offer/rejected
    python3 scripts/outcome.py report                      # response-rate by persona & zone

Statuses: sensed, drafted, applied, interviewing, offer, rejected.
"""
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proteus import db  # noqa: E402

VALID = ("sensed", "drafted", "applied", "interviewing", "offer", "rejected")


def main() -> None:
    conn = db.connect()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        rows = conn.execute(
            "SELECT p.job_id, p.status, p.positioning_used, p.fit_score, j.title, j.org "
            "FROM pipeline p JOIN jobspecs j ON j.id = p.job_id ORDER BY p.updated_at DESC").fetchall()
        for r in rows:
            print(f"{r['status']:<13} fit={r['fit_score']:<7} {r['positioning_used'] or '-':<12} "
                  f"{r['title'][:60]}  ({r['org'] or '?'})  id={r['job_id']}")
        if not rows:
            print("Nothing tracked yet — run scripts/apply.py first.")

    elif cmd == "set" and len(sys.argv) >= 4:
        substr, status = sys.argv[2], sys.argv[3]
        note = " ".join(sys.argv[4:]) or None
        if status not in VALID:
            print(f"Status must be one of {VALID}")
            sys.exit(1)
        rows = conn.execute(
            "SELECT p.job_id, j.title FROM pipeline p JOIN jobspecs j ON j.id=p.job_id "
            "WHERE j.title LIKE ? OR p.job_id LIKE ? OR j.source LIKE ?",
            (f"%{substr}%", f"%{substr}%", f"%{substr}%")).fetchall()
        if len(rows) != 1:
            print(f"Matched {len(rows)} jobs for '{substr}' — be more specific:")
            for r in rows:
                print(f"  {r['job_id']}  {r['title']}")
            sys.exit(1)
        now = _dt.datetime.now().isoformat(timespec="seconds")
        conn.execute("UPDATE pipeline SET status=?, updated_at=? WHERE job_id=?",
                     (status, now, rows[0]["job_id"]))
        conn.execute("INSERT INTO events(job_id, event, note, created_at) VALUES(?,?,?,?)",
                     (rows[0]["job_id"], status, note, now))
        conn.commit()
        print(f"✓ {rows[0]['title']} -> {status}" + (f"  ({note})" if note else ""))

    elif cmd == "report":
        rows = conn.execute(
            "SELECT p.status, p.positioning_used, COUNT(*) n FROM pipeline p "
            "GROUP BY p.status, p.positioning_used").fetchall()
        print("status        persona       n")
        for r in rows:
            print(f"{r['status']:<13} {r['positioning_used'] or '-':<13} {r['n']}")
        applied = conn.execute("SELECT COUNT(*) n FROM pipeline WHERE status NOT IN ('sensed','drafted')").fetchone()["n"]
        moved = conn.execute("SELECT COUNT(*) n FROM pipeline WHERE status IN ('interviewing','offer')").fetchone()["n"]
        if applied:
            print(f"\nResponse rate: {moved}/{applied} applications progressed.")
    else:
        print(__doc__)
    conn.close()


if __name__ == "__main__":
    main()
