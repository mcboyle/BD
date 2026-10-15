"""The 1046 tool-state budget is the suite's CPU time; wall time is only a hang ceiling.

Row gate-model-cpu-budget r1 (PM-B ANSWER 2026-10-14 09:12Z,
harness-work/O1789/main-red/stale-gate-dbb2a1a/fix-r1). The old runner asserted
`elapsed <= wall_baseline x 2.0 x (1 + load1/cores)`. S64-D's loaded battery
stretched the 1040 suite 3.7x (45 -> 167 s) at a measured load factor of 1.36
and the gate refused correct work; the part size cancels out of that inequality,
so no split of the suite could clear it. These cases drive the real runner,
`_run_tool_state_suite`, with its subprocess, clock, CPU and load readings faked:

  * a healthy run on a loaded host passes (the old inequality fails it);
  * a CPU regression of 1.5x extra work fails idle AND loaded;
  * a run past the wall ceiling fails naming "HANG", not a raw TimeoutExpired;
  * a healthy idle run still passes, and the ceiling does not read load;
  * elapsed and CPU are reported on every run, pass or fail.

No case here loads the host, and only the one real hang waits (a 2s ceiling):
the measured loaded-host evidence is the row's ev/ probe, and these cases
replay its shape.
"""
import os
import resource
import subprocess
import sys
import time

import pytest

import test_v3_66_1046_gates_for_this_sessions_shapes as gate

BD_GATE_SCOPE = "module"

_SUITE = "tests/test_v3_66_1040_remote_job_registry.py"
_CORES = 64
# S64-D rep2: load factor 1.36 on a 64-core host, 1040 stretched 3.7x.
_LOADED = 23.0
_STRETCH = 3.7
# "Extra work = 1.5x B": the suite does its baseline work plus 1.5x more.
_REGRESSION = 2.5


def _wall(suite):
    return gate._SUITE_BASELINE_S[suite]


def _cpu_baseline(suite):
    # The CPU table is what this row adds; before it, a suite's CPU is
    # taken as its wall baseline so the same case runs on both trees.
    return getattr(gate, "_SUITE_CPU_BASELINE_S", gate._SUITE_BASELINE_S)[suite]


def _replay(seq):
    """Hand out seq in order, then keep repeating its last value."""
    seq = list(seq)

    def nxt():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return nxt


@pytest.fixture
def harness(monkeypatch, tmp_path):
    jobs, runctx = tmp_path / "jobs", tmp_path / "runctx"
    jobs.mkdir()
    runctx.mkdir()
    monkeypatch.setattr(gate, "_REAL_STATE", (str(jobs),))
    monkeypatch.setattr(gate, "_PER_RUN_STATE", str(runctx))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    monkeypatch.setattr(os, "cpu_count", lambda: _CORES)
    calls = []

    def run(load, elapsed, cpu, hang=False):
        monkeypatch.setattr(os, "getloadavg", lambda: (load, load, load))
        monkeypatch.setattr(time, "monotonic", _replay((1000.0, 1000.0 + elapsed)))
        cpu_ticks = _replay((50.0, 50.0 + cpu))

        def getrusage(who):
            assert who == resource.RUSAGE_CHILDREN, who
            return type("Usage", (), {"ru_utime": cpu_ticks(), "ru_stime": 0.0})()

        monkeypatch.setattr(resource, "getrusage", getrusage)

        def inner(argv, **kwargs):
            calls.append(kwargs["timeout"])
            if hang:
                raise subprocess.TimeoutExpired(
                    argv, kwargs["timeout"], output=b"PARTIAL-INNER-OUTPUT\n",
                    stderr=None)
            mine = runctx / ("inner-%d" % len(calls))
            mine.mkdir()
            return subprocess.CompletedProcess(
                argv, 0, stdout="1 passed\n1 worker chain(s): %s\n" % mine,
                stderr="")

        monkeypatch.setattr(subprocess, "run", inner)
        gate._run_tool_state_suite(_SUITE)

    run.calls = calls
    return run


def _timing_lines(recwarn):
    return [str(w.message) for w in recwarn.list
            if str(w.message).startswith("TOOL-STATE-TIMING")]


def test_a_healthy_run_on_a_loaded_host_passes(harness, recwarn):
    elapsed = _STRETCH * _wall(_SUITE)
    load_factor = 1.0 + _LOADED / _CORES
    # The control: the shape this replays is one the old wall model refused.
    assert elapsed > _wall(_SUITE) * 2.0 * load_factor, (
        "the replayed stretch no longer exceeds the old wall bound")
    try:
        harness(_LOADED, elapsed, 1.0 * _cpu_baseline(_SUITE))
    except AssertionError as exc:
        pytest.fail("WALL_MODEL_FAILS_HEALTHY_WORK: %.1fs wall at load factor "
                    "%.2f on correct work was refused: %s"
                    % (elapsed, load_factor, str(exc)[:400]))
    assert harness.calls and harness.calls[0] >= elapsed
    assert len(_timing_lines(recwarn)) == 1, "a passing run must report its timing"


@pytest.mark.parametrize("load", [0.0, _LOADED])
def test_a_cpu_regression_fails_idle_and_loaded(harness, recwarn, load):
    stretch = 1.0 if load == 0.0 else _STRETCH
    cpu = _REGRESSION * _cpu_baseline(_SUITE)
    # Wall stays under the hang ceiling: a run that returned cannot be past it.
    elapsed = min(stretch * _REGRESSION * _wall(_SUITE),
                  0.9 * gate._inner_budget_s(_SUITE))
    with pytest.raises(AssertionError, match=r"^CPU-REGRESSION: .*recorded CPU baseline"):
        harness(load, elapsed, cpu)
    assert len(_timing_lines(recwarn)) == 1, "a failing run must report its timing"


