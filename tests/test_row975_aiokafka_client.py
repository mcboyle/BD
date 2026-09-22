"""Row 975: Enterprise Distributed Event Streaming Client Modernization (faststream / aiokafka).

Provides asynchronous, LAN-fenced event streaming client (aiokafka compatible),
FastStream broker routing adapter, and coroutine-native completion publishing.

RED on baseline: AIOKafkaEventStreamer, FastStreamKafkaAdapter,
async_publish_download_completion, and get_streaming_client_metadata do not exist.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

BD_GATE_SCOPE = "repo-wide"


def test_aiokafka_client_metadata_and_exports():
    """Verify modern event streaming client exports and metadata."""
    from bulk_downloader.events import (
        AIOKafkaEventStreamer,
        FastStreamKafkaAdapter,
        async_publish_download_completion,
        get_streaming_client_metadata,
    )

    meta = get_streaming_client_metadata()
    assert isinstance(meta, dict)
    assert meta.get("aiokafka_capable") is True
    assert meta.get("faststream_adapter") is True
    assert meta.get("cluster_lan_fenced") is True
    assert meta.get("schema_version") == 1
    assert callable(AIOKafkaEventStreamer)
    assert callable(FastStreamKafkaAdapter)
    assert callable(async_publish_download_completion)


def test_aiokafka_streamer_strictly_enforces_cluster_lan_fence():
    """Verify AIOKafkaEventStreamer rejects external brokers and accepts cluster LAN."""
    from bulk_downloader.events import AIOKafkaEventStreamer

    # External IP must raise ValueError
    with pytest.raises(ValueError, match="Cluster LAN fence violation"):
        AIOKafkaEventStreamer(bootstrap_servers="8.8.8.8:9092")

    with pytest.raises(ValueError, match="Cluster LAN fence violation"):
        AIOKafkaEventStreamer(bootstrap_servers="192.168.1.1:9092")

    with pytest.raises(ValueError, match="Cluster LAN fence violation"):
        AIOKafkaEventStreamer(bootstrap_servers="10.0.70.25:99999")

    # Cluster LAN valid endpoint must succeed
    streamer = AIOKafkaEventStreamer(bootstrap_servers="10.0.70.25:9092")
    assert streamer.bootstrap_servers == "10.0.70.25:9092"
    assert streamer.is_running is False


def test_aiokafka_streamer_lifecycle_and_send():
    """Verify AIOKafkaEventStreamer start, async context manager, send, and stop."""
    from bulk_downloader.events import AIOKafkaEventStreamer, KAFKA_TOPIC

    sent_records: List[tuple[str, bytes]] = []

    class MockAIOProducer:
        def __init__(self, **kwargs):
            self.started = False
            self.stopped = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

        async def send_and_wait(self, topic: str, value: bytes):
            sent_records.append((topic, value))

    async def run_lifecycle():
        streamer = AIOKafkaEventStreamer(
            bootstrap_servers="10.0.70.25:9092",
            producer_factory=lambda **kw: MockAIOProducer(**kw),
        )
        assert streamer.is_running is False

        async with streamer:
            assert streamer.is_running is True
            payload = b'{"event":"test"}'
            await streamer.send(KAFKA_TOPIC, payload)

        assert streamer.is_running is False

    asyncio.run(run_lifecycle())
    assert len(sent_records) == 1
    assert sent_records[0] == (KAFKA_TOPIC, b'{"event":"test"}')


def test_async_publish_download_completion_flow(monkeypatch):
    """Verify async_publish_download_completion with cluster LAN configuration."""
    from bulk_downloader.events import async_publish_download_completion, KAFKA_TOPIC

    published: List[Dict[str, Any]] = []

    class MockStreamer:
        def __init__(self):
            self.is_running = True

        async def send(self, topic: str, payload: bytes):
            published.append({"topic": topic, "data": json.loads(payload.decode("utf-8"))})

    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "10.0.70.25:9092")

    mock_client = MockStreamer()

    # Disabled site config returns False
    res_disabled = asyncio.run(
        async_publish_download_completion(
            {"kafka_event_streaming_enabled": False},
            downloaded_bytes=2048,
            duration_seconds=1.5,
            site_id="site-disabled",
            status="completed",
            streamer=mock_client,
        )
    )
    assert res_disabled is False
    assert len(published) == 0

    # Enabled site config returns True and publishes valid JSON event
    res_enabled = asyncio.run(
        async_publish_download_completion(
            {"kafka_event_streaming_enabled": True},
            downloaded_bytes=4096,
            duration_seconds=2.0,
            site_id="faststream-site",
            status="completed",
            streamer=mock_client,
        )
    )
    assert res_enabled is True
    assert len(published) == 1
    record = published[0]
    assert record["topic"] == KAFKA_TOPIC
    event_data = record["data"]
    assert event_data["event_type"] == "download.completed"
    assert event_data["schema_version"] == 1
    assert event_data["downloaded_bytes"] == 4096
    assert event_data["duration_seconds"] == 2.0
    assert event_data["site_id"] == "faststream-site"
    assert event_data["status"] == "completed"


def test_async_publish_resilience_on_broker_failure(monkeypatch):
    """Verify async publish swallows broker exceptions and returns False."""
    from bulk_downloader.events import async_publish_download_completion

    class FailingStreamer:
        async def send(self, topic: str, payload: bytes):
            raise ConnectionError("Kafka cluster unreachable")

    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "10.0.70.25:9092")

    res = asyncio.run(
        async_publish_download_completion(
            {"kafka_event_streaming_enabled": True},
            downloaded_bytes=100,
            duration_seconds=0.1,
            site_id="error-site",
            status="failed",
            streamer=FailingStreamer(),
        )
    )
    assert res is False


def test_faststream_adapter_publish_and_subscription():
    """Verify FastStreamKafkaAdapter message routing and subscription pattern."""
    from bulk_downloader.events import FastStreamKafkaAdapter, KAFKA_TOPIC

    adapter = FastStreamKafkaAdapter(bootstrap_servers="10.0.70.25:9092")
    received: List[Any] = []

    @adapter.subscriber(KAFKA_TOPIC)
    async def on_download(message: Dict[str, Any]):
        received.append(message)

    async def run_broker():
        async with adapter:
            await adapter.publish(KAFKA_TOPIC, {"sample": "metric", "count": 10})

    asyncio.run(run_broker())
    assert len(received) == 1
    assert received[0] == {"sample": "metric", "count": 10}
