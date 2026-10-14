"""Row 1027: Decoupling Egress Transport Abstraction & Dual-Client Unification (EgressTransport).

Provides a decoupled transport interface, response abstractions, and unified client execution
across standard and guarded proxy egress channels, wrapping bulk_downloader.ssrf_transport.
"""
from __future__ import annotations

import abc
import contextlib
import json
import logging
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional

import httpx

from bulk_downloader.provider_resolve_impl._common import SSRFBlocked
from bulk_downloader.ssrf_transport import (
    PINNED,
    PUBLIC_ONLY,
    GuardedTransportRefused,
    guarded_transport,
)

log = logging.getLogger("bulk_downloader.egress_transport")


class EgressTransportError(Exception):
    """Raised when an egress transport connection or resolution fails before an HTTP response."""

    def __init__(self, message: str, *, url: str, original_error: Optional[Exception] = None) -> None:
        super().__init__(message)
        self.url = url
        self.original_error = original_error


DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024


MAX_TIMEOUT_SECONDS = 86400.0
_MAX_REDIRECTS = 20
_MAX_ENCODINGS = 3
_CHUNK = 65536


def _checked_timeout(value: Any, default: Any, url: str) -> float:
    """The request's time budget in seconds: ``value``, else ``default``, else 30; refused inside the error contract."""
    t = default if value is None else value
    if t is None:
        t = 30.0
    seconds = None
    if not isinstance(t, bool) and isinstance(t, (int, float)):
        try:
            seconds = float(t)
        except OverflowError:
            seconds = None
    if seconds is None or not 0 < seconds <= MAX_TIMEOUT_SECONDS:  # nan and inf fail the comparison too
        shown = repr(t) if len(repr(t)) <= 40 else repr(t)[:37] + "..."
        raise EgressTransportError(
            f"invalid timeout {shown}: a number of seconds greater than 0 and at most {MAX_TIMEOUT_SECONDS:g} is required",
            url=url)
    return seconds


