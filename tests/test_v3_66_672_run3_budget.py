"""v3.66.672 -- RUN-3: unified per-run resource budget (run_budget).

Proves the pure evaluator: per-dimension breach detection with 0=uncapped, the
config/env reader, the memory admission-gate contract (fail-open, default-off),
and that current_rss_mb() returns a real number. Zero-arg, no DB, no HTTP.
"""
from __future__ import annotations

from bulk_downloader import run_budget as rb


def test_breach_wall_mem_net_independently():
    b = rb.RunBudget(wall_s=10, mem_mb=500, net_bytes=1000)
    assert b.breach(elapsed_s=5, rss_mb=100, bytes_done=100) is None
    assert b.breach(elapsed_s=10, rss_mb=0, bytes_done=0) == "wall"
    assert b.breach(elapsed_s=0, rss_mb=500, bytes_done=0) == "mem"
    assert b.breach(elapsed_s=0, rss_mb=0, bytes_done=1000) == "net"


def test_zero_limit_never_breaches():
    b = rb.RunBudget()  # all uncapped
    assert not b.is_active()
    assert b.breach(elapsed_s=1e9, rss_mb=1e9, bytes_done=10**12) is None


def test_from_config_reads_cfg():
    b = rb.from_config({"run_wall_budget_s": 30, "run_mem_budget_mb": 800,
                        "daily_byte_budget": 2048})
    assert (b.wall_s, b.mem_mb, b.net_bytes) == (30, 800, 2048)
    # empty cfg -> uncapped
    b2 = rb.from_config({})
    assert (b2.wall_s, b2.mem_mb, b2.net_bytes) == (0, 0, 0)
    # negative / junk -> uncapped
    b3 = rb.from_config({"run_mem_budget_mb": -5, "run_wall_budget_s": "x"})
    assert b3.mem_mb == 0 and b3.wall_s == 0


def test_mem_admission_gate_over_and_under():
    # budget 500MB, sampled 600MB -> over
    over = rb.is_over_mem_budget({"run_mem_budget_mb": 500}, rss_mb=600)
    assert over["over"] is True and over["budget_mb"] == 500 and over["rss_mb"] == 600
    # sampled 100MB -> under
    under = rb.is_over_mem_budget({"run_mem_budget_mb": 500}, rss_mb=100)
    assert under["over"] is False
    # no budget -> never over (fail-open / default-off)
    off = rb.is_over_mem_budget({}, rss_mb=99999)
    assert off["over"] is False


def test_current_rss_mb_returns_number():
    v = rb.current_rss_mb()
    assert isinstance(v, float) and v >= 0.0
