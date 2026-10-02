"""dl95-app-1 delta over c6c22093e: /api/sites/v2 must not wait on the honeypot advisory DB read."""
import threading
import time
from contextlib import contextmanager
from types import MethodType

import pytest
from flask import Flask

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe


@pytest.fixture
def endpoints(monkeypatch):
    from bulk_downloader import app_health as health, app_sites_id_core as sites
    from bulk_downloader.runner import SiteRunner
    from bulk_downloader import db
    import bulk_downloader.app  # noqa: F401  (lazy first-call import is not endpoint latency)
    db.db_init()
    runners = {}
    for i in range(6):
        r = SiteRunner.__new__(SiteRunner)
        r._lock = threading.RLock()
        r.jobs = {"a": {"status": "running"}, "b": {"status": "pending"},
                  "c": {"status": "done"}}
        r._state = "running"
        r._event_log = []
        r.cookies = []

        def full_status(self, light=False):
            with self._lock:
                return {"counts": {"pending": 1, "running": 1, "done": 1}, "active": 1}

        r.get_status = MethodType(full_status, r)
        runners[str(i)] = r
    for module in (health, sites):
        monkeypatch.setattr(module, "_app_runners", lambda: runners)
        monkeypatch.setattr(module, "_app_s_cfg", lambda: {})
        monkeypatch.setattr(module, "_runners_generation", lambda mapping: list(mapping.items()))
    monkeypatch.setattr(health, "_app__app_boot_time", lambda: time.time())
    monkeypatch.setattr(health, "app_test_mode", lambda: False)
    monkeypatch.setattr(health, "build_identity", lambda _root: {})
    for name in ("_attach_credential_health", "_attach_download_hold",
                 "_attach_sites_config_health", "_attach_cloak_capability", "_attach_mod3_health"):
        monkeypatch.setattr(health, name, lambda *a: None)
    monkeypatch.setattr(sites, "_m2_auth_state", lambda *a: "unknown")
    monkeypatch.setattr(sites, "_m2_honeypot_suggestion", lambda _sid: (None, 0))
    app = Flask(__name__)

    def invoke(which):
        with app.test_request_context():
            result = health.api_health() if which == "health" else sites.api_sites_v2()
            response = app.make_response(result)
            return response.status_code, response.get_json()

    return runners, invoke, sites


@contextmanager
def _held_jobs(runners):
    held, release = threading.Event(), threading.Event()

    def holder():
        for r in runners.values():
            r._lock.acquire()
        held.set()
        try:
            assert release.wait(5), "fixture jobs release missing"
        finally:
            for r in reversed(list(runners.values())):
                r._lock.release()

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(1), "fixture did not acquire jobs locks"
    try:
        yield
    finally:
        release.set()
        thread.join(2)
        assert not thread.is_alive()


def _bounded_request(invoke, which, threads):
    done, result = threading.Event(), []

    def request():
        try:
            result.append(invoke(which))
        finally:
            done.set()

    thread = threading.Thread(target=request)
    threads.append(thread)
    thread.start()
    assert done.wait(0.5), f"APP1_POLL_BLOCKED: {which}, six occupied jobs locks/probe"
    thread.join(1)
    return result[0]


def test_sites_poll_lock_free_control(endpoints):
    """Control (passes on main already): held job locks do not block the poll."""
    runners, invoke, _sites = endpoints
    threads = []
    try:
        with _held_jobs(runners):
            code, body = _bounded_request(invoke, "sites", threads)
            assert code == 200 and len(body["sites"]) == 6
    finally:
        for thread in threads:
            thread.join(2)


def test_sites_poll_does_not_wait_for_honeypot_database(endpoints, monkeypatch):
    _runners, invoke, sites = endpoints
    release, entered = threading.Event(), threading.Event()
    calls = []

    def slow_suggestion(sid):
        calls.append(sid)
        entered.set()
        assert release.wait(5), "fixture advisory release missing"
        return 0.7, 20

    monkeypatch.setattr(sites, "_m2_honeypot_suggestion", slow_suggestion)
    threads = []
    try:
        code, body = _bounded_request(invoke, "sites", threads)
        assert code == 200 and len(body["sites"]) == 6
        assert entered.wait(1), "APP1_NO_ADVISORY_REFRESH_CONTROL"
        assert 1 <= len(calls) <= 2, "APP1_UNBOUNDED_REFRESH_THREADS"
        assert all(r["honeypot_threshold_suggested"] is None
                   and r["honeypot_threshold_samples"] == 0 for r in body["sites"]), \
            "APP1_UNKNOWN_ADVISORY_SHAPE: pending refresh must read (None, 0) like the helper's fail-soft"
    finally:
        release.set()
        for thread in threads:
            thread.join(2)
