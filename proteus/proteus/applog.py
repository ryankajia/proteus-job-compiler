"""T13 Application log — a dated record of what was sent and what came back.

Everything here is derived from the `events` table, which already records every
status change (scripts/outcome.py and the board's status dropdown both write to
it). This module turns that history into a diary you can read: what happened on
each day, what is still waiting, and what needs doing next.

No LLM involved — pure SQL and string formatting.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

from . import jobspec

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "out" / "application_log.md"

# Labels are deliberately neutral — see webapp.STATUS_LABEL. Keys unchanged.
EVENT_LABEL = {
    "drafted": ("📝", "Docs built"),
    "applied": ("📤", "Submitted"),
    "interviewing": ("🎤", "In progress"),
    "offer": ("🎉", "Offer"),
    "rejected": ("❌", "Closed"),
    "sensed": ("👀", "Entry added"),
}
# Statuses that mean the application is live and awaiting a reply.
WAITING = ("applied", "interviewing")


def add_note(conn: sqlite3.Connection, job_id: str, note: str,
             when: str | None = None) -> None:
    """Record a free-text note against a job (a recruiter call, an email, …)."""
    conn.execute("INSERT INTO events(job_id, event, note, created_at) VALUES(?,?,?,?)",
                 (job_id, "note", note,
                  when or _dt.datetime.now().isoformat(timespec="seconds")))
    conn.commit()


def _days_since(iso: str) -> int | None:
    try:
        d = _dt.datetime.fromisoformat(iso).date()
    except ValueError:
        return None
    return (_dt.date.today() - d).days


def build(conn: sqlite3.Connection) -> str:
    hidden = jobspec.hidden_sources()
    jobs = {r["id"]: r for r in conn.execute(
        "SELECT id, title, org, deadline, source FROM jobspecs")
        if r["source"] not in hidden}
    rows = conn.execute(
        "SELECT job_id, event, note, created_at FROM events ORDER BY created_at DESC").fetchall()
    pipe = {r["job_id"]: r for r in conn.execute(
        "SELECT job_id, status, updated_at, fit_score FROM pipeline")
        if r["job_id"] in jobs}

    today = _dt.date.today().isoformat()
    out = ["# Activity log", "",
           f"*Generated {today}. Every status change is recorded here automatically.*", ""]

    # ---- current state, most useful at the top ---------------------------
    live = [(jid, p) for jid, p in pipe.items()
            if p["status"] in WAITING and jid in jobs]
    out.append("## 📮 Waiting to hear back")
    out.append("")
    if not live:
        out.append("Nothing is currently waiting on a reply.")
    else:
        out.append("| Name | Organisation | Waiting | Progress |")
        out.append("|---|---|---|---|")
        for jid, p in sorted(live, key=lambda kv: kv[1]["updated_at"]):
            j = jobs[jid]
            d = _days_since(p["updated_at"])
            waited = "today" if d == 0 else f"{d} days" if d is not None else "—"
            _, label = EVENT_LABEL.get(p["status"], ("", p["status"]))
            out.append(f"| {j['title']} | {j['org'] or '—'} | {waited} | {label} |")
        out.append("")
        out.append("> Australian academic and public-sector roles typically take 2–6 weeks "
                   "to respond. After 4 weeks with no word, a polite follow-up email is reasonable.")
    out.append("")

    # ---- still to send ---------------------------------------------------
    todo = [(jid, p) for jid, p in pipe.items()
            if p["status"] in ("sensed", "drafted") and jid in jobs]
    out.append("## 📋 Documents ready — NOT YET SUBMITTED")
    out.append("")
    if not todo:
        out.append("Nothing is sitting unsubmitted — everything prepared has been sent.")
    else:
        out.append("| Name | Organisation | Closes | Fit |")
        out.append("|---|---|---|---|")
        for jid, p in sorted(todo, key=lambda kv: -(kv[1]["fit_score"] or 0)):
            j = jobs[jid]
            out.append(f"| {j['title']} | {j['org'] or '—'} | "
                       f"{j['deadline'] or 'not recorded'} | {p['fit_score'] or '—'} |")
    out.append("")

    # ---- the diary itself ------------------------------------------------
    out.append("## 🗓 Day by day")
    out.append("")
    if not rows:
        out.append("Nothing recorded yet.")
    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(r["created_at"][:10], []).append(r)
    for day in sorted(by_day, reverse=True):
        entries = by_day[day]
        # One package regeneration touches every job; that is machine noise,
        # not something you did — collapse it to a single line.
        drafted = [e for e in entries if e["event"] == "drafted"]
        real = [e for e in entries if e["event"] != "drafted"]
        out.append(f"### {day}" + (" (today)" if day == today else ""))
        out.append("")
        for e in real:
            j = jobs.get(e["job_id"])
            if not j:
                continue
            icon, label = EVENT_LABEL.get(e["event"], ("·", e["event"]))
            note = f" — {e['note']}" if e["note"] else ""
            out.append(f"- {icon} **{label}**：{j['title']}"
                       f"{'（' + j['org'] + '）' if j['org'] else ''}{note}")
        if drafted:
            names = {jobs[e["job_id"]]["title"] for e in drafted if e["job_id"] in jobs}
            out.append(f"- 📝 Documents built or updated: {len(names)} entries")
        out.append("")

    out.append("---")
    out.append("")
    out.append("**Recording progress:** change an entry's status on the board to "
               "In progress / Offer / Closed, or run "
               "`python3 scripts/outcome.py set <keyword> interviewing \"phone screen 3 Aug\"`. "
               "This log updates itself.")
    return "\n".join(out)


def write(conn: sqlite3.Connection) -> Path:
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text(build(conn), encoding="utf-8")
    return LOG
