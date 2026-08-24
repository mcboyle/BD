"""bd-wedge-hunt must not leave a remote pytest master running when it gives up.

BACKLOG 146. Five orphaned masters were found across the fleet at the end of the
~19h hunt on 2026-08-14, the oldest 41614s (11.6 HOURS), each a master plus up
to 48 idle workers. Load is this bug's dominant covariate -- reduced-size arms
are 0/73 against 15 of ~620 on full -- so an unreaped master progressively
corrupts the very measurement the hunt exists to take, in the direction that
INCREASES the apparent rate over time.

ROW 146's DIAGNOSIS WAS WRONG, AND THE CORRECTION IS THE POINT. It says the hunt
"captures forensics and sends SIGINT, but a master livelocked per row 145 is not
in a state that SIGINT unwinds, so the run simply stays". Read the code: the
WEDGE-CONFIRMED path sends SIGINT, waits `sigint_grace`, re-runs forensics, and
THEN sends `kill -9` to the process GROUP and the pid. That path is correct and
is not where the orphans came from. Diagnosing the one correct path as the
defect would have produced a fix that changed nothing.

THE ORPHANS COME FROM THE PATHS THAT ABANDON A RUN WITHOUT KILLING IT. Measured
by reading every terminal branch of the monitor loop at v3.66.1131:

  * INTERRUPT. `main` catches KeyboardInterrupt, sets STOP, and RETURNS. The
    host threads are `daemon=True`, so the interpreter kills them at exit: the
    remote run is never killed AND its row is never written. That is the whole
    of the five observed orphans, and it is why `rows.jsonl` contains ZERO
    abandoned rows -- they were not mis-recorded, they were never recorded.
  * --hours. Says "letting in-flight samples finish" and does the opposite: the
    monitor loop is `while not STOP.is_set()`, so it exits on the next tick and
    the row falls through to `setdefault("state", "COMPLETED")`. A run that was
    abandoned mid-flight is recorded as COMPLETED with no `pytest_exit`, which
    is a FALSE NEGATIVE in the wedge denominator. It never fired during the
    2026-08-14 hunt -- measured: 686 of 686 COMPLETED rows carry a real
    `pytest_exit` -- so no preserved row is contaminated. The defect is live
    regardless.
  * CAPPED. Kills the master pid ONLY. The wedge path three lines above kills
    the process GROUP. Same job, two branches, one of them leaves up to 48
    workers behind.
  * UNKNOWN. Records "the run was NOT killed and may still be there" and does
    not try. Honest, and still a leak.

WHY A STRUCTURAL TEST RATHER THAN A LIVE ONE. Driving the real abandon paths
needs a remote host, an ssh round trip and a wedged pytest master -- none of
which belongs in a band. These assertions read the tool's own source, which is
the same instrument `test_bd_ready_preflight.py` uses for the same reason, and
they are paired with a direct behavioural test of the one piece that CAN be
isolated: the command builder.

WHAT THIS FILE CANNOT SEE, stated because a gate that cannot say so is worse
than none: it does not prove a reap SUCCEEDS on a live host, only that every
abandon path issues one and that the command targets the group. A remote kill
that silently fails is outside its denominator.
"""

from __future__ import annotations

import ast
import errno
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import re
import select
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time

import pytest

# Its subject is one tool's source and one pure function in it, not the tree.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
HUNT = REPO / "toolchain" / "bin" / "bd-wedge-hunt"


def _load():
    """Import the extensionless, python-shebang tool as a module.

    `git ls-files -- '*.py'` cannot see this file and neither can a plain
    import; CLAUDE.md section 1 is about exactly this population.
    """
    spec = importlib.util.spec_from_loader(
        "bd_wedge_hunt_under_test",
        importlib.machinery.SourceFileLoader("bd_wedge_hunt_under_test", str(HUNT)),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _source() -> str:
    return HUNT.read_text(encoding="utf-8")


def _string_constants() -> list[str]:
    """Every string LITERAL in the tool, with comments structurally excluded.

    A comment is inside the denominator of every gate that reads source text,
    and CLAUDE.md section 0 records four separate times an assertion in this
    repo could not tell prose from code -- including one where the comment
    written to explain a removal re-created the thing it described. This file
    is full of prose naming the very markers it asserts on, so it must not
    grep raw source. Comments never reach the AST, so reading literals out of
    it fixes the denominator for free.
    """
    return [n.value for n in ast.walk(ast.parse(_source()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_the_tool_exists_and_parses():
    """PRECONDITION. Without it, every assertion below is vacuous on a typo."""
    assert HUNT.is_file(), f"no bd-wedge-hunt at {HUNT}"
    ast.parse(_source())


def test_it_imports_without_side_effects():
    """PRECONDITION for the behavioural test: the module must be importable."""
    mod = _load()
    assert hasattr(mod, "STOP"), "module loaded but does not look like the hunt"


def test_a_reap_command_builder_exists_and_targets_the_process_GROUP():
    """The one piece testable in isolation, asserted behaviourally.

    A master is `setsid`-launched, so its workers share its process group. A
    kill aimed at the pid alone leaves up to 48 workers running -- which is the
    CAPPED path's bug. The builder must aim at the group.
    """
    mod = _load()
    assert hasattr(mod, "reap_cmd"), (
        "bd-wedge-hunt has no reap_cmd(). Every path that abandons a remote run "
        "needs ONE shared way to kill it; four branches hand-rolling their own "
        "is how the CAPPED path ended up killing the master and leaving its 48 "
        "workers. Backlog 146."
    )
    cmd = mod.reap_cmd("/private/run/runner.receipt")
    assert "/private/run/runner.receipt" in cmd, (
        "the builder ignored the durable receipt it was given")
    assert "pidfd_send_signal" in cmd and "owned_census" in cmd, (
        "reap_cmd does not bind signals to exact process identities and census "
        "the whole owned context")
    assert "SIGTERM" in cmd and "SIGKILL" in cmd and "monotonic_ns" in cmd


def _capture_outer_receipt(mod, proc, root: pathlib.Path, run_id: str):
    receipt = root / "runner.receipt"
    captured = subprocess.run(
        [os.environ.get("PYTHON", "python3"), "-c",
         mod.PROCESS_GUARD_PROGRAM, "capture", str(proc.pid), str(receipt),
         run_id, str(root), str(os.getpid())],
        capture_output=True, text=True, timeout=10,
    )
    assert captured.returncode == 0, (captured.stdout, captured.stderr)
    assert "RECEIPT-OK" in captured.stdout and receipt.is_file()
    return receipt


def test_reap_cmd_actually_kills_a_real_process_GROUP(tmp_path):
    """THE SEAM, DRIVEN. A structural test cannot tell a correct kill from a
    plausible-looking one, and this is how that mattered:

    the first version of `reap_cmd` used `kill -0` for its liveness check and
    reported REAP-SURVIVED after successfully killing all five processes of a
    real tree -- because `kill -0` succeeds on a ZOMBIE, and a master whose
    parent has not wait()ed yet is exactly that. A gate firing on correct work
    gets switched off (section 0), so this arm exists to keep the verdict
    honest, not only the kill.

    Shape matched to production: `setsid` so the tree has its own process group,
    which is what the hunt's runner does.
    """
    mod = _load()
    run_id = os.urandom(16).hex()
    env = dict(os.environ)
    env["BD_WEDGE_RUN_ID"] = run_id
    proc = subprocess.Popen(
        ["bash", "-c", "sleep 300 & sleep 300 & sleep 300 & sleep 300"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env=env)
    try:
        receipt = _capture_outer_receipt(mod, proc, tmp_path, run_id)
        pgid = os.getpgid(proc.pid)

        def live_in_group():
            r = subprocess.run(["ps", "-eo", "pgid=,pid=,stat="],
                               capture_output=True, text=True)
            rows = [l.split() for l in r.stdout.splitlines() if l.split()]
            return [x for x in rows
                    if x[0] == str(pgid) and not x[2].startswith("Z")]

        before = live_in_group()
        # PRECONDITION: assert the fixture built the shape before judging it.
        # Without this, "nothing survived" and "nothing was ever there" are the
        # same green -- CLAUDE.md section 6.
        assert len(before) >= 4, (
            f"the fixture did not build a process group to kill (found "
            f"{len(before)}); this test would otherwise pass vacuously")

        out = subprocess.run(["bash", "-c", mod.reap_cmd(str(receipt), term_grace=0.05)],
                             capture_output=True, text=True, timeout=60)
        after = live_in_group()

        assert not after, (
            f"{len(after)} process(es) survived the reap. Killing the master "
            "alone leaves its workers -- the CAPPED path's bug, backlog 146.")
        assert out.stdout.startswith("REAP-OK ") \
            and "census=ABSENT" in out.stdout \
            and "failures=NONE" in out.stdout, (
            f"the group WAS killed but reap_cmd reported {out.stdout.strip()!r}. "
            "A false SURVIVED is a gate firing on correct work: `kill -0` "
            "succeeds on a zombie, so the liveness probe must read the process "
            "STATE and treat Z as gone.")
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def test_reap_cmd_can_still_report_a_survivor(tmp_path):
    """A drifted receipt is UNKNOWN and cannot authorize a signal."""
    mod = _load()
    run_id = os.urandom(16).hex()
    env = dict(os.environ)
    env["BD_WEDGE_RUN_ID"] = run_id
    live = subprocess.Popen(
        ["bash", "-c", "trap '' TERM; sleep 300"],
        start_new_session=True, env=env)
    try:
        receipt = _capture_outer_receipt(mod, live, tmp_path, run_id)
        row = json.loads(receipt.read_text(encoding="ascii"))
        row["starttime"] += 1
        receipt.write_text(json.dumps(row), encoding="ascii")
        out = subprocess.run(["bash", "-c", mod.reap_cmd(str(receipt), term_grace=0.05)],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode != 0 and "REAP-UNKNOWN" in out.stdout
        assert live.poll() is None, (
            "receipt drift signalled the unrelated/recycled identity")
    finally:
        live.kill()
        live.wait()


def test_wedge_interrupt_uses_the_saved_gate_receipt_not_a_bare_pid():
    """The evidence-flush SIGINT is subject to the same reuse rule as reap."""
    mod = _load()
    source = _source()
    assert '"kill -INT %s 2>/dev/null; echo INT-sent"' not in source
    assert hasattr(mod, "signal_receipt_cmd")
    command = mod.signal_receipt_cmd("123:7:123:7:9001", "INT")
    assert "pidfd_send_signal" in command and "123:7:123:7:9001" in command
    assert 'getattr(signal, "SIG" + argv[2])' in command
    assert command.endswith(" INT")


def test_signal_receipt_refuses_starttime_drift_before_pidfd_signal(tmp_path):
    """A stale five-field token cannot authorize the actual signal transport."""
    mod = _load()
    signal_marker = tmp_path / "drifted-receipt-signalled"
    entered, _unused_release, entered_fd = _w1_fifo_barrier(
        tmp_path, "signal-receipt")
    program = (
        "import pathlib, signal\n"
        "marker = pathlib.Path(%r)\n"
        "signal.signal(signal.SIGHUP, lambda *_: "
        "marker.write_text('signalled\\n', encoding='ascii'))\n"
        "with open(%r, 'w', encoding='ascii') as stream:\n"
        "    stream.write('handler-ready\\n')\n"
        "while True:\n"
        "    signal.pause()\n"
    ) % (str(signal_marker), str(entered))
    proc = subprocess.Popen(
        [os.environ.get("PYTHON", "python3"), "-c", program],
        start_new_session=True,
    )
    try:
        assert _w1_await_fifo(entered_fd) == "handler-ready\n"
        raw = pathlib.Path("/proc", str(proc.pid), "stat").read_text(
            encoding="ascii")
        head, tail_text = raw.rsplit(") ", 1)
        tail = tail_text.split()
        current = (int(head.split(" (", 1)[0]), int(tail[1]),
                   int(tail[2]), int(tail[3]), int(tail[19]))
        assert current[0] == proc.pid and current[0] == current[2] == current[3]
        drifted = (*current[:4], current[4] + 1)
        assert not signal_marker.exists()

        out = subprocess.run(
            ["bash", "-c", mod.signal_receipt_cmd(
                ":".join(map(str, drifted)), "HUP")],
            capture_output=True, text=True, timeout=10,
        )

        assert (out.returncode != 0 and "SIGNAL-UNKNOWN" in out.stdout
                and proc.poll() is None and not signal_marker.exists()), (
            "DRIFTED-OUTER-RECEIPT-AUTHORIZED-SIGNAL", out.stdout,
            out.stderr, proc.poll(), signal_marker.exists())
    finally:
        os.close(entered_fd)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))
        proc.wait(timeout=5)


def test_pidfd_open_race_after_owned_census_is_already_absent(monkeypatch):
    """A process disappearing between census and pidfd_open is not a leak.

    The reaper has already bound the row to an exact receipt.  ESRCH at the
    kernel handle acquisition seam means that identity no longer exists; it
    must not turn a successful concurrent exit into REAP-UNKNOWN.
    """
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    guard_os = namespace["os"]

    def vanished_before_pidfd(_pid, _flags):
        raise ProcessLookupError

    monkeypatch.setattr(guard_os, "pidfd_open", vanished_before_pidfd)
    failures = namespace["signal_exact"]([{"pid": 987654321}], signal.SIGTERM)

    assert failures == [], (
        "PIDFD-OPEN-ESRCH-MISCLASSIFIED-AS-CLEANUP-FAILURE", failures)

    closed: list[int] = []
    monkeypatch.setattr(guard_os, "pidfd_open", lambda _pid, _flags: 17)
    monkeypatch.setattr(namespace["signal"], "pidfd_send_signal",
                        lambda _fd, _sig: (_ for _ in ()).throw(
                            ProcessLookupError()))
    monkeypatch.setattr(guard_os, "close", closed.append)
    namespace["exact_current"] = lambda saved: ("MATCH", saved)

    failures = namespace["signal_exact"]([{
        "pid": 987654321, "ppid": 1, "pgid": 987654321,
        "sid": 987654321, "starttime": 44,
    }], signal.SIGTERM)

    assert failures == [], (
        "PIDFD-SEND-ESRCH-MISCLASSIFIED-AS-CLEANUP-FAILURE", failures)
    assert closed == [17], "successful pidfd acquisition was not closed"

    monkeypatch.setattr(
        guard_os, "pidfd_open",
        lambda _pid, _flags: (_ for _ in ()).throw(PermissionError()))
    failures = namespace["signal_exact"]([{"pid": 987654321}], signal.SIGTERM)
    assert failures == ["pidfd-open-987654321-PermissionError"], (
        "NON-ABSENCE-PIDFD-ERROR-WAS-LAUNDERED-AS-SUCCESS", failures)


def test_pid_reuse_after_pidfd_open_is_the_owned_identity_already_absent(
        monkeypatch):
    """A pidfd pins the censused process, not a later PID reuse.

    If procfs reports DRIFT after that kernel handle was acquired, the owned
    identity necessarily exited.  The replacement must not be signalled and
    its reuse must not turn completed cleanup into REAP-UNKNOWN.
    """
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    sent: list[tuple[int, signal.Signals]] = []
    closed: list[int] = []
    monkeypatch.setattr(namespace["os"], "pidfd_open",
                        lambda _pid, _flags: 23)
    monkeypatch.setattr(namespace["os"], "close", closed.append)
    monkeypatch.setattr(namespace["signal"], "pidfd_send_signal",
                        lambda fd, sig: sent.append((fd, sig)))
    namespace["exact_current"] = lambda _saved: ("DRIFT", {
        "pid": 4242, "ppid": 1, "pgid": 4242,
        "sid": 4242, "starttime": 9002,
    })

    failures = namespace["signal_exact"]([{
        "pid": 4242, "ppid": 1, "pgid": 4242,
        "sid": 4242, "starttime": 9001,
    }], signal.SIGKILL)

    assert failures == [], (
        "POST-PIDFD-REUSE-MISCLASSIFIED-AS-CLEANUP-FAILURE", failures)
    assert sent == [], "a replacement process was signalled after PID reuse"
    assert closed == [23], "the pidfd was not settled after observed reuse"


def test_signal_exact_close_failure_does_not_skip_later_owned_identity(
        monkeypatch):
    """A failed pidfd settlement is evidence, not permission to stop draining."""
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    first = {"pid": 101, "ppid": 1, "pgid": 101,
             "sid": 101, "starttime": 1001}
    second = {"pid": 202, "ppid": 1, "pgid": 202,
              "sid": 202, "starttime": 2002}
    fds = iter([41, 42])
    signalled: list[tuple[int, signal.Signals]] = []
    closed: list[int] = []

    def close_with_first_fault(fd):
        closed.append(fd)
        if fd == 41:
            raise OSError("injected close fault")

    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(namespace["os"], "pidfd_open",
                            lambda _pid, _flags: next(fds))
        guard_patch.setattr(
            namespace["signal"], "pidfd_send_signal",
            lambda fd, sig: signalled.append((fd, sig)))
        guard_patch.setattr(namespace["os"], "close", close_with_first_fault)
        namespace["exact_current"] = lambda saved: ("MATCH", saved)

        failures = namespace["signal_exact"]([first, second], signal.SIGTERM)

    assert signalled == [(41, signal.SIGTERM), (42, signal.SIGTERM)]
    assert closed == [41, 42]
    assert failures == ["pidfd-close-101-OSError"]


def test_reap_continues_to_kill_and_final_census_after_term_pidfd_close_failure(
        monkeypatch, capsys):
    """A TERM close fault cannot bypass escalation or the authoritative census."""
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    saved = {
        "version": 1, "role": "runner", "run_id": "a" * 32,
        "root": "/srv/bd-wedge/run", "cwd_hex": "2f", "cmdline_hex": "00",
        "fds": [], "pid": 303, "ppid": 1, "pgid": 303,
        "sid": 303, "starttime": 3003,
    }
    census_results = iter([
        ([saved], False),
        ([saved], False),
        ([], False),
    ])
    census_calls: list[int] = []
    fds = iter([51, 52])
    signalled: list[tuple[int, signal.Signals]] = []
    closed: list[int] = []
    waits: list[float] = []

    def controlled_census(_saved):
        census_calls.append(len(census_calls) + 1)
        return next(census_results)

    def close_with_term_fault(fd):
        closed.append(fd)
        if fd == 51:
            raise OSError("injected TERM close fault")

    with monkeypatch.context() as guard_patch:
        namespace["load_receipt"] = lambda _path: saved
        namespace["exact_current"] = lambda current: ("MATCH", current)
        namespace["owned_census"] = controlled_census
        namespace["wait_interval"] = (
            lambda seconds: waits.append(seconds) or int(seconds * 1_000_000))
        guard_patch.setattr(namespace["os"], "pidfd_open",
                            lambda _pid, _flags: next(fds))
        guard_patch.setattr(
            namespace["signal"], "pidfd_send_signal",
            lambda fd, sig: signalled.append((fd, sig)))
        guard_patch.setattr(namespace["os"], "close", close_with_term_fault)

        with pytest.raises(SystemExit) as exited:
            namespace["reap"](["reap", "/receipt", "0.25"])

    reported = capsys.readouterr().out
    assert exited.value.code == 4
    assert signalled == [(51, signal.SIGTERM), (52, signal.SIGKILL)]
    assert closed == [51, 52]
    assert waits == [0.25]
    assert census_calls == [1, 2, 3]
    assert ("REAP-UNKNOWN " in reported
            and "term=FAILED" in reported
            and "kill=SENT" in reported
            and "census=UNKNOWN" in reported
            and "failures=pidfd-close-303-OSError" in reported), reported


@pytest.mark.parametrize("cancel_at", ["close", "signal"])
def test_signal_exact_preserves_baseexception_cancellation_primary(
        monkeypatch, cancel_at):
    """Close settlement records ordinary faults without swallowing cancellation."""
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    first = {"pid": 404, "ppid": 1, "pgid": 404,
             "sid": 404, "starttime": 4004}
    second = {"pid": 505, "ppid": 1, "pgid": 505,
              "sid": 505, "starttime": 5005}
    primary = KeyboardInterrupt("injected cancellation")
    fds = iter([61, 62])
    signalled: list[tuple[int, signal.Signals]] = []
    closed: list[int] = []

    def signal_with_optional_cancel(fd, sig):
        signalled.append((fd, sig))
        if cancel_at == "signal":
            raise primary

    def close_with_optional_cancel(fd):
        closed.append(fd)
        if cancel_at == "close":
            raise primary
        raise OSError("secondary close fault")

    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(namespace["os"], "pidfd_open",
                            lambda _pid, _flags: next(fds))
        guard_patch.setattr(namespace["signal"], "pidfd_send_signal",
                            signal_with_optional_cancel)
        guard_patch.setattr(namespace["os"], "close", close_with_optional_cancel)
        namespace["exact_current"] = lambda saved: ("MATCH", saved)

        with pytest.raises(KeyboardInterrupt) as raised:
            namespace["signal_exact"]([first, second], signal.SIGTERM)

    assert raised.value is primary
    assert signalled == [(61, signal.SIGTERM)]
    assert closed == [61]


@pytest.mark.parametrize(
    "secondary",
    [OSError("close fault"), SystemExit(91)],
    ids=["ordinary-close", "close-cancellation"],
)
def test_process_guard_owned_close_preserves_the_active_primary(
        monkeypatch, secondary):
    """An owned-fd close cannot replace cancellation already in flight."""
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    primary = KeyboardInterrupt("first cancellation")

    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(
            namespace["os"], "close",
            lambda _fd: (_ for _ in ()).throw(secondary))

        def fail_then_close():
            try:
                raise primary
            finally:
                namespace["close_preserving_primary"](71, "capture-temp")

        with pytest.raises(KeyboardInterrupt) as raised:
            fail_then_close()

    assert raised.value is primary
    notes = "\n".join(getattr(primary, "__notes__", []))
    assert "capture-temp" in notes and type(secondary).__name__ in notes, notes


def test_every_process_guard_fd_owner_uses_first_primary_close_settlement():
    """All embedded descriptor owners route through the proven close helper."""
    mod = _load()

    def helper_calls(program, function):
        tree = ast.parse(program)
        owner = next(node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == function)
        return [node for node in ast.walk(owner)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "close_preserving_primary"]

    assert len(helper_calls(mod.PROCESS_GUARD_PROGRAM, "signal_exact")) == 1
    assert len(helper_calls(mod.PROCESS_GUARD_PROGRAM, "capture")) == 2
    assert len(helper_calls(mod.PROCESS_GUARD_PROGRAM, "signal_receipt")) == 1
    reader_tree = ast.parse(mod.REGISTRATION_CHANNEL_READER_PROGRAM)
    assert sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "close_preserving_primary"
        for node in ast.walk(reader_tree)
    ) == 1


def _embedded_process_guard_namespace():
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    return namespace


def _assert_close_secondary_note(primary, label):
    notes = "\n".join(getattr(primary, "__notes__", []))
    assert label in notes and "SystemExit" in notes, notes


def test_signal_exact_double_cancellation_keeps_signal_primary(monkeypatch):
    namespace = _embedded_process_guard_namespace()
    saved = {"pid": 601, "ppid": 1, "pgid": 601,
             "sid": 601, "starttime": 6001}
    primary = KeyboardInterrupt("signal cancellation")
    secondary = SystemExit(91)
    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(namespace["os"], "pidfd_open", lambda *_a: 71)
        guard_patch.setattr(
            namespace["signal"], "pidfd_send_signal",
            lambda *_a: (_ for _ in ()).throw(primary))
        guard_patch.setattr(
            namespace["os"], "close",
            lambda _fd: (_ for _ in ()).throw(secondary))
        namespace["exact_current"] = lambda current: ("MATCH", current)
        with pytest.raises(KeyboardInterrupt) as raised:
            namespace["signal_exact"]([saved], signal.SIGTERM)
    assert raised.value is primary
    _assert_close_secondary_note(primary, "pidfd-close-601")


def test_process_guard_capture_temp_close_fault_keeps_write_cancellation_primary(
        monkeypatch, tmp_path):
    namespace = _embedded_process_guard_namespace()
    primary = KeyboardInterrupt("receipt write cancellation")
    secondary = SystemExit(92)
    real_close = os.close
    closed = []

    def close_then_cancel(fd):
        closed.append(fd)
        real_close(fd)
        raise secondary

    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(
            namespace["os"], "write",
            lambda *_a: (_ for _ in ()).throw(primary))
        guard_patch.setattr(namespace["os"], "close", close_then_cancel)
        with pytest.raises(KeyboardInterrupt) as raised:
            namespace["capture"]([
                "capture", str(os.getpid()), str(tmp_path / "receipt.json"),
                "a" * 32, str(tmp_path), "0",
            ])
    assert raised.value is primary and len(closed) == 1
    _assert_close_secondary_note(primary, "capture-temp")


def test_process_guard_capture_directory_close_fault_keeps_fsync_primary(
        monkeypatch, tmp_path):
    namespace = _embedded_process_guard_namespace()
    primary = KeyboardInterrupt("directory fsync cancellation")
    secondary = SystemExit(93)
    real_fsync = os.fsync
    real_close = os.close
    fsyncs = []
    closes = []

    def fsync_then_cancel(fd):
        fsyncs.append(fd)
        if len(fsyncs) == 2:
            raise primary
        return real_fsync(fd)

    def close_directory_then_cancel(fd):
        closes.append(fd)
        real_close(fd)
        if len(closes) == 2:
            raise secondary

    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(namespace["os"], "fsync", fsync_then_cancel)
        guard_patch.setattr(namespace["os"], "close", close_directory_then_cancel)
        with pytest.raises(KeyboardInterrupt) as raised:
            namespace["capture"]([
                "capture", str(os.getpid()), str(tmp_path / "receipt.json"),
                "b" * 32, str(tmp_path), "0",
            ])
    assert raised.value is primary and len(fsyncs) == 2 and len(closes) == 2
    _assert_close_secondary_note(primary, "capture-directory")


def test_process_guard_signal_receipt_close_fault_keeps_send_primary(monkeypatch):
    namespace = _embedded_process_guard_namespace()
    primary = KeyboardInterrupt("receipt signal cancellation")
    secondary = SystemExit(94)
    saved = {"pid": 701, "ppid": 1, "pgid": 701,
             "sid": 701, "starttime": 7001}
    with monkeypatch.context() as guard_patch:
        namespace["exact_current"] = lambda current: ("MATCH", current)
        guard_patch.setattr(namespace["os"], "pidfd_open", lambda *_a: 81)
        guard_patch.setattr(
            namespace["signal"], "pidfd_send_signal",
            lambda *_a: (_ for _ in ()).throw(primary))
        guard_patch.setattr(
            namespace["os"], "close",
            lambda _fd: (_ for _ in ()).throw(secondary))
        receipt = ":".join(str(saved[key]) for key in
                           ("pid", "ppid", "pgid", "sid", "starttime"))
        with pytest.raises(KeyboardInterrupt) as raised:
            namespace["signal_receipt"](["signal", receipt, "TERM"])
    assert raised.value is primary
    _assert_close_secondary_note(primary, "receipt-signal")


def test_registration_channel_reader_close_fault_keeps_read_primary(monkeypatch):
    mod = _load()
    primary = KeyboardInterrupt("registration read cancellation")
    secondary = SystemExit(95)
    with monkeypatch.context() as reader_patch:
        reader_patch.setattr(sys, "argv", ["reader", "/fifo", "1", "ready", "1"])
        reader_patch.setattr(os, "open", lambda *_a, **_k: 91)
        reader_patch.setattr(select, "select", lambda *_a, **_k: ([91], [], []))
        reader_patch.setattr(
            os, "read", lambda *_a: (_ for _ in ()).throw(primary))
        reader_patch.setattr(
            os, "close", lambda _fd: (_ for _ in ()).throw(secondary))
        with pytest.raises(KeyboardInterrupt) as raised:
            exec(compile(
                mod.REGISTRATION_CHANNEL_READER_PROGRAM,
                "<registration-channel-reader>", "exec"), {})
    assert raised.value is primary
    _assert_close_secondary_note(primary, "registration-channel")


def test_reap_requires_exact_ssh_status_for_reap_ok(monkeypatch, capsys):
    """A transport failure cannot authenticate a success token in stdout."""
    mod = _load()
    monkeypatch.setattr(
        mod, "ssh",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            "reap", 255,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "connection lost"))

    verdict = mod.reap(
        "10.0.70.95", "/srv/bd-wedge/run/runner.receipt", "test")
    reported = capsys.readouterr().out

    assert verdict != "REAP-OK", (
        "FAILED-SSH-TRANSACTION-LAUNDERED-REAP-OK", verdict)
    assert "REAP-UNKNOWN" in reported, (
        "SSH-STATUS-TOKEN-AMBIGUITY-WAS-NOT-REPORTED", reported)


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        pytest.param(
            0,
            "REAP-SURVIVED receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=PRESENT wait=NOT-PARENT failures=NONE\n",
            "REAP-UNKNOWN",
            id="zero-cannot-authenticate-survived"),
        pytest.param(
            5,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="survived-status-cannot-authenticate-ok"),
        pytest.param(
            4,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="unknown-status-cannot-authenticate-ok"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n"
            "REAP-SURVIVED receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=PRESENT wait=NOT-PARENT failures=NONE\n",
            "REAP-UNKNOWN",
            id="conflicting-terminal-lines"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "REAP-OK",
            id="matching-early-ok"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=ABSENT wait=NOT-PARENT failures=NONE\n",
            "REAP-OK",
            id="matching-final-ok"),
        pytest.param(
            4,
            "REAP-UNKNOWN receipt=9:1:9:9:4 current=UNKNOWN term=NOT-SENT "
            "grace_us=0 kill=NOT-SENT census=UNKNOWN wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="matching-unknown"),
        pytest.param(
            5,
            "REAP-SURVIVED receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=PRESENT wait=NOT-PARENT failures=NONE\n",
            "REAP-SURVIVED",
            id="matching-survived"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4\n",
            "REAP-UNKNOWN",
            id="truncated-terminal-line"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=ABSENT wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="late-truncated-final-ok"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n"
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="duplicate-identical-terminal-lines"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n"
            "PROCESS-GUARD-UNKNOWN error=OSError\n",
            "REAP-UNKNOWN",
            id="reap-plus-process-guard-terminal"),
        pytest.param(
            0,
            "prefix REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="embedded-terminal-token"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=PRESENT wait=NOT-PARENT failures=NONE\n",
            "REAP-UNKNOWN",
            id="ok-verdict-census-mismatch"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 current=DRIFT term=NOT-SENT "
            "grace_us=0 kill=NOT-SENT census=UNKNOWN wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="ok-verdict-current-mismatch"),
        pytest.param(
            5,
            "REAP-SURVIVED receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=ABSENT wait=NOT-PARENT failures=NONE\n",
            "REAP-UNKNOWN",
            id="survived-verdict-census-mismatch"),
    ],
)
def test_reap_rejects_mismatched_terminal_status_and_token(
        monkeypatch, returncode, stdout, expected):
    """Only complete terminal lines paired with their protocol status survive."""
    mod = _load()
    monkeypatch.setattr(
        mod, "ssh",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            "reap", returncode, stdout, ""))

    verdict = mod.reap(
        "10.0.70.95", "/srv/bd-wedge/run/runner.receipt", "test")

    assert verdict == expected


def test_ambiguous_launch_result_records_and_reaps_predicted_receipt(
        monkeypatch):
    """Transport loss after fork cannot be reported as definite refusal."""
    mod = _load()

    def fake_ssh(_addr, command, timeout):
        if command == "nproc":
            return subprocess.CompletedProcess(command, 0, "24\n", "")
        assert "git rev-parse --short HEAD" in command
        return subprocess.CompletedProcess(command, 0, "deadbeef\n0\n", "")

    predicted = "/srv/bd-wedge/run/runner.receipt"
    monkeypatch.setattr(mod, "ssh", fake_ssh)
    settlements: list[tuple[str, str, str]] = []

    def fake_settle(addr, receipt, run_id):
        settlements.append((addr, receipt, run_id))
        return {
            "verdict": "REAP-OK",
            "terminal": "receipt-observed",
            "observations": ["PROCESS-GUARD-UNKNOWN", "RECEIPT-MATCH"],
        }

    assert hasattr(mod, "settle_ambiguous_launch"), (
        "ambiguous launch has no bounded receipt settlement seam")
    monkeypatch.setattr(mod, "settle_ambiguous_launch", fake_settle)
    for launch_rc, launch_out in ((255, ""), (0, "reply without marker")):
        settlements.clear()
        monkeypatch.setattr(
            mod, "launch", lambda *_args, rc=launch_rc, out=launch_out: {
                "rc": rc,
                "out": out,
                "err": "connection lost after remote fork",
                "run_id": "a" * 32,
                "runner_receipt": predicted,
            })
        row = mod.run_one(
            "test2", "10.0.70.95", ("base", [], True, {}, "note"), 1,
            object())

        assert row["state"] == "UNKNOWN", row
        assert row["runner_receipt"] == predicted
        assert row["run_nonce"] == "a" * 32
        assert row["reap"] == "REAP-OK"
        assert row["launch_settlement"]["terminal"] == "receipt-observed"
        assert settlements == [("10.0.70.95", predicted, "a" * 32)]


def test_ambiguous_launch_settlement_waits_for_matching_receipt_before_reap(
        monkeypatch):
    """A runner may publish its receipt after the ssh reply is lost."""
    mod = _load()
    receipt = "/srv/bd-wedge/run/runner.receipt"
    run_id = "b" * 32
    replies = iter([
        subprocess.CompletedProcess("probe", 4,
                                    "PROCESS-GUARD-UNKNOWN error=FileNotFoundError\n", ""),
        subprocess.CompletedProcess(
            "probe", 0,
            "RECEIPT-MATCH receipt=9:1:9:9:4 current=9:1:9:9:4 "
            f"run_id={run_id}\n", ""),
    ])
    monkeypatch.setattr(mod, "ssh", lambda *_args, **_kwargs: next(replies))
    reaps: list[tuple[str, str, str]] = []

    def fake_reap(addr, path, why):
        reaps.append((addr, path, why))
        return "REAP-OK"

    monkeypatch.setattr(mod, "reap", fake_reap)
    settled = mod.settle_ambiguous_launch(
        "10.0.70.95", receipt, run_id, timeout_s=1.0, poll_s=0.001)

    assert settled["verdict"] == "REAP-OK"
    assert settled["terminal"] == "receipt-observed"
    assert [item["state"] for item in settled["observations"]] == [
        "PROCESS-GUARD-UNKNOWN", "RECEIPT-MATCH"]
    assert reaps == [("10.0.70.95", receipt, "ambiguous launch result")]


def test_ambiguous_launch_settlement_refuses_a_different_run_nonce(monkeypatch):
    """The predicted pathname alone never authorizes cleanup."""
    mod = _load()
    receipt = "/srv/bd-wedge/run/runner.receipt"
    expected = "b" * 32
    observed = "c" * 32
    reply = subprocess.CompletedProcess(
        "probe", 0,
        "RECEIPT-MATCH receipt=9:1:9:9:4 current=9:1:9:9:4 "
        f"run_id={observed}\n", "")
    monkeypatch.setattr(mod, "ssh", lambda *_args, **_kwargs: reply)
    monkeypatch.setattr(
        mod, "reap",
        lambda *_args: pytest.fail("mismatched nonce authorized a reap"))

    settled = mod.settle_ambiguous_launch(
        "10.0.70.95", receipt, expected, timeout_s=1.0, poll_s=0.001)

    assert settled["verdict"] == "REAP-UNKNOWN"
    assert settled["terminal"] == "receipt-run-id-mismatch"
    assert settled["observations"][-1]["state"] == "RECEIPT-MATCH"


def test_ambiguous_launch_settlement_deadline_remains_unknown(monkeypatch):
    """No receipt by the bounded deadline proves neither launch nor cleanup."""
    mod = _load()
    reply = subprocess.CompletedProcess(
        "probe", 4, "PROCESS-GUARD-UNKNOWN error=FileNotFoundError\n", "")
    probes: list[str] = []

    def fake_ssh(_addr, command, **_kwargs):
        probes.append(command)
        return reply

    monkeypatch.setattr(mod, "ssh", fake_ssh)
    monkeypatch.setattr(
        mod, "reap",
        lambda *_args: pytest.fail("missing receipt authorized a reap"))
    started = time.monotonic()
    settled = mod.settle_ambiguous_launch(
        "10.0.70.95", "/srv/bd-wedge/run/runner.receipt", "d" * 32,
        timeout_s=0.01, poll_s=0.002)

    assert time.monotonic() - started < 1.0, "settlement deadline was not bounded"
    assert probes, "deadline result was vacuous: no receipt probe ran"
    assert settled["verdict"] == "REAP-UNKNOWN"
    assert settled["terminal"] == "receipt-deadline"
    assert all(item["state"] == "PROCESS-GUARD-UNKNOWN"
               for item in settled["observations"])


def test_every_abandon_path_reaps():
    """No terminal branch may leave a remote master running.

    Read as SOURCE STRUCTURE rather than per-line: the abandon branches are
    multi-line and a line-scoped check cannot see a reap three lines below the
    state it is judging (CLAUDE.md section 0's shell-construct trap, in Python).
    """
    src = _source()
    tree = ast.parse(src)
    consts = _string_constants()

    # The branches that END a run. Each is identified by the state it records,
    # asserted over LITERALS so a comment naming a state cannot satisfy it.
    for state in ("CAPPED", "UNKNOWN", "ABANDONED"):
        assert state in consts, (
            f"no branch records state {state!r} as a string literal. If the "
            "monitor loop can end a run without recording a distinguishable "
            "state, an abandoned sample is indistinguishable from a completed "
            "one -- which is how a false COMPLETED enters the wedge "
            "denominator. Backlog 146."
        )

    # Every reap must go through the shared builder, so there is exactly one
    # definition of "kill it properly" to get right.
    #
    # EXEMPT reap_cmd's OWN BODY BY STRUCTURE, NOT BY A SUBSTRING. The first
    # draft filtered constants that did not contain the word "reap", which
    # flagged reap_cmd itself -- the one place the kill is supposed to live.
    # Walking to the FunctionDef and excluding its subtree asks the question
    # that was actually meant.
    builder = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "reap_cmd"),
                   None)
    assert builder is not None, "reap_cmd is not a module-level function"
    exempt = {id(n) for n in ast.walk(builder)}

    # THE RUNNER TEMPLATE IS A SECOND EXEMPTION, AND THE COVERAGE MOVES RATHER
    # THAN DISAPPEARS -- the replacement assertion below is strictly stronger
    # than the ban it replaces. Row 212 gave the runner a registration-failure
    # branch that must reap the group it just launched. It cannot route that
    # through `reap_cmd`: reap_cmd builds an SSH command that probes a pid it
    # did not create and reports REAP-OK/REAP-SURVIVED to a monitor, while the
    # runner is already ON the host, owns the pid, and has no channel to report
    # to -- registration failing is exactly why nothing else knows the pid
    # exists. Shipping the remote verdict protocol into the runner text would
    # be a worse answer than this exemption.
    #
    # Exempted BY STRUCTURE (the RUNNER Assign node's subtree), never by a
    # substring: a substring exemption would also excuse a hand-rolled kill
    # anywhere else that happened to mention the word.
    runner_assign = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.Assign)
         and any(isinstance(t, ast.Name) and t.id == "RUNNER" for t in n.targets)),
        None)
    assert runner_assign is not None, (
        "RUNNER is no longer a module-level assignment; this exemption is now "
        "aimed at nothing and the check below cannot see its subject")
    exempt |= {id(n) for n in ast.walk(runner_assign)}

    # THE RE-CONSTRAINT. Registration failure happens before the anonymous gate
    # releases the workload, so that branch owns exactly one inert direct child
    # and must have NO numeric group-signal sink. EOF + ABORTED + checked wait is
    # stronger than trying to close a recyclable receipt-to-kill window.
    runner_text = _load().RUNNER
    assert not re.search(r'kill\s+-9\s+-"\$PYTEST_PGID"', runner_text), (
        "registration failure restored a numeric process-group signal sink; "
        "the PGID can be reused between a receipt and that action")
    executable_runner = "\n".join(
        line for line in runner_text.splitlines()
        if not line.lstrip().startswith("#"))
    alternate_signal_sinks = (
        r"(?m)^\s*(?:(?:builtin|command)\s+)?(?:kill|pkill)\b",
        r"(?m)^\s*/(?:usr/)?bin/kill\b",
        r"\bos\.(?:kill|killpg)\s*\(",
        r"\bsignal\.pidfd_send_signal\s*\(",
    )
    stop_start = executable_runner.index("registration_owner_stop() {{")
    stop_end = executable_runner.index(
        "\n}}\n\nregistration_promote_spawn_group_receipt() {{", stop_start)
    stop_end += len("\n}}\n")
    stop_body = executable_runner[stop_start:stop_end]
    assert "registration_promote_spawn_group_receipt" not in stop_body, (
        "the single signal-capability slice includes an adjacent owner helper")
    signal_lines = [
        line for line in executable_runner.splitlines()
        if any(re.search(pattern, line) for pattern in alternate_signal_sinks)
    ]
    assert len(signal_lines) == 2 and all(
        line in stop_body for line in signal_lines
    ), "a signal sink escaped the single timeout-owner stop capability"
    forbidden_targets = (
        "PYTEST_PID", "PYTEST_PGID", "PYTEST_GATE_PID",
        "W1_TERMINAL_RELAY_PID", "W1_JOB_ID",
    )
    assert all(not any(target in line for target in forbidden_targets)
               for line in signal_lines), (
        "the timeout-owner signal capability accepts a gate/receipt/job id")
    outside_stop = executable_runner[:stop_start] + executable_runner[stop_end:]
    stop_targets = re.findall(
        r'^\s*registration_owner_stop "\$(W1_[A-Z_]+)"',
        outside_stop, re.MULTILINE)
    assert stop_targets and set(stop_targets) == {
        "W1_SPAWN_PID", "W1_COLLECT_PID", "W1_TIMER_PID",
        "W1_ACTIVE_OWNER_PID",
    }, "an unowned identity reaches registration_owner_stop"
    assert "W1_READY_SECONDS=10" in runner_text, (
        "READY admission has no explicit finite deadline")
    assert "W1_GATE_SECONDS=10" in runner_text, (
        "abort/handoff has no explicit finite deadline")
    assert 'registration_read_frame "$W1_READY_SECONDS"' in runner_text
    assert "registration_read_terminal" in runner_text
    assert "registration_status_is_quiet_and_open" not in runner_text
    assert "registration_status_is_eof" not in runner_text
    assert 'kill "$W1_REGISTRAR_PID"' not in runner_text
    assert "registration_checked_gate_wait" in runner_text
    assert runner_text.count("registration_checked_child_wait() {{") == 1
    assert ('wait -n -p W1_RACE_WAITED_PID "$W1_CHILD_PID" '
            '"$W1_TIMER_PID"') in runner_text
    assert "READY v1 pid=" in _load().REGISTRATION_GATE_PROGRAM
    assert "ABORTED v1 reason=" in _load().REGISTRATION_GATE_PROGRAM
    assert "EXEC-OK v1" in _load().registration_workload_shim("/x", "true")
    assert "REGISTER-GATE-ABORT" in runner_text

    hand_rolled = sorted(
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and "kill -9" in n.value and id(n) not in exempt
    )
    assert not hand_rolled, (
        "a hand-rolled `kill -9` survives outside reap_cmd, at line(s) "
        f"{hand_rolled}. Four branches with four kill spellings is what let the "
        "CAPPED path kill only the master and orphan its 48 workers. Route it "
        "through reap_cmd."
    )


