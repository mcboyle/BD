"""dl95-dailymotion-4: failed jobs must count in ACTION REQUIRED on the site page.

O1508 rerun on test2 (harness-work/UIUX-20260928/download-95/A5-A/RESULT-dailymotion.md, D4): after both dailymotion
jobs failed, the site overview showed "ACTION REQUIRED 0 none" (dm10/DM__site-after-fail.png), because
_collect_live_health counted only captcha/login needs_review items. A failed job with no auto-retry scheduled is
waiting on the operator; a failed job whose auto-retry is scheduled stays in "Retries pending" only.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"
import threading
import time


class _Runner:
    config = {"max_concurrent": 2}

    def __init__(self, jobs):
        self._lock = threading.Lock()
        self._captcha_encounters_lock = threading.Lock()
        self._captcha_encounters = []
        self.jobs = jobs

    def get_status(self, light=False):
        return {"counts": {"pending": 0, "running": 0},
                "awaiting_manual_login": False, "awaiting_manual_download": False}

    def _current_throughput_bps(self):
        return 0.0


def _health(jobs):
    from bulk_downloader import app_widgets_api
    return app_widgets_api._collect_live_health({"dailymotion": _Runner(jobs)})


def test_failed_jobs_count_as_action_required():
    # The D4 state: two jobs failed "[page_shape] No download button found", nothing retrying.
    out = _health({
        "j1": {"status": "failed", "message": "[page_shape] No download button found"},
        "j2": {"status": "failed", "message": "[page_shape] No download button found", "next_auto_retry_at": 0},
    })
    assert out["action_req"] == 2, f"dl95-dailymotion-4: 2 failed jobs shown as ACTION REQUIRED {out['action_req']}"
    assert out["action_req_breakdown"] == "2 failed jobs", out["action_req_breakdown"]


def test_failed_job_with_scheduled_auto_retry_is_not_action_required():
    now = time.time()
    out = _health({"j1": {"status": "failed", "next_auto_retry_at": now + 600}})
    assert out["action_req"] == 0 and out["retries_pending"] == 1, out
    assert out["action_req_breakdown"] == "none"


def test_review_items_and_failed_jobs_add_up_and_say_both():
    out = _health({
        "cap": {"status": "needs_review", "captcha_type": "turnstile"},
        "f1": {"status": "failed"},
        "done": {"status": "done"},
        "stopped": {"status": "stopped"},
        "plain-review": {"status": "needs_review", "message": "low resolution"},
    })
    assert out["action_req"] == 2, out
    assert out["action_req_breakdown"] == "1 captcha/login review item, 1 failed job", out["action_req_breakdown"]


def test_site_scope_reaches_the_widget_payload(monkeypatch, tmp_path):
    from bulk_downloader import app_state, app_widgets_api, db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "w.db"))
    db.db_init()
    monkeypatch.setattr(app_state, "runners", {
        "dailymotion": _Runner({"j1": {"status": "failed"}, "j2": {"status": "failed"}}),
        "other": _Runner({"x": {"status": "failed"}}),
    })
    monkeypatch.setattr(app_state, "s_cfg", {"dailymotion": {"name": "dailymotion"}, "other": {"name": "other"}})
    assert app_widgets_api._collect_data("dailymotion")["action_req"] == 2, "site page must count only its own failures"
