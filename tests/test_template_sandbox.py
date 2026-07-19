"""Tests for Phase 176: template sandbox HTTP + browser modes.

The sandbox lets operators test a draft template's selectors against
a live URL without saving. Two modes:
  • http (default): urllib GET — fast, static-only
  • browser: Playwright — slow, JS-aware

These tests cover argument validation + the HTTP mode against a
local test server. Browser-mode tests are guarded by a Playwright
availability check so they skip cleanly when chromium isn't
installed.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import unittest


def _isolated_bd():
    tmp = tempfile.mkdtemp(prefix="bd-176-test-")
    # v3.66.9: snapshot env + modules so teardown_module can
    # restore them. Without this, BD_INSTALL_DIR leaks to
    # subsequent test files that use chdir-only isolation.
    if not _ISOLATED_BD_SAVED:
        _ISOLATED_BD_SAVED['env'] = {
            k: os.environ.get(k)
            for k in ('BD_INSTALL_DIR', 'BD_DISABLE_KEEPALIVE')
        }
        _ISOLATED_BD_SAVED['modules'] = {
            k: v for k, v in sys.modules.items()
            if k.startswith('bulk_downloader')
        }
    os.environ["BD_INSTALL_DIR"] = tmp
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    for m in list(sys.modules):
        if m.startswith("bulk_downloader"):
            del sys.modules[m]
    return tmp


_ISOLATED_BD_SAVED = {}


def teardown_module(module):
    """v3.66.9: pytest hook — restore env + sys.modules after
    this file's tests run, so the leaked BD_INSTALL_DIR doesn't
    taint downstream tests."""
    if not _ISOLATED_BD_SAVED:
        return
    for k, v in _ISOLATED_BD_SAVED['env'].items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for m in [k for k in list(sys.modules)
              if k.startswith('bulk_downloader')]:
        del sys.modules[m]
    sys.modules.update(_ISOLATED_BD_SAVED['modules'])
    _ISOLATED_BD_SAVED.clear()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ─── Lightweight HTTP server fixture ──────────────────────────────────

class _TestPagesServer:
    """Serves two test pages:
      /static-page — DL button + .player div in initial HTML
      /js-page     — empty initial; JS injects the same content after 500ms

    Used as context manager:
        with _TestPagesServer() as srv:
            assert srv.url(\"/static-page\").startswith(\"http://\")
    """

    STATIC_HTML = """<html><body>
        <div class="player">Static content</div>
        <button id="dl-btn">Download</button>
    </body></html>"""

    JS_HTML = """<html><body>
        <div id="loader">Loading...</div>
        <script>
          setTimeout(() => {
            document.body.innerHTML =
              '<div class="player">JS-rendered content</div>' +
              '<button id="dl-btn">Download</button>';
          }, 500);
        </script>
    </body></html>"""

    def __init__(self):
        self.port = _free_port()
        self._server = None
        self._thread = None

    def __enter__(self):
        from flask import Flask
        from werkzeug.serving import make_server
        app = Flask(__name__)
        app.add_url_rule("/static-page", "static_page",
                         lambda: self.STATIC_HTML)
        app.add_url_rule("/js-page", "js_page",
                         lambda: self.JS_HTML)
        self._server = make_server("127.0.0.1", self.port, app,
                                    threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # Wait for port
        for _ in range(20):
            try:
                s = socket.create_connection(("127.0.0.1", self.port),
                                              timeout=0.2)
                s.close()
                break
            except OSError:
                time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        if self._server:
            self._server.shutdown()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"


# ─── Validation tests ──────────────────────────────────────────────────


class TestSandboxValidation(unittest.TestCase):

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import app as bd_app
        self.client = bd_app.app.test_client()
        # Bootstrap a CSRF token + cookie
        self.client.get("/")
        self.csrf = self.client.get("/api/csrf").get_json()["csrf_token"]

    setUp = setup_method

    def _post(self, body):
        return self.client.post("/api/template/sandbox", json=body,
                                  headers={"X-CSRF-Token": self.csrf})

    def test_missing_url_rejected(self):
        r = self._post({"template": {}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("url required", r.get_json()["error"])

    def test_non_http_scheme_rejected(self):
        r = self._post({"url": "file:///etc/passwd",
                         "template": {}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("http(s)", r.get_json()["error"])

    def test_template_must_be_dict(self):
        r = self._post({"url": "http://x.test",
                         "template": ["not", "a", "dict"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("object", r.get_json()["error"])

    def test_bad_mode_rejected(self):
        r = self._post({"url": "http://x.test", "template": {},
                         "mode": "playwright"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("mode", r.get_json()["error"])

    def test_http_mode_is_default(self):
        """Omitting `mode` should default to http (not browser)."""
        with _TestPagesServer() as srv:
            r = self._post({"url": srv.url("/static-page"),
                             "template": {"dl_selector": "#dl-btn"}})
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["mode"], "http")


# ─── HTTP mode functional tests ────────────────────────────────────────


class TestSandboxHTTPMode(unittest.TestCase):

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import app as bd_app
        self.client = bd_app.app.test_client()
        self.client.get("/")
        self.csrf = self.client.get("/api/csrf").get_json()["csrf_token"]
        self.srv = _TestPagesServer().__enter__()

    setUp = setup_method

    def teardown_method(self, method=None):
        self.srv.__exit__(None, None, None)

    tearDown = teardown_method

    def test_finds_selectors_in_static_page(self):
        r = self.client.post("/api/template/sandbox", json={
            "url": self.srv.url("/static-page"),
            "template": {"dl_selector": "#dl-btn",
                          "trigger_selector": ".player"},
            "mode": "http",
        }, headers={"X-CSRF-Token": self.csrf})
        d = r.get_json()
        self.assertTrue(d["ok"], f"unexpected error: {d.get('error')}")
        self.assertEqual(d["matches"]["dl_selector"]["match_count"], 1)
        self.assertEqual(
            d["matches"]["trigger_selector"]["match_count"], 1)

    def test_misses_js_rendered_content(self):
        """HTTP mode by design doesn't run JS. This test documents
        that and protects against accidental in-process headless
        rendering creeping in."""
        r = self.client.post("/api/template/sandbox", json={
            "url": self.srv.url("/js-page"),
            "template": {"dl_selector": "#dl-btn"},
            "mode": "http",
        }, headers={"X-CSRF-Token": self.csrf})
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["matches"]["dl_selector"]["match_count"], 0,
            "HTTP mode shouldn't see JS-injected content")

    def test_unreachable_url_returns_clean_error(self):
        r = self.client.post("/api/template/sandbox", json={
            "url": "http://127.0.0.1:1/never-listening",
            "template": {}, "mode": "http",
        }, headers={"X-CSRF-Token": self.csrf})
        # Returns 200 with ok=false — UI can render the message
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertFalse(d["ok"])
        self.assertIn("fetch failed", d["error"])

    def test_response_includes_metadata(self):
        r = self.client.post("/api/template/sandbox", json={
            "url": self.srv.url("/static-page"),
            "template": {"dl_selector": "#dl-btn"},
        }, headers={"X-CSRF-Token": self.csrf})
        d = r.get_json()
        self.assertIn("url", d)
        self.assertIn("html_bytes", d)
        self.assertGreater(d["html_bytes"], 0)
        self.assertIn("content_type", d)


# ─── Browser mode functional tests (skipped if Playwright unavailable) ──

try:
    from playwright.sync_api import sync_playwright as _pw
    _PW_AVAILABLE = True
except ImportError:
    _PW_AVAILABLE = False


@unittest.skipUnless(_PW_AVAILABLE,
                     "playwright not installed; browser-mode tests skipped")
class TestSandboxBrowserMode(unittest.TestCase):
    """These tests cost real time (~3-8s each) since they spin up
    Chromium. The test_e2e_smoke.py file does similar; this one is
    narrower — only the sandbox endpoint, not full UI flows."""

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import app as bd_app
        self.client = bd_app.app.test_client()
        self.client.get("/")
        self.csrf = self.client.get("/api/csrf").get_json()["csrf_token"]
        self.srv = _TestPagesServer().__enter__()

    setUp = setup_method

    def teardown_method(self, method=None):
        self.srv.__exit__(None, None, None)

    tearDown = teardown_method

    def test_browser_sees_js_rendered_content(self):
        """The whole reason browser mode exists."""
        r = self.client.post("/api/template/sandbox", json={
            "url": self.srv.url("/js-page"),
            "template": {"dl_selector": "#dl-btn",
                          "trigger_selector": ".player"},
            "mode": "browser",
            "wait_ms": 1500,
        }, headers={"X-CSRF-Token": self.csrf})
        d = r.get_json()
        self.assertTrue(d["ok"], f"unexpected error: {d.get('error')}")
        self.assertEqual(d["mode"], "browser")
        self.assertEqual(d["matches"]["dl_selector"]["match_count"], 1,
            "browser mode should see JS-injected button")
        self.assertEqual(
            d["matches"]["trigger_selector"]["match_count"], 1)

    def test_browser_wait_ms_capped(self):
        """Pathological wait_ms shouldn't hang the server — capped
        at 10s server-side."""
        # Pass an unreasonable value; should still return promptly
        # because the cap clamps it. Use a static page so we don't
        # need to wait the full duration.
        start = time.time()
        r = self.client.post("/api/template/sandbox", json={
            "url": self.srv.url("/static-page"),
            "template": {"dl_selector": "#dl-btn"},
            "mode": "browser",
            "wait_ms": 999_999_999,  # nonsense
        }, headers={"X-CSRF-Token": self.csrf})
        elapsed = time.time() - start
        # 10s cap + ~4s for browser startup = ~14s ceiling
        self.assertLess(elapsed, 20,
            f"browser mode took {elapsed:.1f}s — wait_ms cap not honored?")
        self.assertTrue(r.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
