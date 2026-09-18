"""Read a job ad from a link you pasted — one URL, one click, on request.

This is *assisted paste*, not a crawler. It fetches exactly the one page you is
already looking at, when you press the button, and hands the text back into
the same review box the paste flow uses. There is no queue, no link discovery,
no scheduled re-fetch, and no site is visited that you did not type in.

Three extraction paths, best first:

1. **schema.org JobPosting** (`<script type="application/ld+json">`) — most
   Australian university / government / ATS career pages publish one, and it
   carries the title, the employer, the closing date and the full description
   as clean fields. When it is there, nothing is guessed.
2. **Main-content HTML** — `<main>` / `<article>` / `[role=main]` if present,
   otherwise the body, with chrome (nav, script, footer, …) removed.
3. **PDF** — a link that serves a PDF goes straight to `jobdoc.extract`, so a
   linked position description behaves exactly like an uploaded one.

Sites that wall automated readers (LinkedIn) are refused up front with an
instruction to paste instead, rather than silently ingesting a login page as if
it were an ad. Stdlib only, like the rest of Proteus.
"""
from __future__ import annotations

import gzip
import html as _html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser

from . import jobdoc

TIMEOUT = 25          # seconds; career sites can be slow
MAX_BYTES = 8 * 1024 * 1024
MIN_CHARS = 300       # below this a "job page" is a wall, a stub or a redirect

# A real browser UA: several .gov.au and university CDNs return 403 to the
# default python-urllib string even for pages that are entirely public.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Hosts whose terms forbid automated retrieval outright. Proteus does not try.
_WALLED = {
    "linkedin.com": "LinkedIn's terms forbid automated access, and without a login you would only get a sign-in page.",
    "www.linkedin.com": "LinkedIn's terms forbid automated access, and without a login you would only get a sign-in page.",
    "au.linkedin.com": "LinkedIn's terms forbid automated access, and without a login you would only get a sign-in page.",
}

# Sites that usually work but sometimes serve a bot check — worth a specific hint.
_FLAKY_HINT = {
    "seek.com.au": "SEEK sometimes blocks automated reading.",
    "www.seek.com.au": "SEEK sometimes blocks automated reading.",
    "au.indeed.com": "Indeed sometimes blocks automated reading.",
    "www.indeed.com": "Indeed sometimes blocks automated reading.",
}

# Whole domains that refuse every non-browser request (verified 2026-08-06:
# 403 on the listing, on a job page, on robots.txt and even on the Gazette PDF).
# Retrying never helps here, so say so instead of suggesting a wait.
_ALWAYS_BLOCKED = ("This site returns 403 to every automated request, no matter how long "
                   "you wait. Download the PD and upload it, or paste the text.")
_BLOCKED_SUFFIXES = (
    # Government recruitment portals that hard-block automation. Known from
    # testing; add others as you meet them — the check is a domain-suffix match
    # with impersonation guarded against (notact.gov.au.evil.com never matches).
    ("jobs.act.gov.au", _ALWAYS_BLOCKED),
    ("canberrahealthservices.act.gov.au", _ALWAYS_BLOCKED),
    ("health.act.gov.au", _ALWAYS_BLOCKED),
    ("act.gov.au", _ALWAYS_BLOCKED),
)


def _host_note(host: str) -> str:
    """One line of host-specific advice: a hard block outranks a flaky hint."""
    return _blocked_domain_note(host) or _FLAKY_HINT.get((host or "").lower(), "")


def _blocked_domain_note(host: str) -> str:
    """Advice for hosts known to refuse scripted reads outright ('' if none)."""
    host = (host or "").lower()
    for suffix, note in _BLOCKED_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return note
    return ""


_PASTE_HINT = ("Open the link in your browser, select all the text (⌘A / Ctrl+A), "
               "copy it, and paste it into the box above — or download the job's "
               "PDF and upload that.")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class UnfetchableURL(Exception):
    """Message is written for you and shown verbatim in the browser."""


