"""Executable capture.sh lane-boundary regressions.

The probe runs the real script only through suite-exit selection. All mutable
paths and service commands are redirected into ``tmp_path``.
"""

from __future__ import annotations

import shutil
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
    # Anchors updated at v3.66.1099: capture.sh's output directory is keyed by
    # run id (backlog 5), so the old fixed literals no longer exist.
    replacements = {
        'OUT="/tmp/bd_capture-${CAPTURE_RUN_ID}"': 'OUT="${CAPTURE_TEST_OUT:?}"',
        'ARCHIVE="/tmp/bd_capture-${CAPTURE_RUN_ID}.tar.gz"': (
            'ARCHIVE="${CAPTURE_TEST_ARCHIVE:?}"'
        ),
    }
    for old, new in replacements.items():
        # SAY WHAT WAS BEING LOOKED FOR. This was a bare `assert
        # probe.count(old) == 1`, which reported `assert 0 == 1` when the
        # anchors moved -- true, and useless. A substitution guard that does not
        # name its subject sends the reader to the wrong file.
        assert probe.count(old) == 1, (
            f"capture.sh no longer contains {old!r} exactly once "
            f"(found {probe.count(old)}); this harness rewrites that line to "
            "redirect output into the sandbox, so a moved anchor silently "
            "stops sandboxing and the probe writes to the real path")
        probe = probe.replace(old, new)
    probe += r'''
printf 'PARALLEL_EXIT=%s\nSERIAL_EXIT=%s\nRESULTS_EXIT=%s\nSKIP_BASELINE_EXIT=%s\nSUITE_EXIT=%s\n' \
  "$PARALLEL_EXIT" "$SERIAL_EXIT" "$RESULTS_EXIT" "$SKIP_BASELINE_EXIT" "$SUITE_EXIT" \
  > "${CAPTURE_PROBE_RESULT:?}"
exit "$SUITE_EXIT"
'''
    _write_executable(path, probe)

    # STAGE THE REAL scripts/lib BESIDE THE PROBE. capture.sh sources its
    # libraries with `. "$(dirname "$0")/scripts/lib/..."` and the probe is
    # written to tmp_path, so every one of those sources FAILED here -- visibly,
    # in this harness's own captured stderr, for as long as the libraries have
    # existed. It never failed a test because capture.sh runs `set -uo pipefail`
    # with no `-e`: a failed source does not abort, the functions simply do not
    # exist, and nothing this harness asserted happened to call them. Measured
    # at v3.66.1111, when run_with_heartbeat moved into a library and the lanes
    # DO call it: exit 127 instead of the expected lane exit.
    lib_src = CAPTURE_SH.parent / "scripts" / "lib"
    lib_dst = path.parent / "scripts" / "lib"
    lib_dst.mkdir(parents=True, exist_ok=True)
    staged = 0
    for lib in sorted(lib_src.glob("*.sh")):
        shutil.copy2(lib, lib_dst / lib.name)
        staged += 1
    assert staged, (
        f"no shell libraries were staged from {lib_src}; the probe would run "
        "with every `. scripts/lib/...` failing and would prove nothing about "
        "the code those libraries hold")


def _run_probe(
    tmp_path: Path,
    *,
    parallel_exit: int = 0,
    serial_exit: int = 0,
    results_exit: int = 0,
    skip_baseline_exit: int = 0,
    frontend_ready: bool = True,
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
    if frontend_ready:
        (fake_home / "frontend" / "dist").mkdir(parents=True)
        (fake_home / "frontend" / "dist" / "index.html").write_text(
            "<!doctype html><title>capture probe</title>\n",
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

if args[:1] == ["tools/check_skip_baseline.py"]:
    raise SystemExit(int(os.environ.get("FAKE_SKIP_BASELINE_EXIT", "0")))

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
            "FAKE_SKIP_BASELINE_EXIT": str(skip_baseline_exit),
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
    exits = {}
    if result_path.is_file():
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
        "SKIP_BASELINE_EXIT": 0,
        "SUITE_EXIT": 0,
    }


def test_capture_runtime_fails_before_pytest_when_spa_build_missing(
    tmp_path,
) -> None:
    completed, exits, calls = _run_probe(tmp_path, frontend_ready=False)

    assert completed.returncode == 2
    assert "frontend/dist/index.html is missing" in (
        completed.stdout + completed.stderr
    )
    assert exits == {}
    assert not any(call[:2] == ["-m", "pytest"] for call in calls)


@pytest.mark.parametrize(
    ("parallel_exit", "serial_exit", "results_exit", "skip_baseline_exit", "expected"),
    [
        pytest.param(5, 0, 0, 12, 5, id="empty-parallel-lane"),
        pytest.param(0, 5, 0, 12, 5, id="empty-serial-lane"),
        pytest.param(7, 0, 0, 12, 7, id="parallel-lane-failure"),
        pytest.param(0, 9, 0, 12, 9, id="serial-lane-failure"),
        pytest.param(0, 0, 11, 12, 11, id="converter-failure-precedence"),
        pytest.param(0, 0, 0, 12, 12, id="skip-reconciliation-reaches-verdict"),
    ],
)
def test_capture_runtime_propagates_lane_and_converter_exits(
    tmp_path,
    parallel_exit,
    serial_exit,
    results_exit,
    skip_baseline_exit,
    expected,
) -> None:
    completed, exits, _ = _run_probe(
        tmp_path,
        parallel_exit=parallel_exit,
        serial_exit=serial_exit,
        results_exit=results_exit,
        skip_baseline_exit=skip_baseline_exit,
    )

    assert completed.returncode == expected, completed.stdout + completed.stderr
    assert exits["SUITE_EXIT"] == expected
