"""fx-xhamster-login-verdict (O1568d, spare12 10.0.70.183 21:47Z; results/spare12/xhamster.md): xhamster's login is an
AJAX modal. The submit consumed the form without navigation and the jar held 13 cookies, none auth-named:

  login: no nav signal and cookies unconvincing (13 cookie(s), 9 substantial, 3 new substantial, ...)
  login: handing off for manual takeover -- Couldn't submit form: click submit selector consumed the login form ...

while the page read was logged in (bell, My News, Watch History, the user dropdown's <a href=".../logout">). Row 708
admits only a DECLARED positive check for a no-navigation login, and a site could not declare one: member_indicator
was read from a learned template only, and this branch never asked. Fix: a site-level ``member_indicator`` (in
CFG_FIELDS), consulted by that branch; the jar still never decides.

do_login driven with the browser/UI boundaries replaced (as test_row708_no_nav_login_is_not_success); zero-entropy
fixture credentials, no live site.
"""
from types import SimpleNamespace

import pytest

BD_GATE_SCOPE = "module"

SPA_METHOD = "click submit selector consumed the login form without navigation (SPA login)"
INDICATOR = 'a[href$="/logout"]'


def _jar(names):
    return [{"name": name, "value": "0" * 16} for name in names]


class _Locator:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


def _drive(monkeypatch, tmp_path, *, method=SPA_METHOD, member_indicator=INDICATOR, present=True,
           learned=False, allow_manual=False):
    from bulk_downloader import cloak, interstitial, learn, stealth
    from bulk_downloader.login_impl import submit

    jar = _jar(["pref_a", "pref_b", "pref_c"])            # unchanged by the submit: unconvincing
    calls = {"submit": 0, "locator": [], "handoff": 0}
    login_url = "https://fixture-xhamster.invalid/"

    class Page:
        url = login_url

        def goto(self, url, **kwargs):
            return None

        def content(self):
            return "<html><body>fixture page</body></html>"

        def locator(self, selector):
            calls["locator"].append(selector)
            return _Locator(1 if present else 0)

        def wait_for_load_state(self, *a, **kw):
            return None

        def evaluate(self, *a, **kw):
            return None

    page = Page()
    ctx = SimpleNamespace(new_page=lambda: page, cookies=lambda: jar)
    browser = SimpleNamespace(new_context=lambda **kw: ctx, close=lambda: None)
    monkeypatch.setattr(cloak, "launch_browser", lambda **kw: (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(learn, "install_recorder", lambda page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *a: None)
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *a, **kw: [])
    monkeypatch.setattr(submit.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(submit, "replay_saved_login_flow", lambda *a: {"ran": False})
    monkeypatch.setattr(submit, "_fire_login_trigger_if_needed", lambda *a, **kw: (False, False, ""))
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *a, **kw: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda page: None)
    monkeypatch.setattr(submit, "_try_fill", lambda page, selectors, value, role: (True, "fixture field"))

    def submit_form(page, selectors, password_selectors):
        calls["submit"] += 1
        return False, method

    monkeypatch.setattr(submit, "_submit_login", submit_form)
    config = {"login_url": login_url, "username": "fixture", "password": "zero-entropy-password", "wait": 0,
              "use_real_chrome": False, "use_stealth": False, "use_stealth_library": False,
              "login_evidence_dir": str(tmp_path / "evidence")}
    if member_indicator and learned:
        config["learned"] = {"login": {"member_indicator": member_indicator}}
    elif member_indicator:
        config["member_indicator"] = member_indicator
    result = submit.do_login(config, allow_manual_takeover=allow_manual)
    assert calls["submit"] == 1, calls
    return result, calls


def test_declared_member_indicator_confirms_the_ajax_login(monkeypatch, tmp_path):
    """THE ROW: xhamster's shape with its declared indicator present on the page read."""
    result, calls = _drive(monkeypatch, tmp_path)
    assert result[0] is True, (
        f"FX_XHAMSTER_LOGIN_VERDICT: a declared member indicator present on the page read did not confirm the "
        f"no-navigation login: {result[0]!r} / {result[1]!r}")
    assert "member indicator" in result[1] and INDICATOR in result[1], result[1]
    assert INDICATOR in calls["locator"], calls
    assert (tmp_path / "evidence").exists() and any((tmp_path / "evidence").iterdir())


def test_learned_indicator_still_counts(monkeypatch, tmp_path):
    result, _calls = _drive(monkeypatch, tmp_path, learned=True)
    assert result[0] is True, result


def test_control_indicator_absent_fails_exactly_as_before(monkeypatch, tmp_path):
    result, calls = _drive(monkeypatch, tmp_path, present=False)
    assert result[0] is False and "Submit failed" in result[1], result
    assert INDICATOR in calls["locator"], calls


def test_control_nothing_declared_never_reaches_success(monkeypatch, tmp_path):
    result, calls = _drive(monkeypatch, tmp_path, member_indicator="")
    assert result[0] is False and "Submit failed" in result[1], result
    assert calls["locator"] == [], calls


def test_control_a_submit_that_never_consumed_the_form_is_not_judged(monkeypatch, tmp_path):
    result, calls = _drive(monkeypatch, tmp_path, method="could not click submit button; tried 59 selectors")
    assert result[0] is False and "Submit failed" in result[1], result
    assert calls["locator"] == [], calls


def test_member_indicator_is_a_persisted_site_field():
    from bulk_downloader.app_kernel import CFG_FIELDS

    assert "member_indicator" in CFG_FIELDS
