"""capture_model_golden (B0) — Phase-B convergence regression guard.

Asserts the three capture readers/normalizers still produce their committed
derived output on the fixed synthetic capture. This is the gate that makes a
B1/B2 reroute provably behaviour-preserving: if a reroute changes any derived
field, `--check` drifts and this test fails with a diff. Also pins the projection
shape and the posture guarantee (no raw signing values reach the golden).
Synthetic only; browser-free.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import capture_model_golden as G


def test_golden_matches_current_readers():
    ok, diff = G.check_golden()
    assert ok, "capture-model golden DRIFT (reroute changed derived behaviour):\n" + "\n".join(diff[:80])


def test_projection_has_three_readers():
    proj = G.build_projection()
    for key in ("capture_ingest.normalize_capture",
                "workflow_diagnostic.load_capture",
                "build_template_from_wacz.build_template"):
        assert key in proj, f"missing reader projection: {key}"


def test_projection_is_deterministic():
    assert G.build_projection() == G.build_projection()


def test_golden_carries_no_raw_signing_values():
    blob = json.dumps(G.build_projection())
    for needle in ("token=SECRET", "sig=ABC", "SECRET"):
        assert needle not in blob, f"raw signing value reached the golden: {needle}"
    # signing is recorded by marker NAME only on the ingest side
    ingest = G.build_projection()["capture_ingest.normalize_capture"]
    markers = ingest["requests"][0].get("signing_markers") or []
    assert markers and markers[0].get("name") == "token" and "value" not in markers[0]
