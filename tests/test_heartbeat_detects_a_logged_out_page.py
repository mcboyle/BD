"""The keeper's logout detector misses every sign-in page that avoids one word.

WHY THIS IS THE FIRST CUT AND NOT THE COOKIE WRITER. Audit finding #8 proposed
guarding `_persist_cookies` so it refuses to overwrite a non-empty jar with an
empty capture. Measured against the operator's real jars, that fix is silent on
the majority case:

    total-clobber   (every cookie session-scoped, jar -> [])   5 of 11 sites
    partial-clobber (persistent survive, AUTH cookie lost)     6 of 11 sites

Six of eleven leave a NON-EMPTY jar with the auth cookie gone, so `if not
cookies: return` never fires. The writer is only dangerous because the heartbeat
cannot tell logged-in from logged-out: `_heartbeat_navigate` returns False at
session_keeper.py:713/715/723 and never reaches the `_persist_cookies()` call at
:728. A detector that correctly says "logged out" costs the writer nothing,
because the write is already unreachable on that path.

THE TWO DEFECTS.

1. THE URL TEST, session_keeper.py:713:

       if "login" in final_url.lower() or "signin" in final_url.lower():

   "sign_in" does not contain "signin" -- the underscore breaks the match. That
   is Devise's `/users/sign_in`, the single most common sign-in route in Rails
   applications. `/auth/...` and `/session/new` are missed for the same reason.

2. THE DOM TEST, session_keeper.py:719-721:

       !!document.querySelector('input[type=password]')
       && document.body.textContent.toLowerCase().includes('login')

   The `&&` requires the literal string "login" in the body text. A page whose
   button reads "Sign in" has the password field and not the word, so it reads
   as a healthy session. tests/test_keepalive_browser.py:154 passes today only
   because its fixture renders `<button>Login</button>`.

THE FIX MUST NOT BE "A PASSWORD FIELD MEANS LOGGED OUT". A logged-in settings
page with a change-password form has a password input and is not a logout. That
is the "looks correct, makes things worse" shape CLAUDE.md warns about, and
`test_a_change_password_form_is_not_a_logout` below is the canary for it: it is
GREEN on pristine source and must stay green. A fix that drops the conjunction
entirely passes every other test in this file and starts tearing down live
sessions into `_auto_relogin`.

NOTE ON SKIPPING. tests/test_keepalive_browser.py uses `if not
_playwright_available(): return`, which reports PASS when the test did not run.
This file uses pytest.skip instead -- a check that could not execute must say so
rather than certify.
"""
from __future__ import annotations

import contextlib
import http.server
import os
import socketserver
import sys
import tempfile
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _require_playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip("playwright not installed -- this check cannot run here, "
                    "which is not the same as passing")


@contextlib.contextmanager
def _isolated_cwd():
    prev = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            yield d
        finally:
            os.chdir(prev)


@contextlib.contextmanager
def _serving(handler_cls):
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler_cls)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


def _body_server(html: bytes):
    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *a):
            pass
    return _H


def _redirect_server(target: str):
    """Root 302s to `target`; `target` serves a neutral page.

    The landing page carries NO password field and NO sign-in wording, so the
    only thing that can detect the logout is the URL test. That isolates
    defect 1 from defect 2 -- otherwise a DOM-test fix would make these pass
    and the URL bug would survive.
    """
    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()
                return
            body = b"<html><body><p>welcome</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    return _H


def _heartbeat_against(server_cls):
    from bulk_downloader.session_keeper import SessionKeeper
    with _isolated_cwd(), _serving(server_cls) as base_url:
        keeper = SessionKeeper(
            "test", 0,
            {"login_url": base_url, "success_url": base_url, "password": "p"},
            lambda *_: (False, "test"),
        )
        try:
            keeper._last_navigate_at = 0
            return keeper._heartbeat()
        finally:
            keeper._teardown_browser()


