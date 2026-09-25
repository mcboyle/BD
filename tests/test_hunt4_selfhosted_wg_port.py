"""Self-hosted WireGuard endpoints need a usable remote UDP port."""

from bulk_downloader.vpn_providers import selfhosted

BD_GATE_SCOPE = "module"


def test_selfhosted_wireguard_rejects_out_of_range_endpoint_port():
    key = "A" * 43 + "="
    credentials = {
        "wg_private_key": key,
        "wg_address": "10.0.0.2/32",
        "wg_peer_pubkey": key,
        "wg_endpoint": "vpn.example.com:51820",
    }
    assert selfhosted.test_credentials(credentials)[0] is True

    for port in ("0", "65536", "-1"):
        credentials["wg_endpoint"] = f"vpn.example.com:{port}"
        ok, reason = selfhosted.test_credentials(credentials)
        assert ok is False, port
        assert "port" in reason

    # lens (B1): the legal range is inclusive at both ends.
    for port in ("1", "65535"):
        credentials["wg_endpoint"] = f"vpn.example.com:{port}"
        assert selfhosted.test_credentials(credentials)[0] is True, port
