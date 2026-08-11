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
