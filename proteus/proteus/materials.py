"""CV sync — keep the compiler's profile in step with the master CV.

The user maintains ground-truth documents (master CV .docx, notes) in the
my-materials/cv/ folder. `sync()` detects new/changed files there, extracts the
master CV's text (stdlib-only docx reader), re-parses the profile sections the
résumé compiler uses (summary, education, experience, languages, contact), and
writes them to both the DB profile and seed_identity.json so a re-seed cannot
silently revert them. Capability units and the evidence ledger are NOT touched
automatically (charter invariant #1: the ledger stays human-curated).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zipfile
from pathlib import Path

from . import db, identity

ROOT = Path(__file__).resolve().parent.parent
MATERIALS = ROOT.parent / "my-materials" / "cv"
DATA = ROOT / "data"
MANIFEST = DATA / "materials_manifest.json"
CV_SNAPSHOT = DATA / "master_cv.txt"
SEED_BACKUP = DATA / "seed_identity.backup.json"

# Profile keys sync may overwrite; everything else (name, personas…) is kept.
SYNCABLE = ("title", "email", "phone", "location", "work_rights",
            "summary", "education", "experience", "languages")


# ------------------------------------------------------------ docx reader ----

def docx_text(path: Path) -> str:
    """Extract a .docx to plain text: one line per paragraph, real tabs kept,
    list items prefixed with '•\t' — same shape textutil produces."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    lines = []
    for para in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S):
        parts = []
        for m in re.finditer(r"<w:tab/>|<w:t(?: [^>]*)?>(.*?)</w:t>", para, re.S):
            parts.append("\t" if m.group(1) is None else m.group(1))
        text = "".join(parts)
        text = (text.replace("&amp;", "&").replace("&lt;", "<")
                    .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'"))
        if "<w:numPr>" in para:
            text = "•\t" + text
        lines.append(text)
    return "\n".join(lines)


# -------------------------------------------------------- master CV parser ----

_HEADER_RE = re.compile(r"[A-Z][A-Z0-9 &()\-/,'’.]+")


def _is_section_header(line: str) -> bool:
    s = line.strip()
    return (len(s) >= 6 and s == s.upper() and any(c.isalpha() for c in s)
            and _HEADER_RE.fullmatch(s) is not None)


def _is_bullet(line: str) -> bool:
    return line.lstrip("\t ").startswith("•")


def _bullet_text(line: str) -> str:
    return line.lstrip("\t ").lstrip("•").strip("\t ").strip()


def _words(*texts: str) -> set[str]:
    return {w for t in texts for w in re.findall(r"[a-z]{4,}", (t or "").lower())}


def parse_master_cv(text: str, old: dict | None = None) -> dict:
    """Parse the master CV text into the profile fields the compiler uses.
    Starts from `old` and only overwrites what it confidently finds."""
    profile = dict(old or {})
    lines = [ln.rstrip() for ln in text.splitlines()]

    # --- header block (everything before the first section header) ---------
    header, i = [], 0
    while i < len(lines) and not _is_section_header(lines[i]):
        if lines[i].strip():
            header.append(lines[i].strip())
        i += 1
    if len(header) >= 2:
        profile["title"] = re.sub(r"\s*\|\s*", " | ", re.sub(r"\s{2,}", " ", header[1]))
    for ln in header:
        low = ln.lower()
        if "@" in ln:
            segs = [s.strip() for s in re.split(r"[•·]", ln) if s.strip()]
            rest = []
            for s in segs:
                if "@" in s:
                    profile["email"] = s
                elif re.fullmatch(r"\+?[\d][\d \-]{7,}", s):
                    profile["phone"] = s
                else:
                    rest.append(s)
            if rest:
                profile["location"] = " · ".join(rest)
        elif low.startswith("work rights") and ":" in ln:
            profile["work_rights"] = ln.split(":", 1)[1].strip()

    # --- split remaining text into sections --------------------------------
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for ln in lines[i:]:
        if _is_section_header(ln):
            current = []
            sections.append((ln.strip(), current))
        elif current is not None and ln.strip():
            current.append(ln)

    def entries_of(body: list[str]) -> list[dict]:
        """Tab-separated 'Left<TAB>Period' lines start an entry; the next plain
        line is the org; bullets belong to the entry."""
        out: list[dict] = []
        for ln in body:
            if _is_bullet(ln):
                if out:
                    out[-1]["bullets"].append(_bullet_text(ln))
            elif "\t" in ln.strip("\t") and re.search(r"(19|20)\d{2}", ln):
                cells = [c.strip() for c in ln.split("\t") if c.strip()]
                left, period = " ".join(cells[:-1]), cells[-1]
                role, org1 = (left.split("—", 1) + [""])[:2] if "—" in left else (left, "")
                out.append({"role": role.strip(), "org": org1.strip(),
                            "period": period, "bullets": []})
            elif out and not out[-1]["org"]:
                out[-1]["org"] = ln.strip()
            elif out:
                extra = ln.strip()
                if extra.lower().startswith("host"):
                    out[-1]["org"] += f" ({extra.split('|')[0].strip()})"
                else:
                    out[-1]["bullets"].append(extra)
        return out

    experience: list[dict] = []
    for name, body in sections:
        if name.startswith("PROFESSIONAL SUMMARY"):
            profile["summary"] = " ".join(ln.strip() for ln in body)
        elif name == "EDUCATION":
            edu = []
            for e in entries_of(body):
                edu.append({"degree": e["role"], "org": e["org"], "period": e["period"],
                            "note": " ".join(e["bullets"])})
            if edu:
                profile["education"] = edu
        elif "EXPERIENCE" in name:
            experience.extend(entries_of(body))
        elif name.startswith("TECHNICAL SKILLS"):
            for ln in body:
                if _is_bullet(ln) and _bullet_text(ln).lower().startswith("languages"):
                    rest = _bullet_text(ln).split(":", 1)[1].strip()
                    profile["languages"] = [p.strip() for p in
                                            re.split(r",\s+(?=[A-Z])", rest) if p.strip()]

    if experience:
        # Carry evidence ids over from old entries that describe the same job.
        old_exp = (old or {}).get("experience", [])
        for e in experience:
            new_w = _words(e["role"], e["org"])
            best, best_score = None, 0
            for o in old_exp:
                score = len(new_w & _words(o.get("role", ""), o.get("org", "")))
                if score > best_score:
                    best, best_score = o, score
            e["evidence"] = best.get("evidence", []) if (best and best_score >= 2) else []
        profile["experience"] = experience

    return profile


# ------------------------------------------------------------------- sync ----

def _find_master_cv() -> Path | None:
    """The one CV that defines the profile.

    Picking "whichever .docx was edited last" was silently dangerous once more
    than one CV lived in the folder: editing a role-specific CV would rewrite
    the whole identity — title, summary and positioning — from that variant.
    A file whose name contains "master" wins outright; otherwise fall back to
    most-recent, and sync() reports which file it used so the choice is never
    invisible.
    """
    docx = [f for f in MATERIALS.glob("*.docx") if not f.name.startswith("~")]
    if not docx:
        return None
    master = [f for f in docx if "master" in f.name.lower()]
    if master:
        return max(master, key=lambda f: f.stat().st_mtime)
    named = [f for f in docx if re.search(r"cv|resume", f.name, re.I)]
    return max(named or docx, key=lambda f: f.stat().st_mtime)


def sync(conn: sqlite3.Connection) -> dict:
    """Scan my-materials/cv, refresh the profile from the master CV, report changes."""
    report: dict = {"files": [], "cv": None, "error": None}
    if not MATERIALS.is_dir():
        report["error"] = f"CV folder not found — my-materials/cv: {MATERIALS}"
        return report

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    for f in sorted(MATERIALS.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.name.startswith("~"):
            continue
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        status = ("new" if f.name not in manifest
                  else "changed" if manifest[f.name] != digest else "unchanged")
        manifest[f.name] = digest
        report["files"].append({"name": f.name, "status": status})
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    cv = _find_master_cv()
    if cv is None:
        report["error"] = "No master CV (.docx) found in my-materials/cv."
        return report

    text = docx_text(cv)
    CV_SNAPSHOT.write_text(text, encoding="utf-8")

    old = identity.get_profile(conn)
    new = parse_master_cv(text, old)
    changed = [k for k in SYNCABLE if old.get(k) != new.get(k)]
    conn.execute("INSERT OR REPLACE INTO profile(id, data) VALUES(1, ?)", (db.dumps(new),))
    conn.commit()

    # Keep seed_identity.json consistent so a future re-seed can't revert this.
    seed_path = identity.SEED
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    if not SEED_BACKUP.exists():
        SEED_BACKUP.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    seed["profile"] = new
    seed_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")

    report["cv"] = {"file": cv.name, "changed_fields": changed,
                    "education": len(new.get("education", [])),
                    "experience": len(new.get("experience", []))}
    return report