def test_an_abandoned_run_is_not_recorded_as_COMPLETED():
    """A false COMPLETED is a false NEGATIVE in the wedge rate.

    The monitor loop is `while not STOP.is_set()`, so a STOP set by --hours or
    by an interrupt drops it out on the next tick with no `pytest_exit`. If the
    row then defaults to COMPLETED, an abandoned sample silently joins the
    denominator as a non-wedge.
    """
    consts = _string_constants()
    assert "ABANDONED" in consts, (
        "no ABANDONED state is ever recorded as a literal. The row still "
        "defaults to COMPLETED with no guard for the abandoned case, so a run "
        "dropped by STOP -- which has no pytest_exit -- is counted as a "
        "completed non-wedge, a false negative in the wedge rate."
    )
    assert "pytest_exit" in consts, "the completion marker vanished"

    # The ABANDONED branch must be GUARDED on the run not having finished, not
    # written unconditionally: an unconditional ABANDONED would mark every
    # completed sample abandoned, which passes the literal check above and
    # destroys the data. Over-sensitivity is a soundness bug (section 0).
    tree = ast.parse(_source())
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.unparse(node.test)
        body_src = "\n".join(ast.unparse(b) for b in node.body)
        if "ABANDONED" in body_src and "pytest_exit" in test_src:
            guarded = True
    assert guarded, (
        "the ABANDONED state is not guarded on the absence of pytest_exit. It "
        "must fire only for a run that never finished; marking a completed "
        "sample abandoned would be the same defect pointing the other way."
    )


@pytest.mark.parametrize(("status", "state"), [
    (91, "REGISTRATION_REFUSED"),
    (92, "REGISTRATION_UNKNOWN"),
    (93, "REGISTERED_FAILURE"),
    (94, "REGISTRATION_SETUP_FAILURE"),
])
def test_registration_runner_exit_is_classified_before_completed_default(
        status, state):
    """The outer reader must not call a registration failure COMPLETED."""
    mod = _load()
    row = {}

    mod.record_runner_exit(row, str(status), "/remote/run/private-root")

    assert row["pytest_exit"] == str(status)
    assert row["state"] == state, (
        f"runner status {status} was laundered into the COMPLETED default")
    assert row["state"] != "COMPLETED"
    assert row["registration_artifacts"] == {
        "jobid": "/remote/run/private-root/jobid",
        "error": "/remote/run/private-root/jobid.err",
        "owners": "/remote/run/private-root/registration-owners.log",
        "runner_receipt": "/remote/run/private-root/runner.receipt",
        "gate_receipt": "/remote/run/private-root/gate.receipt",
        "authority_fds": (
            "/remote/run/private-root/registration-authority-fds.log"),
    }


@pytest.mark.parametrize("status", [0, 1, 75, 90])
def test_nonregistration_runner_exit_keeps_ordinary_completion_policy(status):
    mod = _load()
    row = {}

    mod.record_runner_exit(row, str(status), "/remote/run/control")

    assert row == {"pytest_exit": str(status)}


def test_the_interrupt_handler_does_not_promise_what_it_does_not_do():
    """CLAUDE.md section 10: the verdict line is the least-tested output.

    Two messages here were false. The interrupt said in-flight samples are NOT
    killed -- true, and a leak. `--hours` said it was 'letting in-flight samples
    finish', which the loop's own STOP predicate makes impossible. A message
    that misdescribes the behaviour beside it is how row 146 got its wrong
    diagnosis in the first place.
    """
    # Over LITERALS, not raw source. This test's own explanatory prose quotes
    # both retired phrases, and the first draft grepped the file -- it passed
    # only because the comment wrap happened to split one of them across a
    # newline. That is luck, not a check.
    said = " || ".join(_string_constants())
    assert "in-flight samples are NOT killed" not in said, (
        "the interrupt handler still advertises that it leaks. It should reap "
        "in-flight runs and join its threads so their rows are written."
    )
    assert "letting in-flight samples finish" not in said, (
        "--hours still claims to let in-flight samples finish. The monitor loop "
        "is `while not STOP.is_set()`, so they are abandoned on the next tick."
    )


def test_the_interrupt_joins_its_threads_so_rows_are_written():
    """Daemon threads die at interpreter exit, taking their unwritten rows.

    That is why `rows.jsonl` held ZERO abandoned rows after an interrupted 19h
    hunt: the samples were not mis-recorded, they were never recorded at all.
    """
    tree = ast.parse(_source())
    handlers = [n for n in ast.walk(tree)
                if isinstance(n, ast.ExceptHandler)
                and isinstance(n.type, ast.Name)
                and n.type.id == "KeyboardInterrupt"]
    assert handlers, "no KeyboardInterrupt handler found -- has main() changed?"

    # ASK FOR A THREAD JOIN, NOT FOR THE SUBSTRING "join". The first version of
    # this assertion was `"join" in ast.unparse(handler)`, and a mutation
    # battery escaped it immediately: the handler's own warning line contains
    # `", ".join(alive)`, so the str method satisfied a check written about
    # Thread.join. CLAUDE.md section 1 -- a predicate over the wrong part of
    # the syntax is worse than a grep, because it looks rigorous.
    #
    # The shape required: a `for` loop over the threads whose body joins the
    # loop variable.
    def _joins_its_loop_var(handler: ast.ExceptHandler) -> bool:
        for node in ast.walk(handler):
            if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
                continue
            var = node.target.id
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "join"
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == var):
                    return True
        return False

    assert any(_joins_its_loop_var(h) for h in handlers), (
        "the KeyboardInterrupt handler does not join its host threads. They are "
        "daemon=True, so the interpreter kills them at exit: the remote run is "
        "never reaped AND its row is never written. That is the whole of "
        "backlog 146's five orphaned masters, and it is why rows.jsonl held "
        "ZERO abandoned rows after an interrupted 19h hunt. Join them (bounded) "
        "so each thread can reap and record."
    )


# ---------------------------------------------------------------------------
# W1 -- BACKLOG 212. The runner must not wait on a job it could not register.
#
# The abandon paths above are about a run the MONITOR gives up on. This battery is
# about the other end of the same leak: the remote runner script itself. Its
# registration step reads
#
#     python3 .../bd-jobs register ... > "$RUNDIR/jobid" 2>... || \
#         echo "REGISTER-FAILED" > "$RUNDIR/jobid"
#     wait "$PYTEST_PID"
#
# so a registrar that fails -- a torn write, a full disk, an unwritable
# JOBS_DIR, row 212's whole subject -- is SWALLOWED, and the runner then waits
# for the full pytest run it just started. The result is a live pytest master
# and up to 48 workers on a fleet host that `bd-jobs list` cannot see and
# `bd-jobs reap` will never reach, for the entire duration of the run. The
# monitor's own reaping cannot help: it reaps by the row it knows about, and
# the operator's registry is precisely what is missing.
#
# WHY THE PRODUCTION TEMPLATE AND A REAL BASH. The subject is shell text. A
# copy of it in this file would be a test of the copy, and a structural check
# over `mod.RUNNER` cannot tell `kill` from `kill` in a comment or prove the
# child actually died. So: format the REAL `mod.RUNNER`, point `$HOME` at a
# fake checkout whose only content is a stub `bd-jobs`, and run it under real
# bash. Exactly one boundary is stubbed -- the registrar's exit status.
#
# WHAT THIS BATTERY CANNOT SEE: it drives the runner LOCALLY. The ssh transport,
# `setsid nohup`, and the remote host's environment are outside it.
# ---------------------------------------------------------------------------

W1_REGISTER_FAILURE_CODE = 73    # the stub registrar's distinctive exit
W1_RUNNER_FAILURE_CODE = "91"    # what the runner must record for itself
W1_RETAINED_FAILURE_CODE = "92"  # cleanup timed out with an exact retained id
W1_SETUP_FAILURE_CODE = "94"     # setup owners settled UNSUCCESSFULLY,
                                 # which is a PROVED settlement, not an
                                 # unknown one -- see the gate-ready
                                 # admission controls below
W1_RELEASE_FAILURE_CODE = "93"   # registration landed but gate release failed
W1_WORKLOAD_CODE = 7             # the success control's workload exit
W1_STUB_MARKER = "STUB-REGISTRAR-REACHED"
W1_RUNNER_BOUND = 40.0           # every wait in this battery is bounded


