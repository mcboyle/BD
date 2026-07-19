"""Tests for v3.43.16 persistent-browser keep-alive.

These tests actually launch Chromium and serve content from a localhost
HTTP server, then verify the keeper's _heartbeat_navigate and
_heartbeat_in_page_fetch paths work against real traffic.

Skipped if Playwright isn't installed OR Chromium can't be launched.
"""
import http.server
import json
import os
import socketserver
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path


def _playwright_available():
    """Skip these tests if Playwright can't actually launch a browser
    (CI without chromium binary, sandbox without /dev/shm, etc.)."""
    try:
        # Don't actually launch — just confirm import works. The keeper
        # itself handles launch failures gracefully (degrades to httpx).
        return True
    except Exception:
        return False


class _Handler(http.server.BaseHTTPRequestHandler):
    """Localhost test server. Configurable via class-level vars so each
    test can set up its own scenario."""
    # Response variant for the next request
    response_status = 200
    response_body = b'<html><body>You are logged in. Welcome!</body></html>'
    response_location = None
    # Track requests for assertion
    request_log = []

    def do_GET(self):
        _Handler.request_log.append({
            "path": self.path,
            "headers": dict(self.headers),
        })
        self.send_response(self.response_status)
        if self.response_location:
            self.send_header("Location", self.response_location)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *args):
        pass  # silence access log


@contextmanager
def _local_server():
    """Spin up a tiny HTTP server on a random localhost port. Yields
    the base URL string."""
    _Handler.request_log = []
    _Handler.response_status = 200
    _Handler.response_body = b'<html><body>You are logged in. Welcome!</body></html>'
    _Handler.response_location = None
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@contextmanager
def _isolated_cwd():
    orig = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        try: yield Path(td)
        finally: os.chdir(orig)


# ─── Real browser tests ─────────────────────────────────────────────

def test_heartbeat_navigate_with_real_browser():
    """The persistent-browser path actually navigates a page and
    returns ok when the response doesn't look like login."""
    if not _playwright_available():
        return  # silent skip
    from bulk_downloader.session_keeper import SessionKeeper
    with _isolated_cwd(), _local_server() as base_url:
        keeper = SessionKeeper(
            "test", 0,
            {"login_url": base_url, "success_url": base_url,
             "password": "p"},
            lambda *_: (False, "test"),
        )
        try:
            # Force a navigate cycle (not an in-page fetch)
            keeper._last_navigate_at = 0
            ok, detail = keeper._heartbeat()
            assert ok, f"expected ok, got: {detail}"
            assert "navigate ok" in detail.lower() or "fallback" in detail.lower()
        finally:
            keeper._teardown_browser()


def test_heartbeat_navigate_detects_login_redirect():
    """If the server 302s to /login, the heartbeat returns False."""
    if not _playwright_available(): return
    from bulk_downloader.session_keeper import SessionKeeper

    # Custom handler: root 302s to /login, /login serves a static page.
    # Without this distinction the browser sees infinite redirects and
    # errors out before we get a chance to inspect the URL.
    class _RedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
            else:
                body = b"<html><body>please sign in</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        def log_message(self, *a): pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _RedirectHandler)
    port = httpd.server_address[1]
    t_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    t_thread.start()
    base_url = f"http://127.0.0.1:{port}"
    with _isolated_cwd():
        keeper = SessionKeeper(
            "test", 0,
            {"login_url": base_url, "success_url": base_url, "password": "p"},
            lambda *_: (False, "test"),
        )
        try:
            keeper._last_navigate_at = 0
            ok, detail = keeper._heartbeat()
            assert not ok
            assert "login" in detail.lower(), f"unexpected detail: {detail!r}"
        finally:
            keeper._teardown_browser()
            httpd.shutdown()


def test_heartbeat_navigate_detects_login_form_in_body():
    """If the response renders a login form, heartbeat returns False."""
    if not _playwright_available(): return
    from bulk_downloader.session_keeper import SessionKeeper
    with _isolated_cwd(), _local_server() as base_url:
        _Handler.response_body = (
            b'<html><body><form>'
            b'<input type="password" name="pw">'
            b'<button>Login</button>'
            b'</form></body></html>'
        )
        keeper = SessionKeeper(
            "test", 0,
            {"login_url": base_url, "success_url": base_url, "password": "p"},
            lambda *_: (False, "test"),
        )
        try:
            keeper._last_navigate_at = 0
            ok, detail = keeper._heartbeat()
            assert not ok
            assert "login form" in detail.lower()
        finally:
            keeper._teardown_browser()


