"""Reading a job ad from a pasted link. No real network: `_get` is stubbed."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import joburl, webapp  # noqa: E402

DESC = ("<p>The Example University is seeking a Senior Research Fellow to "
        "lead the sampling program.</p><ul><li>Design and run mixed-methods "
        "studies on soil microbiome sampling</li><li>Publish in top journals and "
        "supervise HDR students</li><li>Win competitive grants</li></ul>"
        "<p>Essential: a PhD in a relevant discipline, a strong publication "
        "record, demonstrated statistical and qualitative analysis skills, and "
        "experience writing for policy audiences. Applicants must have the "
        "right to work in Australia.</p>")

JOBPOSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Laboratory Coordinator — Example Institute",
    "hiringOrganization": {"@type": "Organization", "name": "Example University"},
    "jobLocation": {"@type": "Place",
                    "address": {"addressLocality": "Bruce", "addressRegion": "ACT"}},
    "employmentType": "FULL_TIME",
    "validThrough": "2026-08-10T23:59:59+10:00",
    "description": DESC,
}


def page(*, jsonld=None, body="", title="Careers"):
    ld = (f'<script type="application/ld+json">{json.dumps(jsonld)}</script>'
          if jsonld is not None else "")
    return (f"<!doctype html><html><head><title>{title}</title>{ld}"
            "<style>.x{color:red}</style></head>"
            f"<body><nav>Home Jobs Contact</nav>{body}"
            "<footer>© Example University</footer></body></html>")


@pytest.fixture
def serve(monkeypatch):
    """Stub the one network call: serve(bytes|str, ctype) -> joburl sees it."""
    def _serve(payload, ctype="text/html; charset=utf-8", url="https://example.edu.au/job/1"):
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        monkeypatch.setattr(joburl, "_get", lambda u, h: (data, ctype, url))
    return _serve


# ------------------------------------------------------------- happy paths ----

def test_jsonld_jobposting_gives_title_org_and_deadline(serve):
    serve(page(jsonld=JOBPOSTING, body="<main><p>see above</p></main>"))
    got = joburl.fetch("https://example.edu.au/job/1")
    assert got["via"] == "jsonld"
    assert got["title"] == "Laboratory Coordinator — Example Institute"
    assert got["org"] == "Example University"
    # Formatted the way the board's ⏰ badge reads it.
    assert got["deadline"] == "10 Aug 2026"
    assert "soil microbiome sampling" in got["text"]
    assert "<p>" not in got["text"] and "<li>" not in got["text"]
    # jobspec.parse reads the title off line 1.
    assert got["text"].splitlines()[0] == "Laboratory Coordinator — Example Institute"


def test_jsonld_inside_a_graph_array_is_found(serve):
    serve(page(jsonld=[{"@type": "WebSite", "name": "Careers"},
                       {"@graph": [JOBPOSTING]}]))
    assert joburl.fetch("https://example.edu.au/job/1")["via"] == "jsonld"


def test_html_fallback_prefers_main_and_drops_chrome(serve):
    serve(page(body=f"<main><h1>Senior Research Fellow</h1>{DESC}</main>",
               title="Senior Research Fellow | UC Careers"))
    got = joburl.fetch("https://example.edu.au/job/1")
    assert got["via"] == "html"
    assert "soil microbiome sampling" in got["text"]
    assert "Home Jobs Contact" not in got["text"]      # <nav> dropped
    assert "© Example University" not in got["text"]  # <footer> dropped
    assert ".x{color:red}" not in got["text"]          # <style> dropped
    assert got["title"] == "Senior Research Fellow"    # from <h1>, not <title>


def test_icon_sprite_titles_do_not_become_the_entry_name(serve):
    """<svg><title>Arrow</title></svg> is not the page title — the name field
    feeds the folder name, so junk here names the whole application package."""
    icons = "".join(f"<svg><title>Icon {i}</title></svg>" for i in range(12))
    serve(page(body=f"<main>{icons}<h1>Program Support Officer</h1>{DESC}</main>",
               title="Program Support Officer – Job in Melbourne – MCWH"))
    got = joburl.fetch("https://example.edu.au/job/1")
    assert got["title"] == "Program Support Officer"
    assert "Icon" not in got["title"]


def test_title_tail_is_trimmed_when_there_is_no_h1(serve):
    serve(page(body=f"<main>{DESC}</main>",
               title="Laboratory Coordinator – Job in Example City – EU"))
    assert joburl.fetch("https://example.edu.au/job/1")["title"] == "Laboratory Coordinator"


def test_html_falls_back_to_whole_body_when_main_is_a_stub(serve):
    serve(page(body=f"<main><p>Apply now</p></main><div id='content'>{DESC}</div>"))
    assert "soil microbiome sampling" in joburl.fetch("https://example.edu.au/job/1")["text"]


def test_pdf_link_goes_through_the_document_reader(serve, monkeypatch):
    monkeypatch.setattr(joburl.jobdoc, "extract", lambda fn, data: "PD text " * 40)
    serve(b"%PDF-1.4 ...", ctype="application/pdf")
    got = joburl.fetch("https://example.edu.au/pd.pdf")
    assert got["via"] == "pdf" and "PD text" in got["text"]


def test_iso_date_without_time_still_formats(serve):
    serve(page(jsonld={**JOBPOSTING, "validThrough": "2026-12-01"}))
    assert joburl.fetch("https://example.edu.au/job/1")["deadline"] == "1 Dec 2026"


# ----------------------------------------------------------------- refusals ----

def test_linkedin_is_refused_without_a_request(monkeypatch):
    monkeypatch.setattr(joburl, "_get", lambda u, h: pytest.fail("must not fetch"))
    with pytest.raises(joburl.UnfetchableURL, match="LinkedIn"):
        joburl.fetch("https://www.linkedin.com/jobs/view/123")


def test_login_wall_is_not_ingested_as_an_ad(serve):
    serve(page(body="<main><p>Sign in to continue. " + "Please enable JavaScript to view this job. " * 12 + "</p></main>"))
    with pytest.raises(joburl.UnfetchableURL, match="sign-in, verification or JavaScript-required"):
        joburl.fetch("https://www.seek.com.au/job/1")


def test_aws_waf_bot_check_is_named_for_what_it_is(serve):
    """A challenge page is HTTP 200 and looks normal — say "robot check", not
    "empty page", or no idea it is worth retrying in five minutes."""
    serve('<html><head><script>window.awsWafCookieDomainList=[];</script>'
          '<script src="https://x.token.awswaf.com/challenge.js"></script></head>'
          '<body><div id="challenge-container"></div></body></html>')
    with pytest.raises(joburl.UnfetchableURL, match="bot check"):
        joburl.fetch("https://careers.example.edu.au/jobs/x")


def test_recaptcha_on_a_real_ad_is_not_mistaken_for_a_challenge(serve):
    """A job page whose *application form* uses reCAPTCHA is a readable ad."""
    serve(page(jsonld=JOBPOSTING,
               body='<main>' + DESC * 3 +
                    '<script src="https://www.google.com/recaptcha/api.js"></script></main>'))
    assert joburl.fetch("https://example.edu.au/job/1")["via"] == "jsonld"


def test_javascript_only_page_reports_instead_of_saving_nothing(serve):
    serve(page(body="<main><div id='root'></div></main>"))
    with pytest.raises(joburl.UnfetchableURL, match="Almost no text"):
        joburl.fetch("https://example.edu.au/job/1")


def test_non_document_content_type_is_refused(serve):
    serve(b"\x89PNG\r\n", ctype="image/png")
    with pytest.raises(joburl.UnfetchableURL, match="neither a web page"):
        joburl.fetch("https://example.edu.au/flyer.png")


def test_empty_and_malformed_urls(monkeypatch):
    monkeypatch.setattr(joburl, "_get", lambda u, h: pytest.fail("must not fetch"))
    for bad in ("", "   ", "not a url"):
        with pytest.raises(joburl.UnfetchableURL):
            joburl.fetch(bad)


def test_bare_host_is_upgraded_to_https(serve, monkeypatch):
    seen = {}

    def _get(url, host):
        seen["url"] = url
        return page(jsonld=JOBPOSTING).encode("utf-8"), "text/html", url

    monkeypatch.setattr(joburl, "_get", _get)
    joburl.fetch("example.edu.au/job/1")
    assert seen["url"] == "https://example.edu.au/job/1"


# ---------------------------------------------------- fetch -> saved ad ----

def test_fetched_ad_assembles_into_a_clean_jd(serve):
    """The link path must hand jobspec the same shape the paste path does:
    role name on line 1, link and closing date in the footer."""
    serve(page(jsonld=JOBPOSTING))
    got = joburl.fetch("https://example.edu.au/job/1")
    jd = webapp._assemble_jd(got["title"], got["text"], [], got["url"], got["deadline"])
    lines = jd.splitlines()
    assert lines[0] == "Laboratory Coordinator — Example Institute"
    assert lines[1] != lines[0]          # name not repeated on top of the ad
    assert "Job link: https://example.edu.au/job/1" in jd
    assert "Closes: 10 Aug 2026" in jd   # _job_meta reads this for the ⏰ badge
    assert webapp.jobspec.parse(jd, source="t")["title"] == "Laboratory Coordinator — Example Institute"


def test_name_still_leads_when_it_is_not_already_the_first_line(serve):
    """The paste path is unchanged: a name you typed still heads the JD."""
    jd = webapp._assemble_jd("Example Institute Coordinator", "Some ad text about research.",
                             [], "", "")
    assert jd.splitlines()[0] == "Example Institute Coordinator"


# --------------------------------------------------------------- the form ----

def test_fetch_prefills_the_review_box_and_never_builds(monkeypatch):
    """POST /fetch renders the form back with the ad in it — nothing is saved."""
    monkeypatch.setattr(joburl, "fetch", lambda url: {
        "title": "Senior Research Fellow", "org": "Example University",
        "deadline": "10 Aug 2026", "text": "AD BODY " * 50,
        "via": "jsonld", "url": "https://example.edu.au/job/1"})
    sent = []

    class FakeHandler(webapp.Handler):
        def __init__(self):
            pass
        _send = lambda self, payload, status=200: sent.append(payload)  # noqa: E731

    FakeHandler()._handle_fetch({"url": ["https://example.edu.au/job/1"],
                                 "raw_text": ["a note I typed myself"]})
    html_out = sent[0].decode("utf-8")
    assert "AD BODY" in html_out
    assert "a note I typed myself" in html_out            # your own note is kept, not eaten
    # Role name only — it lands on line 1 of the JD, where jobspec reads the
    # title; "Role — Employer" there would parse as the title.
    assert 'name="name" value="Senior Research Fellow"' in html_out
    assert "10 Aug 2026" in html_out
    assert "Read:" in html_out or "Read:" in html_out


def test_fetch_failure_keeps_what_she_typed(monkeypatch):
    monkeypatch.setattr(joburl, "fetch",
                        lambda url: (_ for _ in ()).throw(joburl.UnfetchableURL("blocked")))
    sent = []

    class FakeHandler(webapp.Handler):
        def __init__(self):
            pass
        _send = lambda self, payload, status=200: sent.append(payload)  # noqa: E731

    FakeHandler()._handle_fetch({"url": ["https://www.seek.com.au/job/1"],
                                 "raw_text": ["half-written draft"], "name": ["Example Institute"]})
    html_out = sent[0].decode("utf-8")
    assert "blocked" in html_out
    assert "half-written draft" in html_out and "Example Institute" in html_out
    assert "https://www.seek.com.au/job/1" in html_out


def test_act_government_hosts_get_a_dont_bother_retrying_message(serve):
    """ACT Gov 403s every scripted request, Gazette PDFs included — telling your
    to 'try again in a few minutes' would just waste your time."""
    for host in ("jobs.act.gov.au", "www.canberrahealthservices.act.gov.au"):
        note = joburl._host_note(host)
        assert "403 to every automated request" in note
    # A hard block outranks the softer 'sometimes blocks' hint.
    assert "SEEK" in joburl._host_note("www.seek.com.au")
    # Suffix matching must not be fooled by a lookalike host.
    assert joburl._host_note("notact.gov.au.example.com") == ""
    assert joburl._host_note("careers.example.edu.au") == ""


def test_blocked_host_note_reaches_the_user_on_a_403(monkeypatch):
    """Stub urlopen, not _get — the HTTPError -> message conversion is in _get."""
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(joburl.urllib.request, "urlopen", boom)
    with pytest.raises(joburl.UnfetchableURL, match="403 to every automated request"):
        joburl.fetch("https://www.jobs.act.gov.au/jobs/health/temporary/1")
    # A host with no note still gets the generic refusal, not a blank one.
    with pytest.raises(joburl.UnfetchableURL, match="refused an automated read"):
        joburl.fetch("https://example.org/job/1")
