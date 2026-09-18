"""Local single-user web UI for Proteus — no dependencies beyond the stdlib.

Paste a job ad (or upload the PDF/Word ad) -> get a fit score, a compiled
resume, and a gap report. Track everything on one board. Nothing is ever sent
anywhere; this only reads and writes the local SQLite database at
databases/proteus.db.

Discretion note: what a passer-by sees — window title, board heading, buttons,
status words — is deliberately neutral («Job Board»). The details are one click
in, behind a collapsed panel.

Run:  python3 -m proteus.webapp
Then open http://localhost:8765
"""
from __future__ import annotations

import datetime as _dt
import html
import json
import re
import sqlite3
import subprocess
import sys
import urllib.parse
from email.parser import BytesParser
from email.policy import default as _email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import (applog, calibration, compiler, db, fit, gate, identity,
               jobdoc, joburl, jobspec, materials, reuse, revisions)

PORT = 8765
# Shown in the browser tab / window title — nothing job-hunting-specific.
APP_NAME = "Job Board"
MAX_UPLOAD = 40 * 1024 * 1024  # whole request, i.e. all attachments together
OUT = Path(__file__).resolve().parent.parent / "out"
PKG = OUT / "apply"
REAL_JOBS = Path(__file__).resolve().parent.parent / "data" / "real_jobs"

# Package files in reading order. FINAL files first — they
# are the hand-polished, ready-to-send versions.
FILE_LABELS = [
    ("checklist.md", "✅ Checklist (read this first)"),
    ("resume_FINAL.md", "📄 Résumé · final (ready to send)"),
    ("cover_letter_FINAL.md", "✉️ Cover letter · final (ready to send)"),
    ("selection_criteria_FINAL.md", "📝 Selection criteria · final (checked against the PD)"),
    ("selection_criteria_draft.md", "📝 Selection criteria (draft — check against the PD)"),
    ("selection_criteria.md", "📝 Selection criteria · auto draft (required in Australia)"),
    ("review.md", "🔍 Pre-submission review (blockers / warnings)"),
    ("resume.md", "Résumé · auto draft"),
    ("cover_letter.md", "Cover letter · auto draft"),
    ("gap.md", "Gap report (cram before the interview)"),
]

# Neutral wording on purpose — the keys (and everything downstream) are
# unchanged, only what shows on screen.
STATUS_LABEL = {
    "sensed": "To do",
    "drafted": "Docs ready — NOT SUBMITTED",
    # An application begun in the employer's portal but not finished. Some
    # portals delete an incomplete application after about a week, so this
    # is a deadline of its own, separate from the vacancy closing.
    "started_portal": "Started on portal",
    "applied": "Submitted",
    "interviewing": "In progress",
    "offer": "Offer 🎉",
    "rejected": "Closed",
}
STATUS_ORDER = list(STATUS_LABEL)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Only these are servable. A general directory server here would be a path
# traversal hole for the sake of two files.
STATIC_FILES = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}


def _asset_version(filename: str) -> int:
    """mtime as a cache-buster, so an edited stylesheet shows up on reload
    instead of being served from the browser cache."""
    f = STATIC_DIR / filename
    try:
        return int(f.stat().st_mtime)
    except OSError:
        return 0


# Word opens a downloaded .doc as a standalone file, so this one stylesheet
# has to stay inline — there is no server to fetch /static from. Defined as a
# plain string (not inside an f-string) so its braces need no escaping.
DOC_CSS = (
    'body{font-family:Arial,"PingFang SC",sans-serif;font-size:11pt;line-height:1.5}'
    "h2{font-size:16pt}h3{font-size:13pt}h4{font-size:11.5pt}"
)


def _page(title: str, body: str) -> bytes:
    css_v, js_v = _asset_version("app.css"), _asset_version("app.js")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · {APP_NAME}</title>
<link rel="stylesheet" href="/static/app.css?v={css_v}">
<script src="/static/app.js?v={js_v}" defer></script>
</head><body>{body}</body></html>""".encode("utf-8")


_VISA_BLOCKERS = ("citizenship", "clearance", "pr/citizenship")


def _visa_blocked(visa_requirement) -> bool:
    v = (visa_requirement or "").lower()
    return any(b in v for b in _VISA_BLOCKERS)


def _fit_badge(fit_score, visa_requirement=None) -> str:
    """Detail-page badge: the band AND the raw score.

    The list deliberately shows only the word — four decimals there is false
    precision. The raw number still has to live somewhere a human can read it,
    because calibrating the score against real outcomes needs the number, not
    the bucket.
    """
    if _visa_blocked(visa_requirement):
        raw = fit_score if fit_score is not None else "—"
        return f'<span class="badge grey">{raw} (visa-restricted)</span>'
    if fit_score is None:
        return '<span class="badge low">Not scored</span>'
    word, band = _fit_band(fit_score)
    cls = {"live": "high", "warn": "mid"}.get(band, "low")
    return f'<span class="badge {cls}">{word}</span> <span class="sub">(raw {fit_score})</span>'


def _ensure_identity(conn: sqlite3.Connection) -> None:
    if not identity.get_capabilities(conn):
        identity.load_seed(conn)
        calibration.calibrate(conn)


def _upsert_pipeline(conn: sqlite3.Connection, job_id: str, status=None,
                      fit_score=None, stretch_score=None) -> None:
    now = _dt.datetime.now().isoformat(timespec="seconds")
    row = conn.execute("SELECT * FROM pipeline WHERE job_id=?", (job_id,)).fetchone()
    status = status or (row["status"] if row else "sensed")
    fit_score = fit_score if fit_score is not None else (row["fit_score"] if row else None)
    stretch_score = stretch_score if stretch_score is not None else (row["stretch_score"] if row else None)
    conn.execute(
        "INSERT OR REPLACE INTO pipeline(job_id, status, positioning_used, fit_score, stretch_score, deadline, updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (job_id, status, row["positioning_used"] if row else None, fit_score, stretch_score,
         row["deadline"] if row else None, now),
    )
    conn.commit()


def _spec_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "title": row["title"], "org": row["org"], "seniority": row["seniority"],
        "required_skills": json.loads(row["required_skills"]), "nice_to_have": json.loads(row["nice_to_have"]),
        "visa_requirement": row["visa_requirement"], "security_clearance": row["security_clearance"],
        "salary": row["salary"], "deadline": row["deadline"],
        "closes_at": (row["closes_at"] if "closes_at" in row.keys() else "") or "",
        "apply_channel": row["apply_channel"],
        "source": row["source"], "raw_text": row["raw_text"],
    }


# ------------------------------------------------------- package viewer ----

def _md_to_html(md: str) -> str:
    """Tiny stdlib Markdown renderer — enough for our own generated files."""
    out: list[str] = []
    in_list = False
    in_note = False
    for raw_line in md.splitlines():
        line = raw_line.rstrip()
        # '>' lines are notes-to-self: yellow sticky in the browser, stripped
        # from print and Word download.
        if line.startswith(">"):
            if in_list:
                out.append("</ul>")
                in_list = False
            if not in_note:
                out.append('<blockquote class="note noprint">')
                in_note = True
            esc = html.escape(line.lstrip("> ").strip())
            esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
            esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
            if esc:
                out.append(f"<p>{esc}</p>")
            continue
        if in_note and line:
            out.append("</blockquote>")
            in_note = False
        esc = html.escape(line)
        # inline: bold, links, code
        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
        esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
        esc = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
                     r'<a href="\2" target="_blank">\1</a>', esc)
        esc = re.sub(r"(?<![\"'>])(https?://[^\s<]+)", r'<a href="\1" target="_blank">\1</a>', esc)
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{esc[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("### "):
            out.append(f"<h4>{esc[4:]}</h4>")
        elif line.startswith("## "):
            out.append(f"<h3>{esc[3:]}</h3>")
        elif line.startswith("# "):
            out.append(f"<h2>{esc[2:]}</h2>")
        elif line.startswith("---"):
            out.append("<hr>")
        elif not line:
            out.append("<br>")
        else:
            out.append(f"<p>{esc}</p>")
    if in_list:
        out.append("</ul>")
    if in_note:
        out.append("</blockquote>")
    return "\n".join(out)


def _deadline_passed(deadline: str) -> bool:
    """Has this closing date already gone by?

    A date only marked closed when someone typed "PASSED" into the file silently
    reads as still-open once it lapses — the badge said "Closes: 2 August 2026" in
    live orange three days after it shut. Compare against today instead; an
    unparseable date stays open rather than greying a live job by mistake.
    """
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})", deadline or "")
    if not m:
        return False
    day, month, year = m.group(1), m.group(2)[:3].title(), m.group(3)
    try:
        closes = _dt.datetime.strptime(f"{day} {month} {year}", "%d %b %Y").date()
    except ValueError:
        return False
    return closes < _dt.date.today()


# --------------------------------------------------------------- board ----
# One row per job. The board used to show the same job twice — once as a
# "package" card and once as a "tracker" row — which is the main reason it
# read as cluttered and why a job's real state was never in one place.

# Fixed display order. A job lands in exactly one of these.
GROUP_INBOX = "inbox"
GROUP_DRAFTING = "drafting"
GROUP_READY = "ready_not_submitted"
GROUP_SUBMITTED = "submitted"
GROUP_CLOSED = "closed"

GROUP_ORDER = [GROUP_INBOX, GROUP_DRAFTING, GROUP_READY, GROUP_SUBMITTED, GROUP_CLOSED]

GROUP_TITLE = {
    GROUP_INBOX: "To assess",
    GROUP_DRAFTING: "Writing documents",
    GROUP_READY: "Documents ready — NOT SUBMITTED",
    GROUP_SUBMITTED: "Submitted — awaiting a result",
    GROUP_CLOSED: "Past — closed",
}

GROUP_NOTE = {
    GROUP_READY: "Everything here is written and unsent. This is where applications get lost.",
    GROUP_SUBMITTED: "No word after about 6 weeks is a reasonable point to mark it closed.",
}

# Statuses that mean the application has left your hands.
_SENT = ("applied", "interviewing", "offer")
_TERMINAL = ("rejected",)

# An incomplete application in some portals is deleted after roughly a week.
# Warn with room to act rather than on the last day.
PORTAL_STALE_DAYS = 5


def is_ready_not_submitted(row: dict) -> bool:
    """Written, unsent, and still winnable.

    The single most expensive failure in this pipeline: cost fully paid,
    return exactly zero. Six applications were lost this way.
    """
    return bool(row["polished"]) and row["status"] not in _SENT \
        and row["status"] not in _TERMINAL and not row["closed"]


def is_expired_unsubmitted(row: dict) -> bool:
    """Written, unsent, and now impossible. Wasted work, counted not hidden."""
    return bool(row["polished"]) and row["status"] not in _SENT \
        and row["status"] not in _TERMINAL and bool(row["closed"])


def portal_days_open(row: dict) -> int | None:
    """Days since the application was begun in the employer's portal."""
    started = row.get("started_on_portal")
    if not started:
        return None
    d = jobspec.parse_closing_date(started)
    return (_dt.date.today() - d).days if d else None


