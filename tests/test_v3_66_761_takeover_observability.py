"""v3.66.761 -- MOD-1 A-5c: takeover observability + timeout lifecycle audit.

Completes Architecture A. Four legs:

  * bd_takeover_active gauge  -- open takeover channels right now (== active
    remote solve sessions). Reads takeover.active_channel_count().
  * bd_takeover_total counter -- cumulative takeover channels opened since boot
    (monotonic; a viewer reconnect that re-opens the channel counts as a new
    stream, which is the churn signal we want).
  * session age in /pending   -- each pending item carries age_s (now -
    detected_at) and, for a `solving` session, idle_s (now - last_input_at) so
    the operator panel can show how long a challenge has waited / been idle.
  * timeout lifecycle audit   -- the A-5b sweep reaps idle/expired `solving`
    sessions; A-5c audits each such reap as a distinct "timeout" lifecycle
    event (session-summary granularity), separate from an operator "dismissed",
    so the full lifecycle is start / resolved / dismissed / timeout.

RED-first on pristine v3.66.760 (metric block / fields / audit action ABSENT).
"""
from __future__ import annotations

import time

import pytest


class _Reset:
    def setup_method(self):
        from bulk_downloader import captcha_relay, takeover
        captcha_relay._reset_for_tests()
        with takeover._channels_lock:
            for sid in list(takeover._channels):
                takeover._channels[sid].closed.set()
            takeover._channels.clear()
        takeover.reset_takeover_total()

    def teardown_method(self):
        self.setup_method()


# ════════════════════════════════════════════════════════════════════
#  bd_takeover_total counter (takeover module)
# ════════════════════════════════════════════════════════════════════

class TestTakeoverTotal(_Reset):
    def test_total_starts_zero(self):
        from bulk_downloader import takeover
        assert takeover.takeover_total() == 0

    def test_open_channel_increments_total(self):
        from bulk_downloader import takeover
        takeover.open_channel("s1")
        takeover.open_channel("s2")
        assert takeover.takeover_total() == 2

    def test_reopen_same_sid_after_close_counts_again(self):
        from bulk_downloader import takeover
        takeover.open_channel("s1")
        takeover.close_channel("s1")
        takeover.open_channel("s1")  # reconnect -> a new stream
        assert takeover.takeover_total() == 2

    def test_idempotent_open_does_not_double_count(self):
        from bulk_downloader import takeover
        takeover.open_channel("s1")
        takeover.open_channel("s1")  # already open -> same channel, no increment
        assert takeover.takeover_total() == 1

    def test_close_does_not_decrement_total(self):
        from bulk_downloader import takeover
        takeover.open_channel("s1")
        takeover.close_channel("s1")
        assert takeover.takeover_total() == 1  # monotonic
        assert takeover.active_channel_count() == 0


# ════════════════════════════════════════════════════════════════════
#  /metrics exposition
# ════════════════════════════════════════════════════════════════════

class TestMetricsExposition(_Reset):
    def test_render_emits_takeover_gauge_and_counter(self):
        from bulk_downloader import metrics_prom, takeover
        takeover.open_channel("s1")
        takeover.open_channel("s2")
        takeover.close_channel("s2")
        doc = metrics_prom.render()
        assert "bd_takeover_active" in doc
        assert "bd_takeover_total" in doc
        # gauge reflects the one still-open channel; counter the two opened
        lines = doc.splitlines()
        active = [l for l in lines if l.startswith("bd_takeover_active ")]
        total = [l for l in lines if l.startswith("bd_takeover_total ")]
        assert active and active[0].split()[-1] == "1"
        assert total and total[0].split()[-1] == "2"

    def test_metric_help_types_declared(self):
        from bulk_downloader import metrics_prom
        doc = metrics_prom.render()
        assert "# TYPE bd_takeover_active gauge" in doc
        assert "# TYPE bd_takeover_total counter" in doc


# ════════════════════════════════════════════════════════════════════
#  session age in /pending
# ════════════════════════════════════════════════════════════════════

class TestPendingAge(_Reset):
    def test_pending_item_carries_age_s(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        with captcha_relay._lock:
            captcha_relay._pending["https://x.com/1"].detected_at = time.time() - 42
        p = captcha_relay.list_pending()[0]
        assert "age_s" in p
        assert 40 <= p["age_s"] <= 60

    def test_solving_item_carries_idle_s(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        with captcha_relay._lock:
            p = captcha_relay._pending["https://x.com/1"]
            p.status = "solving"
            p.solve_session_id = "s1"
            p.last_input_at = time.time() - 12
        item = captcha_relay.list_pending()[0]
        assert "idle_s" in item and 10 <= item["idle_s"] <= 30

    def test_non_solving_idle_s_is_none(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        p = captcha_relay.list_pending()[0]
        assert p.get("idle_s") is None  # only meaningful while solving

    def test_age_present_on_api_pending(self):
        # end-to-end through the blueprint
        import os
        os.environ["BD_DISABLE_KEEPALIVE"] = "1"
        from bulk_downloader import captcha_relay
        from bulk_downloader.app import app
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        c = app.test_client()
        r = c.get("/api/captcha/pending")
        assert r.status_code == 200
        items = r.get_json()["pending"]
        assert items and "age_s" in items[0]


# ════════════════════════════════════════════════════════════════════
#  timeout lifecycle audit (distinct from operator dismiss)
# ════════════════════════════════════════════════════════════════════

class TestTimeoutAudit(_Reset):
    def _capture_audit(self, monkeypatch):
        from bulk_downloader import audit
        events = []
        monkeypatch.setattr(
            audit, "audit_log",
            lambda category, action, target, **kw: events.append((category, action, target, kw)))
        return events

    def test_idle_reap_audits_timeout_not_dismissed(self, monkeypatch):
        from bulk_downloader import captcha_relay, takeover
        events = self._capture_audit(monkeypatch)
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        with captcha_relay._lock:
            p = captcha_relay._pending["https://x.com/1"]
            p.status = "solving"
            p.solve_session_id = "s1"
            p.last_input_at = time.time() - captcha_relay._takeover_idle_timeout_s() - 1
        takeover.open_channel("s1")
        captcha_relay.sweep_report()
        actions = [(cat, act, tgt) for (cat, act, tgt, _kw) in events if cat == "takeover"]
        assert ("takeover", "timeout", "s1") in actions, actions
        assert not any(act == "dismissed" for (_c, act, _t) in actions), \
            "a sweep timeout must not masquerade as an operator dismiss"

    def test_operator_dismiss_still_audits_dismissed(self, monkeypatch):
        from bulk_downloader import captcha_relay
        events = self._capture_audit(monkeypatch)
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        captcha_relay.mark_dismissed("https://x.com/1")
        actions = [act for (cat, act, _t, _kw) in events if cat == "takeover"]
        assert "dismissed" in actions and "timeout" not in actions

    def test_orphan_reap_not_audited_as_timeout(self, monkeypatch):
        # An orphan-channel reap is not a session timeout; it must not emit a
        # per-session "timeout" (no tracked session it belongs to).
        from bulk_downloader import captcha_relay, takeover
        events = self._capture_audit(monkeypatch)
        takeover.open_channel("ghost")
        captcha_relay.sweep_report()
        assert not any(act == "timeout" for (_c, act, _t, _kw) in events)
