"""dl-runner -- bug-hunt findings #2, #5 and #9 (download-engine sweep, bd-worker-S01-A).

#2 MED  QueueMixin.bulk_approve approved ANY status: a stale review list reset
        running and done jobs to pending with force_download=True.
#5 MED  /api/runners/resume_all counted start() ATTEMPTS: under a download hold
        start() refuses (returns None) and the runner stays held, yet the
        response said {"resumed": 1}.
#9 LOW  nothing acquired drain-engine operations, so SiteRunner.drain()
        answered 'drained' over a URL still being processed; and a drained
        task that admitted an operation stayed 'drained' with in-flight work.

Each defect test has a control beside it that passes on the old code too, so a
pass is not just a probe that can never say yes.
"""
from __future__ import annotations

import threading

import pytest
from flask import Flask


BD_GATE_SCOPE = "module"


# ── #2 bulk_approve ─────────────────────────────────────────────────────


def _runner(site_id):
    from bulk_downloader import db
    from bulk_downloader.runner import SiteRunner
    db.db_init()
    return SiteRunner(site_id, {"name": site_id})


def _seeded(site_id, statuses):
    r = _runner(site_id)
    urls = {s: f"https://example.test/{site_id}/{s}.mp4" for s in statuses}
    r.load_urls(list(urls.values()))
    with r._lock:
        for s, u in urls.items():
            r.jobs[u]["status"] = s
    return r, urls


def test_bulk_approve_leaves_running_and_done_jobs_alone():
    r, u = _seeded("dlr_approve", ("running", "done", "needs_review"))
    n = r.bulk_approve([u["running"], u["done"], u["needs_review"]])
    after = {s: r.jobs[url]["status"] for s, url in u.items()}
    assert after["running"] == "running" and after["done"] == "done", (
        "DL-RUNNER-2: bulk_approve re-queued a non-review job: %r" % after)
    assert not r.jobs[u["done"]].get("force_download"), (
        "DL-RUNNER-2: bulk_approve set force_download on a done job")
    assert n == 1, "DL-RUNNER-2: bulk_approve counted %d approvals, want 1" % n


def test_bulk_approve_still_approves_a_needs_review_job():
    """Control: the review decision itself is unchanged."""
    r, u = _seeded("dlr_approve_ok", ("needs_review",))
    assert r.bulk_approve([u["needs_review"]]) == 1
    job = r.jobs[u["needs_review"]]
    assert job["status"] == "pending" and job["force_download"] is True
    assert u["needs_review"] in r.urls


# ── #5 resume_all ───────────────────────────────────────────────────────


@pytest.fixture()
def hold_store(tmp_path, monkeypatch):
    """An isolated durable hold store, proven CLEAR before the test holds it."""
    from bulk_downloader import download_hold, global_config

    store = tmp_path / "app_config.json"
    monkeypatch.setattr(global_config, "_CONFIG_FILE", store)
    monkeypatch.setattr(global_config, "_cached", None)
    monkeypatch.setattr(global_config, "_cached_mtime", 0.0)
    assert download_hold._store_path() == store, (
        "the isolated store was not adopted; this test would read the host's "
        "real app_config.json")
    assert download_hold.hold_state()["state"] == download_hold.CLEAR
    return store


class _StartsRunning:
    """A runner whose start() really arms (state becomes running)."""

    def __init__(self):
        self._state = "paused"
        self.started = 0

    def start(self):
        self.started += 1
        self._state = "running"


def _post_resume_all(monkeypatch, pairs):
    from bulk_downloader import app_runners
    monkeypatch.setattr(app_runners, "_runners_snapshot", lambda: list(pairs))
    app = Flask(__name__)
    app.register_blueprint(app_runners.runners_bp)
    resp = app.test_client().post("/api/runners/resume_all")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def test_resume_all_does_not_count_a_runner_the_download_hold_refused(
        monkeypatch, hold_store):
    from bulk_downloader import download_hold

    held = _runner("dlr_held")
    monkeypatch.setattr(held, "log_event", lambda *a, **k: None)
    assert download_hold.hold("dl-runner test", note="dl-runner #5")
    assert download_hold.downloads_allowed()[0] is False, "hold did not take"
    body = _post_resume_all(monkeypatch, [("dlr_held", held)])
    assert held._state != "running", (
        "fixture: start() armed the pool under a hold; the case is not tested")
    assert body["resumed"] == 0, (
        "DL-RUNNER-5: resume_all reported resumed=%r while the hold left the "
        "runner in state %r" % (body["resumed"], held._state))
    assert body.get("not_started") == [
        {"site_id": "dlr_held", "state": str(held._state)}], body