def is_portal_stale(row: dict) -> bool:
    """Begun on the portal, still unsent, and old enough to be deleted."""
    if row["status"] not in ("started_portal",) or row["status"] in _SENT:
        return False
    days = portal_days_open(row)
    return days is not None and days >= PORTAL_STALE_DAYS

_MONTHS = ("jan feb mar apr may jun jul aug sep oct nov dec")


def _deadline_date(deadline: str) -> _dt.date | None:
    """Kept as the board's entry point; the parsing itself lives in jobspec so
    intake and display can never disagree about what a date means."""
    return jobspec.parse_closing_date(deadline)


def _sort_key(row: dict) -> tuple:
    """Closing date ascending; undated jobs last. Never raises — an undated or
    unparseable job sorts to the end rather than breaking the whole page.

    A job with a hard-threshold flag sorts first within its group, so the ones
    worth a decision before any writing starts are the ones you see first.
    """
    d = row.get("closes_on")
    return (not row.get("gate_flags"), d is None, d or _dt.date.max,
            (row.get("title") or "").lower())


def _fit_band(fit_score) -> tuple[str, str]:
    """(word, css class). A four-decimal score is false precision on a list;
    the number stays in the DB and on the detail page. PR 3 tunes the cuts."""
    try:
        v = float(fit_score)
    except (TypeError, ValueError):
        return ("Not scored", "plain")
    if v >= 0.80:
        return ("Strong", "live")
    if v >= 0.55:
        return ("Possible", "warn")
    return ("Stretch", "plain")


_CRIT_HEAD = re.compile(r"^##\s+(?:Criterion\s+)?\d{1,2}[.:)]?\s+\S", re.M)


def _criteria_progress(slug: str) -> tuple[int, int]:
    """(answered, total) selection criteria for this job.

    "final ready" was a yes/no that said nothing about how much was left.
    A criterion counts as answered when it carries real prose — the generated
    Claim/evidence scaffolding and the writing prompts do not count.
    """
    pkg = PKG / slug
    if not pkg.is_dir():
        return (0, 0)
    for name in ("selection_criteria_FINAL.md", "selection_criteria.md",
                 "selection_criteria_draft.md"):
        f = pkg / name
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        total = len(_CRIT_HEAD.findall(text))
        if not total:
            continue
        answered = len(reuse._sections(text))
        return (min(answered, total), total)
    return (0, 0)


def _job_meta(name: str) -> dict:
    """Deadline + employer apply link, read from the saved JD file."""
    meta = {"deadline": "", "url": "", "closed": False}
    jd = REAL_JOBS / f"{name}.txt"
    if not jd.exists():
        return meta
    text = jd.read_text(encoding="utf-8")
    m = re.search(r"^Closes?:\s*(.+)$", text, re.M)
    if m:
        meta["deadline"] = m.group(1).split("(")[0].strip()
        meta["closed"] = ("PASSED" in m.group(1) or "Closed" in m.group(1)
                          or _deadline_passed(meta["deadline"]))
    # A link typed into the add form wins over whatever URL happens to appear
    # first in the ad text (PDF ads are full of unrelated links).
    m = (re.search(r"^(?:Job link|Apply|Link)[：:]\s*(https?://\S+)", text, re.M)
         or re.search(r"(https?://\S+)", text))
    if m:
        meta["url"] = m.group(1).rstrip(")")
    return meta


def _package_status() -> dict[str, str]:
    """source -> pipeline status, so a card knows if it has been sent already."""
    try:
        conn = db.connect()
        try:
            return {r["source"]: r["status"] for r in conn.execute(
                "SELECT j.source, p.status FROM jobspecs j "
                "JOIN pipeline p ON p.job_id = j.id")}
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


