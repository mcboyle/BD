import pytest

from bulk_downloader import vpn_runtime


def test_global_required_vpn_blocks_missing_tunnel(monkeypatch, tmp_path):
    monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    monkeypatch.setenv("BD_VPN_CONFIG_PATH", str(tmp_path / "tunnels.json"))
    vpn_runtime._reset_for_tests()
    try:
        result = vpn_runtime.init({
            "global_vpn": {"tunnel_id": "missing-global", "required": True},
            "sites": [
                {"site_id": "explicit", "vpn": {"tunnel_id": "missing-site", "required": True}},
                {"site_id": "inherited"},
                {"site_id": "optional", "vpn": {"tunnel_id": "missing-optional", "required": False}},
            ],
        }, start_monitors=False)
        assert result["ok"] is True
        with pytest.raises(vpn_runtime.VPNRequiredError):
            vpn_runtime.get_socks_url_for_site("explicit")
        assert vpn_runtime.get_tunnel_for_site("inherited") == "missing-global"
        with pytest.raises(vpn_runtime.VPNRequiredError):
            vpn_runtime.get_socks_url_for_site("inherited")
        assert vpn_runtime.get_socks_url_for_site("optional") is None
    finally:
        vpn_runtime._reset_for_tests()
    # lens (B1): the inherited flag is runtime state and must be cleared with the rest of it.
    assert vpn_runtime.is_vpn_required_for_site("inherited") is False
