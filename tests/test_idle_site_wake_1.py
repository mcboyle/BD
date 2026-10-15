"""idle-site-wake r1 (APP-DL-FIX, RULING pm-A4-A 2026-10-15T00:34Z).

Live defect (vip4k, .95, 2026-10-14 22:28Z -> 00:20Z): two imports into a
site whose run had drained were logged "Imported 1 URL(s)" and then nothing
happened for ~2h until a service restart resumed the site. Root cause:
``load_urls`` never feeds ``_url_queue`` (``start()`` is its only producer),
so a URL added to a running site drains with the run, and a URL added to a
drained ("done"/"idle") site is never started. ``_finalize_watch_done`` then
parks the site "idle" with ready pending jobs and no restart.

Acceptance (1): add_url / import into a drained or running site reaches a
worker without an operator Start -- observed as the real
``_claim_worker_item`` marking the job "Claimed by worker".
Acceptance (2): every /api/queue/v2 "waiting" item carries a non-empty
``hold_reason`` (retry_wait / not_started / site_idle / queued / hold text).

Negative controls: a stopped site and a never-started site are NOT woken
(those are operator decisions; queue/v2 names them "stopped"/"not_started").

Fake workers: the Playwright ``_worker_loop`` is replaced by a loop that
takes from the real ``_url_queue``, honours the None sentinel and calls the
real ``_claim_worker_item``; every wait is bounded and event-driven, nothing
sleeps to decide a race.
"""

from __future__ import annotations

import importlib
import queue
import threading
import time

import pytest
from flask import Flask

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

A = "https://example.invalid/a.mp4"
B = "https://example.invalid/b.mp4"
CLAIM_WINDOW_S = 5.0  # generous bound; the wake is synchronous, not a tick


def _make_runner(site_id, tmp_path):
    from bulk_downloader.runner import SiteRunner

    cfg = {
        "name": site_id,
        "auto_teach_first_run": False,
        "download_dir": str(tmp_path),
        "disk_threshold_gb": 0.0,
        "site_quota_gb": 1_000_000.0,
        "max_concurrent": 1,
    }
    return SiteRunner(site_id, cfg)


def _kill(runner):
    for fn in (runner.stop, runner._stop_auto_retry):
        try:
            fn()
        except Exception:
            pass


class _Pool:
    """Fake worker pool: records claimed URLs, fires ``claimed[url]`` events."""

    def __init__(self, runner, gate=None, on_sentinel=None):
        self.runner, self.gate, self.on_sentinel = runner, gate, on_sentinel
        self.claimed = {}
        self.order = []
        self._lock = threading.Lock()
        runner._worker_loop = self._loop

    def event(self, url):
        with self._lock:
            return self.claimed.setdefault(url, threading.Event())

    def _loop(self, worker_idx=0, run_generation=None):
        r = self.runner
        while True:
            try:
                item = r._url_queue.get(timeout=2.0)
            except queue.Empty:
                if r._stop.is_set():
                    return
                continue
            try:
                if item is None:
                    if self.on_sentinel is not None:
                        self.on_sentinel(run_generation)
                    return
                if self.gate is not None and not self.gate.wait(timeout=10.0):
                    return
                url = item[1]
                outcome, _ = r._claim_worker_item(worker_idx, url, run_generation)
                if outcome == "claimed":
                    assert r.jobs[url]["message"] == "Claimed by worker"
                    self.order.append(url)
                    self.event(url).set()
                    r._update_job(url, "done", "fake worker finished")
            finally:
                r._url_queue.task_done()


