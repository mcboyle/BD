"""Rehydrated jobs carried a UTC stamp into a LOCAL day window.

THE DEFECT. `runner_queue.py:_restore_queue` copied the queue table's
`ts_updated` verbatim into `jobs[url]["ts_iso"]`. db.py writes that column with
SQLite ``strftime('%Y-%m-%dT%H:%M:%S','now')`` -- UTC. All four day-window
consumers compare it against a LOCAL ``time.strftime("%Y-%m-%d")``
(app.py:3912, app_dashboard.py:66 and :203, app_queue.py:228). Two clocks, one
comparison.

MEASURED, not near-midnight hand-waving: on America/Los_Angeles a job completed
at 17:05 local rehydrates as ``2026-08-01T00:05:02`` and is tested against
``2026-07-31`` -- False. It simply drops out of the count, seven hours from any
midnight. Sweeping 24 hourly instants per zone: Tokyo loses 9 of 24, Kiritimati
14, Los_Angeles 7, Berlin 2, UTC 0. East-of-UTC zones only LOSE jobs;
west-of-UTC zones also GAIN jobs that belong to tomorrow.

WHY THE EXISTING GUARD COULD NOT SEE IT. tests/test_cut31_done_today_iso.py's
T3 asserts ``jobs[url]["ts_iso"] == ts_updated`` -- EQUALITY with the raw
column -- and its docstring calls that "tz-robust" precisely because a
startswith(local today) assertion would have been flaky. That choice made the
test green in every timezone, including the ones where the defect is maximal.
It pinned the copy, not the contract. Section 0: the denominator excluded the
subject. T3 is updated by this cut to assert equality with the LOCAL rendering,
which is still exact and still not flaky.

WHY THESE TESTS SET TZ EXPLICITLY. This container is Etc/UTC, where the fix is
a NO-OP and every assertion below passes on pristine source. A test written
without TZ manipulation would prove nothing here and would only ever fail on
the deploy box. Both signs are exercised, because the failure is asymmetric.

WHY THE CONVERSION IS AT THE COPY SITE. `ts_updated` has non-day-window
consumers that build UTC cutoffs (storage_tier.py:310, cost_economics.py:185),
and db.py:1792 uses the column as a monotonic ORDER BY cursor -- stamping
'localtime' there would give it mixed semantics and break a DST fall-back.
`ts_iso` has exactly four readers and all four want LOCAL.
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_URL = "https://example.invalid/cut42.mp4"

# One east of UTC and one west, because the defect is asymmetric: east-of-UTC
# only loses jobs, west-of-UTC also gains them from the next day.
_EAST = "Asia/Tokyo"        # UTC+9, no DST
_WEST = "America/Los_Angeles"  # UTC-7/-8, has DST


@contextmanager
def _tz(name):
    """Run the block under a specific local timezone, restoring afterwards.

    time.tzset() makes the change visible to time.strftime and to
    datetime.astimezone() in-process, which is what both the fix and the
    consumers use.
    """
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset unavailable on this platform; TZ cannot be "
                    "forced, so this test would measure nothing")
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def _conv(s):
    from bulk_downloader.runner_util import _utc_iso_to_local_iso
    return _utc_iso_to_local_iso(s)


@pytest.mark.parametrize("zone,stamp_utc,expected_local", [
    # 2026-08-01 00:05:02 UTC is 09:05 the same day in Tokyo (+9) ...
    (_EAST, "2026-08-01T00:05:02", "2026-08-01T09:05:02"),
    # ... and 17:05 the PREVIOUS day in Los Angeles (-7, PDT).
    (_WEST, "2026-08-01T00:05:02", "2026-07-31T17:05:02"),
])
def test_utc_stamp_is_rendered_in_local_time(zone, stamp_utc, expected_local):
    """The conversion itself, pinned in both directions across a date boundary."""
    with _tz(zone):
        assert _conv(stamp_utc) == expected_local


def test_the_defect_would_change_the_day_in_a_real_zone():
    """The control that makes the other assertions meaningful.

    If the raw UTC stamp and its LOCAL rendering fell on the same DATE, this
    whole file would be asserting nothing. Proven, not assumed.
    """
    with _tz(_WEST):
        raw = "2026-08-01T00:05:02"
        assert raw[:10] != _conv(raw)[:10], (
            "raw and converted stamps fall on the same date, so this fixture "
            "cannot distinguish the broken tree from the fixed one")


def test_empty_stays_empty_and_malformed_is_unchanged():
    """Never invent a stamp.

    Empty must stay empty. An unparseable value is returned UNCHANGED, which is
    a no-op relative to the old verbatim copy -- so the fix cannot make a row
    that used to count stop counting. Neither may become today: test_cut40's G4
    pins `or today_iso` as the seductive wrong fix.
    """
    with _tz(_WEST):
        assert _conv("") == ""
        assert _conv(None) == ""
        for junk in ("not-a-stamp", "2026-08-01", "2026-08-01 00:05:02"):
            assert _conv(junk) == junk, junk
        today = time.strftime("%Y-%m-%d")
        assert not _conv("not-a-stamp").startswith(today)


@pytest.mark.parametrize("zone", [_EAST, _WEST])
def test_restart_rehydrates_a_locally_dated_stamp(zone, clean_workdir):
    """RED on pristine source, in a non-UTC zone: the real restart path.

    Drives db.queue_upsert -> SiteRunner.__init__ -> _restore_queue, then
    asserts the rehydrated ts_iso is on the LOCAL date -- which is exactly what
    every consumer tests with startswith(today_iso).
    """
    from bulk_downloader.db import db_init, queue_upsert, queue_load
    from bulk_downloader.runner import SiteRunner

    with _tz(zone):
        db_init()
        sid = f"cut42_{zone.split('/')[-1].lower()}"
        queue_upsert(sid, _URL, status="done", message="ok",
                     filename="a.mp4", file_size=1)
        rows = {r["url"]: r for r in queue_load(sid)}
        db_ts = rows[_URL].get("ts_updated") or ""
        assert db_ts, "queue row has no ts_updated; the fixture is wrong, not the fix"

        r = SiteRunner(sid, {"name": sid})
        got = r.jobs[_URL].get("ts_iso") or ""
        assert got == _conv(db_ts), (
            f"rehydrated ts_iso={got!r} is not the LOCAL rendering of "
            f"ts_updated={db_ts!r}")

        today_local = time.strftime("%Y-%m-%d")
        assert got.startswith(today_local), (
            f"a job completed just now does not carry today's LOCAL date "
            f"({today_local}); ts_iso={got!r}, ts_updated={db_ts!r}. Every "
            f"day-window consumer filters with exactly this comparison.")
