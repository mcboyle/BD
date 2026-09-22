"""Row 1011: Zero-Copy Memory-Mapped I/O (mmap_size) & Page Cache Auto-Tuner.

Validates:
1. Zero-copy memory-mapped I/O (PRAGMA mmap_size) configuration and activation.
2. Dynamic SQLite page cache auto-tuning (PRAGMA cache_size) based on database size,
   available memory, and workload profile.
3. Architecture-aware safety limits (64-bit ceilings, 32-bit address space exhaustion prevention).
4. Concrete caller integration with bulk_downloader.db without breaking existing callers or the MOD-3 seam.
5. Telemetry, status inspection, and error resilience on non-fatal pragma failures.

RED on baseline: fails with AssertionError (db module lacks auto_tune_connection).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from unittest.mock import MagicMock

import pytest

BD_GATE_SCOPE = "module"


def test_red_baseline_capability_probe():
    """Verify that baseline lacks Row 1011 zero-copy mmap and page cache auto-tuner.

    RED on baseline (bc1544b751e2): fails with AssertionError.
    """
    from bulk_downloader import db

    assert hasattr(
        db, "auto_tune_connection"
    ), "Row 1011 capability missing: db module lacks auto_tune_connection"
    assert hasattr(
        db, "MmapPageCacheTuner"
    ), "Row 1011 capability missing: db module lacks MmapPageCacheTuner"


def test_db_baseline_positive_control():
    """Positive control proving probe can distinguish existing db capabilities from missing ones."""
    from bulk_downloader import db

    assert hasattr(db, "db_conn"), "Positive control failed: db.db_conn missing"
    assert callable(db.db_conn), "Positive control failed: db.db_conn not callable"
    assert hasattr(db, "_open_history_conn"), "Positive control failed: _open_history_conn missing"


def test_mmap_page_cache_tuner_profiles():
    """Verify tuning profiles and configuration parameter calculations."""
    from bulk_downloader.mmap_autotuner import (
        MmapPageCacheTuner,
        TuningConfig,
        TuningProfile,
        calculate_tuning_parameters,
    )

    # 1. Balanced profile (default)
    mmap_size, cache_kb = calculate_tuning_parameters(
        db_size_bytes=10 * 1024 * 1024,  # 10 MiB DB
        available_ram_bytes=4 * 1024 * 1024 * 1024,  # 4 GiB RAM
        profile=TuningProfile.BALANCED,
    )
    assert mmap_size >= 10 * 1024 * 1024
    assert cache_kb >= 16384  # at least 16 MiB cache

    # 2. Read-heavy profile (aggressive mmap and cache)
    mmap_rh, cache_rh = calculate_tuning_parameters(
        db_size_bytes=50 * 1024 * 1024,
        available_ram_bytes=8 * 1024 * 1024 * 1024,
        profile=TuningProfile.READ_HEAVY,
    )
    assert mmap_rh >= 50 * 1024 * 1024
    assert cache_rh >= cache_kb

    # 3. Memory-constrained profile (mmap disabled, minimal cache)
    mmap_mc, cache_mc = calculate_tuning_parameters(
        db_size_bytes=100 * 1024 * 1024,
        available_ram_bytes=512 * 1024 * 1024,
        profile=TuningProfile.MEMORY_CONSTRAINED,
    )
    assert mmap_mc == 0
    assert cache_mc <= 8192

    # 4. Custom config override
    cfg = TuningConfig(
        profile=TuningProfile.CUSTOM,
        mmap_size_bytes=64 * 1024 * 1024,
        cache_size_kb=32768,
    )
    mmap_c, cache_c = calculate_tuning_parameters(
        db_size_bytes=1024,
        available_ram_bytes=1024 * 1024 * 1024,
        profile=TuningProfile.CUSTOM,
        config=cfg,
    )
    assert mmap_c == 64 * 1024 * 1024
    assert cache_c == 32768


def test_mmap_zero_copy_live_sqlite():
    """Verify live SQLite connection receives PRAGMA mmap_size and PRAGMA cache_size."""
    from bulk_downloader.mmap_autotuner import (
        TuningProfile,
        auto_tune_connection,
        inspect_connection,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_mmap.db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, content BLOB);")
            conn.execute("INSERT INTO test_data (content) VALUES (?);", (b"A" * 8192,))
            conn.commit()

            result = auto_tune_connection(conn, profile=TuningProfile.BALANCED)
            assert result.applied is True
            assert result.error is None
            assert result.configured_mmap_size > 0
            assert result.configured_cache_size != 0
            # R2: headline zero-copy capability must take effect
            assert result.zero_copy_active is True
            assert result.effective_mmap_size == result.configured_mmap_size
            assert result.effective_mmap_size > 0
            assert result.effective_cache_size_kb > 0

            # Verify through inspection helper
            status = inspect_connection(conn)
            assert status.page_size > 0
            assert status.page_count >= 1
            assert status.db_size_bytes > 0
            assert status.zero_copy_active is True
            assert status.mmap_size_bytes == result.effective_mmap_size
            assert status.cache_size_kb == result.effective_cache_size_kb
        finally:
            conn.close()


def test_32bit_platform_safety_clamping():
    """Verify that 32-bit platforms clamp mmap_size to prevent address space exhaustion."""
    from bulk_downloader.mmap_autotuner import (
        TuningProfile,
        calculate_tuning_parameters,
    )

    # Force is_64bit=False
    mmap_size, _ = calculate_tuning_parameters(
        db_size_bytes=500 * 1024 * 1024,
        available_ram_bytes=4 * 1024 * 1024 * 1024,
        profile=TuningProfile.HIGH_THROUGHPUT,
        is_64bit=False,
    )
    # Must not exceed 64 MiB on 32-bit architectures
    assert mmap_size <= 64 * 1024 * 1024


def test_zero_copy_large_payload_read_integrity():
    """Verify data integrity when reading large payloads through zero-copy memory mapped connection."""
    from bulk_downloader.mmap_autotuner import auto_tune_connection, TuningProfile

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "payload.db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE payloads (id INTEGER PRIMARY KEY, chunk BLOB);")
            payload = os.urandom(64 * 1024)  # 64 KiB
            conn.execute("INSERT INTO payloads (chunk) VALUES (?);", (payload,))
            conn.commit()

            # Tune for read heavy
            tune_res = auto_tune_connection(conn, profile=TuningProfile.READ_HEAVY)
            assert tune_res.applied is True
            assert tune_res.zero_copy_active is True
            assert tune_res.effective_mmap_size > 0
            assert tune_res.effective_cache_size_kb > 0

            cur = conn.execute("SELECT chunk FROM payloads WHERE id = 1;")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == payload
        finally:
            conn.close()


def test_error_resilience_on_invalid_connection():
    """Verify tuner does not raise exceptions when encountering invalid or failing connections."""
    from bulk_downloader.mmap_autotuner import auto_tune_connection

    # Passing an object that raises on execute
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = sqlite3.OperationalError("disk I/O error")

    result = auto_tune_connection(mock_conn)
    assert result.applied is False
    assert result.error is not None
    assert "disk I/O error" in result.error


def test_db_module_integration():
    """Verify that bulk_downloader.db exposes auto-tuning and applies it to connections."""
    from bulk_downloader import db

    # Verify exports
    assert hasattr(db, "auto_tune_connection")
    assert hasattr(db, "MmapPageCacheTuner")
    assert hasattr(db, "TuningProfile")
    assert hasattr(db, "get_tuning_metrics")
    assert hasattr(db, "inspect_connection")

    # R1: Snapshot baseline metric counter to verify caller integration delta == 1
    initial_tuned = db.get_tuning_metrics()["total_tuned"]

    # Verify _open_history_conn applies auto-tuning
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "history_test.db")
        conn = db._open_history_conn(path=db_file)
        try:
            assert conn is not None
            # Check telemetry from db: must increment by exactly 1
            metrics = db.get_tuning_metrics()
            assert metrics["total_tuned"] - initial_tuned == 1

            # Assert live connection received active zero-copy mmap & page cache tuning
            status = db.inspect_connection(conn)
            assert status.mmap_size_bytes > 0
            assert status.zero_copy_active is True
            assert status.cache_size_kb > 0
        finally:
            conn.close()


def test_tuning_status_and_telemetry():
    """Verify tuner telemetry recording and resetting."""
    from bulk_downloader.mmap_autotuner import MmapPageCacheTuner, TuningProfile

    tuner = MmapPageCacheTuner(default_profile=TuningProfile.BALANCED)
    tuner.reset_telemetry()
    assert tuner.get_telemetry()["total_tuned"] == 0

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "telemetry.db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE t (x INT);")
            res = tuner.tune(conn)
            assert res.applied is True

            tel = tuner.get_telemetry()
            assert tel["total_tuned"] == 1
            assert tel["total_errors"] == 0

            status = tuner.inspect(conn)
            assert status.page_size > 0
            assert status.db_size_bytes > 0
        finally:
            conn.close()


def test_zero_copy_inactive_when_mmap_disabled():
    """Verify zero_copy_active is False when mmap is disabled (MEMORY_CONSTRAINED profile)."""
    from bulk_downloader.mmap_autotuner import (
        TuningProfile,
        auto_tune_connection,
        inspect_connection,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "no_mmap.db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, data BLOB);")
            conn.execute("INSERT INTO t (data) VALUES (?);", (b"X" * 4096,))
            conn.commit()

            result = auto_tune_connection(conn, profile=TuningProfile.MEMORY_CONSTRAINED)
            assert result.applied is True
            assert result.configured_mmap_size == 0
            assert result.zero_copy_active is False

            status = inspect_connection(conn)
            assert status.zero_copy_active is False
            assert status.mmap_size_bytes == 0
        finally:
            conn.close()


def test_cache_size_equals_configured_target(monkeypatch):
    """Effective cache size equals the computed target, not merely its own read-back."""
    from bulk_downloader import mmap_autotuner
    from bulk_downloader.mmap_autotuner import (
        TuningProfile,
        auto_tune_connection,
        calculate_tuning_parameters,
        inspect_connection,
    )

    pinned_ram = 2 * 1024 * 1024 * 1024
    monkeypatch.setattr(mmap_autotuner, "detect_available_ram", lambda: pinned_ram)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "cache_target.db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, data BLOB);")
            conn.execute("INSERT INTO t (data) VALUES (?);", (b"Y" * 4096,))
            conn.commit()

            before = inspect_connection(conn)
            _, target_kb = calculate_tuning_parameters(
                before.db_size_bytes, pinned_ram, TuningProfile.BALANCED
            )
            # BALANCED at 2 GiB RAM: 2 GiB // 64 KiB = 32768 KiB, distinct from
            # SQLite's default (-2000) and from the 65536 KiB ceiling.
            assert target_kb == 32768

            result = auto_tune_connection(conn, profile=TuningProfile.BALANCED)
            assert result.applied is True
            assert result.configured_cache_size == -target_kb

            status = inspect_connection(conn)
            assert status.cache_size_raw == -target_kb
            assert status.cache_size_kb == target_kb
            assert result.effective_cache_size_kb == target_kb
        finally:
            conn.close()


def test_page_cache_auto_tuning_ram_awareness():
    """Verify that page cache sizing adapts to available system RAM."""
    from bulk_downloader.mmap_autotuner import TuningProfile, calculate_tuning_parameters

    # Low RAM system: 1 GiB
    _, cache_low = calculate_tuning_parameters(
        db_size_bytes=100 * 1024 * 1024,
        available_ram_bytes=1024 * 1024 * 1024,
        profile=TuningProfile.READ_HEAVY,
    )

    # High RAM system: 32 GiB
    _, cache_high = calculate_tuning_parameters(
        db_size_bytes=100 * 1024 * 1024,
        available_ram_bytes=32 * 1024 * 1024 * 1024,
        profile=TuningProfile.READ_HEAVY,
    )

    assert cache_high > cache_low
    assert cache_high <= 256 * 1024  # Max cache limit respected

