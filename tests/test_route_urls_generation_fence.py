"""Generation fences for site-agnostic URL routing."""

import threading

BD_GATE_SCOPE = "module"


def _status(response):
    if isinstance(response, tuple):
        return response[1]
    return response.status_code


def test_router_scored_on_deleted_config_cannot_enqueue_recreated_same_key(
        monkeypatch):
    """A score belongs to the exact config generation it inspected."""
    from bulk_downloader import account_pool, app as app_module, app_state
    from bulk_downloader import audit, cookie_health, db
    from bulk_downloader import app_sites_id_core as site_core

    sid = "route-generation-reuse"
    old_cfg = {
        "name": "Old generation",
        "login_url": "https://old-generation.invalid/login",
    }

    class _Runner:
        def __init__(self):
            self.loaded = []

        def load_urls(self, urls):
            self.loaded.extend(urls)
            return len(urls), 0

        def retire_scheduler(self, timeout=2.0):
            return True

        def retire_auto_retry(self, timeout=2.0):
            return True

        def retire_workers(self, timeout=2.0):
            return True

    old_runner = _Runner()
    new_runner = _Runner()
    score_entered = threading.Event()
    release_score = threading.Event()
    route_result = []

    def score_old_generation(_url, cfg_snapshot=None):
        captured = dict(cfg_snapshot or ())
        assert captured.get(sid) == {
            "url_patterns": "",
            "login_url": "https://old-generation.invalid/login",
            "success_url": "",
        }
        assert captured.get(sid) is not old_cfg
        score_entered.set()
        assert release_score.wait(2), "test never released captured score"
        return sid, 200, "old config matched"

    monkeypatch.setattr(
        app_module, "_score_url_against_sites", score_old_generation)
    monkeypatch.setattr(
        site_core, "_stop_site_keeper_generations", lambda _sid: True)
    monkeypatch.setattr(site_core, "_save_sites_config", lambda: None)
    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    monkeypatch.setattr(db, "queue_delete_site", lambda _sid: None)
    monkeypatch.setattr(cookie_health, "forget_site", lambda _sid: None)
    monkeypatch.setattr(audit, "audit_log", lambda **_kwargs: None)

    assert sid not in app_state.s_cfg and sid not in app_state.runners
    app_state.s_cfg[sid] = old_cfg
    app_state.s_meta[sid] = {"name": "Old generation"}
    app_state.runners[sid] = old_runner

    router = threading.Thread(
        target=lambda: route_result.append(app_module._route_urls_internal(
            ["https://old-generation.invalid/video/1"])),
        name="old-generation-router",
        daemon=True,
    )
    try:
        router.start()
        assert score_entered.wait(2), "router never captured old config"

        with app_module.app.test_request_context(
                f"/api/sites/{sid}", method="DELETE"):
            delete_response = site_core.api_delete(sid)
        assert _status(delete_response) == 200
        assert sid not in app_state.s_cfg and sid not in app_state.runners

        new_cfg = {
            "name": "New generation",
            "login_url": "https://new-generation.invalid/login",
        }
        with app_state.site_lifecycle_lock(sid):
            app_state.s_cfg[sid] = new_cfg
            app_state.s_meta[sid] = {"name": "New generation"}
            app_state.runners[sid] = new_runner

        release_score.set()
        router.join(timeout=2)
        assert not router.is_alive()
        assert route_result
        summary, _unrouted = route_result[0]
        assert sid not in summary
        assert old_runner.loaded == []
        assert new_runner.loaded == [], (
            "a URL scored against deleted config was enqueued into the "
            "new runner that reused its site ID")
    finally:
        release_score.set()
        router.join(timeout=2)
        with app_state.site_lifecycle_lock(sid):
            app_state.runners.pop(sid, None)
            app_state.s_meta.pop(sid, None)
            app_state.s_cfg.pop(sid, None)


def test_router_cannot_enqueue_after_scoring_config_mutated_in_place(
        monkeypatch):
    """A stable dict identity is not a stable routing-config generation."""
    from bulk_downloader import app as app_module, app_state

    sid = "route-in-place-config-update"
    cfg = {
        "name": "Mutable generation",
        "login_url": "https://old-generation.invalid/login",
    }

    class _Runner:
        def __init__(self):
            self.loaded = []

        def load_urls(self, urls):
            self.loaded.extend(urls)
            return len(urls), 0

    runner = _Runner()
    score_entered = threading.Event()
    release_score = threading.Event()
    route_result = []

    def score_old_config(_url, cfg_snapshot=None):
        captured = dict(cfg_snapshot or ())
        assert captured[sid]["login_url"] == (
            "https://old-generation.invalid/login")
        score_entered.set()
        assert release_score.wait(2), "test never released captured score"
        return sid, 200, "old config matched"

    monkeypatch.setattr(
        app_module, "_score_url_against_sites", score_old_config)

    assert sid not in app_state.s_cfg and sid not in app_state.runners
    app_state.s_cfg[sid] = cfg
    app_state.s_meta[sid] = {"name": "Mutable generation"}
    app_state.runners[sid] = runner

    router = threading.Thread(
        target=lambda: route_result.append(app_module._route_urls_internal(
            ["https://old-generation.invalid/video/1"])),
        name="in-place-config-router",
        daemon=True,
    )
    try:
        router.start()
        assert score_entered.wait(2), "router never scored old config"

        with app_state.site_lifecycle_lock(sid):
            assert app_state.s_cfg[sid] is cfg
            cfg["login_url"] = "https://new-generation.invalid/login"

        release_score.set()
        router.join(timeout=2)
        assert not router.is_alive()
        assert route_result
        summary, _unrouted = route_result[0]
        assert sid not in summary
        assert runner.loaded == [], (
            "a URL scored against the old login URL was enqueued after the "
            "same config dict changed to a new routing generation")
    finally:
        release_score.set()
        router.join(timeout=2)
        with app_state.site_lifecycle_lock(sid):
            app_state.runners.pop(sid, None)
            app_state.s_meta.pop(sid, None)
            app_state.s_cfg.pop(sid, None)