def fetch(url: str) -> dict:
    """One job ad from one URL. -> {title, org, deadline, text, via, url}

    Raises UnfetchableURL with a message you can act on.
    """
    url = (url or "").strip()
    if not url:
        raise UnfetchableURL("No link was given.")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    # A dotted, well-formed host: else "not a url" would be fetched as a name.
    if not re.fullmatch(r"[A-Za-z0-9¡-￿][A-Za-z0-9¡-￿.\-]*"
                        r"\.[A-Za-z0-9¡-￿\-]{2,}", host):
        raise UnfetchableURL(f"“{url.strip()}” does not look like a web address. "
                             "Copying the whole thing from the browser's address "
                             "bar is the safest way.")
    if host.lower() in _WALLED:
        raise UnfetchableURL(f"{_WALLED[host.lower()]}{_PASTE_HINT}")

    body, ctype, final_url = _get(url, host)
    if "pdf" in ctype or final_url.lower().split("?")[0].endswith(".pdf"):
        text = jobdoc.extract("linked.pdf", body)          # raises UnreadableDocument
        return {"title": "", "org": "", "deadline": "", "text": text,
                "via": "pdf", "url": final_url}

    if not ("html" in ctype or "xml" in ctype or "text" in ctype or not ctype):
        raise UnfetchableURL(
            f"That link returned {ctype or 'an unknown type'} — neither a web page "
            f"nor a PDF. {_PASTE_HINT}")

    page = _decode(body, ctype)
    _check_not_a_challenge(page, host)
    result = _from_jsonld(page) or _from_html(page)
    result["url"] = final_url
    _check_usable(result["text"], host, page)
    return result


# ------------------------------------------------------------------ fetch ----

def _get(url: str, host: str) -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
        "Accept-Language": "en-AU,en;q=0.9,zh-CN;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = _read_all(resp)
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            final_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        raise UnfetchableURL(_http_message(exc.code, host)) from exc
    except urllib.error.URLError as exc:
        raise UnfetchableURL(
            f"Could not reach that address ({exc.reason}). Check the link and your "
            "internet connection.") from exc
    except Exception as exc:                       # timeout, bad TLS, redirect loop
        raise UnfetchableURL(f"Something went wrong opening that address ({exc}). {_PASTE_HINT}") from exc

    if len(raw) > MAX_BYTES:
        raise UnfetchableURL("That page is too large (over 8 MB), so it was not read.")
    return _decompress(raw, enc), ctype, final_url


def _read_all(resp) -> bytes:
    """Whole body, in chunks.

    `resp.read(n)` is *up to* n bytes — on a chunked response it happily stops
    at a chunk boundary, which truncates a gzip body into undecodable garbage
    and looks exactly like an empty page. Loop until EOF instead.
    """
    buf = bytearray()
    while len(buf) <= MAX_BYTES:
        chunk = resp.read(1 << 16)
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def _decompress(raw: bytes, enc: str) -> bytes:
    try:
        if enc == "gzip":
            return gzip.decompress(raw)
        if enc == "deflate":
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        return raw        # mislabelled encoding: the bytes are usually plain
    return raw


def _http_message(code: int, host: str) -> str:
    hint = _host_note(host)
    if code in (401, 403, 429):
        return (f"That site refused an automated read (HTTP {code}). {hint}{_PASTE_HINT}")
    if code == 404:
        return "That link does not open (HTTP 404). The vacancy may have been taken down, or the link is mistyped."
    return f"That site returned an error (HTTP {code}). {_PASTE_HINT}"


def _decode(body: bytes, ctype: str) -> str:
    charsets = []
    m = re.search(r"charset=([\w\-]+)", ctype)
    if m:
        charsets.append(m.group(1))
    m = re.search(rb'charset=["\']?([\w\-]+)', body[:4096], re.I)
    if m:
        charsets.append(m.group(1).decode("ascii", "replace"))
    for cs in charsets + ["utf-8", "gb18030", "latin-1"]:
        try:
            return body.decode(cs)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", "replace")


# ------------------------------------------------------------- schema.org ----

_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)


def _from_jsonld(page: str) -> dict | None:
    """schema.org JobPosting, if the page publishes one."""
    for blob in _LD_RE.findall(page):
        for node in _walk_json(blob):
            if not isinstance(node, dict):
                continue
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" not in [str(t) for t in types]:
                continue
            desc = _tag_text(str(node.get("description") or ""))
            if len(desc.strip()) < MIN_CHARS:
                continue                      # stub posting; fall through to HTML
            org = node.get("hiringOrganization") or {}
            org_name = org.get("name") if isinstance(org, dict) else str(org or "")
            return {
                "title": _clean_line(str(node.get("title") or "")),
                "org": _clean_line(str(org_name or "")),
                "deadline": _date(str(node.get("validThrough") or "")),
                "text": _compose(node, desc),
                "via": "jsonld",
            }
    return None


