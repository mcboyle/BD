"""deep-vpn-admin residual rows U20 / K2 / V13 / K8 / K10 (bughunt/deep-vpn-admin/FINDINGS.md).

U20: with multi-user on, a VPN mutation (tunnel create/delete, settings, kill switch, auto_blacklist) needs the
     admin role, like the /api/auth/users* routes; multi-user off (the default) stays byte-identical; reads stay open.
K2:  kill_switch trigger/clear on an unknown id -> 404. "_probe" is a reserved probe id that keeps the existing 200
     path (ruling pm-B-H1-A-0800Z; test_v3_66_1160 drives the real kill_tunnel through it).
V13: a backend exception's text never reaches tunnel.last_error (GET shows it); the exception type does.
K8:  an IPv6 vpn_endpoint ("[v6]:port", bare v6, "v6:port") yields the whole address in remoteip=.
K10: _safe_id of ids longer than 32 chars no longer collide on their first 32; ids <= 32 are unchanged.

Bare Flask app with the VPN routes, scratch BD_VPN_CONFIG_PATH, in-memory secret store, fake tunnel backend, and the
user_accounts gate monkeypatched (no app_config.json, accounts file, netsh, wireguard or host network).
Every value here is a synthetic literal; none is a credential.
"""
from __future__ import annotations

import pytest

from bulk_downloader import (
    app_vpn,
    app_vpn_api,
    secrets_store,
    user_accounts,
    vpn,
    vpn_config,
    vpn_openvpn,
)
from bulk_downloader import vpn_kill_switch as KS
from bulk_downloader import vpn_kill_switch_system as KSS

BD_GATE_SCOPE = "module"

_LEAK = "u20-SYNTH-LEAK"


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


class _Backend:
    start_exc = None
    stop_exc = None
    health_exc = None

    def start(self, t):
        if _Backend.start_exc:
            raise _Backend.start_exc
        return True

    def stop(self, t):
        if _Backend.stop_exc:
            raise _Backend.stop_exc
        return True

    def is_running(self, t):
        if _Backend.health_exc:
            raise _Backend.health_exc
        return True


def _reset():
    _Backend.start_exc = _Backend.stop_exc = _Backend.health_exc = None
    for m in (vpn, vpn_config, KS):
        m._reset_for_tests()


@pytest.fixture
def env(monkeypatch, tmp_path):
    from flask import Flask
    path = tmp_path / "vpn" / "tunnels.json"
    monkeypatch.setenv("BD_VPN_CONFIG_PATH", str(path))
    mem = _Mem()
    monkeypatch.setattr(secrets_store, "get_backend", lambda: mem)
    monkeypatch.setattr(vpn, "_get_backend", lambda t: _Backend())
    monkeypatch.setattr(KS, "_schedule_auto_cycle", lambda *a, **k: None)
    monkeypatch.setattr(app_vpn, "_check_csrf", lambda *a, **k: None)
    from bulk_downloader import vpn_stats
    monkeypatch.setattr(vpn_stats, "auto_blacklist_check", list)
    _reset()
    KS.set_auto_recover(False)
    app = Flask(__name__)
    app.testing = True
    app_vpn_api.register_routes(app)
    app_vpn.register_routes(app)
    yield app.test_client(), path
    _reset()


def _t(tid):
    return {"name": tid, "provider": "generic", "backend": "wireguard", "tunnel_id": tid,
            "config": {"endpoint": "198.51.100.1:51820"}}


def _as(monkeypatch, multi, role):
    """Multi-user flag and the cookie's caller; role None = no signed-in user."""
    monkeypatch.setattr(user_accounts, "multi_user_enabled", lambda *a, **k: multi)
    who = None if role is None else {"username": "u20-" + role, "role": role}
    monkeypatch.setattr(user_accounts, "current_user_from_cookie", lambda *a, **k: who)


# ── U20 ──────────────────────────────────────────────────────────────────────

_MUTATIONS = [("post", "/api/vpn/tunnels", _t("m1")), ("put", "/api/vpn/settings", {}),
              ("delete", "/api/vpn/tunnels/m0", None), ("post", "/api/vpn/kill_switch/m0/trigger", {}),
              ("put", "/api/vpn/kill_switch/auto_recover", {"enabled": True}),
              ("post", "/api/vpn/auto_blacklist", None)]


