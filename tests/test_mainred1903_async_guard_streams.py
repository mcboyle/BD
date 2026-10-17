"""mainred-1903 r3: the installed PUBLIC_ONLY guard serves ``httpx.AsyncClient``.

r2's guard answered ``handle_async_request`` by running the SYNC transport in a
thread.  httpx.AsyncClient asserts ``isinstance(response.stream,
AsyncByteStream)`` and every admitted ``AsyncHttpClient`` request died (cx53 F1).
These tests keep the REAL installed guard in place -- no transport swap -- and
substitute only the network bytes beneath it, so a refusal that leaks or an
admitted request that cannot stream is a failure of the shipped object.
"""
from __future__ import annotations

import asyncio
import socket

import httpcore
import httpx
import pytest

WIRE = b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nsynthetic"
PUBLIC_LITERAL = "http://8.8.8.8/probe"
NONPUBLIC_LITERALS = ("127.0.0.1", "10.0.0.1", "169.254.169.254")

BD_GATE_SCOPE = "module"


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


def wire_under(transport, body: bytes = WIRE) -> None:
    """Place synthetic bytes UNDER the transport an ASYNC client holds.

    The guard keeps its async pool on ``async_wire()``; a bare
    ``httpx.AsyncHTTPTransport`` (unguarded) carries the pool itself.  A guard
    without ``async_wire`` (r2's shape) only has its sync pool, so the sync
    mock goes there and the client must fail on the stream type, not on DNS.
    The guard object is never replaced.
    """
    async_wire = getattr(transport, "async_wire", None)
    if callable(async_wire):
        async_wire()._pool._network_backend = httpcore.AsyncMockBackend([body])
    elif isinstance(transport, httpx.AsyncHTTPTransport):
        transport._pool._network_backend = httpcore.AsyncMockBackend([body])
    else:
        transport._pool._network_backend = httpcore.MockBackend([body])


def sync_wire_under(transport, body: bytes = WIRE) -> None:
    """Place synthetic bytes UNDER the transport a SYNC client holds."""
    transport._pool._network_backend = httpcore.MockBackend([body])


def _no_retry():
    from bulk_downloader.network.http_client import BackoffConfig
    return BackoffConfig(max_retries=0)


def test_async_client_admitted_request_is_served():
    """cx53 test_async_actual_transport: the installed AsyncHttpClient."""
    from bulk_downloader.network.http_client import AsyncHttpClient

    async def run():
        async with AsyncHttpClient(backoff_config=_no_retry()) as client:
            wire_under(client._client._transport)
            response = await client.get(PUBLIC_LITERAL)
            assert response.status_code == 200
            assert response.text == "synthetic"
    asyncio.run(run())


def test_async_guard_streams_an_async_byte_stream():
    """The response the guard returns is the async wire's: AsyncByteStream,
    streamed by aiter_bytes, closed by aclose -- not a sync stream in a thread."""
    from bulk_downloader.ssrf_transport import guarded_transport, PUBLIC_ONLY

    async def run():
        async with httpx.AsyncClient(transport=guarded_transport(PUBLIC_ONLY)) as client:
            wire_under(client._transport)
            async with client.stream("GET", PUBLIC_LITERAL) as response:
                assert isinstance(response.stream, httpx.AsyncByteStream), type(response.stream)
                chunks = [chunk async for chunk in response.aiter_bytes()]
            assert b"".join(chunks) == b"synthetic"
            assert response.is_closed
            # redirect/cookie identity: the pinned literal never leaks back.
            assert str(response.request.url) == PUBLIC_LITERAL
            assert "sni_hostname" not in response.request.extensions
    asyncio.run(run())


def test_sync_client_admitted_request_is_served():
    """cx53 test_sync_actual_transport: unchanged sync path, same guard class."""
    from bulk_downloader.network.http_client import HttpClient
    with HttpClient(backoff_config=_no_retry()) as client:
        sync_wire_under(client._client._transport)
        response = client.get(PUBLIC_LITERAL)
        assert response.status_code == 200
        assert response.text == "synthetic"


@pytest.mark.parametrize("host", NONPUBLIC_LITERALS)
def test_async_refuses_nonpublic_literal_before_network(host):
    """cx53 test_async_refuses_nonpublic_before_network: loopback, private,
    cloud metadata are refused by the guard with the structured reason."""
    from bulk_downloader.network.http_client import AsyncHttpClient
    from bulk_downloader.ssrf_transport import GuardedTransportRefused

    async def run():
        async with AsyncHttpClient(backoff_config=_no_retry()) as client:
            wire_under(client._client._transport)
            with pytest.raises(GuardedTransportRefused) as refused:
                await client.get(f"http://{host}/probe")
            assert refused.value.host == host
    asyncio.run(run())


def test_async_hostname_resolving_private_is_refused(monkeypatch):
    """The async path runs the established guard's resolve/classify/pin: a
    hostname whose only address is private never reaches the wire."""
    from bulk_downloader.network.http_client import AsyncHttpClient
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    def private_only(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port))]
    monkeypatch.setattr(socket, "getaddrinfo", private_only)

    async def run():
        async with AsyncHttpClient(backoff_config=_no_retry()) as client:
            wire_under(client._client._transport)
            with pytest.raises(SSRFBlocked):
                await client.get("http://rebinder.example/probe")
    asyncio.run(run())


def test_async_hostname_resolving_public_is_pinned_and_served(monkeypatch):
    """Positive control for the hostname case: a public-only resolution is
    admitted, pinned for the socket, and the logical request restored."""
    from bulk_downloader.ssrf_transport import guarded_transport, PUBLIC_ONLY

    def public_only(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
    monkeypatch.setattr(socket, "getaddrinfo", public_only)

    async def run():
        async with httpx.AsyncClient(transport=guarded_transport(PUBLIC_ONLY)) as client:
            wire_under(client._transport)
            response = await client.get("http://public.example/probe")
            assert response.text == "synthetic"
            assert response.request.url.host == "public.example"
            assert "sni_hostname" not in response.request.extensions
    asyncio.run(run())


def test_aclose_closes_the_async_wire():
    from bulk_downloader.ssrf_transport import guarded_transport, PUBLIC_ONLY

    async def run():
        guard = guarded_transport(PUBLIC_ONLY)
        wire = guard.async_wire()
        assert guard.async_wire() is wire
        await guard.aclose()
        assert guard._async_wire is None
    asyncio.run(run())
