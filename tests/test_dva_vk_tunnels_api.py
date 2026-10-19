"""deep-vpn-admin V rows: tunnel create/update/delete over /api/vpn/tunnels.

Each test reproduces one FINDINGS row (bughunt/deep-vpn-admin/FINDINGS.md) on a
bare Flask app with the VPN routes, a scratch BD_VPN_CONFIG_PATH, an in-memory
secret store and a fake tunnel backend: no wireguard/openvpn, no host network.
Every value here is a synthetic literal; none is a credential.
"""
from __future__ import annotations

import json
import os

import pytest

from bulk_downloader import (app_vpn_api, secrets_store, vpn, vpn_config,
                             vpn_kill_switch as KS)

BD_GATE_SCOPE = "module"

_PK = "dvavk-SYNTH-PRIVATE-KEY"


class _Mem:
    name = "mem"

    def __init__(self):
        self.vault = {}

    def set(self, k, v):
        self.vault[k] = v

    def get(self, k):
        return self.vault.get(k)

    def delete(self, k):
        return self.vault.pop(k, None) is not None

    def list_keys(self):
        return list(self.vault)

    def is_unlocked(self):
        return True


class _FakeBackend:
    stop_result = True
    start_exc = None

    def start(self, t):
        if _FakeBackend.start_exc:
            raise _FakeBackend.start_exc
        return True

    def stop(self, t):
        return _FakeBackend.stop_result

    def is_running(self, t):
        return True


def _reset():
    _FakeBackend.stop_result, _FakeBackend.start_exc = True, None
    for m in (vpn, vpn_config, KS):
        m._reset_for_tests()


@pytest.fixture
def env(monkeypatch, tmp_path):
    from flask import Flask
    path = tmp_path / "vpn" / "tunnels.json"
    monkeypatch.setenv("BD_VPN_CONFIG_PATH", str(path))
    mem = _Mem()
    monkeypatch.setattr(secrets_store, "get_backend", lambda: mem)
    monkeypatch.setattr(vpn, "_get_backend", lambda t: _FakeBackend())
    monkeypatch.setattr(KS, "_schedule_auto_cycle", lambda *a, **k: None)
    _reset()
    KS.set_auto_recover(False)
    app = Flask(__name__)
    app.testing = True
    app_vpn_api.register_routes(app)
    yield app.test_client(), path, mem
    _reset()


def _t(tid, **kw):
    body = {"name": tid, "provider": "generic", "backend": "wireguard",
            "tunnel_id": tid, "config": {"endpoint": "198.51.100.1:51820"}}
    body.update(kw)
    return body