def _w1_fake_home(tmp_path, *, code: int, stdout: str = "", sleep: float = 0.0):
    """A fake `$HOME` whose only inhabitant is a stub `bd-jobs`.

    The runner invokes `python3 "$HOME/BulkDownloader/toolchain/bin/bd-jobs"`,
    so the stub is Python, not shell, and needs no exec bit. `sleep` exists so
    the caller can observe the launched process group WHILE it is alive: a
    reap assertion with no proven live group before it is the empty-iterable
    green CLAUDE.md section 7 names.
    """
    home = tmp_path / "fakehome"
    binp = home / "BulkDownloader" / "toolchain" / "bin"
    binp.mkdir(parents=True)
    (binp / "bd-jobs").write_text(
        "import os, pathlib, signal, sys, time\n"
        "marker = os.environ.get('W1_REGISTRAR_MARKER')\n"
        "if marker:\n"
        "    pathlib.Path(marker).write_text('invoked', encoding='utf-8')\n"
        "argv_log = os.environ.get('W1_STUB_ARGV_LOG')\n"
        "if argv_log:\n"
        "    with pathlib.Path(argv_log).open('a', encoding='utf-8') as stream:\n"
        "        stream.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "pid_marker = os.environ.get('W1_REGISTRAR_PID_MARKER')\n"
        "if pid_marker:\n"
        "    pathlib.Path(pid_marker).write_text(str(os.getpid()), encoding='utf-8')\n"
        "is_register = len(sys.argv) > 1 and sys.argv[1] == 'register'\n"
        "is_reap = len(sys.argv) > 1 and sys.argv[1] == 'reap'\n"
        "registered_pid_file = os.environ.get('W1_REGISTERED_PID_FILE')\n"
        "if is_register and registered_pid_file:\n"
        "    registered_pid = sys.argv[sys.argv.index('--pid') + 1]\n"
        "    pathlib.Path(registered_pid_file).write_text(registered_pid, encoding='utf-8')\n"
        "register_entered = os.environ.get('W1_REGISTER_ENTERED_FIFO')\n"
        "register_release = os.environ.get('W1_REGISTER_RELEASE_FIFO')\n"
        "if is_register and register_entered and register_release:\n"
        "    with open(register_entered, 'w', encoding='utf-8') as stream:\n"
        "        stream.write('register-entered\\n')\n"
        "    with open(register_release, 'r', encoding='utf-8') as stream:\n"
        "        stream.readline()\n"
        "reap_pid_marker = os.environ.get('W1_REAP_PID_MARKER')\n"
        "if is_reap and reap_pid_marker:\n"
        "    pathlib.Path(reap_pid_marker).write_text(str(os.getpid()), encoding='utf-8')\n"
        "if len(sys.argv) > 1 and sys.argv[1] == 'reap' and "
        "os.environ.get('W1_REAP_IGNORE_TERM'):\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "reap_child_marker = os.environ.get('W1_REAP_CHILD_PID_MARKER')\n"
        "if is_reap and reap_child_marker:\n"
        "    child_ready_read, child_ready_write = os.pipe()\n"
        "    child = os.fork()\n"
        "    if child == 0:\n"
        "        os.close(child_ready_read)\n"
        "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "        pathlib.Path(reap_child_marker).write_text(str(os.getpid()), encoding='utf-8')\n"
        "        os.write(child_ready_write, b'1')\n"
        "        os.close(child_ready_write)\n"
        "        time.sleep(300)\n"
        "        raise SystemExit(0)\n"
        "    os.close(child_ready_write)\n"
        "    os.read(child_ready_read, 1)\n"
        "    os.close(child_ready_read)\n"
        "reap_entered = os.environ.get('W1_REAP_ENTERED_FIFO')\n"
        "reap_release = os.environ.get('W1_REAP_RELEASE_FIFO')\n"
        "if is_reap and reap_entered and reap_release:\n"
        "    with open(reap_entered, 'w', encoding='utf-8') as stream:\n"
        "        stream.write('reap-entered\\n')\n"
        "    with open(reap_release, 'r', encoding='utf-8') as stream:\n"
        "        stream.readline()\n"
        "if is_reap and os.environ.get('W1_REAP_IGNORE_TERM'):\n"
        "    time.sleep(float(os.environ.get('W1_REAP_IGNORE_TERM', '300')))\n"
        "if is_reap and os.environ.get('W1_REAP_KILL_REGISTERED') and registered_pid_file:\n"
        "    registered_pid = int(pathlib.Path(registered_pid_file).read_text())\n"
        "    try:\n"
        "        os.killpg(registered_pid, signal.SIGKILL)\n"
        "    except ProcessLookupError:\n"
        "        pass\n"
        "fd_report = os.environ.get('W1_REGISTRAR_FD_REPORT')\n"
        "if fd_report:\n"
        "    rows = []\n"
        "    for item in pathlib.Path('/proc/self/fd').iterdir():\n"
        "        try:\n"
        "            rows.append(item.name + '=' + os.readlink(item))\n"
        "        except OSError:\n"
        "            pass\n"
        "    fd_report_path = pathlib.Path(fd_report)\n"
        "    fd_report_temp = fd_report_path.with_name(\n"
        "        fd_report_path.name + '.tmp.%d' % os.getpid())\n"
        "    fd_report_temp.write_text('\\n'.join(rows) + '\\n', encoding='utf-8')\n"
        "    os.replace(fd_report_temp, fd_report_path)\n"
        "sys.stderr.write({marker!r} + ' ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep({sleep!r})\n"
        "if os.environ.get('W1_REGISTRAR_KILL_REGISTERED_PID'):\n"
        "    pid = int(sys.argv[sys.argv.index('--pid') + 1])\n"
        "    os.kill(pid, 9)\n"
        "    deadline = time.monotonic() + 2.0\n"
        "    while pathlib.Path('/proc', str(pid)).exists() and "
        "time.monotonic() < deadline:\n"
        "        time.sleep(0.01)\n"
        "sys.stdout.write({stdout!r})\n"
        "sys.exit({code!r})\n".format(
            marker=W1_STUB_MARKER, sleep=float(sleep), stdout=stdout, code=int(code)),
        encoding="utf-8")
    return home


def _w1_live_in_group(pgid: int) -> list[str]:
    """Live (non-zombie) pids sharing `pgid`. A zombie is gone for our purpose."""
    r = subprocess.run(["ps", "-eo", "pgid=,pid=,stat="],
                       capture_output=True, text=True)
    rows = [l.split() for l in r.stdout.splitlines() if l.split()]
    return [x[1] for x in rows if x[0] == str(pgid) and not x[2].startswith("Z")]


def _w1_pid_is_live(pid: int) -> bool:
    """A present zombie is already terminal and cannot perform a side effect."""
    try:
        state = _w1_proc_observation(pid)[-1]
    except (FileNotFoundError, ProcessLookupError, ValueError, AssertionError):
        return False
    return state not in {"Z", "X"}


def _w1_build_runner(mod, tmp_path, workload_body: str, *, reap_seconds=None,
                     ready_seconds=None,
                     proc_stat_path=None, gate_prelude=None,
                     pytest_pid_override=None, gate_program=None,
                     terminal_relay_program=None,
                     channel_reader_program=None,
                     timeout_owner_program=None,
                     owner_kill_grace_us=None,
                     missing_setup_fd=None, missing_setup_pid=None,
                     registrar_seconds=None,
                     reconcile_seconds=None, checked_wait_probe=None,
                     monotonic_samples=None,
                     cancel_before_observation=False,
                     cancel_registered_failure=False,
                     after_relay_acquire_barrier=None,
                     abnormal_owner_fifo=None,
                     after_terminal_owner_ready_barrier=None,
                     mutate_terminal_owner_ready=False,
                     handoff_deadline_probe=None,
                     before_release_write_barrier=None,
                     after_release_pipe_probe=None,
                     owned_group_census_override=None,
                     before_group_receipt_recheck_fifo=None,
                     after_group_receipt_recheck_fifo=None):
    """Format the PRODUCTION template around a workload we can watch."""
    rundir = tmp_path / "rundir"
    workload = tmp_path / "workload.sh"
    workload.write_text(workload_body, encoding="utf-8")
    cmd = "bash " + shlex.quote(str(workload))
    body = mod.RUNNER.format(
        rundir=shlex.quote(str(rundir)),
        run_id=shlex.quote(os.urandom(16).hex()),
        cmd=cmd,
        purpose=shlex.quote("row212-w1"),
        origin=shlex.quote("pytest-w1"),
        cmdq=shlex.quote(cmd),
        registration_probe=shlex.quote(
            getattr(mod, "REGISTRATION_PROBE_PROGRAM", "")),
        registration_gate=shlex.quote(
            (getattr(mod, "REGISTRATION_GATE_PROGRAM", "")
             if gate_program is None else gate_program)),
        registration_bootstrap=shlex.quote(
            getattr(mod, "REGISTRATION_GATE_BOOTSTRAP_PROGRAM", "")),
        registration_terminal_relay=shlex.quote(
            (getattr(mod, "REGISTRATION_TERMINAL_RELAY_PROGRAM", "")
             if terminal_relay_program is None else terminal_relay_program)),
        registration_timeout_owner=shlex.quote(
            (getattr(mod, "REGISTRATION_TIMEOUT_OWNER_PROGRAM", "")
             if timeout_owner_program is None else timeout_owner_program)),
        registration_channel_reader=shlex.quote(
            (getattr(mod, "REGISTRATION_CHANNEL_READER_PROGRAM", "")
             if channel_reader_program is None else channel_reader_program)),
        process_guard=shlex.quote(
            getattr(mod, "PROCESS_GUARD_PROGRAM", "")),
        workload_shim=shlex.quote(
            mod.registration_workload_shim(str(rundir), cmd)),
    )
    if reap_seconds is not None:
        anchor = "W1_GATE_SECONDS=10"
        assert body.count(anchor) == 1, (
            "the production runner has no single finite gate-protocol deadline")
        body = body.replace(anchor, "W1_GATE_SECONDS=%d" % reap_seconds)
    if ready_seconds is not None:
        ready_anchor = "W1_READY_SECONDS=10"
        assert body.count(ready_anchor) == 1, (
            "the production runner has no finite READY-owner deadline")
        body = body.replace(
            ready_anchor, "W1_READY_SECONDS=%d" % ready_seconds)
    if registrar_seconds is not None:
        registrar_anchor = "W1_REGISTRAR_SECONDS=30"
        assert body.count(registrar_anchor) == 1, (
            "the production runner has no finite registrar-owner deadline")
        body = body.replace(
            registrar_anchor, "W1_REGISTRAR_SECONDS=%d" % registrar_seconds)
    if reconcile_seconds is not None:
        reconcile_anchor = "W1_RECONCILE_SECONDS=10"
        assert body.count(reconcile_anchor) == 1, (
            "the production runner has no finite reconciliation deadline")
        body = body.replace(
            reconcile_anchor, "W1_RECONCILE_SECONDS=%d" % reconcile_seconds)
    if owner_kill_grace_us is not None:
        grace_anchor = "W1_OWNER_KILL_GRACE_US=100000"
        assert body.count(grace_anchor) == 1, (
            "the production runner has no unique positive owner KILL grace")
        body = body.replace(
            grace_anchor,
            "W1_OWNER_KILL_GRACE_US=%d" % int(owner_kill_grace_us),
        )
    if proc_stat_path is not None:
        anchor = '"/proc/$PYTEST_PID/stat"'
        assert body.count(anchor) >= 1, (
            "the production runner has no process-observation path to drive")
        body = body.replace(anchor, shlex.quote(str(proc_stat_path)), 1)
    if gate_prelude is not None:
        anchor = "coproc W1_REGISTRATION_GATE {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no single blocked registration gate; "
            "the group-settlement schedule is unreachable")
        body = body.replace(anchor, anchor + gate_prelude.rstrip() + "\n", 1)
    if pytest_pid_override is not None:
        anchors = ("PYTEST_PID=$PYTEST_GATE_PID",
                   "PYTEST_PID=$W1_REGISTRATION_GATE_PID", "PYTEST_PID=$!")
        found = [anchor for anchor in anchors if body.count(anchor) == 1]
        assert len(found) == 1, (
            "the production runner has no unique launched-child pid assignment")
        body = body.replace(
            found[0], "PYTEST_PID=%d" % int(pytest_pid_override), 1)
    if missing_setup_fd is not None:
        aliases = {
            "read": "gate_read",
            "write": "gate_write",
        }
        missing_setup_fd = aliases.get(missing_setup_fd, missing_setup_fd)
        fd_specs = {
            "gate_read": ("W1_GATE_READ_FD", "<&-"),
            "gate_write": ("W1_GATE_WRITE_FD", ">&-"),
            "terminal_read": ("W1_TERMINAL_READ_FD", "<&-"),
            "terminal_write": ("W1_TERMINAL_PARENT_WRITE_FD", ">&-"),
        }
        assert missing_setup_fd in fd_specs
        name, operator = fd_specs[missing_setup_fd]
        anchor = "PYTEST_PID=$PYTEST_GATE_PID"
        assert body.count(anchor) == 1
        body = body.replace(
            anchor,
            'eval "exec $%s%s"\n%s=""\n' % (name, operator, name) + anchor,
            1,
        )
    if missing_setup_pid is not None:
        assert missing_setup_pid in {"gate", "relay"}
        name = ("PYTEST_GATE_PID" if missing_setup_pid == "gate"
                else "W1_TERMINAL_RELAY_PID")
        anchor = "PYTEST_PID=$PYTEST_GATE_PID"
        assert body.count(anchor) == 1
        body = body.replace(
            anchor,
            "builtin printf '%s\\n' \"$PYTEST_GATE_PID\" > "
            "\"$RUNDIR/injected-gate.pid\"\n"
            "builtin printf '%s\\n' \"$W1_TERMINAL_RELAY_PID\" > "
            "\"$RUNDIR/injected-relay.pid\"\n"
            "%s=\"\"\n%s" % ("%s", "%s", name, anchor),
            1,
        )
    if checked_wait_probe is not None:
        anchor = "registration_checked_gate_wait() {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no unique checked-wait owner to probe")
        body = body.replace(
            anchor,
            anchor + "    builtin printf 'entered\\n' >> %s\n" %
            shlex.quote(str(checked_wait_probe)),
            1,
        )
    if owned_group_census_override is not None:
        assert owned_group_census_override in {
            "ABSENT", "UNKNOWN", "AUXILIARY-ABSENT"}
        anchor = "registration_owned_group_census() {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no unique owned-group census boundary")
        if owned_group_census_override == "AUXILIARY-ABSENT":
            injected = (
                '    if [ "$1" != gate ]; then\n'
                "        W1_OWNED_GROUP_STATUS=ABSENT\n"
                "        return 0\n"
                "    fi\n"
            )
        else:
            injected = "    W1_OWNED_GROUP_STATUS=%s\n    return 0\n" % (
                owned_group_census_override)
        body = body.replace(anchor, anchor + injected, 1)
    if after_group_receipt_recheck_fifo is not None:
        # A barrier AFTER the deciding probe, not merely before it. Releasing
        # the pre-probe barrier only proves the runner was WOKEN; it does not
        # prove it reached the probe, and under load the gate can exit in the
        # gap. That gap is exactly how the ABSENT control failed on a loaded
        # 48-core host and on a 2-core CI runner. Reading this fifo proves the
        # receipt recheck has already run.
        anchor = (
            "    W1_OWNER_FDS_BY_PID[$PYTEST_GATE_PID]=UNKNOWN\n"
            "fi\n")
        assert body.count(anchor) == 1, (
            "the production runner has no unique gate receipt-recheck exit")
        body = body.replace(
            anchor,
            anchor + "builtin printf 'gate-receipt-recheck-done\\n' > %s\n" %
            shlex.quote(str(after_group_receipt_recheck_fifo)),
            1,
        )
    if before_group_receipt_recheck_fifo is not None:
        anchor = (
            'registration_cancel_checkpoint "after-gate-acquire"\n\n'
            "W1_GATE_GROUP_READY_AT_ACQUIRE=0")
        assert body.count(anchor) == 1, (
            "the production runner has no unique gate-receipt recheck boundary")
        body = body.replace(
            anchor,
            'registration_cancel_checkpoint "after-gate-acquire"\n'
            "IFS= read -r W1_TEST_GATE_POST_SETSID < %s\n\n"
            "W1_GATE_GROUP_READY_AT_ACQUIRE=0" %
            shlex.quote(str(before_group_receipt_recheck_fifo)),
            1,
        )
    if cancel_before_observation:
        anchor = "registration_observation_matches_original() {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no unique identity boundary to inject")
        body = body.replace(
            anchor, anchor + "    W1_CANCEL_STATUS=130\n", 1)
    if cancel_registered_failure:
        anchor = "registration_fail_registered() {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no unique registered-failure funnel")
        body = body.replace(
            anchor, anchor + "    W1_CANCEL_STATUS=130\n", 1)
    if after_relay_acquire_barrier is not None:
        entered, release = after_relay_acquire_barrier
        anchor = 'registration_cancel_checkpoint "after-relay-acquire"'
        assert body.count(anchor) == 1, (
            "the production runner has no unique post-relay authority boundary")
        body = body.replace(
            anchor,
            "registration_process_receipt \"$W1_TERMINAL_RELAY_PID\"\n"
            "builtin printf '%s\\n' \"$W1_PROCESS_RECEIPT\" > "
            "\"$RUNDIR/injected-relay.receipt\"\n"
            "builtin printf 'relay-acquired\\n' > %s\n"
            "IFS= read -r W1_TEST_RELAY_RELEASE < %s\n%s" % (
                "%s", shlex.quote(str(entered)),
                shlex.quote(str(release)), anchor),
            1,
        )
    if abnormal_owner_fifo is not None:
        anchor = (
            'W1_ACTIVE_OWNER_PID="$W1_SPAWN_PID"\n'
            '    W1_ACTIVE_OWNER_GROUP_READY=0\n'
            '    W1_ACTIVE_OWNER_RECEIPT=UNKNOWN')
        assert body.count(anchor) == 1, (
            "the production owner has no unique post-acquisition boundary")
        body = body.replace(
            anchor,
            anchor
            + "\n    if [ \"$W1_SPAWN_ROLE\" = ready-reader ]; then\n"
              "        IFS= read -r W1_TEST_OWNER_ACQUIRED < %s\n"
              "        : \"$W1_INJECTED_EXIT_UNSET\"\n"
              "    fi" %
              shlex.quote(str(abnormal_owner_fifo)),
            1,
        )
    if after_terminal_owner_ready_barrier is not None:
        entered, release, owner_pid_path = after_terminal_owner_ready_barrier
        anchor = (
            "    registration_promote_spawn_group_receipt || :\n"
            "    IFS= read -r -t \"$W1_SPAWN_TIMEOUT\" W1_SPAWN_EXTRA \\\n")
        assert body.count(anchor) == 1, (
            "the production owner has no unique post-READY promotion boundary")
        ready_mutation = (
            "        W1_TEST_READY_CLAIM=\"${W1_SPAWN_READY_LINE#OWNER-READY v2 receipt=}\"\n"
            "        W1_TEST_READY_CLAIM=\"${W1_TEST_READY_CLAIM% fds=0,1,2}\"\n"
            "        W1_TEST_READY_START=\"${W1_TEST_READY_CLAIM##*:}\"\n"
            "        W1_TEST_READY_CLAIM=\"${W1_TEST_READY_CLAIM%:*}:$((W1_TEST_READY_START + 1))\"\n"
            "        W1_SPAWN_READY_LINE=\"OWNER-READY v2 receipt=$W1_TEST_READY_CLAIM fds=0,1,2\"\n"
            if mutate_terminal_owner_ready else "")
        injection = (
            '    if [ "$W1_SPAWN_ROLE" = terminal-reader ]; then\n'
            + ready_mutation
            + "        builtin printf '%s\\n' \"$W1_SPAWN_PID\" > %s\n"
            "        builtin printf 'owner-ready-read\\n' > %s\n"
            "        IFS= read -r W1_TEST_OWNER_RELEASE < %s\n"
            "    fi\n" % (
                "%s", shlex.quote(str(owner_pid_path)),
                shlex.quote(str(entered)), shlex.quote(str(release))))
        body = body.replace(anchor, injection + anchor, 1)
    if handoff_deadline_probe is not None:
        before = 'registration_cancel_checkpoint "pre-release"'
        after = "registration_read_terminal\nW1_HANDOFF_FRAME=\"$W1_FRAME\""
        assert body.count(before) == 1 and body.count(after) == 1, (
            "the production handoff has no unique deadline snapshot boundaries")
        body = body.replace(
            before,
            before + "\nbuiltin printf 'pre=%s\\n' "
            '"$W1_ACTIVE_DEADLINE_US" > ' +
            shlex.quote(str(handoff_deadline_probe)),
            1,
        )
        body = body.replace(
            after,
            "builtin printf 'post=%s\\n' \"$W1_ACTIVE_DEADLINE_US\" >> "
            + shlex.quote(str(handoff_deadline_probe)) + "\n" + after,
            1,
        )
    if before_release_write_barrier is not None:
        entered, release = before_release_write_barrier
        anchor = 'registration_cancel_checkpoint "pre-release"'
        assert body.count(anchor) == 1, (
            "the production runner has no unique pre-release authority boundary")
        body = body.replace(
            anchor,
            anchor
            + "\nbuiltin printf 'release-write-entered\\n' > %s\n"
              "IFS= read -r W1_TEST_RELEASE_WRITE < %s" % (
                  shlex.quote(str(entered)), shlex.quote(str(release))),
            1,
        )
    if after_release_pipe_probe is not None:
        anchor = 'registration_cancel_checkpoint "post-release-write"'
        assert body.count(anchor) == 1, (
            "the production runner has no unique release PIPE restoration edge")
        body = body.replace(
            anchor,
            "trap -p PIPE > %s\n%s" % (
                shlex.quote(str(after_release_pipe_probe)), anchor),
            1,
        )
    if monotonic_samples is not None:
        samples = [int(value) for value in monotonic_samples]
        assert samples
        anchor = "registration_monotonic_sample() {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no injectable monotonic sample boundary")
        end = "}\n\nregistration_now_us() {\n"
        start_at = body.index(anchor)
        end_at = body.index(end, start_at)
        cases = "\n".join(
            "        %d) W1_CLOCK_SAMPLE_US=%d ;;" % (index, value)
            for index, value in enumerate(samples)
        )
        replacement = (
            "W1_CLOCK_SAMPLE_INDEX=0\n"
            "registration_monotonic_sample() {\n"
            "    case \"$W1_CLOCK_SAMPLE_INDEX\" in\n%s\n"
            "        *) W1_CLOCK_SAMPLE_US=%d ;;\n"
            "    esac\n"
            "    W1_CLOCK_SAMPLE_INDEX=$((W1_CLOCK_SAMPLE_INDEX + 1))\n"
            "}\n\nregistration_now_us() {\n" % (cases, samples[-1])
        )
        body = body[:start_at] + replacement + body[end_at + len(end):]
    script = tmp_path / "runner.sh"
    script.write_text(body, encoding="utf-8")
    return script, rundir


def _w1_adversarial_gate_program(*, ready=None, terminal=None,
                                 terminal_bytes=None, delay_before_terminal=0.0,
                                 terminal_suffix=None, delay_before_suffix=0.0,
                                 terminal_suffix_entered=None,
                                 terminal_suffix_release=None,
                                 hold=0.0, status=0, extra_ready=None,
                                 delay_before_ready=0.0,
                                 delay_before_extra_ready=0.0,
                                 nul_ready=False, before_ready_marker=None,
                                 ready_release=None, ready_written_marker=None,
                                 extra_ready_release=None):
    """A real pipe peer for protocol-boundary schedules, not a runner mock."""
    assert (terminal is None) != (terminal_bytes is None)
    ready_expr = ("'READY v1 pid=%d' % os.getpid()"
                  if ready is None else repr(ready))
    ready_stmt = (
        "os.write(1, b'READY\\x00 v1 pid=%d\\n' % os.getpid())"
        if nul_ready else "emit(1, %s)" % ready_expr
    )
    terminal_stmt = (
        "emit(3, %r)" % terminal if terminal is not None
        else "os.write(3, %r)" % bytes(terminal_bytes)
    )
    if terminal_suffix is None:
        suffix_stmt = ""
    elif (terminal_suffix_entered is not None
          and terminal_suffix_release is not None):
        suffix_stmt = (
            "with open(%r, 'w', encoding='utf-8') as stream:\n"
            "    stream.write('partial-terminal-written\\n')\n"
            "with open(%r, 'r', encoding='utf-8') as stream:\n"
            "    stream.readline()\n"
            "os.write(3, %r)\n" % (
                str(terminal_suffix_entered), str(terminal_suffix_release),
                bytes(terminal_suffix))
        )
    else:
        suffix_stmt = "time.sleep(%r)\nos.write(3, %r)\n" % (
            float(delay_before_suffix), bytes(terminal_suffix))
    if extra_ready is None:
        extra_ready_stmt = ""
    elif ready_written_marker is not None and extra_ready_release is not None:
        extra_ready_stmt = (
            "pathlib.Path(%r).write_text('ready-written')\n"
            "with open(%r, 'r', encoding='utf-8') as stream:\n"
            "    stream.readline()\n"
            "try:\n"
            "    emit(1, %r)\n"
            "except BrokenPipeError:\n"
            "    pass\n" %
            (str(ready_written_marker), str(extra_ready_release), extra_ready)
        )
    else:
        extra_ready_stmt = (
            "time.sleep(%r)\nemit(1, %r)\n" %
            (float(delay_before_extra_ready), extra_ready)
        )
    before_ready_stmt = ""
    if before_ready_marker is not None and ready_release is not None:
        before_ready_stmt = (
            "pathlib.Path(%r).write_text('before-ready')\n"
            "with open(%r, 'r', encoding='utf-8') as stream:\n"
            "    stream.readline()\n" %
            (str(before_ready_marker), str(ready_release))
        )
    return (
        "import os, pathlib, sys, time\n"
        "def emit(fd, frame):\n"
        "    data = (frame + '\\n').encode('ascii')\n"
        "    while data:\n"
        "        written = os.write(fd, data)\n"
        "        data = data[written:]\n"
        "%s"
        "time.sleep(%r)\n"
        "%s\n"
        "%s"
        "os.close(1)\n"
        "release = bytearray()\n"
        "while True:\n"
        "    chunk = os.read(0, 4096)\n"
        "    if not chunk:\n"
        "        break\n"
        "    release.extend(chunk)\n"
        "time.sleep(%r)\n"
        "%s\n"
        "%s"
        "time.sleep(%r)\n"
        "raise SystemExit(%d)\n"
        % (before_ready_stmt, float(delay_before_ready), ready_stmt, extra_ready_stmt,
           float(delay_before_terminal), terminal_stmt,
           suffix_stmt, float(hold), int(status))
    )


