"""v3.66.680 (B1/P2): PIA WireGuard backend for render_config.

No network: the token/addKey calls are monkeypatched; the pure config
assembler is tested directly against vpn_wireguard.render_conf.
"""
import bulk_downloader.vpn_providers.pia as pia
import bulk_downloader.vpn_wireguard as wg


_ADDKEY = {
    "status": "OK",
    "server_key": "c2VydmVycHVia2V5MDAwMDAwMDAwMDAwMDAwMDAwMDAwMD0=",
    "server_ip": "203.0.113.5",
    "server_port": 1337,
    "peer_ip": "10.20.30.40",
    "dns_servers": ["10.0.0.243"],
}


def test_pia_supports_wireguard_backend():
    assert "wireguard" in pia.SUPPORTED_BACKENDS


def test_pia_build_wg_config_shape_renders():
    loc = {"id": "us_atlanta", "hostname": "atlanta.privacy.network"}
    cfg = pia._build_wg_config(loc, "cHJpdmF0ZWtleTAwMDAwMDAwMDAwMDAwMDAwMDAwMDA9", _ADDKEY)
    for k in wg.REQUIRED_KEYS:
        assert cfg.get(k), f"missing {k}"
    conf = wg.render_conf(cfg)  # must not raise
    assert "[Interface]" in conf and "[Peer]" in conf
    assert "203.0.113.5:1337" in conf


def test_pia_render_config_wireguard_no_longer_raises(monkeypatch):
    monkeypatch.setattr(pia, "generate_keypair",
                        lambda: ("cHJpdmF0ZWtleTAwMDAwMDAwMDAwMDAwMDAwMDAwMDA9",
                                 "cHVibGlja2V5MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMD0="))
    monkeypatch.setattr(pia, "_pia_token", lambda user, pw: "tok-abc")
    monkeypatch.setattr(pia, "_pia_addkey", lambda host, token, pubkey: dict(_ADDKEY))
    out = pia.render_config(
        backend="wireguard", location="us_atlanta",
        credentials={"username": "p1234567", "password": "secret"}, socks_port=0)
    assert isinstance(out, dict)
    assert out.get("private_key") and out.get("peer_public_key") and out.get("endpoint")
