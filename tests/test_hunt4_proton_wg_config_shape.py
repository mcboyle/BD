"""Malformed Proton WireGuard entries fail validation without a runtime crash."""

import pytest

from bulk_downloader.vpn_providers import proton

BD_GATE_SCOPE = "module"


def test_proton_wireguard_entry_must_be_object():
    location = proton._LOCATIONS[0]["id"]
    valid = {
        "private_key": "private-example",
        "address": "10.0.0.2/32",
        "peer_public_key": "public-example",
        "endpoint": "vpn.example:51820",
    }
    credentials = {"device_configs": {location: valid}}
    assert proton.test_credentials(credentials)[0] is True
    config = proton.render_config("wireguard", location, credentials, 1080)
    assert config["endpoint"] == "vpn.example:51820"

    assert proton.test_credentials({"device_configs": {location: True}})[0] is False
    assert proton.test_credentials({"device_configs": {location: {"endpoint": "vpn.example:51820"}}})[0] is False

    # lens (B1): "import at least one WireGuard config" -- one complete entry
    # beside a broken one still counts (any, not all).
    other = proton._LOCATIONS[1]["id"]
    assert proton.test_credentials({"device_configs": {location: valid, other: True}})[0] is True

    with pytest.raises(TypeError, match="WireGuard config.*object"):
        proton.render_config(
            "wireguard", location, {"device_configs": {location: True}}, 1080
        )
