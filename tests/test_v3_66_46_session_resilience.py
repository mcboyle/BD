"""Mid-process session-loss detection: broadened auth detection so the
existing relogin+re-queue+re-press machinery fires for in-place login
walls and non-standard login paths. Detect-and-recover; no evasion."""
import re
from bulk_downloader.constants import AUTH_HINTS, AUTH_BODY_RE


def test_widened_auth_paths():
    for p in ("/account/login", "/users/sign_in", "/log-in", "/session/new",
              "/members/login", "/sign-in"):
        assert p in AUTH_HINTS, p


def test_legacy_auth_paths_retained():
    for p in ("/login", "/signin", "/auth"):
        assert p in AUTH_HINTS


def test_body_re_catches_password_wall():
    assert AUTH_BODY_RE.search('<input type="password" name="pw">')


def test_body_re_catches_session_expired():
    assert AUTH_BODY_RE.search("Your session has expired, please sign in")


def test_body_re_catches_login_to_continue():
    assert AUTH_BODY_RE.search("Please log in to download this video")


def test_body_re_ignores_logged_in_nav():
    # The word "login"/"log out" in nav must NOT trigger on a working page.
    assert not AUTH_BODY_RE.search(
        '<nav><a href="/logout">Log out</a><span>Welcome back, user</span></nav>')


def test_body_re_ignores_plain_mention():
    assert not AUTH_BODY_RE.search("<p>You can login from the menu.</p>")
