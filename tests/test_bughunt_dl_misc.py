"""Bug-hunt dl-misc (download-engine sweep findings 3, 7, 8).

3  download_window.parse_hm rejected '24:00', so '18:00-24:00' parsed to no
   window at all and site_in_window() then answered "no restriction".
7  resume.bytes_already_downloaded raised TypeError on done_bytes=None.
8  download_supervisor.TokenBucket.try_acquire could never grant a request
   larger than the bucket capacity, however long it was allowed to wait.

Pure-function / in-memory tests: no app, no DB, no network.
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from bulk_downloader.download_supervisor import TokenBucket
from bulk_downloader.download_window import (
    in_window, next_transition_seconds, parse_hm, parse_windows,
    site_in_window,
)
from bulk_downloader.resume import bytes_already_downloaded

BD_GATE_SCOPE = "module"


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, 13, hour, minute, second)


# ── finding 3: '24:00' ────────────────────────────────────────────────

def test_f3_parse_hm_accepts_end_of_day():
    assert parse_hm("24:00", -1) == 1440


@pytest.mark.parametrize("bad", ["24:01", "24:59", "25:00", "24:60", "-1:00"])
def test_f3_parse_hm_still_rejects_out_of_range(bad):
    assert parse_hm(bad, -1) == -1


def test_f3_window_ending_at_2400_is_kept_and_restricts():
    assert parse_windows("18:00-24:00") == [(1080, 1440)]
    cfg = {"window_enabled": True, "window_active_hours": "18:00-24:00"}
    assert site_in_window(cfg, now=_at(9)) is False
    assert site_in_window(cfg, now=_at(17, 59)) is False
    assert site_in_window(cfg, now=_at(18)) is True
    assert site_in_window(cfg, now=_at(23, 59)) is True
    assert site_in_window(cfg, now=_at(0)) is False


def test_f3_window_matches_its_2359_spelling_all_day():
    """'18:00-24:00' differs from '18:00-23:59' only in minute 23:59."""
    a = parse_windows("18:00-24:00")
    b = parse_windows("18:00-23:59")
    differing = [(h, m) for h in range(24) for m in range(60)
                 if in_window(a, now=_at(h, m)) != in_window(b, now=_at(h, m))]
    assert differing == [(23, 59)]


def test_f3_2400_as_start_means_midnight():
    assert parse_windows("24:00-06:00") == [(0, 360)]
    cfg = {"window_enabled": True, "window_active_hours": "24:00-06:00"}
    assert site_in_window(cfg, now=_at(3)) is True
    assert site_in_window(cfg, now=_at(12)) is False


def test_f3_zero_length_spellings_of_midnight_are_rejected():
    assert parse_windows("24:00-00:00") == []
    assert parse_windows("24:00-24:00") == []
    assert parse_windows("00:00-00:00") == []


def test_f3_whole_day_window():
    assert parse_windows("00:00-24:00") == [(0, 1440)]
    w = parse_windows("00:00-24:00")
    assert all(in_window(w, now=_at(h, m))
               for h in range(24) for m in (0, 30, 59))


def test_f3_next_transition_reaches_midnight_end():
    cfg = {"window_enabled": True, "window_active_hours": "18:00-24:00"}
    assert next_transition_seconds(cfg, now=_at(23, 0, 0)) == 3600
    assert next_transition_seconds(cfg, now=_at(17, 0, 0)) == 3600


# ── finding 7: corrupt done_bytes ─────────────────────────────────────

@pytest.mark.parametrize("bad", [None, "abc", "", [], {}, float("inf")])
def test_f7_corrupt_done_bytes_counts_as_zero(bad):
    cp = {"chunks": [{"done_bytes": bad}, {"done_bytes": 5}]}
    assert bytes_already_downloaded(cp) == 5


def test_f7_valid_values_unchanged():
    assert bytes_already_downloaded({"chunks": [{"done_bytes": 5}]}) == 5
    assert bytes_already_downloaded(
        {"chunks": [{"done_bytes": 7}, {"done_bytes": "3"}, {}, "junk"]}) == 10
    assert bytes_already_downloaded({"chunks": None}) == 0
    assert bytes_already_downloaded(None) == 0


def test_f7_negative_done_bytes_does_not_reduce_progress():
    cp = {"chunks": [{"done_bytes": -100}, {"done_bytes": 40}]}
    assert bytes_already_downloaded(cp) == 40


# ── finding 8: request larger than capacity ───────────────────────────

def test_f8_oversized_request_is_granted_from_a_full_bucket():
    b = TokenBucket("t", rate_bps=100)            # capacity 100
    t0 = time.monotonic()
    ok, waited = b.try_acquire(200, max_wait_s=10)
    assert ok is True
    assert waited == 0.0
    assert time.monotonic() - t0 < 0.5
    assert b.stats()["tokens_available"] == 0     # in debt, never negative


def test_f8_oversized_request_waits_only_for_a_full_bucket():
    b = TokenBucket("t", rate_bps=1000, capacity_factor=0.1)   # capacity 100
    assert b.try_acquire(100) == (True, 0.0)                    # drain it
    ok, waited = b.try_acquire(300, max_wait_s=5)
    assert ok is True
    assert 0.05 <= waited <= 0.5


def test_f8_wait_budget_too_short_refuses_without_consuming():
    b = TokenBucket("t", rate_bps=1000, capacity_factor=0.1)
    assert b.try_acquire(100) == (True, 0.0)
    assert b.try_acquire(300, max_wait_s=0.001) == (False, 0.0)
    assert b.try_acquire(300) == (False, 0.0)                   # no wait asked
    before = b.stats()
    assert before["acquires"] == 1 and before["bytes_passed"] == 100


def test_f8_debt_is_paid_before_the_next_grant():
    b = TokenBucket("t", rate_bps=1000, capacity_factor=0.1)   # capacity 100
    assert b.try_acquire(300, max_wait_s=1)[0] is True          # debt 200
    assert b.try_acquire(50) == (False, 0.0)
    slept = b.acquire(50)                                       # 250 B to earn
    assert slept >= 0.2


def test_f8_debt_stays_in_the_bucket_under_interleaving(monkeypatch):
    """r2 (lens cx156): a blocking acquire() must not absorb the bucket's debt.

    Simulated clock. While acquire(50) sleeps, another caller keeps asking for
    300 B with a 2 s wait budget. When acquire() moved the shared debt into
    its private count the bucket read as merely empty, so every oversized
    request was granted about a second later: 300 B/s through a 100 B/s
    bucket. Every grant is checked against the rate at the moment it lands."""
    import types

    import bulk_downloader.download_supervisor as ds

    rate, cap = 100, 100
    clock = {"t": 0.0, "nested": False}
    grants = []                       # (time, cumulative bytes) per grant

    def fake_sleep(seconds):
        clock["t"] += seconds
        if clock["nested"] or clock["t"] >= 8.0:
            return
        clock["nested"] = True
        try:
            if bucket.try_acquire(300, max_wait_s=2)[0]:
                grants.append((clock["t"], bucket.stats()["bytes_passed"]))
        finally:
            clock["nested"] = False

    monkeypatch.setattr(ds, "time", types.SimpleNamespace(
        monotonic=lambda: clock["t"], sleep=fake_sleep))
    bucket = TokenBucket("t", rate_bps=rate)
    assert bucket.try_acquire(300) == (True, 0.0)        # debt 200 at t=0
    bucket.acquire(50)
    assert grants, "the competing caller was never granted: probe is vacuous"
    # capacity + one outstanding oversized grant is all the slack there is
    over = [(t, n) for t, n in grants if n > rate * t + cap + 300]
    assert over == [], over
    assert len(grants) <= 3           # 8 s at 100 B/s: at most 3 x 300 B more


def test_f8_blocking_caller_is_not_starved_by_oversized_waiters():
    """Real threads. A caller that waits 250 B worth (0.25 s here) must get
    through while another thread keeps requesting oversized grants."""
    import threading

    rate, cap = 1000, 100
    bucket = TokenBucket("t", rate_bps=rate, capacity_factor=cap / rate)
    stop = threading.Event()

    def greedy():
        while not stop.is_set():
            bucket.try_acquire(300, max_wait_s=0.2)
            time.sleep(0.005)

    assert bucket.try_acquire(300) == (True, 0.0)        # debt 200
    t0 = time.monotonic()
    th = threading.Thread(target=greedy, daemon=True)
    th.start()
    try:
        timer = threading.Timer(1.5, stop.set)
        timer.start()
        bucket.acquire(50)
        waited = time.monotonic() - t0
    finally:
        stop.set()
        timer.cancel()
        th.join(5)
    elapsed = time.monotonic() - t0
    assert waited < 0.9, waited       # starved until the others stop = 1.5 s
    assert bucket.stats()["bytes_passed"] <= rate * elapsed + cap + 300


def test_f8_long_run_rate_is_not_exceeded():
    """Oversized grants must not turn the limiter into a bypass."""
    rate, cap = 1000, 100
    b = TokenBucket("t", rate_bps=rate, capacity_factor=cap / rate)
    t0 = time.monotonic()
    passed = 0
    for _ in range(5):
        ok, _w = b.try_acquire(300, max_wait_s=2)
        assert ok is True
        passed += 300
    elapsed = time.monotonic() - t0
    # The last grant may still be owed (one request of debt) plus one burst.
    assert passed <= rate * elapsed + cap + 300
    assert elapsed >= 1.0


def test_f8_request_within_capacity_keeps_old_contract():
    b = TokenBucket("t", rate_bps=100)
    assert b.try_acquire(60) == (True, 0.0)
    assert b.try_acquire(60) == (False, 0.0)       # 40 left, no wait asked
    ok, waited = b.try_acquire(60, max_wait_s=0.05)
    assert (ok, waited) == (False, 0.0)            # needs 0.2 s, refused
