"""tests/test_row873_event_gateway.py - Row 873: REDIS-CLUSTER-PUBSUB-LIVE-EVENT-BROADCASTER.

Verifies:
(1) Queue state events published to Redis channel/stream with valid schema
(2) Connected WebSocket clients receive updates within 10ms
(3) Automatic reconnection on network drop
(4) E1: App/queue integration emits real queue change notifications
(5) E2: Redis error responses (-NOAUTH, -ERR) and clean EOF raise rather than returning fake success
(6) E3: Slow/blocked client writer does not stall healthy clients
(7) E4: UTF-8 multi-byte channel names declare exact byte lengths in RESP protocol
(8) Negative control and exact-count assertions
"""
from __future__ import annotations

import asyncio
import importlib
import json
import time
from typing import Any

import pytest

try:
    event_gateway = importlib.import_module("bulk_downloader.event_gateway")
except ImportError:
    event_gateway = None

BD_GATE_SCOPE = "module"


def _run(coro: Any, timeout: float = 10.0) -> Any:
    """Run an async coroutine with a timeout ceiling."""
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


def test_event_gateway_module_implemented():
    """Verify that event_gateway module is importable and exposes canonical APIs."""
    assert event_gateway is not None, (
        "bulk_downloader.event_gateway module not implemented; "
        "Redis Pub/Sub WebSocket event gateway required"
    )
    for expected_attr in (
        "EventGateway",
        "EventPublisher",
        "RedisPubSubClient",
        "WebSocketClient",
        "notify_queue_event",
        "publish_queue_sync",
    ):
        assert hasattr(event_gateway, expected_attr), (
            f"event_gateway missing expected attribute: {expected_attr}"
        )


def test_queue_state_events_published_to_redis():
    """Verify queue state events are published to Redis channel with valid schema (Acceptance 1)."""
    assert event_gateway is not None, "event_gateway not implemented"

    async def _test():
        channel = f"bd:events:queue_test_{int(time.time()*1000)}"
        publisher = event_gateway.EventPublisher(host="127.0.0.1", port=6379)
        await publisher.connect()

        received_events: list[dict[str, Any]] = []
        subscriber = event_gateway.RedisPubSubClient(host="127.0.0.1", port=6379)
        await subscriber.connect()

        async def on_message(ch: str, msg: str):
            received_events.append(json.loads(msg))

        await subscriber.subscribe(channel, on_message)
        await asyncio.sleep(0.05)

        payload = {
            "event": "queue_update",
            "queue_len": 42,
            "active_downloads": 3,
            "timestamp": time.time(),
        }
        sent = await publisher.publish(channel, payload)
        assert sent >= 1, "Published event must reach subscriber"

        for _ in range(50):
            if received_events:
                break
            await asyncio.sleep(0.01)

        assert len(received_events) == 1
        rec = received_events[0]
        assert rec["event"] == "queue_update"
        assert rec["queue_len"] == 42
        assert rec["active_downloads"] == 3

        await subscriber.close()
        await publisher.close()

    _run(_test())


def test_connected_clients_receive_updates_within_10ms():
    """Verify connected WebSocket clients receive broadcast updates within 10ms (Acceptance 2)."""
    assert event_gateway is not None, "event_gateway not implemented"

    async def _test():
        # Pick dynamic free port
        gateway = event_gateway.EventGateway(host="127.0.0.1", port=0, redis_host="127.0.0.1", redis_port=6379)
        await gateway.start()
        port = gateway.bound_port
        channel = f"bd:events:live_test_{int(time.time()*1000)}"

        client = event_gateway.WebSocketClient(f"ws://127.0.0.1:{port}")
        await client.connect()
        await asyncio.sleep(0.05)

        t_publish = time.time()
        event_data = {
            "event": "download_progress",
            "scene_id": "scene-873",
            "progress_pct": 78.5,
            "t_publish": t_publish,
        }
        await gateway.publish(channel, event_data)

        raw_msg = await client.recv(timeout=1.0)
        t_recv = time.time()
        latency_ms = (t_recv - t_publish) * 1000.0

        msg = json.loads(raw_msg)
        assert msg["event"] == "download_progress"
        assert msg["scene_id"] == "scene-873"
        assert msg["progress_pct"] == 78.5
        assert latency_ms < 10.0, f"Delivery latency exceeded 10ms: {latency_ms:.2f}ms"

        await client.close()
        await gateway.close()

    _run(_test())


