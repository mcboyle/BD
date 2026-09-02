"""Executable capture.sh lane-boundary regressions.

The probe runs the real script only through suite-exit selection. All mutable
paths and service commands are redirected into ``tmp_path``.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SH = REPO_ROOT / "capture.sh"
BD_GATE_SCOPE = "repo-wide"
PHASE_TIMING = "00_phase_timing.tsv"
PHASE_BANNER = re.compile(r'^echo "=== \[([^]]+/9)\] (.+) ==="$')


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _phase_banners(source: str) -> list[tuple[str, str]]:
    return [
        (match.group(1), match.group(2))
        for line in source.splitlines()
        if (match := PHASE_BANNER.fullmatch(line)) is not None
    ]


def _read_phase_timing(path: Path) -> list[dict[str, str]]:
    assert path.is_file(), f"phase timing record absent: {path}"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines, f"phase timing record is empty: {path}"
    header = lines[0].split("\t")
    assert header == [
        "phase",
        "name",
        "start_epoch_seconds",
        "end_epoch_seconds",
        "exit_status",
    ]
    return [dict(zip(header, line.split("\t"), strict=True)) for line in lines[1:]]


def _assert_complete_phase_record(
    rows: list[dict[str, str]], expected: list[tuple[str, str]]
) -> None:
    assert len(expected) > 0, "phase denominator must be nonzero"
    assert len(rows) == len(expected)
    assert [(row["phase"], row["name"]) for row in rows] == expected


def _build_probe(path: Path, *, abort_phase_2: bool = False) -> None:
    source = CAPTURE_SH.read_text(encoding="utf-8")
    sentinel = "# ── [2b/9]"
    assert source.count(sentinel) == 1
    probe = source.split(sentinel, 1)[0]
    if abort_phase_2:
        banner = 'echo "=== [2/9] Full test suite (5-15 min) ==="'
        assert probe.count(banner) == 1
        probe = probe.replace(banner, banner + "\nexit 77")
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
    lock_override: Path | None = None,
    require_python_log: bool = True,
    abort_phase_2: bool = False,
) -> tuple[subprocess.CompletedProcess[str], dict[str, int], list[list[str]]]:
    fake_home = tmp_path / "BulkDownloader"
    fake_bin = tmp_path / "bin"
    lock_dir = tmp_path / "capture-lock"
    gc_guard_log = tmp_path / "gc-guard.log"
    (fake_home / "bulk_downloader").mkdir(parents=True)
    (fake_home / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    fake_bin.mkdir()
    if lock_override is None:
        lock_dir.mkdir(mode=0o700)
        lock_path = lock_dir / "capture-vault.lock"
    else:
        lock_path = lock_override
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
    _write_executable(
        tmp_path / "venv" / "bin" / "python",
        r'''#!/usr/bin/env bash
printf '%s\n' "$*" >> "${CAPTURE_GC_GUARD_LOG:?}"
exit 0
''',
    )

    probe = tmp_path / "capture-probe.sh"
    result_path = tmp_path / "probe-results.txt"
    _build_probe(probe, abort_phase_2=abort_phase_2)
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
            "CAPTURE_KEEP": "999999999",
            "CAPTURE_GC_GUARD_LOG": str(gc_guard_log),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "CAPTURE_VAULT_GLOBAL_LOCK": str(lock_path),
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
    assert gc_guard_log.is_file(), (
        "the probe did not route bd_test_root_gc through its inert sandbox "
        f"interpreter:\n{completed.stdout}{completed.stderr}"
    )
    assert "toolchain/bin/bd-gc --apply --older-than 1440 --only classified" in (
        gc_guard_log.read_text(encoding="utf-8")
    )
    assert "pruned " not in completed.stdout + completed.stderr
    exits = {}
    if result_path.is_file():
        exits = {
            key: int(value)
            for key, value in (
                line.split("=", 1)
                for line in result_path.read_text(encoding="utf-8").splitlines()
            )
        }
    if require_python_log:
        assert python_log.is_file(), (
            f"capture probe exited {completed.returncode} before invoking its "
            f"sandboxed Python subject:\n{completed.stdout}{completed.stderr}"
        )
    calls = [
        json.loads(line)
        for line in (
            python_log.read_text(encoding="utf-8").splitlines()
            if python_log.is_file()
            else []
        )
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


def test_capture_runtime_records_every_reached_phase_duration(tmp_path) -> None:
    source = CAPTURE_SH.read_text(encoding="utf-8")
    all_phases = _phase_banners(source)
    reached_phases = _phase_banners(source.split("# ── [2b/9]", 1)[0])
    assert len(all_phases) == 15, "precondition: capture.sh phase denominator"
    assert len(reached_phases) == 3, "precondition: sandbox probe phase denominator"

    completed, exits, _calls = _run_probe(tmp_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert exits["SUITE_EXIT"] == 0
    rows = _read_phase_timing(tmp_path / "capture-out" / PHASE_TIMING)
    _assert_complete_phase_record(rows, reached_phases)
    for row in rows:
        assert row["exit_status"].isdigit()
        assert int(row["end_epoch_seconds"]) - int(row["start_epoch_seconds"]) >= 0


def test_capture_source_arms_all_phase_banners_once() -> None:
    source = CAPTURE_SH.read_text(encoding="utf-8")
    banners = _phase_banners(source)
    begins = re.findall(r'^phase_begin "([^"]+)" "([^"]+)"$', source, re.MULTILINE)
    ends = re.findall(r'^phase_end "([^"]+)" ', source, re.MULTILINE)

    assert len(banners) == 15, "precondition: capture.sh phase denominator"
    assert begins == banners
    assert sorted(ends) == sorted(phase for phase, _name in banners)


def test_phase_record_checker_rejects_only_an_incomplete_population() -> None:
    expected = [("1/9", "one"), ("2/9", "two"), ("3/9", "three")]
    complete = [
        {"phase": phase, "name": name}
        for phase, name in expected
    ]

    _assert_complete_phase_record(complete, expected)
    with pytest.raises(AssertionError):
        _assert_complete_phase_record(complete[:-1], expected)


def test_capture_abort_preserves_started_phase_without_inventing_an_end(tmp_path) -> None:
    completed, _exits, _calls = _run_probe(
        tmp_path,
        abort_phase_2=True,
        require_python_log=False,
    )

    assert completed.returncode == 77
    rows = _read_phase_timing(tmp_path / "capture-out" / PHASE_TIMING)
    by_phase = {row["phase"]: row for row in rows}
    assert by_phase["1/9"]["exit_status"] == "0"
    assert by_phase["1/9"]["end_epoch_seconds"].isdigit()
    assert by_phase["2/9"]["start_epoch_seconds"].isdigit()
    assert by_phase["2/9"]["end_epoch_seconds"] == "UNKNOWN"
    assert by_phase["2/9"]["exit_status"] == "UNKNOWN"


def test_runtime_probe_owns_its_singleton_inside_a_parent_capture(
    tmp_path,
) -> None:
    """The lane probe refuses a parent's lock, then owns a scratch lock."""
    outer_dir = tmp_path / "outer-capture"
    outer_dir.mkdir(mode=0o700)
    outer_lock = outer_dir / "capture-vault.lock"
    outer_lock.touch(mode=0o600)
    outer_lock.chmod(0o600)

    with outer_lock.open("r+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        contention = subprocess.run(
            ["flock", "-n", str(outer_lock), "true"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert contention.returncode == 1, (
            "the outer lock was not actually held, so this test did not "
            "reproduce a probe nested inside a live capture"
        )
        inherited = tmp_path / "inherited"
        inherited.mkdir()
        refused, _exits, _calls = _run_probe(
            inherited,
            lock_override=outer_lock,
            require_python_log=False,
        )
        assert refused.returncode == 73, (
            "the control probe did not inherit and refuse the live capture's "
            f"contended singleton:\n{refused.stdout}{refused.stderr}"
        )
        assert "another capture owns the singleton" in refused.stderr
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        completed, exits, calls = _run_probe(isolated)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert exits["SUITE_EXIT"] == 0
    assert len(calls) >= 2, calls
    scratch_lock = isolated / "capture-lock" / "capture-vault.lock"
    assert scratch_lock.is_file(), (
        f"the nested probe did not create its singleton under tmp_path: "
        f"{scratch_lock}"
    )
    assert scratch_lock.stat().st_mode & 0o777 == 0o600
    holder_pid = scratch_lock.read_text(encoding="utf-8").split()[0]
    assert holder_pid.isdecimal(), scratch_lock.read_text(encoding="utf-8")


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
