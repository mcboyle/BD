"""O1698 retire-adapter-timeout: the dispatch adapter's retire must outlive run()'s 30 s cap.

bd-retire-seat.sh waits up to 300 s for the seat's handoff before it kills; the adapter ran it under a 30 s
subprocess cap, so the wait was killed and the seat stayed live (A7 on .183, D2). Hermetic: the "seat" is a
sleep process owned by the test and the retire helper is a stub; no tmux session or real seat is touched.

RED: point BD_O1698_RETIRE_ADAPTER_TIMEOUT_CANDIDATE at operations.py.pre (the live byte copy).
"""
import ast
import datetime
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_RETIRE_ADAPTER_TIMEOUT_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

SLOW = 35  # > the adapter's 30 s subprocess cap
NEW_NAMES = {"retire", "retire_detached", "retire_receipts", "RETIRE_GRACE", "_RETIRE_FINISH"}


def pre_path():
    return Path(os.environ.get("BD_O1698_RETIRE_ADAPTER_TIMEOUT_PRE", "") or Path(CANDIDATE).with_name("operations.py.pre"))


@pytest.fixture
def env(tmp_path):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
    clean.update(BD_PERSIST=str(tmp_path / "persist"), BD_HARNESS=str(tmp_path / "harness"),
                 BD_RETIRE_RECEIPTS=str(tmp_path / "receipts"), PYTHONDONTWRITEBYTECODE="1", LC_ALL="C")
    return clean


@pytest.fixture
def seat():
    proc = subprocess.Popen(["sleep", "600"], start_new_session=True)
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def stub(tmp_path, body):
    path = tmp_path / "bd-retire-seat-stub.sh"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def handoff(tmp_path, seat_name="fixture-seat"):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = tmp_path / "handoff.md"
    path.write_text(f"SEAT: {seat_name}\nSTATE: idle\nSTAGED: none\nRECEIPT: fixture\nREMAINING: none\n"
                    f"LESSONS: fixture lesson\nWRITTEN-AT: {now}\nBACKGROUND: none\nPRECUT: none\n")
    return path


def adapter(env, *args, timeout=90):
    """Invoke the adapter the way dispatch does: a capturing subprocess with the old 30 s budget in mind."""
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, CANDIDATE, "bd-retire-seat", *args], env=env, text=True,
                       capture_output=True, timeout=timeout)
    return p, time.monotonic() - t0


def wait_final(receipt, deadline):
    while time.monotonic() < deadline:
        text = receipt.read_text()
        if re.search(r"^FINAL: ", text, re.M):
            return text
        time.sleep(0.5)
    return receipt.read_text()


def started_receipt(stdout):
    m = re.match(r"STARTED (\S+) pid=\d+ ", stdout)
    return Path(m[1]) if m else None


def test_slow_retire_finishes_detached_with_receipt(tmp_path, env, seat):
    env["BD_RETIRE_HELPER"] = str(stub(tmp_path, f'sleep {SLOW}\nkill "$FIXTURE_SEAT_PID" && echo "RETIRED $1 killed=yes"\n'))
    env["FIXTURE_SEAT_PID"] = str(seat.pid)
    t0 = time.monotonic()
    p, elapsed = adapter(env, "fixture-seat", "fixture reason", "--handoff", str(handoff(tmp_path)))
    receipt = started_receipt(p.stdout) if p.returncode == 0 else None
    text = wait_final(receipt, t0 + SLOW + 30) if receipt else ""
    if not receipt:  # .pre: give the stub's kill time to happen, so a live seat really means never retired
        time.sleep(max(0.0, t0 + SLOW + 3 - time.monotonic()))
    alive = seat.poll() is None
    diag = f"RETIRE-TIMEOUT-RED rc={p.returncode} elapsed={elapsed:.1f}s seat_alive={alive} stdout={p.stdout!r} stderr={p.stderr!r}"
    assert receipt is not None and elapsed < 30, diag
    assert re.search(r"^FINAL: rc=0 status=OK ", text, re.M), f"{diag} receipt={text!r}"
    assert "RETIRED fixture-seat killed=yes" in text, text
    assert not alive, diag