def test_resume_all_counts_a_runner_that_started(monkeypatch):
    """Control: a runner start() really arms is still counted, nothing listed."""
    ok = _StartsRunning()
    body = _post_resume_all(monkeypatch, [("dlr_ok", ok)])
    assert ok.started == 1 and ok._state == "running"
    assert body["resumed"] == 1
    assert body.get("not_started", []) == []


# ── #9 drain ────────────────────────────────────────────────────────────


@pytest.fixture()
def engine(monkeypatch):
    from bulk_downloader import task_drain_engine as mod
    e = mod.TaskStatePauseDrainEngine()
    monkeypatch.setattr(mod, "_GLOBAL_ENGINE", e)
    return e


def _runner_processing_one_url(monkeypatch, site_id):
    """A real SiteRunner whose worker is inside _process_one for one URL."""
    from bulk_downloader.runner import SiteRunner

    r = SiteRunner(site_id, {"name": site_id, "max_concurrent": 1})
    r._state = "running"
    inside, release = threading.Event(), threading.Event()
    monkeypatch.setattr(r, "_claim_worker_item",
                        lambda idx, url, gen: ("claimed", gen))
    monkeypatch.setattr(r, "_update_job", lambda *a, **k: None)

    def _process_one(browser, url, persistent_ctx=None):
        inside.set()
        assert release.wait(10), "test never released the in-flight URL"

    monkeypatch.setattr(r, "_process_one", _process_one)
    worker = threading.Thread(
        target=r._process_worker_url,
        args=(0, None, f"https://example.test/{site_id}/a.mp4"), daemon=True)
    worker.start()
    assert inside.wait(10), "fixture: the worker never reached _process_one"
    return r, release, worker


def test_drain_waits_for_a_url_still_being_processed(monkeypatch, engine):
    r, release, worker = _runner_processing_one_url(monkeypatch, "dlr_drain")
    try:
        status = r.drain(timeout_seconds=0.2)
        assert status == "timed_out", (
            "DL-RUNNER-9: drain() answered %r while a worker was still inside "
            "_process_one (in_flight=%d)" % (status, engine.in_flight_count("dlr_drain")))
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert engine.in_flight_count("dlr_drain") == 0, "the URL's operation leaked"
    assert r.drain(timeout_seconds=0.2) == "drained"


def test_drain_with_no_work_in_flight_is_drained(engine):
    """Control: an idle runner still drains at once."""
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner("dlr_idle", {"name": "dlr_idle"})
    r._state = "running"
    assert r.drain(timeout_seconds=0.2) == "drained"
    assert r._state == "paused"


def test_an_operation_on_a_drained_task_makes_it_running_again(engine):
    from bulk_downloader import task_drain_engine as mod
    engine.register_task("t")
    engine.pause_task("t")
    assert engine.drain_task("t", 0.1).status == "drained"
    engine.acquire_operation("t", "op1")
    state = engine.get_task_status("t").state
    assert state == mod.TaskState.RUNNING.value, (
        "DL-RUNNER-9: task stayed %r with %d in-flight operation(s)"
        % (state, engine.in_flight_count("t")))


def test_an_operation_on_a_paused_task_keeps_it_paused(engine):
    """Control: only a SETTLED drain is ended by new work; a pause is not."""
    from bulk_downloader import task_drain_engine as mod
    engine.register_task("p")
    engine.pause_task("p")
    engine.acquire_operation("p", "op1")
    assert engine.get_task_status("p").state == mod.TaskState.PAUSED.value
    assert engine.drain_task("p", 0.05).status == "timed_out"


