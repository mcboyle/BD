"""Row 722 -- a post-submit page that still shows the ANONYMOUS surface is
not a login, whatever the cookie jar or the navigation said.

Live (nookies.com, 2026-09-15 04:50Z and 04:57Z): the product reported
login_status "OK -- 5 cookies (submit: Tab+Enter)" twice; the screenshot of
the members page afterwards showed logged-OUT chrome ("LOG IN / JOIN NOW",
"You have 3 trailer views left"). The jar held a session cookie -- which an
anonymous visitor gets too. The verdict came from do_login's navigated
return: same-origin navigation + readable jar == OK, with nothing read
from the page.

Fix: before that return, read what the page SHOWS (replay.anonymous_
surface_check). A visible password field, or a LOG IN / SIGN IN / JOIN NOW
affordance with no logout / account / member affordance, refuses the
verdict with a distinctive diagnostic and keeps the page as evidence. A
declared success_url that matches, or a present member indicator, still
wins (row 708's positive checks are unchanged).

The browser boundary replaced here is `replay._read_login_surface` (the
page.evaluate that reads the rendered surface); the judgement and the
wiring into do_login are real. Fixture pages only; no live site.
"""
from pathlib import Path

import pytest

from tests.test_row708_no_nav_login_is_not_success import _drive

BD_GATE_SCOPE = "module"

LOGIN = "https://login.example.invalid/login"
LANDED = "https://login.example.invalid/"  # same origin, URL changed: a navigation
ANON_TEXT = "HOME  TOUR  LOG IN  JOIN NOW\nYou have 3 trailer views left"
MEMBER_TEXT = "HOME  MY VIDEOS  Log out\nWelcome back"


def _surface(monkeypatch, *, password_visible=False, text=""):
    from bulk_downloader.login_impl import replay
    reads = []

    def read(page):
        reads.append(page)
        return {"password_visible": password_visible, "text": text}

    monkeypatch.setattr(replay, "_read_login_surface", read)
    return reads


def _navigated(monkeypatch, tmp_path, **kw):
    kw.setdefault("success_url", None)
    kw.setdefault("final_url", LANDED)
    return _drive(monkeypatch, tmp_path, branch="ajax", submit_result=True, **kw)


# ── THE ROW ──

def test_visible_password_field_after_navigation_is_not_ok(monkeypatch, tmp_path):
    """RED on base: same-origin navigation, four new cookies, no success_url
    declared -> base returns True. The page still renders the login form."""
    reads = _surface(monkeypatch, password_visible=True, text=ANON_TEXT)
    result, calls = _navigated(monkeypatch, tmp_path)
    assert result[0] is not True, (
        "a post-submit page with a VISIBLE password field was reported as a "
        f"login: {result[1]!r}")
    assert result[0] is False, repr(result[0])
    assert "post-submit page still anonymous (login form visible)" in result[1], result[1]
    assert "NOT success" in result[1], result[1]
    assert result[2] == [], "no cookie jar is handed out for a refused login"
    assert len(reads) == 1, "the surface is read from the page do_login drove"
    assert calls["close"] == 1, calls


def test_login_affordance_without_member_affordance_is_not_ok(monkeypatch, tmp_path):
    """The nookies shape: no password field on the landing page, but the
    chrome says LOG IN / JOIN NOW and nothing says log out."""
    _surface(monkeypatch, password_visible=False, text=ANON_TEXT)
    result, _ = _navigated(monkeypatch, tmp_path)
    assert result[0] is False, repr(result)
    assert "post-submit page still anonymous ('log in' affordance" in result[1], result[1]


# ── evidence ──

def test_refused_page_is_kept_as_evidence(monkeypatch, tmp_path):
    _surface(monkeypatch, password_visible=True, text=ANON_TEXT)
    result, calls = _navigated(monkeypatch, tmp_path)
    assert result[0] is False, repr(result)
    assert calls["content"] == 1, calls
    files = list((tmp_path / "evidence").glob("login-post-submit-anonymous-*.html"))
    assert len(files) == 1, [str(f) for f in (tmp_path / "evidence").glob("*")]
    assert str(files[0]) in result[1], result[1]
    body = files[0].read_text(encoding="utf-8")
    assert f"final_url: {LANDED}" in body, body[:200]
    assert "fixture members page" in body, body[:200]


# ── negative controls ──

