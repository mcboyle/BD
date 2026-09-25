from types import SimpleNamespace

from flask import Flask

from bulk_downloader import app_flaresolverr

BD_GATE_SCOPE = "module"


class FakeFlareClient:
    DEFAULT_ENDPOINT = "http://127.0.0.1:8191"

    def __init__(self):
        self.timeouts = []

    def solve_cloudflare(self, url, *, endpoint, timeout_s):
        self.timeouts.append(timeout_s)
        return SimpleNamespace(ok=True, elapsed_s=0, cookies=[], user_agent="", status_code=200, html="", error=None)


def test_invalid_test_timeout_returns_validation_result(monkeypatch):
    client = FakeFlareClient()
    monkeypatch.setattr(app_flaresolverr, "_app__FLARE_AVAILABLE", lambda: True)
    monkeypatch.setattr(app_flaresolverr, "_app__flare_client", lambda: client)
    monkeypatch.setattr(app_flaresolverr, "_check_csrf", lambda: None)
    app = Flask(__name__)

    with app.test_request_context(json={"url": "https://example.com", "timeout_s": 5}):
        assert app_flaresolverr.api_flaresolverr_test().get_json()["ok"]
    assert client.timeouts == [5.0]

    with app.test_request_context(json={"url": "https://example.com", "timeout_s": "invalid"}):
        response = app_flaresolverr.api_flaresolverr_test()
        assert response[1] == 400
        assert response[0].get_json()["ok"] is False
    assert client.timeouts == [5.0]

    # lens (B1): a non-scalar JSON value raises TypeError from float(), not ValueError;
    # it must take the same 400 path and never reach solve.
    with app.test_request_context(json={"url": "https://example.com", "timeout_s": [5]}):
        response = app_flaresolverr.api_flaresolverr_test()
        assert response[1] == 400
        assert response[0].get_json()["ok"] is False
    assert client.timeouts == [5.0]
