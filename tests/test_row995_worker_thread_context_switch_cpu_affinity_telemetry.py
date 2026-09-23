"""Row 995: per-worker-thread context switch and CPU affinity telemetry.

Each SiteRunner worker thread stamps a thread_telemetry snapshot beside its
heartbeat; get_status() (served as /api/status) publishes the latest one per
worker. The reading must be of the worker thread itself, never the process.

The tests drive the product seam -- the REAL SiteRunner._worker_loop on a thread
named as SiteRunner names its workers, read back through get_status() -- and
import nothing this row adds: on a base without the row the file collects and
runs, and each test fails on its ROW995-* assertion (the regression guards pass).
"""
from __future__ import annotations

import ast
import builtins
import contextlib
import importlib.util
import inspect
import json
import os
import resource
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

from bulk_downloader import runner as runner_mod

# Its subject is a SiteRunner seam that tests across the tree construct by hand.
BD_GATE_SCOPE = "repo-wide"

_KEYS = {"thread_name", "native_id", "ts", "voluntary_ctxt_switches", "involuntary_ctxt_switches",
         "user_time_s", "system_time_s", "cpu_affinity", "cpu_affinity_pinned"}
_COLLECTOR = "bulk_downloader.thread_telemetry"


class _OnePass(threading.Event):
    """The worker's pause gate: open, and it ends the loop after one pass."""

    def __init__(self, stop):
        super().__init__()
        self.set()
        self.ends = stop
        self.passes = 0

    def wait(self, timeout=None):
        self.passes += 1
        self.ends.set()
        return True


def _site_runner(monkeypatch, site_id="row995"):
    """A real SiteRunner; only the browser launch and the VPN/netns seams are replaced."""
    monkeypatch.setattr(runner_mod, "_VPN_RUNTIME_AVAILABLE", True)
    monkeypatch.setattr(runner_mod.netns_isolation, "capture_netns",
                        lambda *_a, **_k: contextlib.nullcontext(None))
    runner = runner_mod.SiteRunner(site_id, {"concurrency": 1})
    runner._launch_browser = lambda **_k: (None, None, None, "row995-test")
    return runner


def _worker_passes(runner, passes=1, between=lambda: None):
    """Run the REAL _worker_loop `passes` times, one iteration each, on ONE worker thread."""
    seen = {"status": [], "gate": 0}

    def work():
        seen["native_id"] = threading.get_native_id()
        for n in range(passes):
            if n:
                between()
            runner._stop.clear()
            gate = runner._pause = _OnePass(runner._stop)
            runner._worker_loop(0, runner._worker_run_generation)
            seen["gate"] += gate.passes
            seen["status"].append(runner.get_status(light=True).get("worker_threads"))

    worker = threading.Thread(target=work, name=f"dl-{runner.site_id}-0")
    worker.start()
    worker.join(60)
    assert not worker.is_alive(), "a worker pass never returned"
    return seen


def _published(threads):
    """Worker 0's reading in a get_status()["worker_threads"] payload."""
    assert threads is not None, "ROW995-STATUS-UNWIRED: get_status() carries no worker_threads"
    assert "0" in threads, ("ROW995-WORKER-LOOP-UNWIRED: a worker pass published no reading", threads)
    return threads["0"]


def _wrap_collector(monkeypatch, wrap):
    """Wrap the row's collector where the tree has one; a base has none to wrap."""
    if importlib.util.find_spec(_COLLECTOR) is None:
        return
    module = importlib.import_module(_COLLECTOR)
    monkeypatch.setattr(module, "current_thread_telemetry", wrap(module.current_thread_telemetry))


def test_worker_thread_stamps_its_own_snapshot_and_status_publishes_it(monkeypatch):
    runner = _site_runner(monkeypatch)
    calls = []

    def counted(real):
        def collector():
            calls.append(threading.get_native_id())
            return real()
        return collector

    def twenty_sleeps():
        for _ in range(20):
            time.sleep(0.001)  # each sleep is a voluntary switch of THIS thread

    _wrap_collector(monkeypatch, counted)
    seen = _worker_passes(runner, passes=2, between=twenty_sleeps)
    first, snap = (_published(threads) for threads in seen["status"])
    assert set(snap) == _KEYS
    assert (snap["thread_name"], snap["native_id"]) == (f"dl-{runner.site_id}-0", seen["native_id"]), \
        "ROW995-NOT-WORKER-THREAD"
    assert snap["native_id"] != threading.get_native_id()
    assert calls == [seen["native_id"]] * 2, ("ROW995-STAMP-COUNT: one reading per pass, on the worker", calls)
    assert seen["gate"] == 2
    assert snap["voluntary_ctxt_switches"] - first["voluntary_ctxt_switches"] >= 20
    assert snap["cpu_affinity"] == sorted(os.sched_getaffinity(0))
    json.dumps(seen["status"][-1])


