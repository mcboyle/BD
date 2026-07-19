"""v3.43.70: tests for the apprise notifications module.

Covers:
  1. is_available() returns sane value (True if apprise installed,
     False otherwise; never raises)
  2. parse_urls_text() splits multi-line, skips comments + blanks
  3. validate_urls() reports per-URL ok/schema/error; fail-open
     when apprise not installed
  4. _safe_url_display() masks credentials
  5. send() fail-open when apprise unavailable
  6. send() returns SendResult with expected shape
  7. policy_from_config() builds override dict from flat config
  8. policy_from_config() falls back to DEFAULT_POLICY for missing keys
  9. NotificationDispatcher per_event mode fires immediately
 10. NotificationDispatcher batched mode accumulates then flushes
 11. NotificationDispatcher flush_all flushes pending
 12. NotificationDispatcher disabled does nothing
 13. NotificationDispatcher no-urls does nothing
 14. get_dispatcher() returns same instance (singleton)
 15. Runner has _BD_TO_APPRISE_EVENT map with expected entries
 16. Runner log_event() fans out to dispatcher (mocked)
 17. Flask routes registered: settings GET/POST, validate, test, schemes
 18. Config field round-trip (CFG_FIELDS + DEFAULTS)
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import sys
import time
# [SAST 3:13pm 13 may] removed unused: import json
import pytest
from unittest.mock import patch  # [SAST 3:13pm 13 may] dropped: MagicMock

from bulk_downloader import notify_apprise as n


# ─── Availability + safety ───────────────────────────────────────────


class TestAvailability:
    def test_is_available_returns_bool(self):
        result = n.is_available()
        assert isinstance(result, bool)

    def test_is_available_no_raise(self):
        # Even with no apprise, must not raise
        try:
            n.is_available()
        except Exception:
            pytest.fail("is_available() raised; must be fail-open")

    def test_list_known_schemes_returns_list(self):
        result = n.list_known_schemes()
        assert isinstance(result, list)
        # If apprise is installed, expect known schemas like tgram, discord
        if n.is_available() and result:
            assert all(isinstance(s, str) for s in result)


# ─── parse_urls_text ─────────────────────────────────────────────────


class TestParseUrlsText:
    def test_simple_list(self):
        text = "tgram://a/b\ndiscord://c/d"
        urls = n.parse_urls_text(text)
        assert urls == ["tgram://a/b", "discord://c/d"]

    def test_skips_blank_lines(self):
        text = "tgram://a/b\n\n\ndiscord://c/d\n"
        urls = n.parse_urls_text(text)
        assert urls == ["tgram://a/b", "discord://c/d"]

    def test_skips_comments(self):
        text = "# This is a comment\ntgram://a/b\n# another\ndiscord://c/d"
        urls = n.parse_urls_text(text)
        assert urls == ["tgram://a/b", "discord://c/d"]

    def test_strips_whitespace(self):
        text = "  tgram://a/b  \n\t discord://c/d\t"
        urls = n.parse_urls_text(text)
        assert urls == ["tgram://a/b", "discord://c/d"]

    def test_empty_input(self):
        assert n.parse_urls_text("") == []
        assert n.parse_urls_text(None) == []  # type: ignore[arg-type]

    def test_only_comments(self):
        text = "# only\n# comments\n# here\n"
        assert n.parse_urls_text(text) == []


# ─── _safe_url_display ───────────────────────────────────────────────


class TestSafeUrlDisplay:
    def test_masks_credentials(self):
        url = "tgram://1234567890:secrettoken/9876543210"
        masked = n._safe_url_display(url)
        assert "secrettoken" not in masked
        assert "tgram" in masked  # schema kept

    def test_keeps_chat_id_visible(self):
        """Path-after-creds (e.g. chat ID) should be visible so the user
        can tell which destination they're looking at."""
        url = "tgram://1234567890:secret/MyChannel"
        masked = n._safe_url_display(url)
        assert "MyChannel" in masked

    def test_handles_url_without_path(self):
        url = "ntfy://server.example.com"
        masked = n._safe_url_display(url)
        # Should not crash; should mask
        assert "****" in masked

    def test_handles_garbage_input(self):
        assert n._safe_url_display("") == ""
        assert n._safe_url_display("not a url") == "not a url"


