from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone


_FIXED_NOW = datetime(2030, 1, 2, 12, tzinfo=timezone.utc).timestamp()


class _FixedUtcClock:
    def __init__(self, now):
        self._now = now

    def time(self):
        return self._now

    @staticmethod
    def gmtime(value=None):
        return time.gmtime(value)

    @staticmethod
    def strftime(fmt, value):
        return time.strftime(fmt, value)


def _seed_widget_db(monkeypatch, tmp_path, *, now=None):
    from bulk_downloader import db, migrations

    now = time.time() if now is None else now

    def iso_utc(seconds_ago):
        return time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(now - seconds_ago))

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "widgets.db"))
    db.db_init()
    with db.db_conn() as cx:
        migrations._m3(cx)
        cx.executemany(
            """
            INSERT INTO history(site_id,site_name,url,status,file_size,ts)
              VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("alpha", "Alpha", "a1", "done", 1000, iso_utc(10 * 60)),
                ("alpha", "Alpha", "a2", "done", 2000, iso_utc(2 * 3600)),
                ("alpha", "Alpha", "a3", "failed", 0, iso_utc(20 * 60)),
                ("other", "Other", "o1", "done", 9000, iso_utc(5 * 60)),
            ],
        )
        cx.executemany(
            """
            INSERT INTO library(site_id,file_path,file_size,watched,rating,
                                resolution,studio,added_at,file_exists)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("alpha", "/a/1.mp4", 1000, 1, 5, "1080p", "Studio A", now, 1),
                ("alpha", "/a/2.mp4", 2000, 0, None, "1080p", "Studio A", now, 1),
                ("other", "/o/1.mp4", 9000, 0, None, "2160p", "Studio O", now, 0),
            ],
        )
    return db


def test_history_and_library_primaries_are_real_and_site_scoped(
        monkeypatch, tmp_path):
    from bulk_downloader import app_state, app_widgets_api, library

    _seed_widget_db(monkeypatch, tmp_path, now=_FIXED_NOW)
    monkeypatch.setattr(app_widgets_api, "time", _FixedUtcClock(_FIXED_NOW))
    monkeypatch.setattr(app_state, "runners", {})
    monkeypatch.setattr(app_state, "s_cfg", {
        "alpha": {"name": "Alpha"}, "other": {"name": "Other"},
    })
    monkeypatch.setattr(library, "_SCHEMA_READY", True)

    out = app_widgets_api._collect_data("alpha")

    assert out["done_today"] == 2
    assert out["done_hour"] == 1
    assert out["bytes_today_fmt"] == "2.9 KB"
    assert out["files_hour"] == 1
    assert out["success_rate"] == 66.7
    assert out["failures_hr"] == 1
    assert out["avg_size_fmt"] == "1.5 KB"
    assert out["lib_total"] == 2
    assert out["lib_size_fmt"] == "2.9 KB"
    assert out["lib_watched_pct"] == 50.0
    assert out["lib_unrated"] == 1
    assert out["lib_missing"] == 0
    assert out["lib_recent"] == 2
    assert out["lib_top_studio"] == "Studio A"
    assert out["avg_quality_label"] == "1080p"
    assert "2160p" not in (out.get("avg_quality_breakdown") or "")


class _LiveHealthRunner:
    config = {"max_concurrent": 4}

    def __init__(self):
        now = time.time()
        self._lock = threading.Lock()
        self._captcha_encounters_lock = threading.Lock()
        self.jobs = {
            "running": {"status": "running", "last_progress_at": now - 3700},
            "retry": {"status": "needs_review", "next_auto_retry_at": now + 60},
            "failed-retry": {"status": "failed", "next_auto_retry_at": now + 60},
            "pending-delay": {"status": "pending", "retry_after": now + 60},
            "done-timer": {"status": "done", "next_auto_retry_at": now + 60},
            "stopped-timer": {"status": "stopped", "retry_after": now + 60},
            "running-timer": {"status": "running", "next_auto_retry_at": now + 60},
            "captcha-review": {
                "status": "needs_review",
                "captcha_type": "turnstile",
                "message": "Captcha challenge detected",
            },
        }
        owner = self

        class _LockCheckedEncounters:
            def __iter__(self):
                if not owner._captcha_encounters_lock.locked():
                    raise RuntimeError("captcha encounters read without lock")
                return iter([now - 60, now - 90000])

        self._captcha_encounters = _LockCheckedEncounters()
        self._event_log = [
            {"kind": "captcha", "ts": now - 60},
            {"kind": "captcha", "ts": now - 30},
            {"kind": "captcha", "url": "old", "ts": now - 90000},
        ]

    def get_status(self, light=False):
        return {
            "counts": {"pending": 0, "running": 1},
            "awaiting_manual_login": False,
            "awaiting_manual_download": False,
            "hung_workers": [],
        }

    def _current_throughput_bps(self):
        return 4096.0


def test_live_health_and_captcha_fields_use_current_runner_state(
        monkeypatch, tmp_path):
    from bulk_downloader import app_state, app_widgets_api

    _seed_widget_db(monkeypatch, tmp_path)
    monkeypatch.setattr(app_state, "runners", {"alpha": _LiveHealthRunner()})
    monkeypatch.setattr(app_state, "s_cfg", {"alpha": {"name": "Alpha"}})

    out = app_widgets_api._collect_data("alpha")

    assert out["action_req"] == 1
    assert out["stuck"] == 1
    assert out["retries_pending"] == 3
    assert out["captcha_24h"] == 1
    assert out["queue_depth"] == 0
    assert out["workers_active"] == 1