def _w1_pre_ready_descendant_gate_program():
    """Fork one hostile descendant before presenting an otherwise exact READY."""
    return (
        "import os, signal, sys, time\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.close(0)\n"
        "    os.close(1)\n"
        "    os.close(3)\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "    time.sleep(300)\n"
        "    raise SystemExit(0)\n"
        "os.write(1, ('READY v1 pid=%d\\n' % os.getpid()).encode('ascii'))\n"
        "os.close(1)\n"
        "while os.read(0, 4096):\n"
        "    pass\n"
        "os.write(3, b'ABORTED v1 reason=release-eof\\n')\n"
        "raise SystemExit(0)\n"
    )


def _w1_kill_group(pgid: int) -> None:
    """Kill a group, refusing to aim at our own. Never raises."""
    try:
        if pgid <= 0 or pgid == os.getpgid(0):
            return
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _w1_proc_receipt(pid: int, raw: str | None = None) -> tuple[int, int, int]:
    """Return the hand-derived stable receipt: pid, process group, start time."""
    if raw is None:
        raw = pathlib.Path("/proc", str(pid), "stat").read_text(encoding="utf-8")
    tail = raw[raw.rindex(") ") + 2:].split()
    assert len(tail) > 19, f"short /proc stat fixture for pid {pid}: {raw!r}"
    return pid, int(tail[2]), int(tail[19])


def _w1_proc_observation(
        pid: int, raw: str | None = None) -> tuple[int, int, int, int, str]:
    """Hand-derive pid, ppid, pgrp, starttime and state from one stat row."""
    if raw is None:
        raw = pathlib.Path("/proc", str(pid), "stat").read_text(encoding="utf-8")
    head, delimiter, tail_text = raw.rpartition(") ")
    assert delimiter and " (" in head, f"malformed /proc stat fixture: {raw!r}"
    observed_pid = int(head.split(" (", 1)[0])
    tail = tail_text.split()
    assert len(tail) > 19, f"short /proc stat fixture for pid {pid}: {raw!r}"
    return observed_pid, int(tail[1]), int(tail[2]), int(tail[19]), tail[0]


def _w1_change_proc_starttime(raw: str) -> str:
    """Model PID reuse by changing only field 22 of one complete stat row."""
    split_at = raw.rindex(") ") + 2
    tail = raw[split_at:].split()
    assert len(tail) > 19, f"short /proc stat fixture: {raw!r}"
    tail[19] = str(int(tail[19]) + 1)
    return raw[:split_at] + " ".join(tail) + "\n"


def _w1_change_proc_field(raw: str, *, ppid=None, pgrp=None, starttime=None,
                          state=None) -> str:
    """Change explicit stat fields without borrowing the production parser."""
    split_at = raw.rindex(") ") + 2
    tail = raw[split_at:].split()
    assert len(tail) > 19, f"short /proc stat fixture: {raw!r}"
    if state is not None:
        tail[0] = str(state)
    if ppid is not None:
        tail[1] = str(ppid)
    if pgrp is not None:
        tail[2] = str(pgrp)
    if starttime is not None:
        tail[19] = str(starttime)
    return raw[:split_at] + " ".join(tail) + "\n"


def _w1_stat_row(pid: int, *, ppid: int, pgrp: int, starttime: int,
                 state: str = "S", comm: str = "worker") -> str:
    """One hand-positioned Linux stat row for parser boundary tests."""
    tail = [
        state, str(ppid), str(pgrp), str(pgrp), "0", "-1", "4194304",
        "1", "2", "3", "4", "5", "6", "7", "8", "20", "0",
        "1", "0", str(starttime), "4096", "10", "0",
    ]
    assert tail[1] == str(ppid) and tail[2] == str(pgrp)
    assert tail[19] == str(starttime)
    return "%d (%s) %s\n" % (pid, comm, " ".join(tail))


def _w1_wait_for_gate(rundir, *, minimum_live=1, timeout=5.0):
    """Return the real launched gate pid after proving its group is live."""
    deadline = time.time() + timeout
    pid = -1
    live: list[str] = []
    while time.time() < deadline:
        pidfile = rundir / "pytest.pid"
        if pidfile.is_file() and pidfile.read_text().strip().isdigit():
            pid = int(pidfile.read_text().strip())
            live = _w1_live_in_group(pid)
            if len(live) >= minimum_live:
                return pid, live
        time.sleep(0.01)
    raise AssertionError(
        "the runner never established the required real child-led group: "
        f"pid={pid}, live={live!r}")


def _w1_wait_for_exit(proc, rundir, *, watchdog=20.0, forbidden=None):
    """Collect the runner from durable state, with wall time only as a guard.

    Receipt checks and owned-process censuses make elapsed time host- and
    scheduler-dependent.  The runner's durable ``exitcode`` is the semantic
    completion signal; a fixed short ``wait`` is not.
    """
    deadline = time.monotonic() + float(watchdog)
    exitcode = rundir / "exitcode"
    while time.monotonic() < deadline:
        if forbidden is not None and forbidden.exists():
            raise AssertionError(
                "terminal bytes without authority entered checked child wait")
        if exitcode.is_file() or proc.poll() is not None:
            proc.communicate(timeout=5)
            return proc.returncode
        time.sleep(0.01)
    raise AssertionError(
        "runner produced neither a durable exit record nor the forbidden "
        "checked-wait transition before the emergency watchdog")


def _w1_wait_for_exit_or_forbidden_checked_wait(
        proc, rundir, checked_wait_probe, *, watchdog=20.0):
    """Reject a forbidden checked wait while awaiting durable completion."""
    assert not checked_wait_probe.exists(), (
        "forbidden checked wait occurred before the oracle started")
    return _w1_wait_for_exit(
        proc, rundir, watchdog=watchdog, forbidden=checked_wait_probe)


def _w1_signal_probe(tmp_path, *, result=0, passthrough=False,
                     capture_receipt=False):
    """Intercept only the production group SIGKILL; leave liveness real."""
    signal_log = tmp_path / "runner-signal-attempts"
    receipt_log = tmp_path / "runner-signal-receipts"
    bash_env = tmp_path / "probe-runner-signal.bash"
    function_body = (
        "kill() {\n"
        "    if [ \"$1\" = \"-9\" ] || [[ \"${1-}\" =~ ^[0-9]+$ ]]; then\n"
        "        printf '%s\\n' \"$*\" >> \"$W1_SIGNAL_LOG\"\n"
        + ("        target=${2:-$1}\n"
           "        target=${target#-}\n"
           "        cat \"/proc/$target/stat\" >> \"$W1_SIGNAL_RECEIPT_LOG\"\n"
           if capture_receipt else "")
        + ("        builtin kill \"$@\"\n"
           "        return $?\n" if passthrough else
           "        return %d\n" % int(result))
        + "    fi\n"
          "    builtin kill \"$@\"\n"
          "}\n"
          "export -f kill\n"
    )
    bash_env.write_text(function_body, encoding="utf-8")
    if capture_receipt:
        assert not receipt_log.exists()
    return bash_env, signal_log


def _w1_process_probe_drift(tmp_path, field: str, *, after_calls: int,
                            mutate_group: bool = True,
                            one_shot: bool = False):
    """Change one production observer field after N exact real observations."""
    assert field in {"ppid", "pgrp", "starttime", "state"}
    counter = tmp_path / "process-probe-count"
    bash_env = tmp_path / "process-probe-drift.bash"
    mutations = {
        "ppid": "ppid=$((ppid + 1))",
        "pgrp": "pgrp=$((pgrp + 1))",
        "starttime": "start=$((start + 1))",
        "state": "state=Z",
    }
    bash_env.write_text(
        "python3() {\n"
        "    if [ \"${1-}\" = '-c' ] && [ \"${3-}\" = 'process' ]; then\n"
        "        local out rc count payload receipt state pid ppid pgrp start\n"
        "        out=$(command python3 \"$@\"); rc=$?\n"
        "        count=$(cat %s 2>/dev/null || echo 0)\n"
        "        echo $((count + 1)) > %s\n"
        "        if [ \"$count\" %s %d ] && [[ \"$out\" == OBSERVED\\|*\\|* ]]; then\n"
        "            payload=${out#OBSERVED|}\n"
        "            receipt=${payload%%|*}\n"
        "            state=${payload##*|}\n"
        "            IFS=: read -r pid ppid pgrp start <<< \"$receipt\"\n"
        "            %s\n"
        "            builtin printf 'OBSERVED|%%s:%%s:%%s:%%s|%%s\\n' "
        "\"$pid\" \"$ppid\" \"$pgrp\" \"$start\" \"$state\"\n"
        "        else\n"
        "            builtin printf '%%s\\n' \"$out\"\n"
        "        fi\n"
        "        return \"$rc\"\n"
        "    elif [ \"${1-}\" = '-c' ] && [ \"${3-}\" = 'group' ]; then\n"
        "        local out rc payload pid ppid pgrp start state\n"
        "        out=$(command python3 \"$@\"); rc=$?\n"
        "        if [[ \"$out\" == PRESENT\\|* ]] "
        "&& [[ \"${out#PRESENT|}\" != *,* ]]; then\n"
        "            payload=${out#PRESENT|}\n"
        "            IFS=: read -r pid ppid pgrp start state <<< \"$payload\"\n"
        "            %s\n"
        "            builtin printf 'PRESENT|%%s:%%s:%%s:%%s:%%s\\n' "
        "\"$pid\" \"$ppid\" \"$pgrp\" \"$start\" \"$state\"\n"
        "        else\n"
        "            builtin printf '%%s\\n' \"$out\"\n"
        "        fi\n"
        "        return \"$rc\"\n"
        "    fi\n"
        "    command python3 \"$@\"\n"
        "}\n"
        "export -f python3\n" % (
            shlex.quote(str(counter)), shlex.quote(str(counter)),
            "-eq" if one_shot else "-ge", int(after_calls), mutations[field],
            mutations[field] if mutate_group else ":"),
        encoding="utf-8",
    )
    return bash_env, counter


def _w1_block_process_probe(tmp_path, *, on_call: int):
    """Block one real process-observer call until the test releases it."""
    counter = tmp_path / "process-probe-count"
    entered = tmp_path / "process-probe-entered"
    release = tmp_path / "process-probe-release"
    os.mkfifo(release)
    bash_env = tmp_path / "process-probe-block.bash"
    bash_env.write_text(
        "python3() {\n"
        "    if [ \"${1-}\" = '-c' ] && [ \"${3-}\" = 'process' ]; then\n"
        "        local count token\n"
        "        count=$(cat %s 2>/dev/null || echo 0)\n"
        "        echo $((count + 1)) > %s\n"
        "        if [ \"$count\" -eq %d ]; then\n"
        "            : > %s\n"
        "            IFS= read -r token < %s\n"
        "        fi\n"
        "    fi\n"
        "    command python3 \"$@\"\n"
        "}\n"
        "export -f python3\n" % (
            shlex.quote(str(counter)), shlex.quote(str(counter)),
            int(on_call) - 1, shlex.quote(str(entered)),
            shlex.quote(str(release))),
        encoding="utf-8",
    )
    return bash_env, entered, release


def _w1_hung_process_probe(tmp_path):
    """Make one observer and its child ignore TERM behind an exact barrier."""
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "hung-process-probe")
    helper_pid = tmp_path / "hung-process-probe.pid"
    child_pid = tmp_path / "hung-process-probe-child.pid"
    bash_env = tmp_path / "hung-process-probe.bash"
    bash_env.write_text(
        "python3() {\n"
        "    if [ \"${1-}\" = '-c' ] && [ \"${3-}\" = 'process' ]; then\n"
        "        trap '' TERM\n"
        "        builtin printf '%%s\\n' \"$BASHPID\" > %s\n"
        "        sleep 300 &\n"
        "        local W1_TEST_CHILD=$!\n"
        "        builtin printf '%%s\\n' \"$W1_TEST_CHILD\" > %s\n"
        "        builtin printf 'observer-entered\\n' > %s\n"
        "        IFS= read -r W1_TEST_RELEASE < %s\n"
        "        builtin wait \"$W1_TEST_CHILD\"\n"
        "        return $?\n"
        "    fi\n"
        "    command python3 \"$@\"\n"
        "}\n"
        "export -f python3\n" % (
            shlex.quote(str(helper_pid)), shlex.quote(str(child_pid)),
            shlex.quote(str(entered)), shlex.quote(str(release))),
        encoding="utf-8",
    )
    return bash_env, entered_fd, release, helper_pid, child_pid


def _w1_block_probe_fifo(tmp_path, mode: str):
    """Hold one real production observer at a FIFO-defined authority boundary."""
    assert mode in {"process", "group"}
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "%s-observer" % mode)
    bash_env = tmp_path / ("block-%s-observer.bash" % mode)
    bash_env.write_text(
        "python3() {\n"
        "    if [ \"${W1_TEST_PROBE_USED:-0}\" -eq 0 ] "
        "&& [ \"${1-}\" = '-c' ] && [ \"${3-}\" = %s ]; then\n"
        "        W1_TEST_PROBE_USED=1\n"
        "        builtin printf '%s-observer-entered\\n' > %s\n"
        "        IFS= read -r W1_TEST_PROBE_RELEASE < %s\n"
        "    fi\n"
        "    command python3 \"$@\"\n"
        "}\n"
        "export -f python3\n" % (
            shlex.quote(mode), mode, shlex.quote(str(entered)),
            shlex.quote(str(release))),
        encoding="utf-8",
    )
    return bash_env, entered_fd, release


def _w1_run_registration_probe(mod, mode: str, *args: object):
    """Drive the production observer without borrowing its parser in tests."""
    return subprocess.run(
        [os.environ.get("PYTHON", "python3"), "-c",
         mod.REGISTRATION_PROBE_PROGRAM, mode, *map(str, args)],
        text=True, capture_output=True, timeout=5, check=False,
    )


def _w1_wait_for_path(path: pathlib.Path, *, timeout=5.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), f"timed out waiting for fixture marker {path}"


def _w1_delay_path_write(tmp_path, target: pathlib.Path):
    """Pause a real Path.write_text after open but before its payload write."""
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "delayed-path-write")
    shim = tmp_path / "delayed-path-write-shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import pathlib\n"
        "_target = %r\n"
        "_entered = %r\n"
        "_release = %r\n"
        "_original_write_text = pathlib.Path.write_text\n"
        "def _delayed_write_text(self, data, encoding=None, errors=None, newline=None):\n"
        "    candidate = str(self)\n"
        "    if candidate == _target or candidate.startswith(_target + '.tmp.'):\n"
        "        with self.open('w', encoding=encoding, errors=errors, newline=newline) as stream:\n"
        "            with open(_entered, 'w', encoding='ascii') as marker:\n"
        "                marker.write('payload-write-opened\\n')\n"
        "            with open(_release, 'r', encoding='ascii') as barrier:\n"
        "                barrier.readline()\n"
        "            return stream.write(data)\n"
        "    return _original_write_text(self, data, encoding=encoding, errors=errors, newline=newline)\n"
        "pathlib.Path.write_text = _delayed_write_text\n" % (
            str(target), str(entered), str(release)),
        encoding="utf-8",
    )
    return shim, entered_fd, release


def _w1_prepend_pythonpath(env, path: pathlib.Path) -> None:
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(path) + (
        os.pathsep + inherited if inherited else "")


def _w1_delay_shell_publish(tmp_path, target: pathlib.Path):
    """Pause old redirection or new rename at the same publication boundary."""
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "delayed-shell-publish")
    bash_env = tmp_path / "delayed-shell-publish.bash"
    bash_env.write_text(
        "_w1_test_hold_publish() {\n"
        "    builtin printf 'publish-boundary-entered\\n' > %s\n"
        "    IFS= read -r W1_TEST_PUBLISH_RELEASED < %s\n"
        "}\n"
        "echo() {\n"
        "    if [ \"/proc/$$/fd/1\" -ef %s ]; then\n"
        "        _w1_test_hold_publish\n"
        "    fi\n"
        "    builtin echo \"$@\"\n"
        "}\n"
        "mv() {\n"
        "    local W1_TEST_LAST_ARG=${!#}\n"
        "    if [ \"$W1_TEST_LAST_ARG\" = %s ]; then\n"
        "        _w1_test_hold_publish\n"
        "    fi\n"
        "    command mv \"$@\"\n"
        "}\n"
        "export -f _w1_test_hold_publish echo mv\n" % (
            shlex.quote(str(entered)), shlex.quote(str(release)),
            shlex.quote(str(target)), shlex.quote(str(target))),
        encoding="utf-8",
    )
    return bash_env, entered_fd, release


def _w1_fail_shell_publish(tmp_path, target: pathlib.Path):
    """Fail only the rename that would publish the selected target."""
    bash_env = tmp_path / "fail-shell-publish.bash"
    bash_env.write_text(
        "mv() {\n"
        "    local W1_TEST_LAST_ARG=${!#}\n"
        "    if [ \"$W1_TEST_LAST_ARG\" = %s ]; then\n"
        "        return 73\n"
        "    fi\n"
        "    command mv \"$@\"\n"
        "}\n"
        "export -f mv\n" % shlex.quote(str(target)),
        encoding="utf-8",
    )
    return bash_env


def _w1_fifo_barrier(tmp_path, name: str):
    """Create a scheduler-independent entered/release handshake."""
    entered = tmp_path / (name + "-entered")
    release = tmp_path / (name + "-release")
    os.mkfifo(entered)
    os.mkfifo(release)
    entered_fd = os.open(entered, os.O_RDONLY | os.O_NONBLOCK)
    return entered, release, entered_fd


def _w1_release_fifo(path, payload: str = "go\n", *,
                     timeout: float = 10.0) -> None:
    """Release a runner blocked on `read < path`, WITHOUT risking a hang.

    A plain open-for-write on a fifo blocks until a reader arrives, so a runner
    that never reached the injected barrier would turn a failing assertion into
    a hung test. O_NONBLOCK turns "no reader yet" into ENXIO, which this retries
    to a deadline and then REPORTS -- an unreached barrier is a fixture failure
    with a name, not a timeout with none.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_NONBLOCK)
            break
        except OSError as exc:
            if exc.errno != errno.ENXIO:
                raise
            assert time.monotonic() < deadline, (
                "the runner never reached the injected barrier at %s, so the "
                "precondition this control forces was never applied" % path)
            time.sleep(0.01)
    try:
        os.write(fd, payload.encode("ascii"))
    finally:
        os.close(fd)


def _w1_await_fifo(fd: int, *, timeout: float = 5.0) -> str:
    readable, _, _ = select.select([fd], [], [], timeout)
    assert readable, "timed out waiting for deterministic fixture barrier"
    payload = os.read(fd, 4096).decode("utf-8")
    assert payload, "fixture barrier reached EOF without an entered record"
    return payload


def _w1_gate_wait_barrier(tmp_path):
    """Block the first pre-release checked gate wait in the real runner."""
    entered, release, entered_fd = _w1_fifo_barrier(tmp_path, "gate-wait")
    bash_env = tmp_path / "gate-wait-barrier.bash"
    bash_env.write_text(
        "wait() {\n"
        "    local W1_TEST_WAIT_TARGET=${3-}\n"
        "    [ \"${1-}\" = '-n' ] && W1_TEST_WAIT_TARGET=${4-}\n"
        "    if [ \"${W1_TEST_GATE_WAIT_USED:-0}\" -eq 0 ] "
        "&& [ \"${W1_RELEASE_WRITE_COUNT:-0}\" -eq 0 ] "
        "&& [ \"$W1_TEST_WAIT_TARGET\" = \"${PYTEST_GATE_PID:-missing}\" ]; then\n"
        "        W1_TEST_GATE_WAIT_USED=1\n"
        "        builtin printf 'gate-wait-entered\\n' > %s\n"
        "        IFS= read -r W1_TEST_GATE_WAIT_RELEASE < %s\n"
        "    fi\n"
        "    builtin wait \"$@\"\n"
        "}\n"
        "export -f wait\n" % (
            shlex.quote(str(entered)), shlex.quote(str(release))),
        encoding="utf-8",
    )
    return bash_env, entered_fd, release


def _w1_checked_child_wait_barrier(tmp_path, role: str):
    """Hold the real relay or workload checked wait at its saved PID."""
    assert role in {"terminal-relay", "workload"}
    entered, release, entered_fd = _w1_fifo_barrier(tmp_path, role + "-wait")
    bash_env = tmp_path / (role + "-wait-barrier.bash")
    if role == "terminal-relay":
        predicate = (
            "[ \"${1-}\" = '-n' ] && "
            "[ \"${4-}\" = \"${W1_TERMINAL_RELAY_PID:-missing}\" ]")
    else:
        predicate = (
            "[ \"${1-}\" = '-p' ] && "
            "[ \"${3-}\" = \"${PYTEST_GATE_PID:-missing}\" ] && "
            "[ \"${W1_TERMINAL_CLASS:-}\" = EXEC_OK ]")
    bash_env.write_text(
        "wait() {\n"
        "    if [ \"${W1_TEST_WAIT_USED:-0}\" -eq 0 ] && %s; then\n"
        "        W1_TEST_WAIT_USED=1\n"
        "        builtin printf '%s-wait-entered\\n' > %s\n"
        "        IFS= read -r W1_TEST_WAIT_RELEASE < %s\n"
        "    fi\n"
        "    builtin wait \"$@\"\n"
        "}\n"
        "export -f wait\n" % (
            predicate, role, shlex.quote(str(entered)),
            shlex.quote(str(release))),
        encoding="utf-8",
    )
    return bash_env, entered_fd, release


def _w1_terminal_reader_barrier(mod, tmp_path):
    """Prefix the production byte reader with an exact terminal-only barrier."""
    entered, release, entered_fd = _w1_fifo_barrier(tmp_path, "terminal-reader")
    used = tmp_path / "terminal-reader-used"
    prefix = (
        "import os, pathlib, sys\n"
        "if (len(sys.argv) > 3 and sys.argv[3] == 'terminal' "
        "and not pathlib.Path(%r).exists()):\n"
        "    pathlib.Path(%r).write_text('used', encoding='utf-8')\n"
        "    with open(%r, 'w', encoding='utf-8') as stream:\n"
        "        stream.write('terminal-reader-entered\\n')\n"
        "    with open(%r, 'r', encoding='utf-8') as stream:\n"
        "        stream.readline()\n" % (
            str(used), str(used), str(entered), str(release))
    )
    return prefix + mod.REGISTRATION_CHANNEL_READER_PROGRAM, entered_fd, release


def _w1_owner_records(rundir: pathlib.Path) -> list[dict[str, str]]:
    """Parse the runner's append-only owner ledger without inferring outcomes."""
    records = []
    for line in (rundir / "registration-owners.log").read_text(
            encoding="utf-8").splitlines():
        fields = line.split()
        assert fields and fields[0] == "OWNER", line
        record = {}
        for field in fields[1:]:
            key, separator, value = field.partition("=")
            assert separator and key and value, line
            record[key] = value
        records.append(record)
    return records


def test_registration_probe_binds_complete_identity_and_last_parenthesis(tmp_path):
    """The production parser owns pid/ppid/pgrp/starttime, not just a number."""
    mod = _load()
    path = tmp_path / "stat"
    cases = [
        ("plain", 411, 73, 411, 9001, "S"),
        ("worker ) name with spaces", 412, 74, 412, 9002, "R"),
        ("worker (nested) ) name", 413, 75, 413, 9003, "Z"),
    ]
    for comm, pid, ppid, pgrp, starttime, state in cases:
        path.write_text(_w1_stat_row(
            pid, ppid=ppid, pgrp=pgrp, starttime=starttime,
            state=state, comm=comm), encoding="utf-8")
        result = _w1_run_registration_probe(mod, "process", pid, path)
        assert result.returncode == 0, (
            f"production observer rejected {comm!r}: {result.stderr!r}")
        assert result.stdout.strip() == (
            "OBSERVED|%d:%d:%d:%d|%s" %
            (pid, ppid, pgrp, starttime, state)), (
            "observer did not split at the final `) ` or mapped a Linux stat "
            f"field incorrectly: {result.stdout!r}")


def test_registration_probe_exposes_pgrp_and_starttime_changes_separately(tmp_path):
    """Both recyclable group membership and process birth are load-bearing."""
    mod = _load()
    pid = 514
    path = tmp_path / "stat"
    original = _w1_stat_row(pid, ppid=88, pgrp=pid, starttime=12000)
    schedules = [
        (original, "OBSERVED|514:88:514:12000|S"),
        (_w1_change_proc_field(original, pgrp=515),
         "OBSERVED|514:88:515:12000|S"),
        (_w1_change_proc_field(original, starttime=12001),
         "OBSERVED|514:88:514:12001|S"),
    ]
    for raw, expected in schedules:
        path.write_text(raw, encoding="utf-8")
        result = _w1_run_registration_probe(mod, "process", pid, path)
        assert (result.returncode, result.stdout.strip()) == (0, expected)


