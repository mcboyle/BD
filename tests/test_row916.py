"""Row 916: regional egress routing must reach the real download boundary."""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import threading

import httpx


class _Response:
    status_code = 200
    headers = {"content-length": "2"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self, _chunk_size):
        yield b"ok"


def _runner():
    from bulk_downloader import runner as runner_mod

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "regional-test"
    runner.config = {
        "regional_gateway_routes": {
            "media.example": ["http://primary:8080", "http://secondary:8080"],
        },
    }
    runner._stop = threading.Event()
    runner._download_proxy_url = lambda: None
    runner._update_job = lambda *args, **kwargs: None
    return runner


def test_domain_routes_to_designated_regional_gateway(monkeypatch, tmp_path):
    """A configured CDN suffix is sent through its primary regional egress."""
    from bulk_downloader import runner_transport as transport

    proxies = []
    from bulk_downloader import ssrf_transport
    monkeypatch.setattr(
        ssrf_transport, "guarded_transport",
        lambda _policy, *, proxy=None: proxies.append(proxy) or httpx.HTTPTransport(),
    )
    monkeypatch.setattr(httpx.Client, "stream", lambda *_args, **_kwargs: _Response())

    assert _runner()._do_direct_http_download(
        "https://page.example/item", "https://cdn.media.example/video.mp4",
        str(tmp_path / "video.mp4"),
    ) is True
    assert proxies == ["http://primary:8080"]


def test_connection_drop_retries_real_request_on_secondary_gateway(monkeypatch, tmp_path):
    """The guarded httpx stream retries the unchanged request on secondary."""
    from bulk_downloader import runner_transport as transport

    proxies = []
    attempts = []
    from bulk_downloader import ssrf_transport
    monkeypatch.setattr(
        ssrf_transport, "guarded_transport",
        lambda _policy, *, proxy=None: proxies.append(proxy) or httpx.HTTPTransport(),
    )

    def _stream(_client, method, url, **kwargs):
        attempts.append((method, url, kwargs["headers"]))
        if len(attempts) == 1:
            raise httpx.ConnectError("simulated packet drop")
        return _Response()

    monkeypatch.setattr(httpx.Client, "stream", _stream)
    assert _runner()._do_direct_http_download(
        "https://page.example/item", "https://cdn.media.example/video.mp4",
        str(tmp_path / "video.mp4"), referer="https://page.example/item",
    ) is True
    assert proxies == ["http://primary:8080", "http://secondary:8080"]
    assert attempts == [
        ("GET", "https://cdn.media.example/video.mp4", {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://page.example/item",
        }),
        ("GET", "https://cdn.media.example/video.mp4", {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://page.example/item",
        }),
    ]


def test_unhealthy_gateway_is_skipped_before_the_request():
    """Health checks prevent a known-unhealthy primary from receiving traffic."""
    from bulk_downloader.runner_transport import RegionalGatewayRouter

    router = RegionalGatewayRouter(
        {"media.example": ["http://primary:8080", "http://secondary:8080"]},
        health_check=lambda gateway: not gateway.endswith("primary:8080"),
    )
    attempted = []

    assert router.route_request(
        "cdn.media.example", lambda gateway: attempted.append(gateway) or "ok",
    ) == "ok"
    assert attempted == ["http://secondary:8080"]


def test_explicit_proxy_keeps_precedence_over_regional_route(monkeypatch, tmp_path):
    """Regional policy never bypasses an operator's explicit egress proxy."""
    from bulk_downloader import runner_transport as transport

    runner = _runner()
    runner._download_proxy_url = lambda: "http://operator:8080"
    proxies = []
    from bulk_downloader import ssrf_transport
    monkeypatch.setattr(
        ssrf_transport, "guarded_transport",
        lambda _policy, *, proxy=None: proxies.append(proxy) or httpx.HTTPTransport(),
    )
    monkeypatch.setattr(httpx.Client, "stream", lambda *_args, **_kwargs: _Response())

    assert runner._do_direct_http_download(
        "https://page.example/item", "https://cdn.media.example/video.mp4",
        str(tmp_path / "video.mp4"),
    ) is True
    assert proxies == ["http://operator:8080"]


# ── FIXER (row916-r2 REFUTE E1-E4) ───────────────────────────────────────

import pytest

from bulk_downloader import runner_transport as rt


def test_unmapped_host_goes_direct_not_error():
    """E2: when regional routes are configured for OTHER domains, a host with
    no route is a clear-net request: send(None), never ConnectionError."""
    router = rt.RegionalGatewayRouter({"eu.example": ["http://gw-eu:3128"]})
    seen = []
    assert router.route_request("other.example", lambda gw: seen.append(gw) or "ok") == "ok"
    assert seen == [None]
    assert router.has_route("cdn.eu.example") and not router.has_route("eu.example.org")


def test_failed_gateway_quarantine_expires_and_backs_off(monkeypatch):
    """E3: a failed gateway is skipped for a timed window (doubling per
    consecutive failure, capped), then re-offered through the health check;
    a success clears its streak."""
    clock = [1000.0]
    monkeypatch.setattr(rt.time, "monotonic", lambda: clock[0])
    checks = []
    router = rt.RegionalGatewayRouter({"eu.example": ["http://gw1", "http://gw2"]},
                                      health_check=lambda gw: checks.append(gw) or True)
    calls = []

    def send(gw):
        calls.append(gw)
        if gw == "http://gw1" and len(calls) < 6:
            raise ConnectionError("gw1 down")
        return "ok"

    assert router.route_request("a.eu.example", send) == "ok"      # gw1 fails -> gw2
    assert calls == ["http://gw1", "http://gw2"]
    assert router.resolve("a.eu.example") == "http://gw2"            # gw1 quarantined
    clock[0] += rt.GATEWAY_QUARANTINE_BASE_SEC + 1
    assert router.resolve("a.eu.example") == "http://gw1"            # window elapsed: re-offered
    calls.clear()
    router.route_request("a.eu.example", send)                       # gw1 fails again -> streak 2
    assert router._failed["http://gw1"] - clock[0] == pytest.approx(2 * rt.GATEWAY_QUARANTINE_BASE_SEC)
    clock[0] += 5 * rt.GATEWAY_QUARANTINE_BASE_SEC
    calls[:] = ["x"] * 6                                              # make gw1 succeed now
    assert router.route_request("a.eu.example", send) == "ok"
    assert "http://gw1" not in router._failed and router._failure_streak.get("http://gw1") is None
    # the window never exceeds the cap
    for _ in range(12):
        router._quarantine("http://gw2")
    assert router._failed["http://gw2"] - clock[0] <= rt.GATEWAY_QUARANTINE_MAX_SEC


def test_all_routed_gateways_down_raises_the_last_transport_error():
    router = rt.RegionalGatewayRouter({"eu.example": ["http://gw1"]})

    def send(gw):
        raise ConnectionError("down: %s" % gw)

    with pytest.raises(ConnectionError, match="down"):
        router.route_request("a.eu.example", send)
