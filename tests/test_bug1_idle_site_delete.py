"""BUG-1 -- DELETE /api/sites/<sid> is a no-op for idle (never-started) sites.

api_delete nests the entire teardown (config removal + _save_sites_config) inside
`if sid in runners:`. A site created-but-never-started has no runner, so nothing
is removed -- yet the handler still returns {"ok": True}, so the UI shows a
successful delete while the site persists.

Fix: config/meta removal (and queue cleanup + save) must be UNCONDITIONAL; only
the runner teardown is conditional. A truly-absent id returns 404.

Direct-call test (no CSRF/before_request) via app_state + a request context.
"""
import importlib
import threading

import pytest

pytestmark = pytest.mark.bd_module_wipe

st = None
app = None
site_core = None
api_delete = None
api_sites_v2_bulk = None


@pytest.fixture(autouse=True)
def _bug1_subjects(isolated_bd_home):
    """Import the app only after canonical cwd/env/module isolation."""
    global st, app, site_core, api_delete, api_sites_v2_bulk
    st = importlib.import_module("bulk_downloader.app_state")
    app = importlib.import_module("bulk_downloader.app").app
    site_core = importlib.import_module(
        "bulk_downloader.app_sites_id_core")
    api_delete = site_core.api_delete
    api_sites_v2_bulk = site_core.api_sites_v2_bulk
    yield
    st = app = site_core = api_delete = api_sites_v2_bulk = None


def _status(resp):
    return resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", 200)


def test_delete_idle_site_removes_config():
    sid = "idle_probe_bug1"
    st.s_cfg[sid] = {"url": "http://example.com", "name": "idle"}
    st.s_meta[sid] = {"status": "idle"}
    st.runners.pop(sid, None)          # idle: no runner
    with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
        api_delete(sid)
    assert sid not in st.s_cfg, "BUG-1: idle site was NOT removed from s_cfg"
    assert sid not in st.s_meta, "BUG-1: idle site was NOT removed from s_meta"


def test_delete_idle_site_reaps_keeper_and_account_pool(monkeypatch):
    """Runner-less does not mean the site's independent workers are absent."""
    from bulk_downloader import account_pool, session_keeper

    sid = "idle_independent_runtime_bug1"
    stopped_keepers = []
    removed_pools = []
    monkeypatch.setattr(
        session_keeper,
        "stop_site_keepers",
        lambda site_id, timeout: (
            stopped_keepers.append((site_id, timeout)) or True),
    )
    monkeypatch.setattr(account_pool, "remove_pool", removed_pools.append)

    st.s_cfg[sid] = {"url": "http://example.com", "name": "idle"}
    st.s_meta[sid] = {"status": "idle"}
    st.runners.pop(sid, None)
    with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
        api_delete(sid)

    assert stopped_keepers == [
        (sid, site_core._SITE_DELETE_KEEPER_TIMEOUT_S)]
    assert removed_pools == [sid]


def test_delete_absent_site_returns_404():
    sid = "totally_absent_bug1"
    st.s_cfg.pop(sid, None); st.s_meta.pop(sid, None); st.runners.pop(sid, None)
    with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
        resp = api_delete(sid)
    assert _status(resp) == 404, f"absent site should 404, got {_status(resp)}"


def test_delete_running_site_still_works():
    # a site WITH a runner must still be fully torn down (no regression)
    sid = "running_probe_bug1"

    class _FakeRunner:
        def retire_scheduler(self, timeout=12.0): return True
        def retire_auto_retry(self, timeout=2.0): return True
        def retire_workers(self, timeout=5.0): self.stop(); return True
        def stop(self): pass
        def _stop_auto_retry(self): pass
    st.s_cfg[sid] = {"url": "http://example.com", "name": "run"}
    st.s_meta[sid] = {"status": "ready"}
    st.runners[sid] = _FakeRunner()
    with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
        api_delete(sid)
    assert sid not in st.s_cfg and sid not in st.runners, "running site teardown regressed"