# ── defect 1: the URL test ───────────────────────────────────────────────────

@pytest.mark.parametrize("target", [
    "/users/sign_in",     # Devise. "sign_in" does not contain "signin".
    "/auth/session",
    "/session/new",
])
def test_a_redirect_to_a_sign_in_route_is_a_logout(target):
    _require_playwright()
    ok, detail = _heartbeat_against(_redirect_server(target))
    assert not ok, (
        f"the server redirected to {target} and the heartbeat reported the "
        f"session HEALTHY ({detail!r}). session_keeper.py:713 tests only for "
        f"'login' and 'signin' as substrings, so an underscore or an /auth "
        f"prefix defeats it -- and the keeper then persists the depleted "
        f"cookie jar over the good one."
    )


# ── defect 2: the DOM test ───────────────────────────────────────────────────

def test_a_sign_in_form_without_the_word_login_is_a_logout():
    _require_playwright()
    ok, detail = _heartbeat_against(_body_server(
        b'<html><body><form>'
        b'<input type="password" name="pw">'
        b'<button>Sign in</button>'
        b'</form></body></html>'
    ))
    assert not ok, (
        f"a page with a password field and a 'Sign in' button reported the "
        f"session HEALTHY ({detail!r}). The predicate at "
        f"session_keeper.py:719-721 requires the literal string 'login' in the "
        f"body text, so any site that says 'Sign in' is undetectable."
    )


# ── the canary: this must be GREEN now and STAY green ────────────────────────

def test_a_change_password_form_is_not_a_logout():
    """A password input alone does not mean logged out.

    Green on pristine source. It exists to fail the obvious over-correction --
    dropping the conjunction so any password field counts -- which would pass
    every other test in this file and start tearing down live sessions into
    _auto_relogin. Two fixes in this repository have already had that shape.
    """
    _require_playwright()
    ok, detail = _heartbeat_against(_body_server(
        b'<html><body><h1>Account settings</h1>'
        b'<p>Signed in as tester.</p><form>'
        b'<input type="password" name="current">'
        b'<input type="password" name="new">'
        b'<button>Change password</button>'
        b'</form></body></html>'
    ))
    assert ok, (
        f"a logged-in settings page carrying a change-password form was "
        f"reported as a LOGOUT ({detail!r}). The detector must not treat a "
        f"password input on its own as evidence -- that tears down a live "
        f"session and forces a needless re-login."
    )


def test_a_word_that_merely_contains_sign_in_is_not_a_logout():
    """The word boundaries are load-bearing, and nothing else forces them.

    Found by mutation, not by reading: dropping \\b from the pattern left every
    other test in this file green. A plain substring test matches "sign in"
    inside "de|sign in|tent", so a page discussing design that also carries a
    password field would be declared a logout and torn down.

    "Signed in as tester" is here for the same reason from the other direction --
    it must NOT match, because the "ed" breaks the boundary.
    """
    _require_playwright()
    ok, detail = _heartbeat_against(_body_server(
        b'<html><body><h1>Design intent review</h1>'
        b'<p>Signed in as tester.</p><form>'
        b'<input type="password" name="current">'
        b'<button>Change password</button>'
        b'</form></body></html>'
    ))
    assert ok, (
        f"a page containing 'Design intent' and a password field was reported "
        f"as a LOGOUT ({detail!r}). The pattern must anchor on word boundaries "
        f"-- a substring test matches 'sign in' inside 'design intent'."
    )


# ── the healthy case, so a detector that fails everything is caught ──────────

def test_an_ordinary_page_is_not_a_logout():
    _require_playwright()
    ok, detail = _heartbeat_against(_body_server(
        b'<html><body><h1>Latest</h1><p>Signed in as tester.</p></body></html>'
    ))
    assert ok, (
        f"an ordinary authenticated page was reported as a logout ({detail!r}). "
        f"A detector that says 'logged out' unconditionally passes every "
        f"positive test in this file."
    )
