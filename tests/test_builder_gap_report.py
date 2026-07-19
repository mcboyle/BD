"""builder_gap_report (A6-0) — characterization tests.

Pins the gap classification the tool draws between what the builder auto-derives
and what the reviewed gold needs: resolutions / network_patterns / match are AUTO,
download.trigger + row_selectors are PARTIAL (heuristic forms), api.base + named
endpoints are MANUAL, and template_logic is excluded as human-only-by-schema. Also
pins the POSTURE guarantee that no capture values reach the report. Synthetic,
in-process; browser-free; stdlib + project modules.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import builder_gap_report as BGR


def _gold():
    # minimal reviewed-gold shape carrying every load-bearing field
    return {
        "host": "g.example", "status": "enabled",
        "selectors": {"download": {"trigger": ".real-open-button",
                                   "row_selectors": ["a", "b", "c"]}},
        "api": {"base": "https://api.g.example", "movie_watch": "...", "trailer": "..."},
        "network_patterns": ["p1", "p2"],
        "resolutions": [1080, 720, 480],
        "template_logic": {"steps": ["human-authored"]},
    }


def test_gold_profile_reads_load_bearing_fields():
    gp = BGR.gold_profile(_gold())
    assert gp["download.trigger"]["present"] is True
    assert gp["download.row_selectors"]["count"] == 3
    assert gp["api.base"]["present"] is True
    assert gp["api.named_endpoints"]["count"] == 2          # movie_watch, trailer
    assert gp["_human"]["template_logic"] is True


def test_builder_profile_from_synthetic_emits_expected():
    import tempfile
    from bulk_downloader.wacz_export import write_wacz
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "c.wacz"
        write_wacz(BGR._synthetic_capture(), str(w))
        bp = BGR.builder_profile(str(w))
    # auto-derived from the synthetic capture
    assert bp["resolutions"]["present"] and bp["resolutions"]["count"] >= 1
    assert bp["network_patterns"]["present"]
    assert bp["download.row_selectors"]["present"]           # serialized-node rows fire
    # A6-1: the enrichment layer now derives a CONCRETE runtime api.base +
    # named endpoint(s) from the observed download-resolution request (the
    # guard, extraction_core, still never builds api.base itself).
    assert bp["api.base"]["present"] is True
    assert bp["api.named_endpoints"]["present"] is True
    assert bp["api.named_endpoints"]["count"] >= 1


def test_classification_labels_against_full_gold():
    bp = {  # a controlled builder profile
        "download.trigger": {"present": True, "value_kind": "download-attr"},
        "download.row_selectors": {"present": True, "count": 2},
        "api.base": {"present": False, "review_only_candidate": True},
        "api.named_endpoints": {"present": False, "count": 0},
        "network_patterns": {"present": True, "count": 1},
        "resolutions": {"present": True, "count": 1},
        "match": {"present": True},
    }
    cls = BGR.classify(BGR.gold_profile(_gold()), bp)
    assert cls["resolutions"]["label"] == "AUTO"
    assert cls["network_patterns"]["label"] == "AUTO"
    assert cls["match"]["label"] == "AUTO"
    assert cls["download.trigger"]["label"] == "PARTIAL"
    assert cls["download.row_selectors"]["label"] == "PARTIAL"
    assert cls["api.base"]["label"] == "MANUAL"
    assert cls["api.named_endpoints"]["label"] == "MANUAL"
    # the review-only-candidate caveat is surfaced in the api.base note
    assert "review-only" in cls["api.base"]["note"]


def test_targets_exclude_human_only_and_map_to_slices():
    gp = BGR.gold_profile(_gold())
    bp = BGR.builder_profile  # not called; build a minimal profile inline
    cls = BGR.classify(gp, {
        "download.trigger": {"present": True, "value_kind": "text-heuristic"},
        "download.row_selectors": {"present": True, "count": 2},
        "api.base": {"present": False},
        "api.named_endpoints": {"present": False},
        "network_patterns": {"present": True, "count": 2},
        "resolutions": {"present": True, "count": 3},
        "match": {"present": True},
    })
    targets, human = BGR._targets(cls, gp)
    fields = {t["field"] for t in targets}
    assert fields == {"download.trigger", "download.row_selectors",
                      "api.base", "api.named_endpoints"}
    assert "template_logic" not in fields        # human-only never a target
    assert "template_logic" in human
    assert all(t["slice"].startswith("A6-") for t in targets)


def test_report_end_to_end_and_posture():
    # full report over the in-tree reviewed gold(s) + synthetic builder profile
    rep = BGR.report()
    assert rep["rows"], "expected at least the reptyle reviewed gold"
    blob = json.dumps(rep)
    # posture: no fabricated-capture values (urls/hosts/markup) leak into output
    for needle in ("demo.example", "file_1080.mp4", "download-resolution",
                   "ant-modal", "<div", "blob:"):
        assert needle not in blob, f"capture value leaked into report: {needle}"
    # render is text and names the target section
    md = BGR.render_markdown(rep)
    assert "ADDRESSABLE TARGETS" in md
