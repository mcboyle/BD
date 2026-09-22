"""Unit tests for Row 983: In-Process Heap Arena Compaction and Glibc malloc_trim(0) Mitigator.

Guards:
- In-process heap arena compaction using glibc malloc_trim(0) ctypes bindings
- Resilient fallback and error isolation on non-glibc / non-Linux platforms
- Configurable arena compaction thresholds and interval policies
- Asynchronous periodic compaction lifecycle management
- Observability and telemetry export for arena stats and reclaimed memory
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

try:
    from bulk_downloader.heap_compactor import (
        ArenaTelemetry,
        CompactionConfig,
        CompactionResult,
        GlibcMallocTrimMitigator,
        HeapArenaCompactor,
    )
except (ImportError, ModuleNotFoundError):
    ArenaTelemetry = None
    CompactionConfig = None
    CompactionResult = None
    GlibcMallocTrimMitigator = None
    HeapArenaCompactor = None

BD_GATE_SCOPE = "repo-wide"


def test_metadata_and_classes():
    """Verify class interfaces, configurations, and defaults."""
    assert HeapArenaCompactor is not None, "HeapArenaCompactor capability must be present (bulk_downloader.heap_compactor)"
    assert GlibcMallocTrimMitigator is not None, "GlibcMallocTrimMitigator capability must be present"
    assert CompactionConfig is not None, "CompactionConfig capability must be present"
    config = CompactionConfig(interval_seconds=1.0, min_rss_bytes=1048576, pad=0, enabled=True)
    assert config.interval_seconds == 1.0
    assert config.min_rss_bytes == 1048576
    assert config.pad == 0
    assert config.enabled is True

    mitigator = GlibcMallocTrimMitigator()
    assert mitigator is not None
    assert isinstance(mitigator.allocator_name, str)

    compactor = HeapArenaCompactor(config=config, mitigator=mitigator)
    assert compactor.config is config
    assert compactor.mitigator is mitigator
    assert compactor.is_running is False


def test_glibc_malloc_trim_detection():
    """Verify detection of malloc_trim capability."""
    mitigator = GlibcMallocTrimMitigator()
    has_trim = mitigator.is_available()
    assert isinstance(has_trim, bool)
    status = mitigator.get_status()
    assert isinstance(status, dict)
    assert "available" in status
    assert "allocator" in status
    assert status["allocator"] == mitigator.allocator_name


def test_manual_compaction_call():
    """Verify direct compaction call returns structured CompactionResult."""
    mitigator = GlibcMallocTrimMitigator()
    result = mitigator.compact(pad=0)
    assert isinstance(result, CompactionResult)
    assert isinstance(result.success, bool)
    assert result.duration_seconds >= 0.0
    assert result.timestamp > 0.0
    assert isinstance(result.allocator, str)
    assert result.reclaimed_bytes_est >= 0


def test_heap_arena_compactor_threshold():
    """Verify compaction triggered only when memory exceeds configured min_rss_bytes."""
    mitigator = GlibcMallocTrimMitigator()
    # High threshold: should skip compaction
    high_config = CompactionConfig(min_rss_bytes=100 * 1024 * 1024 * 1024)  # 100 GB
    compactor_high = HeapArenaCompactor(config=high_config, mitigator=mitigator)
    res_skipped = compactor_high.check_and_compact()
    assert res_skipped is None or res_skipped.success is False

    # Zero threshold: should execute compaction
    low_config = CompactionConfig(min_rss_bytes=0)
    compactor_low = HeapArenaCompactor(config=low_config, mitigator=mitigator)
    res_executed = compactor_low.check_and_compact()
    assert res_executed is not None
    assert isinstance(res_executed, CompactionResult)


def test_background_async_compaction_loop():
    """Verify background async periodic compaction loop start and stop."""
    async def _run():
        config = CompactionConfig(interval_seconds=0.05, min_rss_bytes=0, enabled=True)
        compactor = HeapArenaCompactor(config=config)

        assert not compactor.is_running
        await compactor.start()
        assert compactor.is_running

        # Let loop run at least 2 ticks
        await asyncio.sleep(0.15)

        await compactor.stop()
        assert not compactor.is_running
        assert compactor.metrics.total_compactions >= 1

    asyncio.run(_run())


def test_graceful_error_and_mock_fallback():
    """Verify resilient handling when libc or malloc_trim is unavailable."""
    with patch("ctypes.CDLL", side_effect=OSError("Libc not loadable")):
        mock_mitigator = GlibcMallocTrimMitigator()
        assert not mock_mitigator.is_available()
        res = mock_mitigator.compact()
        assert not res.success
        assert res.reclaimed_bytes_est == 0
        assert res.error is not None


def test_telemetry_export():
    """Verify telemetry export and JSON serialization."""
    compactor = HeapArenaCompactor()
    compactor.check_and_compact()
    telemetry = compactor.get_telemetry()
    assert isinstance(telemetry, ArenaTelemetry)
    assert telemetry.total_compactions >= 1

    payload = compactor.export_telemetry_dict()
    assert isinstance(payload, dict)
    assert "metrics" in payload
    assert "config" in payload

    # Ensure JSON serializable
    json_str = json.dumps(payload)
    assert len(json_str) > 0