def test_registration_probe_distinguishes_absent_malformed_and_unknown(tmp_path):
    mod = _load()
    missing = tmp_path / "missing"
    malformed = tmp_path / "malformed"
    unreadable_shape = tmp_path / "directory"
    malformed.write_text("123 (short) S 1\n", encoding="utf-8")
    unreadable_shape.mkdir()
    cases = [
        (missing, "ABSENT"),
        (malformed, "MALFORMED"),
        (unreadable_shape, "UNKNOWN"),
    ]
    for path, expected in cases:
        result = _w1_run_registration_probe(mod, "process", 123, path)
        assert (result.returncode, result.stdout.strip()) == (0, expected)


def test_group_probe_never_promotes_an_incomplete_census_to_present(tmp_path):
    """One matching member cannot prove sole ownership if any row is unknown."""
    mod = _load()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    member = proc_root / "601"
    member.mkdir()
    (member / "stat").write_text(
        _w1_stat_row(601, ppid=77, pgrp=601, starttime=9001),
        encoding="utf-8",
    )
    incomplete = proc_root / "602"
    incomplete.mkdir()
    (incomplete / "stat").mkdir()

    result = _w1_run_registration_probe(mod, "group", 601, proc_root)

    assert result.returncode == 0
    assert result.stdout.strip() == "UNKNOWN", (
        "a matching member was promoted to PRESENT even though another numeric "
        "census row could not be classified"
    )


def test_registration_gate_precedes_registration_and_workload_release():
    """Source-order control for the race-closing direct-child gate."""
    mod = _load()
    runner = mod.RUNNER
    relay = runner.index("coproc W1_TERMINAL_RELAY {")
    gate = runner.index("coproc W1_REGISTRATION_GATE {")
    receipt = runner.index(
        'PYTEST_ORIGINAL_RECEIPT="$W1_INITIAL_RECEIPT"', gate)
    register = runner.index('"$HOME/BulkDownloader/toolchain/bin/bd-jobs" register')
    release = runner.index("W1_GATE_RELEASE_TOKEN")
    assert runner.index("trap 'registration_on_exit $?' EXIT") < relay < gate
    assert runner.index("trap 'registration_on_cancel 130' INT") < relay
    assert relay < gate < receipt < register < release
    assert 'registration_cancel_checkpoint "after-relay-acquire"' in runner
    assert 'registration_cancel_checkpoint "after-gate-acquire"' in runner
    assert "PYTEST_PID=$PYTEST_GATE_PID" in runner
    assert "PYTEST_ORIGINAL_RECEIPT" in runner
    assert "READY v1" in mod.REGISTRATION_GATE_PROGRAM
    assert "ABORTED v1" in mod.REGISTRATION_GATE_PROGRAM
    assert "EXEC-FAIL v1" in mod.REGISTRATION_GATE_PROGRAM
    shim = mod.registration_workload_shim("/tmp/run", "echo exact-command")
    assert "EXEC-OK v1" in shim and shim.rstrip().endswith("exec echo exact-command")


def test_registration_state_machine_has_one_total_deadline_and_failure_funnel():
    """Owner budgets and registered cleanup have one structural authority."""
    runner = _load().RUNNER
    assert runner.count("W1_LIFECYCLE_DEADLINE_US=$((") == 1
    assert "W1_FORWARD_DEADLINE_US=$((W1_LIFECYCLE_DEADLINE_US" in runner
    assert "registration_cap_deadline" in runner
    assert "registration_fail_registered()" in runner
    assert runner.count("registration_finish 93") == 1, (
        "a registered abnormal edge bypasses the single reconciliation funnel")
    assert "registration_checked_terminal_relay_wait()" in runner
    assert "registration_checked_child_wait() {{" in runner
    relay_wait = runner.split(
        "registration_checked_terminal_relay_wait() {{", 1)[1].split(
            "\n}}", 1)[0]
    assert "registration_checked_child_wait terminal-relay \\" in relay_wait
    assert '"$W1_TERMINAL_RELAY_PID" 0' in relay_wait
    assert ('wait -n -p W1_RACE_WAITED_PID "$W1_CHILD_PID" '
            '"$W1_TIMER_PID"') in runner
    assert "W1_FRAME_CLASS" in runner and '"$W1_TERMINAL_CLASS"' in runner


def test_zero_owner_kill_grace_is_rejected_before_timeout_launch(tmp_path):
    """A TERM-only timeout is not a bounded owner for resistant children."""
    mod = _load()
    timeout_log = tmp_path / "timeout-argv"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    timeout_wrapper = fake_bin / "timeout"
    timeout_wrapper.write_text(
        "#!/bin/bash\n"
        "builtin printf '%s\\n' \"$*\" >> \"$W1_TIMEOUT_ARGV_LOG\"\n"
        "exec /usr/bin/timeout \"$@\"\n",
        encoding="utf-8",
    )
    timeout_wrapper.chmod(0o755)
    script, _rundir = _w1_build_runner(
        mod, tmp_path, "#!/bin/bash\nexit 0\n",
        reap_seconds=1, owner_kill_grace_us=0,
    )
    assert "W1_OWNER_KILL_GRACE_US=0" in script.read_text(encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["W1_TIMEOUT_ARGV_LOG"] = str(timeout_log)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7,
    )

    assert not timeout_log.exists(), (
        "ZERO-KILL-GRACE-REACHED-TIMEOUT-OWNER: "
        + timeout_log.read_text(encoding="utf-8", errors="replace")
        if timeout_log.exists() else
        "ZERO-KILL-GRACE-REACHED-TIMEOUT-OWNER"
    )
    assert result.returncode == int(W1_RETAINED_FAILURE_CODE)


def test_direct_owner_stop_has_positive_monotonic_grace_before_conditional_kill():
    """GNU timeout grace cannot stand in for the direct teardown path."""
    runner = _load().RUNNER
    start = runner.index("registration_owner_stop() {{")
    body = runner[start:runner.index("\n}}", start)]
    assert "builtin kill -TERM" in body and "builtin kill -KILL" in body
    assert "registration_owner_term_grace" in body, (
        "DIRECT-OWNER-KILL-HAS-NO-POSITIVE-GRACE")
    term = body.index("builtin kill -TERM")
    grace = body.index("registration_owner_term_grace")
    kill = body.index("builtin kill -KILL")

    assert term < grace < kill, (
        "DIRECT-OWNER-KILL-HAS-NO-POSITIVE-GRACE")
    assert "registration_now_us" in body
    assert "W1_OWNER_TERM_AT_US" in body and "W1_OWNER_KILL_AT_US" in body
    assert "registration_process_receipt_matches" in body
    assert "PROCESS-RESTORATION-UNKNOWN" in body
    rollback = body.index(
        'W1_OWNER_GRACE_US="UNAVAILABLE-$W1_CLOCK_STATE"')
    assert grace < rollback < kill, (
        "CLOCK-FAILURE-REFUSED-IDENTITY-BOUND-KILL-CONTINUATION")
    grace_body = runner.split("registration_owner_term_grace() {{", 1)[1].split(
        "\n}}", 1)[0]
    assert "sleep " not in grace_body, (
        "the direct grace spawned an unowned sleeper")
    assert "W1_GRACE_TARGET_US" in grace_body


def test_timeout_owner_launch_keeps_a_positive_kill_after_escalation():
    """The wrapper must hard-stop a TERM-resistant command after its grace."""
    runner = _load().RUNNER
    start = runner.index("registration_owner_spawn() {{")
    body = runner[start:runner.index("\n}}", start)]
    assert 'timeout --kill-after="$W1_SPAWN_KILL_AFTER" \\' in body, (
        "TIMEOUT-OWNER-LOST-KILL-AFTER-ESCALATION")
    assert "W1_SPAWN_KILL_AFTER=\"$W1_OWNER_KILL_AFTER\"" in body


def test_owner_group_promotion_requires_full_launch_identity():
    """READY bytes cannot weaken PPID/PGID/SID/starttime ownership checks."""
    runner = _load().RUNNER
    start = runner.index("registration_promote_spawn_group_receipt() {{")
    body = runner[start:runner.index("\n}}", start)]
    same_start = runner.index("registration_receipt_same_process() {{")
    same_body = runner[same_start:runner.index("\n}}", same_start)]
    required = (
        'registration_receipt_same_process',
        'W1_PROMOTE_FIELDS[1]}}" = "$W1_SPAWN_PARENT_PID"',
        'W1_PROMOTE_FIELDS[2]}}" = "$W1_PROMOTE_PID"',
        'W1_PROMOTE_FIELDS[3]}}" = "$W1_PROMOTE_PID"',
    )
    assert all(token in body for token in required), (
        "OWNER-PROMOTION-TRUSTED-INCOMPLETE-RECEIPT")
    assert 'W1_SAME_OLD[4]}}" = "${{W1_SAME_NEW[4]}}"' in same_body, (
        "OWNER-PROMOTION-TRUSTED-INCOMPLETE-RECEIPT")


