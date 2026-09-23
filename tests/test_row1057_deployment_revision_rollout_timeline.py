"""Row 1057 -- Deployment Lifecycle & Revision Rollout Timeline.

Lifecycle transitions, rollout events, the unified activity timeline and the
deploy REST endpoint -- fed by the one deploy signal the app observes (a new
running revision at boot) and stored in the app DB.

Rebuild (N6-A / P2-B refutes on cf3203e9):
  E1 nothing produced a deployment and /api/deploy/timeline was never
     registered -> the boot path records the running revision and the real
     app serves the route (test_the_real_app_boot_records_*).
  E2 record_event overwrote the lifecycle state -> informational events
     append only (test_informational_event_does_not_transition).
  E3 advance_state's transition and the env filter were unpinned.
  E4 in-process singleton -> history survives a module reload ("restart").

Base invariant: RED fails with AssertionError, never an ImportError at
collection.
"""
from __future__ import annotations

import importlib
import time

import pytest

from bulk_downloader import timeline

BD_GATE_SCOPE = "repo-wide"


def _mod():
    try:
        from bulk_downloader import deployment_timeline
        return deployment_timeline
    except (ImportError, ModuleNotFoundError):
        return None


@pytest.fixture
def tracker(clean_workdir, inmemory_sqlite):
    mod = _mod()
    assert mod is not None, "Row 1057 capability missing: bulk_downloader.deployment_timeline module does not exist"
    return mod.DeploymentRolloutTimeline()


def _states(dep):
    return [e.status for e in dep.events]


# ── capability ───────────────────────────────────────────────────────────

def test_deployment_timeline_integrated_in_timeline_subsystem():
    assert hasattr(timeline, "_entries_from_deployment_rollouts"), (
        "Row 1057 capability missing: bulk_downloader.timeline has no _entries_from_deployment_rollouts"
    )


def test_deployment_timeline_module_available():
    mod = _mod()
    assert mod is not None, "Row 1057 capability missing: bulk_downloader.deployment_timeline module does not exist"
    for name in ("DeploymentRolloutTimeline", "DeploymentState", "RolloutEventType",
                 "get_deployment_timeline", "record_running_revision", "register_routes"):
        assert hasattr(mod, name), f"{name} missing"


# ── E1: fed by the real boot path, exposed by the real app ──────────────

def test_the_real_app_boot_records_the_running_revision_and_serves_it(fresh_app):
    from bulk_downloader import __version__
    resp = fresh_app.get("/api/deploy/timeline")
    assert resp.status_code == 200, (
        f"row1057 E1: /api/deploy/timeline is not registered on the app (HTTP {resp.status_code})")
    data = resp.get_json()
    active = data["active_deployment"]
    assert active is not None, "row1057 E1: booting the app recorded no deployment"
    assert active["revision"].startswith(__version__) and active["state"] == "active", active
    assert [e["kind"] for e in data["timeline"]][:1] == ["rollout_completed"], data["timeline"]
    # a second request (same process, same revision) records nothing new
    again = fresh_app.get("/api/deploy/timeline").get_json()
    assert len(again["deployments"]) == 1


def test_boot_revision_supersedes_and_detects_rollback(tracker):
    a = tracker.record_boot_revision("3.66.1+aaa")
    assert tracker.record_boot_revision("3.66.1+aaa") is None, "same-revision restart must record nothing"
    b = tracker.record_boot_revision("3.66.2+bbb")
    assert tracker.get_deployment(a.deployment_id).state == "superseded"
    assert tracker.get_active_deployment().deployment_id == b.deployment_id
    c = tracker.record_boot_revision("3.66.1+aaa")          # an earlier revision again
    assert tracker.get_deployment(b.deployment_id).state == "rolled_back"
    assert tracker.get_active_deployment().deployment_id == c.deployment_id
    assert len(tracker.list_deployments()) == 3


def test_record_running_revision_uses_version_and_never_raises(tracker, clean_workdir, monkeypatch):
    mod = _mod()
    from bulk_downloader import __version__
    dep = mod.record_running_revision(str(clean_workdir))
    assert dep is not None and dep.revision.startswith(__version__) and dep.metadata["version"] == __version__

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(mod.DeploymentRolloutTimeline, "record_boot_revision", boom)
    assert mod.record_running_revision(str(clean_workdir)) is None


# ── E2 / E3: only advance_state transitions; transitions and env pinned ─

def test_informational_event_does_not_transition(tracker):
    mod = _mod()
    dep = tracker.create_deployment("v1", target_env="production")
    tracker.advance_state(dep.deployment_id, mod.DeploymentState.ROLLING_OUT.value, progress_percent=40)
    tracker.record_event(dep.deployment_id, mod.RolloutEventType.HEALTH_CHECK_PASSED.value,
                         status="canary", message="probe ok", progress_percent=55)
    got = tracker.get_deployment(dep.deployment_id)
    assert got.state == "rolling_out", f"row1057 E2: a health-check event changed the state to {got.state!r}"
    assert got.progress_percent == 55.0
    assert [e.event_type for e in got.events][-1] == "health_check_passed"


