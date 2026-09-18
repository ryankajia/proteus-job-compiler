"""CSS and JS are served as files, not inlined into f-strings.

Inlining them meant every literal { and } had to be doubled, and a single
missed brace stopped webapp.py parsing at all. These tests keep the assets
out of the module and keep the route from becoming a directory server.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from proteus import webapp  # noqa: E402


class _FakeHandler(webapp.Handler):
    """Exercise the routing without opening a socket."""

    def __init__(self):  # noqa: D107 - deliberately skips BaseHTTPRequestHandler.__init__
        self.status = None
        self.headers_sent = {}
        self.body = b""

    def send_response(self, code, *a):
        self.status = code

    def send_header(self, k, v):
        self.headers_sent[k] = v

    def end_headers(self):
        pass

    @property
    def wfile(self):
        handler = self

        class _W:
            @staticmethod
            def write(data):
                handler.body += data

        return _W()


def test_css_is_served_with_the_right_content_type():
    h = _FakeHandler()
    assert h._send_static("app.css") is True
    assert h.status == 200
    assert h.headers_sent["Content-Type"] == "text/css; charset=utf-8"
    assert b"body{" in h.body


def test_js_is_served_with_the_right_content_type():
    h = _FakeHandler()
    assert h._send_static("app.js") is True
    assert h.status == 200
    assert h.headers_sent["Content-Type"].startswith("application/javascript")
    assert b"dropzone" in h.body


@pytest.mark.parametrize("attempt", [
    "../webapp.py",
    "../../proteus/webapp.py",
    "..%2Fwebapp.py",
    "/etc/passwd",
    "app.css/../../webapp.py",
    "",
    "APP.CSS",            # the allow-list is exact, not case-folded
])
def test_traversal_and_unknown_names_are_refused(attempt):
    h = _FakeHandler()
    assert h._send_static(attempt) is False
    assert h.status is None, "nothing may be written for a refused name"


def test_the_allow_list_is_the_only_way_in():
    """Adding a file to static/ must not make it servable by accident."""
    assert set(webapp.STATIC_FILES) == {"app.css", "app.js"}


def test_no_doubled_braces_left_in_webapp():
    """The escaping hazard this PR exists to remove."""
    src = (Path(webapp.__file__)).read_text(encoding="utf-8")
    assert "{{" not in src and "}}" not in src


def test_pages_link_the_assets_with_a_cache_buster():
    page = webapp._page("t", "<p>body</p>").decode("utf-8")
    assert '<link rel="stylesheet" href="/static/app.css?v=' in page
    assert '<script src="/static/app.js?v=' in page and "defer" in page
    assert "<style>" not in page, "stylesheets are linked, not inlined"


def test_asset_version_changes_with_mtime(tmp_path, monkeypatch):
    f = tmp_path / "app.css"
    f.write_text("body{}", encoding="utf-8")
    monkeypatch.setattr(webapp, "STATIC_DIR", tmp_path)
    first = webapp._asset_version("app.css")
    import os
    os.utime(f, (first + 100, first + 100))
    assert webapp._asset_version("app.css") != first


def test_asset_version_is_zero_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "STATIC_DIR", tmp_path)
    assert webapp._asset_version("app.css") == 0


def test_word_download_keeps_its_css_inline():
    """A downloaded .doc has no server to fetch /static from."""
    assert "font-family:Arial" in webapp.DOC_CSS
