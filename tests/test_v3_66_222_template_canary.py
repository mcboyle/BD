"""Tests for the F3.3 template canary (v3.66.222).

Harness conventions: zero-arg test functions, no pytest builtins, use
tempfile.mkdtemp (no tmp_path), restore module globals in try/finally.
The canary's run_all is injected via the _run_all seam so no real
fixtures / DB / network are needed.
"""
import os
import json
import tempfile
from pathlib import Path

from bulk_downloader import template_canary as tc


# ── helpers ─────────────────────────────────────────────────────────────────
def _run(per_site):
    """Build a synthetic_tests.run_all-shaped result from
    {site: (passed, failed)}."""
    total = passed = failed = 0
    ps = {}
    for sid, (p, f) in per_site.items():
        ps[sid] = {"passed": p, "failed": f, "results": []}
        total += p + f
        passed += p
        failed += f
    return {
        "total": total, "passed": passed, "failed": failed,
        "per_site": ps,
        "pass_rate": (passed / max(1, total)) * 100.0,
    }


def _state_file():
    d = tempfile.mkdtemp(prefix="canary_")
    return Path(d) / "state.json"


# ── pure drop-detection ──────────────────────────────────────────────────────
def test_drop_triggers_alert():
    run = _run({"siteA": (1, 9)})        # 10% now
    baseline = {"siteA": 100.0}          # was 100%
    alerts = tc.compare_to_baseline(run, baseline, drop_pct=25.0)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["site"] == "siteA"
    assert a["baseline"] == 100.0
    assert a["current"] == 10.0
    assert a["drop"] == 90.0


def test_pass_is_quiet():
    run = _run({"siteA": (9, 1)})        # 90% now
    baseline = {"siteA": 100.0}          # 10pt drop, under 25pt threshold
    alerts = tc.compare_to_baseline(run, baseline, drop_pct=25.0)
    assert alerts == []


def test_first_sight_establishes_baseline_no_alert():
    run = _run({"newsite": (0, 10)})     # 0% but no prior baseline
    alerts = tc.compare_to_baseline(run, {}, drop_pct=25.0)
    assert alerts == []


def test_min_fixtures_floor_skips_noisy_single():
    # one fixture flapping to 0% should not alert (below min_fixtures=2)
    run = _run({"siteA": (0, 1)})
    baseline = {"siteA": 100.0}
    alerts = tc.compare_to_baseline(run, baseline, drop_pct=25.0,
                                    min_fixtures=2)
    assert alerts == []


def test_alerts_sorted_worst_first():
    run = _run({"a": (5, 5), "b": (1, 9)})   # a:50% b:10%
    baseline = {"a": 100.0, "b": 100.0}
    alerts = tc.compare_to_baseline(run, baseline, drop_pct=25.0)
    assert [x["site"] for x in alerts] == ["b", "a"]  # bigger drop first


# ── baseline evolution ───────────────────────────────────────────────────────
def test_alerting_site_keeps_high_baseline():
    # a regressed site must NOT lower its baseline to the broken value
    run = _run({"siteA": (1, 9)})            # 10%
    prev = {"siteA": 100.0}
    alerts = tc.compare_to_baseline(run, prev, drop_pct=25.0)
    nb = tc._next_baseline(run, prev, {a["site"] for a in alerts})
    assert nb["siteA"] == 100.0              # held, not dropped to 10


def test_recovery_resets_baseline():
    run = _run({"siteA": (10, 0)})           # back to 100%
    prev = {"siteA": 100.0}
    nb = tc._next_baseline(run, prev, set())  # no alert -> adopt current
    assert nb["siteA"] == 100.0


def test_nonalerting_site_adopts_current():
    run = _run({"siteA": (9, 1)})            # 90%, 10pt drop (no alert)
    prev = {"siteA": 100.0}
    nb = tc._next_baseline(run, prev, set())
    assert nb["siteA"] == 90.0


# ── state persistence round-trip ─────────────────────────────────────────────
def test_state_roundtrip():
    p = _state_file()
    st = {"baseline": {"x": 88.0}, "last_run": 123.0, "last_result": None}
    assert tc.save_state(st, p) is True
    back = tc.load_state(p)
    assert back["baseline"]["x"] == 88.0
    assert back["last_run"] == 123.0