def test_completed_owner_ready_receipt_remains_census_authority(tmp_path):
    """A fast helper may exit after READY but before its parent promotes it.

    The authenticated READY receipt must remain sufficient to census the
    helper's owned process group.  Treating this ordinary completion race as
    UNKNOWN makes the same exact runner alternate between 91 and 92.
    """
    mod = _load()
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "terminal-owner-ready")
    owner_pid_path = tmp_path / "terminal-owner.pid"
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
        after_terminal_owner_ready_barrier=(
            entered, release, owner_pid_path),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    try:
        assert _w1_await_fifo(entered_fd) == "owner-ready-read\n"
        owner_pid = int(owner_pid_path.read_text(encoding="ascii"))
        deadline = time.monotonic() + 5
        owner_state = "UNKNOWN"
        while time.monotonic() < deadline:
            try:
                owner_state = _w1_proc_observation(owner_pid)[-1]
            except (FileNotFoundError, ProcessLookupError):
                owner_state = "ABSENT"
            if owner_state in {"Z", "X", "ABSENT"}:
                break
            time.sleep(0.01)
        assert owner_state in {"Z", "X", "ABSENT"}, (
            "fixture did not force the post-READY completion race", owner_state)
        with release.open("w", encoding="ascii") as stream:
            stream.write("release\n")
        rc = proc.wait(timeout=8)

        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-reader"]
        assert len(records) == 1, records
        assert records[0]["group_ready"] == "1", records
        assert records[0]["descendants"] == "ABSENT", records
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "frame=ABORTED v1 reason=release-eof" in evidence
        assert rc == int(W1_RUNNER_FAILURE_CODE)
        assert not marker.exists()
    finally:
        os.close(entered_fd)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_completed_owner_forged_ready_receipt_never_grants_census_authority(
        tmp_path):
    """The terminal-state fallback remains bound to the launch start time."""
    mod = _load()
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "forged-terminal-owner-ready")
    owner_pid_path = tmp_path / "forged-terminal-owner.pid"
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
        after_terminal_owner_ready_barrier=(
            entered, release, owner_pid_path),
        mutate_terminal_owner_ready=True,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    try:
        assert _w1_await_fifo(entered_fd) == "owner-ready-read\n"
        owner_pid = int(owner_pid_path.read_text(encoding="ascii"))
        deadline = time.monotonic() + 5
        owner_state = "UNKNOWN"
        while time.monotonic() < deadline:
            try:
                owner_state = _w1_proc_observation(owner_pid)[-1]
            except (FileNotFoundError, ProcessLookupError):
                owner_state = "ABSENT"
            if owner_state in {"Z", "X", "ABSENT"}:
                break
            time.sleep(0.01)
        assert owner_state in {"Z", "X", "ABSENT"}, owner_state
        with release.open("w", encoding="ascii") as stream:
            stream.write("release\n")
        rc = proc.wait(timeout=8)

        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-reader"]
        assert len(records) == 1, records
        assert records[0]["group_ready"] == "0", records
        assert records[0]["descendants"] == "UNKNOWN", records
        assert rc == int(W1_RETAINED_FAILURE_CODE)
        assert not marker.exists()
    finally:
        os.close(entered_fd)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_completed_owner_ready_receipt_censuses_live_descendant(tmp_path):
    """Fallback authority cannot launder a surviving group into ABSENT."""
    mod = _load()
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "descendant-terminal-owner-ready")
    owner_pid_path = tmp_path / "descendant-terminal-owner.pid"
    descendant_pid_path = tmp_path / "terminal-owner-descendant.pid"
    write_shim, payload_entered_fd, payload_release = _w1_delay_path_write(
        tmp_path, descendant_pid_path)
    marker = tmp_path / "workload-started"
    print_anchor = 'print("S:" + state)\n'
    assert mod.REGISTRATION_CHANNEL_READER_PROGRAM.count(print_anchor) == 1
    descendant_program = (
        "if mode == 'terminal':\n"
        "    child = os.fork()\n"
        "    if child == 0:\n"
        "        import pathlib, signal\n"
        "        descendant_pid_path = pathlib.Path(%r)\n"
        "        descendant_pid_temp = descendant_pid_path.with_name(\n"
        "            descendant_pid_path.name + '.tmp.%%d' %% os.getpid())\n"
        "        descendant_pid_temp.write_text(str(os.getpid()), encoding='ascii')\n"
        "        os.replace(descendant_pid_temp, descendant_pid_path)\n"
        "        for inherited_fd in (0, 1, 2):\n"
        "            try:\n"
        "                os.close(inherited_fd)\n"
        "            except OSError:\n"
        "                pass\n"
        "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "        while True:\n"
        "            signal.pause()\n"
    ) % str(descendant_pid_path)
    reader_program = mod.REGISTRATION_CHANNEL_READER_PROGRAM.replace(
        print_anchor, descendant_program + print_anchor, 1)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, channel_reader_program=reader_program,
        after_terminal_owner_ready_barrier=(
            entered, release, owner_pid_path),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    _w1_prepend_pythonpath(env, write_shim)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    owner_pid = descendant_pid = -1
    try:
        assert _w1_await_fifo(entered_fd) == "owner-ready-read\n"
        owner_pid = int(owner_pid_path.read_text(encoding="ascii"))
        assert _w1_await_fifo(payload_entered_fd) == "payload-write-opened\n"
        try:
            assert not descendant_pid_path.exists(), (
                "descendant pid became visible before its payload was complete")
        finally:
            _w1_release_fifo(payload_release)
        _w1_wait_for_path(descendant_pid_path)
        descendant_pid = int(descendant_pid_path.read_text(encoding="ascii"))
        assert _w1_pid_is_live(descendant_pid)
        assert os.getpgid(descendant_pid) == owner_pid
        deadline = time.monotonic() + 5
        owner_state = "UNKNOWN"
        while time.monotonic() < deadline:
            try:
                owner_state = _w1_proc_observation(owner_pid)[-1]
            except (FileNotFoundError, ProcessLookupError):
                owner_state = "ABSENT"
            if owner_state in {"Z", "X", "ABSENT"}:
                break
            time.sleep(0.01)
        assert owner_state in {"Z", "X", "ABSENT"}, (
            "fixture did not force completed-owner descendant census",
            owner_state)
        with release.open("w", encoding="ascii") as stream:
            stream.write("release\n")
        rc = proc.wait(timeout=8)

        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-reader"]
        assert len(records) == 1, records
        assert records[0]["group_ready"] == "1", records
        assert records[0]["descendants"] == "PRESENT", records
        assert rc == int(W1_RETAINED_FAILURE_CODE)
        assert _w1_pid_is_live(descendant_pid)
        assert not marker.exists()
    finally:
        os.close(entered_fd)
        os.close(payload_entered_fd)
        if owner_pid > 0:
            _w1_kill_group(owner_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_one_second_lifecycle_cap_remains_truthfully_unknown(tmp_path):
    """The short-cap control cannot borrow later-path definite evidence."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=1,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7)

    assert not registrar.exists() and not marker.exists(), (
        "N58B-SHORT-CAP-CROSSED-LATE-AUTHORITY")
    assert not (rundir / "jobid").exists()
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "initial-observation-unavailable" in evidence
    records = [record for record in _w1_owner_records(rundir)
               if record["role"] == "process-observer"]
    assert len(records) == 1 and records[0]["wait_ok"] == "0", records
    assert records[0]["descendants"] == "UNKNOWN"
    assert records[0]["stop"] == "SPAWN-FAILED"
    assert result.returncode == int(W1_RETAINED_FAILURE_CODE)


def test_every_authority_helper_is_named_and_checked_waited(tmp_path):
    """The ordinary path leaves no ambient reader/observer/publisher owner."""
    mod = _load()
    owner_program = mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM
    assert "live_fds = []" in owner_program
    assert "OWNER-READY v2 receipt=%s fds=%s" in owner_program
    script, rundir = _w1_build_runner(
        mod, tmp_path, "#!/bin/bash\nexit %d\n" % W1_WORKLOAD_CODE)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)

    assert result.returncode == 0, (
        "OWNER-BOOTSTRAP-RETAINED-UNDECLARED-FD: "
        f"runner returned {result.returncode}; stderr={result.stderr!r}")
    records = _w1_owner_records(rundir)
    roles = {record["role"] for record in records}
    assert {
        "ready-reader", "process-observer", "group-observer", "registrar",
        "terminal-reader", "terminal-relay", "workload",
        "gate-fd-observer", "relay-fd-observer",
    } <= roles
    for record in records:
        assert record["owner_pid"].isdigit(), record
        assert record["waited_pid"] == record["owner_pid"], record
        assert record["wait_ok"] == "1", record
        assert record["descendants"] in {"ABSENT", "NOT-APPLICABLE"}, record
        if (record["role"] in {
                "ready-reader", "process-observer", "group-observer",
                "registrar", "terminal-reader", "reconciliation",
                "owner-stop-grace",
        } or record["role"].endswith("-census")):
            assert record.get("fds") == "0,1,2", (
                "OWNER-BOOTSTRAP-RETAINED-UNDECLARED-FD", record)
            receipt = record.get("receipt", "").split(":")
            assert len(receipt) == 5 and all(value.isdigit() for value in receipt), (
                "owner ledger omitted its exact pid/ppid/pgid/sid/start receipt",
                record,
            )

    relay = [record for record in records
             if record["role"] == "terminal-relay"]
    assert len(relay) == 1 and relay[0].get("group_ready") == "1", relay

    fd_rows = {}
    for line in (rundir / "registration-authority-fds.log").read_text(
            encoding="ascii").splitlines():
        prefix, payload = line.split(" FDS|", 1)
        role = prefix.split("role=", 1)[1]
        _pid, encoded = payload.split("|", 1)
        fd_rows[role] = {
            int(fd): os.fsdecode(bytes.fromhex(target))
            for fd, target in (field.split(":", 1)
                               for field in encoded.split(","))
        }
    assert set(fd_rows) == {"gate", "relay"}, fd_rows
    assert set(fd_rows["gate"]) == {0, 2, 3}
    assert set(fd_rows["relay"]) == {0, 1, 2}
    assert fd_rows["gate"][3] == fd_rows["relay"][0]
    assert fd_rows["gate"][0] not in fd_rows["relay"].values()
    assert fd_rows["relay"][1] not in fd_rows["gate"].values()


def test_descendant_census_is_itself_a_named_checked_owner(tmp_path):
    """A post-wait /proc scan cannot be an unbudgeted ambient shell loop."""
    mod = _load()
    script, rundir = _w1_build_runner(
        mod, tmp_path, "#!/bin/bash\nexit %d\n" % W1_WORKLOAD_CODE)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)

    assert result.returncode == 0
    records = _w1_owner_records(rundir)
    census_roles = {record["role"] for record in records
                    if record["role"].endswith("-census")}
    assert {
        "ready-reader-census", "process-observer-census",
        "group-observer-census", "registrar-census",
        "terminal-reader-census", "terminal-relay-census",
    } <= census_roles
    for record in records:
        if record["role"].endswith("-census"):
            assert record["wait_ok"] == "1", record
            assert record["waited_pid"] == record["owner_pid"], record
            assert record["descendants"] == "NOT-APPLICABLE", record
    assert "registration_builtin_group_census" not in mod.RUNNER
    assert "No such file or directory" not in result.stderr


def test_failed_registration_never_releases_the_workload(tmp_path):
    """A rejected registry transaction must never start the real command."""
    mod = _load()
    workload_marker = tmp_path / "workload-started"
    registrar_marker = tmp_path / "registrar-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\n"
        "touch %s\n"
        "sleep 0.2\n" % shlex.quote(str(workload_marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=1.5))
    env["W1_REGISTRAR_MARKER"] = str(registrar_marker)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    pgid = -1
    try:
        pgid, live = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(registrar_marker)
        observation = _w1_proc_observation(pgid)
        assert observation[:3] == (pgid, proc.pid, pgid), (
            "the registration receipt was not a direct-child group leader: "
            f"{observation!r}")
        assert len(live) == 1, (
            "the gate started the workload or descendants before registration: "
            f"{live!r}")
        # Let the real transaction settle all of its named owners before the
        # mutation assertion can unwind pytest.  The early-work mutant is
        # still observed by its durable marker, but cannot strand a registrar
        # by killing the runner at the assertion boundary.
        rc = proc.wait(timeout=8)
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "release_writes=0" in err, (
            "REGISTRAR-REFUSAL-WROTE-GO", err)
        assert not workload_marker.exists(), (
            "the workload ran before its registry transaction committed")
        assert not workload_marker.exists(), (
            "a registration failure released a workload the registry cannot see")
        assert (rundir / "exitcode").read_text().strip() == W1_RUNNER_FAILURE_CODE
        assert not _w1_live_in_group(pgid)
        assert rc == int(W1_RUNNER_FAILURE_CODE)
    finally:
        _w1_kill_group(pgid)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))
            proc.kill()
            proc.wait(timeout=5)


def test_successful_registration_releases_exact_command_and_status(tmp_path):
    """Commit releases the same pid/pgid/starttime and preserves workload rc."""
    mod = _load()
    registrar_marker = tmp_path / "registrar-started"
    workload_receipt = tmp_path / "workload-receipt"
    workload_marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\n"
        "cat /proc/$$/stat > %s\n"
        "touch %s\n"
        "exit %d\n" % (
            shlex.quote(str(workload_receipt)),
            shlex.quote(str(workload_marker)), W1_WORKLOAD_CODE),
        owned_group_census_override="ABSENT",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n", sleep=1.0))
    env["W1_REGISTRAR_MARKER"] = str(registrar_marker)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    pgid = -1
    try:
        pgid, live = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(registrar_marker)
        before = _w1_proc_observation(pgid)
        assert before[:3] == (pgid, proc.pid, pgid)
        assert len(live) == 1 and not workload_marker.exists(), (
            "the command ran while registration was still undecided")
        rc = proc.wait(timeout=8)
        assert workload_marker.exists() and workload_receipt.is_file()
        after = _w1_proc_observation(
            pgid, workload_receipt.read_text(encoding="utf-8"))
        assert after[:4] == before[:4], (
            "release did not exec the registered direct child in place: "
            f"before={before!r}, after={after!r}")
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        assert (rundir / "exitcode").read_text().strip() == str(W1_WORKLOAD_CODE)
        assert rc == 0
    finally:
        _w1_kill_group(pgid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_registration_release_failure_is_bounded_and_classified(tmp_path):
    """A committed row with an unreleased gate is neither success nor a hang."""
    mod = _load()
    workload_marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\n"
        "touch %s\n"
        "sleep 300\n" % shlex.quote(str(workload_marker)),
        reap_seconds=3,
    )
    bash_env = tmp_path / "fail-gate-release.bash"
    bash_env.write_text(
        "printf() {\n"
        "    if [ \"$1\" = '%s\\n' ] && [ \"${2-}\" = 'GO v1' ]; then\n"
        "        return 1\n"
        "    fi\n"
        "    builtin printf \"$@\"\n"
        "}\n"
        "export -f printf\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["BASH_ENV"] = str(bash_env)
    started = time.monotonic()
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    pgid = -1
    try:
        pgid, _ = _w1_wait_for_gate(rundir)
        rc = proc.wait(timeout=8)
        assert time.monotonic() - started < 8
        assert rc == int(W1_RELEASE_FAILURE_CODE)
        assert not workload_marker.exists(), (
            "a failed release nevertheless started the registered command")
        assert (rundir / "exitcode").read_text().strip() == W1_RELEASE_FAILURE_CODE
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "REGISTER-HANDOFF write_status=1 writes=1" in err
        assert "frame=ABORTED v1 reason=release-eof" in err
        assert "receipt=" in err
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        assert not _w1_live_in_group(pgid)
    finally:
        _w1_kill_group(pgid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_registration_failure_never_signals_after_original_child_disappears(
        tmp_path):
    """A READY gate that dies during registration is collected, never killed."""
    mod = _load()
    workload_marker = tmp_path / "workload-started"
    registrar_marker = tmp_path / "registrar-started"
    disappeared, release_disappearance, disappeared_fd = _w1_fifo_barrier(
        tmp_path, "gate-disappearance")
    gate_program = (
        "import os\n"
        "def emit(fd, frame):\n"
        "    os.write(fd, (frame + '\\n').encode('ascii'))\n"
        "emit(1, 'READY v1 pid=%%d' %% os.getpid())\n"
        "os.close(1)\n"
        "with open(%r, 'w', encoding='ascii') as stream:\n"
        "    stream.write('gate-ready-to-disappear\\n')\n"
        "with open(%r, 'r', encoding='ascii') as stream:\n"
        "    if not stream.readline():\n"
        "        raise SystemExit(97)\n"
        "raise SystemExit(98)\n" % (
            str(disappeared), str(release_disappearance))
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(workload_marker)),
        reap_seconds=3, gate_program=gate_program,
    )
    bash_env, signal_log = _w1_signal_probe(tmp_path)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=1.0))
    env["W1_REGISTRAR_MARKER"] = str(registrar_marker)
    env["BASH_ENV"] = str(bash_env)
    env["W1_SIGNAL_LOG"] = str(signal_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(disappeared_fd) == (
            "gate-ready-to-disappear\n")
        _w1_wait_for_path(registrar_marker)
        with release_disappearance.open("w", encoding="ascii") as stream:
            stream.write("disappear\n")
        rc = proc.wait(timeout=6)
        assert rc == int(W1_RETAINED_FAILURE_CODE)
        assert not workload_marker.exists()
        assert not signal_log.exists(), (
            "registration cleanup restored a negative numeric signal sink")
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "context=registrar-refused" in err
        assert "frame_rc=1" in err and "wait=98" in err
        assert "receipt=" in err and "release_writes=0" in err
        assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE
    finally:
        os.close(disappeared_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_vanished_gate_leader_with_live_descendant_is_retained_unknown(tmp_path):
    """Collecting the direct child is not proof that its original group is gone."""
    mod = _load()
    marker = tmp_path / "workload-started"
    child_marker = tmp_path / "gate-child"
    write_shim, payload_entered_fd, payload_release = _w1_delay_path_write(
        tmp_path, child_marker)
    gate_post_setsid = tmp_path / "gate-post-setsid"
    os.mkfifo(gate_post_setsid)
    gate_program = (
        "import os, pathlib, time\n"
        "def emit(fd, frame):\n"
        "    os.write(fd, (frame + '\\n').encode('ascii'))\n"
        "with open(%r, 'w', encoding='ascii') as stream:\n"
        "    stream.write('gate-post-setsid\\n')\n"
        "emit(1, 'READY v1 pid=%%d' %% os.getpid())\n"
        "os.close(1)\n"
        "while os.read(0, 4096):\n"
        "    pass\n"
        "ready_read, ready_write = os.pipe()\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.close(ready_read)\n"
        "    os.close(3)\n"
        "    child_marker = pathlib.Path(%r)\n"
        "    child_marker_temp = child_marker.with_name(\n"
        "        child_marker.name + '.tmp.%%d' %% os.getpid())\n"
        "    child_marker_temp.write_text(str(os.getpid()))\n"
        "    os.replace(child_marker_temp, child_marker)\n"
        "    os.write(ready_write, b'1')\n"
        "    os.close(ready_write)\n"
        "    time.sleep(30)\n"
        "    raise SystemExit(0)\n"
        "os.close(ready_write)\n"
        "if os.read(ready_read, 1) != b'1':\n"
        "    raise SystemExit(97)\n"
        "os.close(ready_read)\n"
        "emit(3, 'ABORTED v1 reason=release-eof')\n"
        "raise SystemExit(0)\n" % (str(gate_post_setsid), str(child_marker))
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
        owned_group_census_override="AUXILIARY-ABSENT",
        before_group_receipt_recheck_fifo=gate_post_setsid,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    _w1_prepend_pythonpath(env, write_shim)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(payload_entered_fd) == "payload-write-opened\n"
        try:
            assert not child_marker.exists(), (
                "gate-child pid became visible before its payload was complete")
        finally:
            _w1_release_fifo(payload_release)
        assert proc.wait(timeout=6) == int(W1_RETAINED_FAILURE_CODE)
        _w1_wait_for_path(child_marker)
        child_pid = int(child_marker.read_text().strip())
        assert child_pid in {int(pid) for pid in _w1_live_in_group(gate_pid)}
        assert not marker.exists()
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "group=PRESENT" in err and "wait=0" in err
        assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE
    finally:
        os.close(payload_entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_unreadable_group_absence_probe_never_grants_status_91(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    bash_env = tmp_path / "unknown-group-probe.bash"
    bash_env.write_text(
        "python3() {\n"
        "    if [ \"${1-}\" = '-c' ] && [ \"${3-}\" = 'group' ]; then\n"
        "        builtin printf 'UNKNOWN\\n'\n"
        "        return 0\n"
        "    fi\n"
        "    command python3 \"$@\"\n"
        "}\n"
        "export -f python3\n",
        encoding="utf-8",
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["BASH_ENV"] = str(bash_env)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6)
    assert result.returncode == int(W1_RETAINED_FAILURE_CODE)
    assert not marker.exists()
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "wait=0" in err and "group=UNKNOWN" in err
    assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE


def test_registration_failure_never_signals_changed_process_identity(tmp_path):
    """An untrusted pre-receipt foreign group is neither registered nor acted on."""
    mod = _load()
    foreign = subprocess.Popen(
        ["setsid", "sleep", "300"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=False)
    workload_marker = tmp_path / "workload-started"
    registrar_marker = tmp_path / "registrar-started"
    bash_env, signal_log = _w1_signal_probe(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(workload_marker)),
        reap_seconds=3, pytest_pid_override=foreign.pid,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar_marker)
    env["BASH_ENV"] = str(bash_env)
    env["W1_SIGNAL_LOG"] = str(signal_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        rc = proc.wait(timeout=6)
        assert rc == int(W1_RETAINED_FAILURE_CODE)
        assert foreign.poll() is None and os.getpgid(foreign.pid) == foreign.pid
        assert not registrar_marker.exists(), (
            "the runner registered a PID whose PPID was not the runner")
        assert not workload_marker.exists() and not signal_log.exists()
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "initial-not-direct-child" in err
        assert "release_writes=0" in err
    finally:
        _w1_kill_group(foreign.pid)
        foreign.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.parametrize("field,reason", [
    ("ppid", "initial-not-direct-child"),
    ("pgrp", "initial-not-group-leader"),
    ("state", "initial-receipt-refused"),
])
def test_gate_receipt_admission_rejects_wrong_provenance(
        tmp_path, field, reason):
    mod = _load()
    workload_marker = tmp_path / "workload-started"
    registrar_marker = tmp_path / "registrar-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(workload_marker)),
        reap_seconds=3,
    )
    bash_env, counter = _w1_process_probe_drift(
        tmp_path, field, after_calls=0, mutate_group=False, one_shot=True)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar_marker)
    env["BASH_ENV"] = str(bash_env)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6)
    assert not registrar_marker.exists() and not workload_marker.exists(), (
        "N219-N220-FORBIDDEN-REGISTRAR-CROSSING")
    assert counter.read_text().strip() == "1"
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert reason in err and "release_writes=0" in err
    assert result.returncode == int(W1_RETAINED_FAILURE_CODE)


@pytest.mark.parametrize("field", ["pgrp", "starttime"])
def test_registration_receipt_drift_before_go_refuses_release(tmp_path, field):
    """Separate pgrp/starttime changes both block GO after registration."""
    mod = _load()
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    bash_env, counter = _w1_process_probe_drift(
        tmp_path, field, after_calls=2, mutate_group=False)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n", sleep=0.1))
    env["BASH_ENV"] = str(bash_env)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        rc = proc.wait(timeout=7)
        assert rc == int(W1_RELEASE_FAILURE_CODE)
        assert counter.read_text().strip() == "3"
        assert not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "pre-go-identity-refused" in err and "release_writes=0" in err
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_wedge_hunt_does_not_wait_on_a_job_it_could_not_register(tmp_path):
    """Registrar refusal never directs a numeric signal at the inert gate."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    syscall_log = tmp_path / "registration-signal-syscalls"
    bash_env, signal_log = _w1_signal_probe(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\nsleep 300\n" % shlex.quote(str(marker)),
        reap_seconds=3,
        owned_group_census_override="ABSENT",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.5))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    env["BASH_ENV"] = str(bash_env)
    env["W1_SIGNAL_LOG"] = str(signal_log)
    proc = subprocess.Popen(
        ["strace", "-qq", "-e", "trace=kill,tgkill,tkill",
         "-o", str(syscall_log), "bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, live = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(registrar)
        assert gate_pid > 0 and os.getpgid(gate_pid) == gate_pid
        assert live == [str(gate_pid)] and not marker.exists()
        rc = proc.wait(timeout=6)
        assert not marker.exists() and not signal_log.exists()
        syscalls = syscall_log.read_text(encoding="utf-8")
        assert not re.search(
            r"\bkill\(\s*-%d\s*," % gate_pid, syscalls), (
            "registration failure signalled the pre-id target group")
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "frame=ABORTED v1 reason=release-eof" in err
        assert "wait=0" in err and "release_writes=0" in err
        assert (rundir / "exitcode").read_text().strip() == W1_RUNNER_FAILURE_CODE
        assert not _w1_live_in_group(gate_pid)
        assert rc == int(W1_RUNNER_FAILURE_CODE)
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# The explicit dynamic catcher name is retained alongside the legacy node id.
test_pre_registration_target_is_never_signalled = (
    test_wedge_hunt_does_not_wait_on_a_job_it_could_not_register)


# The four ways a READY frame can be inadmissible: absent, partial, malformed,
# and duplicate. Each must be refused before the registrar, under EITHER
# settlement outcome below -- that half of the contract is not what was wrong.
_W1_INADMISSIBLE_READY_PREAMBLES = [
    "exit 97",
    "printf 'READY'; exit 97",
    "printf 'WRONG v1\\n'; exit 97",
    "printf 'READY v1 pid=%s\\nEXTRA v1\\n' \"$BASHPID\"; exit 97",
]


@pytest.mark.parametrize("preamble", _W1_INADMISSIBLE_READY_PREAMBLES)
@pytest.mark.parametrize("settlement", ["ABSENT", "UNKNOWN"])
def test_gate_ready_is_exact_terminal_admission(tmp_path, preamble, settlement):
    """Inadmissible READY never reaches the registrar, and the status it
    reports is decided by a precondition this test FORCES rather than races.

    WHY THIS IS PARAMETRIZED TWICE. Refusing the frame and classifying the
    settlement are two different claims, and only the first one was ever
    tested. `registration_settle_abort 94 92 "ready-refused"` resolves to a
    PROVED setup failure (94) or a RETAINED unknown (92) according to whether
    the runner could prove its gate group had settled -- and both answers are
    correct for what each run could prove.

    THE RACE THIS REPLACES, measured 2026-08-23 on seven hosts. Every
    parametrization used a prelude that exits immediately, so the gate child
    and the runner's own group-receipt probe at
    `bd-wedge-hunt:W1_GATE_GROUP_READY_AT_ACQUIRE` raced. Gate still alive at
    the probe -> group_ready=1 -> the census runs -> ABSENT -> 94. Gate already
    a zombie or reaped -> `registration_child_group_is_leader` refuses ->
    group_ready=0 -> THE GATE-ROLE CENSUS IS NEVER CALLED, because the `&&` at
    `bd-wedge-hunt:2314` in `registration_checked_child_wait` short-circuits
    (`registration_checked_gate_wait` only delegates to it) -> the status stays
    at its initialised UNKNOWN -> 92. The census function has three call sites
    and the other two still run; it is the gate one that is skipped. The test
    demanded 92 unconditionally, so three of twenty-eight outcomes across the
    fleet failed on test3 and test4 for being right. Thirty low-load
    repetitions afterwards -- 120 parameter outcomes -- all returned 92, which
    is exactly why a green rerun could not dispose of it.

    WHY FORCING THE CENSUS WOULD NOT HAVE WORKED, and why these controls drive
    the barrier instead: on the runs that produced 92 the census function is
    never entered, so an injected census verdict is unobservable there.
    `descendants=UNKNOWN` in those preserved records is an initialised value,
    not a measurement. The deciding precondition is the group receipt, so that
    is what gets held still.

    NEITHER BRANCH IS ALLOWED TO LAUNDER THE OTHER. The status assertion is
    exact per branch, the forced precondition is asserted from the runner's own
    durable record, and each branch asserts the liveness state it built.
    """
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    gate_pidfile = tmp_path / "gate-coproc.pid"
    recheck = tmp_path / "before-group-receipt-recheck"
    probed = tmp_path / "after-group-receipt-recheck"
    hold = tmp_path / "gate-hold"
    os.mkfifo(recheck)
    os.mkfifo(probed)
    # Opened BEFORE the runner starts, and non-blocking, so the runner's write
    # can never block on a missing reader and this test can never deadlock on
    # a barrier the runner reached first.
    probed_fd = os.open(probed, os.O_RDONLY | os.O_NONBLOCK)

    # $BASHPID inside the coproc body IS the pid the runner probes.
    publish = (
        'printf \'%%s\\n\' "$BASHPID" > %s\n'
        'mv %s %s\n' % (
            shlex.quote(str(gate_pidfile) + ".tmp"),
            shlex.quote(str(gate_pidfile) + ".tmp"),
            shlex.quote(str(gate_pidfile))))
    # BOTH branches build the SAME gate and hold it on the SAME fifo. The only
    # difference between a 94 and a 92 is the ORDER of the two releases below,
    # which is exactly the difference the fleet was racing -- stating it as an
    # ordering rather than as two different fixtures is what makes the contrast
    # exact instead of merely plausible.
    os.mkfifo(hold)
    prelude = publish + "IFS= read -r _ < %s\n" % shlex.quote(str(hold))
    prelude += preamble

    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_prelude=prelude,
        before_group_receipt_recheck_fifo=recheck,
        after_group_receipt_recheck_fifo=probed,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    # EXACT PER BRANCH. `status in {92, 94}` would accept the very race this
    # control exists to remove, so each forced precondition names one status.
    expected_code = (W1_SETUP_FAILURE_CODE if settlement == "ABSENT"
                     else W1_RETAINED_FAILURE_CODE)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    gate_pid = -1
    try:
        _w1_wait_for_path(gate_pidfile, timeout=15.0)
        gate_pid = int(gate_pidfile.read_text().strip())
        assert gate_pid > 0, gate_pid

        # SHARED PRECONDITION, asserted before either branch relies on it: the
        # gate is blocked on its hold fifo, so it is alive and is its own group
        # leader -- which is what `registration_child_group_is_leader` requires.
        # Nothing here is a timing argument: the child cannot exit until this
        # test opens that fifo.
        assert _w1_pid_is_live(gate_pid), (
            "the gate child was not alive at the hold barrier, so neither "
            "settlement precondition could be built from a known state")
        assert os.getpgid(gate_pid) == gate_pid, (
            "the gate child is not its own group leader")

        if settlement == "ABSENT":
            # Probe first, THEN let the gate exit -- and WAIT for the probe to
            # actually finish before letting it.
            #
            # MEASURED, not assumed. An earlier draft released these two
            # barriers back to back, reasoning that a released runner probes a
            # still-blocked gate. Releasing the first barrier only proves the
            # runner was WOKEN. Under 48 burners this host produced
            # group_ready=0 on 3 of 4 ABSENT cases, and a 2-core CI runner
            # produced the same 92-instead-of-94 -- the gate won the gap and
            # exited before the probe read /proc. The mutation battery had
            # already reported the swapped-order mutant as ESCAPED; it was a
            # true positive about this exact gap, not an artefact.
            _w1_release_fifo(recheck)
            assert "gate-receipt-recheck-done" in _w1_await_fifo(
                probed_fd, timeout=30.0), (
                "the runner never reported completing its gate receipt "
                "recheck, so the ABSENT precondition was not forced")
            _w1_release_fifo(hold)
        else:
            # Let the gate exit FIRST, and prove it reached the state that
            # makes the probe refuse, before releasing the probe.
            _w1_release_fifo(hold)
            # A SETTLED TERMINAL STATE, named exactly rather than inferred
            # from "not live". Measured here: bash reaps the coproc child in
            # its own SIGCHLD handling even while the runner script is blocked
            # at the barrier, so the stable observation is REAPED (the stat
            # file is gone) rather than the Z this first assumed. Both are
            # terminal and both are what `registration_process_receipt` fails
            # on, which is what makes the probe refuse -- but only one of them
            # actually occurs, and the assertion says which.
            #
            # This is a bounded wait for a state TRANSITION, not a sleep, and
            # it is LOAD-BEARING: released without it the gate is still waking
            # from its fifo read and is observably alive. A mutation battery
            # that deletes this loop goes RED on the assertion below -- an
            # earlier draft polled `_w1_pid_is_live` before releasing `hold` at
            # all, and that mutant ESCAPED because the gate had already exited
            # on its own by then. The loop was doing nothing and the control
            # was relying on the very race it claims to remove.
            deadline = time.monotonic() + 15.0
            state = None
            while time.monotonic() < deadline:
                try:
                    state = _w1_proc_observation(gate_pid)[-1]
                except (FileNotFoundError, ProcessLookupError, ValueError,
                        AssertionError):
                    state = "REAPED"
                if state in {"Z", "REAPED"}:
                    break
                time.sleep(0.01)
            assert state in {"Z", "REAPED"}, (
                "the gate child never reached a settled terminal state, so "
                "this control did not build the UNKNOWN precondition and the "
                "receipt probe would still be racing: state=%r" % (state,))
            _w1_release_fifo(recheck)
            assert "gate-receipt-recheck-done" in _w1_await_fifo(
                probed_fd, timeout=30.0), (
                "the runner never reported completing its gate receipt "
                "recheck, so the UNKNOWN precondition was not forced")

        observed = _w1_wait_for_exit(proc, rundir)
        if observed != int(expected_code):
            # Say WHICH failure this is. A starved host cannot spawn the census
            # or deadline owners the runner needs, records
            # stop=DEADLINE-SPAWN-FAILED or no gate row at all, and honestly
            # reports UNKNOWN -- which is the runner being right about not
            # knowing, not the status policy being wrong. Measured under 64
            # burners with concurrent -n 24 lanes; the capture serial lane runs
            # this module at -n 0, where it is deterministic. Backlog row 221.
            try:
                owners = (rundir / "registration-owners.log").read_text(
                    encoding="utf-8")
            except OSError:
                owners = "<no registration-owners.log>"
            gate = [l for l in owners.splitlines()
                    if l.startswith("OWNER role=gate ")]
            raise AssertionError(
                "gate-ready settlement status %s, expected %s for forced "
                "%s.\ngate owner rows: %s\nIf that row says "
                "stop=DEADLINE-SPAWN-FAILED, or there is no gate row at all, "
                "this host could not spawn the runner's own measurement "
                "owners and the runner reported UNKNOWN correctly -- see "
                "backlog row 221, not a policy mismatch."
                % (observed, expected_code, settlement, gate or "NONE"))
    finally:
        os.close(probed_fd)
        if gate_pid > 0:
            _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)

    # The half of the contract that was always right: no inadmissible frame
    # reaches the registrar or the workload, under either settlement.
    assert not registrar.exists() and not marker.exists()
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "phase=ready" in err and "release_writes=0" in err

    # The forced precondition, read back from the runner's OWN durable record
    # rather than assumed from the fixture that forced it.
    owners = (rundir / "registration-owners.log").read_text(encoding="utf-8")
    gate_rows = [line for line in owners.splitlines()
                 if line.startswith("OWNER role=gate ")]
    assert len(gate_rows) == 1, owners
    expected_ready = "group_ready=1" if settlement == "ABSENT" else "group_ready=0"
    assert expected_ready in gate_rows[0], (
        "the forced settlement precondition did not take effect", gate_rows[0])
    assert "group=%s" % settlement in err, err

    # NEGATIVE CONTROL on the 94 branch. `registration_finish` downgrades a
    # requested 94 to 92 when the owners were not all proved settled, and it
    # SAYS SO. Its absence is what makes this 94 a proved settlement rather
    # than a status that survived by not being checked.
    if settlement == "ABSENT":
        assert "SETUP-CLASSIFICATION-DOWNGRADE" not in err, err
    else:
        assert "wait_ok=0" in gate_rows[0], gate_rows[0]

    assert (rundir / "exitcode").read_text().strip() == expected_code


def test_live_wrong_ready_frame_never_reaches_the_registrar(tmp_path):
    """A live, well-shaped child does not launder a malformed READY frame."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    gate_program = _w1_adversarial_gate_program(
        ready="WRONG v1", terminal="ABORTED v1 reason=release-eof")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
        owned_group_census_override="ABSENT",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    assert not registrar.exists() and not marker.exists(), (
        "WRONG-READY-REACHED-REGISTRAR", registrar.exists(), marker.exists(),
        result.returncode, result.stdout, result.stderr)
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "phase=ready" in err and "frame=WRONG v1" in err
    assert result.returncode == 94


def test_live_duplicate_ready_frame_never_reaches_the_registrar(tmp_path):
    """A second buffered admission frame is a protocol failure, not READY."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    gate_program = _w1_adversarial_gate_program(
        extra_ready="EXTRA v1", terminal="ABORTED v1 reason=release-eof")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6)
    assert not registrar.exists() and not marker.exists()
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "phase=ready" in err and "release_writes=0" in err
    assert result.returncode == 94


def test_delayed_extra_ready_frame_never_reaches_the_registrar(tmp_path):
    """Registration waits for exact READY-channel EOF, not a quiet instant."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    ready_written = tmp_path / "ready-written"
    release_extra = tmp_path / "release-extra"
    reader_entered, reader_release, reader_entered_fd = _w1_fifo_barrier(
        tmp_path, "ready-reader-consumed")
    os.mkfifo(release_extra)
    gate_program = _w1_adversarial_gate_program(
        terminal="ABORTED v1 reason=release-eof",
        extra_ready="EXTRA v1", ready_written_marker=ready_written,
        extra_ready_release=release_extra)
    reader_anchor = "            data.extend(chunk)\n"
    assert mod.REGISTRATION_CHANNEL_READER_PROGRAM.count(reader_anchor) == 1
    reader_program = mod.REGISTRATION_CHANNEL_READER_PROGRAM.replace(
        reader_anchor,
        reader_anchor
        + "            if (mode == 'ready' and data.endswith(b'\\n')\n"
        + "                    and b'EXTRA v1' not in data):\n"
        + "                with open(%r, 'w', encoding='ascii') as stream:\n"
          % str(reader_entered)
        + "                    stream.write('ready-consumed\\n')\n"
        + "                with open(%r, 'r', encoding='ascii') as stream:\n"
          % str(reader_release)
        + "                    stream.readline()\n",
        1,
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
        channel_reader_program=reader_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(reader_entered_fd) == "ready-consumed\n"
        _w1_wait_for_path(ready_written)
        assert not registrar.exists(), (
            "exact READY bytes without admission EOF reached the registrar")
        with reader_release.open("w", encoding="utf-8") as stream:
            stream.write("release\n")
        with release_extra.open("w", encoding="utf-8") as stream:
            stream.write("release\n")
        rc = proc.wait(timeout=6)
        assert not registrar.exists() and not marker.exists(), (
            "exact READY bytes without admission EOF reached the registrar")
        assert (rundir / "exitcode").read_text().strip() == "94"
        assert rc == 94
    finally:
        os.close(reader_entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_nul_bearing_ready_frame_never_reaches_the_registrar(tmp_path):
    """Bash string normalization cannot turn byte-invalid READY into authority."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    gate_program = _w1_adversarial_gate_program(
        terminal="ABORTED v1 reason=release-eof", nul_ready=True)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6)
    assert not registrar.exists() and not marker.exists()
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "frame_hex=" in evidence and "00" in evidence
    assert result.returncode == 94


