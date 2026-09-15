"""Row 772 -- explicit rejected-login landings are not successful logins."""

from types import SimpleNamespace

import pytest

BD_GATE_SCOPE = "module"


def _drive(monkeypatch, tmp_path, *, final_url, html):
    """Run the real login path with a post-submit rejection fixture."""
    from bulk_downloader import cloak, interstitial, learn, stealth
    from bulk_downloader.login_impl import submit

    calls = {"submit": 0, "content": 0}

    class Page:
        url = "https://login.example.invalid/login"

        def goto(self, url, **kwargs):
            assert url == self.url

        def content(self):
            calls["content"] += 1
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
