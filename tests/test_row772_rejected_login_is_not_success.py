"""Row 772 -- explicit rejected-login landings are not successful logins."""

from types import SimpleNamespace

import pytest

BD_GATE_SCOPE = "module"


def _drive(monkeypatch, tmp_path, *, final_url, html, content_raises=None):
    """Run the real login path with a post-submit rejection fixture.

    Row 813: `content_raises` makes the body probe fail, which is the state the
    deferred DP-13 hit was about.
    """
    from bulk_downloader import cloak, interstitial, learn, stealth
    from bulk_downloader.login_impl import submit

    calls = {"submit": 0, "content": 0}

    class Page:
        url = "https://login.example.invalid/login"

        def goto(self, url, **kwargs):
            assert url == self.url

        def content(self):
            calls["content"] += 1
            if content_raises is not None:
                raise content_raises
            return html

        def locator(self, selector):
            return SimpleNamespace(count=lambda: 0)

        def wait_for_load_state(self, *args, **kwargs):
            return None

    page = Page()
    ctx = SimpleNamespace(new_page=lambda: page, cookies=lambda: [])
    browser = SimpleNamespace(new_context=lambda **kwargs: ctx, close=lambda: None)
    monkeypatch.setattr(cloak, "launch_browser", lambda **kwargs: (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *args, **kwargs: None)
    monkeypatch.setattr(learn, "install_recorder", lambda page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *args, **kwargs: None)
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *args, **kwargs: [])
    monkeypatch.setattr(submit.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(submit, "replay_saved_login_flow", lambda *args: {"ran": False})
    monkeypatch.setattr(submit, "_fire_login_trigger_if_needed", lambda *args: (False, False, ""))
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *args, **kwargs: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda page: None)
    monkeypatch.setattr(submit, "_try_fill", lambda *args: (True, "fixture field"))

    def submit_form(*args):
        calls["submit"] += 1
        page.url = final_url
        return True, "fixture submit"

    monkeypatch.setattr(submit, "_submit_login", submit_form)
    result = submit.do_login({
        "login_url": "https://login.example.invalid/login",
        "username": "fixture", "password": "zero-entropy-password",
        "wait": 0, "use_real_chrome": False, "use_stealth": False,
        "use_stealth_library": False, "login_evidence_dir": str(tmp_path),
    })
    assert calls["submit"] == 1, calls
    return result, calls


@pytest.mark.parametrize(("final_url", "html"), [
    ("https://login.example.invalid/badlogin?ats=x", "<p>ordinary page</p>"),
    ("https://login.example.invalid/login", "<p>Wrong username or password provided</p>"),
])
def test_rejected_login_landing_never_returns_success(monkeypatch, tmp_path, final_url, html):
    """The row: neither explicit rejection shape may spend another login cap."""
    result, calls = _drive(monkeypatch, tmp_path, final_url=final_url, html=html)
    assert result[0] is False, result[1]
    assert "rejected login" in result[1].lower(), result[1]
    assert calls["content"] == (0 if "/badlogin" in final_url else 1), calls


# --- Row 813: the DP-13 hit O805 re-pinned the ceiling over rather than judge.
# The body probe was wrapped in `except Exception: pass`, so a page whose body
# could not be read looked exactly like a page whose body said nothing. The
# swallow is correct -- the /badlogin URL check above is the primary signal and
# must keep deciding -- but it is no longer silent.
PROBE_DIAGNOSTIC = "rejected-login body probe"


def test_an_unreadable_body_does_not_silently_skip_the_rejection_probe(
        monkeypatch, tmp_path, capsys):
    """THE ROW 813 HIT: the failed probe must announce itself, and say why the
    run can still decide."""
    _, calls = _drive(monkeypatch, tmp_path,
                      final_url="https://login.example.invalid/dashboard",
                      html="<p>irrelevant</p>",
                      content_raises=RuntimeError("target page has been closed"))
    assert calls["content"] == 1, calls
    err = capsys.readouterr().err
    assert PROBE_DIAGNOSTIC in err, err[-800:]
    assert "RuntimeError" in err, err[-800:]


def test_a_readable_body_never_emits_the_probe_diagnostic(monkeypatch, tmp_path, capsys):
    """NEGATIVE CONTROL: the diagnostic is evidence of a FAILED probe, so the
    ordinary path must not print it -- otherwise the assertion above would pass
    against a line that always fires."""
    _drive(monkeypatch, tmp_path,
           final_url="https://login.example.invalid/login",
           html="<p>Wrong username or password provided</p>")
    assert PROBE_DIAGNOSTIC not in capsys.readouterr().err


def test_an_unreadable_body_still_leaves_the_url_check_deciding(monkeypatch, tmp_path):
    """A failed probe is not a rejection: /badlogin decides, exactly as before."""
    result, _ = _drive(monkeypatch, tmp_path,
                       final_url="https://login.example.invalid/badlogin",
                       html="<p>irrelevant</p>",
                       content_raises=RuntimeError("target page has been closed"))
    assert result[0] is False, result[1]
    assert "rejected login" in result[1].lower(), result[1]
