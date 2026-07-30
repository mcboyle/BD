"""done_today_count was structurally always 0 -- producer and consumer disagreed
on the shape of the timestamp they shared.

THE DEFECT. `/api/queue/v2` counts today's completed jobs like this
(app_queue.py:182, :228-230):

    today_iso = time.strftime("%Y-%m-%d")     # "2026-07-30"
    ...
    ts = j.get("ts", "") or ""
    if ts.startswith(today_iso):
        done_today += 1

But `ts` is written by `_update_job` as `_ts()`, and `_ts()` is
`datetime.now().strftime("%H:%M:%S")` (runner_util.py:57) -- a wall-clock time
of day with no date in it at all. So the predicate is

    "12:34:56".startswith("2026-07-30")

which is False for every possible pair of values. Not "usually 0", not "0 when
idle" -- structurally 0, always, on every host, for every job. The counter has
never once incremented.

This is not caught by the existing empty-state test
(test_d3_u2_v2_endpoints.py:228 asserts `done_today_count == 0` with NO runners
registered) because that assertion is true on both the broken and the fixed
tree. Its denominator excludes the only state that could tell them apart --
CLAUDE.md section 0. The fix therefore needs a test that registers a runner
holding a job completed today, which is what T1 does.

THE FIX. `ts` keeps its HH:MM:SS shape -- it is the human-readable value the
queue UI renders, and changing it would be a visible regression (T4 guards
that). A sibling field `ts_iso` carries a full date-comparable stamp, and the
consumer reads that instead. Three edits land together: producer (runner.py),
rehydrate-after-restart (runner_queue.py), consumer (app_queue.py).

WHY THE FAKE RUNNER PINS ITS SHAPE. app_queue.py wraps the per-runner loop in
`except Exception: continue` (:231) and dereferences `runner._lock` (:201). A
fake without a real `_lock` and a `jobs` dict raises, gets swallowed, and
`done_today` stays 0 on the FIXED tree too -- the test would fail on both trees
and be a mislabelled RED that can never go green. `_CountRunner` below supplies
both, and the cut was verified green-after-fix, not merely red-before.

TIMEZONE. T3 asserts `ts_iso == ts_updated` (equality), NOT
`ts_iso.startswith(local today)`. SQLite stamps `ts_updated` in UTC
(db.py:71, `strftime('%Y-%m-%dT%H:%M:%S','now')`) while `time.strftime` is
LOCAL, so a startswith-today assertion would false-fail near a day boundary on
any non-UTC host. Equality is both tz-robust and strictly stronger: it tests
the edit's actual contract, which is "copy ts_updated verbatim".
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

_REPO = str(Path(__file__).resolve().parents[1])
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

pytestmark = pytest.mark.bd_module_wipe

_URL = "https://example.invalid/cut31.mp4"


class _CountRunner:
    """Minimal stand-in with the exact surface app_queue.py touches.

    _lock and jobs are REQUIRED: :201 does `with runner._lock:` and iterates
    `runner.jobs.items()`, and :231 swallows any exception from that block. A
    fake missing either one makes done_today_count 0 for the wrong reason.
    """

    def __init__(self, jobs):
        self._lock = threading.Lock()
        self.jobs = jobs
        self._recent_per_min = 0


def _register(sid, jobs):
    """Register a fake runner, returning a cleanup callable."""
    from bulk_downloader import app_state as st
    st.s_cfg[sid] = {"name": sid}
    st.runners[sid] = _CountRunner(jobs)

    def _cleanup():
        st.runners.pop(sid, None)
        st.s_cfg.pop(sid, None)
    return _cleanup


def _queue_v2():
    from bulk_downloader import app as a
    return a.app.test_client().get("/api/queue/v2").get_json()


@pytest.fixture
def runner_db(clean_workdir):
    """Isolated BD home WITH the sqlite tables created.

    Every test here that constructs a real SiteRunner needs this. SiteRunner
    hits the queue table unconditionally on construct (via _restore_queue), so
    without db_init() the test dies on `no such table: queue` -- which would be
    a RED that fails for the wrong reason and proves nothing about the defect.
    clean_workdir also keeps the runner's state files out of the work tree.
    """
    from bulk_downloader.db import db_init
    db_init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    return clean_workdir


# ── T1: the counter must actually count (RED) ───────────────────────────────

def test_queue_v2_counts_a_done_job_from_today():
    """RED. A job completed today, with the display `ts` the producer really
    writes, must be counted. Pristine reads `ts` ("12:00:00"), compares it to
    "%Y-%m-%d", gets False, and reports 0."""
    job = {"status": "done",
           "ts": time.strftime("%H:%M:%S"),
           "ts_iso": time.strftime("%Y-%m-%d") + "T12:00:00"}
    cleanup = _register("cut31_t1", {_URL: job})
    try:
        body = _queue_v2()
        assert body.get("ok") is True, body
        assert body.get("done_today_count") == 1, body
    finally:
        cleanup()


# ── T2: the producer must stamp a date-comparable value (RED) ───────────────

def test_update_job_stamps_date_comparable_ts_iso(runner_db):
    """RED. Checks the VALUE, not merely that the key exists -- so it also
    catches a ts_iso re-stamped as HH:MM:SS or written in the wrong format."""
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner("cut31_t2", {"name": "cut31_t2"})
    r._update_job(_URL, "done", "ok", filename="a.mp4", file_size=1)
    got = r.jobs[_URL].get("ts_iso", "")
    assert got.startswith(time.strftime("%Y-%m-%d")), (
        f"ts_iso={got!r} is not a date-comparable stamp for today")


# ── T3: the stamp must survive a restart (RED, tz-robust) ───────────────────

def test_restart_preserves_done_today(runner_db):
    """RED. _restore_queue rebuilds self.jobs from the queue table. Without
    ts_iso, every job that predates the process restart drops out of the count.

    Asserts EQUALITY with the row's own ts_updated rather than
    startswith(local today): ts_updated is UTC and time.strftime is LOCAL, so
    a startswith assertion would false-fail near midnight on a non-UTC host --
    a cry-wolf in the test itself. Equality tests the edit's real contract.
    """
    from bulk_downloader.db import queue_upsert, queue_load
    from bulk_downloader.runner import SiteRunner
    sid = "cut31_t3"
    queue_upsert(sid, _URL, status="done", message="ok",
                 filename="a.mp4", file_size=1)
    rows = {r["url"]: r for r in queue_load(sid)}
    db_ts = rows[_URL].get("ts_updated") or ""
    assert db_ts, "queue row has no ts_updated; the fixture, not the fix, is wrong"
    r = SiteRunner(sid, {"name": sid})
    assert r.jobs[_URL].get("ts_iso") == db_ts, (
        f"rehydrated ts_iso={r.jobs[_URL].get('ts_iso')!r} != ts_updated={db_ts!r}")


# ── T4: the display value must NOT change (regression guard, green today) ───

def test_display_ts_stays_hhmmss(runner_db):
    """REGRESSION GUARD -- green on pristine too, NOT counted as RED.

    `ts` is what the queue UI renders. Widening it to a full ISO stamp would be
    a visible regression, so the fix must add a sibling field rather than
    repurpose this one.
    """
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner("cut31_t4", {"name": "cut31_t4"})
    r._update_job(_URL, "done", "ok")
    ts = r.jobs[_URL].get("ts", "")
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", ts), (
        f"display ts={ts!r} is no longer HH:MM:SS")


# ── T5: yesterday must not count (soundness guard) ──────────────────────────

def test_only_yesterdays_done_does_not_count():
    """SOUNDNESS GUARD. Green on the correct fix; fails if the date filter is
    dropped rather than repaired. Without this, "count every done job" would
    satisfy T1 -- a fix that trades always-0 for always-wrong.
    """
    y = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    job = {"status": "done", "ts": "23:59:00", "ts_iso": y + "T23:59:00"}
    cleanup = _register("cut31_t5", {_URL: job})
    try:
        body = _queue_v2()
        assert body.get("ok") is True, body
        assert body.get("done_today_count") == 0, body
    finally:
        cleanup()


# ── T6: empty state stays 0 (regression guard, green today) ─────────────────

def test_queue_v2_empty_state_zero():
    """REGRESSION GUARD -- mirrors test_d3_u2_v2_endpoints.py:228. Green on
    both trees by construction; present so the cut cannot fix the counter by
    making it non-zero unconditionally."""
    body = _queue_v2()
    assert body.get("ok") is True, body
    assert body.get("done_today_count") == 0, body
