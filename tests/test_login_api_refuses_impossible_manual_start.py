"""The login API must report an immediate manual-browser startup refusal.

The first-run teaching path is synchronous until the headed manual browser is
open.  A missing display is therefore a known refusal, not an asynchronous
login result that the route may truthfully describe as accepted.
"""
from __future__ import annotations

import importlib
from pathlib import Path
import threading
from unittest import mock

from flask import Flask


app_sites_auth = importlib.import_module("bulk_downloader.app_sites_auth")
app_sites_id_core = importlib.import_module(
    "bulk_downloader.app_sites_id_core")
CloakLaunchError = getattr(
    importlib.import_module("bulk_downloader.cloak"), "CloakLaunchError")
AuthMixin = getattr(
    importlib.import_module("bulk_downloader.runner_auth"), "AuthMixin")


BD_GATE_SCOPE = "repo-wide"


class _ManualHandle:
    def snapshot_cookies(self, timeout=10):
        return []


class _Runner(AuthMixin):
    def __init__(self, cookie_file: Path):
        self.site_id = "displayless"
        self.config = {
            "auto_teach_first_run": True,
            "learned": {"login": {}},
            "login_url": "https://example.invalid/login",
            "cookie_file": str(cookie_file),
            "manual_use_persistent_profile": False,
        }
        self._login_thread = None
        self._manual_login_handle = None
        self._manual_snapshot_thread = None
        self._manual_snapshot_stop = None
        self._login_status = ""
        self._run_retired = False

    def _manual_profile_dir(self):
        raise AssertionError("persistent profile must be disabled by the fixture")

    def _poll_manual_cookies(self, _handle, _stop_event):
        return None

    def _begin_auxiliary_start(self):
        return not self._run_retired

    def _end_auxiliary_start(self):
        return None

    def retire_scheduler(self, timeout):
        return True

    def retire_auto_retry(self, timeout):
        return True

    def retire_workers(self, timeout):
        self._run_retired = True
        return False


def _client(monkeypatch, runner):
    monkeypatch.setattr(app_sites_auth, "_app_runners",
                        lambda: {runner.site_id: runner})
    app = Flask(__name__)
    app.register_blueprint(app_sites_auth.sites_bp)
    return app.test_client()


def _retire_and_restore(monkeypatch, runner):
    runners = {runner.site_id: runner}
    configs = {runner.site_id: dict(runner.config)}
    metadata = {runner.site_id: {"name": "Displayless"}}
    registry_lock = threading.RLock()
    monkeypatch.setattr(app_sites_id_core, "_app_runners", lambda: runners)
    monkeypatch.setattr(app_sites_id_core, "_app_s_cfg", lambda: configs)
    monkeypatch.setattr(app_sites_id_core, "_app_s_meta", lambda: metadata)
    monkeypatch.setattr(app_sites_id_core, "_app__watch_stops", lambda: {})
    monkeypatch.setattr(app_sites_id_core, "_app__watch_threads", lambda: {})
    monkeypatch.setattr(
        app_sites_id_core, "_app__watch_registry_lock", lambda: registry_lock)
    monkeypatch.setattr(
        app_sites_id_core, "_stop_site_keeper_generations", lambda _sid: True)
    app = Flask(__name__)
    with app.test_request_context(
            f"/api/sites/{runner.site_id}", method="DELETE"):
        response = app_sites_id_core._api_delete_transaction(runner.site_id)
    return response, runners, configs, metadata


def test_login_api_refuses_a_retired_runtime_restored_after_failed_delete(
        tmp_path, monkeypatch):
    runner = _Runner(tmp_path / "cookies.json")
    delete_response, runners, configs, metadata = _retire_and_restore(
        monkeypatch, runner)

    assert delete_response[1] == 503
    assert delete_response[0].get_json()["error"] == "runner worker did not stop"
    assert runners == {runner.site_id: runner}
    assert runner.site_id in configs and runner.site_id in metadata
    assert runner._run_retired is True
    assert runner._begin_auxiliary_start() is False

    client = _client(monkeypatch, runner)
    with mock.patch("bulk_downloader.login.open_manual_login_browser") as launch:
        response = client.post(f"/api/sites/{runner.site_id}/login")

    assert launch.call_count == 0
    assert response.status_code == 503, (
        "retired runtime was restored to the registry, but the login API "
        f"reported HTTP {response.status_code} {response.get_json()!r}")
    assert response.get_json() == {
        "ok": False,
        "error": "Site runtime is being deleted",
    }


def test_login_api_refuses_when_first_run_manual_browser_cannot_launch(
        tmp_path, monkeypatch):
    cookie_file = tmp_path / "cookies.json"
    runner = _Runner(cookie_file)
    client = _client(monkeypatch, runner)
    launch_error = CloakLaunchError(
        "Headed browser launch requires a display; start Xvfb/noVNC")

    assert runner.config["auto_teach_first_run"] is True
    assert runner.config["learned"]["login"] == {}
    assert cookie_file.exists() is False
    monkeypatch.delenv("DISPLAY", raising=False)
    assert "DISPLAY" not in __import__("os").environ

    with mock.patch("bulk_downloader.session_keeper.pause_site_keepers") as pause, \
         mock.patch("bulk_downloader.login.open_manual_login_browser",
                    side_effect=launch_error) as launch:
        response = client.post(f"/api/sites/{runner.site_id}/login")

    assert pause.call_count == 1
    assert launch.call_count == 1
    assert runner._manual_login_handle is None
    assert "Headed browser launch requires a display" in runner._login_status
    assert cookie_file.exists() is False
    assert response.status_code == 503, (
        "manual browser launch was impossible, but the login API reported "
        f"HTTP {response.status_code} {response.get_json()!r}")
    assert response.get_json()["ok"] is False
    assert response.get_json()["error"] == (
        "Couldn't open browser: Headed browser launch requires a display; "
        "start Xvfb/noVNC")


def test_login_api_names_a_manual_browser_that_returns_no_handle(
        tmp_path, monkeypatch):
    runner = _Runner(tmp_path / "cookies.json")
    client = _client(monkeypatch, runner)

    with mock.patch("bulk_downloader.session_keeper.pause_site_keepers"), \
         mock.patch("bulk_downloader.login.open_manual_login_browser",
                    return_value=None) as launch:
        response = client.post(f"/api/sites/{runner.site_id}/login")

    assert launch.call_count == 1
    assert runner._manual_login_handle is None
    assert runner._login_status == "✗ Browser open returned no handle"
    assert response.status_code == 503
    assert response.get_json() == {
        "ok": False,
        "error": "Browser open returned no handle",
    }


def test_login_api_still_accepts_a_manual_browser_that_really_opened(
        tmp_path, monkeypatch):
    cookie_file = tmp_path / "cookies.json"
    runner = _Runner(cookie_file)
    client = _client(monkeypatch, runner)
    handle = _ManualHandle()

    with mock.patch("bulk_downloader.session_keeper.pause_site_keepers"), \
         mock.patch("bulk_downloader.login.open_manual_login_browser",
                    return_value=handle) as launch:
        response = client.post(f"/api/sites/{runner.site_id}/login")

    assert launch.call_count == 1
    assert runner._manual_login_handle is handle
    assert runner._manual_snapshot_thread is not None
    runner._manual_snapshot_thread.join(timeout=2)
    assert runner._manual_snapshot_thread.is_alive() is False
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert cookie_file.exists() is False


def test_transform_control_only_imports_login_api():
    """An import-only node cannot detect a truthful-response regression."""
    assert callable(app_sites_auth.api_login)