def _packages() -> list[dict]:
    if not PKG.exists():
        return []
    status = _package_status()
    pkgs = []
    for d in sorted(PKG.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        files = {f.name for f in d.iterdir() if f.suffix == ".md"}
        pkgs.append({"name": d.name, "files": files,
                     "status": status.get(d.name, "sensed"),
                     "polished": any("FINAL" in f for f in files), **_job_meta(d.name)})
    pkgs.sort(key=lambda p: (not p["polished"], p["deadline"] == "", p["deadline"]))
    return pkgs


# Job-board ads are taken down; an employer's own careers page is stable. SEEK
# states it removes ads after 30 days, so a SEEK link is a wasting asset.
_EXPIRING_BOARDS = ("seek.com", "seek.com.au", "indeed.com", "to.indeed.com",
                    "jora.com", "adzuna.com.au")


def _is_expiring_board(url: str) -> bool:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return any(host == b or host.endswith("." + b) for b in _EXPIRING_BOARDS)


def _apply_link(p: dict) -> str:
    """The apply-link cell.

    Two ways this link lies to your, both seen in practice: a closed job still
    offering a live-looking Apply link, and a job-board ad that has since been
    taken down ("This job is no longer advertised") even though the job itself
    is open. Say so rather than sending your to a dead page.
    """
    if not p["url"]:
        return ""
    # Already sent, or decided against: there is nothing to go and do, and the
    # ad is usually gone anyway. Offering an apply link here wastes a click.
    done = {"applied": "Submitted — nothing to click", "interviewing": "In progress",
            "offer": "Offer 🎉", "rejected": "Closed"}
    if p.get("status") in done:
        return f' · <span class="sub">{done[p["status"]]}</span>'
    if p["closed"]:
        return ' · <span class="sub">Ad has been taken down</span>'
    warn = ""
    if _is_expiring_board(p["url"]):
        warn = ('<span class="badge grey" title="Job-board ads are taken down when '
                'they expire, so the link may show &ldquo;This job is no longer '
                'advertised&rdquo;. Use the employer&rsquo;s own careers page '
                'instead.">may have expired</span>')
    return (f' · <a href="{html.escape(p["url"])}" target="_blank">'
            f'Go to the employer&rsquo;s page &uarr;</a> {warn}')


HOWTO = """
<div class="card">
<ol>
<li><b>Add</b> — the system<b>does not</b> does not search or browse job boards — that is deliberate. You find the vacancy yourself on
SEEK/Indeed , or ask Claude to help you search. Then press “+ Add entry” at the top right and pick one of three ways in:
<b>paste a link and press Fetch</b> (one click reads only the page you gave it, and shows you the result),
<b>copy and paste the whole thing</b>, <b>Upload a PDF or Word document</b> (many vacancies only publish a PDF — just upload it, no retyping.)
University, government and ATS pages usually read fine. SEEK and Indeed often block; LinkedIn is not attempted, by their terms —
if blocked, paste or upload instead.</li>
<li><b>Build the package</b> — After submitting, the system<b>auto</b>It builds the whole set: tailored résumé, cover letter, selection-criteria response where required,
a pre-submission check and a gap report. Everything already built is under “My packages” above.</li>
<li><b>Submit</b> — There is no one-click apply. Open the package, press ✏️ Edit to change anything, save,
then Print / Save as PDF (or Download Word), and use “go to the employer's page” to apply on their site.
upload the PDF — and you press submit yourself. The system never submits anything for you.</li>
</ol>
<p class="sub">When you have submitted, come back and set the status to Submitted.<br>
💡 “My packages” above and the table below are<b>the same entries</b> two views of the same thing: files above, fit and progress below.<br>
💡 Edited the master CV (.docx) in my-materials/cv? Press “🔄 Sync CV” at the top right —
the system re-reads it, updates the data and rebuilds every auto draft (_FINAL files are left alone).<br>
💡 If an Australian vacancy asks for a selection-criteria statement, the package includes a draft response automatically.</p>
</div>
"""


def render_package(name: str) -> bytes:
    d = PKG / name
    if not d.is_dir() or "/" in name or ".." in name:
        return _page("Not found", '<p>No such package.</p><p><a href="/">← Back to the board</a></p>')
    meta = _job_meta(name)
    known = [fn for fn, _ in FILE_LABELS]
    files = [(fn, label) for fn, label in FILE_LABELS if (d / fn).exists()]
    files += [(f.name, f.name) for f in sorted(d.iterdir())
              if f.suffix == ".md" and f.name not in known]
    items = "".join(
        f'<li style="margin:.4rem 0"><a href="/pkg/{name}/{fn}">{html.escape(label)}</a></li>'
        for fn, label in files)
    dl = f'<p class="sub">⏰ Closes: {html.escape(meta["deadline"])}</p>' if meta["deadline"] else ""
    apply_btn = (f'<a class="btn" href="{html.escape(meta["url"])}" target="_blank">Go to the employer&rsquo;s page &uarr;</a>'
                 if meta["url"] else "")
    body = f"""
<div class="topbar"><h1>{html.escape(name)}</h1><a class="btn secondary" href="/">← Back to the board</a></div>
{dl}
<div class="card"><h3>Documents (in reading order)</h3><ul style="list-style:none;padding-left:0">{items}</ul>
<p>{apply_btn}</p></div>
"""
    return _page(name, body)


def _pkg_file(name: str, fname: str) -> Path | None:
    """Validate that name/fname point at a real .md inside a package dir."""
    d = (PKG / name).resolve()
    f = (d / fname).resolve()
    if (d.is_dir() and f.is_file() and f.suffix == ".md"
            and d.parent == PKG.resolve() and f.parent == d):
        return f
    return None


def render_package_file(name: str, fname: str) -> bytes:
    f = _pkg_file(name, fname)
    if f is None:
        return _page("Not found", '<p>No such file.</p><p><a href="/">← Back to the board</a></p>')
    content = _md_to_html(f.read_text(encoding="utf-8"))
    body = f"""
<div class="topbar noprint"><h1>{html.escape(fname)}</h1>
<span><button class="btn" onclick="window.print()">🖨 Print / Save as PDF</button>
<a class="btn secondary" href="/pkg/{name}/{fname}?edit=1">✏️ Edit</a>
<a class="btn secondary" href="/pkg/{name}/{fname}?dl=doc">⬇️ Download Word</a>
<a class="btn secondary" href="/pkg/{name}/{fname}?dl=md">⬇️ Download Markdown</a>
<a class="btn secondary" href="/pkg/{name}">← Back to the document list</a></span></div>
<p class="sub noprint">To save a PDF: press Print, then choose “Save as PDF” as the destination. To change the wording, press Edit, save, then download.
Yellow sticky = a note to yourself,<b>removed automatically when printing or downloading Word</b>, no need to delete it by hand.</p>
<div class="card doc">{content}</div>
"""
    return _page(fname, body)


def render_package_file_edit(name: str, fname: str) -> bytes:
    f = _pkg_file(name, fname)
    if f is None:
        return _page("Not found", '<p>No such file.</p><p><a href="/">← Back to the board</a></p>')
    raw = f.read_text(encoding="utf-8")
    body = f"""
<div class="topbar"><h1>✏️ Edit {html.escape(fname)}</h1>
<a class="btn secondary" href="/pkg/{name}/{fname}">Cancel without saving</a></div>
<p class="sub">Just edit the text. Formatting: <code># heading</code>, <code>## subheading</code>, <code>- list item</code>, 
<code>**bold**</code>；<code>&gt;</code> a line starting with this is a note to yourself (shown as a yellow sticky,
removed automatically when printing or downloading Word). The previous version is backed up to .backups before saving, so a bad edit can be recovered.</p>
<div class="card">
<form method="post" action="/pkg/save">
<input type="hidden" name="name" value="{html.escape(name, quote=True)}">
<input type="hidden" name="fname" value="{html.escape(fname, quote=True)}">
<textarea name="content" style="min-height:65vh">{html.escape(raw)}</textarea>
<p><button class="btn" type="submit">💾 Save</button>
&nbsp;<a class="btn secondary" href="/pkg/{name}/{fname}">Cancel</a></p>
</form>
</div>
"""
    return _page(f"Edit {fname}", body)


def _doc_download(name: str, fname: str) -> bytes | None:
    """Render the markdown as a Word-openable .doc (HTML) payload."""
    f = _pkg_file(name, fname)
    if f is None:
        return None
    md = re.sub(r"(?m)^\s*>.*\n?", "", f.read_text(encoding="utf-8"))
    content = _md_to_html(md)
    return (f"""<html><head><meta charset="utf-8"><title>{html.escape(fname)}</title>
<style>{DOC_CSS}</style></head>
<body>{content}</body></html>""").encode("utf-8")


FIELD_LABEL = {
    "title": "Title", "email": "Email", "phone": "Phone", "location": "Location",
    "work_rights": "Work rights", "summary": "Profile", "education": "Education",
    "experience": "Experience", "languages": "Languages",
}


def render_sync_report(report: dict, regen_output: str) -> bytes:
    parts = ['<div class="topbar"><h1>🔄 Sync CV — result</h1>'
             '<a class="btn secondary" href="/">← Back to the board</a></div>']
    if report.get("error"):
        parts.append(f'<div class="card"><p style="color:#c0392b">{html.escape(report["error"])}</p></div>')
        return _page("Sync result", "".join(parts))

    badge = {"new": '<span class="badge high">New file</span>',
             "changed": '<span class="badge mid">changed</span>',
             "unchanged": '<span class="badge low">no change</span>'}
    rows = "".join(f'<tr><td>{html.escape(f["name"])}</td><td>{badge[f["status"]]}</td></tr>'
                   for f in report["files"])
    parts.append(f'<div class="card"><h3>CV files</h3><table>'
                 f'<tr><th>Files</th><th>Status</th></tr>{rows}</table></div>')

    cv = report.get("cv") or {}
    if cv:
        changed = cv.get("changed_fields") or []
        changed_txt = (", ".join(FIELD_LABEL.get(k, k) for k in changed)
                       if changed else "No fields changed — the CV data was already current.")
        parts.append(
            f'<div class="card"><h3>Master CV synced: {html.escape(cv["file"])}</h3>'
            f'<p>Fields updated: <b>{html.escape(changed_txt)}</b></p>'
            f'<p class="sub">Parsed {cv["education"]}  education entries and {cv["experience"]}  work entries.'
            '<p class="sub">Capabilities and evidence are <b>not</b> changed '
            'automatically — anything needing human confirmation is left to you.'
            '</p></div>')

    parts.append('<div class="card"><h3>Packages rebuilt</h3>'
                 '<p class="sub">Only the <b>auto drafts</b> were rebuilt (resume.md / cover_letter.md).'
                 'All <b>_FINAL finals are never overwritten</b> — If the CV changed a lot, check the final documents by hand.</p>'
                 f'<pre class="resume">{html.escape(regen_output.strip() or "(no output)")}</pre></div>')
    parts.append('<p><a class="btn" href="/">← Back to the board to see the updated documents</a></p>')
    return _page("Sync result", "".join(parts))


# ---------------------------------------------------------------- pages ----

def _group_of(row: dict) -> str:
    """Which section of the board a job belongs in. Exactly one."""
    status = row["status"]
    if status in _TERMINAL:
        return GROUP_CLOSED
    if status in _SENT:
        return GROUP_SUBMITTED
    if is_expired_unsubmitted(row):
        # Nothing left to do but learn from it. Filed under Past, but labelled
        # rather than quietly folded away.
        return GROUP_CLOSED
    if is_ready_not_submitted(row) or row["status"] == "started_portal":
        return GROUP_READY
    if status == "drafted" or row["has_package"]:
        return GROUP_DRAFTING
    return GROUP_INBOX


def _board_rows(conn: sqlite3.Connection) -> list[dict]:
    """One row per job, joined across jobspecs, pipeline and the package folder.

    Previously the same job appeared twice — as a package card and as a tracker
    row — with no shared key between the two views.
    """
    pipe = {r["job_id"]: r for r in conn.execute(
        "SELECT job_id, status, started_on_portal FROM pipeline")}
    statuses = {k: v["status"] for k, v in pipe.items()}

    root = Path(__file__).resolve().parent.parent
    hidden = {p.stem for p in (root / "data" / "sample_jobs").glob("*.txt")}
    hidden |= {p.stem for p in (root / "data" / "real_jobs" / "archived").glob("*.txt")}

    rows = []
    for jr in conn.execute("SELECT * FROM jobspecs").fetchall():
        slug = jr["source"] or ""
        if slug in hidden:
            continue
        spec = _spec_from_row(jr)
        scored = fit.score(conn, spec)
        meta = _job_meta(slug)
        pkg_dir = PKG / slug
        has_package = pkg_dir.is_dir()
        files = {f.name for f in pkg_dir.iterdir() if f.suffix == ".md"} if has_package else set()
        answered, total = _criteria_progress(slug)
        # Hard thresholds, evaluated now rather than before submitting: by then
        # the documents are already written. Recomputed each render so that
        # editing data/gate_rules.json takes effect immediately.
        flags = gate.gate(spec.get("raw_text") or "")
        deadline = meta["deadline"] or (spec.get("deadline") or "")
        # The stored ISO date is authoritative: it survives re-parsing and can
        # be typed in by hand when the ad never states one.
        closes_on = jobspec.parse_closing_date(spec.get("closes_at") or "") \
            or _deadline_date(deadline)
        rows.append({
            "id": jr["id"],
            "slug": slug,
            "title": (spec.get("title") or "").strip(),
            "org": (spec.get("org") or "").strip(),
            "salary": (spec.get("salary") or "").strip(),
            "status": statuses.get(jr["id"], "sensed"),
            "started_on_portal": (pipe[jr["id"]]["started_on_portal"]
                                  if jr["id"] in pipe else "") or "",
            "fit_score": scored["fit_score"],
            "visa_requirement": spec.get("visa_requirement"),
            "visa_feasible": scored.get("visa_feasible", True),
            "deadline": deadline,
            "closes_on": closes_on,
            "closed": (closes_on < _dt.date.today()) if closes_on else bool(meta["closed"]),
            "url": meta["url"],
            "has_package": has_package,
            "polished": any("FINAL" in f for f in files),
            "criteria_done": answered,
            "criteria_total": total,
            "gate_flags": flags,
        })
    for r in rows:
        r["group"] = _group_of(r)
    return rows


def _deadline_form(row: dict, label: str, cls: str) -> str:
    """The closing date doubles as the way to correct it.

    Most Australian PDs never state a closing date in the document, so the
    parser legitimately finds nothing. Guessing would be worse than asking,
    and a date that cannot be entered is a date that stays wrong.
    """
    current = row["closes_on"].isoformat() if row["closes_on"] else ""
    return (f'<details class="stage deadline"><summary class="pill {cls}">{label}</summary>'
            f'<form method="post" action="/deadline" class="stage-menu">'
            f'<input type="hidden" name="job_id" value="{row["id"]}">'
            f'<label class="dl-label">Closing date'
            f'<input type="date" name="closes_at" value="{current}"></label>'
            f'<button class="stage-opt" type="submit">Save</button>'
            f'<button class="stage-opt" type="submit" name="clear" value="1">Clear</button>'
            f'</form></details>')


def _deadline_cell(row: dict) -> str:
    """Closing date as urgency, not a date string to be decoded.

    <= 3 days red, <= 7 amber, otherwise grey; an expired job greys its whole
    row. Clicking the pill opens the date entry.
    """
    if row["closed"]:
        return _deadline_form(row, "Closed", "dead")
    d = row["closes_on"]
    if d is None:
        # Say it is unknown rather than implying the job has no deadline.
        return _deadline_form(row, "Date unknown", "plain")
    days = (d - _dt.date.today()).days
    cls = "crit" if days <= 3 else "warn" if days <= 7 else "plain"
    word = ("today" if days == 0
            else "1 day left" if days == 1
            else f"{days} days left")
    return _deadline_form(row, word, cls)


def _stage_menu(row: dict) -> str:
    """A menu behind one button. Six open <select>s on one screen was the
    heaviest thing on the page and invited a mis-click into the wrong status."""
    current = STATUS_LABEL.get(row["status"], row["status"])
    opts = "".join(
        f'<button class="stage-opt" type="submit" name="status" value="{k}">{v}</button>'
        for k, v in STATUS_LABEL.items() if k != row["status"])
    portal = ""
    if row["status"] == "started_portal":
        started = row.get("started_on_portal") or ""
        portal = (f'<form method="post" action="/portal" class="portal-date">'
                  f'<input type="hidden" name="job_id" value="{row["id"]}">'
                  f'<label class="dl-label">Started on portal'
                  f'<input type="date" name="started_on" value="{html.escape(started)}"></label>'
                  f'<button class="stage-opt" type="submit">Save</button></form>')
    return (f'<details class="stage"><summary>{html.escape(current)}</summary>'
            f'<div class="stage-menu">{portal}'
            f'<form method="post" action="/status">'
            f'<input type="hidden" name="job_id" value="{row["id"]}">{opts}</form>'
            f'</div></details>')


def _board_row_html(row: dict) -> str:
    title = row["title"] or row["slug"] or "(untitled)"
    sub_bits = [b for b in (row["org"], row["salary"]) if b]
    sub = html.escape(" · ".join(sub_bits)) if sub_bits else "&mdash;"
    # The slug is a folder name, not a heading. Shown small, and only when it
    # is not already the title.
    slug_line = ""
    if row["slug"] and row["slug"] != title:
        slug_line = f'<div class="slug">{html.escape(row["slug"])}</div>'
    word, band = _fit_band(row["fit_score"])
    if not row["visa_feasible"]:
        word, band = "Visa-restricted", "dead"
    done, total = row["criteria_done"], row["criteria_total"]
    if total:
        pct = int(round(done * 100 / total))
        docs = (f'<span class="bar"><i style="width:{pct}%"></i></span>{done}/{total}')
    else:
        docs = '<span class="sub">—</span>'
    open_href = f'/pkg/{urllib.parse.quote(row["slug"])}' if row["has_package"] else f'/job/{row["id"]}'
    dead = " dead" if row["closed"] else ""
    # Say plainly that this one was written and never sent. Hiding it would
    # lose the only lesson it still has to give.
    wasted = ('<span class="pill crit wasted">Written, never sent</span>'
              if is_expired_unsubmitted(row) else "")
    # Marked, never removed — the same position fit.py takes on visa blocks.
    flags = row.get("gate_flags") or []
    gate_html = ""
    if flags:
        chips = "".join(
            f'<span class="pill crit chip" title="{html.escape(f["why"], quote=True)}'
            f' — {html.escape(f["evidence"], quote=True)}">{html.escape(f["label"])}</span>'
            for f in flags)
        detail = html.escape(gate.summary(flags))
        gate_html = f'<div class="gate">{chips}<div class="gate-why">{detail}</div></div>' 
    return (f'<div class="row{dead}">'
            f'<div><p class="title"><a href="{open_href}">{html.escape(title)}</a></p>'
            f'<div class="sub">{sub}</div>{slug_line}{wasted}{gate_html}</div>'
            f'{_deadline_cell(row)}'
            f'<span class="pill {band}">{word}</span>'
            f'<div class="docs">{docs}</div>'
            f'{_stage_menu(row)}'
            f'<a class="go" href="{open_href}" title="Open">&rarr;</a>'
            f'</div>')


def _days_word(row: dict) -> str:
    d = row["closes_on"]
    if d is None:
        return "no closing date recorded"
    left = (d - _dt.date.today()).days
    if left < 0:
        return "already closed"
    return "closes today" if left == 0 else "1 day left" if left == 1 else f"{left} days left"


def _status_strip(rows: list[dict]) -> str:
    today = _dt.date.today()
    closing_soon = sum(
        1 for r in rows if r["closes_on"] and not r["closed"]
        and 0 <= (r["closes_on"] - today).days <= 7 and r["status"] not in _SENT)
    ready = sum(1 for r in rows if is_ready_not_submitted(r))
    waiting = sum(1 for r in rows if r["group"] == GROUP_SUBMITTED)
    live = sum(1 for r in rows if not r["closed"] and r["status"] not in _TERMINAL)
    cells = [("Closing within 7 days", closing_soon, "crit" if closing_soon else ""),
             ("Ready, NOT submitted", ready, "crit" if ready else ""),
             ("Submitted, awaiting", waiting, ""),
             ("Still open", live, "ok" if live else "crit")]
    return ('<dl class="strip">' + "".join(
        f'<div class="stat {c}"><dt>{t}</dt><dd>{n}</dd></div>' for t, n, c in cells)
        + "</dl>")


def _alerts(rows: list[dict]) -> str:
    """The loudest thing on the page, and only when it has earned it.

    Three separate ways work already paid for returns nothing:
      - written and unsent while the vacancy is still open (recoverable today)
      - begun in the employer's portal, where an incomplete application is
        deleted after about a week
      - nothing open on the board at all, so compiling more documents is
        motion without progress
    """
    out = []

    ready = sorted([r for r in rows if is_ready_not_submitted(r)], key=_sort_key)
    if ready:
        items = "".join(
            f'<li><a href="/pkg/{urllib.parse.quote(r["slug"])}">'
            f'{html.escape(r["title"] or r["slug"])}</a> &mdash; {_days_word(r)}</li>'
            for r in ready)
        out.append(
            f'<div class="alert"><b>{len(ready)} application'
            f'{"s are" if len(ready) != 1 else " is"} written but not submitted.</b> '
            f'The work is already done; only the Submit button is missing.'
            f'<ul class="alert-list">{items}</ul>'
            f'<span class="alert-foot">It counts only when the '
            f'&ldquo;Application received&rdquo; email arrives.</span></div>')

    stale = [r for r in rows if is_portal_stale(r)]
    if stale:
        items = "".join(
            f'<li>{html.escape(r["title"] or r["slug"])} &mdash; '
            f'started {portal_days_open(r)} days ago</li>' for r in stale)
        out.append(
            f'<div class="alert"><b>Begun in the employer&rsquo;s portal and left '
            f'unfinished.</b> An incomplete application is typically deleted after '
            f'about a week &mdash; finish or it disappears.'
            f'<ul class="alert-list">{items}</ul></div>')

    if rows and not any(not r["closed"] and r["status"] not in _TERMINAL for r in rows):
        out.append(
            '<div class="alert"><b>Nothing on this board is still open.</b> '
            'Every entry has closed or been decided. The useful next action is '
            'adding new vacancies, not compiling more documents.</div>')
    return "".join(out)


def render_board(conn: sqlite3.Connection) -> bytes:
    rows = _board_rows(conn)
    body = [f'<div class="topbar"><h1>{APP_NAME}</h1>'
            '<span><a class="btn secondary" href="/log">Activity log</a> '
            '<form class="inline" method="post" action="/sync">'
            '<button class="btn secondary" type="submit">Sync CV</button></form> '
            '<a class="btn secondary" href="/help" title="How to use this">?</a> '
            '<a class="btn" href="/new">+ Add a job</a></span></div>']

    if not rows:
        body.append('<div class="card empty">Nothing here yet. '
                    'Use &ldquo;+ Add a job&rdquo; at the top right to start.</div>')
        return _page("Overview", "".join(body))

    body.append(_status_strip(rows))
    body.append(_alerts(rows))
    body.append(f'<p class="hint">Sorted by closing date, soonest first &middot; '
                f'today is {_dt.date.today().isoformat()}</p>')

    grouped: dict[str, list[dict]] = {g: [] for g in GROUP_ORDER}
    for r in rows:
        grouped[r["group"]].append(r)
    for g in grouped:
        grouped[g].sort(key=_sort_key)

    for g in GROUP_ORDER:
        items = grouped[g]
        if not items:
            continue
        rows_html = "".join(_board_row_html(r) for r in items)
        note = GROUP_NOTE.get(g, "")
        note_html = f'<span class="note">{note}</span>' if note else ""
        if g == GROUP_CLOSED:
            body.append(f'<details class="past"><summary>{GROUP_TITLE[g]} ({len(items)})'
                        f'</summary><div class="rows">{rows_html}</div></details>')
        else:
            body.append(
                f'<div class="group"><div class="group-head"><h2>{GROUP_TITLE[g]}</h2>'
                f'<span class="count">{len(items)}</span>{note_html}</div>'
                f'<div class="rows">{rows_html}</div></div>')
    return _page("Overview", "".join(body))


def render_log(conn: sqlite3.Connection) -> bytes:
    """Activity log — rebuilt from the events table on every view, so it is never stale."""
    applog.write(conn)
    return _page("Activity log", '<div class="topbar"><h1>🗓 Activity log</h1>'
                 '<a class="btn secondary" href="/">← Back to the board</a></div>'
                 '<div class="card">' + _md_to_html(applog.build(conn)) + '</div>')


def render_new_form(error: str | None = None, values: dict | None = None,
                    notice: str | None = None) -> bytes:
    v = values or {}

    def val(key: str) -> str:
        return html.escape(str(v.get(key) or ""), quote=True)

    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    ok = f'<p class="ok">{notice}</p>' if notice else ""
    body = f"""
<div class="topbar"><h1>Add a new entry</h1><a class="btn secondary" href="/">← Back to the board</a></div>
<p class="sub">Three ways in. Use whichever suits, or <b>mix them</b> (For example: fetched page text in the middle,
upload the official PD below — both are merged into one entry.)</p>
{err}{ok}
<div class="card">
<form method="post" action="/analyze" enctype="multipart/form-data">

<p><b>① Paste a link</b> (easiest: paste it, press Fetch, and the text lands in the box below.)</p>
<div class="fields" style="margin-top:.2rem">
<label style="flex:3 1 22rem">Job URL
<input type="url" name="url" value="{val('url')}" placeholder="https://..."></label>
<label style="flex:0 0 auto;justify-content:flex-end">&nbsp;
<button class="btn secondary" type="submit" formaction="/fetch"
        formenctype="application/x-www-form-urlencoded">🔗 Fetch the page</button></label>
</div>
<p class="sub" style="margin:.1rem 0 0">Check the text is right, then press the build button. The system never submits for you.<br>
Failing to read a page is normal (SEEK and Indeed block; LinkedIn is not attempted). Paste or upload instead.
The link is kept either way, as the “go to the employer's page” button.</p>

<p style="margin-top:1.4rem"><b>② Paste text</b> (optional)</p>
<textarea name="raw_text" placeholder="Paste the whole ad here — keep the title, employer and requirements; no need to tidy it up.">{html.escape(str(v.get('raw_text') or ''))}</textarea>

<p style="margin-top:1.2rem"><b>③ Upload a document</b> (optional, more than one allowed)</p>
<div class="drop" id="dropzone">
<p style="margin:0 0 .5rem"><b>Drag files onto this box</b>, or use the button below to choose them:</p>
<input type="file" name="docs" id="docsinput" multiple
       accept=".pdf,.docx,.doc,.rtf,.txt,.md,application/pdf">
<ul class="picked" id="pickedlist"></ul>
<p class="sub" style="margin:.5rem 0 0">PDF, Word (.docx/.doc), .rtf, .txt and .md are supported. 25 MB per file.<br>
⚠️ A scanned or image-only PDF has no extractable text; you will be told so — paste the text instead.</p>
</div>

<div class="fields">
<label>Name (optional — used as the title and folder name)
<input type="text" name="name" value="{val('name')}" placeholder="Research Officer — School of Psychology"></label>
<label>Closing date (optional)
<input type="text" name="deadline" value="{val('deadline')}" placeholder="10 Aug 2026"></label>
</div>

<p><button class="btn" type="submit">Analyse and build the full package</button></p>
<p class="sub">After you submit this, the résumé, cover letter, pre-submission check and gap report are built — a few seconds.</p>
</form>
</div>
"""
    return _page("Add entry", body)


def render_ingest_error(msgs: list[str]) -> bytes:
    items = "".join(f"<li>{html.escape(m)}</li>" for m in msgs)
    body = (f'<div class="topbar"><h1>Could not read this entry</h1>'
            f'<a class="btn secondary" href="/new">← Try again</a></div>'
            f'<div class="card"><ul class="err">{items}</ul>'
            '<p class="sub">Safest route: select the text in the original file, copy it, and paste it into the text box.</p>'
            '<p><a class="btn" href="/new">← Back — try again</a></p></div>')
    return _page("Could not add it", body)


def render_build_failure(slug: str, job_id: str, output: str) -> bytes:
    body = (f'<div class="topbar"><h1>Entry saved, but the package did not finish building</h1>'
            f'<a class="btn secondary" href="/">← Back to the board</a></div>'
            f'<div class="card"><p>&ldquo;{html.escape(slug)}&rdquo;is saved (you can see it on the board),'
            'but building the documents failed. Send the output below to Claude to get it fixed:</p>'
            f'<pre class="resume">{html.escape(output.strip() or "(no output)")}</pre>'
            f'<p><a class="btn" href="/job/{html.escape(job_id)}">Look at what was parsed →</a></p></div>')
    return _page("Build incomplete", body)


def render_result(conn: sqlite3.Connection, job_id: str) -> bytes:
    row = conn.execute("SELECT * FROM jobspecs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return _page("Not found", '<p>No such job.</p><p><a href="/">← Back to the board</a></p>')
    spec = _spec_from_row(row)
    scored = {**spec, **fit.score(conn, spec)}
    seniority = (spec.get("seniority") or "").lower()
    is_academic = seniority in ("postdoc", "professor", "research associate", "principal", "lead") \
        or "research" in (spec.get("title") or "").lower()
    target = "academic" if is_academic else "industry-ai"
    resume_md = compiler.compile_resume(conn, scored, target=target)
    gap = calibration.gap_report(conn, scored)

    pipe = conn.execute("SELECT status FROM pipeline WHERE job_id=?", (job_id,)).fetchone()
    status = pipe["status"] if pipe else "sensed"

    gap_lines = []
    if gap["no_evidence"]:
        gap_lines.append(f'<div class="gap-item"><b>No evidence at all for these — check before the interview:</b> {html.escape(", ".join(gap["no_evidence"]))}</div>')
    if gap["high_stretch"]:
        items = "; ".join(f'{h["skill"]} (Your level {h["user_level"]}, Gap {h["stretch"]})' for h in gap["high_stretch"])
        gap_lines.append(f'<div class="gap-item"><b>Biggest gaps — the cram list:</b> {html.escape(items)}</div>')
    if not gap_lines:
        gap_lines.append('<div class="gap-item">No obvious gaps: every requirement has evidence behind it.</div>')

    opts = "".join(
        f'<option value="{k}"{" selected" if k == status else ""}>{v}</option>'
        for k, v in STATUS_LABEL.items()
    )

    body = f"""
<div class="topbar"><h1>{html.escape(spec.get("title") or "Job")}</h1><a class="btn secondary" href="/">← Back to the board</a></div>
<p class="sub">{html.escape(spec.get("org") or "")} · Fit {_fit_badge(scored["fit_score"], spec.get("visa_requirement"))}
&nbsp; Stretch: {html.escape(scored["zone"])} &nbsp;·&nbsp;
Visa: {"eligible" if scored["visa_feasible"] else "restricted"}</p>

<div class="card">
<form method="post" action="/status">
<input type="hidden" name="job_id" value="{job_id}">
Status: <select name="status" onchange="this.form.submit()">{opts}</select>
</form>
</div>

<div class="card">
<h3>Generated résumé</h3>
<pre class="resume">{html.escape(resume_md)}</pre>
</div>

<div class="card">
<h3>Gap report</h3>
{"".join(gap_lines)}
</div>

<div class="card">
<h3>Original job description</h3>
<pre class="resume">{html.escape(spec.get("raw_text") or "")}</pre>
</div>
"""
    return _page(spec.get("title") or "Job", body)


# ------------------------------------------------------------- ingestion ----

def _slug(*candidates: str) -> str:
    """Folder-safe name for a new entry, from the first usable candidate."""
    for c in candidates:
        s = re.sub(r"[^a-z0-9]+", "_", (c or "").lower()).strip("_")[:48].strip("_")
        if len(s) >= 3:
            return s
    return "job_" + _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _unique_slug(slug: str) -> str:
    REAL_JOBS.mkdir(parents=True, exist_ok=True)
    if not (REAL_JOBS / f"{slug}.txt").exists():
        return slug
    for n in range(2, 100):
        if not (REAL_JOBS / f"{slug}_{n}.txt").exists():
            return f"{slug}_{n}"
    return f"{slug}_{_dt.datetime.now().strftime('%H%M%S')}"


def _assemble_jd(name: str, pasted: str, docs: list[tuple[str, str]],
                 url: str, deadline: str) -> str:
    """One plain-text ad out of the pasted text plus every uploaded document.

    The body comes first because the JD parser reads the title off line 1; the
    provenance/link/deadline footer goes last so it cannot become the title.
    """
    body = [p for p in ([pasted.strip()] + [t.strip() for _, t in docs]) if p]
    # Skip the name when the ad already opens with it — a link-fetched ad
    # leads with its own title, and repeating it just doubles line 1.
    head = [name.strip()] if name.strip() and not any(
        b.lstrip().lower().startswith(name.strip().lower()) for b in body) else []
    parts = head + body
    footer = []
    if docs:
        footer.append("Source files: " + ", ".join(fn for fn, _ in docs))
    if url.strip():
        footer.append(f"Job link: {url.strip()}")
    if deadline.strip():
        # _job_meta() reads this line for the deadline badge on the board.
        footer.append(f"Closes: {deadline.strip()}")
    footer.append(f"(added {_dt.date.today().isoformat()})")
    return "\n\n".join(parts) + "\n\n-----\n" + "\n".join(footer) + "\n"


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w.\- ]", "_", Path(name).name)[:120] or "attachment"


def ingest(conn: sqlite3.Connection, name: str, pasted: str,
           docs: list[tuple[str, str]], url: str, deadline: str) -> tuple[str, str, str, int]:
    """Save a new entry and build its full package. -> (slug, job_id, output, rc)"""
    text = _assemble_jd(name, pasted, docs, url, deadline)
    slug = _unique_slug(_slug(name, Path(docs[0][0]).stem if docs else "",
                              next((ln for ln in text.splitlines() if ln.strip()), "")))
    (REAL_JOBS / f"{slug}.txt").write_text(text, encoding="utf-8")

    spec = jobspec.parse(text, source=slug)
    job_id = jobspec.save(conn, spec)
    scored = fit.score(conn, spec)
    _upsert_pipeline(conn, job_id, fit_score=scored["fit_score"],
                     stretch_score=scored["stretch_score"])

    # apply.py owns package building (personas, learned edits, reviewer, …) —
    # run the same code path the terminal uses instead of duplicating it here.
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "apply.py"), slug],
        capture_output=True, text=True, timeout=300)
    return slug, job_id, proc.stdout + ("\n" + proc.stderr if proc.returncode else ""), proc.returncode


