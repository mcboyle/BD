"""Synthetic report IDs belong to one report invocation."""
import copy
import importlib.util
import json
from functools import partial
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"


@pytest.fixture
def report_module(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    path = root / "tools" / "builder_gap_report.py"
    spec = importlib.util.spec_from_file_location("gap_report_counter_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def nodes(capture):
    root = capture["dom_log"][1]["data"]["node"]
    def visit(node):
        yield node
        for child in node.get("childNodes", []):
            yield from visit(child)
    return list(visit(root))


def observe_write(captures, writer, capture, path):
    captures.append(copy.deepcopy(capture))
    return writer(capture, path)


def test_single_capture_keeps_original_ids_and_payload(report_module):
    capture = report_module._synthetic_capture()
    tree = nodes(capture)
    assert len(tree) == 16
    assert len({node["id"] for node in tree}) == 16
    assert sorted(node["id"] for node in tree if node["type"] == 2) == list(range(1, 14))
    assert sorted(node["id"] for node in tree if node["type"] == 3) == [10001, 10002, 10003]
    assert [node["textContent"] for node in tree if node["type"] == 3] == ["2160", "1080", "720"]
    assert capture["host"] == "demo.example"


def test_synthetic_capture_ids_restart_on_each_call(report_module):
    first = report_module._synthetic_capture()
    second = report_module._synthetic_capture()
    assert len(nodes(first)) == len(nodes(second)) == 16
    assert second == first, "synthetic capture IDs leaked across reports"


def test_reports_use_fresh_ids_and_preserve_output(report_module, tmp_path, monkeypatch):
    gold = tmp_path / "gold.template.json"
    gold.write_text(json.dumps({"host": "gold.example", "selectors": {"download": {"trigger": ".download"}}}))
    captures = []
    real_write = report_module.write_wacz
    monkeypatch.setattr(report_module, "write_wacz", partial(observe_write, captures, real_write))
    first = report_module.report(gold_path=str(gold))
    second = report_module.report(gold_path=str(gold))
    assert len(captures) == 2
    assert len(nodes(captures[0])) == len(nodes(captures[1])) == 16
    assert len(first["rows"]) == 1
    assert second == first
    assert captures[1] == captures[0], "report serialized IDs leaked across invocations"
