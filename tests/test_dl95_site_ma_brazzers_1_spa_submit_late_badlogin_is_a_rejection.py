"""dl95-site-ma-brazzers-1 -- a submit that consumed the form without
navigating, then landed on /badlogin, is a rejected login.

Live (.82, build 2c0c48c0, 2026-09-29 10:35:51Z): the click on
button[type=submit] consumed the site-ma.brazzers.com form, the 8 s sweep saw
no navigation, and do_login reported "click submit selector consumed the login
form without navigation (SPA login)" and opened a manual takeover. The
takeover evidence written 2 s later has final_url
https://www.brazzers.com/badlogin?ats=... -- the server's rejection landing.
The same landing on a navigating submit is already "Rejected login landing"
(what the session keeper stops on); the no-navigation branch never looked.
"""

from types import SimpleNamespace


BD_GATE_SCOPE = "module"

LOGIN = "https://site-ma.example.invalid/login"
BADLOGIN = "https://www.example.invalid/badlogin?ats=fixture"
SPA_NO_NAV = ("click submit selector consumed the login form without "
              "navigation (SPA login)")


def _drive(monkeypatch, tmp_path, *, late_url, flip_after_waits=1,
           jar=(), allow_manual_takeover=False, late_body=None, method=SPA_NO_NAV):
    """Real do_login; the sweep returns ``method`` (default the SPA no-nav
    verdict) and the page reaches ``late_url`` / ``late_body`` only after
    ``flip_after_waits`` settle waits (0 = already there when the sweep ends)."""
    from bulk_downloader import cloak, interstitial, learn, stealth
    from bulk_downloader.login_impl import submit

    calls = {"submit": 0, "waits_after_submit": 0}

    class Page:
        url = LOGIN
        body = "<p>fixture</p>"

        def flip(self):
            if late_url:
                self.url = late_url
            if late_body:
                self.body = late_body

        def goto(self, url, **kwargs):
            pass

        def content(self):
            return self.body

        def locator(self, selector):
            return SimpleNamespace(count=lambda: 0)

        def is_closed(self):
            return False

        def wait_for_load_state(self, *args, **kwargs):
            if calls["submit"]:
                calls["waits_after_submit"] += 1
                if calls["waits_after_submit"] >= flip_after_waits:
                    self.flip()

    page = Page()
    ctx = SimpleNamespace(new_page=lambda: page, cookies=lambda: list(jar))
    browser = SimpleNamespace(new_context=lambda **kwargs: ctx, close=lambda: None)
    monkeypatch.setattr(cloak, "launch_browser", lambda **kwargs: (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *args, **kwargs: None)
    monkeypatch.setattr(learn, "install_recorder", lambda page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *args, **kwargs: None)
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *args, **kwargs: [])
    monkeypatch.setattr(submit.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(submit, "replay_saved_login_flow", lambda *args: {"ran": False})
    monkeypatch.setattr(submit, "_fire_login_trigger_if_needed",
                        lambda *args, **kwargs: (False, False, ""))
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *args, **kwargs: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda page: None)
    monkeypatch.setattr(submit, "_try_fill", lambda *args: (True, "fixture field"))
    monkeypatch.setattr(submit, "keep_pre_submit_screenshot", lambda *args: "")
    monkeypatch.setattr(submit, "_captcha_mount_name", lambda page: "", raising=False)

    monkeypatch.setattr(submit, "write_login_evidence", lambda *args, **kwargs: "")

    def sweep(*args):
        calls["submit"] += 1
        if flip_after_waits == 0:
            page.flip()
        return False, method

    monkeypatch.setattr(submit, "_submit_login", sweep)
    result = submit.do_login({
        "login_url": LOGIN,
        "username": "fixture", "password": "zero-entropy-password",
        "wait": 0, "use_real_chrome": False, "use_stealth": False,
        "use_stealth_library": False, "login_evidence_dir": str(tmp_path),
    }, allow_manual_takeover=allow_manual_takeover)
    assert calls["submit"] == 1, ("BRZ1_SWEEP_NOT_REACHED", calls, result)
    return result, calls


