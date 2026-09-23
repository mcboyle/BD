"""Unit tests for Row 976: Native Memory Allocator Runtime Profiling & Heap Arena Fragmentation Suppressor.

Guards:
- Localized runtime memory profiling under bulk_downloader.jemalloc_profile
- Active native allocator detection (jemalloc, glibc, system)
- Heap arena telemetry: allocated, active, resident, fragmentation ratio
- Arena fragmentation suppressor with automatic and manual purge / decay
- Asynchronous periodic suppression loop lifecycle
- Graceful degradation in non-jemalloc / mock environments
"""
import asyncio
import ctypes
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

try:
    from bulk_downloader.jemalloc_profile import (
        AllocatorType,
        ArenaStats,
        HeapArenaFragmentationSuppressor,
        JemallocProfile,
        PurgeResult,
    )
except ImportError:
    AllocatorType = None
    ArenaStats = None
    HeapArenaFragmentationSuppressor = None
    JemallocProfile = None
    PurgeResult = None

BD_GATE_SCOPE = "module"


def test_metadata_and_classes():
    """Verify class interfaces, enums, and types exist."""
    assert JemallocProfile is not None, "Row 976 capability missing: JemallocProfile not exposed"
    assert AllocatorType.JEMALLOC.value == "jemalloc"
    assert AllocatorType.GLIBC.value == "glibc"
    assert AllocatorType.SYSTEM.value == "system"

    profiler = JemallocProfile()
    assert profiler is not None
    suppressor = HeapArenaFragmentationSuppressor(profiler=profiler)
    assert suppressor is not None
    assert suppressor.profiler is profiler


def test_allocator_detection():
    """Verify detection identifies active allocator without error."""
    profiler = JemallocProfile()
    alloc_type = profiler.detect_allocator()
    assert isinstance(alloc_type, AllocatorType)
    is_je = profiler.is_jemalloc_active()
    assert isinstance(is_je, bool)
    if is_je:
        assert alloc_type == AllocatorType.JEMALLOC
    else:
        assert alloc_type in (AllocatorType.GLIBC, AllocatorType.SYSTEM, AllocatorType.TCMALLOC, AllocatorType.MIMALLOC)


def test_arena_stats_collection():
    """Verify arena stats structure, field types, and fragmentation bounds."""
    profiler = JemallocProfile()
    stats = profiler.get_arena_stats()
    assert isinstance(stats, ArenaStats)
    assert isinstance(stats.allocator, str)
    assert stats.allocated_bytes >= 0
    assert stats.resident_bytes >= 0
    assert 0.0 <= stats.fragmentation_ratio <= 1.0

    # Fragmentation calculation unit check
    ratio = profiler.compute_fragmentation(allocated_bytes=800, active_bytes=1000)
    assert ratio == pytest.approx(0.20)

    # Edge cases: allocated >= active or zero active
    assert profiler.compute_fragmentation(allocated_bytes=1000, active_bytes=1000) == 0.0
    assert profiler.compute_fragmentation(allocated_bytes=1200, active_bytes=1000) == 0.0
    assert profiler.compute_fragmentation(allocated_bytes=0, active_bytes=0) == 0.0


def test_suppressor_manual_purge():
    """Verify manual arena purge returns valid PurgeResult and updates counters."""
    profiler = JemallocProfile()
    suppressor = HeapArenaFragmentationSuppressor(profiler=profiler)

    initial_purges = suppressor.total_purges
    result = suppressor.purge_arenas(decay_only=False)
    assert isinstance(result, PurgeResult)
    assert result.success is True
    assert result.purged_at > 0
    assert suppressor.total_purges == initial_purges + 1
    assert suppressor.last_purge_timestamp == result.purged_at


