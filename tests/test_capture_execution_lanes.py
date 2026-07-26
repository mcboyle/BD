from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LANES_MODULE = REPO_ROOT / "tests" / "capture_lanes.py"


def _load_lanes_module():
    assert LANES_MODULE.is_file(), "capture lane classifier is missing"
    spec = importlib.util.spec_from_file_location(
        "bd_capture_lanes_under_test", LANES_MODULE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect(marker: str, test_path: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            marker,
            test_path,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_classifier_routes_each_risky_category_to_serial() -> None:
    lanes = _load_lanes_module()

    cases = [
        ("tests/test_global_probe.py", "pytestmark = pytest.mark.bd_module_wipe"),
        ("tests/test_run_tests_contract.py", "RUNNER = 'run_tests.py'"),
        ("tests/test_browser_flow.py", ""),
        ("tests/test_service_install.py", ""),
        ("tests/test_generated_artifact_workflow.py", ""),
        ("tests/test_network_probe.py", ""),
    ]
    for path, source in cases:
        assert lanes.classify_capture_file(path, source=source) == "serial", path

    assert (
        lanes.classify_capture_file(
            "tests/test_validators.py",
            source="def test_rejects_bad_path(): assert True",
        )
        == "parallel"
    )


def test_classifier_serializes_unscoped_state_and_external_io_signals() -> None:
    lanes = _load_lanes_module()

    cases = [
        ("tests/test_state_probe.py", 'sys.modules["probe"] = object()'),
        ("tests/test_env_probe.py", 'os.environ["PROBE"] = "dirty"'),
        ("tests/test_cwd_probe.py", 'os.chdir("/tmp")'),
        ("tests/test_client_probe.py", 'import httpx\nhttpx.get("https://example")'),
        ("tests/test_transport_probe.py", "import socket\nsocket.create_connection(addr)"),
        ("tests/test_index_sync.py", 'Path("PIN_INDEX.json").read_text()'),
    ]
    for path, source in cases:
        assert lanes.classify_capture_file(path, source=source) == "serial", path


def test_allowlisted_file_cannot_bypass_dynamic_runner_import_risk() -> None:
    lanes = _load_lanes_module()
    allowlisted = "tests/test_validators.py"

    for source in (
        'import importlib\nimportlib.import_module("run_tests")',
        'from importlib import import_module as load\nload("run_tests_core")',
        '__import__("run_tests")',
    ):
        assert (
            lanes.classify_capture_file(allowlisted, source=source)
            == "serial"
        )


def test_classifier_defaults_unreviewed_files_to_serial() -> None:
    lanes = _load_lanes_module()

    assert (
        lanes.classify_capture_file(
            "tests/test_unreviewed_probe.py",
            source="def test_pure_looking_but_unreviewed(): assert True",
        )
        == "serial"
    )
    assert (
        lanes.classify_capture_file(
            REPO_ROOT / "tests" / "test_validators.py",
        )
        == "parallel"
    )
    for risky in (
        "test_v3_43_21_jd_bridge.py",
        "test_v3_66_446_scrape_listing_httpx.py",
    ):
        assert (
            lanes.classify_capture_file(REPO_ROOT / "tests" / risky)
            == "serial"
        ), risky


def test_parallel_manifest_is_explicit_complete_and_risk_free() -> None:
    lanes = _load_lanes_module()
    tests_root = REPO_ROOT / "tests"
    allowlist = lanes.parallel_allowlist()

    assert allowlist
    for relative in sorted(allowlist):
        path = tests_root / relative
        assert path.is_file(), f"stale parallel allowlist entry: {relative}"
        assert lanes.classify_capture_file(path) == "parallel", relative

    for path in tests_root.rglob("test*.py"):
        if lanes.classify_capture_file(path) == "parallel":
            assert path.relative_to(tests_root).as_posix() in allowlist


def test_real_pytest_collection_selects_safe_and_serial_lanes() -> None:
    parallel = _collect("capture_parallel", "tests/test_validators.py")
    assert parallel.returncode == 0, parallel.stdout + parallel.stderr
    assert "test_validators.py" in parallel.stdout

    serial = _collect(
        "capture_serial", "tests/test_v3_66_797_runner_isolate.py"
    )
    assert serial.returncode == 0, serial.stdout + serial.stderr
    assert "test_v3_66_797_runner_isolate.py" in serial.stdout


def test_capture_script_gives_workers_only_to_parallel_lane() -> None:
    source = (REPO_ROOT / "capture.sh").read_text(encoding="utf-8")

    assert "-m capture_parallel" in source
    assert '-n "$WORKERS"' in source
    assert '--junitxml="$OUT/02_pytest_parallel.xml"' in source

    assert "-m capture_serial" in source
    assert "-n 0" in source
    assert '--junitxml="$OUT/02_pytest_serial.xml"' in source

    assert source.count("--junit ") >= 2
    assert '"$OUT/02_pytest_parallel.xml"' in source
    assert '"$OUT/02_pytest_serial.xml"' in source
