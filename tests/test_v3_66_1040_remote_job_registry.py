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
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-jobs"


def _load(name="bd_jobs"):
    """Import the extensionless tool as a module."""
    import importlib.util
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(_TOOL)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def jobs(tmp_path):
    mod = _load()
    mod.JOBS_DIR = tmp_path / "bd-jobs"
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


def test_reap_refuses_an_entry_with_no_start_time(jobs, sleeper, capsys):
    """A registry written by an older version carries no start time. That is
    not a reason to kill on the strength of a bare pid -- it is a reason to
    refuse and say why."""
    p = sleeper()
    entry = jobs.register(p.pid, "unit test", "sleep 60")
    path = jobs.JOBS_DIR / ("%s.json" % entry["id"])
    stripped = dict(entry)
    stripped.pop("starttime")
    path.write_text(json.dumps(stripped), encoding="utf-8")

    rc = jobs.cmd_reap(type("A", (), {"id": None})())
    err = capsys.readouterr().err
    assert rc == 1, "refusing must be a nonzero outcome, not a silent skip"
    assert "REFUSED" in err and "recycled" in err, err
    assert p.poll() is None, "bd-jobs killed a process it could not identify"
    assert jobs.load_all(), (
        "the unidentifiable entry was DROPPED. Its process is still running, so "
        "deleting the record turns a tracked job into exactly the untracked "
        "orphan this tool exists to prevent")


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
    path.write_text(json.dumps(dict(entry, starttime=entry["starttime"] + 1)),
                    encoding="utf-8")

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
    """
    seen = {}

    class FakeProc:
        pid = os.getpid()

    def fake_popen(cmd, **kw):
        seen["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    rc = jobs.cmd_run(type("A", (), {
        "host": "local", "purpose": "p", "command": ["--", "sleep", "90"]})())
    assert rc == 0
    assert seen["cmd"] == ["bash", "-c", "sleep 90"], (
        "the argparse separator reached the shell: %r" % (seen["cmd"],))


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
        rc = 0
        if cmd[0] == "ssh" and "test -f" in " ".join(cmd):
            rc = 0
        return subprocess.CompletedProcess(cmd, rc, "", "")

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    rc = jobs.cmd_run(type("A", (), {
        "host": "somewhere", "purpose": "sweep", "script": str(script),
        "command": []})())
    assert rc == 0, calls

    scp = [c for c in calls if c[0] == "scp"]
    assert scp, "the script was never copied: %s" % calls
    assert str(script) in scp[0], scp[0]
    assert calls.index(scp[0]) < len(calls) - 1, (
        "the copy was not the first thing done -- a script that did not arrive "
        "must be a refusal, not a job running the previous copy of itself")

    launched = " ".join(calls[-1])
    assert "bash /tmp/bd-jobs-script-" in launched, (
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
        rc = scp_rc if cmd and cmd[0] == "scp" else 0
        return subprocess.CompletedProcess(cmd, rc, "", "copy failed")

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
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


def test_run_refuses_when_the_script_could_not_be_copied(jobs, tmp_path,
                                                         monkeypatch, capsys):
    """A copy that failed leaves either nothing at the far end or a STALE
    script from a previous run under a different pid. Launching either is
    worse than refusing."""
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
    assert [c for c in calls if c and c[0] == "scp"], "scp was never attempted"
    assert not [c for c in calls if c and c[0] == "ssh"], (
        "it went on to talk to the host after the copy failed: %s" % calls)
