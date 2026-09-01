"""Row-212 registration lifecycle, split out of the module that owns them.

WHY A SECOND FILE EXISTS. The sanctioned suite runs `--dist loadfile`, which
hands one FILE to one worker, so a single large module is serial no matter how
many workers there are and is the suite's long pole. Backlog rows 231 and 237
asked whether these tests can run BESIDE their siblings. They can, and the
reason they could not before v3.66.1241 was not group identity: every test
drives its own process group and every census filters on an EXACT pgid, and a
pgid is a live pid, so two concurrently live groups cannot share one. What
blocked the split was that an owner OBSERVATION took its bound from what
remained of the FORWARD deadline, so the extra concurrency the split creates
turned correct observations into UNKNOWN. That is fixed in the product, and
this file is the split it was blocking.

WHAT STAYED BEHIND, deliberately. All helpers, the runner fixture, the frozen
budget table and every source-scanning gate remain in
`test_v3_66_1132_the_hunt_reaps_what_it_abandons.py`, and this file imports
them by name. `import *` would drag that module's own `test_` functions into
this namespace and collect them twice. The autouse budget-boundary fixture is
imported explicitly for the same reason: without it every self-policing
assertion below would be decoration, and nothing else in this file names it.
"""
import os
import select
import shlex
import signal
import stat
import subprocess
import time

import pytest

from test_v3_66_1132_the_hunt_reaps_what_it_abandons import (
    # AUTOUSE, AND SO REFERENCED BY NAME NOWHERE. Importing it is what
    # keeps `_w1_police` watching the waits below; a fixture that is not
    # in this namespace simply does not run here.
    W1_REGISTER_FAILURE_CODE,
    W1_RELEASE_FAILURE_CODE,
    W1_RETAINED_FAILURE_CODE,
    W1_RUNNER_FAILURE_CODE,
    W1_WORKLOAD_CODE,
    _W1_SLOW_SETTLEMENT_S,
    _load,
    _w1_adversarial_gate_program,
    _w1_await_fifo,
    _w1_budget_boundary,
    _W1_POLICED,
    _w1_budget_s,
    _w1_build_runner,
    _w1_cancel_a_registrar_bound_runner,
    _w1_checked_child_wait_barrier,
    _w1_delay_path_write,
    _w1_fake_home,
    _w1_fifo_barrier,
    _w1_kill_group,
    _w1_live_in_group,
    _w1_owner_records,
    _w1_pid_is_live,
    _w1_prepend_pythonpath,
    _w1_proc_observation,
    _w1_process_probe_drift,
    _w1_readlink_when_installed,
    _w1_release_fifo,
    _w1_signal_probe,
    _w1_slow_settlement_gate_program,
    _w1_terminal_reader_barrier,
    _w1_wait_for_exit,
    _w1_wait_for_exit_or_forbidden_checked_wait,
    _w1_wait_for_gate,
    _w1_wait_for_path,
)

# Its subject is one tool's runtime behaviour, not the tree.
BD_GATE_SCOPE = "module"


