"""appfeat-F013 r3 -- per-JOB pause / resume / retry on the Queue page.

UIUX-20260928 F013 (agy-feat-26 r2, v3.66.1841): the row Pause button posted
/api/sites/<sid>/pause, pausing the whole SITE, and a cancelled job had no
retry control because the queue page never rendered terminal jobs. Backend
had only the per-site bulk_* list endpoints, which answer 200 with a zero
count on every refusal, so the SPA could not tell "paused" from "ignored".

Three single-job endpoints beside /api/queue/v2/cancel, each with a state
guard that answers 4xx WITH A REASON instead of a silent zero:

    POST /api/queue/v2/pause   {site_id, url}   pending -> stopped (+_paused_by_user)
    POST /api/queue/v2/resume  {site_id, url}   paused  -> pending (same queue slot)
    POST /api/queue/v2/retry   {site_id, url}   failed/cancelled -> pending

RED on 346655ee: the three routes are not registered (405/404).
The fake runner below carries exactly the surface app_queue.py touches
(_lock, jobs, urls, _update_job) plus the real QueueMixin bulk_pause /
bulk_resume so the pause flag semantics are the production ones;
queue_bulk_update is stubbed to a recorder (no DB in a unit test).
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

_REPO = str(Path(__file__).resolve().parents[1])
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

pytestmark = pytest.mark.bd_module_wipe
BD_GATE_SCOPE = "module"

SID = "f013-site"
U_A = "https://example.invalid/f013/a.mp4"
U_B = "https://example.invalid/f013/b.mp4"
U_C = "https://example.invalid/f013/c.mp4"
U_D = "https://example.invalid/f013/d.mp4"


class _FakeRunner:
    """jobs is insertion-ordered: A pending, B pending, C running, D done."""

    def __init__(self):
        from bulk_downloader.runner_queue import QueueMixin
        self._mixin = QueueMixin
        self.site_id = SID
        self._lock = threading.Lock()
        self._run_lifecycle_lock = threading.RLock()
        self.urls = [U_A, U_B, U_C, U_D]
        self.jobs = {
            U_A: {"status": "pending", "message": "", "filename": "a.mp4"},
            U_B: {"status": "pending", "message": "", "filename": "b.mp4"},
            U_C: {"status": "running", "message": "", "filename": "c.mp4"},
            U_D: {"status": "done", "message": "", "filename": "d.mp4"},
        }
        self.updates = []
        self.persisted = []

    # the central mutation path /api/queue/v2/cancel already uses
    def _update_job(self, url, status, message, **extra):
        # r4: like the real _update_job_current, the private publication
        # kwargs are consumed, and an already-applied memory flip (the
        # retry's atomic compare-and-set) is not re-applied.
        memory_done = extra.pop("_memory_already_updated", False)
        extra.pop("_transition_prev_status", None)
        self.updates.append((url, status, message, dict(extra)))
        if not memory_done:
            self.jobs[url].update({"status": status, "message": message, **extra})
        return True

    def _job_status_writer(self):
        return self._mixin._job_status_writer(self)

    def bulk_pause(self, urls):
        return self._mixin.bulk_pause(self, urls)

    def bulk_resume(self, urls):
        return self._mixin.bulk_resume(self, urls)

    def _log_queue_persist_failure(self, verb, urls, exc):
        self.persisted.append(("FAIL", verb, list(urls), repr(exc)))


@pytest.fixture
def runner(monkeypatch):
    from bulk_downloader import app_state as st
    from bulk_downloader import runner_queue as rq
    r = _FakeRunner()
    monkeypatch.setattr(
        rq, "queue_bulk_update",
        lambda site_id, urls, **f: r.persisted.append((site_id, list(urls), f)))
    st.s_cfg[SID] = {"name": SID}
    st.runners[SID] = r
    try:
        yield r
    finally:
        st.runners.pop(SID, None)
        st.s_cfg.pop(SID, None)


@pytest.fixture
def client():
    from bulk_downloader import app as a
    return a.app.test_client()


def _post(client, path, url):
    return client.post(path, json={"site_id": SID, "url": url})


def _snapshot(r):
    return (list(r.jobs), list(r.urls),
            {u: j["status"] for u, j in r.jobs.items()})


# ── routes ────────────────────────────────────────────────────────────

def test_f013_routes_registered():
    from bulk_downloader import app as a
    rules = {r.rule for r in a.app.url_map.iter_rules()}
    for p in ("/api/queue/v2/pause", "/api/queue/v2/resume", "/api/queue/v2/retry"):
        assert p in rules, p


# ── pause ─────────────────────────────────────────────────────────────

def test_f013_pause_one_pending_job_only(client, runner):
    before = _snapshot(runner)
    r = _post(client, "/api/queue/v2/pause", U_A)
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True and body["paused"] is True
    assert body["previous_status"] == "pending"
    assert runner.jobs[U_A]["status"] == "stopped"
    assert runner.jobs[U_A]["_paused_by_user"] is True
    # negative control: neighbours untouched, order untouched, no site-wide effect
    assert runner.jobs[U_B]["status"] == "pending"
    assert runner.jobs[U_C]["status"] == "running"
    assert list(runner.jobs) == before[0] and runner.urls == before[1]
    assert runner.persisted and runner.persisted[-1][1] == [U_A]


def test_f013_pause_twice_is_noop_not_error(client, runner):
    assert _post(client, "/api/queue/v2/pause", U_A).status_code == 200
    r = _post(client, "/api/queue/v2/pause", U_A)
    assert r.status_code == 200
    assert r.get_json()["noop"] is True
    assert runner.jobs[U_A]["status"] == "stopped"


@pytest.mark.parametrize("url,status", [(U_C, "running"), (U_D, "done")])
def test_f013_pause_refuses_running_and_finished_with_reason(client, runner, url, status):
    before = _snapshot(runner)
    r = _post(client, "/api/queue/v2/pause", url)
    assert r.status_code == 409, r.data
    body = r.get_json()
    assert body["ok"] is False
    assert body["status"] == status
    assert "pause" in body["error"] and status in body["error"]
    assert _snapshot(runner) == before
    assert runner.updates == []


# ── resume ────────────────────────────────────────────────────────────

def test_f013_resume_restores_prior_slot_and_only_that_job(client, runner):
    assert _post(client, "/api/queue/v2/pause", U_B).status_code == 200
    order_before = list(runner.jobs), list(runner.urls)
    r = _post(client, "/api/queue/v2/resume", U_B)
    assert r.status_code == 200, r.data
    assert r.get_json()["resumed"] is True
    assert runner.jobs[U_B]["status"] == "pending"
    assert runner.jobs[U_B]["_paused_by_user"] is False
    assert (list(runner.jobs), list(runner.urls)) == order_before
    assert runner.urls.index(U_B) == 1  # same slot as before the pause
    assert runner.jobs[U_A]["status"] == "pending"


def test_f013_resume_pending_is_noop(client, runner):
    r = _post(client, "/api/queue/v2/resume", U_A)
    assert r.status_code == 200
    assert r.get_json()["noop"] is True


def test_f013_resume_refuses_cancelled_job_with_reason(client, runner):
    # user-cancelled (stopped, no pause flag) is not "paused": say so
    runner._update_job(U_A, "stopped", "Cancelled by user")
    r = _post(client, "/api/queue/v2/resume", U_A)
    assert r.status_code == 409
    body = r.get_json()
    assert body["status"] == "stopped" and "retry" in body["error"]
    assert runner.jobs[U_A]["status"] == "stopped"


# ── retry ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["stopped", "failed", "cancelled", "error"])
def test_f013_retry_requeues_cancelled_or_failed_job_only(client, runner, status):
    runner._update_job(U_A, status, "Cancelled by user")
    runner.updates.clear()
    before = _snapshot(runner)
    r = _post(client, "/api/queue/v2/retry", U_A)
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["retried"] is True and body["previous_status"] == status
    assert runner.jobs[U_A]["status"] == "pending"
    assert runner.jobs[U_A]["retries"] == 0 and runner.jobs[U_A]["retry_after"] == 0
    assert runner.updates == [(U_A, "pending", "Retry requested",
                               {"retries": 0, "retry_after": 0})]
    assert list(runner.jobs) == before[0] and runner.urls == before[1]
    assert runner.jobs[U_B]["status"] == "pending"


def test_f013_retry_double_click_is_idempotent(client, runner):
    runner._update_job(U_A, "stopped", "Cancelled by user")
    assert _post(client, "/api/queue/v2/retry", U_A).status_code == 200
    r = _post(client, "/api/queue/v2/retry", U_A)
    assert r.status_code == 200
    assert r.get_json()["noop"] is True
    assert runner.urls.count(U_A) == 1
    assert [u for u in runner.updates if u[1] == "pending"] == [
        (U_A, "pending", "Retry requested", {"retries": 0, "retry_after": 0})]


def test_f013_retry_re_adds_a_job_dropped_from_the_url_list(client, runner):
    runner._update_job(U_A, "failed", "boom")
    runner.urls.remove(U_A)
    r = _post(client, "/api/queue/v2/retry", U_A)
    assert r.status_code == 200
    assert runner.urls.count(U_A) == 1


@pytest.mark.parametrize("url,status", [(U_C, "running"), (U_D, "done")])
def test_f013_retry_refuses_running_and_done_with_reason(client, runner, url, status):
    before = _snapshot(runner)
    r = _post(client, "/api/queue/v2/retry", url)
    assert r.status_code == 409, r.data
    body = r.get_json()
    assert body["ok"] is False and body["status"] == status
    assert "retry" in body["error"] and status in body["error"]
    assert _snapshot(runner) == before and runner.updates == []


def test_f013_retry_refuses_paused_job_points_at_resume(client, runner):
    assert _post(client, "/api/queue/v2/pause", U_A).status_code == 200
    r = _post(client, "/api/queue/v2/retry", U_A)
    assert r.status_code == 409
    assert "resume" in r.get_json()["error"]
    assert runner.jobs[U_A]["status"] == "stopped"


# ── shared argument guards (same shape as /api/queue/v2/cancel) ───────

@pytest.mark.parametrize("path", ["/api/queue/v2/pause", "/api/queue/v2/resume", "/api/queue/v2/retry"])
def test_f013_unknown_site_and_unknown_url(client, runner, path):
    r = client.post(path, json={"site_id": "nope", "url": U_A})
    assert r.status_code == 400 and r.get_json()["ok"] is False
    r = client.post(path, json={"site_id": SID, "url": "https://example.invalid/not-queued"})
    assert r.status_code == 404 and r.get_json()["ok"] is False
    r = client.post(path, json={"site_id": SID})
    assert r.status_code == 400


# ── terminal records expose the pause flag so the SPA can pick Resume vs Retry ──

def test_f013_queue_v2_terminal_entry_carries_paused_flag(client, runner):
    assert _post(client, "/api/queue/v2/pause", U_A).status_code == 200
    runner._update_job(U_B, "stopped", "Cancelled by user")
    body = client.get("/api/queue/v2").get_json()
    term = {t["url"]: t for t in body["terminal"] if t["site_id"] == SID}
    assert term[U_A]["status"] == "stopped" and term[U_A]["paused"] is True
    assert term[U_B]["status"] == "stopped" and term[U_B]["paused"] is False


# ── r4: cx67 F013-r3 REFUTE findings (F1-F3), ported from evidence/cx67 ──
# Each interleaves a runner transition in the window after the endpoint's
# resolver snapshot (or across a real SQLite reload); r3 trusted the
# snapshot and failed all three.

def test_f013_r4_f1_retry_cannot_make_active_claim_pending(client, runner, monkeypatch):
    """F1: a concurrent retry + real runner claim between the resolver
    snapshot and the mutation must not flip running -> pending (409)."""
    from bulk_downloader import runner as rm, app_state, app_queue as aq
    monkeypatch.setattr(rm.SiteRunner, "start_scheduler", lambda self: None)
    monkeypatch.setattr(rm.SiteRunner, "_start_auto_retry", lambda self: None)
    real = rm.SiteRunner(SID, {"name": SID, "max_retries": 2})
    real.jobs[U_A] = {"status": "failed", "message": "failed", "filename": "a.mp4"}
    real.urls[:] = [U_A]
    monkeypatch.setitem(app_state.runners, SID, real)
    resolve = aq._f013_resolve
    interleaved = []

    def interleave(body):
        result = resolve(body)

        def worker():
            real._update_job(U_A, "pending", "Other retry request", retries=0, retry_after=0)
            assert real._claim_worker_item(0, U_A)[0] == "claimed"
        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive()
        assert real.jobs[U_A]["status"] == "running"
        interleaved.append(result[2])
        return result
    monkeypatch.setattr(aq, "_f013_resolve", interleave)
    r = _post(client, "/api/queue/v2/retry", U_A)
    assert interleaved == ["failed"]  # positive control: the window was hit
    assert real.jobs[U_A]["status"] == "running", (r.status_code, r.get_json(), real.jobs[U_A])
    assert r.status_code == 409, (r.status_code, r.get_json())
    assert r.get_json()["status"] == "running"


def test_f013_r4_f2_resume_survives_real_sqlite_reload(client, runner, monkeypatch):
    """F2: a job paused through the endpoint, persisted, and restored by a
    NEW SiteRunner from the same disposable SQLite queue still resumes, and
    the terminal record still offers Resume (paused=True), not Retry."""
    from bulk_downloader import runner as rm, runner_queue as rq, app_state, db
    assert client.get("/api/queue/v2").status_code == 200  # boot disposable SQLite
    monkeypatch.setattr(rm.SiteRunner, "start_scheduler", lambda self: None)
    monkeypatch.setattr(rm.SiteRunner, "_start_auto_retry", lambda self: None)
    monkeypatch.setattr(rq, "queue_bulk_update", db.queue_bulk_update)
    db.queue_upsert(SID, U_A, status="pending", message="")
    db.queue_upsert(SID, U_B, status="stopped", message="Cancelled by user")
    real = rm.SiteRunner(SID, {"name": SID})
    monkeypatch.setitem(app_state.runners, SID, real)
    assert _post(client, "/api/queue/v2/pause", U_A).status_code == 200
    assert real.jobs[U_A]["_paused_by_user"] is True
    row = next(r for r in db.queue_load(SID) if r["url"] == U_A)
    assert (row["status"], row["message"]) == ("stopped", "Paused by user")
    restored = rm.SiteRunner(SID, {"name": SID})
    monkeypatch.setitem(app_state.runners, SID, restored)
    assert restored.jobs[U_A]["status"] == "stopped"
    term = {t["url"]: t for t in client.get("/api/queue/v2").get_json()["terminal"]
            if t["site_id"] == SID}
    assert term[U_A]["paused"] is True and term[U_B]["paused"] is False
    r = _post(client, "/api/queue/v2/resume", U_A)
    assert r.status_code == 200, (r.status_code, r.get_json(), restored.jobs[U_A])
    assert restored.jobs[U_A]["status"] == "pending"
    # negative control: a restored CANCELLED job is still not resumable
    r = _post(client, "/api/queue/v2/resume", U_B)
    assert r.status_code == 409 and "retry" in r.get_json()["error"]


def test_f013_r4_f3_pause_refuses_after_runner_claim(client, runner, monkeypatch):
    """F3: when the runner claims the pending job after the resolver
    snapshot, bulk_pause pauses nothing; the endpoint must say 409, not
    200/paused=true."""
    from bulk_downloader import app_queue as aq
    resolve = aq._f013_resolve
    interleaved = []

    def interleave(body):
        result = resolve(body)
        with runner._lock:
            runner.jobs[U_A]["status"] = "running"
        interleaved.append(result[2])
        return result
    monkeypatch.setattr(aq, "_f013_resolve", interleave)
    r = _post(client, "/api/queue/v2/pause", U_A)
    assert interleaved == ["pending"]  # positive control: the window was hit
    assert r.status_code == 409, (r.status_code, r.get_json())
    assert r.get_json()["status"] == "running"
    assert runner.jobs[U_A]["status"] == "running"
    assert not runner.jobs[U_A].get("_paused_by_user")


@pytest.mark.parametrize("transition", ["done", "paused"])
def test_f013_r5_retry_publication_preserves_newer_sqlite_transition(
        client, runner, monkeypatch, transition):
    """A competing completion or Pause must survive Retry publication/reload."""
    from bulk_downloader import runner as rm, runner_queue as rq, app_state, db, app
    assert client.get("/api/queue/v2").status_code == 200
    monkeypatch.setattr(rm.SiteRunner, "start_scheduler", lambda self: None)
    monkeypatch.setattr(rm.SiteRunner, "_start_auto_retry", lambda self: None)
    monkeypatch.setattr(rq, "queue_bulk_update", db.queue_bulk_update)
    db.queue_upsert(SID, U_A, status="failed", message="Failed fixture")
    real = rm.SiteRunner(SID, {"name": SID})
    monkeypatch.setitem(app_state.runners, SID, real)
    assert real.jobs[U_A]["status"] == "failed"
    publish = real._update_job
    attempted = threading.Event()
    attempts, observed, errors, threads = [], [], [], []
    expected = "done" if transition == "done" else "stopped"

    def competitor():
        acquired = real._run_lifecycle_lock.acquire(blocking=False)
        attempts.append(acquired)
        attempted.set()
        if not acquired:
            real._run_lifecycle_lock.acquire()
        try:
            if transition == "done":
                assert real._claim_worker_item(0, U_A)[0] == "claimed"
                publish(U_A, "running", "Claim published",
                        _transition_prev_status="pending", _memory_already_updated=True)
                publish(U_A, "done", "Finished during Retry publication")
            else:
                response = _post(app.app.test_client(), "/api/queue/v2/pause", U_A)
                assert response.status_code == 200, response.get_json()
                assert real.jobs[U_A]["_paused_by_user"] is True
            row = next(r for r in db.queue_load(SID) if r["url"] == U_A)
            assert real.jobs[U_A]["status"] == row["status"] == expected
            observed.append(row["status"])
        except BaseException as exc:
            errors.append(exc)
        finally:
            real._run_lifecycle_lock.release()

    def delayed(url, status, message, **extra):
        if status == "pending" and message == "Retry requested":
            thread = threading.Thread(target=competitor)
            threads.append(thread)
            thread.start()
            assert attempted.wait(5), "competing writer did not attempt publication gap"
            if attempts == [True]:
                # On r4 the guard is released: force the newer write BEFORE
                # Retry publishes. With serialization, join AFTER the API
                # returns so the test never deadlocks a correctly held guard.
                thread.join(5)
                assert not thread.is_alive(), "competing transition did not complete"
                assert not errors, errors
                assert observed == [expected]
        return publish(url, status, message, **extra)

    monkeypatch.setattr(real, "_update_job", delayed)
    try:
        response = _post(client, "/api/queue/v2/retry", U_A)
    finally:
        for thread in threads:
            thread.join(5)
    assert len(threads) == len(attempts) == 1
    assert all(not thread.is_alive() for thread in threads), "publication guard deadlocked"
    assert not errors, errors
    assert response.status_code == 200, response.get_json()
    assert observed == [expected]  # positive control: real newer write persisted
    row = next(r for r in db.queue_load(SID) if r["url"] == U_A)
    assert real.jobs[U_A]["status"] == expected
    assert row["status"] == expected, "Retry publisher overwrote a newer SQLite transition"
    restored = rm.SiteRunner(SID, {"name": SID})
    assert restored.jobs[U_A]["status"] == expected
    assert bool(restored.jobs[U_A].get("_paused_by_user")) == (transition == "paused")
