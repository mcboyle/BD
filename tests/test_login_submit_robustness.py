"""Login submit-path robustness — regression guards.

Pins two fixes from OPEN_THREADS:

  1. The multi-method submit loop kept firing methods (requestSubmit,
     form.submit, …) after an earlier method already submitted and the
     page closed/navigated — noisy "Target page has been closed"
     errors and a redundant second submit. _submit_login now stops the
     loop the moment the page is closed or has navigated.

  2. "page closed mid-submit + >=1 cookie" was treated as a successful
     login. A single stray cookie (analytics, a CSRF token, a consent
     flag) is not a session — that over-accepts a failed login.
     _looks_authenticated() now requires a real signal.
"""
from bulk_downloader.login import _submit_login, _looks_authenticated


# ── 1. submit loop stops once an earlier method had its effect ──────

class _ClosedAfterEntryPage:
    """url is readable (so _submit_login gets past initial_url) but the
    page reports itself closed — the loop guard must catch that before
    running method 1."""
    url = "https://site.example/login"
    def is_closed(self):
        return True


class _NavigatedPage:
    """url returns the login URL once (the initial read), then a
    different URL — i.e. an earlier action already navigated us."""
    def __init__(self):
        self._reads = 0
    @property
    def url(self):
        self._reads += 1
        return ("https://site.example/login" if self._reads == 1
                else "https://site.example/members")
    def is_closed(self):
        return False


def test_submit_loop_stops_when_page_already_closed():
    """A closed page must short-circuit to PAGE_CLOSED, not run the
    eight submit methods against a dead page."""
    res, info = _submit_login(_ClosedAfterEntryPage(), [], [])
    assert res == "PAGE_CLOSED", f"expected PAGE_CLOSED, got {res!r}"
    assert "closed before" in info


def test_submit_loop_stops_when_already_navigated():
    """If the page already navigated away from the login URL, an
    earlier method submitted — report success, do not keep trying."""
    res, info = _submit_login(_NavigatedPage(), [], [])
    assert res is True, f"expected True, got {res!r}"
    assert "navigated before" in info


# ── 2. cookie jar must actually look like a session ─────────────────

def test_single_stray_cookie_is_not_a_session():
    """The original bug: one non-empty cookie read as 'logged in'."""
    ok, _ = _looks_authenticated(
        [{"name": "_ga", "value": "GA1.2.1122334455.6677"}])
    assert ok is False


def test_csrf_cookie_alone_is_not_a_session():
    """A CSRF token is set BEFORE login — must not count as auth."""
    ok, _ = _looks_authenticated(
        [{"name": "csrf_token", "value": "9f8e7d6c5b4a3928171615"}])
    assert ok is False


def test_empty_jar_is_not_a_session():
    ok, _ = _looks_authenticated([])
    assert ok is False
    ok2, _ = _looks_authenticated([{"name": "x", "value": ""}])
    assert ok2 is False


def test_session_named_cookie_counts_as_auth():
    """A session/auth-named cookie is a strong signal, even short."""
    for name in ("PHPSESSID", "sessionid", "auth_token", "logged_in"):
        ok, why = _looks_authenticated([{"name": name, "value": "1"}])
        assert ok is True, f"{name!r} should read as authenticated ({why})"


def test_several_substantial_cookies_count_as_auth():
    """A real post-login jar has several substantial cookies even when
    none has an obviously auth-y name."""
    jar = [{"name": "a", "value": "x" * 16},
           {"name": "b", "value": "y" * 16},
           {"name": "c", "value": "z" * 16},
           {"name": "d", "value": "w" * 16}]
    ok, _ = _looks_authenticated(jar)
    assert ok is True
    # one fewer is not enough
    ok2, _ = _looks_authenticated(jar[:3])
    assert ok2 is False
