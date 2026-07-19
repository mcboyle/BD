"""A6 -- continuous CI / auto-regression + snapshot + rollback (v3.66.480).

On every change, take a snapshot baseline, run the sandbox regression band, and
on a regression assemble a rollback artifact (and auto-rollback when a rollback
applier is injected). PURE orchestration with injected fns; the BINDING
full-suite gate stays ``capture.sh`` on stash -- the in-sandbox band here is
ADVISORY, and the verdict says so explicitly.

  * snapshot_baseline -- collect handles from injected snapshot fns
    (baselines_snapshot / route_map_snapshot / runner_api_snapshot live).
  * run_regression    -- run the band via an injected runner -> {regressed,...}.
  * make_rollback_artifact -- assemble a restorable artifact from the baseline.
  * auto_ci_cycle     -- the gated entry (toggle `auto_ci`, DEFAULT OFF, NOT
    keystone-required: snapshot/rollback RESTORE, they never overwrite a serving
    template with new content).

Fail-safe: a throwing runner is treated CONSERVATIVELY (cannot prove green ->
regressed=True, staged for attention), never raised.

Zero-arg + injected fakes; runs under run_tests.py AND pytest.
"""
import contextlib

from bulk_downloader import auto_ci as ci
from bulk_downloader import lifecycle_automation as la


@contextlib.contextmanager
def _toggles(on_set):
    orig_read, orig_keystone = la._read_toggle, la.keystone_available
    on_keys = {la.AUTOMATION_TOGGLES[n] for n in on_set}
    la._read_toggle = lambda key: key in on_keys
    la.keystone_available = lambda: False
    try:
        yield
    finally:
        la._read_toggle, la.keystone_available = orig_read, orig_keystone


def test_auto_ci_toggle_registered_not_keystone():
    assert la.AUTOMATION_TOGGLES.get("auto_ci") == "automation.auto_ci_enabled"
    assert "auto_ci" not in la.KEYSTONE_REQUIRED
    with _toggles({"auto_ci"}):
        assert la.is_enabled("auto_ci") is True


# ── snapshot ─────────────────────────────────────────────────────────────────

def test_snapshot_baseline_collects_handles():
    snaps = {"routes": lambda: "r@1", "api": lambda: "a@1"}
    base = ci.snapshot_baseline(snaps)
    assert base["routes"] == "r@1" and base["api"] == "a@1", base


def test_snapshot_baseline_isolates_a_failing_snapshot():
    snaps = {"ok": lambda: "v", "bad": lambda: (_ for _ in ()).throw(RuntimeError())}
    base = ci.snapshot_baseline(snaps)
    assert base["ok"] == "v", base
    assert base["bad"] is None, base  # failed snapshot recorded as None, not raised


# ── regression ───────────────────────────────────────────────────────────────

def test_run_regression_clean():
    r = ci.run_regression(run_fn=lambda: {"passed": 100, "failed": 0, "failures": []})
    assert r["regressed"] is False, r
    assert r["advisory"] is True, r  # binding gate is stash, this is advisory


def test_run_regression_detects_failures():
    r = ci.run_regression(run_fn=lambda: {"passed": 98, "failed": 2,
                                          "failures": ["t_a", "t_b"]})
    assert r["regressed"] is True, r
    assert r["failures"] == ["t_a", "t_b"], r


def test_run_regression_failsafe_is_conservative():
    r = ci.run_regression(run_fn=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert r["regressed"] is True, r  # cannot prove green -> treat as regressed
    assert r.get("error"), r


# ── rollback artifact ────────────────────────────────────────────────────────

def test_make_rollback_artifact_carries_baseline_and_failures():
    art = ci.make_rollback_artifact({"routes": "r@1"},
                                    {"regressed": True, "failures": ["t_a"]})
    assert art["baseline"] == {"routes": "r@1"}, art
    assert art["failures"] == ["t_a"], art
    assert art["restorable"] is True, art


# ── gated cycle ──────────────────────────────────────────────────────────────

def test_auto_ci_cycle_off_is_noop():
    out = ci.auto_ci_cycle({"change": "x"},
                           snapshot_fns={"r": lambda: "r@1"},
                           run_fn=lambda: {"passed": 1, "failed": 0, "failures": []})
    assert out.get("skipped"), out


def test_auto_ci_cycle_clean_change_no_rollback():
    with _toggles({"auto_ci"}):
        out = ci.auto_ci_cycle(
            {"change": "x"},
            snapshot_fns={"r": lambda: "r@1"},
            run_fn=lambda: {"passed": 10, "failed": 0, "failures": []})
    assert out["ok"] is True and out["regressed"] is False, out
    assert out.get("rolled_back") is False, out
    assert out["binding"] == "stash:capture.sh", out  # advisory-only marker


def test_auto_ci_cycle_regression_stages_artifact_and_autorolls():
    rolled = []
    with _toggles({"auto_ci"}):
        out = ci.auto_ci_cycle(
            {"change": "x"},
            snapshot_fns={"r": lambda: "r@1"},
            run_fn=lambda: {"passed": 8, "failed": 2, "failures": ["t_a", "t_b"]},
            rollback_fn=lambda art: rolled.append(art))
    assert out["regressed"] is True, out
    assert out["artifact"]["failures"] == ["t_a", "t_b"], out
    assert out["rolled_back"] is True, out
    assert len(rolled) == 1, rolled


def test_auto_ci_cycle_regression_without_rollback_fn_stages_only():
    with _toggles({"auto_ci"}):
        out = ci.auto_ci_cycle(
            {"change": "x"},
            snapshot_fns={"r": lambda: "r@1"},
            run_fn=lambda: {"passed": 8, "failed": 2, "failures": ["t_a"]})
    assert out["regressed"] is True, out
    assert out["artifact"]["restorable"] is True, out
    assert out["rolled_back"] is False, out  # staged for the operator, not auto-rolled
