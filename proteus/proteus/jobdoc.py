"""Read a job ad out of an uploaded document — PDF, Word, or plain text.

Many Australian roles publish the whole ad (and the position description) as a
PDF, so "copy the ad text" is not always possible. This module turns an
uploaded file into exactly the kind of plain text the paste box would have
produced; everything downstream (jobspec parsing, fit scoring, the compiler) is
unchanged, so an uploaded job behaves like a pasted one.

Still no hard dependency: PDFs go through `pypdf` when it is installed and fall
back to the `pdftotext` CLI. Image-only (scanned) PDFs are detected and
reported rather than silently ingested as an empty ad.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import materials

SUPPORTED = (".pdf", ".docx", ".doc", ".rtf", ".txt", ".md")
MAX_BYTES = 25 * 1024 * 1024
# Below this many characters a document is treated as unreadable rather than as
# a very short ad — a scanned PDF extracts to a handful of stray glyphs.
MIN_CHARS = 40


class UnreadableDocument(Exception):
    """Message is written for you and shown verbatim in the browser."""


def extract(filename: str, data: bytes) -> str:
    """Plain text of one uploaded job document. Raises UnreadableDocument."""
    name = Path(filename).name
    ext = Path(name).suffix.lower()
    if len(data) > MAX_BYTES:
        raise UnreadableDocument(
            f"“{name}” is too large ({len(data) // (1024 * 1024)} MB; the limit is 25 MB).")
    if not data:
        raise UnreadableDocument(f"“{name}” is an empty file.")
    if ext == ".pdf":
        text = _pdf_text(name, data)
    elif ext == ".docx":
        text = _with_tempfile(name, data, ext, materials.docx_text)
    elif ext in (".doc", ".rtf"):
        text = _textutil_text(name, data, ext)
    elif ext in (".txt", ".md"):
        text = _decode(data)
    else:
        raise UnreadableDocument(
            f"“{name}” is not a format that can be read yet. Supported: PDF, Word "
            "(.docx/.doc), .rtf, .txt and .md. For anything else, paste the text "
            "into the box above.")
    text = tidy(text)
    if len(text.strip()) < MIN_CHARS:
        raise UnreadableDocument(
            f"“{name}” has almost no extractable text — it is probably a scan or an "
            "image-only PDF. Select the text in the original and paste it into the "
            "box above, or run it through a tool that recognises text first.")
    return text


# Running headers and footers repeat on every page of a PD and carry no job
# information. Left in, the first one becomes the parsed job title — a PD
# opens "24/08/2026  HR125  Page 1 of 3", which is what the CBE Operations
# Coordinator package was titled before this (2026-09-09).
_PAGE_FURNITURE = re.compile(
    r"^(?:"
    r"page\s+\d+\s+of\s+\d+"                       # Page 1 of 3
    r"|\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\S+)?\s+page\s+\d+\s+of\s+\d+"
    r"|\d{1,2}/\d{1,2}/\d{2,4}\s+[A-Z]{2,}\d*\s+page\s+\d+\s+of\s+\d+"
    r"|\d+"                                          # a bare page number
    r")$", re.I)


# A running header or footer often ends in its own page number
# ("... Branch | Agency  5"), so no two copies are identical and the
# "repeats on every page" rule never fires. Left in, one of them lands after a
# blank line in the middle of a criteria list, which the criteria parser reads
# as "the list is over" — a 9-criterion PD came out as 2.
_MIN_HEADER_LEN = 30


def _furniture_key(line: str) -> str:
    """The line as it should be counted: whitespace-normalised, lower-cased,
    and with a trailing page number removed.

    Deleting content by mistake is worse than missing a footer, so a trailing
    number is only treated as a page number when it is set off by a layout gap
    (two or more spaces, as in "... Agency  5") and the line is long. Both
    conditions matter: an inline "... unique to page 1" is body text, and short
    rows such as "Level 1" / "Level 2" / "Level 3" are content — none of them
    may be collapsed into one repeated line and swept away.
    """
    squashed = " ".join(line.split()).lower()
    m = re.search(r"\s{2,}\d{1,3}$", line.rstrip())
    if m:
        bare = " ".join(line.rstrip()[:m.start()].split()).lower()
        if len(bare) >= _MIN_HEADER_LEN:
            return bare
    return squashed


def _strip_page_furniture(lines: list[str]) -> list[str]:
    """Drop page numbers and any line repeating on 3+ pages (running header
    or footer). Content lines legitimately repeat at most once or twice."""
    counts: dict[str, int] = {}
    for ln in lines:
        k = _furniture_key(ln)
        if k:
            counts[k] = counts.get(k, 0) + 1
    kept = []
    for ln in lines:
        k = _furniture_key(ln)
        if k and (_PAGE_FURNITURE.match(" ".join(ln.split()).lower()) or counts[k] >= 3):
            continue
        kept.append(ln)
    return kept


def tidy(text: str) -> str:
    """Normalise extractor output into readable lines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = _strip_page_furniture(lines)
    out: list[str] = []
    for ln in lines:
        # PDF layout mode pads with runs of spaces; keep single spaces only.
        ln = re.sub(r"[ \t]{2,}", "  ", ln)
        if not ln.strip() and out and not out[-1].strip():
            continue  # collapse blank runs
        out.append(ln)
    return "\n".join(out).strip()


# ------------------------------------------------------------------- PDF ----

def _pdf_text(name: str, data: bytes) -> str:
    attempts: list[str] = []
    try:
        return _pdf_text_pypdf(data)
    except Exception as exc:  # pypdf missing, encrypted, or malformed
        attempts.append(f"pypdf: {exc}")
    try:
        return _pdf_text_cli(data)
    except Exception as exc:
        attempts.append(f"pdftotext: {exc}")
    raise UnreadableDocument(
        f"Could not read the PDF “{name}” ({'; '.join(attempts)}). "
        "Copying the text out of the PDF and pasting it above works just as well.")


def _pdf_text_pypdf(data: bytes) -> str:
    from pypdf import PdfReader  # optional dependency, imported lazily

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        # Ads are often "protected" with an empty owner password; that opens.
        if not reader.decrypt(""):
            raise ValueError("the PDF is password-protected")
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _pdf_text_cli(data: bytes) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        raise FileNotFoundError("pdftotext is not installed")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.pdf"
        src.write_bytes(data)
        proc = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(src), "-"],
                              capture_output=True, timeout=120)
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[:200])
        return proc.stdout.decode("utf-8", "replace")


# ------------------------------------------------------- Word / RTF / txt ----

def _with_tempfile(name: str, data: bytes, ext: str, reader) -> str:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"upload{ext}"
        p.write_bytes(data)
        try:
            return reader(p)
        except Exception as exc:
            raise UnreadableDocument(f"Could not read “{name}”: {exc}") from exc


def _textutil_text(name: str, data: bytes, ext: str) -> str:
    """Old .doc / .rtf via macOS textutil (present on every Mac)."""
    exe = shutil.which("textutil")
    if not exe:
        raise UnreadableDocument(
            f"Could not read “{name}” (old Word format). Save it as .docx or PDF in "
            "Word and upload that instead.")

    def read(p: Path) -> str:
        proc = subprocess.run([exe, "-convert", "txt", "-stdout", str(p)],
                              capture_output=True, timeout=120)
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[:200])
        return proc.stdout.decode("utf-8", "replace")

    return _with_tempfile(name, data, ext, read)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")
