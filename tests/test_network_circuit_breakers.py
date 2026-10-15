from __future__ import annotations

from pathlib import Path
from unittest import mock

import httpx
import pytest

from bulk_downloader.download_engine import DownloadEngine
from bulk_downloader.network.http_client import (
    BackoffConfig,
    HttpClient,
    PoolConfig,
    TimeoutConfig,
    _HostConcurrencyTracker,
    compute_full_jitter,
    parse_retry_after,
)
from bulk_downloader.network.url_sanitizer import SSRFBlocked


def test_timeout_config_defaults() -> None:
    cfg = TimeoutConfig()
    assert cfg.connect == 5.0
    assert cfg.read == 30.0
    assert cfg.write == 30.0
    assert cfg.pool == 5.0
    hx_timeout = cfg.to_httpx_timeout()
    assert hx_timeout.connect == 5.0
    assert hx_timeout.read == 30.0

def test_pool_config_defaults() -> None:
    cfg = PoolConfig()
    assert cfg.max_connections == 100
    assert cfg.max_keepalive_connections == 20
    assert cfg.max_connections_per_host == 10
    limits = cfg.to_httpx_limits()
    assert limits.max_connections == 100
    assert limits.max_keepalive_connections == 20

def test_full_jitter_distribution() -> None:
    base = 1.0
    max_delay = 30.0
    factor = 2.0
    delays = [compute_full_jitter(attempt=2, base_delay=base, max_delay=max_delay, factor=factor) for _ in range(50)]
    for d in delays:
        assert 0.0 <= d <= 4.0
    assert len(set(delays)) > 10, "Full Jitter backoff must have non-zero variance"

def test_parse_retry_after() -> None:
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after("12.5") == 12.5
    assert parse_retry_after("invalid") is None

def test_host_concurrency_bounds_enforcement() -> None:
    tracker = _HostConcurrencyTracker(max_per_host=3)
    host = "api.example.com"
    assert tracker.acquire(host, timeout=1.0) is True
    assert tracker.acquire(host, timeout=1.0) is True
    assert tracker.acquire(host, timeout=1.0) is True
    assert tracker.active_count(host) == 3
    assert tracker.acquire(host, timeout=0.05) is False
    tracker.release(host)
    assert tracker.active_count(host) == 2
    assert tracker.acquire(host, timeout=0.05) is True

def test_http_client_429_throttling_retry() -> None:
    attempts = 0
    slept_delays: list[float] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, headers={"Retry-After": "0.01"}, text="Rate limited", request=request)
        return httpx.Response(200, text="Success", request=request)
    transport = httpx.MockTransport(mock_handler)
    client = HttpClient(sleep_fn=slept_delays.append, backoff_config=BackoffConfig(max_retries=3, base_delay_s=0.1, max_delay_s=1.0))
    client._client = httpx.Client(transport=transport)
    resp = client.get("https://api.example.com/data")
    assert resp.status_code == 200
    assert resp.text == "Success"
    assert attempts == 3
    assert len(slept_delays) == 2
    for delay in slept_delays:
        assert delay >= 0.01

def test_http_client_503_throttling_retry() -> None:
    attempts = 0
    slept_delays: list[float] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            return httpx.Response(503, text="Service Unavailable", request=request)
        return httpx.Response(200, text="Recovered", request=request)
    transport = httpx.MockTransport(mock_handler)
    client = HttpClient(sleep_fn=slept_delays.append, backoff_config=BackoffConfig(max_retries=3, base_delay_s=0.05, max_delay_s=0.5))
    client._client = httpx.Client(transport=transport)
    resp = client.get("https://api.example.com/status")
    assert resp.status_code == 200
    assert attempts == 2
    assert len(slept_delays) == 1

def test_download_engine_file_download_with_retry(tmp_path: Path) -> None:
    attempts = 0
    file_content = b"Simulated large binary chunk payload"
    dest_file = tmp_path / "downloads" / "video.mp4"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"}, request=request)
        return httpx.Response(200, content=file_content, request=request)
    transport = httpx.MockTransport(mock_handler)
    engine = DownloadEngine(sleep_fn=lambda d: None, backoff_config=BackoffConfig(max_retries=3, base_delay_s=0.01))
    engine._client._client = httpx.Client(transport=transport)
    with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        res = engine.download_file("https://cdn.example.com/media/test.mp4", dest_file)
    assert res.success is True
    assert res.retries_taken == 1
    assert dest_file.exists()
    assert dest_file.read_bytes() == file_content

def test_download_engine_ssrf_blocked(tmp_path: Path) -> None:
    engine = DownloadEngine()
    dest_file = tmp_path / "leak.txt"
    with pytest.raises(SSRFBlocked):
        engine.download_file("http://127.0.0.1:8080/secret", dest_file)
    assert not dest_file.exists()
