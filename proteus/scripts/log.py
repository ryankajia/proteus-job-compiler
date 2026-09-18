#!/usr/bin/env python3
"""T13 Application log — write out/application_log.md, or add a note.

Usage:
    python3 scripts/log.py                          # rebuild the log
    python3 scripts/log.py note <substr> "text"     # record something that happened

Examples:
    python3 scripts/log.py note example_inst "HR called to confirm receipt"
    python3 scripts/log.py note my_job_name "received the confirmation email"

Status changes (applied / interviewing / offer / rejected) go through
scripts/outcome.py or the board's dropdown — they land in the log automatically.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proteus import applog, db  # noqa: E402


def main() -> None:
    conn = db.connect()
    args = sys.argv[1:]

    if args and args[0] == "note":
        if len(args) < 3:
            print('usage: log.py note <substr> "text"')
            sys.exit(1)
        substr, text = args[1], " ".join(args[2:])
        rows = conn.execute(
            "SELECT id, title FROM jobspecs WHERE title LIKE ? OR id LIKE ? OR source LIKE ?",
            (f"%{substr}%", f"%{substr}%", f"%{substr}%")).fetchall()
        if len(rows) != 1:
            print(f"Matched {len(rows)} jobs for '{substr}' — be more specific:")
            for r in rows:
                print(f"  {r['id']}  {r['title']}")
            sys.exit(1)
        applog.add_note(conn, rows[0]["id"], text)
        print(f"recorded: {rows[0]['title']} — {text}")

    path = applog.write(conn)
    print(f"log -> {path}")
    conn.close()


if __name__ == "__main__":
    main()