def test_automatic_reconnection_on_network_drop():
    """Verify automatic reconnection on network drop (Acceptance 3)."""
    assert event_gateway is not None, "event_gateway not implemented"

    async def _test():
        channel = f"bd:events:reconnect_test_{int(time.time()*1000)}"
        client = event_gateway.RedisPubSubClient(
            host="127.0.0.1", port=6379, auto_reconnect=True, reconnect_interval=0.1
        )
        await client.connect()

        received: list[str] = []

        async def callback(ch: str, msg: str):
            received.append(msg)

        await client.subscribe(channel, callback)
        await asyncio.sleep(0.05)

        # Drop underlying writer socket to simulate drop
        assert client._writer is not None
        client._writer.close()
        try:
            await client._writer.wait_closed()
        except Exception:
            pass

        # Wait for auto-reconnect loop to restore connection
        for _ in range(50):
            if client.is_connected:
                break
            await asyncio.sleep(0.05)

        assert client.is_connected, "Client must automatically reconnect after drop"

        # Publish event after reconnect and verify delivery
        pub = event_gateway.EventPublisher(host="127.0.0.1", port=6379)
        await pub.connect()
        await pub.publish(channel, {"reconnected": True})

        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.02)

        assert len(received) == 1
        assert "reconnected" in received[0]

        await client.close()
        await pub.close()

    _run(_test())


def test_redis_error_and_eof_not_suppressed_as_success():
    """Verify E2: Redis error responses (-NOAUTH, -ERR) and EOF raise rather than returning 1."""
    assert event_gateway is not None, "event_gateway not implemented"

    async def _test():
        pub = event_gateway.EventPublisher(host="127.0.0.1", port=6379)

        # Mock reader returning -NOAUTH error
        class MockReaderErr:
            async def readline(self):
                return b"-NOAUTH Authentication required.\r\n"

        class MockWriter:
            def is_closing(self):
                return False
            def write(self, data):
                pass
            async def drain(self):
                pass

        pub._reader = MockReaderErr()
        pub._writer = MockWriter()

        with pytest.raises(Exception) as exc_info:
            await pub.publish("test_chan", {"data": 1})
        assert "NOAUTH" in str(exc_info.value) or "RedisError" in type(exc_info.value).__name__

        # Mock reader returning clean EOF (b"")
        class MockReaderEOF:
            async def readline(self):
                return b""

        pub._reader = MockReaderEOF()
        with pytest.raises(ConnectionResetError):
            await pub.publish("test_chan", {"data": 1})

    _run(_test())


