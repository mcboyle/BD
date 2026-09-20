"""Row 922: EXTRACTION-SCHEMA-PERFORMANCE-AND-MEMORY-PROFILING-SUITE.

Acceptance:
  (1) template parsing completes in <15ms
  (2) detection of catastrophic regex backtracking
  (3) structured memory reporting

toolchain/bin/bd-template-bench is an extensionless shebang script (like the
rest of toolchain/bin), so its internals are exercised directly via
importlib.util.spec_from_file_location -- the same loading shape the toolchain
census already tracks for extensionless scripts -- and its CLI contract is
exercised via subprocess, matching test_toolchain_534.py's convention.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

BD_GATE_SCOPE = "module"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "toolchain", "bin", "bd-template-bench")

_loader = importlib.machinery.SourceFileLoader("bd_template_bench_row922", TOOL)
_spec = importlib.util.spec_from_file_location(
    "bd_template_bench_row922", TOOL, loader=_loader)
bench = importlib.util.module_from_spec(_spec)
_loader.exec_module(bench)


# ── (1) real templates parse under the latency budget ────────────────────

def test_reviewed_templates_parse_under_15ms():
    results = bench.scan(REPO)
    assert results, "expected at least one templates/reviewed/*.template.json"
    for r in results:
        assert r["error"] is None, r
        assert r["parse_ms"] < 15.0, r


# ── (2) catastrophic regex backtracking detection ────────────────────────

def test_redos_risk_flags_nested_quantifiers():
    assert bench.redos_risk(r"(a+)+$")
    assert bench.redos_risk(r"(a*)*b")
    assert bench.redos_risk(r"(x|x)+y")


def test_redos_risk_does_not_flag_the_tool_s_own_generated_patterns():
    # negative control: every regex bd-template-bench itself generates from a
    # {placeholder} must be redos-clean by construction.
    for raw in ("https://api2.reptyle.com/api/v1/movie/{id}/watch",
                "https://x.example/{a}/{b}/{c}"):
        assert not bench.redos_risk(bench.pattern_to_regex(raw))


def test_benchmark_template_surfaces_redos_findings_for_a_planted_selector(
        tmp_path):
    td = tmp_path / "templates" / "reviewed"
    td.mkdir(parents=True)
    f = td / "evil.template.json"
    f.write_text(json.dumps({
        "selectors": {"weird": "(a+)+$"}, "network_patterns": [],
    }))
    result = bench.benchmark_template(str(f))
    assert result["error"] is None
    assert result["redos_findings"], result


# ── (3) structured memory reporting ──────────────────────────────────────

def test_benchmark_template_reports_structured_memory(tmp_path):
    td = tmp_path / "templates" / "reviewed"
    td.mkdir(parents=True)
    f = td / "mem.template.json"
    f.write_text(json.dumps({
        "selectors": {"a": ".b"},
        "network_patterns": ["https://x/{id}/watch"],
    }))
    result = bench.benchmark_template(str(f))
    assert result["error"] is None
    assert isinstance(result["parse_ms"], float)
    assert isinstance(result["memory"], dict)
    assert set(result["memory"]) == {"current_bytes", "peak_bytes"}
    assert result["memory"]["peak_bytes"] > 0
    assert result["memory"]["peak_bytes"] >= result["memory"]["current_bytes"]
    assert result["regex_count"] == 1
    assert result["redos_findings"] == []


def test_benchmark_template_never_raises_on_a_malformed_file(tmp_path):
    f = tmp_path / "broken.template.json"
    f.write_text("{not json")
    result = bench.benchmark_template(str(f))
    assert result["parse_ms"] is None
    assert result["error"] is not None


# ── CLI contract (subprocess, matches test_toolchain_534.py convention) ──

def test_cli_selftest_exits_zero():
    r = subprocess.run([sys.executable, TOOL, "--selftest"],
                        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_json_scan_of_real_templates_exits_zero_and_is_valid_json():
    r = subprocess.run([sys.executable, TOOL, "--work", REPO, "--json"],
                        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    payload = json.loads(r.stdout)
    assert payload
    for entry in payload:
        assert entry["error"] is None
        assert entry["parse_ms"] < 15.0


def test_cli_exits_nonzero_on_a_planted_slow_over_ms_budget(tmp_path):
    td = tmp_path / "templates" / "reviewed"
    td.mkdir(parents=True)
    (td / "x.template.json").write_text(json.dumps({
        "selectors": {}, "network_patterns": ["https://x/{id}"],
    }))
    r = subprocess.run([sys.executable, TOOL, "--work", str(tmp_path),
                        "--json", "--max-ms", "-1"],
                        capture_output=True, text=True, timeout=30)
    assert r.returncode == 1, r.stdout + r.stderr


# ── FIXER (row922 REFUTE E1 HIGH / E2 / E3) ──────────────────────────────

import tracemalloc

import pytest


@pytest.mark.parametrize("body", ["null", "12345", '"string"', "[]", '{"network_patterns": [1, 2]}',
                                   '{"network_patterns": [null]}', '{"network_patterns": "not-a-list"}'])
def test_benchmark_template_reports_valid_json_with_a_wrong_shape_as_error(tmp_path, body):
    """E1: a valid JSON document whose root is not an object, or whose pattern
    entries are not strings, is a malformed TEMPLATE -- recorded as a
    structured error, never an AttributeError/TypeError out of scan()."""
    f = tmp_path / "shape.template.json"
    f.write_text(body)
    result = bench.benchmark_template(str(f))
    assert result["parse_ms"] is None and result["error"], result
    assert result["regex_count"] == 0
    assert not tracemalloc.is_tracing()


def test_scan_survives_one_wrong_shaped_template_among_good_ones(tmp_path):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "a.template.json").write_text('{"network_patterns": ["https://x/{id}"]}')
    (tdir / "b.template.json").write_text("null")
    results = bench.scan(str(tmp_path))
    assert [r["error"] is None for r in results] == [True, False]


def test_tracemalloc_is_stopped_even_when_the_body_raises(tmp_path, monkeypatch):
    """E2: start/stop are paired in try/finally -- an error the except clause
    does not name still leaves tracing off and no leaked peak."""
    f = tmp_path / "boom.template.json"
    f.write_text('{"network_patterns": ["x"]}')
    monkeypatch.setattr(bench, "pattern_to_regex", lambda raw: (_ for _ in ()).throw(RuntimeError("fixture")))
    assert not tracemalloc.is_tracing()
    with pytest.raises(RuntimeError, match="fixture"):
        bench.benchmark_template(str(f))
    assert not tracemalloc.is_tracing()


def test_caller_owned_tracing_is_left_running_and_peak_is_reset(tmp_path):
    """E2: if the caller already traces, the tool does not stop their trace
    and resets the peak so a previous template's allocations do not bleed in."""
    f = tmp_path / "ok.template.json"
    f.write_text('{"network_patterns": ["https://x/{id}"]}')
    tracemalloc.start()
    try:
        junk = bytearray(4 * 1024 * 1024)  # noqa: F841 -- inflate the peak before the call
        del junk
        result = bench.benchmark_template(str(f))
        assert tracemalloc.is_tracing()
        assert result["memory"]["peak_bytes"] < 2 * 1024 * 1024, result["memory"]
    finally:
        tracemalloc.stop()


