"""Cut 882: PARALLEL-TEST-SHARDING-COORDINATOR-FOR-PYTEST-XDIST (Row 882).

Automated test duration bin-packing in toolchain/bin/bd-test-shard distributing
pytest test files across cores based on historical execution duration.
Verifies:
(1) test duration ledger correctly balances shard weights within 10%,
(2) full test suite execution completes in <30s across 16 cores,
(3) zero test dropped or duplicated.
0 site logins touched (Fleet Rule 21).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

BD_GATE_SCOPE = "module"

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARD_TOOL = REPO_ROOT / "toolchain" / "bin" / "bd-test-shard"


def _load_tool_module():
    """Load toolchain/bin/bd-test-shard as a Python module."""
    if not SHARD_TOOL.exists():
        raise FileNotFoundError(f"Missing required tool: {SHARD_TOOL}")
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("bd_test_shard", str(SHARD_TOOL))
    spec = importlib.util.spec_from_loader("bd_test_shard", loader)
    if spec is None:
        raise ImportError(f"Could not create module spec for {SHARD_TOOL}")
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_tool_executable_exists_and_runs():
    """Verifies that toolchain/bin/bd-test-shard exists, is executable, and responds to --help."""
    assert SHARD_TOOL.exists(), f"Expected {SHARD_TOOL} to exist"
    assert os.access(SHARD_TOOL, os.X_OK), f"Expected {SHARD_TOOL} to be executable"
    res = subprocess.run([sys.executable, str(SHARD_TOOL), "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "bd-test-shard" in res.stdout or "shard" in res.stdout


def test_zero_test_dropped_or_duplicated():
    """Acceptance (3): zero test dropped or duplicated across all shards."""
    mod = _load_tool_module()
    
    # Generate 500 synthetic test file paths
    test_files = [f"tests/test_file_{i:04d}.py" for i in range(500)]
    
    # Partition into 16 shards
    shards = mod.lpt_bin_pack(test_files, num_shards=16)
    assert len(shards) == 16, f"Expected 16 shards, got {len(shards)}"
    
    # Check partition property
    collected_files = []
    for idx, shard in enumerate(shards):
        assert len(shard) > 0, f"Shard {idx} is empty"
        collected_files.extend(shard)
        
    assert len(collected_files) == len(test_files), "Mismatch in total file count (dropped or duplicated)"
    assert set(collected_files) == set(test_files), "Union of shards does not match input files"
    
    # Pairwise disjoint
    for i in range(len(shards)):
        for j in range(i + 1, len(shards)):
            overlap = set(shards[i]).intersection(set(shards[j]))
            assert len(overlap) == 0, f"Shards {i} and {j} overlap on {overlap}"


def test_duration_ledger_balances_shard_weights_within_10_percent(tmp_path):
    """Acceptance (1): test duration ledger correctly balances shard weights within 10%."""
    mod = _load_tool_module()
    
    # Create realistic skewed test duration distribution:
    # Most tests are fast (0.05 - 0.2s), some medium (0.5 - 1.5s), a few slow (2.0 - 5.0s)
    durations = {}
    test_files = []
    
    for i in range(300):
        fn = f"tests/unit/test_{i}.py"
        durations[fn] = 0.08 + (i % 7) * 0.02
        test_files.append(fn)
    for i in range(100):
        fn = f"tests/integration/test_{i}.py"
        durations[fn] = 0.8 + (i % 5) * 0.2
        test_files.append(fn)
    for i in range(20):
        fn = f"tests/slow/test_{i}.py"
        durations[fn] = 2.5 + (i % 4) * 0.5
        test_files.append(fn)
        
    ledger_file = tmp_path / "duration_ledger.json"
    mod.save_ledger(ledger_file, durations)
    loaded_ledger = mod.load_ledger(ledger_file)
    assert loaded_ledger == durations
    
    num_shards = 16
    shards = mod.lpt_bin_pack(test_files, num_shards=num_shards, ledger=loaded_ledger)
    weights = mod.compute_shard_weights(shards, ledger=loaded_ledger)
    
    assert len(weights) == num_shards
    max_weight = max(weights)
    min_weight = min(weights)
    mean_weight = sum(weights) / len(weights)
    
    # (max - min) / max <= 0.10 (within 10%)
    imbalance = (max_weight - min_weight) / max_weight
    assert imbalance <= 0.10, (
        f"Shard imbalance {imbalance:.3%} exceeds 10% tolerance: "
        f"max={max_weight:.2f}s, min={min_weight:.2f}s, mean={mean_weight:.2f}s, weights={weights}"
    )


def test_full_suite_partition_over_the_real_tracked_test_set(tmp_path):
    """Acceptance (2), honestly stated. The <30s-across-16-cores claim is a
    property of REAL durations; it is asserted only when a recorded ledger
    exists at reports/test_durations.json (DEFAULT_LEDGER). What is provable on
    every host is asserted on the REAL tracked test set (not invented paths):
    every real file lands in exactly one of 16 shards, the ledger-less packing
    is count-balanced, and the 90th-percentile fallback keeps an unknown-heavy
    suite's makespan consistent between packing and reporting (E1)."""
    mod = _load_tool_module()
    real_files = mod.collect_test_files(REPO_ROOT / "tests")
    assert len(real_files) > 1000, f"real test set unexpectedly small: {len(real_files)}"
    assert all(f.startswith("tests/") and not os.path.isabs(f) for f in real_files)
    missing = [f for f in real_files[:50] if not (REPO_ROOT / f).is_file()]
    assert not missing, missing

    shards = mod.lpt_bin_pack(real_files, num_shards=16)
    flat = [f for s in shards for f in s]
    assert sorted(flat) == sorted(real_files) and len(flat) == len(set(flat))
    counts = [len(s) for s in shards]
    assert max(counts) - min(counts) <= 1

    ledger_path = REPO_ROOT / mod.DEFAULT_LEDGER
    if ledger_path.is_file():
        ledger = mod.load_ledger(ledger_path, REPO_ROOT)
        covered = sum(1 for f in real_files if f in ledger)
        assert covered >= 0.9 * len(real_files), f"ledger covers only {covered}/{len(real_files)} real files"
        shards = mod.lpt_bin_pack(real_files, num_shards=16, ledger=ledger)
        makespan = mod.simulate_makespan(shards, ledger=ledger)
        assert makespan < 30.0, f"real-ledger makespan {makespan:.2f}s exceeds 30s across 16 shards"
    else:
        # No recorded ledger on this host: the <30s claim stays UNKNOWN here (never
        # faked with synthetic durations); the packing contract above is what is proven.
        assert mod.unknown_duration({}) == mod.DEFAULT_DURATION_SECS