def test_bulk_delete_idle_site_matches_single_delete_semantics(monkeypatch):
    """Bulk delete must not redefine a configured runner-less site as unknown."""
    from bulk_downloader import account_pool, cookie_health, db, session_keeper

    sid = "idle_bulk_probe_bug1"
    deleted_queue = []
    forgotten_health = []
    stopped_keepers = []
    removed_pools = []
    saved = []
    monkeypatch.setattr(site_core, "_check_csrf", lambda: None)
    monkeypatch.setattr(site_core, "_rate_check", lambda _key: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: saved.append(True))
    monkeypatch.setattr(db, "queue_delete_site", deleted_queue.append)
    monkeypatch.setattr(cookie_health, "forget_site", forgotten_health.append)
    monkeypatch.setattr(
        session_keeper,
        "stop_site_keepers",
        lambda site_id, timeout: (
            stopped_keepers.append((site_id, timeout)) or True),
    )
    monkeypatch.setattr(account_pool, "remove_pool", removed_pools.append)

    st.s_cfg[sid] = {"url": "http://example.com", "name": "idle bulk"}
    st.s_meta[sid] = {"status": "idle"}
    st.runners.pop(sid, None)
    with app.test_request_context(
        "/api/sites/v2/bulk",
        method="POST",
        json={"action": "delete", "site_ids": [sid]},
    ):
        response = api_sites_v2_bulk()

    payload = response.get_json()
    assert payload["applied_to"] == 1, payload
    assert payload["errors"] == [], payload
    assert sid not in st.s_cfg and sid not in st.s_meta
    assert deleted_queue == [sid]
    assert forgotten_health == [sid]
    assert stopped_keepers == [
        (sid, site_core._SITE_DELETE_KEEPER_TIMEOUT_S)]
    assert removed_pools == [sid]
    assert saved == [True]


class _StillLiveWatch:
    """Thread-shaped probe for a watcher that misses the bounded join."""

    def __init__(self):
        self.join_timeouts = []

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)

    def is_alive(self):
        return True


class _TrackedRunner:
    def __init__(self):
        self.stop_calls = []

    def stop(self):
        self.stop_calls.append("stop")

    def _stop_auto_retry(self):
        self.stop_calls.append("auto_retry")


def test_delete_refuses_to_remove_state_while_watch_writer_is_live(monkeypatch):
    """A timed-out watcher may still write; retain its site until it exits."""
    from bulk_downloader import account_pool, db, session_keeper

    sid = "live_watch_delete_fence_bug1"
    runner = _TrackedRunner()
    watch = _StillLiveWatch()
    stop = threading.Event()
    queue_deletes = []
    removed_pools = []
    saved = []
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: saved.append(True))
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(session_keeper, "get_status", lambda: [])
    monkeypatch.setattr(account_pool, "remove_pool", removed_pools.append)

    st.s_cfg[sid] = {"url": "http://example.com", "name": "live watch"}
    st.s_meta[sid] = {"status": "ready"}
    st.runners[sid] = runner
    st._watch_threads[sid] = watch
    st._watch_stops[sid] = stop
    try:
        with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            response = api_delete(sid)

        assert _status(response) == 503
        assert st.s_cfg.get(sid, {}).get("name") == "live watch"
        assert st.s_meta.get(sid, {}).get("status") == "ready"
        assert st.runners.get(sid) is runner
        assert st._watch_threads.get(sid) is watch
        assert st._watch_stops.get(sid) is stop
        assert stop.is_set()
        assert watch.join_timeouts == [2.0]
        assert runner.stop_calls == []
        assert queue_deletes == []
        assert removed_pools == []
        assert saved == []
    finally:
        st.s_cfg.pop(sid, None)
        st.s_meta.pop(sid, None)
        st.runners.pop(sid, None)
        st._watch_threads.pop(sid, None)
        st._watch_stops.pop(sid, None)