def test_suppressor_threshold_trigger():
    """Verify check_and_suppress triggers purge only when fragmentation exceeds threshold."""
    profiler = JemallocProfile()
    suppressor = HeapArenaFragmentationSuppressor(profiler=profiler, default_threshold=0.25)

    # Mock stats below threshold (10% fragmentation)
    low_frag_stats = ArenaStats(
        allocator="jemalloc",
        allocated_bytes=900,
        active_bytes=1000,
        resident_bytes=1100,
        metadata_bytes=50,
        fragmentation_ratio=0.10,
        arenas_count=4,
        timestamp=time.time(),
    )
    with patch.object(profiler, "get_arena_stats", return_value=low_frag_stats):
        res = suppressor.check_and_suppress(threshold=0.25)
        assert res is None  # no purge triggered

    # Mock stats above threshold (35% fragmentation)
    high_frag_stats = ArenaStats(
        allocator="jemalloc",
        allocated_bytes=650,
        active_bytes=1000,
        resident_bytes=1200,
        metadata_bytes=50,
        fragmentation_ratio=0.35,
        arenas_count=4,
        timestamp=time.time(),
    )
    with patch.object(profiler, "get_arena_stats", return_value=high_frag_stats):
        res = suppressor.check_and_suppress(threshold=0.25)
        assert isinstance(res, PurgeResult)
        assert res.success is True


def test_telemetry_export():
    """Verify telemetry snapshot contains required keys and is serializable."""
    profiler = JemallocProfile()
    suppressor = HeapArenaFragmentationSuppressor(profiler=profiler)
    telemetry = suppressor.get_telemetry()

    assert "allocator" in telemetry
    assert "arena_stats" in telemetry
    assert "suppressor" in telemetry
    assert telemetry["suppressor"]["total_purges"] >= 0
    assert "fragmentation_ratio" in telemetry["arena_stats"]

    json_str = suppressor.export_telemetry_json()
    assert isinstance(json_str, str)
    assert "allocator" in json_str


@pytest.mark.anyio
async def test_async_suppression_loop():
    """Verify async periodic suppression loop starts, executes, and cancels cleanly."""
    profiler = JemallocProfile()
    suppressor = HeapArenaFragmentationSuppressor(profiler=profiler, default_threshold=0.20)

    # Start loop with small interval
    await suppressor.start_suppression_loop(interval_seconds=0.02, threshold=0.10)
    assert suppressor.is_loop_running is True

    # Allow loop to tick at least once
    await asyncio.sleep(0.06)

    # Stop loop
    await suppressor.stop_suppression_loop()
    assert suppressor.is_loop_running is False


def test_graceful_degradation_on_c_call_error():
    """Verify purge_arenas does not raise when underlying C functions fail."""
    profiler = JemallocProfile()
    suppressor = HeapArenaFragmentationSuppressor(profiler=profiler)

    with patch.object(profiler, "_invoke_mallctl", side_effect=OSError("mallctl symbol missing")), \
         patch.object(profiler, "_invoke_malloc_trim", side_effect=OSError("trim failed")):
        res = suppressor.purge_arenas()
        assert isinstance(res, PurgeResult)
        # Should gracefully return success=False or handled
        assert res.error is not None


def test_real_allocator_metrics_live_mmap():
    """Live mmap allocations are used bytes, not reclaimable fragmentation."""
    profiler = JemallocProfile()
    assert profiler.detect_allocator() == AllocatorType.GLIBC
    assert hasattr(profiler._libc, "mallinfo2")
    before = profiler.get_arena_stats()
    size = max(64 * 1024 * 1024, 8 * before.active_bytes)
    libc = profiler._libc
    libc.malloc.argtypes = [ctypes.c_size_t]
    libc.malloc.restype = ctypes.c_void_p
    libc.free.argtypes = [ctypes.c_void_p]
    libc.free.restype = None
    ptr = libc.malloc(size)
    assert ptr, "native allocation precondition failed"
    try:
        stats = profiler.get_arena_stats()
        assert stats.active_bytes >= size
        assert stats.allocated_bytes >= size, "live mmap omitted from allocated bytes"
        assert stats.fragmentation_ratio < 0.25
        suppressor = HeapArenaFragmentationSuppressor(profiler=profiler)
        assert suppressor.check_and_suppress() is None
        assert suppressor.total_purges == 0
    finally:
        libc.free(ptr)


