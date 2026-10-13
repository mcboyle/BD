"""BD-WATCH-ARCHIVE (F015 advisory A1): Settings shows the folder watcher's real default.

At BASE the watcher read ``_app_cfg.get("watch_archive", True)`` while the key was
not in the ``_app_cfg`` seed. An app_config.json without the key therefore served
no ``watch_archive`` from GET /api/global_config, and Settings rendered the switch
OFF (``!!undefined``) while the watcher archived files as if ON.

Contract (PM default): the UI shows the server's effective default; the watcher's
behaviour does not change; the default lives in one place (the ``_app_cfg`` seed,
frozen as ``_APP_CFG_DEFAULTS``), and an explicit false saves and persists.
"""

BD_GATE_SCOPE = "module"

import json
import threading

import pytest


def _client(monkeypatch, cfg, saved):
    from flask import Flask

    from bulk_downloader import app_global_config as agc

    monkeypatch.setattr(agc, "_app__app_cfg", lambda: cfg)
    monkeypatch.setattr(agc, "_save_app_config", lambda: saved.append(json.dumps(cfg)))
    app = Flask(__name__)
    app.register_blueprint(agc.global_config_bp)
    return app.test_client()


def _loaded(file_text):
    """What _load_app_config builds: the shipped seed, updated from app_config.json."""
    from bulk_downloader.app_kernel import _APP_CFG_DEFAULTS

    cfg = dict(_APP_CFG_DEFAULTS)
    cfg.update(json.loads(file_text))
    return cfg


def test_absent_watch_archive_is_served_as_on(monkeypatch):
    cfg = _loaded(json.dumps({"watch_folder": "/srv/watch"}))
    body = _client(monkeypatch, cfg, []).get("/api/global_config").get_json()
    assert body.get("watch_archive") is True, (
        "WATCH_ARCHIVE_ABSENT_NOT_SERVED: GET omits the watcher default, Settings shows OFF; "
        f"watch_archive={body.get('watch_archive')!r}"
    )


def test_defaults_endpoint_carries_the_watcher_default(monkeypatch):
    body = _client(monkeypatch, {}, []).get("/api/global_config/defaults").get_json()
    assert body.get("watch_archive") is True, body.get("watch_archive")


def test_explicit_false_saves_and_persists_across_reload(monkeypatch):
    saved = []
    cfg = _loaded("{}")
    c = _client(monkeypatch, cfg, saved)
    r = c.post("/api/global_config", json={"watch_archive": False})
    assert r.status_code == 200, r.get_json()
    assert saved, "POST did not save app_config.json"
    assert json.loads(saved[-1]).get("watch_archive") is False, saved[-1]
    # Restart: a fresh seed updated from the saved file keeps the operator's false.
    reloaded = _loaded(saved[-1])
    body = _client(monkeypatch, reloaded, []).get("/api/global_config").get_json()
    assert body.get("watch_archive") is False, body.get("watch_archive")


class _OnePass(threading.Event):
    """stop_event for one watcher pass: not set while scanning, set by the poll wait."""

    def wait(self, timeout=None):
        self.set()
        return True


def _watch_once(monkeypatch, tmp_path, archive_value=None, default=None):
    from bulk_downloader import app

    folder = tmp_path / "watch"
    folder.mkdir()
    (folder / "list.txt").write_text("https://watch.example/a\n", encoding="utf-8")
    routed = []
    monkeypatch.setattr(
        app, "_route_urls_internal", lambda urls: routed.append(list(urls)) or ({}, [])
    )
    monkeypatch.setitem(app._app_cfg, "watch_folder", str(folder))
    monkeypatch.setitem(app._app_cfg, "watch_interval_sec", 5)
    if archive_value is None:
        monkeypatch.delitem(app._app_cfg, "watch_archive", raising=False)
    else:
        monkeypatch.setitem(app._app_cfg, "watch_archive", archive_value)
    if default is not None:
        monkeypatch.setitem(app._APP_CFG_DEFAULTS, "watch_archive", default)
    app._watcher_loop(_OnePass())
    assert routed == [["https://watch.example/a"]], routed
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.txt"))


def test_watcher_absent_key_still_archives(monkeypatch, tmp_path):
    assert _watch_once(monkeypatch, tmp_path) == ["processed/list.txt"]


def test_watcher_explicit_false_renames_in_place(monkeypatch, tmp_path):
    assert _watch_once(monkeypatch, tmp_path, archive_value=False) == [
        "list.imported.txt"
    ]


@pytest.mark.parametrize(
    "default, expect", [(True, ["processed/list.txt"]), (False, ["list.imported.txt"])]
)
def test_watcher_fallback_is_the_served_default(monkeypatch, tmp_path, default, expect):
    # One source: with the key absent, the watcher follows _APP_CFG_DEFAULTS, the
    # same seed GET and /defaults serve, not a literal of its own.
    assert _watch_once(monkeypatch, tmp_path, default=default) == expect, (
        "WATCH_ARCHIVE_TWO_SOURCES: watcher fallback ignores _APP_CFG_DEFAULTS"
    )
