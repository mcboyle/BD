"""RED-first repro for F-COREBD08-01.

vpn_wireguard.start() writes the secret WireGuard .conf (contains PrivateKey)
via Path.write_text() -- which creates the file with the default umask (world/
group readable) -- and only THEN chmods it to 0o600. The follow-up chmod is
wrapped in `except OSError: pass`, so if it is a no-op/failure the secret is
left world-readable. After the fix the file is written private-from-birth via
os.open(..., 0o600).

Pristine RED (with chmod neutralized): the .conf is NOT 0o600 at write time.
"""
import stat
import os
import types


def test_conf_private_from_birth(tmp_path, monkeypatch):
    import bulk_downloader.vpn_wireguard as m
    import bulk_downloader.vpn_config as vc

    monkeypatch.setattr(m, "CONF_DIR", tmp_path)
    monkeypatch.setattr(m, "_WG_BINARY", "/usr/bin/wg", raising=False)
    monkeypatch.setattr(vc, "resolve_secrets", lambda cfg: cfg, raising=False)
    # the code tolerates chmod failure; neutralize it so birth-mode is what matters
    monkeypatch.setattr(m.os, "chmod", lambda *a, **k: None)

    captured = {}

    def _cap(conf_path, iface):
        captured["mode"] = stat.S_IMODE(os.stat(conf_path).st_mode)
        raise RuntimeError("stop after write")

    monkeypatch.setattr(m, "_wg_up", _cap, raising=False)

    t = types.SimpleNamespace(
        tunnel_id="t1", socks_port=1080, last_error=None,
        config={"private_key": "aQ==", "address": "10.0.0.2/32",
                "peer_public_key": "bQ==", "endpoint": "1.2.3.4:51820"})
    m.start(t)
    assert captured.get("mode") == 0o600, oct(captured.get("mode") if captured.get("mode") is not None else -1)