@pytest.mark.parametrize("role", ["operator", None])
def test_u20_non_admin_vpn_mutation_refused_with_multi_user_on(env, monkeypatch, role):
    c, _ = env
    _as(monkeypatch, False, None)
    assert c.post("/api/vpn/tunnels", json=_t("m0")).status_code == 200
    _as(monkeypatch, True, role)
    got = {u: getattr(c, m)(u, json=b).status_code for m, u, b in _MUTATIONS}
    assert set(got.values()) == {403}, f"U20_NO_ROLE_GATE role={role}: {got}"
    r = c.post("/api/vpn/tunnels", json=_t("m2"))
    assert r.get_json() == {"ok": False, "error": "admin role required"}, r.get_data(as_text=True)
    assert vpn.get_tunnel("m1") is None and vpn.get_tunnel("m0") is not None, "U20_MUTATION_APPLIED_DESPITE_403"
    assert not KS.list_kill_states(), "U20_MUTATION_APPLIED_DESPITE_403 (kill state)"


def test_u20_admin_mutates_and_reads_stay_open(env, monkeypatch):
    c, _ = env
    _as(monkeypatch, True, "admin")
    assert c.post("/api/vpn/tunnels", json=_t("a1")).status_code == 200
    assert c.post("/api/vpn/kill_switch/a1/trigger", json={}).status_code == 200
    _as(monkeypatch, True, "operator")
    got = [c.get(u).status_code for u in ("/api/vpn/tunnels", "/api/vpn/tunnels/a1", "/api/vpn/settings",
                                         "/api/vpn/kill_switch/state", "/api/vpn/status")]
    assert got == [200] * 5, f"U20_READ_GATED: {got}"


def test_u20_multi_user_off_is_unchanged(env, monkeypatch):
    c, _ = env
    _as(monkeypatch, False, None)
    got = {u: getattr(c, m)(u, json=b).status_code for m, u, b in _MUTATIONS}
    assert 403 not in got.values(), f"U20_DEFAULT_INSTALL_GATED: {got}"
    assert vpn.get_tunnel("m1") is not None


def test_u20_gate_fails_closed(env, monkeypatch):
    c, _ = env

    def _boom(*a, **k):
        raise OSError("u20 store unreadable")
    monkeypatch.setattr(user_accounts, "multi_user_enabled", _boom)
    r = c.post("/api/vpn/tunnels", json=_t("f1"))
    assert r.status_code == 403 and vpn.get_tunnel("f1") is None, f"U20_GATE_FAILS_OPEN: {r.status_code}"


# ── K2 ───────────────────────────────────────────────────────────────────────

def test_k2_kill_switch_on_unknown_id_is_404(env):
    c, _ = env
    got = (c.post("/api/vpn/kill_switch/nope/trigger", json={}).status_code,
           c.post("/api/vpn/kill_switch/nope/clear").status_code)
    assert got == (404, 404), f"K2_UNKNOWN_ID_200: {got}"
    assert not KS.is_killed("nope") and not KS.list_kill_states(), "K2_UNKNOWN_ID_KILL_STATE_CREATED"


def test_k2_known_ids_and_reserved_probe_keep_200(env):
    c, _ = env
    c.post("/api/vpn/tunnels", json=_t("k1"))
    assert c.post("/api/vpn/kill_switch/k1/trigger", json={"reason": "r"}).status_code == 200
    assert KS.is_killed("k1")
    assert c.post("/api/vpn/kill_switch/k1/clear").status_code == 200 and not KS.is_killed("k1")
    KS.kill_tunnel("ghost", reason="stale state, tunnel gone")  # a stale kill state stays clearable
    assert c.post("/api/vpn/kill_switch/ghost/clear").status_code == 200 and not KS.is_killed("ghost")
    assert c.post("/api/vpn/kill_switch/_probe/trigger", json={}).status_code == 200, "K2_PROBE_ID_BROKEN"
    assert KS.is_killed("_probe"), "K2_PROBE_ID_NO_LONGER_REACHES_KILL"
    assert c.post("/api/vpn/kill_switch/_probe/clear").status_code == 200


# ── V13 ──────────────────────────────────────────────────────────────────────

def test_v13_backend_start_exception_text_not_in_last_error(env):
    c, _ = env
    c.post("/api/vpn/tunnels", json=_t("e1"))
    _Backend.start_exc = RuntimeError(f"key={_LEAK}")
    r = c.post("/api/vpn/tunnels/e1/start")
    g = c.get("/api/vpn/tunnels/e1")
    bodies = r.get_data(as_text=True) + g.get_data(as_text=True)
    assert _LEAK not in bodies, f"V13_LAST_ERROR_LEAKS start: {bodies[:300]}"
    assert "RuntimeError" in (g.get_json()["tunnel"]["last_error"] or ""), g.get_data(as_text=True)


