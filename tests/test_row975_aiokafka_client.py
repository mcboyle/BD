"""Row 975: Enterprise Distributed Event Streaming Client Modernization (faststream / aiokafka).

Provides modernized asynchronous, LAN-fenced event streaming client,
FastStream broker routing adapter, and coroutine-native completion publishing.
Exercised through production callers: publish_download_completion and diagnostics_bundle.

RED on baseline: publish_download_completion lacks modern streamer routing and
diagnostics_bundle lacks streaming metadata (fails with AssertionError, NOT ImportError).
RED on fail-open: with aiokafka absent, publish_download_completion returns False, NOT True (O1224).
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List
import pytest

BD_GATE_SCOPE = "repo-wide"


def test_diagnostics_bundle_streaming_metadata_contract():
    """Verify diagnostics_bundle includes streaming_metadata through production caller."""
    from bulk_downloader import diagnostics_bundle

    snap = diagnostics_bundle.bundle()
    assert "streaming_metadata" in snap, (
        "Baseline diagnostics bundle lacks streaming metadata"
    )
    meta = snap["streaming_metadata"]
    assert "aiokafka_capable" in meta
    assert "faststream_adapter" in meta
    assert "cluster_lan_fenced" in meta


def test_publish_download_completion_routes_via_modern_streamer(monkeypatch):
    """Verify production caller publish_download_completion routes to modern event streamer."""
    from bulk_downloader import events

    sent: List[tuple[str, bytes]] = []

    class MockStreamer:
        async def send(self, topic: str, payload: bytes):
            sent.append((topic, payload))

    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "10.0.70.25:9092")
    cfg = {"kafka_event_streaming_enabled": True}

    try:
        ok = events.publish_download_completion(
            cfg,
            downloaded_bytes=4096,
            duration_seconds=1.5,
            site_id="site-modern",
            status="completed",
            streamer=MockStreamer(),
        )
    except TypeError:
        ok = False

    assert ok is True, (
        "Baseline publish_download_completion lacks modern event streamer routing"
    )
    assert len(sent) == 1
    topic, payload = sent[0]
    assert topic == events.KAFKA_TOPIC
    data = json.loads(payload.decode("utf-8"))
    assert data["downloaded_bytes"] == 4096
    assert data["site_id"] == "site-modern"
    assert data["status"] == "completed"


def test_publish_download_completion_faststream_adapter(monkeypatch):
    """Verify production caller publish_download_completion routes to FastStream adapter."""
    from bulk_downloader import events

    received: List[Dict[str, Any]] = []

    class MockProducer:
        async def send_and_wait(self, topic: str, value: bytes) -> None:
            pass

    adapter = events.FastStreamKafkaAdapter(
        bootstrap_servers="10.0.70.25:9092",
        producer_factory=lambda **kw: MockProducer(),
    )

    @adapter.subscriber(events.KAFKA_TOPIC)
    async def handle_event(msg: Dict[str, Any]):
        received.append(msg)

    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "10.0.70.25:9092")
    cfg = {"kafka_event_streaming_enabled": True}

    try:
        ok = events.publish_download_completion(
            cfg,
            downloaded_bytes=8192,
            duration_seconds=3.0,
            site_id="site-faststream",
            status="completed",
            streamer=adapter,
        )
    except TypeError:
        ok = False

    assert ok is True, (
        "Baseline publish_download_completion lacks FastStream adapter routing"
    )
    assert len(received) == 1
    assert received[0]["site_id"] == "site-faststream"
    assert received[0]["downloaded_bytes"] == 8192


def test_publish_download_completion_without_aiokafka_must_not_claim_success(monkeypatch):
    """Verify publish_download_completion does NOT fail open when aiokafka is absent (O1224)."""
    from bulk_downloader import events

    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "10.0.70.25:9092")
    cfg = {"kafka_event_streaming_enabled": True, "use_async_streamer": True}

    ok = events.publish_download_completion(
        cfg,
        downloaded_bytes=1024,
        duration_seconds=0.5,
        site_id="site-no-aiokafka",
        status="completed",
    )
    assert ok is False, (
        "O1224 violation: publish_download_completion returned True when aiokafka is absent"
    )


def test_faststream_adapter_without_aiokafka_must_not_claim_success(monkeypatch):
    """Verify FastStream adapter publish rejects when aiokafka is absent and no producer given."""
    from bulk_downloader import events

    adapter = events.FastStreamKafkaAdapter(bootstrap_servers="10.0.70.25:9092")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "10.0.70.25:9092")
    cfg = {"kafka_event_streaming_enabled": True}

    ok = events.publish_download_completion(
        cfg,
        downloaded_bytes=1024,
        duration_seconds=0.5,
        site_id="site-faststream-fallback",
        status="completed",
        streamer=adapter,
    )
    assert ok is False, (
        "O1224 violation: FastStream adapter claimed success when aiokafka is absent"
    )


def test_publish_download_completion_rejects_external_brokers(monkeypatch):
    """Verify production caller strictly enforces cluster LAN egress fence."""
    from bulk_downloader import events

    # External IP must fail egress check and not publish
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "8.8.8.8:9092")
    cfg = {"kafka_event_streaming_enabled": True, "use_async_streamer": True}

    ok = events.publish_download_completion(
        cfg,
        downloaded_bytes=1024,
        duration_seconds=0.5,
        site_id="site-external",
        status="completed",
    )
    assert ok is False


def test_streaming_metadata_truthful_probe():
    """Verify get_streaming_client_metadata truthfulness without failing open (O1224)."""
    from bulk_downloader import events

    assert hasattr(events, "get_streaming_client_metadata"), (
        "Baseline events module lacks get_streaming_client_metadata"
    )
    meta = events.get_streaming_client_metadata()
    assert isinstance(meta, dict)
    assert meta["cluster_lan_fenced"] is True
    # If aiokafka is not installed in environment, must report False, not True
    import importlib.util
    installed = importlib.util.find_spec("aiokafka") is not None
    assert meta["aiokafka_capable"] == installed
    assert meta["backend"] == ("aiokafka" if installed else "fallback")


def test_async_publish_download_completion_coroutine(monkeypatch):
    """Verify coroutine-native async_publish_download_completion."""
    from bulk_downloader import events

    assert hasattr(events, "async_publish_download_completion"), (
        "Baseline events module lacks async_publish_download_completion"
    )

    published: List[Dict[str, Any]] = []

    class MockAsyncStreamer:
        async def send(self, topic: str, payload: bytes):
            published.append(json.loads(payload.decode("utf-8")))

    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "10.0.70.25:9092")
    cfg = {"kafka_event_streaming_enabled": True}

    res = asyncio.run(
        events.async_publish_download_completion(
            cfg,
            downloaded_bytes=2048,
            duration_seconds=1.0,
            site_id="site-async",
            status="completed",
            streamer=MockAsyncStreamer(),
        )
    )
    assert res is True
    assert len(published) == 1
    assert published[0]["site_id"] == "site-async"
