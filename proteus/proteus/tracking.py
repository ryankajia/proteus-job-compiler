"""Small persistent checklist for each real application."""
from __future__ import annotations

import datetime as dt
import sqlite3

ITEMS = (
    ("cv", "CV / résumé"),
    ("cover_letter", "Cover letter"),
    ("statement", "Selection statement / pitch"),
    ("referees", "Referees"),
    ("other", "Other requirement"),
    ("portal_submitted", "Pressed Submit on employer site"),
    ("confirmation_received", "Application confirmation received"),
)
ITEM_KEYS = tuple(key for key, _ in ITEMS)


def ensure(conn: sqlite3.Connection, job_id: str) -> None:
    for key, _ in ITEMS:
        required = int(key in ("cv", "portal_submitted", "confirmation_received"))
        conn.execute(
            "INSERT OR IGNORE INTO application_requirements(job_id,item,required) VALUES(?,?,?)",
            (job_id, key, required),
        )
    conn.commit()


def get(conn: sqlite3.Connection, job_id: str) -> dict[str, dict]:
    ensure(conn, job_id)
    return {r["item"]: dict(r) for r in conn.execute(
        "SELECT item,required,done,detail FROM application_requirements WHERE job_id=?", (job_id,)
    )}


def save(conn: sqlite3.Connection, job_id: str, values: dict[str, dict]) -> None:
    ensure(conn, job_id)
    old = get(conn, job_id)
    for key in ITEM_KEYS:
        v = values.get(key, {})
        required = int(key in ("portal_submitted", "confirmation_received") or bool(v.get("required")))
        done = int(bool(v.get("done")))
        detail = str(v.get("detail") or "").strip()[:1000]
        conn.execute(
            "UPDATE application_requirements SET required=?,done=?,detail=? WHERE job_id=? AND item=?",
            (required, done, detail, job_id, key),
        )
    if values.get("confirmation_received", {}).get("done") and not old["confirmation_received"]["done"]:
        now = dt.datetime.now().isoformat(timespec="seconds")
        conn.execute("UPDATE pipeline SET status='applied',updated_at=? WHERE job_id=?", (now, job_id))
        conn.execute(
            "INSERT INTO events(job_id,event,note,created_at) VALUES(?,?,?,?)",
            (job_id, "applied", "Application confirmation received", now),
        )
    conn.commit()


def progress(conn: sqlite3.Connection, job_id: str) -> tuple[int, int]:
    rows = get(conn, job_id)
    required = [v for v in rows.values() if v["required"]]
    return sum(bool(v["done"]) for v in required), len(required)
