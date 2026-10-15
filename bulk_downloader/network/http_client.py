from __future__ import annotations

import dataclasses
import email.utils
import random
import threading
import time
from collections.abc import Callable
from typing import Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self
from urllib.parse import urlparse

import httpx

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_WRITE_TIMEOUT = 30.0
DEFAULT_POOL_TIMEOUT = 5.0
DEFAULT_MAX_CONNECTIONS = 100
DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20
DEFAULT_MAX_CONNECTIONS_PER_HOST = 10
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})

@dataclasses.dataclass(frozen=True)
class TimeoutConfig:
    connect: float = DEFAULT_CONNECT_TIMEOUT
    read: float = DEFAULT_READ_TIMEOUT
    write: float = DEFAULT_WRITE_TIMEOUT
    pool: float = DEFAULT_POOL_TIMEOUT

    def to_httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(connect=self.connect, read=self.read, write=self.write, pool=self.pool)

@dataclasses.dataclass(frozen=True)
class PoolConfig:
    max_connections: int = DEFAULT_MAX_CONNECTIONS
    max_keepalive_connections: int = DEFAULT_MAX_KEEPALIVE_CONNECTIONS
    max_connections_per_host: int = DEFAULT_MAX_CONNECTIONS_PER_HOST

    def to_httpx_limits(self) -> httpx.Limits:
        return httpx.Limits(max_connections=self.max_connections, max_keepalive_connections=self.max_keepalive_connections)

@dataclasses.dataclass(frozen=True)
class BackoffConfig:
    max_retries: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    factor: float = 2.0
    retryable_codes: frozenset[int] = RETRYABLE_STATUS_CODES

def parse_retry_after(header_value: str | None) -> float | None:
    if not header_value:
        return None
    header_str = header_value.strip()
    try:
        val = float(header_str)
        return max(0.0, val)
    except ValueError:
        pass
    try:
        parsed_date = email.utils.parsedate_to_datetime(header_str)
        delay = parsed_date.timestamp() - time.time()
        return max(0.0, delay)
    except (ValueError, TypeError, OverflowError):
        return None

def compute_full_jitter(attempt: int, base_delay: float=1.0, max_delay: float=30.0, factor: float=2.0, retry_after: float | None=None, rng: Any=None) -> float:
    safe_attempt = max(0, min(attempt, 10))
    cap = min(max_delay, base_delay * factor ** safe_attempt)
    rand_fn = rng if rng is not None else random.uniform
    delay = rand_fn(0.0, cap)
    if retry_after is not None and retry_after > 0:
        delay = max(delay, min(retry_after, max_delay))
    return delay

class _HostConcurrencyTracker:

    def __init__(self, max_per_host: int=DEFAULT_MAX_CONNECTIONS_PER_HOST):
        self.max_per_host = max_per_host
        self._lock = threading.Lock()
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._active_counts: dict[str, int] = {}

    def get_semaphore(self, host: str) -> threading.Semaphore:
        with self._lock:
            if host not in self._semaphores:
                self._semaphores[host] = threading.Semaphore(self.max_per_host)
            return self._semaphores[host]

    def acquire(self, host: str, timeout: float=5.0) -> bool:
        sem = self.get_semaphore(host)
        acquired = sem.acquire(timeout=timeout)
        if acquired:
            with self._lock:
                self._active_counts[host] = self._active_counts.get(host, 0) + 1
        return acquired

    def release(self, host: str) -> None:
        sem = self.get_semaphore(host)
        with self._lock:
            if host in self._active_counts:
                self._active_counts[host] = max(0, self._active_counts[host] - 1)
        sem.release()

    def active_count(self, host: str) -> int:
        with self._lock:
            return self._active_counts.get(host, 0)

