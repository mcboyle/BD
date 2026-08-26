"""v3.66.1256 -- socket evidence survives temp-root cleanup and xdist.

The recorder used to resolve ``tempfile.gettempdir()`` after conftest had moved
that process-global value into one private root per pytest process. A serial
record was deleted before ``pytest_terminal_summary`` read it, and xdist
workers wrote outside the master's root from the start. Both failures rendered
as a clean zero even though the hook reported a connect.

These are subprocess tests because the defect is in pytest's hook ordering and
process topology. Calling ``summarize`` before session cleanup would reproduce
neither escape.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import NamedTuple

import _run_context
import _socket_record as sr


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_PAIRING_NODE = (
    "tests/test_security.py::TestPhase40CSRFAndPairing::"
    "test_pair_endpoint_registers_token"
)
_BUDGET_S = 120


class _Run(NamedTuple):
    returncode: int
    output: str
    rows_by_file: dict[str, list[dict]]


def _environment() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(_REPO / "tests") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run_pytest(arguments: list[str], *, load_repo_conftest: bool) -> _Run:
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", "-s"]
    if load_repo_conftest:
        # Generated suites live outside the checkout, so load the repository's
        # real hooks explicitly. The repo test below discovers them normally.
        argv.extend(["-p", "conftest"])
    argv.extend(arguments)
    proc = subprocess.Popen(
        argv,
        cwd=_REPO,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = proc.communicate(timeout=_BUDGET_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        output, _ = proc.communicate(timeout=10)
        raise AssertionError(
            "nested pytest exceeded %ds:\n%s" % (_BUDGET_S, output[-3000:])
        ) from None

    socket_run = sr.sink_dir() / str(proc.pid)
    rows_by_file: dict[str, list[dict]] = {}
    if socket_run.is_dir():
        for path in sorted(socket_run.glob("*.jsonl")):
            rows_by_file[path.name] = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

    # Both recorders retain real runs by design. These artificial runs have
    # already been copied into this test's assertions, so remove only their
    # exact master-pid directories rather than consuming retention slots.
    shutil.rmtree(socket_run, ignore_errors=True)
    shutil.rmtree(_run_context.sink_dir() / str(proc.pid), ignore_errors=True)
    return _Run(proc.returncode, output, rows_by_file)


def _generated_suite(tmp_path: Path, sources: list[str]) -> Path:
    suite = tmp_path / "socket-recorder-inner"
    suite.mkdir()
    for index, source in enumerate(sources):
        (suite / ("test_probe_%d.py" % index)).write_text(source, encoding="ascii")
    return suite


def test_the_pairing_reproduction_reports_the_route_lookup_after_session_cleanup():
    run = _run_pytest([_PAIRING_NODE], load_repo_conftest=False)
    assert run.returncode == 0, run.output[-3000:]
    assert "1 non-loopback attempt(s) from 1 test(s)" in run.output, run.output
    assert "8.8.8.8:53 (SOCK_DGRAM)" in run.output, run.output
    assert "UNKNOWN non-loopback" not in run.output, run.output


def test_xdist_rows_from_more_than_one_worker_reach_the_master_summary(tmp_path):
    sources = []
    for index in range(4):
        sources.append(
            "import socket\n"
            "\n"
            "def test_worker_row(worker_id):\n"
            "    assert worker_id.startswith('gw')\n"
            "    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            "    try:\n"
            "        sock.connect(('8.8.8.8', %d))\n"
            "    finally:\n"
            "        sock.close()\n" % (5300 + index)
        )
    suite = _generated_suite(tmp_path, sources)

    run = _run_pytest(
        ["-n", "2", "--dist", "loadfile", str(suite)],
        load_repo_conftest=True,
    )
    assert run.returncode == 0, run.output[-3000:]
    assert "4 non-loopback attempt(s) from 4 test(s)" in run.output, run.output
    assert "UNKNOWN non-loopback" not in run.output, run.output

    # One JSONL per pid is the recorder's write contract. Two nonempty files
    # with two distinct row pids proves this is an aggregation across workers,
    # not four tests that happened to execute in one process.
    assert len(run.rows_by_file) == 2, run.rows_by_file
    rows = [row for file_rows in run.rows_by_file.values() for row in file_rows]
    assert len(rows) == 4, rows
    assert len({row["pid"] for row in rows}) == 2, rows
    assert {row["port"] for row in rows} == {5300, 5301, 5302, 5303}, rows


def test_a_lost_measurement_reports_unknown_instead_of_zero(tmp_path):
    suite = _generated_suite(
        tmp_path,
        [
            "import socket\n"
            "import _socket_record as sr\n"
            "\n"
            "def test_delete_the_record_after_the_hook_writes_it():\n"
            "    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            "    try:\n"
            "        sock.connect(('8.8.8.8', 53))\n"
            "    finally:\n"
            "        sock.close()\n"
            "    assert sr.observed == 1\n"
            "    assert sr._sink_path.is_file()\n"
            "    sr._sink_path.unlink()\n"
        ],
    )
    run = _run_pytest([str(suite)], load_repo_conftest=True)

    assert run.returncode == 0, run.output[-3000:]
    assert "UNKNOWN non-loopback attempt count" in run.output, run.output
    assert "0 row(s) readable, 1 connects observed" in run.output, run.output
    assert "0 non-loopback attempts recorded" not in run.output, run.output


def test_a_genuinely_clean_run_reports_zero_not_unknown(tmp_path):
    suite = _generated_suite(
        tmp_path,
        [
            "def test_no_socket_connects():\n"
            "    assert 6 * 7 == 42\n"
        ],
    )
    run = _run_pytest([str(suite)], load_repo_conftest=True)

    assert run.returncode == 0, run.output[-3000:]
    assert "0 non-loopback attempts recorded (0 connects observed" in run.output, run.output
    assert "UNKNOWN non-loopback" not in run.output, run.output


def test_transform_control_imports_the_recorder_without_exercising_temp_lifecycle():
    """The anchor mutant must load and pass when no lifecycle claim is made."""
    assert sr.is_local(("127.0.0.1", 1)) is True
