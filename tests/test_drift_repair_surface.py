"""Item 4 -- F3.2 drift-repair sweep: status + control surface.

The daily drift->AI-repair sweep (drift_repair.scheduled_drift_repair) already
exists and lands REVIEW-ONLY drafts, but there was no way to SEE it, run it on
demand, or toggle it from the GUI, and its result was returned but never
persisted. This adds:

  * persistence of the last sweep result (counts + ts + considered site_ids,
    value-free) to a small state file, read back via read_last_run;
  * a force flag so an operator can run the sweep on demand WITHOUT flipping the
    daily automation on;
  * GET  /api/automation/drift_repair        -> {enabled, last_run, drafts_pending}
  * POST /api/automation/drift_repair/run     -> run now (honours force), summary
  * POST /api/automation/drift_repair/toggle  -> set automation.drift_repair_enabled

Backend stubs (dom_provider / ai_fn / status_fn) keep it model-free.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from bulk_downloader import drift_repair as dr


def _drafts():
    return Path(tempfile.mkdtemp(prefix="drift_drafts_"))


def _ok_status():
    return {"ok": True, "enabled": True, "provider": "ollama"}


def _repair_fn_ok(broken, working, dom, *, page_url=""):
    return {"ok": True, "repairs": [
        {"old_selector": broken[0], "new_selector": ".new-dl-link",
         "role": "row_selectors", "confidence": 80, "reasoning": "moved"}
    ], "removed": []}


# ── persistence ──────────────────────────────────────────────────────────────
def test_sweep_persists_last_run_and_reads_back():
    import bulk_downloader.global_config as gc
    import bulk_downloader.selector_drift as sd
    orig_gc, orig_sa = gc.get, sd.status_all
    state = Path(tempfile.mkdtemp()) / "drift_repair_last.json"
    try:
        gc.get = lambda k, d=None: True if k == dr.ENABLE_KEY else d
        sd.status_all = lambda: [
            {"site_id": "ctxsite", "flagged_stale": True, "last_selector": ".old-dl"}]
        dd = _drafts()
        ctx = lambda sid: {"dom_excerpt": "<html>x</html>", "page_url": "https://ctxsite/x",
                           "host": "ctxsite", "broken_selectors": [".old-dl"]}
        out = dr.scheduled_drift_repair(
            dom_provider=ctx, drafts_dir=dd, reviewed_dir=dd,
            status_fn=_ok_status, ai_fn=_repair_fn_ok, state_file=str(state))
        assert out["ran"] is True and out["considered"] == 1
        # the run was persisted
        rec = dr.read_last_run(state_file=str(state))
        assert rec is not None
        assert rec["considered"] == 1
        assert rec["repaired"] == out["repaired"]
        assert "ts" in rec and rec["ts"] > 0
        assert "ctxsite" in rec.get("site_ids", [])


    finally:
        gc.get, sd.status_all = orig_gc, orig_sa


def test_read_last_run_absent_is_none():
    state = Path(tempfile.mkdtemp()) / "nope.json"
    assert dr.read_last_run(state_file=str(state)) is None


# ── force (run-now without flipping the daily toggle) ────────────────────────
def test_force_runs_even_when_disabled():
    import bulk_downloader.global_config as gc
    import bulk_downloader.selector_drift as sd
    orig_gc, orig_sa = gc.get, sd.status_all
    try:
        gc.get = lambda k, d=None: False if k == dr.ENABLE_KEY else d  # OFF
        sd.status_all = lambda: []  # nothing stale -> considered 0
        out = dr.scheduled_drift_repair(force=True, drafts_dir=_drafts(),
                                        reviewed_dir=_drafts(),
                                        state_file=str(Path(tempfile.mkdtemp()) / "lr.json"))
        assert out["ran"] is True
        assert out["considered"] == 0
    finally:
        gc.get, sd.status_all = orig_gc, orig_sa


def test_disabled_without_force_still_noops():
    import bulk_downloader.global_config as gc
    orig = gc.get
    try:
        gc.get = lambda k, d=None: False if k == dr.ENABLE_KEY else d
        out = dr.scheduled_drift_repair()  # no force
        assert out == {"ran": False, "reason": "disabled"}
    finally:
        gc.get = orig


# ── routes ───────────────────────────────────────────────────────────────────
def test_status_route_shape(fresh_app):
    r = fresh_app.get("/api/automation/drift_repair")
    assert r.status_code == 200, r.get_data(as_text=True)
    b = r.get_json()
    assert b["ok"] is True
    assert "enabled" in b
    assert "last_run" in b           # None when never run
    assert isinstance(b["drafts_pending"], int)


def test_run_route_returns_summary(fresh_app):
    r = fresh_app.post("/api/automation/drift_repair/run", json={"force": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    b = r.get_json()
    assert b["ok"] is True
    assert "summary" in b
    assert "ran" in b["summary"]


def test_toggle_route_flips_enabled(fresh_app):
    # toggle ON, then GET status reflects it
    r1 = fresh_app.post("/api/automation/drift_repair/toggle", json={"enabled": True})
    assert r1.status_code == 200
    assert r1.get_json()["enabled"] is True
    assert fresh_app.get("/api/automation/drift_repair").get_json()["enabled"] is True
    # toggle OFF
    r2 = fresh_app.post("/api/automation/drift_repair/toggle", json={"enabled": False})
    assert r2.get_json()["enabled"] is False
    assert fresh_app.get("/api/automation/drift_repair").get_json()["enabled"] is False


# ── FE wiring (static scan: the 3 literals are spa_wired) ────────────────────
def test_spa_drift_repair_wired():
    panel = (Path(__file__).resolve().parent.parent
             / "frontend" / "src" / "components" / "DriftRepairPanel.tsx")
    src = panel.read_text(encoding="utf-8")
    assert '"/api/automation/drift_repair"' in src
    assert "/api/automation/drift_repair/run" in src
    assert "/api/automation/drift_repair/toggle" in src
