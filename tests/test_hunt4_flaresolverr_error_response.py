"""Service error responses must preserve the client's fail-open contract."""

from types import SimpleNamespace

import httpx

from bulk_downloader import flaresolverr_client, ssrf_transport

BD_GATE_SCOPE = "module"


def test_null_service_message_returns_failure(monkeypatch):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr(flaresolverr_client, "_httpx_available", lambda: True)
    monkeypatch.setattr(ssrf_transport, "guarded_transport", lambda _policy: object())
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: Client())
    payload = {"status": "error", "message": "busy"}
    positive = flaresolverr_client.solve_cloudflare("https://example.test/")
    assert positive.ok is False and positive.error == "flare_status_error:busy"

    payload["message"] = None
    result = flaresolverr_client.solve_cloudflare("https://example.test/")
    assert result.ok is False and result.error == "flare_status_error:"

    payload["message"] = 7  # lens (B1): a non-string message must be coerced, not sliced
    result = flaresolverr_client.solve_cloudflare("https://example.test/")
    assert result.ok is False and result.error == "flare_status_error:7"
