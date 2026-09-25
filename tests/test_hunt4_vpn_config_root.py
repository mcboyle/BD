"""A malformed VPN store root must not crash configuration loading."""

from bulk_downloader import vpn_config

BD_GATE_SCOPE = "module"


def test_non_object_vpn_config_root_is_handled(monkeypatch, tmp_path):
    path = tmp_path / "tunnels.json"
    monkeypatch.setenv("BD_VPN_CONFIG_PATH", str(path))
    try:
        path.write_text('{"tunnels": []}', encoding="utf-8")
        vpn_config._reset_for_tests()
        assert vpn_config.load()["tunnels"] == []

        path.write_text("[]", encoding="utf-8")
        vpn_config._reset_for_tests()
        assert vpn_config.load()["tunnels"] == []
        assert path.read_text(encoding="utf-8") == "[]"
    finally:
        vpn_config._reset_for_tests()
