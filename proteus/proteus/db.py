"""L0 storage — SQLite schema and connection for the Identity Graph and Pipeline.

Invariant (per the Fable 5 charter): schema changes must stay backward
compatible. Bump SCHEMA_VERSION and add a migration rather than dropping data.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 6

DEFAULT_DB = Path(__file__).resolve().parent.parent / "databases" / "proteus.db"


def connect(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init_schema(conn)
    migrate(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        -- L0 Identity Core --------------------------------------------------
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            data TEXT NOT NULL              -- JSON blob of profile fields
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,             -- paper/repo/url/project/award
            ref TEXT NOT NULL,
            verifiable INTEGER NOT NULL DEFAULT 0,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS capability_units (
            id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            statement TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '[]',   -- JSON array
            evidence_ids TEXT NOT NULL DEFAULT '[]'-- JSON array of evidence.id
        );

        -- Calibration: evidence-driven level, never user-supplied ----------
        CREATE TABLE IF NOT EXISTS calibration (
            capability_id TEXT PRIMARY KEY REFERENCES capability_units(id) ON DELETE CASCADE,
            level REAL NOT NULL,            -- 1-5, may be fractional
            rationale TEXT NOT NULL,
            source TEXT NOT NULL,           -- 'llm' | 'analyst' | 'seed'
            assessed_at TEXT NOT NULL
        );

        -- L1 JobSpec + L5 Pipeline -----------------------------------------
        CREATE TABLE IF NOT EXISTS jobspecs (
            id TEXT PRIMARY KEY,
            title TEXT, org TEXT, seniority TEXT,
            required_skills TEXT NOT NULL DEFAULT '[]',
            nice_to_have TEXT NOT NULL DEFAULT '[]',
            visa_requirement TEXT,
            security_clearance INTEGER NOT NULL DEFAULT 0,
            salary TEXT, deadline TEXT, closes_at TEXT, apply_channel TEXT,
            source TEXT, raw_text TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pipeline (
            job_id TEXT PRIMARY KEY REFERENCES jobspecs(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'sensed',
            positioning_used TEXT,
            fit_score REAL,
            stretch_score REAL,
            deadline TEXT,
            updated_at TEXT NOT NULL
        );

        -- v2: T11 learning loop — an append-only log of what happened to each
        -- application (drafted / applied / response / interview / outcome).
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobspecs(id) ON DELETE CASCADE,
            event TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );

        -- v3: per-job upload and submission checklist. A job can be tracked
        -- before any documents are generated.
        CREATE TABLE IF NOT EXISTS application_requirements (
            job_id TEXT NOT NULL REFERENCES jobspecs(id) ON DELETE CASCADE,
            item TEXT NOT NULL,
            required INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            detail TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(job_id, item)
        );
        """
    )
    cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    elif int(row["value"]) < SCHEMA_VERSION:
        # Additive tables are created above; bump the marker without losing data.
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
    conn.commit()


def dumps(obj) -> str:
    """Deterministic JSON — sorted keys keep prompt caches and diffs stable."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Add a column to an existing database without losing what is in it.

    Existing installs were created before closes_at existed; recreating the
    table would throw away a real job hunt, so widen it in place instead.
    """
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema. Safe to re-run."""
    _ensure_column(conn, "jobspecs", "closes_at", "TEXT")
    _ensure_column(conn, "pipeline", "started_on_portal", "TEXT")
    _ensure_column(conn, "pipeline", "interested", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "pipeline", "submitted_at", "TEXT")
    _ensure_column(conn, "pipeline", "rejected_at", "TEXT")
    _ensure_column(conn, "pipeline", "role_category", "TEXT")
    conn.commit()
