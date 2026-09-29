"""dl95-xvideos-3 (harness-work/DOT95-LANE/live-dl95-xvideos-1/LIVE-RESULT-A1-A.md#2, HIGH).

dl95-xvideos-1 made discovery accept a public listing, but only for sites that declare no login at all
(scene_crawler._site_is_public). xvideos, porndoe, scrolller and spankbang carry an optional account (http login_url +
username), so they stay fail-closed NOT_LOGGED_IN. The predicate already lets an explicit ``auth_required`` bool win --
but the app could not hold one: the key was not in CFG_FIELDS, so a PUT value vanished at the next _load_sites_config
(test2 restarts every 30 minutes), and nothing checked its type (discovery honours only a real bool).

Fix: auth_required is a persisted site field; PUT accepts true / false (bool or the strings) or empty (= infer) and
refuses anything else. Hermetic: fresh_app (tmp workdir, in-memory sqlite), the real PUT route and loader.
"""
from __future__ import annotations

import json

import pytest

from bulk_downloader import scene_crawler

BD_GATE_SCOPE = "module"

OPTIONAL_ACCOUNT = {"name": "tube-with-account", "login_url": "https://www.tube.test/account",
                    "username": "fixture-user"}


def _site(fresh_app):
    from bulk_downloader import app as app_module
    r = fresh_app.post("/api/sites", json=dict(OPTIONAL_ACCOUNT))
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    sid = r.get_json()["id"]
    assert app_module.s_cfg[sid]["login_url"].startswith("http"), "fixture: the optional account must be stored"
    return app_module, sid


def test_control_an_optional_account_is_fail_closed_without_the_setting(fresh_app):
    app_module, sid = _site(fresh_app)
    assert scene_crawler._site_is_public(app_module.s_cfg[sid]) is False


@pytest.mark.parametrize("value,stored", [(False, False), ("false", False), (" False ", False), (True, True),
                                          ("true", True), ("", ""), (None, "")])
def test_put_normalises_auth_required(fresh_app, value, stored):
    app_module, sid = _site(fresh_app)
    rp = fresh_app.put(f"/api/sites/{sid}", json={"auth_required": value})
    assert rp.status_code == 200, rp.get_data(as_text=True)[:300]
    assert app_module.s_cfg[sid]["auth_required"] == stored
    assert type(app_module.s_cfg[sid]["auth_required"]) is type(stored)


@pytest.mark.parametrize("value", ["no", "0", 0, 1, [], {"x": 1}])
def test_put_refuses_a_value_discovery_would_ignore(fresh_app, value):
    app_module, sid = _site(fresh_app)
    rp = fresh_app.put(f"/api/sites/{sid}", json={"auth_required": value})
    assert rp.status_code == 400, (value, rp.status_code)
    assert "auth_required" in rp.get_json()["error"]
    assert app_module.s_cfg[sid].get("auth_required", "") == ""


def test_the_setting_survives_a_restart_and_opens_discovery(fresh_app, monkeypatch, tmp_path):
    """The live failure shape: set it, restart (the loader rebuilds every site from CFG_FIELDS), scan."""
    app_module, sid = _site(fresh_app)
    rp = fresh_app.put(f"/api/sites/{sid}", json={"auth_required": False})
    assert rp.status_code == 200
    on_disk = {sid: dict(app_module.s_cfg[sid])}
    assert on_disk[sid]["auth_required"] is False, "precondition: the PUT stored it"
    cfg_file = tmp_path / "sites_config.json"
    cfg_file.write_text(json.dumps(on_disk), encoding="utf-8")
    monkeypatch.setattr(app_module, "SITES_FILE", cfg_file)
    monkeypatch.setattr(app_module, "s_cfg", {})
    app_module._load_sites_config()
    assert sid in app_module.s_cfg, "precondition: the fixture site was loaded from disk"
    assert app_module.s_cfg[sid]["auth_required"] is False, "the setting was dropped at load"
    assert scene_crawler._site_is_public(app_module.s_cfg[sid]) is True


def test_an_unset_value_still_infers_after_a_restart(fresh_app, monkeypatch, tmp_path):
    """Negative control: a site that never set it keeps the login-field inference (fail-closed) after load."""
    app_module, sid = _site(fresh_app)
    cfg_file = tmp_path / "sites_config.json"
    cfg_file.write_text(json.dumps({sid: dict(app_module.s_cfg[sid])}), encoding="utf-8")
    monkeypatch.setattr(app_module, "SITES_FILE", cfg_file)
    monkeypatch.setattr(app_module, "s_cfg", {})
    app_module._load_sites_config()
    assert app_module.s_cfg[sid].get("auth_required", "") == ""
    assert scene_crawler._site_is_public(app_module.s_cfg[sid]) is False


# ── the other reader: cookie_relogin must not read the new "unset" ("") as False ──

@pytest.mark.parametrize("value,expect_scheduled", [("", 1), (True, 1), (False, 0)])
def test_cookie_relogin_opts_out_only_on_an_explicit_false(monkeypatch, value, expect_scheduled):
    from bulk_downloader import cookie_quality, cookie_relogin
    monkeypatch.setattr(cookie_quality, "score", lambda sid, s_cfg_entry=None: {"score": 10})
    monkeypatch.setattr(cookie_relogin, "_ratelimit_ok", lambda sid, min_seconds_between=0: True)
    monkeypatch.setattr(cookie_relogin, "_record", lambda sid, score, action: 1)
    cfg = dict(OPTIONAL_ACCOUNT, auth_required=value)
    out = cookie_relogin.check_and_schedule({"s1": cfg})
    assert out.get("scheduled") == expect_scheduled, out