def test_v13_backend_stop_exception_text_not_in_last_error(env):
    c, _ = env
    c.post("/api/vpn/tunnels", json=_t("e2"))
    assert c.post("/api/vpn/tunnels/e2/start").status_code == 200
    _Backend.stop_exc = ValueError(f"pw={_LEAK}")
    c.post("/api/vpn/tunnels/e2/stop")
    t = vpn.get_tunnel("e2")
    assert t is not None and _LEAK not in (t.last_error or ""), f"V13_LAST_ERROR_LEAKS stop: {t and t.last_error}"
    assert "ValueError" in (t.last_error or ""), t.last_error


def test_v13_backend_health_exception_text_not_in_last_error(env):
    c, _ = env
    c.post("/api/vpn/tunnels", json=_t("e3"))
    assert c.post("/api/vpn/tunnels/e3/start").status_code == 200 and vpn.get_tunnel("e3").state == "up"
    _Backend.health_exc = OSError(f"tok={_LEAK}")
    vpn._run_health_pass()
    t = vpn.get_tunnel("e3")
    assert t.failure_count == 1, f"V13_HEALTH_PROBE_DID_NOT_RUN: {t.failure_count}"  # the probe can say yes
    assert _LEAK not in (t.last_error or ""), f"V13_LAST_ERROR_LEAKS health: {t.last_error}"
    assert "OSError" in (t.last_error or ""), t.last_error


# ── K8 ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("endpoint,host", [("2001:db8::1:51820", "2001:db8::1"), ("[2001:db8::1]:51820", "2001:db8::1"),
                                           ("[2001:db8::1]", "2001:db8::1"), ("2001:db8::1", "2001:db8::1"),
                                           ("198.51.100.1:51820", "198.51.100.1"), ("vpn.example.test:443", "vpn.example.test")])
def test_k8_endpoint_host_parsed_whole(endpoint, host):
    try:
        first = KSS.plan("k8", endpoint)[0]
    except ValueError as e:
        pytest.fail(f"K8_IPV6_REFUSED {endpoint!r}: {e}")
    assert f" remoteip={host} " in first, f"K8_IPV6_TRUNCATED {endpoint!r}: {first}"


@pytest.mark.parametrize("endpoint", ["bad host", "[2001:db8::1", "2001:db8::zz:51820", ""])
def test_k8_malformed_endpoint_still_refused(endpoint):
    with pytest.raises(ValueError):
        KSS.plan("k8", endpoint)


def test_k8_ipv4_plan_unchanged():
    assert KSS.plan("k8", "198.51.100.1:51820")[0] == (
        'advfirewall firewall add rule name="BulkDL-VPN-KS-k8-vpn-out" dir=out action=allow '
        'remoteip=198.51.100.1 protocol=any')


# ── K10 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mod", [vpn_openvpn, KSS], ids=["vpn_openvpn", "vpn_kill_switch_system"])
def test_k10_long_ids_sharing_32_chars_get_distinct_safe_ids(mod):
    a, b = "t" * 32 + "-east", "t" * 32 + "-west"
    sa, sb = mod._safe_id(a), mod._safe_id(b)
    assert sa != sb, f"K10_SAFE_ID_COLLISION {mod.__name__}: {sa!r} == {sb!r}"
    assert len(sa) <= 32 and len(sb) <= 32 and sa == mod._safe_id(a), (sa, sb)


@pytest.mark.parametrize("mod", [vpn_openvpn, KSS], ids=["vpn_openvpn", "vpn_kill_switch_system"])
def test_k10_short_ids_unchanged(mod):
    got = [mod._safe_id(s) for s in ("tunnel-1", "a.b", "", "x" * 32, "é_1")]
    assert got == ["tunnel-1", "a_b", "anon", "x" * 32, "é_1"], f"K10_SHORT_ID_RENAMED {mod.__name__}: {got}"


def test_k10_long_tunnels_get_distinct_firewall_rule_names():
    ra = KSS.plan("t" * 32 + "-east", "198.51.100.1:51820")
    rb = KSS.plan("t" * 32 + "-west", "198.51.100.1:51820")
    assert not set(ra) & set(rb), "K10_SAFE_ID_COLLISION rule names shared between two tunnels"
