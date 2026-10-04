"""O1826 C22 -- job-detail history and the single-URL mark route.

M021: api_jobs_detail asked db_search for the site's newest 20 history rows
and THEN kept the ones for this url. A site with more than 20 rows newer than
the job's own rows showed the job an empty history. The url filter belongs in
the SQL, before the LIMIT, and it must be an exact match: a LIKE substring
would also pull in sibling urls that merely contain this one.

M020: api_jobs_mark changed the in-memory job and then swallowed a
queue_upsert failure with a bare ``pass``, answering ok while the DB kept the
old status (a restart rehydrates the old status). The sibling routes log the
failure; this one now restores the in-memory job, logs, and returns an error.
"""
from __future__ import annotations

# H622 slice A. An ordinary module test: its subject is the module under
# test, not the tree, so it is not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

import sys
import threading
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytestmark = pytest.mark.bd_module_wipe

_SID = "o1826c22"
_JOB = "https://example.invalid/o1826/job.mp4"


@pytest.fixture
def history_db(clean_workdir):
    """Isolated BD home with the sqlite tables created."""
    from bulk_downloader.db import db_init
    db_init()
    return clean_workdir


class _Runner:
    """The surface api_jobs_detail / api_jobs_mark dereference."""

    def __init__(self, jobs):
        self._lock = threading.Lock()
        self.jobs = jobs
        self.events = []

    def log_event(self, *a, **k):
        self.events.append((a, k))


@pytest.fixture
def registered():
    from bulk_downloader import app_state as st
    made = []

    def _reg(runner):
        st.s_cfg[_SID] = {"name": _SID}
        st.runners[_SID] = runner
        made.append(runner)
        return runner
    yield _reg
    st.runners.pop(_SID, None)
    st.s_cfg.pop(_SID, None)


def _client():
    from bulk_downloader import app as a
    client = a.app.test_client()
    client.get("/")                       # warm the bd_session cookie
    return client


def _post(path, payload):
    client = _client()
    tok = (client.get("/api/csrf").get_json() or {}).get("csrf_token", "")
    return client.post(path, json=payload, headers={"X-CSRF-Token": tok})


def _detail(url):
    resp = _client().get(f"/api/sites/{_SID}/jobs/detail",
                         query_string={"url": url})
    assert resp.status_code == 200, (resp.status_code, resp.get_data(as_text=True))
    return resp.get_json()


def _log(url, status, message):
    from bulk_downloader.db import db_log
    db_log(_SID, _SID, url, status, message=message)


# -- M021: job history ------------------------------------------------------

def test_job_history_survives_30_newer_site_rows(history_db, registered):
    """RED on base: 30 newer rows for other urls push the job's only row out
    of the site-wide newest 20, so the job's history reads empty."""
    _log(_JOB, "failed", "o1826-job-row")
    for i in range(30):
        _log(f"https://example.invalid/o1826/other-{i}.mp4", "done", f"other-{i}")
    registered(_Runner({_JOB: {"status": "failed", "message": ""}}))

    body = _detail(_JOB)
    msgs = [h.get("message") for h in body["history"]]
    assert msgs == ["o1826-job-row"], (
        f"job history lost its only row behind 30 newer site rows: {body!r}")
    assert body["history_count"] == 1
    assert body["history"][0]["url"] == _JOB


def test_newest_rows_still_shown_newest_first(history_db, registered):
    """NEG: a job whose rows ARE among the newest keeps all of them, newest
    first; other urls' rows never leak in."""
    newest = "https://example.invalid/o1826/newest.mp4"
    for i in range(10):
        _log(f"https://example.invalid/o1826/old-{i}.mp4", "done", f"old-{i}")
    _log(newest, "failed", "newest-1")
    _log(newest, "done", "newest-2")
    registered(_Runner({newest: {"status": "done", "message": ""}}))

    body = _detail(newest)
    assert [h.get("message") for h in body["history"]] == ["newest-2", "newest-1"], body
    assert {h.get("url") for h in body["history"]} == {newest}


def test_history_is_exact_url_not_substring(history_db, registered):
    """A LIKE '%url%' filter would also return url+suffix siblings, and with
    more than 20 of them would crowd the job's own row out again."""
    _log(_JOB, "failed", "o1826-job-row")
    for i in range(25):
        _log(f"{_JOB}?part={i}", "done", f"sibling-{i}")
    registered(_Runner({_JOB: {"status": "failed", "message": ""}}))

    body = _detail(_JOB)
    assert [h.get("message") for h in body["history"]] == ["o1826-job-row"], body


def test_history_is_capped_at_20_newest(history_db, registered):
    """The cap is per url now: 25 rows for the job -> its newest 20."""
    for i in range(25):
        _log(_JOB, "failed", f"attempt-{i}")
    registered(_Runner({_JOB: {"status": "failed", "message": ""}}))

    body = _detail(_JOB)
    assert [h.get("message") for h in body["history"]] == [
        f"attempt-{i}" for i in range(24, 4, -1)], body
    assert body["history_count"] == 20


