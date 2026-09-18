"""L0 Identity Core — load the seed graph and query capability units + evidence."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import db

SEED = Path(__file__).resolve().parent.parent / "data" / "seed_identity.json"


def load_seed(conn: sqlite3.Connection, seed_path: Path | None = None) -> dict:
    data = json.loads((seed_path or SEED).read_text(encoding="utf-8"))

    conn.execute(
        "INSERT OR REPLACE INTO profile(id, data) VALUES(1, ?)",
        (db.dumps(data["profile"]),),
    )
    for ev in data["evidence"]:
        conn.execute(
            "INSERT OR REPLACE INTO evidence(id, type, ref, verifiable, note) VALUES(?,?,?,?,?)",
            (ev["id"], ev["type"], ev["ref"], int(ev.get("verifiable", False)), ev.get("note")),
        )
    for cap in data["capability_units"]:
        conn.execute(
            "INSERT OR REPLACE INTO capability_units(id, domain, statement, keywords, evidence_ids) "
            "VALUES(?,?,?,?,?)",
            (
                cap["id"], cap["domain"], cap["statement"],
                db.dumps(cap.get("keywords", [])), db.dumps(cap.get("evidence", [])),
            ),
        )
    conn.commit()
    return {"evidence": len(data["evidence"]), "capabilities": len(data["capability_units"])}


def get_profile(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT data FROM profile WHERE id=1").fetchone()
    return json.loads(row["data"]) if row else {}


def get_capabilities(conn: sqlite3.Connection) -> list[dict]:
    caps = []
    for r in conn.execute("SELECT * FROM capability_units ORDER BY id"):
        caps.append({
            "id": r["id"], "domain": r["domain"], "statement": r["statement"],
            "keywords": json.loads(r["keywords"]), "evidence": json.loads(r["evidence_ids"]),
        })
    return caps


def get_evidence(conn: sqlite3.Connection, ev_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM evidence WHERE id=?", (ev_id,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "type": r["type"], "ref": r["ref"],
            "verifiable": bool(r["verifiable"]), "note": r["note"]}
