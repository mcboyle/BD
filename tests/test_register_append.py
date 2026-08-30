"""The register append tool only publishes validated OPEN rows atomically."""

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


# The register append/close/amend paths serialize one canonical release
# authority across independent processes; that safety contract is tree-wide.
BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain" / "bin" / "bd-register-append"
AMEND_TOOL = ROOT / "toolchain" / "bin" / "bd-register-amend"
CLOSE_TOOL = ROOT / "toolchain" / "bin" / "bd-register-close"
PARSER = ROOT / "project-knowledge" / "build_current_overlay.py"
HEADER = re.compile(
    r"<!-- canonical-task-register schema=1 rows=\d+ open=\d+ "
    r"ids-sha256=[0-9a-f]{64} -->"
)


def _derive(repo: Path, text: str) -> tuple[int, int, str, list[str]]:
    spec = importlib.util.spec_from_file_location("append_overlay", repo / "project-knowledge" / "build_current_overlay.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.derive_backlog(text)
    assert result is not None
    return result


def _marker(repo: Path, text: str) -> str:
    rows, opened, digest, _ = _derive(repo, text)
    return f"<!-- canonical-task-register schema=1 rows={rows} open={opened} ids-sha256={digest} -->"


def _fixture_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    knowledge = repo / "project-knowledge"
    knowledge.mkdir(parents=True)
    shutil.copyfile(PARSER, knowledge / "build_current_overlay.py")
    provisional = (
        "# fixture\n\n"
        "<!-- canonical-task-register schema=1 rows=0 open=0 ids-sha256=" + "0" * 64 + " -->\n\n"
        "| id | status | item |\n"
        "| --- | --- | --- |\n"
        "| 401 | OPEN | preserved before |\n"
        "| 402 | CLOSED @1359 | preserved after |\n"
    )
    register = knowledge / "IMPROVEMENT_BACKLOG.md"
    register.write_text(HEADER.sub(_marker(repo, provisional), provisional), encoding="ascii")
    register.chmod(0o640)
    return repo, register


def _digest(repo: Path, register: Path) -> str:
    return _derive(repo, register.read_text(encoding="ascii"))[2]


def _request(repo: Path, register: Path, rows: list[str], **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "bd-register-append/v1",
        "expected_ids_sha256": _digest(repo, register),
        "rows": rows,
    }
    payload.update(overrides)
    return payload


def _write_request(path: Path, payload: dict[str, object]) -> None:
    path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _run(repo: Path, request: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(repo), "--request", str(request)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def _load_tool_module() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("test_bd_register_append", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _start(repo: Path, request: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(TOOL), "--repo", str(repo), "--request", str(request)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _start_close(
    repo: Path, version: str = "3.66.4321", env: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            str(CLOSE_TOOL),
            "--repo",
            str(repo),
            "--row",
            "401",
            "--version",
            version,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _start_amend(repo: Path, request: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(AMEND_TOOL), "--repo", str(repo), "--request", str(request)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if process.poll() is not None:
            out, err = process.communicate()
            pytest.fail(f"append exited before barrier: {process.returncode}, {out!r}, {err!r}")
        if time.monotonic() >= deadline:
            process.kill()
            pytest.fail("timed out waiting for append barrier")
        time.sleep(0.005)


def _arrives_before(path: Path, process: subprocess.Popen[str], seconds: float = 1.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        if process.poll() is not None:
            out, err = process.communicate()
            pytest.fail(f"writer exited before lock observation: {process.returncode}, {out!r}, {err!r}")
        time.sleep(0.005)
    return path.exists()


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


def _install_lock_barrier(repo: Path) -> None:
    parser = repo / "project-knowledge" / "build_current_overlay.py"
    with parser.open("a", encoding="ascii") as handle:
        handle.write(
            "\nimport os as _append_os\nimport sys as _append_sys\nfrom pathlib import Path as _AppendPath\nimport time as _append_time\n"
            "_append_real_derive = derive_backlog\n_append_entered = False\n"
            "def derive_backlog(text):\n"
            "    global _append_entered\n"
            "    barrier = _append_os.environ.get('BD_REGISTER_APPEND_TEST_BARRIER')\n"
            "    if barrier and 'bd-register-append' in _append_os.path.basename(_append_sys.argv[0]) and not _append_entered:\n"
            "        _append_entered = True\n        gate = _AppendPath(barrier)\n"
            "        (gate / f'arrived-{_append_os.getpid()}').touch()\n"
            "        while not (gate / 'release').exists(): _append_time.sleep(0.005)\n"
            "    close_barrier = _append_os.environ.get('BD_REGISTER_CLOSE_TEST_BARRIER')\n"
            "    if close_barrier and 'bd-register-close' in _append_os.path.basename(_append_sys.argv[0]):\n"
            "        close_gate = _AppendPath(close_barrier)\n"
            "        (close_gate / f'arrived-{_append_os.getpid()}').touch()\n"
            "        while not (close_gate / 'release').exists(): _append_time.sleep(0.005)\n"
            "    return _append_real_derive(text)\n"
        )


def test_appends_a_single_complete_open_row_and_restamps_header(tmp_path: Path) -> None:
    repo, register = _fixture_repo(tmp_path)
    before = register.read_bytes()
    mode = stat.S_IMODE(register.stat().st_mode)
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 403 | OPEN | new work | "]))

    result = _run(repo, request_path)

    assert result.returncode == 0, result.stderr
    after = register.read_text(encoding="ascii")
    assert stat.S_IMODE(register.stat().st_mode) == mode
    assert after.endswith("| 403 | OPEN | new work |\n")
    assert after.splitlines()[2] == _marker(repo, after)
    assert HEADER.sub("<header>", before.decode("ascii"), count=1) == HEADER.sub("<header>", after, count=1).replace("| 403 | OPEN | new work |\n", "")


def test_appends_a_numerically_ordered_batch_as_one_publication(tmp_path: Path) -> None:
    repo, register = _fixture_repo(tmp_path)
    request_path = tmp_path / "append.json"
    rows = ["| 403 | OPEN | first new work |", "| 405 | OPEN | second new work |"]
    _write_request(request_path, _request(repo, register, rows))

    result = _run(repo, request_path)

    assert result.returncode == 0, result.stderr
    after = register.read_text(encoding="ascii")
    assert after.endswith("| 403 | OPEN | first new work |\n| 405 | OPEN | second new work |\n")
    assert after.splitlines()[2] == _marker(repo, after)


@pytest.mark.parametrize(
    ("name", "rows", "overrides", "diagnostic"),
    [
        ("stale digest", ["| 403 | OPEN | new work |"], {"expected_ids_sha256": "0" * 64}, "compare-and-swap canonical header digest mismatch"),
        ("duplicate proposed id", ["| 403 | OPEN | one |", "| 403 | OPEN | two |"], {}, "unique numeric IDs"),
        ("existing id", ["| 401 | OPEN | duplicate |"], {}, "already exists"),
        ("out of order", ["| 405 | OPEN | later |", "| 403 | OPEN | earlier |"], {}, "numeric insertion order"),
        ("not after existing IDs", ["| 400 | OPEN | late physical insertion |"], {}, "numeric insertion order"),
        ("not open", ["| 403 | CLOSED @1 | false claim |"], {}, "exactly OPEN"),
        ("open evidence", ["| 403 | OPEN @1 | false claim |"], {}, "exactly OPEN"),
        ("empty item", ["| 403 | OPEN |  |"], {}, "non-empty item"),
        ("embedded pipe", ["| 403 | OPEN | unsafe | extra |"], {}, "exactly four table pipes"),
        ("embedded newline", ["| 403 | OPEN | unsafe\ntext |"], {}, "without newline or pipe"),
        ("NUL item", ["| 403 | OPEN | unsafe\x00text |"], {}, "printable ASCII"),
        ("TAB item", ["| 403 | OPEN | unsafe\ttext |"], {}, "printable ASCII"),
        ("DEL item", ["| 403 | OPEN | unsafe\x7ftext |"], {}, "printable ASCII"),
        ("non-ascii", ["| 403 | OPEN | caf\u00e9 |"], {}, "ASCII JSON object"),
        ("wrong schema", ["| 403 | OPEN | new work |"], {"schema": "bd-register-append/v2"}, "schema must be bd-register-append/v1"),
        ("extra request key", ["| 403 | OPEN | new work |"], {"unexpected": "value"}, "exactly the bd-register-append/v1 schema"),
    ],
)
def test_refusals_preserve_the_register_byte_for_byte(
    tmp_path: Path, name: str, rows: list[str], overrides: dict[str, object], diagnostic: str
) -> None:
    repo, register = _fixture_repo(tmp_path)
    before = register.read_bytes()
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, rows, **overrides))

    result = _run(repo, request_path)

    assert result.returncode == 2, (name, result.stderr)
    assert diagnostic in result.stderr, (name, result.stderr)
    assert register.read_bytes() == before, name


def test_concurrent_same_digest_requests_have_exactly_one_success(tmp_path: Path) -> None:
    repo, register = _fixture_repo(tmp_path)
    _install_lock_barrier(repo)
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    paths = [tmp_path / "append-a.json", tmp_path / "append-b.json"]
    _write_request(paths[0], _request(repo, register, ["| 403 | OPEN | first winner | "]))
    _write_request(paths[1], _request(repo, register, ["| 404 | OPEN | second winner | "]))
    env = os.environ.copy()
    env["BD_REGISTER_APPEND_TEST_BARRIER"] = str(barrier)

    first = _start(repo, paths[0], env)
    _wait_for(barrier / f"arrived-{first.pid}", first)
    assert _directory_has_exclusive_flock(register.parent), (
        "append reached its deterministic barrier without holding the stable "
        "directory flock"
    )
    second = _start(repo, paths[1], env)
    (barrier / "release").touch()
    results = []
    for process in (first, second):
        out, err = process.communicate(timeout=10)
        results.append((process.returncode, out, err))

    assert sorted(result[0] for result in results) == [0, 2], results
    assert sum("canonical header digest mismatch" in result[2] for result in results) == 1
    final = register.read_text(encoding="ascii")
    assert sum(row in final for row in ("| 403 | OPEN | first winner |", "| 404 | OPEN | second winner |")) == 1


@pytest.mark.parametrize("writer", ["close", "amend"])
def test_append_serializes_with_other_register_writers(tmp_path: Path, writer: str) -> None:
    repo, register = _fixture_repo(tmp_path)
    _install_lock_barrier(repo)
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    append_request = tmp_path / "append.json"
    _write_request(append_request, _request(repo, register, ["| 403 | OPEN | appended work | "]))
    env = os.environ.copy()
    env["BD_REGISTER_APPEND_TEST_BARRIER"] = str(barrier)
    append_process = _start(repo, append_request, env)
    _wait_for(barrier / f"arrived-{append_process.pid}", append_process)

    if writer == "close":
        close_barrier = tmp_path / "close-barrier"
        close_barrier.mkdir()
        env["BD_REGISTER_CLOSE_TEST_BARRIER"] = str(close_barrier)
        other_process = _start_close(repo, env=env)
        preserved = "| 401 | CLOSED @4321 | preserved before |"
    else:
        amend_request = tmp_path / "amend.json"
        original_row = "| 402 | CLOSED @1359 | preserved after |"
        _write_request(
            amend_request,
            {
                "schema": "bd-register-amend/v1",
                "row": 402,
                "expected_status": "CLOSED @1359",
                "expected_row_sha256": hashlib.sha256(original_row.encode("ascii")).hexdigest(),
                "find": "preserved after",
                "replace": "amended after",
            },
        )
        other_process = _start_amend(repo, amend_request)
        preserved = "| 402 | CLOSED @1359 | amended after |"

    lock_evidence = _directory_has_exclusive_flock(register.parent)
    close_entered_while_append_locked = False
    if writer == "close":
        close_entered_while_append_locked = _arrives_before(
            close_barrier / f"arrived-{other_process.pid}", other_process
        )
        (close_barrier / "release").touch()
    if not lock_evidence or close_entered_while_append_locked:
        other_process.communicate(timeout=10)
    (barrier / "release").touch()
    append_out, append_err = append_process.communicate(timeout=10)
    other_out, other_err = other_process.communicate(timeout=10)

    assert lock_evidence, (
        "no-flock negative control: the competing writer completed while append "
        "was paused, so an unlocked append can overwrite its stale snapshot"
    )
    assert not close_entered_while_append_locked, (
        "no-flock negative control: close read the register while append held "
        "the stable directory flock"
    )
    assert append_process.returncode == 0, (append_out, append_err)
    assert other_process.returncode == 0, (other_out, other_err)
    final = register.read_text(encoding="ascii")
    assert "| 403 | OPEN | appended work |" in final
    assert preserved in final


def test_malformed_request_refuses_without_writing(tmp_path: Path) -> None:
    repo, register = _fixture_repo(tmp_path)
    before = register.read_bytes()
    request_path = tmp_path / "append.json"
    request_path.write_bytes(b'{"schema":')

    result = _run(repo, request_path)

    assert result.returncode == 2
    assert "request must be an ASCII JSON object" in result.stderr
    assert register.read_bytes() == before


def test_post_replace_directory_fsync_failure_reports_commit_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, register = _fixture_repo(tmp_path)
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 403 | OPEN | durable work | "]))
    module = _load_tool_module()
    real_open, real_fsync = os.open, os.fsync
    directory_opens = 0
    sync_fd: int | None = None

    def injected_open(path: object, flags: int, *args: object) -> int:
        nonlocal directory_opens, sync_fd
        descriptor = real_open(path, flags, *args)
        if Path(path) == register.parent and flags & getattr(os, "O_DIRECTORY", 0):
            directory_opens += 1
            if directory_opens == 2:
                sync_fd = descriptor
        return descriptor

    def injected_fsync(descriptor: int) -> None:
        if descriptor == sync_fd:
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(module.os, "open", injected_open)
    monkeypatch.setattr(module.os, "fsync", injected_fsync)
    monkeypatch.setattr(sys, "argv", [str(TOOL), "--repo", str(repo), "--request", str(request_path)])

    assert module.main() == 3
    captured = capsys.readouterr()
    assert "COMMIT UNCERTAIN" in captured.err
    assert "directory fsync" in captured.err
    assert "| 403 | OPEN | durable work |" in register.read_text(encoding="ascii")


def test_help_describes_the_guarded_append_interface() -> None:
    result = subprocess.run([sys.executable, str(TOOL), "--help"], cwd=ROOT, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert "--repo" in result.stdout
    assert "--request" in result.stdout