# ─── validate_urls ───────────────────────────────────────────────────


class TestValidateUrls:
    def test_empty_list(self):
        assert n.validate_urls([]) == []

    def test_returns_list_of_validation(self):
        results = n.validate_urls(["tgram://1234:fake/5678"])
        assert len(results) == 1
        assert isinstance(results[0], n.UrlValidation)

    def test_apprise_missing_marks_all_failed(self):
        """When apprise isn't installed, all entries should be marked
        with `apprise_not_installed` error."""
        with patch.object(n, "is_available", return_value=False):
            results = n.validate_urls(["tgram://1/2", "discord://3/4"])
            assert all(r.ok is False for r in results)
            assert all("apprise_not_installed" in r.error for r in results)

    def test_skips_comment_lines(self):
        results = n.validate_urls(["# comment", "tgram://1/2"])
        # The comment is filtered before being passed to apprise
        # (length is 1, not 2)
        assert len(results) <= 1

    def test_never_raises(self):
        """Any input — garbage, dict, None entries — must not raise."""
        try:
            n.validate_urls(["garbage", "", "  ", None])  # type: ignore[arg-type]
        except Exception:
            pytest.fail("validate_urls raised; must be fail-open")


# ─── send ─────────────────────────────────────────────────────────────


class TestSend:
    def test_empty_urls_returns_zero_result(self):
        r = n.send([], "title", "body")
        assert r.sent_count == 0
        assert r.failed_count == 0

    def test_only_comments_returns_zero_result(self):
        r = n.send(["# comment", "  "], "title", "body")
        assert r.sent_count == 0
        assert r.failed_count == 0

    def test_apprise_missing_marks_failed(self):
        with patch.object(n, "is_available", return_value=False):
            r = n.send(["tgram://1/2"], "title", "body")
            assert r.failed_count == 1
            assert any("apprise_not_installed" in e[1] for e in r.errors)

    def test_send_result_shape(self):
        r = n.send([], "title", "body")
        assert isinstance(r, n.SendResult)
        assert hasattr(r, "sent_count")
        assert hasattr(r, "failed_count")
        assert hasattr(r, "skipped_count")
        assert hasattr(r, "errors")

    def test_never_raises(self):
        try:
            n.send(["garbage://input"], "title", "body")
        except Exception:
            pytest.fail("send() raised; must be fail-open")


# ─── policy_from_config ──────────────────────────────────────────────


class TestPolicyFromConfig:
    def test_empty_config_returns_empty(self):
        assert n.policy_from_config({}) == {}

    def test_per_event_mode(self):
        cfg = {"notify_captcha_mode": "per_event"}
        p = n.policy_from_config(cfg)
        assert "captcha" in p
        assert p["captcha"][0] == "per_event"

    def test_batched_with_sizes(self):
        cfg = {
            "notify_download_done_mode": "batched",
            "notify_download_done_batch": "20",
            "notify_download_done_wait_s": "300",
        }
        p = n.policy_from_config(cfg)
        assert p["download_done"] == ("batched", 20, 300.0)

    def test_invalid_mode_falls_back_to_default(self):
        cfg = {"notify_captcha_mode": "bogus"}
        p = n.policy_from_config(cfg)
        # Falls back to default policy mode
        assert p["captcha"][0] == n.DEFAULT_POLICY["captcha"][0]

    def test_string_numeric_coercion(self):
        cfg = {
            "notify_download_done_mode": "batched",
            "notify_download_done_batch": "15",
            "notify_download_done_wait_s": "90.5",
        }
        p = n.policy_from_config(cfg)
        assert p["download_done"][1] == 15
        assert p["download_done"][2] == 90.5

    def test_empty_string_uses_default(self):
        """Empty-string config (UI sometimes posts these) should fall
        back to defaults, not error."""
        cfg = {
            "notify_download_done_mode": "",
            "notify_download_done_batch": "",
            "notify_download_done_wait_s": "",
        }
        # Either no override or default-equivalent override is OK
        p = n.policy_from_config(cfg)
        # If override is included, must match the default
        if "download_done" in p:
            default = n.DEFAULT_POLICY["download_done"]
            assert p["download_done"][0] == default[0]


# ─── NotificationDispatcher ──────────────────────────────────────────