def test_spa_no_nav_submit_that_lands_late_on_badlogin_is_rejected(monkeypatch, tmp_path):
    result, calls = _drive(monkeypatch, tmp_path, late_url=BADLOGIN)
    assert result[0] is False, result
    assert str(result[1]).startswith("Rejected login landing"), (
        "BRZ1_LATE_BADLOGIN_READ_AS_SPA_NO_NAV: a no-navigation submit whose "
        f"page reached /badlogin was reported as {result[1]!r}")


def test_late_badlogin_does_not_open_a_manual_takeover(monkeypatch, tmp_path):
    result, _ = _drive(monkeypatch, tmp_path, late_url=BADLOGIN,
                       allow_manual_takeover=True)
    assert result[0] is False and str(result[1]).startswith("Rejected login landing"), (
        "BRZ1_LATE_BADLOGIN_HANDED_TO_TAKEOVER: a server rejection became "
        f"{result[0]!r} {str(result[1])[:120]!r}")


def test_late_badlogin_outranks_a_cookie_jar(monkeypatch, tmp_path):
    jar = [{"name": "auth_token", "value": "x" * 40, "domain": ".example.invalid",
            "path": "/"}]
    result, _ = _drive(monkeypatch, tmp_path, late_url=BADLOGIN, jar=jar)
    assert result[0] is False and str(result[1]).startswith("Rejected login landing"), (
        "BRZ1_JAR_JUDGED_BEFORE_BADLOGIN: the jar decided a /badlogin landing: "
        f"{result[0]!r} {str(result[1])[:120]!r}")


def test_spa_no_nav_that_stays_on_the_login_page_is_unchanged(monkeypatch, tmp_path):
    result, calls = _drive(monkeypatch, tmp_path, late_url=None)
    assert result == (False, f"Submit failed: {SPA_NO_NAV}", []), (
        "BRZ1_CONTROL_CHANGED", result)
    assert calls["waits_after_submit"] <= 12, ("BRZ1_UNBOUNDED_WAIT", calls)


# REFUTE R1 (A10-A, 14:54Z): the product's rejected-login predicate is the
# /badlogin URL OR this body phrase (row 772: Gamma /en/login re-renders the
# login page inline). The no-navigation branch must use the same predicate.
REJECTED_BODY = ("<form><input type=password></form>"
                 "<div class=error>Wrong username or password provided</div>")


def test_spa_no_nav_submit_whose_page_later_says_wrong_password_is_rejected(monkeypatch, tmp_path):
    result, _ = _drive(monkeypatch, tmp_path, late_url=None, late_body=REJECTED_BODY)
    assert result[0] is False and str(result[1]).startswith("Rejected login landing"), (
        "BRZ1_LATE_PHRASE_NOT_JUDGED: the inline wrong-password page was reported as "
        f"{result[0]!r} {str(result[1])[:120]!r}")


def test_inline_wrong_password_is_not_handed_to_a_takeover(monkeypatch, tmp_path):
    result, _ = _drive(monkeypatch, tmp_path, late_url=None, late_body=REJECTED_BODY,
                       allow_manual_takeover=True)
    assert result[0] is False and str(result[1]).startswith("Rejected login landing"), (
        "BRZ1_PHRASE_HANDED_TO_TAKEOVER: a credential rejection became "
        f"{result[0]!r} {str(result[1])[:120]!r}")


def test_other_no_nav_methods_read_the_phrase_already_on_the_page(monkeypatch, tmp_path):
    """No polling for other methods, but the page as it stands is still judged."""
    result, calls = _drive(monkeypatch, tmp_path, late_url=None, late_body=REJECTED_BODY,
                           flip_after_waits=0, method="no submit method produced navigation")
    assert result[0] is False and str(result[1]).startswith("Rejected login landing"), (
        "BRZ1_PHRASE_NOT_JUDGED_OTHER_METHOD: "
        f"{result[0]!r} {str(result[1])[:120]!r}")