# ── #9 r2: the claim/drain seam (REFUTE cx162 F1) ──────────────────────
# r1 claimed (published the URL as running) BEFORE registering its drain
# operation, so a drain landing in between saw zero work, answered 'drained',
# and the worker then processed the URL anyway. Both orientations of that seam
# are pinned deterministically: no sleeps decide the order, barriers do.


def _claim_race_runner(monkeypatch, site_id):
    r, urls = _seeded(site_id, ["pending"])
    r._state = "running"
    r._pause.set()
    processed = []
    monkeypatch.setattr(r, "_update_job", lambda *a, **k: None)
    monkeypatch.setattr(
        r, "_process_one",
        lambda browser, url, persistent_ctx=None: processed.append(
            (r._state, r.jobs[url]["status"])))
    return r, urls["pending"], processed


def test_a_drain_during_the_claim_counts_the_url(monkeypatch, engine):
    """Drain arrives while the worker is between registration and claim: the
    URL's operation is already counted, so the drain cannot say 'drained'."""
    r, url, processed = _claim_race_runner(monkeypatch, "dlr_claim_mid")
    at_claim, release = threading.Event(), threading.Event()
    original_claim = r._claim_worker_item

    def claim(idx, u, gen):
        at_claim.set()
        assert release.wait(10), "test never released the claim"
        return original_claim(idx, u, gen)

    monkeypatch.setattr(r, "_claim_worker_item", claim)
    worker = threading.Thread(target=r._process_worker_url, args=(0, None, url), daemon=True)
    worker.start()
    try:
        assert at_claim.wait(10), "fixture: the worker never reached the claim"
        status = r.drain(timeout_seconds=0.1)
        assert status == "timed_out", (
            "DL-RUNNER-9-R2: drain() answered %r with a worker about to claim "
            "(in_flight=%d) -- the claim is not counted by the drain"
            % (status, engine.in_flight_count("dlr_claim_mid")))
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert processed == [("paused", "running")], processed
    assert engine.in_flight_count("dlr_claim_mid") == 0, "the URL's operation leaked"
    assert r.drain(timeout_seconds=0.1) == "drained"


def test_a_drain_decided_before_the_claim_holds_the_url_until_resume(
        monkeypatch, engine, hold_store):
    """Drain decides 'no work' first: a worker that has the URL but has not
    claimed it must not claim or process it until the runner is resumed."""
    r, url, processed = _claim_race_runner(monkeypatch, "dlr_claim_after")
    assert r.drain(timeout_seconds=0.1) == "drained"
    worker = threading.Thread(target=r._process_worker_url, args=(0, None, url), daemon=True)
    worker.start()
    worker.join(0.6)
    try:
        assert processed == [] and r.jobs[url]["status"] == "pending", (
            "DL-RUNNER-9-R2: a URL was claimed/processed after drain() "
            "answered 'drained' (processed=%r status=%r)"
            % (processed, r.jobs[url]["status"]))
        assert worker.is_alive(), "the held worker gave up instead of waiting"
        r.resume()
        worker.join(10)
    finally:
        r._pause.set()
        worker.join(10)
    assert not worker.is_alive()
    assert processed == [("running", "running")], processed
    assert engine.in_flight_count("dlr_claim_after") == 0


def test_a_worker_held_by_a_drain_lets_go_on_stop(monkeypatch, engine):
    """Control: a held worker exits on Stop without claiming the URL."""
    r, url, processed = _claim_race_runner(monkeypatch, "dlr_claim_stop")
    assert r.drain(timeout_seconds=0.1) == "drained"
    out = []
    worker = threading.Thread(
        target=lambda: out.append(r._process_worker_url(0, None, url)), daemon=True)
    worker.start()
    worker.join(0.3)
    r._stop.set()
    worker.join(10)
    assert out == [r._WORKER_CLAIM_INELIGIBLE] and processed == []
    assert r.jobs[url]["status"] == "pending"
    assert engine.in_flight_count("dlr_claim_stop") == 0


