"""Row 722 G26: bulk_delete then add_url of the same URL starts a fresh job
(retry count 0, no backoff).

Live (dfxtra/evilangel): after jobs/bulk_delete + add_url the re-added URL sat
'pending' with job_log 'Retry 2/2 in 1h' inherited from the deleted job and was
never claimed. Two owners: the queue table's INSERT OR IGNORE kept a stale
row's retries/retry_after, and a failure published for a job deleted while in
flight auto-created the row again with the old ladder.
"""
import logging
import sqlite3
import threading

import pytest

BD_GATE_SCOPE = "module"

URL = "https://members.example.test/video/1"


class _FakeQ:
    """QueueMixin surface with real load_urls / bulk_delete."""

    def __new__(cls):
        import bulk_downloader.runner_queue as m
        inst = type("FakeQ", (m.QueueMixin,), {})()
        inst.config = {"download_dir": "", "name": "s"}
        inst.jobs = {}
        inst.urls = []
        inst.site_id = "row722"
        inst._lock = threading.RLock()
        inst.log = logging.getLogger("test_rq")
        inst.log_event = lambda *a, **k: None
        return inst


@pytest.fixture
def isolated_db():
    # conftest's autouse isolated_bd_home chdirs into tmp_path; the relative
    # DB_PATH resolves there, so db_init creates a private queue table.
    from bulk_downloader import db
    db.db_init()
    return db


def _row(db):
    with db.db_conn() as cx:
        r = cx.execute("SELECT * FROM queue WHERE site_id='row722' AND url=?",
                       (URL,)).fetchone()
        return dict(r) if r else None


def test_readded_url_after_bulk_delete_starts_at_retry_zero(isolated_db, monkeypatch):
    db = isolated_db
    import bulk_downloader.content_rights as cr
    import bulk_downloader.runner_queue as m
    monkeypatch.setattr(cr, "url_is_blocked", lambda u: None, raising=False)
    q = _FakeQ()
    q.load_urls([URL])
    # the worker failed twice: the ladder is at 2/2 with a 1h backoff
    q.jobs[URL].update({"status": "pending", "retries": 2, "retry_after": 9e12,
                        "message": "Retry 2/2 in 1h — worker error: Locator.count"})
    db.queue_upsert("row722", URL, status="pending", retries=2, retry_after=9e12,
                    message="Retry 2/2 in 1h — worker error: Locator.count")
    # the durable DELETE is the part that failed live (swallowed by bulk_delete)
    monkeypatch.setattr(m, "queue_bulk_delete",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    assert q.bulk_delete([URL]) == 1
    assert URL not in q.jobs
    stale = _row(db)
    assert stale and stale["retries"] == 2, stale
    added, dupes, _ = q.load_urls([URL])
    assert (added, dupes) == (1, 0)
    assert q.jobs[URL]["retries"] == 0 and q.jobs[URL]["retry_after"] == 0
    fresh = _row(db)
    assert fresh is not None
    assert (fresh["retries"], fresh["retry_after"], fresh["status"], fresh["message"]) == (0, 0, "pending", ""), (
        "re-added URL inherited the deleted job's retry ladder: %r" % (fresh,))


def test_restart_after_readd_restores_a_fresh_job(isolated_db, monkeypatch):
    db = isolated_db
    import bulk_downloader.content_rights as cr
    monkeypatch.setattr(cr, "url_is_blocked", lambda u: None, raising=False)
    db.queue_upsert("row722", URL, status="pending", retries=2, retry_after=9e12,
                    message="Retry 2/2 in 1h")
    q = _FakeQ()          # in-memory map empty: the operator deleted the job
    q.load_urls([URL])
    q2 = _FakeQ()
    q2._restore_queue()
    assert q2.jobs[URL]["retries"] == 0, q2.jobs[URL]
    assert q2.jobs[URL]["retry_after"] == 0, q2.jobs[URL]


def _failure_surface(jobs):
    from bulk_downloader.runner import SiteRunner
    r = type("FailureSurface", (), {})()
    r.site_id = "row722"
    r.config = {"name": "s", "max_retries": 2}
    r.jobs = jobs
    r._lock = threading.Lock()
    r._RETRY_DELAYS_BY_KIND = SiteRunner._RETRY_DELAYS_BY_KIND
    r._classify_error = SiteRunner._classify_error.__get__(r)
    updates = []
    r._update_job = lambda *a, **k: updates.append((a, k))
    return r, updates


def test_failure_for_a_job_deleted_in_flight_is_not_published(monkeypatch):
    from bulk_downloader import hooks, runner_telemetry
    from bulk_downloader.runner import SiteRunner
    monkeypatch.setattr(runner_telemetry, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(hooks, "fire_event", lambda *a, **k: None)
    r, updates = _failure_surface({})   # bulk_delete removed it mid-attempt
    out = SiteRunner._handle_failure_current(r, URL, "worker error: Locator.count: SyntaxError")
    assert out is False
    assert updates == [], ("deleted job resurrected with a retry ladder: %r" % (updates,))


def test_plain_retry_keeps_its_count(monkeypatch):
    from bulk_downloader import hooks, runner_telemetry
    from bulk_downloader.runner import SiteRunner
    monkeypatch.setattr(runner_telemetry, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(hooks, "fire_event", lambda *a, **k: None)
    r, updates = _failure_surface({URL: {"retries": 1, "status": "running"}})
    SiteRunner._handle_failure_current(r, URL, "ordinary unclassified failure")
    (args, kwargs), = updates
    assert args[1] == "pending" and args[2].startswith("Retry 2/2 in "), args
    assert kwargs["retries"] == 2