# EVERY SPAWN OF THE BUILT RUNNER PINS ITS WORKING DIRECTORY. The production
# RUNNER template shells out to `git rev-parse --short HEAD`, so a spawn that
# inherits the pytest worker's directory is asserting over ambient state -- the
# exact isolation A7 names alongside HOME, TMPDIR and module globals. One band
# spawn returned rc=93 with `fatal: not a git repository`; that event is
# recorded as an open flake on row 241 and is NOT claimed to be fixed here.
# What is fixed is that the directory is now DETERMINISTIC instead of inherited.
_W1_SPAWN_CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
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
        rc = proc.wait(timeout=_w1_budget_s("failed_registration_never_releases_the_workload/wait"))
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
            proc.wait(timeout=_w1_budget_s("failed_registration_never_releases_the_workload/wait-2"))
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
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    pgid = -1
    try:
        pgid, _ = _w1_wait_for_gate(rundir)
        rc = proc.wait(timeout=_w1_budget_s("registration_release_failure_is_bounded_and_classified/wait"))
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
            proc.wait(timeout=_w1_budget_s("registration_release_failure_is_bounded_and_classified/wait-2"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(disappeared_fd, site="registration_failure_never_signals_after_original_child_disappears/fifo") == (
            "gate-ready-to-disappear\n")
        _w1_wait_for_path(registrar_marker)
        with release_disappearance.open("w", encoding="ascii") as stream:
            stream.write("disappear\n")
        rc = proc.wait(timeout=_w1_budget_s("registration_failure_never_signals_after_original_child_disappears/wait"))
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
            proc.wait(timeout=_w1_budget_s("registration_failure_never_signals_after_original_child_disappears/wait-2"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("gate_receipt_admission_rejects_wrong_provenance/run"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        rc = proc.wait(timeout=_w1_budget_s("registration_receipt_drift_before_go_refuses_release/wait"))
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
            proc.wait(timeout=_w1_budget_s("registration_receipt_drift_before_go_refuses_release/wait-2"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("registrar_success_requires_one_exact_job_id_before_release/run"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        rc = _w1_wait_for_exit(proc, rundir, site="release_write_error_is_resolved_only_by_gate_status/exit")
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
            proc.communicate(timeout=_w1_budget_s("release_write_error_is_resolved_only_by_gate_status/communicate"))
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
        reap_seconds=3, cleanup_seconds=3,
        before_release_write_barrier=(entered, release),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    calls_path = tmp_path / "bd-jobs-calls"
    env["W1_STUB_ARGV_LOG"] = str(calls_path)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    gate_pidfd = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, site="real_release_sigpipe_is_contained_and_enters_registered_failure/fifo") == "release-write-entered\n"
        gate_pidfd = os.pidfd_open(gate_pid)
        os.kill(gate_pid, signal.SIGKILL)
        readable, _, _ = select.select([gate_pidfd], [], [], 5)
        assert readable == [gate_pidfd], (
            "the exact release peer did not reach a terminal state")
        with release.open("w", encoding="ascii") as stream:
            stream.write("release\n")

        returncode = proc.wait(timeout=_w1_budget_s("real_release_sigpipe_is_contained_and_enters_registered_failure/wait"))
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
            proc.wait(timeout=_w1_budget_s("real_release_sigpipe_is_contained_and_enters_registered_failure/wait-2"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("partial_or_duplicate_release_frame_never_execs/run"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("gate_exec_failure_is_named_registered_handoff_failure/run"))
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
        terminal="EXEC-OK v1", hold=300, status=0)
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        rc = proc.wait(timeout=_w1_budget_s("invalid_exec_ok_reconciles_registered_id_without_waiting_live_group/wait"))
        live_gate = _w1_live_in_group(gate_pid)
        assert str(gate_pid) in live_gate, (
            "reconciliation did not return while the hostile registered "
            "gate was still live: %r" % live_gate)
        calls = argv_log.read_text(encoding="utf-8").splitlines()
        assert len(calls) == 2 and calls[0].startswith("register ")
        assert calls[1] == "reap --id stubhost-4242"
        assert not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        assert rc == int(W1_RELEASE_FAILURE_CODE)
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("invalid_exec_ok_reconciles_registered_id_without_waiting_live_group/wait-2"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        assert _w1_await_fifo(entered_fd, site="reconciliation_term_resistance_stays_inside_total_budget/fifo") == "reap-entered\n"
        reap_pid = int(reap_pid_path.read_text().strip())
        reap_child_pid = int(reap_child_path.read_text().strip())
        assert _w1_pid_is_live(reap_pid) and _w1_pid_is_live(reap_child_pid)
        try:
            rc = proc.wait(timeout=_w1_budget_s("reconciliation_term_resistance_stays_inside_total_budget/wait"))
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
            proc.wait(timeout=_w1_budget_s("reconciliation_term_resistance_stays_inside_total_budget/wait-2"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, site="cancellation_during_reconciliation_retains_primary_and_reaps_owner/fifo") == "reap-entered\n"
        reap_pid = int(reap_pid_path.read_text().strip())
        reap_child_pid = int(reap_child_path.read_text().strip())
        assert _w1_pid_is_live(reap_pid) and _w1_pid_is_live(reap_child_pid)
        os.kill(proc.pid, signal.SIGINT)
        assert proc.wait(timeout=_w1_budget_s("cancellation_during_reconciliation_retains_primary_and_reaps_owner/wait")) == int(W1_RELEASE_FAILURE_CODE)
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
            proc.wait(timeout=_w1_budget_s("cancellation_during_reconciliation_retains_primary_and_reaps_owner/wait-2"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("cooperative_registered_cancellation_returns_primary_status/run"))

    assert not marker.exists()
    calls = argv_log.read_text(encoding="utf-8").splitlines()
    assert calls.count("reap --id stubhost-4242") == 1, calls
    assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "primary_cancel=130" in evidence
    assert "reconcile=0" in evidence and "gate_settled=1" in evidence
    assert result.returncode == 130
    assert (rundir / "exitcode").read_text().strip() == "130"
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("post_ready_protocol_budget_is_positive_and_reaches_terminal_frame/run"))
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
        reap_seconds=9, forward_expiry_is_subject=True,
        gate_program=gate_program,
        checked_wait_probe=checked_wait_log,
        handoff_deadline_probe=deadline_probe,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, site="partial_handoff_frame_does_not_restart_the_protocol_budget/fifo") == (
            "partial-terminal-written\n")
        deadline_rows = deadline_probe.read_text(encoding="ascii").splitlines()
        assert len(deadline_rows) == 2, deadline_rows
        pre_deadline = int(deadline_rows[0].removeprefix("pre="))
        post_deadline = int(deadline_rows[1].removeprefix("post="))
        assert post_deadline == pre_deadline, (
            "N225-PARTIAL-FRAME-RESTARTED-TOTAL-BUDGET",
            pre_deadline, post_deadline)
        rc = _w1_wait_for_exit_or_forbidden_checked_wait(
            proc, rundir, checked_wait_log, site="partial_handoff_frame_does_not_restart_the_protocol_budget/exit")
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
                proc.wait(timeout=_w1_budget_s("partial_handoff_frame_does_not_restart_the_protocol_budget/wait"))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=_w1_budget_s("partial_handoff_frame_does_not_restart_the_protocol_budget/wait-2"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        rc = _w1_wait_for_exit(proc, rundir, site="handoff_eof_is_not_exec_success/exit")
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
            proc.communicate(timeout=_w1_budget_s("handoff_eof_is_not_exec_success/communicate"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, site="cancellation_during_terminal_reader_reconciles_exact_id_once/fifo") == (
            "terminal-reader-entered\n")
        assert registrar.exists() and not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        with release.open("w", encoding="utf-8") as stream:
            # Hold the exact release peer open before cancellation can tear
            # the terminal reader down; otherwise this open can block forever.
            os.kill(proc.pid, signal.SIGINT)
            stream.write("continue\n")
        assert not marker.exists()
        rc = proc.wait(timeout=_w1_budget_s("cancellation_during_terminal_reader_reconciles_exact_id_once/wait"))
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
            proc.wait(timeout=_w1_budget_s("cancellation_during_terminal_reader_reconciles_exact_id_once/wait-2"))
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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, site="cancellation_during_terminal_relay_wait_reconciles_once/fifo") == (
            "terminal-relay-wait-entered\n")
        assert registrar.exists() and not marker.exists()
        with release.open("w", encoding="utf-8") as stream:
            os.kill(proc.pid, signal.SIGINT)
            stream.write("continue\n")
        assert not marker.exists()
        rc = proc.wait(timeout=_w1_budget_s("cancellation_during_terminal_relay_wait_reconciles_once/wait"))
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
            proc.wait(timeout=_w1_budget_s("cancellation_during_terminal_relay_wait_reconciles_once/wait-2"))
def test_a_slow_settlement_never_downgrades_a_decided_cancellation(tmp_path):
    """ROW 231, PART C. A SIGINT that was received is 130, not 92.

    THE DEFECT, as v3.66.1226 recorded it and left open. `W1_GATE_SECONDS` was
    ONE constant doing TWO jobs: bounding the FORWARD gate protocol, which a
    test may legitimately drive small because its expiry is the subject, and
    bounding every SETTLEMENT path, which must not expire on correct work.
    `reap_seconds=3` therefore gave settlement three seconds as a SIDE EFFECT,
    and `registration_settle_cancel` reaches `registration_finish
    "$W1_CANCEL_STATUS"` only when the terminal reached EOF, the relay was
    waited for, and the gate wait succeeded. Miss the deadline and the runner
    reports 92 -- RETAINED UNCERTAINTY -- for a cancellation it classified
    exactly. One key describing two costs is the v3.66.1222 defect; this is
    that defect in the product rather than in a test budget.

    RED ON THE PARENT, WITHOUT A STOPWATCH IN THE ASSERTION. The planted gate
    settles correctly after `_W1_SLOW_SETTLEMENT_S`, which is above the three
    seconds the shared constant left and below the ten the split gives. At
    2a2fc85 this returns 92; here it must return 130.
    """
    mod = _load()
    settled = tmp_path / "gate-settlement-entered"
    marker, registrar, rundir, proc = _w1_cancel_a_registrar_bound_runner(
        tmp_path, mod,
        gate_program=_w1_slow_settlement_gate_program(
            settled, _W1_SLOW_SETTLEMENT_S))
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(registrar)
        assert not settled.exists(), (
            "precondition: the gate has not begun settling before the signal")
        started = time.monotonic()
        os.kill(proc.pid, signal.SIGINT)
        rc = proc.wait(timeout=_w1_budget_s(
            "a_slow_settlement_never_downgrades_a_decided_cancellation/wait"))
        elapsed = time.monotonic() - started

        # PRECONDITIONS BEFORE THE VERDICT. A gate that never reached the
        # delay would settle instantly and pass for the wrong reason.
        assert settled.is_file(), (
            "the planted gate never entered its settlement delay, so this run "
            "says nothing about the settlement deadline")
        assert not (rundir / "jobid").exists(), (
            "precondition: no id was recorded, so settle_cancel and not "
            "fail_registered is the path under test")
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "REGISTER-CANCELLED status=130" in evidence, evidence
        assert "primary_cancel=130" in evidence, evidence

        # THE DISTINCTIVE VERDICT COMES FIRST. A later precondition that fires
        # instead would launder the very failure this test exists to report --
        # on the parent the runner ABANDONS settlement early, so an
        # "it did not wait long enough" assertion placed above this one would
        # be the message the run produced. A5: assert the distinctive
        # diagnostic, and let the remaining conditions pass afterwards.
        assert rc == 130, (
            "a received SIGINT was classified exactly and then downgraded to "
            "%d after %.2fs, because settlement ran out of a deadline it "
            "never should have shared with the forward gate protocol"
            % (rc, elapsed))
        assert (rundir / "exitcode").read_text().strip() == "130"
        assert elapsed >= _W1_SLOW_SETTLEMENT_S, (
            "the runner reported 130 in %.2fs, before the planted %.1fs "
            "settlement could have completed, so the verdict above was not "
            "earned by settling" % (elapsed, _W1_SLOW_SETTLEMENT_S))
        assert not marker.exists()
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s(
                "a_slow_settlement_never_downgrades_a_decided_cancellation/wait-2"))
def test_gate_control_is_anonymous_and_registrar_inherits_no_authority_fd(
        tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    fd_report = tmp_path / "registrar-fds"
    authority_fd_report = tmp_path / "runner-authority-fds"
    fixed_fd_control = tmp_path / "host-held-fd3"
    fixed_fd_control.mkdir()
    gate_entered, gate_release, gate_entered_fd = _w1_fifo_barrier(
        tmp_path, "gate-fixed-fd")
    gate_prelude = (
        "exec 3<%s\n"
        "builtin printf 'fixed-fd-installed\\n' > %s\n"
        "IFS= read -r W1_TEST_GATE_FD_RELEASE < %s\n" % (
            shlex.quote(str(fixed_fd_control)),
            shlex.quote(str(gate_entered)),
            shlex.quote(str(gate_release)),
        )
    )
    write_shim, payload_entered_fd, payload_release = _w1_delay_path_write(
        tmp_path, fd_report)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        gate_prelude=gate_prelude,
        authority_fd_report=authority_fd_report,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=1.0))
    env["W1_REGISTRAR_FD_REPORT"] = str(fd_report)
    _w1_prepend_pythonpath(env, write_shim)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    gate_released = False
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(
            gate_entered_fd,
            site="gate_control_is_anonymous_and_registrar_inherits_no_authority_fd/fifo",
        ) == "fixed-fd-installed\n"
        fixed_gate_fd = _w1_readlink_when_installed(gate_pid, 3)
        assert fixed_gate_fd == str(fixed_fd_control)
        assert not fixed_gate_fd.startswith("pipe:["), (
            "negative control failed: fd 3 accidentally names a pipe, so the "
            "old fixed-number assertion would not be disproved")

        _w1_release_fifo(gate_release)
        gate_released = True
        _w1_wait_for_path(authority_fd_report)
        authority_rows = authority_fd_report.read_text(
            encoding="ascii").splitlines()
        assert len(authority_rows) == 2, (
            "the runner did not record exactly both authority descriptors: %r"
            % (authority_rows,))
        parsed_rows = [row.split("=", 1) for row in authority_rows]
        assert all(len(row) == 2 and row[1].isdigit() for row in parsed_rows), (
            "the recorded authority descriptors are malformed: %r"
            % (authority_rows,))
        authority_fds = {name: int(fd) for name, fd in parsed_rows}
        assert set(authority_fds) == {"release", "status"}
        assert len(set(authority_fds.values())) == 2
        assert all(fd >= 0 for fd in authority_fds.values())

        release_pipe = _w1_readlink_when_installed(
            proc.pid, authority_fds["release"])
        status_pipe = _w1_readlink_when_installed(
            proc.pid, authority_fds["status"])
        assert release_pipe.startswith("pipe:["), release_pipe
        assert status_pipe.startswith("pipe:["), status_pipe
        assert release_pipe != status_pipe
        assert _w1_await_fifo(payload_entered_fd, site="gate_control_is_anonymous_and_registrar_inherits_no_authority_fd/fifo") == "payload-write-opened\n"
        try:
            assert not fd_report.exists(), (
                "registrar fd report became visible before its payload was complete")
        finally:
            _w1_release_fifo(payload_release)
        _w1_wait_for_path(fd_report)
        inherited = fd_report.read_text(encoding="utf-8")
        assert release_pipe not in inherited and status_pipe not in inherited
        assert str(fixed_fd_control) not in inherited
        rc = proc.wait(timeout=_w1_budget_s("gate_control_is_anonymous_and_registrar_inherits_no_authority_fd/wait"))
        assert not marker.exists()
        assert not [path for path in rundir.iterdir()
                    if stat.S_ISFIFO(path.stat().st_mode)], (
            "production created a pathname control endpoint")
        assert rc == int(W1_RUNNER_FAILURE_CODE)
    finally:
        if not gate_released:
            _w1_release_fifo(gate_release)
        os.close(gate_entered_fd)
        os.close(payload_entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("gate_control_is_anonymous_and_registrar_inherits_no_authority_fd/wait-2"))


def test_host_fd_transform_control_imports_without_judging_authority_fds():
    """A valid helper transform must escape this non-behavioural control."""
    assert callable(_w1_build_runner)


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
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("partial_coproc_setup_settles_every_acquired_owner/run"))
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


def test_the_budget_boundary_followed_the_tests_into_this_file():
    """PRECONDITION for every self-policing assertion in this file.

    `_w1_budget_boundary` is AUTOUSE, so nothing here names it and the split
    could have left it behind without a single test changing colour. The
    instrument is asked at the RESOURCE BOUNDARY rather than read out of the
    import list: a real subprocess is waited for through a real derived budget
    and the shared policing log must have grown by exactly one entry.
    """
    before = len(_W1_POLICED)
    proc = subprocess.Popen(["bash", "-c", "sleep 0.05"])
    assert proc.wait(timeout=_w1_budget_s(
        "the_budget_boundary_followed_the_tests_into_this_file/wait")) == 0
    assert len(_W1_POLICED) == before + 1, (
        "the wait boundary is not instrumented in this file, so every budget "
        "the tests above declare is a number nothing checks (%d -> %d)"
        % (before, len(_W1_POLICED)))
    assert _W1_POLICED[-1] == (
        "the_budget_boundary_followed_the_tests_into_this_file/wait")
