#!/usr/bin/env python3
"""Monthly count of work that was done and never sent.

The one failure mode where the cost is fully paid and the return is exactly
zero: documents finished, Submit never pressed, vacancy now closed. Counting it
is the point — an unmeasured failure repeats.

    python3 scripts/wasted.py           # print the table
    python3 scripts/wasted.py --append  # append it to the decision log
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proteus import db, webapp  # noqa: E402

LOG = Path(__file__).resolve().parent.parent.parent / \
    "my-materials" / "reference" / "DECISIONS.md"


def build() -> str:
    conn = db.connect()
    rows = webapp._board_rows(conn)
    wasted = [r for r in rows if webapp.is_expired_unsubmitted(r)]
    at_risk = [r for r in rows if webapp.is_ready_not_submitted(r)]

    by_month = Counter(
        r["closes_on"].strftime("%Y-%m") for r in wasted if r["closes_on"])
    undated = sum(1 for r in wasted if not r["closes_on"])

    out = ["## Written but never sent", ""]
    if not wasted:
        out.append("None on record. Every finished package was submitted.")
    else:
        out.append(f"**{len(wasted)} finished and never submitted.** "
                   "Cost fully paid, return zero.")
        out += ["", "| Month closed | Lost this way |", "|---|---|"]
        for month, n in sorted(by_month.items()):
            out.append(f"| {month} | {n} |")
        if undated:
            out.append(f"| (no closing date recorded) | {undated} |")
        out += ["", "Which ones:"]
        out += [f"- {r['title'] or r['slug']}" for r in wasted]
    if at_risk:
        out += ["", f"**{len(at_risk)} currently ready and unsent** — still recoverable:"]
        out += [f"- {r['title'] or r['slug']} ({webapp._days_word(r)})" for r in at_risk]
    conn.close()
    return "\n".join(out)


def main() -> None:
    text = build()
    print(text)
    if "--append" in sys.argv and LOG.exists():
        import datetime as dt
        stamp = dt.date.today().isoformat()
        LOG.write_text(LOG.read_text(encoding="utf-8").rstrip()
                       + f"\n\n---\n\n*Wasted-work count, {stamp}*\n\n{text}\n",
                       encoding="utf-8")
        print(f"\nAppended to {LOG}")


if __name__ == "__main__":
    main()
