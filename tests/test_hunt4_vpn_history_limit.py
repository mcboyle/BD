"""VPN history rejects malformed limits without failing the request."""

BD_GATE_SCOPE = "module"


def test_vpn_history_limit_validation(monkeypatch):
    from flask import Flask

    from bulk_downloader import app_vpn_api, vpn_leak_tests

    calls = []

    def fake_history(tunnel_id, limit):
        calls.append((tunnel_id, limit))
        return []

    monkeypatch.setattr(vpn_leak_tests, "get_history", fake_history)
    app = Flask(__name__)
    app.testing = True
    assert app_vpn_api.register_routes(app) > 0
    client = app.test_client()

    good = client.get("/api/vpn/tunnels/tunnel-a/leak_test/history?limit=2")
    assert good.status_code == 200
    assert calls == [("tunnel-a", 2)]

    bad = client.get("/api/vpn/tunnels/tunnel-a/leak_test/history?limit=abc")
    assert bad.status_code == 400
    assert calls == [("tunnel-a", 2)]
