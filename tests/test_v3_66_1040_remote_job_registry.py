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
import contextlib
import errno
import inspect
import io
import json
import os
import pathlib
import re
import select
import shlex
import stat
import subprocess
import sys
import threading
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
    would do the same. MEASURED at v3.66.1207: under a mutated `_remote_self`,
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


def test_lifecycle_exit_status_table_is_exact_and_collision_free(jobs):
    """Every machine-facing lifecycle outcome owns one stable numeric code."""
    actual = {
        "REGISTRATION_FAILED": jobs._EXIT_REGISTRATION_FAILED,
        "REGISTRY_UNKNOWN": jobs._EXIT_REGISTRY_UNKNOWN,
        "PUBLISHED_NOT_DURABLE": jobs._EXIT_PUBLISHED_NOT_DURABLE,
        "REMOTE_UNKNOWN": jobs._EXIT_REMOTE_UNKNOWN,
        "CLOSE_UNKNOWN": jobs._EXIT_CLOSE_UNKNOWN,
        "RELEASE_PROVED_NOT_DELIVERED": (
            jobs._EXIT_RELEASE_PROVED_NOT_DELIVERED),
        "RELEASE_UNKNOWN": jobs._EXIT_RELEASE_UNKNOWN,
        "EXEC_FAILED": jobs._EXIT_EXEC_FAILED,
        "EXEC_HANDOFF_UNKNOWN": jobs._EXIT_EXEC_HANDOFF_UNKNOWN,
        "GATE_ABANDONED": jobs._EXIT_GATE_ABANDONED,
    }
    assert actual == {
        "REGISTRATION_FAILED": 3,
        "REGISTRY_UNKNOWN": 4,
        "PUBLISHED_NOT_DURABLE": 5,
        "REMOTE_UNKNOWN": 6,
        "CLOSE_UNKNOWN": 7,
        "RELEASE_PROVED_NOT_DELIVERED": 8,
        "RELEASE_UNKNOWN": 9,
        "EXEC_FAILED": 10,
        "EXEC_HANDOFF_UNKNOWN": 11,
        "GATE_ABANDONED": 75,
    }
    assert len(set(actual.values())) == len(actual)
    assert set(actual.values()).isdisjoint({0, 1, 2})


def test_the_selftest_passes_and_says_so(tmp_path):
    """An exit code with no verdict behind it is the shape this repo keeps
    finding, which is why test_toolchain_534 requires the words."""
    r = subprocess.run([sys.executable, str(_TOOL), "--selftest"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELFTEST PASS" in r.stdout + r.stderr


def test_selftest_fails_when_forget_cleanup_is_incomplete(
        jobs, monkeypatch, capsys):
    original_dir = jobs.JOBS_DIR
    monkeypatch.setattr(
        jobs, "_forget_or_retain",
        lambda entry: {
            "cleanup_complete": False,
            "notes": ["injected selftest cleanup incomplete"],
        })
    try:
        rc = jobs.cmd_selftest(None)
        out, err = capsys.readouterr()
    finally:
        jobs.JOBS_DIR = original_dir

    assert rc == 1
    assert "SELFTEST PASS" not in out + err
    assert "SELFTEST FAIL" in err
    assert "injected selftest cleanup incomplete" in err


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


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["/repo/venv/bin/python", "-m", "pytest", "tests/"], True),
        (["/repo/venv/bin/python", "-mpytest", "tests/"], True),
        (["/repo/venv/bin/pytest", "tests/"], True),
        (["/repo/venv/bin/py.test", "tests/"], True),
        (["/repo/venv/bin/python", "/repo/venv/bin/pytest", "tests/"], True),
        (["/repo/venv/bin/python", "runner.py", "-m", "pytest"], False),
        (["bash", "-c", "python -m pytest tests/"], False),
        (["/repo/venv/bin/python", "-c", "import pytest"], False),
    ],
)
def test_orphan_classifier_recognises_process_argv_not_ambient_text(
        jobs, argv, expected):
    """The classifier owns executable argv, not a substring in ``ps args``."""
    classifier = getattr(jobs, "_is_pytest_argv", None)
    assert callable(classifier), (
        "bd-jobs has no exact-argv pytest classifier"
    )
    assert classifier(argv) is expected


