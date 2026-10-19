"""deep-vpn-admin K rows: kill switch, settings, system-killswitch plan,
WebRTC result and provider routes under /api/vpn.

Same harness as tests/test_dva_vk_tunnels_api.py: bare Flask app, scratch
BD_VPN_CONFIG_PATH, in-memory secret store, fake backend, no host network,
auto-cycle patched out. Every value is a synthetic literal; none is a credential.
"""
from __future__ import annotations

import json

import pytest

from bulk_downloader import (app_vpn_api, secrets_store, vpn, vpn_config,
                             vpn_kill_switch as KS, vpn_leak_tests, vpn_providers)

BD_GATE_SCOPE = "module"


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


class _FakeBackend:
    def start(self, t):
        return True

    def stop(self, t):
        return True

    def is_running(self, t):
        return True


def _reset():
    for m in (vpn, vpn_config, KS, vpn_leak_tests):
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
    c = app.test_client()
    assert c.post("/api/vpn/tunnels", json={
        "name": "w1", "provider": "generic", "backend": "wireguard", "tunnel_id": "w1",
        "config": {"endpoint": "198.51.100.1:51820"}}).status_code == 200
    yield c, path
    _reset()


def _gs(path):
    return json.loads(path.read_text(encoding="utf-8"))["global_settings"]


def test_k3_auto_recover_requires_a_boolean(env):
    c, _ = env
    KS.set_auto_recover(True)
    r = c.put("/api/vpn/kill_switch/auto_recover", json={"enabled": "false"})
    assert r.status_code == 400 and KS.get_auto_recover() is True, (
        f"K3_NONBOOL_ENABLED_COERCED: {r.status_code} live={KS.get_auto_recover()}")


def test_k4_auto_recover_change_is_persisted(env):
    c, path = env
    KS.set_auto_recover(True)
    r = c.put("/api/vpn/kill_switch/auto_recover", json={"enabled": False})
    saved = _gs(path).get("kill_switch_auto_recover")
    assert r.status_code == 200 and KS.get_auto_recover() is False and saved is False, (
        f"K4_AUTO_RECOVER_NOT_PERSISTED: {r.status_code} live={KS.get_auto_recover()} saved={saved!r}")


def test_k5_settings_auto_recover_applies_live(env):
    c, _ = env
    KS.set_auto_recover(True)
    r = c.put("/api/vpn/settings", json={"kill_switch_auto_recover": False})
    assert r.status_code == 200 and KS.get_auto_recover() is False, (
        f"K5_SETTING_NOT_LIVE: {r.status_code} live={KS.get_auto_recover()}")


def test_k6_bad_setting_values_are_refused(env):
    c, path = env
    before = _gs(path)
    got = {repr(b): c.put("/api/vpn/settings", json=b).status_code for b in (
        {"leak_test_interval_s": "abc"}, {"leak_test_interval_s": 0},
        {"leak_test_interval_s": True}, {"max_concurrent_tunnels": -5},
        {"kill_switch_auto_recover": "false"}, {"system_killswitch_allow_ports": ["x"]})}
    assert set(got.values()) == {400} and _gs(path) == before, (
        f"K6_BAD_SETTING_SAVED: {got} saved={_gs(path)!r}")


def test_k6_control_spa_settings_body_is_accepted(env):
    c, path = env
    r = c.put("/api/vpn/settings", json={"leak_test_interval_s": 1800 * 2,
                                          "system_killswitch_default": True})
    assert r.status_code == 200
    assert _gs(path)["leak_test_interval_s"] == 3600


def test_k7_nonobject_settings_bodies_are_refused(env):
    c, _ = env
    got = (c.put("/api/vpn/settings", json=[]).status_code,
           c.put("/api/vpn/settings", data="null", content_type="application/json").status_code,
           c.put("/api/vpn/kill_switch/auto_recover", json=[]).status_code)
    assert got == (400, 400, 400), f"K7_NONOBJECT_BODY_ACCEPTED: {got}"


def test_k8_ipv6_endpoint_is_refused_not_truncated(env):
    c, _ = env
    got = {ep: c.post("/api/vpn/system_killswitch/w1/plan", json={"vpn_endpoint": ep})
           for ep in ("2001:db8::1:51820", "[2001:db8::1]:51820")}
    codes = {ep: r.status_code for ep, r in got.items()}
    bodies = " ".join(r.get_data(as_text=True) for r in got.values())
    assert set(codes.values()) == {400} and "remoteip=2001 " not in bodies, (
        f"K8_IPV6_TRUNCATED_OR_500: {codes}")
    ok = c.post("/api/vpn/system_killswitch/w1/plan", json={"vpn_endpoint": "198.51.100.7:51820"})
    assert ok.status_code == 200 and "remoteip=198.51.100.7" in ok.get_data(as_text=True)


def test_k9_invalid_plan_input_is_400(env):
    c, _ = env
    got = (c.post("/api/vpn/system_killswitch/w1/plan", json={"vpn_endpoint": "bad host"}).status_code,
           c.post("/api/vpn/system_killswitch/w1/plan",
                  json={"vpn_endpoint": "198.51.100.7:51820", "allow_ports": ["x"]}).status_code,
           c.post("/api/vpn/system_killswitch/w1/apply", json={"vpn_endpoint": "bad host"}).status_code)
    assert got == (400, 400, 400), f"K9_PLAN_VALUEERROR_500: {got}"


def test_k11_webrtc_result_for_unknown_tunnel_is_404(env):
    c, _ = env
    codes = {c.post(f"/api/vpn/tunnels/ghost{i}/webrtc_result",
                    json={"ips": ["203.0.113.5"]}).status_code for i in range(50)}
    ghosts = [k for k in vpn_leak_tests._external_results if k.startswith("ghost")]
    assert codes == {404} and ghosts == [], (
        f"K11_UNKNOWN_ID_RECORDED: codes={codes} stored={len(ghosts)}")
    assert c.post("/api/vpn/tunnels/w1/webrtc_result",
                  json={"ips": ["203.0.113.5"]}).status_code == 200
    assert "w1" in vpn_leak_tests._external_results


def test_k12_provider_errors_do_not_echo_the_submission(env, monkeypatch):
    c, _ = env

    class _Prov:
        def test_credentials(self, creds):
            raise ValueError(f"bad login for {creds.get('password')}")

        def list_locations(self, creds):
            raise ValueError(f"loc fail {creds.get('password')}")
    monkeypatch.setattr(vpn_providers, "get_provider", lambda pid: _Prov())
    body = {"username": "u", "password": "dvavk-SYNTH-PW"}
    rs = [c.post("/api/vpn/providers/mullvad/test_credentials", json=body),
          c.post("/api/vpn/providers/mullvad/locations", json=body)]
    leaked = [r.status_code for r in rs if "dvavk-SYNTH-PW" in r.get_data(as_text=True)]
    assert leaked == [] and {r.status_code for r in rs} == {502}, (
        f"K12_PASSWORD_ECHOED: statuses={[r.status_code for r in rs]} leaked={leaked}")
