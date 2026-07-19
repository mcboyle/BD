"""U50 — backfill three primary-value widget keys.

The widget audit (v3.63.6 unit #5) found three widgets that showed
'—' on every deployment because the backend never emitted their
primary key:

  • avg_size_fmt    — required by the `avg_size` widget (performance)
  • eta_clear_fmt   — required by the `eta_clear` widget (system)
  • cookies_oldest_days + cookies_oldest_site — required by the
                       `cookies` widget (system)

These are *primary value* fields, not decorative slots (`*_extra`,
`*_delta`, `*_spark`). Decorative slots stay unfilled — they need
historical baselines or charting data this collector doesn't have
to invent.

v3.63.6 unit #7 ships the three collectors. Each fails open: if the
required data isn't available (no completions, queue empty, no
cookies), the key stays absent and the renderer shows '—'.

These tests verify both the present-data path and the absent-data
fail-open behaviour via fake-subprocess / in-process mocking.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock


_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_PY = _REPO_ROOT / "bulk_downloader" / "app_widgets_api.py"


# ── Source-grep contracts ─────────────────────────────────────────


def test_avg_size_collector_present():
    body = _API_PY.read_text(encoding="utf-8")
    # AVG(file_size) query is the canonical shape.
    assert re.search(r"AVG\(file_size\)", body), (
        "avg_size collector must query AVG(file_size) over history"
    )
    assert "avg_size_fmt" in body, (
        "avg_size collector must emit avg_size_fmt"
    )


def test_eta_clear_collector_present():
    body = _API_PY.read_text(encoding="utf-8")
    assert "eta_clear_fmt" in body
    assert "eta_clear_extra" in body


def test_cookies_oldest_collector_present():
    body = _API_PY.read_text(encoding="utf-8")
    assert "cookies_oldest_days" in body
    assert "cookies_oldest_site" in body
    # Reuses doctor.cookie_freshness rather than reimplementing
    # the cookie-file walk.
    assert "cookie_freshness" in body, (
        "cookies_oldest must reuse doctor.cookie_freshness, not "
        "reimplement the cookie-file walk"
    )


# ── eta_clear behaviour ─────────────────────────────────────────


def test_eta_clear_says_now_when_queue_empty():
    """queue_depth=0 → 'now' (queue empty). The widget intent is
    'how long until queue drains'; if it's already drained, 'now'
    is the most readable answer.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        import importlib as _il
        app_mod = _il.import_module("bulk_downloader.app_state")

        original_runners = getattr(app_mod, "runners", None)
        app_mod.runners = {}  # forces queue_depth = 0 via db_stats path
        try:
            # db_stats may not seed queue_depth if history table is empty;
            # we can force the queue_depth = 0 by patching db_stats.
            with patch("bulk_downloader.db.db_stats", return_value={
                "counts": {"queued": 0, "pending": 0, "stuck": 0,
                           "failed_recent": 0, "retry_pending": 0,
                           "login_required": 0, "captcha": 0}}):
                out = app_widgets_api._collect_data(None)
        finally:
            if original_runners is None:
                if hasattr(app_mod, "runners"):
                    del app_mod.runners
            else:
                app_mod.runners = original_runners

        assert out.get("eta_clear_fmt") == "now", (
            f"empty queue should give eta='now', got {out.get('eta_clear_fmt')!r}"
        )
        assert out.get("eta_clear_extra") == "queue empty"
    finally:
        sys.path.pop(0)


