"""v3.43.60: Captcha relay test suite.

Covers:
  - detect_captcha_in_html: pattern matching against known DOM fragments
  - State machine: mark → start_solve → resolved / dismissed
  - Push dedupe window: same (site, type) within window = no double-push
  - MAX_PENDING eviction
  - Ender callback invoked on resolve/dismiss
  - Blueprint registers 5 routes
  - JS↔HTML↔CSS contract checks

NOTE: This file uses class setup_method() and plain helper methods instead
of pytest fixtures, for portability across the project's three test runners
(real pytest, run_min_pytest, run_tests.py).
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import re
import sys
import time
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _bd_runner_src():
    """v3.66.403: runner.py decomposed into runner_*.py mixins; aggregate the
    package so moved SiteRunner method bodies stay visible to source checks."""
    from pathlib import Path as _P
    from bulk_downloader import runner as _R
    _pd = _P(_R.__file__).parent
    return "\n".join(q.read_text(encoding="utf-8")
                     for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))


class _RelayResetBase:
    """Base class resetting captcha_relay state per test — replaces the
    @pytest.fixture(autouse=True) module-level fixture."""
    def setup_method(self):
        from bulk_downloader import captcha_relay
        captcha_relay._reset_for_tests()

    def teardown_method(self):
        from bulk_downloader import captcha_relay
        captcha_relay._reset_for_tests()
        # Always remove any test-injected push stub
        sys.modules.pop("bulk_downloader.push", None)


# ════════════════════════════════════════════════════════════════════
#  HTML detection (no Playwright dependency)
# ════════════════════════════════════════════════════════════════════

class TestHtmlDetection(_RelayResetBase):
    def test_turnstile_iframe_detected(self):
        from bulk_downloader import captcha_relay
        html = '<html><body><iframe src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/foo"></iframe></body></html>'
        assert captcha_relay.detect_captcha_in_html(html) == "turnstile"

    def test_turnstile_widget_class_detected(self):
        from bulk_downloader import captcha_relay
        html = '<div class="cf-turnstile" data-sitekey="0x4AAA..."></div>'
        assert captcha_relay.detect_captcha_in_html(html) == "turnstile"

    def test_hcaptcha_detected(self):
        from bulk_downloader import captcha_relay
        html = '<iframe src="https://newassets.hcaptcha.com/captcha"></iframe>'
        assert captcha_relay.detect_captcha_in_html(html) == "hcaptcha"

    def test_recaptcha_detected(self):
        from bulk_downloader import captcha_relay
        html = '<div class="g-recaptcha" data-sitekey="6Le..."></div>'
        assert captcha_relay.detect_captcha_in_html(html) == "recaptcha"

    def test_cloudflare_interstitial_detected_by_id(self):
        from bulk_downloader import captcha_relay
        html = '<div id="challenge-stage"><div id="cf-please-wait"></div></div>'
        assert captcha_relay.detect_captcha_in_html(html) == "cloudflare"

    def test_cloudflare_detected_by_title_only(self):
        from bulk_downloader import captcha_relay
        assert captcha_relay.detect_captcha_in_html("", title="Just a moment...") == "cloudflare"
        assert captcha_relay.detect_captcha_in_html("", title="Attention Required! | Cloudflare") == "cloudflare"
        assert captcha_relay.detect_captcha_in_html("", title="Please wait while we verify") == "cloudflare"

    def test_clean_page_returns_none(self):
        from bulk_downloader import captcha_relay
        assert captcha_relay.detect_captcha_in_html('<html><body><h1>Welcome</h1></body></html>') is None

    def test_empty_html_returns_none(self):
        from bulk_downloader import captcha_relay
        assert captcha_relay.detect_captcha_in_html("") is None
        assert captcha_relay.detect_captcha_in_html(None) is None


# ════════════════════════════════════════════════════════════════════
#  State machine
# ════════════════════════════════════════════════════════════════════

class TestStateMachine(_RelayResetBase):
    def test_mark_creates_pending(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wowgirls", "https://x.com/1", "turnstile")
        p = captcha_relay.get_pending("https://x.com/1")
        assert p is not None
        assert p["status"] == "pending"
        assert p["site_id"] == "wowgirls"
        assert p["captcha_type"] == "turnstile"

    def test_mark_is_idempotent(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wowgirls", "https://x.com/1", "turnstile")
        captcha_relay.mark_captcha_needed("wowgirls", "https://x.com/1", "turnstile")
        pending = captcha_relay.list_pending()
        assert sum(1 for p in pending if p["url"] == "https://x.com/1") == 1

    def test_mark_unknown_type_rejected(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wowgirls", "https://x.com/1", "made_up")
        assert captcha_relay.get_pending("https://x.com/1") is None

    def test_resolved_transitions(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wowgirls", "https://x.com/1", "turnstile")
        assert captcha_relay.mark_resolved("https://x.com/1") is True
        p = captcha_relay.get_pending("https://x.com/1")
        assert p["status"] == "resolved"

    def test_dismissed_transitions(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wowgirls", "https://x.com/1", "turnstile")
        captcha_relay.mark_dismissed("https://x.com/1")
        p = captcha_relay.get_pending("https://x.com/1")
        assert p["status"] == "dismissed"

    def test_resolve_unknown_url_returns_false(self):
        from bulk_downloader import captcha_relay
        assert captcha_relay.mark_resolved("https://nope.com/x") is False

    def test_is_pending_predicate(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "hcaptcha")
        assert captcha_relay.is_pending("https://x.com/1") is True
        captcha_relay.mark_resolved("https://x.com/1")
        assert captcha_relay.is_pending("https://x.com/1") is False

    def test_list_pending_excludes_resolved_by_default(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("a", "https://x.com/1", "turnstile")
        captcha_relay.mark_captcha_needed("a", "https://x.com/2", "turnstile")
        captcha_relay.mark_resolved("https://x.com/1")
        urls = {p["url"] for p in captcha_relay.list_pending()}
        assert urls == {"https://x.com/2"}
        urls_all = {p["url"] for p in captcha_relay.list_pending(include_resolved=True)}
        assert urls_all == {"https://x.com/1", "https://x.com/2"}


# ════════════════════════════════════════════════════════════════════
#  Solve session lifecycle
# ════════════════════════════════════════════════════════════════════

class TestSolveSession(_RelayResetBase):
    def test_start_solve_without_starter_raises(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        with pytest.raises(RuntimeError, match="takeover_starter"):
            captcha_relay.start_solve("https://x.com/1")

    def test_start_solve_unknown_url_raises(self):
        from bulk_downloader import captcha_relay
        captcha_relay.register_takeover_starter(lambda sid, u: {"session_id": "s1"})
        with pytest.raises(RuntimeError, match="not pending"):
            captcha_relay.start_solve("https://nope.com/x")

    def test_start_solve_calls_starter(self):
        from bulk_downloader import captcha_relay
        calls = []
        def starter(sid, url):
            calls.append((sid, url))
            return {"session_id": "sess-123", "ok": True}
        captcha_relay.register_takeover_starter(starter)
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        info = captcha_relay.start_solve("https://x.com/1")
        assert calls == [("wg", "https://x.com/1")]
        assert info["session_id"] == "sess-123"
        p = captcha_relay.get_pending("https://x.com/1")
        assert p["status"] == "solving"

    def test_start_solve_already_solving_rejected(self):
        from bulk_downloader import captcha_relay
        captcha_relay.register_takeover_starter(lambda sid, u: {"session_id": "s1"})
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        captcha_relay.start_solve("https://x.com/1")
        with pytest.raises(RuntimeError, match="not in 'pending'"):
            captcha_relay.start_solve("https://x.com/1")


class TestEnderCallback(_RelayResetBase):
    def test_resolved_invokes_ender(self):
        from bulk_downloader import captcha_relay
        ends = []
        captcha_relay.register_takeover_ender(
            lambda sid, url, resolution: ends.append((sid, url, resolution))
        )
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        captcha_relay.mark_resolved("https://x.com/1")
        assert ends == [("wg", "https://x.com/1", "resolved")]

    def test_dismissed_invokes_ender(self):
        from bulk_downloader import captcha_relay
        ends = []
        captcha_relay.register_takeover_ender(
            lambda sid, url, resolution: ends.append((sid, url, resolution))
        )
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        captcha_relay.mark_dismissed("https://x.com/1")
        assert ends == [("wg", "https://x.com/1", "dismissed")]

    def test_ender_exception_does_not_break_state_change(self):
        from bulk_downloader import captcha_relay
        def bad_ender(sid, url, res): raise RuntimeError("boom")
        captcha_relay.register_takeover_ender(bad_ender)
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        assert captcha_relay.mark_resolved("https://x.com/1") is True
        p = captcha_relay.get_pending("https://x.com/1")
        assert p["status"] == "resolved"

    def test_no_ender_registered_is_fine(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        assert captcha_relay.mark_resolved("https://x.com/1") is True


# ════════════════════════════════════════════════════════════════════
#  Push dedupe window
# ════════════════════════════════════════════════════════════════════

class _FakePush:
    """Test double for bulk_downloader.push — captures invocations."""
    pushes = []
    @classmethod
    def send_push(cls, **kwargs):
        cls.pushes.append(kwargs)
        return {"ok": True}
    @classmethod
    def reset(cls):
        cls.pushes = []


class TestPushDedupe(_RelayResetBase):
    def setup_method(self):
        super().setup_method()
        _FakePush.reset()
        sys.modules["bulk_downloader.push"] = _FakePush

    def teardown_method(self):
        super().teardown_method()
        # super's teardown removes the stub from sys.modules

    def test_first_mark_pushes(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        assert len(_FakePush.pushes) == 1
        assert "wg" in _FakePush.pushes[0]["title"]
        assert "turnstile" in _FakePush.pushes[0]["body"].lower()

    def test_same_site_type_deduped_within_window(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        captcha_relay.mark_captcha_needed("wg", "https://x.com/2", "turnstile")
        assert len(_FakePush.pushes) == 1  # second deduped

    def test_different_type_not_deduped(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        captcha_relay.mark_captcha_needed("wg", "https://x.com/2", "hcaptcha")
        assert len(_FakePush.pushes) == 2  # different types push independently


# ════════════════════════════════════════════════════════════════════
#  MAX_PENDING eviction
# ════════════════════════════════════════════════════════════════════

class TestMaxPending(_RelayResetBase):
    def test_max_pending_caps_state(self):
        from bulk_downloader import captcha_relay
        for i in range(70):
            captcha_relay.mark_captcha_needed("wg", f"https://x.com/{i}", "turnstile")
        all_p = captcha_relay.list_pending(include_resolved=True)
        assert len(all_p) <= 64

    def test_resolved_makes_room_for_new(self):
        from bulk_downloader import captcha_relay
        for i in range(60):
            captcha_relay.mark_captcha_needed("a", f"https://x.com/{i}", "turnstile")
            captcha_relay.mark_resolved(f"https://x.com/{i}")
        captcha_relay.mark_captcha_needed("b", "https://new.com/1", "hcaptcha")
        pendings = captcha_relay.list_pending()
        assert any(p["url"] == "https://new.com/1" for p in pendings)


# ════════════════════════════════════════════════════════════════════
#  Sweep expired
# ════════════════════════════════════════════════════════════════════

class TestSweepExpired(_RelayResetBase):
    def test_old_pendings_marked_dismissed(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        future_now = time.time() + captcha_relay._pending_timeout_s() + 1
        swept = captcha_relay.sweep_expired(now=future_now)
        assert swept == 1
        p = captcha_relay.get_pending("https://x.com/1")
        assert p["status"] == "dismissed"

    def test_fresh_pendings_untouched(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        assert captcha_relay.sweep_expired() == 0


# ════════════════════════════════════════════════════════════════════
#  Blueprint registration
# ════════════════════════════════════════════════════════════════════

class TestApiRegistration(_RelayResetBase):
    def test_blueprint_registers_5_routes(self):
        try:
            import flask
        except ImportError:
            pytest.skip("flask not installed")
        from bulk_downloader import app_captcha_relay
        app = flask.Flask("test_app")
        n = app_captcha_relay.register_routes(app)
        assert n == 5
        rules = {r.rule for r in app.url_map.iter_rules() if "/api/captcha" in r.rule}
        assert "/api/captcha/pending" in rules
        assert "/api/captcha/start_solve" in rules
        assert "/api/captcha/resolved" in rules
        assert "/api/captcha/dismiss" in rules


# ════════════════════════════════════════════════════════════════════
#  HTML / JS / CSS surface
# ════════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════════
#  Runner integration
# ════════════════════════════════════════════════════════════════════

class TestRunnerIntegration(_RelayResetBase):
    """Verify runner.py's _process_one was modified to call captcha_relay
    when use_captcha_relay is on, and that SiteRunner has the two takeover
    methods that the app.py dispatcher needs."""

    def _runner_src(self):
        return _bd_runner_src()

    def test_runner_imports_captcha_relay(self):
        src = self._runner_src()
        assert "from . import captcha_relay" in src
        assert "_CAPTCHA_RELAY_AVAILABLE" in src

    def test_process_one_calls_relay_after_check(self):
        src = self._runner_src()
        assert "captcha_relay.check_and_handle" in src
        assert 'self.config.get("use_captcha_relay")' in src

    def test_siterunner_has_takeover_methods(self):
        src = self._runner_src()
        assert "def start_captcha_solve_session" in src
        assert "def end_captcha_solve_session" in src

    def test_app_py_wires_dispatchers(self):
        src = (_repo_root() / "bulk_downloader" / "app.py").read_text(encoding="utf-8")
        assert "_captcha_takeover_starter" in src
        assert "_captcha_takeover_ender" in src
        assert "register_takeover_starter" in src
        assert "register_takeover_ender" in src
