"""dqb-fix F8 single -- the jobs/mark route, as dqb-fix-r2 fixed jobs/bulk_mark.

F8  jobs/mark overwrote a RUNNING job (the worker owns running) and left
    "Paused by user" / _paused_by_user on a job it moved on, so the job
    still read as user-paused (dqb r3 F6: bulk_resume acts on that flag).

Every assertion message carries a distinctive diagnostic token.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytestmark = pytest.mark.bd_module_wipe

_SID = "dqbf8single"
_A = "https://example.invalid/dqbf8/a.mp4"


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


def _seed(r, url, status="pending", message="", **extra):
    from bulk_downloader.db import queue_upsert
    queue_upsert(_SID, url, status=status, message=message, ord=0)
    with r._lock:
        r.jobs[url] = {"status": status, "message": message, "ts": "00:00:01",
                       **extra}
        if url not in r.urls:
            r.urls.append(url)


def _row(url):
    from bulk_downloader.db import db_conn
    with db_conn() as cx:
        return cx.execute(
            "SELECT status, message FROM queue WHERE site_id=? AND url=?",
            (_SID, url)).fetchone()


def _mark(payload):
    from bulk_downloader import app as a
    client = a.app.test_client()
    client.get("/")
    tok = (client.get("/api/csrf").get_json() or {}).get("csrf_token", "")
    return client.post(f"/api/sites/{_SID}/jobs/mark", json=payload,
                       headers={"X-CSRF-Token": tok})


def test_f8_single_mark_refuses_a_running_job(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="running", message="Downloading")
    resp = _mark({"url": _A, "status": "done"})
    assert resp.status_code == 409 and (resp.get_json() or {}).get("ok") is False, (
        f"F8_SINGLE_RUNNING_ACCEPTED: {resp.status_code} {resp.get_data(as_text=True)[:200]}")
    assert r.jobs[_A]["status"] == "running", f"F8_SINGLE_RUNNING_OVERWRITTEN: {r.jobs[_A]!r}"
    assert _row(_A)[0] == "running", f"F8_SINGLE_RUNNING_ROW_OVERWRITTEN: {tuple(_row(_A))!r}"


def test_f8_single_mark_clears_paused_by_user(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="stopped", message="Paused by user", _paused_by_user=True)
    resp = _mark({"url": _A, "status": "pending"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    j = r.jobs[_A]
    assert j["status"] == "pending"
    assert j.get("message") != "Paused by user", f"F8_SINGLE_PAUSED_MESSAGE_KEPT: {j!r}"
    assert not j.get("_paused_by_user"), f"F8_SINGLE_PAUSED_FLAG_KEPT: {j!r}"
    assert _row(_A)[1] != "Paused by user", \
        f"F8_SINGLE_PAUSED_ROW_MESSAGE_KEPT: {tuple(_row(_A))!r}"
    assert not _runner().jobs[_A].get("_paused_by_user"), "F8_SINGLE_PAUSED_BACK_AFTER_RESTART"


def test_f8_single_mark_failed_persist_restores_the_pause(qdb, registered, monkeypatch):
    """O1826 M020 rollback covers the retired pause identity too."""
    r = registered(_runner())
    _seed(r, _A, status="stopped", message="Paused by user", _paused_by_user=True)

    def _boom(*a, **k):
        raise OSError("disk full (dqbf8 injected)")
    monkeypatch.setattr("bulk_downloader.app_sites_queue.queue_upsert", _boom)
    resp = _mark({"url": _A, "status": "pending"})
    assert resp.status_code == 500, resp.get_data(as_text=True)
    j = r.jobs[_A]
    assert (j["status"], j.get("message"), j.get("_paused_by_user")) == (
        "stopped", "Paused by user", True), f"F8_SINGLE_ROLLBACK_LOST_PAUSE: {j!r}"


def test_f8_single_control_operator_message_wins(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="stopped", message="Paused by user", _paused_by_user=True)
    _mark({"url": _A, "status": "failed", "message": "bad source"})
    assert r.jobs[_A]["message"] == "bad source" and _row(_A)[1] == "bad source", \
        "F8_SINGLE_OPERATOR_MESSAGE_LOST"


def test_f8_single_control_non_running_mark_lands(qdb, registered):
    r = registered(_runner())
    _seed(r, _A, status="needs_review", message="check me")
    resp = _mark({"url": _A, "status": "failed"})
    assert resp.status_code == 200 and r.jobs[_A]["status"] == "failed", \
        f"F8_SINGLE_CONTROL_NOT_MARKED: {resp.get_data(as_text=True)[:200]}"
    assert tuple(_row(_A)) == ("failed", "check me"), f"F8_SINGLE_CONTROL_ROW: {tuple(_row(_A))!r}"