@pytest.mark.parametrize("allocated,mapped,expected", [(600, 0, 0.4), (900, 0, 0.1), (600, 1000, 0.2)])
def test_glibc_mallinfo2_values(allocated, mapped, expected):
    profiler = JemallocProfile()
    mallinfo = MagicMock(return_value=SimpleNamespace(arena=1000, uordblks=allocated, hblkhd=mapped))
    libc = SimpleNamespace(mallinfo2=mallinfo, malloc_trim=MagicMock(return_value=0))
    with patch.object(profiler, "_libc", libc), patch.object(profiler, "_read_proc_status_rss", return_value=9000):
        stats = profiler.get_arena_stats()
    mallinfo.assert_called_once_with()
    assert stats.allocated_bytes == allocated + mapped, "mallinfo2 allocated-byte mapping lost"
    assert stats.active_bytes == 1000 + mapped, "mallinfo2 active-byte mapping lost"
    assert stats.resident_bytes == 9000
    assert stats.fragmentation_ratio == pytest.approx(expected)


@pytest.mark.parametrize("allocated,purges", [(600, 1), (900, 0)])
def test_runtime_scheduler_executes_fragmentation_check(monkeypatch, allocated, purges):
    from bulk_downloader import bg_scheduler as scheduler

    mallinfo = MagicMock(return_value=SimpleNamespace(arena=1000, uordblks=allocated, hblkhd=0))
    trim = MagicMock(return_value=0)
    libc = SimpleNamespace(mallinfo2=mallinfo, malloc_trim=trim)
    stop = threading.Event()
    with monkeypatch.context() as m:
        m.setattr(scheduler, "_tasks", {})
        m.setattr(scheduler, "_stop_event", stop)
        m.setattr(scheduler, "_last_activity", 0)
        m.setattr(scheduler, "_wait_next", lambda interval: stop.set())
        m.setattr(JemallocProfile, "_init_ctypes", lambda self: setattr(self, "_libc", libc))
        scheduler.register_default_tasks()
        assert "saved_searches.run_due" in scheduler._tasks, "scheduler positive control missing"
        name = "memory.check_fragmentation"
        assert name in scheduler._tasks, "runtime fragmentation task is not registered"
        task = scheduler._tasks[name]
        assert isinstance(task["fn"].__self__, HeapArenaFragmentationSuppressor)
        assert task["interval"] == 300
        for task_name, registered in scheduler._tasks.items():
            registered["enabled"] = task_name == name
        scheduler._loop()
        assert task["run_count"] == 1
        assert task["last_status"] == "ok", task["last_error"]
        assert trim.call_count == purges, "runtime threshold did not control native purge"
        assert mallinfo.call_count == 1 + 2 * purges
        assert task["fn"].__self__.total_purges == purges
        scheduler._loop()
        assert task["run_count"] == 1, "stopped scheduler executed another purge"


@pytest.mark.parametrize("decay_only,command", [(False, "arena.4096.purge"), (True, "arena.4096.decay")])
def test_jemalloc_all_arena_command(decay_only, command):
    profiler = JemallocProfile()
    suppressor = HeapArenaFragmentationSuppressor(profiler=profiler)
    with patch.object(profiler, "detect_allocator", return_value=AllocatorType.JEMALLOC), \
         patch.object(profiler, "get_arena_stats", return_value=SimpleNamespace(resident_bytes=1000)), \
         patch.object(profiler, "_invoke_mallctl", return_value=0) as mallctl:
        result = suppressor.purge_arenas(decay_only=decay_only, run_gc=False)
    assert result.success
    mallctl.assert_called_once_with(command)


