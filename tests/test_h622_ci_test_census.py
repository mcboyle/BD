"""H622: every tracked Python test path is scheduled or explicitly exempt.

Denominator: git ls-files -- tests/*.py (including support and nested fixtures).
Module-scoped tests are exempt from the per-PR repository gate because they run
in affected-module bands. The frozen legacy baseline records unclassified debt,
not execution coverage. Support paths below are explicit, so a new test or helper
cannot silently exempt itself by name. Required acceptance modules override the
module exemption and must run in exactly one CI gate shard.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

from tools import ci_shards

BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
# withdrawn 2026-09-23 (PM ruling A, QUESTION-T91-SQLITE-RATIO-S2-B): tests/test_inmemory_sqlite_fixture.py is a wall-clock ratio benchmark (hosted 4-core runner 3.3-3.5x < 4x); it stays BD_GATE_SCOPE=module, as before H622.
ACCEPTANCE = {
    "tests/test_adaptive_chunk_sizing.py",
    "tests/test_esxi_vm_clone_harness.py",
    "tests/test_local_wheelhouse.py",
    "tests/test_proactive_token_refresh.py",
    "tests/test_shard_rebalancer.py",
    "tests/test_sparse_worktree.py",
    "tests/test_video_dedup.py",
    "tests/test_zero_copy_assembly.py",
}

# Non-collected support modules / fixture inputs; explicit identities, no glob.
SUPPORT_EXEMPTIONS = {
    "tests/_child_guard.py": "support module or fixture input; not a collected test suite",
    "tests/_cockpit_tasks.py": "support module or fixture input; not a collected test suite",
    "tests/_cut_quality_test_support.py": "support module or fixture input; not a collected test suite",
    "tests/_env.py": "support module or fixture input; not a collected test suite",
    "tests/_event_loop_guard.py": "support module or fixture input; not a collected test suite",
    "tests/_phase_scripts/__init__.py": "support module or fixture input; not a collected test suite",
    "tests/_phase_scripts/phase02_selector.py": "support module or fixture input; not a collected test suite",
    "tests/_phase_scripts/phase03_live_template.py": "support module or fixture input; not a collected test suite",
    "tests/_phase_scripts/phases04_06.py": "support module or fixture input; not a collected test suite",
    "tests/_phase_scripts/phases07_12.py": "support module or fixture input; not a collected test suite",
    "tests/_phase_scripts/phases13_18.py": "support module or fixture input; not a collected test suite",
    "tests/_phase_scripts/phases19_24.py": "support module or fixture input; not a collected test suite",
    "tests/_phase_scripts/phases25_30.py": "support module or fixture input; not a collected test suite",
    "tests/_phase_scripts/phases31_40.py": "support module or fixture input; not a collected test suite",
    "tests/_row_census_pin.py": "support module or fixture input; not a collected test suite",
    "tests/_run_context.py": "support module or fixture input; not a collected test suite",
    "tests/_socket_record.py": "support module or fixture input; not a collected test suite",
    "tests/_sys_modules_guard.py": "support module or fixture input; not a collected test suite",
    "tests/_timeout_reap.py": "support module or fixture input; not a collected test suite",
    "tests/_tmproot.py": "support module or fixture input; not a collected test suite",
    "tests/capture_lanes.py": "support module or fixture input; not a collected test suite",
    "tests/ci_workflow_model.py": "support module or fixture input; not a collected test suite",
    "tests/conftest.py": "support module or fixture input; not a collected test suite",
    "tests/fixtures/code_intelligence/semantic/after/sample.py": "support module or fixture input; not a collected test suite",
    "tests/fixtures/code_intelligence/semantic/before/sample.py": "support module or fixture input; not a collected test suite",
    "tests/fixtures/shuffle_control/order_probe_alpha.py": "support module or fixture input; not a collected test suite",
    "tests/fixtures/shuffle_control/order_probe_beta.py": "support module or fixture input; not a collected test suite",
    "tests/frontend_vitest.py": "support module or fixture input; not a collected test suite",
    "tests/mod3_pg_isolation.py": "support module or fixture input; not a collected test suite",
    "tests/perf_row1016_lock_hold_probe.py": "support module or fixture input; not a collected test suite",
    "tests/provisioner_probe.py": "support module or fixture input; not a collected test suite",
    "tests/python_source.py": "support module or fixture input; not a collected test suite",
    "tests/rate_limit_seam.py": "support module or fixture input; not a collected test suite",
    "tests/registrable_domain_census.py": "support module or fixture input; not a collected test suite",
    "tests/scan_wait.py": "support module or fixture input; not a collected test suite",
    "tests/shell_source.py": "support module or fixture input; not a collected test suite",
    "tests/tracked_source.py": "support module or fixture input; not a collected test suite",
}


def _tracked_python(repo: Path) -> set[str]:
    return set(subprocess.check_output(
        ["git", "-C", str(repo), "ls-files", "--", "tests/*.py"], text=True).splitlines())


def _assert_census(repo: Path, scheduled: set[str]) -> dict[str, str]:
    tracked = _tracked_python(repo)
    baseline = {line.strip() for line in (repo / "tests/gate_scope_baseline.txt").read_text().splitlines()
                if line.strip() and not line.startswith("#")}
    exemptions = {}
    for path in tracked - scheduled:
        if path in SUPPORT_EXEMPTIONS:
            exemptions[path] = SUPPORT_EXEMPTIONS[path]
        elif ci_shards.gate_scope((repo / path).read_text()) == "module":
            exemptions[path] = "BD_GATE_SCOPE=module: affected-module band, not per-PR gate"
        elif path in baseline:
            exemptions[path] = "frozen gate_scope_baseline.txt: legacy classification debt"
    missing = sorted(tracked - scheduled - exemptions.keys())
    assert tracked, "H622: empty tracked Python denominator"
    assert not missing, f"H622: tracked Python paths neither scheduled nor exempt: {missing}"
    assert not (scheduled - tracked), f"H622: scheduled paths not tracked: {sorted(scheduled - tracked)}"
    assert not (SUPPORT_EXEMPTIONS.keys() - tracked), "H622: stale support exemptions"
    assert len(tracked) == len(scheduled) + len(exemptions)
    return exemptions


def _scheduled() -> set[str]:
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    runs = [str(step.get("run", "")) for step in jobs["postgres-integration"]["steps"]
            if "python -m pytest" in str(step.get("run", ""))]
    assert len(runs) == 1, "H622: PostgreSQL execution census changed shape"
    command = shlex.split(runs[0].replace("\\\n", ""))
    assert command[:4] == ["python", "-m", "pytest", "-q"]
    postgres = command[4:]
    assert postgres and all(path.startswith("tests/") and path.endswith(".py") for path in postgres)
    gates = {path for files in ci_shards.shards(ROOT).values() for path in files}
    return gates | ci_shards.process_tests(ROOT) | set(postgres)


def test_required_acceptance_modules_run_once_in_ci():
    table = ci_shards.shards(ROOT)
    executed = [path for files in table.values() for path in files]
    assert executed.count("tests/test_keepalive_default_off.py") == 1
    wrong = {path: executed.count(path) for path in sorted(ACCEPTANCE)
             if executed.count(path) != 1}
    assert not wrong, f"H622: acceptance modules must run exactly once in CI: {wrong}"


def test_every_tracked_python_path_is_scheduled_or_exempt():
    exemptions = _assert_census(ROOT, _scheduled())
    assert not (ACCEPTANCE & exemptions.keys()), "H622: required acceptance modules were exempted"


def test_a_new_unreferenced_test_fails_the_tree_census(tmp_path):
    # A real Git index supplies the denominator; no mock of the walk or checker.
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for path in SUPPORT_EXEMPTIONS:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
    (repo / "tests/gate_scope_baseline.txt").write_text("")
    control = "tests/test_control.py"
    (repo / control).write_text("def test_control(): pass\n")
    paths = [*SUPPORT_EXEMPTIONS, "tests/gate_scope_baseline.txt", control]
    subprocess.run(["git", "-C", str(repo), "add", "--", *paths], check=True)
    _assert_census(repo, {control})
    newcomer = "tests/test_h622_unreferenced.py"
    (repo / newcomer).write_text("def test_new(): pass\n")
    subprocess.run(["git", "-C", str(repo), "add", "--", newcomer], check=True)
    with pytest.raises(AssertionError, match="H622:.*neither scheduled nor exempt.*test_h622_unreferenced"):
        _assert_census(repo, {control})
    _assert_census(repo, {control, newcomer})


def test_ci_provisions_ffmpeg_for_the_video_acceptance_suite():
    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["gate-suites"]
    installs = [str(step.get("run", "")) for step in job["steps"]
                if "apt-get install" in str(step.get("run", ""))]
    assert any("ffmpeg" in shlex.split(line) for run in installs for line in run.splitlines()
               if "apt-get install" in line), "H622: video acceptance CI needs ffmpeg installed"
    assert any("command -v ffmpeg" in run for run in installs), "H622: verify ffmpeg before pytest"


def test_process_census_uses_the_registry_that_ci_executes():
    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["process-tests"]
    runs = [str(step.get("run", "")) for step in job["steps"]]
    assert any("tests/PROCESS_TESTS.txt" in run and 'pytest "${files[@]}"' in run
               for run in runs), "H622: process registry is not executed by CI"