def _walk_json(blob: str):
    """Every dict inside one ld+json block (arrays and @graph included)."""
    try:
        data = json.loads(blob.strip())
    except (json.JSONDecodeError, ValueError):
        return
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def _compose(node: dict, desc: str) -> str:
    """JobPosting fields -> the plain-text ad the paste box would have held.

    Title and employer lead, because jobspec.parse reads the title off line 1.
    """
    head = [v for v in (_clean_line(str(node.get("title") or "")),) if v]
    org = node.get("hiringOrganization") or {}
    if isinstance(org, dict) and org.get("name"):
        head.append(_clean_line(str(org["name"])))
    loc = _location(node.get("jobLocation"))
    if loc:
        head.append(loc)
    for key, label in (("employmentType", "Employment type"),
                       ("datePosted", "Posted"),
                       ("validThrough", "Closes")):
        val = node.get(key)
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        if val:
            val = _date(str(val)) if key == "validThrough" else _clean_line(str(val))
            head.append(f"{label}: {val}")
    return "\n".join(head) + "\n\n" + desc


def _location(loc) -> str:
    if isinstance(loc, list):
        return ", ".join(filter(None, (_location(x) for x in loc)))
    if not isinstance(loc, dict):
        return _clean_line(str(loc or ""))
    addr = loc.get("address")
    if isinstance(addr, dict):
        bits = [addr.get("addressLocality"), addr.get("addressRegion"),
                addr.get("addressCountry")]
        bits = [b.get("name") if isinstance(b, dict) else b for b in bits]
        return ", ".join(_clean_line(str(b)) for b in bits if b)
    return _clean_line(str(addr or loc.get("name") or ""))


def _date(value: str) -> str:
    """ISO-8601 -> "10 Aug 2026", the format the board's ⏰ badge reads."""
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", value or "")
    if not m:
        return _clean_line(value)
    year, month, day = (int(g) for g in m.groups())
    if not 1 <= month <= 12:
        return _clean_line(value)
    return f"{day} {_MONTHS[month - 1]} {year}"


# ------------------------------------------------------------------- HTML ----

_DROP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe",
              "nav", "header", "footer", "form", "select", "button", "aside"}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "section", "article", "table", "ul", "ol", "dl", "dd", "dt", "hr"}
_MAIN_TAGS = {"main", "article"}


