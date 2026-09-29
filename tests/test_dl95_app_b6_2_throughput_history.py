"""dl95-app-B6-2: Home "THROUGHPUT · LAST HOUR" said "No activity yet" beside a live rate.

test2 23:4xZ (harness-work/DOT95-LANE/live-dl95-file-examples-4/home-settled45s.png, and re-measured
by A8-A): /api/dashboard/v2/sparkline answered {"current": 135460, "history": [ONE point]} while
downloads ran. The route asks dashboard_widgets for get_history("bytes_per_sec"), which never
existed, so it always fell back to a single point; ThroughputSparkline draws the chart only for
more than one sample and otherwise prints "No activity yet" -- under a non-zero rate.

The history is one-minute buckets of bytes written over the last hour. An hour with no bytes has
no history, so "No activity yet" still means exactly that.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from bulk_downloader import dashboard_widgets as dw  # noqa: E402


class _Clock:
    def __init__(self, t):
        self.t = float(t)

    def time(self):
        return self.t


def _fresh(monkeypatch, t=1_790_000_000):
    clock = _Clock(t)
    monkeypatch.setattr(dw.time, "time", clock.time)
    monkeypatch.setattr(dw, "_WIDGETS", None)
    return clock


def _sparkline():
    from bulk_downloader import app as a
    return a.app.test_client().get("/api/dashboard/v2/sparkline").get_json()


def test_an_active_download_gives_the_sparkline_an_hour_of_samples(monkeypatch):
    clock = _fresh(monkeypatch)
    for _ in range(10):
        dw.note_progress("site-a", 1_000_000)
        clock.t += 5
    body = _sparkline()
    assert body["current"] > 0, body
    hist = body["history"]
    # The SPA draws the chart only for more than one sample (hasData).
    assert len(hist) > 1, ("DL95-APP-B6-2: a live rate with a one-point history renders "
                           "'No activity yet': %r" % (body,))
    assert len(hist) == dw.HISTORY_BUCKETS, len(hist)
    assert [p["ts"] for p in hist] == sorted(p["ts"] for p in hist)
    assert hist[-1]["value"] > 0 and hist[0]["value"] == 0, hist[-3:]


def test_bytes_land_in_their_own_minute_and_age_out_after_an_hour(monkeypatch):
    clock = _fresh(monkeypatch)
    dw.note_progress("site-a", 6_000)          # minute 0: 6000 B -> 100 B/s
    clock.t += 30 * 60
    hist = dw.get_widgets().get_history("bytes_per_sec")
    by_ts = {p["ts"]: p["value"] for p in hist}
    start = int(1_790_000_000 // 60) * 60
    assert by_ts.get(start) == 100, hist
    assert sum(1 for v in by_ts.values() if v) == 1, hist
    clock.t += 31 * 60                         # the sample is now > 1 h old
    assert dw.get_widgets().get_history("bytes_per_sec") == []


def test_negative_an_idle_hour_still_reads_no_activity(monkeypatch):
    _fresh(monkeypatch)
    body = _sparkline()
    assert body["current"] == 0
    assert len(body["history"]) <= 1, body     # the SPA's "No activity yet" branch


def test_negative_an_unknown_metric_has_no_history(monkeypatch):
    _fresh(monkeypatch)
    dw.note_progress("site-a", 1_000)
    assert dw.get_widgets().get_history("success_pct") == []
