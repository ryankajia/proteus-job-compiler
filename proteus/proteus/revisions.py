"""T12 Revision learning — the system learns from your own edits.

Every generated material file gets a pristine snapshot in the package's
`.generated/` folder. When you edits a draft (in the webapp editor or any
external editor) or polishes a `*_FINAL.md`, `learn()` diffs your version
against the snapshot and records line-level edits in the `revisions` table.

Rules are then re-applied to future compiler output via `apply_rules()`:

- **same package**: your edits always stick — regenerating a package reapplies
  every edit you made in it, so a re-sync can no longer revert your wording.
- **globally**: an edit crosses over to other jobs only once you have made the
  SAME edit in >= 2 different packages. One edit may be job-specific tailoring
  ("Dear Dr Gotsis"); the same edit twice is a preference ("Aug" -> "August").
- Lines that are just a date are never learned (the letter date is stale by
  design, not a preference).

Truthfulness invariant: rules only ever contain text you wrote yourself,
so applying them can never invent new claims.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "out" / "apply"
SNAP_DIR = ".generated"

# Material files whose edits are worth learning (reports like review/gap are
# regenerated analysis, not authored material).
LEARNABLE = ("resume.md", "cover_letter.md", "selection_criteria.md",
             "selection_criteria_draft.md")
FINAL_OF = {"resume.md": "resume_FINAL.md",
            "cover_letter.md": "cover_letter_FINAL.md",
            "selection_criteria.md": "selection_criteria_FINAL.md",
            "selection_criteria_draft.md": "selection_criteria_FINAL.md"}

GLOBAL_THRESHOLD = 2  # distinct packages making the same edit before it goes global

# Lines that are nothing but a date — the letter date goes stale between
# generations; learning it as a rewrite would pin every future letter to it.
_DATE_LINE = re.compile(r"^\d{1,2} [A-Z][a-z]+ \d{4}$")

# Worksheet scaffolding, not authored prose. selection_criteria.md is a
# work surface: the Claim/evidence lines exist to be read, used and then
# deleted while writing the real answer. Deleting them is the intended
# workflow, so a diff must never read it as "you wants these gone forever" —
# doing so taught the compiler to strip the evidence out of every future
# package, which is exactly how the criteria of one real
# draft came out blank (2026-09-09).
_SCAFFOLD_LINE = re.compile(
    r"^(?:"
    r"\*\*Claim:\*\*"          # capability claim headers
    r"|- ✓ \("                  # evidence-ledger rows: - ✓ (repo) / (paper) / …
    r"|- \*\*"                  # bolded capability bullets in the evidence bank
    r"|> ?✍️"                    # the yellow ✍️ writing-instruction stickies
    r"|## Criterion \d"          # criterion headings (they come from the PD)
    r"|-{3,}$"                   # horizontal rules
    r")")


# Only the selection-criteria worksheet has scaffolding. A resume bullet
# like "- **Runs field surveys** (L3.5)" is authored content and stays learnable.
_SCAFFOLD_FILES = ("selection_criteria.md", "selection_criteria_draft.md",
                   "selection_criteria_FINAL.md")


def _is_scaffold(line: str, fname: str = "selection_criteria.md") -> bool:
    """True for generated worksheet furniture that must not be learned from."""
    if fname not in _SCAFFOLD_FILES:
        return False
    return bool(_SCAFFOLD_LINE.match(line.strip()))


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,             -- 'rewrite' | 'drop'
            fname TEXT NOT NULL,            -- which material file the edit came from
            old_line TEXT NOT NULL,         -- normalized generated line
            new_line TEXT NOT NULL DEFAULT '',
            package TEXT NOT NULL,          -- package the edit was observed in
            observed_at TEXT NOT NULL,
            UNIQUE(kind, fname, old_line, new_line, package)
        )""")
    conn.commit()


def _norm(line: str) -> str:
    """Normalize a line for matching: collapse whitespace, strip evidence refs
    like [1] [2] (their numbering shifts between compiles)."""
    line = re.sub(r"\s*\[\d+\]", "", line)
    return re.sub(r"\s+", " ", line).strip()


def _snap_path(pkg_dir: Path, fname: str) -> Path:
    return pkg_dir / SNAP_DIR / fname


def snapshot(pkg_dir: Path, fname: str, content: str) -> None:
    """Record what the system actually wrote, so later diffs see only user edits."""
    p = _snap_path(pkg_dir, fname)
    p.parent.mkdir(exist_ok=True)
    p.write_text(content, encoding="utf-8")


def user_edited(pkg_dir: Path, fname: str) -> bool:
    f, snap = pkg_dir / fname, _snap_path(pkg_dir, fname)
    if not f.exists() or not snap.exists():
        return False
    return f.read_text(encoding="utf-8") != snap.read_text(encoding="utf-8")


def _diff_edits(generated: str, edited: str,
                fname: str = "selection_criteria.md") -> list[dict]:
    """Line-level diff -> edit observations. 'replace' blocks pair up 1:1 in
    order; unpaired removals count as deletions."""
    a = [_norm(ln) for ln in generated.splitlines()]
    b = [_norm(ln) for ln in edited.splitlines()]
    edits: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "replace":
            olds, news = a[i1:i2], b[j1:j2]
            for k, old in enumerate(olds):
                if not old or _DATE_LINE.match(old) or _is_scaffold(old, fname):
                    continue
                if k < len(news) and news[k]:
                    edits.append({"kind": "rewrite", "old": old, "new": news[k]})
                else:
                    edits.append({"kind": "drop", "old": old, "new": ""})
        elif tag == "delete":
            for old in a[i1:i2]:
                if old and not _DATE_LINE.match(old) and not _is_scaffold(old, fname):
                    edits.append({"kind": "drop", "old": old, "new": ""})
    return edits