# -- M020: mark persists or reports ----------------------------------------

def _queue_status(url):
    from bulk_downloader.db import db_conn
    with db_conn() as cx:
        row = cx.execute("SELECT status, message FROM queue WHERE site_id=? AND url=?",
                         (_SID, url)).fetchone()
    return tuple(row) if row else None


def test_successful_mark_persists(history_db, registered):
    """NEG: the normal path still answers ok, changes the job and writes the
    queue row."""
    r = registered(_Runner({_JOB: {"status": "needs_review", "message": ""}}))
    resp = _post(f"/api/sites/{_SID}/jobs/mark",
                 {"url": _JOB, "status": "failed", "message": "operator said no"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["ok"] is True
    assert r.jobs[_JOB]["status"] == "failed"
    assert _queue_status(_JOB) == ("failed", "operator said no")
    assert [a[0] for a, _ in r.events] == ["manual_mark"]


def test_mark_persist_failure_reverts_and_reports(history_db, registered,
                                                 monkeypatch, capsys):
    """RED on base: queue_upsert raises, the route answers ok anyway and the
    in-memory job keeps a status the DB never got."""
    from bulk_downloader import app_sites_queue as asq

    def _boom(*a, **k):
        raise RuntimeError("o1826-disk-full")
    monkeypatch.setattr(asq, "queue_upsert", _boom)

    job = {"status": "needs_review", "message": "awaiting review",
           "ts": "01:02:03", "auto_retry_count": 2, "next_auto_retry_at": 99}
    before = dict(job)
    r = registered(_Runner({_JOB: job}))
    resp = _post(f"/api/sites/{_SID}/jobs/mark",
                 {"url": _JOB, "status": "failed", "message": "operator said no"})

    assert resp.status_code == 500, (resp.status_code, resp.get_data(as_text=True))
    body = resp.get_json()
    assert body["ok"] is False and "o1826-disk-full" in body["error"], body
    assert r.jobs[_JOB] == before, (
        f"in-memory job not restored after the DB write failed: {r.jobs[_JOB]!r}")
    assert r.jobs[_JOB] is job
    assert not [e for e in r.events if e[0][0] == "manual_mark"], r.events
    assert "o1826-disk-full" in capsys.readouterr().err


def _mark_inside_failing_persist(monkeypatch, second_writer):
    """A marks needs_review -> failed. Inside A's queue_upsert another writer
    runs to completion through its real route and persists; THEN A's write
    fails, as it would with "database is locked" behind that writer."""
    from bulk_downloader import app_sites_queue as asq
    real = asq.queue_upsert

    def _upsert(sid, url, **k):
        if k.get("message") == "A":
            resp = second_writer()
            assert resp.status_code == 200, resp.get_data(as_text=True)
            raise RuntimeError("database is locked")
        return real(sid, url, **k)
    monkeypatch.setattr(asq, "queue_upsert", _upsert)
    return _post(f"/api/sites/{_SID}/jobs/mark",
                 {"url": _JOB, "status": "failed", "message": "A"})


def test_same_status_mark_inside_failed_persist_survives(history_db, registered,
                                                        monkeypatch):
    """r1 REFUTE F1: B marks the job to the SAME status inside A's failing
    persist window and B's write lands. A's restore must not put the job back
    to needs_review over B (memory needs_review vs DB failed/B)."""
    from bulk_downloader import app_sites_queue as asq
    r = registered(_Runner({_JOB: {"status": "needs_review", "message": "awaiting"}}))
    resp = _mark_inside_failing_persist(monkeypatch, lambda: _post(
        f"/api/sites/{_SID}/jobs/mark",
        {"url": _JOB, "status": "failed", "message": "B"}))

    assert resp.status_code == 500, (resp.status_code, resp.get_data(as_text=True))
    assert resp.get_json()["ok"] is False
    assert _queue_status(_JOB) == ("failed", "B")
    assert (r.jobs[_JOB]["status"], r.jobs[_JOB]["message"]) == ("failed", "B"), (
        f"A's failed persist erased B's persisted mark in memory: {r.jobs[_JOB]!r}")
    assert (_SID, _JOB) not in asq._mark_tokens


def test_same_status_bulk_mark_inside_failed_persist_survives(history_db, registered,
                                                             monkeypatch):
    """The same window with B as a bulk mark: also a mark, also not erased."""
    from bulk_downloader import app_sites_queue as asq
    r = registered(_Runner({_JOB: {"status": "needs_review", "message": "awaiting"}}))
    resp = _mark_inside_failing_persist(monkeypatch, lambda: _post(
        f"/api/sites/{_SID}/jobs/bulk_mark",
        {"urls": [_JOB], "status": "failed", "message": "B"}))

    assert resp.status_code == 500, (resp.status_code, resp.get_data(as_text=True))
    assert (r.jobs[_JOB]["status"], r.jobs[_JOB]["message"]) == ("failed", "B"), (
        f"A's failed persist erased B's bulk mark in memory: {r.jobs[_JOB]!r}")
    assert (_SID, _JOB) not in asq._mark_tokens


def _worker_write_inside_failing_persist(registered, monkeypatch, worker_message):
    """A real SiteRunner: the job is running and the operator marks it done
    (A). Inside A's queue_upsert the worker's download finishes through
    SiteRunner._update_job, which takes no mark token and persists; THEN A's
    write fails."""
    from bulk_downloader import app_sites_queue as asq
    from bulk_downloader.runner import SiteRunner
    real = asq.queue_upsert
    r = SiteRunner(_SID, {"name": _SID})
    r.jobs[_JOB] = {"status": "running", "message": "Downloading", "ts": "00:00:01"}
    registered(r)

    def _upsert(sid, url, **k):
        if k.get("message") == "A":
            r._update_job(url, "done", worker_message, filename="w.mp4", file_size=7)
            raise RuntimeError("database is locked")
        return real(sid, url, **k)
    monkeypatch.setattr(asq, "queue_upsert", _upsert)
    resp = _post(f"/api/sites/{_SID}/jobs/mark",
                 {"url": _JOB, "status": "done", "message": "A"})
    assert resp.status_code == 500, (resp.status_code, resp.get_data(as_text=True))
    return r


def test_worker_write_inside_failed_persist_survives(history_db, registered,
                                                     monkeypatch):
    """r2 REFUTE N1: the worker's own write to the SAME status lands inside
    A's failing persist. A's restore must not put the job back to running
    (memory running vs DB done, with no worker left to own it)."""
    from bulk_downloader import app_sites_queue as asq
    r = _worker_write_inside_failing_persist(registered, monkeypatch,
                                             "Downloaded by worker")
    assert _queue_status(_JOB) == ("done", "Downloaded by worker")
    assert (r.jobs[_JOB]["status"], r.jobs[_JOB]["message"]) == _queue_status(_JOB), (
        f"A's failed persist erased the worker's persisted write: {r.jobs[_JOB]!r}")
    assert (_SID, _JOB) not in asq._mark_tokens


def test_worker_equal_value_write_inside_failed_persist_survives(
        history_db, registered, monkeypatch):
    """The worker rewrites exactly A's values -- same status, same message,
    same second. Still a newer, persisted write: the restore compares the
    objects A wrote, not their values."""
    from bulk_downloader import app_sites_queue as asq
    from bulk_downloader import runner as runner_mod
    for mod in (asq, runner_mod):          # one second: equal, fresh strings
        monkeypatch.setattr(mod, "_ts", lambda: f"12:00:{0:02d}")
        monkeypatch.setattr(mod, "_ts_iso", lambda: f"2026-10-04T12:00:{0:02d}")
    r = _worker_write_inside_failing_persist(registered, monkeypatch, "A")
    assert _queue_status(_JOB) == ("done", "A")
    assert (r.jobs[_JOB]["status"], r.jobs[_JOB]["message"],
            r.jobs[_JOB]["ts"]) == ("done", "A", "12:00:00"), (
        f"A's failed persist erased the worker's equal-value write: {r.jobs[_JOB]!r}")


def test_key_added_inside_failed_persist_survives(history_db, registered,
                                                  monkeypatch):
    """A marks pending, which leaves next_auto_retry_at unset. Inside A's
    window a single-key writer (as the auto-retry scheduler) sets it. A key A
    left absent is a key A touched too: the restore must not pop the newer
    value."""
    from bulk_downloader import app_sites_queue as asq
    r = registered(_Runner({_JOB: {"status": "failed", "message": "awaiting"}}))

    def _upsert(sid, url, **k):
        with r._lock:
            r.jobs[url]["next_auto_retry_at"] = 1234.5
        raise RuntimeError("database is locked")
    monkeypatch.setattr(asq, "queue_upsert", _upsert)
    resp = _post(f"/api/sites/{_SID}/jobs/mark",
                 {"url": _JOB, "status": "pending", "message": "A"})
    assert resp.status_code == 500, (resp.status_code, resp.get_data(as_text=True))
    assert r.jobs[_JOB].get("next_auto_retry_at") == 1234.5, (
        f"A's failed persist popped a key written inside its window: {r.jobs[_JOB]!r}")


def test_mark_tokens_do_not_accumulate(history_db, registered, monkeypatch):
    """NEG: a finished mark, ok or failed, leaves no token behind."""
    from bulk_downloader import app_sites_queue as asq
    registered(_Runner({_JOB: {"status": "needs_review", "message": ""}}))
    assert _post(f"/api/sites/{_SID}/jobs/mark",
                 {"url": _JOB, "status": "failed"}).status_code == 200
    assert (_SID, _JOB) not in asq._mark_tokens

    def _boom(*a, **k):
        raise RuntimeError("o1826-disk-full")
    monkeypatch.setattr(asq, "queue_upsert", _boom)
    assert _post(f"/api/sites/{_SID}/jobs/mark",
                 {"url": _JOB, "status": "done"}).status_code == 500
    assert (_SID, _JOB) not in asq._mark_tokens
