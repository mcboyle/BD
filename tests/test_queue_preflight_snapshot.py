"""Queue preflight must not occupy request threads while checks are blocked."""

import os
import select
import signal
import threading

import pytest
from flask import Flask

BD_GATE_SCOPE = "module"


@pytest.fixture
def preflight(monkeypatch):
    from bulk_downloader import app_queue, cookie_health, daily_budget, selector_drift

    monkeypatch.setattr(app_queue, "_PREFLIGHT_CACHE", {}, raising=False)
    clock = [0.0]
    monkeypatch.setattr(app_queue, "monotonic", lambda: clock[0], raising=False)
    monkeypatch.setattr(app_queue, "_app_runners", dict)
    monkeypatch.setattr(app_queue, "_app_s_cfg", dict)
    monkeypatch.setattr(app_queue, "_oi_default_download_dir", lambda: "")
    monkeypatch.setattr(
        app_queue,
        "_chk",
        lambda key, label, status, detail="": {
            "key": key,
            "label": label,
            "status": status,
            "detail": detail,
        },
    )
    monkeypatch.setattr(
        app_queue,
        "_oi_flagged",
        lambda rows, flag_keys=(): sum(
            bool(any(row.get(key) for key in flag_keys)) for row in rows
        ),
    )
    monkeypatch.setattr(cookie_health, "status_all", list)
    monkeypatch.setattr(selector_drift, "status_all", list)
    monkeypatch.setattr(daily_budget, "status_all", lambda _configs: [])
    app = Flask(__name__)
    app.add_url_rule("/preflight", view_func=app_queue.api_queue_preflight)
    return app, daily_budget, cookie_health, clock


def _join_refresh():
    workers = [t for t in threading.enumerate() if t.name == "QueuePreflight"]
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive(), "PREFLIGHT_REFRESH_DID_NOT_FINISH"


def test_blocked_checks_return_pending_without_holding_requests(preflight, monkeypatch):
    app, budget, _, _ = preflight
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    calls, responses = [], []

    def held(_configs):
        calls.append(1)
        entered.set()
        assert release.wait(5), "fixture release was not signalled"
        return []

    monkeypatch.setattr(budget, "status_all", held)

    def request():
        try:
            with app.test_client() as client:
                responses.append(client.get("/preflight"))
        finally:
            returned.set()

    thread = threading.Thread(target=request, daemon=True)
    thread.start()
    try:
        assert entered.wait(3), "positive control: budget check was never reached"
        was_returned = returned.wait(1)
        assert was_returned, "PREFLIGHT_REQUEST_WAITED_FOR_BLOCKED_CHECK"
        response = responses[0]
        assert response.status_code == 200
        assert response.json["pending"] is True and response.json["ready"] is False
        with app.test_client() as client:
            again = client.get("/preflight")
        assert again.json["pending"] is True
        assert calls == [1], "PREFLIGHT_DUPLICATED_INFLIGHT_CHECKS"
    finally:
        release.set()
        thread.join(3)
        _join_refresh()
    with app.test_client() as client:
        complete = client.get("/preflight")
    assert complete.json["ready"] is True
    assert calls == [1], "PREFLIGHT_COMPLETED_RESULT_NOT_CACHED"


def test_completed_snapshot_is_reused_then_refreshed(preflight, monkeypatch):
    app, budget, _, clock = preflight
    calls = []
    monkeypatch.setattr(
        budget, "status_all", lambda configs: calls.append(configs) or []
    )
    with app.test_client() as client:
        client.get("/preflight")
        _join_refresh()
        response = client.get("/preflight")
        assert response.json["ready"] is True
        assert len(calls) == 1, "PREFLIGHT_CACHE_MISS_ON_FRESH_SNAPSHOT"
        clock[0] = 31.0
        client.get("/preflight")
        _join_refresh()
        assert len(calls) == 2, "PREFLIGHT_EXPIRED_SNAPSHOT_NOT_REFRESHED"


def test_real_failure_is_preserved_in_completed_snapshot(preflight, monkeypatch):
    app, _, health, _ = preflight
    monkeypatch.setattr(health, "status_all", lambda: [{"expired": True}])
    with app.test_client() as client:
        client.get("/preflight")
        _join_refresh()
        response = client.get("/preflight")
    assert response.status_code == 200
    assert response.json["ready"] is False
    auth = next(
        item for item in response.json["checks"] if item["key"] == "auth_health"
    )
    assert auth["status"] == "fail"
    assert "1 site(s)" in auth["detail"]


def test_collector_failure_finishes_as_not_ready(preflight, monkeypatch):
    from bulk_downloader import app_queue

    app, _, _, _ = preflight

    def unavailable():
        raise OSError("fixture download path unavailable")

    monkeypatch.setattr(app_queue, "_oi_default_download_dir", unavailable)
    with app.test_client() as client:
        client.get("/preflight")
        _join_refresh()
        response = client.get("/preflight")
    assert response.status_code == 200, "PREFLIGHT_COLLECT_FAILURE_ESCAPED"
    assert response.json["ready"] is False
    assert response.json["pending"] is False
    assert all(
        "could not be completed" in item["detail"] for item in response.json["checks"]
    )


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork inheritance is POSIX-only")
def test_forked_child_does_not_inherit_a_busy_refresh(preflight, monkeypatch):
    from bulk_downloader import app_queue

    app, _, _, _ = preflight
    held = threading.Lock()
    monkeypatch.setattr(app_queue, "_PREFLIGHT_LOCK", held, raising=False)
    monkeypatch.setattr(app_queue, "_PREFLIGHT_CACHE", {"running": True}, raising=False)
    read_fd, write_fd = os.pipe()
    held.acquire()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            with app.test_client() as client:
                client.get("/preflight")
                _join_refresh()
                result = client.get("/preflight")
            os.write(write_fd, b"ready" if result.json["ready"] else b"not-ready")
        finally:
            os.close(write_fd)
            os._exit(0)
    os.close(write_fd)
    try:
        readable, _, _ = select.select([read_fd], [], [], 4)
        assert readable, "PREFLIGHT_CHILD_INHERITED_BLOCKED_REFRESH"
        assert os.read(read_fd, 64) == b"ready"
    finally:
        os.close(read_fd)
        held.release()
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