def learn_package(conn: sqlite3.Connection, pkg_dir: Path) -> int:
    """Harvest edits from one package: draft-vs-snapshot and FINAL-vs-snapshot."""
    _ensure_table(conn)
    now = _dt.datetime.now().isoformat(timespec="seconds")
    found = 0
    for fname in LEARNABLE:
        snap = _snap_path(pkg_dir, fname)
        if not snap.exists():
            continue
        generated = snap.read_text(encoding="utf-8")
        sources = []
        draft = pkg_dir / fname
        if draft.exists():
            sources.append(draft)
        final = pkg_dir / FINAL_OF[fname]
        if final.exists():
            sources.append(final)
        for src in sources:
            edited = src.read_text(encoding="utf-8")
            if edited == generated:
                continue
            for e in _diff_edits(generated, edited, fname):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO revisions(kind, fname, old_line, new_line, package, observed_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (e["kind"], fname, e["old"], e["new"], pkg_dir.name, now))
                found += cur.rowcount
    conn.commit()
    return found


def learn_all(conn: sqlite3.Connection) -> int:
    if not PKG.is_dir():
        return 0
    return sum(learn_package(conn, d) for d in sorted(PKG.iterdir())
               if d.is_dir() and not d.name.startswith("_"))


def get_rules(conn: sqlite3.Connection, fname: str, package: str | None = None) -> dict:
    """Active rules for a material file.

    Global tier: the identical edit observed in >= GLOBAL_THRESHOLD distinct
    packages. Package tier: every edit observed in `package` itself (your
    tailoring of this job must survive regeneration). Package rules win."""
    _ensure_table(conn)
    rewrites: dict[str, str] = {}
    for r in conn.execute(
            "SELECT old_line, new_line, COUNT(DISTINCT package) AS n FROM revisions "
            "WHERE kind='rewrite' AND fname=? GROUP BY old_line, new_line "
            "HAVING n >= ? ORDER BY MAX(observed_at)", (fname, GLOBAL_THRESHOLD)):
        rewrites[r["old_line"]] = r["new_line"]
    drops = {r["old_line"] for r in conn.execute(
        "SELECT old_line, COUNT(DISTINCT package) AS n FROM revisions "
        "WHERE kind='drop' AND fname=? GROUP BY old_line HAVING n >= ?",
        (fname, GLOBAL_THRESHOLD))}
    if package:
        for r in conn.execute(
                "SELECT kind, old_line, new_line FROM revisions "
                "WHERE fname=? AND package=? ORDER BY observed_at", (fname, package)):
            if r["kind"] == "rewrite":
                rewrites[r["old_line"]] = r["new_line"]
                drops.discard(r["old_line"])
            else:
                drops.add(r["old_line"])
    # A line you rewrote is not a line you wants gone.
    drops -= set(rewrites)
    return {"rewrites": rewrites, "drops": drops}


def apply_rules(conn: sqlite3.Connection, text: str, fname: str,
                package: str | None = None) -> tuple[str, list[str]]:
    """Re-apply learned edits to freshly compiled text (exact line match on the
    normalized form; the original line's evidence refs are kept on rewrites
    only if you kept them in your version)."""
    rules = get_rules(conn, fname, package)
    if not rules["rewrites"] and not rules["drops"]:
        return text, []
    out, applied = [], []
    for line in text.splitlines():
        key = _norm(line)
        if key in rules["drops"]:
            applied.append(f"dropped: {key[:60]}")
            continue
        if key in rules["rewrites"] and rules["rewrites"][key] != key:
            new = rules["rewrites"][key]
            indent = line[:len(line) - len(line.lstrip())]
            out.append(indent + new)
            applied.append(f"rewrote: {key[:40]} -> {new[:40]}")
            continue
        out.append(line)
    return "\n".join(out), applied


def report_md(conn: sqlite3.Connection) -> str:
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT kind, fname, old_line, new_line, COUNT(DISTINCT package) AS n, "
        "MAX(observed_at) AS last FROM revisions "
        "GROUP BY kind, fname, old_line, new_line ORDER BY fname, kind, last DESC").fetchall()
    lines = ["# Learned edits (rules the system took from your own changes)", ""]
    if not rows:
        lines.append("Nothing learned yet — edit any draft or _FINAL file, then run "
                     "`python3 scripts/learn.py`。")
        return "\n".join(lines)
    lines.append(f"{len(rows)} observations. A rule always applies when **its own "
                 f"package** is rebuilt; "
                 f"to apply **across jobs** the same edit must appear in "
                 f"at least {GLOBAL_THRESHOLD} different packages "
                 "(check = global, part = its own package only).")
    lines.append("")
    cur_f = None
    for r in rows:
        if r["fname"] != cur_f:
            cur_f = r["fname"]
            lines += [f"## {cur_f}", ""]
        active = "✅" if r["n"] >= GLOBAL_THRESHOLD else f"◔ {r['n']}/{GLOBAL_THRESHOLD}"
        if r["kind"] == "rewrite":
            lines.append(f"- {active} rewrite: `{r['old_line'][:70]}` -> `{r['new_line'][:70]}`")
        else:
            lines.append(f"- {active} drop: `{r['old_line'][:70]}`")
    lines.append("")
    return "\n".join(lines)