class _Inflater:
    """One Content-Encoding layer (gzip / deflate) decoded in pieces of at most ``_CHUNK`` bytes."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._d = zlib.decompressobj(47)  # gzip or zlib framing, detected
        self._started = False

    def decode(self, data: bytes) -> Iterator[bytes]:
        buf = data
        while buf and not self._d.eof:
            try:
                out = self._d.decompress(buf, _CHUNK)
            except zlib.error:
                if self._started or self.name != "deflate":
                    raise
                self._d = zlib.decompressobj(-15)  # "deflate" sent as a raw stream
                self._started = True
                continue
            self._started = True
            buf = self._d.unconsumed_tail
            if out:
                yield out
            if not buf and len(out) == _CHUNK:
                while not self._d.eof:  # output still pending behind an input that is fully consumed
                    more = self._d.decompress(b"", _CHUNK)
                    if not more:
                        break
                    yield more

    @property
    def complete(self) -> bool:
        return self._d.eof or not self._started


class _BoundedDecoder:
    """Decodes a response body through its Content-Encoding chain with every layer's output bounded.

    httpx decodes a raw chunk whole before handing it over, so a few KiB of nested gzip became gigabytes in memory
    before any cap could look at it. Here no layer ever holds more than ``_CHUNK`` bytes of output at a time and no
    layer may produce more than ``cap`` bytes in total.
    """

    def __init__(self, header: str, cap: int, url: str) -> None:
        names = [n.strip().lower() for n in header.split(",")]
        names = [n for n in names if n and n != "identity"]
        if len(names) > _MAX_ENCODINGS:
            raise EgressTransportError(f"Transport failure: {len(names)} stacked Content-Encodings refused", url=url)
        for n in names:
            if n not in ("gzip", "x-gzip", "deflate"):
                raise EgressTransportError(f"Transport failure: unsupported Content-Encoding {n!r}", url=url)
        self._layers = [_Inflater(n) for n in reversed(names)]  # the last one applied is the first one undone
        self._made = [0] * len(self._layers)
        self._cap = cap
        self._url = url

    def feed(self, data: bytes, layer: int = 0) -> Iterator[bytes]:
        if layer == len(self._layers):
            if data:
                yield data
            return
        try:
            for piece in self._layers[layer].decode(data):
                self._made[layer] += len(piece)
                if self._made[layer] > self._cap:
                    raise EgressTransportError(
                        f"Response too large: decoded body exceeds the {self._cap}-byte cap", url=self._url)
                yield from self.feed(piece, layer + 1)
        except zlib.error as exc:
            raise EgressTransportError(
                f"Transport failure: undecodable {self._layers[layer].name} body: {exc}", url=self._url,
                original_error=exc) from exc

    def finish(self) -> None:
        for layer in self._layers:
            if not layer.complete:
                raise EgressTransportError(f"Transport failure: truncated {layer.name} body", url=self._url)


def _checked_max_bytes(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"max_response_bytes must be a positive int, got {value!r}")
    return value


@dataclass
class EgressTransportConfig:
    proxy_url: Optional[str] = None
    timeout: float = 30.0
    verify_ssl: bool = True
    user_agent: str = "BulkDownloader/3.66"
    headers: Dict[str, str] = field(default_factory=dict)
    policy: str = PINNED
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES


@dataclass
class EgressResponse:
    status_code: int
    headers: Dict[str, str]
    content: bytes
    url: str

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))

    def get_header(self, key: str, default: Optional[str] = None) -> Optional[str]:
        target = key.lower()
        for k, v in self.headers.items():
            if k.lower() == target:
                return v
        return default


class BaseEgressTransport(abc.ABC):
    """Abstract base transport interface."""

    @abc.abstractmethod
    def execute(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> EgressResponse:
        raise NotImplementedError


class HttpxEgressTransport(BaseEgressTransport):
    """Guarded httpx-based egress transport driver wrapping ssrf_transport."""

    def __init__(
        self,
        policy: str = PINNED,
        verify_ssl: bool = True,
        proxy_url: Optional[str] = None,
        timeout: float = 30.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if policy not in (PUBLIC_ONLY, PINNED):
            raise ValueError(
                f"unknown egress policy {policy!r}; expected one of {(PUBLIC_ONLY, PINNED)}")
        self.policy = policy
        self.verify_ssl = verify_ssl
        self.proxy_url = proxy_url
        self.default_timeout = timeout
        self.max_response_bytes = _checked_max_bytes(max_response_bytes)

        transport_kwargs: Dict[str, Any] = {"verify": verify_ssl}
        if proxy_url:
            transport_kwargs["proxy"] = proxy_url

        if policy == PUBLIC_ONLY:
            self._client = httpx.Client(
                transport=guarded_transport(PUBLIC_ONLY, **transport_kwargs),
                verify=verify_ssl,
                follow_redirects=True,
            )
        else:
            self._client = httpx.Client(
                transport=guarded_transport(PINNED, **transport_kwargs),
                verify=verify_ssl,
                follow_redirects=True,
            )
        self._transport = self._client._transport

    def execute(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> EgressResponse:
        req_headers = dict(headers or {})
        # ``t`` is the budget for the WHOLE exchange: every redirect hop gets what is left of it as its per-operation
        # timeout, and the deadline is checked when each hop's headers arrive and after every piece of the body.
        t = _checked_timeout(timeout, self.default_timeout, url)
        deadline = time.monotonic() + t
        verb = method.upper()
        if not any(k.lower() == "accept-encoding" for k in req_headers):
            req_headers["Accept-Encoding"] = "gzip, deflate"  # the encodings _BoundedDecoder undoes

        def late() -> EgressTransportError:
            return EgressTransportError(f"Transport failure: response not complete within {t:g} s", url=url)

        try:
            request = self._client.build_request(verb, url, headers=req_headers, content=data, timeout=t)
            for _hop in range(_MAX_REDIRECTS + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise late()
                request.extensions = dict(request.extensions, timeout=httpx.Timeout(remaining).as_dict())
                resp = self._client.send(request, stream=True, follow_redirects=False)
                try:
                    if time.monotonic() > deadline:
                        raise late()
                    if resp.next_request is None:
                        return self._read(resp, deadline, late, url)
                    request = resp.next_request
                finally:
                    resp.close()
            raise EgressTransportError(f"Transport failure: more than {_MAX_REDIRECTS} redirects", url=url)
        except (GuardedTransportRefused, SSRFBlocked) as exc:
            log.warning("Guarded transport refused %s: %s", url, exc)
            raise EgressTransportError(f"SSRF refused: {exc}", url=url, original_error=exc) from exc
        except (httpx.RequestError, OSError) as exc:
            log.warning("EgressTransport error for %s: %s", url, exc)
            raise EgressTransportError(f"Transport failure: {exc}", url=url, original_error=exc) from exc

    def _read(self, resp: httpx.Response, deadline: float, late: Any, url: str) -> EgressResponse:
        cap = self.max_response_bytes
        declared = resp.headers.get("content-length", "")
        if resp.request.method != "HEAD" and declared.isdigit() and int(declared) > cap:
            raise EgressTransportError(
                f"Response too large: Content-Length {declared} exceeds the {cap}-byte cap", url=url)
        decoder = _BoundedDecoder(resp.headers.get("content-encoding", ""), cap, url)
        body = bytearray()
        wire = 0
        for raw in resp.iter_raw():  # as the bytes arrive (a chunk_size would hold a trickle back past the deadline)
            wire += len(raw)
            if wire > cap:
                raise EgressTransportError(f"Response too large: body exceeds the {cap}-byte cap", url=url)
            for piece in decoder.feed(raw):  # at most ``cap`` bytes in all: the wire and every layer are capped
                body += piece
            if time.monotonic() > deadline:
                raise late()
        decoder.finish()
        return EgressResponse(
            status_code=resp.status_code,
            headers=dict(resp.headers.items()),
            content=bytes(body),
            url=str(resp.url),
        )

    def close(self) -> None:
        self._client.close()


class UnifiedEgressClient:
    """Unified client orchestrating egress requests across pluggable transport layers."""

    def __init__(
        self,
        config: Optional[EgressTransportConfig] = None,
        transport: Optional[BaseEgressTransport] = None,
    ) -> None:
        self.config = config or EgressTransportConfig()
        self._transport = transport or HttpxEgressTransport(
            policy=self.config.policy,
            verify_ssl=self.config.verify_ssl,
            proxy_url=self.config.proxy_url,
            timeout=self.config.timeout,
            max_response_bytes=self.config.max_response_bytes,
        )

    @property
    def transport(self) -> BaseEgressTransport:
        return self._transport

    def close(self) -> None:
        if hasattr(self._transport, "close") and callable(self._transport.close):
            self._transport.close()

    def set_transport(self, transport: BaseEgressTransport) -> None:
        self._transport = transport

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> EgressResponse:
        req_headers = dict(self.config.headers)
        if self.config.user_agent:
            req_headers["User-Agent"] = self.config.user_agent
        if headers:
            req_headers.update(headers)

        t = _checked_timeout(timeout, self.config.timeout, url)
        return self._transport.execute(
            method=method,
            url=url,
            headers=req_headers,
            data=data,
            timeout=t,
        )

    def get(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> EgressResponse:
        return self.request("GET", url, headers=headers, timeout=timeout)

    def post(
        self,
        url: str,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> EgressResponse:
        return self.request("POST", url, headers=headers, data=data, timeout=timeout)


_GLOBAL_CLIENT: Optional[UnifiedEgressClient] = None
_GLOBAL_LOCK = threading.RLock()
# Active temporary_egress_client scopes, oldest first (one single-item list per scope, matched by identity), and the
# global client that was installed before the first of them.
_OVERRIDES: list = []
_OVERRIDE_BASE: Optional[UnifiedEgressClient] = None


def get_unified_egress_client() -> UnifiedEgressClient:
    global _GLOBAL_CLIENT
    client = _GLOBAL_CLIENT
    if client is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_CLIENT is None:
                _GLOBAL_CLIENT = UnifiedEgressClient()
            client = _GLOBAL_CLIENT
    return client


@contextlib.contextmanager
def temporary_egress_client(client: UnifiedEgressClient) -> Iterator[UnifiedEgressClient]:
    """Scoped context manager for temporarily overriding the process-global client.

    Scopes may overlap across threads and need not exit in the order they were entered: the newest scope still
    open decides the global client, and when the last one exits the client installed before the first is restored.
    """
    global _GLOBAL_CLIENT, _OVERRIDE_BASE
    scope = [client]
    with _GLOBAL_LOCK:
        if not _OVERRIDES:
            _OVERRIDE_BASE = _GLOBAL_CLIENT
        _OVERRIDES.append(scope)
        _GLOBAL_CLIENT = client
    try:
        yield client
    finally:
        with _GLOBAL_LOCK:
            for i in range(len(_OVERRIDES) - 1, -1, -1):
                if _OVERRIDES[i] is scope:
                    del _OVERRIDES[i]
                    break
            if _OVERRIDES:
                _GLOBAL_CLIENT = _OVERRIDES[-1][0]
            else:
                _GLOBAL_CLIENT, _OVERRIDE_BASE = _OVERRIDE_BASE, None