def test_advance_state_transitions_and_emits_the_matching_event(tracker):
    dep = tracker.create_deployment("v2")
    assert dep.state == "pending" and _states(dep) == ["pending"]
    for new, kind in (("staging", "stage_updated"), ("canary", "stage_updated"),
                      ("active", "rollout_completed")):
        got = tracker.advance_state(dep.deployment_id, new)
        assert got.state == new, f"row1057 E3: advance_state did not transition to {new!r}: {got.state!r}"
        assert got.events[-1].event_type == kind
    with pytest.raises(ValueError):
        tracker.advance_state(dep.deployment_id, "sideways")
    with pytest.raises(KeyError):
        tracker.advance_state("dep-missing", "active")
    with pytest.raises(KeyError):
        tracker.record_event("dep-missing", "progress_updated", "x", "m")


def test_active_deployment_is_per_env(tracker):
    staging = tracker.create_deployment("v3", target_env="staging")
    tracker.advance_state(staging.deployment_id, "active")
    assert tracker.get_active_deployment("production") is None, "row1057 E3: env filter ignored"
    assert tracker.get_active_deployment("staging").deployment_id == staging.deployment_id


def test_progress_is_clamped(tracker):
    dep = tracker.create_deployment("v4")
    assert tracker.advance_state(dep.deployment_id, "rolling_out", progress_percent=150).progress_percent == 100.0
    assert tracker.advance_state(dep.deployment_id, "rolling_out", progress_percent=-5).progress_percent == 0.0


def test_list_deployments_filter_and_limit(tracker):
    for i in range(3):
        tracker.create_deployment(f"p{i}", target_env="production", created_at=1000.0 + i)
    tracker.create_deployment("s0", target_env="staging", created_at=2000.0)
    assert [d.revision for d in tracker.list_deployments("production", limit=2)] == ["p2", "p1"]
    assert len(tracker.list_deployments()) == 4


# ── E4: durable across a restart ────────────────────────────────────────

def test_history_survives_a_module_reload(tracker):
    mod = _mod()
    dep = tracker.create_deployment("3.66.9+ccc")
    tracker.advance_state(dep.deployment_id, "active")
    reloaded = importlib.reload(mod)
    got = reloaded.get_deployment_timeline().get_deployment(dep.deployment_id)
    assert got is not None and got.state == "active", (
        "row1057 E4: the rollout history did not survive a restart of the module")


# ── timeline entries and the unified activity feed ──────────────────────

def test_timeline_entries_shape_severity_and_since(tracker):
    dep = tracker.create_deployment("v5")
    tracker.advance_state(dep.deployment_id, "canary")
    tracker.advance_state(dep.deployment_id, "failed")
    entries = tracker.get_timeline_entries(since_ts=0.0)
    assert {e["source"] for e in entries} == {"deployment_rollout"}
    assert set(entries[0]) == {"ts", "source", "kind", "severity", "title", "description", "link"}
    by_kind = {e["kind"]: e["severity"] for e in entries}
    assert by_kind["rollout_failed"] == "error" and by_kind["deploy_initiated"] == "info"
    assert "warn" in [e["severity"] for e in entries]
    assert tracker.get_timeline_entries(since_ts=time.time() + 60) == []


def test_unified_activity_timeline_includes_deployment_rollouts(tracker):
    now = time.time()
    dep = tracker.create_deployment("3.66.7+unified")
    tracker.advance_state(dep.deployment_id, "active")
    merged = timeline.merged_timeline(since_ts=now - 60.0, limit_total=50)
    assert any(e.get("source") == "deployment_rollout" and "3.66.7+unified" in e.get("title", "")
               for e in merged)
    assert "deployment_rollout" in timeline.summary(since_hours=1).get("by_source", {})


def test_timeline_source_error_isolation(monkeypatch):
    mod = _mod()

    def broken():
        raise RuntimeError("broken")
    monkeypatch.setattr(mod, "get_deployment_timeline", broken)
    assert timeline._entries_from_deployment_rollouts(0.0, 10) == []


def test_api_rejects_non_numeric_params(fresh_app):
    resp = fresh_app.get("/api/deploy/timeline?since_ts=abc")
    assert resp.status_code == 400


# ── SPA wiring ───────────────────────────────────────────────────────────
def test_deploy_timeline_route_is_wired_to_an_spa_control():
    """GET /api/deploy/timeline is operator-facing, so the ratchet (unwired_operator_endpoints,
    counted on the regenerated ROUTE_INDEX) needs an SPA caller for it, found by the same
    scanner the route index uses. Positive control: an existing wired route is seen; an
    unwired sibling under /api/deploy/ is not (the scanner does not credit a path family)."""
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "gui_parity_inventory_row1057", root / "tools" / "gui_parity_inventory.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    _eps, method_eps = g._spa_wiring(root)
    assert ("GET", "/api/provenance/digest") in method_eps, "scanner sees no SPA calls at all"
    assert ("GET", "/api/deploy/systemd") not in method_eps
    assert ("GET", "/api/deploy/timeline") in method_eps, "row1057: no SPA control calls GET /api/deploy/timeline"