def test_orphans_treats_a_live_registered_ancestor_as_the_child_owner(
        jobs, sleeper, monkeypatch, capsys):
    """A registered runner owns its live descendants, but a stale PID does not."""
    owner = sleeper()
    intermediary = sleeper()
    child = sleeper()
    entry = jobs.register(owner.pid, "owned runner", "runner.sh")
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    argv_reads = []

    def proc_argv(pid):
        argv_reads.append(pid)
        if pid == child.pid:
            return ["/repo/venv/bin/python", "-m", "pytest", "tests/"]
        return ["/bin/sleep", "60"]

    monkeypatch.setattr(jobs, "proc_argv", proc_argv, raising=False)
    monkeypatch.setattr(
        jobs.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0],
            0,
            "%d 1 bash bash runner.sh\n"
            "%d %d timeout timeout 240 bd-run\n"
            "%d %d python3 python3 -m pytest tests/\n"
            % (owner.pid, intermediary.pid, owner.pid,
               child.pid, intermediary.pid),
            "",
        ),
    )

    rc = jobs.cmd_orphans(type("A", (), {})())
    out, err = capsys.readouterr()

    assert argv_reads == [child.pid], "the planted pytest candidate was not classified"
    assert rc == 0 and err == ""
    assert "0 unregistered pytest process(es)" in out

    stale = dict(entry, starttime=entry["starttime"] + 1)
    stale.pop("_path", None)
    final.write_text(json.dumps(stale), encoding="utf-8")
    argv_reads.clear()

    rc = jobs.cmd_orphans(type("A", (), {})())
    out, err = capsys.readouterr()

    assert argv_reads == [child.pid]
    assert rc == 1 and err == ""
    assert "ORPHAN pid=%-7s" % child.pid in out


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
    monkeypatch.setattr(  # v3.66.1223: same seam, now via the deadline funnel
        jobs, "_run_remote",
        lambda argv, deadline, **kw: (fake_run(argv), True, ""))
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

    RE-ANCHORED at v3.66.1207, and the property is unchanged. The local launch
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
    #    Three descriptors: release, READY, and gate-to-shell exec status.
    gate_argv = jobs._gate_argv(9, 11, 13, user_argv)
    assert gate_argv[-3:] == user_argv, (
        "the wrapper did not carry the user argv verbatim at the end: %r"
        % (gate_argv,))
    assert gate_argv[-4] == "--", (
        "nothing separates the wrapper's own options from the user's argv, so "
        "a command starting with a dash would be parsed as ours: %r"
        % (gate_argv,))
    assert gate_argv[2] == jobs._GATE_FLAG and gate_argv[3:6] == ["9", "11", "13"], (
        "the wrapper argv lost its mode or one of its three descriptors: %r"
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
    transport_calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        identity = _identity_record_for_command(jobs, cmd)
        if identity is not None:
            return subprocess.CompletedProcess(cmd, 0, identity, "")
        # A REAL TARGET AUTHENTICATES ITS ANSWER (v3.66.1207): the payload ends
        # in a status sentinel on stderr, and a launcher that accepted a silent
        # rc 0 could not tell a target result from a dropped connection.
        err = ""
        if cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            err = _status_record_for_payload(
                jobs, cmd[-1], 0, disposition="ADOPTED")
        return subprocess.CompletedProcess(cmd, 0, "", err)

    def fake_remote(argv, deadline, **options):
        transport_calls.append((list(argv), deadline, dict(options)))
        return fake_run(argv), True, ""

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(  # v3.66.1223: same seam, now via the deadline funnel
        jobs, "_run_remote", fake_remote)
    rc = jobs.cmd_run(type("A", (), {
        "host": "somewhere", "purpose": "sweep", "script": str(script),
        "command": []})())
    assert rc == 0, calls

    scp = [c for c in calls if c[0] == "scp"]
    assert len(scp) == 1, "the script copy seam fired %d times: %s" % (
        len(scp), calls)
    assert str(script) in scp[0], scp[0]
    assert calls.index(scp[0]) < len(calls) - 1, (
        "the copy was not done before the launch -- a script that did not "
        "arrive must be a refusal, not a job running the previous copy of "
        "itself")

    launched = " ".join(calls[-1])
    copied = scp[0][-1].split(":", 1)[1]
    # RE-ANCHORED at v3.66.1207: the copy now lands in the target's registry
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

    # ROW 235 BUDGET DECISION, measured at the production call seam. The four
    # SSH stages share one deadline object, while SCP keeps the exact independent
    # 60-second allowance it had before the two owners were folded together.
    copy_transport = [item for item in transport_calls if item[0][0] == "scp"]
    ssh_transports = [item for item in transport_calls if item[0][0] == "ssh"]
    assert len(copy_transport) == 1 and ssh_transports, transport_calls
    _, copy_deadline, copy_options = copy_transport[0]
    assert copy_deadline is None, (
        "scp silently joined the shared SSH deadline: %r" % copy_deadline)
    assert copy_options == {
        "phase": "scp",
        "retained": scp[0][-1],
        "fixed_timeout": jobs._SCP_TRANSFER_TIMEOUT_S,
    }, copy_options
    assert jobs._SCP_TRANSFER_TIMEOUT_S == 60.0
    shared_deadline = ssh_transports[0][1]
    assert shared_deadline is not None
    assert all(item[1] is shared_deadline for item in ssh_transports), (
        "SSH stages did not share one deadline: %r" % (ssh_transports,))
    assert all(item[2].get("fixed_timeout") is None
               for item in ssh_transports), ssh_transports


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

    def fake_remote(argv, deadline, *, phase, retained="", fixed_timeout=None):
        # The sole remote funnel owns a real process group and therefore uses
        # Popen. Stub it here so the Popen ban below stays live and still
        # catches a genuine stray LOCAL launch.
        #
        # SCP keeps its separately selected outcome; SSH delegates to fake_run
        # so the existing assertions over `calls` remain unchanged.
        return (fake_scp(argv) if argv and argv[0] == "scp"
                else (fake_run(argv), True, ""))

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(jobs, "_run_remote", fake_remote)
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


def _owned_popen_transport_funnels(source):
    """Return the complete Popen denominator and its owned transport subset.

    The subset is structural: a function both acquires a new-session Popen and
    communicates with a child.  That distinguishes an ssh/scp transport owner
    from the local release-gate spawn without depending on helper names,
    comments, error text, or which executable happens to occupy ``argv[0]``.
    """
    tree = ast.parse(source)
    popen_sites = []
    funnels = []

    class DirectCalls(ast.NodeVisitor):
        """Visit one owner without charging nested owners twice."""

        def __init__(self, owner):
            self.owner = owner
            self.calls = []

        def visit_FunctionDef(self, node):
            if node is self.owner:
                self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            return

        def visit_Call(self, node):
            self.calls.append(node)
            self.generic_visit(node)

    for owner in (node for node in ast.walk(tree)
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        visitor = DirectCalls(owner)
        visitor.visit(owner)
        calls = visitor.calls
        popens = [
            call for call in calls
            if (isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "subprocess"
                and call.func.attr == "Popen")
        ]
        popen_sites.extend((owner.name, call.lineno) for call in popens)
        starts_owned_group = any(
            any(keyword.arg == "start_new_session"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in call.keywords)
            for call in popens)
        communicates = any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "communicate"
            for call in calls)
        if starts_owned_group and communicates:
            funnels.append((owner.name, tuple(call.lineno for call in popens)))
    return tuple(sorted(popen_sites)), tuple(sorted(funnels))


def test_ssh_and_scp_have_one_owned_popen_transport_funnel():
    """A second owner can silently diverge acquisition and settlement again."""
    source = _TOOL.read_text(encoding="utf-8")
    popen_sites, funnels = _owned_popen_transport_funnels(source)

    assert popen_sites, "bd-jobs has no Popen denominator; the gate saw nothing"
    assert funnels, (
        "bd-jobs has no owned transport funnel; the classifier saw %r"
        % (popen_sites,))
    assert all(lines for _, lines in funnels), funnels

    # NEGATIVE CONTROL: add a differently named second owner in memory.  The
    # parser must count one extra Popen and identify that function as a funnel;
    # otherwise the final one-funnel verdict could be a classifier no-op.
    negative = source + """
def _synthetic_second_transport(command):
    child = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True)
    return child.communicate(timeout=1.0)
"""
    negative_sites, negative_funnels = _owned_popen_transport_funnels(negative)
    assert len(negative_sites) == len(popen_sites) + 1, (
        popen_sites, negative_sites)
    assert [name for name, _ in negative_funnels] == sorted(
        [name for name, _ in funnels] + ["_synthetic_second_transport"]), (
            negative_funnels)

    assert [name for name, _ in funnels] == ["_run_remote"], (
        "owned ssh/scp transport funnels must be exactly _run_remote; found %r"
        % (funnels,))


def test_transport_transform_control_imports_without_judging_behaviour():
    """Mutation control: valid source is importable, with no transport verdict."""
    imported = _load(name="bd_jobs_transport_transform_control")
    assert imported.__name__ == "bd_jobs_transport_transform_control"


def _run_scp_through_remote_funnel(jobs, argv):
    return jobs._run_remote(
        argv, None, phase="scp", retained=argv[-1] if argv else "UNKNOWN",
        fixed_timeout=jobs._SCP_TRANSFER_TIMEOUT_S)


def _stub_ssh_but_run_real_scp(jobs, fake_ssh):
    real_run_remote = jobs._run_remote

    def run(argv, deadline, **kwargs):
        if argv and argv[0] == "scp":
            return real_run_remote(argv, deadline, **kwargs)
        return fake_ssh(argv), True, ""

    return run


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

    result, cleanup_complete, cleanup_note = _run_scp_through_remote_funnel(
        jobs, argv)

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
    monkeypatch.setattr(  # v3.66.1223: same seam, now via the deadline funnel
        jobs, "_run_remote", _stub_ssh_but_run_real_scp(jobs, fake_run))
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
    monkeypatch.setattr(  # v3.66.1223: same seam, now via the deadline funnel
        jobs, "_run_remote", _stub_ssh_but_run_real_scp(jobs, fake_run))
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
        _run_scp_through_remote_funnel(
            jobs, ["scp", "source", "target:/partial"])

    assert events == [("communicate", 60.0), ("cleanup", 616161)]
    assert excinfo.value.args == ("operator cancelled copy",)
    notes = "\n".join(getattr(excinfo.value, "__notes__", []))
    assert "cleanup is UNKNOWN" in notes
    assert "still exists" in notes


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
    monkeypatch.setattr(  # v3.66.1223: same seam, now via the deadline funnel
        jobs, "_run_remote", _stub_ssh_but_run_real_scp(jobs, fake_run))
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
    message = "\n".join(getattr(raised, "__notes__", []))
    after_copy = events[scp_index + 1:]

    assert raised is primary
    assert raised.args == ("operator cancelled copy",)
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
    message = "\n".join(getattr(raised, "__notes__", []))
    after_copy = events[scp_index + 1:]

    assert raised is primary
    assert raised.args == ("operator cancelled copy",)
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
    result, cleanup_complete, cleanup_note = _run_scp_through_remote_funnel(
        jobs, ["scp", "source", "target:/partial"])

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
        _run_scp_through_remote_funnel(
            jobs, ["scp", "source", "target:/partial"])

    assert excinfo.value.args == ("primary cancellation",)
    notes = "\n".join(getattr(excinfo.value, "__notes__", []))
    assert "cleanup is UNKNOWN" in notes
    assert "cleanup transport broke" in notes


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

    result, cleanup_complete, cleanup_note = _run_scp_through_remote_funnel(
        jobs, ["scp", "source", "target:/never-created"])

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
    monkeypatch.setattr(jobs, "_run_remote",
                        lambda argv, deadline, **kw: (fake_run(argv), True, ""))
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
    monkeypatch.setattr(jobs, "_run_remote",
                        lambda argv, deadline, **kw: (fake_run(argv), True, ""))
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
        # the v3.66.1207 note in the --script node above.
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
    monkeypatch.setattr(  # v3.66.1223: same seam, now via the deadline funnel
        jobs, "_run_remote",
        lambda argv, deadline, **kw: (fake_run(argv), True, ""))
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

    TIGHTENED at v3.66.1207 after the audit: counting two `fsync` calls does
    not prove durability. TWO FILE FSYNCS WOULD SATISFY A COUNT, and neither
    would make the RENAME survive a power loss -- the name lives in the
    parent directory, so the directory is a separate object that must itself be
    fsynced, AFTER the rename. Each descriptor is therefore classified with
    `os.fstat`, and the whole event ORDER is asserted:
    file fsync -> identity recheck -> replace -> directory fsync.
    """
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    assert list(jobs.JOBS_DIR.glob("*.json")) == [], (
        "precondition: the registry directory must start empty")
    assert jobs.proc_starttime(p.pid) is not None, (
        "precondition: the process to register must be alive and identifiable")

    real_fsync = os.fsync
    real_rename = jobs._RegistryTxn.rename_name
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

    def counting_rename(txn, src, dst):
        seen["replace"] += 1
        seen["events"].append("replace")
        snapshot("replace")
        assert src.startswith(jobs._ENTRY_TEMP_PREFIX), src
        assert dst.endswith(".json"), dst
        assert txn.display_root == jobs.JOBS_DIR
        return real_rename(txn, src, dst)

    def counting_starttime(pid):
        seen["events"].append("starttime")
        return real_starttime(pid)

    monkeypatch.setattr(jobs.os, "fsync", counting_fsync)
    monkeypatch.setattr(jobs._RegistryTxn, "rename_name", counting_rename)
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
        "expected exactly one atomic descriptor-relative rename publication, "
        "saw %d -- the "
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
            # a TypeError when register() grows a keyword (v3.66.1207 added
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


# ── v3.66.1207: the launch is ONE transaction, and it is audited as one ──────
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


def _pipe_bytes_through_eof(fd, timeout=1.0, maximum=8192):
    deadline = time.monotonic() + timeout
    got = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "pipe did not reach terminal EOF: %r" % bytes(got)
        readable, _, _ = select.select([fd], [], [], remaining)
        assert readable, "pipe did not become readable before its deadline"
        chunk = os.read(fd, maximum + 1)
        if chunk == b"":
            return bytes(got)
        got.extend(chunk)
        assert len(got) <= maximum


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


def test_local_launch_release_refuses_when_entry_is_absent(jobs):
    """No call-site reorder may turn release into unregistered execution."""
    launch = jobs._LocalLaunch("unpublished")
    launch.open_channels()
    release_probe = os.dup(launch.release_r)
    try:
        with pytest.raises(jobs._LaunchAborted) as excinfo:
            launch.release()

        assert excinfo.value.status == jobs._EXIT_REGISTRATION_FAILED
        assert "no durable registry entry exists" in str(excinfo.value)
        assert launch.released is False
        assert os.read(release_probe, 2) == b"", (
            "release wrote a byte before a durable registry entry existed")
    finally:
        launch.close_idle()
        os.close(release_probe)


def test_cmd_run_completes_durable_publication_before_releasing_the_gate(
        jobs, monkeypatch, tmp_path, capsys):
    """N54 catcher: release begins only after real publish returns."""
    marker = tmp_path / "released-after-publication.marker"
    events = []
    launched = []
    argvs = []
    entry = None
    real_publish = jobs._LocalLaunch.publish
    real_release = jobs._LocalLaunch.release

    def traced_publish(self, cmd, origin):
        events.append(("publish-enter", None))
        published = real_publish(self, cmd, origin)
        events.append(("publish-complete", published["id"]))
        return published

    def traced_release(self):
        entry_id = None if self.entry is None else self.entry["id"]
        events.append(("release-enter", entry_id))
        return real_release(self)

    monkeypatch.setattr(jobs._LocalLaunch, "publish", traced_publish)
    monkeypatch.setattr(jobs._LocalLaunch, "release", traced_release)
    _watch_popen(jobs, monkeypatch, launched, argvs)

    try:
        rc = jobs.cmd_run(_run_args(
            "publication-order", _marker_command(marker)))
        out, err = capsys.readouterr()

        assert len(launched) == 1 and len(argvs) == 1, (
            "the real launch path did not execute exactly once: %r" % argvs)
        assert rc == 0, "launch failed: rc=%r stderr=%r" % (rc, err)
        entries = jobs.load_all()
        assert len(entries) == 1, entries
        entry = entries[0]
        assert events == [
            ("publish-enter", None),
            ("publish-complete", entry["id"]),
            ("release-enter", entry["id"]),
        ], (
            "release did not follow completion of the same durable "
            "publication: %r" % (events,))
        assert _wait_until(marker.exists), (
            "positive control: the command never ran after its durable "
            "publication")
        assert out.strip().splitlines() == [entry["id"]], out
    finally:
        for pid in launched:
            _reap_process(pid)
        if entry is not None:
            jobs.forget(entry)


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
    status_r, status_w = os.pipe()
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
            str(release_r), str(ready_w), str(status_w), "--",
            "echo", "released"])
        out, err = capsys.readouterr()
        ready = os.read(ready_r, 2)

        assert fired == [release_r], (
            "the post-release descriptor close seam did not fire once: %r"
            % fired)
        assert ready == jobs._READY_BYTE, (
            "the real gate never emitted the exact READY byte: %r" % ready)
        assert os.read(status_r, 1) == jobs._EXEC_REGISTERED_CHILD_BYTE
        assert execs == [], (
            "an uncertain descriptor was inherited into user work: %r" % execs)
        assert rc == jobs._EXIT_GATE_ABANDONED and out == ""
        assert err == (
            "gate: release was received, but its descriptor close is UNKNOWN "
            "([Errno 5] injected release-fd close EIO); the user command did "
            "not run\n")
    finally:
        for fd in (ready_r, status_r, status_w):
            try:
                real_close(fd)
            except OSError:
                pass


def test_public_run_accepts_only_the_exact_post_release_child_outcome(
        tmp_path, monkeypatch, capsys):
    """A registered gate close fault is asynchronous, but never anonymous.

    Drive the public parent and a fresh private gate process.  The child really
    consumes RELEASE, really closes its descriptor, then reports the injected
    close uncertainty through the terminal protocol before exiting 75.
    """
    copy, registry = _private_tool(tmp_path / "private")
    source = copy.read_text(encoding="utf-8")
    anchor = """    try:\n        os.close(read_fd)\n    except OSError as exc:\n        release_close_error = exc\n"""
    replacement = """    try:\n        os.close(read_fd)\n        raise OSError(5, \"injected post-release close uncertainty\")\n    except OSError as exc:\n        release_close_error = exc\n"""
    assert source.count(anchor) == 1
    copy.write_text(source.replace(anchor, replacement), encoding="utf-8")
    jobs = _load(name="bd_jobs_post_release_outcome", path=copy)
    marker = tmp_path / "user-command-must-not-run"
    children = []
    real_popen = jobs.subprocess.Popen

    def retained_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(jobs.subprocess, "Popen", retained_popen)
    entry = None
    try:
        rc = jobs.cmd_run(_run_args(
            "post-release child outcome", _marker_command(marker)))
        out, err = capsys.readouterr()
        entries = jobs.load_all()

        assert len(entries) == 1, entries
        entry = entries[0]
        assert len(children) == 1 and children[0].pid == entry["pid"]
        assert rc == 0, (rc, out, err)
        assert out.strip().splitlines() == [entry["id"]], out
        assert children[0].returncode == jobs._EXIT_GATE_ABANDONED, (
            "the parent accepted the child receipt without collecting its "
            "exact reserved status")
        assert children[0].wait(timeout=10) == jobs._EXIT_GATE_ABANDONED
        assert "post-release close uncertainty" in pathlib.Path(
            entry["log"]).read_text(encoding="utf-8")
        assert not marker.exists(), "the child outcome executed user work"
        assert registry == jobs.JOBS_DIR
    finally:
        if entry is not None:
            jobs.forget(entry)


@pytest.mark.parametrize(("mode", "child_status"), [
    pytest.param("bare", 75, id="bare-75"),
    pytest.param("wrong", 74, id="receipt-wrong-74"),
    pytest.param("unsettled", None, id="receipt-unsettled"),
])
def test_public_run_refuses_unproved_registered_child_outcomes(
        jobs, monkeypatch, tmp_path, capsys, mode, child_status):
    """Neither status 75 nor receipt C is sufficient without the other."""
    children = []
    real_popen = jobs.subprocess.Popen

    def gate_argv(release_r, ready_w, exec_w, _user_argv):
        program = (
            "import os,sys,time\n"
            "release_r,ready_w,exec_w=map(int,sys.argv[1:4])\n"
            "os.write(ready_w,b'R'); os.close(ready_w)\n"
            "assert os.read(release_r,1)==b'G'; os.close(release_r)\n"
            "mode=sys.argv[4]\n"
            "if mode!='bare': os.write(exec_w,b'C')\n"
            "os.close(exec_w)\n"
            "if mode=='unsettled': time.sleep(30)\n"
            "raise SystemExit(74 if mode=='wrong' else 75)\n"
        )
        return [sys.executable, "-c", program, str(release_r),
                str(ready_w), str(exec_w), mode]

    def retained_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(jobs, "_gate_argv", gate_argv)
    monkeypatch.setattr(jobs.subprocess, "Popen", retained_popen)
    monkeypatch.setattr(jobs, "_EXEC_HANDOFF_TIMEOUT_S", 0.4)
    marker = tmp_path / (mode + "-must-not-run")
    entry = None
    started = time.monotonic()
    try:
        rc = jobs.cmd_run(_run_args(mode, _marker_command(marker)))
        elapsed = time.monotonic() - started
        out, err = capsys.readouterr()
        entries = jobs.load_all()

        assert len(entries) == 1 and len(children) == 1
        entry = entries[0]
        assert rc == jobs._EXIT_EXEC_HANDOFF_UNKNOWN, (mode, rc, out, err)
        assert out.strip().splitlines()[-1] == entry["id"]
        assert "EXEC_HANDOFF_UNKNOWN" in err
        assert elapsed < 5.0 and not marker.exists()
        if child_status is None:
            assert children[0].poll() is None, "the unsettled child was invented"
        else:
            assert children[0].wait(timeout=10) == child_status
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
        if entry is not None:
            jobs.forget(entry)


def test_real_gate_child_wait_status_is_75_and_user_command_never_execs(
        jobs, tmp_path):
    """The child process, not an in-process helper call, owns status 75."""
    marker = tmp_path / "gate-status-75-must-not-exist"
    release_r, release_w = os.pipe()
    ready_r, ready_w = os.pipe()
    status_r, status_w = os.pipe()
    proc = None
    parent_fds = [release_r, release_w, ready_r, ready_w, status_r, status_w]
    try:
        proc = subprocess.Popen([
            sys.executable, str(pathlib.Path(jobs.__file__).resolve()),
            jobs._GATE_FLAG, str(release_r), str(ready_w), str(status_w), "--",
            sys.executable, "-c",
            "from pathlib import Path; Path(%r).write_text('ran')" % str(marker),
        ], pass_fds=(release_r, ready_w, status_w),
           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for fd in (release_r, ready_w, status_w):
            os.close(fd)
            parent_fds.remove(fd)

        assert _pipe_bytes_through_eof(ready_r) == jobs._READY_BYTE
        os.close(ready_r)
        parent_fds.remove(ready_r)
        os.close(release_w)
        parent_fds.remove(release_w)
        out, err = proc.communicate(timeout=10)
        assert _pipe_bytes_through_eof(status_r) == b""
        os.close(status_r)
        parent_fds.remove(status_r)

        assert proc.returncode == 75, (proc.returncode, out, err)
        assert out == "" and "never released" in err and "nothing ran" in err
        assert not marker.exists(), "the status-75 child executed user work"
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        for fd in parent_fds:
            try:
                os.close(fd)
            except OSError:
                pass


def test_gate_execvp_failure_emits_one_complete_parent_status_record(
        jobs, monkeypatch, capsys):
    """A failed gate-to-shell handoff is named; it is never false success."""
    release_r, release_w = os.pipe()
    ready_r, ready_w = os.pipe()
    status_r, status_w = os.pipe()
    real_close = os.close
    os.set_inheritable(status_w, True)
    os.write(release_w, jobs._RELEASE_BYTE)
    real_close(release_w)
    calls = []

    def missing_shell(file, argv):
        calls.append((file, list(argv), os.get_inheritable(status_w)))
        raise FileNotFoundError(2, "injected configured shell missing", file)

    monkeypatch.setattr(jobs.os, "execvp", missing_shell)
    try:
        rc = jobs._gate_main([
            str(release_r), str(ready_w), str(status_w), "--",
            "/definitely/missing/configured-shell", "-c", "true",
        ])
        record = _pipe_bytes_through_eof(status_r)
        ready = _pipe_bytes_through_eof(ready_r)
        out, err = capsys.readouterr()

        assert calls == [(
            "/definitely/missing/configured-shell",
            ["/definitely/missing/configured-shell", "-c", "true"],
            False,
        )]
        assert ready == jobs._READY_BYTE
        assert rc == jobs._EXIT_GATE_ABANDONED and out == ""
        assert record == b'EF{"errno":2,"kind":"EXEC_FAILED","stage":"execvp"}\n'
        assert "configured shell could not be executed" in err
        assert "command did not run" in err
        assert "Traceback" not in err
    finally:
        for fd in (release_r, ready_r, ready_w, status_r, status_w):
            try:
                real_close(fd)
            except OSError:
                pass


@pytest.mark.parametrize(("payload", "expected_status", "phrase"), [
    (b"E", 0, "configured shell handoff"),
    (b"C", 11, "UNKNOWN"),
    (b'EF{"errno":2,"kind":"EXEC_FAILED","stage":"execvp"}\n',
     10, "EXEC_FAILED"),
    (b"", 11, "UNKNOWN"),
    (b'{"errno":2,"kind":"EXEC_FAILED","stage":"execvp"}\n',
     11, "UNKNOWN"),
    (b'F{"errno":2,"kind":"EXEC_FAILED","stage":"execvp"}\n',
     11, "UNKNOWN"),
    (b'E{"errno":2,"kind":"EXEC_FAILED","stage":"execvp"}\n',
     11, "UNKNOWN"),
])
def test_exec_status_reader_accepts_only_attempt_then_terminal_eof(
        jobs, payload, expected_status, phrase):
    status_r, status_w = os.pipe()
    launch = jobs._LocalLaunch("exec-handoff")
    launch.exec_r = status_r
    if payload:
        os.write(status_w, payload)
    os.close(status_w)
    try:
        reader = getattr(launch, "await_exec_handoff", None)
        assert callable(reader), "launcher has no exec-status reconciliation"
        status, detail = reader(timeout=0.5)
        assert status == expected_status, (payload, status, detail)
        assert phrase.lower() in detail.lower()
        assert launch.exec_r is None
    finally:
        launch.close_idle()


@pytest.mark.parametrize(("child_result", "expected_status", "phrase"), [
    (75, 0, "registered child outcome"),
    (74, 11, "expected 75"),
    ("timeout", 11, "did not settle"),
])
def test_registered_child_receipt_requires_its_exact_reserved_exit(
        jobs, child_result, expected_status, phrase):
    class Child:
        def wait(self, timeout):
            assert 0 <= timeout <= 0.5
            if child_result == "timeout":
                raise subprocess.TimeoutExpired("private gate", timeout)
            return child_result

    status_r, status_w = os.pipe()
    launch = jobs._LocalLaunch("registered-child-outcome")
    launch.exec_r = status_r
    launch.proc = Child()
    os.write(status_w, jobs._EXEC_REGISTERED_CHILD_BYTE)
    os.close(status_w)
    try:
        status, detail = launch.await_exec_handoff(timeout=0.5)
        assert status == expected_status, (child_result, status, detail)
        assert phrase.lower() in detail.lower()
    finally:
        launch.proc = None
        launch.close_idle()


def test_public_run_missing_configured_shell_is_exec_failed_with_exact_id(
        jobs, monkeypatch, capsys):
    """The parent must consume the gate's failure, not return release success."""
    missing = "/definitely/missing/configured-shell"
    monkeypatch.setattr(jobs, "_user_argv", lambda cmd: [missing, "-c", cmd])
    entry = None
    try:
        rc = jobs.cmd_run(_run_args(
            "missing configured shell", ["--", "printf", "never-ran"]))
        out, err = capsys.readouterr()
        entries = jobs.load_all()

        assert len(entries) == 1, entries
        entry = entries[0]
        assert rc == jobs._EXIT_EXEC_FAILED
        assert out.strip().splitlines()[-1] == entry["id"]
        assert "EXEC_FAILED" in err
        assert "Traceback" not in err
        assert pathlib.Path(entry["_path"]).exists()
        assert pathlib.Path(entry["log"]).exists()
        log = pathlib.Path(entry["log"]).read_text(encoding="utf-8")
        assert "configured shell could not be executed" in log
        assert "command did not run" in log
    finally:
        if entry is not None:
            jobs.forget(entry)


def test_failed_exec_failure_record_write_times_out_unknown_not_success(
        jobs, monkeypatch, capsys):
    release_r, release_w = os.pipe()
    ready_r, ready_w = os.pipe()
    status_r, status_w = os.pipe()
    real_write = os.write
    real_close = os.close
    status_writes = []
    exec_calls = []
    gate_results = []
    assert real_write(release_w, jobs._RELEASE_BYTE) == 1
    real_close(release_w)

    def fail_second_status_write(fd, data):
        if fd == status_w:
            status_writes.append(bytes(data))
            if len(status_writes) == 2:
                raise OSError(errno.EIO, "injected exec failure-frame EIO")
        return real_write(fd, data)

    def missing_shell(file, argv):
        exec_calls.append((file, list(argv)))
        raise FileNotFoundError(errno.ENOENT, "injected missing shell", file)

    monkeypatch.setattr(jobs.os, "write", fail_second_status_write)
    monkeypatch.setattr(jobs.os, "execvp", missing_shell)
    monkeypatch.setattr(jobs, "_EXEC_HANDOFF_TIMEOUT_S", 0.10)
    monkeypatch.setattr(jobs, "_EXEC_FAILURE_HOLD_S", 0.25)
    gate = threading.Thread(
        target=lambda: gate_results.append(jobs._gate_main([
            str(release_r), str(ready_w), str(status_w), "--",
            "/definitely/missing/configured-shell", "-c", "true"])),
        daemon=True)
    launch = jobs._LocalLaunch("exec-frame-write-fault")
    launch.exec_r = status_r
    gate.start()
    started = time.monotonic()
    try:
        status, detail = launch.await_exec_handoff(timeout=0.10)
        elapsed = time.monotonic() - started
        assert len(exec_calls) == 1
        assert status_writes[0] == b"E"
        assert status_writes[1].startswith(b"F{")
        assert len(status_writes) == 2
        assert status == jobs._EXIT_EXEC_HANDOFF_UNKNOWN, (status, detail)
        assert 0.08 <= elapsed < 1.0
        assert gate.is_alive(), "early EOF forged E-only success"
        gate.join(timeout=1.0)
        assert not gate.is_alive()
        assert _pipe_bytes_through_eof(ready_r) == jobs._READY_BYTE
    finally:
        launch.close_idle()
        gate.join(timeout=1.0)
        for fd in (release_r, release_w, ready_r, ready_w, status_r, status_w):
            try:
                real_close(fd)
            except OSError:
                pass
    assert gate_results == [jobs._EXIT_GATE_ABANDONED]
    assert "Traceback" not in capsys.readouterr().err


def test_short_exec_failure_frame_return_holds_unknown_not_eof_success(
        jobs, monkeypatch, capsys):
    release_r, release_w = os.pipe()
    ready_r, ready_w = os.pipe()
    status_r, status_w = os.pipe()
    real_close, real_write = os.close, os.write
    status_writes, frame_calls, exec_calls = [], [], []
    gate_results, gate_raised = [], []
    assert real_write(release_w, jobs._RELEASE_BYTE) == 1
    real_close(release_w)

    def observe_status_write(fd, data):
        if fd == status_w:
            status_writes.append(bytes(data))
        return real_write(fd, data)

    def missing_shell(file, argv):
        exec_calls.append((file, list(argv)))
        raise FileNotFoundError(errno.ENOENT, "injected missing shell", file)

    def short_failure_frame(fd, frame):
        frame_calls.append((fd, bytes(frame)))
        return 0

    def run_gate():
        try:
            gate_results.append(jobs._gate_main([
                str(release_r), str(ready_w), str(status_w), "--",
                "/definitely/missing/configured-shell", "-c", "true"]))
        except BaseException as exc:
            gate_raised.append(exc)
        finally:
            try:
                real_close(status_w)
            except OSError:
                pass

    monkeypatch.setattr(jobs.os, "write", observe_status_write)
    monkeypatch.setattr(jobs.os, "execvp", missing_shell)
    monkeypatch.setattr(jobs, "_write_exec_failure_frame", short_failure_frame)
    monkeypatch.setattr(jobs, "_EXEC_FAILURE_HOLD_S", 0.20)
    gate = threading.Thread(target=run_gate, daemon=True)
    launch = jobs._LocalLaunch("short exec failure frame")
    launch.exec_r = status_r
    launch.entry = {"id": "exact-short-frame"}
    gate.start()
    started = time.monotonic()
    try:
        status, detail = launch.await_exec_handoff(timeout=0.06)
        elapsed = time.monotonic() - started
        assert exec_calls == [(
            "/definitely/missing/configured-shell",
            ["/definitely/missing/configured-shell", "-c", "true"])]
        assert status_writes == [jobs._EXEC_ATTEMPT_BYTE]
        assert len(frame_calls) == 1 and frame_calls[0][0] == status_w
        assert frame_calls[0][1].startswith(b"F{")
        assert frame_calls[0][1].endswith(b"}\n")
        assert status == jobs._EXIT_EXEC_HANDOFF_UNKNOWN, (status, detail)
        assert "record exact-short-frame is durable and RETAINED" in detail
        assert 0.04 <= elapsed < 0.5
        assert gate.is_alive(), "short frame return closed E into EOF success"
        assert _pipe_bytes_through_eof(ready_r) == jobs._READY_BYTE
        gate.join(timeout=1.0)
        assert not gate.is_alive()
    finally:
        launch.close_idle()
        gate.join(timeout=1.0)
        for fd in (release_r, ready_r, ready_w, status_r, status_w):
            try:
                real_close(fd)
            except OSError:
                pass
    assert gate_results == [jobs._EXIT_GATE_ABANDONED]
    assert gate_raised == []
    err = capsys.readouterr().err
    assert "failure record was incomplete" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("fault", (
    "attempt-post-effect-oserror", "attempt-post-effect-runtime",
    "encoder-runtime", "frame-runtime", "frame-post-effect",
    "execvp-runtime", "execvp-cancellation",
))
def test_known_post_attempt_fault_never_closes_into_eof_success(
        jobs, monkeypatch, capsys, fault):
    release_r, release_w = os.pipe()
    ready_r, ready_w = os.pipe()
    status_r, status_w = os.pipe()
    real_write, real_close = os.write, os.close
    assert real_write(release_w, jobs._RELEASE_BYTE) == 1
    real_close(release_w)
    primary = KeyboardInterrupt("injected execvp cancellation")
    fired, gate_results, gate_raised = [], [], []

    if fault.startswith("attempt-post-effect"):
        def write_then_raise(fd):
            fired.append(fault)
            assert real_write(fd, jobs._EXEC_ATTEMPT_BYTE) == 1
            if fault.endswith("oserror"):
                raise OSError(errno.EIO, "injected after exec-attempt write")
            raise RuntimeError("injected after exec-attempt write")
        monkeypatch.setattr(jobs, "_write_exec_attempt_raw", write_then_raise)
    else:
        def failed_exec(file, argv):
            fired.append(fault)
            if fault == "execvp-cancellation":
                raise primary
            if fault == "execvp-runtime":
                raise RuntimeError("injected unexpected execvp fault")
            raise FileNotFoundError(errno.ENOENT, "injected missing shell", file)
        monkeypatch.setattr(jobs.os, "execvp", failed_exec)
        if fault == "encoder-runtime":
            monkeypatch.setattr(
                jobs, "_encode_exec_failure",
                lambda *a: (_ for _ in ()).throw(
                    RuntimeError("injected encoder failure")))
        elif fault == "frame-runtime":
            monkeypatch.setattr(
                jobs, "_write_exec_failure_frame",
                lambda *a: (_ for _ in ()).throw(
                    RuntimeError("injected frame failure")))
        elif fault == "frame-post-effect":
            def frame_then_raise(fd, frame):
                assert real_write(fd, frame) == len(frame)
                raise RuntimeError("injected after complete frame")
            monkeypatch.setattr(
                jobs, "_write_exec_failure_frame", frame_then_raise)

    monkeypatch.setattr(jobs, "_EXEC_FAILURE_HOLD_S", 0.20)

    def run_gate():
        try:
            gate_results.append(jobs._gate_main([
                str(release_r), str(ready_w), str(status_w), "--",
                "/configured/shell", "-c", "true",
            ]))
        except BaseException as exc:
            gate_raised.append(exc)
        finally:
            try:
                real_close(status_w)
            except OSError:
                pass

    gate = threading.Thread(target=run_gate, daemon=True)
    launch = jobs._LocalLaunch("post-attempt fault")
    launch.exec_r = status_r
    launch.entry = {"id": "exact-" + fault}
    gate.start()
    started = time.monotonic()
    try:
        status, detail = launch.await_exec_handoff(timeout=0.06)
        elapsed = time.monotonic() - started
        assert _pipe_bytes_through_eof(ready_r) == jobs._READY_BYTE
        assert status == jobs._EXIT_EXEC_HANDOFF_UNKNOWN, (status, detail)
        assert "EXEC_HANDOFF_UNKNOWN" in detail and "RETAINED" in detail
        assert "record exact-%s" % fault in detail
        assert 0.04 <= elapsed < 0.5
        assert gate.is_alive(), "observed post-E failure closed into E+EOF success"
        gate.join(timeout=1.0)
        assert not gate.is_alive()
        if fault == "execvp-cancellation":
            assert gate_results == [75] and gate_raised == []
            assert primary.args == ("injected execvp cancellation",)
        else:
            assert gate_results == [jobs._EXIT_GATE_ABANDONED]
            assert gate_raised == []
    finally:
        gate.join(timeout=1.0)
        launch.close_idle()
        for fd in (release_r, ready_r, ready_w, status_r, status_w):
            try:
                real_close(fd)
            except OSError:
                pass
    assert fired == [fault]
    err = capsys.readouterr().err
    assert "Traceback" not in err
    if fault == "execvp-runtime":
        assert "configured-shell exec is UNKNOWN" in err
        assert "was interrupted" not in err, (
            "an ordinary helper fault was misclassified as cancellation")
    elif fault == "execvp-cancellation":
        assert "configured-shell exec was interrupted and is UNKNOWN" in err


def test_exec_unknown_hold_preserves_status_75_and_records_first_interruption(
        jobs, monkeypatch):
    status_r, status_w = os.pipe()
    real_sleep, real_close = time.sleep, os.close
    primary = KeyboardInterrupt("first hold cancellation")
    calls = []

    def interrupt_once(delay):
        calls.append(delay)
        if len(calls) == 1:
            raise primary
        return real_sleep(delay)

    monkeypatch.setattr(jobs.time, "sleep", interrupt_once)
    monkeypatch.setattr(jobs, "_EXEC_FAILURE_HOLD_S", 0.08)
    started = time.monotonic()
    escaped = None
    try:
        try:
            retained = jobs._hold_exec_status_unknown(status_w)
        except BaseException as exc:
            escaped = exc
        if escaped is None:
            elapsed = time.monotonic() - started
            assert retained is primary
            assert primary.args == ("first hold cancellation",)
            assert elapsed >= 0.06
            assert os.read(status_r, 1) == b""
    finally:
        for fd in (status_r, status_w):
            try:
                real_close(fd)
            except OSError:
                pass
    assert escaped is None, (
        "EXEC-UNKNOWN-HOLD-REPLACED-FIRST-BASEEXCEPTION: %r" % escaped)


def _public_authority_args(jobs, suffix):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    adopted = jobs.JOBS_DIR / (
        jobs._REMOTE_SCRIPT_PREFIX + "authority-%s.sh" % suffix)
    adopted.write_text("true\n", encoding="utf-8")
    args = type("A", (), {
        "host": "local", "purpose": "exec authority " + suffix,
        "command": [], "script": str(adopted),
        "adopt_script": str(adopted), "attempt_token": "a" * 32,
        "request_id": "r6-authority-" + suffix,
        "origin": "r6-public-test",
    })()
    return args, adopted


def _bind_manual_launch_registry(jobs, launch):
    """Give a hand-built launch the same pinned evidence as ``open_log``."""
    if launch.registry_txn.dir_fd is None:
        assert launch.registry_txn.open()
    for path_attr, component_attr, identity_attr in (
            ("log_path", "log_component", "log_identity"),
            ("adopt_path", "adopt_component", "adopt_identity")):
        path = getattr(launch, path_attr)
        if path is None:
            continue
        component = launch.registry_txn.component_from_display(path)
        setattr(launch, component_attr, component)
        setattr(
            launch, identity_attr,
            jobs._component_identity(launch.registry_txn, component))
    return launch


@contextlib.contextmanager
def _open_test_registry_txn(jobs, *, require_write=True, create=True,
                            lock=False):
    """Own one real transaction while a test injects descriptor-relative faults."""
    txn = jobs._RegistryTxn(
        jobs.JOBS_DIR, require_write=require_write, create=create)
    assert txn.open(), "precondition: the private registry must be open"
    primary = None
    try:
        if lock:
            txn.lock()
        yield txn
    except BaseException as exc:
        primary = exc
    finally:
        txn.close(primary=primary)


def _test_registry_identity(jobs, path):
    """Observe one display child through a real read-only transaction."""
    txn = jobs._RegistryTxn(
        jobs.JOBS_DIR, require_write=False, create=False)
    primary = None
    try:
        component = txn.component_from_display(path)
        if not txn.open():
            return ("unreadable", "FileNotFoundError", "registry absent")
        return jobs._component_identity(txn, component)
    except Exception as exc:
        return ("unreadable", type(exc).__name__, str(exc))
    except BaseException as exc:
        primary = exc
        raise
    finally:
        txn.close(primary=primary)


def _publish_valid_public_authority_entry(jobs, launch, cmd, origin):
    host, pid = jobs.socket.gethostname(), os.getpid()
    entry_id = "%s-%d" % (host, pid)
    raw = {
        "id": entry_id, "pid": pid, "starttime": jobs.proc_starttime(pid),
        "purpose": launch.purpose, "cmd": cmd, "host": host,
        "origin": origin, "started_at": "2026-08-22T00:00:00Z",
        "log": str(launch.log_path), "log_owned": True,
        "owned_paths": [str(launch.adopt_path)],
        "request_id": launch.request_id,
        "attempt_token": launch.attempt_token,
    }
    assert jobs._entry_schema_reason(
        raw, filename=entry_id + ".json") is None
    publication = jobs._publish_entry(launch.registry_txn, raw)
    launch.entry = publication.entry
    launch.retained_entry_id = entry_id
    return launch.entry


def _forbid_post_release_destruction(jobs, monkeypatch):
    effects = []
    monkeypatch.setattr(
        jobs, "_forget_or_retain",
        lambda *a, **k: effects.append("_forget_or_retain") or {
            "entry_absent_durable": False, "notes": []})
    monkeypatch.setattr(
        jobs, "forget", lambda *a, **k: effects.append("forget") or {})
    monkeypatch.setattr(
        jobs, "_terminate_and_wait",
        lambda *a, **k: effects.append("_terminate_and_wait") or
        "forbidden termination")
    monkeypatch.setattr(
        jobs, "_discard_owned_log",
        lambda *a, **k: effects.append("_discard_owned_log") or
        "forbidden deletion")
    monkeypatch.setattr(
        jobs, "_cleanup_attempt_identity",
        lambda *a, **k: effects.append("_cleanup_attempt_identity") or
        (False, "forbidden deletion"))
    for name in ("kill", "killpg", "unlink", "remove", "replace"):
        monkeypatch.setattr(
            jobs.os, name,
            lambda *a, _name=name, **k: effects.append("os." + _name))
    return effects


def _install_controlled_public_exec_gate(
        jobs, monkeypatch, observed, *, e_delay_s, writer_hold_s):
    real_close, real_read, real_write = os.close, os.read, os.write
    launches, threads = [], []

    class LiveGate:
        def poll(self):
            return None

    def spawn(launch, argv):
        launches.append(launch)
        launch.user_argv = list(argv)
        launch.proc = LiveGate()
        launch.pid = 424242
        launch.exec_reader_probe = launch.exec_r
        release_r, ready_w, exec_w = (
            launch.release_r, launch.ready_w, launch.exec_w)
        launch.release_r = launch.ready_w = launch.exec_w = None
        real_close(launch.log_fd)
        launch.log_fd = None

        def run_gate():
            owned = {release_r, ready_w, exec_w}

            def close_owned(fd):
                if fd in owned:
                    real_close(fd)
                    owned.remove(fd)

            try:
                observed["ready"].append(
                    real_write(ready_w, jobs._READY_BYTE))
                close_owned(ready_w)
                observed["release"].append(real_read(release_r, 1))
                close_owned(release_r)
                time.sleep(e_delay_s)
                observed["exec"].append(
                    real_write(exec_w, jobs._EXEC_ATTEMPT_BYTE))
                time.sleep(writer_hold_s)
            except BaseException as exc:
                observed["errors"].append(exc)
            finally:
                for fd in list(owned):
                    try:
                        close_owned(fd)
                    except OSError:
                        pass

        gate = threading.Thread(target=run_gate, daemon=True)
        threads.append(gate)
        gate.start()

    monkeypatch.setattr(jobs._LocalLaunch, "spawn", spawn)
    monkeypatch.setattr(
        jobs._LocalLaunch, "publish",
        lambda launch, cmd, origin: _publish_valid_public_authority_entry(
            jobs, launch, cmd, origin))
    return launches, threads


@pytest.mark.parametrize("first_kind", ("keyboard", "system-exit"))
def test_exec_wait_cancellation_preserves_first_primary_when_close_is_interrupted(
        jobs, monkeypatch, capsys, first_kind):
    primary = (KeyboardInterrupt("first parent cancellation")
               if first_kind == "keyboard" else SystemExit(71))
    secondary = (SystemExit(73) if first_kind == "keyboard"
                 else KeyboardInterrupt("second close cancellation"))
    before_notes = list(getattr(primary, "__notes__", []))
    before_args, before_code = primary.args, getattr(primary, "code", None)
    args, adopted = _public_authority_args(jobs, "cancel-" + first_kind)
    observed = {"ready": [], "release": [], "exec": [],
                "exec_read": [], "errors": []}
    effects = _forbid_post_release_destruction(jobs, monkeypatch)
    launches, threads = _install_controlled_public_exec_gate(
        jobs, monkeypatch, observed, e_delay_s=0.04, writer_hold_s=0.15)
    real_close, real_read = os.close, os.read

    def cancel_wait(launch, timeout=None):
        readable, _, _ = select.select([launch.exec_r], [], [], 0.5)
        assert readable, "controlled gate never emitted exec attempt E"
        observed["exec_read"].append(real_read(launch.exec_r, 1))
        raise primary

    def close_then_cancel(fd):
        real_close(fd)
        if launches and fd == launches[0].exec_reader_probe:
            raise secondary

    monkeypatch.setattr(jobs._LocalLaunch, "await_exec_handoff", cancel_wait)
    monkeypatch.setattr(jobs.os, "close", close_then_cancel)
    started = time.monotonic()
    with pytest.raises(BaseException) as raised:
        jobs.cmd_run(args)
    elapsed = time.monotonic() - started
    out, err = capsys.readouterr()
    for gate in threads:
        gate.join(timeout=1.0)

    assert raised.value is primary and type(raised.value) is type(primary)
    assert raised.value.args == before_args
    assert getattr(raised.value, "code", None) == before_code
    assert 0.03 <= elapsed < 1.0
    assert len(launches) == 1 and len(threads) == 1
    assert not threads[0].is_alive() and observed["errors"] == []
    assert observed["ready"] == [1]
    assert observed["release"] == [jobs._RELEASE_BYTE]
    assert observed["exec"] == [1]
    assert observed["exec_read"] == [jobs._EXEC_ATTEMPT_BYTE]
    launch = launches[0]
    assert launch.release_phase == jobs._RELEASE_DELIVERED
    assert launch.may_signal_child is False and launch.may_delete_artifacts is False
    entries = jobs.load_all()
    assert entries.malformed == [] and len(entries) == 1
    entry = entries[0]
    assert entry["id"] == launch.entry["id"]
    assert pathlib.Path(entry["_path"]).exists()
    assert pathlib.Path(entry["log"]).exists()
    assert entry["owned_paths"] == [str(adopted)] and adopted.exists()
    assert effects == [] and out == "" and err == ""
    notes = getattr(primary, "__notes__", [])[len(before_notes):]
    assert len(notes) == 3
    assert "local launch orchestration was interrupted" in notes[0]
    assert "bd-jobs cleanup facts:" in notes[0]
    assert "secondary cancellation" in notes[1]
    assert str(secondary) in notes[1]
    assert "exact durable entry %s" % entry["id"] in notes[2]
    assert "RETAINED without signal or deletion" in notes[2]


def test_exec_handoff_success_does_not_wait_for_shell_or_workload_exit(
        jobs, monkeypatch, tmp_path):
    fifo = tmp_path / "workload-blocker.fifo"
    marker = tmp_path / "workload-started"
    os.mkfifo(fifo)
    script = (
        'printf "%%s\\n" "$$" > %s; read -r _ < %s'
        % (shlex.quote(str(marker)), shlex.quote(str(fifo))))
    args = _run_args("async-handoff", ["--", "bash", "-c", script])
    launched, argvs, results, raised = [], [], [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)

    def invoke():
        try:
            results.append(jobs.cmd_run(args))
        except BaseException as exc:
            raised.append(exc)

    runner = threading.Thread(target=invoke, daemon=True)
    runner.start()
    entry = None
    try:
        assert _wait_until(marker.exists, 15.0)
        entries = jobs.load_all()
        assert len(entries) == 1 and len(launched) == 1
        entry = entries[0]
        assert entry["pid"] == launched[0] and jobs.alive(entry)
        runner.join(timeout=2.0)
        assert not runner.is_alive(), "cmd_run waited for workload exit"
        assert raised == [] and results == [0]
        assert jobs.alive(entry)
    finally:
        for pid in launched:
            _reap_process(pid)
        runner.join(timeout=5.0)
        if entry is not None:
            jobs.forget(entry)


def test_public_exec_timeout_names_unknown_state_durable_id_and_retention(
        jobs, monkeypatch, capsys):
    args, adopted = _public_authority_args(jobs, "timeout")
    observed = {"ready": [], "release": [], "exec": [], "errors": []}
    effects = _forbid_post_release_destruction(jobs, monkeypatch)
    launches, threads = _install_controlled_public_exec_gate(
        jobs, monkeypatch, observed, e_delay_s=0.0, writer_hold_s=0.20)
    monkeypatch.setattr(jobs, "_EXEC_HANDOFF_TIMEOUT_S", 0.08)

    started = time.monotonic()
    rc = jobs.cmd_run(args)
    elapsed = time.monotonic() - started
    out, err = capsys.readouterr()
    for gate in threads:
        gate.join(timeout=1.0)

    assert rc == jobs._EXIT_EXEC_HANDOFF_UNKNOWN
    assert 0.06 <= elapsed < 1.0
    assert len(launches) == 1 and len(threads) == 1
    assert not threads[0].is_alive() and observed["errors"] == []
    assert observed["ready"] == [1]
    assert observed["release"] == [jobs._RELEASE_BYTE]
    assert observed["exec"] == [1]
    launch = launches[0]
    assert launch.release_phase == jobs._RELEASE_DELIVERED
    assert launch.may_signal_child is False and launch.may_delete_artifacts is False
    entries = jobs.load_all()
    assert entries.malformed == [] and len(entries) == 1
    entry = entries[0]
    assert out.strip().splitlines() == [entry["id"]]
    assert "EXEC_HANDOFF_UNKNOWN" in err
    assert "record %s is durable and RETAINED" % entry["id"] in err
    assert "reconcile by id" in err
    assert pathlib.Path(entry["_path"]).exists()
    assert pathlib.Path(entry["log"]).exists()
    assert entry["owned_paths"] == [str(adopted)] and adopted.exists()
    assert effects == []


def test_configured_bash_handoff_does_not_translate_inner_command_127(
        jobs, capsys):
    entry = None
    try:
        rc = jobs.cmd_run(_run_args(
            "inner command failure",
            ["--", "definitely-missing-inner-command-row212"]))
        out, err = capsys.readouterr()
        entries = jobs.load_all()
        assert rc == 0, err
        assert len(entries) == 1
        entry = entries[0]
        assert out.strip().splitlines()[-1] == entry["id"]
        assert "EXEC_FAILED" not in err and "EXEC_HANDOFF_UNKNOWN" not in err
    finally:
        if entry is not None:
            jobs.forget(entry)


def test_raw_release_epipe_is_proved_not_delivered(jobs):
    release_r, release_w = os.pipe()
    os.close(release_r)
    try:
        state, detail = jobs._write_release_byte_raw(release_w)
        assert state == jobs._RELEASE_PROVED_NOT_DELIVERED
        assert isinstance(detail, BrokenPipeError) and detail.errno == errno.EPIPE
    finally:
        os.close(release_w)


def test_post_effect_release_wrapper_is_unknown_and_never_cleans(
        jobs, monkeypatch, tmp_path):
    release_r, release_w = os.pipe()
    launch = jobs._LocalLaunch("post-effect release")
    launch.release_w = release_w
    launch.entry = {"id": "job-post-effect"}
    launch.retained_entry_id = "job-post-effect"
    jobs.JOBS_DIR.mkdir(mode=0o700)
    launch.log_path = jobs.JOBS_DIR / "owned.log"
    launch.log_path.write_text("evidence", encoding="utf-8")
    _bind_manual_launch_registry(jobs, launch)
    cleanups = []
    real_write = os.write

    def write_then_raise(fd):
        assert real_write(fd, jobs._RELEASE_BYTE) == 1
        raise OSError(errno.EIO, "injected wrapper after delivered byte")

    monkeypatch.setattr(jobs, "_write_release_byte_raw", write_then_raise,
                        raising=False)
    monkeypatch.setattr(
        jobs, "_forget_or_retain",
        lambda entry: cleanups.append(entry) or pytest.fail(
            "delivery-UNKNOWN release granted cleanup authority"),
    )
    try:
        with pytest.raises(jobs._LaunchAborted) as raised:
            launch.release()

        assert os.read(release_r, 1) == jobs._RELEASE_BYTE
        assert raised.value.status == jobs._EXIT_RELEASE_UNKNOWN
        assert launch.release_phase == jobs._RELEASE_UNKNOWN
        assert launch.entry["id"] == "job-post-effect"
        assert launch.log_path.read_text() == "evidence"
        assert cleanups == []
    finally:
        launch.close_idle()
        os.close(release_r)


def test_release_close_cancellation_cannot_replace_initiating_cancellation(
        jobs, monkeypatch, tmp_path):
    primary = KeyboardInterrupt("initiating release cancellation")
    secondary = SystemExit(88)
    launch, final, log, adopted = _release_failure_transaction(jobs, tmp_path)
    release_w = launch.release_w
    real_close = os.close
    try:
        with monkeypatch.context() as mp:
            effects = _forbid_post_release_destruction(jobs, mp)
            mp.setattr(
                jobs, "_write_release_byte_raw",
                lambda _fd: (_ for _ in ()).throw(primary))

            def close_then_cancel(fd):
                real_close(fd)
                if fd == release_w:
                    raise secondary

            mp.setattr(jobs.os, "close", close_then_cancel)
            with pytest.raises(BaseException) as raised:
                launch.release()

            assert raised.value is primary
            assert launch.release_w is None and launch.close_failed is True
            assert launch.release_phase == jobs._RELEASE_UNKNOWN
            assert launch.retained_entry_id == launch.entry["id"]
            assert launch.may_signal_child is False
            assert launch.may_delete_artifacts is False
            assert final.exists() and log.exists() and adopted.exists()
            assert effects == []
            notes = "\n".join(getattr(primary, "__notes__", []))
            assert "secondary cancellation while closing release_w" in notes
            assert str(secondary.code) in notes
    finally:
        launch.close_idle()
        if final.exists():
            jobs.forget(launch.entry)


def test_release_unknown_close_cancellation_preserves_status_id_and_retention(
        jobs, monkeypatch, tmp_path):
    secondary = SystemExit(89)
    launch, final, log, adopted = _release_failure_transaction(jobs, tmp_path)
    release_w = launch.release_w
    real_close = os.close
    try:
        with monkeypatch.context() as mp:
            effects = _forbid_post_release_destruction(jobs, mp)
            mp.setattr(
                jobs, "_write_release_byte_raw",
                lambda _fd: (jobs._RELEASE_UNKNOWN,
                             OSError(errno.EIO,
                                     "injected release uncertainty")))

            def close_then_cancel(fd):
                real_close(fd)
                if fd == release_w:
                    raise secondary

            mp.setattr(jobs.os, "close", close_then_cancel)
            with pytest.raises(jobs._LaunchAborted) as raised:
                launch.release()

            assert raised.value.status == jobs._EXIT_RELEASE_UNKNOWN
            assert launch.entry["id"] in raised.value.message
            assert launch.release_w is None and launch.close_failed is True
            assert launch.release_phase == jobs._RELEASE_UNKNOWN
            assert launch.retained_entry_id == launch.entry["id"]
            assert launch.may_signal_child is False
            assert launch.may_delete_artifacts is False
            assert final.exists() and log.exists() and adopted.exists()
            assert effects == []
            notes = "\n".join(getattr(raised.value, "__notes__", []))
            assert "secondary cancellation while closing release_w" in notes
            assert str(secondary.code) in notes
    finally:
        launch.close_idle()
        if final.exists():
            jobs.forget(launch.entry)


@pytest.mark.parametrize("stage", ("spawn", "publish"))
@pytest.mark.parametrize("cancel_kind", ("keyboard", "system-exit"))
def test_initiating_launch_cancellation_preserves_identity_through_cleanup(
        jobs, monkeypatch, tmp_path, stage, cancel_kind):
    primary = (KeyboardInterrupt("initiating cancellation")
               if cancel_kind == "keyboard" else SystemExit(73))
    launch = jobs._LocalLaunch("initiating cancellation")
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    log = jobs.JOBS_DIR / (stage + ".log")
    log.write_text("owned evidence", encoding="utf-8")
    launch.log_path = log
    _bind_manual_launch_registry(jobs, launch)
    release_r, release_w = os.pipe()
    launch.release_r, launch.release_w = release_r, release_w
    before_args = primary.args
    before_code = getattr(primary, "code", None)
    if stage == "spawn":
        monkeypatch.setattr(
            jobs.subprocess, "Popen",
            lambda *a, **k: (_ for _ in ()).throw(primary))
        invoke = lambda: launch.spawn(["bash", "-c", "true"])
    else:
        launch.pid = 123456789
        monkeypatch.setattr(
            jobs, "register", lambda *a, **k: (_ for _ in ()).throw(primary))
        monkeypatch.setattr(
            jobs, "_terminate_and_wait", lambda pid: "proven absent")
        invoke = lambda: launch.publish("true", "test5")

    try:
        with pytest.raises(BaseException) as raised:
            invoke()

        assert raised.value is primary
        assert type(raised.value) is type(primary)
        assert raised.value.args == before_args
        assert getattr(raised.value, "code", None) == before_code
        assert not log.exists(), "pre-publication owned log was not reclaimed"
        assert launch.release_r is None and launch.release_w is None
        notes = "\n".join(getattr(primary, "__notes__", []))
        assert "never released" in notes and "cleanup" in notes
    finally:
        launch.close_idle()


@pytest.mark.parametrize(
    ("stage", "published"),
    [
        pytest.param("open_channels", False, id="between-channel-acquisitions"),
        pytest.param("spawn", False, id="after-popen-before-child-end-closes"),
        pytest.param("await_ready", False, id="during-ready-observation"),
        pytest.param("release", True, id="during-final-attachedness-check"),
    ],
)
def test_outer_local_cancellation_runs_state_aware_abort_before_idle_close(
        jobs, monkeypatch, stage, published):
    """Every orchestration cancellation drains pre-release ownership first."""
    primary = KeyboardInterrupt("injected orchestration cancellation")
    events = []

    class ControlledLaunch:
        def __init__(self, *_args, **_kwargs):
            self.entry = ({"id": "retained-row212"} if published else None)
            self.retained_entry_id = None
            self.release_phase = jobs._RELEASE_PRE
            self.abort_settled = False
            self.close_failed = False
            self.notes = []

        def _step(self, name, result=None):
            events.append(name)
            if name == stage:
                raise primary
            return result

        def open_log(self):
            return self._step("open_log")

        def open_channels(self):
            return self._step("open_channels")

        def spawn(self, _argv):
            return self._step("spawn")

        def await_ready(self):
            return self._step("await_ready")

        def publish(self, _cmd, _origin):
            return self._step("publish", self.entry or {"id": "published"})

        def release(self):
            return self._step("release")

        def await_exec_handoff(self):
            return self._step("await_exec_handoff", (0, "ok"))

        def abort(self, status=None, reason=None, keep_log=False, primary=None):
            events.append(("abort", status, keep_log, primary, reason))
            raise primary

        def close_idle(self, primary=None):
            events.append(("close_idle", primary))
            if primary is not None:
                raise primary

    monkeypatch.setattr(jobs, "_LocalLaunch", ControlledLaunch)

    with pytest.raises(KeyboardInterrupt) as raised:
        jobs.cmd_run(_run_args("orchestration cancellation", ["true"]))

    assert raised.value is primary
    abort_event = next(event for event in events
                       if isinstance(event, tuple) and event[0] == "abort")
    close_event = next(event for event in events
                       if isinstance(event, tuple) and event[0] == "close_idle")
    assert events.index(abort_event) < events.index(close_event), events
    assert abort_event[1] is None and abort_event[3] is primary, abort_event
    assert abort_event[2] is published, abort_event
    assert close_event[1] is primary, close_event


def test_cmd_run_pipe_acquisition_cancellation_drains_pre_release_resources(
        jobs, monkeypatch):
    primary = KeyboardInterrupt("cancel between pipe acquisitions")
    real_pipe = os.pipe
    acquired = []

    def cancel_second_pipe():
        if acquired:
            raise primary
        pair = real_pipe()
        acquired.extend(pair)
        return pair

    monkeypatch.setattr(jobs.os, "pipe", cancel_second_pipe)
    with pytest.raises(KeyboardInterrupt) as raised:
        jobs.cmd_run(_run_args("pipe cancellation", ["true"]))

    assert raised.value is primary and len(acquired) == 2
    for fd in acquired:
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == errno.EBADF
    assert list(jobs.JOBS_DIR.iterdir()) == []


def test_cmd_run_post_popen_close_cancellation_reaps_gate_and_drains_artifacts(
        jobs, monkeypatch, tmp_path):
    primary = KeyboardInterrupt("cancel after Popen")
    marker = tmp_path / "must-not-run"
    real_close = jobs._LocalLaunch._close
    real_terminate = jobs._terminate_and_wait
    terminated = []
    injected = []

    def close_then_cancel(launch, name):
        result = real_close(launch, name)
        if name == "release_r" and not injected:
            injected.append(name)
            raise primary
        return result

    def terminate_and_record(pid):
        terminated.append(pid)
        return real_terminate(pid)

    monkeypatch.setattr(jobs._LocalLaunch, "_close", close_then_cancel)
    monkeypatch.setattr(jobs, "_terminate_and_wait", terminate_and_record)
    with pytest.raises(KeyboardInterrupt) as raised:
        jobs.cmd_run(_run_args("post-Popen cancellation", _marker_command(marker)))

    assert raised.value is primary and injected == ["release_r"]
    assert len(terminated) == 1 and not marker.exists()
    assert list(jobs.JOBS_DIR.iterdir()) == []


def test_cmd_run_ready_wait_cancellation_reaps_gate_and_drains_artifacts(
        jobs, monkeypatch, tmp_path):
    primary = KeyboardInterrupt("cancel during READY wait")
    marker = tmp_path / "must-not-run"
    real_select = jobs.select.select
    real_terminate = jobs._terminate_and_wait
    terminated = []
    injected = []

    def select_then_cancel(*args, **kwargs):
        if not injected:
            injected.append(True)
            raise primary
        return real_select(*args, **kwargs)

    def terminate_and_record(pid):
        terminated.append(pid)
        return real_terminate(pid)

    monkeypatch.setattr(jobs.select, "select", select_then_cancel)
    monkeypatch.setattr(jobs, "_terminate_and_wait", terminate_and_record)
    with pytest.raises(KeyboardInterrupt) as raised:
        jobs.cmd_run(_run_args("READY cancellation", _marker_command(marker)))

    assert raised.value is primary and injected == [True]
    assert len(terminated) == 1 and not marker.exists()
    assert list(jobs.JOBS_DIR.iterdir()) == []


def test_final_attachedness_cancellation_drains_child_and_retains_exact_entry(
        jobs, monkeypatch, tmp_path):
    """Cancellation after publication retains evidence but still settles the gate."""
    primary = KeyboardInterrupt("cancel before release authority")
    marker = tmp_path / "must-not-run"
    real_require_attached = jobs._RegistryTxn.require_attached
    real_terminate = jobs._terminate_and_wait
    terminated = []

    def cancel_at_release(txn, context):
        if context == "before local release":
            raise primary
        return real_require_attached(txn, context)

    def terminate_and_record(pid):
        terminated.append(pid)
        return real_terminate(pid)

    monkeypatch.setattr(jobs._RegistryTxn, "require_attached", cancel_at_release)
    monkeypatch.setattr(jobs, "_terminate_and_wait", terminate_and_record)

    with pytest.raises(KeyboardInterrupt) as raised:
        jobs.cmd_run(_run_args("attachedness cancellation", _marker_command(marker)))

    entries = jobs.load_all()
    try:
        assert raised.value is primary
        assert len(entries) == 1, entries
        entry = entries[0]
        assert terminated == [entry["pid"]], terminated
        assert not marker.exists(), "the gated workload ran during cancellation"
        assert pathlib.Path(entry["log"]).is_file(), entry
        notes = "\n".join(getattr(primary, "__notes__", []))
        assert entry["id"] in notes and "RETAINED" in notes, notes
    finally:
        for entry in entries:
            jobs.forget(entry)


def test_cmd_run_proved_not_delivered_cleanup_cancellation_retains_evidence(
        jobs, monkeypatch, tmp_path):
    primary = KeyboardInterrupt("cancel during entry withdrawal")
    marker = tmp_path / "must-not-run"
    real_terminate = jobs._terminate_and_wait
    terminated = []

    monkeypatch.setattr(
        jobs, "_write_release_byte_raw",
        lambda _fd: (jobs._RELEASE_PROVED_NOT_DELIVERED, "injected EPIPE"))
    monkeypatch.setattr(
        jobs, "_forget_or_retain",
        lambda _entry: (_ for _ in ()).throw(primary))

    def terminate_and_record(pid):
        terminated.append(pid)
        return real_terminate(pid)

    monkeypatch.setattr(jobs, "_terminate_and_wait", terminate_and_record)
    with pytest.raises(KeyboardInterrupt) as raised:
        jobs.cmd_run(_run_args("withdrawal cancellation", _marker_command(marker)))

    entries = jobs.load_all()
    try:
        assert raised.value is primary and len(entries) == 1, entries
        entry = entries[0]
        assert terminated == [entry["pid"]] and not marker.exists()
        assert pathlib.Path(entry["log"]).is_file()
        notes = "\n".join(getattr(primary, "__notes__", []))
        assert entry["id"] in notes and "RETAINED" in notes, notes
    finally:
        for entry in entries:
            jobs.forget(entry)


def test_outer_local_cancellation_does_not_repeat_a_step_local_abort(
        jobs, monkeypatch):
    """A step that already drained ownership is not destructively settled twice."""
    primary = KeyboardInterrupt("already settled in spawn")
    events = []

    class SettledLaunch:
        def __init__(self, *_args, **_kwargs):
            self.entry = None
            self.retained_entry_id = None
            self.release_phase = jobs._RELEASE_PRE
            self.abort_settled = False
            self.close_failed = False
            self.notes = []

        def open_log(self):
            pass

        def open_channels(self):
            pass

        def spawn(self, _argv):
            self.abort_settled = True
            events.append("step-abort")
            raise primary

        def abort(self, **_kwargs):
            events.append("outer-abort")
            raise primary

        def close_idle(self, primary=None):
            events.append("close-idle")
            raise primary

    monkeypatch.setattr(jobs, "_LocalLaunch", SettledLaunch)

    with pytest.raises(KeyboardInterrupt) as raised:
        jobs.cmd_run(_run_args("single settlement", ["true"]))

    assert raised.value is primary
    assert events == ["step-abort", "close-idle"], events


def test_cmd_run_does_not_repeat_a_real_publish_cancellation_abort(
        jobs, monkeypatch, tmp_path):
    primary = KeyboardInterrupt("cancel inside publish")
    marker = tmp_path / "must-not-run"
    real_terminate = jobs._terminate_and_wait
    terminated = []

    monkeypatch.setattr(
        jobs, "register", lambda *_a, **_k: (_ for _ in ()).throw(primary))

    def terminate_and_record(pid):
        terminated.append(pid)
        return real_terminate(pid)

    monkeypatch.setattr(jobs, "_terminate_and_wait", terminate_and_record)
    with pytest.raises(KeyboardInterrupt) as raised:
        jobs.cmd_run(_run_args("publish cancellation", _marker_command(marker)))

    assert raised.value is primary
    assert len(terminated) == 1, terminated
    assert not marker.exists() and list(jobs.JOBS_DIR.iterdir()) == []


def test_abort_close_cancellation_cannot_replace_initiating_primary(
        jobs, monkeypatch, tmp_path):
    primary = KeyboardInterrupt("initiating launch cancellation")
    secondary = SystemExit(88)
    adopted = jobs.JOBS_DIR / ".bd-jobs-script-abort-cancel.sh"
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    adopted.write_text("owned script\n", encoding="utf-8")
    log = jobs.JOBS_DIR / "abort-cancel.log"
    log.write_text("owned log\n", encoding="utf-8")
    launch = jobs._LocalLaunch(
        "abort cancellation", adopt_path=str(adopted))
    launch.log_path = log
    _bind_manual_launch_registry(jobs, launch)
    launch.pid = 424242
    peers, owned_fds = [], []
    for name in ("release_w", "ready_r", "ready_w", "release_r", "log_fd"):
        owned_fd, peer = os.pipe()
        setattr(launch, name, owned_fd)
        owned_fds.append(owned_fd)
        peers.append(peer)
    first_fd = launch.release_w
    real_close = os.close
    closes, cleanup, cancelled = [], [], []

    def close_then_cancel(fd):
        closes.append(fd)
        real_close(fd)
        if fd == first_fd and not cancelled:
            cancelled.append(fd)
            raise secondary

    monkeypatch.setattr(jobs.os, "close", close_then_cancel)
    monkeypatch.setattr(
        jobs, "_terminate_and_wait",
        lambda pid: cleanup.append("child") or "child reaped")
    real_log = jobs._discard_owned_log
    real_adopt = jobs._cleanup_attempt_identity

    def cleanup_log(*args, **kwargs):
        cleanup.append("log")
        return real_log(*args, **kwargs)

    def cleanup_adopt(*args, **kwargs):
        cleanup.append(
            "log-identity" if pathlib.Path(args[0]) == log else "adopt")
        return real_adopt(*args, **kwargs)

    monkeypatch.setattr(jobs, "_discard_owned_log", cleanup_log)
    monkeypatch.setattr(jobs, "_cleanup_attempt_identity", cleanup_adopt)
    try:
        with pytest.raises(BaseException) as raised:
            launch.abort(
                jobs._EXIT_REGISTRATION_FAILED,
                "initiating operation cancelled", primary=primary)
        assert raised.value is primary
        assert primary.args == ("initiating launch cancellation",)
        assert secondary.code == 88
        assert cleanup == ["child", "log", "log-identity", "adopt"]
        assert all(getattr(launch, name) is None for name in (
            "release_w", "ready_r", "ready_w", "release_r", "log_fd"))
        assert closes[:5] == owned_fds and len(set(owned_fds)) == 5
        assert not log.exists() and not adopted.exists()
        notes = "\n".join(getattr(primary, "__notes__", []))
        assert "secondary cancellation" in notes and "88" in notes
        assert "child reaped" in notes
    finally:
        monkeypatch.setattr(jobs.os, "close", real_close)
        launch.close_idle()
        for fd in peers:
            try:
                real_close(fd)
            except OSError:
                pass


@pytest.mark.parametrize("status", (3, 5, 8))
def test_abort_cleanup_cancellation_cannot_replace_computed_lifecycle_status(
        jobs, monkeypatch, status):
    """Cleanup uncertainty is evidence on, never a replacement for, status."""
    launch = jobs._LocalLaunch("computed status survives cleanup")
    secondary = SystemExit(80 + status)
    fired = []

    def cancel_first_close(name):
        if not fired:
            fired.append(name)
            raise secondary

    monkeypatch.setattr(launch, "_close", cancel_first_close)
    with pytest.raises(jobs._LaunchAborted) as raised:
        launch.abort(status, "computed lifecycle outcome")

    assert fired == ["release_w"]
    assert raised.value.status == status
    assert "computed lifecycle outcome" in raised.value.message
    assert "cleanup was interrupted" in raised.value.message
    assert str(secondary.code) in raised.value.message


@pytest.mark.parametrize("status", (3, 5, 8))
def test_cmd_run_close_cancellation_cannot_replace_computed_lifecycle_status(
        jobs, monkeypatch, status):
    """The outer close funnel retains an already-decided CLI status."""
    secondary = SystemExit(90 + status)
    closes = []

    def computed(_launch):
        raise jobs._LaunchAborted(status, "PRIMARY STATUS %d" % status)

    def close_with_secondary(txn, primary=None):
        closes.append(primary)
        if primary is not None:
            jobs._note_secondary_failure(
                primary, "registry close is UNKNOWN", secondary)
            raise primary
        raise secondary

    monkeypatch.setattr(jobs._LocalLaunch, "open_log", computed)
    monkeypatch.setattr(jobs._RegistryTxn, "close", close_with_secondary)
    merged = io.StringIO()
    with contextlib.redirect_stdout(merged), contextlib.redirect_stderr(merged):
        rc = jobs.cmd_run(_run_args(
            "computed status", ["--", "bash", "-c", "true"]))

    assert rc == status
    assert len(closes) == 1 and isinstance(closes[0], jobs._LaunchAborted)
    assert "PRIMARY STATUS %d" % status in merged.getvalue()
    assert "registry close is UNKNOWN" in merged.getvalue()


def test_abort_retained_record_names_log_and_adopted_path(
        jobs, tmp_path):
    adopted = tmp_path / "retained-adopted.sh"
    log = tmp_path / "retained.log"
    adopted.write_text("echo retained\n", encoding="utf-8")
    log.write_text("retained log\n", encoding="utf-8")
    launch = jobs._LocalLaunch(
        "retained resources", adopt_path=str(adopted))
    launch.log_path = log

    with pytest.raises(jobs._LaunchAborted) as raised:
        launch.abort(
            jobs._EXIT_PUBLISHED_NOT_DURABLE,
            "published record retained", keep_log=True)

    assert log.exists() and adopted.exists()
    assert str(log) in raised.value.message
    assert str(adopted) in raised.value.message
    assert "retained record names" in raised.value.message


def test_note_and_render_helpers_cannot_replace_a_hostile_primary(jobs):
    calls = []

    class HostilePrimary(RuntimeError):
        def add_note(self, note):
            calls.append(note)
            raise SystemExit(97)

    class HostileText(RuntimeError):
        def __str__(self):
            raise KeyboardInterrupt("hostile __str__")

    primary = HostilePrimary("exact primary")
    jobs._note_secondary_failure(
        primary, "secondary cleanup", RuntimeError("ordinary secondary"))
    assert primary.args == ("exact primary",)
    assert calls == ["secondary cleanup (ordinary secondary)"]
    primary_render = jobs._exception_text(primary)
    assert "exact primary" in primary_render
    assert "secondary cleanup (ordinary secondary)" in primary_render

    rendered_primary = RuntimeError("render owner")
    jobs._note_secondary_failure(
        rendered_primary, "hostile secondary", HostileText("hidden"))
    rendered = jobs._exception_text(HostileText("hidden primary"))
    assert "HostileText" in rendered and "text unavailable" in rendered
    assert any("HostileText" in note and "text unavailable" in note
               for note in rendered_primary.__notes__)

    class HostileMetadata(RuntimeError):
        def __init__(self, attribute):
            super().__init__("metadata owner %s" % attribute)
            self.attribute = attribute

        def __getattribute__(self, name):
            attribute = RuntimeError.__getattribute__(self, "attribute")
            if name == attribute:
                raise SystemExit("hostile metadata %s" % name)
            return RuntimeError.__getattribute__(self, name)

    for attribute in (
            "__notes__", "_bd_secondary_notes", "__cause__", "__context__"):
        metadata_render = jobs._exception_text(HostileMetadata(attribute))
        assert "metadata owner %s" % attribute in metadata_render
        assert attribute in metadata_render and "unavailable" in metadata_render

    non_string_notes = RuntimeError("non-string notes owner")
    non_string_notes.__notes__ = [object(), HostileText("hostile note")]
    non_string_render = jobs._exception_text(non_string_notes)
    assert "non-string notes owner" in non_string_render
    assert "object at" in non_string_render
    assert "HostileText" in non_string_render
    assert "text unavailable" in non_string_render

    class InterruptedNotes:
        def __iter__(self):
            yield "note before interruption"
            raise KeyboardInterrupt("hostile notes iterator")

    interrupted_notes = RuntimeError("interrupted notes owner")
    interrupted_notes.__notes__ = InterruptedNotes()
    interrupted_render = jobs._exception_text(interrupted_notes)
    assert "note before interruption" in interrupted_render
    assert "__notes__ iteration unavailable" in interrupted_render


def test_public_register_renders_notes_from_completed_unlock_helper(
        jobs, sleeper, monkeypatch, capsys):
    proc = sleeper()
    real_unlock = jobs._RegistryTxn.unlock
    failure = RuntimeError("PRIMARY completed unlock helper fault")
    failure.add_note("SECONDARY exact unlock disposition is UNKNOWN")
    calls = []

    def unlock_then_raise(txn, primary=None):
        calls.append(txn)
        real_unlock(txn, primary=primary)
        raise failure

    monkeypatch.setattr(jobs._RegistryTxn, "unlock", unlock_then_raise)
    rc = jobs.main(["register", "--pid", str(proc.pid), "--purpose",
                    "noted unlock", "--cmd", "sleep 60"])
    out, err = capsys.readouterr()
    finals = list(jobs.JOBS_DIR.glob("*.json"))
    assert len(calls) == 1 and calls[0].display_root == jobs.JOBS_DIR
    assert len(finals) == 1
    assert rc == jobs._EXIT_PUBLISHED_NOT_DURABLE == 5
    assert out.strip().splitlines() == [finals[0].stem]
    assert "PRIMARY completed unlock helper fault" in err
    assert "SECONDARY exact unlock disposition is UNKNOWN" in err
    assert failure.args == ("PRIMARY completed unlock helper fault",)


def test_public_register_renders_notes_through_withdrawal_wrapper(
        jobs, sleeper, monkeypatch, capsys):
    proc = sleeper()
    real_fsync = jobs._RegistryTxn.fsync
    primary = OSError(errno.EIO, "PRIMARY post-replace directory EIO")
    primary.add_note("SECONDARY directory descriptor close was UNKNOWN")
    calls = []

    def fail_first(txn):
        calls.append(txn)
        if len(calls) == 1:
            raise primary
        return real_fsync(txn)

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", fail_first)
    rc = jobs.main(["register", "--pid", str(proc.pid), "--purpose",
                    "noted withdraw", "--cmd", "sleep 60"])
    out, err = capsys.readouterr()
    assert len(calls) == 3
    assert all(txn.display_root == jobs.JOBS_DIR for txn in calls)
    assert rc == jobs._EXIT_REGISTRATION_FAILED
    assert out == "" and list(jobs.JOBS_DIR.glob("*.json")) == []
    assert "PRIMARY post-replace directory EIO" in err
    assert "SECONDARY directory descriptor close was UNKNOWN" in err
    assert primary.args == (errno.EIO, "PRIMARY post-replace directory EIO")


def test_public_reap_renders_notes_through_forget_wrapper(
        jobs, monkeypatch, capsys):
    entry = {"id": "host-303", "pid": 303, "starttime": 1,
             "purpose": "noted forget", "owned_paths": [], "log": None}
    primary = RuntimeError("PRIMARY forget helper failure")
    primary.add_note("SECONDARY exact cleanup disposition is UNKNOWN")
    monkeypatch.setattr(jobs, "load_all", lambda: jobs._RegistrySnapshot([entry]))
    monkeypatch.setattr(jobs, "proc_starttime", lambda _pid: None)
    monkeypatch.setattr(jobs, "forget", lambda candidate: (_ for _ in ()).throw(primary))
    rc = jobs.main(["reap"])
    out, err = capsys.readouterr()
    assert rc == 1 and "host-303" in err
    assert "PRIMARY forget helper failure" in err
    assert "SECONDARY exact cleanup disposition is UNKNOWN" in err
    assert "Traceback" not in out + err
    assert primary.args == ("PRIMARY forget helper failure",)


def test_forget_wrapper_does_not_fabricate_retention_after_partial_cleanup(
        jobs, monkeypatch):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    final = jobs.JOBS_DIR / "host-partial.json"
    final.write_text("{}", encoding="utf-8")
    entry = {"id": "host-partial", "_path": str(final),
             "owned_paths": [], "log": None}

    def remove_then_raise(candidate):
        final.unlink()
        raise RuntimeError("injected after final removal")

    monkeypatch.setattr(jobs, "forget", remove_then_raise)
    outcome = jobs._forget_or_retain(entry)

    assert not final.exists(), "partial-effect precondition did not fire"
    assert outcome["entry_removed"] is None
    assert outcome["entry_absent_durable"] is None
    assert outcome["cleanup_complete"] is False
    assert "disposition UNKNOWN" in "; ".join(outcome["notes"])


@pytest.mark.parametrize("fault", ("pre-read", "post-unlink"))
def test_forget_itself_totalizes_pre_and_post_effect_helper_faults(
        jobs, sleeper, monkeypatch, fault):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / (".bd-jobs-script-forget-%s.sh" % fault)
    owned.write_text("owned evidence\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "forget helper fault", "sleep 60",
        owned_paths=[str(owned)])
    final = pathlib.Path(entry["_path"])
    real_read = jobs._RegistryTxn.read_entry
    real_unlink = jobs._unlink_verified
    calls = []

    if fault == "pre-read":
        def read_then_raise(txn, component):
            if component == final.name:
                calls.append((txn, component))
                raise RuntimeError("injected pre-read helper fault")
            return real_read(txn, component)
        monkeypatch.setattr(jobs._RegistryTxn, "read_entry", read_then_raise)
    else:
        def unlink_then_raise(txn, component):
            result = real_unlink(txn, component)
            if component == final.name:
                calls.append((txn, component))
                raise RuntimeError("injected after final unlink")
            return result
        monkeypatch.setattr(jobs, "_unlink_verified", unlink_then_raise)

    outcome = jobs.forget(entry)

    assert len(calls) == 1
    assert calls[0][0].display_root == jobs.JOBS_DIR
    assert calls[0][1] == final.name
    assert final.exists() is (fault == "pre-read")
    assert owned.exists()
    assert outcome["entry_removed"] is None
    assert outcome["entry_absent_durable"] is None
    assert outcome["log_removed"] is None
    assert outcome["owned_removed"] == [None]
    assert outcome["cleanup_complete"] is False
    notes = "; ".join(outcome["notes"])
    assert "disposition UNKNOWN" in notes
    assert str(final) in notes and str(owned) in notes


def test_ready_close_fault_stays_gated_until_the_parent_releases(
        jobs, monkeypatch, capsys):
    release_r, release_w = os.pipe()
    ready_r, ready_w = os.pipe()
    status_r, status_w = os.pipe()
    real_close = os.close
    real_write = os.write
    close_faults = []
    execs = []
    gate_results = []

    def fail_after_ready_close(fd):
        real_close(fd)
        if fd == ready_w and not close_faults:
            close_faults.append(fd)
            raise OSError(5, "injected ready-fd close EIO")

    monkeypatch.setattr(jobs.os, "close", fail_after_ready_close)
    monkeypatch.setattr(
        jobs.os, "execvp",
        lambda file, argv: execs.append((file, list(argv))),
    )

    gate = threading.Thread(
        target=lambda: gate_results.append(jobs._gate_main([
            str(release_r), str(ready_w), str(status_w), "--", "echo", "released",
        ])),
        daemon=True,
    )
    launch = jobs._LocalLaunch("ready-close-fault")
    launch.ready_r = ready_r
    launch.proc = type("ProcessProbe", (), {"poll": lambda self: None})()
    gate.start()
    try:
        launch.await_ready(timeout=1.0)

        assert _wait_until(lambda: close_faults == [ready_w], 1.0), (
            "the wrapper's close-fault outcome did not become observable: %r"
            % close_faults)
        assert gate.is_alive(), (
            "the wrapper reported READY but abandoned the release wait after "
            "its ready-descriptor close fault")

        assert real_write(release_w, jobs._RELEASE_BYTE) == 1
        gate.join(timeout=2.0)
        out, err = capsys.readouterr()

        assert not gate.is_alive()
        assert gate_results == [None]
        assert execs == [("echo", ["echo", "released"])]
        assert out == ""
        assert "ready" in err.lower() and "continuing after release" in err
    finally:
        if gate.is_alive():
            try:
                real_write(release_w, jobs._RELEASE_BYTE)
            except OSError:
                pass
            gate.join(timeout=2.0)
        for fd in (release_w, release_r, ready_w, ready_r, status_w, status_r):
            try:
                real_close(fd)
            except OSError:
                pass


def test_raw_ready_epipe_is_proved_not_delivered(jobs):
    ready_r, ready_w = os.pipe()
    os.close(ready_r)
    try:
        state, detail = jobs._write_ready_byte_raw(ready_w)
        assert state == jobs._READY_PROVED_NOT_DELIVERED
        assert isinstance(detail, BrokenPipeError) and detail.errno == errno.EPIPE
    finally:
        os.close(ready_w)


def test_ready_post_effect_wrapper_error_remains_gated_until_release(
        jobs, monkeypatch):
    release_r, release_w = os.pipe()
    ready_r, ready_w = os.pipe()
    status_r, status_w = os.pipe()
    real_write = os.write
    calls = []
    execs = []
    results = []

    def write_then_raise(fd):
        calls.append(fd)
        assert real_write(fd, jobs._READY_BYTE) == 1
        raise OSError(errno.EIO, "injected wrapper after ready byte")

    monkeypatch.setattr(jobs, "_write_ready_byte_raw", write_then_raise,
                        raising=False)
    monkeypatch.setattr(
        jobs.os, "execvp", lambda file, argv: execs.append((file, list(argv))))
    gate = threading.Thread(
        target=lambda: results.append(jobs._gate_main([
            str(release_r), str(ready_w), str(status_w), "--", "echo", "ok",
        ])),
        daemon=True,
    )
    gate.start()
    try:
        assert os.read(ready_r, 2) == jobs._READY_BYTE
        assert gate.is_alive() and execs == []
        assert real_write(release_w, jobs._RELEASE_BYTE) == 1
        gate.join(timeout=2.0)
        assert not gate.is_alive()
        assert calls == [ready_w]
        assert execs == [("echo", ["echo", "ok"])]
        assert results == [None]
    finally:
        for fd in (release_r, release_w, ready_r, ready_w, status_r, status_w):
            try:
                os.close(fd)
            except OSError:
                pass
        gate.join(timeout=2.0)


def test_await_ready_rejects_a_terminal_wrapper_after_a_complete_record(jobs):
    ready_r, ready_w = os.pipe()
    real_close = os.close
    os.write(ready_w, jobs._READY_BYTE)
    real_close(ready_w)
    launch = jobs._LocalLaunch("terminal-ready-wrapper")
    launch.ready_r = ready_r
    launch.proc = type("ProcessProbe", (), {"poll": lambda self: 75})()
    try:
        with pytest.raises(jobs._LaunchAborted) as raised:
            launch.await_ready(timeout=1.0)

        assert raised.value.status == jobs._EXIT_REGISTRATION_FAILED
        assert "exited after reporting ready" in raised.value.message
        assert "75" in raised.value.message
    finally:
        try:
            real_close(ready_r)
        except OSError:
            pass


def test_await_ready_rejects_a_ready_byte_without_terminal_eof(jobs):
    ready_r, ready_w = os.pipe()
    real_close = os.close
    os.write(ready_w, jobs._READY_BYTE)
    launch = jobs._LocalLaunch("unterminated-ready-wrapper")
    launch.ready_r = ready_r
    launch.proc = type("ProcessProbe", (), {"poll": lambda self: None})()
    started = time.monotonic()
    try:
        with pytest.raises(jobs._LaunchAborted) as raised:
            launch.await_ready(timeout=0.1)

        assert raised.value.status == jobs._EXIT_REGISTRATION_FAILED
        assert "did not finalize the ready channel" in raised.value.message
        assert 0.08 <= time.monotonic() - started < 2.0
    finally:
        for fd in (ready_w, ready_r):
            try:
                real_close(fd)
            except OSError:
                pass


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

    The descriptor-relative rename has already made a complete, valid entry
    visible when the directory fsync runs. If that fsync fails, the entry
    EXISTS. Reporting
    "nothing was registered" and deleting the log it names is not a rollback --
    it leaves a live registry record pointing at a file that was just removed,
    and tells the operator the opposite of what is on disk.

    When the entry cannot be provably withdrawn it must be RETAINED and NAMED,
    its log kept because the retained entry refers to it, the user work never
    released, and the status must be distinguishable from "nothing published".
    """
    marker = tmp_path / "must-never-exist.marker"
    calls = []

    def failing_dir_fsync(txn):
        calls.append(txn)
        raise OSError(5, "injected EIO on the registry directory")

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", failing_dir_fsync)
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    try:
        rc = jobs.cmd_run(_run_args("post-replace", _marker_command(marker)))
        out, err = capsys.readouterr()

        assert len(calls) >= 1, "the directory-fsync seam never fired"
        assert all(txn.display_root == jobs.JOBS_DIR for txn in calls)
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
    real_fsync = jobs._RegistryTxn.fsync
    calls = []

    def failing_once(txn):
        calls.append(txn)
        if len(calls) == 1:
            raise OSError(5, "injected EIO on the publication fsync")
        return real_fsync(txn)

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", failing_once)
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    try:
        rc = jobs.cmd_run(_run_args("rollback", _marker_command(marker)))
        out, err = capsys.readouterr()

        assert len(calls) >= 2, (
            "the launcher never attempted to make its rollback durable "
            "(%d directory fsync call(s)): an unlink that is not itself "
            "fsynced can come back" % len(calls))
        assert all(txn.display_root == jobs.JOBS_DIR for txn in calls)
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
    directory_fsyncs = []
    monkeypatch.setattr(
        jobs._RegistryTxn, "fsync",
        lambda txn: directory_fsyncs.append(txn))
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
    assert directory_fsyncs == [], (
        "a pre-rename failure reached the directory-fsync seam: %r"
        % directory_fsyncs)
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

        def raiser(purpose, **kwargs):
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

    def watching_open(purpose, **kwargs):
        path, fd = real_open_job_log(purpose, **kwargs)
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
    """An unlock-after-effect fault must not release locally gated work.

    The historical node name is retained for the durable mutation-band
    identity.  R12 separates descriptor close from flock disposition: even
    when the real ``LOCK_UN`` completed before the injected exception, the
    caller cannot infer that fact and must withhold the release byte.
    """
    marker = tmp_path / "registry-unlock-unknown-must-not-run.marker"
    exclusives = []
    unlocks = []
    real_flock = jobs.fcntl.flock

    def unlock_after_effect_then_raise(fd, operation):
        if operation & jobs.fcntl.LOCK_EX:
            exclusives.append(fd)
        if operation == jobs.fcntl.LOCK_UN and not unlocks:
            finals = list(jobs.JOBS_DIR.glob("*.json"))
            assert len(finals) == 1, (
                "unlock fault fired before one final was published: %r"
                % finals)
            json.loads(finals[0].read_text(encoding="utf-8"))
            unlocks.append(fd)
            real_flock(fd, operation)
            raise OSError(5, "injected post-effect registry unlock EIO")
        return real_flock(fd, operation)

    monkeypatch.setattr(jobs.fcntl, "flock", unlock_after_effect_then_raise)
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    entry = None
    try:
        rc = jobs.cmd_run(_run_args(
            "registry-unlock-unknown", _marker_command(marker)))
        out, err = capsys.readouterr()

        assert len(exclusives) == 1 and unlocks == exclusives, (
            "the exact post-publication unlock seam did not fire once: "
            "locks=%r unlocks=%r argvs=%r"
            % (exclusives, unlocks, argvs))
        assert rc == jobs._EXIT_PUBLISHED_NOT_DURABLE, (
            "an unknown flock disposition authorized local release "
            "(rc=%r, stderr=%r)" % (rc, err))
        entries = jobs.load_all()
        assert len(entries) == 1, entries
        entry = entries[0]
        time.sleep(0.05)
        assert not marker.exists(), (
            "work ran after publication flock release became UNKNOWN")
        assert not jobs.alive(entry), (
            "the unreleased gate wrapper survived abort cleanup")
        assert pathlib.Path(entry["log"]).exists(), (
            "abort cleanup deleted the log owned by the durable record")
        assert "LOCK RELEASE" in err and "never released" in err, err
        assert entry["id"] in err, (
            "the lock-UNKNOWN diagnostic omitted its reconciliation id: %r"
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
    """Historical node id: lock-unknown is status 5; close-only stays 7."""
    proc = sleeper()
    exclusives = []
    unlocks = []
    real_flock = jobs.fcntl.flock

    def unlock_after_effect_then_raise(fd, operation):
        if operation & jobs.fcntl.LOCK_EX:
            exclusives.append(fd)
        if operation == jobs.fcntl.LOCK_UN and not unlocks:
            finals = list(jobs.JOBS_DIR.glob("*.json"))
            assert len(finals) == 1
            unlocks.append(fd)
            real_flock(fd, operation)
            raise OSError(5, "injected direct-register unlock EIO")
        return real_flock(fd, operation)

    monkeypatch.setattr(jobs.fcntl, "flock", unlock_after_effect_then_raise)
    args = type("A", (), {
        "pid": proc.pid, "purpose": "direct unlock unknown",
        "cmd": "sleep 60", "origin": "local", "request_id": None,
    })()

    try:
        rc = jobs.cmd_register(args)
    except BaseException as exc:  # noqa: BLE001 - the historical defect
        rc = ("RAISED", type(exc).__name__, str(exc))
    out, err = capsys.readouterr()

    assert len(exclusives) == 1 and unlocks == exclusives, (
        "the direct-register unlock seam did not fire exactly once: "
        "locks=%r unlocks=%r" % (exclusives, unlocks))
    entries = jobs.load_all()
    assert len(entries) == 1, entries
    entry = entries[0]
    assert rc == 5 == jobs._EXIT_PUBLISHED_NOT_DURABLE, (
        "the direct CLI did not preserve lock-unknown as unaccounted publish: "
        "rc=%r stderr=%r" % (rc, err))
    assert "PUBLISHED" in err and "LOCK RELEASE UNKNOWN" in err, err
    assert entry["id"] in err, err
    assert out.strip().splitlines() == [entry["id"]], out

    # The owned transaction was closed before cmd_register structured the
    # result, so an independent transaction can acquire the same flock.
    probe = jobs._RegistryTxn(jobs.JOBS_DIR, require_write=True, create=False)
    assert probe.open()
    try:
        probe.lock()
        probe.unlock()
    finally:
        probe.close()


def test_direct_register_owned_close_after_publication_is_status_7(
        jobs, sleeper, monkeypatch, capsys):
    """An owned post-effect close fault preserves the durable CLI result."""
    proc = sleeper()
    entry_id = "%s-%d" % (jobs.socket.gethostname(), proc.pid)
    final = jobs.JOBS_DIR / (entry_id + ".json")
    failure = OSError(
        errno.EIO, "injected owned close after durable publication")
    real_close = jobs._RegistryTxn.close
    fired = []

    def close_after_effect_then_raise(txn, primary=None):
        candidates = list(jobs.JOBS_DIR.glob("*.json"))
        inject = False
        if primary is None and not fired and candidates == [final]:
            parsed = json.loads(final.read_text(encoding="utf-8"))
            inject = jobs._entry_schema_reason(
                parsed, filename=final.name) is None
        assigned = (txn.dir_fd, txn.parent_fd)
        result = real_close(txn, primary=primary)
        if inject:
            fired.append((txn, assigned))
            raise failure
        return result

    monkeypatch.setattr(
        jobs._RegistryTxn, "close", close_after_effect_then_raise)
    args = type("A", (), {
        "pid": proc.pid, "purpose": "owned close after publication",
        "cmd": "sleep 60", "origin": "local", "request_id": None,
    })()

    try:
        rc = jobs.cmd_register(args)
    except BaseException as exc:  # noqa: BLE001 - N57 must be assertion RED
        rc = ("RAISED", type(exc).__name__, str(exc))
    out, err = capsys.readouterr()

    assert len(fired) == 1, (
        "the owned post-publication close seam did not fire exactly once: %r"
        % (fired,))
    closed_txn, assigned = fired[0]
    assert all(fd is not None for fd in assigned), assigned
    assert closed_txn.dir_fd is None and closed_txn.parent_fd is None
    assert not closed_txn.locked and not closed_txn.unlock_unknown
    assert all(not pathlib.Path("/proc/self/fd/%d" % fd).exists()
               for fd in assigned), assigned
    assert rc == jobs._EXIT_CLOSE_UNKNOWN, (
        "the direct CLI did not structure the durable owned-close fault: "
        "rc=%r stderr=%r" % (rc, err))
    assert out == entry_id + "\n", out

    finals = list(jobs.JOBS_DIR.glob("*.json"))
    assert finals == [final], finals
    entry = json.loads(final.read_text(encoding="utf-8"))
    assert jobs._entry_schema_reason(entry, filename=final.name) is None
    assert entry["id"] == entry_id and entry["pid"] == proc.pid
    assert "PUBLISHED BUT REGISTRY LOCK CLOSE UNKNOWN" in err, err
    assert entry_id in err and str(final) in err, err
    assert "durable" in err and failure.args[1] in err, err

    # The real owned close completed before the injected error, so neither a
    # descriptor nor the registry flock may leak into the caller.
    probe = jobs._RegistryTxn(jobs.JOBS_DIR, require_write=True, create=False)
    assert probe.open()
    try:
        probe.lock()
        probe.unlock()
    finally:
        probe.close()
    assert probe.dir_fd is None and probe.parent_fd is None


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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    adopted.write_text("touch %s; sleep 45\n" % marker, encoding="utf-8")
    args = type("A", (), {
        "host": "local", "purpose": "combined post-replace faults",
        "command": [], "script": str(adopted),
        "adopt_script": str(adopted), "origin": None,
        "request_id": "combined-post-replace-request",
        "attempt_token": "a" * 32,
    })()
    transactions = []
    events = []
    unlock_faults = []
    real_rename = jobs._RegistryTxn.rename_name
    real_unlock = jobs._RegistryTxn.unlock
    real_await_ready = jobs._LocalLaunch.await_ready

    def tracking_ready(launch, timeout=None):
        result = real_await_ready(launch, timeout=timeout)
        events.append("ready")
        return result

    def tracking_rename(txn, src, dst):
        events.append("replace")
        transactions.append(txn)
        assert txn.locked
        return real_rename(txn, src, dst)

    def fail_dir_fsync(txn):
        events.append("dir-fsync")
        transactions.append(txn)
        assert txn.locked
        raise OSError(5, "PRIMARY injected registry directory fsync EIO")

    def fail_registry_unlock(txn, primary=None):
        events.append("unlock")
        transactions.append(txn)
        real_unlock(txn, primary=primary)
        unlock_faults.append(txn)
        raise OSError(9, "SECONDARY injected registry unlock EBADF")

    monkeypatch.setattr(jobs._LocalLaunch, "await_ready", tracking_ready)
    monkeypatch.setattr(jobs._RegistryTxn, "rename_name", tracking_rename)
    monkeypatch.setattr(jobs._RegistryTxn, "fsync", fail_dir_fsync)
    monkeypatch.setattr(jobs._RegistryTxn, "unlock", fail_registry_unlock)
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    try:
        rc = jobs.cmd_run(args)
        out, err = capsys.readouterr()

        assert events == [
            "ready", "replace", "dir-fsync", "dir-fsync", "unlock"], (
            "the combined fault missed READY/replace or the retained second "
            "durability probe: %r; argv=%r" % (events, argvs))
        assert len({id(txn) for txn in transactions}) == 1
        assert unlock_faults == [transactions[0]], (
            "the secondary exact registry unlock did not fire once: %r %r"
            % (transactions, unlock_faults))
        finals = list(jobs.JOBS_DIR.glob("*.json"))
        assert len(finals) == 1, finals
        entry = json.loads(finals[0].read_text(encoding="utf-8"))
        assert rc == jobs._EXIT_PUBLISHED_NOT_DURABLE, (rc, out, err)
        assert out.strip().splitlines() == [entry["id"]], out
        assert "PRIMARY injected registry directory fsync EIO" in err, err
        assert "SECONDARY injected registry unlock EBADF" in err, err
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

        real_unlock = jobs._RegistryTxn.unlock
        unlocks = []

        def fail_first_unlock(txn, primary=None):
            unlocks.append(txn)
            real_unlock(txn, primary=primary)
            if len(unlocks) == 1:
                raise OSError(5, "injected first forget unlock EIO")

        monkeypatch.setattr(jobs._RegistryTxn, "unlock", fail_first_unlock)

        rc = jobs.cmd_reap(type("A", (), {"id": None})())
        out, err = capsys.readouterr()

        assert len(unlocks) == 2, (
            "reap stopped before the sibling after unlock uncertainty: %r"
            % unlocks)
        assert all(txn.display_root == jobs.JOBS_DIR for txn in unlocks)
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

    def watching_open(purpose, **kwargs):
        path, fd = real_open_job_log(purpose, **kwargs)
        recorded["path"] = path
        return path, fd

    refused = []

    def refusing_owned_unlink(txn, component, observed):
        path = txn.display_path(component)
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    log = jobs.JOBS_DIR / "release-failure.log"
    log.write_text("held command output\n", encoding="utf-8")
    adopted = jobs.JOBS_DIR / ".bd-jobs-script-release-failure.sh"
    adopted.write_text("echo never-ran\n", encoding="utf-8")
    host, pid = jobs.socket.gethostname(), os.getpid()
    entry = {
        "id": "%s-%d" % (host, pid),
        "pid": pid,
        "starttime": jobs.proc_starttime(pid),
        "purpose": "release failure",
        "cmd": "echo never-ran",
        "host": host,
        "origin": host,
        "started_at": "2026-08-21T00:00:00Z",
        "log": str(log),
        "log_owned": True,
        "owned_paths": [str(adopted)],
    }
    launch = jobs._LocalLaunch("release failure", adopt_path=str(adopted))
    launch.log_path = str(log)
    _bind_manual_launch_registry(jobs, launch)
    launch.entry = jobs._publish_entry(
        launch.registry_txn, entry).entry
    final = pathlib.Path(launch.entry["_path"])
    launch.release_w = os.open("/dev/null", os.O_WRONLY)
    return launch, final, log, adopted


def test_release_failure_reports_the_cleanup_for_the_files_it_actually_removed(
        jobs, monkeypatch, tmp_path):
    """A successful withdrawal must not be described as a retained record."""
    launch, final, log, adopted = _release_failure_transaction(jobs, tmp_path)

    try:
        with monkeypatch.context() as mp:
            mp.setattr(jobs.os, "write", lambda *_: (_ for _ in ()).throw(
                OSError(32, "injected EPIPE releasing the gate")))
            with pytest.raises(jobs._LaunchAborted) as excinfo:
                launch.release()
    finally:
        launch.close_idle()

    assert excinfo.value.status == jobs._EXIT_RELEASE_PROVED_NOT_DELIVERED
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
    real_unlink = jobs._RegistryTxn.unlink_name
    attempts = []

    def refuse_final(txn, component):
        if component == final.name:
            attempts.append(component)
            raise PermissionError("injected retained final")
        return real_unlink(txn, component)

    try:
        with monkeypatch.context() as mp:
            mp.setattr(jobs.os, "write", lambda *_: (_ for _ in ()).throw(
                OSError(32, "injected EPIPE releasing the gate")))
            mp.setattr(jobs._RegistryTxn, "unlink_name", refuse_final)
            with pytest.raises(jobs._LaunchAborted) as excinfo:
                launch.release()
    finally:
        launch.close_idle()

    assert excinfo.value.status == jobs._EXIT_RELEASE_PROVED_NOT_DELIVERED
    assert attempts == [final.name], "the retained-final fault did not fire once"
    assert final.exists(), "precondition: the injected final unlink must fail"
    assert log.exists() and adopted.exists(), (
        "forget deleted files still named by the retained record: log=%s script=%s"
        % (log.exists(), adopted.exists()))
    assert "COULD NOT remove" in excinfo.value.message, excinfo.value.message
    assert all(str(path) in excinfo.value.message for path in (final, log, adopted)), (
        "the retained-state diagnostic did not name every path: %r"
        % excinfo.value.message)


def test_release_failure_contains_forget_exception_as_retained_state(
        jobs, monkeypatch, tmp_path):
    launch, final, log, adopted = _release_failure_transaction(jobs, tmp_path)

    monkeypatch.setattr(jobs.os, "write", lambda *_: (_ for _ in ()).throw(
        OSError(32, "PRIMARY injected release EPIPE")))
    monkeypatch.setattr(jobs, "forget", lambda *_: (_ for _ in ()).throw(
        RuntimeError("SECONDARY injected forget failure")))

    try:
        with pytest.raises(jobs._LaunchAborted) as excinfo:
            launch.release()
    finally:
        launch.close_idle()

    assert excinfo.value.status == jobs._EXIT_RELEASE_PROVED_NOT_DELIVERED
    assert final.exists() and log.exists() and adopted.exists()
    message = excinfo.value.message
    assert "PRIMARY injected release EPIPE" in message
    assert "SECONDARY injected forget failure" in message
    assert launch.entry["id"] in message
    assert all(str(path) in message for path in (final, log, adopted)), message


@pytest.mark.parametrize("retained", (False, True))
def test_status_8_cli_emits_exact_id_last_only_when_the_live_record_survives(
        jobs, monkeypatch, tmp_path, retained):
    marker = tmp_path / "status-8-user-work-must-not-run"
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    monkeypatch.setattr(
        jobs, "_write_release_byte_raw",
        lambda _fd: (jobs._RELEASE_PROVED_NOT_DELIVERED,
                     BrokenPipeError(errno.EPIPE, "injected release EPIPE")))
    if retained:
        monkeypatch.setattr(
            jobs, "_forget_or_retain",
            lambda entry: {
                "entry_removed": False,
                "entry_absent_durable": False,
                "log_removed": False,
                "cleanup_complete": False,
                "owned_removed": [],
                "notes": ["injected retained final %s" % entry["id"]],
            })

    merged = io.StringIO()
    with contextlib.redirect_stdout(merged), contextlib.redirect_stderr(merged):
        rc = jobs.cmd_run(_run_args(
            "status-8-%s" % ("retained" if retained else "removed"),
            _marker_command(marker)))
    lines = [line for line in merged.getvalue().splitlines() if line]
    rows = jobs.load_all()
    try:
        assert len(launched) == 1 and len(argvs) == 1
        exact_id = "%s-%d" % (jobs.socket.gethostname(), launched[0])
        assert rc == 8
        assert not marker.exists(), "release-proved-failed work executed"
        assert (len(rows) == 1) is retained, rows
        assert (lines[-1] == exact_id) is retained, lines
        assert lines.count(exact_id) == (1 if retained else 0), lines
        assert "release was proved not delivered" in merged.getvalue()
    finally:
        for pid in launched:
            _reap_process(pid)
        for entry in rows:
            jobs.forget(entry)


def test_status_9_cli_is_numeric_and_keeps_the_exact_id_last(
        jobs, monkeypatch, tmp_path):
    marker = tmp_path / "status-9-user-work-must-not-run"
    launched, argvs = [], []
    _watch_popen(jobs, monkeypatch, launched, argvs)
    monkeypatch.setattr(
        jobs, "_write_release_byte_raw",
        lambda _fd: (jobs._RELEASE_UNKNOWN,
                     OSError(errno.EIO, "injected release delivery unknown")))
    merged = io.StringIO()
    with contextlib.redirect_stdout(merged), contextlib.redirect_stderr(merged):
        rc = jobs.cmd_run(_run_args("status-9", _marker_command(marker)))
    lines = [line for line in merged.getvalue().splitlines() if line]
    rows = jobs.load_all()
    try:
        assert len(launched) == 1 and len(argvs) == 1 and len(rows) == 1
        assert rc == 9
        assert lines[-1] == rows[0]["id"] and lines.count(rows[0]["id"]) == 1
        assert "release delivery is UNKNOWN" in merged.getvalue()
        assert not marker.exists(), "release-unknown work executed"
    finally:
        for pid in launched:
            _reap_process(pid)
        for entry in rows:
            jobs.forget(entry)


def test_reap_contains_one_forget_exception_and_continues_the_sibling(
        jobs, monkeypatch, capsys):
    entries = jobs._RegistrySnapshot([
        {"id": "host-100", "pid": 100, "starttime": 1,
         "purpose": "first", "owned_paths": [], "log": None},
        {"id": "host-200", "pid": 200, "starttime": 1,
         "purpose": "second", "owned_paths": [], "log": None},
    ])
    calls = []

    monkeypatch.setattr(jobs, "load_all", lambda: entries)
    monkeypatch.setattr(jobs, "proc_starttime", lambda _pid: None)

    def forget(entry):
        calls.append(entry["id"])
        if entry["id"] == "host-100":
            raise RuntimeError("injected first cleanup failure")
        return {"cleanup_complete": True, "notes": ["second removed"]}

    monkeypatch.setattr(jobs, "forget", forget)

    rc = jobs.cmd_reap(type("A", (), {"id": None})())
    out, err = capsys.readouterr()

    assert rc == 1
    assert calls == ["host-100", "host-200"]
    assert "host-100" in err and "injected first cleanup failure" in err
    assert "1 refused" in out
    assert "Traceback" not in out + err


@pytest.mark.parametrize("branch", ("dead", "recycled", "post-kill"))
@pytest.mark.parametrize("kind", ("keyboard", "system-exit"))
def test_reap_cancellation_stops_before_the_next_entry_unchanged(
        jobs, monkeypatch, branch, kind):
    entries = jobs._RegistrySnapshot([
        {"id": "host-first", "pid": 101, "starttime": 1,
         "purpose": "first", "owned_paths": [], "log": None},
        {"id": "host-second", "pid": 202, "starttime": 1,
         "purpose": "second", "owned_paths": [], "log": None},
    ])
    primary = (KeyboardInterrupt("stop reap")
               if kind == "keyboard" else SystemExit(83))
    inspected, forgotten, signals = [], [], []
    monkeypatch.setattr(jobs, "load_all", lambda: entries)

    def identity_bound_reap(entry):
        inspected.append(entry["pid"])
        if branch in ("dead", "recycled"):
            return {"state": "STALE", "notes": [branch]}
        signals.append(entry["pid"])
        return {"state": "REAPED", "scope": "injected exact identity",
                "notes": []}

    def cancel_forget(entry):
        forgotten.append(entry["id"])
        raise primary

    monkeypatch.setattr(jobs, "_identity_bound_reap", identity_bound_reap)
    monkeypatch.setattr(jobs, "_forget_or_retain", cancel_forget)
    with pytest.raises(BaseException) as raised:
        jobs.cmd_reap(type("A", (), {"id": None})())

    assert raised.value is primary
    assert primary.args == (("stop reap",) if kind == "keyboard" else (83,))
    assert getattr(primary, "code", None) == (83 if kind == "system-exit" else None)
    assert inspected == [101] and forgotten == ["host-first"]
    assert signals == ([101] if branch == "post-kill" else [])


def test_forget_continues_independent_owned_cleanup_after_helper_exception(
        jobs, sleeper, monkeypatch, capsys):
    proc = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    first = jobs.JOBS_DIR / ".bd-jobs-script-first.sh"
    second = jobs.JOBS_DIR / ".bd-jobs-script-second.sh"
    log = jobs.JOBS_DIR / "owned-cleanup.log"
    for path in (first, second, log):
        path.write_text("owned\n", encoding="utf-8")
    entry = jobs.register(
        proc.pid, "independent cleanup", "sleep 60", log=str(log),
        log_owned=True, owned_paths=[str(first), str(second)])
    final = pathlib.Path(entry["_path"])
    _reap_process(proc.pid)
    real_unlink_owned = jobs._unlink_owned_identity
    calls = []

    def fail_first(txn, component, observed):
        path = txn.display_path(component)
        calls.append(str(path))
        if path == first:
            raise RuntimeError("injected first owned cleanup failure")
        return real_unlink_owned(txn, component, observed)

    monkeypatch.setattr(jobs, "_unlink_owned_identity", fail_first)

    rc = jobs.cmd_reap(type("A", (), {"id": None})())
    out, err = capsys.readouterr()

    assert rc == 1
    assert not final.exists()
    assert first.exists()
    assert not second.exists() and not log.exists()
    assert calls == [str(first), str(second), str(log)]
    assert "injected first owned cleanup failure" in err
    assert "1 refused" in out and "Traceback" not in out + err


@pytest.mark.parametrize("failed_step", ("terminate", "log", "adopt"))
def test_abort_contains_each_cleanup_helper_failure_and_attempts_later_steps(
        jobs, monkeypatch, tmp_path, failed_step):
    launch = jobs._LocalLaunch(
        "helper containment", adopt_path=str(tmp_path / "adopted.sh"))
    launch.pid = 424242
    launch.log_path = str(tmp_path / "launch.log")
    launch.log_identity = ("present", 1, 2, stat.S_IFREG)
    calls = []

    def step(name, result):
        def run(*_args, **_kwargs):
            calls.append(name)
            if name == failed_step:
                raise RuntimeError("injected %s helper failure" % name)
            return result
        return run

    monkeypatch.setattr(
        jobs, "_terminate_and_wait", step("terminate", "terminated child"))
    monkeypatch.setattr(
        jobs, "_discard_owned_log", step("log", "removed log"))
    monkeypatch.setattr(
        jobs, "_cleanup_attempt_identity", step("adopt", (True, "removed adopt")))

    with pytest.raises(jobs._LaunchAborted) as excinfo:
        launch.abort(jobs._EXIT_REGISTRATION_FAILED, "primary refusal")

    assert calls == ["terminate", "log", "adopt"]
    assert excinfo.value.status == jobs._EXIT_REGISTRATION_FAILED
    assert "primary refusal" in excinfo.value.message
    assert "injected %s helper failure" % failed_step in excinfo.value.message
    assert "UNKNOWN" in excinfo.value.message


def test_abort_preserves_computed_status_while_finishing_cancelled_cleanup(
        jobs, monkeypatch, tmp_path):
    jobs.JOBS_DIR.mkdir(mode=0o700)
    launch = jobs._LocalLaunch(
        "cancelled cleanup",
        adopt_path=str(jobs.JOBS_DIR / "adopted.sh"))
    launch.pid = 424243
    launch.log_path = str(jobs.JOBS_DIR / "launch.log")
    pathlib.Path(launch.adopt_path).write_text("adopted", encoding="utf-8")
    pathlib.Path(launch.log_path).write_text("log", encoding="utf-8")
    _bind_manual_launch_registry(jobs, launch)
    cancellation = KeyboardInterrupt("operator cancellation")
    calls = []

    def interrupted(_pid):
        calls.append("terminate")
        raise cancellation

    monkeypatch.setattr(jobs, "_terminate_and_wait", interrupted)
    monkeypatch.setattr(
        jobs, "_discard_owned_log",
        lambda *_args, **_kwargs: calls.append("log") or "removed log")
    monkeypatch.setattr(
        jobs, "_cleanup_attempt_identity",
        lambda *_args, **_kwargs: calls.append("adopt") or
        (True, "removed adopt"))

    try:
        with pytest.raises(jobs._LaunchAborted) as excinfo:
            launch.abort(jobs._EXIT_REGISTRATION_FAILED, "superseded refusal")
    finally:
        launch.close_idle()

    assert excinfo.value.status == jobs._EXIT_REGISTRATION_FAILED == 3
    assert calls == ["terminate", "log", "adopt"]
    assert "child process cleanup was interrupted" in excinfo.value.message
    assert "operator cancellation" in excinfo.value.message
    assert "removed log" in excinfo.value.message
    assert "removed adopt" in excinfo.value.message


@pytest.mark.parametrize("failed_helper", ("durability", "unlink"))
def test_withdrawal_contains_unexpected_helper_failure_as_retained(
        jobs, monkeypatch, failed_helper):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    final = jobs.JOBS_DIR / "host-300.json"
    final.write_text("{}", encoding="utf-8")
    owned = jobs.JOBS_DIR / ".bd-jobs-script-owned.sh"
    entry = {"id": "host-300", "log": str(jobs.JOBS_DIR / "owned.log"),
             "owned_paths": [str(owned)]}
    fired = []

    if failed_helper == "durability":
        def fail_fsync(txn):
            fired.append((failed_helper, txn))
            raise RuntimeError("injected durability helper failure")
        monkeypatch.setattr(jobs._RegistryTxn, "fsync", fail_fsync)
    else:
        def fail_unlink(txn, component):
            fired.append((failed_helper, txn, component))
            raise RuntimeError("injected unlink helper failure")
        monkeypatch.setattr(jobs, "_unlink_verified", fail_unlink)

    with _open_test_registry_txn(jobs, lock=True) as txn:
        outcome = jobs._withdraw_or_retain(
            txn, final.name, entry,
            RuntimeError("primary publication failure"))

    assert len(fired) == 1
    assert fired[0][1] is txn
    if failed_helper == "unlink":
        assert fired[0][2] == final.name
    assert isinstance(outcome, jobs.PublishedNotDurable)
    assert final.exists()
    assert "primary publication failure" in str(outcome)
    assert "injected %s helper failure" % failed_helper in str(outcome)
    assert all(path in str(outcome) for path in (
        str(final), str(entry["log"]), str(owned)))


def test_attempt_cleanup_contains_unexpected_quarantine_helper_failure(
        jobs, monkeypatch):
    path = jobs.JOBS_DIR / ".bd-jobs-script-cleanup.sh"
    observed = ("present", 1, 2, stat.S_IFREG)
    monkeypatch.setattr(jobs, "_unlink_owned_identity", lambda *_: (
        _ for _ in ()).throw(RuntimeError("injected quarantine helper failure")))

    removed, note = jobs._cleanup_attempt_identity(path, observed)

    assert removed is False
    assert str(path) in note
    assert "injected quarantine helper failure" in note
    assert "UNKNOWN" in note


@pytest.mark.parametrize(
    "fault", ("unlink-helper", "failed-unlink-observation",
              "successful-unlink-observation"))
def test_unlink_verified_totalizes_helper_and_observation_faults(
        jobs, monkeypatch, tmp_path, fault):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = jobs.JOBS_DIR / (fault + ".txt")
    path.write_text("owned\n", encoding="utf-8")
    real_unlink = jobs._RegistryTxn.unlink_name
    fired = []

    def faulting_unlink(txn, component):
        fired.append(("unlink", txn, component))
        if fault == "unlink-helper":
            raise RuntimeError("injected unlink helper failure")
        if fault == "failed-unlink-observation":
            raise PermissionError("injected unlink refusal")
        return real_unlink(txn, component)

    def unknown_observation(txn, component):
        fired.append(("observe", txn, component))
        return jobs._NameReceipt(
            jobs._OBS_UNKNOWN, component, None, "stat",
            "injected %s observation failure" % (
                "failed-unlink" if fault == "failed-unlink-observation"
                else "success"))

    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", faulting_unlink)
    if fault != "unlink-helper":
        monkeypatch.setattr(
            jobs._RegistryTxn, "observe_name", unknown_observation)

    with _open_test_registry_txn(jobs) as txn:
        removed, note = jobs._unlink_verified(txn, path.name)

    expected = ["unlink"] if fault == "unlink-helper" else ["unlink", "observe"]
    assert [event[0] for event in fired] == expected
    assert all(event[1] is txn and event[2] == path.name for event in fired)
    assert removed is False
    assert str(path) in note
    assert "UNKNOWN" in note or "COULD NOT" in note
    assert "injected" in note


def test_discard_owned_log_totalizes_identity_helper_failure(
        jobs, monkeypatch):
    log = jobs.JOBS_DIR / "owned-helper-failure.log"
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    log.write_text("owned\n", encoding="utf-8")
    monkeypatch.setattr(jobs, "_unlink_owned_identity", lambda *_: (
        _ for _ in ()).throw(RuntimeError("injected owned-log helper failure")))

    note = jobs._discard_owned_log(
        log, observed=("present", 1, 2, stat.S_IFREG))

    assert str(log) in note and "UNKNOWN" in note
    assert "injected owned-log helper failure" in note
    assert log.exists()


@pytest.mark.parametrize("removed", (False, True))
def test_discard_owned_log_makes_every_namespace_change_durable(
        jobs, monkeypatch, removed):
    log = jobs.JOBS_DIR / "owned-changed.log"
    calls = []
    monkeypatch.setattr(
        jobs, "_unlink_owned_identity",
        lambda *_: (removed, "measured cleanup outcome", True))
    monkeypatch.setattr(
        jobs._RegistryTxn, "fsync", lambda txn: calls.append(txn))

    note = jobs._discard_owned_log(
        log, observed=("present", 1, 2, stat.S_IFREG))

    assert len(calls) == 1 and calls[0].display_root == jobs.JOBS_DIR
    assert ("removed its log" in note) is removed
    assert ("COULD NOT remove" in note) is (not removed)


@pytest.mark.parametrize("removed", (False, True))
def test_discard_owned_log_reports_changed_namespace_durability_failure(
        jobs, monkeypatch, removed):
    log = jobs.JOBS_DIR / "owned-durability-unknown.log"
    monkeypatch.setattr(
        jobs, "_unlink_owned_identity",
        lambda *_: (removed, "measured cleanup outcome", True))
    calls = []

    def fail_fsync(txn):
        calls.append(txn)
        raise RuntimeError("injected owned-log durability failure")

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", fail_fsync)

    note = jobs._discard_owned_log(
        log, observed=("present", 1, 2, stat.S_IFREG))

    assert str(log) in note and "UNKNOWN" in note
    assert len(calls) == 1 and calls[0].display_root == jobs.JOBS_DIR
    assert "durability failure" in note
    assert "removal state was %r" % removed in note


def test_attempt_cleanup_totalizes_unexpected_durability_helper_failure(
        jobs, monkeypatch):
    path = jobs.JOBS_DIR / ".bd-jobs-script-durability.sh"
    monkeypatch.setattr(
        jobs, "_unlink_owned_identity",
        lambda *_: (True, "removed captured path", True))
    calls = []

    def fail_fsync(txn):
        calls.append(txn)
        raise RuntimeError("injected cleanup durability helper failure")

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", fail_fsync)

    removed, note = jobs._cleanup_attempt_identity(
        path, ("present", 1, 2, stat.S_IFREG))

    assert removed is False
    assert len(calls) == 1 and calls[0].display_root == jobs.JOBS_DIR
    assert str(path) in note and "UNKNOWN" in note
    assert "injected cleanup durability helper failure" in note


@pytest.mark.parametrize("fault", (
    "close-post-effect", "unlink-helper", "captured-identity",
))
def test_identity_bound_cleanup_totalizes_post_effect_helper_faults(
        jobs, monkeypatch, fault):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    source = jobs.JOBS_DIR / (".bd-jobs-script-%s.sh" % fault)
    source.write_text("owned evidence\n", encoding="utf-8")
    real_create = jobs._RegistryTxn.create_exclusive
    real_close = jobs.os.close
    real_identity = jobs._component_identity
    real_unlink = jobs._RegistryTxn.unlink_name
    cleanup = {}
    calls = []

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if prefix == ".bd-jobs-cleanup-":
            cleanup.update(txn=txn, component=component, fd=fd)
        return component, fd

    def close_then_raise(fd):
        if fault == "close-post-effect" and fd == cleanup.get("fd"):
            calls.append(fault)
            real_close(fd)
            raise RuntimeError("injected after quarantine close")
        return real_close(fd)

    def identity(txn, component):
        if (fault == "captured-identity"
                and component.startswith(".bd-jobs-cleanup-")):
            calls.append(fault)
            raise RuntimeError("injected captured identity helper fault")
        return real_identity(txn, component)

    def unlink(txn, component):
        if fault == "unlink-helper" and component == cleanup.get("component"):
            calls.append(fault)
            raise RuntimeError("injected quarantine unlink helper fault")
        return real_unlink(txn, component)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(jobs.os, "close", close_then_raise)
    monkeypatch.setattr(jobs, "_component_identity", identity)
    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", unlink)

    with _open_test_registry_txn(jobs) as txn:
        component = txn.component_from_display(source)
        observed = jobs._component_identity(txn, component)
        removed, note, changed = jobs._unlink_owned_identity(
            txn, component, observed)
    quarantines = list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*"))

    assert cleanup["txn"] is txn
    assert calls == [fault]
    assert removed is False and changed is True
    assert len(quarantines) == 1
    assert cleanup["component"] == quarantines[0].name
    assert str(source) in note and str(quarantines[0]) in note
    assert "UNKNOWN" in note


def test_identity_bound_cleanup_preserves_cancellation_after_quarantine_close(
        jobs, monkeypatch):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    source = jobs.JOBS_DIR / ".bd-jobs-script-cancel-close.sh"
    source.write_text("owned evidence\n", encoding="utf-8")
    real_create = jobs._RegistryTxn.create_exclusive
    real_close = jobs.os.close
    cleanup = {}
    primary = SystemExit(82)
    closes = []

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if prefix == ".bd-jobs-cleanup-":
            cleanup.update(txn=txn, component=component, fd=fd)
        return component, fd

    def close_then_cancel(fd):
        if fd == cleanup.get("fd"):
            closes.append(fd)
            real_close(fd)
            raise primary
        return real_close(fd)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(jobs.os, "close", close_then_cancel)
    with pytest.raises(BaseException) as raised:
        with _open_test_registry_txn(jobs) as txn:
            component = txn.component_from_display(source)
            observed = jobs._component_identity(txn, component)
            jobs._unlink_owned_identity(txn, component, observed)

    quarantines = list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*"))
    assert raised.value is primary and primary.code == 82
    assert len(closes) == 1 and len(quarantines) == 1
    assert cleanup["txn"] is txn
    assert cleanup["component"] == quarantines[0].name
    notes = "\n".join(getattr(primary, "__notes__", []))
    assert str(source) in notes and str(quarantines[0]) in notes


@pytest.mark.parametrize("helper", ("disposition", "identity", "cleanup"))
def test_hidden_attempt_protocol_totalizes_ordinary_helper_fault(
        jobs, monkeypatch, capsys, helper):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = jobs.JOBS_DIR / ".bd-jobs-script-hidden-fault.sh"
    path.write_text("echo retained\n", encoding="utf-8")
    request_id, nonce, attempt = "hidden-fault", "a" * 32, "b" * 32
    injected = RuntimeError("injected %s helper boundary fault" % helper)

    if helper == "disposition":
        monkeypatch.setattr(
            jobs, "_attempt_disposition",
            lambda *a: (_ for _ in ()).throw(injected))
        rc = jobs._attempt_disposition_main(
            [request_id, nonce, attempt, str(path)])
        sentinel, expected = jobs._REMOTE_DISPOSITION_SENTINEL, "UNKNOWN"
    elif helper == "identity":
        calls = []
        monkeypatch.setattr(
            jobs, "_component_identity",
            lambda *a: calls.append(a) or (_ for _ in ()).throw(injected))
        rc = jobs._attempt_identity_main(
            [request_id, nonce, attempt, str(path)])
        assert len(calls) == 1
        sentinel, expected = jobs._REMOTE_IDENTITY_SENTINEL, "UNKNOWN"
    else:
        monkeypatch.setattr(
            jobs, "_cleanup_attempt_identity",
            lambda *a: (_ for _ in ()).throw(injected))
        rc = jobs._attempt_cleanup_main([
            request_id, nonce, attempt, str(path), "1", "2",
            str(stat.S_IFREG),
        ])
        sentinel, expected = jobs._REMOTE_CLEANUP_SENTINEL, "RETAINED"

    out, err = capsys.readouterr()
    assert rc == 0
    assert out == "%s%s:%s:%s:%s\n" % (
        sentinel, request_id, nonce, attempt, expected)
    assert path.read_text() == "echo retained\n"
    assert "Traceback" not in out + err
    if helper == "cleanup":
        assert str(path) in err and "UNKNOWN" in err


@pytest.mark.parametrize(
    ("helper_name", "valid_arity", "invalid_binding"),
    (
        ("disposition", 4, ["bad request", "a" * 32, "b" * 32, "/tmp/x"]),
        ("identity", 4, ["bad request", "a" * 32, "b" * 32, "/tmp/x"]),
        ("cleanup", 7, [
            "bad request", "a" * 32, "b" * 32, "/tmp/x", "d", "i", "k",
        ]),
    ),
)
def test_hidden_attempt_refusals_are_distinctive_and_never_silent(
        jobs, capsys, helper_name, valid_arity, invalid_binding):
    helper = getattr(jobs, "_attempt_%s_main" % helper_name)

    assert helper([]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err == (
        "REFUSED: internal attempt %s expects %d fields\n"
        % (helper_name, valid_arity))

    assert helper(invalid_binding) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err == (
        "REFUSED: internal attempt %s binding is invalid\n" % helper_name)


def test_withdrawal_totalizes_post_unlink_durability_helper_failure(
        jobs, monkeypatch):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    final = jobs.JOBS_DIR / "host-301.json"
    final.write_text("{}", encoding="utf-8")
    log = jobs.JOBS_DIR / "owned.log"
    log.write_text("owned\n", encoding="utf-8")
    calls = []
    real_fsync = jobs._RegistryTxn.fsync

    def fsync(txn):
        calls.append(txn)
        if len(calls) == 2:
            raise RuntimeError("injected post-unlink durability helper failure")
        return real_fsync(txn)

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", fsync)

    with _open_test_registry_txn(jobs, lock=True) as txn:
        outcome = jobs._withdraw_or_retain(
            txn, final.name, {"id": "host-301", "log": str(log)},
            RuntimeError("primary publication failure"))

    assert isinstance(outcome, jobs.PublishedNotDurable)
    assert calls == [txn, txn]
    assert not final.exists() and log.exists()
    assert "injected post-unlink durability helper failure" in str(outcome)
    assert str(final) in str(outcome) and str(log) in str(outcome)


@pytest.mark.parametrize("finalizer", ("fsync-dir", "unlock-registry"))
def test_finalizer_secondary_never_replaces_or_mutates_primary(
        jobs, monkeypatch, finalizer):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    txn = jobs._RegistryTxn(jobs.JOBS_DIR, require_write=True, create=False)
    assert txn.open()
    if finalizer == "unlock-registry":
        txn.lock()
    fd = txn.dir_fd
    fired = []
    if finalizer == "fsync-dir":
        primary = OSError(5, "primary fsync failure", "registry-path")

        def fail_fsync(candidate):
            fired.append((finalizer, candidate))
            raise primary

        monkeypatch.setattr(jobs._RegistryTxn, "fsync", fail_fsync)

        def invoke():
            try:
                txn.fsync()
            except BaseException as exc:
                txn.close(primary=exc)
    else:
        primary = SystemExit(73)
        real_flock = jobs.fcntl.flock

        def fail_unlock(candidate_fd, operation):
            if candidate_fd == fd and operation == jobs.fcntl.LOCK_UN:
                fired.append((finalizer, txn))
                raise primary
            return real_flock(candidate_fd, operation)

        monkeypatch.setattr(jobs.fcntl, "flock", fail_unlock)
        invoke = txn.close
    close_calls = []
    real_close = jobs.os.close

    def fail_close(value):
        if value == fd:
            close_calls.append(value)
            real_close(value)
            raise RuntimeError("secondary close failure")
        return real_close(value)

    monkeypatch.setattr(jobs.os, "close", fail_close)
    original_args = primary.args
    original_errno = getattr(primary, "errno", None)
    original_filename = getattr(primary, "filename", None)
    original_code = getattr(primary, "code", None)

    with pytest.raises(BaseException) as excinfo:
        invoke()

    assert excinfo.value is primary
    assert primary.args == original_args
    assert getattr(primary, "errno", None) == original_errno
    assert getattr(primary, "filename", None) == original_filename
    assert getattr(primary, "code", None) == original_code
    assert fired == [(finalizer, txn)]
    assert close_calls == [fd]
    notes = "\n".join(getattr(primary, "__notes__", []))
    assert "secondary close failure" in notes


# ── v3.66.1207: recovery -- a registry that cannot be read is UNKNOWN ────────
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    final.write_text(json.dumps(entry), encoding="utf-8")
    real_unlink = jobs._RegistryTxn.unlink_name
    attempts = []

    def refuse_final(txn, component):
        if component == final.name:
            attempts.append((txn, component))
            raise PermissionError("injected retained reap final")
        return real_unlink(txn, component)

    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", refuse_final)
    rc = jobs.cmd_reap(type("A", (), {"id": entry["id"]})())
    out, err = capsys.readouterr()

    assert len(attempts) == 1 and attempts[0][1] == final.name
    assert attempts[0][0].display_root == jobs.JOBS_DIR
    assert final.exists(), "the final disappeared despite the injected refusal"
    assert rc == 1, "retained cleanup must have the refusal status"
    assert "REFUSED" in err and "COULD NOT remove" in err, (
        "reap hid its retained cleanup: stdout=%r stderr=%r" % (out, err))
    assert "0 reaped, 1 refused" in out, (
        "the retained record was counted as a clean reap: %r" % out)


def test_reap_reports_owned_artifact_cleanup_failure_after_durable_withdrawal(
        jobs, sleeper, monkeypatch, capsys):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-unlink-refused.sh"
    owned.write_text("echo retained\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "artifact cleanup refusal", "bash script",
        owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    p.terminate()
    p.wait(timeout=10)
    assert jobs.proc_starttime(p.pid) is None
    real_unlink = jobs._RegistryTxn.unlink_name
    attempts = []

    def refuse_quarantine(txn, component):
        if (component.startswith(".bd-jobs-cleanup-")
                and component.endswith(".tmp")):
            attempts.append((txn, component))
            raise PermissionError("injected quarantine-artifact refusal")
        return real_unlink(txn, component)

    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", refuse_quarantine)
    rc = jobs.cmd_reap(type("A", (), {"id": entry["id"]})())
    out, err = capsys.readouterr()

    assert len(attempts) == 1, "the quarantine-unlink fault did not fire once"
    txn, component = attempts[0]
    assert txn.display_root == jobs.JOBS_DIR
    quarantine = txn.display_path(component)
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
    monkeypatch.setattr(
        jobs, "proc_argv",
        lambda pid: ["python3", "-m", "pytest", "tests/test_x.py"],
    )
    monkeypatch.setattr(jobs.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(
                            a[0], 0,
                            "%d 1 python3 python3 -m pytest tests/test_x.py\n" % p.pid,
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
    monkeypatch.setattr(
        jobs, "proc_argv",
        lambda pid: ["python3", "-m", "pytest", "tests/test_x.py"],
    )
    monkeypatch.setattr(jobs.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(
                            a[0], 0,
                            "%d 1 python3 python3 -m pytest tests/test_x.py\n" % p.pid,
                            ""))
    monkeypatch.setattr(jobs, "proc_starttime", lambda pid: None)

    rc = jobs.cmd_orphans(type("A", (), {})())
    out, err = capsys.readouterr()

    assert err == "" and rc == 0
    assert "ORPHAN" not in out
    assert "0 unregistered pytest process(es)" in out


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "oserror"])
def test_orphans_process_table_failure_is_unknown_not_a_fabricated_zero(
        jobs, monkeypatch, capsys, failure):
    observed = []
    if failure == "oserror":
        jobs.JOBS_DIR.mkdir(mode=0o700)
    torn = _write_malformed(jobs.JOBS_DIR) if failure == "oserror" else None
    torn_bytes = torn.read_bytes() if torn is not None else None

    def fail(argv, **kwargs):
        observed.append((argv, kwargs))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if failure == "oserror":
            raise OSError(errno.EIO, "injected ps execution failure")
        return subprocess.CompletedProcess(
            argv, 2, "", "ps: injected nonzero failure")

    monkeypatch.setattr(jobs.subprocess, "run", fail)
    monkeypatch.setattr(  # v3.66.1223: same seam, now via the deadline funnel
        jobs, "_run_remote",
        lambda argv, deadline, **kw: (fail(argv), True, ""))

    rc = jobs.cmd_orphans(type("A", (), {})())
    out, err = capsys.readouterr()

    assert len(observed) == 1
    assert observed[0][0] == ["ps", "-eo", "pid=,ppid=,comm=,args="]
    assert 0 < observed[0][1]["timeout"] <= 30
    assert rc == jobs._EXIT_REGISTRY_UNKNOWN, (out, err)
    assert "UNKNOWN" in err and "process table" in err and failure in err, err
    assert "unregistered pytest process(es)" not in out, (
        "a process table that was never measured was reported as a count: %r"
        % out)
    if torn is not None:
        assert "UNREADABLE %s" % torn in err
        assert torn.exists() and torn.read_bytes() == torn_bytes


def test_orphans_healthy_empty_process_table_remains_exact_success(
        jobs, monkeypatch, capsys):
    observed = []

    def empty(argv, **kwargs):
        observed.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(jobs.subprocess, "run", empty)
    monkeypatch.setattr(  # v3.66.1223: same seam, now via the deadline funnel
        jobs, "_run_remote",
        lambda argv, deadline, **kw: (empty(argv), True, ""))

    rc = jobs.cmd_orphans(type("A", (), {})())
    out, err = capsys.readouterr()

    assert len(observed) == 1 and 0 < observed[0][1]["timeout"] <= 30
    assert rc == 0 and err == ""
    assert "0 unregistered pytest process(es)" in out


def test_no_verb_ever_deletes_a_malformed_final(jobs, sleeper, capsys):
    """Retention for operator adjudication. Bytes nobody can parse are still
    the only evidence of what went wrong, and a tool that tidies them away
    destroys the thing an operator would need to reconstruct."""
    p = sleeper()
    jobs.register(p.pid, "unit test", "sleep 60")
    torn = _write_malformed(jobs.JOBS_DIR)
    before = torn.read_bytes()

    results = []
    for name, invoke in (
            ("list", lambda: jobs.cmd_list(type("A", (), {})())),
            ("orphans", lambda: jobs.cmd_orphans(type("A", (), {})())),
            ("reap", lambda: jobs.cmd_reap(type("A", (), {"id": None})()))):
        rc = invoke()
        out, err = capsys.readouterr()
        results.append((name, rc, out, err))
        assert torn.exists(), "%s deleted the malformed final" % name
        assert torn.read_bytes() == before, "%s rewrote malformed bytes" % name

    assert [rc for _name, rc, _out, _err in results] == [4, 4, 4]
    for name, _rc, out, err in results:
        assert str(torn) in out + err, "%s did not name retained evidence" % name
        assert "UNREADABLE" in err, (name, out, err)
        if name == "orphans":
            assert "denominator is incomplete" in err, (name, out, err)
        else:
            assert "KEPT" in err, (name, out, err)


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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    real_create = jobs._RegistryTxn.create_exclusive
    staged = []

    def watching_create(txn, prefix, suffix):
        staged.append((txn, prefix, suffix))
        return real_create(txn, prefix, suffix)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", watching_create)
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    real_create = jobs._RegistryTxn.create_exclusive
    staged = []

    def watching_create(txn, prefix, suffix):
        staged.append((txn, prefix, suffix))
        return real_create(txn, prefix, suffix)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", watching_create)
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
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
    identity = _test_registry_identity(jobs, "unencodable\ud800")
    assert identity[0] == "unreadable" and (
        "unencodable" in " ".join(identity[1:]).lower()
        or "unicode" in " ".join(identity[1:]).lower()), identity


# ── v3.66.1207: publication collisions, decided only by pid + starttime ──────


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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    prior_script = jobs.JOBS_DIR / ".bd-jobs-script-displaced.sh"
    prior_script.write_text("echo old\n", encoding="utf-8")
    prior = jobs.register(
        p.pid, "displaced owner", "sleep 60",
        owned_paths=[str(prior_script)])
    final = jobs.JOBS_DIR / ("%s.json" % prior["id"])
    stale = dict(prior, starttime=prior["starttime"] - 1)
    stale.pop("_path", None)
    final.write_text(json.dumps(stale), encoding="utf-8")
    real_fsync = jobs._RegistryTxn.fsync
    calls = []

    def fail_first(txn):
        calls.append(txn)
        if len(calls) == 1:
            raise OSError(5, "injected first directory fsync failure")
        return real_fsync(txn)

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", fail_first)
    with pytest.raises(jobs.PublishedNotDurable) as raised:
        jobs.register(p.pid, "replacement owner", "sleep 60")

    assert len(calls) == 1 and calls[0].display_root == jobs.JOBS_DIR, (
        "the failed stale replacement was withdrawn through a second fsync "
        "sequence, abandoning the displaced record: %r" % calls)
    assert final.exists(), "the sole surviving cleanup authority was withdrawn"
    on_disk = json.loads(final.read_text(encoding="utf-8"))
    assert on_disk["purpose"] == "replacement owner", on_disk
    assert str(prior_script) in (on_disk.get("owned_paths") or []), on_disk
    assert prior_script.exists()
    rendered = jobs._exception_text(raised.value)
    assert str(final) in rendered and str(prior_script) in rendered


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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    final = jobs.JOBS_DIR / ("%s.json" % old["id"])
    final.write_text(json.dumps(old), encoding="utf-8")
    replacement = dict(old, starttime=actual, purpose="new owner",
                       request_id="new-request")
    real_proc_starttime = jobs.proc_starttime
    interleaved = []

    def publish_between_snapshot_and_forget(pid):
        if not interleaved:
            interleaved.append(("firing",))
            with _open_test_registry_txn(jobs) as txn:
                publication = jobs._publish_entry(txn, replacement)
                interleaved[0] = (txn, publication.entry["request_id"])
        return real_proc_starttime(pid)

    monkeypatch.setattr(jobs, "proc_starttime", publish_between_snapshot_and_forget)
    rc = jobs.cmd_reap(type("A", (), {"id": old["id"]})())
    capsys.readouterr()

    assert len(interleaved) == 1
    assert interleaved[0][0].display_root == jobs.JOBS_DIR
    assert interleaved[0][1] == "new-request"
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    assert probe_lock(jobs.JOBS_DIR) == "FREE", (
        "precondition: the registry must not be locked before the transaction")

    observed = []
    real_fsync = jobs._RegistryTxn.fsync

    def watching(txn):
        assert txn.locked
        observed.append(probe_lock(txn.display_root))
        return real_fsync(txn)

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", watching)
    jobs.register(p.pid, "locked publication", "sleep 60")

    assert observed == ["BLOCKED"], (
        "the registry was not held while the entry was being published: %r -- "
        "a concurrent publisher could read, decide and replace inside this "
        "window" % observed)
    assert probe_lock(jobs.JOBS_DIR) == "FREE", (
        "the registry lock outlived the transaction that took it")


def test_registry_lock_retries_nonblocking_contention(jobs, monkeypatch):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    calls = []
    sleeps = []
    monotonic = iter((100.0, 100.0, 100.0))

    lock_fds = []

    def contend_then_acquire(fd, operation):
        if operation == jobs.fcntl.LOCK_UN:
            return
        lock_fds.append(fd)
        calls.append(operation)
        if len(calls) < 3:
            raise BlockingIOError(jobs.errno.EWOULDBLOCK, "held")

    monkeypatch.setattr(jobs.fcntl, "flock", contend_then_acquire)
    monkeypatch.setattr(jobs.time, "sleep", sleeps.append)
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(monotonic))

    with _open_test_registry_txn(jobs) as txn:
        pinned_fd = txn.dir_fd
        txn.lock()
        assert txn.locked
    assert calls == [jobs.fcntl.LOCK_EX | jobs.fcntl.LOCK_NB] * 3
    assert lock_fds == [pinned_fd] * 3
    assert len(sleeps) == 2 and all(0 < delay <= 0.05 for delay in sleeps)


def test_registry_lock_contention_has_a_finite_explicit_refusal(
        jobs, monkeypatch):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    real_close = os.close
    closed = []

    def always_held(fd, operation):
        raise BlockingIOError(jobs.errno.EWOULDBLOCK, "held")

    def record_close(fd):
        closed.append(fd)
        return real_close(fd)

    with _open_test_registry_txn(jobs) as txn:
        dir_fd, parent_fd = txn.dir_fd, txn.parent_fd
        monkeypatch.setattr(jobs.fcntl, "flock", always_held)
        monkeypatch.setattr(jobs.os, "close", record_close)
        monkeypatch.setattr(jobs, "_REGISTRY_LOCK_TIMEOUT_S", 0.0)

        with pytest.raises(jobs.RegistryLockTimeout) as excinfo:
            txn.lock()

    assert "remained held" in str(excinfo.value)
    assert closed == [dir_fd, parent_fd], closed
    assert txn.dir_fd is None and txn.parent_fd is None


def test_registry_writers_refuse_a_symlink_registry(jobs, sleeper, tmp_path):
    for operation in ("register", "open-log"):
        outside = tmp_path / ("attacker-controlled-" + operation)
        outside.mkdir()
        jobs.JOBS_DIR = tmp_path / ("registry-" + operation)
        jobs.JOBS_DIR.symlink_to(outside, target_is_directory=True)

        with pytest.raises(jobs.RegistrationError) as excinfo:
            if operation == "register":
                jobs.register(sleeper().pid, "symlink", "sleep 60")
            else:
                jobs.open_job_log("symlink")

        assert "existing path is not a real directory" in str(excinfo.value)
        assert list(outside.iterdir()) == []


def test_open_job_log_refuses_group_writable_registry(jobs):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o770)
    jobs.JOBS_DIR.chmod(0o770)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.open_job_log("writable")

    assert "group- or other-writable" in str(excinfo.value)
    assert list(jobs.JOBS_DIR.iterdir()) == []


def test_local_run_structures_registry_refusal_before_launch(
        jobs, tmp_path, capsys):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o770)
    jobs.JOBS_DIR.chmod(0o770)
    marker = tmp_path / "must-not-run.marker"

    rc = jobs.main([
        "run", "--host", "local", "--purpose", "insecure registry", "--",
        "bash", "-c", "touch %s" % marker,
    ])
    out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REGISTRATION_FAILED
    assert out == "" and "REFUSED" in err and "Traceback" not in err
    assert "group- or other-writable" in err
    assert not marker.exists() and list(jobs.JOBS_DIR.iterdir()) == []


def test_register_refuses_registry_owned_by_another_identity(
        jobs, sleeper, monkeypatch):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    actual_uid = jobs.JOBS_DIR.stat().st_uid
    monkeypatch.setattr(jobs.os, "geteuid", lambda: actual_uid + 1)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(sleeper().pid, "foreign owner", "sleep 60")

    assert "owned by uid" in str(excinfo.value)
    assert list(jobs.JOBS_DIR.iterdir()) == []


def test_insecure_registry_reader_is_unknown_and_never_acts(
        jobs, sleeper, monkeypatch, capsys):
    proc = sleeper()
    entry = jobs.register(proc.pid, "reader authority", "sleep 60")
    final = pathlib.Path(entry["_path"])
    jobs.JOBS_DIR.chmod(0o770)
    effects = []

    monkeypatch.setattr(
        jobs.os, "kill", lambda *args: effects.append(("kill", args)))
    monkeypatch.setattr(
        jobs.os, "killpg", lambda *args: effects.append(("killpg", args)))
    monkeypatch.setattr(
        jobs, "forget", lambda value: effects.append(("forget", value)))

    rc = jobs.cmd_reap(type("A", (), {"id": None})())
    _out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REGISTRY_UNKNOWN
    assert effects == []
    assert final.exists()
    assert "group- or other-writable" in err


def test_inaccessible_registry_reader_is_unknown_and_never_acts(
        jobs, sleeper, monkeypatch, capsys):
    proc = sleeper()
    entry = jobs.register(proc.pid, "inaccessible reader", "sleep 60")
    final = pathlib.Path(entry["_path"])
    effects = []
    jobs.JOBS_DIR.chmod(0o000)
    try:
        monkeypatch.setattr(
            jobs.os, "kill", lambda *args: effects.append(("kill", args)))
        monkeypatch.setattr(
            jobs.os, "killpg", lambda *args: effects.append(("killpg", args)))
        monkeypatch.setattr(
            jobs, "forget", lambda value: effects.append(("forget", value)))

        rc = jobs.cmd_reap(type("A", (), {"id": None})())
        _out, err = capsys.readouterr()

        assert rc == jobs._EXIT_REGISTRY_UNKNOWN
        assert effects == []
        assert "owner read/search" in err
    finally:
        jobs.JOBS_DIR.chmod(0o700)
    assert final.exists()


def test_registry_scandir_failure_is_unknown_and_never_acts(
        jobs, sleeper, monkeypatch, capsys):
    proc = sleeper()
    entry = jobs.register(proc.pid, "scandir failure", "sleep 60")
    final = pathlib.Path(entry["_path"])
    effects = []

    monkeypatch.setattr(
        jobs.os, "scandir",
        lambda path: (_ for _ in ()).throw(
            PermissionError(13, "injected registry scandir refusal")))
    monkeypatch.setattr(
        jobs.os, "kill", lambda *args: effects.append(("kill", args)))
    monkeypatch.setattr(
        jobs.os, "killpg", lambda *args: effects.append(("killpg", args)))
    monkeypatch.setattr(
        jobs, "forget", lambda value: effects.append(("forget", value)))

    rc = jobs.cmd_reap(type("A", (), {"id": None})())
    _out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REGISTRY_UNKNOWN
    assert effects == [] and final.exists()
    assert "registry root cannot be listed" in err
    assert "injected registry scandir refusal" in err


def test_registry_is_created_0700_under_permissive_umask(jobs):
    previous = os.umask(0)
    try:
        jobs._prepare_registry()
    finally:
        os.umask(previous)

    observed = os.lstat(jobs.JOBS_DIR)
    assert stat.S_ISDIR(observed.st_mode)
    assert stat.S_IMODE(observed.st_mode) == 0o700
    assert observed.st_uid == os.geteuid()


def test_hidden_prepare_requires_owner_write_permission(jobs, capsys):
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o500)
    jobs.JOBS_DIR.chmod(0o500)

    rc = jobs.main([jobs._PREPARE_REGISTRY_FLAG])
    out, err = capsys.readouterr()

    assert rc == 2 and out == ""
    assert "REFUSED" in err and "owner read/write/search" in err
    assert list(jobs.JOBS_DIR.iterdir()) == []


def test_hidden_prepare_close_fault_is_structured_status_2_without_traceback(
        jobs, monkeypatch, capsys):
    real_close = jobs._RegistryTxn.close
    failure = OSError(errno.EIO, "injected registry prepare close EIO")
    fired = []

    def close_after_effect_then_raise(txn, primary=None):
        result = real_close(txn, primary=primary)
        if primary is None and not fired:
            fired.append((txn.dir_fd, txn.parent_fd))
            raise failure
        return result

    monkeypatch.setattr(
        jobs._RegistryTxn, "close", close_after_effect_then_raise)
    try:
        rc = jobs.main([jobs._PREPARE_REGISTRY_FLAG])
    except BaseException as exc:  # noqa: BLE001 - F16 regression surface
        rc = ("RAISED", type(exc).__name__, str(exc))
    out, err = capsys.readouterr()

    assert fired == [(None, None)]
    assert rc == 2 and out == ""
    assert "REFUSED" in err and "registry preparation close is UNKNOWN" in err
    assert failure.args[1] in err and "Traceback" not in err


# ── v3.66.1207: cleanup touches only proven-owned, normalized paths ──────────


def test_forget_removes_an_owned_auxiliary_path_and_never_an_unowned_one(
        jobs, sleeper, tmp_path):
    """A delegated script is copied into the registry directory and ADOPTED by
    the entry, so the entry owns it. Everything else an entry merely mentions
    is somebody else's file."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-durable-withdrawal.sh"
    owned.write_text("echo keep until durable\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "durable withdrawal", "bash script",
        owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    calls = []

    def fail_fsync(txn):
        calls.append(txn)
        raise OSError(5, "injected withdrawal fsync failure")

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", fail_fsync)
    outcome = jobs.forget(entry)

    assert len(calls) == 1 and calls[0].display_root == jobs.JOBS_DIR, (
        "forget never tried to make final absence durable: %r" % calls)
    assert outcome["entry_removed"] is True
    assert outcome["entry_absent_durable"] is False
    assert outcome["cleanup_complete"] is False
    assert not final.exists(), (
        "the injected fault is after unlink; the visible final should be absent")
    assert owned.exists(), (
        "forget deleted an artifact before final absence was durable")


def test_forget_totalizes_ordinary_final_absence_durability_helper_fault(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    owned = jobs.JOBS_DIR / ".bd-jobs-script-runtime-fsync.sh"
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned.write_text("echo retained\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "runtime fsync", "bash script", owned_paths=[str(owned)])
    final = pathlib.Path(entry["_path"])
    calls = []

    def fail_fsync(txn):
        calls.append(txn)
        raise RuntimeError("injected ordinary durability helper fault")

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", fail_fsync)

    outcome = jobs.forget(entry)

    assert not final.exists() and owned.exists()
    assert len(calls) == 1 and calls[0].display_root == jobs.JOBS_DIR
    assert outcome["entry_removed"] is True
    assert outcome["entry_absent_durable"] is False
    assert outcome["cleanup_complete"] is False
    notes = "; ".join(outcome["notes"])
    assert "ordinary durability helper fault" in notes
    assert "Traceback" not in notes


def _assert_quarantine_setup_failure(jobs, sleeper, monkeypatch, fault):
    """Cleanup setup failure is a named retained path, never a traceback."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / (".bd-jobs-script-%s-failure.sh" % fault)
    owned.write_text("echo retained\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "%s cleanup failure" % fault, "bash script",
        owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    real_create = jobs._RegistryTxn.create_exclusive
    real_close = os.close
    cleanup = {}
    fired = []

    def faulting_create(txn, prefix, suffix):
        if prefix != ".bd-jobs-cleanup-":
            return real_create(txn, prefix, suffix)
        fired.append("create-exclusive")
        cleanup.update(txn=txn, prefix=prefix, suffix=suffix)
        if fault == "exclusive-create":
            raise OSError(28, "injected cleanup exclusive-create failure")
        component, fd = real_create(txn, prefix, suffix)
        cleanup.update(component=component, fd=fd)
        return component, fd

    def faulting_close(fd):
        if fault == "close" and fd == cleanup.get("fd"):
            fired.append("close")
            real_close(fd)
            raise OSError(5, "injected cleanup close failure")
        return real_close(fd)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", faulting_create)
    monkeypatch.setattr(jobs.os, "close", faulting_close)
    outcome = jobs.forget(entry)

    assert fired == (["create-exclusive"] if fault == "exclusive-create"
                     else ["create-exclusive", "close"]), fired
    assert cleanup["txn"].display_root == jobs.JOBS_DIR
    assert cleanup["prefix"] == ".bd-jobs-cleanup-"
    assert cleanup["suffix"] == ".tmp"
    assert not final.exists(), "the final was not durably withdrawn first"
    assert owned.exists(), "setup failure deleted the artifact it could not bind"
    assert outcome["cleanup_complete"] is False, outcome
    notes = "; ".join(outcome["notes"])
    assert str(owned) in notes and "COULD NOT" in notes, notes
    quarantines = list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*"))
    if fault == "exclusive-create":
        assert quarantines == [], (
            "exclusive-create failure invented a quarantine: %r" % quarantines)
    else:
        assert len(quarantines) == 1, (
            "uncertain close lost the only name for its temp: %r" % quarantines)
        assert str(quarantines[0]) in notes, (
            "the retained close-failure temp was not named: %s" % notes)


def test_forget_reports_exclusive_create_failure_after_durable_withdrawal(
        jobs, sleeper, monkeypatch):
    _assert_quarantine_setup_failure(
        jobs, sleeper, monkeypatch, "exclusive-create")


def test_forget_reports_close_failure_after_durable_withdrawal(
        jobs, sleeper, monkeypatch):
    _assert_quarantine_setup_failure(jobs, sleeper, monkeypatch, "close")


def _assert_replace_cleanup_failure(
        jobs, sleeper, monkeypatch, source_vanished):
    """Both source and temp disposition stay truthful after replace failure."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-replace-failure.sh"
    owned.write_text("echo retained\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "replace cleanup failure", "bash script",
        owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    real_rename = jobs._RegistryTxn.rename_name
    real_unlink = jobs._RegistryTxn.unlink_name
    fired = []

    def faulting_rename(txn, src, dst):
        if src == owned.name:
            fired.append("replace")
            if source_vanished:
                real_unlink(txn, src)
                raise FileNotFoundError(str(src))
            raise PermissionError("injected quarantine replace failure")
        return real_rename(txn, src, dst)

    def faulting_unlink(txn, component):
        if component.startswith(".bd-jobs-cleanup-"):
            fired.append("unlink-temp")
            raise PermissionError("injected retained cleanup temp")
        return real_unlink(txn, component)

    monkeypatch.setattr(jobs._RegistryTxn, "rename_name", faulting_rename)
    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", faulting_unlink)
    outcome = jobs.forget(entry)

    quarantines = list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*"))
    assert fired == ["replace", "unlink-temp"], fired
    assert not final.exists(), "the final was not durably withdrawn first"
    assert owned.exists() is (not source_vanished)
    assert len(quarantines) == 1, quarantines
    assert outcome["cleanup_complete"] is False, outcome
    assert outcome["entry_absent_durable"] is True
    assert outcome["owned_removed"] == [False]
    assert quarantines[0].read_bytes() == b""
    notes = "; ".join(outcome["notes"])
    assert str(owned) in notes and str(quarantines[0]) in notes, notes


def test_forget_names_a_retained_quarantine_after_replace_failure(
        jobs, sleeper, monkeypatch):
    _assert_replace_cleanup_failure(jobs, sleeper, monkeypatch, False)


def test_forget_names_a_retained_quarantine_after_source_vanishes(
        jobs, sleeper, monkeypatch):
    _assert_replace_cleanup_failure(jobs, sleeper, monkeypatch, True)


@pytest.mark.parametrize("error_type", (RuntimeError, FileNotFoundError))
def test_forget_retains_captured_source_when_replace_raises_after_effect(
        jobs, sleeper, monkeypatch, error_type):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-replace-post-effect.sh"
    payload = b"echo retained after uncertain replace\n"
    owned.write_bytes(payload)
    entry = jobs.register(
        p.pid, "replace post-effect", "bash script",
        owned_paths=[str(owned)])
    real_rename = jobs._RegistryTxn.rename_name
    calls = []

    def rename_then_raise(txn, src, dst):
        if src == owned.name:
            calls.append((txn, src, dst))
            real_rename(txn, src, dst)
            raise error_type("injected post-effect replace failure")
        return real_rename(txn, src, dst)

    monkeypatch.setattr(jobs._RegistryTxn, "rename_name", rename_then_raise)
    outcome = jobs.forget(entry)
    quarantines = list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*"))

    assert len(calls) == 1 and calls[0][1] == owned.name
    assert calls[0][0].display_root == jobs.JOBS_DIR
    assert calls[0][2].startswith(".bd-jobs-cleanup-")
    assert not owned.exists()
    assert len(quarantines) == 1 and quarantines[0].read_bytes() == payload
    assert outcome["cleanup_complete"] is False
    notes = "; ".join(outcome["notes"])
    assert str(owned) in notes and str(quarantines[0]) in notes
    assert "RETAINED/UNKNOWN" in notes


def test_forget_cleans_only_proved_empty_quarantine_after_pre_effect_failure(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-replace-pre-effect.sh"
    payload = b"echo still at source\n"
    owned.write_bytes(payload)
    entry = jobs.register(
        p.pid, "replace pre-effect", "bash script",
        owned_paths=[str(owned)])
    real_rename = jobs._RegistryTxn.rename_name
    calls = []

    def refuse_replace(txn, src, dst):
        if src == owned.name:
            calls.append((txn, src, dst))
            raise PermissionError("injected pre-effect replace refusal")
        return real_rename(txn, src, dst)

    monkeypatch.setattr(jobs._RegistryTxn, "rename_name", refuse_replace)
    outcome = jobs.forget(entry)

    assert len(calls) == 1 and calls[0][0].display_root == jobs.JOBS_DIR
    assert calls[0][1] == owned.name
    assert calls[0][2].startswith(".bd-jobs-cleanup-")
    assert owned.read_bytes() == payload
    assert not list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*"))
    assert outcome["entry_absent_durable"] is True
    assert outcome["owned_removed"] == [False]
    assert outcome["cleanup_complete"] is False


def test_forget_never_uses_unreadable_empty_quarantine_as_cleanup_authority(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-unreadable-empty-quarantine.sh"
    payload = b"echo identity unproved\n"
    owned.write_bytes(payload)
    entry = jobs.register(
        p.pid, "unreadable empty quarantine", "bash script",
        owned_paths=[str(owned)])
    real_identity = jobs._component_identity
    rename_calls = []

    def unreadable_quarantine(txn, component):
        if component.startswith(".bd-jobs-cleanup-"):
            return ("unreadable", "RuntimeError", "injected unreadable identity")
        return real_identity(txn, component)

    monkeypatch.setattr(jobs, "_component_identity", unreadable_quarantine)
    monkeypatch.setattr(
        jobs._RegistryTxn, "rename_name",
        lambda *args: rename_calls.append(args))
    outcome = jobs.forget(entry)
    quarantines = list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*"))

    assert rename_calls == [] and owned.read_bytes() == payload
    assert len(quarantines) == 1 and quarantines[0].read_bytes() == b""
    assert outcome["owned_removed"] == [False]
    assert outcome["cleanup_complete"] is False
    assert "not a proved regular file" in "; ".join(outcome["notes"])


def test_forget_reports_owned_cleanup_directory_fsync_failure(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-cleanup-fsync.sh"
    owned.write_text("echo cleanup\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "cleanup fsync", "bash script", owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    real_fsync = jobs._RegistryTxn.fsync
    calls = []

    def fail_second(txn):
        calls.append(txn)
        if len(calls) == 2:
            raise OSError(5, "injected owned-cleanup fsync failure")
        return real_fsync(txn)

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", fail_second)
    outcome = jobs.forget(entry)

    assert len(calls) == 2 and len({id(txn) for txn in calls}) == 1, calls
    assert all(txn.display_root == jobs.JOBS_DIR for txn in calls)
    assert not final.exists() and not owned.exists()
    assert outcome["entry_absent_durable"] is True
    assert outcome["cleanup_complete"] is False
    assert "owned-artifact cleanup durable" in "; ".join(outcome["notes"])


def test_forget_never_deletes_a_new_file_reusing_an_owned_path(
        jobs, sleeper, monkeypatch):
    """Cleanup authority is bound to the file observed with the old record,
    not every future inode that happens to reuse its pathname."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-reused.sh"
    owned.write_text("old owner\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "old pathname owner", "bash script",
        owned_paths=[str(owned)])
    final = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    real_unlink_verified = jobs._unlink_verified
    swapped = []

    def replace_after_final_withdrawal(txn, component):
        result = real_unlink_verified(txn, component)
        if component == final.name and result[0]:
            replacement, fd = txn.create_exclusive(
                ".replacement-owned-", ".tmp")
            os.write(fd, b"new owner\n")
            os.close(fd)
            txn.rename_name(replacement, owned.name)
            swapped.append((txn, component, replacement))
        return result

    monkeypatch.setattr(jobs, "_unlink_verified", replace_after_final_withdrawal)
    outcome = jobs.forget(entry)

    assert len(swapped) == 1, "the pathname-reuse schedule never occurred"
    assert swapped[0][0].display_root == jobs.JOBS_DIR
    assert swapped[0][1] == final.name
    assert owned.exists() and owned.read_text(encoding="utf-8") == "new owner\n", (
        "stale cleanup deleted a replacement file that reused the old path")
    assert outcome["cleanup_complete"] is False, (
        "retaining a changed owned path was reported as complete cleanup")


def test_forget_captures_the_old_inode_before_unlinking_its_quarantine(
        jobs, sleeper, monkeypatch):
    """A replacement between identity comparison and rename must survive."""
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-check-unlink-race.sh"
    owned.write_text("old owner\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "check unlink race", "bash script",
        owned_paths=[str(owned)])
    real_rename = jobs._RegistryTxn.rename_name
    swapped = []

    def replace_between_check_and_quarantine(txn, src, dst):
        if src == owned.name and not swapped:
            replacement, fd = txn.create_exclusive(
                ".replacement-before-unlink-", ".tmp")
            os.write(fd, b"new owner\n")
            os.close(fd)
            real_rename(txn, replacement, src)
            swapped.append((txn, src, dst))
        return real_rename(txn, src, dst)

    monkeypatch.setattr(
        jobs._RegistryTxn, "rename_name",
        replace_between_check_and_quarantine)
    outcome = jobs.forget(entry)

    assert len(swapped) == 1, "the check/rename replacement schedule never ran"
    assert swapped[0][0].display_root == jobs.JOBS_DIR
    assert swapped[0][1] == owned.name
    assert swapped[0][2].startswith(".bd-jobs-cleanup-")
    assert owned.exists() and owned.read_text(encoding="utf-8") == "new owner\n", (
        "cleanup deleted the pathname occupant installed after its identity "
        "check")
    assert outcome["cleanup_complete"] is False, outcome
    assert "restored" in "; ".join(outcome["notes"]), outcome


def test_forget_retains_quarantine_when_restore_link_is_unsupported(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    owned = jobs.JOBS_DIR / ".bd-jobs-script-unsupported-link.sh"
    owned.write_text("old owner\n", encoding="utf-8")
    entry = jobs.register(
        p.pid, "unsupported restore link", "bash script",
        owned_paths=[str(owned)])
    real_rename = jobs._RegistryTxn.rename_name
    swapped = []

    def replace_between_check_and_quarantine(txn, src, dst):
        if src == owned.name and not swapped:
            replacement, fd = txn.create_exclusive(
                ".replacement-link-unsupported-", ".tmp")
            os.write(fd, b"new owner\n")
            os.close(fd)
            real_rename(txn, replacement, src)
            swapped.append((txn, src, dst))
        return real_rename(txn, src, dst)

    link_calls = []

    def unsupported_link(txn, source, destination):
        link_calls.append((txn, source, destination))
        raise NotImplementedError("injected link follow_symlinks refusal")

    monkeypatch.setattr(
        jobs._RegistryTxn, "rename_name",
        replace_between_check_and_quarantine)
    monkeypatch.setattr(jobs._RegistryTxn, "link_name", unsupported_link)

    outcome = jobs.forget(entry)

    quarantines = list(jobs.JOBS_DIR.glob(".bd-jobs-cleanup-*"))
    assert len(swapped) == 1 and swapped[0][0].display_root == jobs.JOBS_DIR
    assert swapped[0][1] == owned.name
    assert len(link_calls) == 1 and link_calls[0][0] is swapped[0][0]
    assert link_calls[0][1].startswith(".bd-jobs-cleanup-")
    assert link_calls[0][2] == owned.name
    assert not owned.exists()
    assert len(quarantines) == 1, quarantines
    assert quarantines[0].read_text(encoding="utf-8") == "new owner\n"
    assert outcome["cleanup_complete"] is False, outcome
    notes = "; ".join(outcome["notes"])
    assert "could not be restored" in notes and str(quarantines[0]) in notes


# ── v3.66.1207: the merged-stream id contract every consumer parses ──────────


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
    never knew it started -- measured at v3.66.1207 as one orphaned `sleep 45`.

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
    tool_in_argv = jobs._gate_argv(9, 11, 13, ["bash", "-c", "true"])[1]
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
            env=hostile, capture_output=True, text=True, timeout=90)
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
        assert _wait_until(marker.exists, 10.0), "the gated job never ran"

        reaped = subprocess.run(
            [sys.executable, str(copy), "reap", "--id", job_id],
            env=hostile, capture_output=True, text=True, timeout=90)
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
    registry.mkdir(parents=True, mode=0o700, exist_ok=True)
    copied.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    entry = None
    escaped = []
    try:
        executed = subprocess.run(
            ["bash", "-c", payload],
            env=dict(os.environ, BD_JOBS_RUN_MARKER=run_marker),
            capture_output=True, text=True, timeout=90)
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
        assert _wait_until(marker.exists, 10.0), "the delegated script never ran"

        _reap_process(entry["pid"])
        reaped = subprocess.run(
            [sys.executable, str(target), "reap", "--id", entry["id"]],
            capture_output=True, text=True, timeout=90)
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
    v3.66.1207. That form isolated the target process and nothing it launched:
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


# ── v3.66.1207: the remote path delegates instead of reimplementing ──────────
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

    def fake_remote(argv, deadline, *, phase, retained="", fixed_timeout=None):
        # Stub the sole owned-process-group transport rather than exempting it,
        # so the Popen ban below stays live and catches a stray LOCAL launch.
        return (fake_scp(argv) if argv and argv[0] == "scp"
                else (fake_run(argv), True, ""))

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    monkeypatch.setattr(jobs, "_run_remote", fake_remote)
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


def test_remote_script_refuses_insecure_registry_before_scp(
        jobs, monkeypatch, tmp_path, capsys):
    script = tmp_path / "sweep.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")

    def outcome(cmd):
        if (cmd and cmd[0] == "ssh"
                and jobs._PREPARE_REGISTRY_FLAG in cmd[-1]):
            return (2, "", "REFUSED insecure registry")
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.main(["run", "--host", "somewhere", "--purpose", "p",
                    "--script", str(script)])
    err = capsys.readouterr().err

    assert rc == 2
    assert any(jobs._PREPARE_REGISTRY_FLAG in c[-1]
               for c in calls if c and c[0] == "ssh")
    assert not any(c and c[0] == "scp" for c in calls), calls
    assert "could not prepare" in err and "insecure registry" in err


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


@pytest.mark.parametrize("disposition", ("ADOPTED", "NOT_ADOPTED"))
def test_status_8_remote_copy_follows_measured_record_survival(
        jobs, monkeypatch, tmp_path, capsys, disposition):
    script = tmp_path / ("status-8-%s.sh" % disposition.lower())
    script.write_text("echo hi\n", encoding="utf-8")

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            stdout = "target-retained-id\n" if disposition == "ADOPTED" else ""
            return (8, stdout, _status_record_for_payload(
                jobs, cmd[-1], 8, disposition=disposition))
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.cmd_run(_remote_args(
        script=str(script), request_id="status-8-%s" % disposition.lower()))
    out, err = capsys.readouterr()
    scp = [c for c in calls if c and c[0] == "scp"]
    cleanup = [
        c for c in calls if c and c[0] == "ssh"
        and jobs._ATTEMPT_CLEANUP_FLAG in shlex.split(c[-1])]

    assert rc == 8 and len(scp) == 1
    copied = scp[0][-1].split(":", 1)[1]
    if disposition == "ADOPTED":
        assert out == "target-retained-id\n"
        assert cleanup == []
        assert "target RETAINED a record" in err and copied in err
    else:
        assert out == ""
        assert len(cleanup) == 1 and copied in cleanup[0][-1]
        assert "target refused;" in err and "removed the captured copy" in err


@pytest.mark.parametrize("status", (10, 11))
@pytest.mark.parametrize("disposition", ("ADOPTED", "NOT_ADOPTED"))
def test_remote_exec_status_preserves_copy_and_requires_adopted_disposition(
        jobs, monkeypatch, tmp_path, capsys, status, disposition):
    script = tmp_path / "exec-status.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    monkeypatch.setenv("BD_JOBS_REMOTE_SELF", "/opt/bd/bd-jobs")

    def outcome(cmd):
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            return (status, "target-exec-id\n", _status_record_for_payload(
                jobs, cmd[-1], status, disposition=disposition))
        return (0, "", "")

    calls = _fake_transport(jobs, monkeypatch, outcome)
    rc = jobs.main(["run", "--host", "somewhere", "--purpose", "p",
                    "--script", str(script)])
    out, err = capsys.readouterr()
    expected = (status if disposition == "ADOPTED"
                else jobs._EXIT_REMOTE_UNKNOWN)
    assert rc == expected
    if disposition == "ADOPTED":
        assert out.strip().splitlines()[-1] == "target-exec-id"
    else:
        assert out == "", "unauthenticated target stdout was promoted as an id"
    scp = [c for c in calls if c and c[0] == "scp"]
    assert len(scp) == 1
    copied = scp[0][-1].split(":", 1)[1]
    cleanup = [
        c for c in calls if c and c[0] == "ssh"
        and jobs._ATTEMPT_CLEANUP_FLAG in shlex.split(c[-1])
    ]
    assert cleanup == [], "retained/unknown target authority allowed cleanup"
    assert copied in err and "RETAINED" in err
    if disposition == "ADOPTED":
        expected_retained = (
            "target RETAINED a record; %s is left for it to collect" % copied)
        assert err.count(expected_retained) == 1, err
    else:
        assert "did not carry ADOPTED disposition" in err


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


def test_a_target_address_with_control_characters_is_refused(
        jobs, monkeypatch, capsys):
    """A resolved address goes into an argv AND into diagnostics. A newline or
    an option-shaped label there is not something to quote around: it is
    something no legitimate target has."""
    calls = _fake_transport(jobs, monkeypatch)
    for hostile in ("has space", "line\nbreak", "-oProxyCommand=touch /tmp/x",
                    "bell\x07"):
        before = len(calls)
        rc = jobs.cmd_run(_remote_args(host=hostile,
                                       command=["--", "sleep", "45"]))
        out, err = capsys.readouterr()
        assert rc == 2, (
            "a target address containing %r was accepted (rc=%r)"
            % (hostile, rc))
        assert len(calls) == before, "hostile target reached transport: %r" % calls
        assert out == "" and "REFUSED: the resolved target" in err
        expected = "option-shaped" if hostile.startswith("-") else "contains"
        assert expected in err and "nothing was copied or launched" in err


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


# ── v3.66.1207: adjudicated ownership remediation G1-G7 ─────────────────────


def test_repeated_forget_never_rebinds_cleanup_authority_after_final_absence(
        jobs, sleeper):
    p = sleeper()
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
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
    real_create = jobs._RegistryTxn.create_exclusive
    real_fsync = jobs.os.fsync
    real_unlink = jobs._RegistryTxn.unlink_name
    staged = {}
    fired = []

    def capture_stage(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if prefix == jobs._ENTRY_TEMP_PREFIX:
            fired.append("create-exclusive")
            staged.update(
                txn=txn, component=component, fd=fd,
                path=txn.display_path(component))
        return component, fd

    def fail_stage_fsync(fd):
        if fd == staged.get("fd"):
            fired.append("file-fsync")
            raise OSError(5, "injected staged fsync EIO")
        return real_fsync(fd)

    def fail_stage_unlink(txn, component):
        if component == staged.get("component"):
            fired.append("unlink-name")
            raise PermissionError("injected stage unlink refusal")
        return real_unlink(txn, component)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_stage)
    monkeypatch.setattr(jobs.os, "fsync", fail_stage_fsync)
    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", fail_stage_unlink)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "retained stage", "sleep 60")

    assert fired == ["create-exclusive", "file-fsync", "unlink-name"], fired
    assert staged["txn"].display_root == jobs.JOBS_DIR
    assert staged["path"].exists(), staged
    assert getattr(excinfo.value, "cleanup_complete", None) is False
    assert getattr(excinfo.value, "retained_paths", None) == [
        str(staged["path"])]
    assert str(staged["path"]) in str(excinfo.value)
    assert list(jobs.JOBS_DIR.glob("*.json")) == []


def test_stage_fdopen_failure_accounts_for_raw_fd_and_path(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_create = jobs._RegistryTxn.create_exclusive
    real_fdopen = jobs.os.fdopen
    real_close = jobs.os.close
    opened = {}
    closed = []

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if prefix == jobs._ENTRY_TEMP_PREFIX:
            opened.update(
                txn=txn, component=component, fd=fd,
                path=txn.display_path(component))
        return component, fd

    def fail_fdopen(fd, *args, **kwargs):
        if fd == opened.get("fd"):
            raise OSError(24, "injected fdopen failure")
        return real_fdopen(fd, *args, **kwargs)

    def capture_close(fd):
        if fd == opened.get("fd"):
            closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(jobs.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(jobs.os, "close", capture_close)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "fdopen failure", "sleep 60")

    assert opened["txn"].display_root == jobs.JOBS_DIR
    assert closed == [opened["fd"]], (opened, closed)
    assert not opened["path"].exists(), opened
    assert getattr(excinfo.value, "cleanup_complete", None) is True
    assert getattr(excinfo.value, "retained_paths", None) == []


def test_stage_fdopen_and_raw_close_unknown_retain_without_unlink_or_retry(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_create = jobs._RegistryTxn.create_exclusive
    real_fdopen = jobs.os.fdopen
    real_close = jobs.os.close
    real_unlink = jobs._RegistryTxn.unlink_name
    staged = {}
    closes = []
    unlinks = []

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if prefix == jobs._ENTRY_TEMP_PREFIX:
            staged.update(
                txn=txn, component=component, fd=fd,
                path=txn.display_path(component))
        return component, fd

    def fail_fdopen(fd, *args, **kwargs):
        if fd == staged.get("fd"):
            raise OSError(24, "PRIMARY injected fdopen EMFILE")
        return real_fdopen(fd, *args, **kwargs)

    def fail_raw_close(fd):
        if fd == staged.get("fd"):
            closes.append(fd)
            real_close(fd)
            raise OSError(5, "SECONDARY injected raw close EIO")
        return real_close(fd)

    def capture_unlink(txn, component):
        if component == staged.get("component"):
            unlinks.append(component)
        return real_unlink(txn, component)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(jobs.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(jobs.os, "close", fail_raw_close)
    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", capture_unlink)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "fdopen close unknown", "sleep 60")

    assert staged["txn"].display_root == jobs.JOBS_DIR
    assert closes == [staged["fd"]], (staged, closes)
    assert unlinks == [], "close UNKNOWN authorized an unlink: %r" % unlinks
    assert staged["path"].exists(), "the possibly-open stage lost its name"
    assert getattr(excinfo.value, "cleanup_complete", None) is False
    assert getattr(excinfo.value, "retained_paths", None) == [
        str(staged["path"])]
    message = str(excinfo.value)
    assert "PRIMARY injected fdopen EMFILE" in message, message
    assert "SECONDARY injected raw close EIO" in message, message
    assert str(staged["path"]) in message, message


def test_stage_fdopen_cleanup_unlink_failure_is_attempted_only_once(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_create = jobs._RegistryTxn.create_exclusive
    real_fdopen = jobs.os.fdopen
    real_unlink = jobs._RegistryTxn.unlink_name
    staged = {}
    unlinks = []

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if prefix == jobs._ENTRY_TEMP_PREFIX:
            staged.update(
                txn=txn, component=component, fd=fd,
                path=txn.display_path(component))
        return component, fd

    def fail_fdopen(fd, *args, **kwargs):
        if fd == staged.get("fd"):
            raise OSError(24, "PRIMARY injected fdopen EMFILE")
        return real_fdopen(fd, *args, **kwargs)

    def fail_first_unlink(txn, component):
        if component == staged.get("component"):
            unlinks.append(component)
            if len(unlinks) == 1:
                raise OSError(1, "SECONDARY injected stage unlink EPERM")
        return real_unlink(txn, component)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(jobs.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", fail_first_unlink)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "fdopen unlink failure", "sleep 60")

    assert staged["txn"].display_root == jobs.JOBS_DIR
    assert unlinks == [staged["component"]], (
        "stage cleanup was retried: %r" % unlinks)
    assert staged["path"].exists(), "a retry hid the first cleanup failure"
    assert getattr(excinfo.value, "cleanup_complete", None) is False
    assert getattr(excinfo.value, "retained_paths", None) == [
        str(staged["path"])]
    message = str(excinfo.value)
    assert "PRIMARY injected fdopen EMFILE" in message, message
    assert "SECONDARY injected stage unlink EPERM" in message, message
    assert str(staged["path"]) in message, message


def test_stage_cleanup_contains_exception_from_unlink_helper_boundary(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_create = jobs._RegistryTxn.create_exclusive
    real_fsync = jobs.os.fsync
    real_unlink = jobs._unlink_verified
    staged = {}
    fired = []

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if prefix == jobs._ENTRY_TEMP_PREFIX:
            fired.append("create-exclusive")
            staged.update(
                txn=txn, component=component, fd=fd,
                path=txn.display_path(component))
        return component, fd

    def fail_stage_fsync(fd):
        if fd == staged.get("fd"):
            fired.append("file-fsync")
            raise OSError(errno.EIO, "PRIMARY staged fsync EIO")
        return real_fsync(fd)

    def fail_unlink_helper(txn, component):
        if component == staged.get("component"):
            fired.append("unlink-verified")
            raise RuntimeError("SECONDARY unlink helper boundary fault")
        return real_unlink(txn, component)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(jobs.os, "fsync", fail_stage_fsync)
    monkeypatch.setattr(jobs, "_unlink_verified", fail_unlink_helper)

    with pytest.raises(jobs.RegistrationError) as raised:
        jobs.register(p.pid, "unlink boundary", "sleep 60")

    assert fired == ["create-exclusive", "file-fsync", "unlink-verified"]
    assert staged["txn"].display_root == jobs.JOBS_DIR
    assert staged["path"].exists()
    assert raised.value.cleanup_complete is False
    assert raised.value.retained_paths == [str(staged["path"])]
    rendered = jobs._exception_text(raised.value)
    assert "PRIMARY staged fsync EIO" in rendered
    assert "SECONDARY unlink helper boundary fault" in rendered
    assert "UNKNOWN" in rendered and str(staged["path"]) in rendered


def test_stage_cleanup_failure_is_attached_to_keyboard_interrupt_primary(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_create = jobs._RegistryTxn.create_exclusive
    real_fsync = jobs.os.fsync
    real_unlink = jobs._RegistryTxn.unlink_name
    staged = {}
    unlinks = []
    primary = KeyboardInterrupt("PRIMARY injected interrupt")

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if prefix == jobs._ENTRY_TEMP_PREFIX:
            staged.update(
                txn=txn, component=component, fd=fd,
                path=txn.display_path(component))
        return component, fd

    def interrupt_fsync(fd):
        if fd == staged.get("fd"):
            raise primary
        return real_fsync(fd)

    def fail_unlink(txn, component):
        if component == staged.get("component"):
            unlinks.append(component)
            raise OSError(1, "SECONDARY injected interrupt cleanup EPERM")
        return real_unlink(txn, component)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(jobs.os, "fsync", interrupt_fsync)
    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", fail_unlink)

    with pytest.raises(KeyboardInterrupt) as excinfo:
        jobs.register(p.pid, "interrupt cleanup", "sleep 60")

    assert excinfo.value is primary
    assert staged["txn"].display_root == jobs.JOBS_DIR
    assert unlinks == [staged["component"]]
    assert staged["path"].exists()
    assert excinfo.value.args == ("PRIMARY injected interrupt",)
    notes = "\n".join(getattr(excinfo.value, "__notes__", []))
    assert "SECONDARY injected interrupt cleanup EPERM" in notes, notes
    assert str(staged["path"]) in notes, notes


def test_stage_close_unknown_retains_named_path_without_retry_or_unlink(
        jobs, sleeper, monkeypatch):
    p = sleeper()
    real_create = jobs._RegistryTxn.create_exclusive
    real_fdopen = jobs.os.fdopen
    real_unlink = jobs._RegistryTxn.unlink_name
    staged = {}
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

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if prefix == jobs._ENTRY_TEMP_PREFIX:
            staged.update(
                txn=txn, component=component, fd=fd,
                path=txn.display_path(component))
        return component, fd

    def wrap_fdopen(fd, *args, **kwargs):
        return CloseUnknownFile(real_fdopen(fd, *args, **kwargs))

    def capture_unlink(txn, component):
        if component == staged.get("component"):
            unlinks.append(component)
        return real_unlink(txn, component)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(jobs.os, "fdopen", wrap_fdopen)
    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", capture_unlink)

    with pytest.raises(jobs.RegistrationError) as excinfo:
        jobs.register(p.pid, "close unknown", "sleep 60")

    assert staged["txn"].display_root == jobs.JOBS_DIR
    assert staged["path"].exists(), staged
    assert len(closes) == 1, "uncertain close was retried: %r" % closes
    assert staged["component"] not in unlinks, "possibly-open stage was unlinked"
    assert getattr(excinfo.value, "cleanup_complete", None) is False
    assert getattr(excinfo.value, "retained_paths", None) == [
        str(staged["path"])]
    assert ("close" in str(excinfo.value).lower()
            and str(staged["path"]) in str(excinfo.value))


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


@pytest.mark.parametrize(
    "failure", ("identity-transport", "identity-helper",
                "cleanup-transport", "cleanup-helper"))
def test_remote_copy_helpers_totalize_faults_as_retained_unknown(
        jobs, monkeypatch, tmp_path, capsys, failure):
    script = tmp_path / (failure + ".sh")
    script.write_text("sleep 45\n", encoding="utf-8")
    request_id = "r5-" + failure
    calls = _fake_transport(jobs, monkeypatch)

    if failure == "identity-helper":
        monkeypatch.setattr(
            jobs, "_capture_remote_copy_identity",
            lambda *_args: (_ for _ in ()).throw(
                RuntimeError("injected identity helper failure")))
    if failure == "cleanup-helper":
        monkeypatch.setattr(
            jobs, "_remove_remote_copy",
            lambda *_args: (_ for _ in ()).throw(
                RuntimeError("injected cleanup helper failure")))

    def transport(cmd, **_kwargs):
        cmd = list(cmd)
        calls.append(cmd)
        tokens = shlex.split(cmd[-1]) if cmd and cmd[0] == "ssh" else []
        if jobs._ATTEMPT_IDENTITY_FLAG in tokens:
            if failure == "identity-transport":
                raise FileNotFoundError("injected identity ssh missing")
            marker = _identity_record_for_command(jobs, cmd)
            return subprocess.CompletedProcess(cmd, 0, marker, "")
        if jobs._ATTEMPT_CLEANUP_FLAG in tokens:
            if failure == "cleanup-transport":
                raise FileNotFoundError("injected cleanup ssh missing")
            index = tokens.index(jobs._ATTEMPT_CLEANUP_FLAG)
            req, nonce, attempt = tokens[index + 1:index + 4]
            marker = "%s%s:%s:%s:REMOVED\n" % (
                jobs._REMOTE_CLEANUP_SENTINEL, req, nonce, attempt)
            return subprocess.CompletedProcess(cmd, 0, marker, "")
        if cmd and cmd[0] == "ssh" and jobs._REMOTE_STATUS_SENTINEL in cmd[-1]:
            return subprocess.CompletedProcess(
                cmd, jobs._EXIT_REGISTRATION_FAILED, "",
                _status_record_for_payload(
                    jobs, cmd[-1], jobs._EXIT_REGISTRATION_FAILED,
                    disposition="NOT_ADOPTED"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(jobs.subprocess, "run", transport)
    monkeypatch.setattr(  # v3.66.1223: same seam, now via the deadline funnel
        jobs, "_run_remote",
        lambda argv, deadline, **kw: (transport(argv), True, ""))

    rc = jobs.cmd_run(_remote_args(
        script=str(script), request_id=request_id))
    out, err = capsys.readouterr()

    copied = [call[-1].split(":", 1)[1]
              for call in calls if call and call[0] == "scp"]
    assert len(copied) == 1
    assert rc == jobs._EXIT_REMOTE_UNKNOWN
    assert request_id in err and copied[0] in err and "RETAINED" in err
    assert "Traceback" not in out + err
    launches = [call for call in calls
                if call and call[0] == "ssh"
                and jobs._REMOTE_STATUS_SENTINEL in call[-1]]
    cleanups = [call for call in calls
                if call and call[0] == "ssh"
                and jobs._ATTEMPT_CLEANUP_FLAG in shlex.split(call[-1])]
    if failure.startswith("identity"):
        assert launches == [] and cleanups == []
    else:
        assert len(launches) == 1
        assert len(cleanups) == (0 if failure == "cleanup-helper" else 1)


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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = jobs.JOBS_DIR / ".bd-jobs-script-captured-race.sh"
    path.write_text("captured owner\n", encoding="utf-8")
    captured = _test_registry_identity(jobs, path)
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
    monkeypatch.setattr(  # v3.66.1223: same seam, now via the deadline funnel
        jobs, "_run_remote",
        lambda argv, deadline, **kw: (fake_run(argv), True, ""))
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    adopted = jobs.JOBS_DIR / ".bd-jobs-script-new-attempt.sh"
    adopted.write_text("touch %s\nsleep 45\n" % marker, encoding="utf-8")
    recorded_logs = []
    real_open = jobs.open_job_log
    real_publish = jobs._LocalLaunch.publish
    prior = {}

    def capture_log(purpose, **kwargs):
        path, fd = real_open(purpose, **kwargs)
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    adopted = jobs.JOBS_DIR / ".bd-jobs-script-retained-attempt.sh"
    adopted.write_text("touch %s\nsleep 45\n" % marker, encoding="utf-8")
    recorded_logs = []
    real_open = jobs.open_job_log
    real_publish = jobs._LocalLaunch.publish
    prior = {}

    def capture_log(purpose, **kwargs):
        path, fd = real_open(purpose, **kwargs)
        recorded_logs.append(pathlib.Path(path))
        return path, fd

    def interleave_prior(self, cmd, origin):
        prior.update(jobs.register(
            self.pid, "prior retained owner", "sleep 60",
            request_id="prior-retained-request"))
        prior["bytes"] = pathlib.Path(prior["_path"]).read_bytes()
        return real_publish(self, cmd, origin)

    cleanup_calls = []

    def retain_current_component(path, observed, **kwargs):
        path = pathlib.Path(path)
        cleanup_calls.append(path)
        assert observed is not None
        if path == recorded_logs[0]:
            return (False,
                    "could not quarantine %s: injected log cleanup fault"
                    % path)
        assert path == adopted
        return (False,
                "RETAINED attempt file %s: injected script cleanup fault"
                % path)

    monkeypatch.setattr(jobs, "open_job_log", capture_log)
    monkeypatch.setattr(jobs._LocalLaunch, "publish", interleave_prior)
    monkeypatch.setattr(
        jobs, "_cleanup_attempt_identity", retain_current_component)

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
    assert cleanup_calls == [recorded_logs[0], adopted]


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
    real_pidfd_open = jobs.os.pidfd_open
    real_pidfd_signal = jobs.signal.pidfd_send_signal
    opened = {}

    def track_pidfd_open(pid, flags=0):
        fd = real_pidfd_open(pid, flags)
        opened[fd] = pid
        return fd

    def track_pidfd_signal(fd, sig, *args):
        assert fd in opened, "signal used a handle this test never observed"
        signaled.append(opened[fd])
        return real_pidfd_signal(fd, sig, *args)

    monkeypatch.setattr(jobs.os, "pidfd_open", track_pidfd_open)
    monkeypatch.setattr(jobs.signal, "pidfd_send_signal", track_pidfd_signal)
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
    cases = ("mkdir", "root-open", "stage-create")
    problems = []
    for case in cases:
        jobs.JOBS_DIR = tmp_path / ("registry-" + case)
        fired = []
        with pytest.MonkeyPatch.context() as mp:
            if case != "mkdir":
                jobs.JOBS_DIR.mkdir(parents=True, mode=0o700)
            if case == "mkdir":
                real_mkdir = jobs.os.mkdir
                def fail_mkdir(path, *args, **kwargs):
                    if (str(path) == jobs.JOBS_DIR.name
                            and kwargs.get("dir_fd") is not None):
                        fired.append(case)
                        raise OSError(28, "injected mkdir ENOSPC")
                    return real_mkdir(path, *args, **kwargs)
                mp.setattr(jobs.os, "mkdir", fail_mkdir)
            elif case == "root-open":
                real_open = jobs.os.open
                def fail_open(path, flags, *args, **kwargs):
                    if (str(path) == jobs.JOBS_DIR.name
                            and kwargs.get("dir_fd") is not None):
                        fired.append(case)
                        raise OSError(24, "injected root open EMFILE")
                    return real_open(path, flags, *args, **kwargs)
                mp.setattr(jobs.os, "open", fail_open)
            else:
                real_create = jobs._RegistryTxn.create_exclusive
                def fail_create(txn, prefix, suffix):
                    if prefix == jobs._ENTRY_TEMP_PREFIX:
                        fired.append(case)
                        assert txn.display_root == jobs.JOBS_DIR
                        raise OSError(
                            28, "injected stage exclusive-create ENOSPC")
                    return real_create(txn, prefix, suffix)
                mp.setattr(jobs._RegistryTxn, "create_exclusive", fail_create)
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
    jobs.JOBS_DIR.mkdir(parents=True, mode=0o700)
    real_open = jobs.os.open
    real_close = jobs.os.close
    lock_fd = []
    closes = []

    def capture_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if (str(path) == jobs.JOBS_DIR.name
                and kwargs.get("dir_fd") is not None):
            lock_fd.append(fd)
        return fd

    real_flock = jobs.fcntl.flock
    primary = OSError(5, "injected flock primary")

    def scoped_flock(fd, operation):
        if lock_fd and fd == lock_fd[0]:
            raise primary
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
    assert primary.args == (5, "injected flock primary")


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
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                path.parent.chmod(0o700)
                path.write_bytes(script.read_bytes())
                copied["path"] = path
                return subprocess.CompletedProcess(cmd, 0, "", "")
            tokens = shlex.split(cmd[-1]) if cmd[0] == "ssh" else []
            if jobs._ATTEMPT_IDENTITY_FLAG in tokens:
                index = tokens.index(jobs._ATTEMPT_IDENTITY_FLAG)
                request_id, nonce, attempt, path = tokens[index + 1:index + 5]
                with _open_test_registry_txn(
                        jobs, require_write=False, create=False) as txn:
                    component = txn.component_from_display(path)
                    observed = jobs._component_identity(txn, component)
                assert observed[0] == "present", observed
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
                with _open_test_registry_txn(jobs) as txn:
                    component = txn.component_from_display(path)
                    removed, note = jobs._cleanup_attempt_identity(
                        path, observed, _txn=txn, _component=component)
                marker = "%s%s:%s:%s:%s\n" % (
                    jobs._REMOTE_CLEANUP_SENTINEL, request_id, nonce, attempt,
                    "REMOVED" if removed else "RETAINED")
                return subprocess.CompletedProcess(cmd, 0, marker, note + "\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(jobs.subprocess, "run", fake_run)
                mp.setattr(  # v3.66.1223: same seam via the funnel
                    jobs, "_run_remote",
                    lambda argv, deadline, **kw: (fake_run(argv), True, ""))
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
        assert copied["replacement"] is not None, (rc, out, err)
        assert copied["path"].read_bytes() == copied["replacement"], (
            copied, rc, out, err, calls)
        assert str(copied["path"]) in err and "RETAINED" in err
        assert any(jobs._ATTEMPT_CLEANUP_FLAG in c[-1] for c in calls
                   if c and c[0] == "ssh")
        assert not any(c[-1].startswith("rm -f ") for c in calls
                       if c and c[0] == "ssh")


# ── R12: one pinned registry transaction ────────────────────────────────────

def test_locked_publication_never_resolves_into_replacement_registry_root(
        jobs, monkeypatch, tmp_path):
    """The immediate post-flock receipt is independently authoritative."""
    detached = tmp_path / "detached-root"
    swapped = {"done": False}
    after_lock_checks = []
    bypassed_later_checks = []
    real_flock = jobs.fcntl.flock
    real_require_attached = jobs._RegistryTxn.require_attached

    def swap_after_exclusive_lock(fd, operation):
        result = real_flock(fd, operation)
        if (not swapped["done"]
                and operation & jobs.fcntl.LOCK_EX
                and operation & jobs.fcntl.LOCK_NB):
            swapped["done"] = True
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
        return result

    def isolate_post_lock_check(txn, operation):
        if operation == "after registry lock":
            after_lock_checks.append(operation)
            return real_require_attached(txn, operation)
        if swapped["done"]:
            # This fixture isolates N175.  If the immediate check is deleted,
            # later receipts must not rescue that mutant before it grants the
            # publication result from the detached descriptor.
            bypassed_later_checks.append(operation)
            return jobs._RootReceipt(
                jobs._OBS_PRESENT, txn.root_identity, operation, None)
        return real_require_attached(txn, operation)

    monkeypatch.setattr(jobs.fcntl, "flock", swap_after_exclusive_lock)
    monkeypatch.setattr(
        jobs._RegistryTxn, "require_attached", isolate_post_lock_check)
    final_name = "%s-%d.json" % (jobs.socket.gethostname(), os.getpid())

    with pytest.raises(jobs.RegistrationError) as raised:
        jobs.register(os.getpid(), "root swap", "true")

    assert swapped["done"], "the fixture never swapped the root after flock"
    assert after_lock_checks == ["after registry lock"]
    assert bypassed_later_checks == [], (
        "a later guard, rather than the immediate post-lock guard, refused")
    assert "ROOT_DETACHED" in jobs._exception_text(raised.value)
    assert not (jobs.JOBS_DIR / final_name).exists()
    assert not (detached / final_name).exists()
    assert not list(jobs.JOBS_DIR.glob(".bd-jobs-entry-*.tmp"))
    assert not list(detached.glob(".bd-jobs-entry-*.tmp"))


def test_descriptor_relative_rename_uses_pinned_root_after_precheck_race(
        jobs, monkeypatch, tmp_path):
    """A root swap after the rename precheck cannot redirect its effect."""
    detached = tmp_path / "pre-rename-detached"
    real_require_attached = jobs._RegistryTxn.require_attached
    swapped = {"done": False, "stage": None}

    def swap_after_rename_precheck(txn, operation):
        receipt = real_require_attached(txn, operation)
        if operation == "before rename" and not swapped["done"]:
            stages = list(jobs.JOBS_DIR.glob(
                jobs._ENTRY_TEMP_PREFIX + "*" + jobs._ENTRY_TEMP_SUFFIX))
            assert len(stages) == 1, stages
            stage = stages[0]
            stage_bytes = stage.read_bytes()
            swapped["done"] = True
            swapped["stage"] = stage.name
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
            # A path-reopening mutant now sees a valid same-name source in B.
            # Descriptor-relative rename must continue to consume A's stage.
            (jobs.JOBS_DIR / stage.name).write_bytes(stage_bytes)
        return receipt

    monkeypatch.setattr(
        jobs._RegistryTxn, "require_attached", swap_after_rename_precheck)
    final_name = "%s-%d.json" % (jobs.socket.gethostname(), os.getpid())

    with pytest.raises(jobs.PublishedNotDurable) as raised:
        jobs.register(os.getpid(), "pre-rename race", "true")

    assert swapped["done"] and swapped["stage"] is not None
    assert not (jobs.JOBS_DIR / final_name).exists(), (
        "publication was redirected into replacement root B")
    assert (jobs.JOBS_DIR / swapped["stage"]).is_file(), (
        "production consumed the replacement-root decoy stage")
    assert (detached / final_name).is_file(), (
        "descriptor-relative rename did not publish within pinned root A")
    assert "ROOT_DETACHED" in jobs._exception_text(raised.value)


def test_loaded_snapshot_never_forgets_from_a_replacement_registry_root(
        jobs, tmp_path):
    """Pinned reads and loaded cleanup authority both stay bound to root A."""
    jobs.JOBS_DIR.mkdir(mode=0o700)
    artifact = jobs.JOBS_DIR / "snapshot-owned.txt"
    artifact.write_text("root-a artifact\n", encoding="utf-8")
    entry = jobs.register(
        os.getpid(), "snapshot root", "true", owned_paths=[str(artifact)])
    loaded = jobs.load_all()[0]
    final_name = pathlib.Path(entry["_path"]).name
    final_bytes = (jobs.JOBS_DIR / final_name).read_bytes()
    txn_a = jobs._RegistryTxn(
        jobs.JOBS_DIR, require_write=False, create=False)
    assert txn_a.open()

    detached = tmp_path / "snapshot-root-a"
    jobs.JOBS_DIR.rename(detached)
    jobs.JOBS_DIR.mkdir(mode=0o700)
    replacement_final = jobs.JOBS_DIR / final_name
    replacement_artifact = jobs.JOBS_DIR / artifact.name
    replacement_entry = dict(json.loads(final_bytes.decode("utf-8")))
    replacement_entry["purpose"] = "root-b read decoy"
    replacement_final.write_text(
        json.dumps(replacement_entry), encoding="utf-8")
    replacement_artifact.write_text("root-b decoy\n", encoding="utf-8")

    try:
        receipt = txn_a.read_entry(final_name)
    finally:
        txn_a.close()

    assert receipt.state == jobs._OBS_PRESENT
    assert receipt.raw_bytes == final_bytes
    assert receipt.entry["purpose"] == "snapshot root"
    # Restore byte equality so N177, independently of N173, reaches the
    # replacement-root unlink authority it must never receive.
    replacement_final.write_bytes(final_bytes)

    outcome = jobs.forget(loaded)

    assert not outcome["cleanup_complete"], outcome
    assert replacement_final.read_bytes() == final_bytes
    assert replacement_artifact.read_text(encoding="utf-8") == "root-b decoy\n"
    assert (detached / final_name).read_bytes() == final_bytes
    assert (detached / artifact.name).read_text(encoding="utf-8") == (
        "root-a artifact\n")
    assert any("root" in note.lower() and "RETAIN" in note
               for note in outcome["notes"]), outcome


def test_local_log_and_publication_share_one_pinned_registry_root(
        jobs, monkeypatch, tmp_path, capsys):
    """Log creation must use the transaction retained before a root swap."""
    marker = tmp_path / "work-ran"
    detached = tmp_path / "local-detached-root"
    real_txn_open = jobs._RegistryTxn.open
    swapped = {"done": False}

    def swap_after_retained_transaction_open(txn):
        opened = real_txn_open(txn)
        if not swapped["done"]:
            assert opened and txn.require_write and txn.create
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
            swapped["done"] = True
        return opened

    monkeypatch.setattr(
        jobs._RegistryTxn, "open", swap_after_retained_transaction_open)
    code = "from pathlib import Path; Path(%r).write_text('ran')" % str(marker)
    args = type("Args", (), {
        "host": "local", "purpose": "pinned local root",
        "command": ["--", sys.executable, "-c", code], "script": None,
        "origin": None, "request_id": "r12-local-root",
        "adopt_script": None, "attempt_token": None,
    })()

    rc = jobs.cmd_run(args)
    _out, err = capsys.readouterr()
    deadline = time.monotonic() + 1.0
    while marker.exists() is False and time.monotonic() < deadline:
        time.sleep(0.01)

    assert swapped["done"], "the fixture never detached the log's root"
    assert rc == 3, (rc, err)
    assert not marker.exists(), "work ran after its registry root detached"
    assert list(jobs.JOBS_DIR.iterdir()) == [], (
        "a separately reopened log leaked into the replacement root")
    assert list(detached.iterdir()) == [], (
        "the retained transaction created a log after its root detached")
    assert "ROOT_DETACHED" in err and "root" in err.lower(), err


def test_local_release_rechecks_root_after_publication(
        jobs, monkeypatch, tmp_path, capsys):
    """Detach immediately before the release grant and keep work gated."""
    marker = tmp_path / "pre-release-must-not-run"
    detached = tmp_path / "pre-release-detached"
    real_release = jobs._LocalLaunch.release
    swapped = {"done": False, "final": None}

    def detach_immediately_before_release(launch):
        assert launch.entry is not None and not launch.released
        final = jobs.JOBS_DIR / (launch.entry["id"] + ".json")
        assert final.is_file(), "release schedule ran before publication"
        swapped["done"] = True
        swapped["final"] = final.name
        jobs.JOBS_DIR.rename(detached)
        jobs.JOBS_DIR.mkdir(mode=0o700)
        return real_release(launch)

    monkeypatch.setattr(
        jobs._LocalLaunch, "release", detach_immediately_before_release)
    rc = jobs.cmd_run(_run_args(
        "pre-release attachedness", _marker_command(marker)))
    out, err = capsys.readouterr()
    time.sleep(0.05)

    assert swapped["done"], "the fixture never reached the release grant"
    assert rc == jobs._EXIT_PUBLISHED_NOT_DURABLE, (rc, out, err)
    assert out.strip() == swapped["final"].removesuffix(".json"), out
    assert not marker.exists(), "work ran from a detached registry root"
    assert list(jobs.JOBS_DIR.iterdir()) == []
    assert (detached / swapped["final"]).is_file()
    assert any(path.suffix == ".log" for path in detached.iterdir())
    assert "release was withheld" in err and "ROOT_DETACHED" in err, err


def test_registry_namespace_effects_are_descriptor_relative(
        jobs, monkeypatch):
    """Any path-form registry effect must trip this syscall boundary."""
    registry = str(jobs.JOBS_DIR)
    parent = str(jobs.JOBS_DIR.parent)
    leaf = jobs.JOBS_DIR.name
    real_open = jobs.os.open
    real_stat = jobs.os.stat
    real_mkdir = jobs.os.mkdir
    real_rename = jobs.os.rename
    real_link = jobs.os.link
    real_unlink = jobs.os.unlink
    real_scandir = jobs.os.scandir
    real_fsync = jobs.os.fsync
    parent_fds = set()
    root_fds = set()
    ledger = []
    race = {"done": False}

    def component(value):
        return (type(value) is str and value not in ("", ".", "..")
                and "/" not in value and "\0" not in value)

    def inside(value):
        try:
            text = os.fspath(value)
        except TypeError:
            return False
        return text == registry or text.startswith(registry + os.sep)

    def guarded_open(path, flags, mode=0o777, *, dir_fd=None):
        if os.fspath(path) == parent and dir_fd is None:
            fd = real_open(path, flags, mode)
            parent_fds.add(fd)
            ledger.append(("parent-open", fd))
            return fd
        if path == leaf and dir_fd in parent_fds:
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            root_fds.add(fd)
            ledger.append(("root-open", fd, dir_fd))
            return fd
        if dir_fd in root_fds:
            assert component(path), path
            ledger.append(("child-open", path, dir_fd))
            return real_open(path, flags, mode, dir_fd=dir_fd)
        if inside(path):
            pytest.fail("path-form registry os.open: %r" % (path,))
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def guarded_stat(path, *, dir_fd=None, follow_symlinks=True):
        if path == leaf and dir_fd in parent_fds:
            assert follow_symlinks is False
            ledger.append(("root-stat", path, dir_fd))
        elif dir_fd in root_fds:
            assert component(path) and follow_symlinks is False
            ledger.append(("child-stat", path, dir_fd))
        elif inside(path):
            pytest.fail("path-form registry os.stat: %r" % (path,))
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def guarded_mkdir(path, mode=0o777, *, dir_fd=None):
        if path == leaf and dir_fd in parent_fds:
            ledger.append(("root-mkdir", path, dir_fd))
            return real_mkdir(path, mode, dir_fd=dir_fd)
        if inside(path):
            pytest.fail("path-form registry os.mkdir: %r" % (path,))
        if dir_fd is None:
            return real_mkdir(path, mode)
        return real_mkdir(path, mode, dir_fd=dir_fd)

    def guarded_rename(source, destination, *, src_dir_fd=None,
                       dst_dir_fd=None):
        assert src_dir_fd in root_fds and dst_dir_fd == src_dir_fd
        assert component(source) and component(destination)
        ledger.append(("rename", source, destination, src_dir_fd, dst_dir_fd))
        if source == "owned-race.txt" and not race["done"]:
            race["done"] = True
            real_rename(source, ".r12-original", src_dir_fd=src_dir_fd,
                        dst_dir_fd=dst_dir_fd)
            fd = real_open(
                source, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600, dir_fd=src_dir_fd)
            try:
                os.write(fd, b"replacement")
            finally:
                os.close(fd)
        return real_rename(source, destination, src_dir_fd=src_dir_fd,
                           dst_dir_fd=dst_dir_fd)

    def guarded_link(source, destination, *, src_dir_fd=None,
                     dst_dir_fd=None, follow_symlinks=True):
        assert src_dir_fd in root_fds and dst_dir_fd == src_dir_fd
        assert component(source) and component(destination)
        assert follow_symlinks is False
        ledger.append(("link", source, destination, src_dir_fd, dst_dir_fd))
        return real_link(source, destination, src_dir_fd=src_dir_fd,
                         dst_dir_fd=dst_dir_fd, follow_symlinks=False)

    def guarded_unlink(path, *, dir_fd=None):
        assert dir_fd in root_fds and component(path), (path, dir_fd)
        ledger.append(("unlink", path, dir_fd))
        return real_unlink(path, dir_fd=dir_fd)

    def guarded_scandir(path):
        assert path in root_fds, path
        ledger.append(("scandir", path))
        return real_scandir(path)

    def guarded_fsync(fd):
        if fd in root_fds:
            ledger.append(("dir-fsync", fd))
        return real_fsync(fd)

    monkeypatch.setattr(jobs.os, "open", guarded_open)
    monkeypatch.setattr(jobs.os, "stat", guarded_stat)
    monkeypatch.setattr(jobs.os, "mkdir", guarded_mkdir)
    monkeypatch.setattr(jobs.os, "rename", guarded_rename)
    monkeypatch.setattr(jobs.os, "link", guarded_link)
    monkeypatch.setattr(jobs.os, "unlink", guarded_unlink)
    monkeypatch.setattr(jobs.os, "scandir", guarded_scandir)
    monkeypatch.setattr(jobs.os, "fsync", guarded_fsync)
    monkeypatch.setattr(
        jobs.tempfile, "mkstemp",
        lambda *a, **k: pytest.fail("registry tempfile.mkstemp path fallback"))

    log_path, log_fd = jobs.open_job_log("descriptor boundary")
    os.close(log_fd)
    txn = jobs._RegistryTxn(jobs.JOBS_DIR, require_write=True, create=True)
    assert txn.open()
    try:
        owned_component, owned_fd = txn.create_exclusive("owned-race", ".txt")
        os.close(owned_fd)
        txn.rename_name(owned_component, "owned-race.txt")
    finally:
        txn.close()
    entry = jobs.register(
        os.getpid(), "descriptor boundary", "true", log=log_path,
        log_owned=True, owned_paths=[str(jobs.JOBS_DIR / "owned-race.txt")])
    loaded = jobs.load_all()
    assert [item["id"] for item in loaded] == [entry["id"]]
    outcome = jobs.forget(loaded[0])
    assert outcome["entry_absent_durable"], outcome
    assert race["done"], "the fixture never exercised restore-link cleanup"
    kinds = [item[0] for item in ledger]
    for required in ("root-mkdir", "root-open", "child-open", "root-stat",
                     "rename", "link", "unlink", "scandir", "dir-fsync"):
        assert required in kinds, (required, ledger)


def _r12_disk_entry(jobs, *, purpose="r12", request_id=None,
                    attempt_token=None, owned_paths=None):
    pid = os.getpid()
    entry = {
        "id": "%s-%d" % (jobs.socket.gethostname(), pid),
        "pid": pid,
        "starttime": jobs.proc_starttime(pid),
        "purpose": purpose,
        "cmd": "true",
        "host": jobs.socket.gethostname(),
        "origin": jobs.socket.gethostname(),
        "started_at": "2026-08-22T00:00:00Z",
        "log": None,
    }
    if request_id is not None:
        entry["request_id"] = request_id
    if attempt_token is not None:
        entry["attempt_token"] = attempt_token
    if owned_paths is not None:
        entry["owned_paths"] = list(owned_paths)
    assert jobs._entry_schema_reason(entry) is None
    return entry


@pytest.mark.parametrize(
    "bad_name", ["", ".", "..", "a/b", "../x", "nul\0name", 42, "\ud800"])
def test_registrytxn_refuses_multicomponent_name_before_any_registry_syscall(
        jobs, monkeypatch, bad_name):
    """Weakening component validation must let one operation reach the OS."""
    txn = jobs._RegistryTxn(jobs.JOBS_DIR, require_write=True, create=True)
    calls = []

    def forbidden_open_check(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("invalid component passed validation and reached I/O")

    # Patch the transaction-local boundary, not ``jobs.os``: the latter is the
    # process-global os module and poisoning os.stat can prevent pytest from
    # recording the intended mutant failure in JUnit XML.
    monkeypatch.setattr(
        jobs._RegistryTxn, "_require_open", forbidden_open_check)
    operations = [
        lambda: txn.observe_name(bad_name),
        lambda: txn.read_entry(bad_name),
        lambda: txn.rename_name(bad_name, "valid"),
        lambda: txn.rename_name("valid", bad_name),
        lambda: txn.link_name(bad_name, "valid"),
        lambda: txn.link_name("valid", bad_name),
        lambda: txn.unlink_name(bad_name),
        lambda: txn.create_exclusive(bad_name, ".tmp"),
        lambda: txn.create_exclusive("valid", bad_name),
    ]
    for operation in operations:
        with pytest.raises(jobs.RegistrationError):
            operation()
        assert calls == []


def test_registrytxn_reads_identity_and_bytes_from_one_nofollow_fd(
        jobs, monkeypatch):
    """Reopening a name after fstat must return replacement bytes and fail."""
    jobs.JOBS_DIR.mkdir(mode=0o700)
    entry = _r12_disk_entry(jobs, purpose="opened inode")
    component = entry["id"] + ".json"
    final = jobs.JOBS_DIR / component
    original_bytes = json.dumps(entry).encode("utf-8")
    final.write_bytes(original_bytes)
    original_identity = ("present", final.stat().st_dev, final.stat().st_ino,
                         stat.S_IFREG)
    txn = jobs._RegistryTxn(jobs.JOBS_DIR, require_write=False, create=False)
    assert txn.open()
    real_fstat = jobs.os.fstat
    swapped = {"done": False}

    def replace_after_file_fstat(fd):
        observed = real_fstat(fd)
        if stat.S_ISREG(observed.st_mode) and not swapped["done"]:
            swapped["done"] = True
            final.rename(jobs.JOBS_DIR / "opened-old")
            decoy = dict(entry, purpose="replacement name")
            final.write_text(json.dumps(decoy), encoding="utf-8")
        return observed

    monkeypatch.setattr(jobs.os, "fstat", replace_after_file_fstat)
    try:
        receipt = txn.read_entry(component)
        assert receipt.state == jobs._OBS_PRESENT
        assert receipt.raw_bytes == original_bytes
        assert receipt.entry["purpose"] == "opened inode"
        assert receipt.identity == original_identity
        assert swapped["done"]

        symlink = jobs.JOBS_DIR / "symlink.json"
        symlink.symlink_to(final.name)
        directory = jobs.JOBS_DIR / "directory.json"
        directory.mkdir()
        assert txn.read_entry(symlink.name).state == jobs._OBS_UNKNOWN
        assert txn.read_entry(directory.name).state == jobs._OBS_UNKNOWN
    finally:
        txn.close()


def test_registrytxn_capability_refusal_precedes_every_writer_effect(
        jobs, monkeypatch, capsys):
    """Removing the capability gate must create a root, log, or child."""
    effects = []
    monkeypatch.setattr(
        jobs, "_registry_capability_reason",
        lambda: "host lacks directory-fd support for os.rename")
    monkeypatch.setattr(
        jobs.os, "mkdir",
        lambda *a, **k: effects.append("mkdir") or pytest.fail("mkdir ran"))
    monkeypatch.setattr(
        jobs._RegistryTxn, "create_exclusive",
        lambda *a, **k: effects.append("create-exclusive") or pytest.fail(
            "create_exclusive ran"))
    monkeypatch.setattr(
        jobs.subprocess, "Popen",
        lambda *a, **k: effects.append("Popen") or pytest.fail("Popen ran"))
    args = type("Args", (), {
        "host": "local", "purpose": "unsupported registry",
        "command": ["--", "true"], "script": None, "origin": None,
        "request_id": "r12-capability", "adopt_script": None,
        "attempt_token": None,
    })()

    rc = jobs.cmd_run(args)
    _out, err = capsys.readouterr()

    assert rc == jobs._EXIT_REGISTRATION_FAILED
    assert effects == []
    assert not jobs.JOBS_DIR.exists()
    assert "REFUSED" in err and "directory-fd" in err


def test_registrytxn_flock_pins_the_same_descriptor_used_by_effects(
        jobs, monkeypatch):
    """Locking a different fd from rename/unlink/fsync must fail this ledger."""
    flocked = []
    renamed = []
    unlinked = []
    fsynced = []
    real_flock = jobs.fcntl.flock
    real_rename = jobs.os.rename
    real_unlink = jobs.os.unlink
    real_fsync = jobs.os.fsync

    def record_flock(fd, operation):
        if operation & jobs.fcntl.LOCK_EX:
            flocked.append(fd)
        return real_flock(fd, operation)

    def record_rename(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        renamed.append((src_dir_fd, dst_dir_fd))
        return real_rename(source, destination, src_dir_fd=src_dir_fd,
                           dst_dir_fd=dst_dir_fd)

    def record_unlink(component, *, dir_fd=None):
        unlinked.append(dir_fd)
        return real_unlink(component, dir_fd=dir_fd)

    def record_fsync(fd):
        fsynced.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(jobs.fcntl, "flock", record_flock)
    monkeypatch.setattr(jobs.os, "rename", record_rename)
    monkeypatch.setattr(jobs.os, "unlink", record_unlink)
    monkeypatch.setattr(jobs.os, "fsync", record_fsync)
    txn = jobs._RegistryTxn(jobs.JOBS_DIR, require_write=True, create=True)
    assert txn.open()
    try:
        txn.lock()
        component, fd = txn.create_exclusive("flock-source-", ".tmp")
        os.close(fd)
        txn.rename_name(component, "flock-destination.tmp")
        txn.unlink_name("flock-destination.tmp")
        txn.fsync()
        txn.unlock()
    finally:
        txn.close()

    assert flocked and flocked[0] in unlinked and flocked[0] in fsynced
    assert renamed == [(flocked[0], flocked[0])]


def test_public_register_preserves_schema_and_private_root_receipt(jobs):
    """Serializing receipt evidence or omitting it from the return must fail."""
    returned = jobs.register(os.getpid(), "receipt", "true")
    final = pathlib.Path(returned["_path"])
    raw = final.read_bytes()
    on_disk = json.loads(raw.decode("utf-8"))

    assert on_disk == jobs._disk_entry(returned)
    assert set(key for key in on_disk if key.startswith("_registry_")) == set()
    assert returned._registry_root_identity
    assert returned._registry_component == final.name
    assert returned._registry_raw_bytes == raw


def test_attempt_disposition_scans_the_root_it_locked(
        jobs, monkeypatch, tmp_path):
    """Calling public load_all() under an old lock must adopt from root B."""
    request_id = "r12-disposition"
    attempt_token = "a" * 32
    path = str(jobs.JOBS_DIR / ".bd-jobs-script-r12.sh")
    planted = _r12_disk_entry(
        jobs, request_id=request_id, attempt_token=attempt_token,
        owned_paths=[path])
    detached = tmp_path / "disposition-detached"
    real_flock = jobs.fcntl.flock
    swapped = {"done": False}

    def swap_and_plant(fd, operation):
        result = real_flock(fd, operation)
        if (not swapped["done"] and operation & jobs.fcntl.LOCK_EX):
            swapped["done"] = True
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
            (jobs.JOBS_DIR / (planted["id"] + ".json")).write_text(
                json.dumps(planted), encoding="utf-8")
        return result

    monkeypatch.setattr(jobs.fcntl, "flock", swap_and_plant)

    assert jobs._attempt_disposition(
        request_id, attempt_token, path) == "UNKNOWN"
    assert swapped["done"]


def test_forget_root_mismatch_does_not_probe_replacement_artifacts(
        jobs, monkeypatch, tmp_path):
    """Pinned unlink and loaded cleanup authority cannot resolve into root B."""
    jobs.JOBS_DIR.mkdir(mode=0o700)
    artifact = jobs.JOBS_DIR / "never-probe.txt"
    artifact.write_text("root-a", encoding="utf-8")
    jobs.register(os.getpid(), "no probe", "true", owned_paths=[str(artifact)])
    loaded = jobs.load_all()[0]
    victim = jobs.JOBS_DIR / "unlink-race.txt"
    victim.write_text("root-a victim", encoding="utf-8")
    detached = tmp_path / "no-probe-detached"
    real_require_attached = jobs._RegistryTxn.require_attached
    swapped = {"done": False}
    txn_a = jobs._RegistryTxn(
        jobs.JOBS_DIR, require_write=True, create=False)
    assert txn_a.open()

    def swap_after_unlink_precheck(txn, operation):
        receipt = real_require_attached(txn, operation)
        if (operation == "before unlink" and not swapped["done"]):
            swapped["done"] = True
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
            (jobs.JOBS_DIR / victim.name).write_text(
                "root-b decoy", encoding="utf-8")
        return receipt

    monkeypatch.setattr(
        jobs._RegistryTxn, "require_attached", swap_after_unlink_precheck)
    try:
        txn_a.unlink_name(victim.name)
    finally:
        txn_a.close()

    assert swapped["done"], "the unlink precheck race never fired"
    assert not (detached / victim.name).exists(), (
        "descriptor-relative unlink did not consume root A's occupant")
    assert (jobs.JOBS_DIR / victim.name).read_text(encoding="utf-8") == (
        "root-b decoy")

    def forbidden(*args, **kwargs):
        pytest.fail("forget probed a replacement-root child")

    monkeypatch.setattr(jobs._RegistryTxn, "read_entry", forbidden)
    monkeypatch.setattr(jobs._RegistryTxn, "observe_name", forbidden)
    monkeypatch.setattr(jobs._RegistryTxn, "rename_name", forbidden)
    monkeypatch.setattr(jobs._RegistryTxn, "unlink_name", forbidden)

    outcome = jobs.forget(loaded)

    assert not outcome["cleanup_complete"]
    assert any("root" in note.lower() and "RETAIN" in note
               for note in outcome["notes"]), outcome


def test_detach_after_descriptor_relative_publish_withholds_release(
        jobs, monkeypatch, tmp_path, capsys):
    """Omitting the post-rename receipt must release work after root detach."""
    marker = tmp_path / "post-publish-work-ran"
    detached = tmp_path / "post-publish-detached"
    real_rename = jobs.os.rename
    real_mkdir = jobs.os.mkdir
    swapped = {"done": False, "final": None}

    def detach_after_final(source, destination, *, src_dir_fd=None,
                           dst_dir_fd=None):
        result = real_rename(source, destination, src_dir_fd=src_dir_fd,
                             dst_dir_fd=dst_dir_fd)
        if (not swapped["done"] and destination.endswith(".json")):
            swapped["done"] = True
            swapped["final"] = destination
            real_rename(str(jobs.JOBS_DIR), str(detached))
            real_mkdir(str(jobs.JOBS_DIR), 0o700)
        return result

    monkeypatch.setattr(jobs.os, "rename", detach_after_final)
    code = "from pathlib import Path; Path(%r).write_text('ran')" % str(marker)
    args = type("Args", (), {
        "host": "local", "purpose": "post publish detach",
        "command": ["--", sys.executable, "-c", code], "script": None,
        "origin": None, "request_id": "r12-post-publish",
        "adopt_script": None, "attempt_token": None,
    })()

    rc = jobs.cmd_run(args)
    _out, err = capsys.readouterr()
    deadline = time.monotonic() + 1.0
    while marker.exists() is False and time.monotonic() < deadline:
        time.sleep(0.01)

    assert swapped["done"], "the final publication never used os.rename"
    assert rc == 5, (rc, err)
    assert not marker.exists()
    assert list(jobs.JOBS_DIR.iterdir()) == []
    assert (detached / swapped["final"]).is_file()
    assert "ROOT_DETACHED" in err and "RETAIN" in err


def test_detach_after_idempotent_read_refuses_preserved_publication_grant(
        jobs, monkeypatch, tmp_path):
    """A valid old-root receipt cannot grant success after that root detaches."""
    first = jobs.register(os.getpid(), "idempotent old root", "true")
    final_name = pathlib.Path(first["_path"]).name
    detached = tmp_path / "idempotent-read-detached"
    real_read_entry = jobs._RegistryTxn.read_entry
    swapped = {"done": False}

    def detach_after_final_read(txn, component):
        receipt = real_read_entry(txn, component)
        if (not swapped["done"] and txn.locked
                and component == final_name
                and receipt.state == jobs._OBS_PRESENT):
            swapped["done"] = True
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
        return receipt

    monkeypatch.setattr(
        jobs._RegistryTxn, "read_entry", detach_after_final_read)

    with pytest.raises(jobs.RegistryRootDetached) as raised:
        jobs.register(os.getpid(), "idempotent old root", "true")

    assert swapped["done"], "the fixture never detached after the final read"
    assert "before preserved publication result" in str(raised.value)
    assert list(jobs.JOBS_DIR.iterdir()) == []
    assert (detached / final_name).is_file()
    assert not list(detached.glob(jobs._ENTRY_TEMP_PREFIX + "*"))


def test_detach_after_json_enumeration_returns_unknown_snapshot(
        jobs, monkeypatch, tmp_path):
    """Old-root component names are not healthy rows after enumeration detaches."""
    entry = jobs.register(os.getpid(), "enumerated old root", "true")
    final_name = pathlib.Path(entry["_path"]).name
    detached = tmp_path / "enumeration-detached"
    real_json_components = jobs._RegistryTxn.json_components
    swapped = {"done": False, "components": None}

    def detach_after_enumeration(txn):
        components = real_json_components(txn)
        if not swapped["done"]:
            swapped["done"] = True
            swapped["components"] = list(components)
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
        return components

    monkeypatch.setattr(
        jobs._RegistryTxn, "json_components", detach_after_enumeration)

    rows = jobs.load_all()

    assert swapped == {"done": True, "components": [final_name]}
    assert rows == [], "detached old-root rows were reported healthy: %r" % rows
    assert len(rows.malformed) == 1, rows.malformed
    malformed_path, reason = rows.malformed[0]
    assert pathlib.Path(malformed_path) == jobs.JOBS_DIR
    assert "ROOT_DETACHED/UNKNOWN after enumeration" in reason
    assert list(jobs.JOBS_DIR.iterdir()) == []
    assert (detached / final_name).is_file()


def test_registry_owner_settlement_closes_after_unlock_cancellation(jobs):
    """The shared funnel attempts close and keeps the first BaseException."""
    primary = KeyboardInterrupt("unlock cancellation")
    secondary = SystemExit(72)
    calls = []

    class FakeTxn:
        locked = True

        def unlock(self):
            calls.append("unlock")
            raise primary

        def close(self):
            calls.append("close")
            raise secondary

    settled, events = jobs._settle_registry_owner(
        FakeTxn(), unlock=True, close=True, context="owner census")

    assert settled is primary and calls == ["unlock", "close"]
    assert events == [
        ("registry unlock", primary), ("registry close", secondary)]
    assert any("registry close" in note and "72" in note
               for note in primary.__notes__), primary.__notes__


@pytest.mark.parametrize("owner", ("attempt-cleanup", "forget", "disposition"))
def test_registry_owning_wrappers_close_after_unlock_cancellation(
        jobs, monkeypatch, owner):
    """Every lock-owning public/helper lifetime funnels cancellation to close."""
    jobs.JOBS_DIR.mkdir(mode=0o700)
    path = jobs.JOBS_DIR / ("owner-%s.txt" % owner)
    path.write_text("owned", encoding="utf-8")
    observed_stat = path.stat()
    observed = ("present", observed_stat.st_dev, observed_stat.st_ino,
                stat.S_IFMT(observed_stat.st_mode))
    entry = None
    if owner == "forget":
        entry = jobs.register(
            os.getpid(), "owner close", "true", owned_paths=[str(path)])

    primary = KeyboardInterrupt("%s unlock cancellation" % owner)
    real_unlock = jobs._RegistryTxn.unlock
    real_close = jobs._RegistryTxn.close
    fired = []
    closed = []

    def unlock_then_cancel(txn, primary=None):
        result = real_unlock(txn, primary=primary)
        if not fired:
            fired.append(txn)
            raise primary_cancel
        return result

    def record_close(txn, primary=None):
        if fired and txn is fired[0]:
            closed.append((txn.dir_fd, txn.parent_fd))
        return real_close(txn, primary=primary)

    primary_cancel = primary
    monkeypatch.setattr(jobs._RegistryTxn, "unlock", unlock_then_cancel)
    monkeypatch.setattr(jobs._RegistryTxn, "close", record_close)

    with pytest.raises(BaseException) as raised:
        if owner == "attempt-cleanup":
            jobs._cleanup_attempt_identity(str(path), observed)
        elif owner == "forget":
            jobs.forget(entry)
        else:
            jobs._attempt_disposition(
                "owner-request", "a" * 32, str(path))

    assert raised.value is primary and type(raised.value) is KeyboardInterrupt
    assert len(fired) == 1 and len(closed) == 1, (fired, closed)
    assert fired[0].dir_fd is None and fired[0].parent_fd is None


def test_attempt_disposition_closes_after_lock_acquisition_cancellation(
        jobs, monkeypatch):
    """A post-flock BaseException cannot escape the owning close funnel."""
    primary = SystemExit(73)
    real_lock = jobs._RegistryTxn.lock
    real_close = jobs._RegistryTxn.close
    acquired = []
    closed = []

    def lock_then_cancel(txn):
        real_lock(txn)
        acquired.append(txn)
        raise primary

    def record_close(txn, primary=None):
        if acquired and txn is acquired[0]:
            closed.append((txn.dir_fd, txn.parent_fd, txn.locked))
        return real_close(txn, primary=primary)

    monkeypatch.setattr(jobs._RegistryTxn, "lock", lock_then_cancel)
    monkeypatch.setattr(jobs._RegistryTxn, "close", record_close)

    with pytest.raises(BaseException) as raised:
        jobs._attempt_disposition(
            "acquisition-request", "b" * 32,
            str(jobs.JOBS_DIR / ".bd-jobs-script-owner.sh"))

    assert raised.value is primary and type(raised.value) is SystemExit
    assert len(acquired) == 1 and len(closed) == 1
    assert acquired[0].dir_fd is None and acquired[0].parent_fd is None


def test_unlink_verified_detach_after_effect_never_grants_absence(
        jobs, monkeypatch, tmp_path):
    jobs.JOBS_DIR.mkdir(mode=0o700)
    component = "unlink-after-effect.txt"
    (jobs.JOBS_DIR / component).write_text("root-a", encoding="utf-8")
    txn = jobs._RegistryTxn(jobs.JOBS_DIR, require_write=True, create=False)
    assert txn.open()
    detached = tmp_path / "unlink-effect-detached"
    real_unlink = jobs._RegistryTxn.unlink_name
    fired = []

    def unlink_detach_then_raise(current, name):
        result = real_unlink(current, name)
        fired.append(name)
        jobs.JOBS_DIR.rename(detached)
        jobs.JOBS_DIR.mkdir(mode=0o700)
        (jobs.JOBS_DIR / name).write_text("root-b", encoding="utf-8")
        raise OSError(errno.EIO, "post-effect unlink EIO")

    monkeypatch.setattr(
        jobs._RegistryTxn, "unlink_name", unlink_detach_then_raise)
    try:
        removed, note = jobs._unlink_verified(txn, component)
    finally:
        txn.close()

    assert fired == [component] and removed is False
    assert "ROOT_DETACHED/UNKNOWN" in note and "RETAINED" in note
    assert (jobs.JOBS_DIR / component).read_text(encoding="utf-8") == "root-b"
    assert not (detached / component).exists()


def test_owned_absence_observation_detach_never_grants_cleanup_success(
        jobs, monkeypatch, tmp_path):
    jobs.JOBS_DIR.mkdir(mode=0o700)
    component = "owned-absence-race.txt"
    txn = jobs._RegistryTxn(jobs.JOBS_DIR, require_write=True, create=False)
    assert txn.open()
    detached = tmp_path / "owned-absence-detached"
    real_identity = jobs._component_identity
    fired = []

    def observe_absent_then_detach(current, name):
        identity = real_identity(current, name)
        if not fired and name == component:
            assert identity == ("absent",)
            fired.append(name)
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
            (jobs.JOBS_DIR / name).write_text("root-b", encoding="utf-8")
        return identity

    monkeypatch.setattr(
        jobs, "_component_identity", observe_absent_then_detach)
    try:
        removed, note, changed = jobs._unlink_owned_identity(
            txn, component, ("absent",))
    finally:
        txn.close()

    assert fired == [component]
    assert (removed, changed) == (False, False)
    assert "ROOT_DETACHED/UNKNOWN" in note and "RETAINED" in note
    assert (jobs.JOBS_DIR / component).read_text(encoding="utf-8") == "root-b"


def test_attempt_cleanup_detach_after_fsync_never_grants_success(
        jobs, monkeypatch, tmp_path):
    jobs.JOBS_DIR.mkdir(mode=0o700)
    path = jobs.JOBS_DIR / ".bd-jobs-script-fsync-detach.sh"
    path.write_text("root-a", encoding="utf-8")
    captured = _test_registry_identity(jobs, path)
    detached = tmp_path / "attempt-fsync-detached"
    real_fsync = jobs._RegistryTxn.fsync
    fired = []

    def fsync_then_detach(txn):
        result = real_fsync(txn)
        if not fired:
            fired.append(txn.root_identity)
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
            (jobs.JOBS_DIR / path.name).write_text("root-b", encoding="utf-8")
        return result

    monkeypatch.setattr(jobs._RegistryTxn, "fsync", fsync_then_detach)
    removed, note = jobs._cleanup_attempt_identity(path, captured)

    assert len(fired) == 1 and removed is False
    assert "ROOT_DETACHED" in note and "UNKNOWN" in note
    assert (jobs.JOBS_DIR / path.name).read_text(encoding="utf-8") == "root-b"


@pytest.mark.parametrize("detach_call", (1, 2))
def test_withdraw_detach_after_fsync_never_grants_no_publication(
        jobs, monkeypatch, tmp_path, detach_call):
    jobs.JOBS_DIR.mkdir(mode=0o700)
    component = "withdraw-fsync-detach.json"
    entry = _r12_disk_entry(jobs, purpose="withdraw detach")
    entry["id"] = component[:-len(".json")]
    entry["host"] = entry["id"].rsplit("-", 1)[0]
    # The helper's safety decision depends on existence, not schema parsing.
    (jobs.JOBS_DIR / component).write_text(
        json.dumps(entry), encoding="utf-8")
    txn = jobs._RegistryTxn(jobs.JOBS_DIR, require_write=True, create=False)
    assert txn.open()
    detached = tmp_path / ("withdraw-fsync-detached-%d" % detach_call)
    real_fsync = jobs._RegistryTxn.fsync
    calls = []

    def detach_after_selected_fsync(current):
        result = real_fsync(current)
        calls.append(current.root_identity)
        if len(calls) == detach_call:
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
            (jobs.JOBS_DIR / component).write_text(
                "root-b decoy", encoding="utf-8")
        return result

    monkeypatch.setattr(
        jobs._RegistryTxn, "fsync", detach_after_selected_fsync)
    try:
        outcome = jobs._withdraw_or_retain(
            txn, component, entry, RuntimeError("publication cause"))
    finally:
        txn.close()

    assert len(calls) == detach_call
    assert isinstance(outcome, jobs.PublishedNotDurable)
    assert "ROOT_DETACHED" in str(outcome)
    assert (jobs.JOBS_DIR / component).read_text(encoding="utf-8") == (
        "root-b decoy")


def test_open_job_log_receipt_cancellation_closes_assigned_fd_and_preserves_primary(
        jobs, monkeypatch):
    """A receipt cancellation settles the unreturned child fd exactly once."""
    primary = KeyboardInterrupt("injected log receipt cancellation")
    real_create = jobs._RegistryTxn.create_exclusive
    real_receipt = jobs._RegistryTxn.root_receipt
    real_close = jobs.os.close
    created = {}
    receipt_calls = []
    closes = []

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if suffix == ".log":
            created.update(
                txn=txn, component=component, fd=fd,
                path=txn.display_path(component))
        return component, fd

    def cancel_log_receipt(txn, operation):
        if operation == "after log creation":
            receipt_calls.append(operation)
            raise primary
        return real_receipt(txn, operation)

    def capture_close(fd):
        if fd == created.get("fd"):
            closes.append(fd)
        return real_close(fd)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(jobs._RegistryTxn, "root_receipt", cancel_log_receipt)
    monkeypatch.setattr(jobs.os, "close", capture_close)

    with pytest.raises(BaseException) as raised:
        jobs.open_job_log("receipt cancellation")

    assert raised.value is primary and type(primary) is KeyboardInterrupt
    assert receipt_calls == ["after log creation"]
    assert created["txn"].display_root == jobs.JOBS_DIR
    assert closes == [created["fd"]]
    assert not pathlib.Path("/proc/self/fd/%d" % created["fd"]).exists()
    assert created["path"].is_file(), "the retained log lost its pinned name"
    notes = "\n".join(getattr(primary, "__notes__", []))
    assert created["component"] in notes
    assert "RETAINED/UNKNOWN" in notes


def test_open_job_log_close_cancellation_wins_detached_receipt_and_retains_component(
        jobs, monkeypatch, tmp_path):
    """A real close cancellation outranks the synthetic detach refusal."""
    detached = tmp_path / "log-close-cancellation-root-a"
    cancellation = SystemExit(87)
    real_create = jobs._RegistryTxn.create_exclusive
    real_receipt = jobs._RegistryTxn.root_receipt
    real_close = jobs.os.close
    created = {}
    receipt_calls = []
    closes = []

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if suffix == ".log":
            created.update(txn=txn, component=component, fd=fd)
        return component, fd

    def detach_at_log_receipt(txn, operation):
        if operation == "after log creation":
            receipt_calls.append(operation)
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
            return jobs._RootReceipt(
                jobs._OBS_UNKNOWN, txn.root_identity, operation,
                "injected detach")
        return real_receipt(txn, operation)

    def close_then_cancel(fd):
        if fd == created.get("fd"):
            closes.append(fd)
            real_close(fd)
            raise cancellation
        return real_close(fd)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(
        jobs._RegistryTxn, "root_receipt", detach_at_log_receipt)
    monkeypatch.setattr(jobs.os, "close", close_then_cancel)

    with pytest.raises(BaseException) as raised:
        jobs.open_job_log("close cancellation precedence")

    assert raised.value is cancellation and cancellation.code == 87
    assert receipt_calls == ["after log creation"]
    assert closes == [created["fd"]]
    assert not pathlib.Path("/proc/self/fd/%d" % created["fd"]).exists()
    assert (detached / created["component"]).is_file()
    assert list(jobs.JOBS_DIR.iterdir()) == [], (
        "the replacement root received a log component")
    notes = "\n".join(getattr(cancellation, "__notes__", []))
    assert created["component"] in notes and "RETAINED/UNKNOWN" in notes
    assert "root receipt is detached/UNKNOWN" in notes


def test_quarantine_receipt_cancellation_closes_assigned_fd_and_preserves_primary(
        jobs, monkeypatch):
    """Quarantine receipt cancellation retains both named components."""
    jobs.JOBS_DIR.mkdir(mode=0o700)
    source = jobs.JOBS_DIR / "receipt-cancellation-source.txt"
    source.write_text("owned evidence\n", encoding="utf-8")
    primary = SystemExit(88)
    real_create = jobs._RegistryTxn.create_exclusive
    real_receipt = jobs._RegistryTxn.root_receipt
    real_close = jobs.os.close
    quarantine = {}
    receipt_calls = []
    closes = []

    def capture_create(txn, prefix, suffix):
        component, fd = real_create(txn, prefix, suffix)
        if prefix == ".bd-jobs-cleanup-":
            quarantine.update(
                txn=txn, component=component, fd=fd,
                path=txn.display_path(component))
        return component, fd

    def cancel_quarantine_receipt(txn, operation):
        if operation == "after cleanup quarantine creation":
            receipt_calls.append(operation)
            raise primary
        return real_receipt(txn, operation)

    def capture_close(fd):
        if fd == quarantine.get("fd"):
            closes.append(fd)
        return real_close(fd)

    monkeypatch.setattr(jobs._RegistryTxn, "create_exclusive", capture_create)
    monkeypatch.setattr(
        jobs._RegistryTxn, "root_receipt", cancel_quarantine_receipt)
    monkeypatch.setattr(jobs.os, "close", capture_close)

    with pytest.raises(BaseException) as raised:
        with _open_test_registry_txn(jobs) as txn:
            source_component = txn.component_from_display(source)
            observed = jobs._component_identity(txn, source_component)
            jobs._unlink_owned_identity(txn, source_component, observed)

    assert raised.value is primary and primary.code == 88
    assert receipt_calls == ["after cleanup quarantine creation"]
    assert quarantine["txn"] is txn
    assert closes == [quarantine["fd"]]
    assert not pathlib.Path("/proc/self/fd/%d" % quarantine["fd"]).exists()
    assert source.is_file() and quarantine["path"].is_file()
    notes = "\n".join(getattr(primary, "__notes__", []))
    assert str(source) in notes and str(quarantine["path"]) in notes
    assert quarantine["component"] in notes and "RETAINED/UNKNOWN" in notes


def test_detach_after_created_final_read_refuses_publication_grant(
        jobs, monkeypatch, tmp_path):
    """A detached final receipt cannot become a successful public result."""
    detached = tmp_path / "created-final-read-root-a"
    final_name = "%s-%d.json" % (jobs.socket.gethostname(), os.getpid())
    real_require_attached = jobs._RegistryTxn.require_attached
    fired = []

    def detach_before_created_grant(txn, operation):
        if operation == "before created publication result":
            fired.append(operation)
            assert (jobs.JOBS_DIR / final_name).is_file(), (
                "the grant seam ran before the final was re-read")
            jobs.JOBS_DIR.rename(detached)
            jobs.JOBS_DIR.mkdir(mode=0o700)
        return real_require_attached(txn, operation)

    monkeypatch.setattr(
        jobs._RegistryTxn, "require_attached", detach_before_created_grant)

    with pytest.raises(jobs.PublishedNotDurable) as raised:
        jobs.register(os.getpid(), "created final grant", "true")

    assert fired == ["before created publication result"]
    assert not (jobs.JOBS_DIR / final_name).exists(), (
        "detached root A granted publication into replacement root B")
    assert (detached / final_name).is_file(), (
        "the complete old-root final was not retained")
    assert not list(jobs.JOBS_DIR.iterdir())
    assert not list(detached.glob(jobs._ENTRY_TEMP_PREFIX + "*"))
    rendered = jobs._exception_text(raised.value)
    assert final_name in rendered and "ROOT_DETACHED" in rendered
    assert "RETAINED" in rendered
