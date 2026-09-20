"""Tests for toolchain/bin/bd-shard-rebalance (Row 863).

Acceptance criteria:
- Deterministic JUnit-duration shard partitioning near five-minute bins (~300s).
- Max bin duration is within 15% of mean: abs(max_duration - mean_duration) / mean_duration <= 0.15.
- Map every test exactly once (union preserved, zero duplicates).
- Repeated input yields identical output (deterministic ordering and partitioning).
- Preserve CI matrix capability boundaries (node/vitest, playwright/chromium, long-pole isolation).
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import subprocess
import xml.etree.ElementTree as ET

import pytest

BD_GATE_SCOPE = "module"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REBALANCE_BIN = REPO_ROOT / "toolchain" / "bin" / "bd-shard-rebalance"


def _create_junit_xml(suites_data: dict[str, list[tuple[str, float]]]) -> str:
    """Helper to generate JUnit XML string.

    suites_data: {file_path: [(test_name, duration_seconds), ...]}
    """
    root = ET.Element("testsuites")
    for file_path, test_list in suites_data.items():
        total_time = sum(d for _, d in test_list)
        suite = ET.SubElement(
            root,
            "testsuite",
            name=file_path.replace("/", "."),
            tests=str(len(test_list)),
            time=f"{total_time:.4f}",
            file=file_path,
        )
        for tname, dur in test_list:
            ET.SubElement(
                suite,
                "testcase",
                name=tname,
                classname=file_path.replace("/", ".").removesuffix(".py"),
                file=file_path,
                time=f"{dur:.4f}",
            )
    return ET.tostring(root, encoding="utf-8").decode("utf-8")


def test_rebalance_binary_exists_and_is_executable():
    """The bd-shard-rebalance tool must exist and have executable permissions."""
    assert REBALANCE_BIN.is_file(), f"Expected binary at {REBALANCE_BIN}"
    assert os.access(REBALANCE_BIN, os.X_OK), f"{REBALANCE_BIN} must be executable"


def test_junit_duration_aggregation(tmp_path: pathlib.Path):
    """Parses single and multiple JUnit XML files, aggregating duration per test file."""
    junit1 = tmp_path / "junit1.xml"
    junit2 = tmp_path / "junit2.xml"

    data1 = {
        "tests/test_alpha.py": [("test_a1", 10.5), ("test_a2", 5.5)],
        "tests/test_beta.py": [("test_b1", 40.0)],
    }
    data2 = {
        "tests/test_alpha.py": [("test_a3", 4.0)],  # Aggregates across xmls
        "tests/test_gamma.py": [("test_g1", 12.0), ("test_g2", 8.0)],
    }

    junit1.write_text(_create_junit_xml(data1), encoding="utf-8")
    junit2.write_text(_create_junit_xml(data2), encoding="utf-8")

    cmd = [
        str(REBALANCE_BIN),
        "--junit", str(junit1),
        "--junit", str(junit2),
        "--dump-durations",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    durations = json.loads(res.stdout)

    assert durations["tests/test_alpha.py"] == pytest.approx(20.0, rel=1e-3)
    assert durations["tests/test_beta.py"] == pytest.approx(40.0, rel=1e-3)
    assert durations["tests/test_gamma.py"] == pytest.approx(20.0, rel=1e-3)


def test_partitioning_near_five_minute_bins(tmp_path: pathlib.Path):
    """Target bin duration is 300s. Bins must stay close to 300s."""
    junit_file = tmp_path / "junit.xml"
    # Create 30 files, average 50s each -> total 1500s -> ~5 bins of 300s
    rng = random.Random(42)
    data = {}
    for i in range(30):
        fname = f"tests/test_file_{i:02d}.py"
        dur = rng.uniform(30.0, 70.0)
        data[fname] = [("test_run", dur)]

    junit_file.write_text(_create_junit_xml(data), encoding="utf-8")

    cmd = [
        str(REBALANCE_BIN),
        "--junit", str(junit_file),
        "--target-bin-duration", "300",
        "--format", "json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    plan = json.loads(res.stdout)

    shards = plan["shards"]
    assert 4 <= len(shards) <= 6, f"Expected ~5 shards for 1500s at 300s target, got {len(shards)}"

    for shard in shards:
        assert shard["duration"] > 0
        assert 240.0 <= shard["duration"] <= 360.0


def test_max_bin_duration_within_15_percent_of_mean(tmp_path: pathlib.Path):
    """Acceptance: abs(max_duration - mean_duration) / mean_duration <= 0.15."""
    junit_file = tmp_path / "junit.xml"
    # Create 40 files of varying realistic sizes (2s to 85s)
    durations_list = [
        85.0, 75.0, 60.0, 55.0, 50.0, 48.0, 45.0, 42.0, 40.0, 38.0,
        35.0, 35.0, 30.0, 30.0, 28.0, 25.0, 25.0, 22.0, 20.0, 20.0,
        18.0, 18.0, 15.0, 15.0, 14.0, 12.0, 10.0, 10.0, 8.0,  8.0,
        6.0,  5.0,  5.0,  5.0,  4.0,  4.0,  3.0,  3.0,  2.0,  2.0,
    ]  # Total = 918.0s -> ~3 bins at 300s, mean = 306.0s
    data = {}
    for idx, dur in enumerate(durations_list):
        data[f"tests/test_suite_{idx:02d}.py"] = [("test_case", dur)]

    junit_file.write_text(_create_junit_xml(data), encoding="utf-8")

    cmd = [
        str(REBALANCE_BIN),
        "--junit", str(junit_file),
        "--target-bin-duration", "300",
        "--format", "json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    plan = json.loads(res.stdout)

    shards = plan["shards"]
    durations = [s["duration"] for s in shards]
    mean_dur = sum(durations) / len(durations)
    max_dur = max(durations)
    min_dur = min(durations)

    ratio_max = (max_dur - mean_dur) / mean_dur
    assert ratio_max <= 0.15, (
        f"Max duration {max_dur:.2f}s is {ratio_max * 100:.2f}% above mean {mean_dur:.2f}s (budget <= 15%)"
    )
    ratio_min = (mean_dur - min_dur) / mean_dur
    assert ratio_min <= 0.15, (
        f"Min duration {min_dur:.2f}s is {ratio_min * 100:.2f}% below mean {mean_dur:.2f}s"
    )


def test_large_scale_distribution_balance(tmp_path: pathlib.Path):
    """Verify large test population (120 files, ~3600s, targeting 12 shards) meets 15% rule."""
    junit_file = tmp_path / "junit_large.xml"
    rng = random.Random(999)
    # Generate 120 files with random durations between 5s and 60s
    data = {}
    for i in range(120):
        dur = rng.uniform(5.0, 60.0)
        data[f"tests/test_large_{i:03d}.py"] = [("t", dur)]

    junit_file.write_text(_create_junit_xml(data), encoding="utf-8")

    cmd = [
        str(REBALANCE_BIN),
        "--junit", str(junit_file),
        "--target-bin-duration", "300",
        "--format", "json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    plan = json.loads(res.stdout)

    shards = plan["shards"]
    assert len(shards) >= 10
    durations = [s["duration"] for s in shards]
    mean_dur = sum(durations) / len(durations)
    max_dur = max(durations)

    ratio = (max_dur - mean_dur) / mean_dur
    assert ratio <= 0.15, f"Large scale max deviation {ratio * 100:.2f}% exceeds 15%"


def test_all_files_map_exactly_once(tmp_path: pathlib.Path):
    """Every test file in input must map to exactly one shard (union preserved, 0 duplicates)."""
    junit_file = tmp_path / "junit.xml"
    input_files = [f"tests/test_file_{i:03d}.py" for i in range(50)]
    data = {fname: [("t", float(i % 20 + 1))] for i, fname in enumerate(input_files)}
    junit_file.write_text(_create_junit_xml(data), encoding="utf-8")

    cmd = [
        str(REBALANCE_BIN),
        "--junit", str(junit_file),
        "--target-bin-duration", "150",
        "--format", "json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    plan = json.loads(res.stdout)

    all_mapped_files: list[str] = []
    for shard in plan["shards"]:
        all_mapped_files.extend(shard["suites"])

    # Check union
    assert set(all_mapped_files) == set(input_files), "Partition union does not match input set"
    # Check no duplicates
    assert len(all_mapped_files) == len(input_files), (
        f"Duplicates detected: {len(all_mapped_files)} mapped vs {len(input_files)} input files"
    )


def test_repeated_input_yields_identical_output(tmp_path: pathlib.Path):
    """Repeated runs with same input (even shuffled) must produce identical partition outputs."""
    junit1 = tmp_path / "junit1.xml"
    junit2 = tmp_path / "junit2.xml"

    files_and_durations = [
        (f"tests/test_{i:02d}.py", float((i * 17) % 50 + 5))
        for i in range(35)
    ]

    # Write in original order
    data1 = {f: [("t", d)] for f, d in files_and_durations}
    junit1.write_text(_create_junit_xml(data1), encoding="utf-8")

    # Write in shuffled order
    shuffled = list(files_and_durations)
    random.Random(1337).shuffle(shuffled)
    data2 = {f: [("t", d)] for f, d in shuffled}
    junit2.write_text(_create_junit_xml(data2), encoding="utf-8")

    cmd1 = [str(REBALANCE_BIN), "--junit", str(junit1), "--format", "json"]
    cmd2 = [str(REBALANCE_BIN), "--junit", str(junit2), "--format", "json"]

    res1 = subprocess.run(cmd1, capture_output=True, text=True, check=True)
    res2 = subprocess.run(cmd2, capture_output=True, text=True, check=True)

    plan1 = json.loads(res1.stdout)
    plan2 = json.loads(res2.stdout)

    assert plan1 == plan2, "Partitioning must be strictly deterministic regardless of input order"


def test_preserves_capability_boundaries(tmp_path: pathlib.Path):
    """Preserves CI matrix capability boundaries (node/vitest, playwright/chromium, long-poles)."""
    junit_file = tmp_path / "junit.xml"
    caps_file = tmp_path / "capabilities.json"

    # Define test files with different capability requirements
    node_files = ["tests/test_vitest_ui.py", "tests/test_frontend_modules.py"]
    browser_files = ["tests/test_browser_crawler.py", "tests/test_login_chromium.py"]
    long_pole_files = ["tests/test_heavy_pole_1.py", "tests/test_heavy_pole_2.py"]
    standard_files = [f"tests/test_std_{i:02d}.py" for i in range(20)]

    all_data = {}
    for f in node_files:
        all_data[f] = [("t", 45.0)]
    for f in browser_files:
        all_data[f] = [("t", 60.0)]
    for f in long_pole_files:
        all_data[f] = [("t", 120.0)]
    for f in standard_files:
        all_data[f] = [("t", 20.0)]

    junit_file.write_text(_create_junit_xml(all_data), encoding="utf-8")

    shard_capabilities: dict[str, list[str]] = {
        "parity-vitest": ["node"],
        "browser-fixtures": ["chromium"],
        "general-01": [],
        "general-02": [],
        "general-03": [],
    }
    caps_data = {
        "file_capabilities": {
            "tests/test_vitest_ui.py": ["node"],
            "tests/test_frontend_modules.py": ["node"],
            "tests/test_browser_crawler.py": ["chromium"],
            "tests/test_login_chromium.py": ["chromium"],
        },
        "independent_long_poles": [
            "tests/test_heavy_pole_1.py",
            "tests/test_heavy_pole_2.py",
        ],
        "shard_capabilities": shard_capabilities,
    }
    caps_file.write_text(json.dumps(caps_data, indent=2), encoding="utf-8")

    cmd = [
        str(REBALANCE_BIN),
        "--junit", str(junit_file),
        "--capabilities", str(caps_file),
        "--format", "json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    plan = json.loads(res.stdout)

    file_to_shard = {}
    for shard in plan["shards"]:
        sname = shard["name"]
        for suite in shard["suites"]:
            file_to_shard[suite] = sname

    # Check node files only in node shards
    for f in node_files:
        shard_name = file_to_shard[f]
        assert "node" in shard_capabilities.get(shard_name, []), (
            f"{f} requires node but placed in {shard_name}"
        )

    # Check chromium files only in chromium shards
    for f in browser_files:
        shard_name = file_to_shard[f]
        assert "chromium" in shard_capabilities.get(shard_name, []), (
            f"{f} requires chromium but placed in {shard_name}"
        )

    # Check long pole files are never in the same shard
    pole1_shard = file_to_shard["tests/test_heavy_pole_1.py"]
    pole2_shard = file_to_shard["tests/test_heavy_pole_2.py"]
    assert pole1_shard != pole2_shard, (
        f"Independent long poles placed in same shard: {pole1_shard}"
    )


def test_cli_check_mode(tmp_path: pathlib.Path):
    """--check mode returns exit 0 for balanced valid plan, exit 1 for unbalanced plan."""
    junit_file = tmp_path / "junit.xml"
    plan_good = tmp_path / "plan_good.json"
    plan_bad = tmp_path / "plan_bad.json"

    data = {
        "tests/test_1.py": [("t", 100.0)],
        "tests/test_2.py": [("t", 100.0)],
        "tests/test_3.py": [("t", 105.0)],
        "tests/test_4.py": [("t", 95.0)],
    }
    junit_file.write_text(_create_junit_xml(data), encoding="utf-8")

    # Balanced plan: 2 shards of 200s and 200s (diff 0%)
    good_data = {
        "shards": [
            {"name": "shard-1", "suites": ["tests/test_1.py", "tests/test_2.py"]},
            {"name": "shard-2", "suites": ["tests/test_3.py", "tests/test_4.py"]},
        ],
    }
    plan_good.write_text(json.dumps(good_data), encoding="utf-8")

    # Unbalanced plan: 1 shard of 300s, 1 shard of 100s (diff > 15%)
    bad_data = {
        "shards": [
            {"name": "shard-1", "suites": ["tests/test_1.py", "tests/test_2.py", "tests/test_3.py"]},
            {"name": "shard-2", "suites": ["tests/test_4.py"]},
        ],
    }
    plan_bad.write_text(json.dumps(bad_data), encoding="utf-8")

    # Check good plan passes
    cmd_good = [
        str(REBALANCE_BIN),
        "--junit", str(junit_file),
        "--plan", str(plan_good),
        "--check",
    ]
    res_good = subprocess.run(cmd_good, capture_output=True, text=True, check=False)
    assert res_good.returncode == 0, f"Expected 0, got {res_good.returncode}: {res_good.stderr}"

    # Check bad plan fails
    cmd_bad = [
        str(REBALANCE_BIN),
        "--junit", str(junit_file),
        "--plan", str(plan_bad),
        "--check",
    ]
    res_bad = subprocess.run(cmd_bad, capture_output=True, text=True, check=False)
    assert res_bad.returncode == 1, f"Expected 1, got {res_bad.returncode}: {res_bad.stdout}"


def test_matrix_format_and_unmeasured_files(tmp_path: pathlib.Path):
    """Tests matrix format output and handling unmeasured files via --test-files."""
    junit_file = tmp_path / "junit.xml"
    test_files_list = tmp_path / "all_tests.txt"

    data = {
        "tests/test_measured_1.py": [("t", 150.0)],
        "tests/test_measured_2.py": [("t", 150.0)],
    }
    junit_file.write_text(_create_junit_xml(data), encoding="utf-8")

    test_files_list.write_text(
        "tests/test_measured_1.py\n"
        "tests/test_measured_2.py\n"
        "tests/test_unmeasured_3.py\n"
        "tests/test_unmeasured_4.py\n",
        encoding="utf-8",
    )

    cmd = [
        str(REBALANCE_BIN),
        "--junit", str(junit_file),
        "--test-files", str(test_files_list),
        "--default-duration", "5.0",
        "--format", "matrix",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    yaml_text = res.stdout

    assert "strategy:" in yaml_text
    assert "matrix:" in yaml_text
    assert "include:" in yaml_text
    assert "tests/test_unmeasured_3.py" in yaml_text
    assert "tests/test_unmeasured_4.py" in yaml_text


def test_selftest_passes():
    """Verify toolchain --selftest convention passes with 0 exit code."""
    cmd = [str(REBALANCE_BIN), "--selftest"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert "SELFTEST PASS" in res.stdout
    assert res.returncode == 0



# ---- fixer round (row863-fx3 correctness REFUTE F1-F4) ----------------------

def _run(args: list[str], cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(REBALANCE_BIN), *args], capture_output=True, text=True, check=False, cwd=cwd)


def test_f1_small_feasible_instance_meets_15_percent_exactly(tmp_path: pathlib.Path):
    """durations [50,100,100,100,150,150,250] over 3 shards: LPT alone yields
    350/300/250 (16.67% over mean); the feasible 300/300/300 must be found."""
    dur = {"a.py": 50, "b.py": 100, "c.py": 100, "d.py": 100, "e.py": 150, "f.py": 150, "g.py": 250}
    dpath = tmp_path / "d.json"
    dpath.write_text(json.dumps(dur), encoding="utf-8")
    res = _run(["--durations", str(dpath), "--num-shards", "3"])
    assert res.returncode == 0, res.stderr
    plan = json.loads(res.stdout)
    assert sorted(s["duration"] for s in plan["shards"]) == [300.0, 300.0, 300.0]
    assert plan["summary"]["max_deviation_percent"] == 0.0
    chk = _run(["--durations", str(dpath), "--num-shards", "3", "--check"])
    assert chk.returncode == 0, chk.stdout + chk.stderr


def test_f2_classname_resolves_to_the_real_module_file(tmp_path: pathlib.Path):
    """A pytest class testcase (classname pkg.module.Class) maps to pkg/module.py,
    never to a nonexistent pkg/module/Class.py; one real file stays one file."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("class TestGroup:\n    def test_x(self):\n        pass\n", encoding="utf-8")
    xml = (
        '<testsuites><testsuite name="s">'
        '<testcase classname="tests.test_sample.TestGroup" name="test_x" time="1.5"/>'
        '<testcase classname="tests.test_sample" name="test_y" time="2.0"/>'
        "</testsuite></testsuites>"
    )
    (tmp_path / "j.xml").write_text(xml, encoding="utf-8")
    res = _run(["--junit", "j.xml", "--dump-durations"], cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == {"tests/test_sample.py": 3.5}
    # negative control: a classname that resolves to no file is refused, not invented
    (tmp_path / "k.xml").write_text(
        '<testsuites><testsuite name="s"><testcase classname="tests.test_ghost.TestX" name="t" time="1"/></testsuite></testsuites>',
        encoding="utf-8",
    )
    res = _run(["--junit", "k.xml", "--dump-durations"], cwd=tmp_path)
    assert res.returncode == 2 and "resolves to no file" in res.stderr


def test_f3_infeasible_capability_assignment_is_refused_before_emission(tmp_path: pathlib.Path):
    """A chromium-required file with only a python-only shard declared: the
    planner refuses (rc=1) instead of emitting a plan --check would reject."""
    caps = {
        "file_capabilities": {"tests/test_browser.py": ["chromium"]},
        "shard_capabilities": {"py-only": ["python"]},
        "independent_long_poles": [],
    }
    (tmp_path / "caps.json").write_text(json.dumps(caps), encoding="utf-8")
    (tmp_path / "d.json").write_text(json.dumps({"tests/test_browser.py": 10, "tests/test_a.py": 10}), encoding="utf-8")
    res = _run(["--durations", str(tmp_path / "d.json"), "--capabilities", str(tmp_path / "caps.json")])
    assert res.returncode == 1, (res.returncode, res.stdout[:200])
    assert "infeasible capability assignment" in res.stderr and "tests/test_browser.py" in res.stderr
    assert res.stdout == ""
    # positive control: add a chromium shard and the same input is planned
    caps["shard_capabilities"]["browser"] = ["python", "chromium"]
    (tmp_path / "caps.json").write_text(json.dumps(caps), encoding="utf-8")
    res = _run(["--durations", str(tmp_path / "d.json"), "--capabilities", str(tmp_path / "caps.json"), "--check"])
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize(
    "args, needle",
    [
        (["--check", "--plan", "MISSING/plan.json", "--durations", "D"], "plan file not found"),
        (["--check", "--junit", "MISSING/junit.xml"], "JUnit report not found"),
        (["--check", "--durations", "D", "--capabilities", "MISSING/caps.json"], "capability file not found"),
        (["--check"], "no test durations"),
        (["--check", "--durations", "NAN"], "not finite"),
    ],
)
def test_f4_missing_or_invalid_evidence_never_passes(tmp_path: pathlib.Path, args: list[str], needle: str):
    """Absent JUnit/capabilities/plan, empty input and NaN durations are rc=2
    with the reason on stderr -- never CHECK PASSED."""
    (tmp_path / "d.json").write_text(json.dumps({"tests/test_a.py": 10.0}), encoding="utf-8")
    (tmp_path / "nan.json").write_text('{"tests/test_a.py": "nan"}', encoding="utf-8")
    argv = [
        str(tmp_path / "d.json") if a == "D" else str(tmp_path / "nan.json") if a == "NAN" else a.replace("MISSING", str(tmp_path / "missing"))
        for a in args
    ]
    res = _run(argv)
    assert res.returncode == 2, (res.returncode, res.stdout, res.stderr)
    assert needle in res.stderr
    assert "CHECK PASSED" not in res.stdout
