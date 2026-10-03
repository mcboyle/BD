"""Tests for the Tier-1 request-metrics and thread tools — latency
histogram (D-64), slow-endpoint flagger (D-67), error-rate panel
(D-62), exception ring-buffer (D-66), thread dump (D-59) and the
heuristic deadlock detector (D-60) — their /api/dev/* endpoints, and
the app.py request hook that feeds dev_metrics.
"""

# H622 slice A. An ordinary module test: its subject is the module under
# test, not the tree, so it is not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

import json
import os
import subprocess
import sys

from bulk_downloader import dev_metrics as dm
from bulk_downloader import dev_suite as ds


def _seed_requests():
    dm.reset()
    for ms in (10, 20, 30, 40, 1500):              # 5 calls to /api/a
        dm.record_request("GET", "/api/a", "/api/a", 200, ms)
    dm.record_request("GET", "/api/b", "/api/b", 404, 5)
    dm.record_request("POST", "/api/b", "/api/b", 500, 8)


# ── D-64 latency histogram ────────────────────────────────────────

def test_latency_histogram_computes_percentiles():
    _seed_requests()
    r = ds.latency_histogram()
    assert r["sampled_requests"] == 7
    a = next(x for x in r["routes"] if x["rule"] == "/api/a")
    assert a["count"] == 5
    assert a["max_ms"] == 1500
    assert a["p50_ms"] <= a["p95_ms"] <= a["p99_ms"] <= a["max_ms"]


def test_latency_histogram_empty():
    dm.reset()
    r = ds.latency_histogram()
    assert r["sampled_requests"] == 0
    assert "no requests" in r["verdict"]


# ── D-67 slow-endpoint flagger ────────────────────────────────────

def test_slow_endpoints_flags_over_threshold():
    _seed_requests()
    r = ds.slow_endpoints(threshold_ms=500)
    assert r["slow_request_count"] == 1
    assert r["routes"][0]["rule"] == "/api/a"
    assert r["routes"][0]["max_ms"] == 1500


def test_slow_endpoints_none_when_all_fast():
    _seed_requests()
    r = ds.slow_endpoints(threshold_ms=5000)
    assert r["slow_request_count"] == 0
    assert "no requests over" in r["verdict"]


# ── D-62 error-rate panel ─────────────────────────────────────────

def test_error_rate_counts_4xx_and_5xx():
    _seed_requests()
    r = ds.error_rate()
    assert r["total_4xx"] == 1
    assert r["total_5xx"] == 1
    b = next(x for x in r["routes_with_errors"]
             if x["rule"] == "/api/b")
    assert b["4xx"] == 1 and b["5xx"] == 1


# ── D-66 exception ring-buffer ────────────────────────────────────

def test_exception_log_newest_first():
    dm.reset()
    for name in ("first", "second", "third"):
        try:
            raise RuntimeError(name)
        except RuntimeError as e:
            dm.record_exception(e, "/api/x")
    r = ds.exception_log()
    assert r["captured"] == 3
    assert "third" in r["exceptions"][0]["message"]   # newest first
    dm.reset()


# ── D-59 thread dump ──────────────────────────────────────────────

def test_thread_dump_includes_stacks():
    r = ds.thread_dump()
    assert r["thread_count"] >= 1
    t0 = r["threads"][0]
    for key in ("ident", "name", "frame_count", "top_frame", "stack"):
        assert key in t0
    assert isinstance(t0["stack"], list)


# ── D-60 deadlock detector ────────────────────────────────────────

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _detector_in_fresh_process(setup=""):
    """deadlock_detector() run in a NEW interpreter after `setup`; its JSON result.

    sg-deadlock-tier1b: the detector scans every thread of its process. A shared pytest
    worker is not a clean process: xdist's execnet reader sits in a blocking read(), and an
    earlier test can leave a thread asleep (test_challenge_circuit's abandoned
    challenge_action, 3 s). Two such threads made the nightly band RED on b8441270 (gw9).
    A fresh interpreter is clean by construction, whatever ran before in this worker."""
    code = ("import json, os, sys\n"
            f"sys.path.insert(0, {_REPO!r})\n"
            f"{setup}\n"
            "from bulk_downloader import dev_suite as ds\n"
            "print(json.dumps(ds.deadlock_detector()), flush=True)\n"
            "os._exit(0)\n")  # a deliberately deadlocked setup must not hang the exit
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=_REPO, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# A healthy process still has threads: two workers parked in intentional idle waits
# (Event / Queue). They must not count, so the idle-wait exclusion stays under test.
_IDLE_WORKERS = """
import queue, threading
threading.Thread(target=threading.Event().wait, name="idle-event", daemon=True).start()
threading.Thread(target=queue.Queue().get, name="idle-queue", daemon=True).start()
import time
time.sleep(0.2)
"""


def test_deadlock_detector_clean_process():
    r = _detector_in_fresh_process(_IDLE_WORKERS)
    assert r["live_threads_scanned"] >= 2, r  # the idle workers were there to be judged
    # a healthy process has no thread frozen at an identical
    # non-idle frame across both snapshots; the inspecting thread is
    # excluded, so it can never flag itself
    assert r["deadlock_suspected"] is False, r["stalled_suspects"]
    assert "stalled_suspects" in r
    assert "verdict" in r


# Positive control: two threads that each hold one lock and block on the other's.
_LOCK_ORDER_DEADLOCK = """
import threading
la, lb = threading.Lock(), threading.Lock()
held = threading.Barrier(3)
def grab_ab():
    with la:
        held.wait()
        with lb:
            pass
def grab_ba():
    with lb:
        held.wait()
        with la:
            pass
for fn in (grab_ab, grab_ba):
    threading.Thread(target=fn, name=fn.__name__, daemon=True).start()
held.wait()
import time
time.sleep(0.2)
"""


def test_deadlock_detector_flags_a_real_deadlock():
    r = _detector_in_fresh_process(_LOCK_ORDER_DEADLOCK)
    assert r["deadlock_suspected"] is True, r
    names = sorted(s["name"] for s in r["stalled_suspects"])
    assert names == ["grab_ab", "grab_ba"], r["stalled_suspects"]


# ── app.py request hook → dev_metrics integration ─────────────────

def test_request_hook_feeds_dev_metrics(fresh_app):
    dm.reset()
    resp = fresh_app.get("/api/dev/routes")
    assert resp.status_code == 200
    snap = dm.request_snapshot()
    assert any(rec["path"] == "/api/dev/routes" for rec in snap), snap


# ── endpoint smoke ────────────────────────────────────────────────

def test_tier1b_endpoints_respond(fresh_app):
    for path in ("/api/dev/latency", "/api/dev/slow_endpoints",
                 "/api/dev/error_rate", "/api/dev/exceptions",
                 "/api/dev/thread_dump", "/api/dev/deadlock_check"):
        resp = fresh_app.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        assert resp.get_json() is not None, f"{path} returned no JSON"
