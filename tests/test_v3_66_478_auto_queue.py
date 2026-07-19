"""A8 -- queue self-management (v3.66.478).

Auto-dedup, auto-prioritize, and auto-pause/resume-on-rate-limit, expressed as
PURE planners plus one gated orchestration entry. Mirrors the A4/A5 posture:

  * Every planner is pure + deterministic (no DB, no I/O) so it is unit-testable
    and the controller (A9) / operator can apply the plan through existing
    primitives (app_dedup remove, queue_priority ranking, runner pause/resume).
  * manage_queue_if_enabled is gated by the `auto_queue` toggle (DEFAULT OFF).
    With the toggle off it is a no-op -- byte-identical behaviour.
  * `auto_queue` is NOT keystone-required: queue ops (drop a dup, reorder,
    pause/resume a site) are reversible and never overwrite a serving template,
    so the keystone gate (which guards template overwrites) does not apply.

Two layers tested:
  * plan_dedup / plan_prioritize / plan_pause_resume -- pure planning.
  * manage_queue_if_enabled -- gated orchestration (off -> skip; on -> plan;
    apply_fns -> applied counts).

Zero-arg + injected fakes; runs under run_tests.py AND pytest.
"""
import contextlib

from bulk_downloader import auto_queue as aq
from bulk_downloader import lifecycle_automation as la


@contextlib.contextmanager
def _toggles(on_set):
    """Turn the named automation toggles ON for the duration. Keystone forced
    absent to prove auto_queue does NOT depend on it."""
    orig_read, orig_keystone = la._read_toggle, la.keystone_available
    on_keys = {la.AUTOMATION_TOGGLES[n] for n in on_set}
    la._read_toggle = lambda key: key in on_keys
    la.keystone_available = lambda: False
    try:
        yield
    finally:
        la._read_toggle, la.keystone_available = orig_read, orig_keystone


def _row(hid, url, ts="2026-06-28T00:00:00", site_id="vixen"):
    return {"history_id": hid, "url": url, "ts": ts, "site_id": site_id}


# ── toggle registration ──────────────────────────────────────────────────────

def test_auto_queue_toggle_registered_not_keystone():
    assert "auto_queue" in la.AUTOMATION_TOGGLES, la.AUTOMATION_TOGGLES
    assert la.AUTOMATION_TOGGLES["auto_queue"] == "automation.auto_queue_enabled"
    assert "auto_queue" not in la.KEYSTONE_REQUIRED, la.KEYSTONE_REQUIRED
    # Enabled purely by its toggle even with the keystone absent.
    with _toggles({"auto_queue"}):
        assert la.is_enabled("auto_queue") is True


# ── plan_dedup ───────────────────────────────────────────────────────────────

def test_plan_dedup_drops_duplicate_urls():
    rows = [_row(1, "https://site/a"), _row(2, "https://site/a"),
            _row(3, "https://site/b")]
    p = aq.plan_dedup(rows)
    assert set(p["keep"]) == {1, 3}, p
    assert p["drop"] == [2], p


def test_plan_dedup_keeps_distinct_urls_untouched():
    rows = [_row(1, "https://site/a"), _row(2, "https://site/b")]
    p = aq.plan_dedup(rows)
    assert p["drop"] == [], p
    assert set(p["keep"]) == {1, 2}, p


def test_plan_dedup_keeps_earliest_of_a_group():
    # Out-of-order ids; the earliest history_id in each url group is the keeper.
    rows = [_row(5, "https://site/x"), _row(2, "https://site/x"),
            _row(9, "https://site/x")]
    p = aq.plan_dedup(rows)
    assert p["keep"] == [2], p
    assert sorted(p["drop"]) == [5, 9], p


def test_plan_dedup_normalizes_fragment_and_trailing_slash():
    rows = [_row(1, "https://Site/a/"), _row(2, "https://site/a#frag")]
    p = aq.plan_dedup(rows)
    # Same normalized URL -> one keeper, one drop.
    assert len(p["keep"]) == 1 and len(p["drop"]) == 1, p


