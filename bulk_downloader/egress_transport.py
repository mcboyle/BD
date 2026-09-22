"""Row 1027: Decoupling Egress Transport Abstraction & Dual-Client Unification (EgressTransport).

Provides a decoupled transport interface, response abstractions, and unified client execution
across standard and guarded proxy egress channels, wrapping bulk_downloader.ssrf_transport.
"""
from __future__ import annotations

import abc
import contextlib
import json
import logging
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


@dataclass
class EgressTransportConfig:
    proxy_url: Optional[str] = None
    timeout: float = 30.0
    verify_ssl: bool = True
    user_agent: str = "BulkDownloader/3.66"
    headers: Dict[str, str] = field(default_factory=dict)
    policy: str = PINNED


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
    ) -> None:
        self.policy = policy
        self.verify_ssl = verify_ssl
        self.proxy_url = proxy_url

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
        t = timeout if timeout is not None else 30.0

        try:
            resp = self._client.request(
                method=method.upper(),
                url=url,
                headers=req_headers,
                content=data,
                timeout=t,
            )
            return EgressResponse(
                status_code=resp.status_code,
                headers=dict(resp.headers.items()),
                content=resp.content,
                url=str(resp.url),
            )
        except (GuardedTransportRefused, SSRFBlocked) as exc:
            log.warning("Guarded transport refused %s: %s", url, exc)
            raise EgressTransportError(f"SSRF refused: {exc}", url=url, original_error=exc) from exc
        except (httpx.RequestError, OSError) as exc:
            log.warning("EgressTransport error for %s: %s", url, exc)
            raise EgressTransportError(f"Transport failure: {exc}", url=url, original_error=exc) from exc

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

        t = timeout if timeout is not None else self.config.timeout
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


def get_unified_egress_client() -> UnifiedEgressClient:
    global _GLOBAL_CLIENT
    if _GLOBAL_CLIENT is None:
        _GLOBAL_CLIENT = UnifiedEgressClient()
    return _GLOBAL_CLIENT


@contextlib.contextmanager
def temporary_egress_client(client: UnifiedEgressClient) -> Iterator[UnifiedEgressClient]:
    """Scoped context manager for temporarily overriding the process-global client."""
    global _GLOBAL_CLIENT
    previous = _GLOBAL_CLIENT
    _GLOBAL_CLIENT = client
    try:
        yield client
    finally:
        _GLOBAL_CLIENT = previous