def test_jemalloc_mallctl_mocked_execution():
    """Verify jemalloc mallctl parsing and decay/purge execution under mock (F4 fix)."""
    profiler = JemallocProfile()
    mock_libc = MagicMock()
    mock_libc.mallctl = MagicMock(return_value=0)
    with patch.object(profiler, "_libc", mock_libc), \
         patch.object(profiler, "detect_allocator", return_value=AllocatorType.JEMALLOC):
        assert profiler.is_jemalloc_active() is True
        suppressor = HeapArenaFragmentationSuppressor(profiler=profiler)
        res = suppressor.purge_arenas(decay_only=True)
        assert res.success is True
        assert res.decay_only is True
        mock_libc.mallctl.assert_called()


@pytest.mark.parametrize("failed", [b"epoch", b"stats.allocated", b"stats.active", b"stats.metadata", b"stats.resident"])
def test_jemalloc_failed_read_declines_fragmentation(failed, caplog):
    profiler = JemallocProfile()
    values = {b"stats.allocated": 800, b"stats.active": 1000,
              b"stats.metadata": 40, b"stats.resident": 1200}
    calls = []

    def mallctl(name, output, output_size, new, new_size):
        calls.append(name)
        if name == failed:
            return 5
        if name in values:
            output._obj.value = values[name]
        return 0

    libc = SimpleNamespace(mallctl=mallctl)
    with patch.object(profiler, "_libc", libc), \
         patch.object(profiler, "_read_proc_status_rss", return_value=9000), \
         caplog.at_level("DEBUG", logger="bulk_downloader.jemalloc_profile"):
        stats = profiler.get_arena_stats()
    commands = [b"epoch", *values]
    assert calls == commands[:commands.index(failed) + 1], "failed mallctl request did not abort metrics collection"
    assert f"mallctl {failed.decode()} failed" in caplog.text
    assert stats.allocated_bytes == stats.active_bytes == stats.resident_bytes == 9000
    assert stats.fragmentation_ratio == 0.0


def test_t72_native_stats_failure_is_reported_not_swallowed():
    """T72 ratchet (defect_DP_total +5, all DP-13 in jemalloc_profile.py): a failed native read
    falls back to RSS, and the stats must say so rather than read as a clean measurement."""
    profiler = JemallocProfile()
    libc = SimpleNamespace(mallctl=lambda *a: 5)
    with patch.object(profiler, "_libc", libc), \
         patch.object(profiler, "detect_allocator", return_value=AllocatorType.JEMALLOC), \
         patch.object(profiler, "_read_proc_status_rss", return_value=9000):
        stats = profiler.get_arena_stats()
    assert stats.allocated_bytes == 9000
    native_error = getattr(stats, "native_error", None)
    assert native_error and "mallctl epoch failed" in native_error, (
        f"T72: native stats failure was swallowed, native_error={getattr(stats, 'native_error', 'MISSING')!r}")


def test_t72_unreadable_rss_is_reported_not_zero(monkeypatch):
    profiler = JemallocProfile()

    def _no_proc(*a, **k):
        raise OSError("t72 no /proc")

    monkeypatch.setattr("builtins.open", _no_proc)
    with patch.object(profiler, "detect_allocator", return_value=AllocatorType.SYSTEM):
        stats = profiler.get_arena_stats()
    native_error = getattr(stats, "native_error", None)
    assert native_error and "t72 no /proc" in native_error, (
        f"T72: unreadable RSS reported as a 0-byte measurement, native_error={getattr(stats, 'native_error', 'MISSING')!r}")


@pytest.mark.asyncio
async def test_t72_worker_tick_failure_is_counted():
    suppressor = HeapArenaFragmentationSuppressor()
    ticks = []

    def _boom(threshold=None):
        ticks.append(1)
        suppressor._stop_event.set()
        raise RuntimeError("t72 tick failure")

    suppressor.check_and_suppress = _boom
    await suppressor._suppression_worker(interval_seconds=0.01, threshold=None)
    sup = suppressor.get_telemetry()["suppressor"]
    assert ticks == [1]
    assert sup.get("worker_failures") == 1 and "t72 tick failure" in (sup.get("last_worker_error") or ""), (
        f"T72: suppression worker tick failure was swallowed, telemetry={sup!r}")
