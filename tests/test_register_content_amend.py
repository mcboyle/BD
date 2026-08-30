"""The register amendment tool is a narrow, atomic compare-and-swap."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
from types import ModuleType

import pytest


# This exercises a single register-mutation tool, but is pinned explicitly in
# CI because that tool is a release-register safety boundary.
BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain" / "bin" / "bd-register-amend"
PARSER = ROOT / "project-knowledge" / "build_current_overlay.py"
_CANONICAL_HEADER = re.compile(
    r"<!-- canonical-task-register schema=1 rows=\d+ open=\d+ "
    r"ids-sha256=[0-9a-f]{64} -->"
)


def _derive(repo: Path, text: str) -> tuple[int, int, str, list[str]]:
    source = repo / "project-knowledge" / "build_current_overlay.py"
    spec = importlib.util.spec_from_file_location("test_current_overlay", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.derive_backlog(text)
    assert result is not None
    return result


def _marker(repo: Path, text: str) -> str:
    rows, opened, digest, _ = _derive(repo, text)
    return (
        f"<!-- canonical-task-register schema=1 rows={rows} open={opened} "
        f"ids-sha256={digest} -->"
    )


def _fixture_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    knowledge = repo / "project-knowledge"
    knowledge.mkdir(parents=True)
    shutil.copyfile(PARSER, knowledge / "build_current_overlay.py")
    row = (
        "| 402 | CLOSED @1359 | Acceptance: unlocking an uninitialised vault is "
        "refused with a distinct named state |\n"
    )
    provisional = (
        "# fixture\n\n"
        "<!-- canonical-task-register schema=1 rows=0 open=0 "
        "ids-sha256=" + "0" * 64 + " -->\n\n"
        "| 401 | OPEN | preserved before |\n"
        + row
        + "| 403 | OPEN | preserved after |\n"
    )
    register = knowledge / "IMPROVEMENT_BACKLOG.md"
    register.write_text(provisional, encoding="ascii")
    register.write_text(
        provisional.replace("<!-- canonical-task-register schema=1 rows=0 open=0 " +
                            "ids-sha256=" + "0" * 64 + " -->", _marker(repo, provisional)),
        encoding="ascii",
    )
    register.chmod(0o640)
    return repo, register, row.rstrip("\n")


def _request(row: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "bd-register-amend/v1",
        "row": 402,
        "expected_status": "CLOSED @1359",
        "expected_row_sha256": hashlib.sha256(row.encode("ascii")).hexdigest(),
        "find": "is refused with a distinct named state",
        "replace": "initialises explicitly and reports its named initialized state",
    }
    payload.update(overrides)
    return payload


def _run(repo: Path, request: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(repo), "--request", str(request)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def _load_tool_module() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(
        "test_bd_register_amend", str(TOOL)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _start(
    repo: Path, request: Path, *, env: dict[str, str]
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(TOOL), "--repo", str(repo), "--request", str(request)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _write_request(path: Path, payload: dict[str, object]) -> None:
    path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _install_first_derive_barrier(repo: Path) -> None:
    parser = repo / "project-knowledge" / "build_current_overlay.py"
    with parser.open("a", encoding="ascii") as handle:
        handle.write(
            "\n"
            "import os as _bd_test_os\n"
            "from pathlib import Path as _BdTestPath\n"
            "import time as _bd_test_time\n"
            "_bd_real_derive_backlog = derive_backlog\n"
            "_bd_test_barrier_entered = False\n"
            "def derive_backlog(text):\n"
            "    global _bd_test_barrier_entered\n"
            "    barrier = _bd_test_os.environ.get("
            "'BD_REGISTER_AMEND_TEST_BARRIER')\n"
            "    if barrier and not _bd_test_barrier_entered:\n"
            "        _bd_test_barrier_entered = True\n"
            "        gate = _BdTestPath(barrier)\n"
            "        (gate / f'arrived-{_bd_test_os.getpid()}').touch()\n"
            "        while not (gate / 'release').exists():\n"
            "            _bd_test_time.sleep(0.005)\n"
            "    return _bd_real_derive_backlog(text)\n"
        )


def _wait_for(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(
                f"register amendment exited before {path.name}: "
                f"rc={process.returncode} stdout={stdout!r} stderr={stderr!r}"
            )
        if time.monotonic() >= deadline:
            process.kill()
            stdout, stderr = process.communicate()
            pytest.fail(
                f"timed out waiting for {path.name}: "
                f"stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.005)


def _directory_has_exclusive_flock(path: Path) -> bool:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def _restamp_header(repo: Path, register: Path) -> None:
    text = register.read_text(encoding="ascii")
    register.write_text(
        _CANONICAL_HEADER.sub(_marker(repo, text), text, count=1), encoding="ascii"
    )


def _missing_row(repo: Path, register: Path, old_row: str) -> None:
    register.write_text(
        register.read_text(encoding="ascii").replace(
            old_row, old_row.replace("| 402 |", "| 404 |", 1), 1
        ),
        encoding="ascii",
    )
    _restamp_header(repo, register)


def _duplicate_row(_: Path, register: Path, old_row: str) -> None:
    register.write_text(
        register.read_text(encoding="ascii") + old_row + "\n", encoding="ascii"
    )


def _stale_header(_: Path, register: Path, __: str) -> None:
    register.write_text(
        re.sub(r"open=\d+", "open=99", register.read_text(encoding="ascii"), count=1),
        encoding="ascii",
    )


def _malformed_header(_: Path, register: Path, __: str) -> None:
    register.write_text(
        register.read_text(encoding="ascii").replace("schema=1", "schema=x", 1),
        encoding="ascii",
    )


def test_amendment_is_a_one_row_atomic_compare_and_swap(tmp_path: Path) -> None:
    repo, register, old_row = _fixture_repo(tmp_path)
    before = register.read_bytes()
    before_mode = stat.S_IMODE(register.stat().st_mode)
    request_path = tmp_path / "amend.json"
    request_path.write_text(json.dumps(_request(old_row)), encoding="ascii")

    result = _run(repo, request_path)

    assert result.returncode == 0, result.stderr
    after = register.read_bytes()
    assert stat.S_IMODE(register.stat().st_mode) == before_mode
    before_lines, after_lines = before.decode("ascii").splitlines(), after.decode("ascii").splitlines()
    changed = [index for index, pair in enumerate(zip(before_lines, after_lines)) if pair[0] != pair[1]]
    assert changed == [5]
    assert after_lines[5] == (
        "| 402 | CLOSED @1359 | Acceptance: unlocking an uninitialised vault "
        "initialises explicitly and reports its named initialized state |"
    )
    assert after.decode("ascii").splitlines()[2] == _marker(repo, after.decode("ascii"))


@pytest.mark.parametrize(
    "failure_point",
    [
        "directory open",
        "directory fsync",
        "directory close",
        "lock unlock",
        "lock close",
    ],
)
def test_post_replace_failure_reports_commit_uncertain_not_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_point: str,
) -> None:
    repo, register, old_row = _fixture_repo(tmp_path)
    request_path = tmp_path / "amend.json"
    _write_request(request_path, _request(old_row))
    module = _load_tool_module()
    original_open = os.open
    original_fsync = os.fsync
    original_close = os.close
    original_flock = fcntl.flock
    directory_opens = 0
    lock_descriptor: int | None = None
    sync_descriptor: int | None = None
    faulted = False

    def injected_open(path: object, flags: int, *args: object) -> int:
        nonlocal directory_opens, faulted, lock_descriptor, sync_descriptor
        if Path(path) == register.parent and flags & getattr(os, "O_DIRECTORY", 0):
            directory_opens += 1
            if failure_point == "directory open" and directory_opens == 2:
                faulted = True
                raise OSError("injected post-replace directory open failure")
            descriptor = original_open(path, flags, *args)
            if directory_opens == 1:
                lock_descriptor = descriptor
            elif directory_opens == 2:
                sync_descriptor = descriptor
            return descriptor
        return original_open(path, flags, *args)

    def injected_fsync(descriptor: int) -> None:
        nonlocal faulted
        if (
            failure_point == "directory fsync"
            and descriptor == sync_descriptor
        ):
            faulted = True
            raise OSError("injected post-replace directory fsync failure")
        original_fsync(descriptor)

    def injected_close(descriptor: int) -> None:
        nonlocal faulted
        original_close(descriptor)
        if (
            (failure_point == "directory close" and descriptor == sync_descriptor)
            or (failure_point == "lock close" and descriptor == lock_descriptor)
        ):
            faulted = True
            raise OSError(f"injected post-replace {failure_point} failure")

    def injected_flock(descriptor: int, operation: int) -> None:
        nonlocal faulted
        if (
            failure_point == "lock unlock"
            and descriptor == lock_descriptor
            and operation == fcntl.LOCK_UN
        ):
            faulted = True
            raise OSError("injected post-replace lock unlock failure")
        original_flock(descriptor, operation)

    with monkeypatch.context() as fault_patch:
        fault_patch.setattr(module.os, "open", injected_open)
        fault_patch.setattr(module.os, "fsync", injected_fsync)
        fault_patch.setattr(module.os, "close", injected_close)
        fault_patch.setattr(module.fcntl, "flock", injected_flock)
        fault_patch.setattr(
            sys,
            "argv",
            [
                str(TOOL),
                "--repo",
                str(repo),
                "--request",
                str(request_path),
            ],
        )
        returncode = module.main()

    captured = capsys.readouterr()
    assert faulted, failure_point
    assert returncode == 3
    assert "COMMIT UNCERTAIN" in captured.err
    assert failure_point in captured.err
    assert "compare-and-swap" not in captured.err
    assert "row 402 amended" not in captured.out
    amended = register.read_text(encoding="ascii")
    assert "initialises explicitly and reports its named initialized state" in amended
    assert amended.splitlines()[2] == _marker(repo, amended)


def test_concurrent_stale_requests_have_one_success_and_one_cas_refusal(
    tmp_path: Path,
) -> None:
    repo, register, old_row = _fixture_repo(tmp_path)
    _install_first_derive_barrier(repo)
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    request_paths = [tmp_path / "amend-a.json", tmp_path / "amend-b.json"]
    replacements = [
        "initialises explicitly and reports its named initialized state",
        "initialises explicitly and reports a durable initialized state",
    ]
    for request_path, replacement in zip(request_paths, replacements):
        _write_request(request_path, _request(old_row, replace=replacement))
    env = os.environ.copy()
    env["BD_REGISTER_AMEND_TEST_BARRIER"] = str(barrier)

    processes = [_start(repo, request_paths[0], env=env)]
    _wait_for(barrier / f"arrived-{processes[0].pid}", processes[0])
    processes.append(_start(repo, request_paths[1], env=env))
    if not _directory_has_exclusive_flock(register.parent):
        _wait_for(barrier / f"arrived-{processes[1].pid}", processes[1])
    (barrier / "release").touch()
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        results.append((process.returncode, stdout, stderr))

    assert sorted(result[0] for result in results) == [0, 2], results
    winner = next(index for index, result in enumerate(results) if result[0] == 0)
    loser = 1 - winner
    assert "row 402 amended" in results[winner][1]
    assert "compare-and-swap" in results[loser][2]
    final = register.read_text(encoding="ascii")
    assert replacements[winner] in final
    assert replacements[loser] not in final
    assert final.splitlines()[2] == _marker(repo, final)


def test_amendment_targets_the_physical_row_not_an_earlier_prose_copy(
    tmp_path: Path,
) -> None:
    repo, register, old_row = _fixture_repo(tmp_path)
    original = register.read_text(encoding="ascii")
    quoted = f"quoted register evidence: {old_row}\n"
    register.write_text(
        original.replace("# fixture\n", f"# fixture\n{quoted}", 1),
        encoding="ascii",
    )
    request_path = tmp_path / "amend.json"
    request_path.write_text(json.dumps(_request(old_row)), encoding="ascii")

    result = _run(repo, request_path)

    assert result.returncode == 0, result.stderr
    after = register.read_text(encoding="ascii")
    assert quoted in after
    assert after.count("initialises explicitly and reports") == 1
    assert "| 402 | CLOSED @1359 | Acceptance:" in after


def test_refusal_leaves_the_register_byte_identical(tmp_path: Path) -> None:
    repo, register, old_row = _fixture_repo(tmp_path)
    before = register.read_bytes()
    request_path = tmp_path / "amend.json"
    request_path.write_text(
        json.dumps(_request(old_row, expected_row_sha256="0" * 64)), encoding="ascii"
    )

    result = _run(repo, request_path)

    assert result.returncode != 0
    assert "compare-and-swap" in result.stderr
    assert register.read_bytes() == before


def test_schema_rejects_unsafe_replacement_without_writing(tmp_path: Path) -> None:
    repo, register, old_row = _fixture_repo(tmp_path)
    before = register.read_bytes()
    request_path = tmp_path / "amend.json"
    request_path.write_text(
        json.dumps(_request(old_row, replace="unsafe | replacement")), encoding="ascii"
    )

    result = _run(repo, request_path)

    assert result.returncode != 0
    assert "ASCII text without newline or pipe" in result.stderr
    assert register.read_bytes() == before


@pytest.mark.parametrize(
    ("case", "overrides", "mutator"),
    [
        ("wrong status", {"expected_status": "OPEN"}, None),
        ("missing row", {}, _missing_row),
        ("duplicate row", {}, _duplicate_row),
        ("stale header", {}, _stale_header),
        ("malformed header", {}, _malformed_header),
        ("zero find matches", {"find": "does not occur"}, None),
        ("two find matches", {"find": "is"}, None),
        ("non-ASCII replacement", {"replace": "caf\u00e9"}, None),
        ("newline replacement", {"replace": "unsafe\nreplacement"}, None),
        ("pipe replacement", {"replace": "unsafe | replacement"}, None),
        ("wrong schema", {"schema": "bd-register-amend/v2"}, None),
        ("extra key", {"unexpected": "value"}, None),
        ("malformed SHA-256", {"expected_row_sha256": "not-a-sha"}, None),
        ("stale SHA-256", {"expected_row_sha256": "0" * 64}, None),
    ],
)
def test_each_refusal_preserves_the_register_byte_for_byte(
    tmp_path: Path,
    case: str,
    overrides: dict[str, object],
    mutator: object,
) -> None:
    repo, register, old_row = _fixture_repo(tmp_path)
    if mutator is not None:
        mutator(repo, register, old_row)
    before = register.read_bytes()
    request_path = tmp_path / "amend.json"
    _write_request(request_path, _request(old_row, **overrides))

    result = _run(repo, request_path)

    assert result.returncode != 0, case
    expected_diagnostic = {
        "wrong status": "compare-and-swap status mismatch",
        "missing row": "row 402 occurs 0 times",
        "duplicate row": "duplicate backlog row identity",
        "stale header": "canonical register header does not match",
        "malformed header": "canonical register header occurs other than once",
        "zero find matches": "find text must occur exactly once",
        "two find matches": "find text must occur exactly once",
        "non-ASCII replacement": "request must be an ASCII JSON object",
        "newline replacement": "must be ASCII text without newline or pipe",
        "pipe replacement": "must be ASCII text without newline or pipe",
        "wrong schema": "schema must be bd-register-amend/v1",
        "extra key": "must use exactly the bd-register-amend/v1 schema",
        "malformed SHA-256": "must be lowercase SHA-256",
        "stale SHA-256": "whole-row SHA-256 mismatch",
    }[case]
    assert expected_diagnostic in result.stderr, (case, result.stderr)
    assert register.read_bytes() == before, case


def test_help_describes_the_guarded_request_interface() -> None:
    result = subprocess.run(
        [sys.executable, str(TOOL), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--repo" in result.stdout
    assert "--request" in result.stdout