def _disk(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _disk_ids(path):
    return [t["tunnel_id"] for t in _disk(path)["tunnels"]]


def _reload():
    vpn._reset_for_tests()
    vpn_config._reset_for_tests()
    vpn_config.load()
    vpn_config.register_loaded_tunnels()


# ── MED ─────────────────────────────────────────────────────────────────


def test_v1_corrupt_file_is_reported_and_never_overwritten(env):
    c, path, _ = env
    assert c.post("/api/vpn/tunnels", json=_t("keep1")).status_code == 200
    path.write_text("{", encoding="utf-8")
    vpn_config._reset_for_tests()
    vpn_config.load()
    errs = vpn_config.load_errors()
    r = c.post("/api/vpn/tunnels", json=_t("new1"))
    raw = path.read_text(encoding="utf-8")
    assert errs and r.status_code != 200 and raw == "{", (
        f"V1_CORRUPT_FILE_OVERWRITTEN: load_errors={errs!r} post={r.status_code} "
        f"disk={raw[:80]!r}")
    assert c.get("/api/vpn/status").get_json()["config_load_errors"]


def test_v1_control_valid_file_reload_keeps_records(env):
    c, path, _ = env
    c.post("/api/vpn/tunnels", json=_t("keep1"))
    vpn_config._reset_for_tests()
    vpn_config.load()
    assert vpn_config.load_errors() == []
    assert c.post("/api/vpn/tunnels", json=_t("new1")).status_code == 200
    assert _disk_ids(path) == ["keep1", "new1"]


def test_v2_save_failure_is_an_error_and_changes_nothing(env, monkeypatch):
    c, path, _ = env
    c.post("/api/vpn/tunnels", json=_t("a1"))
    before = path.read_bytes()

    def _refuse(*a, **k):
        raise PermissionError(13, "dvavk synthetic read-only config dir")
    monkeypatch.setattr(vpn_config.os, "replace", _refuse)
    post = c.post("/api/vpn/tunnels", json=_t("b1")).status_code
    put = c.put("/api/vpn/tunnels/a1", json={"name": "renamed"}).status_code
    st = c.put("/api/vpn/settings", json={"leak_test_interval_s": 600}).status_code
    live = (vpn.get_tunnel("b1"), vpn_config.get_tunnel_config("b1"),
            vpn.get_tunnel("a1").name, vpn_config.get_tunnel_config("a1")["name"],
            vpn_config.get_global_settings()["leak_test_interval_s"])
    assert (post, put, st) == (500, 500, 500) and live == (None, None, "a1", "a1", 1800), (
        f"V2_SAVE_FAILURE_REPORTED_OK: post/put/settings={post}/{put}/{st} live={live!r}")
    assert path.read_bytes() == before


def test_v5_disabled_tunnel_stays_visible_and_deletable_after_reload(env):
    c, path, _ = env
    assert c.post("/api/vpn/tunnels", json=_t("off1", enabled=False)).status_code == 200
    _reload()
    g = c.get("/api/vpn/tunnels/off1")
    assert g.status_code == 200 and g.get_json()["tunnel"]["enabled"] is False, (
        f"V5_DISABLED_TUNNEL_INVISIBLE: GET {g.status_code} {g.get_data(as_text=True)[:160]}")
    d = c.delete("/api/vpn/tunnels/off1")
    assert d.status_code == 200 and _disk_ids(path) == [], (
        f"V5_DISABLED_TUNNEL_INVISIBLE: DELETE {d.status_code} disk={_disk_ids(path)}")


def test_v6_repost_of_a_saved_disabled_id_is_refused(env):
    c, path, _ = env
    c.post("/api/vpn/tunnels", json=_t("off1", enabled=False))
    _reload()
    r = c.post("/api/vpn/tunnels", json=_t("off1", name="replacement"))
    names = [t["name"] for t in _disk(path)["tunnels"]]
    assert r.status_code == 409 and names == ["off1"], (
        f"V6_REPOST_OVERWRITES_SAVED: {r.status_code} disk names={names}")


def test_v8_put_of_the_redaction_mask_keeps_the_stored_key(env):
    c, path, mem = env
    c.post("/api/vpn/tunnels", json=_t("k1", config={"endpoint": "e", "private_key": _PK}))
    shown = c.get("/api/vpn/tunnels/k1").get_json()["tunnel"]["config"]["private_key"]
    r = c.put("/api/vpn/tunnels/k1", json={"name": "k1b", "config": {"private_key": shown}})
    assert r.status_code == 200 and mem.vault.get("k1:private_key") == _PK, (
        f"V8_MASK_OVERWROTE_VAULT_KEY: shown={shown!r} vault={mem.vault.get('k1:private_key')!r}")
    assert _disk(path)["tunnels"][0]["config"]["private_key"] == "@cred:k1:private_key"


def test_v8_control_a_new_key_still_replaces_the_stored_one(env):
    c, _, mem = env
    c.post("/api/vpn/tunnels", json=_t("k1", config={"endpoint": "e", "private_key": _PK}))
    assert c.put("/api/vpn/tunnels/k1", json={"config": {"private_key": "dvavk-NEW"}}).status_code == 200
    assert mem.vault["k1:private_key"] == "dvavk-NEW"


def test_v11_delete_refused_while_the_backend_cannot_stop(env):
    c, path, mem = env
    c.post("/api/vpn/tunnels", json=_t("s1", config={"endpoint": "e", "private_key": _PK}))
    assert c.post("/api/vpn/tunnels/s1/start").get_json()["started"] is True
    _FakeBackend.stop_result = False
    r = c.delete("/api/vpn/tunnels/s1")
    state = (r.status_code, _disk_ids(path), sorted(mem.vault), vpn.get_tunnel("s1") is not None)
    assert state == (409, ["s1"], ["s1:private_key"], True), (
        f"V11_DELETE_PURGED_WHILE_RUNNING: (status, disk, vault, registered)={state!r}")


def test_v11_control_idle_tunnel_deletes(env):
    c, path, mem = env
    c.post("/api/vpn/tunnels", json=_t("s1", config={"endpoint": "e", "private_key": _PK}))
    assert c.delete("/api/vpn/tunnels/s1").status_code == 200
    assert _disk_ids(path) == [] and mem.vault == {} and vpn.get_tunnel("s1") is None


def test_k1_delete_forgets_kill_state(env):
    c, _, _ = env
    c.post("/api/vpn/tunnels", json=_t("t1"))
    assert c.post("/api/vpn/kill_switch/t1/trigger", json={}).status_code == 200
    assert c.delete("/api/vpn/tunnels/t1").status_code == 200
    c.post("/api/vpn/tunnels", json=_t("t1"))
    assert not KS.is_killed("t1"), (
        f"K1_KILL_STATE_SURVIVES_DELETE: {KS.get_kill_state('t1')!r}")


# ── LOW ─────────────────────────────────────────────────────────────────


def test_v3_bad_id_is_400_and_stores_no_secret(env):
    c, _, mem = env
    r = c.post("/api/vpn/tunnels", json=_t("a:b", config={"private_key": _PK}))
    assert r.status_code == 400 and mem.vault == {}, (
        f"V3_ORPHAN_SECRET_OR_500: {r.status_code} vault={sorted(mem.vault)}")


def test_v4_extra_secret_is_not_written_in_plaintext(env):
    c, path, _ = env
    c.post("/api/vpn/tunnels", json=_t("x1", extra={"account_number": "dvavk-ACCT-123"}))
    raw = path.read_text(encoding="utf-8")
    assert "dvavk-ACCT-123" not in raw, f"V4_EXTRA_SECRET_PLAINTEXT: {raw[:300]!r}"
    assert vpn_config.credentials_resolver_for_vpn("x1")["account_number"] == "dvavk-ACCT-123"


def test_v7_unsafe_tunnel_ids_are_refused(env):
    c, _, _ = env
    got = {tid: c.post("/api/vpn/tunnels", json=_t(tid)).status_code
           for tid in ("../x", "a/b", ".", "..", "a\\b", " x")}
    assert set(got.values()) == {400}, f"V7_UNSAFE_TUNNEL_ID_ACCEPTED: {got}"
    assert c.post("/api/vpn/tunnels", json=_t("ok-id_1.x")).status_code == 200


def test_v9_bad_names_are_refused(env):
    c, path, _ = env
    c.post("/api/vpn/tunnels", json=_t("n1"))
    got = {repr(v): c.put("/api/vpn/tunnels/n1", json={"name": v}).status_code
           for v in ("", "   ", 5, None)}
    disk_name = _disk(path)["tunnels"][0]["name"]
    assert set(got.values()) == {400} and disk_name == "n1", (
        f"V9_BAD_NAME_ACCEPTED: {got} disk name={disk_name!r}")


def test_v10_unusable_put_fields_are_refused(env):
    c, path, _ = env
    c.post("/api/vpn/tunnels", json=_t("n1"))
    got = (c.put("/api/vpn/tunnels/n1", json={"config": "x"}).status_code,
           c.put("/api/vpn/tunnels/n1", json={"provider": "pia"}).status_code)
    assert got == (400, 400), f"V10_PUT_FIELD_SILENTLY_IGNORED: {got}"
    assert _disk(path)["tunnels"][0]["provider"] == "generic"


def test_v12_lifecycle_on_unknown_id_is_404(env):
    c, _, _ = env
    got = {a: c.post(f"/api/vpn/tunnels/nope/{a}").status_code for a in ("start", "stop", "cycle")}
    assert set(got.values()) == {404}, f"V12_UNKNOWN_ID_OK: {got}"


def test_v13_start_route_does_not_echo_exception_text(env, monkeypatch):
    c, _, _ = env
    c.post("/api/vpn/tunnels", json=_t("e1"))

    def _boom(tid):
        raise RuntimeError("key=dvavk-SYNTH-LEAK")
    monkeypatch.setattr(vpn, "start_tunnel", _boom)
    r = c.post("/api/vpn/tunnels/e1/start")
    body = r.get_data(as_text=True)
    assert r.status_code == 500 and "SYNTH-LEAK" not in body, (
        f"V13_EXCEPTION_TEXT_ECHOED: {r.status_code} {body[:200]}")


def test_v14_name_length_is_capped(env):
    c, _, _ = env
    r = c.post("/api/vpn/tunnels", json=_t("u2", name="N" * 10000))
    assert r.status_code == 400, f"V14_NAME_UNBOUNDED: {r.status_code}"
    assert c.post("/api/vpn/tunnels", json=_t("u1", name="Тест 🚀")).status_code == 200


def test_k7_nonobject_tunnel_bodies_are_refused(env):
    c, _, _ = env
    c.post("/api/vpn/tunnels", json=_t("n1"))
    got = (c.post("/api/vpn/tunnels", json=[]).status_code,
           c.put("/api/vpn/tunnels/n1", json=[]).status_code,
           c.put("/api/vpn/tunnels/n1", data="null", content_type="application/json").status_code)
    assert got == (400, 400, 400), f"K7_NONOBJECT_BODY_ACCEPTED: {got}"


def test_no_real_config_written(env):
    _, path, _ = env
    assert os.environ["BD_VPN_CONFIG_PATH"] == str(path)