def test_e1_unknown_files_share_one_fallback_between_packing_and_weights():
    """E1: 1 known 20s file + 31 unknown across 16 shards. The 90th-percentile
    fallback (20s) is used for packing AND weighing, so the reported makespan
    reflects the packing: 2 x 20s = 40s, not 20.5s."""
    mod = _load_tool_module()
    files = ["tests/test_known.py"] + [f"tests/test_u{i:02d}.py" for i in range(31)]
    ledger = {"tests/test_known.py": 20.0}
    assert mod.unknown_duration(ledger) == 20.0
    shards = mod.lpt_bin_pack(files, num_shards=16, ledger=ledger)
    weights = mod.compute_shard_weights(shards, ledger=ledger)
    assert mod.simulate_makespan(shards, ledger=ledger) == 40.0
    assert all(w == 40.0 for w in weights)


def test_e2_absolute_work_and_relative_ledger_share_one_identity(tmp_path):
    """E2: --work /abs with a ledger keyed 'tests/x.py' must apply the recorded
    100s, not the 0.5s default."""
    mod = _load_tool_module()
    work = tmp_path / "repo"
    (work / "tests").mkdir(parents=True)
    for n in ("test_a.py", "test_b.py", "test_c.py"):
        (work / "tests" / n).write_text("def test_x():\n    pass\n")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"./tests/test_a.py": 100.0, str(work / "tests" / "test_b.py"): 7.0}))
    res = subprocess.run([sys.executable, str(SHARD_TOOL), "--work", str(work.resolve()), "--ledger", str(ledger),
                          "--shards", "2", "--json"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    # a=100 (ledger), b=7 (abs key), c unknown -> 90th pct fallback 100: shards [a,b]=107 / [c]=100
    assert data["metrics"]["makespan_secs"] == 107.0
    by_file = {f: sh["weight_secs"] for sh in data["shards"] for f in sh["files"]}
    assert set(by_file) == {"tests/test_a.py", "tests/test_b.py", "tests/test_c.py"}
    assert mod.normalize_test_path("./tests/x.py") == "tests/x.py"
    assert mod.normalize_test_path(work / "tests" / "x.py", work) == "tests/x.py"


def test_e3_duplicate_file_list_entries_run_once(tmp_path):
    """E3: a repeated file-list entry is executed in exactly one shard."""
    fl = tmp_path / "files.txt"
    fl.write_text("tests/test_a.py\ntests/test_b.py\ntests/test_a.py\n./tests/test_b.py\n")
    res = subprocess.run([sys.executable, str(SHARD_TOOL), "--work", str(tmp_path), "--file-list", str(fl),
                          "--shards", "2", "--json"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    flat = [f for sh in data["shards"] for f in sh["files"]]
    assert sorted(flat) == ["tests/test_a.py", "tests/test_b.py"]
    assert data["metrics"]["total_files"] == 2
    assert "duplicate" in res.stderr


def test_e4_nonexistent_work_root_is_rejected(tmp_path):
    """E4: a missing --work is rc 2, never rc 0 with zero files."""
    res = subprocess.run([sys.executable, str(SHARD_TOOL), "--work", str(tmp_path / "nope"), "--json"],
                         capture_output=True, text=True)
    assert res.returncode == 2 and "not a directory" in res.stderr


def test_e5_invalid_ledger_values_are_rejected(tmp_path):
    """E5: negative / NaN / inf / non-numeric durations and corrupt JSON are refused (rc 2)."""
    mod = _load_tool_module()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_x():\n    pass\n")
    for body in ('{"tests/test_a.py": -20}', '{"tests/test_a.py": NaN}', '{"tests/test_a.py": Infinity}',
                 '{"tests/test_a.py": "fast"}', '{"tests/test_a.py": true}', '{not json', '42'):
        lp = tmp_path / "ledger.json"
        lp.write_text(body)
        res = subprocess.run([sys.executable, str(SHARD_TOOL), "--work", str(tmp_path), "--ledger", str(lp), "--json"],
                             capture_output=True, text=True)
        assert res.returncode == 2, (body, res.stdout[:200], res.stderr)
        assert "Error: ledger" in res.stderr, (body, res.stderr)
    import pytest
    with pytest.raises(mod.LedgerError):
        mod.load_ledger(tmp_path / "ledger.json")
    assert mod.load_ledger(tmp_path / "absent.json") == {}
    assert mod.simulate_makespan([["tests/test_a.py"]], ledger={}) == mod.DEFAULT_DURATION_SECS


def test_e6_shard_bounds_hold_for_every_output_mode(tmp_path):
    """E6: --shard 17 of 16 is rc 2 with or without --json / --simulate; --shards 0 is rc 2."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_x():\n    pass\n")
    base = [sys.executable, str(SHARD_TOOL), "--work", str(tmp_path), "--shards", "16"]
    for extra in ([], ["--json"], ["--simulate"]):
        res = subprocess.run(base + ["--shard", "17"] + extra, capture_output=True, text=True)
        assert res.returncode == 2 and "out of bounds" in res.stderr, (extra, res.stdout[:100])
        res = subprocess.run(base + ["--shard", "0"] + extra, capture_output=True, text=True)
        assert res.returncode == 2
    res = subprocess.run([sys.executable, str(SHARD_TOOL), "--work", str(tmp_path), "--shards", "0", "--json"],
                         capture_output=True, text=True)
    assert res.returncode == 2 and "positive" in res.stderr
    res = subprocess.run(base + ["--shard", "16"], capture_output=True, text=True)
    assert res.returncode == 0


def test_cli_shard_selection_and_json_output(tmp_path):
    """Verifies CLI arguments --shards, --shard, --json, and --ledger."""
    durations = {f"tests/test_{i}.py": float(i + 1) for i in range(20)}
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(durations))
    
    file_list = tmp_path / "files.txt"
    file_list.write_text("\n".join(durations.keys()))
    
    # 1. JSON output across 4 shards
    cmd_json = [
        sys.executable, str(SHARD_TOOL),
        "--shards", "4",
        "--ledger", str(ledger_path),
        "--file-list", str(file_list),
        "--json",
    ]
    res_json = subprocess.run(cmd_json, capture_output=True, text=True)
    assert res_json.returncode == 0, f"CLI error: {res_json.stderr}"
    data = json.loads(res_json.stdout)
    assert "shards" in data
    assert len(data["shards"]) == 4
    assert "metrics" in data
    assert data["metrics"]["total_files"] == 20
    
    # 2. Selecting a single shard (1-indexed: --shard 1)
    cmd_shard1 = [
        sys.executable, str(SHARD_TOOL),
        "--shards", "4",
        "--shard", "1",
        "--ledger", str(ledger_path),
        "--file-list", str(file_list),
    ]
    res_shard1 = subprocess.run(cmd_shard1, capture_output=True, text=True)
    assert res_shard1.returncode == 0
    lines = [line.strip() for line in res_shard1.stdout.splitlines() if line.strip()]
    assert len(lines) == len(data["shards"][0]["files"])
    assert lines == data["shards"][0]["files"]


def test_zero_site_login_interaction():
    """Fleet Rule 21: zero site logins touched; test2 is hands-off."""
    content = SHARD_TOOL.read_text()
    assert "10.0.70.95" not in content, "Found test2 IP in tool source"
    assert "bd-capture-test2" not in content, "Found test2 capture reference"
