"""tests/test_row888_socks5_pool.py

Tests for Row 888: SOCKS5-PROXY-POOL-ROTATION-FOR-LOAD-DISTRIBUTION
Verifies:
(1) round-robin request distribution across active proxy endpoints
(2) automatic health-check removal of dead proxies
(3) direct connection fallback if entire pool is exhausted
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import socket
import threading
from typing import Optional
from unittest import mock
import pytest

from bulk_downloader.socks5_pool import (
    ProxyEndpoint,
    Socks5ProxyPool,
    create_proxy_pool,
)


class TestSocks5ProxyPoolRotation:
    """Acceptance criterion 1: round-robin request distribution across active proxy endpoints."""

    def test_round_robin_distribution(self):
        pool = Socks5ProxyPool([1080, 1081, 1082])
        assert pool.total_count == 3
        assert pool.active_count == 3

        # Call get_next_proxy() repeatedly and verify cycle
        observed = [pool.get_next_proxy() for _ in range(6)]
        expected = [
            "socks5://127.0.0.1:1080",
            "socks5://127.0.0.1:1081",
            "socks5://127.0.0.1:1082",
            "socks5://127.0.0.1:1080",
            "socks5://127.0.0.1:1081",
            "socks5://127.0.0.1:1082",
        ]
        assert observed == expected

    def test_round_robin_with_urls_and_hosts(self):
        endpoints = [
            "socks5://127.0.0.1:9050",
            "socks5h://10.0.70.10:1080",
            "127.0.0.1:9052",
        ]
        pool = Socks5ProxyPool(endpoints)
        assert pool.active_count == 3

        p1 = pool.get_next_proxy()
        p2 = pool.get_next_proxy()
        p3 = pool.get_next_proxy()
        p4 = pool.get_next_proxy()

        assert p1 == "socks5://127.0.0.1:9050"
        assert p2 == "socks5h://10.0.70.10:1080"
        assert p3 == "socks5://127.0.0.1:9052"
        assert p4 == "socks5://127.0.0.1:9050"

    def test_concurrent_round_robin_thread_safety(self):
        pool = Socks5ProxyPool([1080, 1081, 1082, 1083])
        results = []
        lock = threading.Lock()

        def worker():
            for _ in range(50):
                proxy = pool.get_next_proxy()
                with lock:
                    results.append(proxy)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 400
        # Each proxy should be called approximately equally (exactly 100 times)
        for port in [1080, 1081, 1082, 1083]:
            url = f"socks5://127.0.0.1:{port}"
            assert results.count(url) == 100


class TestSocks5ProxyPoolHealthCheck:
    """Acceptance criterion 2: automatic health-check removal of dead proxies."""

    def test_manual_mark_dead_removes_from_rotation(self):
        pool = Socks5ProxyPool([1080, 1081, 1082])
        assert pool.active_count == 3

        # Mark 1081 dead
        pool.mark_dead("socks5://127.0.0.1:1081")
        assert pool.active_count == 2
        assert pool.dead_count == 1

        # Subsequent rotations must only include 1080 and 1082
        observed = [pool.get_next_proxy() for _ in range(4)]
        assert observed == [
            "socks5://127.0.0.1:1080",
            "socks5://127.0.0.1:1082",
            "socks5://127.0.0.1:1080",
            "socks5://127.0.0.1:1082",
        ]

    def test_failure_threshold_removal(self):
        pool = Socks5ProxyPool([1080, 1081], max_failures=2)

        # 1 failure on 1080 does not remove it yet
        pool.record_failure("socks5://127.0.0.1:1080")
        assert pool.active_count == 2

        # 2nd failure marks it dead and removes it
        pool.record_failure("socks5://127.0.0.1:1080")
        assert pool.active_count == 1
        assert pool.dead_count == 1

        # Only 1081 is returned
        assert pool.get_next_proxy() == "socks5://127.0.0.1:1081"
        assert pool.get_next_proxy() == "socks5://127.0.0.1:1081"

    def test_health_check_socket_probing(self):
        pool = Socks5ProxyPool([1080, 1081], check_timeout=0.2)

        # Mock socket to succeed for 1080 and fail for 1081
        def fake_create_connection(address, timeout=None):
            host, port = address
            if port == 1080:
                mock_sock = mock.MagicMock(spec=socket.socket)
                return mock_sock
            raise ConnectionRefusedError(f"Connection refused to {address}")

        with mock.patch("socket.create_connection", side_effect=fake_create_connection):
            results = pool.check_all_health()
            assert results["socks5://127.0.0.1:1080"] is True
            assert results["socks5://127.0.0.1:1081"] is False

        assert pool.active_count == 1
        assert pool.dead_count == 1
        assert pool.get_next_proxy() == "socks5://127.0.0.1:1080"

    def test_revive_healthy_proxy(self):
        pool = Socks5ProxyPool([1080, 1081])
        pool.mark_dead("socks5://127.0.0.1:1080")
        assert pool.active_count == 1

        # Mark healthy again
        pool.mark_healthy("socks5://127.0.0.1:1080")
        assert pool.active_count == 2
        assert pool.dead_count == 0


class TestSocks5ProxyPoolDirectFallback:
    """Acceptance criterion 3: direct connection fallback if entire pool is exhausted."""

    def test_fallback_when_all_proxies_dead(self):
        pool = Socks5ProxyPool([1080, 1081], fallback_to_direct=True)
        pool.mark_dead("socks5://127.0.0.1:1080")
        pool.mark_dead("socks5://127.0.0.1:1081")

        assert pool.active_count == 0
        assert pool.dead_count == 2
        assert pool.is_exhausted is True

        # Fallback returns None (meaning direct outbound connection)
        proxy = pool.get_next_proxy()
        assert proxy is None

    def test_fallback_when_empty_initial_pool(self):
        pool = Socks5ProxyPool([], fallback_to_direct=True)
        assert pool.total_count == 0
        assert pool.is_exhausted is True
        assert pool.get_next_proxy() is None

    def test_no_fallback_raises_exhausted_error(self):
        pool = Socks5ProxyPool([1080], fallback_to_direct=False)
        pool.mark_dead("socks5://127.0.0.1:1080")

        from bulk_downloader.socks5_pool import ProxyPoolExhaustedError
        with pytest.raises(ProxyPoolExhaustedError):
            pool.get_next_proxy()

    def test_recovery_from_exhaustion(self):
        pool = Socks5ProxyPool([1080], fallback_to_direct=True)
        pool.mark_dead("socks5://127.0.0.1:1080")
        assert pool.get_next_proxy() is None

        # Re-enable the proxy
        pool.mark_healthy("socks5://127.0.0.1:1080")
        assert pool.is_exhausted is False
        assert pool.get_next_proxy() == "socks5://127.0.0.1:1080"


class TestProxyPoolFactoryAndHelpers:
    """Test helper functions and environmental initialization."""

    def test_create_proxy_pool_from_string_list(self):
        pool = create_proxy_pool("1080,1081,1082")
        assert pool.total_count == 3
        assert pool.get_next_proxy() == "socks5://127.0.0.1:1080"

    def test_create_proxy_pool_empty_env(self):
        pool = create_proxy_pool("")
        assert pool.total_count == 0
        assert pool.get_next_proxy() is None

    def test_endpoint_addition_and_removal(self):
        pool = Socks5ProxyPool([1080])
        assert pool.total_count == 1

        ep2 = pool.add_endpoint(1081)
        assert pool.total_count == 2
        assert pool.active_count == 2
        assert ep2.port == 1081

        # Adding duplicate returns existing
        ep2_dup = pool.add_endpoint("127.0.0.1:1081")
        assert pool.total_count == 2

        removed = pool.remove_endpoint(1080)
        assert removed is True
        assert pool.total_count == 1
        assert pool.get_next_proxy() == "socks5://127.0.0.1:1081"

    def test_success_record_resets_failures(self):
        pool = Socks5ProxyPool([1080], max_failures=3)
        pool.record_failure(1080)
        pool.record_failure(1080)
        ep = pool._endpoints[0]
        assert ep.failure_count == 2
        assert ep.is_healthy is True

        pool.record_success(1080)
        assert ep.failure_count == 0
        assert ep.success_count == 1

    def test_pool_reset(self):
        pool = Socks5ProxyPool([1080, 1081], max_failures=1)
        pool.mark_dead(1080)
        pool.mark_dead(1081)
        assert pool.is_exhausted is True

        pool.reset()
        assert pool.is_exhausted is False
        assert pool.active_count == 2
        assert pool.dead_count == 0

    def test_live_ephemeral_socket_health_check(self):
        """Test health check against real listening socket vs unused closed port."""
        # Create a live listening socket on loopback
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        _, open_port = server_sock.getsockname()

        # Pick an unused port by binding and immediately closing
        temp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        temp_sock.bind(("127.0.0.1", 0))
        _, closed_port = temp_sock.getsockname()
        temp_sock.close()

        try:
            pool = Socks5ProxyPool([open_port, closed_port], check_timeout=0.5)
            health = pool.check_all_health()
            assert health[f"socks5://127.0.0.1:{open_port}"] is True
            assert health[f"socks5://127.0.0.1:{closed_port}"] is False
            assert pool.active_count == 1
            assert pool.dead_count == 1
            assert pool.get_next_proxy() == f"socks5://127.0.0.1:{open_port}"
        finally:
            server_sock.close()

