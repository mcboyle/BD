"""Cut 0.2 (OBS-1): GET /api/data/capture_analytics -> collect_capture_analytics
-> capture_analytics.analyze() walked EVERY .wacz/capture_*.json unbounded and was
NOT cached -- the same hang class the 596 fix bounded for capture_diagnostics/replay
(route-scanning tests GET every route). Bound the walk (limit, newest-first,
skipped_artifacts) + cache the collector, mirroring the _HEAVY_LIMIT/_cached pattern."""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import capture_analytics as CA  # type: ignore


def _store(n_json=20):
    root = Path(tempfile.mkdtemp(prefix="capan_"))
    cap = root / "captures"
    cap.mkdir(parents=True, exist_ok=True)
    for i in range(n_json):
        p = cap / f"capture_{i:03d}.json"
        p.write_text(json.dumps({"host": f"h{i}.example", "capture_kind": "network"}))
        os.utime(p, (1_000_000 + i, 1_000_000 + i))  # stagger mtime: newest = highest i
    return root, n_json


def test_analyze_bounds_the_walk_newest_first():
    root, n = _store(20)
    res = CA.analyze(str(root), dirs=["captures"], limit=5)
    assert res.get("bounded") is True, "limit not applied (no bounded flag)"
    assert res["artifacts"]["count"] == 5, res["artifacts"]["count"]
    assert res.get("skipped_artifacts") == n - 5, res.get("skipped_artifacts")


def test_analyze_unbounded_processes_all():
    root, n = _store(6)
    res = CA.analyze(str(root), dirs=["captures"])   # no limit -> unchanged
    assert res["artifacts"]["count"] == 6
    assert res.get("skipped_artifacts", 0) == 0
    assert "bounded" not in res


def test_collect_capture_analytics_is_bounded_and_cached():
    from bulk_downloader import app_data_layer as DL
    DL._heavy_cache.clear()
    DL._ensure_path()
    import tools.capture_analytics as CA2
    n_calls = []
    orig = CA2.analyze
    try:
        CA2.analyze = lambda *a, **k: (n_calls.append(k.get("limit")) or {"artifacts": {"count": 0}, "mock": True})
        r1 = DL.collect_capture_analytics()
        r2 = DL.collect_capture_analytics()
    finally:
        CA2.analyze = orig
        DL._heavy_cache.clear()
    assert len(n_calls) == 1, f"not cached: {len(n_calls)} underlying analyze calls"
    assert n_calls[0] is not None, "collector must pass a bound (limit) to analyze"
    assert "cache_age_s" in r2 and "cache_age_s" not in r1
