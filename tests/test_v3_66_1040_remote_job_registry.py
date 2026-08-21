"""`bd-jobs` must never kill a process it cannot prove is the one it registered.

THE INCIDENT (15.88): a sampler launched over ssh outlived its killed local task
by 88 minutes, kept spawning rounds, and its `.pyc` writes broke a deploy at
step 9 -- which had already stopped the unit, leaving test4's service down.
Nothing recorded the work existed, so nothing could find it.

THE HAZARD THE TOOL ITSELF INTRODUCES, which is what most of this file is about.
A reaper that kills by pid, across three hosts, with passwordless root
available, is a loaded weapon pointed at whatever happens to hold that number
next. Pids are recycled. A registry entry that outlives its process and is then
acted on is not a stale record -- it is an instruction to kill a stranger.

So the guard is the subject: every entry carries the process start time from
`/proc/<pid>/stat` field 22, and anything that cannot match it is REFUSED
rather than killed. The tests below try to get the tool to kill the wrong thing.
"""
import ast
import inspect
import json
import os
import pathlib
import re
import shlex
import stat
import subprocess
import sys
import time

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-jobs"


def _load(name="bd_jobs", path=None):
    """Import an extensionless tool file as a module."""
    import importlib.util
    target = str(path or _TOOL)
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, target))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_REGISTRY_LITERAL = 'JOBS_DIR = pathlib.Path("/tmp/bd-jobs")'


def _private_tool(tmp_path, registry=None):
    """A private, executable COPY of `bd-jobs` whose registry is baked in.

    ISOLATION FOLLOWS THE EXECUTABLE, not an ambient value. Rebinding
    `mod.JOBS_DIR` isolates one process and nothing it launches: `_gate_argv`
    resolves `__file__`, so the gate child fresh-imports whatever file the
    module came from, and a delegated target resolved back to the original
    would do the same. MEASURED at v3.66.1206: under a mutated `_remote_self`,
    three delegated launches registered in the LIVE `/tmp/bd-jobs` on this host
    and their process groups had to be killed by hand.

    An environment switch was tried and rejected by design audit -- it would
    have created a second registry that `bd-fleet` cannot see and `bd-gc` does
    not protect, and a real delegated target would never receive it. A copy
    needs no propagation at all: every child of this file, and every target
    pointed at it, is already inside the namespace the test owns and can
    delete.

    The substitution is anchored and counted, because a copy that silently kept
    the production path would run every test against the real registry while
    looking isolated.
    """
    registry = pathlib.Path(registry or (tmp_path / "bd-jobs"))
    source = _TOOL.read_text(encoding="utf-8")
    assert source.count(_REGISTRY_LITERAL) == 1, (
        "the registry literal this harness rewrites no longer occurs exactly "
        "once in %s -- the private copy would silently keep /tmp/bd-jobs"
        % _TOOL)
    tmp_path = pathlib.Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    copy = tmp_path / "bd-jobs-private"
    copy.write_text(
        source.replace(_REGISTRY_LITERAL,
                       'JOBS_DIR = pathlib.Path(%r)' % str(registry)),
        encoding="utf-8")
    copy.chmod(0o755)
    assert str(registry) in copy.read_text(encoding="utf-8")
    return copy, registry


@pytest.fixture
def jobs(tmp_path):
    """The tool under test, loaded from a private copy that owns its registry.

    `JOBS_DIR` is the same `tmp_path/bd-jobs` this fixture has always used, so
    every existing node is unchanged -- but now the gate wrapper and any
    delegated target this file executes resolve the COPY, and land in that same
    private directory without an environment variable in sight.
    """
    copy, registry = _private_tool(tmp_path)
    mod = _load(path=copy)
    assert mod.JOBS_DIR == registry, (mod.JOBS_DIR, registry)
    return mod


@pytest.fixture
def sleeper():
    procs = []

    def start():
        p = subprocess.Popen(["sleep", "60"])
        procs.append(p)
        return p

    yield start
    for p in procs:
        p.kill()
        p.wait(timeout=10)


def test_the_selftest_passes_and_says_so(tmp_path):
    """An exit code with no verdict behind it is the shape this repo keeps
    finding, which is why test_toolchain_534 requires the words."""
    r = subprocess.run([sys.executable, str(_TOOL), "--selftest"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELFTEST PASS" in r.stdout + r.stderr


def test_a_registered_job_round_trips(jobs, sleeper):
    p = sleeper()
    entry = jobs.register(p.pid, "unit test", "sleep 60")
    assert entry["pid"] == p.pid
    assert entry["starttime"] is not None
    loaded = jobs.load_all()
    assert [e["id"] for e in loaded] == [entry["id"]]
    assert jobs.alive(loaded[0])


def test_a_finished_job_is_not_alive(jobs, sleeper):
    p = sleeper()
    entry = jobs.register(p.pid, "unit test", "sleep 60")
    p.kill()
    p.wait(timeout=10)
    assert not jobs.alive(entry)


def test_a_recycled_pid_is_NOT_treated_as_the_registered_job(jobs, sleeper):
    """THE GUARD. The pid is real and running; only the start time disagrees.

    This is what a recycled pid looks like from the registry's point of view,
    and without the start-time check the tool would kill it. Forced by
    tampering rather than by waiting for the kernel to actually recycle a pid,
    which is not something a test can arrange -- but the code path taken is the
    same one, and it is the only path that matters.
    """
    p = sleeper()
    entry = jobs.register(p.pid, "unit test", "sleep 60")
    assert jobs.alive(entry), "precondition: the real entry must be alive"

    impostor = dict(entry, starttime=entry["starttime"] + 1)
    assert not jobs.alive(impostor), (
        "an entry whose start time does not match the running process was "
        "treated as alive -- bd-jobs would kill whatever now holds that pid")


def test_reap_grades_an_entry_with_no_start_time_unknown_and_keeps_it(
        jobs, sleeper, capsys):
    """A registry written by an older version carries no start time. That is
    not a reason to kill on the strength of a bare pid -- it is a reason to
    refuse and say why."""
    p = sleeper()
    entry = jobs.register(p.pid, "unit test", "sleep 60")
    path = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    stripped = dict(entry)
    stripped.pop("starttime")
    stripped.pop("_path", None)
    path.write_text(json.dumps(stripped), encoding="utf-8")
    before = path.read_bytes()

    rc = jobs.cmd_reap(type("A", (), {"id": None})())
    err = capsys.readouterr().err
    assert rc == 4, "an incomplete kill instruction must grade UNKNOWN"
    assert "UNREADABLE" in err and "starttime" in err and "KEPT" in err, err
    assert p.poll() is None, "bd-jobs killed a process it could not identify"
    assert path.read_bytes() == before, (
        "the incomplete record was changed or dropped even though its process "
        "is still running")


def test_reap_kills_a_job_it_can_prove_and_forgets_it(jobs, sleeper):
    p = sleeper()
    jobs.register(p.pid, "unit test", "sleep 60")
    rc = jobs.cmd_reap(type("A", (), {"id": None})())
    assert rc == 0
    for _ in range(100):
        if p.poll() is not None:
            break
        time.sleep(0.05)
    assert p.poll() is not None, "reap reported success without killing anything"
    assert jobs.load_all() == [], "the entry survived its own reaping"


def test_reap_forgets_a_stale_entry_without_killing_anything(jobs, sleeper):
    """A dead job's entry is litter, not a target."""
    p = sleeper()
    jobs.register(p.pid, "unit test", "sleep 60")
    p.kill()
    p.wait(timeout=10)
    other = sleeper()                      # must survive: it is not registered
    rc = jobs.cmd_reap(type("A", (), {"id": None})())
    assert rc == 0
    assert jobs.load_all() == []
    assert other.poll() is None, "reap killed an unregistered bystander"


def test_orphans_reports_but_never_kills(jobs, capsys):
    """An unregistered run is more likely the operator's than an agent's, so
    the tool that finds it must not be the tool that ends it."""
    src = _TOOL.read_text(encoding="utf-8")
    start = src.index("def cmd_orphans")
    body = src[start:src.index("\ndef ", start + 1)]
    assert "os.kill" not in body and "kill(" not in body, (
        "cmd_orphans can kill; it is a report, and the caller decides")
    assert "-m pytest" in body


def test_the_starttime_parser_survives_a_comm_containing_spaces(jobs, tmp_path):
    """`/proc/<pid>/stat` field 2 is the executable name IN PARENTHESES and may
    contain spaces or brackets. Splitting the line on whitespace shifts every
    later field, so the guard compares the WRONG NUMBER -- and still returns an
    int, so it looks like it worked.

    A mutant swapping the parse for `raw.split()[21]` escaped until this test
    existed, because every process in the earlier cases had a space-free comm
    and the two parses agreed. The subject only appears when the name has a
    space in it, so the test makes one: a copy of /bin/sleep called
    "weird (name". comm is the executable basename, truncated to 15 chars.
    """
    assert jobs.proc_starttime(os.getpid()) is not None
    assert jobs.proc_starttime(2 ** 30) is None, "a dead pid must read as None"

    weird = tmp_path / "weird (name"
    weird.write_bytes(pathlib.Path("/bin/sleep").read_bytes())
    weird.chmod(0o755)
    proc = subprocess.Popen([str(weird), "60"])
    try:
        raw = pathlib.Path("/proc", str(proc.pid), "stat").read_text()
        assert " " in raw[raw.index("(") + 1:raw.rindex(")")], (
            "the harness failed to produce a comm containing a space, so this "
            "test cannot tell the two parses apart: %s" % raw[:80])

        ours = jobs.proc_starttime(proc.pid)
        naive = int(raw.split()[21])
        assert ours is not None
        assert ours != naive, (
            "proc_starttime agrees with a naive whitespace split (%s), so it is "
            "reading a field shifted by the spaces in the process name -- the "
            "reuse guard would compare the wrong number and still look correct"
            % ours)
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_a_recycled_pid_is_dropped_without_killing_the_stranger(jobs, sleeper):
    """The third outcome. The pid is live but belongs to someone else now: the
    record is stale and must go, and the stranger must not be touched."""
    p = sleeper()
    entry = jobs.register(p.pid, "unit test", "sleep 60")
    path = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    stale = dict(entry, starttime=entry["starttime"] + 1)
    stale.pop("_path", None)
    path.write_text(json.dumps(stale), encoding="utf-8")

    rc = jobs.cmd_reap(type("A", (), {"id": None})())
    assert rc == 0, "a stale entry is housekeeping, not a refusal"
    assert jobs.load_all() == [], "the stale entry survived"
    assert p.poll() is None, (
        "bd-jobs killed the process now holding a recycled pid -- the exact "
        "outcome the start-time guard exists to prevent")


def test_run_refuses_a_host_that_cannot_register(jobs, monkeypatch, capsys):
    """The tool must not manufacture the orphan it exists to prevent.

    MEASURED on its own first live run at v3.66.1040: `run --host .85` started
    the remote command, THEN failed to register because bd-jobs was not deployed
    there yet. The job ran with no record -- the exact failure this whole tool is
    about, caused by the tool. Verify first, launch second.
    """
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        rc = 1 if "test -f" in " ".join(cmd) else 0
        return subprocess.CompletedProcess(cmd, rc, "", "")

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    rc = jobs.cmd_run(type("A", (), {
        "host": "somewhere", "purpose": "p", "command": ["sleep", "1"]})())

    assert rc == 2, "an unregisterable host must be a refusal, not a launch"
    assert len(calls) == 1 and "test -f" in " ".join(calls[0]), (
        "something ran after the probe failed -- the job was launched anyway: %s"
        % calls)
    assert "REFUSED" in capsys.readouterr().err


def test_run_strips_the_argparse_separator_from_the_command(jobs, monkeypatch):
    """argparse.REMAINDER keeps the `--`, and nothing here covered assembly.

    MEASURED on this tool's first live run: `run --host X -- sleep 90` sent
    `bash -c "-- sleep 90"`, bash answered "invalid option" and exited, and the
    registry then held a correct entry for an already-dead process. `list` said
    DEAD and everything LOOKED consistent -- the tests all passed, because none
    of them ever built a command line.

    RE-ANCHORED at v3.66.1206, and the property is unchanged. The local launch
    is now a release gate: what `Popen` receives is the WRAPPER's argv, and the
    user's argv rides at the end of it. The old form asserted equality against
    the whole `Popen` argv, so it measured the launcher's shape rather than the
    command -- it was already over-sensitive, and a gate makes it false while
    `--`-stripping is still perfectly correct.

    So the two facts are asserted where they live: `_user_argv(_user_command())`
    is the exact argv the job runs, and the wrapper carries THAT LIST, verbatim
    and last, after its own `--`. The old fake (a `Popen` returning an object
    whose `pid` was pytest's own) is gone: it cannot hold the gate's read end,
    so it could only ever prove that a broken gate is broken.
    """
    # 1. The command itself, from the production helpers.
    assert jobs._strip_separator(["--", "sleep", "90"]) == ["sleep", "90"]
    user_argv = jobs._user_argv(jobs._user_command(["--", "sleep", "90"]))
    assert user_argv == ["bash", "-c", "sleep 90"], (
        "the argparse separator reached the command: %r" % (user_argv,))
    assert "--" not in user_argv[2], (
        "the separator survived inside the command string: %r" % user_argv[2])

    # POSITIVE CONTROL for the same helper, because "no `--`" is trivially true
    # of a helper that drops arguments. Quoted, spaced content must survive
    # exactly -- the second bug this seam shipped (bare-space re-joining).
    spaced = jobs._user_command(["--", "bash", "-c", "cd /tmp && echo hi"])
    assert spaced == "bash -c 'cd /tmp && echo hi'", (
        "argv was re-joined so the shell re-splits it: %r" % spaced)
    proof = subprocess.run(["bash", "-c", spaced], capture_output=True,
                           text=True, timeout=30)
    assert proof.stdout.strip() == "hi", (
        "the reassembled command does not run as the caller wrote it: %r -> %r"
        % (spaced, proof.stdout + proof.stderr))

    # 2. The wrapper carries that exact argv, last, after its own delimiter.
    #    Two descriptors now: the release channel it waits on, and the ready
    #    channel it reports through before it waits (v3.66.1206 audit).
    gate_argv = jobs._gate_argv(9, 11, user_argv)
    assert gate_argv[-3:] == user_argv, (
        "the wrapper did not carry the user argv verbatim at the end: %r"
        % (gate_argv,))
    assert gate_argv[-4] == "--", (
        "nothing separates the wrapper's own options from the user's argv, so "
        "a command starting with a dash would be parsed as ours: %r"
        % (gate_argv,))
    assert gate_argv[2] == jobs._GATE_FLAG and gate_argv[3:5] == ["9", "11"], (
        "the wrapper argv lost its mode or one of its two descriptors: %r"
        % (gate_argv,))
    assert user_argv[2] not in gate_argv[:-1], (
        "the command appears more than once in the wrapper argv: %r"
        % (gate_argv,))


def test_run_refuses_an_empty_command(jobs, capsys):
    """`bash -c ""` exits 0 having done nothing, and would register a job that
    never existed -- a record with no work behind it is as bad as work with no
    record."""
    rc = jobs.cmd_run(type("A", (), {
        "host": "local", "purpose": "p", "command": ["--"]})())
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().err


def test_run_takes_a_script_file_rather_than_a_quoted_shell_program(jobs,
                                                                    monkeypatch,
                                                                    tmp_path):
    """--script exists because the inline path crosses three quoting layers.

    MEASURED at v3.66.1044: a four-iteration sweep passed as
    `bash -c '...for N in 8 16 32 64; do ... $(tr ...) ...'` went through argv
    -> shlex.join -> ssh -> the remote shell -> `bash -c`, died on the far
    side, and produced NO OUTPUT AT ALL. The registry held a correct entry for
    a job that had already failed, so `list` looked consistent. Both `--`-class
    bugs in this tool's history were at this same seam. A file crosses one
    layer: it is copied, and its path is the only thing quoted.
    """
    script = tmp_path / "sweep.sh"
    script.write_text("for N in 8 16; do echo $N; done\n")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        identity = _identity_record_for_command(jobs, cmd)
        if identity is not None:
            return subprocess.CompletedProcess(cmd, 0, identity, "")
        # A REAL TARGET AUTHENTICATES ITS ANSWER (v3.66.1206): the payload ends
        # in a status sentinel on stderr, and a launcher that accepted a silent
        # rc 0 could not tell a target result from a dropped connection.
        err = ""
        if cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            err = _status_record_for_payload(
                jobs, cmd[-1], 0, disposition="ADOPTED")
        return subprocess.CompletedProcess(cmd, 0, "", err)

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(
        jobs, "_run_scp",
        lambda argv: (calls.append(list(argv)) or (
            subprocess.CompletedProcess(argv, 0, "", ""), True, "")))
    rc = jobs.cmd_run(type("A", (), {
        "host": "somewhere", "purpose": "sweep", "script": str(script),
        "command": []})())
    assert rc == 0, calls

    scp = [c for c in calls if c[0] == "scp"]
    assert scp, "the script was never copied: %s" % calls
    assert str(script) in scp[0], scp[0]
    assert calls.index(scp[0]) < len(calls) - 1, (
        "the copy was not done before the launch -- a script that did not "
        "arrive must be a refusal, not a job running the previous copy of "
        "itself")

    launched = " ".join(calls[-1])
    copied = scp[0][-1].split(":", 1)[1]
    # RE-ANCHORED at v3.66.1206: the copy now lands in the target's registry
    # directory under an owned prefix, and the target's OWN transaction runs it
    # via `--script`. The property is unchanged and is asserted against the
    # path production actually copied to, rather than a literal.
    assert "/bd-jobs/.bd-jobs-script-" in copied, (
        "the copy is not an owned child of the target registry: %s" % copied)
    assert ("--script %s" % copied) in launched, (
        "the remote command does not run the copied script: %s" % launched)
    for shell_meta in ("for N in", "$(", "&&"):
        assert shell_meta not in launched, (
            "the script's own shell syntax reached the remote command line "
            "(%r) -- the whole point is that it does not" % shell_meta)


def _no_network(jobs, monkeypatch, scp_rc=0):
    """Nothing here may touch a real host.

    Without this the refusals under test are indistinguishable from the ones
    that fire later: scp to a host called "somewhere" fails, and THAT returns 2
    as well. Three mutants escaped exactly that way before this existed.
    """
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "copy failed")

    def fake_scp(argv):
        calls.append(list(argv))
        return (subprocess.CompletedProcess(
            argv, scp_rc, "", "copy failed"), True, "")

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(jobs, "_run_scp", fake_scp)
    monkeypatch.setattr(jobs.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("nothing should launch"))
    return calls


def test_run_refuses_a_script_that_is_not_there(jobs, monkeypatch, capsys):
    """ASSERT THE REASON. Every refusal in this tool exits 2 -- the missing
    script, the failed copy, the undeployed host -- so a test that checks only
    the code passes whichever one fires. A mutant deleting this check escaped
    on exactly that: the path sailed through, scp failed instead, and the exit
    code was identical."""
    _no_network(jobs, monkeypatch)
    rc = jobs.cmd_run(type("A", (), {
        "host": "somewhere", "purpose": "p", "script": "/nope/missing.sh",
        "command": []})())
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "no script at /nope/missing.sh" in err, (
        "it refused, but not for the missing script: %r" % err)


def test_run_refuses_a_script_AND_a_command(jobs, tmp_path, monkeypatch, capsys):
    """Two different jobs. Running one and registering the other is the shape
    this tool exists to prevent."""
    _no_network(jobs, monkeypatch)
    script = tmp_path / "s.sh"
    script.write_text("echo hi\n")
    rc = jobs.cmd_run(type("A", (), {
        "host": "somewhere", "purpose": "p", "script": str(script),
        "command": ["sleep", "1"]})())
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "two different jobs" in err, (
        "it refused for some other reason: %r" % err)


def test_failed_scp_names_maybe_partial_copy_without_claiming_cleanup_authority(
        jobs, tmp_path, monkeypatch, capsys):
    """A failed scp proves neither absence nor pathname ownership.

    Mutation design N62: restoring the old refusal that omits ``remote_script``
    hides the maybe-partial retained path; adding pathname cleanup is caught by
    the explicit no-SSH-after-scp control.
    """
    calls = _no_network(jobs, monkeypatch, scp_rc=1)
    script = tmp_path / "s.sh"
    script.write_text("echo hi\n")
    rc = jobs.cmd_run(type("A", (), {
        "host": "somewhere", "purpose": "p", "script": str(script),
        "command": []})())
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "could not copy" in err, (
        "a failed copy refused for a different reason: %r" % err)
    scp_calls = [c for c in calls if c and c[0] == "scp"]
    assert scp_calls, "scp was never attempted"
    copied = scp_calls[-1][-1].split(":", 1)[1]
    # scp failure does not prove the destination is absent or still denotes an
    # inode this attempt created. Do not delete a possibly replaced pathname.
    after_copy = calls[calls.index(scp_calls[-1]) + 1:]
    assert not [c for c in after_copy if c and c[0] == "ssh"], (
        "failed scp was followed by a launch, probe, or unsafe pathname rm: %r"
        % after_copy)
    assert copied in err and "RETAINED UNKNOWN" in err, err
    assert "partial" in err.lower(), err
    assert "ownership is not proven" in err.lower(), err


def _assert_scp_parent_is_refused(jobs, monkeypatch, parent):
    pid = 424242

    class Copy:
        pid = 424242

        def communicate(self, timeout):
            pytest.fail("unowned scp reached communicate")

    def forbidden(*args, **kwargs):
        pytest.fail("unowned scp reached process-group cleanup")

    monkeypatch.setattr(jobs, "_proc_ppid", lambda candidate: parent)
    monkeypatch.setattr(jobs.os, "getpgid", forbidden)
    monkeypatch.setattr(jobs.os, "killpg", forbidden)

    complete, note = jobs._terminate_owned_popen_group(Copy(), timeout=0)

    assert complete is False
    assert str(pid) in note and "pgid UNKNOWN" in note, note
    assert "parent %r" % parent in note, note


def test_terminate_owned_scp_group_refuses_unknown_parent_without_signalling(
        jobs, monkeypatch):
    _assert_scp_parent_is_refused(jobs, monkeypatch, None)


def test_terminate_owned_scp_group_refuses_foreign_parent_without_signalling(
        jobs, monkeypatch):
    _assert_scp_parent_is_refused(jobs, monkeypatch, os.getpid() + 1)


def test_terminate_owned_scp_group_refuses_unknown_group_identity(
        jobs, monkeypatch):
    pid = 434343

    class Copy:
        pid = 434343

        def communicate(self, timeout):
            pytest.fail("unknown scp group reached communicate")

    def unknown_group(candidate):
        raise PermissionError(1, "injected getpgid EPERM")

    monkeypatch.setattr(jobs, "_proc_ppid", lambda candidate: os.getpid())
    monkeypatch.setattr(jobs.os, "getpgid", unknown_group)
    monkeypatch.setattr(
        jobs.os, "killpg",
        lambda *args, **kwargs: pytest.fail("unknown scp group was signalled"))

    complete, note = jobs._terminate_owned_popen_group(Copy(), timeout=0)

    assert complete is False
    assert str(pid) in note and "pgid UNKNOWN" in note, note
    assert "injected getpgid EPERM" in note, note


def test_terminate_owned_scp_group_refuses_a_nonleader_without_signalling(
        jobs, monkeypatch):
    pid = 444444
    pgid = 555555

    class Copy:
        pid = 444444

        def communicate(self, timeout):
            pytest.fail("nonleader scp reached communicate")

    monkeypatch.setattr(jobs, "_proc_ppid", lambda candidate: os.getpid())
    monkeypatch.setattr(jobs.os, "getpgid", lambda candidate: pgid)
    monkeypatch.setattr(
        jobs.os, "killpg",
        lambda *args, **kwargs: pytest.fail("nonleader group was signalled"))

    complete, note = jobs._terminate_owned_popen_group(Copy(), timeout=0)

    assert complete is False
    assert str(pid) in note and str(pgid) in note, note
    assert "does not lead" in note, note


def test_run_scp_preserves_a_normal_failure_result_and_captures_output(
        jobs, monkeypatch):
    argv = ["scp", "--", "source", "host:destination"]
    seen = {}

    class FailedCopy:
        pid = 616161
        returncode = 23

        def communicate(self, timeout):
            seen["timeout"] = timeout
            return "ordinary-out", "ordinary-err"

    def fake_popen(cmd, **kwargs):
        seen["argv"] = list(cmd)
        seen["kwargs"] = dict(kwargs)
        return FailedCopy()

    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)

    result, cleanup_complete, cleanup_note = jobs._run_scp(argv)

    assert seen["argv"] == argv
    assert seen["kwargs"].get("stdout") == subprocess.PIPE
    assert seen["kwargs"].get("stderr") == subprocess.PIPE
    assert seen["kwargs"].get("text") is True
    assert seen["kwargs"].get("start_new_session") is True
    assert seen["timeout"] == 60.0
    assert result.args == argv
    assert result.returncode == 23
    assert result.stdout == "ordinary-out"
    assert result.stderr == "ordinary-err"
    assert cleanup_complete is True
    assert cleanup_note == ""