def _wait_state(runner, states, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runner.state() in states:
            return True
        time.sleep(0.02)
    return runner.state() in states


def _wake_events(runner):
    return [e for e in list(runner._event_log) if e.get("kind") == "wake"]


# ── acceptance (1): the wake path ────────────────────────────────────────────


def test_add_url_into_a_drained_site_is_claimed_without_a_start(
    clean_workdir, tmp_path
):
    """RED at BASE: B stays "pending", state "done", no worker ever sees it."""
    r = _make_runner("isw-drained", tmp_path)
    pool = _Pool(r)
    try:
        r.load_urls([A])
        r.start()
        assert pool.event(A).wait(CLAIM_WINDOW_S), "fixture: first run never claimed A"
        assert _wait_state(r, ("done", "idle")), r.state()
        assert r.state() == "done"

        added, _dupes, _skipped = r.load_urls([B])  # the add_url / import path
        assert added == 1
        assert pool.event(B).wait(CLAIM_WINDOW_S), (
            f"idle-site-wake: URL added to a drained site was never claimed; "
            f"status={r.jobs[B]['status']!r} state={r.state()!r}"
        )
        assert r.jobs[B]["status"] == "done"
        kinds = [e["message"] for e in _wake_events(r)]
        assert any("Waking the drained run" in m for m in kinds), kinds
    finally:
        _kill(r)


def test_add_url_into_a_running_site_reaches_the_live_pool(clean_workdir, tmp_path):
    """RED at BASE: the running pool never sees B; the run drains and parks B pending."""
    r = _make_runner("isw-running", tmp_path)
    gate = threading.Event()
    pool = _Pool(r, gate=gate)
    try:
        r.load_urls([A])
        r.start()
        assert _wait_state(r, ("running",), 5.0), r.state()
        added, _d, _s = r.load_urls([B])  # while A is still in flight
        assert added == 1
        assert r._url_queue.unfinished_tasks == 2, r._url_queue.unfinished_tasks
        gate.set()
        assert pool.event(B).wait(CLAIM_WINDOW_S), (
            f"idle-site-wake: URL added mid-run never claimed; "
            f"status={r.jobs[B]['status']!r} state={r.state()!r}"
        )
        assert pool.order == [A, B]
        assert any("into the running pool" in e["message"] for e in _wake_events(r))
    finally:
        _kill(r)


def test_add_url_during_teardown_restarts_the_run(clean_workdir, tmp_path):
    """A put that lands after the overseer's drain check (workers already
    told to exit) is repaid: _finalize_watch_done sees unfinished_tasks > 0
    and restarts instead of parking the site idle."""
    r = _make_runner("isw-teardown", tmp_path)
    late = {"done": False}

    def on_sentinel(run_generation):
        # The sentinel is the overseer's "queue drained" decision; a wake put
        # landing now is exactly the race. Add B the way load_urls does, then
        # put it on the queue under the still-current generation.
        if late["done"]:
            return
        late["done"] = True
        with r._lock:
            r.jobs[B] = dict(
                r.jobs[A], status="pending", message="", retries=0, retry_after=0
            )
        with r._worker_heartbeats_lock:
            r._url_queue.put((run_generation, B))

    pool = _Pool(r, on_sentinel=on_sentinel)
    try:
        r.load_urls([A])
        r.start()
        assert pool.event(A).wait(CLAIM_WINDOW_S)
        assert pool.event(B).wait(CLAIM_WINDOW_S), (
            f"idle-site-wake: teardown-race URL never claimed; "
            f"status={r.jobs[B]['status']!r} state={r.state()!r} "
            f"unfinished={r._url_queue.unfinished_tasks}"
        )
        assert any("added during teardown" in e["message"] for e in _wake_events(r))
        assert _wait_state(r, ("done",)), r.state()
    finally:
        _kill(r)


# ── negative controls: operator decisions are not overridden ────────────────


def test_stopped_site_is_not_woken_by_add_url(clean_workdir, tmp_path):
    from bulk_downloader.db import run_intent_is_running

    r = _make_runner("isw-stopped", tmp_path)
    gate = threading.Event()
    pool = _Pool(r, gate=gate)
    try:
        r.load_urls([A])
        r.start()
        assert _wait_state(r, ("running",), 5.0)
        r.stop()
        gate.set()
        assert _wait_state(r, ("stopped",), 5.0), r.state()
        assert run_intent_is_running("isw-stopped") is False
        added, _d, _s = r.load_urls([B])
        assert added == 1
        assert not pool.event(B).wait(1.0), "stopped site was woken by add_url"
        assert r.jobs[B]["status"] == "pending"
        assert r.state() == "stopped"
        assert _wake_events(r) == []
    finally:
        _kill(r)


def test_never_started_site_is_not_woken_by_add_url(clean_workdir, tmp_path):
    from bulk_downloader.db import run_intent_is_running

    r = _make_runner("isw-fresh", tmp_path)
    pool = _Pool(r)
    try:
        assert run_intent_is_running("isw-fresh") is False
        added, _d, _s = r.load_urls([A])
        assert added == 1
        assert not pool.event(A).wait(1.0), "never-started site was auto-started"
        assert r.jobs[A]["status"] == "pending"
        assert r.state() == "idle"
        assert _wake_events(r) == []
    finally:
        _kill(r)


# ── acceptance (2): queue/v2 waiting items name why they wait ───────────────


class _StubRunner:
    def __init__(self, state, jobs, streak=0):
        self._state = state
        self._lock = threading.Lock()
        self.jobs = jobs
        self.config = {}
        self._consec_no_btn = streak

    def state(self):
        return self._state

    def is_rate_limited(self):
        return False

    def get_status(self, light=False):
        return {"counts": {"pending": len(self.jobs), "done": 0}, "active": 0}


def _queue_v2(monkeypatch, runners, intents):
    q = importlib.import_module("bulk_downloader.app_queue")
    monkeypatch.setattr(q, "_app_runners", lambda: runners)
    monkeypatch.setattr(
        q, "_app_s_cfg", lambda: {sid: {"name": sid} for sid in runners}
    )
    monkeypatch.setattr(
        q,
        "_run_intent_is_running",
        lambda sid: bool(intents.get(sid, False)),
        raising=False,
    )
    app = Flask(__name__)
    app.register_blueprint(q.queue_bp)
    return app.test_client().get("/api/queue/v2").get_json()


def test_queue_v2_waiting_items_carry_a_non_empty_hold_reason(monkeypatch):
    now = time.time()
    pend = {"status": "pending", "retry_after": 0}
    runners = {
        "retry": _StubRunner(
            "idle", {"u1": {"status": "pending", "retry_after": now + 600}}
        ),
        "fresh": _StubRunner("idle", {"u2": dict(pend)}),
        "drained": _StubRunner("done", {"u3": dict(pend)}),
        "busy": _StubRunner("running", {"u4": dict(pend), "u5": {"status": "running"}}),
        "stopped": _StubRunner("stopped", {"u6": dict(pend)}),
        "held": _StubRunner("paused_no_button", {"u7": dict(pend)}, streak=5),
    }
    body = _queue_v2(monkeypatch, runners, intents={"drained": True})
    assert all(w.get("hold_reason") for w in body["waiting"]), (
        "idle-site-wake: /api/queue/v2 waiting item without a hold_reason: "
        + repr([w for w in body["waiting"] if not w.get("hold_reason")])
    )
    by_site = {w["site_id"]: w["hold_reason"] for w in body["waiting"]}
    assert by_site["retry"] == "retry_wait"
    assert by_site["fresh"] == "not_started"
    assert by_site["drained"] == "site_idle"
    assert by_site["busy"] == "queued"
    assert by_site["stopped"] == "stopped"
    assert "no download button" in by_site["held"].lower()
    per_site = {p["site_id"]: p for p in body["per_site"]}
    assert per_site["fresh"]["hold_reason"] == "not_started"
    assert per_site["drained"]["hold_reason"] == "site_idle"
    assert per_site["stopped"]["hold_reason"] == "stopped"
    assert "no download button" in per_site["held"]["hold_reason"].lower()
    assert per_site["busy"]["hold_reason"] == ""  # a run in flight is not held
    assert per_site["busy"]["state"] == "running"


def test_queue_v2_retry_wait_wins_over_the_site_state(monkeypatch):
    """A job in back-off on a stopped site is still "retry_wait" -- the timer,
    not the site, is what the operator is waiting on."""
    now = time.time()
    runners = {
        "s": _StubRunner(
            "stopped", {"u": {"status": "pending", "retry_after": now + 60}}
        )
    }
    body = _queue_v2(monkeypatch, runners, intents={})
    assert [w.get("hold_reason") for w in body["waiting"]] == ["retry_wait"]
