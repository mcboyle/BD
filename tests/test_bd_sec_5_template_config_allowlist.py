"""BD-SEC-5 (bughunt queue-templates-io H1; H2 moved to BD-SEC-2 r8).

H1: a shared user-template file planted any config_defaults key into a site.
    POST /api/user_templates/import stored it (preview said "new", no warning) and
    template apply copied every key: post_download_cmd (hooks.py runs it with
    shell=True after each download) and download_dir outside the allowlist.
    Now only user_templates.TEMPLATE_CONFIG_KEYS survive import, save and read;
    the preview lists the rest as "refused"; neither apply seam can set them.

RED on b0ea77a6: the preview has no "refused" list and both apply seams write
post_download_cmd/download_dir. Control: an allowlisted key (quality_preference /
min_resolution) still applies and saves.
"""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

BD_GATE_SCOPE = "module"

TID = "user_bdsec5"
PLANTED = {
    "post_download_cmd": "echo BDSEC5-PLANTED",
    "download_dir": "/tmp/bdsec5-planted",
    "on_complete_cmd": "echo BDSEC5-HOOK",
    "hooks": {"post": "echo BDSEC5-HOOK"},
}
REFUSED = sorted(PLANTED)


def _template(**extra):
    return {
        "id": TID, "name": "BDSEC5 shared", "description": "planted template",
        "patterns": ["bdsec5\\.example"],
        "learned": {"download": {"row_selectors": [".bdsec5-row a.dl"],
                                 "url_attribute": "href"}},
        "config_defaults": {**PLANTED, "quality_preference": "best"},
        **extra,
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    from bulk_downloader import user_templates as ut
    path = tmp_path / "user_templates.json"
    monkeypatch.setattr(ut, "USER_TEMPLATES_FILE", path)
    return path


def _client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, {"X-CSRF-Token": csrf}


def _assert_not_planted(cfg, seam):
    leaked = sorted(k for k in PLANTED if k in cfg)
    assert not leaked, f"BD-SEC-5-H1: {seam} planted {leaked} from a user template"
    assert cfg.get("quality_preference") == "best", f"BD-SEC-5-H1: {seam} dropped the allowlisted key"


def test_h1_import_preview_lists_refused_keys(store):
    c, h = _client()
    preview = c.post("/api/user_templates/import/preview",
                     json={"templates": [_template()]}, headers=h).get_json()
    item = preview["items"][0]
    assert item["status"] == "new"
    assert item.get("refused") == REFUSED, f"BD-SEC-5-H1: preview shows no REFUSED keys: {item}"
    assert preview["counts"].get("refused") == 1
    assert not store.exists(), "the preview wrote the store"


def test_h1_route_import_then_route_apply_never_plants(store, monkeypatch):
    from bulk_downloader import app, app_sites_teach, app_state
    c, h = _client()
    r = c.post("/api/user_templates/import", json={"templates": [_template()]}, headers=h)
    assert r.get_json()["added"] == 1
    stored = json.loads(store.read_text())["templates"][0]["config_defaults"]
    assert sorted(set(stored) & set(PLANTED)) == [], f"BD-SEC-5-H1: import stored {stored}"
    runner = Mock()
    monkeypatch.setattr(app_state, "runners", {"s": runner})
    monkeypatch.setattr(app_state, "s_cfg", {"s": {}})
    monkeypatch.setattr(app, "_save_sites_config", Mock())
    with app.app.test_request_context("/api/sites/s/templates/apply", method="POST",
                                      json={"template_id": TID}):
        rv = app_sites_teach.api_template_apply("s")
    resp = rv[0] if isinstance(rv, tuple) else rv
    assert resp.get_json()["ok"] is True, resp.get_json()
    _assert_not_planted(app_state.s_cfg["s"], "route apply")


def test_h1_template_stored_before_the_fix_is_filtered_on_read(store, monkeypatch):
    """A file already holding planted keys (imported before this fix, or
    hand-edited) must not reach a site through the bulk apply seam either."""
    from bulk_downloader import app
    store.write_text(json.dumps({"version": 1, "templates": [_template()]}))
    runner = Mock()
    monkeypatch.setattr(app, "runners", {"s": runner})
    monkeypatch.setattr(app, "s_cfg", {"s": {}})
    monkeypatch.setattr(app, "_save_sites_config", Mock())
    ok, msg = app._apply_template_by_id("s", TID)
    assert ok is True, msg
    _assert_not_planted(app.s_cfg["s"], "bulk apply")
    assert runner.update_config.call_count == 1


def test_h1_replace_import_and_save_drop_refused_keys(store):
    from bulk_downloader import user_templates as ut
    added, _skipped, errors = ut.import_user_templates({"templates": [_template()]}, merge=False)
    assert (added, errors) == (1, [])
    raw = store.read_text()
    assert "BDSEC5-PLANTED" not in raw and "bdsec5-planted" not in raw, "BD-SEC-5-H1: replace import stored the command"
    ok, saved = ut.save_user_template("BDSEC5 teach", "d", ["x"], _template()["learned"],
                                      config_defaults={**PLANTED, "min_resolution": 720})
    assert ok is True, saved
    kept = saved["config_defaults"]
    assert not set(kept) & set(PLANTED), f"BD-SEC-5-H1: save kept {kept}"
    assert kept.get("min_resolution") == 720, "control: save keeps an allowlisted key"
    # A non-object config_defaults is refused whole.
    assert ut._refused_config_keys({"config_defaults": "post_download_cmd=id"}) == ["config_defaults"]