def test_bulk_delete_reports_error_while_watch_writer_is_live(monkeypatch):
    """Bulk delete applies the same fail-closed watcher fence per site."""
    from bulk_downloader import account_pool, db, session_keeper

    sid = "live_watch_bulk_delete_fence_bug1"
    runner = _TrackedRunner()
    watch = _StillLiveWatch()
    stop = threading.Event()
    queue_deletes = []
    removed_pools = []
    saved = []
    monkeypatch.setattr(site_core, "_check_csrf", lambda: None)
    monkeypatch.setattr(site_core, "_rate_check", lambda _key: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: saved.append(True))
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(session_keeper, "get_status", lambda: [])
    monkeypatch.setattr(account_pool, "remove_pool", removed_pools.append)

    st.s_cfg[sid] = {"url": "http://example.com", "name": "live bulk watch"}
    st.s_meta[sid] = {"status": "ready"}
    st.runners[sid] = runner
    st._watch_threads[sid] = watch
    st._watch_stops[sid] = stop
    try:
        with app.test_request_context(
            "/api/sites/v2/bulk",
            method="POST",
            json={"action": "delete", "site_ids": [sid]},
        ):
            response = api_sites_v2_bulk()

        payload = response.get_json()
        assert payload["applied_to"] == 0, payload
        assert payload["errors"] == [{
            "site_id": sid,
            "error": "watch worker did not stop",
        }]
        assert st.s_cfg.get(sid, {}).get("name") == "live bulk watch"
        assert st.s_meta.get(sid, {}).get("status") == "ready"
        assert st.runners.get(sid) is runner
        assert st._watch_threads.get(sid) is watch
        assert st._watch_stops.get(sid) is stop
        assert stop.is_set()
        assert watch.join_timeouts == [2.0]
        assert runner.stop_calls == []
        assert queue_deletes == []
        assert removed_pools == []
        assert saved == []
    finally:
        st.s_cfg.pop(sid, None)
        st.s_meta.pop(sid, None)
        st.runners.pop(sid, None)
        st._watch_threads.pop(sid, None)
        st._watch_stops.pop(sid, None)


def test_concurrent_delete_cannot_pass_detached_live_watch_generation(monkeypatch):
    """A second delete must not clear state while the first waits to join."""
    from bulk_downloader import account_pool, db, session_keeper

    sid = "concurrent_live_watch_delete_fence_bug1"
    runner = _TrackedRunner()
    join_entered = threading.Event()
    release_join = threading.Event()
    stop = threading.Event()
    queue_deletes = []
    results = []

    class _BlockingLiveWatch(_StillLiveWatch):
        def join(self, timeout=None):
            self.join_timeouts.append(timeout)
            join_entered.set()
            release_join.wait(2)

    watch = _BlockingLiveWatch()
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: None)
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(session_keeper, "get_status", lambda: [])
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)

    st.s_cfg[sid] = {"url": "http://example.com", "name": "serialized delete"}
    st.s_meta[sid] = {"status": "ready"}
    st.runners[sid] = runner
    st._watch_threads[sid] = watch
    st._watch_stops[sid] = stop

    def delete_site():
        with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            results.append(_status(api_delete(sid)))

    first = threading.Thread(target=delete_site)
    second = threading.Thread(target=delete_site)
    try:
        first.start()
        assert join_entered.wait(2)
        second.start()
        # The second caller must be behind the same site transaction, not
        # interpreting the first caller's detached registries as quiescence.
        second.join(timeout=0.25)
        assert second.is_alive(), "second delete bypassed the in-flight join"
        assert sid in st.s_cfg and queue_deletes == []

        release_join.set()
        first.join(timeout=2)
        second.join(timeout=2)
        assert not first.is_alive() and not second.is_alive()
        assert sorted(results) == [503, 503]
        assert sid in st.s_cfg and sid in st.s_meta
        assert st.runners.get(sid) is runner
        assert st._watch_threads.get(sid) is watch
        assert st._watch_stops.get(sid) is stop
        assert queue_deletes == []
    finally:
        release_join.set()
        first.join(timeout=2)
        second.join(timeout=2)
        st.s_cfg.pop(sid, None)
        st.s_meta.pop(sid, None)
        st.runners.pop(sid, None)
        st._watch_threads.pop(sid, None)
        st._watch_stops.pop(sid, None)


