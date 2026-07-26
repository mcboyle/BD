"""Executable capture.sh lane-boundary regressions.

The probe runs the real script only through suite-exit selection. All mutable
paths and service commands are redirected into ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SH = REPO_ROOT / "capture.sh"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _build_probe(path: Path) -> None:
    source = CAPTURE_SH.read_text(encoding="utf-8")
    sentinel = "# ── [2b/9]"
    assert source.count(sentinel) == 1
    probe = source.split(sentinel, 1)[0]
    replacements = {
        'OUT="/tmp/bd_capture"': 'OUT="${CAPTURE_TEST_OUT:?}"',
        'ARCHIVE="/tmp/bd_capture.tar.gz"': (
            'ARCHIVE="${CAPTURE_TEST_ARCHIVE:?}"'
        ),
    }
    for old, new in replacements.items():
        assert probe.count(old) == 1
        probe = probe.replace(old, new)
    probe += r'''
printf 'PARALLEL_EXIT=%s\nSERIAL_EXIT=%s\nRESULTS_EXIT=%s\nSUITE_EXIT=%s\n' \
  "$PARALLEL_EXIT" "$SERIAL_EXIT" "$RESULTS_EXIT" "$SUITE_EXIT" \
  > "${CAPTURE_PROBE_RESULT:?}"
exit "$SUITE_EXIT"
'''
    _write_executable(path, probe)


def _run_probe(
    tmp_path: Path,
    *,
    parallel_exit: int = 0,
    serial_exit: int = 0,
    results_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], dict[str, int], list[list[str]]]:
    fake_home = tmp_path / "BulkDownloader"
    fake_bin = tmp_path / "bin"
    (fake_home / "bulk_downloader").mkdir(parents=True)
    (fake_home / "venv" / "bin").mkdir(parents=True)
    fake_bin.mkdir()
    (fake_home / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "capture-probe"\n',
        encoding="utf-8",
    )
    (fake_home / "CHANGELOG.md").write_text(
        "# capture probe\n",
        encoding="utf-8",
    )

    python_log = tmp_path / "python.jsonl"
    fake_python = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with open(os.environ["CAPTURE_FAKE_PYTHON_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")

if args[:2] == ["-m", "pytest"]:
    marker = next(
        value for value in ("capture_parallel", "capture_serial")
        if value in args
    )
    code = int(os.environ[f"FAKE_{marker.upper()}_EXIT"])
    junit = next(
        value.split("=", 1)[1]
        for value in args
        if value.startswith("--junitxml=")
    )
    Path(junit).parent.mkdir(parents=True, exist_ok=True)
    if code == 5:
        body = '<testsuites tests="0"></testsuites>\n'
    else:
        body = (
            '<testsuites tests="1"><testsuite tests="1">'
            f'<testcase classname="fake" name="{marker}"/>'
            '</testsuite></testsuites>\n'
        )
    Path(junit).write_text(body, encoding="utf-8")
    raise SystemExit(code)

if args[:1] == ["tools/pytest_capture_results.py"]:
    raise SystemExit(int(os.environ.get("FAKE_RESULTS_EXIT", "0")))

raise SystemExit(0)
'''
    _write_executable(fake_home / "venv" / "bin" / "python", fake_python)
    _write_executable(
        fake_home / "venv" / "bin" / "pip",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    _write_executable(
        fake_bin / "sudo",
        '#!/usr/bin/env bash\nexec "$@"\n',
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/usr/bin/env bash
if [ "${1:-}" = "is-active" ]; then exit 3; fi
exit 0
""",
    )
    _write_executable(fake_bin / "sleep", "#!/usr/bin/env bash\nexit 0\n")

    probe = tmp_path / "capture-probe.sh"
    result_path = tmp_path / "probe-results.txt"
    _build_probe(probe)
    env = dict(os.environ)
    env.update(
        {
            "BD_HOME": str(fake_home),
            "CAPTURE_TEST_OUT": str(tmp_path / "capture-out"),
            "CAPTURE_TEST_ARCHIVE": str(tmp_path / "capture.tar.gz"),
            "CAPTURE_PROBE_RESULT": str(result_path),
            "CAPTURE_FAKE_PYTHON_LOG": str(python_log),
            "FAKE_CAPTURE_PARALLEL_EXIT": str(parallel_exit),
            "FAKE_CAPTURE_SERIAL_EXIT": str(serial_exit),
            "FAKE_RESULTS_EXIT": str(results_exit),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        }
    )
    completed = subprocess.run(
        ["bash", str(probe), "--workers=60", "--summary"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    exits = {
        key: int(value)
        for key, value in (
            line.split("=", 1)
            for line in result_path.read_text(encoding="utf-8").splitlines()
        )
    }
    calls = [
        json.loads(line)
        for line in python_log.read_text(encoding="utf-8").splitlines()
    ]
    return completed, exits, calls


def _pytest_call(calls: list[list[str]], marker: str) -> list[str]:
    matches = [
        call
        for call in calls
        if call[:2] == ["-m", "pytest"] and marker in call
    ]
    assert len(matches) == 1
    return matches[0]


def _value_after(arguments: list[str], option: str) -> str:
    index = arguments.index(option)
    return arguments[index + 1]


def test_capture_runtime_parses_workers_and_routes_only_parallel(tmp_path) -> None:
    completed, exits, calls = _run_probe(tmp_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "workers : 60" in completed.stdout
    parallel = _pytest_call(calls, "capture_parallel")
    serial = _pytest_call(calls, "capture_serial")
    assert _value_after(parallel, "-n") == "60"
    assert _value_after(serial, "-n") == "0"
    assert exits == {
        "PARALLEL_EXIT": 0,
        "SERIAL_EXIT": 0,
        "RESULTS_EXIT": 0,
        "SUITE_EXIT": 0,
    }


@pytest.mark.parametrize(
    ("parallel_exit", "serial_exit", "results_exit", "expected"),
    [
        pytest.param(5, 0, 0, 5, id="empty-parallel-lane"),
        pytest.param(0, 5, 0, 5, id="empty-serial-lane"),
        pytest.param(7, 0, 0, 7, id="parallel-lane-failure"),
        pytest.param(0, 9, 0, 9, id="serial-lane-failure"),
        pytest.param(0, 0, 11, 11, id="converter-failure-precedence"),
    ],
)
def test_capture_runtime_propagates_lane_and_converter_exits(
    tmp_path,
    parallel_exit,
    serial_exit,
    results_exit,
    expected,
) -> None:
    completed, exits, _ = _run_probe(
        tmp_path,
        parallel_exit=parallel_exit,
        serial_exit=serial_exit,
        results_exit=results_exit,
    )

    assert completed.returncode == expected, completed.stdout + completed.stderr
    assert exits["SUITE_EXIT"] == expected