def test_pre_ready_descendant_refuses_registration(tmp_path):
    """READY authority requires the direct-child gate to be the sole member."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=_w1_pre_ready_descendant_gate_program(),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, live = _w1_wait_for_gate(rundir, minimum_live=2)
        assert len(live) >= 2
        rc = proc.wait(timeout=6)
        assert not registrar.exists() and not marker.exists()
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "initial-group-not-sole" in evidence
        assert (rundir / "exitcode").read_text().strip() == (
            W1_RETAINED_FAILURE_CODE)
        assert rc == int(W1_RETAINED_FAILURE_CODE)
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.parametrize("stdout,valid", [
    ("", False),
    ("stubhost-4242\nother-7\n", False),
    ("not an id\n", False),
    ("stubhost-4242\n", True),
])
def test_registrar_success_requires_one_exact_job_id_before_release(
        tmp_path, stdout, valid):
    mod = _load()
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\nexit %d\n" %
        (shlex.quote(str(marker)), W1_WORKLOAD_CODE),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout=stdout))
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7)
    if valid:
        assert marker.exists()
        assert (rundir / "exitcode").read_text().strip() == str(W1_WORKLOAD_CODE)
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        assert result.returncode == 0
    else:
        assert not marker.exists()
        assert (rundir / "jobid").read_text(encoding="utf-8") == stdout
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "invalid-job-id" in err and "release_writes=0" in err
        assert (rundir / "exitcode").read_text().strip() == W1_RELEASE_FAILURE_CODE
        assert result.returncode == int(W1_RELEASE_FAILURE_CODE)


@pytest.mark.parametrize("effect_then_error", [False, True])
def test_release_write_error_is_resolved_only_by_gate_status(
        tmp_path, effect_then_error):
    mod = _load()
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\nexit %d\n" %
        (shlex.quote(str(marker)), W1_WORKLOAD_CODE),
        reap_seconds=3,
    )
    bash_env = tmp_path / "release-effect-return.bash"
    effect = "        builtin printf \"$@\"\n" if effect_then_error else ""
    bash_env.write_text(
        "printf() {\n"
        "    if [ \"$1\" = '%s\\n' ] && [ \"${2-}\" = 'GO v1' ]; then\n"
        + effect
        + "        return 1\n"
          "    fi\n"
          "    builtin printf \"$@\"\n"
          "}\n"
          "export -f printf\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-4242\n"))
    env["BASH_ENV"] = str(bash_env)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        rc = _w1_wait_for_exit(proc, rundir)
        protocol = (rundir / "registration-gate.protocol").read_text()
        assert "write_status=1 writes=1" in protocol
        if effect_then_error:
            assert rc == 0 and marker.exists()
            assert "frame=EXEC-OK v1" in protocol
            assert (rundir / "exitcode").read_text().strip() == str(W1_WORKLOAD_CODE)
        else:
            assert rc == int(W1_RELEASE_FAILURE_CODE)
            assert not marker.exists() and (
                "frame=ABORTED v1 reason=release-eof" in protocol)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=5)


def test_real_release_sigpipe_is_contained_and_enters_registered_failure(
        tmp_path):
    """A dead release peer must not terminate Bash before reconciliation.

    A shell function returning 1 does not model the kernel's SIGPIPE delivery to
    a Bash builtin.  Hold the runner after its last identity check, terminate
    the exact gate through a pidfd, and only then permit the real ``printf``.
    """
    mod = _load()
    marker = tmp_path / "workload-started"
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "release-write-sigpipe")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
        before_release_write_barrier=(entered, release),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    calls_path = tmp_path / "bd-jobs-calls"
    env["W1_STUB_ARGV_LOG"] = str(calls_path)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    gate_pidfd = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd) == "release-write-entered\n"
        gate_pidfd = os.pidfd_open(gate_pid)
        os.kill(gate_pid, signal.SIGKILL)
        readable, _, _ = select.select([gate_pidfd], [], [], 5)
        assert readable == [gate_pidfd], (
            "the exact release peer did not reach a terminal state")
        with release.open("w", encoding="ascii") as stream:
            stream.write("release\n")

        returncode = proc.wait(timeout=8)
        protocol = (rundir / "registration-gate.protocol").read_text(
            encoding="utf-8")
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert returncode == int(W1_RELEASE_FAILURE_CODE), (
            "REAL-RELEASE-SIGPIPE-BYPASSED-REGISTERED-FUNNEL: "
            f"runner returned {returncode}")
        assert (rundir / "exitcode").read_text().strip() == W1_RELEASE_FAILURE_CODE
        assert "write_status=" in protocol and "sigpipe=1" in protocol
        assert "writes=1" in protocol
        assert "REGISTERED-FAILURE" in evidence and "reconcile=0" in evidence
        assert "id=stubhost-4242" in evidence
        calls = calls_path.read_text(encoding="utf-8").splitlines()
        assert len(calls) == 2, (
            "REAL-RELEASE-SIGPIPE-DID-NOT-RECONCILE-EXACTLY-ONCE: " +
            repr(calls))
        assert calls[0].startswith("register ")
        assert calls[1] == "reap --id stubhost-4242"
        assert not marker.exists()
    finally:
        os.close(entered_fd)
        if gate_pidfd >= 0:
            os.close(gate_pidfd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_release_sigpipe_handler_is_scoped_and_restored_after_write(tmp_path):
    """The containment handler must not become ambient runner signal policy."""
    mod = _load()
    pipe_state = tmp_path / "post-release-pipe-trap"
    script, rundir = _w1_build_runner(
        mod, tmp_path, "#!/bin/bash\nexit %d\n" % W1_WORKLOAD_CODE,
        reap_seconds=3,
        after_release_pipe_probe=pipe_state,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)

    assert result.returncode == 0
    assert (rundir / "exitcode").read_text().strip() == str(W1_WORKLOAD_CODE)
    assert pipe_state.is_file()
    assert pipe_state.read_text(encoding="utf-8") == "", (
        "RELEASE-SIGPIPE-HANDLER-LEAKED-PAST-WRITE")


@pytest.mark.parametrize("frame", ["G", "GO v1\nX", "GO v1\nGO v1\n"])
def test_partial_or_duplicate_release_frame_never_execs(tmp_path, frame):
    mod = _load()
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    bash_env = tmp_path / "bad-release-frame.bash"
    bash_env.write_text(
        "printf() {\n"
        "    if [ \"$1\" = '%s\\n' ] && [ \"${2-}\" = 'GO v1' ]; then\n"
        "        builtin printf " + shlex.quote(frame) + "\n"
        "        return 0\n"
        "    fi\n"
        "    builtin printf \"$@\"\n"
        "}\n"
        "export -f printf\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-4242\n"))
    env["BASH_ENV"] = str(bash_env)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7)
    assert not marker.exists()
    assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
    protocol = (rundir / "registration-gate.protocol").read_text()
    assert "writes=1" in protocol and "ABORTED v1 reason=release-protocol" in protocol
    assert result.returncode == int(W1_RELEASE_FAILURE_CODE)


def test_gate_exec_failure_is_named_registered_handoff_failure(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "stub-argv"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_GATE_EXECUTABLE"] = str(tmp_path / "does-not-exist")
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7)
    assert not marker.exists()
    assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
    protocol = (rundir / "registration-gate.protocol").read_text()
    assert "EXEC-FAIL v1 errno=2" in protocol
    calls = argv_log.read_text().splitlines()
    assert len(calls) == 2 and calls[0].startswith("register ")
    assert calls[1] == "reap --id stubhost-4242"
    assert result.returncode == int(W1_RELEASE_FAILURE_CODE)


def test_invalid_exec_ok_reconciles_registered_id_without_waiting_live_group(
        tmp_path):
    """EXEC-OK without terminal EOF is delivery-unknown, owned by its id."""
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "stub-argv"
    gate_program = _w1_adversarial_gate_program(
        terminal="EXEC-OK v1", hold=30, status=0)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, reconcile_seconds=3, gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    started = time.monotonic()
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        rc = proc.wait(timeout=8)
        assert _w1_live_in_group(gate_pid), (
            "the fixture did not retain the hostile registered gate")
        calls = argv_log.read_text(encoding="utf-8").splitlines()
        assert len(calls) == 2 and calls[0].startswith("register ")
        assert calls[1] == "reap --id stubhost-4242"
        assert not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        assert time.monotonic() - started < 7.0
        assert rc == int(W1_RELEASE_FAILURE_CODE)
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_terminal_relay_wait_failure_reconciles_registered_id(tmp_path):
    """Terminal bytes are not authoritative until their named relay is reaped."""
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "stub-argv"
    failing_relay = (
        "import os\n"
        "while True:\n"
        "    chunk = os.read(0, 4096)\n"
        "    if not chunk:\n"
        "        break\n"
        "    while chunk:\n"
        "        written = os.write(1, chunk)\n"
        "        chunk = chunk[written:]\n"
        "raise SystemExit(9)\n"
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\nexit %d\n" %
        (shlex.quote(str(marker)), W1_WORKLOAD_CODE),
        reconcile_seconds=3, terminal_relay_program=failing_relay,
        owned_group_census_override="ABSENT",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7)
    calls = argv_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2 and calls[1] == "reap --id stubhost-4242"
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "relay_wait=9" in evidence
    assert marker.exists()
    assert result.returncode == int(W1_RELEASE_FAILURE_CODE)


def test_failed_workload_wait_reconciles_registered_id(tmp_path):
    """A wait ownership failure cannot abandon the registered EXEC-OK child."""
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "stub-argv"
    bash_env = tmp_path / "fail-workload-wait.bash"
    bash_env.write_text(
        "wait() {\n"
        "    if [ \"${1-}\" = '-p' ] && [ \"${3-}\" = \"$PYTEST_GATE_PID\" ]; then\n"
        "        builtin printf 'synthetic workload wait failure\\n' >&2\n"
        "        return 127\n"
        "    fi\n"
        "    builtin wait \"$@\"\n"
        "}\n"
        "export -f wait\n",
        encoding="utf-8",
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\nexit %d\n" %
        (shlex.quote(str(marker)), W1_WORKLOAD_CODE),
        reconcile_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["BASH_ENV"] = str(bash_env)
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7)
    wait_evidence = (
        rundir / "registration-workload-wait.err").read_text(encoding="utf-8")
    assert "synthetic workload wait failure" in wait_evidence
    calls = argv_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2 and calls[0].startswith("register ")
    assert calls[1] == "reap --id stubhost-4242"
    assert marker.exists()
    assert result.returncode == int(W1_RELEASE_FAILURE_CODE)


def test_reconciliation_term_resistance_stays_inside_total_budget(tmp_path):
    """The reconciliation owner reserves a KILL grace inside its one budget."""
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "stub-argv"
    reap_pid_path = tmp_path / "reap.pid"
    reap_child_path = tmp_path / "reap-child.pid"
    entered, _release, entered_fd = _w1_fifo_barrier(
        tmp_path, "timeout-reap")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, reconcile_seconds=2,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_GATE_EXECUTABLE"] = str(tmp_path / "does-not-exist")
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    env["W1_REAP_IGNORE_TERM"] = "300"
    env["W1_REAP_PID_MARKER"] = str(reap_pid_path)
    env["W1_REAP_CHILD_PID_MARKER"] = str(reap_child_path)
    env["W1_REAP_ENTERED_FIFO"] = str(entered)
    env["W1_REAP_RELEASE_FIFO"] = str(_release)
    started = time.monotonic()
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        assert _w1_await_fifo(entered_fd, timeout=10) == "reap-entered\n"
        reap_pid = int(reap_pid_path.read_text().strip())
        reap_child_pid = int(reap_child_path.read_text().strip())
        assert _w1_pid_is_live(reap_pid) and _w1_pid_is_live(reap_child_pid)
        try:
            rc = proc.wait(timeout=7)
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                "TERM-resistant reconciliation escaped its owner") from exc
        assert not _w1_pid_is_live(reap_pid)
        assert not _w1_pid_is_live(reap_child_pid)
        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "reconciliation"]
        assert len(records) == 1 and records[0]["wait_ok"] == "1", records
        assert records[0]["descendants"] == "ABSENT", records
        calls = argv_log.read_text(encoding="utf-8").splitlines()
        assert calls[-1] == "reap --id stubhost-4242"
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        assert records[0]["status"] in {"124", "137"}
        assert '--kill-after="$W1_SPAWN_KILL_AFTER"' in mod.RUNNER
        assert rc == int(W1_RELEASE_FAILURE_CODE)
    finally:
        os.close(entered_fd)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.parametrize("terminal,status", [
    ("EXEC-FAIL v1 errno=5", 96),
    ("ABORTED v1 reason=synthetic", 0),
])
def test_terminal_frame_without_eof_never_enters_an_unbounded_child_wait(
        tmp_path, terminal, status):
    """A terminal-looking line is not authority to bare-wait a live gate."""
    mod = _load()
    marker = tmp_path / "workload-started"
    checked_wait_log = tmp_path / "checked-wait-entered"
    gate_program = _w1_adversarial_gate_program(
        terminal=terminal, hold=30, status=status)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
        checked_wait_probe=checked_wait_log,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        rc = _w1_wait_for_exit_or_forbidden_checked_wait(
            proc, rundir, checked_wait_log)
        assert rc == int(W1_RELEASE_FAILURE_CODE)
        assert not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        assert not (rundir / "registration-wait.err").exists(), (
            "the runner entered child wait without terminal EOF authority")
        assert not checked_wait_log.exists()
        assert _w1_live_in_group(gate_pid), (
            "the hold-open gate did not exercise the pre-wait boundary")
        readers = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-reader"]
        assert len(readers) == 1 and readers[0]["status"] == "0", readers
        assert readers[0]["wait_ok"] == "1"
        assert readers[0]["descendants"] == "ABSENT"
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_cancellation_during_reconciliation_retains_primary_and_reaps_owner(
        tmp_path):
    """Registered uncertainty stays 93, but INT cannot disappear from evidence."""
    mod = _load()
    marker = tmp_path / "workload-started"
    reap_pid_path = tmp_path / "reap.pid"
    reap_child_path = tmp_path / "reap-child.pid"
    _entered, release, entered_fd = _w1_fifo_barrier(tmp_path, "reap")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, reconcile_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_GATE_EXECUTABLE"] = "/definitely/missing/w1-executable"
    env["W1_REAP_IGNORE_TERM"] = "300"
    env["W1_REAP_PID_MARKER"] = str(reap_pid_path)
    env["W1_REAP_CHILD_PID_MARKER"] = str(reap_child_path)
    env["W1_REAP_ENTERED_FIFO"] = str(_entered)
    env["W1_REAP_RELEASE_FIFO"] = str(release)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, timeout=10) == "reap-entered\n"
        reap_pid = int(reap_pid_path.read_text().strip())
        reap_child_pid = int(reap_child_path.read_text().strip())
        assert _w1_pid_is_live(reap_pid) and _w1_pid_is_live(reap_child_pid)
        os.kill(proc.pid, signal.SIGINT)
        assert proc.wait(timeout=12) == int(W1_RELEASE_FAILURE_CODE)
        assert not marker.exists()
        assert not _w1_pid_is_live(reap_pid)
        assert not _w1_pid_is_live(reap_child_pid)
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "primary_cancel=130" in evidence
        reconcile = [record for record in _w1_owner_records(rundir)
                     if record["role"] == "reconciliation"]
        assert len(reconcile) == 1
        assert reconcile[0]["wait_ok"] == "1"
        assert reconcile[0]["descendants"] == "ABSENT"
    finally:
        os.close(entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_cooperative_registered_cancellation_returns_primary_status(tmp_path):
    """The conventional-cancel branch is reachable when every owner settles."""
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "bd-jobs.argv"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, reconcile_seconds=3,
        cancel_registered_failure=True,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_GATE_EXECUTABLE"] = "/definitely/missing/w1-executable"
    env["W1_STUB_ARGV_LOG"] = str(argv_log)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    assert not marker.exists()
    calls = argv_log.read_text(encoding="utf-8").splitlines()
    assert calls.count("reap --id stubhost-4242") == 1, calls
    assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "primary_cancel=130" in evidence
    assert "reconcile=0" in evidence and "gate_settled=1" in evidence
    assert result.returncode == 130
    assert (rundir / "exitcode").read_text().strip() == "130"


def test_handoff_timeout_retains_registered_id_under_one_budget(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    checked_wait_log = tmp_path / "checked-wait-entered"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, checked_wait_probe=checked_wait_log,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_GATE_WITHHOLD_STATUS"] = "30"
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        rc = _w1_wait_for_exit_or_forbidden_checked_wait(
            proc, rundir, checked_wait_log)
        assert rc == int(W1_RELEASE_FAILURE_CODE)
        assert not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        protocol = (rundir / "registration-gate.protocol").read_text()
        assert "writes=1 sigpipe=0 frame_rc=142" in protocol
        assert "frame= frame_hex= eof=0" in protocol
        assert not checked_wait_log.exists()
        readers = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-reader"]
        assert len(readers) == 1 and readers[0]["status"] == "0", readers
        assert readers[0]["wait_ok"] == "1"
        assert readers[0]["descendants"] == "ABSENT"
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_post_ready_protocol_budget_is_positive_and_reaches_terminal_frame(
        tmp_path):
    """ZERO-BUDGET must fail after READY/register/GO, not beside admission."""
    mod = _load()
    marker = tmp_path / "workload-started"
    gate_program = _w1_adversarial_gate_program(
        terminal="ABORTED v1 reason=synthetic", delay_before_terminal=0.2)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert not marker.exists()
    protocol = (rundir / "registration-gate.protocol").read_text()
    assert "writes=1" in protocol
    assert "frame=ABORTED v1 reason=synthetic" in protocol
    assert "eof=1" in protocol


def test_partial_handoff_frame_does_not_restart_the_protocol_budget(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    entered, _release, entered_fd = _w1_fifo_barrier(
        tmp_path, "partial-terminal")
    checked_wait_log = tmp_path / "checked-wait-entered"
    deadline_probe = tmp_path / "handoff-deadlines"
    gate_program = _w1_adversarial_gate_program(
        terminal_bytes=b"EX", terminal_suffix=b"EC\n",
        terminal_suffix_entered=entered, terminal_suffix_release=_release,
        hold=30, status=99)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
        checked_wait_probe=checked_wait_log,
        handoff_deadline_probe=deadline_probe,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, timeout=10) == (
            "partial-terminal-written\n")
        deadline_rows = deadline_probe.read_text(encoding="ascii").splitlines()
        assert len(deadline_rows) == 2, deadline_rows
        pre_deadline = int(deadline_rows[0].removeprefix("pre="))
        post_deadline = int(deadline_rows[1].removeprefix("post="))
        assert post_deadline == pre_deadline, (
            "N225-PARTIAL-FRAME-RESTARTED-TOTAL-BUDGET",
            pre_deadline, post_deadline)
        rc = _w1_wait_for_exit_or_forbidden_checked_wait(
            proc, rundir, checked_wait_log)
        assert not marker.exists()
        protocol = (rundir / "registration-gate.protocol").read_text()
        assert "writes=1" in protocol and "frame_hex=4558" in protocol
        post_write = mod.RUNNER.index(
            'registration_cancel_checkpoint "post-release-write"')
        terminal_read = mod.RUNNER.index(
            "registration_read_terminal", post_write)
        handoff_slice = mod.RUNNER[post_write:terminal_read]
        assert "registration_begin_gate_deadline" not in handoff_slice and (
            'W1_ACTIVE_DEADLINE_US=' not in handoff_slice), (
            "the handoff phase replaces its absolute deadline after partial input")
        terminal = [record for record in _w1_owner_records(rundir)
                    if record["role"] == "terminal-reader"]
        assert len(terminal) == 1 and terminal[0]["wait_ok"] == "1", terminal
        assert rc == int(W1_RELEASE_FAILURE_CODE)
    finally:
        os.close(entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def test_handoff_eof_is_not_exec_success(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "stub-argv"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_GATE_EXIT_AFTER_GO"] = "1"
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        rc = _w1_wait_for_exit(proc, rundir)
        assert not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        protocol = (rundir / "registration-gate.protocol").read_text()
        assert "writes=1 sigpipe=0 frame_rc=1 frame=" in protocol
        calls = argv_log.read_text(encoding="utf-8").splitlines()
        assert len(calls) == 2 and calls[1] == "reap --id stubhost-4242"
        assert rc == int(W1_RELEASE_FAILURE_CODE)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=5)


def test_nul_bearing_terminal_frame_is_not_exec_success(tmp_path):
    """Terminal classification is over exact bytes, never Bash strings."""
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "stub-argv"
    gate_program = _w1_adversarial_gate_program(
        terminal=None, terminal_bytes=b"EXEC-\x00OK v1\n", status=0)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, reconcile_seconds=3, gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        rc = _w1_wait_for_exit(proc, rundir)
        assert rc == int(W1_RELEASE_FAILURE_CODE)
        assert not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        calls = argv_log.read_text(encoding="utf-8").splitlines()
        assert calls[-1] == "reap --id stubhost-4242"
        protocol = (rundir / "registration-gate.protocol").read_text()
        assert "frame_hex=" in protocol and "00" in protocol
        terminal = (rundir / "registration-terminal-reader.out").read_text()
        assert "C:INVALID" in terminal and (
            "H:455845432d004f4b2076310a" in terminal)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=5)


def test_abort_timeout_retains_inert_gate_under_one_budget(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    checked_wait_log = tmp_path / "checked-wait-entered"
    bash_env, signal_log = _w1_signal_probe(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, checked_wait_probe=checked_wait_log,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["W1_GATE_WITHHOLD_ABORT"] = "30"
    env["BASH_ENV"] = str(bash_env)
    env["W1_SIGNAL_LOG"] = str(signal_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        rc = _w1_wait_for_exit_or_forbidden_checked_wait(
            proc, rundir, checked_wait_log)
        assert not marker.exists() and not signal_log.exists()
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "context=registrar-refused" in err
        assert "frame_rc=" in err and "wait=UNKNOWN" in err
        assert "release_writes=0" in err and "receipt=" in err
        terminal_readers = [record for record in _w1_owner_records(rundir)
                            if record["role"] == "terminal-reader"]
        assert len(terminal_readers) == 1, (
            "R12-N250-ZERO-BUDGET-SKIPPED-TERMINAL-OWNER")
        assert terminal_readers[0]["wait_ok"] == "1", terminal_readers
        assert terminal_readers[0]["descendants"] == "ABSENT", terminal_readers
        assert not checked_wait_log.exists(), (
            "abort settlement entered child wait before terminal protocol evidence")
        assert _w1_live_in_group(gate_pid), (
            "the withhold fixture retained no inert gate at classification")
        assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE
        assert rc == int(W1_RETAINED_FAILURE_CODE)
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_registration_cleanup_timeout_is_internal_and_names_retained_group(
        tmp_path):
    """ABORTED cannot become 91 when checked wait says 'not a child'."""
    mod = _load()
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    bash_env = tmp_path / "reject-gate-wait.bash"
    bash_env.write_text(
        "wait() {\n"
        "    if [ \"$1\" = '-n' ] && [ \"${4-}\" = \"$PYTEST_GATE_PID\" ]; then\n"
        "        builtin printf 'synthetic not-a-child\\n' >&2\n"
        "        return 127\n"
        "    fi\n"
        "    builtin wait \"$@\"\n"
        "}\n"
        "export -f wait\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["BASH_ENV"] = str(bash_env)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7)
    assert not marker.exists()
    assert "synthetic not-a-child" in (
        rundir / "registration-wait.err").read_text(encoding="utf-8")
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "frame=ABORTED v1 reason=release-eof" in err
    assert "wait=UNKNOWN" in err and "group=UNKNOWN" in err
    gate_records = [record for record in _w1_owner_records(rundir)
                    if record["role"] == "gate"]
    assert len(gate_records) == 1 and gate_records[0]["wait_ok"] == "0"
    assert gate_records[0]["descendants"] == "UNKNOWN"
    assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE
    assert result.returncode == int(W1_RETAINED_FAILURE_CODE)


def test_term_resistant_observer_stays_inside_gate_budget(tmp_path):
    """An observer is an owned subprocess and cannot outlive the phase budget."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    (bash_env, entered_fd, _release, helper_pid_path,
     child_pid_path) = _w1_hung_process_probe(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=2,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["BASH_ENV"] = str(bash_env)
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    started = time.monotonic()
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        assert _w1_await_fifo(entered_fd, timeout=10) == "observer-entered\n"
        helper_pid = int(helper_pid_path.read_text().strip())
        child_pid = int(child_pid_path.read_text().strip())
        assert _w1_pid_is_live(helper_pid) and _w1_pid_is_live(child_pid)
        try:
            rc = proc.wait(timeout=7)
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                "TERM-resistant observation escaped its owner") from exc
        assert not _w1_pid_is_live(helper_pid)
        assert not _w1_pid_is_live(child_pid)
        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "process-observer"]
        assert len(records) == 1 and records[0]["wait_ok"] == "1", records
        assert records[0]["descendants"] == "ABSENT", records
        assert not registrar.exists() and not marker.exists()
        assert "initial-observation-unavailable" in (
            rundir / "jobid.err").read_text(encoding="utf-8")
        assert time.monotonic() - started < 6.0
        assert rc == int(W1_RETAINED_FAILURE_CODE)
    finally:
        os.close(entered_fd)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))
            proc.kill()
            proc.wait(timeout=5)


def test_monotonic_clock_rollback_fails_closed_without_extending_budget(tmp_path):
    """A decreasing injected sample expires authority; it never gains time."""
    mod = _load()
    marker = tmp_path / "workload-started"
    gate_program = _w1_pre_ready_descendant_gate_program()
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=1, gate_program=gate_program,
        monotonic_samples=[10_000_000, 9_000_000, 8_000_000],
    )
    pytest_pid_path = rundir / "pytest.pid"
    bash_env, publish_entered_fd, publish_release = _w1_delay_shell_publish(
        tmp_path, pytest_pid_path)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["BASH_ENV"] = str(bash_env)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    gate_pid = -1
    try:
        # Rollback deliberately expires authority as soon as the controller
        # samples the clock.  The gate may therefore be fully reaped before a
        # scheduler-delayed test process can observe it live.  Its durable PID
        # is the non-racy proof that the real gate existed; post-exit absence
        # proves the rollback path did not abandon the owned group.
        assert _w1_await_fifo(publish_entered_fd) == (
            "publish-boundary-entered\n")
        try:
            assert not pytest_pid_path.exists(), (
                "pytest pid became visible before its payload was complete")
        finally:
            _w1_release_fifo(publish_release)
        _w1_wait_for_path(pytest_pid_path)
        gate_pid = int(pytest_pid_path.read_text().strip())
        rc = proc.wait(timeout=5)
        assert not marker.exists()
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "clock=ROLLBACK" in evidence
        assert not _w1_live_in_group(gate_pid), (
            "rollback returned while its real gate group remained live")
        assert rc in {
            int(W1_RETAINED_FAILURE_CODE), int(W1_RELEASE_FAILURE_CODE)}
    finally:
        os.close(publish_entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_cancellation_after_relay_before_gate_settles_the_acquired_owner(
        tmp_path):
    """The first acquired child is already behind INT and EXIT guards."""
    mod = _load()
    marker = tmp_path / "workload-started"
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "after-relay-acquire")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=8,
        after_relay_acquire_barrier=(entered, release),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    relay_receipt = None
    try:
        assert _w1_await_fifo(entered_fd, timeout=5) == "relay-acquired\n"
        relay_receipt_path = rundir / "injected-relay.receipt"
        relay_receipt = tuple(int(value) for value in
                              relay_receipt_path.read_text().split(":"))
        assert len(relay_receipt) == 5
        relay_pid, relay_ppid, relay_pgid, relay_sid, relay_start = relay_receipt
        assert relay_ppid == proc.pid and relay_pid == relay_pgid
        assert _w1_pid_is_live(relay_pid)
        os.kill(proc.pid, signal.SIGINT)
        with open(release, "w", encoding="ascii") as stream:
            stream.write("continue\n")
        assert proc.wait(timeout=15) == int(W1_RETAINED_FAILURE_CODE)
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "primary_cancel=130" in evidence
        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-relay"]
        assert len(records) == 1, records
        assert records[0]["waited_pid"] == str(relay_pid)
        assert records[0]["wait_ok"] == "1"
        assert records[0]["descendants"] == "ABSENT"
        gate_records = [record for record in _w1_owner_records(rundir)
                        if record["role"] == "gate"]
        assert len(gate_records) == 1
        assert gate_records[0]["owner_pid"] == "MISSING"
        assert gate_records[0]["stop"] == "PID-MISSING"
        assert not _w1_pid_is_live(relay_pid) and not marker.exists()
    finally:
        os.close(entered_fd)
        if relay_receipt is not None:
            relay_pid, _ppid, relay_pgid, _sid, relay_start = relay_receipt
            try:
                current = _w1_proc_receipt(relay_pid)
            except (FileNotFoundError, ProcessLookupError, ValueError):
                current = None
            if current == (relay_pid, relay_pgid, relay_start):
                _w1_kill_group(relay_pgid)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))


