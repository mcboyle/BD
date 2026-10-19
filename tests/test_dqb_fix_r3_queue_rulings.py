"""dqb-fix r3 -- the deep-queue-bulk rows the PM ruled on (F3 F6 F7).

F3  bulk_priority swallowed a failed queue write (`except Exception: pass`):
    the route answered ok while memory and the queue table disagreed.
    Ruling: 500 + rollback, error logged.
F6  bulk_resume resumed ANY stopped job, so a user cancel came back to life.
    Ruling: resume only user-paused jobs; cancel is terminal.
F7  retry_one refused dead_letter while bulk_retry accepted it.
    Ruling: an explicit user retry (both paths) accepts dead_letter; the
    automatic retries never touch it.

Every assertion message carries a distinctive diagnostic token.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import sqlite3
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytestmark = pytest.mark.bd_module_wipe

_SID = "dqbfixr3"
_A = "https://example.invalid/dqb3/a.mp4"
_B = "https://example.invalid/dqb3/b.mp4"
_C = "https://example.invalid/dqb3/c.mp4"


@pytest.fixture
def qdb(clean_workdir):
    from bulk_downloader.db import db_init
    db_init()
    return clean_workdir


@pytest.fixture
def registered():
    from bulk_downloader import app_state as st

    def _reg(runner):
        st.s_cfg[_SID] = {"name": _SID}
        st.runners[_SID] = runner
        return runner
    yield _reg
    st.runners.pop(_SID, None)
    st.s_cfg.pop(_SID, None)


def _runner(**cfg):
    from bulk_downloader.runner import SiteRunner
    return SiteRunner(_SID, {"name": _SID, **cfg})


def _seed(r, url, status="pending", message="", ord_=0, **extra):
    from bulk_downloader.db import queue_upsert
    queue_upsert(_SID, url, status=status, message=message, ord=ord_)
    with r._lock:
        r.jobs[url] = {"status": status, "message": message, "ts": "00:00:01",
                       **extra}
        if url not in r.urls:
            r.urls.append(url)


def _row(url):
    from bulk_downloader.db import db_conn
    with db_conn() as cx:
        return cx.execute(
            "SELECT status, message, priority FROM queue WHERE site_id=? AND url=?",
            (_SID, url)).fetchone()


def _post(path, payload):
    from bulk_downloader import app as a
    client = a.app.test_client()
    client.get("/")
    tok = (client.get("/api/csrf").get_json() or {}).get("csrf_token", "")
    return client.post(path, json=payload, headers={"X-CSRF-Token": tok})


def _three(r):
    for i, u in enumerate((_A, _B, _C)):
        _seed(r, u, ord_=i)


def _boom(*a, **k):
    raise sqlite3.OperationalError("database is locked (dqb3 injected)")


# -- F3 ---------------------------------------------------------------------

@pytest.mark.parametrize("target", ["queue_set_priority", "queue_reorder"])
def test_f3_bulk_priority_persist_failure_is_500_and_rolled_back(
        qdb, registered, monkeypatch, target):
    r = registered(_runner())
    _three(r)
    log = mock.Mock()
    monkeypatch.setattr(r, "log", log)
    monkeypatch.setattr(f"bulk_downloader.runner_queue.{target}", _boom)
    resp = _post(f"/api/sites/{_SID}/bulk_priority", {"urls": [_C], "priority": "high"})
    body = resp.get_json(silent=True) or {}
    assert resp.status_code == 500 and body.get("ok") is False, (
        f"F3_PERSIST_FAILURE_SWALLOWED {target}: {resp.status_code} "
        f"{resp.get_data(as_text=True)[:200]}")
    assert r.urls == [_A, _B, _C], f"F3_ORDER_NOT_ROLLED_BACK {target}: {r.urls!r}"
    assert not r.jobs[_C].get("priority"), \
        f"F3_PRIORITY_NOT_ROLLED_BACK {target}: {r.jobs[_C]!r}"
    assert not _row(_C)[2], f"F3_ROW_PRIORITY_NOT_ROLLED_BACK {target}: {tuple(_row(_C))!r}"
    assert _runner().urls == [_A, _B, _C], f"F3_ROW_ORDER_NOT_ROLLED_BACK {target}"
    assert log.error.called, f"F3_PERSIST_FAILURE_NOT_LOGGED {target}: {log.mock_calls!r}"


def test_lens_f3_rollback_is_linear_in_the_queue(qdb, monkeypatch):
    """lens DQB3C-A: the rollback runs under runner._lock; a membership test
    against the list per URL made it O(n^2) (3.8 s at 20k jobs)."""
    class _Urls(list):
        hits = 0
        def __contains__(self, u):
            type(self).hits += 1
            return list.__contains__(self, u)
    r = _runner()
    us = [f"https://example.invalid/dqb3/n{i}" for i in range(200)]
    with r._lock:
        for u in us:
            r.jobs[u] = {"status": "pending", "message": "", "priority": "normal"}
        r.urls = _Urls(us)
    monkeypatch.setattr("bulk_downloader.runner_queue.queue_reorder", _boom)
    with pytest.raises(sqlite3.OperationalError):
        r.bulk_priority([us[-1]], "high")
    assert r.urls == us, f"F3_ORDER_NOT_ROLLED_BACK: {r.urls[:3]!r}"
    assert _Urls.hits < 10, f"F3_ROLLBACK_QUADRATIC: {_Urls.hits} list scans"


def test_f3_bulk_priority_persist_failure_raises_to_the_caller(qdb, monkeypatch):
    r = _runner()
    _three(r)
    monkeypatch.setattr("bulk_downloader.runner_queue.queue_set_priority", _boom)
    with pytest.raises(sqlite3.OperationalError):
        r.bulk_priority([_B], "normal")
    assert not r.jobs[_B].get("priority"), f"F3_DIRECT_NOT_ROLLED_BACK: {r.jobs[_B]!r}"


def test_f3_control_bulk_priority_persists_and_answers_ok(qdb, registered):
    r = registered(_runner())
    _three(r)
    resp = _post(f"/api/sites/{_SID}/bulk_priority", {"urls": [_C], "priority": "high"})
    assert resp.status_code == 200 and resp.get_json().get("ok") is True, \
        f"F3_CONTROL_NOT_OK: {resp.get_data(as_text=True)[:200]}"
    assert r.urls[0] == _C and _row(_C)[2] == "high", "F3_CONTROL_NOT_PERSISTED"
    assert _runner().urls[0] == _C, "F3_CONTROL_ORDER_NOT_PERSISTED"


# -- F6 ---------------------------------------------------------------------

def test_f6_bulk_resume_leaves_a_user_cancel_cancelled(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="stopped", message="Cancelled by user")
    resp = _post(f"/api/sites/{_SID}/bulk_resume", {"urls": [_A]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert r.jobs[_A]["status"] == "stopped", f"F6_CANCEL_RESUMED: {r.jobs[_A]!r}"
    assert _row(_A)[0] == "stopped", f"F6_CANCEL_ROW_RESUMED: {tuple(_row(_A))!r}"
    assert resp.get_json().get("resumed") == 0, f"F6_CANCEL_COUNTED: {resp.get_json()!r}"


def test_f6_control_bulk_pause_then_resume_round_trips(qdb, registered):
    r = registered(_runner())
    _seed(r, _A)
    assert r.bulk_pause([_A]) == 1
    resp = _post(f"/api/sites/{_SID}/bulk_resume", {"urls": [_A]})
    assert resp.get_json().get("resumed") == 1, f"F6_PAUSE_NOT_RESUMED: {resp.get_json()!r}"
    assert r.jobs[_A]["status"] == "pending" and _row(_A)[0] == "pending", \
        "F6_PAUSE_ROUND_TRIP_LOST"


def test_f6_control_pause_restored_after_restart_still_resumes(qdb):
    r = _runner()
    _seed(r, _A)
    r.bulk_pause([_A])
    r2 = _runner()
    assert r2.bulk_resume([_A]) == 1, f"F6_RESTORED_PAUSE_NOT_RESUMED: {r2.jobs.get(_A)!r}"


# -- F7 ---------------------------------------------------------------------

def test_f7_retry_one_accepts_dead_letter(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="dead_letter", message="gave up")
    resp = _post(f"/api/sites/{_SID}/retry_one", {"url": _A})
    assert resp.status_code == 200, \
        f"F7_RETRY_ONE_REFUSES_DEAD_LETTER: {resp.get_data(as_text=True)[:200]}"
    assert r.jobs[_A]["status"] == "pending" and _row(_A)[0] == "pending", \
        f"F7_RETRY_ONE_NOT_PENDING: {r.jobs[_A]!r}"


def test_f7_control_bulk_retry_accepts_dead_letter(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="dead_letter", message="gave up")
    resp = _post(f"/api/sites/{_SID}/bulk_retry", {"urls": [_A]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert r.jobs[_A]["status"] == "pending", f"F7_BULK_RETRY_REFUSES_DEAD_LETTER: {r.jobs[_A]!r}"


def test_f7_control_auto_retry_scan_skips_dead_letter(qdb):
    """Positive control beside the zero: the same scan retries a failed job."""
    r = _runner(auto_retry_failed=True, auto_retry_review=True,
                auto_retry_classify=False)
    due = {"auto_retry_count": 0, "next_auto_retry_at": 1.0}
    _seed(r, _A, status="failed", message="timeout", **due)
    _seed(r, _B, status="dead_letter", message="timeout", ord_=1, **due)
    r._auto_retry_scan()
    assert r.jobs[_A]["status"] == "pending", f"F7_SCAN_DID_NOT_FIRE: {r.jobs[_A]!r}"
    assert r.jobs[_B]["status"] == "dead_letter", f"F7_SCAN_RETRIED_DEAD_LETTER: {r.jobs[_B]!r}"


def test_f7_control_retry_failed_skips_dead_letter(qdb):
    r = _runner()
    _seed(r, _A, status="failed", message="timeout")
    _seed(r, _B, status="dead_letter", message="gave up", ord_=1)
    r.retry_failed()
    assert r.jobs[_A]["status"] == "pending", f"F7_RETRY_FAILED_DID_NOT_FIRE: {r.jobs[_A]!r}"
    assert r.jobs[_B]["status"] == "dead_letter", \
        f"F7_RETRY_FAILED_RETRIED_DEAD_LETTER: {r.jobs[_B]!r}"
