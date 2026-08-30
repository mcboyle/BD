"""Lifecycle regressions for POST /api/config/import.

The import route is an all-site transaction: unlike the per-site blueprints it
has no ``<sid>`` argument from which the normal request hook can acquire a
stripe.  These tests pin the externally visible consequences of that missing
transaction rather than the lock implementation itself.
"""

from __future__ import annotations

import threading

from flask import Flask

BD_GATE_SCOPE = "module"


def _response(app, result):
    return app.make_response(result)


def _patch_import_state(monkeypatch, *, runners, configs, metadata, saves):
    from bulk_downloader import app_config as config_api
    from bulk_downloader import app_sites_id_core as site_core

    monkeypatch.setattr(config_api, "_app_CFG_FIELDS", lambda: ("name", "cookie_file"))
    monkeypatch.setattr(config_api, "_app_DEFAULTS", lambda: {})
    monkeypatch.setattr(config_api, "_app_runners", lambda: runners)
    monkeypatch.setattr(config_api, "_app_s_cfg", lambda: configs)
    monkeypatch.setattr(config_api, "_app_s_meta", lambda: metadata)
    monkeypatch.setattr(config_api, "_build_meta", lambda cfg: dict(cfg))
    # app_config owns the final, successful import save.  ``raising=False``
    # keeps this regression RED against the old route, which had no delegate.
    def save_import_snapshot():
        saves.append({sid: dict(cfg) for sid, cfg in configs.items()})
        return True

    monkeypatch.setattr(
        config_api,
        "_save_sites_config",
        save_import_snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        config_api,
        "_start_imported_runtime_dependencies",
        lambda _site_ids: None,
        raising=False,
    )

    watch_stops = {}
    watch_threads = {}
    watch_lock = threading.RLock()
    monkeypatch.setattr(site_core, "_app_runners", lambda: runners)
    monkeypatch.setattr(site_core, "_app_s_cfg", lambda: configs)
    monkeypatch.setattr(site_core, "_app_s_meta", lambda: metadata)
    monkeypatch.setattr(site_core, "_app__watch_stops", lambda: watch_stops)
    monkeypatch.setattr(site_core, "_app__watch_threads", lambda: watch_threads)
    monkeypatch.setattr(site_core, "_app__watch_registry_lock", lambda: watch_lock)
    monkeypatch.setattr(site_core, "_stop_site_keeper_generations", lambda _sid: True)
    monkeypatch.setattr(
        site_core,
        "_save_sites_config",
        lambda: saves.append({sid: dict(cfg) for sid, cfg in configs.items()}),
    )

    from bulk_downloader import account_pool, cookie_health, db

    monkeypatch.setattr(account_pool, "remove_pool", lambda _sid: None)
    monkeypatch.setattr(cookie_health, "forget_site", lambda _sid: None)
    monkeypatch.setattr(db, "queue_delete_site", lambda _sid: None)
    return config_api, site_core


class _Runtime:
    def __init__(self, sid="old", cfg=None, *, scheduler_quiescent=True):
        self.site_id = sid
        self.config = dict(cfg or {"name": sid, "cookie_file": ""})
        self.scheduler_quiescent = scheduler_quiescent
        self.updated = threading.Event()
        self.retired = []

    def stop(self):
        return None

    def retire_scheduler(self, timeout=0):
        self.retired.append(("scheduler", timeout))
        return self.scheduler_quiescent

    def retire_auto_retry(self, timeout=0):
        self.retired.append(("auto", timeout))
        return True

    def retire_workers(self, timeout=0):
        self.retired.append(("workers", timeout))
        return True

    def update_config(self, cfg):
        self.config = cfg
        self.updated.set()

    def set_cookies_from_file(self, _path):
        return True, "loaded"


def test_replace_fails_loudly_and_retains_site_when_a_writer_cannot_retire(monkeypatch):
    """Removing the fail-closed delete result must make this test fail."""
    app = Flask(__name__)
    old = _Runtime(scheduler_quiescent=False)
    runners = {"old": old}
    configs = {"old": {"name": "Old", "cookie_file": ""}}
    metadata = {"old": {"name": "Old", "cookie_file": ""}}
    saves = []
    config_api, _site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    monkeypatch.setattr(
        config_api, "SiteRunner", lambda sid, cfg: _Runtime(sid, cfg))

    with app.test_request_context(
        "/api/config/import?mode=replace",
        method="POST",
        json={"sites": [{"name": "Replacement"}]},
    ):
        response = _response(app, config_api.api_config_import())

    assert response.status_code == 503
    assert response.get_json()["site_id"] == "old"
    assert runners == {"old": old}
    assert configs == {"old": {"name": "Old", "cookie_file": ""}}
    assert metadata == {"old": {"name": "Old", "cookie_file": ""}}
    assert [owner for owner, _timeout in old.retired] == [
        "scheduler", "auto", "workers"
    ]
    assert all(0 <= timeout <= 2.0 for _owner, timeout in old.retired)