# ── plan_prioritize ──────────────────────────────────────────────────────────

def test_plan_prioritize_orders_by_descending_score():
    rows = [_row(1, "u1"), _row(2, "u2"), _row(3, "u3")]
    scores = {1: 10.0, 2: 99.0, 3: 50.0}
    order = aq.plan_prioritize(rows, rank_fn=lambda r: scores[r["history_id"]])
    assert order == [2, 3, 1], order


def test_plan_prioritize_stable_on_ties():
    rows = [_row(1, "u1"), _row(2, "u2")]
    order = aq.plan_prioritize(rows, rank_fn=lambda r: 1.0)
    assert order == [1, 2], order  # input order preserved on a tie


# ── plan_pause_resume ────────────────────────────────────────────────────────

def test_plan_pause_resume_pauses_rate_limited():
    states = {"vixen": {"rate_limited": True, "paused": False}}
    p = aq.plan_pause_resume(states)
    assert p["pause"] == ["vixen"], p
    assert p["resume"] == [], p


def test_plan_pause_resume_resumes_recovered():
    states = {"vixen": {"rate_limited": False, "paused": True}}
    p = aq.plan_pause_resume(states)
    assert p["pause"] == [], p
    assert p["resume"] == ["vixen"], p


def test_plan_pause_resume_noop_when_steady():
    states = {"a": {"rate_limited": False, "paused": False},
              "b": {"rate_limited": True, "paused": True}}
    p = aq.plan_pause_resume(states)
    assert p["pause"] == [] and p["resume"] == [], p


# ── manage_queue_if_enabled (gated orchestration) ────────────────────────────

def test_manage_queue_toggle_off_is_noop():
    rows = [_row(1, "https://site/a"), _row(2, "https://site/a")]
    states = {"vixen": {"rate_limited": True, "paused": False}}
    out = aq.manage_queue_if_enabled(rows, states)
    assert out.get("skipped"), out
    assert "plan" not in out or out.get("plan") is None, out


def test_manage_queue_enabled_produces_plan():
    rows = [_row(1, "https://site/a"), _row(2, "https://site/a"),
            _row(3, "https://site/b")]
    states = {"vixen": {"rate_limited": True, "paused": False}}
    with _toggles({"auto_queue"}):
        out = aq.manage_queue_if_enabled(
            rows, states, rank_fn=lambda r: r["history_id"])
    assert out["ok"] is True, out
    plan = out["plan"]
    assert plan["dedup"]["drop"] == [2], plan
    assert plan["pause"] == ["vixen"], plan
    assert plan["order"], plan


def test_manage_queue_applies_via_injected_fns():
    rows = [_row(1, "https://site/a"), _row(2, "https://site/a")]
    states = {"vixen": {"rate_limited": True, "paused": False}}
    calls = {"removed": [], "paused": [], "resumed": [], "reordered": None}
    apply_fns = {
        "remove": lambda ids: calls["removed"].extend(ids) or len(ids),
        "pause": lambda sid: calls["paused"].append(sid),
        "resume": lambda sid: calls["resumed"].append(sid),
        "reorder": lambda order: calls.__setitem__("reordered", list(order)),
    }
    with _toggles({"auto_queue"}):
        out = aq.manage_queue_if_enabled(
            rows, states, rank_fn=lambda r: r["history_id"], apply_fns=apply_fns)
    assert out["ok"] is True, out
    assert calls["removed"] == [2], calls
    assert calls["paused"] == ["vixen"], calls
    assert calls["reordered"] is not None, calls
    assert out["applied"]["removed"] == 1, out


def test_manage_queue_failsafe_swallows_planner_error():
    # A throwing rank_fn must not blow up the whole orchestration.
    rows = [_row(1, "https://site/a")]
    with _toggles({"auto_queue"}):
        out = aq.manage_queue_if_enabled(
            rows, {}, rank_fn=lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
    assert out["ok"] is True, out
    # Dedup still planned; order degrades to input order on rank error.
    assert "plan" in out, out
