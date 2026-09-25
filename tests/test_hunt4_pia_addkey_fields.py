import pytest

from bulk_downloader.vpn_providers import pia
from bulk_downloader.vpn_wireguard import render_conf

BD_GATE_SCOPE = "module"


def test_pia_rejects_incomplete_addkey_reply(monkeypatch):
    monkeypatch.setattr(pia, "generate_keypair", lambda: ("private-key", "public-key"))
    monkeypatch.setattr(pia, "_pia_token", lambda user, password: "token")
    reply = {
        "status": "OK",
        "server_key": "server-key",
        "server_ip": "203.0.113.5",
        "peer_ip": "10.20.30.40",
    }
    monkeypatch.setattr(pia, "_pia_addkey", lambda host, token, key: reply.copy())

    valid = pia.render_config("wireguard", "us_atlanta", {"username": "p1234567", "password": "pw"}, 0)
    assert "PublicKey = server-key" in render_conf(valid)

    del reply["server_key"]
    with pytest.raises(ValueError, match="server_key"):
        pia.render_config("wireguard", "us_atlanta", {"username": "p1234567", "password": "pw"}, 0)

    # lens (B1): every required field is checked, and a blank value counts as missing.
    reply["server_key"] = "server-key"
    del reply["peer_ip"]
    with pytest.raises(ValueError, match="peer_ip"):
        pia.render_config("wireguard", "us_atlanta", {"username": "p1234567", "password": "pw"}, 0)
    reply["peer_ip"] = "10.20.30.40"
    reply["server_ip"] = "   "
    with pytest.raises(ValueError, match="server_ip"):
        pia.render_config("wireguard", "us_atlanta", {"username": "p1234567", "password": "pw"}, 0)
