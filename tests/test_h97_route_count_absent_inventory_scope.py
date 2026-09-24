"""H97: an absent, gitignored shipped GUI-parity inventory is UNKNOWN, never a block.

reports/gui_parity_inventory.json is untracked (.gitignore `reports/*`), so a fresh worktree never
has it and no cut can add it; check_route_counts used to fail every such tree on "MISSING required
file". The gate now names the shipped-vs-live comparison as NOT RUN and reads the inventory counts
from the live generator, so a real route-count drift still fails it (the negative controls below).

Each case runs the real run() against a temporary root holding copies of the three tracked inputs;
the live generator and the blueprint census are replaced so the cases are fast and exact.
"""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "check_route_counts.py"
INPUTS = ("bulk_downloader/app_data_layer.py", "bulk_downloader/app_report_center.py",
          "tests/test_wave2_backlog.py")


def _load():
    spec = importlib.util.spec_from_file_location("_h97_check_route_counts", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inventory(data_layer, report_center):
    return {"items": [{"name": f"data_layer.v{i}"} for i in range(data_layer)]
            + [{"name": f"report_center.v{i}"} for i in range(report_center)]}


@pytest.fixture
def gate(tmp_path, monkeypatch):
    module = _load()
    for rel in INPUTS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, tmp_path / rel)
    data = module._count_route_decorators(tmp_path / INPUTS[0], "data_layer_bp")
    rc = module._count_route_decorators(tmp_path / INPUTS[1], "report_center_bp")
    assert data > 0 and rc > 0, "precondition: the copied blueprints carry routes"
    monkeypatch.setattr(module, "_blueprint_counts", lambda root: (1, 1))
    live = {"value": _inventory(data, rc)}

    def live_inventory(root):
        if isinstance(live["value"], Exception):
            raise live["value"]
        return live["value"]

    monkeypatch.setattr(module, "_live_inventory", live_inventory)
    return module, tmp_path, live, (data, rc)


def test_absent_shipped_inventory_is_unknown_not_missing(gate, capsys):
    module, root, _live, _counts = gate
    assert not (root / "reports" / "gui_parity_inventory.json").exists()
    assert module.run(root) == 0
    err = capsys.readouterr().err
    assert "UNKNOWN shipped inventory absent" in err and "NOT RUN" in err
    assert "MISSING required file" not in err


def test_absent_inventory_still_fails_a_live_route_drift(gate, capsys):
    module, root, live, (data, rc) = gate
    live["value"] = _inventory(data - 1, rc)
    assert module.run(root) == 1
    assert "ROUTE-COUNT GATE FAIL: drift in data_layer routes" in capsys.readouterr().out


def test_absent_inventory_still_fails_a_stale_test_pin(gate, capsys):
    module, root, _live, (data, _rc) = gate
    pins = root / INPUTS[2]
    text = pins.read_text(encoding="utf-8")
    stale = text.replace(f"register_routes(app) == {data}", f"register_routes(app) == {data + 1}", 1)
    assert stale != text, "precondition: the data_layer pin is present to edit"
    pins.write_text(stale, encoding="utf-8")
    assert module.run(root) == 1
    assert "drift in data_layer routes" in capsys.readouterr().out


def test_absent_inventory_with_a_failing_generator_fails_closed(gate, capsys):
    module, root, live, _counts = gate
    live["value"] = RuntimeError("gui parity generator exited 1")
    assert module.run(root) == 1
    assert "GUI-PARITY GATE FAIL" in capsys.readouterr().err


def test_a_present_but_stale_shipped_inventory_still_fails(gate, capsys):
    module, root, _live, (data, rc) = gate
    (root / "reports").mkdir()
    (root / "reports" / "gui_parity_inventory.json").write_text(
        json.dumps(_inventory(data, rc + 1)), encoding="utf-8")
    assert module.run(root) == 1
    assert "shipped item-set differs from live generator" in capsys.readouterr().err


def test_a_missing_tracked_input_is_still_missing(gate, capsys):
    module, root, _live, _counts = gate
    (root / INPUTS[2]).unlink()
    assert module.run(root) == 1
    assert "MISSING required file" in capsys.readouterr().err