def _save_originals(slug: str, files: list[tuple[str, bytes]]) -> None:
    """Keep the uploaded PDFs next to the JD — the PD is worth re-reading."""
    if not files:
        return
    d = REAL_JOBS / "docs" / slug
    d.mkdir(parents=True, exist_ok=True)
    for filename, data in files:
        (d / _safe_filename(filename)).write_bytes(data)


# --------------------------------------------------------- request parsing ----

def _repair_header_text(s: str) -> str:
    """Browsers send raw UTF-8 in the filename param; email's header decoder
    hands it back as surrogates or as latin-1 mojibake. Put it back together."""
    if any("\ud800" <= ch <= "\udfff" for ch in s):
        return s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _parse_multipart(ctype: str, body: bytes) -> tuple[dict[str, list[str]],
                                                       list[tuple[str, bytes]]]:
    """multipart/form-data -> (text fields, [(filename, bytes)]).

    stdlib only: `cgi` is gone in 3.13, so the body is handed to the email
    parser, which is what implements MIME multipart underneath anyway.
    """
    msg = BytesParser(policy=_email_policy).parsebytes(
        b"Content-Type: " + ctype.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    fields: dict[str, list[str]] = {}
    files: list[tuple[str, bytes]] = []
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            files.append((_repair_header_text(filename), payload))
            continue
        name = part.get_param("name", header="content-disposition")
        if name:
            fields.setdefault(_repair_header_text(str(name)), []).append(
                payload.decode("utf-8", "replace"))
    return fields, files


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet

    def _send(self, payload: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location: str):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _send_download(self, payload: bytes, ctype: str, filename: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        quoted = urllib.parse.quote(filename)
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_static(self, filename: str) -> bool:
        """Serve one allow-listed asset. Returns False if it is not one of ours.

        Deliberately not a directory server: the name must match a key in
        STATIC_FILES exactly, so "../webapp.py" and every other traversal
        attempt simply is not in the mapping.
        """
        ctype = STATIC_FILES.get(filename)
        if ctype is None:
            return False
        try:
            payload = (STATIC_DIR / filename).read_bytes()
        except OSError:
            return False
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        # Cache hard; the ?v=<mtime> query string is what busts it.
        self.send_header("Cache-Control", "public, max-age=31536000")
        self.end_headers()
        self.wfile.write(payload)
        return True

    def do_GET(self):
        path, _, query = self.path.partition("?")
        qs = urllib.parse.parse_qs(query)
        if path.startswith("/static/"):
            # unquote first, so %2e%2e%2f cannot smuggle a separator past the
            # allow-list check below.
            name = urllib.parse.unquote(path[len("/static/"):])
            if self._send_static(name):
                return
            self._send(b"Not found", 404)
            return
        conn = db.connect()
        try:
            _ensure_identity(conn)
            if path == "/" or path == "":
                self._send(render_board(conn))
            elif path == "/new":
                self._send(render_new_form())
            elif path == "/help":
                self._send(render_help())
            elif path == "/log":
                self._send(render_log(conn))
            elif path.startswith("/job/"):
                job_id = path[len("/job/"):]
                self._send(render_result(conn, job_id))
            elif path.startswith("/pkg/"):
                parts = urllib.parse.unquote(path[len("/pkg/"):]).strip("/").split("/")
                if len(parts) == 1:
                    self._send(render_package(parts[0]))
                elif len(parts) == 2:
                    name, fname = parts
                    dl = (qs.get("dl") or [""])[0]
                    if dl == "md":
                        f = _pkg_file(name, fname)
                        if f is None:
                            self._send(b"Not found", 404)
                        else:
                            self._send_download(f.read_bytes(),
                                                "text/markdown; charset=utf-8", fname)
                    elif dl == "doc":
                        payload = _doc_download(name, fname)
                        if payload is None:
                            self._send(b"Not found", 404)
                        else:
                            self._send_download(payload, "application/msword",
                                                fname[:-3] + ".doc")
                    elif (qs.get("edit") or [""])[0]:
                        self._send(render_package_file_edit(name, fname))
                    else:
                        self._send(render_package_file(name, fname))
                else:
                    self._send(b"Not found", 404)
            else:
                self._send(b"Not found", 404)
        finally:
            conn.close()

    def _read_body(self) -> bytes | None:
        """Read the request body, refusing anything absurdly large."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_UPLOAD:
            remaining = length
            while remaining > 0:  # drain, else the browser sees a reset socket
                chunk = self.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                remaining -= len(chunk)
            return None
        return self.rfile.read(length)

    def _handle_fetch(self, fields) -> None:
        """Read one linked ad into the review box. Never builds anything."""
        values = {k: (fields.get(k) or [""])[0].strip()
                  for k in ("url", "raw_text", "name", "deadline")}
        try:
            got = joburl.fetch(values["url"])
        except (joburl.UnfetchableURL, jobdoc.UnreadableDocument) as exc:
            self._send(render_new_form(error=str(exc), values=values))
            return

        # Fetched text replaces the box; anything you had typed is appended so
        # a fetch can never silently eat your own notes.
        text = got["text"]
        if values["raw_text"]:
            text += "\n\n----- pasted by hand -----\n" + values["raw_text"]
        # Role name only: this lands on line 1 of the JD, and jobspec.parse
        # reads the title off line 1 — "Role — Employer" there parses as the
        # title. The employer is already on its own line in the fetched text.
        title = got.get("title") or got.get("org") or ""
        via = {"jsonld": "structured job data (most accurate)", "pdf": "the PDF the link points to",
               "html": "page text"}.get(got.get("via", ""), "web page")
        notice = (f'✅ Read: <b>{html.escape(via)}</b>, {len(got["text"])}  characters.'
                  '<b>Check the text below is right</b> ((any navigation or cookie banners pulled in?),'
                  'edit it in the box if needed, then press “Analyse and build the full package”.')
        self._send(render_new_form(notice=notice, values={
            "url": got.get("url") or values["url"],
            "raw_text": text,
            "name": values["name"] or title,
            "deadline": values["deadline"] or got.get("deadline") or "",
        }))

    def _handle_analyze(self, conn, fields, files) -> None:
        pasted = (fields.get("raw_text") or [""])[0].strip()
        name = (fields.get("name") or [""])[0].strip()
        url = (fields.get("url") or [""])[0].strip()
        deadline = (fields.get("deadline") or [""])[0].strip()

        uploads = [(fn, data) for fn, data in files if fn and data]
        docs: list[tuple[str, str]] = []
        errors: list[str] = []
        for filename, data in uploads:
            try:
                docs.append((Path(filename).name, jobdoc.extract(filename, data)))
            except jobdoc.UnreadableDocument as exc:
                errors.append(str(exc))
        if errors:
            # Nothing is saved on a partial read — a package missing the PD is
            # worse than no package at all.
            self._send(render_ingest_error(errors))
            return
        if not pasted and not docs:
            self._send(render_new_form(error="Paste the job description, or upload a job document (PDF / Word)."))
            return
        if len(pasted) + sum(len(t) for _, t in docs) < 80:
            self._send(render_new_form(error="That is too short to be a full job description. Paste all of it, or upload the original document."))
            return

        slug, job_id, output, rc = ingest(conn, name, pasted, docs, url, deadline)
        _save_originals(slug, uploads)
        if rc or not (PKG / slug).is_dir():
            self._send(render_build_failure(slug, job_id, output))
            return
        self._redirect(f"/pkg/{urllib.parse.quote(slug)}")

    def do_POST(self):
        body = self._read_body()
        if body is None:
            self._send(render_new_form(error="Those files are too large (more than 40 MB in total)."))
            return
        ctype = self.headers.get("Content-Type", "")
        files: list[tuple[str, bytes]] = []
        if ctype.lower().startswith("multipart/form-data"):
            fields, files = _parse_multipart(ctype, body)
        else:
            fields = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
        conn = db.connect()
        try:
            _ensure_identity(conn)
            if self.path == "/analyze":
                self._handle_analyze(conn, fields, files)
            elif self.path == "/fetch":
                self._handle_fetch(fields)
            elif self.path == "/portal":
                job_id = (fields.get("job_id") or [""])[0]
                raw = (fields.get("started_on") or [""])[0].strip()
                if job_id:
                    d = jobspec.parse_closing_date(raw)
                    _upsert_pipeline(conn, job_id, status="started_portal")
                    conn.execute("UPDATE pipeline SET started_on_portal=? WHERE job_id=?",
                                 (d.isoformat() if d else "", job_id))
                    conn.commit()
                self._redirect(self.headers.get("Referer") or "/")
            elif self.path == "/deadline":
                job_id = (fields.get("job_id") or [""])[0]
                raw = (fields.get("closes_at") or [""])[0].strip()
                clearing = bool((fields.get("clear") or [""])[0])
                if job_id:
                    parsed = jobspec.parse_closing_date(raw)
                    value = "" if (clearing or not parsed) else parsed.isoformat()
                    conn.execute("UPDATE jobspecs SET closes_at=? WHERE id=?",
                                 (value, job_id))
                    conn.commit()
                self._redirect(self.headers.get("Referer") or "/")
            elif self.path == "/status":
                job_id = (fields.get("job_id") or [""])[0]
                status = (fields.get("status") or [""])[0]
                if job_id and status in STATUS_LABEL:
                    _upsert_pipeline(conn, job_id, status=status)
                self._redirect(self.headers.get("Referer") or "/")
            elif self.path == "/sync":
                report = materials.sync(conn)
                regen_output = ""
                if not report.get("error"):
                    proc = subprocess.run(
                        [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "apply.py")],
                        capture_output=True, text=True, timeout=300)
                    regen_output = proc.stdout + ("\n" + proc.stderr if proc.returncode else "")
                self._send(render_sync_report(report, regen_output))
            elif self.path == "/pkg/save":
                name = (fields.get("name") or [""])[0]
                fname = (fields.get("fname") or [""])[0]
                content = (fields.get("content") or [""])[0].replace("\r\n", "\n")
                f = _pkg_file(name, fname)
                if f is None:
                    self._send(b"Not found", 404)
                    return
                bak_dir = f.parent / ".backups"
                bak_dir.mkdir(exist_ok=True)
                ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                (bak_dir / f"{fname}.{ts}").write_text(
                    f.read_text(encoding="utf-8"), encoding="utf-8")
                f.write_text(content, encoding="utf-8")
                # T12: learn from this edit immediately so the next regenerate
                # reproduces your wording instead of reverting it.
                revisions.learn_package(conn, f.parent)
                self._redirect(f"/pkg/{urllib.parse.quote(name)}/{urllib.parse.quote(fname)}")
            else:
                self._send(b"Not found", 404)
        finally:
            conn.close()


def run(port: int = PORT) -> None:
    conn = db.connect()
    _ensure_identity(conn)
    conn.close()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"{APP_NAME} · http://localhost:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
