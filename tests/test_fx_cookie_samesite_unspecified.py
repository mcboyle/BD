"""fx-cookie-samesite-unspecified: an imported cookie keeps a sameSite the browser honours.

test3 (2026-09-30 00:0xZ): the operator's adulttime import carried SID with
sameSite "unspecified" and secure false. ``normalize_stored_cookie`` turned every
value outside Strict/Lax/None into "None"; Chromium's ``add_cookies`` accepts
SameSite=None without Secure and then drops it, so SID never reached the worker
and every job landed on the login page.

The mapping cases pin each exporter spelling. The browser cases load a jar through
``load_cookies_from_file`` into real Chromium and ask the loopback server which
cookies it was sent; the control proves the probe can see a dropped cookie.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bulk_downloader import cookies

BD_GATE_SCOPE = "module"


@pytest.mark.parametrize("same_site, secure, expected", [
    ("unspecified", False, "Lax"),
    ("unspecified", True, "Lax"),
    (None, False, "Lax"),
    ("", False, "Lax"),
    ("lax", False, "Lax"),
    ("Lax", False, "Lax"),
    ("strict", False, "Strict"),
    ("STRICT", True, "Strict"),
    ("Strict", False, "Strict"),
    ("no_restriction", True, "None"),
    ("none", True, "None"),
    ("None", True, "None"),
    ("no_restriction", False, "Lax"),
    ("None", False, "Lax"),
    ("bogus", False, "Lax"),
])
def test_exported_samesite_maps_to_a_value_chromium_keeps(same_site, secure, expected):
    stored = {"name": "SID", "value": "v", "domain": "example.invalid", "path": "/",
              "secure": secure}
    if same_site is not None:
        stored["sameSite"] = same_site
    got = cookies.normalize_stored_cookie(stored)
    assert got["sameSite"] == expected, (
        f"FX-SAMESITE: sameSite {same_site!r} secure={secure} -> {got['sameSite']!r}, "
        f"expected {expected!r}")
    assert got["secure"] is secure


def test_a_missing_samesite_key_is_lax():
    got = cookies.normalize_stored_cookie(
        {"name": "SID", "value": "v", "domain": "example.invalid"})
    assert got["sameSite"] == "Lax"


class _EchoCookies:
    def __init__(self):
        seen = self.seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Cookie") or "")
                body = b"<html><body>members</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/members"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site():
    s = _EchoCookies()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def context():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            yield browser.new_context()
        finally:
            browser.close()


# Extension-export shape of the test3 adulttime jar (names only; zero-entropy values).
_EXPORT = [
    {"name": "SID", "value": "zero-entropy-fixture", "domain": "127.0.0.1", "path": "/",
     "sameSite": "unspecified", "secure": False, "httpOnly": True, "hostOnly": True,
     "session": True, "storeId": "0"},
    {"name": "identityStatus", "value": "fixture", "domain": "127.0.0.1", "path": "/",
     "sameSite": "no_restriction", "secure": False, "httpOnly": False},
    {"name": "pref", "value": "fixture", "domain": "127.0.0.1", "path": "/",
     "sameSite": "lax", "secure": False, "httpOnly": False},
]


def _sent_names(site, context):
    page = context.new_page()
    page.goto(site.url)
    header = site.seen[-1]
    return sorted(part.split("=", 1)[0].strip() for part in header.split(";") if "=" in part)


def test_an_imported_unspecified_session_cookie_reaches_the_site(tmp_path, site, context):
    jar = tmp_path / "adulttime.json"
    jar.write_text(json.dumps(_EXPORT), encoding="utf-8")
    context.add_cookies(cookies.load_cookies_from_file(str(jar)))
    names = _sent_names(site, context)
    assert names == ["SID", "identityStatus", "pref"], (
        f"FX-SAMESITE: the browser sent {names} of the imported SID/identityStatus/pref; "
        f"a non-Secure cookie loaded as SameSite=None is dropped by Chromium")


def test_control_samesite_none_without_secure_is_dropped_by_chromium(site, context):
    """The probe can say no: the shape the old mapping produced never reaches the site."""
    context.add_cookies([
        {"name": "SID", "value": "fixture", "domain": "127.0.0.1", "path": "/",
         "sameSite": "None", "secure": False},
        {"name": "kept", "value": "fixture", "domain": "127.0.0.1", "path": "/",
         "sameSite": "Lax", "secure": False},
    ])
    assert _sent_names(site, context) == ["kept"]