def test_a_run_past_the_wall_ceiling_fails_as_a_hang(harness, recwarn, tmp_path):
    ceiling = gate._inner_budget_s(_SUITE)
    try:
        harness(0.0, ceiling + 1.0, 0.5 * _cpu_baseline(_SUITE), hang=True)
    except subprocess.TimeoutExpired as exc:
        pytest.fail("HANG_ESCAPES_AS_RAW_TIMEOUT: the runner let %r out "
                    "unreported" % (exc,))
    except AssertionError as exc:
        message = str(exc)
        assert message.startswith("HANG: "), message
        assert "hang ceiling of %ds" % ceiling in message, message
        assert "slow" not in message, message
        logs = list(tmp_path.glob("bd1046-inner-*.log"))
        assert len(logs) == 1 and "PARTIAL-INNER-OUTPUT" in logs[0].read_text(), logs
    else:
        pytest.fail("HANG_NOT_REPORTED: a run past the ceiling passed")
    assert harness.calls == [ceiling]
    assert len(_timing_lines(recwarn)) == 1, "a hung run must report its timing"


def test_a_real_hung_inner_suite_fails_as_a_hang(monkeypatch, tmp_path):
    """Unfaked: a real nested pytest that sleeps past a 2s ceiling is killed and named."""
    suite = "tests/test_hangs_forever.py"
    (tmp_path / "tests").mkdir()
    (tmp_path / suite).write_text(
        "import time\n\ndef test_hangs():\n    print('REAL-HANG-STARTED', flush=True)\n"
        "    time.sleep(120)\n", encoding="utf-8")
    jobs, runctx = tmp_path / "jobs", tmp_path / "runctx"
    jobs.mkdir()
    runctx.mkdir()
    monkeypatch.setattr(gate, "_REPO", tmp_path)
    monkeypatch.setattr(gate, "_REAL_STATE", (str(jobs),))
    monkeypatch.setattr(gate, "_PER_RUN_STATE", str(runctx))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    monkeypatch.setattr(gate, "_MIN_INNER_BUDGET_S", 2)
    monkeypatch.setattr(gate, "_SUITE_BASELINE_S", {suite: 0.25})
    monkeypatch.setattr(gate, "_SUITE_CPU_BASELINE_S", {suite: 1}, raising=False)
    started = time.monotonic()
    try:
        gate._run_tool_state_suite(suite)
    except subprocess.TimeoutExpired as exc:
        pytest.fail("HANG_ESCAPES_AS_RAW_TIMEOUT: %r" % (exc,))
    except AssertionError as exc:
        assert str(exc).startswith("HANG: %s was still running" % suite), str(exc)
        assert "hang ceiling of 2s" in str(exc), str(exc)
    else:
        pytest.fail("HANG_NOT_REPORTED: a 120s sleeper passed a 2s ceiling")
    assert time.monotonic() - started < 60, "the ceiling did not stop the hung suite"


def test_a_healthy_idle_run_is_unchanged(harness):
    """Passes on the wall model too: the idle verdict is not what changed."""
    harness(0.0, 1.0 * _wall(_SUITE), 1.0 * _cpu_baseline(_SUITE))
    assert harness.calls == [gate._inner_budget_s(_SUITE)]


def test_a_passing_run_reports_elapsed_and_cpu(harness, recwarn):
    harness(0.0, 1.0 * _wall(_SUITE), 1.0 * _cpu_baseline(_SUITE))
    lines = _timing_lines(recwarn)
    assert len(lines) == 1, "TIMING_NOT_REPORTED: %r" % (lines,)
    (line,) = lines
    assert "elapsed %.1fs" % _wall(_SUITE) in line, line
    assert "cpu %.1fs" % _cpu_baseline(_SUITE) in line, line


def test_the_hang_ceiling_does_not_read_load(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: _CORES)
    seen = set()
    for load in (0.0, _LOADED, 10000.0):
        monkeypatch.setattr(os, "getloadavg", lambda load=load: (load, load, load))
        seen.add((gate._inner_budget_s(_SUITE), gate._item_timeout_s(_SUITE)))
    assert len(seen) == 1, "HANG_CEILING_FOLLOWS_LOAD: %r" % (sorted(seen),)


def test_children_cpu_counts_burned_cpu_and_not_sleep():
    """The real getrusage mechanism, unfaked: a busy child counts, a sleeper does not."""
    before = gate._children_cpu_s()
    subprocess.run([sys.executable, "-c",
                    "import time\nt=time.process_time()\n"
                    "while time.process_time()-t<0.4: pass"], check=True, timeout=60)
    burned = gate._children_cpu_s() - before
    before = gate._children_cpu_s()
    subprocess.run([sys.executable, "-c", "import time; time.sleep(0.6)"],
                   check=True, timeout=60)
    slept = gate._children_cpu_s() - before
    assert burned >= 0.35, "CPU_NOT_MEASURED: a 0.4s busy child read %.3fs" % burned
    assert slept < 0.35, "WALL_COUNTED_AS_CPU: a 0.6s sleeper read %.3fs" % slept
