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
import time
from types import SimpleNamespace

import pytest

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


def test_an_unchanged_generic_session_cookie_is_not_login_success():
    before_submit = [
        {"name": "PHPSESSID", "value": "generic-pre-login-session"},
        {
            "name": "members_area_session",
            "value": "expired-members-session",
            "expirationDate": 1,
        },
    ]
    after_submit = [dict(cookie) for cookie in before_submit]
    assert len(after_submit) == 2
    assert after_submit == before_submit, (
        "precondition: the failed submit must not rewrite the cookie jar")
    assert after_submit[1]["expirationDate"] < time.time(), (
        "precondition: the members-area cookie must remain expired")

    ok, why = _looks_authenticated(after_submit)

    assert ok is False, (
        "an unchanged generic PHP session cookie was treated as login success: "
        f"{why}")


def test_explicit_auth_named_cookie_counts_as_auth():
    """An explicitly auth-named cookie is a strong signal, even short."""
    for name in ("auth_token", "logged_in", "remember_token", "account_id"):
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


def test_generic_cookie_transform_control_only_imports_classifier():
    assert callable(_looks_authenticated)


def _delta_jar(names):
    # Documented zero-entropy cookie fixtures, never live session values.
    return [{"name": name, "value": "0" * 16} for name in names]


def _run_delta_login(monkeypatch, tmp_path, before, after, branch, *, unreadable=False):
    """Drive real do_login/classification; replace only browser/UI boundaries."""
    from bulk_downloader import cloak, interstitial, learn, stealth
    from bulk_downloader.login_impl import submit

    calls = {"submit": 0, "fill": [], "reads": [], "close": 0}
    jar = [dict(cookie) for cookie in before]

    class Page:
        @property
        def url(self):
            if calls["submit"] and branch == "post_closed":
                raise RuntimeError("fixture page closed after submit")
            return "https://login.example.invalid/login"

        def goto(self, url, **kwargs):
            assert url == self.url

    page = Page()

    def cookies():
        phase = "after" if calls["submit"] else "before"
        calls["reads"].append(phase)
        if phase == "before" and unreadable:
            raise RuntimeError("fixture cookie snapshot unavailable")
        return jar

    def close():
        calls["close"] += 1

    ctx = SimpleNamespace(new_page=lambda: page, cookies=cookies)
    browser = SimpleNamespace(new_context=lambda **kw: ctx, close=close)
    monkeypatch.setattr(cloak, "launch_browser", lambda **kw: (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(learn, "install_recorder", lambda page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *a: None)
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *a, **kw: [])
    monkeypatch.setattr(submit.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(submit, "replay_saved_login_flow", lambda *a: {"ran": False})
    monkeypatch.setattr(submit, "_fire_login_trigger_if_needed", lambda *a: (False, False, ""))
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *a, **kw: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda page: None)

    def fill(page, selectors, value, role):
        calls["fill"].append(role)
        return True, "fixture field"

    def submit_form(page, selectors, password_selectors):
        calls["submit"] += 1
        assert jar == before and len(before) > 0
        # In-place update proves the before snapshot survives jar mutation.
        jar[:] = [dict(cookie) for cookie in after]
        return {"ajax": False, "closed": "PAGE_CLOSED", "post_closed": True}[branch], "fixture submit"

    monkeypatch.setattr(submit, "_try_fill", fill)
    monkeypatch.setattr(submit, "_submit_login", submit_form)
    # Documented zero-entropy password; no vault reference or real credential.
    config = {"login_url": page.url, "username": "fixture", "password": "zero-entropy-password",
              "success_url": "/members", "wait": 0,
              # v3.66 row 708: evidence goes to the test tmp dir, never the repo tree.
              "login_evidence_dir": str(tmp_path / "login_evidence"), "use_real_chrome": False,
              "use_stealth": False, "use_stealth_library": False}
    result = submit.do_login(config)
    assert calls["submit"] == 1
    assert calls["fill"] == ["username", "password"]
    assert calls["close"] == 1
    assert jar == after
    return result, calls


@pytest.mark.parametrize("branch", ["ajax", "closed", "post_closed"])
def test_unchanged_four_prelogin_cookies_never_mean_submit_succeeded(monkeypatch, tmp_path, branch):
    before = _delta_jar(["pref_a", "pref_b", "pref_c", "pref_d"])
    after = [dict(cookie) for cookie in before]
    assert len(before) == 4 and before == after
    assert all(len(cookie["value"]) > 8 for cookie in before)
    result, calls = _run_delta_login(monkeypatch, tmp_path, before, after, branch)
    assert result[0] is False, "unchanged four-cookie jar falsely accepted login"
    assert "Submit failed" in result[1] or "Page closed after submit" in result[1]
    assert calls["reads"] == ["before", "after"]


@pytest.mark.parametrize("branch", ["ajax", "closed", "post_closed"])
def test_wowgirls_ajax_new_cookie_delta_is_settled_no_nav(monkeypatch, tmp_path, branch):
    """ROW 708: a four-name AJAX cookie delta settles, it does not succeed.

    v3.66.1497's classification is UNCHANGED -- the delta still QUALIFIES the
    jar, and this test still asserts that reason verbatim. What row 708 changed
    is the VERDICT: a qualified jar is not a navigation, so do_login returns the
    distinct settled-no-nav state instead of True. Before row 708 this test was
    named test_wowgirls_ajax_new_cookie_delta_still_succeeds and asserted
    `result[0] is True`. See tests/test_row708_no_nav_login_is_not_success.py.
    """
    before = _delta_jar(["pref_a", "pref_b", "pref_c", "pref_d"])
    added = _delta_jar(["member_a", "member_b", "member_c", "member_d"])
    assert len(added) == 4
    assert {c["name"] for c in before}.isdisjoint(c["name"] for c in added)
    result, calls = _run_delta_login(monkeypatch, tmp_path, before, before + added, branch)
    assert result[0].status == "settled-no-nav", (
        "row 708: a four-name AJAX cookie delta with NO NAV SIGNAL must settle "
        f"as settled-no-nav, never succeed -- got {result[0]!r} / {result[1]}")
    assert result[0].why == "4 new substantial cookies", (
        "row 708 moved the VERDICT only; v3.66.1497's cookie classification "
        f"reason must be carried through verbatim -- got {result[0].why!r}")
    assert len(result[2]) == 8
    assert calls["reads"] == ["before", "after"]


@pytest.mark.parametrize("branch", ["ajax", "closed", "post_closed"])
def test_explicit_auth_cookie_qualifies_the_jar_but_is_not_a_navigation(
        monkeypatch, tmp_path, branch):
    """ROW 708: an auth-named cookie qualifies the jar but is not a navigation.

    An auth-named cookie is still a COOKIE. It qualifies the jar exactly as
    before and the reason is carried through verbatim, but on a no-nav path the
    jar no longer decides the login. Before row 708 this test was named
    test_explicit_auth_cookie_still_succeeds_without_delta and asserted
    `result[0] is True`.
    """
    before = [{"name": "logged_in", "value": "1"}]
    result, calls = _run_delta_login(monkeypatch, tmp_path, before, before, branch)
    assert result[0].status == "settled-no-nav", (
        "row 708: an auth-named cookie with NO NAV SIGNAL must settle as "
        f"settled-no-nav, never succeed -- got {result[0]!r} / {result[1]}")
    assert "auth-looking cookie" in result[1], (
        "row 708 moved the VERDICT only; the auth-looking-cookie reason must "
        f"still be carried through verbatim -- got {result[1]}")
    assert calls["reads"] == ["before", "after"]


@pytest.mark.parametrize("branch", ["ajax", "closed", "post_closed"])
def test_unavailable_cookie_snapshot_cannot_prove_a_delta(monkeypatch, tmp_path, branch):
    before = _delta_jar(["pref_a"])
    after = before + _delta_jar(["member_a", "member_b", "member_c", "member_d"])
    result, calls = _run_delta_login(monkeypatch, tmp_path, before, after, branch, unreadable=True)
    assert result[0] is False, "unavailable before jar was treated as an empty jar"
    assert calls["reads"] == ["before", "after"]


@pytest.mark.parametrize("shape", ["three_new", "values_changed", "duplicate_names"])
def test_cookie_delta_requires_four_new_names(monkeypatch, tmp_path, shape):
    before = _delta_jar(["pref_a", "pref_b", "pref_c", "pref_d"])
    if shape == "three_new":
        after = before + _delta_jar(["member_a", "member_b", "member_c"])
    elif shape == "values_changed":
        # Documented zero-entropy replacement values; names remain identical.
        after = [{**cookie, "value": "1" * 16} for cookie in before]
    else:
        after = before + _delta_jar(["member_a"] * 4)
    assert len({c["name"] for c in after} - {c["name"] for c in before}) < 4
    result, calls = _run_delta_login(monkeypatch, tmp_path, before, after, "ajax")
    assert result[0] is False, f"insufficient new cookie names accepted: {shape}"
    assert calls["reads"] == ["before", "after"]


@pytest.mark.parametrize("branch", ["ajax", "closed", "post_closed"])
# Documented zero-entropy empty, whitespace, and substantial fixture values.
@pytest.mark.parametrize("value", ["", " " * 16, "0" * 16],
                         ids=["empty", "whitespace", "gained_value"])
def test_preexisting_empty_cookie_name_never_counts_as_new(monkeypatch, tmp_path, branch, value):
    before = [{"name": "pref_empty", "value": ""}]
    added = _delta_jar(["member_a", "member_b", "member_c"])
    after = [{"name": "pref_empty", "value": value}] + added
    assert len(before) == 1 and before[0]["value"] == ""
    assert after[0]["name"] == before[0]["name"]
    assert len({c["name"] for c in after} - {c["name"] for c in before}) == 3
    result, calls = _run_delta_login(monkeypatch, tmp_path, before, after, branch)
    assert result[0] is False, "preexisting empty-valued name counted as new"
    assert "Submit failed" in result[1] or "Page closed after submit" in result[1]
    assert calls["reads"] == ["before", "after"]


@pytest.mark.parametrize("branch", ["ajax", "closed", "post_closed"])
# Documented zero-entropy values: whitespace cannot supply substantial bytes.
@pytest.mark.parametrize("value", ["", " " * 16, "\t\n " * 4, "\u2003" * 9,
                                  "  " + "0" * 8 + "  "],
                         ids=["empty", "spaces", "mixed", "unicode", "padded_short"])
def test_cookie_delta_rejects_insubstantial_fourth_value(monkeypatch, tmp_path, branch, value):
    before = [{"name": "pref_empty", "value": ""}]
    added = _delta_jar(["member_a", "member_b", "member_c"])
    # The existing empty name reappears with whitespace, never as new evidence.
    after = [{"name": "pref_empty", "value": " " * 16}] + added
    after.append({"name": "member_d", "value": value})
    assert len(added) == 3 and all(len(c["value"]) == 16 for c in added)
    assert before[0]["name"] == after[0]["name"] and before[0]["value"] == ""
    assert len({c["name"] for c in after} - {c["name"] for c in before}) == 4
    assert len(value.strip()) <= 8
    result, calls = _run_delta_login(monkeypatch, tmp_path, before, after, branch)
    assert result[0] is False, "insubstantial fourth new cookie falsely accepted login"
    assert "Submit failed" in result[1] or "Page closed after submit" in result[1]
    assert calls["reads"] == ["before", "after"]


@pytest.mark.parametrize("branch", ["ajax", "closed", "post_closed"])
def test_cookie_delta_accepts_genuinely_substantial_padded_values(monkeypatch, tmp_path, branch):
    """ROW 708: padded values are still substantial, but the login settles.

    This test keeps its v3.66.1497 name because it still exercises exactly the
    1497 padded-value delta -- nine substantial bytes inside whitespace padding
    still count as four new substantial cookies, and that reason is asserted
    verbatim. Under row 708 the VERDICT it asserts changed: with NO NAV SIGNAL
    the run returns the distinct settled-no-nav state, where before row 708 this
    test asserted `result[0] is True`.
    """
    before = [{"name": "pref_empty", "value": ""}]
    added = _delta_jar(["member_a", "member_b", "member_c", "member_d"])
    # Documented zero-entropy values with nine substantial bytes inside padding.
    after = before + [{**cookie, "value": "  " + "0" * 9 + "  "} for cookie in added]
    assert len(added) == 4 and all(len(c["value"].strip()) == 9 for c in after[1:])
    result, calls = _run_delta_login(monkeypatch, tmp_path, before, after, branch)
    assert result[0].why == "4 new substantial cookies", (
        "row 708 moved the VERDICT only; v3.66.1497's padded-value delta must "
        f"still count 4 new substantial cookies -- got {result[0].why!r}")
    assert result[0].status == "settled-no-nav", (
        "row 708: a padded-value delta with NO NAV SIGNAL must settle as "
        f"settled-no-nav, never succeed -- got {result[0]!r} / {result[1]}")
    assert len(result[2]) == 5
    assert calls["reads"] == ["before", "after"]
