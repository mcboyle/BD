"""HTTP/3 (QUIC) transport for high-packet-loss network paths (row887).

Attempts a QUIC handshake against the target host first; media CDNs that
support HTTP/3 avoid TCP head-of-line blocking and slow-start backoff on
lossy WAN links. When the handshake fails -- UDP/443 is firewalled, the
server has no QUIC listener, or aioquic is not installed -- the client
falls back transparently to httpx, which itself negotiates HTTP/2 or
HTTP/1.1 over TCP via ALPN. No site logins are touched by this module.

Fixer (row887 REFUTE, O963a):
  E1  a negotiated QUIC session CARRIES the media (H3 GET over the same
      connection, ``_quic_fetch``); it is no longer a probe followed by a
      TCP GET.
  E2  the QUIC destination is vetted PUBLIC_ONLY (DNS resolved and every
      address classified) BEFORE any UDP datagram is sent.
  E3  the handshake is bounded by ``quic_timeout`` (``asyncio.wait_for``
      around the connection open AND ``wait_connected``).
  E4  every transport writes to a sibling temp file and publishes with
      ``os.replace``; a failed transfer never disturbs a completed file.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import importlib.util
import logging
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from bulk_downloader.provider_resolve_impl._common import _is_safe_public_host
from bulk_downloader.ssrf_transport import PUBLIC_ONLY, guarded_transport

logger = logging.getLogger(__name__)

# aioquic and h2 are optional (requirements.txt "former requirements-optional"
# section, O963): row 331's guarded-imports gate requires every *statically*
# imported third-party root to be declared, and absence must degrade
# silently. Both are loaded by distribution name through importlib rather
# than a top-level `import aioquic` / `import h2`, so a clean install without
# them still runs this module; installing them unlocks the HTTP/3 data path
# and HTTP/2 framing respectively.
def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


AIOQUIC_AVAILABLE = _module_available("aioquic.asyncio")
HTTP2_AVAILABLE = _module_available("h2")

DEFAULT_QUIC_TIMEOUT = 5.0
_H3_STATUS_HEADER = b":status"


class Http3TransportError(RuntimeError):
    """Raised when a download fails on every available transport."""


class QuicDestinationRefused(Http3TransportError):
    """The QUIC destination failed the PUBLIC_ONLY policy (row887 E2)."""


@dataclass
class DownloadResult:
    protocol: str
    quic_negotiated: bool
    total_bytes: int
    sha256: str
    dest_path: Path
    fallback_reason: str | None = None


# ── E4: atomic publication ──────────────────────────────────────────────
@contextlib.contextmanager
def _atomic_destination(dest_path: Path) -> Iterator[BinaryIO]:
    """Open a sibling temp file for writing; publish it over ``dest_path``
    with ``os.replace`` only if the body completes. On any failure the temp
    file is removed and an existing completed ``dest_path`` is untouched."""
    dest_path = Path(dest_path)
    tmp = dest_path.with_name(f".{dest_path.name}.part-{os.getpid()}")
    try:
        with open(tmp, "wb") as fh:
            yield fh
        os.replace(tmp, dest_path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


class _Digest:
    def __init__(self) -> None:
        self.hasher = hashlib.sha256()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.hasher.update(chunk)
        self.total += len(chunk)


# ── E2: QUIC destination policy ─────────────────────────────────────────
def _guard_quic_destination(host: str) -> None:
    """PUBLIC_ONLY for the QUIC path, applied BEFORE the connector is
    called: the TCP path is vetted inside ``guarded_transport`` at connect
    time, but aioquic's connector has no such hook, so the resolve+classify
    step is done here. Loopback, private, link-local (169.254.169.254),
    reserved, multicast and unresolvable hosts are refused."""
    ok, why = _is_safe_public_host(host)
    if not ok:
        raise QuicDestinationRefused(f"QUIC destination refused ({host}): {why}")


# ── E1: H3 client protocol ──────────────────────────────────────────────
class _ConnectionTerminated(Exception):
    """The QUIC connection ended (peer close, reset, idle timeout) with the
    request stream still open."""


def _h3_protocol_class(base_cls: type, h3_connection_cls: type,
                       headers_event: type, data_event: type,
                       terminated_event: type = None,
                       read_timeout: float = DEFAULT_QUIC_TIMEOUT) -> type:
    """Build the H3 client protocol against injected aioquic classes.

    A factory so the class can be built without aioquic installed (tests
    inject stand-ins) and so the event handling -- the part that is ours --
    is unit-testable offline.

    Fixer round 2 (E5): the event loop has exits other than a frame on the
    request stream -- a quic ConnectionTerminated (peer close / reset /
    idle timeout; maps to NO H3 event) raises, and every wait for the next
    event is bounded by ``read_timeout`` so a silent peer raises too. Either
    lets ``download`` fall back to TCP instead of hanging.
    """

    class H3ClientProtocol(base_cls):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._http = h3_connection_cls(self._quic)
            self._h3_events: asyncio.Queue = asyncio.Queue()

        def quic_event_received(self, event: Any) -> None:
            if terminated_event is not None and isinstance(event, terminated_event):
                self._h3_events.put_nowait(_ConnectionTerminated(
                    getattr(event, "reason_phrase", "") or "QUIC connection terminated"))
                return
            for h3_event in self._http.handle_event(event):
                self._h3_events.put_nowait(h3_event)

        async def _next_event(self) -> Any:
            try:
                event = await asyncio.wait_for(self._h3_events.get(), read_timeout)
            except asyncio.TimeoutError as exc:
                raise Http3TransportError(f"HTTP/3 peer silent for {read_timeout}s") from exc
            if isinstance(event, _ConnectionTerminated):
                raise Http3TransportError(f"HTTP/3 connection terminated: {event}")
            return event

        async def get(self, url: httpx.URL) -> tuple[int, AsyncIterator[bytes]]:
            """Send GET for ``url`` on a new stream; return the status and
            an async iterator over the body."""
            stream_id = self._quic.get_next_available_stream_id()
            path = url.raw_path.decode() if isinstance(url.raw_path, bytes) else str(url.raw_path)
            self._http.send_headers(stream_id, [
                (b":method", b"GET"),
                (b":scheme", b"https"),
                (b":authority", url.netloc if isinstance(url.netloc, bytes) else str(url.netloc).encode()),
                (b":path", path.encode()),
                (b"user-agent", b"bulk-downloader-h3"),
            ], end_stream=True)
            self.transmit()

            status = 0
            while True:
                event = await self._next_event()
                if getattr(event, "stream_id", None) != stream_id:
                    continue
                if isinstance(event, headers_event):
                    for name, value in event.headers:
                        if name == _H3_STATUS_HEADER:
                            status = int(value)
                    if event.stream_ended:
                        return status, self._empty()
                    return status, self._body(stream_id)
                if isinstance(event, data_event):
                    # data before headers: malformed stream
                    raise Http3TransportError("H3 DATA frame before HEADERS")

        async def _empty(self) -> AsyncIterator[bytes]:
            return
            yield b""  # pragma: no cover

        async def _body(self, stream_id: int) -> AsyncIterator[bytes]:
            while True:
                event = await self._next_event()
                if getattr(event, "stream_id", None) != stream_id:
                    continue
                if isinstance(event, data_event):
                    if event.data:
                        yield event.data
                    if event.stream_ended:
                        return
                elif isinstance(event, headers_event) and event.stream_ended:
                    return  # trailers

    return H3ClientProtocol


def _aioquic_h3_protocol_class(read_timeout: float = DEFAULT_QUIC_TIMEOUT) -> type:
    aioquic_asyncio = importlib.import_module("aioquic.asyncio")
    h3_connection = importlib.import_module("aioquic.h3.connection")
    h3_events = importlib.import_module("aioquic.h3.events")
    quic_events = importlib.import_module("aioquic.quic.events")
    return _h3_protocol_class(
        aioquic_asyncio.QuicConnectionProtocol,
        h3_connection.H3Connection,
        h3_events.HeadersReceived,
        h3_events.DataReceived,
        terminated_event=getattr(quic_events, "ConnectionTerminated", None),
        read_timeout=read_timeout,
    )


async def _quic_fetch(url: httpx.URL, dest_path: Path, timeout: float) -> tuple[int, str]:
    """GET ``url`` over one QUIC/H3 connection into ``dest_path``.

    Test seam: monkeypatched in tests/test_row887_http3_transport.py; the
    real body needs aioquic and a QUIC-speaking peer (never a site login).
    Returns (total_bytes, sha256). Raises on any failure (the caller falls
    back to TCP); ``dest_path`` is only written on completion (E4).
    """
    host, port = url.host, url.port or 443
    _guard_quic_destination(host)  # E2: before any UDP is sent

    aioquic_asyncio = importlib.import_module("aioquic.asyncio")
    quic_configuration = importlib.import_module("aioquic.quic.configuration")
    configuration = quic_configuration.QuicConfiguration(is_client=True, alpn_protocols=["h3"])
    protocol_cls = _aioquic_h3_protocol_class(read_timeout=timeout)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with contextlib.AsyncExitStack() as stack:
        # E3: the handshake is bounded -- open AND wait_connected share the budget.
        protocol = await asyncio.wait_for(
            stack.enter_async_context(
                aioquic_asyncio.connect(host, port, configuration=configuration,
                                        create_protocol=protocol_cls)),
            timeout)
        await asyncio.wait_for(protocol.wait_connected(), max(0.0, deadline - loop.time()))

        status, body = await protocol.get(url)
        if status >= 400:
            raise Http3TransportError(f"HTTP/3 status {status} for {url}")
        digest = _Digest()
        with _atomic_destination(dest_path) as fh:
            async for chunk in body:
                digest.feed(chunk)
                fh.write(chunk)
    return digest.total, digest.hasher.hexdigest()


def _fetch_sync(url: str, dest_path: Path, timeout: float) -> tuple[str, int, str]:
    """Blocking GET run off the event loop via asyncio.to_thread.

    Media CDN targets are not operator-configured, so the transport is
    pinned PUBLIC_ONLY (row 703 seam) -- the same policy
    bulk_downloader/multi_conn.py's downloader clients use.
    """
    digest = _Digest()
    with (
        httpx.Client(
            timeout=timeout, transport=guarded_transport(PUBLIC_ONLY, http2=HTTP2_AVAILABLE)
        ) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        with _atomic_destination(Path(dest_path)) as fh:
            for chunk in response.iter_bytes():
                digest.feed(chunk)
                fh.write(chunk)
        protocol = response.http_version or "HTTP/1.1"
    return protocol, digest.total, digest.hasher.hexdigest()


class Http3Client:
    """Downloads a single media resource, preferring HTTP/3 when reachable."""

    def __init__(self, prefer_http3: bool = True, quic_timeout: float = DEFAULT_QUIC_TIMEOUT):
        self.prefer_http3 = prefer_http3
        self.quic_timeout = quic_timeout

    def _http3_eligible(self) -> bool:
        return self.prefer_http3 and AIOQUIC_AVAILABLE

    async def negotiate(self, host: str, port: int = 443) -> bool:
        """True if a bounded QUIC/h3 handshake with host:port succeeds.

        Policy (E2) and timeout (E3) apply exactly as in ``download``; a
        refused or timed-out destination is False, never an exception."""
        if not self._http3_eligible():
            return False
        try:
            _guard_quic_destination(host)
            aioquic_asyncio = importlib.import_module("aioquic.asyncio")
            quic_configuration = importlib.import_module("aioquic.quic.configuration")
            configuration = quic_configuration.QuicConfiguration(is_client=True, alpn_protocols=["h3"])
            async with contextlib.AsyncExitStack() as stack:
                protocol = await asyncio.wait_for(
                    stack.enter_async_context(
                        aioquic_asyncio.connect(host, port, configuration=configuration)),
                    self.quic_timeout)
                await asyncio.wait_for(protocol.wait_connected(), self.quic_timeout)
            return True
        except Exception:
            logger.debug("QUIC handshake to %s:%s failed or refused", host, port, exc_info=True)
            return False

    async def download(self, url: str, dest_path: Path) -> DownloadResult:
        parsed = httpx.URL(url)
        dest_path = Path(dest_path)

        fallback_reason = None
        if self._http3_eligible():
            try:
                total_bytes, sha256 = await _quic_fetch(parsed, dest_path, self.quic_timeout)
            except Exception as exc:
                # UDP/443 blocked, no QUIC listener, refused destination,
                # timeout, or a mid-stream failure: fall back to TCP. E4:
                # nothing was published. The reason rides on the result so a
                # fallback is never silent.
                fallback_reason = f"{type(exc).__name__}: {exc}"
                logger.debug("HTTP/3 transfer of %s failed; falling back", url, exc_info=True)
            else:
                return DownloadResult("HTTP/3", True, total_bytes, sha256, dest_path)

        try:
            protocol, total_bytes, sha256 = await asyncio.to_thread(
                _fetch_sync, url, dest_path, self.quic_timeout)
        except Exception as exc:
            raise Http3TransportError(f"download of {url} failed on every transport: {exc}") from exc
        return DownloadResult(protocol, False, total_bytes, sha256, dest_path,
                              fallback_reason=fallback_reason)