def test_load_missing_file_skeleton():
    p = Path(tempfile.mkdtemp(prefix="canary_")) / "nope.json"
    st = tc.load_state(p)
    assert st == {"baseline": {}, "last_run": 0.0, "last_result": None}


def test_load_corrupt_file_failsoft():
    p = _state_file()
    p.write_text("{not json", encoding="utf-8")
    st = tc.load_state(p)
    assert st["baseline"] == {}


# ── run_canary orchestration (injected run_all) ──────────────────────────────
def test_run_canary_first_run_seeds_no_alert():
    p = _state_file()
    fake = lambda **kw: _run({"siteA": (0, 10)})  # 0% but first ever
    res = tc.run_canary(state_path=p, dispatch=False, _run_all=fake)
    assert res["alerts"] == []
    assert res["pass_rate"] == 0.0
    st = tc.load_state(p)
    assert st["baseline"]["siteA"] == 0.0         # seeded


def test_run_canary_detects_regression_second_run():
    p = _state_file()
    good = lambda **kw: _run({"siteA": (10, 0)})  # 100%
    bad = lambda **kw: _run({"siteA": (1, 9)})    # 10%
    tc.run_canary(state_path=p, dispatch=False, _run_all=good)  # baseline 100
    res = tc.run_canary(state_path=p, dispatch=False, _run_all=bad)
    assert len(res["alerts"]) == 1
    assert res["alerts"][0]["site"] == "siteA"


def test_run_canary_run_all_failure_failsoft():
    p = _state_file()
    def boom(**kw):
        raise RuntimeError("nope")
    res = tc.run_canary(state_path=p, dispatch=False, _run_all=boom)
    assert res["error"] == "run_all_failed"
    assert res["alerts"] == []


# ── scheduled gate (toggle + spacing) ────────────────────────────────────────
def test_scheduled_noop_when_disabled():
    import bulk_downloader.global_config as gc
    orig = gc.get
    try:
        gc.get = lambda k, d=None: False if k == tc.ENABLE_KEY else d
        out = tc.scheduled_canary(state_path=_state_file())
        assert out == {"ran": False, "reason": "disabled"}
    finally:
        gc.get = orig


def test_scheduled_runs_when_enabled():
    import bulk_downloader.global_config as gc
    orig_get = gc.get
    p = _state_file()
    try:
        gc.get = lambda k, d=None: True if k == tc.ENABLE_KEY else d
        # inject run_all via monkeypatching synthetic_tests on the module path
        import bulk_downloader.synthetic_tests as st
        orig_run = st.run_all
        st.run_all = lambda **kw: _run({"siteA": (10, 0)})
        try:
            out = tc.scheduled_canary(state_path=p)
        finally:
            st.run_all = orig_run
        assert out["ran"] is True
        assert out["reason"] == "ok"
    finally:
        gc.get = orig_get


def test_scheduled_min_spacing_blocks_rerun():
    import bulk_downloader.global_config as gc
    import time as _t
    orig_get = gc.get
    p = _state_file()
    try:
        gc.get = lambda k, d=None: True if k == tc.ENABLE_KEY else d
        # seed a recent last_run
        tc.save_state({"baseline": {}, "last_run": _t.time(),
                       "last_result": None}, p)
        out = tc.scheduled_canary(state_path=p, min_spacing_s=3600.0)
        assert out == {"ran": False, "reason": "min_spacing"}
    finally:
        gc.get = orig_get


# ── read surface ─────────────────────────────────────────────────────────────
def test_canary_status_shape():
    p = _state_file()
    tc.save_state({"baseline": {"a": 90.0, "b": 80.0},
                   "last_run": 42.0,
                   "last_result": {"pass_rate": 85.0, "alerts": []}}, p)
    import bulk_downloader.global_config as gc
    orig = gc.get
    try:
        gc.get = lambda k, d=None: False if k == tc.ENABLE_KEY else d
        s = tc.canary_status(p)
        assert s["enabled"] is False
        assert s["last_run"] == 42.0
        assert s["baseline_sites"] == 2
        assert s["last_result"]["pass_rate"] == 85.0
    finally:
        gc.get = orig


def test_canary_status_no_state_failsoft():
    p = Path(tempfile.mkdtemp(prefix="canary_")) / "absent.json"
    s = tc.canary_status(p)
    assert s["baseline_sites"] == 0
    assert s["last_result"] is None
