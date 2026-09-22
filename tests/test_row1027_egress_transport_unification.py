"""Row 1027: Decoupling Egress Transport Abstraction & Dual-Client Unification (EgressTransport).

Validates unified egress transport abstraction wrapping bulk_downloader.ssrf_transport,
SSRF link-local refusal, response models, typed failure errors, process isolation,
and integration with bulk_downloader.http_client and bulk_downloader.discovery.

RED on baseline: fails with semantic and behavioral assertions on base.
Includes positive control test passing on baseline to prove the probe can say YES.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"


def test_positive_control_proxy_open_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline capabilities."""
    from bulk_downloader import http_client

    assert hasattr(http_client, "proxy_open")
    assert callable(http_client.proxy_open)

    called = []

    def mock_direct(req, timeout):
        called.append(timeout)
        return "direct_ok"

    res = http_client.proxy_open("http://example.com", timeout=5, direct_open=mock_direct)
    assert res == "direct_ok"
    assert called == [5]


def test_egress_transport_capability_implemented():
    """RED assertion 1: capability and product callers must be implemented with semantic AssertionError on base."""
    from bulk_downloader import http_client

    try:
        from bulk_downloader import egress_transport
    except ImportError:
        egress_transport = None

    assert egress_transport is not None, (
        "Row 1027 capability missing: Decoupling Egress Transport Abstraction & "
        "Dual-Client Unification (EgressTransport) not implemented in bulk_downloader.egress_transport"
    )
    assert hasattr(egress_transport, "UnifiedEgressClient"), (
        "Row 1027 component missing: bulk_downloader.egress_transport.UnifiedEgressClient"
    )
    assert hasattr(egress_transport, "HttpxEgressTransport"), (
        "Row 1027 component missing: bulk_downloader.egress_transport.HttpxEgressTransport"
    )
    assert hasattr(http_client, "unified_request"), (
        "Row 1027 caller missing: bulk_downloader.http_client.unified_request"
    )


def test_product_caller_discovery_uses_unified_egress():
    """Behavioral verification: discovery._fetch delegates through the unified egress client."""
    from bulk_downloader import discovery, http_client

    assert hasattr(http_client, "unified_request"), "http_client missing unified_request"

    from bulk_downloader.egress_transport import (
        BaseEgressTransport,
        EgressResponse,
        UnifiedEgressClient,
        temporary_egress_client,
    )

    class MockFeedTransport(BaseEgressTransport):
        def execute(self, method, url, headers=None, data=None, timeout=None):
            return EgressResponse(
                status_code=200,
                headers={"Content-Type": "application/rss+xml"},
                content=b"<rss><channel><item><link>https://sample.com/item1</link></item></channel></rss>",
                url=url,
            )

    mock_client = UnifiedEgressClient(transport=MockFeedTransport())
    with temporary_egress_client(mock_client):
        body = discovery._fetch("https://sample.com/feed.xml")
        assert body is not None
        assert b"sample.com/item1" in body
        urls = discovery.parse_rss(body)
        assert urls == ["https://sample.com/item1"]


def test_egress_transport_enforces_ssrf_guard():
    """Verify HttpxEgressTransport enforces SSRF protection against link-local metadata addresses."""
    from bulk_downloader.egress_transport import HttpxEgressTransport, EgressTransportError
    from bulk_downloader.ssrf_transport import PINNED

    transport = HttpxEgressTransport(policy=PINNED)
    with pytest.raises(EgressTransportError) as exc_info:
        transport.execute("GET", "http://169.254.169.254/latest/meta-data/")
    assert "SSRF" in str(exc_info.value) or "refused" in str(exc_info.value)
    transport.close()