def test_delete_does_not_republish_watch_after_its_finalizer_ran(monkeypatch):
    """Liveness proof and restore must be one registry-locked transaction."""
    from bulk_downloader import account_pool, db, session_keeper

    sid = "watch_dies_before_restore_bug1"
    runner = _TrackedRunner()
    stop = threading.Event()
    allow_cleanup = threading.Event()
    cleanup_done = threading.Event()

    class _StaleLivenessWatch:
        def join(self, timeout=None):
            pass

        def is_alive(self):
            # Model Thread.is_alive() observed just before the target's
            # identity-finally runs.  With the registry lock held, cleanup
            # cannot pass until the generation has been republished.
            allow_cleanup.set()
            cleanup_done.wait(0.5)
            return True

    watch = _StaleLivenessWatch()

    def target_finalizer():
        assert allow_cleanup.wait(2)
        with st._watch_registry_lock:
            if st._watch_threads.get(sid) is watch:
                st._watch_threads.pop(sid, None)
            if st._watch_stops.get(sid) is stop:
                st._watch_stops.pop(sid, None)
        cleanup_done.set()

    monkeypatch.setattr(site_core, "_save_sites_config", lambda: None)
    monkeypatch.setattr(db, "queue_delete_site", lambda _sid: None)
    monkeypatch.setattr(session_keeper, "get_status", lambda: [])
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    st.s_cfg[sid] = {"name": "finalizer race"}
    st.s_meta[sid] = {"status": "ready"}
    st.runners[sid] = runner
    st._watch_threads[sid] = watch
    st._watch_stops[sid] = stop
    finalizer = threading.Thread(target=target_finalizer)
    try:
        finalizer.start()
        with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
            response = api_delete(sid)
        assert _status(response) == 503
        assert cleanup_done.wait(2)
        finalizer.join(timeout=2)
        assert not finalizer.is_alive()
        assert sid not in st._watch_threads
        assert sid not in st._watch_stops
        assert st.runners.get(sid) is runner
        assert sid in st.s_cfg and sid in st.s_meta
    finally:
        allow_cleanup.set()
        finalizer.join(timeout=2)
        st.s_cfg.pop(sid, None)
        st.s_meta.pop(sid, None)
        st.runners.pop(sid, None)
        st._watch_threads.pop(sid, None)
        st._watch_stops.pop(sid, None)


@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
def test_delete_refuses_while_real_session_keeper_is_live(monkeypatch, bulk):
    """Config/queue cannot disappear beneath an in-flight keeper callback."""
    from bulk_downloader import account_pool, db, session_keeper

    sid = f"live_session_keeper_delete_bug1_{bulk}"
    entered = threading.Event()
    release = threading.Event()
    queue_deletes = []

    def blocked_keeper(_keeper):
        entered.set()
        release.wait(2)

    monkeypatch.setattr(session_keeper.SessionKeeper, "_run", blocked_keeper)
    monkeypatch.setattr(
        site_core, "_SITE_DELETE_KEEPER_TIMEOUT_S", 0.05, raising=False)
    monkeypatch.setattr(site_core, "_check_csrf", lambda: None)
    monkeypatch.setattr(site_core, "_rate_check", lambda _key: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: None)
    monkeypatch.setattr(db, "queue_delete_site", queue_deletes.append)
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)

    st.s_cfg[sid] = {"name": "live keeper"}
    st.s_meta[sid] = {"status": "ready"}
    st.runners.pop(sid, None)
    keeper = session_keeper.start_keeper(sid, 0, {}, lambda *_: (True, ""))
    assert entered.wait(2)
    try:
        if bulk:
            with app.test_request_context(
                "/api/sites/v2/bulk",
                method="POST",
                json={"action": "delete", "site_ids": [sid]},
            ):
                response = api_sites_v2_bulk()
            payload = response.get_json()
            assert payload["applied_to"] == 0, payload
            assert payload["errors"] == [{
                "site_id": sid,
                "error": "session keeper did not stop",
            }]
        else:
            with app.test_request_context(f"/api/sites/{sid}", method="DELETE"):
                response = api_delete(sid)
            assert _status(response) == 503

        assert keeper._thread.is_alive()
        assert session_keeper._keepers.get((sid, 0)) is keeper
        assert sid in st.s_cfg and sid in st.s_meta
        assert queue_deletes == []
    finally:
        release.set()
        session_keeper.stop_all(timeout=2)
        st.s_cfg.pop(sid, None)
        st.s_meta.pop(sid, None)
        st.runners.pop(sid, None)


if __name__ == "__main__":
    import traceback
    for n in [k for k in sorted(dict(globals())) if k.startswith("test_")]:
        try:
            globals()[n](); print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception:
            print(f"ERROR {n}"); traceback.print_exc()