# ── #9 r3: a stale un-pause must not reopen a newer drain (REFUTE cx180 F1) ──
# r2's held worker saw the runner un-paused and reopened the task with an
# unconditional resume_task(). If a second drain completed between that
# observation and the write, the write reopened the NEW drain: drain() had
# answered 'drained', yet the worker then claimed and processed its URL while
# the runner stayed paused. cx180's schedule, pinned by barriers.


@pytest.mark.parametrize("second_drain", [True, False], ids=["new_drain", "no_new_drain"])
def test_a_stale_unpause_does_not_reopen_a_newer_drain(
        monkeypatch, engine, hold_store, second_drain):
    site = "dlr_stale_%s" % ("new" if second_drain else "ctl")
    r, url, processed = _claim_race_runner(monkeypatch, site)
    assert r.drain(timeout_seconds=0.1) == "drained"
    refused, observe = threading.Event(), threading.Event()
    at_resume, resume_go = threading.Event(), threading.Event()
    retried = threading.Event()
    after_resume, retries = [], []
    original_try, original_resume = engine.try_acquire_operation, engine.resume_task
    worker = threading.Thread(target=r._process_worker_url, args=(0, None, url), daemon=True)

    def try_acquire(*a, **k):
        op = original_try(*a, **k)
        if threading.current_thread() is worker:
            if not refused.is_set():
                refused.set()
                assert observe.wait(10), "test never released the refused worker"
            elif after_resume and not retried.is_set():
                retries.append(op)
                retried.set()
        return op

    def resume(*a, **k):
        if threading.current_thread() is worker:
            at_resume.set()
            assert resume_go.wait(10), "test never released the worker's resume"
            out = original_resume(*a, **k)
            after_resume.append(True)
            return out
        return original_resume(*a, **k)

    monkeypatch.setattr(engine, "try_acquire_operation", try_acquire)
    monkeypatch.setattr(engine, "resume_task", resume)
    worker.start()
    try:
        assert refused.wait(10), "fixture: the drain never refused the worker"
        r.resume()  # legitimate resume: the first drain is over
        assert r._state == "running" and r._pause.is_set()
        observe.set()
        assert at_resume.wait(10), "fixture: the worker never reached its resume"
        if second_drain:
            assert r.drain(timeout_seconds=0.1) == "drained"
            assert r._state == "paused" and not r._pause.is_set()
        resume_go.set()
        assert retried.wait(10), "fixture: the worker never retried admission"
        if second_drain:
            assert retries == [None] and processed == [], (
                "DL-RUNNER-9-R3: a stale un-pause reopened a newer drain that had "
                "answered 'drained' (admitted=%r processed=%r state=%s)"
                % (retries, processed, engine.get_task_status(site).state))
            assert r.jobs[url]["status"] == "pending"
            r.resume()  # the newer drain is over: now the URL goes
        worker.join(10)
        assert not worker.is_alive()
        assert processed == [("running", "running")], processed
    finally:
        r._stop.set()
        observe.set()
        resume_go.set()
        worker.join(10)
    assert engine.in_flight_count(site) == 0


def test_a_resume_from_an_older_drain_leaves_the_task_held(engine):
    from bulk_downloader import task_drain_engine as mod
    engine.register_task("g")
    assert engine.drain_task("g", 0.05).status == "drained"
    first = engine.drain_generation("g")
    engine.resume_task("g")
    assert engine.drain_task("g", 0.05).status == "drained"
    engine.resume_task("g", drain_generation=first)
    assert engine.get_task_status("g").state == mod.TaskState.DRAINED.value
    assert engine.try_acquire_operation("g") is None
    engine.resume_task("g", drain_generation=engine.drain_generation("g"))
    assert engine.get_task_status("g").state == mod.TaskState.RUNNING.value
    # A drain-scoped resume lifts only a drain's hold, never a plain pause.
    engine.pause_task("g")
    engine.resume_task("g", drain_generation=engine.drain_generation("g"))
    assert engine.get_task_status("g").state == mod.TaskState.PAUSED.value