class _Extract(HTMLParser):
    """Text of the page, preferring the main-content subtree when one exists."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.main: list[str] = []
        self.title = ""
        self.heading = ""
        self._drop = 0
        self._capture = ""      # "title" | "h1" | ""
        self._main_depth = 0
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr = {k.lower(): (v or "") for k, v in attrs}
        self._depth += 1
        if tag in _DROP_TAGS:
            self._drop += 1
            return
        # Only the document's own <title>/<h1>, and only the first of each:
        # icon sprites are full of <svg><title>Arrow</title></svg>.
        if tag == "title" and not self._drop and not self.title:
            self._capture = "title"
        elif tag == "h1" and not self._drop and not self.heading:
            self._capture = "h1"
        is_main = tag in _MAIN_TAGS or attr.get("role", "").lower() == "main" \
            or re.search(r"\b(job-?details?|job-?description|position-?description|"
                         r"vacancy|advert)\b", attr.get("id", "") + " " + attr.get("class", ""), re.I)
        if is_main and not self._main_depth:
            self._main_depth = self._depth
            self.main.append("\n")
        if tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_TAGS:
            self._drop = max(0, self._drop - 1)
        elif tag in ("title", "h1"):
            self._capture = ""
        if tag in _BLOCK_TAGS:
            self._emit("\n")
        if self._main_depth and self._depth <= self._main_depth:
            self._main_depth = 0
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        if self._drop:
            return
        if self._capture == "title":
            self.title += data
            return
        if self._capture == "h1":
            self.heading += data
        if not data.strip():
            return
        self._emit(data)

    def _emit(self, s: str) -> None:
        if self._drop:
            return
        self.chunks.append(s)
        if self._main_depth:
            self.main.append(s)


def _from_html(page: str) -> dict:
    p = _Extract()
    try:
        p.feed(page)
        p.close()
    except Exception:
        pass                      # malformed markup: keep whatever parsed
    main = jobdoc.tidy("".join(p.main))
    whole = jobdoc.tidy("".join(p.chunks))
    # The main-content subtree wins only if it kept most of the substance; a
    # tiny <main> on a page that renders the ad elsewhere is a false positive.
    text = main if len(main) >= max(MIN_CHARS, 0.25 * len(whole)) else whole
    return {"title": _page_title(p), "org": "", "deadline": "",
            "text": text, "via": "html"}


def _page_title(p: _Extract) -> str:
    """The role name. <h1> is usually exactly it; <title> carries SEO tails."""
    heading = _clean_line(p.heading)
    if 3 <= len(heading) <= 120:
        return heading
    # "Role — Employer | Careers" / "Role - Job in Melbourne - Employer":
    # keep the head, drop the boilerplate tail.
    title = re.split(r"\s+[|·]\s+|\s+[–—-]\s+(?=Job\b|Careers?\b|Jobs\b)",
                     _clean_line(p.title))[0].strip()
    return title[:120]


def _tag_text(fragment: str) -> str:
    """Strip tags out of an HTML description field (JobPosting carries HTML)."""
    fragment = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<\s*(br|/p|/div|/li|/tr|/h[1-6])\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return jobdoc.tidy(_html.unescape(fragment))


def _clean_line(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(s or "")).strip()


# ----------------------------------------------------------------- checks ----

_WALL_MARKERS = (
    "sign in to continue", "please enable javascript", "enable javascript to",
    "are you a robot", "verify you are human", "unusual traffic",
    "access denied", "checking your browser", "cf-browser-verification",
)

# Bot-check interstitials. They arrive as HTTP 200 with a normal-looking shell,
# so they have to be recognised from the markup — otherwise the ad "reads" as
# an empty page and the real reason (a robot check) never reaches you.
# Vendor fingerprints; these appear on nothing but a challenge page.
_CHALLENGE_MARKERS = (
    "awswafcookiedomainlist", "token.awswaf.com", "cf-browser-verification",
    "__cf_chl", "cf_chl_opt", "_incapsula_resource", "distil_r_captcha",
    "px-captcha",
)
# Weak signals: a real ad carries these too — a job page whose *application
# form* uses reCAPTCHA is not a challenge page — so they are consulted only
# once extraction has already failed, to explain *why* it failed.
_WEAK_CHALLENGE_MARKERS = ("challenge-container", "not a robot",
                           "recaptcha/api.js", "hcaptcha.com/1/api.js")


def _check_not_a_challenge(page: str, host: str) -> None:
    """Refuse an interstitial before extraction — on vendor fingerprints only."""
    if any(m in page.lower() for m in _CHALLENGE_MARKERS):
        raise UnfetchableURL(_challenge_message(host))


def _challenge_message(host: str) -> str:
    return (f"That site showed a bot check instead of the job text. {_host_note(host)}"
            "Reading several jobs from one site in a row tends to trigger it; "
            "waiting a few minutes usually clears it. "
            f"If you are in a hurry: {_PASTE_HINT}")


def _check_usable(text: str, host: str, page: str = "") -> None:
    low = text.lower()
    hint = _host_note(host)
    if any(m in low for m in _WALL_MARKERS) and len(text) < 4000:
        raise UnfetchableURL(
            f"What came back was a sign-in, verification or JavaScript-required page, "
            f"not the job text."
            f"{hint}{_PASTE_HINT}")
    if len(text.strip()) < MIN_CHARS:
        # Nothing came out. If the markup hints at a captcha, that is the real
        # reason and it is worth retrying later — say so instead of "no text".
        if any(m in page.lower() for m in _WEAK_CHALLENGE_MARKERS):
            raise UnfetchableURL(_challenge_message(host))
        raise UnfetchableURL(
            f"Almost no text came off that page (only {len(text.strip())} characters) — "
            f"it is probably rendered by JavaScript. {hint}{_PASTE_HINT}")
