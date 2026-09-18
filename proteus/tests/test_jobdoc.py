"""Uploading a job ad as a document (PDF / Word / text) — no network, no API key."""
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import jobdoc, webapp  # noqa: E402

AD_LINES = ["Laboratory Coordinator - Example Institute", "Example University",
            "Closes: 2 August 2026",
            "Requirements: Python, statistics, report writing"]


def make_pdf(lines: list[str]) -> bytes:
    """A minimal but valid one-page PDF. No third-party writer needed."""
    content = "BT /F1 12 Tf 72 720 Td 14 TL\n"
    for ln in lines:
        esc = ln.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content += f"({esc}) Tj T*\n"
    content += "ET\n"
    cb = content.encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(cb)).encode() + b" >>\nstream\n" + cb + b"endstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


def make_docx(paragraphs: list[str]) -> bytes:
    import io
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    doc = ('<?xml version="1.0"?><w:document '
           'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


# ---------------------------------------------------------- extraction ----

def test_pdf_text_is_extracted():
    text = jobdoc.extract("ad.pdf", make_pdf(AD_LINES))
    assert "Laboratory Coordinator" in text
    assert "report writing" in text


def test_scanned_pdf_is_reported_not_ingested_empty():
    with pytest.raises(jobdoc.UnreadableDocument) as e:
        jobdoc.extract("scan.pdf", make_pdf([]))
    assert "scan or an" in str(e.value)


def test_docx_and_txt():
    assert "Example University" in jobdoc.extract("pd.docx", make_docx(AD_LINES))
    assert "Example University" in jobdoc.extract("ad.txt", "\n".join(AD_LINES).encode("utf-8"))


def test_unsupported_type_and_empty_file():
    with pytest.raises(jobdoc.UnreadableDocument):
        jobdoc.extract("ad.pages", b"x" * 500)
    with pytest.raises(jobdoc.UnreadableDocument):
        jobdoc.extract("ad.pdf", b"")


def test_tidy_collapses_pdf_padding():
    assert jobdoc.tidy("a\r\nb\n\n\n\nc   \n\x0cd    e") == "a\nb\n\nc\n\nd  e"


# ------------------------------------------------------- form plumbing ----

def _multipart(fields: dict[str, str], files: list[tuple[str, bytes]]) -> tuple[str, bytes]:
    b = "----proteustest"
    out = b""
    for k, v in fields.items():
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                f"{v}\r\n").encode("utf-8")
    for fn, data in files:
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"docs\"; "
                f"filename=\"{fn}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
                ).encode("utf-8") + data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return f"multipart/form-data; boundary={b}", out


def test_multipart_parse_keeps_bytes_and_unicode_filenames():
    pdf = make_pdf(AD_LINES)
    ctype, body = _multipart({"raw_text": "pasted body text", "name": "example_inst"},
                             [("position_description.pdf", pdf)])
    fields, files = webapp._parse_multipart(ctype, body)
    assert fields["raw_text"] == ["pasted body text"]
    assert fields["name"] == ["example_inst"]
    assert len(files) == 1
    assert files[0][0] == "position_description.pdf"
    assert files[0][1] == pdf  # byte-exact: PDFs must survive the round trip


def test_slug_falls_back_through_candidates():
    assert webapp._slug("Example Institute Coordinator") == "example_institute_coordinator"
    assert webapp._slug("!!!", "position_description") == "position_description"
    assert webapp._slug("!!!", "").startswith("job_")


def test_assembled_ad_keeps_the_title_first_and_meta_last():
    text = webapp._assemble_jd("Research Fellow", "pasted body",
                               [("pd.pdf", "criteria text")],
                               "https://example.org/apply", "10 Aug 2026")
    lines = text.splitlines()
    assert lines[0] == "Research Fellow"          # what the board will show
    assert "pasted body" in text and "criteria text" in text
    assert "Closes: 10 Aug 2026" in text
    assert text.index("Closes:") > text.index("criteria text")