def test_exit_guard_settles_a_post_setsid_owner_after_nounset(tmp_path):
    """An unexpected shell exit drains the exact active owner before return."""
    mod = _load()
    acquired = tmp_path / "abnormal-owner-acquired"
    owner_receipt_path = tmp_path / "abnormal-owner.receipt"
    owner_entered, owner_release, owner_entered_fd = _w1_fifo_barrier(
        tmp_path, "abnormal-owner")
    os.mkfifo(acquired)
    anchor = "os.setsid()\n"
    malicious = (
        "import pathlib, signal\n"
        + "raw = pathlib.Path('/proc/self/stat').read_text(encoding='ascii')\n"
        + "tail = raw.rsplit(') ', 1)[1].split()\n"
        + "pathlib.Path(%r).write_text('%%d:%%d:%%d:%%d:%%s' %% "
          "(os.getpid(), os.getppid(), os.getpgrp(), os.getsid(0), tail[19]), "
          "encoding='ascii')\n" % str(owner_receipt_path)
        + "with open(%r, 'w', encoding='ascii') as stream:\n" % str(owner_entered)
        + "    stream.write('owner-receipt-stable\\n')\n"
        + "with open(%r, 'r', encoding='ascii') as stream:\n" % str(owner_release)
        + "    stream.readline()\n"
        + "os.close(1)\n"
        + "os.close(2)\n"
        + "with open(%r, 'w', encoding='ascii') as stream:\n" % str(acquired)
        + "    stream.write(str(os.getpid()) + '\\n')\n"
        + "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        + "while True:\n"
        + "    signal.pause()\n"
    )
    injection = (
        anchor
        + "if stdout_path.endswith('/registration-ready-reader.out'):\n"
        + "".join("    " + line + "\n" for line in malicious.splitlines())
    )
    timeout_owner = mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM.replace(
        anchor, injection, 1)
    script, rundir = _w1_build_runner(
        mod, tmp_path, "#!/bin/bash\nexit 0\n",
        reap_seconds=6,
        timeout_owner_program=timeout_owner,
        abnormal_owner_fifo=acquired,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))

    owner_pidfd = -1
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert _w1_await_fifo(owner_entered_fd) == "owner-receipt-stable\n"
        saved = tuple(int(value) for value in owner_receipt_path.read_text(
            encoding="ascii").split(":"))
        assert len(saved) == 5 and saved[0] == saved[2] == saved[3]
        pid = saved[0]
        raw = pathlib.Path("/proc", str(pid), "stat").read_text(
            encoding="ascii")
        head, tail_text = raw.rsplit(") ", 1)
        tail = tail_text.split()
        current = (int(head.split(" (", 1)[0]), int(tail[1]),
                   int(tail[2]), int(tail[3]), int(tail[19]))
        assert current == saved, "the injected owner identity drifted before release"
        owner_pidfd = os.pidfd_open(pid)
        with open(owner_release, "w", encoding="ascii") as stream:
            stream.write("trigger-nounset\n")
        try:
            _stdout, _stderr = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                "EXIT-GUARD-DID-NOT-SETTLE-ACTIVE-OWNER") from exc

        evidence = ((rundir / "jobid.err").read_text(encoding="utf-8")
                    if (rundir / "jobid.err").is_file() else "")
        assert "EXIT-GUARD" in evidence and "restored=1" in evidence, (
            "EXIT-GUARD-DID-NOT-SETTLE-ACTIVE-OWNER")
        assert proc.returncode != 0
        assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE
        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "ready-reader"]
        assert len(records) == 1, (
            "EXIT-GUARD-DID-NOT-SETTLE-ACTIVE-OWNER", records)
        record = records[0]
        assert record["waited_pid"] == record["owner_pid"]
        assert record["wait_ok"] == "1" and record["descendants"] == "ABSENT"
        assert record["stop"] == "TERM-GRACE-KILL-GROUP"
        assert not _w1_pid_is_live(int(record["owner_pid"]))
    finally:
        os.close(owner_entered_fd)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))
            proc.kill()
            proc.wait(timeout=5)
        # The pidfd was acquired while the complete five-field receipt was
        # still stable.  It remains an exact failure-safe capability after the
        # shell exits and Linux reparents an intentionally abandoned owner.
        if (owner_pidfd >= 0
                and not select.select([owner_pidfd], [], [], 0)[0]):
            signal.pidfd_send_signal(owner_pidfd, signal.SIGKILL, None, 0)
            assert select.select([owner_pidfd], [], [], 5)[0], (
                "failure-safe pidfd cleanup did not settle the injected owner")
        if owner_pidfd >= 0:
            os.close(owner_pidfd)


def test_cancellation_during_delayed_ready_preserves_primary(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    before_ready = tmp_path / "before-ready"
    release_ready = tmp_path / "release-ready"
    os.mkfifo(release_ready)
    gate_program = _w1_adversarial_gate_program(
        terminal="ABORTED v1 reason=release-eof",
        before_ready_marker=before_ready, ready_release=release_ready)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(before_ready)
        os.kill(proc.pid, signal.SIGINT)
        with release_ready.open("w", encoding="utf-8") as stream:
            stream.write("release\n")
        rc = proc.wait(timeout=6)
        assert not registrar.exists() and not marker.exists(), (
            "N234-CANCEL-TRAP-CROSSED-LATE-AUTHORITY")
        assert (rundir / "exitcode").read_text().strip() == "130"
        assert "REGISTER-CANCELLED" in (
            rundir / "jobid.err").read_text(encoding="utf-8")
        assert rc == 130
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_cancellation_during_pre_register_observation_never_registers(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    bash_env, observer_entered, observer_release = _w1_block_process_probe(
        tmp_path, on_call=2)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["BASH_ENV"] = str(bash_env)
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(observer_entered)
        os.kill(proc.pid, signal.SIGINT)
        with observer_release.open("w", encoding="utf-8") as stream:
            stream.write("continue\n")
        rc = proc.wait(timeout=6)
        assert not registrar.exists() and not marker.exists(), (
            "N244-CANCELLED-OBSERVER-CROSSED-REGISTRAR")
        assert (rundir / "exitcode").read_text().strip() == "130"
        assert rc == 130
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_cancellation_during_group_observer_forbids_registration(tmp_path):
    """The sole-group authority owner cannot cross into registrar after INT."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    bash_env, entered_fd, release = _w1_block_probe_fifo(tmp_path, "group")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-1\n"))
    env["BASH_ENV"] = str(bash_env)
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, live = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd) == "group-observer-entered\n"
        assert live == [str(gate_pid)]
        assert not registrar.exists() and not marker.exists()
        os.kill(proc.pid, signal.SIGINT)
        with release.open("w", encoding="utf-8") as stream:
            stream.write("continue\n")
        assert not registrar.exists() and not marker.exists()
        assert proc.wait(timeout=8) == 130
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "primary_cancel=130" in evidence
    finally:
        os.close(entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_cancellation_during_terminal_reader_reconciles_exact_id_once(tmp_path):
    """A registered terminal reader owner cannot hide the primary cancel."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    argv_log = tmp_path / "bd-jobs.argv"
    reader, entered_fd, release = _w1_terminal_reader_barrier(mod, tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, reconcile_seconds=3,
        channel_reader_program=reader,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_GATE_EXECUTABLE"] = "/definitely/missing/w1-executable"
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, timeout=10) == (
            "terminal-reader-entered\n")
        assert registrar.exists() and not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        os.kill(proc.pid, signal.SIGINT)
        with release.open("w", encoding="utf-8") as stream:
            stream.write("continue\n")
        assert not marker.exists()
        rc = proc.wait(timeout=10)
        calls = argv_log.read_text(encoding="utf-8").splitlines()
        assert calls.count("reap --id stubhost-4242") == 1, calls
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "primary_cancel=130" in evidence
        assert rc == 130
    finally:
        os.close(entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_cancellation_during_terminal_relay_wait_reconciles_once(tmp_path):
    """The named relay checked wait retains cancellation after registration."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    argv_log = tmp_path / "bd-jobs.argv"
    bash_env, entered_fd, release = _w1_checked_child_wait_barrier(
        tmp_path, "terminal-relay")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, reconcile_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_GATE_EXECUTABLE"] = "/definitely/missing/w1-executable"
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    env["BASH_ENV"] = str(bash_env)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, timeout=10) == (
            "terminal-relay-wait-entered\n")
        assert registrar.exists() and not marker.exists()
        os.kill(proc.pid, signal.SIGINT)
        with release.open("w", encoding="utf-8") as stream:
            stream.write("continue\n")
        assert not marker.exists()
        rc = proc.wait(timeout=10)
        calls = argv_log.read_text(encoding="utf-8").splitlines()
        assert calls.count("reap --id stubhost-4242") == 1, calls
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "primary_cancel=130" in evidence
        assert rc == 130
    finally:
        os.close(entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_pre_observation_cancellation_settles_only_in_the_top_shell(tmp_path):
    """A latched cancel cannot let a command-substitution child finalize files."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, cancel_before_observation=True,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)

    assert not registrar.exists() and not marker.exists()
    assert result.returncode == 130
    assert (rundir / "exitcode").read_text().strip() == "130"
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert evidence.count("REGISTER-CANCELLED") == 1, evidence
    gate_records = [record for record in _w1_owner_records(rundir)
                    if record["role"] == "gate"]
    assert len(gate_records) == 1, gate_records
    runner = mod.RUNNER
    assert 'W1_CURRENT_OBSERVATION="$(' not in runner
    assert 'PYTEST_INITIAL_OBSERVATION="$(' not in runner
    assert 'PYTEST_INITIAL_GROUP="$(' not in runner
    assert '[ "$BASHPID" = "$$" ] || return 1' in runner


def test_cancellation_during_gate_settlement_wait_preserves_primary(tmp_path):
    """A signal inside definite-refusal settlement cannot be laundered to 91."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    bash_env, entered_fd, release = _w1_gate_wait_barrier(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    env["BASH_ENV"] = str(bash_env)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, timeout=10) == "gate-wait-entered\n"
        assert registrar.exists() and not marker.exists()
        os.kill(proc.pid, signal.SIGINT)
        with release.open("w", encoding="utf-8") as stream:
            stream.write("continue\n")
        assert _w1_wait_for_exit(proc, rundir) == 130
        assert not marker.exists() and not _w1_live_in_group(gate_pid)
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "primary_cancel=130" in evidence
        gate_records = [record for record in _w1_owner_records(rundir)
                        if record["role"] == "gate"]
        assert gate_records and gate_records[-1]["wait_ok"] == "1"
    finally:
        os.close(entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_runner_cancellation_closes_gate_and_does_not_start_workload(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    bash_env, signal_log = _w1_signal_probe(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, registrar_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-4242\n", sleep=300))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    env["BASH_ENV"] = str(bash_env)
    env["W1_SIGNAL_LOG"] = str(signal_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(registrar)
        os.kill(proc.pid, signal.SIGINT)
        rc = proc.wait(timeout=7)
        assert rc == 130
        assert not marker.exists() and not signal_log.exists()
        assert (rundir / "exitcode").read_text().strip() == "130"
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "REGISTER-CANCELLED status=130" in err
        assert "frame=ABORTED v1 reason=release-eof" in err and "wait=0" in err
        assert not _w1_live_in_group(gate_pid)
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_gate_control_is_anonymous_and_registrar_inherits_no_authority_fd(
        tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    fd_report = tmp_path / "registrar-fds"
    write_shim, payload_entered_fd, payload_release = _w1_delay_path_write(
        tmp_path, fd_report)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=1.0))
    env["W1_REGISTRAR_FD_REPORT"] = str(fd_report)
    _w1_prepend_pythonpath(env, write_shim)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        release_pipe = os.readlink("/proc/%d/fd/0" % gate_pid)
        status_pipe = os.readlink("/proc/%d/fd/3" % gate_pid)
        assert release_pipe.startswith("pipe:[")
        assert status_pipe.startswith("pipe:[")
        assert _w1_await_fifo(payload_entered_fd) == "payload-write-opened\n"
        try:
            assert not fd_report.exists(), (
                "registrar fd report became visible before its payload was complete")
        finally:
            _w1_release_fifo(payload_release)
        _w1_wait_for_path(fd_report)
        inherited = fd_report.read_text(encoding="utf-8")
        assert release_pipe not in inherited and status_pipe not in inherited
        rc = proc.wait(timeout=6)
        assert not marker.exists()
        assert not [path for path in rundir.iterdir()
                    if stat.S_ISFIFO(path.stat().st_mode)], (
            "production created a pathname control endpoint")
        assert rc == int(W1_RUNNER_FAILURE_CODE)
    finally:
        os.close(payload_entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_pytest_pid_publish_failure_is_settled_without_partial_target(tmp_path):
    """A failed rename cannot publish partial authority or continue the run."""
    mod = _load()
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    pytest_pid_path = rundir / "pytest.pid"
    bash_env = _w1_fail_shell_publish(tmp_path, pytest_pid_path)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["BASH_ENV"] = str(bash_env)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)

    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "REGISTER-GATE-SETUP-FAILED phase=pytest-pid-publish" in evidence
    assert "context=pytest-pid-publish" in evidence
    assert not pytest_pid_path.exists()
    assert not list(rundir.glob("pytest.pid.tmp.*"))
    assert not marker.exists()
    expected = {W1_SETUP_FAILURE_CODE, W1_RETAINED_FAILURE_CODE}
    assert (rundir / "exitcode").read_text().strip() in expected
    assert result.returncode in {int(code) for code in expected}


@pytest.mark.parametrize(("missing_fd", "missing_pid", "expected"), [
    ("gate_read", None, None),
    ("gate_write", None, None),
    ("terminal_read", None, None),
    ("terminal_write", None, None),
    (None, "gate", 92),
    (None, "relay", 92),
])
def test_partial_coproc_setup_settles_every_acquired_owner(
        tmp_path, missing_fd, missing_pid, expected):
    """94 requires accepted checked waits for both independently saved owners."""
    mod = _load()
    marker = tmp_path / "workload-started"
    wait_log = tmp_path / "gate-waits"
    bash_env = tmp_path / "record-gate-wait.bash"
    bash_env.write_text(
        "wait() {\n"
        "    builtin printf '%s\\n' \"$*\" >> \"$W1_WAIT_LOG\"\n"
        "    builtin wait \"$@\"\n"
        "}\n"
        "export -f wait\n",
        encoding="utf-8",
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, missing_setup_fd=missing_fd,
        missing_setup_pid=missing_pid,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["BASH_ENV"] = str(bash_env)
    env["W1_WAIT_LOG"] = str(wait_log)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
    assert not marker.exists()
    pid_path = (rundir / "injected-gate.pid" if missing_pid is not None
                else rundir / "pytest.pid")
    gate_pid = int(pid_path.read_text().strip())
    waits = (wait_log.read_text(encoding="utf-8").splitlines()
             if wait_log.exists() else [])
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")

    assert "SETUP-OWNER role=gate" in evidence
    assert "SETUP-OWNER role=relay" in evidence
    both_accepted = (
        "SETUP-OWNER role=gate accepted=1" in evidence
        and "SETUP-OWNER role=relay accepted=1" in evidence
    )
    if expected is None:
        expected = 94 if both_accepted else 92
    if missing_pid == "gate":
        assert "SETUP-OWNER role=gate" in evidence and "pid=MISSING" in evidence
    else:
        assert any(str(gate_pid) in line.split() for line in waits), waits
    if expected == 94:
        assert "SETUP-OWNER role=gate" in evidence and "gate accepted=1" in evidence
        assert "SETUP-OWNER role=relay" in evidence and "relay accepted=1" in evidence
        assert not _w1_live_in_group(gate_pid)
    else:
        assert "accepted=0" in evidence or "pid=MISSING" in evidence
    assert result.returncode == expected
    assert (rundir / "exitcode").read_text().strip() == str(expected)


def test_malformed_owner_ready_reaps_its_term_resistant_group(tmp_path):
    """A post-setsid framing failure still owns, stops, waits, and censuses."""
    mod = _load()
    claim = tmp_path / "malformed-owner-claimed"
    descendant = tmp_path / "malformed-owner-descendant"
    marker = tmp_path / "workload-started"
    anchor = "os.setsid()\n"
    assert mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM.count(anchor) == 1
    injection = (
        anchor
        + "claim = %r\n" % str(claim)
        + "try:\n"
        + "    claim_fd = os.open(claim, os.O_WRONLY | os.O_CREAT | "
          "os.O_EXCL, 0o600)\n"
        + "except FileExistsError:\n"
        + "    pass\n"
        + "else:\n"
        + "    os.close(claim_fd)\n"
        + "    import signal\n"
        + "    read_fd, write_fd = os.pipe()\n"
        + "    child = os.fork()\n"
        + "    if child == 0:\n"
        + "        os.close(read_fd)\n"
        + "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        + "        with open(%r, 'w', encoding='ascii') as stream:\n"
          % str(descendant)
        + "            stream.write(str(os.getpid()))\n"
        + "        os.write(write_fd, b'1')\n"
        + "        os.close(write_fd)\n"
        + "        while True:\n"
        + "            signal.pause()\n"
        + "    os.close(write_fd)\n"
        + "    if os.read(read_fd, 1) != b'1':\n"
        + "        raise SystemExit(94)\n"
        + "    os.close(read_fd)\n"
        + "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        + "    frame = ('OWNER-READY v1 pid=%d\\nEXTRA v1\\n' % "
          "os.getpid()).encode('ascii')\n"
        + "    os.write(1, frame)\n"
        + "    os.close(1)\n"
        + "    while True:\n"
        + "        signal.pause()\n"
    )
    timeout_owner = mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM.replace(
        anchor, injection, 1)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, ready_seconds=2, timeout_owner_program=timeout_owner,
    )
    registrar = tmp_path / "registrar-started"
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)

    assert claim.exists() and descendant.exists(), (
        "the malformed post-setsid owner schedule never reached its descendant")
    descendant_pid = int(descendant.read_text(encoding="ascii"))
    records = [record for record in _w1_owner_records(rundir)
               if record["role"] == "ready-reader"]
    assert len(records) == 1, records
    record = records[0]
    assert record["group_ready"] == "1"
    assert record["stop"] == "TERM-GRACE-KILL-GROUP"
    assert record["receipt"].split(":", 1)[0] == record["owner_pid"]
    assert int(record["grace_us"]) > 0
    assert int(record["term_at_us"]) < int(record["kill_at_us"])
    assert record["wait_ok"] == "1" and record["descendants"] == "ABSENT"
    assert not _w1_pid_is_live(descendant_pid)
    assert not _w1_live_in_group(int(record["owner_pid"]))
    assert not registrar.exists() and not marker.exists()
    assert result.returncode in {92, 94}


def test_withheld_owner_ready_reaps_post_setsid_term_resistant_group(tmp_path):
    """READY framing is not the source of already-acquired group ownership."""
    mod = _load()
    parent_receipt_path = tmp_path / "no-ready-owner.receipt"
    child_receipt_path = tmp_path / "no-ready-child.receipt"
    marker = tmp_path / "workload-started"
    entered, release, entered_fd = _w1_fifo_barrier(tmp_path, "no-owner-ready")
    anchor = "os.setsid()\n"
    assert mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM.count(anchor) == 1
    malicious = (
        "import pathlib, signal\n"
        + "def persist_receipt(path):\n"
        + "    raw = pathlib.Path('/proc/self/stat').read_text(encoding='ascii')\n"
        + "    tail = raw.rsplit(') ', 1)[1].split()\n"
        + "    value = '%d:%d:%d:%d:%s' % (os.getpid(), os.getppid(), "
          "os.getpgrp(), os.getsid(0), tail[19])\n"
        + "    pathlib.Path(path).write_text(value, encoding='ascii')\n"
        + "persist_receipt(%r)\n" % str(parent_receipt_path)
        + "read_fd, write_fd = os.pipe()\n"
        + "child = os.fork()\n"
        + "if child == 0:\n"
        + "    os.close(read_fd)\n"
        + "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        + "    persist_receipt(%r)\n" % str(child_receipt_path)
        + "    os.write(write_fd, b'1')\n"
        + "    os.close(write_fd)\n"
        + "    while True:\n"
        + "        signal.pause()\n"
        + "os.close(write_fd)\n"
        + "if os.read(read_fd, 1) != b'1':\n"
        + "    raise SystemExit(94)\n"
        + "os.close(read_fd)\n"
        + "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        + "with open(%r, 'w', encoding='ascii') as stream:\n" % str(entered)
        + "    stream.write('owner-and-child-live\\n')\n"
        + "with open(%r, 'r', encoding='ascii') as stream:\n" % str(release)
        + "    stream.readline()\n"
        + "while True:\n"
        + "    signal.pause()\n"
    )
    # Only the initial READY reader owns this injected schedule.  The generic
    # descendant-census helper must still run the real bootstrap; otherwise
    # the fixture replaces the very observer whose result it is asserting and
    # can only produce UNKNOWN.
    injection = (
        anchor
        + "if stdout_path.endswith('/registration-ready-reader.out'):\n"
        + "".join("    " + line + "\n" for line in malicious.splitlines())
    )
    timeout_owner = mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM.replace(
        anchor, injection, 1)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, ready_seconds=2, timeout_owner_program=timeout_owner,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    parent_receipt = child_receipt = None

    def read_receipt(path):
        values = tuple(int(value) for value in path.read_text(
            encoding="ascii").split(":"))
        assert len(values) == 5
        return values

    try:
        assert _w1_await_fifo(entered_fd, timeout=5) == "owner-and-child-live\n"
        parent_receipt = read_receipt(parent_receipt_path)
        child_receipt = read_receipt(child_receipt_path)
        parent_pid, _parent_ppid, parent_pgid, parent_sid, _parent_start = (
            parent_receipt)
        child_pid, child_ppid, child_pgid, child_sid, _child_start = child_receipt
        assert parent_pid == parent_pgid == parent_sid
        assert child_ppid == parent_pid
        assert child_pgid == parent_pgid and child_sid == parent_sid
        assert _w1_pid_is_live(parent_pid) and _w1_pid_is_live(child_pid)
        with open(release, "w", encoding="ascii") as stream:
            stream.write("release\n")
        try:
            rc = proc.wait(timeout=8)
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                "NO-READY-GROUP-AUTHORITY-DOWNGRADED-TO-BARE-PID") from exc
        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "ready-reader"]
        assert len(records) == 1, records
        record = records[0]
        assert record["group_ready"] == "1", (
            "NO-READY-GROUP-AUTHORITY-DOWNGRADED-TO-BARE-PID", record)
        assert record["stop"] == "TERM-GRACE-KILL-GROUP", (
            "NO-READY-POST-SETSID-DESCENDANT-SURVIVED", record)
        assert record["wait_ok"] == "1" and record["descendants"] == "ABSENT"
        assert record["receipt"] == ":".join(map(str, parent_receipt))
        assert not _w1_pid_is_live(parent_pid)
        assert not _w1_pid_is_live(child_pid), (
            "NO-READY-POST-SETSID-DESCENDANT-SURVIVED")
        assert not marker.exists()
        assert rc in {92, 94}
    finally:
        os.close(entered_fd)
        if child_receipt is not None:
            child_pid, _ppid, parent_pgid, _sid, child_start = child_receipt
            try:
                current = _w1_proc_receipt(child_pid)
            except (FileNotFoundError, ProcessLookupError, ValueError):
                current = None
            if current == (child_pid, parent_pgid, child_start):
                _w1_kill_group(parent_pgid)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))
            proc.kill()
            proc.wait(timeout=5)


def test_a_registrar_that_succeeds_still_gets_waited_for_and_recorded(tmp_path):
    """OVER-SENSITIVITY CONTROL for W1, and it is not optional.

    "Registration failure is terminal" is one bad edit away from "the runner
    stops waiting", which would destroy every sample the hunt exists to take:
    no `exitcode`, no `epoch_end`, no pytest status, every row ABANDONED. So
    the same production template, the same real bash, one difference -- the
    stub registrar returns 0 -- must still reach `wait` and record the
    WORKLOAD's status, not the runner's.
    """
    mod = _load()
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path, (
            "#!/bin/bash\n"
            "touch %s\n"
            "sleep 1\n"
            "exit %d\n" % (shlex.quote(str(marker)), W1_WORKLOAD_CODE)),
        owned_group_census_override="ABSENT")
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-4242\n"))

    proc = subprocess.Popen(["bash", str(script)], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    try:
        rc = proc.wait(timeout=W1_RUNNER_BOUND)
    except subprocess.TimeoutExpired:
        _w1_kill_group(os.getpgid(proc.pid))
        proc.kill()
        proc.wait(timeout=10)
        raise AssertionError(
            "the runner never finished a SUCCESSFUL run within "
            f"{W1_RUNNER_BOUND:.0f}s")
    finally:
        try:
            _w1_kill_group(os.getpgid(proc.pid))
        except (ProcessLookupError, OSError):
            pass

    assert marker.exists(), "the workload never ran; the success path proves nothing"
    assert rc == 0, (
        f"a successful registration made the runner exit {rc}. The runner's "
        "own status must stay 0 on the normal path; the sample's status lives "
        "in $RUNDIR/exitcode.")
    assert (rundir / "jobid").read_text().strip() == "stubhost-4242", (
        "the registrar's job id was not recorded, so the monitor cannot name "
        "the job it launched")
    assert "REGISTER-FAILED" not in (rundir / "jobid").read_text(), (
        "a successful registration was recorded as a failure")
    assert (rundir / "exitcode").read_text().strip() == str(W1_WORKLOAD_CODE), (
        "the runner did not wait for its workload and record the workload's "
        f"own status ({W1_WORKLOAD_CODE}). Making registration failure terminal "
        "must not make the success path stop waiting -- that would empty the "
        "wedge denominator entirely.")
    assert (rundir / "epoch_end").is_file(), (
        "epoch_end is missing on the success path; the sample has no duration")