def test_replace_proves_every_old_site_before_deleting_any_old_state(monkeypatch):
    """One early canonical delete before a later survivor must fail this."""
    app = Flask(__name__)
    first = _Runtime("a", scheduler_quiescent=True)
    survivor = _Runtime("b", scheduler_quiescent=False)
    runners = {"a": first, "b": survivor}
    configs = {
        "a": {"name": "A", "cookie_file": ""},
        "b": {"name": "B", "cookie_file": ""},
    }
    metadata = {
        "a": {"name": "A", "cookie_file": ""},
        "b": {"name": "B", "cookie_file": ""},
    }
    saves = []
    config_api, _site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    monkeypatch.setattr(
        config_api, "SiteRunner", lambda sid, cfg: _Runtime(sid, cfg))
    deleted_queue_rows = []
    from bulk_downloader import db
    monkeypatch.setattr(
        db, "queue_delete_site", lambda sid: deleted_queue_rows.append(sid))

    with app.test_request_context(
        "/api/config/import?mode=replace",
        method="POST",
        json={"sites": [{"name": "Replacement", "cookie_file": ""}]},
    ):
        response = _response(app, config_api.api_config_import())

    assert response.status_code == 503
    assert response.get_json()["site_id"] == "b"
    assert response.get_json()["rollback_complete"] is False
    assert response.get_json()["teardown_partial"] is True
    assert set(runners) == {"a", "b"}
    assert set(configs) == {"a", "b"}
    assert set(metadata) == {"a", "b"}
    assert deleted_queue_rows == []


def test_merge_waits_for_the_existing_sites_lifecycle_transaction(monkeypatch):
    """Deleting the all-site transaction must let update_config run early."""
    app = Flask(__name__)
    old = _Runtime(cfg={"name": "Old", "cookie_file": ""})
    runners = {"old": old}
    configs = {"old": {"name": "Old", "cookie_file": ""}}
    metadata = {"old": {"name": "Old", "cookie_file": ""}}
    saves = []
    config_api, _site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    monkeypatch.setattr(
        config_api, "SiteRunner", lambda sid, cfg: _Runtime(sid, cfg))

    from bulk_downloader.app_state import site_lifecycle_lock

    stripe = site_lifecycle_lock("old")
    route_entered = threading.Event()
    original_fields = config_api._app_CFG_FIELDS

    def fields_after_entry():
        route_entered.set()
        return original_fields()

    monkeypatch.setattr(config_api, "_app_CFG_FIELDS", fields_after_entry)
    result = {}

    def invoke():
        with app.test_request_context(
            "/api/config/import?mode=merge",
            method="POST",
            json={"sites": [{"name": "Old", "cookie_file": ""}]},
        ):
            result["response"] = _response(app, config_api.api_config_import())

    stripe.acquire()
    thread = threading.Thread(target=invoke, daemon=True)
    try:
        thread.start()
        assert route_entered.wait(2), "import route never reached its transaction"
        assert not old.updated.wait(0.2), (
            "merge mutated the live runner while another request owned its stripe"
        )
    finally:
        stripe.release()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result["response"].status_code == 200
    assert old.updated.is_set()


def test_successful_merge_persists_the_complete_import_snapshot(monkeypatch):
    """Removing the final config save must leave this test with no snapshot."""
    app = Flask(__name__)
    old = _Runtime(cfg={"name": "Old", "cookie_file": ""})
    runners = {"old": old}
    configs = {"old": {"name": "Old", "cookie_file": ""}}
    metadata = {"old": {"name": "Old", "cookie_file": ""}}
    saves = []
    config_api, _site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    monkeypatch.setattr(
        config_api, "SiteRunner", lambda sid, cfg: _Runtime(sid, cfg))

    with app.test_request_context(
        "/api/config/import?mode=merge",
        method="POST",
        json={"sites": [
            {"name": "Old", "cookie_file": ""},
            {"name": "New", "cookie_file": ""},
        ]},
    ):
        response = _response(app, config_api.api_config_import())

    assert response.status_code == 200
    assert response.get_json()["updated"] == 1
    assert response.get_json()["imported"] == 1
    assert saves, "a successful import was acknowledged without persistence"
    assert sorted(cfg["name"] for cfg in saves[-1].values()) == ["New", "Old"]


