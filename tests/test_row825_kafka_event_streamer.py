"""Row 825: optional, LAN-only Kafka completion-event streamer."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import threading
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
import pytest

BD_GATE_SCOPE = "repo-wide"


def test_events_module_is_discoverable_and_dependency_installed():
    """AC & Remedy E1: Verify kafka package and events module are discoverable."""
    assert importlib.util.find_spec("kafka") is not None, "kafka-python package must be installed in venv"
    assert importlib.util.find_spec("bulk_downloader.events") is not None, "bulk_downloader.events must exist"


def test_completion_event_has_versioned_json_schema():
    """AC 1: JSON event format compliance with versioned schema."""
    from bulk_downloader.events import DownloadCompletionEvent

    event = DownloadCompletionEvent(
        downloaded_bytes=42, duration_seconds=1.25, site_id="site-a", status="completed"
    )
    payload = json.loads(event.to_json())
    assert payload == {
        "event_type": "download.completed",
        "schema_version": 1,
        "downloaded_bytes": 42,
        "duration_seconds": 1.25,
        "site_id": "site-a",
        "status": "completed",
    }


def test_producer_configuration_is_cluster_lan_only_and_port_validated():
    """AC 3 & Review Note: Strictly LAN-only configuration with robust port validation."""
    from bulk_downloader import events

    # Valid cluster LAN endpoints (exact population: 2)
    assert events.kafka_producer_config("10.0.70.25:9092") == {
        "bootstrap_servers": "10.0.70.25:9092"
    }
    assert events.kafka_producer_config("10.0.70.10:9092, 10.0.70.25:9092") == {
        "bootstrap_servers": "10.0.70.10:9092,10.0.70.25:9092"
    }

    # External IPs rejected
    assert events.kafka_producer_config("8.8.8.8:9092") is None
    assert events.kafka_producer_config("1.1.1.1:9092") is None
    assert events.kafka_producer_config("10.0.70.25:9092, 8.8.8.8:9092") is None
    assert events.kafka_producer_config("broker.example.com:9092") is None

    # Invalid port bounds rejected (Reviewer Note: 99999 accepted in old cut)
    assert events.kafka_producer_config("10.0.70.25:99999") is None
    assert events.kafka_producer_config("10.0.70.25:0") is None
    assert events.kafka_producer_config("10.0.70.25:-1") is None
    assert events.kafka_producer_config("10.0.70.25:notaport") is None

    # Exact CLI argv
    assert events.kafka_producer_argv("10.0.70.25:9092") == (
        "kcat", "-P", "-b", "10.0.70.25:9092", "-t", "bd.downloads"
    )
    assert events.kafka_producer_argv("8.8.8.8:9092") is None


def test_discovered_brokers_and_all_connections_are_lan_fenced():
    """Remedy E2: Discovered broker destinations must be strictly fenced to cluster LAN."""
    from bulk_downloader import events

    # Literal cluster LAN addresses pass
    assert events.is_cluster_lan_endpoint("10.0.70.25", 9092) is True
    assert events.is_cluster_lan_endpoint("10.0.70.1", 9092) is True

    # External addresses fail
    assert events.is_cluster_lan_endpoint("8.8.8.8", 9092) is False
    assert events.is_cluster_lan_endpoint("10.0.71.1", 9092) is False
    assert events.is_cluster_lan_endpoint("192.168.1.1", 9092) is False

    # Invalid port bounds fail
    assert events.is_cluster_lan_endpoint("10.0.70.25", 99999) is False
    assert events.is_cluster_lan_endpoint("10.0.70.25", 0) is False

    # Test socket-level fence interception
    events._install_lan_socket_fence()
    from kafka.net.inet import KafkaNetSocket
    from kafka.errors import KafkaConnectionError

    net_sock = KafkaNetSocket(None)
    # External IP attempt must raise KafkaConnectionError
    external_addrinfo = (2, 1, 6, "", ("8.8.8.8", 9092))
    with pytest.raises(KafkaConnectionError, match="Cluster LAN fence violation"):
        import asyncio
        asyncio.run(net_sock.connect(None, external_addrinfo))


def test_publish_is_nonblocking_and_rejects_external_bootstrap(monkeypatch):
    """AC 2: Non-blocking execution even under broker failure or slow connection."""
    from bulk_downloader import events

    calls: List[str] = []
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "8.8.8.8:9092")
    assert events.publish_download_completion(
        {"kafka_event_streaming_enabled": True},
        downloaded_bytes=1,
        duration_seconds=0.1,
        site_id="site-a",
        status="completed",
        producer_factory=lambda _cfg: calls.append("called"),
    ) is False
    assert calls == [], "external bootstrap must not create a producer"

    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "10.0.70.25:9092")

    entered = threading.Event()
    release = threading.Event()

    class SlowBrokenProducer:
        def send(self, *_args, **_kwargs):
            entered.set()
            release.wait(timeout=1)
            raise RuntimeError("broker unavailable")

    assert events.publish_download_completion(
        {"kafka_event_streaming_enabled": True},
        downloaded_bytes=1,
        duration_seconds=0.1,
        site_id="site-a",
        status="completed",
        producer_factory=lambda _cfg: SlowBrokenProducer(),
    ) is True
    assert entered.wait(timeout=0.5), "producer worker did not start"
    release.set()


def test_config_default_keeps_streaming_off():
    """Verify default site settings keep streaming disabled."""
    from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS

    assert "kafka_event_streaming_enabled" in CFG_FIELDS
    assert DEFAULTS["kafka_event_streaming_enabled"] is False


def test_completion_hook_wired_in_both_transport_and_extractor():
    """Remedy E3: Kafka hook covers both _do_download and _try_spa_api_mp4_extractor."""
    transport_source = (Path(__file__).parents[1] / "bulk_downloader" / "runner_transport.py").read_text(
        encoding="utf-8"
    )
    assert "publish_download_completion(" in transport_source

    extractor_source = (Path(__file__).parents[1] / "bulk_downloader" / "runner_extractors.py").read_text(
        encoding="utf-8"
    )
    assert "publish_download_completion(" in extractor_source


def test_spa_api_extraction_invokes_publisher(tmp_path: Path):
    """Remedy E3: Real probe verifying _try_spa_api_mp4_extractor calls publish_download_completion."""
    from bulk_downloader.runner_extractors import ExtractorsMixin

    published_events: List[Dict[str, Any]] = []

    class DummyRunner(ExtractorsMixin):
        def __init__(self):
            self.site_id = "spa_site"
            self.config = {
                "name": "spa_site",
                "kafka_event_streaming_enabled": True,
                "filename_template": "{filename}{ext}",
                "download_dir": str(tmp_path),
            }
            self.jobs = {}

        def _update_job(self, url, status, msg, filename="", file_size=0):
            self.jobs[url] = {"status": status, "msg": msg, "filename": filename, "file_size": file_size}

        def log_event(self, *args, **kwargs):
            pass

        def _size_on_disk_after_tagging(self, output_path, downloaded_size):
            return downloaded_size

        def _capture_website_title(self, page, url):
            pass

        def _do_direct_http_download(self, **kwargs):
            return True

    runner = DummyRunner()

    target_file = tmp_path / "sample.mp4"
    target_file.write_bytes(b"A" * 1024)

    page = MagicMock()
    page.url = "https://spa.example.com/item/1"
    page.evaluate.return_value = [{"url": "https://cdn.example.com/video.mp4", "tag": "video"}]

    with patch("bulk_downloader.events.publish_download_completion") as mock_publish, \
         patch("bulk_downloader.db.db_log"), \
         patch("bulk_downloader.spa_media_extract.api_candidates", return_value=[{"url": "https://cdn.example.com/video.mp4", "height": 1080, "source": "api"}]), \
         patch("bulk_downloader.spa_media_extract.rank_candidates", return_value=[{"url": "https://cdn.example.com/video.mp4", "height": 1080, "source": "api"}]), \
         patch("bulk_downloader.spa_media_extract.resolve_candidate_url", return_value="https://cdn.example.com/video.mp4"):

        res = runner._try_spa_api_media_extractor("https://spa.example.com/item/1", page)
        assert res is True, "_try_spa_api_media_extractor must return True on success"
        assert mock_publish.called, "publish_download_completion must be called on SPA API completion"
        call_kwargs = mock_publish.call_args[1]
        assert call_kwargs["site_id"] == "spa_site"
        assert call_kwargs["status"] == "completed"


def test_exact_count_and_negative_control():
    """Exact count assertion and negative control proving unconfigured site never publishes."""
    from bulk_downloader import events

    # Negative control: disabled site produces 0 events
    calls = []
    assert events.publish_download_completion(
        {"kafka_event_streaming_enabled": False},
        downloaded_bytes=500,
        duration_seconds=1.0,
        site_id="disabled-site",
        status="completed",
        producer_factory=lambda _cfg: calls.append("called"),
    ) is False
    assert len(calls) == 0, "Disabled streaming must never invoke producer factory"

    # Exact count evaluation over mixed bootstrap candidates
    candidates = [
        ("10.0.70.1:9092", True),
        ("10.0.70.25:9092", True),
        ("10.0.70.99:9092", True),
        ("8.8.8.8:9092", False),
        ("1.1.1.1:9092", False),
        ("10.0.70.25:99999", False),
        ("10.0.70.25:0", False),
        ("not_an_endpoint", False),
    ]
    # Exactly 8 candidates tested: 3 valid LAN, 5 invalid/external
    assert len(candidates) == 8
    valid_count = sum(1 for ep, expected in candidates if (events.kafka_producer_config(ep) is not None) == expected)
    assert valid_count == 8, f"Expected all 8 candidates to match expected validity, got {valid_count}"


def test_default_producer_installs_the_lan_fence_and_builds_a_kafka_producer(monkeypatch):
    """Seam _default_producer@events.py (the publish() default factory): it must install the socket
    fence FIRST and then construct KafkaProducer with the validated config -- an early return here
    would publish through an unfenced, absent producer (self-mutation escape, 2026-09-20)."""
    import sys
    import types
    from bulk_downloader import events

    order: list = []
    monkeypatch.setattr(events, "_install_lan_socket_fence", lambda: order.append("fence"))
    fake_kafka = types.ModuleType("kafka")

    class KafkaProducer:  # noqa: N801 - mirrors the kafka-python name
        def __init__(self, **config):
            order.append(("producer", dict(config)))
    fake_kafka.KafkaProducer = KafkaProducer
    monkeypatch.setitem(sys.modules, "kafka", fake_kafka)

    producer = events._default_producer({"bootstrap_servers": "10.0.70.25:9092", "acks": "1"})
    assert isinstance(producer, KafkaProducer)
    assert order == ["fence", ("producer", {"bootstrap_servers": "10.0.70.25:9092", "acks": "1"})]