def test_planted_catastrophic_match_url_pattern_is_found_and_fails_the_cli(tmp_path):
    """r2 E1: match.url_patterns are the RAW regexes the product compiles;
    a planted (a+)+ there is a finding and the CLI exits non-zero. The
    selectors case is the control (same finding shape, already covered)."""
    td = tmp_path / "templates" / "reviewed"
    td.mkdir(parents=True)
    evil = td / "evil.template.json"
    evil.write_text(json.dumps({"match": {"hosts": ["x.test"], "url_patterns": ["^https://x\\.test/(a+)+$"]},
                                "selectors": {}, "network_patterns": []}))
    result = bench.benchmark_template(str(evil))
    assert result["error"] is None and result["regex_count"] == 1
    assert [f for f in result["redos_findings"] if f.startswith("url_pattern ")], result
    proc = subprocess.run([sys.executable, TOOL, "--work", str(tmp_path), "--json"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0, proc.stdout + proc.stderr

    ok = td / "ok.template.json"
    evil.unlink()
    ok.write_text(json.dumps({"match": {"hosts": ["x.test"], "url_patterns": ["^https://(?:www\\.)?x\\.test/"]},
                              "selectors": {}, "network_patterns": []}))
    assert bench.benchmark_template(str(ok))["redos_findings"] == []


def test_malformed_match_url_pattern_is_a_structured_error(tmp_path):
    f = tmp_path / "bad.template.json"
    f.write_text(json.dumps({"match": {"url_patterns": ["^https://x\\.test/("]}}))
    result = bench.benchmark_template(str(f))
    assert result["parse_ms"] is None and result["error"].startswith("error:")