def test_merge_reconfigures_and_removes_the_independent_account_pool(monkeypatch):
    """Dropping account-pool sync from the merge path must fail this test."""
    app = Flask(__name__)
    old = _Runtime(cfg={"name": "Old", "cookie_file": "", "accounts": []})
    runners = {"old": old}
    configs = {"old": {"name": "Old", "cookie_file": "", "accounts": []}}
    metadata = {"old": {"name": "Old", "cookie_file": "", "accounts": []}}
    saves = []
    config_api, _site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    monkeypatch.setattr(
        config_api,
        "_app_CFG_FIELDS",
        lambda: ("name", "cookie_file", "accounts", "account_cooldown_seconds"),
    )
    monkeypatch.setattr(
        config_api, "SiteRunner", lambda sid, cfg: _Runtime(sid, cfg))

    from bulk_downloader import account_pool
    calls = []
    monkeypatch.setattr(
        account_pool,
        "configure_pool",
        lambda sid, accounts, cooldown_seconds: calls.append(
            ("configure", sid, list(accounts), cooldown_seconds)),
    )
    monkeypatch.setattr(
        account_pool,
        "remove_pool",
        lambda sid: calls.append(("remove", sid)),
    )

    with app.test_request_context(
        "/api/config/import?mode=merge",
        method="POST",
        json={"sites": [{
            "name": "Old",
            "accounts": [{"username": "alice", "password": "secret"}],
            "account_cooldown_seconds": 17,
        }]},
    ):
        configured = _response(app, config_api.api_config_import())
    with app.test_request_context(
        "/api/config/import?mode=merge",
        method="POST",
        json={"sites": [{
            "name": "Old",
            "accounts": [],
            "account_cooldown_seconds": 17,
        }]},
    ):
        removed = _response(app, config_api.api_config_import())

    assert configured.status_code == 200
    assert removed.status_code == 200
    assert calls == [
        ("configure", "old", [{"username": "alice", "password": "secret"}], 17),
        ("remove", "old"),
    ]


def test_replace_deletes_config_only_sites_through_the_canonical_transaction(monkeypatch):
    """Iterating only runner keys must leave the configured-only site behind."""
    app = Flask(__name__)
    runners = {}
    configs = {"orphan": {"name": "Orphan", "cookie_file": ""}}
    metadata = {"orphan": {"name": "Orphan", "cookie_file": ""}}
    saves = []
    config_api, _site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    monkeypatch.setattr(
        config_api, "SiteRunner", lambda sid, cfg: _Runtime(sid, cfg))

    with app.test_request_context(
        "/api/config/import?mode=replace",
        method="POST",
        json={"sites": [{"name": "Only new", "cookie_file": ""}]},
    ):
        response = _response(app, config_api.api_config_import())

    assert response.status_code == 200
    assert "orphan" not in configs
    assert "orphan" not in metadata
    assert sorted(cfg["name"] for cfg in configs.values()) == ["Only new"]


def test_replace_canonically_reaps_a_watch_only_orphan(monkeypatch):
    """Ignoring watch registries in canonical known-state must fail this."""
    app = Flask(__name__)
    runners = {}
    configs = {}
    metadata = {}
    saves = []
    config_api, site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    monkeypatch.setattr(
        config_api, "SiteRunner", lambda sid, cfg: _Runtime(sid, cfg))

    class DeadWatch:
        def join(self, timeout=0):
            return None

        def is_alive(self):
            return False

    watch_threads = site_core._app__watch_threads()
    watch_stops = site_core._app__watch_stops()
    watch_threads["orphan-watch"] = DeadWatch()
    watch_stops["orphan-watch"] = threading.Event()

    with app.test_request_context(
        "/api/config/import?mode=replace",
        method="POST",
        json={"sites": [{"name": "Replacement", "cookie_file": ""}]},
    ):
        response = _response(app, config_api.api_config_import())

    assert response.status_code == 200
    assert "orphan-watch" not in watch_threads
    assert "orphan-watch" not in watch_stops
    assert sorted(cfg["name"] for cfg in configs.values()) == ["Replacement"]


