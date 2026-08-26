"""T47 regression — storage_tier.find_candidates UTC cutoff.

Bug (pre-v3.63.1):
  `find_candidates` built `cutoff_iso` via
  `time.strftime(..., time.localtime(cutoff_ts))`, then compared
  it against the `queue.ts_updated` TEXT column, which is written
  by SQLite as `strftime('%Y-%m-%dT%H:%M:%S','now')` — UTC. On any
  non-UTC host the cutoff string was offset from the row strings
  by the host's UTC offset, causing files to be missed or extra-
  included by the per-site migration sweep.

Fix (v3.63.1): use `time.gmtime` instead. This pairs with the
WAL/UTC invariant (LESSONS_LEARNED A2): SQLite times are UTC; any
Python-side cutoff compared against them must be built in UTC.

Test strategy:
  - Force the process timezone to one with a large UTC offset
    (Pacific/Auckland, +13h DST / +12h std).
  - Insert two `queue` rows: one ~5 hours AFTER the intended UTC
    cutoff (must be excluded — too recent) and one ~5 hours BEFORE
    (must be included — old enough).
  - Run `find_candidates`; assert exactly the BEFORE row comes
    back. Under the localtime bug, the AFTER row would be
    incorrectly selected on this host.

The test uses `fresh_app` to get an initialized DB; queue rows
are inserted directly with deliberately-controlled `ts_updated`
strings rather than through `queue_upsert` (which would stamp the
real current time).
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import time
from contextlib import contextmanager

import pytest


# ── Helper ───────────────────────────────────────────────────────────

@contextmanager
def _force_tz(tz_name):
    """Set process TZ and restore both the environment and libc timezone."""
    if not hasattr(time, "tzset"):
        raise RuntimeError(
            "UNKNOWN: time.tzset is unavailable, so a non-UTC timezone cannot "
            "be forced"
        )
    old = os.environ.get("TZ")
    os.environ["TZ"] = tz_name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


# ── The regression ──────────────────────────────────────────────────

def test_find_candidates_uses_utc_cutoff_not_localtime(fresh_app):
    """Boundary row 5h after UTC cutoff must NOT be selected,
    even when host TZ is far from UTC."""
    with _force_tz("Pacific/Auckland"):
        from bulk_downloader.storage_tier import find_candidates

        # Build UTC strings around a known cutoff. age_days=30, so
        # the cutoff is 30 days before now (UTC). We place:
        #   row_before  -> 30 days + 5h ago (UTC) — older than
        #                  cutoff, MUST be selected.
        #   row_after   -> 30 days - 5h ago (UTC) — newer than
        #                  cutoff, MUST NOT be selected.
        # On a +13h host (Auckland), the buggy localtime cutoff
        # string sits 13h ahead of true UTC, so the row 5h newer
        # than the UTC cutoff would lexically compare LESS than
        # the buggy cutoff string and get incorrectly selected.
        now_utc = dt.datetime.now(dt.timezone.utc)
        cutoff = now_utc - dt.timedelta(days=30)
        row_before = (cutoff - dt.timedelta(hours=5)).strftime(
            "%Y-%m-%dT%H:%M:%S")
        row_after = (cutoff + dt.timedelta(hours=5)).strftime(
            "%Y-%m-%dT%H:%M:%S")

        cx = sqlite3.connect("downloader_history.db")
        cx.execute(
            "INSERT INTO queue(site_id,url,status,filename,"
            "file_size,ts_updated) VALUES(?,?,?,?,?,?)",
            ("t47", "http://x/before", "done",
             "/tmp/before.mp4", 0, row_before))
        cx.execute(
            "INSERT INTO queue(site_id,url,status,filename,"
            "file_size,ts_updated) VALUES(?,?,?,?,?,?)",
            ("t47", "http://x/after", "done",
             "/tmp/after.mp4", 0, row_after))
        cx.commit()
        cx.close()

        urls = {r["url"]
                for r in find_candidates("t47", age_days=30)}

        assert "http://x/before" in urls, (
            "row 5h older than cutoff must be included")
        assert "http://x/after" not in urls, (
            "row 5h newer than cutoff must NOT be included — "
            "this is the localtime bug if it fires")


def test_forced_timezone_restores_environment_and_libc_state():
    """The worker must not remain in the forced zone after the gate returns."""
    if not hasattr(time, "tzset"):
        with pytest.raises(RuntimeError, match="UNKNOWN"):
            with _force_tz("Pacific/Auckland"):
                pass
        return
    before_env = os.environ.get("TZ")
    before_state = (time.tzname, time.timezone, time.daylight)
    zone = "Pacific/Auckland"
    if before_env == zone:
        zone = "America/Los_Angeles"
    with _force_tz(zone):
        assert os.environ.get("TZ") == zone
        assert (time.tzname, time.timezone, time.daylight) != before_state
    assert os.environ.get("TZ") == before_env
    assert (time.tzname, time.timezone, time.daylight) == before_state


def test_missing_tzset_is_an_unknown_state(monkeypatch):
    """Capability absence is a loud third state, never a passing return."""
    with monkeypatch.context() as patch:
        patch.delattr(time, "tzset", raising=False)
        with pytest.raises(RuntimeError, match="UNKNOWN"):
            with _force_tz("Pacific/Auckland"):
                pass


# ── Source-level guard ──────────────────────────────────────────────

def test_storage_tier_no_localtime_in_find_candidates():
    """Belt-and-braces: grep the source so a future edit can't
    silently re-introduce time.localtime() inside find_candidates.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "bulk_downloader" / "storage_tier.py").read_text(
               encoding="utf-8")
    # find_candidates is the only place this matters; storage_tier.py
    # has no other localtime use — guard the file.
    assert "time.localtime(" not in src, (
        "time.localtime() call reintroduced in storage_tier.py "
        "— see T47 / LESSONS_LEARNED A2: cutoff times compared "
        "against the UTC ts_updated column must be built with "
        "time.gmtime")
