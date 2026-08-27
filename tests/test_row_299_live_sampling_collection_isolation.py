"""Collecting the U42 tests must not change another test's sampler defaults."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_U42_TEST = _REPO / "tests" / "test_u42_resource_live_tests.py"
_PROBE_NAME = "test_unrelated_live_sampling_defaults_are_intact"
_EXPECTED_U42_TESTS = frozenset({
    "test_l31_l32_l33_registered",
    "test_resource_tests_are_not_disruptive",
    "test_trend_of_empty_series_is_none",
    "test_trend_computes_first_last_peak_growth",
    "test_trend_of_flat_series_has_zero_growth",
    "test_sampler_bails_early_on_an_unreachable_endpoint",
    "test_sampler_collects_all_samples_when_reachable",
    "test_test_sampler_restores_the_exact_prior_configuration",
    "test_all_three_warn_when_unreachable",
    "test_l31_warns_on_rising_rss",
    "test_l31_passes_on_stable_rss",
    "test_l32_warns_on_thread_growth",
    "test_l32_passes_on_stable_thread_count",
    "test_l33_passes_with_zero_orphans",
    "test_l33_warns_on_growing_orphans",
    "test_l33_is_not_exercisable_on_the_windows_shape_empty_procs",
    "test_l33_warns_on_empty_procs_with_actual_findings",
    "test_resource_tests_run_via_harness",
})
_DEFAULT_OBSERVATION = {
    "fired": 1,
    "sample_count": 5,
    "sample_gap_s": 3.0,
}


def _write_probe(case_dir: Path) -> tuple[Path, Path, Path]:
    case_dir.mkdir()
    collection_record = case_dir / "collection.json"
    observation_record = case_dir / "observation.json"
    plugin = case_dir / "row299_collection_probe.py"
    plugin.write_text(
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "_collected = {}\n"
        "def pytest_itemcollected(item):\n"
        "    name = Path(str(item.path)).name\n"
        "    _collected.setdefault(name, []).append(item.name)\n"
        "def pytest_collection_finish(session):\n"
        "    Path(os.environ['BD_ROW299_COLLECTION_RECORD']).write_text(\n"
        "        json.dumps(_collected, sort_keys=True), encoding='utf-8')\n",
        encoding="utf-8",
    )
    probe = case_dir / "test_unrelated_sampling_probe.py"
    probe.write_text(
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import live_tests.checks as checks\n"
        f"def {_PROBE_NAME}():\n"
        "    observed = {\n"
        "        'fired': 1,\n"
        "        'sample_count': checks._SAMPLE_COUNT,\n"
        "        'sample_gap_s': checks._SAMPLE_GAP_S,\n"
        "    }\n"
        "    Path(os.environ['BD_ROW299_OBSERVATION_RECORD']).write_text(\n"
        "        json.dumps(observed, sort_keys=True), encoding='utf-8')\n"
        f"    assert observed == {_DEFAULT_OBSERVATION!r}\n",
        encoding="utf-8",
    )
    return probe, collection_record, observation_record


def _run_probe(case_dir: Path, *, collect_u42: bool) -> tuple[
        subprocess.CompletedProcess[str], dict[str, list[str]], dict[str, float]]:
    probe, collection_record, observation_record = _write_probe(case_dir)
    selected = [str(probe)]
    if collect_u42:
        selected.insert(0, str(_U42_TEST))
    env = os.environ.copy()
    env.pop("BD_INSTALL_DIR", None)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    env["BD_ROW299_COLLECTION_RECORD"] = str(collection_record)
    env["BD_ROW299_OBSERVATION_RECORD"] = str(observation_record)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(case_dir), env.get("PYTHONPATH")) if part
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *selected,
            "-k",
            _PROBE_NAME,
            "-p",
            "row299_collection_probe",
            "-q",
        ],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert collection_record.is_file(), result.stdout + result.stderr
    assert observation_record.is_file(), result.stdout + result.stderr
    return (
        result,
        json.loads(collection_record.read_text(encoding="utf-8")),
        json.loads(observation_record.read_text(encoding="utf-8")),
    )


def test_negative_control_probe_alone_sees_exact_sampler_defaults(tmp_path):
    result, collected, observed = _run_probe(
        tmp_path / "without-u42", collect_u42=False)

    assert collected == {"test_unrelated_sampling_probe.py": [_PROBE_NAME]}
    assert observed == _DEFAULT_OBSERVATION
    assert result.returncode == 0, result.stdout + result.stderr


def test_collecting_u42_leaves_unrelated_test_sampler_defaults_intact(tmp_path):
    result, collected, observed = _run_probe(
        tmp_path / "with-u42", collect_u42=True)

    assert set(collected) == {
        "test_u42_resource_live_tests.py",
        "test_unrelated_sampling_probe.py",
    }
    assert set(collected["test_u42_resource_live_tests.py"]) == _EXPECTED_U42_TESTS
    assert len(collected["test_u42_resource_live_tests.py"]) == len(
        _EXPECTED_U42_TESTS)
    assert collected["test_unrelated_sampling_probe.py"] == [_PROBE_NAME]
    assert observed["fired"] == 1
    assert observed == _DEFAULT_OBSERVATION, (
        "collecting tests/test_u42_resource_live_tests.py changed the sampler "
        f"values seen by an unrelated selected test: {observed}")
    assert result.returncode == 0, result.stdout + result.stderr


def test_transform_control_imports_u42_without_asserting_sampler_values():
    code = (
        "import importlib.util\n"
        "import pathlib\n"
        "import sys\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "spec = importlib.util.spec_from_file_location('row299_u42', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
    )
    env = os.environ.copy()
    env.pop("BD_INSTALL_DIR", None)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code, str(_U42_TEST)],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