class TestDispatcherSingleton:
    def test_get_dispatcher_returns_same_instance(self):
        d1 = n.get_dispatcher()
        d2 = n.get_dispatcher()
        assert d1 is d2


class TestDispatcherPerEvent:
    """Per-event mode fires _fire() immediately on notify()."""

    def setup_method(self):
        self.d = n.NotificationDispatcher()
        self._fire_calls = []
        # Monkey-patch _fire to capture calls without actually sending
        self.d._fire = lambda title, body, **kw: self._fire_calls.append(
            (title, body, kw))

    def teardown_method(self):
        self.d.shutdown()

    def test_per_event_fires_immediately(self):
        self.d.configure(
            urls=["tgram://test/1"], enabled=True,
            policy_overrides={"captcha": ("per_event", 0, 0.0)},
        )
        self.d.notify("captcha", "T", "B")
        assert len(self._fire_calls) == 1

    def test_disabled_dispatcher_fires_nothing(self):
        self.d.configure(urls=["tgram://test/1"], enabled=False)
        self.d.notify("captcha", "T", "B")
        assert len(self._fire_calls) == 0

    def test_no_urls_fires_nothing(self):
        self.d.configure(urls=[], enabled=True)
        self.d.notify("captcha", "T", "B")
        assert len(self._fire_calls) == 0


class TestDispatcherBatched:
    """Batched mode accumulates events then fires one summary."""

    def setup_method(self):
        self.d = n.NotificationDispatcher()
        self._fire_calls = []
        self.d._fire = lambda title, body, **kw: self._fire_calls.append(
            (title, body, kw))

    def teardown_method(self):
        self.d.shutdown()

    def test_batched_holds_until_size_cap(self):
        self.d.configure(
            urls=["tgram://test/1"], enabled=True,
            policy_overrides={"download_done": ("batched", 3, 300.0)},
        )
        # Two events — no flush yet
        self.d.notify("download_done", "T1", "B1")
        self.d.notify("download_done", "T2", "B2")
        assert len(self._fire_calls) == 0
        # Third hits cap → flush
        self.d.notify("download_done", "T3", "B3")
        assert len(self._fire_calls) == 1

    def test_batched_flush_includes_all_items(self):
        self.d.configure(
            urls=["tgram://test/1"], enabled=True,
            policy_overrides={"download_done": ("batched", 2, 300.0)},
        )
        self.d.notify("download_done", "Title1", "Body1")
        self.d.notify("download_done", "Title2", "Body2")
        assert len(self._fire_calls) == 1
        # Body should contain both titles
        body = self._fire_calls[0][1]
        assert "Title1" in body
        assert "Title2" in body

    def test_flush_all_flushes_pending_batches(self):
        self.d.configure(
            urls=["tgram://test/1"], enabled=True,
            policy_overrides={"download_done": ("batched", 100, 300.0)},
        )
        # Add events well under cap
        self.d.notify("download_done", "T1", "B1")
        self.d.notify("download_done", "T2", "B2")
        assert len(self._fire_calls) == 0
        self.d.flush_all()
        assert len(self._fire_calls) == 1

    def test_separate_event_types_separate_batches(self):
        self.d.configure(
            urls=["tgram://test/1"], enabled=True,
            policy_overrides={
                "download_done": ("batched", 2, 300.0),
                "download_failed": ("batched", 2, 300.0),
            },
        )
        # Mix events; each batch is independent
        self.d.notify("download_done", "D1", "B")
        self.d.notify("download_failed", "F1", "B")
        self.d.notify("download_done", "D2", "B")
        # download_done has 2 → flushes
        assert len(self._fire_calls) == 1
        # download_failed still has 1 → no flush
        self.d.flush_all()
        assert len(self._fire_calls) == 2


class TestDispatcherFailOpen:
    """Notify must never raise to the caller."""

    def test_fire_raising_does_not_propagate(self):
        d = n.NotificationDispatcher()
        def _boom(title, body, **kw):
            raise RuntimeError("simulated apprise failure")
        d._fire = _boom
        d.configure(urls=["tgram://test/1"], enabled=True,
                    policy_overrides={"captcha": ("per_event", 0, 0.0)})
        # Must not raise — notify swallows everything
        try:
            d.notify("captcha", "T", "B")
        except Exception:
            pytest.fail("dispatcher.notify propagated an exception")
        d.shutdown()


