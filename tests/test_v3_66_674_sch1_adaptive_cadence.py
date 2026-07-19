"""v3.66.674 -- SCH-1: self-tuning capture cadence from observed change-rate.

capture_schedules fired on a fixed cadence_hours. SCH-1 adapts the NEXT interval
to a site's measured change-rate, using the signal already flowing through the
scheduler: enqueue_fn returns the number of items queued -- n>0 means the site
had new content (it changed). This cut adds:
  * adaptive_cadence_hours(base, change_count, ...): bounded policy -- a site that
    changes often shortens toward min_hours; a quiet one lengthens toward max_hours.
  * record_change / change_count: a per-site change ledger.
  * _fire_one records a change when n>0 and, when BD_ADAPTIVE_CADENCE is set,
    computes next_run_ts from the adaptive cadence (default OFF -> fixed, byte-identical).

Isolated temp DB; zero-arg tests.
"""
from __future__ import annotations

import tempfile
import time

import bulk_downloader.db as db
from bulk_downloader import capture_schedules as cs


def _isolated_db():
    db.DB_PATH = tempfile.mktemp(prefix="sch1_", suffix=".db")
    db.db_init()
    cs._ensure_table()


def test_adaptive_cadence_shortens_on_frequent_change():
    # base 24h; many changes -> toward the 1h floor; bounds respected
    fast = cs.adaptive_cadence_hours(24, change_count=10, window_days=7,
                                     min_hours=1, max_hours=168)
    slow = cs.adaptive_cadence_hours(24, change_count=0, window_days=7,
                                     min_hours=1, max_hours=168)
    assert fast < 24 <= slow, (fast, slow)
    assert fast >= 1 and slow <= 168


def test_adaptive_cadence_respects_bounds():
    assert cs.adaptive_cadence_hours(24, change_count=999, min_hours=6,
                                     max_hours=48) >= 6
    assert cs.adaptive_cadence_hours(24, change_count=0, min_hours=6,
                                     max_hours=48) <= 48


def test_change_ledger_round_trip():
    saved = db.DB_PATH
    try:
        _isolated_db()
        assert cs.change_count("s1", window_days=7) == 0
        cs.record_change("s1")
        cs.record_change("s1")
        assert cs.change_count("s1", window_days=7) == 2
        assert cs.change_count("other", window_days=7) == 0
    finally:
        db.DB_PATH = saved


def test_fire_one_records_change_and_adapts_when_enabled():
    saved = db.DB_PATH
    saved_cfg = cs._adaptive_cfg_for
    try:
        _isolated_db()
        sid = cs.add_schedule(site_id="s1", cadence_hours=24, urls=["u"])
        assert sid
        # site opts in (via the site-config reader, patched here)
        cs._adaptive_cfg_for = lambda site_id: {"adaptive": True, "min_h": 1,
                                                "max_h": 168}
        # enqueue_fn reports 5 new items -> a change; adaptive cadence should
        # schedule the next run sooner than the fixed 24h would.
        now = time.time()
        res = cs.run_one(sid, enqueue_fn=lambda s, u: 5, now=now)
        assert res["ok"], res
        assert cs.change_count("s1", window_days=7) >= 1
        with db.db_conn() as cx:
            nxt = cx.execute("SELECT next_run_ts FROM capture_schedules WHERE id=?",
                             (sid,)).fetchone()[0]
        assert nxt < now + 24 * 3600, "adaptive cadence should pull the next run in"
    finally:
        cs._adaptive_cfg_for = saved_cfg
        db.DB_PATH = saved


def test_fire_one_default_off_is_fixed_cadence():
    saved = db.DB_PATH
    try:
        _isolated_db()
        # no site opt-in -> _adaptive_cfg_for returns adaptive=False (no sites file)
        sid = cs.add_schedule(site_id="s2", cadence_hours=24, urls=["u"])
        now = time.time()
        cs.run_one(sid, enqueue_fn=lambda s, u: 5, now=now)
        with db.db_conn() as cx:
            nxt = cx.execute("SELECT next_run_ts FROM capture_schedules WHERE id=?",
                             (sid,)).fetchone()[0]
        assert abs(nxt - (now + 24 * 3600)) < 2, "default must stay fixed cadence"
    finally:
        db.DB_PATH = saved
