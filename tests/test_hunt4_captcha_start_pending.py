"""Cached clearance cannot bypass captcha session state."""

BD_GATE_SCOPE = "module"


def test_cached_clearance_requires_pending_session(monkeypatch):
    from flask import Flask

    from bulk_downloader import app_captcha_relay, captcha_relay
    from bulk_downloader.login_impl import token_manager

    captcha_relay._reset_for_tests()
    monkeypatch.setattr(captcha_relay, "_maybe_push", lambda *_: None)
    pending_url = "https://example.test/pending"
    unknown_url = "https://example.test/unknown"
    captcha_relay.mark_captcha_needed("site-a", pending_url, "turnstile")
    assert captcha_relay.get_pending(pending_url)["status"] == "pending"
    assert captcha_relay.get_pending(unknown_url) is None

    class Cache:
        def get_clearance(self, egress_ip, domain):
            assert domain == "example.test"
            return [{"name": "cf_clearance", "value": "test-value"}]

    monkeypatch.setattr(token_manager, "get_default_cache", Cache)
    app = Flask(__name__)
    app.testing = True
    assert app_captcha_relay.register_routes(app) > 0
    client = app.test_client()

    valid = client.post("/api/captcha/start_solve", json={"url": pending_url})
    assert valid.status_code == 200
    assert valid.get_json()["cached"] is True
    assert captcha_relay.get_pending(pending_url)["status"] == "resolved"

    invalid = client.post("/api/captcha/start_solve", json={"url": unknown_url})
    assert invalid.status_code == 404
    assert captcha_relay.get_pending(unknown_url) is None