def test_thread_counters_are_not_the_process_counters(monkeypatch):
    # A busy main thread must not appear in an idle worker's reading.
    for _ in range(300):
        time.sleep(0.0005)
    proc = resource.getrusage(resource.RUSAGE_SELF).ru_nvcsw
    snap = _published(_worker_passes(_site_runner(monkeypatch))["status"][-1])
    assert snap["voluntary_ctxt_switches"] < proc - 200, ("ROW995-PROCESS-COUNTERS", snap, proc)


def test_affinity_is_the_worker_threads_own_mask(monkeypatch):
    # sched_setaffinity(0, ...) re-pins only the calling thread: once the worker pins itself to
    # one CPU its reading must show that mask as pinned, while this thread's mask is unchanged.
    allowed = sorted(os.sched_getaffinity(0))
    cpu = allowed[-1]
    seen = _worker_passes(_site_runner(monkeypatch), passes=2,
                          between=lambda: os.sched_setaffinity(0, {cpu}))
    first, snap = (_published(threads) for threads in seen["status"])
    assert (first["cpu_affinity"], first["cpu_affinity_pinned"]) == (allowed, len(allowed) < os.cpu_count())
    assert (snap["cpu_affinity"], snap["cpu_affinity_pinned"]) == ([cpu], os.cpu_count() > 1), \
        ("ROW995-AFFINITY-NOT-THE-WORKERS", snap)
    assert sorted(os.sched_getaffinity(0)) == allowed


def test_no_thread_source_reports_none_not_process_counters(monkeypatch):
    fallback = _site_runner(monkeypatch, "row995-proc")
    neither = _site_runner(monkeypatch, "row995-none")
    for _ in range(300):
        time.sleep(0.0005)  # this thread leads the process: /proc/self/status carries ITS counters
    leader = resource.getrusage(resource.RUSAGE_THREAD).ru_nvcsw
    monkeypatch.delattr(resource, "RUSAGE_THREAD")
    snap = _published(_worker_passes(fallback)["status"][-1])
    assert isinstance(snap["voluntary_ctxt_switches"], int)  # /proc/thread-self fallback
    assert snap["voluntary_ctxt_switches"] < leader - 200, ("ROW995-PROCESS-COUNTERS", snap, leader)
    assert snap["user_time_s"] is None  # CPU times come only from getrusage

    real_open = builtins.open

    def no_thread_self(path, *args, **kwargs):
        if str(path).startswith("/proc/thread-self/"):
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_thread_self)
    monkeypatch.delattr(os, "sched_getaffinity")
    snap = _published(_worker_passes(neither)["status"][-1])
    assert snap["voluntary_ctxt_switches"] is None and snap["involuntary_ctxt_switches"] is None, \
        ("ROW995-PROCESS-COUNTERS", snap)
    assert snap["cpu_affinity"] is None and snap["cpu_affinity_pinned"] is None


def test_an_idle_site_publishes_an_empty_map(monkeypatch):
    # Built, never started, no worker stamped yet: the commonest /api/status reading.
    runner = _site_runner(monkeypatch)
    try:
        threads = runner.get_status(light=True).get("worker_threads")
    except AttributeError as exc:
        threads = exc
    assert threads is not None, "ROW995-STATUS-UNWIRED: get_status() carries no worker_threads"
    assert threads == {}, ("ROW995-IDLE-STATUS", threads)


def test_status_without_the_seam_state_is_empty():
    # Positive control for hand-built runners elsewhere in the tree.
    bare = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    status = getattr(bare, "_worker_threads_status", None)
    assert status is not None, "ROW995-STATUS-UNWIRED: SiteRunner has no _worker_threads_status"
    try:
        threads = status()
    except (AttributeError, TypeError) as exc:  # built by hand: no heartbeat lock, no row state
        threads = exc
    assert threads == {}, ("ROW995-BARE-STATUS", threads)


