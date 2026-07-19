"""replay_validator (A8) — characterization tests.

Pins the rrweb replayability invariants and the posture guarantee (ids/structure
only, never values). Synthetic event logs only; browser-free; stdlib + project.
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import replay_validator as RV
from bulk_downloader.wacz_export import write_wacz

_T = 1_000_000


def _node(nid, children=None):
    return {"id": nid, "type": 2, "tagName": "div", "childNodes": children or []}


def _well_formed():
    return {
        "host": "x.com", "url": "https://x.com/v/9",
        "dom_log": [
            {"dom_seq": 0, "timestamp": _T, "type": "meta",
             "source": -1, "data": {"href": "https://x.com/v/9", "width": 1280, "height": 720}},
            {"dom_seq": 1, "timestamp": _T + 10, "type": "full_snapshot", "source": -1,
             "data": {"node": _node(1, [_node(2), _node(3)])}},
            {"dom_seq": 2, "timestamp": _T + 20, "type": "incremental", "source": 2,
             "data": {"adds": [{"parentId": 1, "node": _node(4)}]}},
            {"dom_seq": 3, "timestamp": _T + 30, "type": "incremental", "source": 0,
             "data": {"attributes": [{"id": 4}], "texts": [{"id": 2}]}},
            {"dom_seq": 4, "timestamp": _T + 40, "type": "incremental", "source": 2,
             "data": {"removes": [{"id": 4, "parentId": 1}]}},
        ],
    }


def test_well_formed_is_replayable():
    v = RV.validate_replay(_well_formed())
    assert v["ok"] is True
    assert v["errors"] == []
    assert v["stats"]["full_snapshots"] == 1
    assert v["stats"]["incrementals"] == 3
    assert v["stats"]["dangling_parent_adds"] == 0
    assert v["stats"]["dangling_ref_ops"] == 0


def test_empty_log_fails():
    v = RV.validate_replay({"dom_log": []})
    assert v["ok"] is False and any("empty" in e for e in v["errors"])


def test_incremental_before_full_snapshot_is_error():
    cap = {"dom_log": [
        {"dom_seq": 0, "timestamp": _T, "type": "incremental", "source": 2,
         "data": {"adds": [{"parentId": 1, "node": _node(2)}]}},
        {"dom_seq": 1, "timestamp": _T + 10, "type": "full_snapshot", "source": -1,
         "data": {"node": _node(1)}},
    ]}
    v = RV.validate_replay(cap)
    assert v["ok"] is False
    assert any("precedes any full_snapshot" in e for e in v["errors"])


def test_no_full_snapshot_is_error():
    cap = {"dom_log": [
        {"dom_seq": 0, "timestamp": _T, "type": "meta", "source": -1, "data": {"href": "h"}},
    ]}
    v = RV.validate_replay(cap)
    assert v["ok"] is False and any("no full_snapshot" in e for e in v["errors"])


def test_empty_full_snapshot_node_is_error():
    cap = {"dom_log": [
        {"dom_seq": 0, "timestamp": _T, "type": "full_snapshot", "source": -1, "data": {}},
    ]}
    v = RV.validate_replay(cap)
    assert v["ok"] is False and any("no serialized node tree" in e for e in v["errors"])


def test_timestamp_regression_is_error():
    cap = {"dom_log": [
        {"dom_seq": 0, "timestamp": _T + 100, "type": "full_snapshot", "source": -1,
         "data": {"node": _node(1)}},
        {"dom_seq": 1, "timestamp": _T + 10, "type": "incremental", "source": 2,
         "data": {"adds": [{"parentId": 1, "node": _node(2)}]}},
    ]}
    v = RV.validate_replay(cap)
    assert v["ok"] is False and any("timestamp regresses" in e for e in v["errors"])


def test_dangling_parent_add_is_warning_not_error():
    cap = {"dom_log": [
        {"dom_seq": 0, "timestamp": _T, "type": "meta", "source": -1, "data": {"href": "h"}},
        {"dom_seq": 1, "timestamp": _T + 10, "type": "full_snapshot", "source": -1,
         "data": {"node": _node(1)}},
        {"dom_seq": 2, "timestamp": _T + 20, "type": "incremental", "source": 2,
         "data": {"adds": [{"parentId": 999, "node": _node(2)}]}},  # 999 not live
    ]}
    v = RV.validate_replay(cap)
    assert v["ok"] is True                       # warning, not a hard error
    assert v["stats"]["dangling_parent_adds"] == 1
    assert any("parent id not present" in w for w in v["warnings"])


def test_missing_meta_is_warning():
    cap = {"dom_log": [
        {"dom_seq": 0, "timestamp": _T, "type": "full_snapshot", "source": -1,
         "data": {"node": _node(1)}},
    ]}
    v = RV.validate_replay(cap)
    assert v["ok"] is True
    assert any("no Meta event" in w for w in v["warnings"])


def test_posture_no_values_in_report():
    # node carries a secret attribute value; the report must never echo it
    cap = {"dom_log": [
        {"dom_seq": 0, "timestamp": _T, "type": "meta", "source": -1,
         "data": {"href": "https://x.com/?token=SECRET", "width": 1, "height": 1}},
        {"dom_seq": 1, "timestamp": _T + 10, "type": "full_snapshot", "source": -1,
         "data": {"node": {"id": 1, "type": 2, "tagName": "input",
                           "attributes": {"value": "SECRETVALUE"}, "childNodes": []}}},
    ]}
    v = RV.validate_replay(cap)
    blob = json.dumps(v)
    assert "SECRET" not in blob and "SECRETVALUE" not in blob


def test_end_to_end_on_wacz_and_exit():
    with tempfile.TemporaryDirectory() as td:
        wacz = Path(td) / "cap.wacz"
        write_wacz(_well_formed(), str(wacz))
        rc = RV.main([str(wacz), "--json"])
    assert rc == 0