def test_scp_timeout_is_bounded_and_names_the_maybe_partial_copy(
        jobs, tmp_path, monkeypatch, capsys):
    script = tmp_path / "bounded-copy.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    calls = []
    events = []

    class TimedOutCopy:
        pid = 424242
        returncode = None
        communicates = 0

        def communicate(self, timeout):
            events.append(("communicate", timeout))
            self.communicates += 1
            if self.communicates == 1:
                raise subprocess.TimeoutExpired(["scp"], timeout)
            self.returncode = -9
            return "", ""

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), dict(kwargs)))
        if cmd[0] == "scp":
            pytest.fail("scp bypassed the owned process-group runner")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_popen(cmd, **kwargs):
        calls.append((list(cmd), dict(kwargs)))
        assert cmd[0] == "scp", cmd
        return TimedOutCopy()

    def fake_killpg(pgid, signal):
        events.append(("killpg", pgid, signal))
        if signal == 0:
            raise ProcessLookupError(pgid)

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(jobs.socket, "gethostname", lambda: "launcher-host")
    monkeypatch.setattr(jobs, "_proc_ppid", lambda pid: os.getpid())
    monkeypatch.setattr(jobs.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(jobs.os, "killpg", fake_killpg)

    rc = jobs.cmd_run(_remote_args(script=str(script)))
    err = capsys.readouterr().err

    scp_calls = [(argv, kwargs) for argv, kwargs in calls if argv[0] == "scp"]
    assert len(scp_calls) == 1, scp_calls
    scp_argv, scp_kwargs = scp_calls[0]
    assert ["-o", "ConnectTimeout=15"] == scp_argv[
        scp_argv.index("--") - 2:scp_argv.index("--")], scp_argv
    assert scp_kwargs.get("start_new_session") is True, scp_kwargs
    assert scp_kwargs.get("text") is True, scp_kwargs
    destination = scp_argv[-1].split(":", 1)[1]
    after_copy = calls[calls.index(scp_calls[0]) + 1:]
    assert after_copy == [], after_copy
    assert events[:2] == [
        ("communicate", 60.0),
        ("killpg", 424242, 9),
    ]
    assert events[2][0] == "communicate", events
    assert 0.0 <= events[2][1] <= 10.0, events
    assert events[3:] == [
        ("killpg", 424242, 0),
    ]
    assert rc == 2
    assert "could not copy" in err and "timed out" in err.lower(), err
    assert destination in err and "RETAINED UNKNOWN" in err, err
    assert "partial" in err.lower() and "ownership is not proven" in err.lower()


def test_scp_timeout_with_a_surviving_group_is_unknown_and_names_ownership(
        jobs, tmp_path, monkeypatch, capsys):
    script = tmp_path / "unclean-copy.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    clock = iter([0.0, 0.0, 11.0])
    events = []

    class TimedOutCopy:
        pid = 515151
        returncode = None
        communicates = 0

        def communicate(self, timeout):
            self.communicates += 1
            if self.communicates == 1:
                raise subprocess.TimeoutExpired(["scp"], timeout)
            events.append(("communicate", timeout))
            self.returncode = -9
            return "", ""

    def fake_run(cmd, **kwargs):
        if cmd[0] == "scp":
            pytest.fail("scp bypassed the owned process-group runner")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_killpg(pgid, signal):
        events.append(("killpg", pgid, signal))

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(
        jobs.subprocess, "Popen", lambda cmd, **kwargs: TimedOutCopy())
    monkeypatch.setattr(jobs.socket, "gethostname", lambda: "launcher-host")
    monkeypatch.setattr(jobs, "_proc_ppid", lambda pid: os.getpid())
    monkeypatch.setattr(jobs.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(jobs.os, "killpg", fake_killpg)
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(jobs.time, "sleep", lambda seconds: None)

    rc = jobs.cmd_run(_remote_args(script=str(script)))
    err = capsys.readouterr().err

    assert rc == jobs._EXIT_REMOTE_UNKNOWN
    assert err.startswith("UNKNOWN: could not copy"), err
    assert "515151" in err and "process group" in err
    assert "RETAINED" in err and "partial" in err.lower()
    assert events == [
        ("killpg", 515151, 9),
        ("communicate", 10.0),
        ("killpg", 515151, 0),
    ]


# ── @1074: the preflight told the truth about the wrong thing ────────────────

def test_scp_interrupt_attempts_cleanup_and_preserves_the_primary(
        jobs, monkeypatch):
    events = []

    class InterruptedCopy:
        pid = 616161
        returncode = None

        def communicate(self, timeout):
            events.append(("communicate", timeout))
            raise KeyboardInterrupt("operator cancelled copy")

    process = InterruptedCopy()
    monkeypatch.setattr(
        jobs.subprocess, "Popen", lambda argv, **kwargs: process)

    def fake_cleanup(candidate):
        events.append(("cleanup", candidate.pid))
        return False, "pid 616161 / process group 616161 still exists"

    monkeypatch.setattr(jobs, "_terminate_owned_popen_group", fake_cleanup)

    with pytest.raises(KeyboardInterrupt) as excinfo:
        jobs._run_scp(["scp", "source", "target:/partial"])

    assert events == [("communicate", 60.0), ("cleanup", 616161)]
    assert "operator cancelled copy" in str(excinfo.value)
    assert "cleanup is UNKNOWN" in str(excinfo.value)
    assert "still exists" in str(excinfo.value)


def _cmd_run_through_interrupted_scp(
        jobs, monkeypatch, tmp_path, cleanup_result):
    """Exercise cmd_run's real copy seam without touching a host."""
    events = []
    primary = KeyboardInterrupt("operator cancelled copy")
    script = tmp_path / "interrupted-copy.sh"
    script.write_text("echo hi\n", encoding="utf-8")

    class InterruptedCopy:
        pid = 717171
        returncode = None

        def communicate(self, timeout):
            events.append(("communicate", timeout))
            raise primary

    def fake_run(cmd, **kwargs):
        events.append(("run", list(cmd)))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_popen(cmd, **kwargs):
        events.append(("popen", list(cmd), dict(kwargs)))
        return InterruptedCopy()

    def fake_cleanup(process):
        events.append(("cleanup", process.pid))
        return cleanup_result

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(jobs, "_terminate_owned_popen_group", fake_cleanup)
    monkeypatch.setattr(
        jobs.socket, "gethostname", lambda: "launcher-host")

    with pytest.raises(KeyboardInterrupt) as excinfo:
        jobs.cmd_run(_remote_args(script=str(script)))

    scp_events = [
        (index, event) for index, event in enumerate(events)
        if event[0] == "popen" and event[1][0] == "scp"
    ]
    assert len(scp_events) == 1, events
    scp_index, scp_event = scp_events[0]
    destination = scp_event[1][-1].split(":", 1)[1]
    return excinfo.value, primary, destination, events, scp_index


def test_cmd_run_scp_interrupt_names_retained_destination_after_complete_cleanup(
        jobs, monkeypatch, tmp_path):
    raised, primary, destination, events, scp_index = (
        _cmd_run_through_interrupted_scp(
            jobs, monkeypatch, tmp_path,
            (True, "reaped pid 717171 and process group 717171 is gone")))
    message = str(raised)
    after_copy = events[scp_index + 1:]

    assert raised is primary
    assert destination in message, message
    assert "RETAINED UNKNOWN" in message, message
    assert "partial" in message.lower(), message
    assert "not deleted" in message.lower(), message
    assert "ownership is not proven" in message.lower(), message
    assert "cleanup is UNKNOWN" not in message, message
    assert not [event for event in after_copy if event[0] == "run"], (
        "an interrupted copy was followed by SSH launch/probe/removal: %r"
        % (after_copy,))
    assert jobs._ATTEMPT_CLEANUP_FLAG not in repr(after_copy), after_copy
    assert "rm -f " not in repr(after_copy), after_copy


def test_cmd_run_scp_interrupt_names_retained_destination_when_cleanup_unknown(
        jobs, monkeypatch, tmp_path):
    raised, primary, destination, events, scp_index = (
        _cmd_run_through_interrupted_scp(
            jobs, monkeypatch, tmp_path,
            (False, "pid 717171 / process group 717171 still exists")))
    message = str(raised)
    after_copy = events[scp_index + 1:]

    assert raised is primary
    assert destination in message, message
    assert "RETAINED UNKNOWN" in message, message
    assert "partial" in message.lower(), message
    assert "not deleted" in message.lower(), message
    assert "ownership is not proven" in message.lower(), message
    assert "cleanup is UNKNOWN" in message, message
    assert "717171" in message and "still exists" in message, message
    assert not [event for event in after_copy if event[0] == "run"], (
        "an interrupted copy was followed by SSH launch/probe/removal: %r"
        % (after_copy,))
    assert jobs._ATTEMPT_CLEANUP_FLAG not in repr(after_copy), after_copy
    assert "rm -f " not in repr(after_copy), after_copy


def test_scp_communication_oserror_is_structured_after_cleanup(
        jobs, monkeypatch):
    events = []

    class BrokenCopy:
        pid = 626262
        returncode = None

        def communicate(self, timeout):
            events.append(("communicate", timeout))
            raise OSError("copy pipe broke")

    process = BrokenCopy()
    monkeypatch.setattr(
        jobs.subprocess, "Popen", lambda argv, **kwargs: process)

    def fake_cleanup(candidate):
        events.append(("cleanup", candidate.pid))
        return False, "pid 626262 / process group 626262 cleanup UNKNOWN"

    monkeypatch.setattr(jobs, "_terminate_owned_popen_group", fake_cleanup)
    result, cleanup_complete, cleanup_note = jobs._run_scp(
        ["scp", "source", "target:/partial"])

    assert events == [("communicate", 60.0), ("cleanup", 626262)]
    assert result.returncode == 125
    assert "communication failed" in result.stderr
    assert "copy pipe broke" in result.stderr
    assert cleanup_complete is False
    assert "626262" in cleanup_note and "UNKNOWN" in cleanup_note


def test_scp_interrupt_preserves_primary_when_cleanup_itself_raises(
        jobs, monkeypatch):
    class InterruptedCopy:
        pid = 627272

        def communicate(self, timeout):
            raise KeyboardInterrupt("primary cancellation")

    monkeypatch.setattr(
        jobs.subprocess, "Popen", lambda argv, **kwargs: InterruptedCopy())

    def broken_cleanup(process):
        raise RuntimeError("cleanup transport broke")

    monkeypatch.setattr(jobs, "_terminate_owned_popen_group", broken_cleanup)

    with pytest.raises(KeyboardInterrupt) as excinfo:
        jobs._run_scp(["scp", "source", "target:/partial"])

    assert "primary cancellation" in str(excinfo.value)
    assert "cleanup is UNKNOWN" in str(excinfo.value)
    assert "cleanup transport broke" in str(excinfo.value)


def test_scp_constructor_failure_is_a_refusal_with_no_cleanup_signal(
        jobs, monkeypatch):
    cleanup_calls = []

    def fail_to_spawn(argv, **kwargs):
        raise FileNotFoundError("scp is not installed")

    monkeypatch.setattr(jobs.subprocess, "Popen", fail_to_spawn)
    monkeypatch.setattr(
        jobs, "_terminate_owned_popen_group",
        lambda process: cleanup_calls.append(process) or (False, "wrong"))
    monkeypatch.setattr(
        jobs.os, "killpg",
        lambda *args: pytest.fail("spawn failure signalled a process group"))

    result, cleanup_complete, cleanup_note = jobs._run_scp(
        ["scp", "source", "target:/never-created"])

    assert result.returncode == 125
    assert "could not start scp" in result.stderr
    assert "not installed" in result.stderr
    assert cleanup_complete is True
    assert cleanup_note == "no scp process was acquired"
    assert cleanup_calls == []


def test_scp_cleanup_spends_one_shared_deadline(jobs, monkeypatch):
    now = [0.0]
    sleeps = []
    signals = []

    class ReapedCopy:
        pid = 636363
        returncode = None

        def communicate(self, timeout):
            assert timeout == pytest.approx(10.0)
            now[0] = 9.8
            self.returncode = -9
            return "", ""

    def fake_killpg(pgid, signal):
        signals.append((pgid, signal))

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(jobs.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(jobs.time, "sleep", fake_sleep)
    monkeypatch.setattr(jobs, "_proc_ppid", lambda pid: os.getpid())
    monkeypatch.setattr(jobs.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(jobs.os, "killpg", fake_killpg)

    complete, note = jobs._terminate_owned_popen_group(
        ReapedCopy(), timeout=10.0)

    assert complete is False
    assert "10.0s cleanup bound" in note
    assert now[0] == pytest.approx(10.0)
    assert sum(sleeps) == pytest.approx(0.2)
    assert sleeps and all(0.0 < delay <= 0.05 for delay in sleeps)
    assert signals[0] == (636363, 9)
    assert signals[1:] and all(
        item == (636363, 0) for item in signals[1:])


def _assert_scp_cleanup_wait_failure_is_unknown(
        jobs, monkeypatch, failure):
    signals = []

    class UnreapedCopy:
        pid = 646464
        returncode = None

        def communicate(self, timeout):
            if failure == "timeout":
                raise subprocess.TimeoutExpired(["scp"], timeout)
            raise OSError("wait channel broke")

    monkeypatch.setattr(jobs.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(jobs, "_proc_ppid", lambda pid: os.getpid())
    monkeypatch.setattr(jobs.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        jobs.os, "killpg",
        lambda pgid, signal: signals.append((pgid, signal)))

    complete, note = jobs._terminate_owned_popen_group(UnreapedCopy())

    assert complete is False
    assert "646464" in note
    assert ("not reaped" in note if failure == "timeout"
            else "wait UNKNOWN" in note)
    assert signals == [(646464, 9)], (
        "terminal absence was probed before the direct child was reaped: %r"
        % signals)


def test_scp_cleanup_timeout_is_unknown_without_a_terminal_probe(
        jobs, monkeypatch):
    _assert_scp_cleanup_wait_failure_is_unknown(jobs, monkeypatch, "timeout")


def test_scp_cleanup_oserror_is_unknown_without_a_terminal_probe(
        jobs, monkeypatch):
    _assert_scp_cleanup_wait_failure_is_unknown(jobs, monkeypatch, "oserror")


def _assert_scp_group_probe_result(
        jobs, monkeypatch, probe, expected_complete):
    signals = []

    class GenericOSError(OSError):
        """Avoid OSError's automatic ESRCH-to-ProcessLookupError coercion."""

    class ReapedCopy:
        pid = 656565
        returncode = None

        def communicate(self, timeout):
            self.returncode = -9
            return "", ""

    def fake_killpg(pgid, signal):
        signals.append((pgid, signal))
        if signal != 0:
            return
        if probe == "process_lookup":
            raise ProcessLookupError(pgid)
        if probe == "esrch":
            raise GenericOSError(jobs.errno.ESRCH, "gone")
        raise PermissionError(jobs.errno.EPERM, "denied")

    monkeypatch.setattr(jobs.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(jobs, "_proc_ppid", lambda pid: os.getpid())
    monkeypatch.setattr(jobs.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(jobs.os, "killpg", fake_killpg)

    complete, note = jobs._terminate_owned_popen_group(ReapedCopy())

    assert complete is expected_complete
    assert signals == [(656565, 9), (656565, 0)]
    if expected_complete:
        assert "group 656565 is gone" in note
    else:
        assert "terminal probe UNKNOWN" in note and "denied" in note


def test_scp_group_probe_accepts_process_lookup_absence(jobs, monkeypatch):
    _assert_scp_group_probe_result(jobs, monkeypatch, "process_lookup", True)


def test_scp_group_probe_accepts_esrch_absence(jobs, monkeypatch):
    _assert_scp_group_probe_result(jobs, monkeypatch, "esrch", True)


def test_scp_group_probe_treats_permission_as_unknown(jobs, monkeypatch):
    _assert_scp_group_probe_result(jobs, monkeypatch, "eperm", False)


def test_a_fleet_label_resolves_to_its_address(jobs, tmp_path, monkeypatch):
    """`--host test6` must reach test6.

    MEASURED at v3.66.1072: `bd-jobs run --host test6` was refused with "test6
    has no .../bd-jobs -- deploy it there first" while the file sat there,
    executable, on a tree at the right commit. `test6` is a FLEET LABEL, the
    same one `scripts/deploy_fleet.sh` reads out of ~/.config/bd/hosts; it has
    no DNS entry, so ssh never reached the box at all.
    """
    hosts = tmp_path / "hosts"
    hosts.write_text("# comment\n\ntest5 10.0.70.164\ntest6 10.0.70.249\n",
                     encoding="utf-8")
    monkeypatch.setenv("HOSTS_FILE", str(hosts))

    assert jobs._resolve_host("test6") == "10.0.70.249"
    assert jobs._resolve_host("test5") == "10.0.70.164"


def test_an_unknown_host_is_passed_through_untouched(jobs, tmp_path, monkeypatch):
    """Resolution must not become a whitelist.

    An IP, a real DNS name, or a host that predates the fleet file has to keep
    working -- otherwise the fix for an unreachable label makes every
    unlisted host unreachable, which is a worse defect than the one it closes.
    """
    hosts = tmp_path / "hosts"
    hosts.write_text("test6 10.0.70.249\n", encoding="utf-8")
    monkeypatch.setenv("HOSTS_FILE", str(hosts))

    for passthrough in ("10.0.70.85", "buildbox.example.com", "nosuchlabel"):
        assert jobs._resolve_host(passthrough) == passthrough

    monkeypatch.setenv("HOSTS_FILE", str(tmp_path / "does-not-exist"))
    assert jobs._resolve_host("test6") == "test6", (
        "a missing host file must degrade to pass-through, not raise -- the "
        "tool has to work on a box that never had one")


def test_an_unreachable_host_is_not_reported_as_a_missing_tool(jobs, monkeypatch,
                                                               capsys):
    """ASSERT THE REASON, NOT THE CODE (CLAUDE.md section 10).

    ssh exits 255 for its own failures -- name resolution, refused connection,
    auth -- and passes the remote command's status through otherwise. The
    preflight turned EVERY non-zero into "has no bd-jobs -- deploy it there
    first", so an unreachable host produced a confident, specific, wrong
    diagnosis that sends the operator to re-run a deploy that already
    succeeded. That is the tool's own subject: it cannot distinguish a
    condition it never observed.
    """
    def fake_run(cmd, **kw):
        if cmd and cmd[0] == "ssh":
            return subprocess.CompletedProcess(
                cmd, 255, "", "ssh: Could not resolve hostname test6: "
                              "Temporary failure in name resolution")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    # PIN THE HOSTNAME. `cmd_run` decides local-vs-remote with
    # `args.host in (None, "", socket.gethostname(), "local")`, so a test that
    # names a real fleet host takes the LOCAL path on that one box and never
    # reaches ssh. MEASURED: this file was green on test5 and in CI and FAILED
    # test6's capture, because test6's hostname is literally "test6". A fixture
    # value that collides with ambient state is not a fixture.
    monkeypatch.setattr(jobs.socket, "gethostname", lambda: "somewhere-else")
    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(jobs.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("nothing may launch"))
    rc = jobs.cmd_run(type("A", (), {
        "host": "test6", "purpose": "p", "command": ["sleep", "1"]})())

    err = capsys.readouterr().err
    assert rc == 2
    assert "deploy it there first" not in err, (
        "an UNREACHABLE host was reported as a host missing the tool:\n%s" % err)
    assert "could not reach" in err.lower(), (
        "the refusal must name what actually happened:\n%s" % err)
    assert "resolve hostname" in err, (
        "ssh's own stderr is the evidence and must be shown:\n%s" % err)


def test_a_reachable_host_missing_the_tool_still_says_deploy_it(jobs, monkeypatch,
                                                                capsys):
    """The over-sensitivity control for the test above.

    A fix that simply renamed every refusal would pass the previous test and
    destroy the message that matters. ssh reached the box and `test -f`
    answered 1: the tool really is absent, and that really is the right words.
    """
    def fake_run(cmd, **kw):
        rc = 1 if "test -f" in " ".join(cmd) else 0
        return subprocess.CompletedProcess(cmd, rc, "", "")

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(jobs.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("nothing may launch"))
    rc = jobs.cmd_run(type("A", (), {
        "host": "somewhere", "purpose": "p", "command": ["sleep", "1"]})())

    err = capsys.readouterr().err
    assert rc == 2
    assert "deploy it there first" in err, (
        "the genuinely-absent case lost its diagnosis:\n%s" % err)


def test_the_resolved_address_is_what_ssh_and_scp_actually_receive(jobs, tmp_path,
                                                                   monkeypatch):
    """TEST THE SEAM, NOT ONLY THE COMPONENTS (CLAUDE.md section 10).

    `_resolve_host` can be perfect and the launch still go to the label if the
    two are never joined -- which is precisely how this tool shipped at
    v3.66.1040 with eleven passing tests and failed on its first real
    invocation. Every test above either drives the resolver directly or
    inspects a refusal; none asks what string reached ssh.
    """
    hosts = tmp_path / "hosts"
    hosts.write_text("test6 10.0.70.249\n", encoding="utf-8")
    monkeypatch.setenv("HOSTS_FILE", str(hosts))
    script = tmp_path / "job.sh"
    script.write_text("echo hi\n", encoding="utf-8")

    seen = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        identity = _identity_record_for_command(jobs, cmd)
        if identity is not None:
            return subprocess.CompletedProcess(cmd, 0, identity, "")
        # The target authenticates its answer with the status sentinel; see
        # the v3.66.1206 note in the --script node above.
        err = (_status_record_for_payload(
            jobs, cmd[-1], 0, disposition="ADOPTED")
               if cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]
               else "")
        return subprocess.CompletedProcess(cmd, 0, "12345\n", err)

    # PIN THE HOSTNAME. `cmd_run` decides local-vs-remote with
    # `args.host in (None, "", socket.gethostname(), "local")`, so a test that
    # names a real fleet host takes the LOCAL path on that one box and never
    # reaches ssh. MEASURED: this file was green on test5 and in CI and FAILED
    # test6's capture, because test6's hostname is literally "test6". A fixture
    # value that collides with ambient state is not a fixture.
    monkeypatch.setattr(jobs.socket, "gethostname", lambda: "somewhere-else")
    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(
        jobs, "_run_scp",
        lambda argv: (seen.append(list(argv)) or (
            subprocess.CompletedProcess(argv, 0, "", ""), True, "")))
    rc = jobs.cmd_run(type("A", (), {
        "host": "test6", "purpose": "p", "script": str(script), "command": []})())
    assert rc == 0

    scp = [c for c in seen if c and c[0] == "scp"]
    ssh = [c for c in seen if c and c[0] == "ssh"]
    assert scp and ssh, "expected both a copy and an ssh: %s" % seen
    assert any("10.0.70.249:" in part for part in scp[0]), (
        "scp went to the unresolved label: %s" % scp[0])
    for call in ssh:
        assert "10.0.70.249" in call, (
            "ssh was handed the label rather than the address: %s" % call)
        assert "test6" not in call, (
            "the unresolved label reached ssh: %s" % call)


def test_register_publishes_one_complete_entry_only_after_durable_validation(
        jobs, sleeper, monkeypatch):
    """AN ENTRY IS A KILL INSTRUCTION, so a half-written one is a loaded gun.

    `register()` publishes the visible `<id>.json` with a single
    `write_text`, so the moment the name appears the bytes behind it may be
    empty, truncated, or missing `starttime` -- and `load_all()` swallows the
    torn read at `bd-jobs:182`, which is how a job with a record becomes a job
    with no record while everything LOOKS consistent. Worse, a `starttime`
    that never made it to disk is exactly the entry `reap` REFUSES and KEEPS
    forever (`bd-jobs:277-285`): permanent, unkillable litter.

    The property is therefore not "a file exists afterwards" -- a `register()`
    that did nothing satisfies that. It is: nothing is visible under
    `JOBS_DIR.glob("*.json")` until the complete bytes are on the device, the
    publication is a single atomic rename, and the containing directory is
    itself durable. So this asserts the COUNT of the durability calls, which
    names the missing seam rather than a missing artefact, and it inspects the
    directory FROM INSIDE the seam, which is the only moment the intermediate
    state exists.

    R9 (measured): `Path.glob("*.json")` matches dotfiles, and `load_all()`
    globs exactly that -- so a temp named `.<id>.json` would be loaded as a
    registry entry and, torn, would be a torn entry. The temp must not end in
    `.json`, and `load_all()` must see nothing at all mid-publication.

    TIGHTENED at v3.66.1206 after the audit: counting two `fsync` calls does
    not prove durability. TWO FILE FSYNCS WOULD SATISFY A COUNT, and neither
    would make the RENAME survive a power loss -- the name lives in the
    parent directory, so the directory is a separate object that must itself be
    fsynced, AFTER the rename. Each descriptor is therefore classified with
    `os.fstat`, and the whole event ORDER is asserted:
    file fsync -> identity recheck -> replace -> directory fsync.
    """
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    assert list(jobs.JOBS_DIR.glob("*.json")) == [], (
        "precondition: the registry directory must start empty")
    assert jobs.proc_starttime(p.pid) is not None, (
        "precondition: the process to register must be alive and identifiable")

    real_fsync = os.fsync
    real_replace = os.replace
    real_starttime = jobs.proc_starttime
    seen = {"fsync": 0, "replace": 0, "snaps": [], "events": []}

    def snapshot(tag):
        names = sorted(q.name for q in jobs.JOBS_DIR.iterdir())
        blobs = {}
        for n in names:
            if not n.endswith(".json"):
                try:
                    blobs[n] = (jobs.JOBS_DIR / n).read_bytes()
                except OSError as exc:          # pragma: no cover - diagnostic
                    blobs[n] = repr(exc).encode()
        seen["snaps"].append({"tag": tag, "names": names, "blobs": blobs,
                              "loaded": len(jobs.load_all())})

    def counting_fsync(fd):
        seen["fsync"] += 1
        # WHICH OBJECT was made durable, not merely how many times. A directory
        # descriptor and a file descriptor are not interchangeable here.
        kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        seen["events"].append("fsync:%s" % kind)
        snapshot("fsync:%s" % kind)
        return real_fsync(fd)

    def counting_replace(src, dst):
        seen["replace"] += 1
        seen["events"].append("replace")
        snapshot("replace")
        return real_replace(src, dst)

    def counting_starttime(pid):
        seen["events"].append("starttime")
        return real_starttime(pid)

    monkeypatch.setattr(jobs.os, "fsync", counting_fsync)
    monkeypatch.setattr(jobs.os, "replace", counting_replace)
    monkeypatch.setattr(jobs, "proc_starttime", counting_starttime)

    entry = jobs.register(p.pid, "unit test", "sleep 60")

    assert entry["starttime"] is not None, (
        "precondition: the entry under test must carry a start time")
    assert seen["fsync"] > 0, (
        "register() returned a published entry having called os.fsync ZERO "
        "times: there is no durability seam at all, so the visible "
        "<id>.json is whatever the page cache happened to hold. Directory "
        "activity observed: %r" % (seen["snaps"],))
    assert seen["replace"] == 1, (
        "expected exactly one atomic os.replace publication, saw %d -- the "
        "visible name was created in place, so it is readable while it is "
        "still incomplete" % seen["replace"])

    events = seen["events"]
    assert events.count("fsync:dir") == 1, (
        "expected exactly one DIRECTORY fsync, saw %d in %r -- a file fsync "
        "cannot make the rename itself durable, and two of them do not add up "
        "to one directory" % (events.count("fsync:dir"), events))
    assert events.count("fsync:file") >= 1, (
        "no descriptor fsynced was a regular file, so the ENTRY's bytes were "
        "never made durable: %r" % events)
    first_file = events.index("fsync:file")
    recheck = [i for i, e in enumerate(events)
               if e == "starttime" and i > first_file]
    assert recheck, (
        "the pid identity was never re-read after the entry was staged, so a "
        "process that died mid-write would still be published: %r" % events)
    assert first_file < recheck[0] < events.index("replace") \
        < events.index("fsync:dir"), (
        "the durable publication is out of order. Required: file fsync -> "
        "identity recheck -> replace -> directory fsync. Observed: %r" % events)
    assert events[-1] == "fsync:dir", (
        "something happened after the directory was made durable: %r" % events)

    first = seen["snaps"][0]
    visible = [n for n in first["names"] if n.endswith(".json")]
    assert visible == [], (
        "a *.json name was already visible at the first durability call, so "
        "the entry is published before it is durable: %r" % (first,))
    assert first["loaded"] == 0, (
        "load_all() already returned %d entr(ies) mid-publication -- the temp "
        "is being read as a registry entry (R9): %r"
        % (first["loaded"], first["names"]))
    temps = [n for n in first["names"] if not n.endswith(".json")]
    assert len(temps) == 1, (
        "expected exactly one owned temp in flight, saw %r" % (first["names"],))
    staged = json.loads(first["blobs"][temps[0]].decode("utf-8"))
    assert staged["pid"] == p.pid and staged["starttime"] is not None, (
        "the staged bytes were incomplete at the durability call: %r" % staged)

    assert seen["fsync"] >= 2, (
        "only %d fsync call(s): the file and the containing DIRECTORY must "
        "both be durable, or the rename itself can be lost" % seen["fsync"])
    final = sorted(q.name for q in jobs.JOBS_DIR.iterdir())
    assert final == ["%s.json" % entry["id"]], (
        "publication left residue or the wrong name behind: %r" % final)
    assert jobs.load_all() == [entry], (
        "the published entry does not round-trip through load_all()")


def test_local_registration_failure_never_releases_the_command_and_cleans_owned_state(
        jobs, monkeypatch, tmp_path):
    """WORK WITH NO RECORD IS THE INCIDENT. The local path creates it.

    `cmd_run` opens the log, `Popen`s the command, and only THEN calls
    `register()` (`bd-jobs:388-396`). The child is running before anything has
    proved a record can be written, so a registration failure leaves precisely
    the orphan this tool exists to prevent -- and, measured on this base, does
    it while raising `OSError` out of `cmd_run`, leaving the log behind and the
    child alive.

    The command must not be RELEASED until its record is durable. This drives
    the real `cmd_run` with exactly one named fault -- `register` raises -- and
    asks four independent questions: is the outcome a nonzero return rather
    than an escaped exception; did the command stay unreleased; was the child
    reaped as a GROUP and waited; is the owned state gone.

    THE POSITIVE CONTROL COMES FIRST and runs the identical command through
    the identical seam with no fault, because "the marker is absent" is
    trivially true of a command that never worked.
    """
    ok_marker = tmp_path / "released.marker"
    fault_marker = tmp_path / "must-never-exist.marker"
    launched = []
    popen_calls = []
    real_popen = jobs.subprocess.Popen

    def watching_popen(cmd, **kw):
        proc = real_popen(cmd, **kw)
        launched.append(proc.pid)
        popen_calls.append(list(cmd))
        return proc

    def wait_for(path, seconds=5.0):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if path.exists():
                return True
            time.sleep(0.05)
        return path.exists()

    def args_for(marker):
        return type("A", (), {
            "host": "local", "purpose": "reg-gate",
            "command": ["--", "bash", "-c",
                        "touch %s; sleep 45" % marker]})()

    monkeypatch.setattr(jobs.subprocess, "Popen", watching_popen)
    try:
        # ---- positive control: the same command DOES run when released ----
        jobs.JOBS_DIR = tmp_path / "jobs-ok"
        rc_ok = jobs.cmd_run(args_for(ok_marker))
        assert rc_ok == 0, "positive control did not launch: rc=%r" % rc_ok
        assert len(popen_calls) == 1, (
            "positive control did not reach Popen exactly once: %r"
            % popen_calls)
        assert wait_for(ok_marker), (
            "POSITIVE CONTROL FAILED: the released command never created its "
            "marker, so the fault case below could not distinguish "
            "'not released' from 'never worked'")
        ok_pid = launched[0]
        _reap_process(ok_pid)

        # ---- the fault case ----
        jobs.JOBS_DIR = tmp_path / "jobs-fault"
        registrations = []

        def refusing_register(pid, purpose, cmd, origin=None, log=None, **kw):
            # **kw so the seam keeps injecting the fault under test rather than
            # a TypeError when register() grows a keyword (v3.66.1206 added
            # log ownership). A stub that refuses for the wrong reason still
            # goes through the failure branch, and would prove nothing.
            registrations.append(pid)
            raise OSError("INJECTED: registry publication refused at the seam")

        monkeypatch.setattr(jobs, "register", refusing_register)

        outcome = None
        try:
            outcome = jobs.cmd_run(args_for(fault_marker))
        except OSError as exc:
            outcome = ("RAISED", type(exc).__name__, str(exc))

        assert len(popen_calls) == 2, (
            "the fault case did not reach the wrapped Popen exactly once "
            "(calls: %r) -- nothing was launched, so every assertion below "
            "would be vacuously true" % popen_calls)
        assert len(registrations) == 1, (
            "the injected registration fault fired %d time(s), not once: the "
            "test never exercised the failure branch" % len(registrations))
        child = launched[1]
        marker_appeared = wait_for(fault_marker, 3.0)
        gone_deadline = time.time() + 5.0
        while time.time() < gone_deadline and jobs.proc_starttime(child) is not None:
            time.sleep(0.05)

        breaks = []
        if not isinstance(outcome, int):
            breaks.append(
                "OUTCOME: cmd_run did not return a status at all, it "
                "propagated %r -- the caller sees a traceback, not a refusal"
                % (outcome,))
        elif outcome == 0:
            breaks.append(
                "OUTCOME: cmd_run returned 0 after registration failed, so "
                "the launcher reports success for unregistered work")
        if marker_appeared:
            breaks.append(
                "RELEASED: %s exists -- the command RAN before its record was "
                "durable, which is work with no record: the incident itself"
                % fault_marker.name)
        if jobs.proc_starttime(child) is not None:
            breaks.append(
                "ORPHAN: pid %d is still present after the failure (it must "
                "be group-killed AND waited, not left as a live child or a "
                "zombie)" % child)
        residue = (sorted(q.name for q in jobs.JOBS_DIR.iterdir())
                   if jobs.JOBS_DIR.is_dir() else [])
        if residue:
            breaks.append(
                "RESIDUE: %r left in JOBS_DIR -- creating a path is a promise "
                "to remove it, and nothing else will ever own these" % residue)

        assert not breaks, (
            "a failed local registration left the system in the state this "
            "tool exists to prevent:\n  - %s" % "\n  - ".join(breaks))
    finally:
        for pid in launched:
            _reap_process(pid)


# ── v3.66.1206: the launch is ONE transaction, and it is audited as one ──────
#
# The wrapper gate closed "work with no record". The audit then asked the next
# question -- what proves the wrapper is HOLDING the gate when the record is
# written? -- and found that nothing did: `Popen` returning proves an
# interpreter exec'd, not that it reached the gate. It also found the mirror
# defect on the other side of publication (a durability failure AFTER the
# rename was reported as "nothing was registered" while a valid record stayed
# on disk), and three resources acquired outside any cleanup boundary.
#
# Everything below drives the REAL cmd_run with ONE named fault at a time.


def _reap_process(pid):
    """Reap a proven child, signalling its group only when it leads one.

    Required, not tidy-up: on the defective base several of these nodes leave a
    LIVE child behind, and a RED-first replay that litters the host is the
    review's risk R7. `waitpid` matters as much as the signal -- an unwaited
    child is a zombie whose /proc entry keeps answering, which is exactly what
    `proc_starttime` reads.

    CHILD AUTHORITY IS PROVEN FIRST. A successful wait spends ownership of
    that numeric pid, so a repeated cleanup must return before `getpgid` or a
    signal can act on a replacement process.

    LEADERSHIP IS PROVEN NEXT, for the same reason production proves it.
    MEASURED TWICE in this cut: `os.killpg(os.getpgid(pid))` on a pid that does
    NOT lead its own group signals the LAUNCHER's group -- the pytest run
    itself. The first time was production's cleanup path; the second was this
    helper, on a `Popen` without `start_new_session`, and it SIGKILLed the run
    that was testing it.
    """
    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
    except OSError:
        return
    if waited_pid != 0:
        return

    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    try:
        if pgid is not None and pgid == pid:
            os.killpg(pgid, 9)
        else:
            os.kill(pid, 9)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass


def _replace_path_with_distinct_inode(path, contents):
    """Replace *path* while retaining the original inode until cleanup ends."""
    original_owner = path.open("rb")
    original_inode = os.fstat(original_owner.fileno()).st_ino
    try:
        path.unlink()
        path.write_bytes(contents)
        assert path.stat().st_ino != original_inode, (
            "precondition: replacement reused the original inode")
    except BaseException:
        original_owner.close()
        raise
    return original_owner


def test_local_registration_fault_control_uses_only_the_guarded_reaper():
    """Every cleanup in the fault node must prove group leadership first."""
    source = inspect.getsource(
        test_local_registration_failure_never_releases_the_command_and_cleans_owned_state)
    tree = ast.parse(source)
    direct_group_signals = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "killpg"]
    guarded_reaps = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_reap_process"]

    assert direct_group_signals == [], (
        "the registration fault test bypasses the guarded reaper: %r"
        % direct_group_signals)
    assert len(guarded_reaps) >= 2, (
        "both the positive control and final cleanup must use _reap_process")


def test_guarded_reaper_spends_child_authority_before_any_signal(monkeypatch):
    """An already-reaped numeric PID is not cleanup authority."""
    pid = 424242
    calls = []

    def no_longer_a_child(candidate, options):
        calls.append(("waitpid", candidate, options))
        raise ChildProcessError(candidate)

    monkeypatch.setattr(os, "waitpid", no_longer_a_child)
    monkeypatch.setattr(
        os, "getpgid", lambda candidate: calls.append(("getpgid", candidate)))
    monkeypatch.setattr(
        os, "killpg", lambda pgid, signal: calls.append(("killpg", pgid, signal)))
    monkeypatch.setattr(
        os, "kill", lambda candidate, signal: calls.append(("kill", candidate, signal)))

    _reap_process(pid)

    assert calls == [("waitpid", pid, os.WNOHANG)]


def test_guarded_reaper_returns_after_successful_authority_spending_reap(
        monkeypatch):
    """Collecting an exited child spends authority before any signal seam."""
    pid = 424242
    calls = []

    def collect_exited_child(candidate, options):
        calls.append(("waitpid", candidate, options))
        return pid, 0

    monkeypatch.setattr(os, "waitpid", collect_exited_child)
    monkeypatch.setattr(
        os, "getpgid", lambda candidate: calls.append(("getpgid", candidate)))
    monkeypatch.setattr(
        os, "killpg", lambda pgid, signal: calls.append(("killpg", pgid, signal)))
    monkeypatch.setattr(
        os, "kill", lambda candidate, signal: calls.append(("kill", candidate, signal)))

    _reap_process(pid)

    assert calls == [("waitpid", pid, os.WNOHANG)]


def test_guarded_reaper_uses_nonblocking_probe_before_live_cleanup(monkeypatch):
    """A live owned leader is probed nonblocking, signalled, then collected."""
    pid = 424242
    waits = iter([(0, 0), (pid, 0)])
    calls = []

    def owned_waitpid(candidate, options):
        calls.append(("waitpid", candidate, options))
        return next(waits)

    monkeypatch.setattr(os, "waitpid", owned_waitpid)
    monkeypatch.setattr(
        os, "getpgid", lambda candidate: calls.append(("getpgid", candidate)) or pid)
    monkeypatch.setattr(
        os, "killpg", lambda pgid, signal: calls.append(("killpg", pgid, signal)))
    monkeypatch.setattr(
        os, "kill", lambda candidate, signal: calls.append(("kill", candidate, signal)))

    _reap_process(pid)

    assert calls == [
        ("waitpid", pid, os.WNOHANG),
        ("getpgid", pid),
        ("killpg", pid, 9),
        ("waitpid", pid, 0),
    ]


@pytest.mark.parametrize(
    ("pgid", "expected_signal"),
    [
        (424242, ("killpg", 424242, 9)),
        (313131, ("kill", 424242, 9)),
    ],
)
def test_guarded_reaper_signals_only_a_proven_live_child(
        monkeypatch, pgid, expected_signal):
    """Live child controls retain leader/non-leader cleanup behavior."""
    pid = 424242
    waits = iter([(0, 0), (pid, 0)])
    calls = []

    def owned_waitpid(candidate, options):
        calls.append(("waitpid", candidate, options))
        return next(waits)

    monkeypatch.setattr(os, "waitpid", owned_waitpid)
    monkeypatch.setattr(
        os, "getpgid", lambda candidate: calls.append(("getpgid", candidate)) or pgid)
    monkeypatch.setattr(
        os, "killpg", lambda group, signal: calls.append(("killpg", group, signal)))
    monkeypatch.setattr(
        os, "kill", lambda candidate, signal: calls.append(("kill", candidate, signal)))

    _reap_process(pid)

    assert calls == [
        ("waitpid", pid, os.WNOHANG),
        ("getpgid", pid),
        expected_signal,
        ("waitpid", pid, 0),
    ]


def _wait_until(predicate, seconds=15.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _open_fds():
    """This process's open descriptors, by number. A launch that refuses must
    not silently keep a pipe or a log open: the leak is invisible to every
    behavioural assertion and fatal to a long-lived launcher."""
    return set(os.listdir("/proc/self/fd"))


def _watch_popen(jobs, patcher, launched, argvs, on_spawn=None):
    """Record what was really launched, without changing it."""
    real_popen = jobs.subprocess.Popen

    def watching(cmd, **kw):
        if on_spawn is not None:
            on_spawn()
        proc = real_popen(cmd, **kw)
        launched.append(proc.pid)
        argvs.append(list(cmd))
        return proc

    patcher.setattr(jobs.subprocess, "Popen", watching)


def _run_args(purpose, command):
    return type("A", (), {"host": "local", "purpose": purpose,
                          "command": command})()


def _marker_command(marker):
    """The user work every failure node uses: it announces itself the instant
    it runs, then stays alive long enough to be found."""
    return ["--", "bash", "-c", "touch %s; sleep 45" % marker]


def _residue(jobs_dir):
    return sorted(q.name for q in jobs_dir.iterdir()) if jobs_dir.is_dir() else []


def test_gate_release_fd_close_failure_is_a_named_registered_job_failure(
        jobs, monkeypatch, capsys):
    """A post-RELEASE close fault is a named child outcome, not a traceback.

    Mutation design N61: restoring bare ``os.close(read_fd)`` makes the fault
    escape; continuing to exec leaks an uncertain inheritable descriptor into
    arbitrary user work and is caught by the zero-exec assertion.
    """
    release_r, release_w = os.pipe()
    ready_r, ready_w = os.pipe()
    real_close = os.close
    os.write(release_w, jobs._RELEASE_BYTE)
    real_close(release_w)
    fired = []
    execs = []

    def fail_release_close(fd):
        if fd == release_r and not fired:
            fired.append(fd)
            real_close(fd)
            raise OSError(5, "injected release-fd close EIO")
        return real_close(fd)

    def capture_exec(file, argv):
        execs.append((file, list(argv)))

    monkeypatch.setattr(jobs.os, "close", fail_release_close)
    monkeypatch.setattr(jobs.os, "execvp", capture_exec)
    try:
        rc = jobs._gate_main([
            str(release_r), str(ready_w), "--", "echo", "released"])
        out, err = capsys.readouterr()
        ready = os.read(ready_r, 2)

        assert fired == [release_r], (
            "the post-release descriptor close seam did not fire once: %r"
            % fired)
        assert ready == jobs._READY_BYTE, (
            "the real gate never emitted the exact READY byte: %r" % ready)
        assert execs == [], (
            "an uncertain descriptor was inherited into user work: %r" % execs)
        assert rc == jobs._EXIT_GATE_ABANDONED and out == ""
        assert err == (
            "gate: release was received, but its descriptor close is UNKNOWN "
            "([Errno 5] injected release-fd close EIO); the user command did "
            "not run\n")
    finally:
        real_close(ready_r)


def test_the_launcher_publishes_nothing_when_the_wrapper_never_enters_the_gate(
        jobs, monkeypatch, tmp_path, capsys):
    """MEASURED BY THE AUDIT, and this is the hole the gate did not close.

    `Popen` returns once the child has `execve`d an interpreter. That is not
    the same event as the wrapper REACHING the gate: Python startup, importing
    this tool, or argv dispatch can all fail afterwards. The parent then
    publishes an entry and returns 0 for a process that never held anything --
    the registry says a job exists, `reap` will act on it, and no user work was
    ever gated by it.

    The auditor's shape is reproduced exactly: point the wrapper at
    `/dev/null`, a VALID EMPTY Python program. It starts, runs nothing, exits
    0, and never reads the release descriptor.
    """
    marker = tmp_path / "must-never-exist.marker"
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    monkeypatch.setattr(jobs, "__file__", "/dev/null")
    try:
        rc = jobs.cmd_run(_run_args("never-ready", _marker_command(marker)))
        out, err = capsys.readouterr()

        assert len(launched) == 1, (
            "nothing was launched, so this test would be vacuously true: %r"
            % (argvs,))
        assert "/dev/null" in " ".join(argvs[0]), (
            "the empty-program seam was not used: %r" % (argvs,))
        assert rc == jobs._EXIT_REGISTRATION_FAILED, (
            "a wrapper that never entered the gate was accepted as a launched "
            "job (rc=%r). The parent has no proof the child ever reached the "
            "release descriptor. stdout=%r stderr=%r" % (rc, out, err))
        assert out.strip() == "", (
            "a job id was printed for work that never started: %r" % out)
        assert list(jobs.JOBS_DIR.glob("*.json")) == [], (
            "an entry was published for a process that never held the gate")
        assert _residue(jobs.JOBS_DIR) == [], (
            "owned residue was left behind: %r" % _residue(jobs.JOBS_DIR))
        assert not marker.exists(), "user work ran without a record"
        assert jobs.proc_starttime(launched[0]) is None, (
            "the wrapper was neither reaped nor waited")
        assert "REFUSED" in err, (
            "the refusal was not explained on stderr: %r" % err)
    finally:
        for pid in launched:
            _reap_process(pid)


def test_a_wrapper_that_never_reports_ready_is_abandoned_within_the_bounded_wait(
        jobs, monkeypatch, tmp_path, capsys):
    """A gate the parent waits on forever is not a gate, it is a hang.

    The ready handshake needs a BOUND, and the bound needs a test that proves
    the launcher gives up, cleans up, and says so -- rather than blocking a
    launcher that operators run interactively.
    """
    marker = tmp_path / "must-never-exist.marker"
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    never_ready = [sys.executable, "-c", "import time; time.sleep(60)"]
    monkeypatch.setattr(jobs, "_gate_argv",
                        lambda *a, **k: list(never_ready), raising=False)
    monkeypatch.setattr(jobs, "_READY_TIMEOUT_S", 1.0, raising=False)
    started = time.time()
    try:
        rc = jobs.cmd_run(_run_args("silent-wrapper", _marker_command(marker)))
        elapsed = time.time() - started
        out, err = capsys.readouterr()

        assert argvs == [never_ready], (
            "the silent-wrapper seam was not what ran: %r" % (argvs,))
        assert rc == jobs._EXIT_REGISTRATION_FAILED, (
            "a wrapper that never reported ready was registered anyway "
            "(rc=%r, stdout=%r)" % (rc, out))
        assert 1.0 <= elapsed < 30.0, (
            "the ready wait was not bounded by _READY_TIMEOUT_S: %.2fs"
            % elapsed)
        assert list(jobs.JOBS_DIR.glob("*.json")) == [], "an entry was published"
        assert _residue(jobs.JOBS_DIR) == [], (
            "owned residue: %r" % _residue(jobs.JOBS_DIR))
        assert not marker.exists()
        assert jobs.proc_starttime(launched[0]) is None, (
            "the silent wrapper was left running")
        assert "ready" in err.lower(), (
            "the timeout was not named on stderr: %r" % err)
    finally:
        for pid in launched:
            _reap_process(pid)


def test_a_released_job_runs_under_the_exact_registered_identity(
        jobs, tmp_path, capsys):
    """THE POSITIVE IDENTITY CONTROL, and the catcher for fork-instead-of-exec.

    Every refusal above is satisfiable by a launcher that registers nothing
    ever. This is the other half: the released job must be THE process that was
    registered -- same pid, same start time, and leading its own process group,
    because `reap` kills the GROUP and refuses any entry whose start time no
    longer matches. A wrapper that forked the command instead of `exec`ing it
    would register the wrapper and kill the wrong thing.

    The job reports its own identity through a private file; nothing is
    inferred from the launcher's side of the pipe.
    """
    probe = tmp_path / "identity.txt"
    command = ('printf "%%s\\n" "$$" > %s; cat /proc/$$/stat >> %s; sleep 45'
               % (probe, probe))
    entry = None
    try:
        rc = jobs.cmd_run(_run_args("identity", ["--", "bash", "-c", command]))
        out, err = capsys.readouterr()
        assert rc == 0, "the launch failed: rc=%r stderr=%r" % (rc, err)
        entries = jobs.load_all()
        assert len(entries) == 1, entries
        entry = entries[0]

        assert _wait_until(lambda: probe.exists()
                           and len(probe.read_text().splitlines()) >= 2, 20), (
            "the released job never reported its identity, so every "
            "comparison below would be vacuous")
        lines = probe.read_text().splitlines()
        user_pid = int(lines[0])
        raw = lines[1]
        user_starttime = int(raw[raw.rindex(")") + 1:].split()[19])

        assert user_pid == entry["pid"], (
            "the user command runs as pid %d but the registry recorded %d: the "
            "wrapper FORKED the command instead of exec'ing it, so reap would "
            "signal a process that is not the work" % (user_pid, entry["pid"]))
        assert user_starttime == entry["starttime"], (
            "the recorded start time is not the running job's (%r vs %r), so "
            "the reuse guard would refuse to reap its own job"
            % (entry["starttime"], user_starttime))
        assert os.getpgid(user_pid) == user_pid, (
            "the job does not lead its own process group, so reap can only "
            "signal the launcher's group -- backlog 88 exactly")
        assert out.strip().splitlines()[-1] == entry["id"], (
            "the job id is not the last line of stdout: %r" % out)
        assert "Traceback" not in err, err

        # NO preexec_fn AND NO shell=True, asserted structurally rather than by
        # scanning the text: this file's own comments discuss both, and a text
        # scan would count them (CLAUDE.md A7).
        import ast
        tree = ast.parse(_TOOL.read_text(encoding="utf-8"))
        banned = []
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            for keyword in call.keywords:
                if keyword.arg == "preexec_fn":
                    banned.append("preexec_fn")
                if keyword.arg == "shell" and getattr(
                        keyword.value, "value", None) is True:
                    banned.append("shell=True")
        assert banned == [], (
            "the launch path uses %r: blocking in preexec_fn deadlocks Popen "
            "(measured), and shell=True re-parses the user's command"
            % (banned,))
    finally:
        if entry:
            _reap_process(entry["pid"])


def test_a_directory_fsync_failure_after_publication_retains_and_names_the_final(
        jobs, monkeypatch, tmp_path, capsys):
    """THE MIRROR OF ROW 212: a record with no work, and a lie about it.

    `os.replace` has already made a complete, valid entry visible when the
    directory fsync runs. If that fsync fails, the entry EXISTS. Reporting
    "nothing was registered" and deleting the log it names is not a rollback --
    it leaves a live registry record pointing at a file that was just removed,
    and tells the operator the opposite of what is on disk.

    When the entry cannot be provably withdrawn it must be RETAINED and NAMED,
    its log kept because the retained entry refers to it, the user work never
    released, and the status must be distinguishable from "nothing published".
    """
    marker = tmp_path / "must-never-exist.marker"
    calls = []

    def failing_dir_fsync(path):
        calls.append(str(path))
        raise OSError(5, "injected EIO on the registry directory")

    monkeypatch.setattr(jobs, "_fsync_dir", failing_dir_fsync)
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    try:
        rc = jobs.cmd_run(_run_args("post-replace", _marker_command(marker)))
        out, err = capsys.readouterr()

        assert len(calls) >= 1, "the directory-fsync seam never fired"
        assert len(launched) == 1, "nothing was launched: %r" % (argvs,)
        finals = sorted(jobs.JOBS_DIR.glob("*.json"))
        assert len(finals) == 1, (
            "expected the published final to be retained, found %r"
            % [f.name for f in finals])
        published = json.loads(finals[0].read_text(encoding="utf-8"))
        assert published["pid"] == launched[0], (
            "the retained final is not this transaction's: %r" % published)
        assert rc == 5, (
            "a failure AFTER publication returned rc=%r. rc 3 means nothing "
            "was published, and a complete entry %s is on disk right now, so "
            "the two outcomes must not share a code (_EXIT_PUBLISHED_NOT_"
            "DURABLE). stderr=%r" % (rc, finals[0].name, err))
        assert "RETAINED" in err and finals[0].name in err, (
            "the retained record was not named for the operator: %r" % err)
        assert published.get("log"), "the retained entry names no log"
        assert pathlib.Path(published["log"]).exists(), (
            "the log named by a RETAINED entry was deleted: %s"
            % published["log"])
        assert not marker.exists(), (
            "the user command was released although publication was never "
            "proven durable")
        assert out.strip().splitlines() == [published["id"]], (
            "status 5 retained a complete record but omitted its id-last "
            "reconciliation handle: %r" % out)
        assert jobs.proc_starttime(launched[0]) is None, (
            "the wrapper was left alive next to a retained record")
    finally:
        for pid in launched:
            _reap_process(pid)


def test_a_recoverable_post_publication_failure_rolls_the_final_back(
        jobs, monkeypatch, tmp_path, capsys):
    """The other side of the same branch, and the control that stops it from
    being implemented as "after replace, always retain".

    If the entry can be withdrawn AND the withdrawal made durable, then nothing
    is published, and saying so is true. The rollback is the exact owned final
    -- never a glob, never the directory.
    """
    marker = tmp_path / "must-never-exist.marker"
    real_fsync_dir = jobs._fsync_dir
    calls = []

    def failing_once(path):
        calls.append(str(path))
        if len(calls) == 1:
            raise OSError(5, "injected EIO on the publication fsync")
        return real_fsync_dir(path)

    monkeypatch.setattr(jobs, "_fsync_dir", failing_once)
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    try:
        rc = jobs.cmd_run(_run_args("rollback", _marker_command(marker)))
        out, err = capsys.readouterr()

        assert len(calls) >= 2, (
            "the launcher never attempted to make its rollback durable "
            "(%d directory fsync call(s)): an unlink that is not itself "
            "fsynced can come back" % len(calls))
        assert list(jobs.JOBS_DIR.glob("*.json")) == [], (
            "the final was left published although the rollback was provable: "
            "%r" % _residue(jobs.JOBS_DIR))
        assert rc == jobs._EXIT_REGISTRATION_FAILED, (
            "a provably withdrawn publication must report 'nothing published' "
            "(rc=%r, stderr=%r)" % (rc, err))
        assert "RETAINED" not in err, (
            "the launcher claimed to retain a record it actually removed: %r"
            % err)
        assert _residue(jobs.JOBS_DIR) == [], (
            "owned residue: %r" % _residue(jobs.JOBS_DIR))
        assert not marker.exists()
        assert out.strip() == ""
        assert jobs.proc_starttime(launched[0]) is None
    finally:
        for pid in launched:
            _reap_process(pid)


def test_a_pre_replace_durability_failure_publishes_nothing_and_leaves_no_temp(
        jobs, sleeper, monkeypatch):
    """OVER-SENSITIVITY CONTROL for the two nodes above: a failure BEFORE the
    rename must still be "nothing was published", with no staged temp left to
    be collected by nobody. The seam is scoped so that only the FILE fsync can
    raise -- the directory fsync is neutralised -- or this node and the two
    above would be testing the same branch.
    """
    p = sleeper()
    monkeypatch.setattr(jobs, "_fsync_dir", lambda path: None)
    fired = []

    def failing_file_fsync(fd):
        fired.append(fd)
        raise OSError(5, "injected EIO on the staged entry")

    monkeypatch.setattr(jobs.os, "fsync", failing_file_fsync)
    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "pre-replace", "sleep 60")

    assert len(fired) == 1, (
        "the file-fsync seam fired %d times, so the failure under test is not "
        "the one that was injected" % len(fired))
    assert list(jobs.JOBS_DIR.glob("*.json")) == [], (
        "a final exists after a failure that happened BEFORE the rename")
    assert list(jobs.JOBS_DIR.glob(".bd-jobs-entry-*")) == [], (
        "the staged temp was left behind: %r" % _residue(jobs.JOBS_DIR))
    assert "publish" in str(excinfo.value).lower(), (
        "the refusal does not say what failed: %r" % str(excinfo.value))


def test_a_failure_acquiring_any_owned_resource_refuses_without_leaking(
        jobs, tmp_path, capsys):
    """THE TRANSACTION BOUNDARY. The log and both pipes are acquired before
    anything can be undone, so a failure in the middle used to escape as a
    traceback with the earlier resources owned by nobody.

    Three faults, one per acquisition, each in its own registry directory: the
    outcome must be a truthful status, an explained refusal, no residue, no
    user work, no surviving process, and NO LEAKED DESCRIPTOR.
    """
    marker = tmp_path / "must-never-exist.marker"

    def install_log_fault(mp):
        fired = []

        def raiser(purpose):
            fired.append(purpose)
            raise OSError(28, "injected ENOSPC creating the job log")

        mp.setattr(jobs, "open_job_log", raiser)
        return fired, None

    def install_pipe_fault(index):
        def install(mp):
            fired, calls, spawned = [], [], []

            real_pipe = os.pipe

            def raiser():
                # ONLY THE LAUNCHER'S OWN ACQUISITIONS. `subprocess.Popen`
                # opens an internal errpipe of its own; faulting that would
                # make this node pass over a launcher that never acquired a
                # second pipe at all -- the exact seam it exists to require.
                if spawned:
                    return real_pipe()
                calls.append(1)
                if len(calls) == index:
                    fired.append(index)
                    raise OSError(24, "injected EMFILE acquiring pipe %d"
                                  % index)
                return real_pipe()

            mp.setattr(jobs.os, "pipe", raiser)
            return fired, lambda: spawned.append(True)
        return install

    cases = [("log-open", install_log_fault),
             ("first-pipe", install_pipe_fault(1)),
             ("second-pipe", install_pipe_fault(2))]
    problems = []
    for name, install in cases:
        jobs.JOBS_DIR = tmp_path / ("jobs-%s" % name)
        launched, argvs = [], []
        before = _open_fds()
        with pytest.MonkeyPatch.context() as mp:
            fired, on_spawn = install(mp)
            _watch_popen(jobs, mp, launched, argvs, on_spawn)
            try:
                outcome = jobs.cmd_run(_run_args(name, _marker_command(marker)))
            except BaseException as exc:            # noqa: BLE001 - the defect
                outcome = ("RAISED", type(exc).__name__, str(exc))
            finally:
                for pid in launched:
                    _reap_process(pid)
        out, err = capsys.readouterr()
        after = _open_fds()

        if len(fired) != 1:
            problems.append(
                "%s: the fault fired %d time(s), not once -- there is no such "
                "acquisition to fault (argvs=%r)" % (name, len(fired), argvs))
            continue
        if not isinstance(outcome, int):
            problems.append("%s: escaped as %r instead of returning a status"
                            % (name, (outcome,)))
        elif outcome != jobs._EXIT_REGISTRATION_FAILED:
            problems.append("%s: returned %r, not _EXIT_REGISTRATION_FAILED"
                            % (name, outcome))
        if "REFUSED" not in err:
            problems.append("%s: no refusal on stderr: %r" % (name, err))
        if out.strip():
            problems.append("%s: printed %r on stdout" % (name, out))
        if _residue(jobs.JOBS_DIR):
            problems.append("%s: residue %r"
                            % (name, _residue(jobs.JOBS_DIR)))
        if marker.exists():
            problems.append("%s: the user command ran" % name)
        if after - before:
            problems.append("%s: leaked descriptor(s) %r"
                            % (name, sorted(after - before)))
    assert not problems, (
        "resources acquired outside the transaction:\n  - %s"
        % "\n  - ".join(problems))


def test_a_close_failure_is_reported_and_never_skips_the_rest_of_the_transaction(
        jobs, monkeypatch, tmp_path, capsys):
    """A `close` that raises is nonzero but keeps the durable launch visible.

    Closing the launcher's copy of the log descriptor happens after the child
    already holds it. If that raise escapes, registration is skipped and the
    wrapper is never waited -- a close error turns into an orphan. The
    descriptor is not recoverable either way, so the transaction continues,
    reports the durable entry id for reconciliation, and returns the distinct
    close-UNKNOWN status rather than claiming complete success.
    """
    marker = tmp_path / "released.marker"
    recorded = {}
    real_open_job_log = jobs.open_job_log

    def watching_open(purpose):
        path, fd = real_open_job_log(purpose)
        recorded["path"], recorded["fd"] = path, fd
        return path, fd

    real_close = os.close
    fired = []

    def failing_close(fd):
        if recorded.get("fd") == fd and not fired:
            fired.append(fd)
            raise OSError(9, "injected EBADF closing the log descriptor")
        return real_close(fd)

    monkeypatch.setattr(jobs, "open_job_log", watching_open)
    monkeypatch.setattr(jobs.os, "close", failing_close)
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    entry = None
    try:
        rc = jobs.cmd_run(_run_args("close-fault", _marker_command(marker)))
        out, err = capsys.readouterr()

        assert len(fired) == 1, (
            "the close fault never fired, so this node proves nothing: %r"
            % recorded)
        assert rc == jobs._EXIT_CLOSE_UNKNOWN, (
            "an unrecoverable close was not graded separately from complete "
            "success (rc=%r, stderr=%r)" % (rc, err))
        entries = jobs.load_all()
        assert len(entries) == 1, entries
        entry = entries[0]
        assert _wait_until(marker.exists), (
            "the job was never released, so the close failure skipped the rest "
            "of the transaction")
        assert "UNKNOWN" in err and "close" in err.lower(), (
            "the nonzero close result did not explain its uncertainty: %r" % err)
        assert out.strip().splitlines() == [entry["id"]], (
            "stdout must carry the id and nothing else: %r" % out)
    finally:
        if entry:
            _reap_process(entry["pid"])
        for pid in launched:
            _reap_process(pid)


def test_registry_lock_close_failure_after_durable_publish_is_status_7(
        jobs, monkeypatch, tmp_path, capsys):
    """A lock-fd close fault cannot reverse an already durable publication.

    Non-vacuity controls require the real atomic replace and directory fsync to
    finish before the exact registry-lock descriptor faults once. Mutation
    design: replacing the structured post-publication close-UNKNOWN branch
    with a bare re-raise must make this node observe status 3, a killed child,
    and deleted owned evidence.
    """
    marker = tmp_path / "registry-lock-close-released.marker"
    events = []
    lock_fds = []
    fired = []
    real_lock = jobs._lock_registry
    real_replace = os.replace
    real_fsync_dir = jobs._fsync_dir
    real_close = os.close

    def tracking_lock():
        fd = real_lock()
        lock_fds.append(fd)
        return fd

    def tracking_replace(src, dst):
        events.append("replace")
        return real_replace(src, dst)

    def tracking_fsync_dir(path):
        result = real_fsync_dir(path)
        events.append("dir-fsync")
        return result

    def fail_registry_lock_close(fd):
        if lock_fds and fd == lock_fds[-1] and not fired:
            assert events == ["replace", "dir-fsync"], (
                "the lock close fault fired before durable publication: %r"
                % events)
            fired.append(fd)
            # Model the kernel accepting the close while userspace reports an
            # error: descriptor state is uncertain, but the test must not leak.
            real_close(fd)
            raise OSError(5, "injected EIO closing the registry lock")
        return real_close(fd)

    monkeypatch.setattr(jobs, "_lock_registry", tracking_lock)
    monkeypatch.setattr(jobs.os, "replace", tracking_replace)
    monkeypatch.setattr(jobs, "_fsync_dir", tracking_fsync_dir)
    monkeypatch.setattr(jobs.os, "close", fail_registry_lock_close)
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    entry = None
    try:
        rc = jobs.cmd_run(_run_args(
            "registry-lock-close", _marker_command(marker)))
        out, err = capsys.readouterr()

        assert len(lock_fds) == 1 and fired == lock_fds, (
            "the exact registry-lock close seam did not fire once: "
            "locks=%r fired=%r argvs=%r" % (lock_fds, fired, argvs))
        assert events == ["replace", "dir-fsync"], events
        assert rc == jobs._EXIT_CLOSE_UNKNOWN, (
            "a close fault after durable publication was misclassified "
            "(rc=%r, stderr=%r)" % (rc, err))
        entries = jobs.load_all()
        assert len(entries) == 1, entries
        entry = entries[0]
        assert _wait_until(marker.exists), (
            "the published job was killed or never released after lock-close "
            "uncertainty")
        assert jobs.alive(entry), "the durable recorded workload did not survive"
        assert pathlib.Path(entry["log"]).exists(), (
            "abort cleanup deleted the log owned by the durable record")
        assert "UNKNOWN" in err and "registry lock" in err.lower(), err
        assert entry["id"] in err, (
            "the close-UNKNOWN diagnostic omitted its reconciliation id: %r"
            % err)
        assert out.strip().splitlines() == [entry["id"]], (
            "stdout must retain the exact id-only reconciliation contract: %r"
            % out)
    finally:
        if entry:
            _reap_process(entry["pid"])
            jobs.forget(entry)
        for pid in launched:
            _reap_process(pid)


def test_direct_register_returns_status_7_and_id_for_lock_close_unknown(
        jobs, sleeper, monkeypatch, capsys):
    """The non-launching CLI must expose the same structured durable result."""
    proc = sleeper()
    lock_fds = []
    fired = []
    real_lock = jobs._lock_registry
    real_close = os.close

    def tracking_lock():
        fd = real_lock()
        lock_fds.append(fd)
        return fd

    def fail_registry_lock_close(fd):
        if lock_fds and fd == lock_fds[-1] and not fired:
            fired.append(fd)
            real_close(fd)
            raise OSError(5, "injected direct-register lock close EIO")
        return real_close(fd)

    monkeypatch.setattr(jobs, "_lock_registry", tracking_lock)
    monkeypatch.setattr(jobs.os, "close", fail_registry_lock_close)
    args = type("A", (), {
        "pid": proc.pid, "purpose": "direct close unknown",
        "cmd": "sleep 60", "origin": "local", "request_id": None,
    })()

    try:
        rc = jobs.cmd_register(args)
    except BaseException as exc:  # noqa: BLE001 - the historical defect
        rc = ("RAISED", type(exc).__name__, str(exc))
    out, err = capsys.readouterr()

    assert len(lock_fds) == 1 and fired == lock_fds, (
        "the direct-register lock-close seam did not fire exactly once: "
        "locks=%r fired=%r" % (lock_fds, fired))
    entries = jobs.load_all()
    assert len(entries) == 1, entries
    entry = entries[0]
    assert rc == jobs._EXIT_CLOSE_UNKNOWN, (
        "the direct CLI did not structure the durable close uncertainty: "
        "rc=%r stderr=%r" % (rc, err))
    assert "PUBLISHED" in err and "registry lock" in err.lower(), err
    assert entry["id"] in err, err
    assert out.strip().splitlines() == [entry["id"]], out


def test_post_replace_failure_remains_status_5_when_registry_unlock_close_also_fails(
        jobs, monkeypatch, tmp_path, capsys):
    """Post-replace state stays retained when registry unlock also faults.

    Mutation design N59: raising the secondary close exception masks the
    post-replace durability failure and misclassifies a retained final as
    status 3/7. Real READY, replace, and two failed fsync attempts make the
    retained-publication branch non-vacuous.
    """
    marker = tmp_path / "combined-fault-must-not-run.marker"
    adopted = jobs.JOBS_DIR / ".bd-jobs-script-combined-fault.sh"
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    adopted.write_text("touch %s; sleep 45\n" % marker, encoding="utf-8")
    args = type("A", (), {
        "host": "local", "purpose": "combined post-replace faults",
        "command": [], "script": str(adopted),
        "adopt_script": str(adopted), "origin": None,
        "request_id": "combined-post-replace-request",
        "attempt_token": "a" * 32,
    })()
    lock_fds = []
    events = []
    close_faults = []
    real_lock = jobs._lock_registry
    real_replace = os.replace
    real_close = os.close
    real_await_ready = jobs._LocalLaunch.await_ready

    def tracking_lock():
        fd = real_lock()
        lock_fds.append(fd)
        return fd

    def tracking_ready(launch, timeout=None):
        result = real_await_ready(launch, timeout=timeout)
        events.append("ready")
        return result

    def tracking_replace(src, dst):
        events.append("replace")
        return real_replace(src, dst)

    def fail_dir_fsync(path):
        events.append("dir-fsync")
        raise OSError(5, "PRIMARY injected registry directory fsync EIO")

    def fail_registry_lock_close(fd):
        if lock_fds and fd == lock_fds[-1] and not close_faults:
            close_faults.append(fd)
            real_close(fd)
            raise OSError(9, "SECONDARY injected registry lock close EBADF")
        return real_close(fd)

    monkeypatch.setattr(jobs, "_lock_registry", tracking_lock)
    monkeypatch.setattr(jobs._LocalLaunch, "await_ready", tracking_ready)
    monkeypatch.setattr(jobs.os, "replace", tracking_replace)
    monkeypatch.setattr(jobs, "_fsync_dir", fail_dir_fsync)
    monkeypatch.setattr(jobs.os, "close", fail_registry_lock_close)
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    try:
        rc = jobs.cmd_run(args)
        out, err = capsys.readouterr()

        assert events == ["ready", "replace", "dir-fsync", "dir-fsync"], (
            "the combined fault missed READY/replace or the retained second "
            "durability probe: %r; argv=%r" % (events, argvs))
        assert len(lock_fds) == 1 and close_faults == lock_fds, (
            "the secondary exact registry-lock close did not fire once: %r %r"
            % (lock_fds, close_faults))
        finals = list(jobs.JOBS_DIR.glob("*.json"))
        assert len(finals) == 1, finals
        entry = json.loads(finals[0].read_text(encoding="utf-8"))
        assert rc == jobs._EXIT_PUBLISHED_NOT_DURABLE, (rc, out, err)
        assert out.strip().splitlines() == [entry["id"]], out
        assert "PRIMARY injected registry directory fsync EIO" in err, err
        assert "SECONDARY injected registry lock close EBADF" in err, err
        assert "registry lock release is UNKNOWN" in err, err
        assert pathlib.Path(entry["log"]).exists(), entry
        assert adopted.exists() and str(adopted) in entry["owned_paths"], entry
        assert not marker.exists(), "the post-replace-failed job was released"
        assert _wait_until(lambda: jobs.proc_starttime(launched[0]) is None, 10), (
            "wrapper cleanup was not bounded")
    finally:
        for pid in launched:
            _reap_process(pid)


def test_reap_continues_after_forget_registry_unlock_close_unknown(
        jobs, monkeypatch, capsys):
    """One uncertain cleanup cannot disarm later live reaping.

    Mutation design N60: re-raising the first forget unlock error prevents the
    second independently led process group from being killed and forgotten.
    """
    procs = [subprocess.Popen(["sleep", "60"], start_new_session=True)
             for _ in range(2)]
    entries = []
    try:
        for proc in procs:
            entries.append(jobs.register(
                proc.pid, "live sibling %s" % proc.pid, "sleep 60"))
        entries = list(jobs.load_all())
        assert len(entries) == 2, entries
        assert all(proc.poll() is None for proc in procs), (
            "both reap targets must be independently live before invocation")
        assert all(os.getpgid(proc.pid) == proc.pid for proc in procs), (
            "both targets must lead separate process groups")

        real_unlock = jobs._unlock_registry
        unlocks = []

        def fail_first_unlock(fd):
            unlocks.append(fd)
            real_unlock(fd)
            if len(unlocks) == 1:
                raise OSError(5, "injected first forget unlock EIO")

        monkeypatch.setattr(jobs, "_unlock_registry", fail_first_unlock)

        rc = jobs.cmd_reap(type("A", (), {"id": None})())
        out, err = capsys.readouterr()

        assert len(unlocks) == 2, (
            "reap stopped before the sibling after unlock uncertainty: %r"
            % unlocks)
        assert all(_wait_until(lambda p=proc: p.poll() is not None, 10)
                   for proc in procs), "both live process groups must be gone"
        assert not list(jobs.JOBS_DIR.glob("*.json")), (
            "a later live sibling final survived the first unlock fault")
        assert rc == 1, "incomplete cleanup must retain refusal status"
        assert entries[0]["id"] in err, err
        assert "REGISTRY LOCK RELEASE UNKNOWN" in err, err
        assert all("reaped %s" % entry["id"] in out for entry in entries), out
        assert "2 reaped, 1 refused" in out, out
    finally:
        for proc in procs:
            if proc.poll() is None:
                os.killpg(proc.pid, 9)
            try:
                proc.wait(timeout=10)
            except (subprocess.TimeoutExpired, ChildProcessError, OSError):
                pass


def test_abort_process_wait_is_bounded_and_names_an_uncollected_child(
        jobs, monkeypatch):
    pid = 424242
    now = [100.0]
    waits = []
    signals = []

    monkeypatch.setattr(jobs, "_proc_ppid", lambda candidate: os.getpid())
    monkeypatch.setattr(jobs.os, "getpgid", lambda candidate: candidate)
    monkeypatch.setattr(jobs.os, "killpg",
                        lambda pgid, signal: signals.append((pgid, signal)))

    def never_collects(candidate, flags):
        waits.append((candidate, flags))
        return (0, 0)

    def advance(seconds):
        now[0] += seconds

    monkeypatch.setattr(jobs.os, "waitpid", never_collects)
    monkeypatch.setattr(jobs.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(jobs.time, "sleep", advance)

    result = jobs._terminate_and_wait(pid, timeout=0.11)

    assert signals == [(pid, 9)]
    assert len(waits) >= 2 and all(call == (pid, os.WNOHANG) for call in waits), (
        "the wait either blocked or never retried under its bound: %r" % waits)
    assert "UNKNOWN" in result and str(pid) in result and "not reaped" in result, (
        "the bounded wait hid the child it could not collect: %r" % result)


def test_forget_never_unlinks_a_log_outside_the_registry_directory(
        jobs, sleeper, tmp_path):
    """A `log` field is a PATH SOMEBODY GAVE US, not a licence to delete.

    `bd-sweep-run` and `bd-wedge-hunt` register a pid whose log is their own
    run directory's evidence file. `forget()` unlinking whatever the field
    named would delete an operator's evidence during an ordinary reap. The rule
    is ownership: this tool removes the logs it CREATED, which live as direct
    children of the registry directory.
    """
    p = sleeper()
    evidence = tmp_path / "formal-evidence.log"
    evidence.write_text("the caller's own evidence\n", encoding="utf-8")
    before = evidence.read_bytes()
    entry = jobs.register(p.pid, "external evidence", "sleep 60",
                          log=str(evidence))
    assert entry.get("log") == str(evidence), entry
    assert evidence.parent != jobs.JOBS_DIR, (
        "precondition: the evidence must live OUTSIDE the registry directory")

    jobs.forget(entry)

    assert not (jobs.JOBS_DIR / ("%s.json" % entry["id"])).exists(), (
        "forget() did not remove the entry it was asked to remove")
    assert evidence.exists() and evidence.read_bytes() == before, (
        "forget() deleted a log this tool never created: %s" % evidence)


def test_forget_removes_a_launcher_owned_log_that_is_a_direct_child(
        jobs, tmp_path, capsys):
    """OVER-SENSITIVITY CONTROL for the node above: the ownership rule must not
    be implemented as "never remove anything". A log this launcher created is
    recorded as owned, lives directly in the registry directory, and goes with
    its entry -- otherwise 744 orphaned files is what the promise costs."""
    entry = None
    try:
        rc = jobs.cmd_run(_run_args("owned-log", ["--", "bash", "-c",
                                                  "sleep 45"]))
        capsys.readouterr()
        assert rc == 0, rc
        entries = jobs.load_all()
        assert len(entries) == 1, entries
        entry = entries[0]
        log = pathlib.Path(entry["log"])
        assert entry.get("log_owned") is True, (
            "a launcher-created record does not declare that it owns its log, "
            "so nothing distinguishes it from a caller's evidence path: %r"
            % entry)
        assert log.parent == jobs.JOBS_DIR and log.exists(), (
            "precondition: the owned log must exist inside the registry")

        jobs.forget(entry)

        assert not log.exists(), (
            "the launcher's own log outlived its entry: %s" % log)
        assert not (jobs.JOBS_DIR / ("%s.json" % entry["id"])).exists()
    finally:
        if entry:
            _reap_process(entry["pid"])


def test_a_cleanup_that_did_not_happen_is_never_reported_as_removed(
        jobs, monkeypatch, tmp_path, capsys):
    """VERIFY BEFORE CLAIMING. "removed its log" printed over a failed unlink
    is a false cleanup record, and false cleanup evidence is worse than none:
    the operator stops looking."""
    marker = tmp_path / "must-never-exist.marker"
    recorded = {}
    real_open_job_log = jobs.open_job_log

    def watching_open(purpose):
        path, fd = real_open_job_log(purpose)
        recorded["path"] = path
        return path, fd

    refused = []

    def refusing_owned_unlink(path, observed):
        if str(path) == recorded.get("path"):
            refused.append(str(path))
            assert observed is not None, (
                "cleanup was not bound to the created log's identity")
            return (False,
                    "could not quarantine %s: injected EPERM removing the log"
                    % path,
                    False)
        pytest.fail("cleanup targeted an unowned path: %r" % (path,))

    def refusing_register(pid, purpose, cmd, origin=None, log=None, **kw):
        raise jobs.RegistrationError("INJECTED: registry publication refused")

    monkeypatch.setattr(jobs, "open_job_log", watching_open)
    monkeypatch.setattr(jobs, "_unlink_owned_identity",
                        refusing_owned_unlink)
    monkeypatch.setattr(jobs, "register", refusing_register)
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    try:
        rc = jobs.cmd_run(_run_args("unremovable-log",
                                    _marker_command(marker)))
        out, err = capsys.readouterr()

        assert len(refused) == 1, (
            "the unlink fault never fired: %r" % recorded)
        assert rc == jobs._EXIT_REGISTRATION_FAILED, rc
        assert "COULD NOT remove" in err and recorded["path"] in err, (
            "a cleanup that failed was not named: %r" % err)
        assert "removed its log" not in err, (
            "the launcher claimed a removal that did not happen: %r" % err)
        assert pathlib.Path(recorded["path"]).exists(), (
            "precondition: the log must still be there for the claim to be "
            "false")
        assert not marker.exists()
        assert out.strip() == ""
    finally:
        for pid in launched:
            _reap_process(pid)


def _release_failure_transaction(jobs, tmp_path):
    """A published transaction with no live child, for the release fault seam."""
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log = jobs.JOBS_DIR / "release-failure.log"
    log.write_text("held command output\n", encoding="utf-8")
    adopted = jobs.JOBS_DIR / ".bd-jobs-script-release-failure.sh"
    adopted.write_text("echo never-ran\n", encoding="utf-8")
    host = jobs.socket.gethostname()
    final = jobs.JOBS_DIR / ("%s-999999.json" % host)
    entry = {
        "id": "%s-999999" % host,
        "pid": 999999,
        "starttime": 1,
        "purpose": "release failure",
        "cmd": "echo never-ran",
        "host": host,
        "origin": host,
        "started_at": "2026-08-21T00:00:00Z",
        "log": str(log),
        "log_owned": True,
        "owned_paths": [str(adopted)],
    }
    final.write_text(json.dumps(entry), encoding="utf-8")
    entry["_path"] = str(final)
    launch = jobs._LocalLaunch("release failure", adopt_path=str(adopted))
    launch.entry = entry
    launch.log_path = str(log)
    launch.release_w = os.open("/dev/null", os.O_WRONLY)
    return launch, final, log, adopted


def test_release_failure_reports_the_cleanup_for_the_files_it_actually_removed(
        jobs, monkeypatch, tmp_path):
    """A successful withdrawal must not be described as a retained record."""
    launch, final, log, adopted = _release_failure_transaction(jobs, tmp_path)

    with monkeypatch.context() as mp:
        mp.setattr(jobs.os, "write", lambda *_: (_ for _ in ()).throw(
            OSError(32, "injected EPIPE releasing the gate")))
        with pytest.raises(jobs._LaunchAborted) as excinfo:
            launch.release()

    assert excinfo.value.status == jobs._EXIT_REGISTRATION_FAILED
    assert not final.exists() and not log.exists() and not adopted.exists(), (
        "the release rollback left owned state: %r"
        % [str(p) for p in (final, log, adopted) if p.exists()])
    message = excinfo.value.message
    assert "removed" in message.lower(), message
    assert all(str(path) in message for path in (final, log, adopted)), (
        "the release diagnostic did not account for every owned path: %r"
        % message)
    assert "KEPT its log" not in message and "retained record names" not in message, (
        "the diagnostic says files were retained after forget removed them: %r"
        % message)


def test_release_failure_preserves_owned_files_when_the_record_cannot_be_removed(
        jobs, monkeypatch, tmp_path):
    """A retained record keeps every artifact it still names."""
    launch, final, log, adopted = _release_failure_transaction(jobs, tmp_path)
    real_unlink = os.unlink
    attempts = []

    def refuse_final(path):
        if pathlib.Path(path) == final:
            attempts.append(pathlib.Path(path))
            raise PermissionError("injected retained final")
        return real_unlink(path)

    with monkeypatch.context() as mp:
        mp.setattr(jobs.os, "write", lambda *_: (_ for _ in ()).throw(
            OSError(32, "injected EPIPE releasing the gate")))
        mp.setattr(jobs.os, "unlink", refuse_final)
        with pytest.raises(jobs._LaunchAborted) as excinfo:
            launch.release()

    assert excinfo.value.status == jobs._EXIT_PUBLISHED_NOT_DURABLE, (
        "a retained target record was reported as 'nothing published', which "
        "makes the remote caller delete the script that record still owns")
    assert attempts == [final], "the retained-final fault did not fire once"
    assert final.exists(), "precondition: the injected final unlink must fail"
    assert log.exists() and adopted.exists(), (
        "forget deleted files still named by the retained record: log=%s script=%s"
        % (log.exists(), adopted.exists()))
    assert "COULD NOT remove" in excinfo.value.message, excinfo.value.message
    assert all(str(path) in excinfo.value.message for path in (final, log, adopted)), (
        "the retained-state diagnostic did not name every path: %r"
        % excinfo.value.message)


# ── v3.66.1206: recovery -- a registry that cannot be read is UNKNOWN ────────
#
# `load_all()` swallowed every unreadable final with `except (OSError,
# ValueError): continue`. That is silent data loss in the one structure whose
# whole job is to say what is running: a torn entry becomes a job with no
# record, which is row 212's subject, and every verb reported success over the
# gap. Loud is the remedy -- but a `raise` would be WORSE, because it disarms
# `reap`, `list`, `orphans` and `bd-cut-preflight` in one move. Every node
# below therefore asserts BOTH halves in the same test: the malformed file is
# named, AND the valid sibling was still acted on.

_TORN = b"{not json"


def _write_malformed(jobs_dir, name="otherhost-999999.json", body=_TORN):
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = jobs_dir / name
    path.write_bytes(body)
    return path


def _valid_entry_with_log(jobs, pid, tmp_path, purpose="unit test"):
    """A registered entry that HAS a log, so its `list` line cannot say UNKNOWN
    for the unrelated no-log reason (over-sensitivity risk 2)."""
    log = jobs.JOBS_DIR / "sibling.log"
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log.write_text("still writing\n", encoding="utf-8")
    return jobs.register(pid, purpose, "sleep 60", log=str(log))


def test_a_malformed_final_is_named_and_makes_list_unknown_without_hiding_valid_entries(
        jobs, sleeper, tmp_path, capsys):
    p = sleeper()
    entry = _valid_entry_with_log(jobs, p.pid, tmp_path)
    torn = _write_malformed(jobs.JOBS_DIR)
    assert jobs.proc_starttime(p.pid) is not None, (
        "precondition: the valid entry's process must be alive, or 'still "
        "listed' proves nothing")

    rc = jobs.cmd_list(type("A", (), {})())
    out, err = capsys.readouterr()

    assert entry["id"] in out and "LIVE" in out, (
        "a malformed sibling hid the valid entry -- an abort-first load_all() "
        "makes the registry unreadable instead of loud:\n%s" % out)
    assert "UNKNOWN: 1 of 2 registry file(s) unreadable" in out, (
        "list did not say how much of its own denominator it could not read: "
        "%r" % out)
    assert "UNREADABLE" in err and str(torn) in err, (
        "the unreadable file was not named for the operator: %r" % err)
    assert rc == 4, (
        "a registry it could not fully read was graded as an ordinary success "
        "(rc=%r): UNKNOWN is a failing third state" % rc)
    assert torn.read_bytes() == _TORN, "the malformed bytes were altered"


def test_list_prints_the_request_id_its_unknown_diagnostic_says_to_reconcile(
        jobs, sleeper, capsys):
    p = sleeper()
    entry = jobs.register(p.pid, "reconciliation target", "sleep 60",
                          request_id="row212-reconcile-me")

    rc = jobs.cmd_list(type("A", (), {})())
    out, err = capsys.readouterr()

    assert rc == 0 and err == ""
    assert entry["id"] in out and "row212-reconcile-me" in out, (
        "the transport diagnostic directs the operator to list by request id, "
        "but list hides that id: %r" % out)


def test_list_prints_a_dead_request_id_without_inventing_one_for_other_entries(
        jobs, sleeper, capsys):
    dead = sleeper()
    dead_entry = jobs.register(
        dead.pid, "finished reconciliation target", "sleep 60",
        request_id="row212-dead-request")
    dead.terminate()
    dead.wait(timeout=10)
    live = sleeper()
    live_entry = jobs.register(live.pid, "ordinary local job", "sleep 60")

    rc = jobs.cmd_list(type("A", (), {})())
    out, err = capsys.readouterr()

    assert rc == 0 and err == ""
    assert "DEAD  %-28s" % dead_entry["id"] in out
    assert "request-id: row212-dead-request" in out, (
        "a completed target cannot be reconciled by the request id the "
        "transport diagnostic tells the operator to use: %r" % out)
    assert live_entry["id"] in out
    assert out.count("request-id:") == 1, (
        "list invented a request-id field for an entry that has none: %r" % out)


def test_reap_still_reaps_every_valid_entry_and_names_the_malformed_one(
        jobs, sleeper, capsys):
    """THE F8 TRAP, and the most likely way this gets implemented wrong: a
    `load_all()` that raises makes `reap` refuse to reap anything, so one torn
    file on a host disarms the remedy for every real orphan on it."""
    p = sleeper()
    entry = jobs.register(p.pid, "unit test", "sleep 60")
    torn = _write_malformed(jobs.JOBS_DIR)
    assert [e["id"] for e in jobs.load_all()] == [entry["id"]], (
        "precondition: exactly one VALID entry must be visible")
    assert p.poll() is None, "precondition: the reap target must be alive"

    rc = jobs.cmd_reap(type("A", (), {"id": None})())
    out, err = capsys.readouterr()

    assert _wait_until(lambda: p.poll() is not None, 10), (
        "the valid entry was NOT reaped while a malformed sibling existed -- "
        "loudness disarmed the reaper:\n%s%s" % (out, err))
    assert "UNREADABLE" in err and str(torn) in err, err
    assert "KEPT" in err, (
        "the malformed final was not declared retained for adjudication: %r"
        % err)
    assert rc == 4, (
        "reap graded an unreadable registry as complete (rc=%r)" % rc)
    assert torn.read_bytes() == _TORN


def test_reap_reports_nonzero_when_the_entry_cleanup_is_retained(
        jobs, sleeper, monkeypatch, capsys):
    """A dead process plus a record that remains is partial cleanup, not PASS."""
    host = jobs.socket.gethostname()
    dead = sleeper()
    pid = dead.pid
    starttime = jobs.proc_starttime(pid)
    dead.terminate()
    dead.wait(timeout=10)
    assert jobs.proc_starttime(pid) is None, (
        "precondition: the record must name a process proven absent")
    entry = {
        "id": "%s-%d" % (host, pid), "pid": pid, "starttime": starttime,
        "purpose": "dead retained cleanup", "cmd": "sleep 60", "host": host,
        "origin": host, "started_at": "2026-08-21T00:00:00Z", "log": None,
    }
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    final.write_text(json.dumps(entry), encoding="utf-8")
    real_unlink = os.unlink
    attempts = []

    def refuse_final(path):
        if pathlib.Path(path) == final:
            attempts.append(pathlib.Path(path))
            raise PermissionError("injected retained reap final")
        return real_unlink(path)

    monkeypatch.setattr(jobs.os, "unlink", refuse_final)
    rc = jobs.cmd_reap(type("A", (), {"id": entry["id"]})())
    out, err = capsys.readouterr()

    assert attempts == [final], "the injected final-unlink fault did not fire once"
    assert final.exists(), "the final disappeared despite the injected refusal"
    assert rc == 1, "retained cleanup must have the refusal status"
    assert "REFUSED" in err and "COULD NOT remove" in err, (
        "reap hid its retained cleanup: stdout=%r stderr=%r" % (out, err))
    assert "0 reaped, 1 refused" in out, (
        "the retained record was counted as a clean reap: %r" % out)


def test_reap_reports_owned_artifact_cleanup_failure_after_durable_withdrawal(
        jobs, sleeper, monkeypatch, capsys):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-unlink-refused.sh"
    owned.write_text("echo retained\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "artifact cleanup refusal", "bash script",
        owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    p.terminate()
    p.wait(timeout=10)
    assert jobs.proc_starttime(p.pid) is None
    real_unlink = os.unlink
    attempts = []

    def refuse_quarantine(path):
        candidate = pathlib.Path(path)
        if (candidate.parent == jobs.JOBS_DIR
                and candidate.name.startswith(".bd-jobs-cleanup-")
                and candidate.suffix == ".tmp"):
            attempts.append(candidate)
            raise PermissionError("injected quarantine-artifact refusal")
        return real_unlink(path)

    monkeypatch.setattr(jobs.os, "unlink", refuse_quarantine)
    rc = jobs.cmd_reap(type("A", (), {"id": entry["id"]})())
    out, err = capsys.readouterr()

    assert len(attempts) == 1, "the quarantine-unlink fault did not fire once"
    quarantine = attempts[0]
    assert not final.exists(), "the withdrawn final unexpectedly survived"
    assert not owned.exists(), "the owned path was not moved into quarantine"
    assert quarantine.exists(), "the injected retained quarantine disappeared"
    assert quarantine.read_text(encoding="utf-8") == "echo retained\n", (
        "the quarantine did not retain the owned artifact's exact bytes")
    assert (rc == 1 and "REFUSED" in err and str(owned) in err
            and str(quarantine) in err)
    assert "0 reaped, 1 refused" in out


def test_reap_by_id_still_works_while_a_malformed_sibling_exists(
        jobs, sleeper, capsys):
    p = sleeper()
    entry = jobs.register(p.pid, "unit test", "sleep 60")
    torn = _write_malformed(jobs.JOBS_DIR)

    rc = jobs.cmd_reap(type("A", (), {"id": entry["id"]})())
    capsys.readouterr()

    assert _wait_until(lambda: p.poll() is not None, 10), (
        "reap --id could not reach a named entry because of an unrelated "
        "malformed file")
    assert rc == 4
    assert torn.exists(), "reap --id deleted a file it was not asked about"


def test_orphans_keeps_its_exact_count_sentence_and_names_the_malformed_read(
        jobs, sleeper, capsys):
    """`bd-cut-preflight` regexes the COUNT out of this sentence and grades on
    it. A `raise` implementation prints no stdout at all, which turns that row
    UNKNOWN on every host; a reworded sentence does the same silently."""
    p = sleeper()
    jobs.register(p.pid, "unit test", "sleep 60")
    torn = _write_malformed(jobs.JOBS_DIR)

    rc = jobs.cmd_orphans(type("A", (), {})())
    out, err = capsys.readouterr()

    assert re.search(r"(\d+)\s+unregistered\s+pytest\s+process", out), (
        "the preflight's own regex no longer matches this tool's stdout: %r"
        % out)
    assert "UNREADABLE" in err and str(torn) in err, err
    assert "denominator" in err, (
        "orphans did not say its registered-pid denominator was incomplete: %r"
        % err)
    assert rc == 4, (
        "orphans reported a complete answer over a registry it could not fully "
        "read (rc=%r)" % rc)


def test_orphans_binds_a_registry_pid_to_its_recorded_starttime(
        jobs, sleeper, monkeypatch, capsys):
    """A stale same-PID record belongs to the old process, not the live pytest."""
    p = sleeper()
    entry = jobs.register(p.pid, "old owner", "sleep 60")
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    stale = dict(entry, starttime=entry["starttime"] + 1)
    stale.pop("_path", None)
    final.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr(jobs.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(
                            a[0], 0,
                            "%d python3 python3 -m pytest tests/test_x.py\n" % p.pid,
                            ""))

    rc = jobs.cmd_orphans(type("A", (), {})())
    out, err = capsys.readouterr()

    assert err == "" and rc == 1
    assert "ORPHAN pid=%-7s" % p.pid in out, (
        "a stale record with the same PID suppressed the live pytest process: %r"
        % out)

    current = dict(entry)
    current.pop("_path", None)
    final.write_text(json.dumps(current), encoding="utf-8")
    rc = jobs.cmd_orphans(type("A", (), {})())
    out, err = capsys.readouterr()

    assert err == "" and rc == 0
    assert "ORPHAN pid=%-7s" % p.pid not in out
    assert "0 unregistered pytest process(es)" in out, (
        "the matching pid/starttime record failed to suppress its own process: "
        "%r" % out)


def test_orphans_does_not_report_a_process_gone_after_the_ps_snapshot(
        jobs, sleeper, monkeypatch, capsys):
    """A process absent at the identity probe is gone, not an orphan."""
    p = sleeper()
    jobs.register(p.pid, "snapshot owner", "sleep 60")
    monkeypatch.setattr(jobs.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(
                            a[0], 0,
                            "%d python3 python3 -m pytest tests/test_x.py\n" % p.pid,
                            ""))
    monkeypatch.setattr(jobs, "proc_starttime", lambda pid: None)

    rc = jobs.cmd_orphans(type("A", (), {})())
    out, err = capsys.readouterr()

    assert err == "" and rc == 0
    assert "ORPHAN" not in out
    assert "0 unregistered pytest process(es)" in out


def test_no_verb_ever_deletes_a_malformed_final(jobs, sleeper, capsys):
    """Retention for operator adjudication. Bytes nobody can parse are still
    the only evidence of what went wrong, and a tool that tidies them away
    destroys the thing an operator would need to reconstruct."""
    p = sleeper()
    jobs.register(p.pid, "unit test", "sleep 60")
    torn = _write_malformed(jobs.JOBS_DIR)
    before = torn.read_bytes()

    jobs.cmd_list(type("A", (), {})())
    jobs.cmd_orphans(type("A", (), {})())
    jobs.cmd_reap(type("A", (), {"id": None})())
    capsys.readouterr()

    assert torn.exists(), "a verb deleted the malformed final"
    assert torn.read_bytes() == before, "a verb rewrote the malformed final"


def test_a_registry_containing_only_valid_entries_says_UNKNOWN_nowhere(
        jobs, sleeper, tmp_path, capsys):
    """OVER-SENSITIVITY CONTROL. `progress()` already prints the literal
    UNKNOWN for a log-less entry, so this entry is given a log: the token must
    then be absent for the ONLY remaining reason, which is the new one."""
    p = sleeper()
    _valid_entry_with_log(jobs, p.pid, tmp_path)

    rc = jobs.cmd_list(type("A", (), {})())
    out, err = capsys.readouterr()

    assert rc == 0, "a healthy registry was graded UNKNOWN (rc=%r)" % rc
    assert "UNKNOWN" not in out + err, (
        "the loudness fires on a registry with nothing wrong with it: %r"
        % (out + err))


def test_a_staged_temp_in_flight_is_not_read_as_a_malformed_entry(
        jobs, sleeper, tmp_path, capsys):
    """The staging temp is deliberately outside the `*.json` population. A
    scan-everything implementation would turn every ordinary publication into a
    permanent UNKNOWN -- and the temp is half-written by design."""
    p = sleeper()
    _valid_entry_with_log(jobs, p.pid, tmp_path)
    (jobs.JOBS_DIR / ".bd-jobs-entry-inflight.tmp").write_bytes(_TORN)

    rows = jobs.load_all()
    rc = jobs.cmd_list(type("A", (), {})())
    out, err = capsys.readouterr()

    assert len(rows) == 1, "a staged temp was read as a registry entry: %r" % rows
    assert rc == 0 and "UNKNOWN" not in out + err, (
        "normal staging was reported as a corrupt registry: %r" % (out + err))


def test_register_rejects_a_malformed_record_before_staging(
        jobs, sleeper, monkeypatch):
    """The sole writer must not publish bytes its own reader rejects."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    real_mkstemp = jobs.tempfile.mkstemp
    staged = []

    def watching_mkstemp(*args, **kwargs):
        staged.append((args, kwargs))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(jobs.tempfile, "mkstemp", watching_mkstemp)
    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, ["not a purpose string"], "sleep 60")

    assert "purpose" in str(excinfo.value), excinfo.value
    assert staged == [], (
        "schema validation ran only after staging began: %r" % staged)
    assert list(jobs.JOBS_DIR.iterdir()) == [], (
        "the writer left a final or temp for a record its reader rejects")


# MUTATION DESIGN (durable spec intentionally out of this task): bypassing the
# shared path helper before staging, dropping its NUL check, or swallowing an
# os.fsencode failure is caught by the three fault cases below; the Unicode
# control prevents replacing that validation with an ASCII-only rule.
def test_register_rejects_unusable_filesystem_paths_before_staging(
        jobs, sleeper, monkeypatch):
    """JSON can encode strings that the host filesystem cannot consume."""
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    real_mkstemp = jobs.tempfile.mkstemp
    staged = []

    def watching_mkstemp(*args, **kwargs):
        staged.append((args, kwargs))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(jobs.tempfile, "mkstemp", watching_mkstemp)
    cases = [
        ("log", {"log": str(jobs.JOBS_DIR / "log") + "\0suffix"}),
        ("owned_paths", {
            "owned_paths": [str(jobs.JOBS_DIR / "good"),
                            str(jobs.JOBS_DIR / "owned") + "\0suffix"]}),
        ("owned_paths", {
            "owned_paths": [str(jobs.JOBS_DIR / "unencodable") + "\ud800"]}),
    ]
    for field, kwargs in cases:
        proc = sleeper()
        with pytest.raises(jobs.RegistrationError) as excinfo:
            jobs.register(proc.pid, "nul path", "sleep 60", **kwargs)
        assert field in str(excinfo.value) and "filesystem" in str(
            excinfo.value).lower(), (
            "the shared schema did not name the unsafe path field: %r"
            % excinfo.value)

    assert staged == [], (
        "NUL path validation ran only after publication staging began: %r"
        % staged)
    assert list(jobs.JOBS_DIR.iterdir()) == [], (
        "a NUL-bearing path produced registry residue")


def test_representable_unicode_filesystem_paths_survive_publish_and_read(
        jobs, sleeper):
    """OVER-SENSITIVITY CONTROL: non-ASCII is not itself an unusable path."""
    proc = sleeper()
    log = str(jobs.JOBS_DIR / "progress-π-日志.log")
    owned = str(jobs.JOBS_DIR / "aux-mañana-雪.dat")

    published = jobs.register(
        proc.pid, "unicode paths", "sleep 60", log=log,
        owned_paths=[owned])
    rows = jobs.load_all()

    assert rows.malformed == []
    assert len(rows) == 1 and rows[0]["id"] == published["id"]
    assert rows[0]["log"] == log and rows[0]["owned_paths"] == [owned]


def test_a_final_is_validated_as_a_path_and_a_schema_before_it_is_believed(
        jobs, sleeper, tmp_path):
    """An entry is a KILL INSTRUCTION, so "it parsed" is not enough.

    Each case below is a file a reader could be handed. A symlink is not this
    tool's file; duplicate keys mean two different readers disagree about the
    same record; `true` is an int in Python and would be signalled as pid 1;
    and a name that disagrees with its own contents means one of the two is a
    lie, so neither can be used to decide what to kill.
    """
    p = sleeper()
    host = jobs.socket.gethostname()
    good = {"id": "%s-%d" % (host, p.pid), "pid": p.pid,
            "starttime": jobs.proc_starttime(p.pid), "purpose": "valid",
            "cmd": "sleep 60", "host": host, "origin": host,
            "started_at": "2026-08-21T00:00:00Z", "log": None}

    def record(pid, omit=(), **updates):
        value = dict(good, id="%s-%d" % (host, pid), pid=pid)
        value.update(updates)
        for key in omit:
            value.pop(key, None)
        return value

    cases = {
        "symlink": ("%s-4242.json" % host, "symlink"),
        "duplicate-keys": ("%s-4243.json" % host,
                           '{"id": "%s-4243", "pid": 4243, "pid": 1, '
                           '"host": "%s", "starttime": 1}' % (host, host)),
        # `True` must be its OWN case, and the id has to AGREE with it. A bool
        # pid formats as 1 under `%d`, so an entry claiming pid 4244 is already
        # caught by the id/pid rule below and would prove nothing about the
        # bool check -- measured: with the bool check removed this case still
        # failed, and the mutant escaped. Named `<host>-1` it is exactly what
        # the check is for: an entry that would otherwise be VALID and would
        # send `reap` at pid 1.
        "bool-pid": ("%s-1.json" % host,
                     json.dumps(dict(good, id="%s-1" % host, pid=True))),
        "name-disagrees": ("%s-4245.json" % host,
                           json.dumps(record(9999))),
        "id-disagrees-with-pid": ("%s-4246.json" % host,
                                  json.dumps(dict(good, id="%s-4246" % host,
                                                  pid=4247))),
        "not-an-object": ("%s-4248.json" % host, "[]"),
        "missing-purpose": (
            "%s-4249.json" % host,
            json.dumps(record(4249, omit=("purpose",)))),
        "null-starttime": (
            "%s-4250.json" % host,
            json.dumps(record(4250, starttime=None))),
        "missing-cmd": ("%s-4251.json" % host,
                        json.dumps(record(4251, omit=("cmd",)))),
        "missing-origin": ("%s-4252.json" % host,
                           json.dumps(record(4252, omit=("origin",)))),
        "missing-started-at": (
            "%s-4253.json" % host,
            json.dumps(record(4253, omit=("started_at",)))),
        "purpose-not-string": ("%s-4254.json" % host,
                               json.dumps(record(4254, purpose=[]))),
        "zero-starttime": ("%s-4255.json" % host,
                           json.dumps(record(4255, starttime=0))),
        "bool-starttime": ("%s-4256.json" % host,
                           json.dumps(record(4256, starttime=True))),
        "log-not-path": ("%s-4257.json" % host,
                         json.dumps(record(4257, log=7))),
        "log-owned-not-bool": (
            "%s-4258.json" % host,
            json.dumps(record(4258, log_owned="yes"))),
        "owned-paths-not-list": (
            "%s-4259.json" % host,
            json.dumps(record(4259, owned_paths="path"))),
        "owned-paths-non-string": (
            "%s-4260.json" % host,
            json.dumps(record(4260, owned_paths=[7]))),
        "owned-paths-empty": (
            "%s-4261.json" % host,
            json.dumps(record(4261, owned_paths=[""]))),
        "request-id-not-string": (
            "%s-4262.json" % host,
            json.dumps(record(4262, request_id=7))),
        "run-marker-empty": (
            "%s-4263.json" % host,
            json.dumps(record(4263, run_marker=""))),
        "pid-not-integer": (
            "%s-4265.json" % host,
            json.dumps(dict(record(4265), pid="x"))),
        "pid-nonpositive": (
            "%s-0.json" % host,
            json.dumps(record(0))),
        "missing-id": (
            "%s-4266.json" % host,
            json.dumps(record(4266, omit=("id",)))),
        "id-not-string": (
            "%s-4267.json" % host,
            json.dumps(record(4267, id=7))),
        "empty-id": (
            "%s-4268.json" % host,
            json.dumps(record(4268, id=""))),
        "missing-host": (
            "None-4269.json",
            json.dumps(record(4269, omit=("host",), id="None-4269"))),
        "host-not-string": (
            "7-4270.json",
            json.dumps(record(4270, host=7, id="7-4270"))),
        "empty-host": (
            "-4271.json",
            json.dumps(record(4271, host="", id="-4271"))),
    }
    expected_reason = {
        "missing-purpose": "purpose", "null-starttime": "starttime",
        "missing-cmd": "cmd", "missing-origin": "origin",
        "missing-started-at": "started_at",
        "purpose-not-string": "purpose", "zero-starttime": "starttime",
        "bool-starttime": "starttime", "log-not-path": "log",
        "log-owned-not-bool": "log_owned",
        "owned-paths-not-list": "owned_paths",
        "owned-paths-non-string": "owned_paths",
        "owned-paths-empty": "owned_paths",
        "request-id-not-string": "request_id",
        "run-marker-empty": "run_marker",
        "name-disagrees": "filename",
        "pid-not-integer": "pid", "pid-nonpositive": "pid",
        "missing-id": "id", "id-not-string": "id", "empty-id": "id",
        "missing-host": "host", "host-not-string": "host",
        "empty-host": "host",
    }
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    (jobs.JOBS_DIR / ("%s.json" % good["id"])).write_text(json.dumps(good),
                                                          encoding="utf-8")
    good_without_log = record(4264, omit=("log",))
    (jobs.JOBS_DIR / ("%s.json" % good_without_log["id"])).write_text(
        json.dumps(good_without_log), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps(dict(good, id="%s-4242" % host, pid=4242)),
                         encoding="utf-8")
    for name, body in cases.values():
        target = jobs.JOBS_DIR / name
        if body == "symlink":
            target.symlink_to(elsewhere)
        else:
            target.write_text(body, encoding="utf-8")

    rows = jobs.load_all()

    assert {e["id"] for e in rows} == {good["id"], good_without_log["id"]}, (
        "a final that is not this tool's, or that contradicts itself, was "
        "accepted as a kill instruction: %r" % [e.get("id") for e in rows])
    named = " ".join(str(path) for path, _ in rows.malformed)
    missing = [label for label, (name, _) in cases.items()
               if str(jobs.JOBS_DIR / name) not in named]
    assert not missing, (
        "these invalid finals were dropped silently rather than named: %r"
        % missing)
    reasons = {path.name: reason for path, reason in rows.malformed}
    wrong_reasons = {
        label: reasons.get(name)
        for label, (name, _) in cases.items()
        if label in expected_reason
        and expected_reason[label] not in (reasons.get(name) or "")
    }
    assert not wrong_reasons, (
        "schema diagnostics did not name the field that made each kill "
        "instruction invalid: %r" % wrong_reasons)


def test_a_nul_log_is_malformed_and_list_keeps_a_valid_sibling_visible(
        jobs, sleeper, capsys):
    valid_proc = sleeper()
    valid = jobs.register(valid_proc.pid, "valid sibling", "sleep 60")
    host = jobs.socket.gethostname()
    bad_log = jobs.JOBS_DIR / ("%s-1.json" % host)
    bad_log.write_text(json.dumps({
        "id": "%s-1" % host, "pid": 1, "starttime": 1,
        "purpose": "nul log", "cmd": "sleep 60", "host": host,
        "origin": host, "started_at": "2026-08-21T00:00:00Z",
        "log": str(jobs.JOBS_DIR / "bad-log") + "\0tail",
        "log_owned": True,
    }), encoding="utf-8")
    before = bad_log.read_bytes()

    rc = jobs.cmd_list(type("A", (), {})())
    out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REGISTRY_UNKNOWN, (out, err)
    assert valid["id"] in out, "list stopped before its valid sibling"
    assert str(bad_log) in err and "log" in err and "NUL" in err, err
    assert bad_log.read_bytes() == before, "list changed malformed evidence"


def test_an_unencodable_owned_path_is_malformed_and_reap_continues_a_valid_sibling(
        jobs, capsys):
    owned = subprocess.Popen(["sleep", "60"], start_new_session=True)
    valid = jobs.register(owned.pid, "valid reap sibling", "sleep 60")
    host = jobs.socket.gethostname()
    bad_owned = jobs.JOBS_DIR / ("%s-2.json" % host)
    bad_owned.write_text(json.dumps({
        "id": "%s-2" % host, "pid": 2, "starttime": 1,
        "purpose": "unencodable owned path", "cmd": "sleep 60", "host": host,
        "origin": host, "started_at": "2026-08-21T00:00:00Z", "log": None,
        "owned_paths": [str(jobs.JOBS_DIR / "good"),
                        str(jobs.JOBS_DIR / "bad-owned") + "\ud800"],
    }), encoding="utf-8")
    before = bad_owned.read_bytes()

    try:
        rc = jobs.cmd_reap(type("A", (), {"id": None})())
        out, err = capsys.readouterr()
        assert rc == jobs._EXIT_REGISTRY_UNKNOWN, (out, err)
        assert "reaped %s" % valid["id"] in out, (
            "reap stopped at an unencodable path before the valid sibling: %r"
            % out)
        assert _wait_until(lambda: owned.poll() is not None, 10)
        assert str(bad_owned) in err and "owned_paths" in err, err
        assert "filesystem" in err.lower() or "encode" in err.lower(), err
        assert bad_owned.read_bytes() == before, (
            "reap changed or deleted malformed evidence")
    finally:
        if owned.poll() is None:
            os.killpg(owned.pid, 9)
        try:
            owned.wait(timeout=10)
        except (subprocess.TimeoutExpired, ChildProcessError, OSError):
            pass


def test_path_consumers_defensively_grade_unusable_legacy_values_unknown(jobs):
    assert jobs.progress({"log": "legacy\0log"}) == ("UNKNOWN", None)
    assert not jobs._log_is_a_direct_child_of_the_registry("legacy\0log")
    identity = jobs._path_identity("unencodable\ud800")
    assert identity[0] == "unreadable" and "Unicode" in identity[1], identity


# ── v3.66.1206: publication collisions, decided only by pid + starttime ──────


def test_publication_is_idempotent_for_the_same_pid_and_starttime(jobs, sleeper):
    """A retry must not rewrite a record another reader may already be acting
    on. Same pid AND same start time is the same job, so the existing record is
    returned unchanged -- not refused, and not overwritten."""
    p = sleeper()
    first = jobs.register(p.pid, "the original purpose", "sleep 60")
    final = jobs.JOBS_DIR / ("%s.json" % first["id"])
    before = final.read_bytes()

    again = jobs.register(p.pid, "a DIFFERENT purpose", "sleep 60")

    assert final.read_bytes() == before, (
        "the existing valid record was overwritten by a same-identity retry")
    assert again["purpose"] == "the original purpose", (
        "register() returned the new metadata rather than the record that is "
        "actually on disk: %r" % again)
    assert len(list(jobs.JOBS_DIR.glob("*.json"))) == 1


def test_publication_replaces_a_final_whose_recorded_starttime_is_provably_stale(
        jobs, sleeper):
    """PID REUSE, from the registry's side. A prior record for this pid whose
    start time does not match the LIVE process is a record of a process that no
    longer exists; keeping it would make the new job invisible."""
    p = sleeper()
    entry = jobs.register(p.pid, "the live job", "sleep 60")
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    stale = dict(entry, starttime=entry["starttime"] - 1,
                 purpose="an older owner of this pid")
    final.write_text(json.dumps(stale), encoding="utf-8")
    assert jobs.proc_starttime(p.pid) != stale["starttime"], (
        "precondition: the prior record must be PROVABLY stale")

    fresh = jobs.register(p.pid, "the live job", "sleep 60")

    on_disk = json.loads(final.read_text(encoding="utf-8"))
    assert on_disk["purpose"] == "the live job", (
        "a provably stale record survived and hid the running job: %r" % on_disk)
    assert on_disk["starttime"] == fresh["starttime"]


def test_stale_collision_transfers_prior_owned_artifacts_to_the_replacement(
        jobs, sleeper, tmp_path):
    """Replacing the record transfers cleanup authority instead of orphaning it."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    prior_log = jobs.JOBS_DIR / "prior-owner.log"
    prior_log.write_text("old output\n", encoding="utf-8")
    prior_script = jobs.JOBS_DIR / ".bd-jobs-script-prior.sh"
    prior_script.write_text("echo old\n", encoding="utf-8")
    foreign = tmp_path / "operator-owned.sh"
    foreign.write_text("echo keep\n", encoding="utf-8")
    prior = jobs.register(
        p.pid, "prior owner", "sleep 60", log=str(prior_log), log_owned=True,
        owned_paths=[str(prior_script), str(foreign)])
    final = jobs.JOBS_DIR / ("%s.json" % prior["id"])
    stale = dict(prior, starttime=prior["starttime"] - 1)
    stale.pop("_path", None)
    final.write_text(json.dumps(stale), encoding="utf-8")

    replacement = jobs.register(p.pid, "replacement owner", "sleep 60")
    on_disk = json.loads(final.read_text(encoding="utf-8"))

    inherited = set(on_disk.get("owned_paths") or [])
    assert inherited.issuperset({str(prior_log), str(prior_script)}), (
        "replacement abandoned artifacts owned by the displaced record: %r"
        % on_disk)
    assert str(foreign) not in inherited, (
        "replacement inherited a path outside the registry as cleanup "
        "authority: %r" % on_disk)
    loaded = [e for e in jobs.load_all() if e["id"] == replacement["id"]]
    assert len(loaded) == 1, "the transferred authority did not survive disk"
    jobs.forget(loaded[0])
    assert not prior_log.exists() and not prior_script.exists(), (
        "transferred cleanup authority was not executable")
    assert foreign.exists(), "transferred cleanup deleted an operator-owned path"


def test_stale_collision_retains_the_replacement_if_its_first_dir_fsync_fails(
        jobs, sleeper, monkeypatch):
    """Withdrawing a replacement would also withdraw the only surviving copy
    of the displaced record's cleanup authority."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    prior_script = jobs.JOBS_DIR / ".bd-jobs-script-displaced.sh"
    prior_script.write_text("echo old\n", encoding="utf-8")
    prior = jobs.register(
        p.pid, "displaced owner", "sleep 60",
        owned_paths=[str(prior_script)])
    final = jobs.JOBS_DIR / ("%s.json" % prior["id"])
    stale = dict(prior, starttime=prior["starttime"] - 1)
    stale.pop("_path", None)
    final.write_text(json.dumps(stale), encoding="utf-8")
    real_fsync_dir = jobs._fsync_dir
    calls = []

    def fail_first(path):
        calls.append(pathlib.Path(path))
        if len(calls) == 1:
            raise OSError(5, "injected first directory fsync failure")
        return real_fsync_dir(path)

    monkeypatch.setattr(jobs, "_fsync_dir", fail_first)
    with pytest.raises(jobs.PublishedNotDurable):
        jobs.register(p.pid, "replacement owner", "sleep 60")

    assert calls == [jobs.JOBS_DIR], (
        "the failed stale replacement was withdrawn through a second fsync "
        "sequence, abandoning the displaced record: %r" % calls)
    assert final.exists(), "the sole surviving cleanup authority was withdrawn"
    on_disk = json.loads(final.read_text(encoding="utf-8"))
    assert on_disk["purpose"] == "replacement owner", on_disk
    assert str(prior_script) in (on_disk.get("owned_paths") or []), on_disk
    assert prior_script.exists()


def test_reap_never_deletes_a_replacement_published_after_its_snapshot(
        jobs, sleeper, monkeypatch, capsys):
    """The stale-reader schedule cannot unlink the new same-name record."""
    p = sleeper()
    host = jobs.socket.gethostname()
    actual = jobs.proc_starttime(p.pid)
    old = {
        "id": "%s-%d" % (host, p.pid), "pid": p.pid,
        "starttime": actual - 1, "purpose": "old owner", "cmd": "sleep 60",
        "host": host, "origin": host,
        "started_at": "2026-08-21T00:00:00Z", "log": None,
        "request_id": "old-request",
    }
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    final = jobs.JOBS_DIR / ("%s.json" % old["id"])
    final.write_text(json.dumps(old), encoding="utf-8")
    replacement = dict(old, starttime=actual, purpose="new owner",
                       request_id="new-request")
    real_proc_starttime = jobs.proc_starttime
    interleaved = []

    def publish_between_snapshot_and_forget(pid):
        if not interleaved:
            interleaved.append(True)
            jobs._publish_entry(replacement)
        return real_proc_starttime(pid)

    monkeypatch.setattr(jobs, "proc_starttime", publish_between_snapshot_and_forget)
    rc = jobs.cmd_reap(type("A", (), {"id": old["id"]})())
    capsys.readouterr()

    assert interleaved == [True], "the replacement was never published in the gap"
    assert rc == 1, "a changed-record retention must have refusal status"
    assert final.exists(), "stale reap unlinked the concurrently published record"
    on_disk = json.loads(final.read_text(encoding="utf-8"))
    assert on_disk["request_id"] == "new-request", on_disk
    assert p.poll() is None, (
        "reap acted on the concurrently published live replacement")


def test_publication_refuses_and_retains_an_unreadable_prior_final(jobs, sleeper):
    """Bytes we cannot read are bytes we cannot decide about. Overwriting them
    destroys the only evidence; the refusal names the exact path instead."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    final = jobs.JOBS_DIR / ("%s-%d.json" % (jobs.socket.gethostname(), p.pid))
    final.write_bytes(_TORN)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "unit test", "sleep 60")

    assert final.read_bytes() == _TORN, (
        "an unreadable prior record was overwritten by a new publication")
    assert str(final) in str(excinfo.value), (
        "the refusal did not name the retained path: %r" % str(excinfo.value))
    assert list(jobs.JOBS_DIR.glob(".bd-jobs-entry-*")) == [], (
        "the refused publication left its staged temp behind")


def test_publication_into_an_empty_slot_is_unaffected_by_the_collision_rule(
        jobs, sleeper):
    """OVER-SENSITIVITY CONTROL: the rule must not be implemented as "always
    refuse when anything is there", which an empty-slot test would not catch
    but a broken tool would still pass."""
    p = sleeper()
    entry = jobs.register(p.pid, "first publication", "sleep 60")
    assert [e["id"] for e in jobs.load_all()] == [entry["id"]]


def test_the_collision_decision_is_made_under_a_registry_lock(
        jobs, sleeper, monkeypatch, tmp_path):
    """READ, DECIDE, REPLACE and FSYNC are one critical section.

    Two launchers registering the same pid at once would otherwise both read
    "absent", both stage, and both replace -- and the loser's record would be
    the one an operator reaps. The probe runs in a SEPARATE PROCESS because
    flock is per open file description: a same-process attempt is granted and
    would prove nothing.
    """
    # THE PROBE ASKS FOR A **SHARED** LOCK, and that is the load-bearing
    # detail. An exclusive request is refused by a shared holder too, so an
    # LOCK_EX probe cannot tell exclusion from sharing -- measured: with
    # LOCK_EX mutated to LOCK_SH in production, an exclusive probe still
    # reported BLOCKED and the mutant escaped. A shared request models the
    # SECOND PUBLISHER, which is the process this lock exists to keep out: it
    # is refused only while the holder is exclusive.
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import fcntl, os, sys\n"
        "fd = os.open(sys.argv[1], os.O_RDONLY)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)\n"
        "except OSError:\n"
        "    print('BLOCKED')\n"
        "else:\n"
        "    print('FREE')\n"
        "    fcntl.flock(fd, fcntl.LOCK_UN)\n"
        "os.close(fd)\n", encoding="utf-8")

    def probe_lock(path):
        r = subprocess.run([sys.executable, str(probe), str(path)],
                           capture_output=True, text=True, timeout=60)
        return r.stdout.strip() or r.stderr.strip()

    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    assert probe_lock(jobs.JOBS_DIR) == "FREE", (
        "precondition: the registry must not be locked before the transaction")

    observed = []
    real_fsync_dir = jobs._fsync_dir

    def watching(path):
        observed.append(probe_lock(path))
        return real_fsync_dir(path)

    monkeypatch.setattr(jobs, "_fsync_dir", watching)
    jobs.register(p.pid, "locked publication", "sleep 60")

    assert observed == ["BLOCKED"], (
        "the registry was not held while the entry was being published: %r -- "
        "a concurrent publisher could read, decide and replace inside this "
        "window" % observed)
    assert probe_lock(jobs.JOBS_DIR) == "FREE", (
        "the registry lock outlived the transaction that took it")


# ── v3.66.1206: cleanup touches only proven-owned, normalized paths ──────────


def test_forget_removes_an_owned_auxiliary_path_and_never_an_unowned_one(
        jobs, sleeper, tmp_path):
    """A delegated script is copied into the registry directory and ADOPTED by
    the entry, so the entry owns it. Everything else an entry merely mentions
    is somebody else's file."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-probe.sh"
    owned.write_text("echo hi\n", encoding="utf-8")
    foreign = tmp_path / "operators-own-script.sh"
    foreign.write_text("echo not yours\n", encoding="utf-8")

    entry = jobs.register(p.pid, "adopted", "bash script", log=None)
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    on_disk = json.loads(final.read_text(encoding="utf-8"))
    on_disk["owned_paths"] = [str(owned), str(foreign)]
    final.write_text(json.dumps(on_disk), encoding="utf-8")

    jobs.forget(jobs.load_all()[0])

    assert not owned.exists(), (
        "the entry's own copied script outlived it: %s" % owned)
    assert foreign.exists(), (
        "forget() unlinked a path outside the registry because an entry named "
        "it -- ownership is a location rule, not a mention: %s" % foreign)


def test_forget_keeps_owned_artifacts_until_final_absence_is_durable(
        jobs, sleeper, monkeypatch):
    """A final that can return after a crash must not return missing its files."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-durable-withdrawal.sh"
    owned.write_text("echo keep until durable\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "durable withdrawal", "bash script",
        owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    calls = []

    def fail_fsync(path):
        calls.append(pathlib.Path(path))
        raise OSError(5, "injected withdrawal fsync failure")

    monkeypatch.setattr(jobs, "_fsync_dir", fail_fsync)
    outcome = jobs.forget(entry)

    assert calls == [jobs.JOBS_DIR], (
        "forget never tried to make final absence durable: %r" % calls)
    assert outcome["entry_removed"] is True
    assert outcome["entry_absent_durable"] is False
    assert outcome["cleanup_complete"] is False
    assert not final.exists(), (
        "the injected fault is after unlink; the visible final should be absent")
    assert owned.exists(), (
        "forget deleted an artifact before final absence was durable")


def _assert_quarantine_setup_failure(jobs, sleeper, monkeypatch, fault):
    """Cleanup setup failure is a named retained path, never a traceback."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    owned = jobs.JOBS_DIR / (".bd-jobs-script-%s-failure.sh" % fault)
    owned.write_text("echo retained\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "%s cleanup failure" % fault, "bash script",
        owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    real_mkstemp = jobs.tempfile.mkstemp
    real_close = os.close
    cleanup_fd = {"value": None}
    fired = []

    def faulting_mkstemp(*args, **kwargs):
        if kwargs.get("prefix") != ".bd-jobs-cleanup-":
            return real_mkstemp(*args, **kwargs)
        fired.append("mkstemp")
        if fault == "mkstemp":
            raise OSError(28, "injected cleanup mkstemp failure")
        fd, path = real_mkstemp(*args, **kwargs)
        cleanup_fd["value"] = fd
        return fd, path

    def faulting_close(fd):
        if fault == "close" and fd == cleanup_fd["value"]:
            fired.append("close")
            cleanup_fd["value"] = None
            real_close(fd)
            raise OSError(5, "injected cleanup close failure")
        return real_close(fd)

    monkeypatch.setattr(jobs.tempfile, "mkstemp", faulting_mkstemp)
    monkeypatch.setattr(jobs.os, "close", faulting_close)
    outcome = jobs.forget(entry)

    assert fired == (["mkstemp"] if fault == "mkstemp"
                     else ["mkstemp", "close"]), fired
    assert not final.exists(), "the final was not durably withdrawn first"
    assert owned.exists(), "setup failure deleted the artifact it could not bind"
    assert outcome["cleanup_complete"] is False, outcome
    notes = "; ".join(outcome["notes"])
    assert str(owned) in notes and "COULD NOT" in notes, notes
    quarantines = list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*"))
    if fault == "mkstemp":
        assert quarantines == [], "mkstemp failure invented a temp: %r" % quarantines
    else:
        assert len(quarantines) == 1, (
            "uncertain close lost the only name for its temp: %r" % quarantines)
        assert str(quarantines[0]) in notes, (
            "the retained close-failure temp was not named: %s" % notes)


def test_forget_reports_mkstemp_failure_after_durable_withdrawal(
        jobs, sleeper, monkeypatch):
    _assert_quarantine_setup_failure(jobs, sleeper, monkeypatch, "mkstemp")


def test_forget_reports_close_failure_after_durable_withdrawal(
        jobs, sleeper, monkeypatch):
    _assert_quarantine_setup_failure(jobs, sleeper, monkeypatch, "close")


def _assert_replace_cleanup_failure(
        jobs, sleeper, monkeypatch, source_vanished):
    """Both source and temp disposition stay truthful after replace failure."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-replace-failure.sh"
    owned.write_text("echo retained\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "replace cleanup failure", "bash script",
        owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    real_replace = os.replace
    real_unlink = os.unlink
    fired = []

    def faulting_replace(src, dst):
        if pathlib.Path(src) == owned:
            fired.append("replace")
            if source_vanished:
                real_unlink(src)
                raise FileNotFoundError(str(src))
            raise PermissionError("injected quarantine replace failure")
        return real_replace(src, dst)

    def faulting_unlink(path):
        candidate = pathlib.Path(path)
        if candidate.name.startswith(".bd-jobs-cleanup-"):
            fired.append("unlink-temp")
            raise PermissionError("injected retained cleanup temp")
        return real_unlink(path)

    monkeypatch.setattr(jobs.os, "replace", faulting_replace)
    monkeypatch.setattr(jobs.os, "unlink", faulting_unlink)
    outcome = jobs.forget(entry)

    quarantines = list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*"))
    assert fired == ["replace", "unlink-temp"], fired
    assert not final.exists(), "the final was not durably withdrawn first"
    assert owned.exists() is (not source_vanished)
    assert len(quarantines) == 1, quarantines
    assert outcome["cleanup_complete"] is False, outcome
    notes = "; ".join(outcome["notes"])
    assert str(owned) in notes and str(quarantines[0]) in notes, notes


def test_forget_names_a_retained_quarantine_after_replace_failure(
        jobs, sleeper, monkeypatch):
    _assert_replace_cleanup_failure(jobs, sleeper, monkeypatch, False)


def test_forget_names_a_retained_quarantine_after_source_vanishes(
        jobs, sleeper, monkeypatch):
    _assert_replace_cleanup_failure(jobs, sleeper, monkeypatch, True)


def test_forget_reports_owned_cleanup_directory_fsync_failure(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-cleanup-fsync.sh"
    owned.write_text("echo cleanup\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "cleanup fsync", "bash script", owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    real_fsync_dir = jobs._fsync_dir
    calls = []

    def fail_second(path):
        calls.append(pathlib.Path(path))
        if len(calls) == 2:
            raise OSError(5, "injected owned-cleanup fsync failure")
        return real_fsync_dir(path)

    monkeypatch.setattr(jobs, "_fsync_dir", fail_second)
    outcome = jobs.forget(entry)

    assert calls == [jobs.JOBS_DIR, jobs.JOBS_DIR], calls
    assert not final.exists() and not owned.exists()
    assert outcome["entry_absent_durable"] is True
    assert outcome["cleanup_complete"] is False
    assert "owned-artifact cleanup durable" in "; ".join(outcome["notes"])


def test_forget_never_deletes_a_new_file_reusing_an_owned_path(
        jobs, sleeper, monkeypatch):
    """Cleanup authority is bound to the file observed with the old record,
    not every future inode that happens to reuse its pathname."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-reused.sh"
    owned.write_text("old owner\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "old pathname owner", "bash script",
        owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    real_unlink_verified = jobs._unlink_verified
    swapped = []

    def replace_after_final_withdrawal(path):
        result = real_unlink_verified(path)
        if pathlib.Path(path) == final and result[0]:
            replacement = jobs.JOBS_DIR / ".replacement-owned.tmp"
            replacement.write_text("new owner\n", encoding="utf-8")
            os.replace(str(replacement), str(owned))
            swapped.append(True)
        return result

    monkeypatch.setattr(jobs, "_unlink_verified", replace_after_final_withdrawal)
    outcome = jobs.forget(entry)

    assert swapped == [True], "the pathname-reuse schedule never occurred"
    assert owned.exists() and owned.read_text(encoding="utf-8") == "new owner\n", (
        "stale cleanup deleted a replacement file that reused the old path")
    assert outcome["cleanup_complete"] is False, (
        "retaining a changed owned path was reported as complete cleanup")


def test_forget_captures_the_old_inode_before_unlinking_its_quarantine(
        jobs, sleeper, monkeypatch):
    """A replacement between identity comparison and rename must survive."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-check-unlink-race.sh"
    owned.write_text("old owner\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "check unlink race", "bash script",
        owned_paths=[str(owned)])
    real_replace = os.replace
    swapped = []

    def replace_between_check_and_quarantine(src, dst):
        if pathlib.Path(src) == owned and not swapped:
            replacement = jobs.JOBS_DIR / ".replacement-before-unlink.tmp"
            replacement.write_text("new owner\n", encoding="utf-8")
            real_replace(str(replacement), str(owned))
            swapped.append((pathlib.Path(src), pathlib.Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(jobs.os, "replace", replace_between_check_and_quarantine)
    outcome = jobs.forget(entry)

    assert len(swapped) == 1, "the check/rename replacement schedule never ran"
    assert swapped[0][0] == owned
    assert swapped[0][1].name.startswith(".bd-jobs-cleanup-")
    assert owned.exists() and owned.read_text(encoding="utf-8") == "new owner\n", (
        "cleanup deleted the pathname occupant installed after its identity "
        "check")
    assert outcome["cleanup_complete"] is False, outcome
    assert "restored" in "; ".join(outcome["notes"]), outcome


# ── v3.66.1206: the merged-stream id contract every consumer parses ──────────


def _last_non_empty(text):
    """`bd-sweep-run:618` exactly: `awk 'NF{last=$0} END{print last}'`."""
    last = ""
    for line in text.splitlines():
        if line.strip():
            last = line
    return last


_REAL_JOBS = pathlib.Path("/tmp/bd-jobs")

_PRINT_REGISTRY = (
    "import importlib.machinery, importlib.util, sys\n"
    "s = importlib.util.spec_from_loader('bd_jobs_probe',\n"
    "        importlib.machinery.SourceFileLoader('bd_jobs_probe', %r))\n"
    "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
    "print(m.JOBS_DIR)\n")


def _reap_marked_entries(registry, marker):
    """Reap only what THIS RUN registered, and prove it before signalling.

    MANDATORY teardown for any node that executes a payload: a node that stubs
    the target expects to launch nothing, but a defect in self-path resolution
    bypasses the stub, runs the real transaction, and leaves a live job the node
    never knew it started -- measured at v3.66.1206 as one orphaned `sleep 45`.

    AND IT AUTHENTICATES BEFORE IT KILLS. The first version signalled every
    positive pid it could read out of JSON in a directory, which is the exact
    shape this whole tool exists to prevent and which this cut has already been
    bitten by once (a fake pid sent `_terminate_and_wait` into `killpg` on the
    pytest run's own group). A number in a file is not an identity: the entry
    must carry this run's marker AND its recorded start time must still match
    the live process. Anything malformed, foreign, unmarked, pre-existing, or
    whose start time has moved is REFUSED and left alone.

    Returns (signalled, refused) as lists of (path, pid, why).
    """
    registry = pathlib.Path(registry)
    signalled, refused = [], []
    if not registry.is_dir():
        return signalled, refused
    for final in sorted(registry.glob("*.json")):
        try:
            entry = json.loads(final.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            refused.append((str(final), None, "not well-formed"))
            continue
        if not isinstance(entry, dict):
            refused.append((str(final), None, "not an object"))
            continue
        pid = entry.get("pid")
        if entry.get("run_marker") != marker:
            refused.append((str(final), pid, "not this run's marker"))
            continue
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            refused.append((str(final), pid, "not a process id"))
            continue
        live = _starttime_of(pid)
        if live is None:
            refused.append((str(final), pid, "already gone"))
            continue
        if live != entry.get("starttime"):
            refused.append((str(final), pid,
                            "start time %r no longer matches %r"
                            % (entry.get("starttime"), live)))
            continue
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = None
        if pgid != pid:
            # MARKER + PID + STARTTIME DO NOT PROVE THE GROUP IS OURS. A gated
            # job leads its own group; anything else shares the group of
            # whatever started it, and signalling that reaches this test runner.
            # Refuse rather than narrow the signal: an entry we cannot reap as
            # what it claims to be is one a human should look at.
            refused.append((str(final), pid,
                            "does not lead its own group (pgid %r), so it was "
                            "not started as a gated job and signalling it "
                            "would reach the launcher's group" % (pgid,)))
            continue
        _reap_process(pid)
        # THE RECORD GOES WITH THE PROCESS. Killing the job and leaving its
        # entry, log and copied script behind still pollutes the registry it
        # was found in -- which, in a failing or mutated run, is the canonical
        # one. Only the exact scanned final and this entry's own DIRECT CHILDREN
        # of that registry are removed; an external path named by the entry is
        # somebody else's evidence and is never followed.
        leftovers = []
        for artifact in [str(final)] + _marked_artifacts(entry, registry):
            try:
                os.unlink(artifact)
            except FileNotFoundError:
                pass
            except OSError as exc:
                leftovers.append("%s (%s)" % (artifact, exc))
                continue
            if os.path.lexists(artifact):
                leftovers.append("%s (still present after unlink)" % artifact)
        if leftovers:
            refused.append((str(final), pid,
                            "reaped, but RETAINED %s" % leftovers))
            outcome = "reaped; cleanup retained %s" % leftovers
        else:
            outcome = "reaped and cleaned"
        signalled.append((str(final), pid, outcome))
    return signalled, refused


def _marked_artifacts(entry, registry):
    """This entry's own files INSIDE `registry` -- its log and anything it
    declared owned. Normalized, and a DIRECT child: an entry may NAME any path,
    but cleanup follows only what lives in the registry it was found in."""
    here = pathlib.Path(os.path.realpath(str(registry)))
    out = []
    for value in [entry.get("log")] + list(entry.get("owned_paths") or []):
        if not isinstance(value, str) or not value:
            continue
        candidate = pathlib.Path(os.path.realpath(value))
        if candidate.parent == here:
            out.append(str(candidate))
    return out


def _starttime_of(pid):
    """Field 22 of /proc/<pid>/stat, read the same way production reads it."""
    try:
        raw = pathlib.Path("/proc", str(pid), "stat").read_text()
        return int(raw[raw.rindex(")") + 1:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _private_target(tmp_path, name="target"):
    """A stand-in for an installed target: a private executable copy of the
    tool with its own baked registry. NO ENVIRONMENT IS INJECTED anywhere --
    the target, its gate child and everything it launches are inside this
    namespace because of which FILE they are, which is the one thing a real
    delegated target would also have."""
    registry = tmp_path / ("%s-registry" % name)
    copy, registry = _private_tool(tmp_path / name, registry)
    return copy, registry


def _registry_a_child_would_use(env=None, tool=None):
    """Which registry a CHILD PROCESS of a given tool file resolves. Read-only:
    it imports the module and prints `JOBS_DIR`, and writes nothing."""
    r = subprocess.run(
        [sys.executable, "-c", _PRINT_REGISTRY % str(tool or _TOOL)],
        capture_output=True, text=True, timeout=60,
        env=env if env is None else dict(env))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_the_private_copy_really_rewrites_the_registry_it_bakes_in(tmp_path):
    """The copy is only isolation if the rewrite actually happened.

    Deliberately FIXTURE-FREE. The `jobs` fixture asserts the same property and
    refuses to hand out a module otherwise -- which is the right hard stop, but
    it surfaces as a collection error, and an error is unmeasured rather than
    caught. This node states the property as an ordinary assertion so a
    mutation that neuters the rewrite is a visible failure. Read-only.
    """
    copy, registry = _private_tool(tmp_path)
    assert registry != _REAL_JOBS, "the copy would share the canonical registry"
    assert _registry_a_child_would_use(tool=str(copy)) == str(registry), (
        "the private copy still resolves %s, so every test that launches "
        "through it would write to the operator's registry"
        % _registry_a_child_would_use(tool=str(copy)))


def test_everything_the_harness_launches_stays_inside_the_private_copy(
        jobs, tmp_path):
    """THE INCIDENT THIS NODE EXISTS FOR, measured during this cut's own scratch
    mutation battery: with `_remote_self` mutated back to
    `Path(__file__).resolve()`, three delegated launches registered themselves
    in the LIVE `/tmp/bd-jobs` on this host, and their process groups had to be
    killed by hand afterwards.

    The reason is structural. `_gate_argv` names `__file__`, so the child of a
    launch fresh-imports the file the module was loaded from and takes ITS
    baked-in registry -- rebinding `mod.JOBS_DIR` in the parent changes nothing
    for it. So the fixture must load a copy, and the launch path must name that
    copy. Read-only by construction: nothing here is launched or written, so
    this node cannot contaminate the registry it is about.
    """
    assert jobs.JOBS_DIR != _REAL_JOBS, "precondition: the fixture must redirect"
    tool_in_argv = jobs._gate_argv(9, 11, ["bash", "-c", "true"])[1]
    assert tool_in_argv.startswith(str(tmp_path)), (
        "the gate child is pointed at %r, outside this test's own tree -- it "
        "will import that file and use ITS registry, which is the live one"
        % tool_in_argv)
    assert _registry_a_child_would_use(tool=tool_in_argv) == str(jobs.JOBS_DIR), (
        "a child of the executable this harness launches resolves a different "
        "registry from the parent, so anything it registers is invisible here")
    assert _registry_a_child_would_use(tool=str(_TOOL)) == str(_REAL_JOBS), (
        "the ORIGINAL tool no longer resolves the one host-wide registry")


def test_the_production_tool_keeps_one_registry_whatever_the_environment_says(
        tmp_path):
    """PRODUCTION REGRESSION GUARD, and the reason the copy exists at all.

    An environment-selected registry was implemented and then rejected by
    design audit: `bd-fleet` counts `/tmp/bd-jobs/*.json` and `bd-gc` protects
    that exact literal, and a real delegated target imports its own module and
    would never receive the caller's selection -- so a job could be live and
    operationally invisible. This node fails if that second authority is ever
    reintroduced.
    """
    for value in ("/tmp/bd-isolated-probe", str(tmp_path / "elsewhere"),
                  "relative/path", ""):
        env = dict(os.environ, BD_JOBS_DIR=value)
        assert _registry_a_child_would_use(env, tool=str(_TOOL)) == str(_REAL_JOBS), (
            "BD_JOBS_DIR=%r moved the production registry away from %s -- the "
            "fleet report and the janitor's protection rule both name that "
            "exact path" % (value, _REAL_JOBS))


def test_the_real_registry_attribution_helper_sees_only_its_own_marker(tmp_path):
    """The guard every executing node below depends on, proven over a SYNTHETIC
    directory.

    Two failure modes, and this control exists for both. A helper that reported
    nothing would make every isolation assertion vacuous -- the nodes would pass
    over any contamination at all. And a helper that reported foreign entries
    would reintroduce the row-180 defect this replaced: the canonical registry
    is shared, an operator's job can appear or vanish mid-assertion, and failing
    on that is failing on somebody else's work.

    Driven over `tmp_path`, never `/tmp/bd-jobs`: proving a real-registry guard
    by writing into the real registry is the hazard, not the test.
    """
    fake = tmp_path / "registry"
    fake.mkdir()
    marker = "row212-attribution-probe"
    host = "somehost"
    mine_log = fake / "mine.log"
    mine_log.write_text("output\n", encoding="utf-8")

    def entry(pid, **extra):
        base = {"id": "%s-%d" % (host, pid), "pid": pid, "starttime": 7,
                "purpose": "p", "cmd": "true", "host": host, "origin": host,
                "started_at": "2026-08-21T00:00:00Z", "log": None}
        base.update(extra)
        return base

    (fake / ("%s-11.json" % host)).write_text(
        json.dumps(entry(11, run_marker=marker, log=str(mine_log))),
        encoding="utf-8")
    (fake / ("%s-12.json" % host)).write_text(
        json.dumps(entry(12, run_marker="a-different-run")), encoding="utf-8")
    (fake / ("%s-13.json" % host)).write_text(json.dumps(entry(13)),
                                              encoding="utf-8")
    (fake / ("%s-14.json" % host)).write_bytes(b"{not json")

    found = _marked_residue(marker, fake)

    assert found == sorted([str(fake / ("%s-11.json" % host)), str(mine_log)]), (
        "the attribution helper did not name exactly this run's entry and the "
        "log it references: %r" % (found,))
    assert _marked_residue("a-marker-nobody-used", fake) == [], (
        "the helper attributed somebody else's entries to this run, which is "
        "how a shared registry's ordinary churn fails an unrelated node")


def test_the_teardown_helper_signals_only_this_run_s_proven_entries(
        tmp_path, sleeper):
    """THE CLEANUP PATH IS A LOADED WEAPON TOO, and this file has already been
    bitten once: a fake pid sent the production cleanup into `killpg` on the
    pytest run's own process group.

    So the teardown authenticates exactly like `reap` does -- this run's marker,
    and a start time that still matches -- and every other shape is refused.
    Each stranger below is a REAL live process, so "survived" is measured, not
    inferred.
    """
    registry = tmp_path / "registry"
    registry.mkdir()
    marker = _run_marker("teardown-control")
    host = "somehost"

    strangers = {}
    for label in ("foreign-marker", "no-marker", "wrong-starttime",
                  "not-group-leader"):
        strangers[label] = sleeper()

    def write(name, pid, **extra):
        body = {"id": "%s-%d" % (host, pid), "pid": pid,
                "starttime": _starttime_of(pid), "purpose": label_of(name),
                "cmd": "sleep 60", "host": host, "origin": host,
                "started_at": "2026-08-21T00:00:00Z", "log": None}
        body.update(extra)
        (registry / name).write_text(json.dumps(body), encoding="utf-8")

    def label_of(name):
        return name

    write("%s-%d.json" % (host, strangers["foreign-marker"].pid),
          strangers["foreign-marker"].pid, run_marker="somebody-elses-run")
    write("%s-%d.json" % (host, strangers["no-marker"].pid),
          strangers["no-marker"].pid)
    write("%s-%d.json" % (host, strangers["wrong-starttime"].pid),
          strangers["wrong-starttime"].pid, run_marker=marker,
          starttime=(_starttime_of(strangers["wrong-starttime"].pid) or 0) + 1)
    # THE HARDEST CASE: this run's own marker, the LIVE start time, a real
    # running process -- and it is not a group leader, because the fixture
    # starts it in pytest's group. Everything the guard checks agrees, and
    # signalling it as a group would still kill this test runner.
    write("%s-%d.json" % (host, strangers["not-group-leader"].pid),
          strangers["not-group-leader"].pid, run_marker=marker)
    assert os.getpgid(strangers["not-group-leader"].pid) != \
        strangers["not-group-leader"].pid, (
        "precondition: this stranger must NOT lead its own group, or it is not "
        "the case this control exists for")
    (registry / ("%s-999999.json" % host)).write_bytes(b"{not json")

    # THE OWNED PROCESS LEADS ITS OWN GROUP, like a gated job does, and is
    # tracked locally rather than by the shared `sleeper` fixture. The fixture
    # starts processes in PYTEST's group: signalling one as a group would kill
    # the runner, which is the incident this file has already recorded twice.
    # The strangers stay on the fixture precisely because they must never be
    # group-signalled.
    owned = subprocess.Popen(["sleep", "60"], start_new_session=True)
    owned_log = registry / "owned.log"
    owned_log.write_text("job output\n", encoding="utf-8")
    owned_script = registry / ".bd-jobs-script-owned.sh"
    owned_script.write_text("echo hi\n", encoding="utf-8")
    external = tmp_path / "operators-evidence.log"
    external.write_text("not ours\n", encoding="utf-8")
    try:
        write("%s-%d.json" % (host, owned.pid), owned.pid, run_marker=marker,
              log=str(owned_log),
              owned_paths=[str(owned_script), str(external)])
        for label, proc in strangers.items():
            assert proc.poll() is None, "precondition: %s must be alive" % label
        assert owned.poll() is None, "precondition: the owned job must be alive"
        assert os.getpgid(owned.pid) == owned.pid, (
            "precondition: the owned job must lead its own group, or reaping "
            "it as a group would signal this test runner")

        signalled, refused = _reap_marked_entries(registry, marker)

        assert [pid for _, pid, _ in signalled] == [owned.pid], (
            "the teardown signalled something other than this run's proven "
            "entry: %r" % (signalled,))
        assert _wait_until(lambda: owned.poll() is not None, 10), (
            "the owned job was not reaped: the teardown reported signalling it "
            "but pid %d is still running" % owned.pid)
        for label, proc in strangers.items():
            assert proc.poll() is None, (
                "a %s entry was signalled -- a number in a file is not an "
                "identity: %r" % (label, refused))
        assert len(refused) == 5, (
            "expected all four strangers and the torn file to be refused by "
            "name: %r" % (refused,))
        # THE RECORD GOES WITH THE PROCESS -- and only the parts of it that
        # live in this registry.
        assert not (registry / ("%s-%d.json" % (host, owned.pid))).exists(), (
            "the reaped job's entry was left behind, so a failing run would "
            "still pollute the registry it ran against")
        assert not owned_log.exists(), "the reaped job's log was left behind"
        assert not owned_script.exists(), (
            "the copy the entry declared owned was left behind")
        assert external.exists() and external.read_text(
            encoding="utf-8") == "not ours\n", (
            "cleanup followed a path OUTSIDE the registry because an entry "
            "named it: %s" % external)
        for label, proc in strangers.items():
            name = registry / ("%s-%d.json" % (host, proc.pid))
            assert name.exists(), (
                "a refused entry (%s) was deleted anyway -- a record nobody "
                "could authenticate is one a human must still see" % label)
    finally:
        if owned.poll() is None:
            _reap_process(owned.pid)
        try:
            owned.wait(timeout=10)
        except (subprocess.TimeoutExpired, ChildProcessError, OSError):
            pass


def test_the_teardown_helper_never_calls_retained_cleanup_clean(
        tmp_path, monkeypatch):
    """A reaped process and retained artifact is a partial outcome, not clean."""
    registry = tmp_path / "registry"
    registry.mkdir()
    marker = _run_marker("retained-cleanup-control")
    owned = subprocess.Popen(["sleep", "60"], start_new_session=True)
    owned_log = registry / "owned.log"
    owned_log.write_text("job output\n", encoding="utf-8")
    final = registry / ("somehost-%d.json" % owned.pid)
    final.write_text(json.dumps({
        "id": "somehost-%d" % owned.pid,
        "pid": owned.pid,
        "starttime": _starttime_of(owned.pid),
        "purpose": "retained cleanup control",
        "cmd": "sleep 60",
        "host": "somehost",
        "origin": "somehost",
        "started_at": "2026-08-21T00:00:00Z",
        "log": str(owned_log),
        "run_marker": marker,
    }), encoding="utf-8")
    real_unlink = os.unlink
    retain_log = [True]

    def fail_one_owned_unlink(path):
        if pathlib.Path(path) == owned_log and retain_log[0]:
            raise PermissionError("measured retained cleanup")
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", fail_one_owned_unlink)
    try:
        signalled, refused = _reap_marked_entries(registry, marker)
        assert len(signalled) == 1 and "retained" in signalled[0][2].lower(), (
            "the helper called partial cleanup clean: %r" % (signalled,))
        assert any("RETAINED" in why for _, _, why in refused), refused
        assert owned_log.exists(), "precondition: the injected unlink must fail"
    finally:
        retain_log[0] = False
        if owned.poll() is None:
            _reap_process(owned.pid)
        try:
            owned.wait(timeout=10)
        except (subprocess.TimeoutExpired, ChildProcessError, OSError):
            pass
        if owned_log.exists():
            owned_log.unlink()


def test_a_local_gated_run_through_the_private_copy_owns_its_whole_namespace(
        tmp_path):
    """A REAL gated launch, end to end, through the copy and nothing else.

    The launch, its gate child, its entry, its log and its reap all belong to
    one directory this test owns. The environment is given a deliberately
    hostile `BD_JOBS_DIR`: production must ignore it completely, so isolation
    here is proven to come from the executable and not from an ambient value.
    """
    run_marker = _run_marker("private-copy")
    copy, registry = _private_tool(tmp_path / "host", tmp_path / "private-jobs")
    marker = tmp_path / "released.marker"
    # Hostile on BOTH channels: a registry authority production must ignore
    # entirely, and this run's attribution marker, which it must stamp so the
    # canonical-registry guard at the end can see anything that escaped.
    hostile = dict(os.environ, BD_JOBS_DIR=str(tmp_path / "not-this-one"),
                   BD_JOBS_RUN_MARKER=run_marker)
    job_id = None
    escaped = []
    try:
        launched = subprocess.run(
            [sys.executable, str(copy), "run", "--purpose", "private-copy",
             "--", "bash", "-c", "touch %s; sleep 45" % marker],
            env=hostile, capture_output=True, text=True, timeout=120)
        escaped = _marked_residue(run_marker, _REAL_JOBS)   # observe first
        assert launched.returncode == 0, launched.stdout + launched.stderr
        job_id = _last_non_empty(launched.stdout)

        finals = sorted(registry.glob("*.json"))
        assert [f.name for f in finals] == ["%s.json" % job_id], (
            "the launch did not register in the copy's own registry: %r"
            % [f.name for f in finals])
        entry = json.loads(finals[0].read_text(encoding="utf-8"))
        assert pathlib.Path(entry["log"]).parent == registry, entry["log"]
        assert not (tmp_path / "not-this-one").exists(), (
            "production honoured BD_JOBS_DIR: a second registry authority is "
            "back, and the fleet report and janitor do not know about it")
        assert _wait_until(marker.exists), "the gated job never ran"

        reaped = subprocess.run(
            [sys.executable, str(copy), "reap", "--id", job_id],
            env=hostile, capture_output=True, text=True, timeout=120)
        assert reaped.returncode == 0, reaped.stdout + reaped.stderr
        assert sorted(p.name for p in registry.iterdir()) == [], (
            "reap left owned residue in the private namespace: %r"
            % sorted(p.name for p in registry.iterdir()))
    finally:
        _reap_marked_entries(registry, run_marker)
        _reap_marked_entries(_REAL_JOBS, run_marker)
    assert escaped == [], (
        "this node registered in the canonical /tmp/bd-jobs: %r" % (escaped,))


def test_a_delegated_script_job_owns_and_reclaims_its_copy(
        jobs, monkeypatch, tmp_path):
    """The delegated `--script` contract, executed against a copied target with
    NO environment injection, and reclaimed afterwards.

    The launcher copies the script into the target's registry; the target's own
    transaction ADOPTS that exact path, records it as owned, and `reap` takes it
    with the entry. Nothing else may be removed, and the canonical registry is
    not involved at any point.
    """
    run_marker = _run_marker("delegated-script")
    target, registry = _private_tool(tmp_path / "target", jobs.JOBS_DIR)
    marker = tmp_path / "script-ran.marker"
    script = tmp_path / "sweep.sh"
    script.write_text("touch %s\nsleep 45\n" % marker, encoding="utf-8")
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", str(target))
    calls = _fake_transport(jobs, monkeypatch)

    rc = jobs.main(["run", "--host", "somewhere", "--purpose", "delegated",
                    "--script", str(script)])
    assert rc == 0, rc
    scp = [c for c in calls if c and c[0] == "scp"]
    assert scp, "the script was never copied: %r" % (calls,)
    copied = pathlib.Path(scp[0][-1].split(":", 1)[1])
    payload = _payload(calls)
    tokens = _delegated_tokens(payload)

    # BEFORE ANY EXECUTION: the target must be this test's own copy. If a later
    # edit points delegation back at the installed tool, this node fails here
    # rather than by writing into the real registry.
    assert tokens[0] == str(target) and str(tmp_path) in tokens[0], (
        "the delegated target is not this test's private copy: %r" % tokens[0])
    assert ("--adopt-script" in tokens
            and tokens[tokens.index("--adopt-script") + 1] == str(copied)), (
        "the copy was not offered to the target for adoption: %r" % tokens)

    monkeypatch.undo()
    registry.mkdir(parents=True, exist_ok=True)
    copied.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    entry = None
    escaped = []
    try:
        executed = subprocess.run(
            ["bash", "-c", payload],
            env=dict(os.environ, BD_JOBS_RUN_MARKER=run_marker),
            capture_output=True, text=True, timeout=120)
        escaped = _marked_residue(run_marker, _REAL_JOBS)   # observe first
        assert executed.returncode == 0, executed.stdout + executed.stderr
        finals = sorted(registry.glob("*.json"))
        assert len(finals) == 1, [f.name for f in finals]
        entry = json.loads(finals[0].read_text(encoding="utf-8"))

        assert entry.get("run_marker") == run_marker, (
            "the delegated entry is not attributable to this run, so the "
            "canonical-registry guard below could not see it either: %r" % entry)
        assert entry.get("owned_paths") == [str(copied)], (
            "the target did not record the copied script as its own: %r" % entry)
        assert pathlib.Path(entry["log"]).parent == registry, entry["log"]
        assert copied.exists(), "the script was removed before the job ran it"
        assert _wait_until(marker.exists), "the delegated script never ran"

        _reap_process(entry["pid"])
        reaped = subprocess.run(
            [sys.executable, str(target), "reap", "--id", entry["id"]],
            capture_output=True, text=True, timeout=120)
        assert reaped.returncode == 0, reaped.stdout + reaped.stderr
        assert not copied.exists(), (
            "the adopted script outlived the entry that owned it: %s" % copied)
        assert not pathlib.Path(entry["log"]).exists(), "the log outlived it too"
        assert sorted(p.name for p in registry.iterdir()) == [], (
            "owned residue: %r" % sorted(p.name for p in registry.iterdir()))
    finally:
        if entry:
            _reap_process(entry["pid"])
        _reap_marked_entries(registry, run_marker)
        _reap_marked_entries(_REAL_JOBS, run_marker)
    assert escaped == [], (
        "this node registered in the canonical /tmp/bd-jobs: %r" % (escaped,))


def _run_marker(label):
    """A marker no other run can produce, for one node.

    `BD_JOBS_RUN_MARKER` is production's existing attribution channel (row 180):
    `register()` stamps it into the entry when it is set. Every node below binds
    its own, which is what lets the two guards underneath be exact without being
    wrong about somebody else's work.
    """
    return "row212-%s-%d-%s" % (label, os.getpid(), os.urandom(6).hex())


def _marked_residue(marker, registry=None):
    """Everything in a registry that is ATTRIBUTABLE TO THIS RUN: well-formed
    entries carrying exactly `marker`, plus every log such an entry references.

    ATTRIBUTION, NOT COUNTING. The canonical registry is shared: an operator's
    job can appear or vanish while a test is running, and an exact
    before/after comparison fails on that -- row 180 exactly. Foreign entries,
    unmarked entries and torn files are not this run's and are never reported.
    """
    registry = pathlib.Path(registry or _REAL_JOBS)
    if not registry.is_dir():
        return []
    found = []
    for path in sorted(registry.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                    # not well-formed, so not attributable
        if not isinstance(entry, dict) or entry.get("run_marker") != marker:
            continue
        found.append(str(path))
        log = entry.get("log")
        if isinstance(log, str) and log:
            found.append(log)
    return sorted(found)


def _real_registry_names():
    """What the REAL registry holds right now.

    Every executing remote node is isolated only because
    `BD_JOBS_REMOTE_SELF` points at a wrapper. MEASURED during this cut's own
    scratch mutation battery: with `_remote_self` mutated back to
    `Path(__file__).resolve()`, three delegated launches registered themselves
    in `/tmp/bd-jobs` on this host -- the isolation was a convention, and the
    tests could not see it break. Pairing each node with a before/after
    snapshot makes the isolation self-proving.
    """
    return ({p.name for p in _REAL_JOBS.iterdir()}
            if _REAL_JOBS.is_dir() else set())


def _target_wrapper(tmp_path, jobs_dir, name="bd-jobs-target"):
    """An isolated stand-in for an installed target: the tool's own source,
    copied, with the registry baked in.

    NOT A WRAPPER THAT REBINDS AN ATTRIBUTE, which is what this was until
    v3.66.1206. That form isolated the target process and nothing it launched:
    the gate child resolves `__file__`, which pointed back at the real tool and
    therefore at the real `/tmp/bd-jobs`. A copy has no such seam -- everything
    downstream of it is the copy.
    """
    copy, registry = _private_tool(tmp_path / name, jobs_dir)
    return copy


def test_the_job_id_is_the_last_non_empty_line_of_the_merged_register_stream(
        tmp_path):
    """MEASURED CONSUMER, replayed rather than described: `bd-sweep-run` runs
    `register` with `2>&1` into one file and takes the last non-empty line as
    the job id. Any diagnostic emitted after the id -- on EITHER stream --
    becomes the id it parses."""
    assert _last_non_empty("an-id\nnoise\n") == "noise", (
        "the parser used by this test is constant, so it cannot detect the "
        "failure it exists to detect")

    wrapper = _target_wrapper(tmp_path, tmp_path / "registry")
    # FAIL BEFORE LAUNCH. This node executes a tool as a subprocess; if that
    # tool were ever the installed one, it would register in the operator's
    # registry and the failure would be a contaminated host rather than a red
    # test. Proven in scratch against a wrapper pointed back at the installed
    # tool: this assertion fires and nothing runs.
    assert str(tmp_path) in str(wrapper), (
        "the target executable is not this test's own copy (%s): it would "
        "register in the operator's registry" % wrapper)
    r = subprocess.run([str(wrapper), "register", "--pid", str(os.getpid()),
                        "--purpose", "id-last"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stdout
    entries = sorted((tmp_path / "registry").glob("*.json"))
    assert len(entries) == 1, [e.name for e in entries]
    entry = json.loads(entries[0].read_text(encoding="utf-8"))
    assert _last_non_empty(r.stdout) == entry["id"], (
        "the merged stream's last non-empty line is not the job id: %r"
        % r.stdout)


def test_register_refuses_a_dead_pid_with_a_diagnostic_not_a_traceback(tmp_path):
    """`register()` now REFUSES semantically, and `cmd_register` is what every
    direct consumer calls. An uncaught exception there writes a Python
    traceback into `bd-sweep-run`'s `register.out`, whose last non-empty line
    then becomes the "job id" it carries for the rest of the run."""
    done = subprocess.Popen(["true"])
    done.wait(timeout=10)
    wrapper = _target_wrapper(tmp_path, tmp_path / "registry")
    assert str(tmp_path) in str(wrapper), (
        "the target executable is not this test's own copy (%s): it would "
        "register in the operator's registry" % wrapper)

    r = subprocess.run([str(wrapper), "register", "--pid", str(done.pid),
                        "--purpose", "dead-pid"],
                       capture_output=True, text=True, timeout=60)

    assert "Traceback" not in r.stdout + r.stderr, (
        "a semantic refusal exited by traceback:\n%s" % (r.stdout + r.stderr))
    assert r.returncode == 3, (
        "a refused registration did not return the distinctive registration "
        "status (rc=%r)" % r.returncode)
    assert "REFUSED" in r.stderr, r.stderr
    assert r.stdout.strip() == "", (
        "a refusal printed something on stdout, where consumers read the id: "
        "%r" % r.stdout)
    assert list((tmp_path / "registry").glob("*.json")) == [], (
        "a refused registration published an entry anyway")


def test_the_real_process_group_and_log_suites_are_wired_into_ci():
    """Scope decision 3. This cut changes exactly the contracts those two
    suites assert -- the process group a job is launched in, and the log an
    entry records -- and a gate CI does not run does not exist."""
    ci = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    declared = (_REPO / "tests"
                / "test_v3_66_939_ci_gate_shards_cover_every_gate.py").read_text(
                    encoding="utf-8")
    for suite in ("tests/test_v3_66_1054_launched_work_is_bounded_and_reapable.py",
                  "tests/test_v3_66_1087_jobs_report_progress_not_just_liveness.py"):
        assert suite in ci, (
            "%s runs in no CI shard, so the contract this cut changes is "
            "unmeasured on every PR" % suite)
        assert suite in declared, (
            "%s is in a shard but not in the declared gate set, so a later "
            "drop from the shard would go unnoticed" % suite)


# ── v3.66.1206: the remote path delegates instead of reimplementing ──────────
#
# MEASURED on the base: the remote protocol is `nohup bash -c CMD >$$.log & P=$!;
# register --pid $P; echo $P`. Every one of its parts is wrong in a way the
# local path already fixed --
#
#   * `register`'s status is discarded, so the ssh command exits 0 and prints a
#     job id for a job the registry does not know (the incident, remotely);
#   * `>$$.log` names the SSH SHELL, `$!` names the job, so the recorded log
#     belongs to a different process (measured: 2585603 vs 2585607);
#   * `register` has no `--log`, so every remote entry publishes `log=None`;
#   * plain `&` leaves the job in the ssh shell's process group, so `reap`
#     takes the pid-only branch and orphans its children -- backlog 88, live;
#   * `echo $P` prints AFTER the id, and every consumer reads the last line.
#
# So the target runs its own gated local transaction and the launcher stops
# maintaining a second protocol. No node below runs ssh: the payload production
# builds is captured from the argv it hands `subprocess.run`, and then EXECUTED
# against an isolated stand-in for the target.


def _fake_transport(jobs, monkeypatch, outcome=None):
    """Capture every ssh/scp argv, and forbid a local launch on the remote path.

    The default stands in for a WELL-BEHAVED target: it answers the launch with
    the status sentinel, because a real target's payload always emits one. A
    fake that stays silent is a target that produced no status marker, which is
    a genuine UNKNOWN and not the case most of these nodes are about.
    """
    calls = []

    def well_behaved(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            disposition = ("ADOPTED"
                           if jobs._ATTEMPT_DISPOSITION_FLAG in cmd[-1]
                           else None)
            return (0, "", _status_record_for_payload(
                jobs, cmd[-1], 0, disposition=disposition))
        return (0, "", "")

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd and cmd[0] == "ssh":
            tokens = shlex.split(cmd[-1])
            if jobs._ATTEMPT_IDENTITY_FLAG in tokens:
                index = tokens.index(jobs._ATTEMPT_IDENTITY_FLAG)
                request_id, nonce, attempt = tokens[index + 1:index + 4]
                marker = "%s%s:%s:%s:PRESENT:1:2:%d\n" % (
                    jobs._REMOTE_IDENTITY_SENTINEL, request_id, nonce, attempt,
                    stat.S_IFREG)
                return subprocess.CompletedProcess(cmd, 0, marker, "")
            if jobs._ATTEMPT_CLEANUP_FLAG in tokens:
                index = tokens.index(jobs._ATTEMPT_CLEANUP_FLAG)
                request_id, nonce, attempt = tokens[index + 1:index + 4]
                marker = "%s%s:%s:%s:REMOVED\n" % (
                    jobs._REMOTE_CLEANUP_SENTINEL, request_id, nonce, attempt)
                return subprocess.CompletedProcess(cmd, 0, marker, "")
        rc, out, err = (outcome or well_behaved)(list(cmd))
        return subprocess.CompletedProcess(cmd, rc, out, err)

    def fake_scp(argv):
        calls.append(list(argv))
        rc, out, err = (outcome or well_behaved)(list(argv))
        return subprocess.CompletedProcess(argv, rc, out, err), True, ""

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(jobs, "_run_scp", fake_scp)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: pytest.fail(
        "the remote path launched something locally"))
    monkeypatch.setattr(jobs.socket, "gethostname", lambda: "launcher-host")
    return calls


def _remote_args(**ns):
    base = dict(host="somewhere", purpose="p", command=[], script=None,
                origin=None)
    base.update(ns)
    return type("A", (), base)()


def _payload(calls):
    ssh = [c for c in calls if c and c[0] == "ssh"]
    assert ssh, "no ssh call was made: %r" % (calls,)
    return ssh[-1][-1]


_STATUS_TAIL = "; rc=$?;"


def _delegated_tokens(payload):
    """The delegated argv, split off production's own status-capture tail.

    Anchored on that exact text rather than on a `;` token: `shlex.split` is a
    lexer, not a shell, so `...'echo b'; rc=$?` yields no standalone `;` and a
    token-based split would silently measure the wrong string.
    """
    assert _STATUS_TAIL in payload, (
        "the payload has no status-capture tail, so a target failure could be "
        "masked by whatever runs last: %r" % payload)
    return shlex.split(payload.split(_STATUS_TAIL, 1)[0])


def _bound_status_prefixes(jobs, payload):
    """Read the opaque bound prefixes production placed in its shell payload."""
    return re.findall(
        re.escape(jobs._REMOTE_STATUS_SENTINEL)
        + r"[A-Za-z0-9._-]{1,128}:[0-9a-f]{32}:", payload)


def _candidate_status_prefix(jobs, payload):
    """Let RED exercise the old fixed marker; GREEN must use one bound prefix."""
    prefixes = _bound_status_prefixes(jobs, payload)
    if prefixes:
        assert len(set(prefixes)) == 1, prefixes
        return prefixes[0]
    return jobs._REMOTE_STATUS_SENTINEL


def _disposition_binding_for_payload(jobs, payload):
    """Parse the real target helper call between rc capture and final status."""
    tokens = _delegated_tokens(payload)
    if "--attempt-token" not in tokens:
        assert jobs._ATTEMPT_DISPOSITION_FLAG not in payload, payload
        return None
    assert payload.count(_STATUS_TAIL) == 1, payload
    after_rc = payload.split(_STATUS_TAIL, 1)[1]
    assert after_rc.count(jobs._ATTEMPT_DISPOSITION_FLAG) == 1, payload
    split = after_rc.split("; printf ", 1)
    assert len(split) == 2, (
        "the disposition helper is not immediately before final status: %r"
        % payload)
    helper, status_tail = split
    helper = helper.strip()
    assert helper.endswith(" >&2"), helper
    helper_tokens = shlex.split(helper[:-len(" >&2")])

    request_id = tokens[tokens.index("--request-id") + 1]
    attempt = tokens[tokens.index("--attempt-token") + 1]
    path = tokens[tokens.index("--adopt-script") + 1]
    prefixes = _bound_status_prefixes(jobs, payload)
    assert len(prefixes) == 1, prefixes
    match = re.fullmatch(
        re.escape(jobs._REMOTE_STATUS_SENTINEL)
        + re.escape(request_id) + r":([0-9a-f]{32}):", prefixes[0])
    assert match, prefixes
    nonce = match.group(1)
    assert helper_tokens == [
        tokens[0], jobs._ATTEMPT_DISPOSITION_FLAG, request_id, nonce,
        attempt, path], helper_tokens
    assert jobs._REMOTE_STATUS_SENTINEL in status_tail, status_tail
    return request_id, nonce, attempt


def _status_record_for_payload(jobs, payload, status, disposition=None):
    prefixes = _bound_status_prefixes(jobs, payload)
    assert len(prefixes) == 1, (
        "the launch payload has no unique bound status prefix: %r" % payload)
    status_record = "%s%d\n" % (prefixes[0], status)
    binding = _disposition_binding_for_payload(jobs, payload)
    if binding is None:
        assert disposition is None
        return status_record
    assert disposition in {"ADOPTED", "NOT_ADOPTED", "UNKNOWN"}, (
        "the fake target must state disposition explicitly, never infer it "
        "from status %r" % status)
    request_id, nonce, attempt = binding
    return "%s%s:%s:%s:%s\n%s" % (
        jobs._REMOTE_DISPOSITION_SENTINEL, request_id, nonce, attempt,
        disposition, status_record)


def _identity_record_for_command(jobs, cmd):
    """Answer one target identity probe with a bound regular-file identity."""
    if not cmd or cmd[0] != "ssh":
        return None
    tokens = shlex.split(cmd[-1])
    if jobs._ATTEMPT_IDENTITY_FLAG not in tokens:
        return None
    index = tokens.index(jobs._ATTEMPT_IDENTITY_FLAG)
    request_id, nonce, attempt = tokens[index + 1:index + 4]
    return "%s%s:%s:%s:PRESENT:1:2:%d\n" % (
        jobs._REMOTE_IDENTITY_SENTINEL, request_id, nonce, attempt,
        stat.S_IFREG)


# MUTATION DESIGN (durable spec intentionally out of this task): a fixed or
# caller-derived nonce, an unbound prefix, last-record-wins parsing, acceptance
# of duplicate/truncated/nonterminal/noncanonical records, cleanup on UNKNOWN,
# and removal of the request-token grammar each have a direct catcher below.
def test_remote_status_parser_accepts_only_canonical_bounded_terminal_codes(jobs):
    request_id = "request-safe_1"
    nonce = "0123456789abcdef" * 2
    prefix = "%s%s:%s:" % (jobs._REMOTE_STATUS_SENTINEL, request_id, nonce)

    for status in (0, 1, 9, 10, 99, 100, 254, 255):
        text = "target diagnostic for %d\n%s%d\n" % (status, prefix, status)
        assert jobs._parse_target_status(text, request_id, nonce) == (
            status, "target diagnostic for %d\n" % status)

    for token in ("", "-1", "+1", "00", "01", "256", "999", " 0",
                  "0 ", "0\r", "1.0"):
        text = "%s%s\n" % (prefix, token)
        assert jobs._parse_target_status(text, request_id, nonce) == (None, text)


def test_unbound_caller_markers_remain_raw_beside_one_matching_terminal_record(
        jobs):
    request_id = "expected-request"
    nonce = "fedcba9876543210" * 2
    prefix = "%s%s:%s:" % (jobs._REMOTE_STATUS_SENTINEL, request_id, nonce)
    caller = (jobs._REMOTE_STATUS_SENTINEL + "0\n"
              + jobs._REMOTE_STATUS_SENTINEL
              + "wrong-request:%s:2\n" % nonce
              + jobs._REMOTE_STATUS_SENTINEL + request_id + ":"
              + ("0" * 32) + ":3\n")
    text = caller + prefix + "7\n"

    assert jobs._parse_target_status(text, request_id, nonce) == (7, caller)


def test_remote_status_nonce_is_unpredictable_and_bound_to_request_identity(
        jobs, monkeypatch, capsys):
    purpose = "caller-purpose-" + ("ab" * 16)

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            prefix = _candidate_status_prefix(jobs, cmd[-1])
            return (0, "", "%s0\n" % prefix)
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    assert jobs.cmd_run(_remote_args(
        purpose=purpose, command=["--", "sleep", "45"])) == 0
    assert jobs.cmd_run(_remote_args(
        purpose=purpose, command=["--", "sleep", "45"])) == 0
    capsys.readouterr()

    launches = [c[-1] for c in calls if c and c[0] == "ssh"
                and jobs._REMOTE_STATUS_SENTINEL in c[-1]]
    assert len(launches) == 2, launches
    prefixes = []
    for payload in launches:
        found = _bound_status_prefixes(jobs, payload)
        assert len(found) == 1, (
            "the terminal record has no single request-bound 128-bit nonce: %r"
            % payload)
        request_id = _delegated_tokens(payload)[
            _delegated_tokens(payload).index("--request-id") + 1]
        match = re.fullmatch(
            re.escape(jobs._REMOTE_STATUS_SENTINEL)
            + r"([0-9a-f]{8,64}):([0-9a-f]{32}):", found[0])
        assert match and match.group(1) == request_id, (match, request_id)
        assert match.group(2) not in purpose, (
            "the nonce was derived from caller-controlled purpose")
        prefixes.append(found[0])
    assert prefixes[0] != prefixes[1], (
        "two launch attempts reused one allegedly unpredictable status nonce")


def test_truncated_caller_diagnostic_cannot_forge_request_bound_target_status(
        jobs, monkeypatch, tmp_path, capsys):
    injected = jobs._REMOTE_STATUS_SENTINEL + "2"
    purpose = "attacker text\n%s\ntrailing diagnostic" % injected
    script = tmp_path / "forged-status.sh"
    script.write_text("sleep 45\n", encoding="utf-8")

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            # Transport is cut after caller-controlled diagnostics and before
            # the target shell can append its bound terminal record.
            return (255, "", "REFUSED: %s\n" % purpose)
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(purpose=purpose, script=str(script)))
    out, err = capsys.readouterr()
    scp = [c for c in calls if c and c[0] == "scp"]
    assert len(scp) == 1, calls
    copied = scp[0][-1].split(":", 1)[1]
    payloads = [c[-1] for c in calls
                if c and c[0] == "ssh" and _STATUS_TAIL in c[-1]]
    assert len(payloads) == 1, calls
    payload = payloads[0]
    request_id = _delegated_tokens(payload)[
        _delegated_tokens(payload).index("--request-id") + 1]
    removals = [c for c in calls if c and c[0] == "ssh"
                and jobs._ATTEMPT_CLEANUP_FLAG in c[-1] and copied in c[-1]]
    bare_removals = [c for c in calls if c and c[0] == "ssh"
                     and c[-1].startswith("rm -f ")]

    assert rc == jobs._EXIT_REMOTE_UNKNOWN and out == "", (rc, out, err)
    assert injected in err, (
        "caller-controlled stderr was consumed as protocol evidence: %r" % err)
    assert copied in err and request_id in err and "RETAINED UNKNOWN" in err, err
    assert removals == [], (
        "a forged caller diagnostic authorized deletion of %s: %r"
        % (copied, removals))
    assert bare_removals == [], bare_removals


def test_remote_status_requires_exactly_one_matching_request_nonce(
        jobs, monkeypatch, capsys):
    records = []

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            prefix = _candidate_status_prefix(jobs, cmd[-1])
            records[:] = [prefix + "5", prefix + "0"]
            return (0, "", "\n".join(records) + "\n")
        return (0, "", "")

    _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(command=["--", "sleep", "45"]))
    out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REMOTE_UNKNOWN and out == "", (rc, out, err)
    assert all(record in err for record in records), (
        "ambiguous protocol evidence was discarded instead of retained: %r"
        % err)


def test_duplicate_remote_status_record_is_unknown_not_accepted_once(
        jobs, monkeypatch, capsys):
    record = []

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            record[:] = [_candidate_status_prefix(jobs, cmd[-1]) + "7"]
            return (7, "", "%s\n%s\n" % (record[0], record[0]))
        return (0, "", "")

    _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(command=["--", "sleep", "45"]))
    out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REMOTE_UNKNOWN and out == "", (rc, out, err)
    assert err.count(record[0]) == 2, (
        "duplicate evidence was stripped or collapsed: %r" % err)


def test_truncated_remote_status_record_is_unknown_and_preserved_raw(
        jobs, monkeypatch, capsys):
    truncated = []

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            truncated[:] = [_candidate_status_prefix(jobs, cmd[-1]) + "5"]
            return (5, "", truncated[0])       # deliberately no terminal LF
        return (0, "", "")

    _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(command=["--", "sleep", "45"]))
    out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REMOTE_UNKNOWN and out == "", (rc, out, err)
    assert truncated[0] in err, (
        "truncated terminal evidence was consumed or rewritten: %r" % err)


def test_matching_remote_status_followed_by_stderr_is_unknown_and_preserved(
        jobs, monkeypatch, capsys):
    evidence = []

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            evidence[:] = [_candidate_status_prefix(jobs, cmd[-1]) + "0",
                           "diagnostic after alleged terminal record"]
            return (0, "", "\n".join(evidence) + "\n")
        return (0, "", "")

    _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(command=["--", "sleep", "45"]))
    out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REMOTE_UNKNOWN and out == "", (rc, out, err)
    assert all(line in err for line in evidence), (
        "non-terminal evidence was consumed or rewritten: %r" % err)


def test_remote_status_with_wrong_request_binding_is_unknown_and_preserved(
        jobs, monkeypatch, capsys):
    forged = []

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            prefix = _candidate_status_prefix(jobs, cmd[-1])
            request_id = _delegated_tokens(cmd[-1])[
                _delegated_tokens(cmd[-1]).index("--request-id") + 1]
            forged[:] = [prefix.replace(request_id, "wrong-request", 1) + "0"]
            return (0, "", forged[0] + "\n")
        return (0, "", "")

    _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(command=["--", "sleep", "45"]))
    out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REMOTE_UNKNOWN and out == "", (rc, out, err)
    assert forged[0] in err, "wrong-request evidence was not preserved: %r" % err


def test_ambiguous_bound_status_never_authorizes_remote_script_cleanup(
        jobs, monkeypatch, tmp_path, capsys):
    script = tmp_path / "ambiguous-status.sh"
    script.write_text("sleep 45\n", encoding="utf-8")
    records = []

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            prefix = _candidate_status_prefix(jobs, cmd[-1])
            records[:] = [prefix + "0", prefix + "3"]
            return (3, "", "\n".join(records) + "\n")
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(script=str(script)))
    out, err = capsys.readouterr()
    scp = [c for c in calls if c and c[0] == "scp"]
    assert len(scp) == 1, calls
    copied = scp[0][-1].split(":", 1)[1]
    removals = [c for c in calls if c and c[0] == "ssh"
                and jobs._ATTEMPT_CLEANUP_FLAG in c[-1] and copied in c[-1]]
    bare_removals = [c for c in calls if c and c[0] == "ssh"
                     and c[-1].startswith("rm -f ")]

    assert rc == jobs._EXIT_REMOTE_UNKNOWN and out == "", (rc, out, err)
    assert removals == [], (
        "ambiguous status authorized pathname deletion of %s: %r"
        % (copied, removals))
    assert bare_removals == [], bare_removals
    assert copied in err and "RETAINED UNKNOWN" in err, err
    assert all(record in err for record in records), err


def test_one_matching_request_nonce_authenticates_refusal_cleanup(
        jobs, monkeypatch, tmp_path, capsys):
    script = tmp_path / "authenticated-refusal.sh"
    script.write_text("sleep 45\n", encoding="utf-8")
    bound = []

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            found = _bound_status_prefixes(jobs, cmd[-1])
            bound[:] = found
            prefix = found[0] if len(found) == 1 else jobs._REMOTE_STATUS_SENTINEL
            return (2, "", _status_record_for_payload(
                jobs, cmd[-1], 2, disposition="NOT_ADOPTED"))
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(script=str(script)))
    out, err = capsys.readouterr()
    scp = [c for c in calls if c and c[0] == "scp"]
    assert len(scp) == 1, calls
    copied = scp[0][-1].split(":", 1)[1]
    removals = [c for c in calls if c and c[0] == "ssh"
                and jobs._ATTEMPT_CLEANUP_FLAG in c[-1] and copied in c[-1]]
    bare_removals = [c for c in calls if c and c[0] == "ssh"
                     and c[-1].startswith("rm -f ")]

    assert len(bound) == 1, "the positive control received no bound marker"
    assert rc == 2 and out == "", (rc, out, err)
    assert len(removals) == 1, (
        "one authenticated pre-adoption refusal did not invoke bound cleanup: %r"
        % removals)
    assert bare_removals == [], bare_removals
    assert copied in err and "removed" in err, err


def test_unsafe_explicit_request_id_is_refused_before_copy_or_launch(
        jobs, monkeypatch, tmp_path, capsys):
    script = tmp_path / "must-not-copy.sh"
    script.write_text("sleep 45\n", encoding="utf-8")
    calls = _fake_transport(jobs, monkeypatch)
    unsafe = "../caller\n" + jobs._REMOTE_STATUS_SENTINEL + "0"

    rc = jobs.cmd_run(_remote_args(script=str(script), request_id=unsafe))
    out, err = capsys.readouterr()

    assert rc == 2 and out == "", (rc, out, err)
    assert calls == [], "unsafe request identity reached transport: %r" % calls
    assert "request id" in err.lower() and "nothing was copied" in err.lower(), err


def test_request_id_grammar_accepts_its_boundary_and_refuses_overlong(
        jobs, monkeypatch, capsys):
    safe = "A" * 128
    accepted_calls = _fake_transport(jobs, monkeypatch)
    accepted = jobs.cmd_run(_remote_args(
        request_id=safe, command=["--", "sleep", "45"]))
    payload = _payload(accepted_calls)
    tokens = _delegated_tokens(payload)
    assert accepted == 0 and tokens[tokens.index("--request-id") + 1] == safe
    assert _bound_status_prefixes(jobs, payload), (
        "the maximum-length safe request id was not bound into the protocol")

    refused_calls = _fake_transport(jobs, monkeypatch)
    refused = jobs.cmd_run(_remote_args(
        request_id="A" * 129, command=["--", "sleep", "45"]))
    out, err = capsys.readouterr()
    assert refused == 2 and refused_calls == [] and out == "", (
        refused, refused_calls, out, err)
    assert "request id" in err.lower() and "nothing was copied" in err.lower()


def test_the_remote_command_delegates_to_the_target_transaction(
        jobs, monkeypatch, tmp_path):
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")
    calls = _fake_transport(jobs, monkeypatch)
    parts = ["bash", "-c", "echo a && echo b"]

    rc = jobs.cmd_run(_remote_args(command=["--"] + parts))
    payload = _payload(calls)
    tokens = _delegated_tokens(payload)

    assert tokens[:4] == ["/opt/bd/bd-jobs", "run", "--host", "local"], (
        "the launcher did not delegate to the target's own transaction: %r"
        % tokens)
    assert tokens[-(len(parts) + 1):] == ["--"] + parts, (
        "the user's argv did not survive as argv: %r" % tokens)
    for flag, value in (("--purpose", "p"), ("--origin", "launcher-host")):
        assert flag in tokens and tokens[tokens.index(flag) + 1] == value, (
            "%s was not delegated: %r" % (flag, tokens))
    assert "--request-id" in tokens, (
        "the delegated argv carries no request id, so an ambiguous transport "
        "outcome could never be reconciled against the target: %r" % tokens)
    request_id = tokens[tokens.index("--request-id") + 1]
    assert re.fullmatch(r"[0-9a-f]{8,64}", request_id), request_id
    for banned in ("nohup", "$$", "P=$!", "echo $P"):
        assert banned not in payload, (
            "the old unregistered-launch protocol is still in the payload "
            "(%r): %r" % (banned, payload))
    assert rc == 0, rc


def test_the_remote_payload_cannot_mask_a_target_failure(
        jobs, monkeypatch, tmp_path):
    """MEASURED on the base: the target's `register` exited 73 and the ssh
    command still exited 0 with a job id on stdout. The status must be captured
    BEFORE any diagnostic, and nothing may follow it."""
    stub = tmp_path / "refusing-target"
    stub.write_text("#!/bin/sh\necho 'STUB REGISTER FAILED' >&2\nexit 73\n",
                    encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", str(stub))
    run_marker = _run_marker("masking")
    calls = _fake_transport(jobs, monkeypatch)
    jobs.cmd_run(_remote_args(command=["--", "sleep", "45"]))
    payload = _payload(calls)
    expected_status = _status_record_for_payload(jobs, payload, 73).strip()
    # THE PAYLOAD'S OWN ENVIRONMENT, built BEFORE the undo below: whatever the
    # payload resolves to -- the stub, or this tool itself if `_remote_self` is
    # broken -- inherits a registry that is not the live one.
    registry = tmp_path / "target-registry"
    # UNDO FIRST. `jobs.subprocess` IS the global module, so the capture above
    # is still installed here -- executing the payload through it would run the
    # FAKE and assert over a CompletedProcess nobody produced.
    monkeypatch.undo()

    escaped = []
    try:
        executed = subprocess.run(
            ["bash", "-c", payload],
            env=dict(os.environ, BD_JOBS_RUN_MARKER=run_marker),
            capture_output=True, text=True, timeout=60)
        # OBSERVE BEFORE ANY CLEANUP. The `finally` below bounds a failing or
        # mutated run by reaping this run's marked entries out of the canonical
        # registry -- so if that ran first, contamination would be tidied away
        # and this node would report PASS over it.
        escaped = _marked_residue(run_marker, _REAL_JOBS)

        assert executed.returncode == 73, (
            "the target's refusal was masked: the remote command exited %d"
            % executed.returncode)
        assert expected_status in executed.stderr, (
            "no authenticated status sentinel: a target status is then "
            "indistinguishable from ssh's own 255. stderr=%r" % executed.stderr)
        assert executed.stdout.strip() == "", (
            "a job id was printed for a target that refused: %r"
            % executed.stdout)
    finally:
        # The stub launches nothing -- but a broken self-path bypasses it and
        # runs the real transaction through the fixture's own copy, so every
        # namespace this node could have written to is reaped, INCLUDING the
        # canonical one: a mutated run must not leave live work on the host.
        # Only entries carrying this run's marker are ever signalled.
        _reap_marked_entries(registry, run_marker)
        _reap_marked_entries(jobs.JOBS_DIR, run_marker)
        _reap_marked_entries(_REAL_JOBS, run_marker)
    assert escaped == [], (
        "this node registered in the canonical registry: its target stub was "
        "bypassed, so it was measuring this host rather than an isolated one: "
        "%r" % (escaped,))


def test_a_delegated_launch_registers_on_the_target_and_owns_its_log(
        jobs, monkeypatch, tmp_path):
    """The whole remote contract, executed: the target's own gated transaction
    runs, so the entry has the target's log, the launcher's origin, the exact
    local `cmd`, its own process group, and the id last on stdout."""
    registry = tmp_path / "target-registry"
    wrapper = _target_wrapper(tmp_path, registry)
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", str(wrapper))
    run_marker = _run_marker("delegated-launch")
    calls = _fake_transport(jobs, monkeypatch)
    parts = ["bash", "-c", "echo hi; sleep 45"]
    jobs.cmd_run(_remote_args(command=["--"] + parts))
    payload = _payload(calls)
    tokens = _delegated_tokens(payload)
    request_id = tokens[tokens.index("--request-id") + 1]
    monkeypatch.undo()          # the capture above still owns subprocess.run

    entry = None
    escaped = []
    try:
        executed = subprocess.run(
            ["bash", "-c", payload],
            env=dict(os.environ, BD_JOBS_RUN_MARKER=run_marker),
            capture_output=True, text=True, timeout=120)
        escaped = _marked_residue(run_marker, _REAL_JOBS)   # observe first
        assert executed.returncode == 0, (
            "the delegated launch failed: %s%s"
            % (executed.stdout, executed.stderr))
        finals = sorted(registry.glob("*.json"))
        assert len(finals) == 1, [f.name for f in finals]
        entry = json.loads(finals[0].read_text(encoding="utf-8"))

        assert entry["origin"] == "launcher-host", (
            "the launching host was not preserved as origin: %r" % entry)
        assert entry["cmd"] == shlex.join(parts), (
            "the recorded command is not byte-identical to the local case: %r"
            % entry["cmd"])
        assert entry.get("log"), "the delegated entry records no log: %r" % entry
        assert pathlib.Path(entry["log"]).parent == registry, (
            "the log is not the target's own: %r" % entry["log"])
        assert entry.get("request_id") == request_id, (
            "the entry does not carry the request id that was delegated "
            "(%r vs %r), so an ambiguous transport outcome is unreconcilable"
            % (entry.get("request_id"), request_id))
        assert os.getpgid(entry["pid"]) == entry["pid"], (
            "the delegated job does not lead its own process group, so reap "
            "would orphan its children -- backlog 88 on the remote path")
        assert _last_non_empty(executed.stdout) == entry["id"], (
            "the id is not the last stdout line of the remote path: %r"
            % executed.stdout)
        assert _wait_until(
            lambda: "hi" in pathlib.Path(entry["log"]).read_text(
                encoding="utf-8", errors="replace"), 20), (
            "the job's output never reached the log the entry names")
        assert jobs.proc_starttime(entry["pid"]) is not None, (
            "the launcher waited for the delegated job instead of returning "
            "while it runs")
    finally:
        if entry:
            _reap_process(entry["pid"])
        _reap_marked_entries(registry, run_marker)
        _reap_marked_entries(jobs.JOBS_DIR, run_marker)
        _reap_marked_entries(_REAL_JOBS, run_marker)
    assert escaped == [], (
        "the delegated launch registered itself in the canonical /tmp/bd-jobs: "
        "the isolated target was bypassed, so every assertion above was about "
        "this host: %r" % (escaped,))


def test_a_transport_failure_without_a_sentinel_is_unknown_not_a_refusal(
        jobs, monkeypatch, capsys):
    """ssh exits 255 for its OWN failures -- and a connection that dropped
    after the command was accepted looks identical to one that never landed.
    Reporting "nothing ran" there is a claim nobody measured; the honest
    answer is UNKNOWN, with the request id to reconcile it against."""
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")

    def outcome(cmd):
        if "test -f" in " ".join(cmd):
            return (0, "", "")
        return (255, "", "ssh: connect to host somewhere port 22: "
                         "Connection timed out\n")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(command=["--", "sleep", "45"]))
    err = capsys.readouterr().err
    tokens = _delegated_tokens(_payload(calls))
    request_id = tokens[tokens.index("--request-id") + 1]

    assert rc == jobs._EXIT_REMOTE_UNKNOWN, (
        "an ambiguous transport outcome was graded with an ordinary code "
        "(rc=%r): 2 means the launcher refused and nothing ran" % rc)
    assert "UNKNOWN" in err and "may have launched" in err.lower(), (
        "the operator was told the job did not start, which nobody measured: "
        "%r" % err)
    assert request_id in err, (
        "the diagnostic does not carry the request id, so the operator cannot "
        "reconcile it on the target: %r" % err)
    assert "deploy it there first" not in err, err


def test_transport_unknown_names_copied_script_without_deleting_ambiguous_authority(
        jobs, monkeypatch, tmp_path, capsys):
    """No sentinel means the target may have adopted the copied script.

    Mutation design N63: replacing the retained-script branch with ``pass``
    loses the exact path; routing UNKNOWN through authenticated refusal cleanup
    adds an observable rm.
    """
    script = tmp_path / "ambiguous.sh"
    script.write_text("sleep 45\n", encoding="utf-8")
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            return (255, "", "transport dropped after target acceptance\n")
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(script=str(script)))
    out, err = capsys.readouterr()

    scp = [c for c in calls if c and c[0] == "scp"]
    assert len(scp) == 1, "the owned remote copy seam did not fire: %r" % calls
    copied = scp[0][-1].split(":", 1)[1]
    tokens = _delegated_tokens(_payload(calls))
    request_id = tokens[tokens.index("--request-id") + 1]
    adopted = tokens[tokens.index("--adopt-script") + 1]
    assert adopted == copied, (
        "the ambiguous delegated transaction did not adopt the path scp wrote: "
        "%r != %r" % (adopted, copied))
    removals = [c for c in calls if c and c[0] == "ssh" and copied in c[-1]
                and (c[-1].startswith("rm -f ")
                     or jobs._ATTEMPT_CLEANUP_FLAG in shlex.split(c[-1]))]
    assert rc == jobs._EXIT_REMOTE_UNKNOWN and out == ""
    assert copied in err and request_id in err and "RETAINED UNKNOWN" in err, (
        "the ambiguous outcome did not name its retained remote copy: %r"
        % err)
    assert "may be owned" in err.lower() and "may be unowned" in err.lower(), err
    assert removals == [], (
        "the launcher deleted %s although the target may own it: %r"
        % (copied, removals))


def test_a_target_status_of_255_is_distinguished_from_a_dead_transport(
        jobs, monkeypatch, capsys):
    """OVER-SENSITIVITY CONTROL. 255 WITH an authenticated sentinel is the
    target's own answer and must be relayed, not turned into UNKNOWN."""
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")

    def outcome(cmd):
        if "test -f" in " ".join(cmd):
            return (0, "", "")
        return (255, "", "target said no\n%s"
                % _status_record_for_payload(jobs, cmd[-1], 255))

    _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(command=["--", "sleep", "45"]))
    err = capsys.readouterr().err

    assert rc == 255, (
        "an authenticated target status was rewritten by the launcher: %r" % rc)
    assert "UNKNOWN" not in err, (
        "a target that answered was reported as an unreachable host: %r" % err)


def test_run_accepts_script_and_origin_from_the_command_line(
        jobs, monkeypatch, tmp_path, capsys):
    """MEASURED: `--script` has been advertised since v3.66.1047 and argparse
    never owned the flag, so every operator invocation died with
    `unrecognized arguments`. A namespace-only test cannot see that."""
    script = tmp_path / "sweep.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")
    calls = _fake_transport(jobs, monkeypatch)

    rc = jobs.main(["run", "--host", "somewhere", "--purpose", "p",
                    "--origin", "elsewhere", "--script", str(script)])

    assert rc == 0, (rc, capsys.readouterr())
    scp = [c for c in calls if c and c[0] == "scp"]
    assert scp, "the script was never copied to the target: %r" % (calls,)
    tokens = _delegated_tokens(_payload(calls))
    assert tokens[tokens.index("--origin") + 1] == "elsewhere", (
        "--origin was accepted by argparse but not delegated: %r" % tokens)


def test_a_refused_script_launch_removes_the_copy_it_made(
        jobs, monkeypatch, tmp_path, capsys):
    """A missing target tool is known before any copied resource is acquired."""
    script = tmp_path / "sweep.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")

    def outcome(cmd):
        # The reachable target answers that the required tool is absent.
        if "test -f" in " ".join(cmd):
            return (1, "", "")
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.main(["run", "--host", "somewhere", "--purpose", "p",
                    "--script", str(script)])
    err = capsys.readouterr().err

    assert rc == 2, rc
    probes = [c for c in calls if c and c[0] == "ssh"
              and "test -f" in c[-1]]
    assert calls == probes and len(probes) == 1, (
        "mkdir/copy/identity/cleanup/launch preceded the deploy refusal: %r"
        % calls)
    assert not any(c and c[0] == "scp" for c in calls), calls
    assert "deploy it there first" in err and "RETAINED" not in err, err


def test_a_status_5_script_launch_preserves_the_copy_the_retained_record_owns(
        jobs, monkeypatch, tmp_path, capsys):
    """Status 5 proves a record exists; its adopted script is not caller-owned."""
    script = tmp_path / "sweep.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            return (5, "", _status_record_for_payload(
                jobs, cmd[-1], 5, disposition="ADOPTED"))
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.main(["run", "--host", "somewhere", "--purpose", "p",
                    "--script", str(script)])
    err = capsys.readouterr().err

    assert rc == jobs._EXIT_PUBLISHED_NOT_DURABLE
    scp = [c for c in calls if c and c[0] == "scp"]
    assert scp, "nothing was copied, so this node proves nothing: %r" % (calls,)
    copied = scp[0][-1].split(":", 1)[1]
    removals = [c for c in calls if c and c[0] == "ssh" and copied in c[-1]
                and (c[-1].startswith("rm -f ")
                     or jobs._ATTEMPT_CLEANUP_FLAG in shlex.split(c[-1]))]
    assert removals == [], (
        "the launcher deleted %s although status 5 proves a retained target "
        "record owns it: %r" % (copied, removals))
    assert "RETAINED" in err and copied in err, (
        "the launcher did not name the retained target copy: %r" % err)


def test_a_status_7_script_launch_preserves_the_copy_the_durable_record_owns(
        jobs, monkeypatch, tmp_path, capsys):
    """Close-UNKNOWN is nonzero, but its durable record adopted the script."""
    script = tmp_path / "sweep.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            return (7, "target-id\n",
                    _status_record_for_payload(
                        jobs, cmd[-1], 7, disposition="ADOPTED"))
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.main(["run", "--host", "somewhere", "--purpose", "p",
                    "--script", str(script)])
    out, err = capsys.readouterr()

    assert rc == 7 and out.strip() == "target-id"
    scp = [c for c in calls if c and c[0] == "scp"]
    assert scp, "nothing was copied, so this node proves nothing: %r" % (calls,)
    copied = scp[0][-1].split(":", 1)[1]
    removals = [c for c in calls if c and c[0] == "ssh" and copied in c[-1]
                and (c[-1].startswith("rm -f ")
                     or jobs._ATTEMPT_CLEANUP_FLAG in shlex.split(c[-1]))]
    assert removals == [], (
        "the launcher deleted %s although close-UNKNOWN has a durable target "
        "record that owns it: %r" % (copied, removals))
    assert "RETAINED" in err and copied in err, err


def test_the_remote_self_path_is_the_installed_public_surface(jobs, monkeypatch):
    """MEASURED: `/usr/local/bin/bd-jobs` is a root-owned symlink into a
    per-user staged copy, so `Path(__file__).resolve()` collapses the uniform
    public path into a HOME-anchored one -- and from this worktree into a
    `.worktrees/...` path that exists on no target."""
    monkeypatch.delenv("BD_JOBS_REMOTE_SELF", raising=False)
    default = jobs._remote_self()
    assert default == "/usr/local/bin/bd-jobs", (
        "the delegated tool path is not the installed public surface: %r"
        % default)
    assert ".worktrees" not in default and "/home/" not in default

    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")
    assert jobs._remote_self() == "/opt/bd/bd-jobs", (
        "the deterministic override seam does not work")


def test_hostile_command_tokens_survive_exactly_one_quoting_layer(
        jobs, monkeypatch, tmp_path):
    """One quoting layer, and the tokens arrive as tokens.

    Every element here is a token an operator can legitimately pass and a shell
    would happily reinterpret: a leading dash, embedded whitespace and a
    newline, both quote characters, a command separator, and text that LOOKS
    like a substitution. The recording stub proves what the target's argv
    actually was, and the marker proves nothing was evaluated on the way.
    """
    marker = tmp_path / "pwned"
    seen = tmp_path / "argv.json"
    stub = tmp_path / "recording-target"
    stub.write_text(
        "#!/usr/bin/env %s\n"
        "import json, sys\n"
        "json.dump(sys.argv[1:], open(%r, 'w'))\n"
        % (sys.executable, str(seen)), encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", str(stub))
    parts = ["-n", "a b", "it's \"quoted\"", "; rm -rf /",
             "$(touch %s)" % marker, "line\nbreak"]
    run_marker = _run_marker("hostile-tokens")
    calls = _fake_transport(jobs, monkeypatch)
    jobs.cmd_run(_remote_args(command=["--"] + parts))
    payload = _payload(calls)
    monkeypatch.undo()          # the capture above still owns subprocess.run

    escaped = []
    try:
        executed = subprocess.run(
            ["bash", "-c", payload],
            env=dict(os.environ, BD_JOBS_RUN_MARKER=run_marker),
            capture_output=True, text=True, timeout=60)
        escaped = _marked_residue(run_marker, _REAL_JOBS)   # observe first

        assert seen.exists(), (
            "the target was never reached: %s%s"
            % (executed.stdout, executed.stderr))
        argv = json.loads(seen.read_text(encoding="utf-8"))
        assert argv[argv.index("--") + 1:] == parts, (
            "hostile tokens did not survive the remote shell intact:\n%r\n%r"
            % (argv[argv.index("--") + 1:], parts))
        assert not marker.exists(), (
            "a command substitution in a USER TOKEN was evaluated by the outer "
            "shell: %s" % marker)
    finally:
        # The recording stub launches nothing; a broken self-path runs the real
        # transaction through the fixture's copy instead, so reap that too --
        # and the canonical registry, to bound a mutated run.
        _reap_marked_entries(jobs.JOBS_DIR, run_marker)
        _reap_marked_entries(_REAL_JOBS, run_marker)
    assert escaped == [], (
        "the recording target was bypassed and this node registered in the "
        "canonical registry: %r" % (escaped,))


def test_a_target_address_with_control_characters_is_refused(jobs, monkeypatch):
    """A resolved address goes into an argv AND into diagnostics. A newline or
    an option-shaped label there is not something to quote around: it is
    something no legitimate target has."""
    _fake_transport(jobs, monkeypatch)
    for hostile in ("has space", "line\nbreak", "-oProxyCommand=touch /tmp/x",
                    "bell\x07"):
        rc = jobs.cmd_run(_remote_args(host=hostile,
                                       command=["--", "sleep", "45"]))
        assert rc == 2, (
            "a target address containing %r was accepted (rc=%r)"
            % (hostile, rc))


def test_ssh_and_scp_terminate_their_own_option_parsing(
        jobs, monkeypatch, tmp_path):
    """`--` before the target, in every transport argv. Without it a host label
    that begins with a dash is read by ssh as its own option."""
    script = tmp_path / "sweep.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")
    calls = _fake_transport(jobs, monkeypatch)
    jobs.main(["run", "--host", "somewhere", "--purpose", "p",
               "--script", str(script)])

    for call in calls:
        assert "--" in call, (
            "%s never terminates its option parsing: %r" % (call[0], call))
        target_index = call.index("--") + 1
        assert target_index < len(call), call
        assert not call[target_index].startswith("-"), (
            "the token after -- is still option-shaped: %r" % call)


def test_a_local_script_runs_the_file_and_stays_the_operator_s_own(
        jobs, tmp_path, capsys):
    """`--script` exists because an inline command crosses three quoting layers
    and this tool has shipped two bugs at that seam. Locally the file is simply
    RUN -- and it is the OPERATOR's file: `forget()` may reclaim the copies
    this tool made on a target, never a path the caller handed it."""
    marker = tmp_path / "script-ran.marker"
    script = tmp_path / "operators.sh"
    script.write_text("touch %s\nsleep 45\n" % marker, encoding="utf-8")
    entry = None
    try:
        rc = jobs.main(["run", "--host", "local", "--purpose", "local-script",
                        "--script", str(script)])
        capsys.readouterr()
        assert rc == 0, rc
        entries = jobs.load_all()
        assert len(entries) == 1, entries
        entry = entries[0]
        assert entry["cmd"] == "bash %s" % script, (
            "the local path did not run the script file itself: %r"
            % entry["cmd"])
        assert _wait_until(marker.exists), "the script never ran"
        assert "owned_paths" not in entry, (
            "an operator's own script was adopted as this tool's to delete: %r"
            % entry)

        jobs.forget(entry)

        assert script.exists(), (
            "forget() deleted the operator's script: %s" % script)
    finally:
        if entry:
            _reap_process(entry["pid"])


def test_the_local_path_is_unchanged_by_delegation(jobs, monkeypatch, tmp_path):
    """OVER-SENSITIVITY CONTROL: `--host local` must still take the gated local
    transaction and touch no transport at all."""
    monkeypatch.setattr(jobs.subprocess, "run", lambda *a, **k: pytest.fail(
        "the local path used the transport"))
    entry = None
    try:
        rc = jobs.cmd_run(_remote_args(host="local",
                                       command=["--", "bash", "-c", "sleep 45"]))
        assert rc == 0, rc
        entries = jobs.load_all()
        assert len(entries) == 1, entries
        entry = entries[0]
        assert entry.get("request_id"), (
            "a locally launched entry carries no request id: %r" % entry)
    finally:
        if entry:
            _reap_process(entry["pid"])


# ── v3.66.1206: adjudicated ownership remediation G1-G7 ─────────────────────


def test_repeated_forget_never_rebinds_cleanup_authority_after_final_absence(
        jobs, sleeper):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-repeated-forget.sh"
    owned.write_text("first owner\n", encoding="utf-8")
    # Keep the original inode allocated after forget() unlinks its pathname.
    # Filesystems may otherwise immediately reuse that inode, making an
    # identity-replacement precondition nondeterministic.
    with owned.open("rb") as original_owner:
        first_inode = os.fstat(original_owner.fileno()).st_ino
        entry = jobs.register(
            p.pid, "first owner", "sleep 60", owned_paths=[str(owned)])
        final = pathlib.Path(entry["_path"])

        first = jobs.forget(entry)
        assert first["cleanup_complete"] is True, first
        assert not final.exists() and not owned.exists()
        owned.write_text("replacement owner\n", encoding="utf-8")
        assert owned.stat().st_ino != first_inode, (
            "precondition: inode was not replaced")
        replacement = owned.read_bytes()

    second = jobs.forget(entry)

    assert owned.read_bytes() == replacement, (
        "an absent final reacquired authority over a replacement inode")
    assert second["cleanup_complete"] is False, second
    notes = "; ".join(second["notes"])
    assert str(final) in notes and str(owned) in notes and "authority" in notes.lower()
    assert list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*")) == []


def test_pre_replace_failure_names_retained_stage_when_stage_unlink_fails(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_mkstemp = jobs.tempfile.mkstemp
    real_unlink = jobs.os.unlink
    staged = []

    def capture_stage(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        if kwargs.get("prefix") == jobs._ENTRY_TEMP_PREFIX:
            staged.append(pathlib.Path(path))
        return fd, path

    def fail_stage_unlink(path):
        if staged and pathlib.Path(path) == staged[0]:
            raise PermissionError("injected stage unlink refusal")
        return real_unlink(path)

    monkeypatch.setattr(jobs.tempfile, "mkstemp", capture_stage)
    monkeypatch.setattr(jobs.os, "fsync", lambda fd: (_ for _ in ()).throw(
        OSError(5, "injected staged fsync EIO")))
    monkeypatch.setattr(jobs.os, "unlink", fail_stage_unlink)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "retained stage", "sleep 60")

    assert len(staged) == 1 and staged[0].exists(), staged
    assert getattr(excinfo.value, "cleanup_complete", None) is False
    assert getattr(excinfo.value, "retained_paths", None) == [str(staged[0])]
    assert str(staged[0]) in str(excinfo.value)
    assert list(jobs.JOBS_DIR.glob("*.json")) == []


def test_stage_fdopen_failure_accounts_for_raw_fd_and_path(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_mkstemp = jobs.tempfile.mkstemp
    real_fdopen = jobs.os.fdopen
    real_close = jobs.os.close
    opened = []
    closed = []

    def capture_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        opened.append((fd, pathlib.Path(path)))
        return fd, path

    def fail_fdopen(fd, *args, **kwargs):
        if opened and fd == opened[0][0]:
            raise OSError(24, "injected fdopen failure")
        return real_fdopen(fd, *args, **kwargs)

    def capture_close(fd):
        if opened and fd == opened[0][0]:
            closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(jobs.tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(jobs.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(jobs.os, "close", capture_close)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "fdopen failure", "sleep 60")

    assert len(opened) == 1 and closed == [opened[0][0]], (opened, closed)
    assert not opened[0][1].exists(), opened
    assert getattr(excinfo.value, "cleanup_complete", None) is True
    assert getattr(excinfo.value, "retained_paths", None) == []


def test_stage_fdopen_and_raw_close_unknown_retain_without_unlink_or_retry(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_mkstemp = jobs.tempfile.mkstemp
    real_fdopen = jobs.os.fdopen
    real_close = jobs.os.close
    real_unlink = jobs.os.unlink
    staged = []
    closes = []
    unlinks = []

    def capture_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        staged.append((fd, pathlib.Path(path)))
        return fd, path

    def fail_fdopen(fd, *args, **kwargs):
        if staged and fd == staged[0][0]:
            raise OSError(24, "PRIMARY injected fdopen EMFILE")
        return real_fdopen(fd, *args, **kwargs)

    def fail_raw_close(fd):
        if staged and fd == staged[0][0]:
            closes.append(fd)
            real_close(fd)
            raise OSError(5, "SECONDARY injected raw close EIO")
        return real_close(fd)

    def capture_unlink(path):
        unlinks.append(pathlib.Path(path))
        return real_unlink(path)

    monkeypatch.setattr(jobs.tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(jobs.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(jobs.os, "close", fail_raw_close)
    monkeypatch.setattr(jobs.os, "unlink", capture_unlink)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "fdopen close unknown", "sleep 60")

    assert len(staged) == 1 and closes == [staged[0][0]], (staged, closes)
    assert unlinks == [], "close UNKNOWN authorized an unlink: %r" % unlinks
    assert staged[0][1].exists(), "the possibly-open stage lost its name"
    assert getattr(excinfo.value, "cleanup_complete", None) is False
    assert getattr(excinfo.value, "retained_paths", None) == [str(staged[0][1])]
    message = str(excinfo.value)
    assert "PRIMARY injected fdopen EMFILE" in message, message
    assert "SECONDARY injected raw close EIO" in message, message
    assert str(staged[0][1]) in message, message


def test_stage_fdopen_cleanup_unlink_failure_is_attempted_only_once(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_mkstemp = jobs.tempfile.mkstemp
    real_fdopen = jobs.os.fdopen
    real_unlink = jobs.os.unlink
    staged = []
    unlinks = []

    def capture_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        staged.append(pathlib.Path(path))
        return fd, path

    def fail_fdopen(fd, *args, **kwargs):
        if staged:
            raise OSError(24, "PRIMARY injected fdopen EMFILE")
        return real_fdopen(fd, *args, **kwargs)

    def fail_first_unlink(path):
        if staged and pathlib.Path(path) == staged[0]:
            unlinks.append(pathlib.Path(path))
            if len(unlinks) == 1:
                raise OSError(1, "SECONDARY injected stage unlink EPERM")
        return real_unlink(path)

    monkeypatch.setattr(jobs.tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(jobs.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(jobs.os, "unlink", fail_first_unlink)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "fdopen unlink failure", "sleep 60")

    assert unlinks == staged, "stage cleanup was retried: %r" % unlinks
    assert staged[0].exists(), "a retry hid the first cleanup failure"
    assert getattr(excinfo.value, "cleanup_complete", None) is False
    assert getattr(excinfo.value, "retained_paths", None) == [str(staged[0])]
    message = str(excinfo.value)
    assert "PRIMARY injected fdopen EMFILE" in message, message
    assert "SECONDARY injected stage unlink EPERM" in message, message
    assert str(staged[0]) in message, message


def test_stage_cleanup_failure_is_attached_to_keyboard_interrupt_primary(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_mkstemp = jobs.tempfile.mkstemp
    real_unlink = jobs.os.unlink
    staged = []
    unlinks = []

    def capture_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        staged.append(pathlib.Path(path))
        return fd, path

    def interrupt_fsync(fd):
        raise KeyboardInterrupt("PRIMARY injected interrupt")

    def fail_unlink(path):
        if staged and pathlib.Path(path) == staged[0]:
            unlinks.append(pathlib.Path(path))
            raise OSError(1, "SECONDARY injected interrupt cleanup EPERM")
        return real_unlink(path)

    monkeypatch.setattr(jobs.tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(jobs.os, "fsync", interrupt_fsync)
    monkeypatch.setattr(jobs.os, "unlink", fail_unlink)

    with pytest.raises(KeyboardInterrupt) as excinfo:
        jobs.register(p.pid, "interrupt cleanup", "sleep 60")

    assert len(staged) == 1 and unlinks == staged
    assert staged[0].exists()
    message = str(excinfo.value)
    assert "PRIMARY injected interrupt" in message, message
    assert "SECONDARY injected interrupt cleanup EPERM" in message, message
    assert str(staged[0]) in message, message


def test_stage_close_unknown_retains_named_path_without_retry_or_unlink(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_mkstemp = jobs.tempfile.mkstemp
    real_fdopen = jobs.os.fdopen
    real_unlink = jobs.os.unlink
    staged = []
    closes = []
    unlinks = []

    class CloseUnknownFile:
        def __init__(self, inner):
            self.inner = inner
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            self.close()
        def __getattr__(self, name):
            return getattr(self.inner, name)
        def close(self):
            closes.append(self.inner.fileno())
            self.inner.close()
            raise OSError(5, "injected staged close EIO")

    def capture_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        staged.append(pathlib.Path(path))
        return fd, path

    def wrap_fdopen(fd, *args, **kwargs):
        return CloseUnknownFile(real_fdopen(fd, *args, **kwargs))

    def capture_unlink(path):
        unlinks.append(pathlib.Path(path))
        return real_unlink(path)

    monkeypatch.setattr(jobs.tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(jobs.os, "fdopen", wrap_fdopen)
    monkeypatch.setattr(jobs.os, "unlink", capture_unlink)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "close unknown", "sleep 60")

    assert len(staged) == 1 and staged[0].exists(), staged
    assert len(closes) == 1, "uncertain close was retried: %r" % closes
    assert staged[0] not in unlinks, "possibly-open stage was unlinked"
    assert getattr(excinfo.value, "cleanup_complete", None) is False
    assert getattr(excinfo.value, "retained_paths", None) == [str(staged[0])]
    assert "close" in str(excinfo.value).lower() and str(staged[0]) in str(excinfo.value)


def test_remote_script_attempts_are_unique_when_request_id_is_reused(
        jobs, monkeypatch, tmp_path, capsys):
    script = tmp_path / "same-request.sh"
    script.write_text("sleep 45\n", encoding="utf-8")
    calls = _fake_transport(
        jobs, monkeypatch,
        lambda cmd: (255, "", "transport dropped\n")
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]
        else (0, "", ""))
    request_id = "stable-request"

    first_rc = jobs.cmd_run(_remote_args(script=str(script), request_id=request_id))
    first_err = capsys.readouterr().err
    first_copy = [c for c in calls if c and c[0] == "scp"][-1][-1].split(":", 1)[1]
    first_count = len(calls)
    second_rc = jobs.cmd_run(_remote_args(script=str(script), request_id=request_id))
    second_err = capsys.readouterr().err
    second_copy = [c for c in calls[first_count:] if c and c[0] == "scp"][-1][-1].split(":", 1)[1]

    assert first_rc == second_rc == jobs._EXIT_REMOTE_UNKNOWN
    assert first_copy != second_copy, "reused request id aliased one copy path"
    assert request_id in first_err and request_id in second_err
    assert first_copy in first_err and second_copy in second_err


def test_remote_script_stem_is_allowlisted_before_scp(
        jobs, monkeypatch, tmp_path):
    hostile_stem = "x$(touch marker); line\nquote'"
    script = tmp_path / (hostile_stem + ".sh")
    script.write_text("sleep 45\n", encoding="utf-8")
    calls = _fake_transport(jobs, monkeypatch)

    rc = jobs.cmd_run(_remote_args(script=str(script)))

    copies = [call for call in calls if call and call[0] == "scp"]
    assert len(copies) == 1, copies
    destination = copies[0][-1]
    target, remote_script = destination.split(":", 1)
    assert target == "somewhere"
    basename = pathlib.PurePosixPath(remote_script).name
    assert re.fullmatch(
        re.escape(jobs._REMOTE_SCRIPT_PREFIX)
        + r"[A-Za-z0-9._-]{1,24}-[0-9a-f]{32}\.sh",
        basename), basename
    assert not set(" \t\r\n'\"`$();") & set(basename), basename

    payloads = [
        call[-1] for call in calls
        if call and call[0] == "ssh"
        and jobs._REMOTE_STATUS_SENTINEL in call[-1]
    ]
    assert len(payloads) == 1, payloads
    tokens = _delegated_tokens(payloads[0])
    adopted = tokens[tokens.index("--adopt-script") + 1]
    assert adopted == remote_script
    assert rc == 0


def test_target_cleanup_quarantine_preserves_a_replacement_inode(jobs, tmp_path):
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = jobs.JOBS_DIR / ".bd-jobs-script-captured-race.sh"
    path.write_text("captured owner\n", encoding="utf-8")
    captured = jobs._path_identity(path)
    replacement = b"replacement owner\n"
    original_owner = _replace_path_with_distinct_inode(path, replacement)
    try:
        removed, note = jobs._cleanup_attempt_identity(path, captured)

        assert removed is False
        assert path.read_bytes() == replacement
        assert "identity" in note.lower() and str(path) in note
        assert list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*")) == []
    finally:
        original_owner.close()


def test_valid_target_identity_preserves_all_unrelated_probe_evidence(
        jobs, monkeypatch, tmp_path, capsys):
    script = tmp_path / "identity-evidence.sh"
    script.write_text("sleep 45\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "ssh":
            tokens = shlex.split(cmd[-1])
            if jobs._ATTEMPT_IDENTITY_FLAG in tokens:
                index = tokens.index(jobs._ATTEMPT_IDENTITY_FLAG)
                request_id, nonce, attempt = tokens[index + 1:index + 4]
                marker = "%s%s:%s:%s:PRESENT:1:2:%d\n" % (
                    jobs._REMOTE_IDENTITY_SENTINEL, request_id, nonce, attempt,
                    stat.S_IFREG)
                return subprocess.CompletedProcess(
                    cmd, 0, "identity stdout evidence\n" + marker,
                    "identity stderr evidence\n")
            if jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
                return subprocess.CompletedProcess(
                    cmd, 0, "job-id\n",
                    _status_record_for_payload(
                        jobs, cmd[-1], 0, disposition="ADOPTED"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(
        jobs, "_run_scp",
        lambda argv: (subprocess.CompletedProcess(argv, 0, "", ""),
                      True, ""))
    rc = jobs.cmd_run(_remote_args(
        script=str(script), request_id="identity-evidence"))
    out, err = capsys.readouterr()

    assert rc == 0 and out == "job-id\n", (rc, out, err)
    assert "identity stdout evidence" in err, err
    assert "identity stderr evidence" in err, err


def test_unexpected_authenticated_target_status_retains_script_as_unknown(
        jobs, monkeypatch, tmp_path, capsys):
    script = tmp_path / "status-137.sh"
    script.write_text("sleep 45\n", encoding="utf-8")

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            return (137, "", _status_record_for_payload(
                jobs, cmd[-1], 137, disposition="UNKNOWN"))
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(script=str(script), request_id="status-137"))
    err = capsys.readouterr().err
    copied = [c for c in calls if c and c[0] == "scp"][0][-1].split(":", 1)[1]
    removals = [c for c in calls if c and c[0] == "ssh"
                and c[-1].startswith("rm -f ")]

    assert rc == 137
    assert removals == []
    assert copied in err and "status-137" in err and "UNKNOWN" in err


def test_same_identity_collision_never_releases_unadopted_launch_resources(
        jobs, monkeypatch, tmp_path, capsys):
    marker = tmp_path / "must-never-run.marker"
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    adopted = jobs.JOBS_DIR / ".bd-jobs-script-new-attempt.sh"
    adopted.write_text("touch %s\nsleep 45\n" % marker, encoding="utf-8")
    recorded_logs = []
    real_open = jobs.open_job_log
    real_publish = jobs._LocalLaunch.publish
    prior = {}

    def capture_log(purpose):
        path, fd = real_open(purpose)
        recorded_logs.append(pathlib.Path(path))
        return path, fd

    def interleave_prior(self, cmd, origin):
        prior.update(jobs.register(
            self.pid, "prior owner", "sleep 60", request_id="prior-request"))
        prior["bytes"] = pathlib.Path(prior["_path"]).read_bytes()
        return real_publish(self, cmd, origin)

    monkeypatch.setattr(jobs, "open_job_log", capture_log)
    monkeypatch.setattr(jobs._LocalLaunch, "publish", interleave_prior)
    rc = jobs.cmd_run(_remote_args(
        host="local", purpose="new attempt", request_id="new-request",
        script=str(adopted), adopt_script=str(adopted),
        attempt_token="a" * 32, command=[]))
    out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REGISTRATION_FAILED, (rc, out, err)
    assert not marker.exists(), "mismatched resources were released"
    final = pathlib.Path(prior["_path"])
    assert final.read_bytes() == prior["bytes"], "preserved prior was rewritten"
    assert jobs.proc_starttime(prior["pid"]) is None, "gate child was not reaped"
    assert not adopted.exists() and recorded_logs and not recorded_logs[0].exists()
    assert out == "" and prior["id"] in err and str(final) in err
    assert str(adopted) in err and str(recorded_logs[0]) in err


def test_same_identity_collision_names_every_current_resource_cleanup_retains(
        jobs, monkeypatch, tmp_path, capsys):
    """A preserved prior cannot hide failed cleanup of the rejected attempt.

    Mutation design: claiming either identity-bound removal succeeded, omitting
    either retained path, or releasing after the resource mismatch loses a
    required observable below.
    """
    marker = tmp_path / "retained-resources-must-never-run.marker"
    jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    adopted = jobs.JOBS_DIR / ".bd-jobs-script-retained-attempt.sh"
    adopted.write_text("touch %s\nsleep 45\n" % marker, encoding="utf-8")
    recorded_logs = []
    real_open = jobs.open_job_log
    real_publish = jobs._LocalLaunch.publish
    prior = {}

    def capture_log(purpose):
        path, fd = real_open(purpose)
        recorded_logs.append(pathlib.Path(path))
        return path, fd

    def interleave_prior(self, cmd, origin):
        prior.update(jobs.register(
            self.pid, "prior retained owner", "sleep 60",
            request_id="prior-retained-request"))
        prior["bytes"] = pathlib.Path(prior["_path"]).read_bytes()
        return real_publish(self, cmd, origin)

    def retain_log(path, observed):
        assert observed is not None and pathlib.Path(path) == recorded_logs[0]
        return (False, "could not quarantine %s: injected log cleanup fault"
                % path, False)

    def retain_script(path, observed):
        assert observed is not None and pathlib.Path(path) == adopted
        return (False, "RETAINED attempt file %s: injected script cleanup fault"
                % path)

    monkeypatch.setattr(jobs, "open_job_log", capture_log)
    monkeypatch.setattr(jobs._LocalLaunch, "publish", interleave_prior)
    monkeypatch.setattr(jobs, "_unlink_owned_identity", retain_log)
    monkeypatch.setattr(jobs, "_cleanup_attempt_identity", retain_script)

    rc = jobs.cmd_run(_remote_args(
        host="local", purpose="retained new attempt", request_id="new-retained",
        script=str(adopted), adopt_script=str(adopted),
        attempt_token="b" * 32, command=[]))
    out, err = capsys.readouterr()

    final = pathlib.Path(prior["_path"])
    assert rc == jobs._EXIT_REGISTRATION_FAILED and out == "", (rc, out, err)
    assert not marker.exists() and jobs.proc_starttime(prior["pid"]) is None
    assert final.read_bytes() == prior["bytes"], "preserved prior was rewritten"
    assert adopted.exists() and recorded_logs[0].exists()
    for retained in (str(adopted), str(recorded_logs[0])):
        assert retained in err, "retained resource was unnamed: %s" % err
    assert prior["id"] in err and str(final) in err
    assert "injected log cleanup fault" in err
    assert "injected script cleanup fault" in err


def test_request_id_grammar_is_shared_by_direct_and_local_admission(
        jobs, sleeper, tmp_path, capsys):
    hostile = "safe\nFAKE LIVE ROW"
    marker = tmp_path / "must-never-run.marker"
    p = sleeper()

    jobs.JOBS_DIR = tmp_path / "direct-registry"
    direct = jobs.cmd_register(type("A", (), {
        "pid": p.pid, "purpose": "direct", "cmd": "sleep 60",
        "origin": None, "request_id": hostile})())
    direct_out, direct_err = capsys.readouterr()
    assert direct == jobs._EXIT_REGISTRATION_FAILED
    assert direct_out == "" and "REFUSED" in direct_err
    assert not jobs.JOBS_DIR.exists(), "invalid direct id acquired the registry"

    jobs.JOBS_DIR = tmp_path / "local-registry"
    local = jobs.cmd_run(_remote_args(
        host="local", purpose="local", request_id=hostile,
        command=_marker_command(marker)))
    local_out, local_err = capsys.readouterr()
    assert local == 2
    assert local_out == "" and "REFUSED" in local_err
    assert not jobs.JOBS_DIR.exists() and not marker.exists()
    assert "\nFAKE LIVE ROW\n" not in direct_err + local_err


@pytest.mark.parametrize(("bad_request", "case"), [
    ("", "empty"),
    ("bad\0request", "nul"),
    ("bad\x01request", "control"),
    ("café", "non-ascii"),
    ("A" * 129, "overlong"),
])
def test_reader_request_id_grammar_keeps_bad_evidence_and_reaps_valid_sibling(
        jobs, monkeypatch, capsys, bad_request, case):
    valid_proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    valid = jobs.register(
        valid_proc.pid, "valid reader sibling", "sleep 60",
        request_id="valid-reader-id")
    host = jobs.socket.gethostname()
    bad_pid = 999999
    bad = {
        "id": "%s-%d" % (host, bad_pid), "pid": bad_pid,
        "starttime": 1, "purpose": "bad request %s" % case,
        "cmd": "sleep 60", "host": host, "origin": host,
        "started_at": "2026-08-22T00:00:00Z", "log": None,
        "request_id": bad_request,
    }
    bad_final = jobs.JOBS_DIR / (bad["id"] + ".json")
    bad_final.write_text(json.dumps(bad), encoding="utf-8")
    before = bad_final.read_bytes()
    signaled = []
    real_killpg = jobs.os.killpg
    real_kill = jobs.os.kill

    def track_killpg(pid, sig):
        signaled.append(pid)
        return real_killpg(pid, sig)

    def track_kill(pid, sig):
        signaled.append(pid)
        return real_kill(pid, sig)

    monkeypatch.setattr(jobs.os, "killpg", track_killpg)
    monkeypatch.setattr(jobs.os, "kill", track_kill)
    try:
        list_rc = jobs.cmd_list(type("A", (), {})())
        list_out, list_err = capsys.readouterr()
        assert list_rc == jobs._EXIT_REGISTRY_UNKNOWN, (list_out, list_err)
        assert valid["id"] in list_out and str(bad_final) in list_err
        assert "request_id" in list_err and bad_final.read_bytes() == before

        reap_rc = jobs.cmd_reap(type("A", (), {"id": None})())
        reap_out, reap_err = capsys.readouterr()
        assert reap_rc == jobs._EXIT_REGISTRY_UNKNOWN, (reap_out, reap_err)
        assert "reaped %s" % valid["id"] in reap_out
        assert signaled == [valid_proc.pid], (
            "malformed request evidence authorized a signal: %r" % signaled)
        assert bad_final.read_bytes() == before and str(bad_final) in reap_err
    finally:
        if valid_proc.poll() is None:
            os.killpg(valid_proc.pid, 9)
        valid_proc.wait(timeout=10)


def test_reader_accepts_legacy_final_with_no_request_id(jobs, sleeper, capsys):
    proc = sleeper()
    entry = jobs.register(proc.pid, "legacy no request", "sleep 60")
    final = pathlib.Path(entry["_path"])

    assert "request_id" not in json.loads(final.read_text(encoding="utf-8"))
    rows = jobs.load_all()
    assert rows.malformed == [] and [row["id"] for row in rows] == [entry["id"]]

    rc = jobs.cmd_list(type("A", (), {})())
    out, err = capsys.readouterr()
    assert rc == 0 and entry["id"] in out and "request-id:" not in out
    assert err == ""


def test_direct_register_acquisition_oserrors_are_structured_before_publication(
        jobs, sleeper, tmp_path, capsys):
    p = sleeper()
    cases = ("mkdir", "lock-open", "stage-mkstemp")
    problems = []
    for case in cases:
        jobs.JOBS_DIR = tmp_path / ("registry-" + case)
        fired = []
        with pytest.MonkeyPatch.context() as mp:
            if case != "mkdir":
                jobs.JOBS_DIR.mkdir(parents=True)
            if case == "mkdir":
                real_mkdir = pathlib.Path.mkdir
                def fail_mkdir(path, *args, **kwargs):
                    if path == jobs.JOBS_DIR:
                        fired.append(case)
                        raise OSError(28, "injected mkdir ENOSPC")
                    return real_mkdir(path, *args, **kwargs)
                mp.setattr(pathlib.Path, "mkdir", fail_mkdir)
            elif case == "lock-open":
                real_open = jobs.os.open
                def fail_open(path, flags, *args, **kwargs):
                    if str(path) == str(jobs.JOBS_DIR):
                        fired.append(case)
                        raise OSError(24, "injected lock open EMFILE")
                    return real_open(path, flags, *args, **kwargs)
                mp.setattr(jobs.os, "open", fail_open)
            else:
                real_mkstemp = jobs.tempfile.mkstemp
                def fail_mkstemp(*args, **kwargs):
                    if kwargs.get("prefix") == jobs._ENTRY_TEMP_PREFIX:
                        fired.append(case)
                        raise OSError(28, "injected stage mkstemp ENOSPC")
                    return real_mkstemp(*args, **kwargs)
                mp.setattr(jobs.tempfile, "mkstemp", fail_mkstemp)
            try:
                rc = jobs.cmd_register(type("A", (), {
                    "pid": p.pid, "purpose": case, "cmd": "sleep 60",
                    "origin": None, "request_id": "safe-id"})())
            except BaseException as exc:
                rc = ("RAISED", type(exc).__name__, str(exc))
        out, err = capsys.readouterr()
        if fired != [case] or rc != jobs._EXIT_REGISTRATION_FAILED:
            problems.append("%s fired=%r rc=%r" % (case, fired, rc))
        if out or err.count("REFUSED") != 1 or "Traceback" in err:
            problems.append("%s output=%r/%r" % (case, out, err))
        if list(jobs.JOBS_DIR.glob("*.json")) or list(
                jobs.JOBS_DIR.glob(jobs._ENTRY_TEMP_PREFIX + "*")):
            problems.append("%s residue=%r" % (case, _residue(jobs.JOBS_DIR)))
    assert not problems, "\n".join(problems)


def test_lock_flock_primary_survives_close_uncertainty(
        jobs, sleeper, monkeypatch, capsys):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True)
    real_open = jobs.os.open
    real_close = jobs.os.close
    lock_fd = []
    closes = []

    def capture_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if str(path) == str(jobs.JOBS_DIR):
            lock_fd.append(fd)
        return fd

    def fail_flock(fd, operation):
        if lock_fd and fd == lock_fd[0]:
            raise OSError(5, "injected flock primary")
        return jobs.fcntl.flock(fd, operation)

    real_flock = jobs.fcntl.flock
    def scoped_flock(fd, operation):
        if lock_fd and fd == lock_fd[0]:
            raise OSError(5, "injected flock primary")
        return real_flock(fd, operation)

    def fail_close(fd):
        if lock_fd and fd == lock_fd[0]:
            closes.append(fd)
            real_close(fd)
            raise OSError(9, "injected lock close uncertainty")
        return real_close(fd)

    monkeypatch.setattr(jobs.os, "open", capture_open)
    monkeypatch.setattr(jobs.fcntl, "flock", scoped_flock)
    monkeypatch.setattr(jobs.os, "close", fail_close)
    rc = jobs.cmd_register(type("A", (), {
        "pid": p.pid, "purpose": "lock combined", "cmd": "sleep 60",
        "origin": None, "request_id": "safe-id"})())
    out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REGISTRATION_FAILED and out == ""
    assert closes == lock_fd and len(closes) == 1
    assert "flock primary" in err and "close" in err and "Traceback" not in err


def test_run_entropy_failures_refuse_before_any_effect(
        jobs, monkeypatch, tmp_path, capsys):
    marker = tmp_path / "must-never-run.marker"

    def entropy_failure():
        raise OSError(5, "injected entropy failure")

    monkeypatch.setattr(jobs, "_new_request_id", entropy_failure)
    try:
        local = jobs.cmd_run(_remote_args(
            host="local", purpose="request entropy",
            command=_marker_command(marker)))
    except BaseException as exc:
        local = ("RAISED", type(exc).__name__, str(exc))
    local_out, local_err = capsys.readouterr()
    assert local == 2 and local_out == "" and "REFUSED" in local_err
    assert not jobs.JOBS_DIR.exists() and not marker.exists()

    monkeypatch.setattr(jobs, "_new_request_id", lambda: "safe-generated")
    monkeypatch.setattr(jobs, "_new_status_nonce", entropy_failure)
    calls = _fake_transport(jobs, monkeypatch)
    try:
        remote = jobs.cmd_run(_remote_args(
            host="somewhere", purpose="status entropy", request_id="safe-id",
            command=["--", "sleep", "45"]))
    except BaseException as exc:
        remote = ("RAISED", type(exc).__name__, str(exc))
    remote_out, remote_err = capsys.readouterr()
    assert remote == 2 and remote_out == "" and "REFUSED" in remote_err
    assert calls == []

    monkeypatch.setattr(jobs, "_new_status_nonce", lambda: "0" * 32)
    attempt_entropy_calls = []

    def attempt_entropy_failure():
        attempt_entropy_calls.append("attempt")
        raise OSError(5, "injected attempt entropy failure")

    # Default raising=True is intentional: renaming/removing production's
    # generator must break this catcher rather than create a fake seam.
    monkeypatch.setattr(jobs, "_new_attempt_token", attempt_entropy_failure)
    script = tmp_path / "attempt-entropy.sh"
    script.write_text("sleep 45\n", encoding="utf-8")
    calls[:] = []
    attempt = jobs.cmd_run(_remote_args(
        host="somewhere", purpose="attempt entropy", request_id="safe-id",
        script=str(script)))
    attempt_out, attempt_err = capsys.readouterr()
    assert attempt == 2 and attempt_out == "" and "REFUSED" in attempt_err
    assert attempt_entropy_calls == ["attempt"], attempt_entropy_calls
    assert calls == []


def test_disposition_is_one_bound_record_immediately_before_final_status(jobs):
    request_id = "pair-request"
    nonce = "1" * 32
    attempt = "2" * 32
    disposition = "%s%s:%s:%s:NOT_ADOPTED\n" % (
        jobs._REMOTE_DISPOSITION_SENTINEL, request_id, nonce, attempt)
    status = "%s%s:%s:3\n" % (
        jobs._REMOTE_STATUS_SENTINEL, request_id, nonce)
    unrelated = "first diagnostic\nsecond diagnostic\n"

    parsed_status, before_status = jobs._parse_target_status(
        unrelated + disposition + status, request_id, nonce)
    parsed_disposition, remaining = jobs._parse_target_disposition(
        before_status, request_id, nonce, attempt)
    assert (parsed_status, parsed_disposition, remaining) == (
        3, "NOT_ADOPTED", unrelated)

    malformed = (
        unrelated + disposition + disposition + status,
        unrelated + disposition + "intervening diagnostic\n" + status,
        unrelated + disposition.replace(request_id, "wrong-request") + status,
        unrelated + disposition.replace(nonce, "0" * 32) + status,
        unrelated + disposition.replace(attempt, "3" * 32) + status,
    )
    for text in malformed:
        parsed_status, before_status = jobs._parse_target_status(
            text, request_id, nonce)
        parsed_disposition, raw = jobs._parse_target_disposition(
            before_status, request_id, nonce, attempt)
        assert parsed_status == 3 and parsed_disposition is None
        assert raw == before_status, "malformed/duplicate evidence was consumed"


def test_disposition_accepts_one_valid_member_despite_malformed_sibling(
        jobs, sleeper):
    proc = sleeper()
    attempt = str(jobs.JOBS_DIR / ".bd-jobs-script-member.sh")
    inherited = str(jobs.JOBS_DIR / "inherited-owned.log")
    request_id = "member-request"
    attempt_token = "a" * 32
    entry = jobs.register(
        proc.pid, "member adoption", "sleep 60", request_id=request_id,
        attempt_token=attempt_token, owned_paths=[attempt, inherited])
    malformed = jobs.JOBS_DIR / "malformed-sibling.json"
    malformed.write_text("{truncated", encoding="utf-8")

    assert attempt in entry["owned_paths"] and len(entry["owned_paths"]) == 2
    assert jobs._attempt_disposition(
        request_id, attempt_token, attempt) == "ADOPTED"


def test_disposition_zero_match_distinguishes_clean_from_malformed_registry(
        jobs, sleeper):
    proc = sleeper()
    path = str(jobs.JOBS_DIR / ".bd-jobs-script-unmatched.sh")
    jobs.register(proc.pid, "unmatched", "sleep 60",
                  request_id="other-request", attempt_token="b" * 32,
                  owned_paths=[path])

    assert jobs._attempt_disposition(
        "wanted-request", "c" * 32, path) == "NOT_ADOPTED"

    malformed = jobs.JOBS_DIR / "malformed-zero-match.json"
    malformed.write_text("[]", encoding="utf-8")
    assert jobs._attempt_disposition(
        "wanted-request", "c" * 32, path) == "UNKNOWN"


def test_disposition_multiple_valid_matches_remain_unknown(jobs, sleeper):
    first = sleeper()
    second = sleeper()
    path = str(jobs.JOBS_DIR / ".bd-jobs-script-duplicate-adoption.sh")
    for proc in (first, second):
        jobs.register(
            proc.pid, "duplicate adoption", "sleep 60",
            request_id="duplicate-request", attempt_token="f" * 32,
            owned_paths=[path])

    assert jobs._attempt_disposition(
        "duplicate-request", "f" * 32, path) == "UNKNOWN"


def test_stale_replacement_disposition_adopts_inherited_owned_siblings(
        jobs, sleeper):
    proc = sleeper()
    attempt = str(jobs.JOBS_DIR / ".bd-jobs-script-current.sh")
    inherited = str(jobs.JOBS_DIR / "inherited-prior.log")
    prior = jobs.register(
        proc.pid, "prior", "sleep 60", request_id="prior-request",
        attempt_token="d" * 32, owned_paths=[inherited])
    final = pathlib.Path(prior["_path"])
    stale = dict(prior, starttime=prior["starttime"] - 1)
    stale.pop("_path", None)
    final.write_text(json.dumps(stale), encoding="utf-8")

    current = jobs.register(
        proc.pid, "current", "sleep 60", request_id="current-request",
        attempt_token="e" * 32, owned_paths=[attempt])

    assert current["owned_paths"] == [attempt, inherited], current
    assert jobs._attempt_disposition(
        "current-request", "e" * 32, attempt) == "ADOPTED"


def test_status_fixture_requires_the_real_bound_disposition_helper_call(jobs):
    request_id = "payload-request"
    nonce = "1" * 32
    attempt = "2" * 32
    path = str(jobs.JOBS_DIR / ".bd-jobs-script-payload.sh")
    payload = jobs._remote_launch_command(
        "/opt/bd/bd-jobs", "payload", "launcher", request_id, nonce, [],
        attempt_token=attempt, adopt=path)
    after_rc = payload.split(_STATUS_TAIL, 1)[1]

    assert after_rc.count(jobs._ATTEMPT_DISPOSITION_FLAG) == 1, payload
    helper, status_tail = after_rc.split("; printf ", 1)
    helper_tokens = shlex.split(helper.removesuffix(" >&2").strip())
    assert helper_tokens == [
        "/opt/bd/bd-jobs", jobs._ATTEMPT_DISPOSITION_FLAG, request_id,
        nonce, attempt, path], helper_tokens
    assert jobs._REMOTE_STATUS_SENTINEL in status_tail

    without_helper = payload.replace(helper + "; ", "", 1)
    with pytest.raises(AssertionError):
        _status_record_for_payload(jobs, without_helper, 0)


def test_authenticated_2_and_3_cleanup_preserves_replacement_inodes(
        jobs, monkeypatch, tmp_path, capsys):
    for target_status in (2, 3):
        jobs.JOBS_DIR = tmp_path / ("target-%d" % target_status)
        script = tmp_path / ("race-%d.sh" % target_status)
        script.write_text("sleep 45\n", encoding="utf-8")
        calls = []
        copied = {"path": None, "replacement": None,
                  "original_owner": None}

        def fake_run(cmd, **kwargs):
            cmd = list(cmd)
            calls.append(cmd)
            if cmd[0] == "scp":
                path = pathlib.Path(cmd[-1].split(":", 1)[1])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(script.read_bytes())
                copied["path"] = path
                return subprocess.CompletedProcess(cmd, 0, "", "")
            tokens = shlex.split(cmd[-1]) if cmd[0] == "ssh" else []
            if jobs._ATTEMPT_IDENTITY_FLAG in tokens:
                index = tokens.index(jobs._ATTEMPT_IDENTITY_FLAG)
                request_id, nonce, attempt, path = tokens[index + 1:index + 5]
                observed = jobs._path_identity(path)
                marker = "%s%s:%s:%s:PRESENT:%d:%d:%d\n" % (
                    jobs._REMOTE_IDENTITY_SENTINEL, request_id, nonce, attempt,
                    observed[1], observed[2], observed[3])
                return subprocess.CompletedProcess(cmd, 0, marker, "")
            if jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
                path = copied["path"]
                replacement = "replacement for status %d\n" % target_status
                copied["replacement"] = replacement.encode()
                copied["original_owner"] = _replace_path_with_distinct_inode(
                    path, copied["replacement"])
                return subprocess.CompletedProcess(
                    cmd, target_status, "",
                    _status_record_for_payload(
                        jobs, cmd[-1], target_status,
                        disposition="NOT_ADOPTED"))
            if jobs._ATTEMPT_CLEANUP_FLAG in tokens:
                index = tokens.index(jobs._ATTEMPT_CLEANUP_FLAG)
                request_id, nonce, attempt, path = tokens[index + 1:index + 5]
                observed = ("present",) + tuple(
                    int(v) for v in tokens[index + 5:index + 8])
                removed, note = jobs._cleanup_attempt_identity(path, observed)
                marker = "%s%s:%s:%s:%s\n" % (
                    jobs._REMOTE_CLEANUP_SENTINEL, request_id, nonce, attempt,
                    "REMOVED" if removed else "RETAINED")
                return subprocess.CompletedProcess(cmd, 0, marker, note + "\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(jobs.subprocess, "run", fake_run)
                mp.setattr(
                    jobs, "_run_scp",
                    lambda argv: (fake_run(argv), True, ""))
                mp.setattr(jobs.subprocess, "Popen", lambda *a, **k: pytest.fail(
                    "remote cleanup race launched locally"))
                mp.setattr(jobs.socket, "gethostname", lambda: "launcher-host")
                rc = jobs.cmd_run(_remote_args(
                    script=str(script),
                    request_id="race-request-%d" % target_status))
        finally:
            if copied["original_owner"] is not None:
                copied["original_owner"].close()
        out, err = capsys.readouterr()

        assert rc == jobs._EXIT_REMOTE_UNKNOWN and out == "", (rc, out, err)
        assert copied["path"].read_bytes() == copied["replacement"]
        assert str(copied["path"]) in err and "RETAINED" in err
        assert any(jobs._ATTEMPT_CLEANUP_FLAG in c[-1] for c in calls
                   if c and c[0] == "ssh")
        assert not any(c[-1].startswith("rm -f ") for c in calls
                       if c and c[0] == "ssh")