class HttpClient:

    def __init__(self, timeout_config: TimeoutConfig | None=None, pool_config: PoolConfig | None=None, backoff_config: BackoffConfig | None=None, sleep_fn: Callable[[float], None]=time.sleep, rng: Any=None):
        self.timeout_config = timeout_config or TimeoutConfig()
        self.pool_config = pool_config or PoolConfig()
        self.backoff_config = backoff_config or BackoffConfig()
        self._sleep_fn = sleep_fn
        self._rng = rng
        self._host_tracker = _HostConcurrencyTracker(self.pool_config.max_connections_per_host)
        self._client = httpx.Client(timeout=self.timeout_config.to_httpx_timeout(), limits=self.pool_config.to_httpx_limits(), follow_redirects=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, url: str, headers: dict[str, str] | None=None, data: Any=None, json: Any=None, params: dict[str, Any] | None=None, timeout: float | None=None) -> httpx.Response:
        parsed = urlparse(url)
        host = (parsed.hostname or "unknown").lower()
        if not self._host_tracker.acquire(host, timeout=self.timeout_config.pool):
            raise httpx.PoolTimeout(f"Connection pool bound exceeded for host {host!r} (limit={self.pool_config.max_connections_per_host})")
        try:
            attempt = 0
            while True:
                req_timeout = timeout or self.timeout_config.read
                try:
                    resp = self._client.request(method=method, url=url, headers=headers, data=data, json=json, params=params, timeout=req_timeout)
                    if resp.status_code in self.backoff_config.retryable_codes and attempt < self.backoff_config.max_retries:
                        attempt += 1
                        retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                        delay = compute_full_jitter(attempt=attempt, base_delay=self.backoff_config.base_delay_s, max_delay=self.backoff_config.max_delay_s, factor=self.backoff_config.factor, retry_after=retry_after, rng=self._rng)
                        self._sleep_fn(delay)
                        continue
                    return resp
                except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
                    if attempt < self.backoff_config.max_retries:
                        attempt += 1
                        delay = compute_full_jitter(attempt=attempt, base_delay=self.backoff_config.base_delay_s, max_delay=self.backoff_config.max_delay_s, factor=self.backoff_config.factor, rng=self._rng)
                        self._sleep_fn(delay)
                        continue
                    raise
        finally:
            self._host_tracker.release(host)

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def head(self, url: str, **kwargs) -> httpx.Response:
        return self.request("HEAD", url, **kwargs)

class AsyncHttpClient:

    def __init__(self, timeout_config: TimeoutConfig | None=None, pool_config: PoolConfig | None=None, backoff_config: BackoffConfig | None=None, sleep_fn: Callable[[float], Any] | None=None, rng: Any=None):
        self.timeout_config = timeout_config or TimeoutConfig()
        self.pool_config = pool_config or PoolConfig()
        self.backoff_config = backoff_config or BackoffConfig()
        self._sleep_fn = sleep_fn
        self._rng = rng
        self._client = httpx.AsyncClient(timeout=self.timeout_config.to_httpx_timeout(), limits=self.pool_config.to_httpx_limits(), follow_redirects=True)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, headers: dict[str, str] | None=None, data: Any=None, json: Any=None, params: dict[str, Any] | None=None, timeout: float | None=None) -> httpx.Response:
        import asyncio
        attempt = 0
        while True:
            req_timeout = timeout or self.timeout_config.read
            try:
                resp = await self._client.request(method=method, url=url, headers=headers, data=data, json=json, params=params, timeout=req_timeout)
                if resp.status_code in self.backoff_config.retryable_codes and attempt < self.backoff_config.max_retries:
                    attempt += 1
                    retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                    delay = compute_full_jitter(attempt=attempt, base_delay=self.backoff_config.base_delay_s, max_delay=self.backoff_config.max_delay_s, factor=self.backoff_config.factor, retry_after=retry_after, rng=self._rng)
                    if self._sleep_fn:
                        await self._sleep_fn(delay)
                    else:
                        await asyncio.sleep(delay)
                    continue
                return resp
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
                if attempt < self.backoff_config.max_retries:
                    attempt += 1
                    delay = compute_full_jitter(attempt=attempt, base_delay=self.backoff_config.base_delay_s, max_delay=self.backoff_config.max_delay_s, factor=self.backoff_config.factor, rng=self._rng)
                    if self._sleep_fn:
                        await self._sleep_fn(delay)
                    else:
                        await asyncio.sleep(delay)
                    continue
                raise

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)
_DEFAULT_CLIENT: HttpClient | None = None
_CLIENT_LOCK = threading.Lock()

def get_default_client() -> HttpClient:
    global _DEFAULT_CLIENT
    with _CLIENT_LOCK:
        if _DEFAULT_CLIENT is None:
            _DEFAULT_CLIENT = HttpClient()
        return _DEFAULT_CLIENT

def request(method: str, url: str, **kwargs) -> httpx.Response:
    return get_default_client().request(method, url, **kwargs)

def get(url: str, **kwargs) -> httpx.Response:
    return get_default_client().get(url, **kwargs)

def post(url: str, **kwargs) -> httpx.Response:
    return get_default_client().post(url, **kwargs)
