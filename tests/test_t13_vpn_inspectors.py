"""T13 (Tier 3) — D-41 VPN config renderer + D-43 provider-rotation
viewer. Group E (Network/VPN). Both read-only, both reuse the
existing vpn / vpn_config infrastructure (especially the canonical
_redact_config helper — never reimplemented here).
"""
import contextlib
import os

from bulk_downloader import dev_suite as ds


@contextlib.contextmanager
def _vpn_state(tunnels):
    """Seed vpn_config._state['tunnels'] directly with the given
    list, optionally also register live Tunnel objects. Saves and
    restores the prior state. We bypass add_tunnel_config to avoid
    disk writes."""
    from bulk_downloader import vpn_config as _cfg
    from bulk_downloader import vpn as _vpn
    saved_tunnels = list(_cfg._state.get("tunnels", []))
    _cfg._state["tunnels"] = list(tunnels)
    try:
        yield
    finally:
        _cfg._state["tunnels"] = saved_tunnels
        # also reset any live state we may have created
        try:
            _vpn._reset_for_tests()
        except Exception:
            pass


# ── D-41 vpn_config_render ─────────────────────────────────────────

def test_vpn_config_render_empty(clean_workdir):
    with _vpn_state([]):
        r = ds.vpn_config_render()
    assert r["ok"] is True
    assert r["tool"] == "vpn_config_render"
    assert r["tunnels"] == []
    assert r["total_tunnels"] == 0


def test_vpn_config_render_lists_configured_tunnels(clean_workdir):
    cfgs = [
        {"tunnel_id": "mul1", "name": "Mullvad-SE",
         "provider": "mullvad", "backend": "wireguard",
         "location": "stockholm",
         "config": {"endpoint": "se1.mullvad.net:51820"}},
        {"tunnel_id": "pro1", "name": "Proton-CH",
         "provider": "proton", "backend": "openvpn",
         "location": "zurich",
         "config": {"ovpn_path": "/etc/proton/ch.ovpn"}},
    ]
    with _vpn_state(cfgs):
        r = ds.vpn_config_render()
    assert r["total_tunnels"] == 2
    ids = {t["tunnel_id"] for t in r["tunnels"]}
    assert ids == {"mul1", "pro1"}


def test_vpn_config_render_redacts_secrets(clean_workdir):
    cfgs = [{
        "tunnel_id": "wg1", "name": "WG-1",
        "provider": "generic", "backend": "wireguard",
        "config": {
            "endpoint": "vpn.example.com:51820",
            "private_key": "SUPERSECRETKEY",
            "public_key": "publickeyhere",
            "preshared_key": "PSK_VALUE",
        },
    }]
    with _vpn_state(cfgs):
        r = ds.vpn_config_render()
    assert r["total_tunnels"] == 1
    rc = r["tunnels"][0]["redacted_config"]
    # endpoint stays visible
    assert rc["endpoint"] == "vpn.example.com:51820"
    # private_key + preshared_key must be redacted
    assert rc["private_key"] == "***"
    assert rc["preshared_key"] == "***"
    # ensure the raw secret never appears anywhere in the result
    import json as _json
    blob = _json.dumps(r)
    assert "SUPERSECRETKEY" not in blob
    assert "PSK_VALUE" not in blob


def test_vpn_config_render_pairs_with_live_state(clean_workdir):
    from bulk_downloader import vpn as _vpn
    cfgs = [{
        "tunnel_id": "live1", "name": "Live-1",
        "provider": "mullvad", "backend": "wireguard",
        "config": {"endpoint": "x.y.z:51820"},
    }]
    with _vpn_state(cfgs):
        try:
            _vpn.register_tunnel(
                tunnel_id="live1", name="Live-1",
                provider="mullvad", backend="wireguard",
                config={"endpoint": "x.y.z:51820"})
        except Exception:
            pass
        r = ds.vpn_config_render()
    assert r["tunnels"][0]["registered_live"] is True
    assert r["live_tunnels"] == 1
    assert r["tunnels"][0]["live"] is not None


# ── D-43 vpn_provider_rotation_view ────────────────────────────────

def test_vpn_rotation_empty(clean_workdir):
    with _vpn_state([]):
        r = ds.vpn_provider_rotation_view()
    assert r["ok"] is True
    assert r["tool"] == "vpn_provider_rotation_view"
    assert r["providers"] == []
    assert r["provider_count"] == 0


def test_vpn_rotation_groups_by_provider(clean_workdir):
    cfgs = [
        {"tunnel_id": "m1", "name": "M1",
         "provider": "mullvad", "backend": "wireguard"},
        {"tunnel_id": "m2", "name": "M2",
         "provider": "mullvad", "backend": "wireguard"},
        {"tunnel_id": "p1", "name": "P1",
         "provider": "proton", "backend": "openvpn"},
    ]
    with _vpn_state(cfgs):
        r = ds.vpn_provider_rotation_view()
    assert r["provider_count"] == 2
    by_prov = {p["provider"]: p for p in r["providers"]}
    assert "mullvad" in by_prov and "proton" in by_prov
    assert by_prov["mullvad"]["tunnel_count"] == 2
    assert by_prov["proton"]["tunnel_count"] == 1


def test_vpn_rotation_reports_health_state(clean_workdir):
    from bulk_downloader import vpn as _vpn
    cfgs = [{
        "tunnel_id": "h1", "name": "H1",
        "provider": "mullvad", "backend": "wireguard",
        "config": {"endpoint": "x.y.z:51820"},
    }]
    with _vpn_state(cfgs):
        try:
            t = _vpn.register_tunnel(
                tunnel_id="h1", name="H1",
                provider="mullvad", backend="wireguard",
                config={"endpoint": "x.y.z:51820"})
        except Exception:
            t = None
        r = ds.vpn_provider_rotation_view()
    assert r["provider_count"] == 1
    prov = r["providers"][0]
    assert prov["tunnel_count"] == 1
    info = prov["tunnels"][0]
    assert info["tunnel_id"] == "h1"
    # state present, health bool present
    assert "state" in info
    assert "health_ok" in info
    assert isinstance(info["health_ok"], bool)


def test_vpn_rotation_picks_next_cycle_target(clean_workdir):
    # all tunnels unregistered -> next_target is most-failed (here
    # tied at 0, so it's whichever sorts first; just assert it's
    # SET, not None, when there are tunnels)
    cfgs = [
        {"tunnel_id": "a", "name": "A",
         "provider": "mullvad", "backend": "wireguard"},
        {"tunnel_id": "b", "name": "B",
         "provider": "mullvad", "backend": "wireguard"},
    ]
    with _vpn_state(cfgs):
        r = ds.vpn_provider_rotation_view()
    prov = r["providers"][0]
    assert prov["next_cycle_target"] in {"a", "b"}


# ── dev routes ─────────────────────────────────────────────────────

def test_vpn_config_route(fresh_app):
    j = fresh_app.get("/api/dev/vpn_config").get_json()
    assert j["ok"] is True
    assert j["tool"] == "vpn_config_render"


def test_vpn_rotation_route(fresh_app):
    j = fresh_app.get("/api/dev/vpn_rotation").get_json()
    assert j["ok"] is True
    assert j["tool"] == "vpn_provider_rotation_view"


def test_t13_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/vpn_config", "/api/dev/vpn_rotation"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