def test_all_36_widget_primary_contracts_are_explicit(monkeypatch, tmp_path):
    from bulk_downloader import app_state, app_widgets_api

    _seed_widget_db(monkeypatch, tmp_path)
    monkeypatch.setattr(app_state, "runners", {"alpha": _LiveHealthRunner()})
    monkeypatch.setattr(app_state, "s_cfg", {"alpha": {"name": "Alpha"}})

    out = app_widgets_api._collect_data("alpha")

    assert len(app_widgets_api.WIDGET_PRIMARY_FIELDS) == 36
    assert set(app_widgets_api.WIDGET_PRIMARY_FIELDS.values()) <= set(out)


def test_site_scope_never_falls_back_to_global_library_values(
        monkeypatch, tmp_path):
    from bulk_downloader import app_state, app_widgets_api, library

    _seed_widget_db(monkeypatch, tmp_path)
    monkeypatch.setattr(app_state, "runners", {})
    monkeypatch.setattr(app_state, "s_cfg", {"alpha": {"name": "Alpha"}})
    monkeypatch.setattr(library, "library_stats", lambda: {
        "total_files": 999, "total_size": 999, "watched_files": 999,
        "missing_files": 999, "unrated_files": 999,
    })
    monkeypatch.setattr(
        app_widgets_api, "_collect_library_data",
        lambda site_id: (_ for _ in ()).throw(RuntimeError("scoped unavailable")),
    )

    out = app_widgets_api._collect_data("alpha")

    assert out["lib_total"] is None
    assert out["lib_size_fmt"] is None
    assert out["lib_watched_pct"] is None
    assert out["avg_quality_label"] is None


def test_site_scope_without_runner_does_not_leak_global_rate(
        monkeypatch, tmp_path):
    from bulk_downloader import app_state, app_widgets_api, dashboard_widgets

    _seed_widget_db(monkeypatch, tmp_path)
    monkeypatch.setattr(app_state, "runners", {})
    monkeypatch.setattr(app_state, "s_cfg", {"alpha": {"name": "Alpha"}})
    monkeypatch.setattr(dashboard_widgets, "snapshot", lambda *args, **kwargs: {
        "bytes_per_sec": 999999, "success_pct": 12.5,
        "active_workers": 1,
    })

    out = app_widgets_api._collect_data("alpha")

    assert out["throughput_fmt"] is None
    assert out["bandwidth_fmt"] is None
    assert out["avg_speed_fmt"] is None
    assert out["success_rate"] == 66.7


def test_site_history_failure_does_not_leak_global_success_rate(
        monkeypatch, tmp_path):
    from bulk_downloader import app_state, app_widgets_api, dashboard_widgets

    _seed_widget_db(monkeypatch, tmp_path)
    monkeypatch.setattr(app_state, "runners", {"alpha": _LiveHealthRunner()})
    monkeypatch.setattr(app_state, "s_cfg", {"alpha": {"name": "Alpha"}})
    monkeypatch.setattr(dashboard_widgets, "snapshot", lambda *args, **kwargs: {
        "bytes_per_sec": 999999, "success_pct": 12.5,
        "active_workers": 1,
    })
    monkeypatch.setattr(
        app_widgets_api, "_collect_history_data",
        lambda site_id: (_ for _ in ()).throw(RuntimeError("history unavailable")),
    )

    out = app_widgets_api._collect_data("alpha")

    assert out["success_rate"] is None


def test_global_empty_fleet_does_not_retain_singleton_rate(
        monkeypatch, tmp_path):
    from bulk_downloader import app_state, app_widgets_api, dashboard_widgets

    _seed_widget_db(monkeypatch, tmp_path)
    monkeypatch.setattr(app_state, "runners", {})
    monkeypatch.setattr(app_state, "s_cfg", {})
    monkeypatch.setattr(dashboard_widgets, "snapshot", lambda *args, **kwargs: {
        "bytes_per_sec": 999999, "success_pct": 12.5,
        "active_workers": 1,
    })

    out = app_widgets_api._collect_data(None)

    assert out["throughput_fmt"] is None
    assert out["bandwidth_fmt"] is None
    assert out["avg_speed_fmt"] is None


def test_captcha_encounter_recorder_keeps_a_true_24_hour_window():
    from bulk_downloader.runner_challenge import ChallengeMixin

    class _Challenge(ChallengeMixin):
        pass

    challenge = _Challenge()
    now = time.time()
    challenge._captcha_encounters_lock = threading.Lock()

    class _LockCheckedDeque(deque):
        def popleft(self):
            assert challenge._captcha_encounters_lock.locked()
            return super().popleft()

        def append(self, value):
            assert challenge._captcha_encounters_lock.locked()
            return super().append(value)

    challenge._captcha_encounters = _LockCheckedDeque([now - 90000, now - 60])

    challenge._record_captcha_encounter(now)

    assert list(challenge._captcha_encounters) == [now - 60, now]