def test_replace_accepts_watch_only_identity_reaped_by_target_finalizer(monkeypatch):
    """Calling canonical DELETE after finalizer reaping must not manufacture 404."""
    app = Flask(__name__)
    runners = {}
    configs = {}
    metadata = {}
    saves = []
    config_api, site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    monkeypatch.setattr(
        config_api, "SiteRunner", lambda sid, cfg: _Runtime(sid, cfg))
    watch_threads = site_core._app__watch_threads()
    watch_stops = site_core._app__watch_stops()

    class FinalizingWatch:
        def join(self, timeout=0):
            watch_threads.pop("finalized-watch", None)
            watch_stops.pop("finalized-watch", None)

        def is_alive(self):
            return False

    watch_threads["finalized-watch"] = FinalizingWatch()
    watch_stops["finalized-watch"] = threading.Event()

    with app.test_request_context(
        "/api/config/import?mode=replace",
        method="POST",
        json={"sites": [{"name": "Replacement", "cookie_file": ""}]},
    ):
        response = _response(app, config_api.api_config_import())

    assert response.status_code == 200
    assert watch_threads == {}
    assert watch_stops == {}
    assert sorted(cfg["name"] for cfg in configs.values()) == ["Replacement"]


def test_import_never_acknowledges_a_failed_atomic_config_replace(monkeypatch):
    """Treating the canonical writer's False verdict as success must fail."""
    app = Flask(__name__)
    runners = {}
    configs = {}
    metadata = {}
    saves = []
    config_api, _site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    monkeypatch.setattr(
        config_api, "SiteRunner", lambda sid, cfg: _Runtime(sid, cfg))
    monkeypatch.setattr(config_api, "_save_sites_config", lambda: False)

    with app.test_request_context(
        "/api/config/import?mode=merge",
        method="POST",
        json={"sites": [{"name": "Not durable", "cookie_file": ""}]},
    ):
        response = _response(app, config_api.api_config_import())

    assert response.status_code == 503
    assert response.get_json()["error"] == "site config persistence failed"


def test_retry_after_dependency_start_failure_restarts_the_existing_site(monkeypatch):
    """Starting dependencies for staged IDs only must make retry miss the site."""
    app = Flask(__name__)
    runners = {}
    configs = {}
    metadata = {}
    saves = []
    config_api, _site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    monkeypatch.setattr(
        config_api, "SiteRunner", lambda sid, cfg: _Runtime(sid, cfg))
    attempts = []

    def start_dependencies(site_ids):
        attempts.append(list(site_ids))
        if len(attempts) == 1:
            raise RuntimeError("keeper launch failed")

    monkeypatch.setattr(
        config_api, "_start_imported_runtime_dependencies", start_dependencies)
    payload = {"sites": [{"name": "Retry me", "cookie_file": ""}]}

    with app.test_request_context(
        "/api/config/import?mode=merge", method="POST", json=payload,
    ):
        first = _response(app, config_api.api_config_import())
    committed_site_id = next(iter(configs))
    with app.test_request_context(
        "/api/config/import?mode=merge", method="POST", json=payload,
    ):
        retry = _response(app, config_api.api_config_import())

    assert first.status_code == 503
    assert retry.status_code == 200
    assert attempts == [[committed_site_id], [committed_site_id]]


def test_failed_candidate_construction_retires_staged_runners_without_publication(monkeypatch):
    """Publishing each constructor immediately must leak the first candidate."""
    app = Flask(__name__)
    runners = {}
    configs = {}
    metadata = {}
    saves = []
    config_api, _site_core = _patch_import_state(
        monkeypatch,
        runners=runners,
        configs=configs,
        metadata=metadata,
        saves=saves,
    )
    instances = []

    class Candidate(_Runtime):
        def __init__(self, sid, cfg):
            super().__init__(sid, cfg)
            instances.append(self)
            if cfg["name"] == "Second":
                raise RuntimeError("constructor failed")

    monkeypatch.setattr(config_api, "SiteRunner", Candidate)

    with app.test_request_context(
        "/api/config/import?mode=merge",
        method="POST",
        json={"sites": [
            {"name": "First", "cookie_file": ""},
            {"name": "Second", "cookie_file": ""},
        ]},
    ):
        response = _response(app, config_api.api_config_import())

    assert response.status_code == 503
    assert response.get_json()["error"] == "site runtime construction failed"
    assert runners == {}
    assert configs == {}
    assert metadata == {}
    assert len(instances) == 2
    assert [name for name, _timeout in instances[0].retired] == [
        "scheduler", "auto", "workers"
    ]
    assert [name for name, _timeout in instances[1].retired] == [
        "scheduler", "auto", "workers"
    ]