def test_slow_helper_failure_lands_in_receipt_never_ok(tmp_path, env, seat):
    env["BD_RETIRE_HELPER"] = str(stub(tmp_path, 'sleep 3\necho "REMOTE-UNREACHABLE $1 spare12 -- fixture (seat NOT retired)"\nexit 6\n'))
    env["BD_RETIRE_GRACE"] = "1"
    t0 = time.monotonic()
    p, _ = adapter(env, "fixture-seat", "--handoff", str(handoff(tmp_path)))
    receipt = started_receipt(p.stdout)
    assert p.returncode == 0 and receipt, f"rc={p.returncode} stdout={p.stdout!r} stderr={p.stderr!r}"
    text = wait_final(receipt, t0 + 30)
    assert re.search(r"^FINAL: rc=6 status=FAIL ", text, re.M), text
    assert "status=OK" not in text and "REMOTE-UNREACHABLE fixture-seat" in text, text
    assert seat.poll() is None


@pytest.mark.parametrize("rc", [3, 5, 7])
def test_fast_helper_refusal_keeps_its_exit(tmp_path, env, rc):
    env["BD_RETIRE_HELPER"] = str(stub(tmp_path, f'echo "REFUSED: $1 fixture rc{rc}" >&2\nexit {rc}\n'))
    p, elapsed = adapter(env, "fixture-seat", "--handoff", str(handoff(tmp_path)))
    assert p.returncode == 2 and elapsed < 30, (p.returncode, p.stdout, p.stderr)
    assert f"REFUSED: helper rc={rc}: REFUSED: fixture-seat fixture rc{rc}" in p.stderr, p.stderr


def test_fast_helper_success_prints_helper_stdout(tmp_path, env):
    env["BD_RETIRE_HELPER"] = str(stub(tmp_path, 'echo "RETIRED $1 role=worker handoff=yes killed=yes relaunch=skipped"\n'))
    p, _ = adapter(env, "fixture-seat", "--handoff", str(handoff(tmp_path)))
    assert (p.returncode, p.stdout, p.stderr) == (0, "RETIRED fixture-seat role=worker handoff=yes killed=yes relaunch=skipped\n", "")


@pytest.mark.parametrize("args,expect_rc,expect", [
    (["fixture-seat", "--dry-run"], 0, "DRY-RUN: lessons before STOP; fresh handoff required for fixture-seat"),
    (["fixture-seat"], 2, "REFUSED: handoff required; collect lessons before retirement"),
])
def test_control_validation_unchanged(tmp_path, env, args, expect_rc, expect):
    env["BD_RETIRE_HELPER"] = str(stub(tmp_path, "echo SHOULD-NOT-RUN\n"))
    p, _ = adapter(env, *args)
    assert p.returncode == expect_rc and expect in (p.stdout + p.stderr), (p.returncode, p.stdout, p.stderr)
    assert "SHOULD-NOT-RUN" not in p.stdout + p.stderr
    assert not (Path(env["BD_RETIRE_RECEIPTS"])).exists()


def test_control_other_verbs_byte_unchanged():
    def top(path):
        tree = ast.parse(Path(path).read_text())
        out = {}
        for node in tree.body:
            names = [node.name] if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else \
                [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]
            key = ",".join(names) or ast.dump(node)
            if not NEW_NAMES & set(names):
                out[key] = ast.dump(node)
        return out
    pre, cand = top(pre_path()), top(CANDIDATE)
    assert pre == cand, sorted(set(pre.items()) ^ set(cand.items()))[:3]
    assert "timeout=30" in Path(CANDIDATE).read_text()  # run()'s cap for every other verb is untouched
