"""ci-shard r1 (O3203): the train-only process-tests lane runs as a matrix, and its split is a
partition of tests/PROCESS_TESTS.txt.

The defect this guards: a shard split that drops, duplicates or empties a leg turns "every
shard green" into a lane that silently stops running declared PROCESS files. Each assertion
carries a `CI-SHARD-SPLIT:` diagnostic so a red names which contract broke.
"""
from __future__ import annotations

import importlib.util
import random
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools" / "process_test_shards.py"
LIST = ROOT / "tests" / "PROCESS_TESTS.txt"
TRAIN_GATE = "github.event_name == 'schedule' || startsWith(github.head_ref, 'train/')"


def _helper():
    assert HELPER.is_file(), f"CI-SHARD-SPLIT: no shard helper at {HELPER.relative_to(ROOT)}"
    spec = importlib.util.spec_from_file_location("process_test_shards", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declared() -> set[str]:
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import ci_shards
    finally:
        sys.path.pop(0)
    return ci_shards.process_tests(ROOT)


def _jobs() -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())["jobs"]


def _matrix_shards() -> list:
    job = _jobs()["process-tests"]
    return ((job.get("strategy") or {}).get("matrix") or {}).get("shard") or []


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HELPER), *args], capture_output=True,
                          text=True, cwd=ROOT, timeout=60)


def test_real_list_splits_into_a_partition_at_the_ci_width():
    helper = _helper()
    declared = _declared()
    files = helper.read_list(LIST)
    assert set(files) == declared, (
        "CI-SHARD-SPLIT: helper parse differs from tools/ci_shards.process_tests: "
        f"{sorted(set(files) ^ declared)}")
    width = len(_matrix_shards())
    assert width >= 2, f"CI-SHARD-SPLIT: process-tests matrix has {width} shard(s)"
    for n in sorted({1, 2, 3, width, width + 1}):
        legs = [_run("files", str(LIST.relative_to(ROOT)), str(k), str(n)) for k in range(1, n + 1)]
        assert all(leg.returncode == 0 for leg in legs), (
            f"CI-SHARD-SPLIT: n={n} leg failed: {[leg.stderr for leg in legs if leg.returncode]}")
        shards = [leg.stdout.splitlines() for leg in legs]
        placed = [path for shard in shards for path in shard]
        assert all(shards), f"CI-SHARD-SPLIT: n={n} has an empty shard"
        assert len(placed) == len(set(placed)), f"CI-SHARD-SPLIT: n={n} runs a file twice"
        assert set(placed) == declared, (
            f"CI-SHARD-SPLIT: n={n} union != PROCESS_TESTS.txt: {sorted(set(placed) ^ declared)}")
        assert max(map(len, shards)) - min(map(len, shards)) <= 1, (
            f"CI-SHARD-SPLIT: n={n} unbalanced {list(map(len, shards))}")
    check = _run("check", str(LIST.relative_to(ROOT)), str(width))
    assert check.returncode == 0 and f"{len(declared)} files in {width} shards" in check.stdout, (
        f"CI-SHARD-SPLIT: check refused the real list: {check.stdout}{check.stderr}")


def test_split_is_deterministic_and_order_independent(tmp_path):
    helper = _helper()
    lines = LIST.read_text(encoding="utf-8").splitlines()
    shuffled = lines[:]
    random.Random(3203).shuffle(shuffled)
    other = tmp_path / "shuffled.txt"
    other.write_text("\n".join(shuffled + ["", "  # comment", lines[-1]]) + "\n", encoding="utf-8")
    width = len(_matrix_shards())
    assert helper.split(helper.read_list(LIST), width) == helper.split(
        helper.read_list(other), width), "CI-SHARD-SPLIT: split depends on list order"


@pytest.mark.parametrize("args, why", [
    (["files", "{list}", "0", "4"], "shard index must be in 1..4"),
    (["files", "{list}", "5", "4"], "shard index must be in 1..4"),
    (["files", "{list}", "1", "0"], "shard count must be >= 1"),
    (["files", "{list}", "1", "four"], "shard count must be an integer"),
    (["files", "{list}", "1", "9"], "leaves a shard empty"),
    (["files", "{empty}", "1", "1"], "declares no files"),
    (["files", "{missing}", "1", "1"], "cannot read"),
    (["files", "{huge}", "1", "1"], "byte cap"),
    (["files", "{list}", "1"], "process_test_shards.py files"),
    (["check", "{list}", ""], "shard count must be an integer"),
])
def test_bad_input_refuses_red_and_prints_no_files(tmp_path, args, why):
    small = tmp_path / "small.txt"
    small.write_text("# head\ntests/test_a.py\ntests/test_b.py\n\ntests/test_c.py\ntests/test_d.py\n")
    empty = tmp_path / "empty.txt"
    empty.write_text("# only a comment\n\n   \n")
    huge = tmp_path / "huge.txt"
    huge.write_bytes(b"tests/test_x.py\n" * ((1 << 20) // 16 + 1))
    paths = {"list": small, "empty": empty, "missing": tmp_path / "absent.txt", "huge": huge}
    result = _run(*(a.format(**{k: str(v) for k, v in paths.items()}) for a in args))
    assert result.returncode == 2, f"CI-SHARD-SPLIT: {args} exited {result.returncode}"
    assert result.stdout == "", f"CI-SHARD-SPLIT: {args} printed files on refusal"
    assert why in result.stderr, f"CI-SHARD-SPLIT: {args} stderr lacks {why!r}: {result.stderr}"


def test_process_tests_is_a_matrix_with_an_aggregate_gate():
    jobs = _jobs()
    job = jobs["process-tests"]
    shards = _matrix_shards()
    assert shards == list(range(1, len(shards) + 1)), (
        f"CI-SHARD-SPLIT: matrix shards must be 1..n, got {shards}")
    assert job["strategy"].get("fail-fast") is False, (
        "CI-SHARD-SPLIT: fail-fast would cancel sibling shards and hide their reds")
    assert job.get("if") == TRAIN_GATE, f"CI-SHARD-SPLIT: train-only gate changed: {job.get('if')}"
    runs = [str(step.get("run", "")) for step in job["steps"]]
    assert any(
        'process_test_shards.py files tests/PROCESS_TESTS.txt "${{ matrix.shard }}" '
        '"${{ strategy.job-total }}"' in run and 'pytest "${files[@]}"' in run
        for run in runs), "CI-SHARD-SPLIT: a shard leg does not run its own slice of the list"
    assert any("process_test_shards.py check tests/PROCESS_TESTS.txt" in run for run in runs), (
        "CI-SHARD-SPLIT: no partition check before the shard runs")
    gate = jobs.get("process-tests-all")
    assert gate, "CI-SHARD-SPLIT: no process-tests-all aggregate job"
    needs = gate.get("needs")
    assert needs == "process-tests" or needs == ["process-tests"], (
        f"CI-SHARD-SPLIT: aggregate needs {needs}")
    assert gate.get("if") == f"always() && ({TRAIN_GATE})", (
        f"CI-SHARD-SPLIT: aggregate if {gate.get('if')}")
    body = "\n".join(str(step.get("run", "")) for step in gate["steps"])
    assert "needs.process-tests.result" in body and "success" in body, (
        "CI-SHARD-SPLIT: aggregate does not require every shard green")