def test_egress_transport_public_only_refuses_private_targets():
    """Verify HttpxEgressTransport with policy=PUBLIC_ONLY installs RestoringPublicGuard and refuses private hosts."""
    from bulk_downloader.egress_transport import (
        HttpxEgressTransport,
        UnifiedEgressClient,
        EgressTransportConfig,
        EgressTransportError,
    )
    from bulk_downloader.ssrf_transport import PUBLIC_ONLY, established_guard_cls

    # 1. Direct transport construction: must use RestoringPublicGuard and share single transport with client
    transport = HttpxEgressTransport(policy=PUBLIC_ONLY)
    assert type(transport._transport).__name__ == "RestoringPublicGuard"
    assert issubclass(type(transport._transport), established_guard_cls())
    assert getattr(transport._transport, "policy", None) == PUBLIC_ONLY
    assert transport._client._transport is transport._transport, (
        "Client must reuse the single constructed guarded transport instance"
    )

    # 2. Unified client with PUBLIC_ONLY must also install RestoringPublicGuard
    client = UnifiedEgressClient(config=EgressTransportConfig(policy=PUBLIC_ONLY))
    assert type(client.transport._transport).__name__ == "RestoringPublicGuard"
    assert issubclass(type(client.transport._transport), established_guard_cls())
    assert client.transport._client._transport is client.transport._transport

    # 3. Private host (localhost) must be refused under PUBLIC_ONLY policy
    with pytest.raises(EgressTransportError) as exc_info:
        transport.execute("GET", "http://localhost/")
    assert "SSRF" in str(exc_info.value) or "refused" in str(exc_info.value)

    transport.close()
    client.close()


def test_models_and_configuration():
    """Verify EgressTransportConfig and EgressResponse models."""
    from bulk_downloader.egress_transport import EgressTransportConfig, EgressResponse

    cfg = EgressTransportConfig(
        proxy_url="http://127.0.0.1:8888",
        timeout=15.0,
        verify_ssl=True,
        user_agent="BulkDownloader/3.66",
    )
    assert cfg.proxy_url == "http://127.0.0.1:8888"
    assert cfg.timeout == 15.0
    assert cfg.verify_ssl is True
    assert cfg.user_agent == "BulkDownloader/3.66"

    resp = EgressResponse(
        status_code=200,
        headers={"Content-Type": "application/json", "X-Server": "Test"},
        content=b'{"status": "ok", "items": [1, 2]}',
        url="http://example.com/api",
    )
    assert resp.status_code == 200
    assert resp.is_success is True
    assert resp.text == '{"status": "ok", "items": [1, 2]}'
    data = resp.json()
    assert data["status"] == "ok"
    assert data["items"] == [1, 2]
    assert resp.get_header("content-type") == "application/json"
    assert resp.get_header("X-SERVER") == "Test"


def test_temporary_egress_client_restores_global_state():
    """Verify temporary_egress_client restores previous global client cleanly without process poisoning."""
    from bulk_downloader.egress_transport import (
        get_unified_egress_client,
        temporary_egress_client,
        UnifiedEgressClient,
        BaseEgressTransport,
        EgressResponse,
    )

    initial_client = get_unified_egress_client()

    class ScopeTransport(BaseEgressTransport):
        def execute(self, method, url, headers=None, data=None, timeout=None):
            return EgressResponse(status_code=200, headers={}, content=b"scoped", url=url)

    scoped_client = UnifiedEgressClient(transport=ScopeTransport())
    with temporary_egress_client(scoped_client):
        active = get_unified_egress_client()
        assert active is scoped_client
        res = active.get("https://dummy.site")
        assert res.content == b"scoped"

    assert get_unified_egress_client() is initial_client


def test_egress_transport_raises_typed_error_on_failure():
    """Verify transport failures raise EgressTransportError rather than synthetic 502 with error body."""
    from bulk_downloader.egress_transport import HttpxEgressTransport, EgressTransportError

    transport = HttpxEgressTransport()
    with pytest.raises(EgressTransportError) as exc_info:
        transport.execute("GET", "http://0.0.0.0:1/")
    assert isinstance(exc_info.value, EgressTransportError)
    assert exc_info.value.url == "http://0.0.0.0:1/"
    transport.close()