def test_eta_clear_absent_when_no_throughput():
    """queue_depth > 0 but files_hour = 0 → can't forecast → key
    absent → renderer shows '—'. Better than rendering 'inf' or '∞'.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        # Patch db_stats to seed queue_depth = 5 and ensure
        # dashboard_widgets.snapshot returns files_hour = 0.
        with patch("bulk_downloader.db.db_stats", return_value={
            "counts": {"queued": 5, "pending": 0, "stuck": 0,
                       "failed_recent": 0, "retry_pending": 0,
                       "login_required": 0, "captcha": 0}}):
            with patch("bulk_downloader.dashboard_widgets.snapshot",
                       return_value={"done_today": 0, "done_hour": 0,
                                     "files_hour": 0, "throughput_fmt": None,
                                     "success_rate_pct": None,
                                     "bytes_today": "0 B",
                                     "avg_speed_fmt": None,
                                     "bandwidth_fmt": None}):
                out = app_widgets_api._collect_data(None)

        # No eta_clear_fmt key when forecast is undefined.
        assert "eta_clear_fmt" not in out, (
            f"eta_clear_fmt should be absent when files_hour=0, "
            f"got {out.get('eta_clear_fmt')!r}"
        )
    finally:
        sys.path.pop(0)


def test_eta_clear_hours_format_for_short_queues():
    """A queue that drains in <1h gets 'Xm' (minutes); 1-24h gets
    'X.Yh' (hours); >24h gets 'X.Yd' (days). The human-readability
    convention matches eta_clear_fmt's display intent.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        # 30 queued at 60/hr → 0.5h → 30m
        with patch("bulk_downloader.db.db_stats", return_value={
            "counts": {"queued": 30, "pending": 0, "stuck": 0,
                       "failed_recent": 0, "retry_pending": 0,
                       "login_required": 0, "captcha": 0}}):
            with patch("bulk_downloader.dashboard_widgets.snapshot",
                       return_value={"done_today": 0, "done_hour": 0,
                                     "files_hour": 60, "throughput_fmt": None,
                                     "success_rate_pct": None,
                                     "bytes_today": "0 B"}):
                out = app_widgets_api._collect_data(None)
        assert out.get("eta_clear_fmt") == "30m", (
            f"expected '30m' for 30/60hr, got {out.get('eta_clear_fmt')!r}"
        )
    finally:
        sys.path.pop(0)


# ── avg_size behaviour ─────────────────────────────────────────


def test_avg_size_absent_when_no_history():
    """No completed rows in the last 24h → key absent → renderer '—'.

    The default test sandbox has an empty `history` table; the
    collector's AVG returns NULL; the `if r and r["avg_size"]`
    guard rejects None.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        out = app_widgets_api._collect_data(None)
        # No avg_size_fmt key — collector saw no completed rows.
        assert "avg_size_fmt" not in out or out.get("avg_size_fmt") is None
    finally:
        sys.path.pop(0)


# ── cookies_oldest behaviour ──────────────────────────────────


def test_cookies_oldest_absent_when_no_sites():
    """No sites configured → cookie_freshness returns empty list →
    no aged entries → both keys stay absent → renderer '—'.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        import importlib as _il
        app_mod = _il.import_module("bulk_downloader.app_state")

        original_s_cfg = getattr(app_mod, "s_cfg", None)
        app_mod.s_cfg = {}
        try:
            out = app_widgets_api._collect_data(None)
        finally:
            if original_s_cfg is None:
                if hasattr(app_mod, "s_cfg"):
                    del app_mod.s_cfg
            else:
                app_mod.s_cfg = original_s_cfg

        assert "cookies_oldest_days" not in out, (
            f"cookies_oldest_days leaked with empty s_cfg: "
            f"{out.get('cookies_oldest_days')!r}"
        )
        assert "cookies_oldest_site" not in out
    finally:
        sys.path.pop(0)


def test_cookies_oldest_picks_max_age_site():
    """With multiple sites of different cookie ages, the collector
    picks the OLDEST (= most due for renewal) and surfaces it.
    """
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from bulk_downloader import app_widgets_api
        importlib.reload(app_widgets_api)
        import importlib as _il
        app_mod = _il.import_module("bulk_downloader.app_state")

        # Mock cookie_freshness to return three fake sites.
        fake_checks = [
            {"site": "site_a", "age_days": 3.2, "status": "ok"},
            {"site": "site_b", "age_days": 27.8, "status": "warn"},
            {"site": "site_c", "age_days": 10.0, "status": "ok"},
        ]
        original_s_cfg = getattr(app_mod, "s_cfg", None)
        # Need a truthy s_cfg so the collector enters the branch.
        app_mod.s_cfg = {"site_a": {"cookie_file": "x"}}
        try:
            with patch("bulk_downloader.doctor.cookie_freshness",
                       return_value=fake_checks):
                out = app_widgets_api._collect_data(None)
        finally:
            if original_s_cfg is None:
                if hasattr(app_mod, "s_cfg"):
                    del app_mod.s_cfg
            else:
                app_mod.s_cfg = original_s_cfg

        # site_b is the oldest at 27.8 days.
        assert out.get("cookies_oldest_days") == 27, (
            f"expected 27 (max age), got {out.get('cookies_oldest_days')!r}"
        )
        assert out.get("cookies_oldest_site") == "site_b", (
            f"expected 'site_b' (max-age site), got "
            f"{out.get('cookies_oldest_site')!r}"
        )
    finally:
        sys.path.pop(0)
