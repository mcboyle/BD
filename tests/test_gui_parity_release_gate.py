"""The release gate must reject full GUI-parity item-set drift."""

import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "_gui_parity_release_gate",
    REPO / "tools" / "check_route_counts.py",
)
GATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GATE)


def _write_minimal_gate_tree(root):
    package = root / "bulk_downloader"
    package.mkdir()
    (package / "app_data_layer.py").write_text(
        "@data_layer_bp.route('/data')\n"
        "def data():\n"
        "    return None\n",
        encoding="utf-8",
    )
    (package / "app_report_center.py").write_text(
        "SECTIONS = [1]\n"
        "@report_center_bp.route('/report')\n"
        "def report():\n"
        "    return None\n",
        encoding="utf-8",
    )
    reports = root / "reports"
    reports.mkdir()
    shipped = {
        "items": [
            {"name": "data_layer.data"},
            {"name": "report_center.report"},
            {"name": "tool.present"},
        ]
    }
    (reports / "gui_parity_inventory.json").write_text(
        json.dumps(shipped),
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_wave2_backlog.py").write_text(
        "assert register_routes(app) == 1\n"
        "assert register_routes(Flask(__name__)) == 1\n"
        'assert len(secs["sections"]) == 1\n',
        encoding="utf-8",
    )
    (root / "DEPENDENCY_GRAPH.json").write_text(
        json.dumps({"blueprint": {}}),
        encoding="utf-8",
    )


def test_release_gate_rejects_non_route_inventory_drift(tmp_path, monkeypatch):
    _write_minimal_gate_tree(tmp_path)
    live = {
        "items": [
            {"name": "data_layer.data"},
            {"name": "report_center.report"},
            {"name": "tool.present"},
            {"name": "tool.missing_from_shipped"},
        ]
    }
    monkeypatch.setattr(GATE, "_live_inventory", lambda root: live)

    assert GATE.run(tmp_path) == 1


def test_release_gate_accepts_matching_full_inventory(tmp_path, monkeypatch):
    _write_minimal_gate_tree(tmp_path)
    shipped = json.loads(
        (tmp_path / "reports" / "gui_parity_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    monkeypatch.setattr(GATE, "_live_inventory", lambda root: shipped)

    assert GATE.run(tmp_path) == 0
