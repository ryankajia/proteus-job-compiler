"""Reuse your own previously approved criterion answers.

Australian selection criteria repeat. Nearly every mid-level public-sector role asks
the same nine things — administrative practice in a complex organisation,
analysis under deadline, competing priorities, communication, supervision,
systems and efficiencies, computer skills, equal opportunity and WHS — worded
differently each time. You have already written and submitted good answers to
most of them.

So the best first draft is not generated prose: it is your own sentence from the
last application, surfaced against the matching criterion of the new one. No
API key, no cost, and nothing invented — the words are yours and were already
judged fit to send.

The match is offered, never applied silently: every reused paragraph is marked
with where it came from so you checks it still fits before it goes out.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "out" / "apply"

# Sources of *approved* prose, best first. A FINAL is hand-polished; a file
# under submit/ was actually sent; a bare selection_criteria.md counts only
# when you edited it away from the generated snapshot.
_FINAL_NAMES = ("selection_criteria_FINAL.md", "selection_criteria.md")

_STOP = {
    "the", "and", "for", "with", "that", "this", "have", "has", "are", "was",
    "will", "your", "you", "our", "their", "its", "from", "into", "under",
    "over", "than", "then", "them", "they", "not", "but", "all", "any", "can",
    "able", "ability", "demonstrated", "proven", "experience", "experienced",
    "high", "highly", "degree", "level", "skills", "skill", "relevant",
    "including", "include", "well", "work", "working", "role", "position",
    "other", "such", "must", "should", "within", "across", "both", "each",
    "while", "when", "where", "which", "who", "what", "how", "a", "an", "of",
    "in", "on", "to", "as", "at", "by", "or", "is", "be", "it",
}

# Worksheet furniture that is never part of an answer.
_SCAFFOLD = re.compile(r"^(?:\*\*Claim:\*\*|- ✓ \(|- \*\*|> |#|-{3,}$|\*Submission)")


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _STOP}


def _similarity(a: str, b: str) -> float:
    """Jaccard overlap on content words. Cheap, deterministic, and good enough:
    two criteria about 'competing deadlines' and 'prioritise own workload'
    share enough vocabulary to surface, and unrelated ones do not."""
    wa, wb = _content_words(a), _content_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _sections(md: str) -> list[tuple[str, str]]:
    """(heading, answer prose) for each '## ' section, scaffolding stripped."""
    out: list[tuple[str, str]] = []
    head, body = None, []
    for ln in md.splitlines():
        if ln.startswith("## "):
            if head and body:
                out.append((head, "\n".join(body).strip()))
            # Drop a leading "1. " / "Criterion 3: " numbering from the heading.
            h = ln[3:].strip()
            h = re.sub(r"^(?:criterion\s*)?\d{1,2}[.):]\s*", "", h, flags=re.I)
            head, body = h, []
        elif head is not None and ln.strip() and not _SCAFFOLD.match(ln.strip()):
            body.append(ln.strip())
    if head and body:
        out.append((head, "\n".join(body).strip()))
    return [(h, b) for h, b in out if len(b.split()) >= 25]


def _candidate_files() -> list[Path]:
    files: list[Path] = []
    if not PKG.is_dir():
        return files
    for pkg in sorted(PKG.iterdir()):
        if not pkg.is_dir() or pkg.name.startswith((".", "_")):
            continue
        for sub in (pkg / "submit", pkg):
            for name in _FINAL_NAMES:
                f = sub / name
                if not f.is_file():
                    continue
                # A bare selection_criteria.md identical to its generated
                # snapshot is a draft you never touched — not approved prose.
                if name == "selection_criteria.md":
                    snap = pkg / ".generated" / name
                    if snap.is_file() and snap.read_text(encoding="utf-8") == f.read_text(encoding="utf-8"):
                        continue
                files.append(f)
    return files


def load_bank(exclude_package: str | None = None) -> list[dict]:
    """Every approved criterion answer you have written, with its origin."""
    bank: list[dict] = []
    seen: set[str] = set()
    for f in _candidate_files():
        pkg_name = f.parent.name if f.parent.name != "submit" else f.parent.parent.name
        if exclude_package and pkg_name == exclude_package:
            continue
        try:
            md = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for heading, answer in _sections(md):
            key = answer[:160]
            if key in seen:
                continue
            seen.add(key)
            bank.append({"criterion": heading, "answer": answer, "package": pkg_name})
    return bank


def best_match(criterion: str, bank: list[dict], threshold: float = 0.16) -> dict | None:
    """The closest previously approved answer to this criterion, or None."""
    best, best_score = None, 0.0
    for item in bank:
        score = _similarity(criterion, item["criterion"])
        if score > best_score:
            best, best_score = item, score
    if best and best_score >= threshold:
        return {**best, "score": round(best_score, 3)}
    return None