def test_slow_client_isolated_from_healthy_clients():
    """Verify E3: A stalled/slow client writer does not block or delay broadcast to healthy clients."""
    assert event_gateway is not None, "event_gateway not implemented"

    async def _test():
        gateway = event_gateway.EventGateway(host="127.0.0.1", port=0)
        await gateway.start()
        port = gateway.bound_port

        # Connect healthy client 1
        c1 = event_gateway.WebSocketClient(f"ws://127.0.0.1:{port}")
        await c1.connect()

        # Connect healthy client 2
        c2 = event_gateway.WebSocketClient(f"ws://127.0.0.1:{port}")
        await c2.connect()
        await asyncio.sleep(0.05)

        # Inject simulated blocked client whose writer.drain hangs
        class BlockedWriter:
            def __init__(self):
                self.written = 0
            def is_closing(self):
                return False
            def write(self, data):
                self.written += len(data)
            async def drain(self):
                # Hang indefinitely or longer than test timeout
                await asyncio.sleep(10.0)

        blocked_conn = event_gateway.WebSocketConnection(
            reader=None, writer=BlockedWriter(), path="/"
        )
        gateway._clients.add(blocked_conn)

        # Broadcast should complete rapidly without waiting for blocked client
        t0 = time.time()
        await gateway.broadcast({"ping": "fast"})
        elapsed = time.time() - t0

        assert elapsed < 0.2, f"Broadcast stalled on slow client ({elapsed:.3f}s)"

        msg1 = await c1.recv(timeout=1.0)
        msg2 = await c2.recv(timeout=1.0)
        assert json.loads(msg1)["ping"] == "fast"
        assert json.loads(msg2)["ping"] == "fast"

        await c1.close()
        await c2.close()
        await gateway.close()

    _run(_test())


def test_utf8_channel_byte_length_encoding():
    """Verify E4: Multi-byte UTF-8 channel names declare exact byte count, not character count."""
    assert event_gateway is not None, "event_gateway not implemented"

    channel_name = "bd:événements"
    char_len = len(channel_name)       # 13 characters
    byte_len = len(channel_name.encode("utf-8"))  # 15 bytes
    assert char_len != byte_len, "Test channel must have multibyte characters"

    cmd = event_gateway._build_subscribe_command(channel_name)
    expected_header = f"${byte_len}\r\n".encode("ascii")
    forbidden_header = f"${char_len}\r\n".encode("ascii")

    assert expected_header in cmd, f"Expected byte length ${byte_len} in command"
    assert forbidden_header not in cmd, f"Found character count ${char_len} in command"


def test_app_queue_integration():
    """Verify E1: Real application integration in app_queue emits event notifications."""
    assert event_gateway is not None, "event_gateway not implemented"

    # Verify event_gateway provides notify_queue_event
    emitted_events: list[tuple[str, dict[str, Any]]] = []

    def mock_listener(evt: str, data: dict[str, Any]):
        emitted_events.append((evt, data))

    event_gateway.register_queue_listener(mock_listener)
    try:
        app_queue = importlib.import_module("bulk_downloader.app_queue")

        class MockRunner:
            def load_urls(self, urls):
                return len(urls), 0, 0

        runners = {"site1": MockRunner()}
        res = app_queue.enqueue_one_url("site1", "https://example.com/video/1", runners=runners)
        assert res["ok"] is True

        assert len(emitted_events) >= 1
        evt_type, evt_data = emitted_events[-1]
        assert evt_type == "queue_add"
        assert evt_data["site_id"] == "site1"
        assert evt_data["url"] == "https://example.com/video/1"
    finally:
        event_gateway.unregister_queue_listener(mock_listener)


def test_negative_control_and_exact_count():
    """Negative control: prove client tracking exact count and dead client pruning."""
    assert event_gateway is not None, "event_gateway not implemented"

    async def _test():
        gateway = event_gateway.EventGateway(host="127.0.0.1", port=0)
        await gateway.start()
        port = gateway.bound_port

        # Connect 3 clients
        clients = [event_gateway.WebSocketClient(f"ws://127.0.0.1:{port}") for _ in range(3)]
        for c in clients:
            await c.connect()
        await asyncio.sleep(0.05)

        # Exact count assertion: exactly 3 active clients
        assert len(gateway._clients) == 3, f"Expected 3 clients, got {len(gateway._clients)}"

        # Close client 0
        await clients[0].close()
        await asyncio.sleep(0.05)

        # Broadcast should prune the dead client and retain exactly 2 healthy clients
        await gateway.broadcast({"status": "active"})
        assert len(gateway._clients) == 2, f"Expected exactly 2 remaining clients, got {len(gateway._clients)}"

        for c in clients[1:]:
            await c.close()
        await gateway.close()

    _run(_test())
