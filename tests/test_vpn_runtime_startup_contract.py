"""Regression coverage for VPN startup and deployed site config shapes."""

from pathlib import Path


def test_vpn_runtime_reads_canonical_flat_site_mapping(monkeypatch):
    from bulk_downloader import vpn_runtime

    monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    vpn_runtime._reset_for_tests()
    try:
        result = vpn_runtime.init({
            "site-a": {
                "vpn_enabled": True,
                "vpn_tunnel_id": "tun-a",
                "vpn_required": True,
            },
            "site-b": {"vpn_enabled": False, "vpn_tunnel_id": "tun-b"},
        }, start_monitors=False)

        assert result["site_to_tunnel"] == {"site-a": "tun-a"}
        assert vpn_runtime.is_vpn_required_for_site("site-a") is True
    finally:
        vpn_runtime._reset_for_tests()


def test_app_startup_calls_vpn_runtime_init():
    source = (Path(__file__).resolve().parents[1]
              / "bulk_downloader" / "app.py").read_text(encoding="utf-8")
    assert "vpn_runtime.init(" in source


def test_vpn_runtime_applies_persisted_auto_recover(monkeypatch):
    from bulk_downloader import vpn_config, vpn_kill_switch, vpn_runtime

    applied = []
    monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    monkeypatch.setattr(vpn_config, "load", lambda: {})
    monkeypatch.setattr(vpn_config, "register_loaded_tunnels", lambda: (0, []))
    monkeypatch.setattr(
        vpn_config,
        "get_global_settings",
        lambda: {"kill_switch_auto_recover": False,
                 "leak_test_interval_s": 1800},
    )
    monkeypatch.setattr(
        vpn_kill_switch, "set_auto_recover", lambda value: applied.append(value))
    vpn_runtime._reset_for_tests()
    try:
        vpn_runtime.init({}, start_monitors=False)
        assert applied == [False]
    finally:
        vpn_runtime._reset_for_tests()