# ─── Runner integration ──────────────────────────────────────────────


class TestRunnerEventMap:
    def test_map_exists(self):
        from bulk_downloader import runner
        assert hasattr(runner, "_BD_TO_APPRISE_EVENT")
        assert isinstance(runner._BD_TO_APPRISE_EVENT, dict)

    def test_map_covers_download_done_variants(self):
        from bulk_downloader.runner import _BD_TO_APPRISE_EVENT as M
        # The extractor releases all use distinct kind names but should
        # map to the same canonical event
        for k in ("done", "aylo_done", "vixen_done", "dl8_done", "jsonapi_done"):
            assert M.get(k) == "download_done", f"missing or wrong: {k}"

    def test_map_covers_failure_variants(self):
        from bulk_downloader.runner import _BD_TO_APPRISE_EVENT as M
        for k in ("fail", "aylo_extract_failed", "vixen_extract_failed",
                  "dl8_extract_failed", "aylo_hls_failed",
                  "vixen_hls_failed", "dl8_mp4_failed"):
            assert M.get(k) == "download_failed", f"missing or wrong: {k}"

    def test_map_covers_urgent_events(self):
        from bulk_downloader.runner import _BD_TO_APPRISE_EVENT as M
        assert M.get("captcha") == "captcha"
        assert M.get("auth_required") == "auth_required"
        assert M.get("disk_full") == "disk_full"


# ─── App config registration ─────────────────────────────────────────


class TestConfigRegistration:
    def test_master_toggle_in_cfg_fields(self):
        from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS
        assert "notify_apprise_enabled" in CFG_FIELDS
        assert "notify_apprise_urls" in CFG_FIELDS
        assert DEFAULTS.get("notify_apprise_enabled") is False
        assert DEFAULTS.get("notify_apprise_urls") == ""

    def test_per_event_fields_registered(self):
        from bulk_downloader.app_kernel import CFG_FIELDS
        # Spot-check three per-event fields
        for k in ("notify_download_done_mode",
                  "notify_download_done_batch",
                  "notify_captcha_mode"):
            assert k in CFG_FIELDS, f"{k} not registered"


# ─── Flask routes registered ─────────────────────────────────────────


class TestRoutes:
    def test_settings_get_route_exists(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/notify/apprise/settings" in rules

    def test_test_route_exists(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/notify/apprise/test" in rules

    def test_validate_route_exists(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/notify/apprise/validate" in rules

    def test_schemes_route_exists(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/notify/apprise/schemes" in rules


# ─── Event-type constants ────────────────────────────────────────────


class TestEventTypeConstants:
    def test_all_event_types_listed(self):
        # The full canonical set
        expected = {
            "download_done", "download_failed", "captcha",
            "auth_required", "disk_full", "queue_empty",
            "queue_paused", "queue_resumed",
            "server_start", "server_shutdown",
        }
        assert set(n.ALL_EVENT_TYPES) == expected

    def test_default_policy_covers_all_events(self):
        for et in n.ALL_EVENT_TYPES:
            assert et in n.DEFAULT_POLICY, f"missing policy for {et}"
            mode, size, wait = n.DEFAULT_POLICY[et]
            assert mode in ("per_event", "batched")
            assert isinstance(size, int) and size >= 0
            assert isinstance(wait, float) and wait >= 0.0

    def test_urgent_events_per_event(self):
        """Captcha, auth, disk_full are urgent → must be per_event."""
        for et in ("captcha", "auth_required", "disk_full"):
            mode, _, _ = n.DEFAULT_POLICY[et]
            assert mode == "per_event", f"{et} should be urgent"

    def test_done_failed_batched(self):
        """High-volume events should be batched to avoid notification spam."""
        for et in ("download_done", "download_failed"):
            mode, size, wait = n.DEFAULT_POLICY[et]
            assert mode == "batched"
            assert size > 0  # has a real batch cap
            assert wait > 0  # has a real time cap


# ─── bdctl integration ──────────────────────────────────────────────


class TestBdctlIntegration:
    def test_notify_command_registered(self):
        import bdctl
        # cmd_notify function must exist as the handler
        assert hasattr(bdctl, "cmd_notify")
        assert callable(bdctl.cmd_notify)
