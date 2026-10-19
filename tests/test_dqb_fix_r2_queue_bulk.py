"""dqb-fix r2 -- the deep-queue-bulk sweep's buildable rows (F2 F4 F5 F8 F9).

F2  /priority wrote queue_upsert for ANY url: a never-queued or just-deleted
    url got a row, and the next restart rehydrated it as a pending job.
F4  a worker write for a url deleted while in flight re-created the job (the
    unknown-url auto-create in _update_job_current) and its row: after a
    restart the deleted job was back as done.
F5  the bulk_* routes iterated whatever `urls` was: 5 -> 500, "u" -> chars.
F8  jobs/bulk_mark overwrote a RUNNING job (the worker owns running) and left
    "Paused by user" / _paused_by_user on a job it moved to pending.
F9  bulk_priority / set_priority "high" front-loaded in memory only; the
    restart rebuilt the old order.

Every assertion message carries a distinctive diagnostic token.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import sys
import threading
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytestmark = pytest.mark.bd_module_wipe

_SID = "dqbfixr2"
_A = "https://example.invalid/dqb/a.mp4"
_B = "https://example.invalid/dqb/b.mp4"
_C = "https://example.invalid/dqb/c.mp4"


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


def _runner():
    from bulk_downloader.runner import SiteRunner
    return SiteRunner(_SID, {"name": _SID})


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


# -- F2 ---------------------------------------------------------------------

def test_f2_priority_on_never_queued_url_creates_no_row(qdb):
    r = _runner()
    r.set_priority(_A, "high")
    assert _row(_A) is None, f"F2_PRIORITY_CREATED_ROW: {tuple(_row(_A))!r}"
    assert _A not in _runner().jobs, "F2_PRIORITY_RESTORED_AS_JOB"


def test_f2_priority_after_delete_does_not_resurrect(qdb):
    r = _runner()
    _seed(r, _A)
    assert r.bulk_delete([_A]) == 1
    r.set_priority(_A, "normal")
    assert _row(_A) is None, f"F2_PRIORITY_RESURRECTED_ROW: {tuple(_row(_A))!r}"


def test_f2_control_priority_on_live_job_persists(qdb):
    r = _runner()
    _seed(r, _A)
    r.set_priority(_A, "high")
    assert _row(_A) is not None and _row(_A)[2] == "high", "F2_LIVE_PRIORITY_NOT_PERSISTED"


# -- F4 ---------------------------------------------------------------------

def _worker_finishes(r, url):
    return r._update_job(url, "done", "Downloaded", filename="a.mp4", file_size=7)


def test_f4_worker_write_after_bulk_delete_does_not_recreate(qdb):
    r = _runner()
    _seed(r, _A, status="running", message="Downloading")
    assert r.bulk_delete([_A]) == 1
    _worker_finishes(r, _A)
    assert _A not in r.jobs, f"F4_DELETED_JOB_RECREATED: {r.jobs.get(_A)!r}"
    assert _row(_A) is None, f"F4_DELETED_ROW_RECREATED: {tuple(_row(_A))!r}"
    assert _A not in _runner().jobs, "F4_DELETED_JOB_BACK_AFTER_RESTART"


def test_f4_worker_write_after_route_delete_does_not_recreate(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="running", message="Downloading")
    resp = _post(f"/api/sites/{_SID}/jobs/bulk_delete", {"urls": [_A]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _worker_finishes(r, _A)
    assert _A not in r.jobs, f"F4_ROUTE_DELETED_JOB_RECREATED: {r.jobs.get(_A)!r}"
    assert _row(_A) is None, "F4_ROUTE_DELETED_ROW_RECREATED"


def test_f4_control_worker_write_on_live_job_lands(qdb):
    r = _runner()
    _seed(r, _A, status="running", message="Downloading")
    _worker_finishes(r, _A)
    assert r.jobs[_A]["status"] == "done", "F4_LIVE_WRITE_LOST"
    assert _row(_A)[0] == "done", "F4_LIVE_ROW_NOT_PERSISTED"


def test_f4_control_readded_url_takes_worker_writes(qdb):
    """A tombstone only blocks re-creation: a url queued again is a job."""
    r = _runner()
    _seed(r, _A, status="running")
    r.bulk_delete([_A])
    _seed(r, _A, status="running")
    _worker_finishes(r, _A)
    assert r.jobs[_A]["status"] == "done", "F4_READDED_WRITE_LOST"


# -- F5 ---------------------------------------------------------------------

_BULK = ("bulk_priority", "bulk_delete", "bulk_approve", "bulk_pause",
         "bulk_resume", "bulk_retry")


@pytest.mark.parametrize("route", _BULK)
@pytest.mark.parametrize("urls", [5, True, 1.5, _A, {_A: 1}],
                         ids=["int", "bool", "float", "str", "dict"])
def test_f5_bulk_routes_validate_the_url_list(qdb, registered, route, urls):
    r = registered(_runner())
    _seed(r, _A)
    resp = _post(f"/api/sites/{_SID}/{route}", {"urls": urls, "priority": "high"})
    assert resp.status_code == 400, (
        f"F5_BULK_UNVALIDATED {route} urls={urls!r} -> {resp.status_code} "
        f"{resp.get_data(as_text=True)[:200]}")
    assert r.jobs[_A]["status"] == "pending", f"F5_BULK_ACTED_ON_BAD_BODY {route}"


@pytest.mark.parametrize("route", _BULK)
def test_f5_control_bulk_routes_take_a_list(qdb, registered, route):
    r = registered(_runner())
    _seed(r, _A)
    resp = _post(f"/api/sites/{_SID}/{route}", {"urls": [f" {_A} "], "priority": "high"})
    assert resp.status_code == 200, f"F5_LIST_REFUSED {route} {resp.get_data(as_text=True)[:200]}"


# -- F8 ---------------------------------------------------------------------

def test_f8_bulk_mark_refuses_a_running_source(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="running", message="Downloading")
    _seed(r, _B, status="failed", message="boom", ord_=1)
    resp = _post(f"/api/sites/{_SID}/jobs/bulk_mark",
                 {"urls": [_A, _B], "status": "done"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert r.jobs[_A]["status"] == "running", f"F8_RUNNING_OVERWRITTEN: {r.jobs[_A]!r}"
    assert _row(_A)[0] == "running", f"F8_RUNNING_ROW_OVERWRITTEN: {tuple(_row(_A))!r}"
    assert resp.get_json()["affected"] == 1, f"F8_AFFECTED_COUNTS_RUNNING: {resp.get_json()!r}"
    assert r.jobs[_B]["status"] == "done" and _row(_B)[0] == "done", "F8_CONTROL_NOT_MARKED"


def test_f8_bulk_mark_clears_paused_by_user(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="stopped", message="Paused by user", _paused_by_user=True)
    resp = _post(f"/api/sites/{_SID}/jobs/bulk_mark", {"urls": [_A], "status": "pending"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    j = r.jobs[_A]
    assert j["status"] == "pending"
    assert j.get("message") != "Paused by user", f"F8_PAUSED_MESSAGE_KEPT: {j!r}"
    assert not j.get("_paused_by_user"), f"F8_PAUSED_FLAG_KEPT: {j!r}"
    assert _row(_A)[1] != "Paused by user", f"F8_PAUSED_ROW_MESSAGE_KEPT: {tuple(_row(_A))!r}"


def test_f8_control_operator_message_wins(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="stopped", message="Paused by user", _paused_by_user=True)
    _post(f"/api/sites/{_SID}/jobs/bulk_mark",
          {"urls": [_A], "status": "failed", "message": "bad source"})
    assert r.jobs[_A]["message"] == "bad source" and _row(_A)[1] == "bad source", \
        "F8_OPERATOR_MESSAGE_LOST"


# -- F9 ---------------------------------------------------------------------

def _three(r):
    for i, u in enumerate((_A, _B, _C)):
        _seed(r, u, ord_=i)


def test_f9_bulk_priority_high_order_survives_restart(qdb):
    r = _runner()
    _three(r)
    r.bulk_priority([_C], "high")
    assert r.urls[0] == _C
    restored = _runner().urls
    assert restored and restored[0] == _C, f"F9_BULK_HIGH_ORDER_LOST: {restored!r}"


def test_f9_set_priority_high_order_survives_restart(qdb):
    r = _runner()
    _three(r)
    r.set_priority(_C, "high")
    assert r.urls[0] == _C
    restored = _runner().urls
    assert restored and restored[0] == _C, f"F9_SINGLE_HIGH_ORDER_LOST: {restored!r}"


def test_f9_control_normal_priority_keeps_order(qdb):
    r = _runner()
    _three(r)
    r.bulk_priority([_C], "normal")
    assert _runner().urls == [_A, _B, _C], "F9_NORMAL_REORDERED"