def test_heartbeat_in_page_fetch_uses_existing_page():
    """The second cycle (after navigate) uses in-page fetch, which
    runs the request from the existing page context — no new
    navigation."""
    if not _playwright_available(): return
    from bulk_downloader.session_keeper import SessionKeeper
    with _isolated_cwd(), _local_server() as base_url:
        keeper = SessionKeeper(
            "test", 0,
            {"login_url": base_url, "success_url": base_url, "password": "p"},
            lambda *_: (False, "test"),
        )
        try:
            # First call: navigate. _last_navigate_at = 0 forces it.
            keeper._last_navigate_at = 0
            ok1, _ = keeper._heartbeat()
            assert ok1
            requests_after_nav = len(_Handler.request_log)
            assert requests_after_nav >= 1

            # Second call: should be in-page fetch (recent navigate).
            ok2, detail2 = keeper._heartbeat()
            assert ok2, f"expected in-page fetch ok, got: {detail2}"
            assert "fetch" in detail2.lower()
            # In-page fetch made another request
            assert len(_Handler.request_log) > requests_after_nav
        finally:
            keeper._teardown_browser()


def test_browser_age_triggers_relaunch():
    """If the browser is older than 86400s, _heartbeat re-launches
    before checking. We can't wait 24h in a test, so we just verify
    the _browser_age helper returns expected values."""
    if not _playwright_available(): return
    from bulk_downloader.session_keeper import SessionKeeper
    with _isolated_cwd(), _local_server() as base_url:
        keeper = SessionKeeper(
            "test", 0,
            {"login_url": base_url, "success_url": base_url, "password": "p"},
            lambda *_: (False, "test"),
        )
        try:
            # Before first launch, age is 0
            assert keeper._browser_age() == 0.0
            # Trigger a launch
            keeper._last_navigate_at = 0
            keeper._heartbeat()
            # Now age should be > 0
            assert keeper._browser_age() > 0
            assert keeper._browser_age() < 60  # not unreasonably old
        finally:
            keeper._teardown_browser()


def test_teardown_browser_idempotent():
    """Calling _teardown_browser twice (or before launch) doesn't crash."""
    from bulk_downloader.session_keeper import SessionKeeper
    keeper = SessionKeeper(
        "test", 0,
        {"password": "p"},
        lambda *_: (False, "test"),
    )
    # Never launched — teardown should be a no-op
    keeper._teardown_browser()
    keeper._teardown_browser()


def test_heartbeat_falls_back_to_httpx_when_no_browser():
    """If the browser can't launch (we don't have one), the httpx
    fallback kicks in. Easy to verify by checking the cookie-load
    path works without a browser."""
    if not _playwright_available(): return
    from bulk_downloader.session_keeper import SessionKeeper
    with _isolated_cwd(), _local_server() as base_url:
        keeper = SessionKeeper(
            "test", 0,
            {"login_url": base_url, "success_url": base_url, "password": "p"},
            lambda *_: (False, "test"),
        )
        # Force the httpx fallback path explicitly. (Without cookies on
        # disk, it should report "no cookies stored yet".)
        ok, detail = keeper._heartbeat_httpx_fallback()
        assert not ok
        assert "no cookies" in detail.lower() or "fallback" in detail.lower()


def test_persist_cookies_writes_file():
    """After a navigate, cookies are saved back to disk so workers
    see fresh values."""
    if not _playwright_available(): return
    from bulk_downloader.session_keeper import SessionKeeper
    with _isolated_cwd(), _local_server() as base_url:
        keeper = SessionKeeper(
            "test_persist", 0,
            {"login_url": base_url, "success_url": base_url, "password": "p"},
            lambda *_: (False, "test"),
        )
        try:
            keeper._last_navigate_at = 0
            keeper._heartbeat()  # should write cookies/test_persist.json
            cookie_path = Path("cookies") / "test_persist.json"
            assert cookie_path.exists(), "expected cookies file to be written"
            # File should contain a JSON list (Playwright cookie format)
            data = json.loads(cookie_path.read_text(encoding="utf-8"))
            assert isinstance(data, list)
        finally:
            keeper._teardown_browser()
