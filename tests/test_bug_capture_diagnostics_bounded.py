"""BUG (pre-existing, unmasked by moving many captures in) -- GET
/api/data/capture_diagnostics -> collect_capture_diagnostics -> capture_diagnostics
.collect() built a FULL template (open wacz + player recognition) for EVERY .wacz
under the capture dirs, unbounded. On a large capture store this is a multi-minute
single-core walk that hangs any route-scanning test (test_secret_display_never GETs
every route) and floods /tmp with a build_template temp dir per capture.

Fix: collect(..., limit=N) diagnoses at most N .wacz newest-first; the endpoint
passes limit=200. We stub diagnose() so the test exercises the BOUND itself
(how many captures get the expensive treatment) without parsing real WACZ.
"""
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import capture_diagnostics as CD  # type: ignore


def _store(n_wacz=20):
    root = Path(tempfile.mkdtemp(prefix="capdiag_"))
    cap = root / "captures"
    cap.mkdir(parents=True, exist_ok=True)
    for i in range(n_wacz):
        (cap / f"cap_{i:03d}.wacz").write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # empty zip
    return root, n_wacz


def _run_with_stub(root, **kw):
    calls = []
    orig_diag, orig_row = CD.diagnose, CD._row
    try:
        CD.diagnose = lambda ap, **k: (calls.append(ap) or ("MOCK", "stub"))
        CD._row = lambda rel, v: {"path": rel, "verdict": "MOCK"}
        res = CD.collect(str(root), **kw)
    finally:
        CD.diagnose, CD._row = orig_diag, orig_row
    return res, calls


def test_collect_bounds_the_expensive_diagnose_loop():
    root, n = _store(20)
    res, calls = _run_with_stub(root, limit=5)
    assert len(calls) == 5, f"diagnose called {len(calls)}x, want 5 (bound not applied)"
    assert res.get("skipped_wacz") == n - 5, f"expected 15 skipped, got {res.get('skipped_wacz')}"
    assert len(res["rows"]) == 5, f"expected 5 rows, got {len(res['rows'])}"


def test_collect_unbounded_attempts_all():
    root, n = _store(6)
    res, calls = _run_with_stub(root)          # no limit
    assert len(calls) == 6, f"unbounded must attempt all; called {len(calls)}x"
    assert res.get("skipped_wacz", 0) == 0


def test_limit_larger_than_store_skips_nothing():
    root, n = _store(4)
    res, calls = _run_with_stub(root, limit=100)
    assert len(calls) == 4
    assert res.get("skipped_wacz") == 0



import replay_validator as RV  # type: ignore


def _run_rv_stub(root, **kw):
    calls = []
    orig_lc, orig_vr = RV._WD.load_capture, RV.validate_replay
    try:
        RV._WD.load_capture = lambda pth, **k: (calls.append(str(pth)) or {"_stub": True})
        RV.validate_replay = lambda cap: {"ok": True, "errors": [], "warnings": [], "stats": {"events": 0}}
        res = RV.collect(str(root), **kw)
    finally:
        RV._WD.load_capture, RV.validate_replay = orig_lc, orig_vr
    return res, calls


def test_replay_collect_is_also_bounded():
    root, n = _store(20)
    res, calls = _run_rv_stub(root, limit=5)
    assert len(calls) == 5, f"replay load_capture called {len(calls)}x, want 5"
    assert res.get("skipped_wacz") == n - 5, f"expected 15 skipped, got {res.get('skipped_wacz')}"


def test_replay_collect_unbounded_attempts_all():
    root, n = _store(6)
    res, calls = _run_rv_stub(root)
    assert len(calls) == 6
    assert res.get("skipped_wacz", 0) == 0



def test_collect_respects_time_budget():
    # a wall-time budget must stop the loop even when `limit` would allow more
    import time as _t
    root, n = _store(20)
    calls = []
    orig_diag, orig_row = CD.diagnose, CD._row
    try:
        CD.diagnose = lambda ap, **k: (calls.append(ap), _t.sleep(0.15), ("MOCK", "s"))[-1]
        CD._row = lambda rel, v: {"path": rel, "verdict": "MOCK"}
        res = CD.collect(str(root), limit=100, budget_s=0.4)
    finally:
        CD.diagnose, CD._row = orig_diag, orig_row
    assert res.get("budget_exhausted") is True, res.keys()
    assert 1 <= len(calls) <= 5, f"budget did not bound the loop: {len(calls)} diagnosed"
    assert len(calls) + res["skipped_wacz"] + res.get("skipped_oversize", 0) == n


def test_collect_skips_oversize_wacz():
    root, n = _store(6)
    fat = root / "captures" / "zz_fat.wacz"
    fat.write_bytes(b"P" * 2048)                       # bigger than the cap below
    res, calls = _run_with_stub(root, limit=100, max_bytes=1024)
    assert res.get("skipped_oversize") == 1, res.get("skipped_oversize")
    assert str(fat) not in calls, "oversize wacz must not be diagnosed"
    assert len(calls) == n, f"all normal wacz still diagnosed: {len(calls)}/{n}"


def test_replay_collect_respects_time_budget():
    import time as _t
    root, n = _store(20)
    calls = []
    orig_lc, orig_vr = RV._WD.load_capture, RV.validate_replay
    try:
        RV._WD.load_capture = lambda pth, **k: (calls.append(str(pth)), _t.sleep(0.15), {"_s": 1})[-1]
        RV.validate_replay = lambda cap: {"ok": True, "errors": [], "warnings": [], "stats": {"events": 0}}
        res = RV.collect(str(root), limit=100, budget_s=0.4)
    finally:
        RV._WD.load_capture, RV.validate_replay = orig_lc, orig_vr
    assert res.get("budget_exhausted") is True
    assert 1 <= len(calls) <= 5, f"budget did not bound replay loop: {len(calls)}"


def test_data_layer_heavy_reports_are_cached():
    # two calls inside the TTL -> ONE underlying collect; the cached copy
    # carries cache_age_s so staleness is visible.
    import sys as _sys
    from bulk_downloader import app_data_layer as DL
    DL._heavy_cache.clear()
    DL._ensure_path()
    import tools.capture_diagnostics as CD2
    n_calls = []
    orig = CD2.collect
    try:
        CD2.collect = lambda *a, **k: (n_calls.append(1) or {"rows": [], "mock": True})
        r1 = DL.collect_capture_diagnostics()
        r2 = DL.collect_capture_diagnostics()
    finally:
        CD2.collect = orig
        DL._heavy_cache.clear()
    assert len(n_calls) == 1, f"cache miss on second call: {len(n_calls)} underlying calls"
    assert r1.get("mock") and r2.get("mock")
    assert "cache_age_s" in r2 and "cache_age_s" not in r1

if __name__ == "__main__":
    for k in [x for x in sorted(dict(globals())) if x.startswith("test_")]:
        try:
            globals()[k](); print(f"PASS  {k}")
        except AssertionError as e:
            print(f"FAIL  {k}: {e}")
        except Exception as e:
            print(f"ERROR {k}: {type(e).__name__}: {e}")
