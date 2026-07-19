"""v3.66.673 -- EXT-3: throughput-adaptive multi-connection count.

Before this, multi_conn used a fixed config N (multi_conn_count, clamped [2,16]);
observed speed only gated an abort-to-single-conn, never adapted N. This cut adds:
  * multi_conn.adaptive_chunk_count(prev_n, chunks_failed, accept_ranges): an
    AIMD-lite policy -- a clean prior run probes one more connection; a run with
    failed chunks backs off by two; ranges-unsupported pins to the base. Clamped [2,16].
  * db.host_throughput_record / host_throughput_get: a tiny per-host store so the
    NEXT run for a host derives N from that host's observed outcome.

Opt-in via config multi_conn_adaptive (default OFF -> fixed config N, byte-identical).
Pure-unit + isolated temp DB. Zero-arg tests.
"""
from __future__ import annotations

import tempfile

import bulk_downloader.db as db
from bulk_downloader import multi_conn as mc


def test_adaptive_clean_run_probes_one_more():
    assert mc.adaptive_chunk_count(4, chunks_failed=0, accept_ranges=True) == 5
    # clamp at 16
    assert mc.adaptive_chunk_count(16, chunks_failed=0, accept_ranges=True) == 16


def test_adaptive_failures_back_off():
    assert mc.adaptive_chunk_count(8, chunks_failed=3, accept_ranges=True) == 6
    # clamp at 2
    assert mc.adaptive_chunk_count(2, chunks_failed=1, accept_ranges=True) == 2
    assert mc.adaptive_chunk_count(3, chunks_failed=1, accept_ranges=True) == 2


def test_adaptive_no_ranges_pins_base():
    # multi-conn can't help without byte ranges -> don't grow it
    assert mc.adaptive_chunk_count(8, chunks_failed=0, accept_ranges=False) == 8


def test_host_throughput_round_trip():
    saved = db.DB_PATH
    try:
        db.DB_PATH = tempfile.mktemp(prefix="ext3_", suffix=".db")
        db.db_init()
        assert db.host_throughput_get("cdn.example.com") is None
        db.host_throughput_record("cdn.example.com", chunk_count=6,
                                  avg_speed_bps=5_000_000.0, chunks_failed=1)
        rec = db.host_throughput_get("cdn.example.com")
        assert rec is not None
        assert rec["chunk_count"] == 6
        assert rec["chunks_failed"] == 1
        assert abs(rec["avg_speed_bps"] - 5_000_000.0) < 1.0
        # upsert overwrites
        db.host_throughput_record("cdn.example.com", chunk_count=7,
                                  avg_speed_bps=6_000_000.0, chunks_failed=0)
        rec2 = db.host_throughput_get("cdn.example.com")
        assert rec2["chunk_count"] == 7 and rec2["chunks_failed"] == 0
    finally:
        db.DB_PATH = saved
