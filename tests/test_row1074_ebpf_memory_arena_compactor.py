"""Row 1074: Kernel eBPF Memory Allocation Tracer and Glibc Memory Arena Compactor (ArenaCompactor).

Tests verify:
1. Positive control on dev_suite.introspection baseline and semantic failure on missing capability.
2. eBPF tracer profiling, allocation observation, and fragmentation ratio estimation.
3. Glibc memory arena compaction execution and CompactionResult telemetry.
4. Compaction pacing and rate-limiting to prevent unnecessary CPU overhead.
5. Safe ctypes malloc_trim execution and graceful degradation.
6. Caller integration with bulk_downloader.dev_suite.introspection.force_gc.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict
import pytest

BD_GATE_SCOPE = "module"


def test_row1074_positive_control_and_capability() -> None:
    """Proves the probe can say YES on positive control and fails on base for missing capability."""
    repo_root = Path(__file__).resolve().parents[1]
    introspection_py = repo_root / "bulk_downloader" / "dev_suite" / "introspection.py"
    assert introspection_py.is_file(), f"Positive control failed: {introspection_py} does not exist"

    try:
        from bulk_downloader.arena_compactor import (  # type: ignore[import-not-found]
            ArenaCompactor,
            get_arena_compactor,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        raise AssertionError(
            "Row 1074 capability missing: Kernel eBPF Memory Allocation Tracer and "
            "Glibc Memory Arena Compactor (ArenaCompactor) not implemented in "
            "bulk_downloader.arena_compactor"
        ) from exc

    compactor = get_arena_compactor()
    assert isinstance(compactor, ArenaCompactor)


def test_ebpf_tracer_profile_and_metrics() -> None:
    """Verifies eBPF tracer profile initialization and memory allocation tracking."""
    from bulk_downloader.arena_compactor import ArenaCompactor, EBPFTracerProfile

    profile = EBPFTracerProfile(
        enabled=True,
        probes_attached=4,
        kernel_tracer_type="ebpf_uprobe_glibc",
    )
    assert profile.enabled is True
    assert profile.probes_attached == 4
    assert profile.kernel_tracer_type == "ebpf_uprobe_glibc"

    compactor = ArenaCompactor(ebpf_profile=profile)
    compactor.record_allocation(size_bytes=1048576, arena_id=1)
    compactor.record_allocation(size_bytes=524288, arena_id=2)

    status = compactor.get_compactor_status()
    assert status["ebpf_tracer"]["allocations_observed"] == 2
    assert status["ebpf_tracer"]["active_arenas"] >= 2


def test_arena_compactor_compaction_execution() -> None:
    """Verifies glibc memory arena compaction execution and result reporting."""
    from bulk_downloader.arena_compactor import ArenaCompactor

    compactor = ArenaCompactor()
    result = compactor.compact_arenas(force=True)

    assert result.rss_before_bytes > 0
    assert result.rss_after_bytes > 0
    assert result.duration_ms >= 0.0
    summary = result.to_dict()
    assert "rss_before_bytes" in summary
    assert "rss_after_bytes" in summary
    assert "bytes_reclaimed" in summary
    assert "duration_ms" in summary


def test_compaction_pacing_and_interval_guard() -> None:
    """Verifies that compaction pacing prevents excessive consecutive glibc trims."""
    from bulk_downloader.arena_compactor import ArenaCompactor

    compactor = ArenaCompactor(min_trim_interval_seconds=10.0)

    # First trim executes
    res1 = compactor.compact_arenas(force=False)
    assert res1.trimmed is True

    # Immediate second trim is skipped by pacing guard
    res2 = compactor.compact_arenas(force=False)
    assert res2.trimmed is False

    # Force bypasses the pacing guard
    res3 = compactor.compact_arenas(force=True)
    assert res3.trimmed is True


def test_glibc_malloc_trim_binding_safety() -> None:
    """Verifies that glibc malloc_trim bindings execute or degrade safely without crashing."""
    from bulk_downloader.arena_compactor import compact_glibc_arenas

    result = compact_glibc_arenas()
    assert isinstance(result.glibc_available, bool)
    assert result.duration_ms >= 0.0


def test_introspection_caller_wiring() -> None:
    """Verifies caller integration with bulk_downloader.dev_suite.introspection.force_gc."""
    from bulk_downloader.dev_suite.introspection import force_gc

    report = force_gc()
    assert report["ok"] is True
    assert "unreachable_collected" in report
    assert "objects_freed" in report
    assert "arena_compaction" in report
    arena_stats = report["arena_compaction"]
    assert "rss_before_bytes" in arena_stats
    assert "rss_after_bytes" in arena_stats
    assert "bytes_reclaimed" in arena_stats
