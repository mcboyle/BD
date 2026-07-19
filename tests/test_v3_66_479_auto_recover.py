"""A7 -- self-recovery loop (v3.66.479).

Autonomous recovery decisions expressed as PURE functions plus one gated
orchestration entry. Mirrors the A8 posture (pure planners + injected appliers):

  * decide_cookie_refresh  -- auth expired/expiring -> refresh action.
  * backoff_delay / decide_retry -- auto-retry with bounded exponential backoff
    (delegates to retry_policy by default; injectable).
  * decide_profile_resync  -- session drift between manual + runtime -> resync
    (profile_sync.sync_manual_to_runtime backs up first -> reversible).
  * decide_health_rollback -- a post-deploy health REGRESSION (was healthy, now
    failing) -> rollback. A steady or improving probe never rolls back.
  * decide_requarantine_recover -- drift over tolerance -> requarantine; drift
    back under -> recover (rides the A1/A3 transition + the 470 cooldown hooks).

auto_recover_if_enabled is the single gated entry (toggle `auto_recover`, DEFAULT
OFF, NOT keystone-required -- recovery RESTORES known-good state, it never
overwrites a serving template with new content; the destructive sub-actions it
can trigger -- requarantine -- delegate to A3's already-keystone-gated path).
Every action is recorded to an audit log so it is reversible + traceable.

Zero-arg + injected fakes; runs under run_tests.py AND pytest.
"""
import contextlib

from bulk_downloader import auto_recover as ar
from bulk_downloader import lifecycle_automation as la


@contextlib.contextmanager
def _toggles(on_set):
    orig_read, orig_keystone = la._read_toggle, la.keystone_available
    on_keys = {la.AUTOMATION_TOGGLES[n] for n in on_set}
    la._read_toggle = lambda key: key in on_keys
    la.keystone_available = lambda: False  # prove auto_recover does NOT need it
    try:
        yield
    finally:
        la._read_toggle, la.keystone_available = orig_read, orig_keystone


# ── toggle registration ──────────────────────────────────────────────────────

def test_auto_recover_toggle_registered_not_keystone():
    assert la.AUTOMATION_TOGGLES.get("auto_recover") == "automation.auto_recover_enabled"
    assert "auto_recover" not in la.KEYSTONE_REQUIRED
    with _toggles({"auto_recover"}):
        assert la.is_enabled("auto_recover") is True


# ── backoff / retry ──────────────────────────────────────────────────────────

def test_backoff_delay_is_bounded_exponential():
    d0 = ar.backoff_delay(0, base=2.0, cap=60.0)
    d1 = ar.backoff_delay(1, base=2.0, cap=60.0)
    d2 = ar.backoff_delay(2, base=2.0, cap=60.0)
    assert d0 < d1 < d2, (d0, d1, d2)
    assert ar.backoff_delay(100, base=2.0, cap=60.0) == 60.0  # capped


def test_decide_retry_within_budget():
    r = ar.decide_retry({"attempt": 1, "failure_class": "network"},
                        should_fn=lambda fc, a: True,
                        delay_fn=lambda fc, a: 4.0)
    assert r["retry"] is True and r["delay"] == 4.0, r


def test_decide_retry_exhausted_budget():
    r = ar.decide_retry({"attempt": 9, "failure_class": "network"},
                        should_fn=lambda fc, a: False)
    assert r["retry"] is False, r


# ── cookie refresh / profile resync ──────────────────────────────────────────

def test_decide_cookie_refresh_on_expiry():
    a = ar.decide_cookie_refresh("vixen", {"expired": True})
    assert a and a["action"] == "refresh_cookies" and a["site_id"] == "vixen", a


def test_decide_cookie_refresh_noop_when_valid():
    assert ar.decide_cookie_refresh("vixen", {"expired": False}) is None


def test_decide_profile_resync_when_drifted():
    a = ar.decide_profile_resync("vixen", {"manual_newer": True})
    assert a and a["action"] == "resync_profile", a


def test_decide_profile_resync_noop_when_in_sync():
    assert ar.decide_profile_resync("vixen", {"manual_newer": False}) is None


# ── health rollback (post-deploy regression only) ────────────────────────────

def test_decide_health_rollback_on_regression():
    r = ar.decide_health_rollback({"ok": True}, {"ok": False})
    assert r["rollback"] is True, r


def test_decide_health_rollback_noop_when_steady_or_improving():
    assert ar.decide_health_rollback({"ok": True}, {"ok": True})["rollback"] is False
    assert ar.decide_health_rollback({"ok": False}, {"ok": True})["rollback"] is False
    # never-healthy -> not a regression (don't roll back onto an already-broken base)
    assert ar.decide_health_rollback({"ok": False}, {"ok": False})["rollback"] is False


# ── requarantine / recover on drift transition ───────────────────────────────

def test_decide_requarantine_over_tolerance():
    r = ar.decide_requarantine_recover(drift=0.9, tolerance=0.3, quarantined=False)
    assert r["action"] == "requarantine", r


def test_decide_recover_back_under_tolerance():
    r = ar.decide_requarantine_recover(drift=0.1, tolerance=0.3, quarantined=True)
    assert r["action"] == "recover", r


def test_decide_requarantine_noop_steady():
    assert ar.decide_requarantine_recover(drift=0.1, tolerance=0.3,
                                          quarantined=False)["action"] is None
    assert ar.decide_requarantine_recover(drift=0.9, tolerance=0.3,
                                          quarantined=True)["action"] is None


# ── gated orchestration ──────────────────────────────────────────────────────

def test_auto_recover_toggle_off_is_noop():
    out = ar.auto_recover_if_enabled({"sites": {"vixen": {"auth": {"expired": True}}}})
    assert out.get("skipped"), out


def test_auto_recover_enabled_plans_and_audits():
    ctx = {
        "sites": {
            "vixen": {"auth": {"expired": True}, "profile": {"manual_newer": True}},
        },
        "jobs": [{"id": 1, "attempt": 1, "failure_class": "network"}],
        "health": {"before": {"ok": True}, "after": {"ok": False}},
    }
    with _toggles({"auto_recover"}):
        out = ar.auto_recover_if_enabled(
            ctx, should_fn=lambda fc, a: True, delay_fn=lambda fc, a: 4.0)
    assert out["ok"] is True, out
    actions = out["plan"]["actions"]
    kinds = {a["action"] for a in actions}
    assert "refresh_cookies" in kinds, actions
    assert "resync_profile" in kinds, actions
    assert "retry" in kinds, actions
    assert "rollback" in kinds, actions
    # Every planned action is audited (reversible + traceable).
    assert len(out["audit"]) == len(actions), out


def test_auto_recover_applies_via_injected_fns():
    ctx = {"sites": {"vixen": {"auth": {"expired": True}}}}
    called = []
    apply_fns = {"refresh_cookies": lambda sid: called.append(("refresh", sid))}
    with _toggles({"auto_recover"}):
        out = ar.auto_recover_if_enabled(ctx, apply_fns=apply_fns)
    assert called == [("refresh", "vixen")], called
    assert out["applied"] >= 1, out


def test_auto_recover_failsafe_never_raises():
    # A throwing apply fn must be isolated; the orchestration still returns ok.
    ctx = {"sites": {"vixen": {"auth": {"expired": True}}}}
    apply_fns = {"refresh_cookies": lambda sid: (_ for _ in ()).throw(RuntimeError("x"))}
    with _toggles({"auto_recover"}):
        out = ar.auto_recover_if_enabled(ctx, apply_fns=apply_fns)
    assert out["ok"] is True, out
    assert out.get("apply_errors"), out