def test_member_affordance_without_password_field_is_still_ok(monkeypatch, tmp_path):
    """Same navigation, same cookie delta, page shows "Log out": exactly the
    verdict base gave."""
    _surface(monkeypatch, password_visible=False, text=MEMBER_TEXT)
    result, calls = _navigated(monkeypatch, tmp_path)
    assert result[0] is True, result[1]
    assert result[1] == "OK — 8 cookies (submit: fixture submit)", result[1]
    assert len(result[2]) == 8
    assert calls["content"] == 0, "no evidence pass on an accepted navigation"


def test_join_now_upsell_beside_log_out_is_a_member_page(monkeypatch, tmp_path):
    """A member page may carry a JOIN NOW / LOGIN link in its footer; the
    member affordance decides."""
    _surface(monkeypatch, text="Log out  |  Upgrade  |  JOIN NOW for more")
    result, _ = _navigated(monkeypatch, tmp_path)
    assert result[0] is True, result[1]


def test_unreadable_surface_does_not_change_the_verdict(monkeypatch, tmp_path):
    """UNKNOWN asserts nothing in either direction: the row-708 fixture page
    has no evaluate at all, and its navigated verdict stays OK."""
    result, _ = _navigated(monkeypatch, tmp_path)
    assert result[0] is True, result[1]


def test_declared_success_url_match_wins_over_the_anonymous_surface(monkeypatch, tmp_path):
    """Row 708's positive check is not overruled by the heuristic."""
    _surface(monkeypatch, password_visible=True, text=ANON_TEXT)
    result, _ = _navigated(monkeypatch, tmp_path, success_url="/members",
                           final_url="https://login.example.invalid/members/home")
    assert result[0] is True, result[1]


def test_present_member_indicator_wins_over_the_anonymous_surface(monkeypatch, tmp_path):
    _surface(monkeypatch, password_visible=True, text=ANON_TEXT)
    result, calls = _navigated(monkeypatch, tmp_path, member_indicator="a.logout",
                               indicator_present=True)
    assert result[0] is True, result[1]
    assert calls["locator"] == ["a.logout"], calls


def test_refused_anonymous_page_hands_off_when_manual_takeover_is_allowed(
        monkeypatch, tmp_path):
    """A refusal on the navigated path takes the same road as the other
    navigated refusals (expected-URL miss, cross-origin): manual takeover
    when the caller allows it, never a silent OK."""
    _surface(monkeypatch, password_visible=True, text=ANON_TEXT)
    result, calls = _navigated(monkeypatch, tmp_path, allow_manual=True)
    assert result[0] is not True, repr(result)
    assert result[0] == "MANUAL_PENDING", repr(result[0])
    assert "post-submit page still anonymous" in result[1], result[1]


# ── the browser boundary itself ──

class _EvalPage:
    def __init__(self, ret=None, exc=None):
        self.ret, self.exc, self.js = ret, exc, []

    def evaluate(self, js):
        self.js.append(js)
        if self.exc:
            raise self.exc
        return self.ret


def test_read_login_surface_evaluates_the_rendered_page():
    from bulk_downloader.login_impl import replay
    page = _EvalPage(ret={"password_visible": True, "text": "LOG IN"})
    assert replay._read_login_surface(page) == {"password_visible": True, "text": "LOG IN"}
    assert len(page.js) == 1 and "input[type=password]" in page.js[0], page.js
    assert "innerText" in page.js[0], "the VISIBLE text is read, not the HTML"
    assert "getClientRects" in page.js[0], "a hidden password field must not count"


def test_read_login_surface_unavailable_is_unknown_not_anonymous():
    from bulk_downloader.login_impl import replay
    assert replay._read_login_surface(_EvalPage(exc=RuntimeError("page closed"))) is None
    assert replay._read_login_surface(_EvalPage(ret="garbage")) is None
    anonymous, why = replay._judge_login_surface(None)
    assert anonymous is False and "UNKNOWN" in why, (anonymous, why)


@pytest.mark.parametrize("text,expected", [
    ("Sign In  Join Now", True),
    ("LOGIN", True),
    ("Log in\nMy Account", False),
    ("members area  log in", False),
    ("Welcome", False),
])
def test_judge_login_surface_text_rules(text, expected):
    from bulk_downloader.login_impl import replay
    anonymous, why = replay._judge_login_surface({"password_visible": False, "text": text})
    assert anonymous is expected, (text, anonymous, why)


def test_transform_control_only_imports_the_seam():
    from bulk_downloader.login_impl import replay, submit
    assert callable(replay.anonymous_surface_check)
    assert callable(submit.do_login)
