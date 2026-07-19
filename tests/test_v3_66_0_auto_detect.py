"""v3.66.0 — tests for the templates-free auto-detector.

Covers the auto_detect.detect_site_config dispatch over its three
input branches (page / html / url) plus the global config toggle that
wires it into _auto_pick_templates.
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── HTML branch ──────────────────────────────────────────────────────

_CLEAN_LOGIN_HTML = """
<form id="login" method="post" action="/auth">
  <input type="text" name="username" id="username" placeholder="Email">
  <input type="password" name="password" id="password">
  <button type="submit" id="go">Log in</button>
</form>
"""

_NO_LOGIN_HTML = """
<html><body><p>Welcome — please click below to start.</p>
<a href="/start" class="cta">Start</a></body></html>
"""

_LOGIN_WITH_BOGUS_TYPE = """
<form class="form-signin" action="/login" method="post">
  <input type="username" name="username" id="inputUsername">
  <input type="password" name="password" id="inputPassword">
  <button type="submit" name="sign-in">Sign in</button>
</form>
"""


def test_html_branch_recognizes_login_form():
    from bulk_downloader import auto_detect
    r = auto_detect.detect_site_config(html=_CLEAN_LOGIN_HTML)
    assert r["ok"] is True
    assert r["source"] == "html"
    login = r["learned"]["login"]
    assert "#username" in login["user_field"]
    assert "#password" in login["pass_field"]
    assert "#go" in login["submit_btn"]
    # HTML branch can't probe download; should warn the caller
    assert "download" not in r["learned"]
    assert any("download" in w.lower() for w in r["warnings"])


def test_html_branch_reports_failure_when_no_login_form():
    from bulk_downloader import auto_detect
    r = auto_detect.detect_site_config(html=_NO_LOGIN_HTML)
    assert r["ok"] is False
    assert "no login form" in (r["error"] or "")
    assert r["learned"] == {}


def test_html_branch_handles_bogus_input_type():
    """The login_nubiles template had input[type='username']. The
    detector treats that as a text-shaped input (browsers do the same
    fallback) and still recognizes the form."""
    from bulk_downloader import auto_detect
    r = auto_detect.detect_site_config(html=_LOGIN_WITH_BOGUS_TYPE)
    assert r["ok"] is True
    login = r["learned"]["login"]
    # The detector should produce a stable selector for the username
    # field even though its declared type is bogus.
    assert login.get("user_field")
    # And it should NOT have copied the bogus type into the produced
    # selector — that's the bug we found in the static template.
    for sel in login["user_field"]:
        assert "type='username'" not in sel


# ── No-input branch ─────────────────────────────────────────────────


def test_empty_call_returns_clear_error():
    from bulk_downloader import auto_detect
    r = auto_detect.detect_site_config()
    assert r["ok"] is False
    assert "URL" in (r["error"] or "") or "HTML" in (r["error"] or "")


# ── Selector synthesis (download half) ──────────────────────────────


class _FakeLocator:
    """Just enough of a Playwright Locator to satisfy the helper."""

    def __init__(self, descriptor):
        self._desc = descriptor

    def evaluate(self, _js):
        return self._desc


def test_synthesize_row_selector_prefers_id():
    from bulk_downloader.auto_detect import _synthesize_row_selector
    sels, ua = _synthesize_row_selector({
        "tag": "a", "id": "dl-main", "classes": ["btn", "primary"],
        "url_attribute": "href", "attrs": {"href": "/x.mp4"},
    })
    assert sels[0] == "#dl-main"
    assert ua == "href"


def test_synthesize_row_selector_skips_hashed_classes():
    """A button whose only classes are auto-generated (e.g. .joHetV)
    should NOT use them — the synthesizer must fall through to the
    data-attribute or aria-label."""
    from bulk_downloader.auto_detect import _synthesize_row_selector
    sels, _ = _synthesize_row_selector({
        "tag": "button", "id": "", "classes": ["joHetV", "ljLTqn"],
        "url_attribute": "data-href",
        "attrs": {"data-href": "/x.mp4", "aria-label": "Download 1080p"},
    })
    # No hashed class selector
    for s in sels:
        assert ".joHetV" not in s
        assert ".ljLTqn" not in s
    # But the data-href anchor must be present
    assert any("data-href" in s for s in sels)


def test_synthesize_row_selector_handles_missing_descriptor():
    from bulk_downloader.auto_detect import _synthesize_row_selector
    assert _synthesize_row_selector(None) == ([], "")
    assert _synthesize_row_selector({}) == ([], "")


# ── Toggle in _auto_pick_templates ──────────────────────────────────


def test_toggle_default_mode_is_static():
    """The new flag must default to legacy behavior so this change is
    non-breaking for existing installs."""
    from bulk_downloader import app
    mode = (app._app_cfg.get("template_auto_detect_mode") or "static")
    assert mode == "static"


def _install_stub_runner(app_mod, sid, cfg):
    """Install a fake site+runner into app.runners/s_cfg. Returns a
    callable that restores the pre-install state."""
    saved_runners = dict(app_mod.runners)
    saved_s_cfg = dict(app_mod.s_cfg)
    app_mod.runners[sid] = type("R", (), {
        "update_config": lambda self, c: None,
    })()
    app_mod.s_cfg[sid] = cfg

    def _restore():
        app_mod.runners.clear()
        app_mod.runners.update(saved_runners)
        app_mod.s_cfg.clear()
        app_mod.s_cfg.update(saved_s_cfg)

    return _restore


def test_toggle_unknown_mode_falls_back_to_static(monkeypatch):
    """An invalid mode in app_config must not break site creation —
    it should be treated as 'static'."""
    from bulk_downloader import app
    app._app_cfg["template_auto_detect_mode"] = "nonsense"
    from bulk_downloader import login_templates_data as _lt
    known_host = _lt.LOGIN_TEMPLATES[0]["host"]
    cfg = {"login_url": f"https://{known_host}/login"}
    restore = _install_stub_runner(app, "T1", cfg)
    try:
        monkeypatch.setattr(app, "_apply_login_template_by_id",
                            lambda *a: (True, "stub"))
        monkeypatch.setattr(app, "_apply_template_by_id",
                            lambda *a: (True, "stub"))
        monkeypatch.setattr(app, "_save_sites_config", lambda: None)

        r = app._auto_pick_templates("T1", cfg)
        # Must not raise; result shape preserved
        assert isinstance(r, dict)
        assert set(r) >= {"download_template_applied",
                          "login_template_applied"}
    finally:
        restore()


def test_toggle_detect_mode_calls_auto_detect(monkeypatch):
    """In 'detect' mode, _auto_pick_templates must call
    auto_detect.detect_site_config and never touch the registries."""
    from bulk_downloader import app, auto_detect
    app._app_cfg["template_auto_detect_mode"] = "detect"
    cfg = {"login_url": "https://example.com/login"}
    restore = _install_stub_runner(app, "T1", cfg)
    try:
        called = {"detect": 0, "static_login": 0, "static_dl": 0}

        def fake_detect(url="", **kw):
            called["detect"] += 1
            return {
                "ok": True,
                "source": "html",
                "learned": {
                    "login": {
                        "user_field": ["#username"],
                        "pass_field": ["#password"],
                        "submit_btn": ["#go"],
                    }
                },
                "warnings": [],
                "error": None,
            }

        monkeypatch.setattr(auto_detect, "detect_site_config", fake_detect)

        def fake_static_login(*a):
            called["static_login"] += 1
            return True, "x"

        def fake_static_dl(*a):
            called["static_dl"] += 1
            return True, "x"

        monkeypatch.setattr(app, "_apply_login_template_by_id",
                            fake_static_login)
        monkeypatch.setattr(app, "_apply_template_by_id", fake_static_dl)
        monkeypatch.setattr(app, "_save_sites_config", lambda: None)

        r = app._auto_pick_templates("T1", cfg)
        assert called["detect"] >= 1
        assert called["static_login"] == 0
        assert called["static_dl"] == 0
        assert r["login_template_applied"] == "<auto-detect>"
    finally:
        restore()


def test_toggle_detect_then_static_falls_back(monkeypatch):
    """In 'detect_then_static', a failed detection must let the static
    path attempt to fill the gap."""
    from bulk_downloader import app, auto_detect, login_templates_data as _lt
    app._app_cfg["template_auto_detect_mode"] = "detect_then_static"
    known_host = _lt.LOGIN_TEMPLATES[0]["host"]
    cfg = {"login_url": f"https://{known_host}/login"}
    restore = _install_stub_runner(app, "T1", cfg)
    try:
        def fake_detect(url="", **kw):
            return {"ok": False, "source": None, "learned": {},
                    "warnings": [], "error": "stubbed failure"}

        static_called = {"login": False}

        def fake_apply_login(sid, tid):
            static_called["login"] = True
            return True, "stub-name"

        monkeypatch.setattr(auto_detect, "detect_site_config", fake_detect)
        monkeypatch.setattr(app, "_apply_login_template_by_id",
                            fake_apply_login)
        monkeypatch.setattr(app, "_apply_template_by_id",
                            lambda *a: (True, "x"))
        monkeypatch.setattr(app, "_save_sites_config", lambda: None)

        app._auto_pick_templates("T1", cfg)
        assert static_called["login"] is True
    finally:
        restore()
