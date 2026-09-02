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


def _run(repo: Path, request: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--repo", str(repo), "--request", str(request), *extra],
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
    """The batch was 403 and 405 until the gap contract landed.

    Its subject is that an ordered batch publishes once with a restamped
    header, and it kept that subject while ALSO demonstrating -- silently --
    that a skipped ID cost nothing. 404 is not a detail here: the same shape,
    repeated across concurrent cuts, is where 57 permanent holes came from. The
    batch is contiguous now and the skipping form has its own refusal test
    below, so both behaviours are asserted instead of one being a side effect.
    """
    repo, register = _fixture_repo(tmp_path)
    request_path = tmp_path / "append.json"
    rows = ["| 403 | OPEN | first new work |", "| 404 | OPEN | second new work |"]
    _write_request(request_path, _request(repo, register, rows))

    result = _run(repo, request_path)

    assert result.returncode == 0, result.stderr
    after = register.read_text(encoding="ascii")
    assert after.endswith("| 403 | OPEN | first new work |\n| 404 | OPEN | second new work |\n")
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


# ---------------------------------------------------------------------------
# A GAP IS PUBLISHED WITH ITS REASON, OR IT IS NOT PUBLISHED.
#
# This tool enforced monotonic increase and uniqueness and never contiguity, so
# a request naming 405 after 402 exited 0 and left 403 and 404 permanently
# absent with nothing recorded anywhere. 57 ids went that way. The measurement
# on the defective parent, kept because it is the RED this section replaces:
# ids [401, 402] -> request ["| 405 | OPEN | ... |"] -> rc 0, ids [401, 402,
# 405], holes [403, 404], no diagnostic.
#
# The declaration is now part of the SAME publication as the row, which is the
# only arrangement in which the reason cannot be forgotten. Ordering and its
# crash residue are asserted below rather than assumed, because "atomic across
# two files" is not something os.replace provides and claiming it would be false.
# ---------------------------------------------------------------------------

GAP_ALLOWLIST_NAME = "REGISTER_GAP_ALLOWLIST.json"


def _write_allowlist(repo: Path, gaps: list[dict] | None = None) -> Path:
    path = repo / "project-knowledge" / GAP_ALLOWLIST_NAME
    path.write_text(
        json.dumps(
            {
                "schema": "bd-register-gap-allowlist/v1",
                "register": "project-knowledge/IMPROVEMENT_BACKLOG.md",
                "notes": ["fixture"],
                "gaps": [] if gaps is None else gaps,
            },
            indent=2,
        )
        + "\n",
        encoding="ascii",
    )
    path.chmod(0o640)
    return path


def _register_ids(register: Path) -> list[int]:
    text = register.read_text(encoding="ascii")
    return sorted(int(value) for value in re.findall(r"^\|\s*(\d+)\s*\|", text, re.MULTILINE))


def _gaps(allowlist: Path) -> list[dict]:
    return json.loads(allowlist.read_bytes().decode("ascii"))["gaps"]


def test_an_append_that_would_skip_an_id_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    """RED CONTROL on the defective behaviour: this exact request used to pass."""
    repo, register = _fixture_repo(tmp_path)
    allowlist = _write_allowlist(repo)
    assert _register_ids(register) == [401, 402], "precondition: the frontier is 402"
    before_register, before_allowlist = register.read_bytes(), allowlist.read_bytes()
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 405 | OPEN | skips two ids |"]))

    result = _run(repo, request_path)

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "append would create undeclared register gap(s): [403, 404]" in result.stderr
    assert "--allow-gap" in result.stderr, "the refusal must name the way through it"
    assert register.read_bytes() == before_register
    assert allowlist.read_bytes() == before_allowlist


def test_allow_gap_publishes_the_reason_and_the_row_together(tmp_path: Path) -> None:
    repo, register = _fixture_repo(tmp_path)
    allowlist = _write_allowlist(repo)
    assert _gaps(allowlist) == [], "precondition: nothing is declared yet"
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 405 | OPEN | skips two ids |"]))
    reason = "cuts holding 403 and 404 were frozen and never landed"

    result = _run(repo, request_path, "--allow-gap", reason)

    assert result.returncode == 0, result.stderr
    assert "2 register gap(s) declared" in result.stdout
    assert _register_ids(register) == [401, 402, 405]
    assert "| 405 | OPEN | skips two ids |" in register.read_text(encoding="ascii")
    assert _gaps(allowlist) == [
        {"id": 403, "status": "DECLARED", "reason": reason},
        {"id": 404, "status": "DECLARED", "reason": reason},
    ]
    assert register.read_text(encoding="ascii").splitlines()[2] == _marker(
        repo, register.read_text(encoding="ascii")
    )


def test_allow_gap_over_a_contiguous_append_banks_no_permission(tmp_path: Path) -> None:
    """The inverse. A declaration for a gap that does not exist is permission."""
    repo, register = _fixture_repo(tmp_path)
    allowlist = _write_allowlist(repo)
    before_register, before_allowlist = register.read_bytes(), allowlist.read_bytes()
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 403 | OPEN | contiguous |"]))

    result = _run(repo, request_path, "--allow-gap", "no gap here")

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "creates no register gap" in result.stderr
    assert register.read_bytes() == before_register
    assert allowlist.read_bytes() == before_allowlist


def test_an_identical_standing_declaration_is_the_retry_path(tmp_path: Path) -> None:
    """The residue of an interruption between the two replaces must be repairable.

    The declaration is published first, so an interruption leaves DECLARED
    entries and an untouched register. Rerunning the identical request must
    therefore succeed rather than refuse -- otherwise the recovery from a crash
    is a hand edit of the very file this tool exists to keep honest.
    """
    repo, register = _fixture_repo(tmp_path)
    reason = "the cut was abandoned"
    allowlist = _write_allowlist(
        repo,
        [
            {"id": 403, "status": "DECLARED", "reason": reason},
            {"id": 404, "status": "DECLARED", "reason": reason},
        ],
    )
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 405 | OPEN | retried |"]))

    result = _run(repo, request_path, "--allow-gap", reason)

    assert result.returncode == 0, result.stderr
    assert [entry["id"] for entry in _gaps(allowlist)] == [403, 404], "no duplicate entry"
    assert _register_ids(register) == [401, 402, 405]


@pytest.mark.parametrize(
    ("name", "gaps", "diagnostic"),
    [
        (
            "a different standing status",
            [{"id": 403, "status": "UNADJUDICATED", "reason": ""}],
            "already declares id 403 as 'UNADJUDICATED'",
        ),
        (
            "a different standing reason",
            [{"id": 403, "status": "DECLARED", "reason": "some other reason"}],
            "already declares id 403 as 'DECLARED'",
        ),
    ],
)
def test_a_standing_declaration_is_never_rewritten(
    tmp_path: Path, name: str, gaps: list[dict], diagnostic: str
) -> None:
    repo, register = _fixture_repo(tmp_path)
    allowlist = _write_allowlist(repo, gaps)
    before_register, before_allowlist = register.read_bytes(), allowlist.read_bytes()
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 405 | OPEN | work |"]))

    result = _run(repo, request_path, "--allow-gap", "a newly invented reason")

    assert result.returncode == 2, (name, result.stdout, result.stderr)
    assert diagnostic in result.stderr, (name, result.stderr)
    assert register.read_bytes() == before_register
    assert allowlist.read_bytes() == before_allowlist


@pytest.mark.parametrize(
    ("name", "body", "diagnostic"),
    [
        ("truncated json", b'{"schema": ', "is not valid JSON"),
        ("non-ascii", '{"schema": "café"}'.encode("utf-8"), "not readable ASCII"),
        ("wrong keys", b'{"schema": "bd-register-gap-allowlist/v1"}', "exactly"),
        (
            "wrong schema",
            b'{"schema": "other/v1", "register": "project-knowledge/IMPROVEMENT_BACKLOG.md",'
            b' "notes": [], "gaps": []}',
            "must declare schema",
        ),
        (
            "unreadable entry",
            b'{"schema": "bd-register-gap-allowlist/v1", "register":'
            b' "project-knowledge/IMPROVEMENT_BACKLOG.md", "notes": [], "gaps": [3]}',
            "an entry this tool cannot read",
        ),
        (
            "duplicate declared id",
            b'{"schema": "bd-register-gap-allowlist/v1", "register":'
            b' "project-knowledge/IMPROVEMENT_BACKLOG.md", "notes": [], "gaps":'
            b' [{"id": 403, "status": "DECLARED", "reason": "a"},'
            b' {"id": 403, "status": "DECLARED", "reason": "b"}]}',
            "declares id 403 twice",
        ),
    ],
)
def test_a_declaration_this_tool_cannot_read_is_never_clobbered(
    tmp_path: Path, name: str, body: bytes, diagnostic: str
) -> None:
    """Each refusal names its own step: four different repairs, four messages."""
    repo, register = _fixture_repo(tmp_path)
    allowlist = repo / "project-knowledge" / GAP_ALLOWLIST_NAME
    allowlist.write_bytes(body)
    before_register = register.read_bytes()
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 405 | OPEN | work |"]))

    result = _run(repo, request_path, "--allow-gap", "a reason")

    assert result.returncode == 2, (name, result.stdout, result.stderr)
    assert diagnostic in result.stderr, (name, result.stderr)
    assert register.read_bytes() == before_register
    assert allowlist.read_bytes() == body, "the declaration was rewritten"


def test_an_absent_declaration_is_refused_rather_than_created(tmp_path: Path) -> None:
    """A tool may not bring its own permission surface into existence."""
    repo, register = _fixture_repo(tmp_path)
    allowlist = repo / "project-knowledge" / GAP_ALLOWLIST_NAME
    assert not allowlist.exists()
    before_register = register.read_bytes()
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 405 | OPEN | work |"]))

    result = _run(repo, request_path, "--allow-gap", "a reason")

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "is absent" in result.stderr
    assert not allowlist.exists(), "the tool created the declaration it was told to amend"
    assert register.read_bytes() == before_register


@pytest.mark.parametrize(
    ("name", "reason", "diagnostic"),
    [
        ("empty", "", "requires a non-empty reason"),
        ("blank", "   ", "requires a non-empty reason"),
        ("newline", "one\ntwo", "printable ASCII on a single line"),
        ("tab", "one\ttwo", "printable ASCII on a single line"),
    ],
)
def test_a_gap_reason_is_printable_ascii_on_one_line(
    tmp_path: Path, name: str, reason: str, diagnostic: str
) -> None:
    """The DISTINCTIVE diagnostic, not merely exit 2.

    Asserting only "--allow-gap appears in stderr" passed against the defective
    parent, where argparse said "unrecognized arguments: --allow-gap" -- a green
    laundered out of a tool that had no such flag at all.
    """
    repo, register = _fixture_repo(tmp_path)
    allowlist = _write_allowlist(repo)
    before_register, before_allowlist = register.read_bytes(), allowlist.read_bytes()
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 405 | OPEN | work |"]))

    result = _run(repo, request_path, "--allow-gap", reason)

    assert result.returncode == 2, (name, result.stdout, result.stderr)
    assert diagnostic in result.stderr, (name, result.stderr)
    assert "unrecognized arguments" not in result.stderr, (name, result.stderr)
    assert register.read_bytes() == before_register
    assert allowlist.read_bytes() == before_allowlist


def test_a_contiguous_append_never_touches_the_declaration(tmp_path: Path) -> None:
    """OVER-SENSITIVITY CONTROL: the ordinary path is byte-for-byte unchanged."""
    repo, register = _fixture_repo(tmp_path)
    allowlist = _write_allowlist(repo, [{"id": 7, "status": "UNADJUDICATED", "reason": ""}])
    before_allowlist = allowlist.read_bytes()
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 403 | OPEN | contiguous |"]))

    result = _run(repo, request_path)

    assert result.returncode == 0, result.stderr
    assert "register gap(s) declared" not in result.stdout
    assert allowlist.read_bytes() == before_allowlist
    assert _register_ids(register) == [401, 402, 403]


def test_the_declaration_is_published_before_the_register(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE ORDER IS THE CONTRACT, so it is measured rather than assumed.

    Failing the SECOND os.replace proves both halves at once: the declaration
    is already visible (so the reason is durable before the gap is), the
    register is untouched (so its compare-and-swap digest still matches and the
    identical request can simply be rerun), and the tool says COMMIT UNCERTAIN
    rather than reporting a clean append.
    """
    repo, register = _fixture_repo(tmp_path)
    allowlist = _write_allowlist(repo)
    before_register = register.read_bytes()
    request_path = tmp_path / "append.json"
    _write_request(request_path, _request(repo, register, ["| 405 | OPEN | work |"]))
    module = _load_tool_module()

    real_replace = os.replace
    order: list[str] = []

    def injected_replace(source: object, destination: object) -> None:
        order.append(Path(destination).name)
        if len(order) == 2:
            raise OSError("injected second replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", injected_replace)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(TOOL),
            "--repo",
            str(repo),
            "--request",
            str(request_path),
            "--allow-gap",
            "the interrupted publication",
        ],
    )

    assert module.main() == 3
    captured = capsys.readouterr()
    assert order == [GAP_ALLOWLIST_NAME, "IMPROVEMENT_BACKLOG.md"], order
    assert "COMMIT UNCERTAIN" in captured.err, captured.err
    assert [entry["id"] for entry in _gaps(allowlist)] == [403, 404], (
        "the declaration must be durable before the gap it declares"
    )
    assert register.read_bytes() == before_register, (
        "the register must be untouched, so the identical request can be rerun"
    )


def test_help_describes_the_gap_declaration_interface() -> None:
    result = subprocess.run([sys.executable, str(TOOL), "--help"], cwd=ROOT, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert "--allow-gap" in result.stdout
    assert "REASON" in result.stdout
