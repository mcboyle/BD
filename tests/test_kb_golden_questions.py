"""Golden-question regression suite for the KB oracle (KB Tier-A / A4).

The in-sync gates keep the *indexes* honest; this keeps the *query layer* (tools/bd_kb.py)
honest. Fixed questions with known-correct answers, run every cut, so `what-pins` /
`can-i-retire` can't silently degrade the way an ungated index could — without it the
answer engine is an ungated index in a nicer coat.

Answers are asserted as STABLE PROPERTIES (ownership facts, verdicts, "is the 302 pin
found"), never as churning counts, so the suite fires on a real oracle regression, not on a
routine route/pin addition.

Custom-runner friendly: repo root on sys.path; zero-arg tests; bd_kb is stdlib-only
(reads the generated JSON, never imports the app).
"""
import os
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.bd_kb as kb  # noqa: E402


def test_golden_who_owns_api_library():
    """Phase-4 (v3.66.419) extracted /api/library onto the 'library' blueprint
    (app_library.py, thin-core-shell), so ownership is now ['library'] — no longer
    top-level @app.route '(app)'. Guards against a mis-attribution / index regression."""
    res = kb.query_routes(path="/api/library")
    assert res["count"] > 0, "no /api/library routes found — index regression"
    assert res["blueprints"] == ["library"], (
        f"/api/library ownership regressed: {res['blueprints']} (expected ['library'])"
    )


def test_golden_what_pins_data_layer_finds_302():
    """what-pins must surface the exact count-dict pin behind the v3.66.302 miss."""
    res = kb.what_pins("data_layer")
    assert any(p["form"] == "count_dict" and "test_v3_66_302" in p["file"]
               for p in res["pins"]), "what-pins 'data_layer' lost the 302 count-dict pin"
    # the answer must also disclose the handled-elsewhere pin forms
    assert "guard_sha" in res["handled_elsewhere"] and "route_count" in res["handled_elsewhere"]


def test_golden_can_i_retire_blocked_for_live_blueprint():
    """A blueprint that owns routes + is imported + is pinned must read BLOCKED with
    its evidence enumerated (this is the A-vs-B scope answer the plan wanted)."""
    res = kb.can_i_retire("data_layer")
    assert res["verdict"] == "BLOCKED"
    assert res["summary"]["routes"] > 0, "should enumerate data_layer's routes"
    assert res["summary"]["importers"] > 0, "should enumerate who imports it"
    assert "bulk_downloader/app_data_layer.py" in res["resolved_modules"]
    # the blocking pin set must include the 302 count-dict pin
    assert any("test_v3_66_302" in p["file"] for p in res["pins_to_update"])


def test_golden_can_i_retire_safe_for_absent_target():
    """A target with no routes / importers / pins reads SAFE — the SAFE-path logic."""
    res = kb.can_i_retire("nonexistent_xyz_module")
    assert res["verdict"] == "SAFE"
    assert res["summary"] == {"routes": 0, "importers": 0, "pins": 0}


def test_golden_can_i_retire_safe_for_retired_module():
    """The cleanup golden: app_monitoring was physically removed @v3.66.353, so it
    must read SAFE (no routes, no importers, no pins reference it)."""
    res = kb.can_i_retire("app_monitoring")
    assert res["verdict"] == "SAFE", f"app_monitoring should be retired-clean: {res['summary']}"


def test_golden_version_pin_is_queryable():
    """The live version pin must be findable via what-pins on the live version."""
    from bulk_downloader import __version__
    res = kb.what_pins(__version__)
    assert any(p["form"] == "version" for p in res["pins"]), \
        f"version pin for {__version__} not queryable via what-pins"