def test_reset_clears_stale_worker_snapshots(monkeypatch):
    # A fresh run must not show the previous run's worker readings.
    runner = _site_runner(monkeypatch)
    _published(_worker_passes(runner)["status"][-1])
    runner.is_rate_limited = lambda: True  # start() resets the run's liveness state, then returns
    runner.start()
    left = runner.get_status(light=True)["worker_threads"]
    assert left == {}, ("ROW995-RESET-KEEPS-STALE", left)


def test_a_worker_of_a_superseded_run_does_not_publish(monkeypatch):
    runner = _site_runner(monkeypatch)
    runner.is_rate_limited = lambda: True
    restarts = []

    def restart_mid_stamp(real):
        def collector():
            reading = real()
            runner.start()  # the site restarts between this worker's heartbeat and its stamp
            restarts.append(runner._worker_run_generation)
            return reading
        return collector

    _wrap_collector(monkeypatch, restart_mid_stamp)
    threads = _worker_passes(runner)["status"][-1]
    assert threads is not None, "ROW995-STATUS-UNWIRED: get_status() carries no worker_threads"
    assert restarts == [1], ("the restart must land inside the stamp exactly once", restarts)
    assert runner._worker_heartbeats == {}  # the old run's heartbeat is already dropped
    assert threads == {}, ("ROW995-STALE-GENERATION: the superseded run's reading was published", threads)


def test_a_runner_built_without_the_row_state_still_restarts_and_runs(monkeypatch):
    # tests/test_live_telemetry.py::_telemetry_runner and the row 296 worker-loop test build
    # SiteRunner by hand, with none of this row's state; the row must not break them.
    runner = _site_runner(monkeypatch)
    runner.is_rate_limited = lambda: True
    vars(runner).pop("_worker_thread_telemetry", None)
    try:
        runner.start()
        raised = None
    except AttributeError as exc:
        raised = exc
    assert raised is None, ("ROW995-HAND-BUILT-RESTART", raised)
    vars(runner).pop("_worker_thread_telemetry", None)
    assert _worker_passes(runner)["gate"] == 1, "ROW995-HAND-BUILT-WORKER-DIES before its pause gate"


_NO_RESOURCE_WORKER_PASS = textwrap.dedent('''\
    import contextlib, sys, threading
    from pathlib import Path
    sys.modules["resource"] = None  # a platform without getrusage, like Windows
    from bulk_downloader import netns_isolation, runner
    runner._VPN_RUNTIME_AVAILABLE = True
    netns_isolation.capture_netns = lambda *a, **k: contextlib.nullcontext(None)
    site = runner.SiteRunner("row995-nores", {"concurrency": 1})
    site._launch_browser = lambda **k: (None, None, None, "row995-test")
    passes = []

    class OnePass(threading.Event):
        def wait(self, timeout=None):
            passes.append(1)
            site._stop.set()
            return True

    site._pause = OnePass()
    worker = threading.Thread(target=site._worker_loop, args=(0, site._worker_run_generation),
                              name="dl-row995-nores-0")
    worker.start()
    worker.join(60)
    print("WORKER-PASSES", len(passes), Path(runner.__file__).resolve())
''')


def test_a_platform_without_the_resource_module_still_runs_workers(tmp_path):
    root = Path(runner_mod.__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        p for p in (str(root), os.environ.get("PYTHONPATH", "")) if p)}
    proc = subprocess.run([sys.executable, "-c", _NO_RESOURCE_WORKER_PASS], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)  # hotfix 1086: below the 240s bound (test_v3_66_1222)
    expected = f"WORKER-PASSES 1 {Path(runner_mod.__file__).resolve()}"
    assert expected in proc.stdout, ("ROW995-NEEDS-RESOURCE", proc.returncode, proc.stderr[-900:])


def _calls(fn, name):
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == name)


def test_worker_loop_and_get_status_carry_the_seam():
    assert _calls(runner_mod.SiteRunner._worker_loop, "_record_worker_thread_telemetry") == 1, \
        "ROW995-WORKER-LOOP-UNWIRED"
    assert _calls(runner_mod.SiteRunner.get_status, "_worker_threads_status") == 1, "ROW995-STATUS-UNWIRED"


def test_ast_pin_can_say_no():
    def loop(self):
        self._worker_heartbeats[0] = time.time()
    assert _calls(loop, "_record_worker_thread_telemetry") == 0
