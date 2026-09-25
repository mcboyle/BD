from bulk_downloader.vpn_providers import generic

BD_GATE_SCOPE = "module"

VALID_WG = """[Interface]
PrivateKey = private
Address = 10.0.0.2/32
[Peer]
PublicKey = public
Endpoint = vpn.example:51820
"""


def test_generic_credentials_reject_incomplete_wireguard_config():
    good, _ = generic.test_credentials({"configs": {"good": VALID_WG}})
    assert good

    incomplete = VALID_WG.replace("Address = 10.0.0.2/32\n", "")
    parsed = generic._parse_configs({"bad": incomplete})
    assert len(parsed) == 1 and parsed[0][1] == "wireguard"
    valid, message = generic.test_credentials({"configs": {"bad": incomplete}})
    assert not valid
    assert "address" in message

    # lens (B1): every WireGuard config in the batch is checked, not only the first
    valid, message = generic.test_credentials({"configs": {"good": VALID_WG, "bad": incomplete}})
    assert not valid
    assert "address" in message
