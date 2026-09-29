"""dl95-reptyle-2: a Start that arms no worker pool must say so.

MEASURED on test2 2026-09-28 (download-95/A5-A/RESULT-reptyle.md D1): after a service restart restored two
mid-download jobs as pending, POST /api/sites/ab1d545d/start answered {"ok": true} three times while the site
stayed stopped, both jobs stayed pending, no run row was opened and nothing was logged. ``runner.start()`` has
several early returns that publish no state change and no event, and ``_do_action`` answered ``ok`` for all of
them. Fix: when ready pending work exists and the runner is not running after start(), the response carries
``blocked_by: not_started`` with the state it was left in, and the site's event log records it.
"""

from __future__ import annotations

import threading
import time

import pytest

BD_GATE_SCOPE = "module"

_SID = "dl95rep2"


class _FakeRunner:
    def __init__(self, jobs, arms=False, state="stopped"):
        self.jobs = jobs
        self._lock = threading.Lock()
        self._state = state
        self._arms = arms
        self.events = []

    def start(self):
        if self._arms:
            self._state = "running"

    def state(self):
        return self._state

    def is_rate_limited(self):
        return False

    def log_event(self, kind, message, **kw):
        self.events.append((kind, message, kw))


def _start(monkeypatch, runner):
    from bulk_downloader import app as app_mod

    monkeypatch.setitem(app_mod.runners, _SID, runner)
    monkeypatch.setattr(app_mod, "_rate_check", lambda _action: True)
    with app_mod.app.test_request_context(f"/api/sites/{_SID}/start", method="POST"):
        resp = app_mod._do_action(_SID, "start")
    status = 200
    if isinstance(resp, tuple):
        resp, status = resp
    return status, resp.get_json()


def _pending(n, retry_after=0):
    return {
        f"https://app.example/movies/{i}": {
            "status": "pending",
            "retry_after": retry_after,
        }
        for i in range(n)
    }


def test_silent_start_with_ready_pending_work_is_named(monkeypatch):
    runner = _FakeRunner(_pending(2), arms=False, state="stopped")
    status, body = _start(monkeypatch, runner)
    assert status == 200
    assert body.get("blocked_by") == "not_started", (
        f"DL95_REPTYLE_SILENT_START: Start answered {body!r} although no worker pool was armed "
        f"and 2 pending jobs were ready"
    )
    assert body == {
        "ok": True,
        "blocked_by": "not_started",
        "state": "stopped",
        "pending_ready": 2,
    }
    assert [e[0] for e in runner.events] == ["start_not_armed"], runner.events


def test_real_runner_early_return_is_named(monkeypatch, tmp_path):
    """A real SiteRunner whose start() returns early (teach already awaited) with work still pending."""
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner(
        _SID,
        {
            "name": "reptyle-fixture",
            "download_dir": str(tmp_path),
            "auto_teach_first_run": True,
        },
    )
    runner.jobs.update(_pending(2))
    runner.jobs["https://app.example/movies/taught"] = {
        "status": "needs_review",
        "auto_teach_seen": True,
    }
    runner.urls.extend(runner.jobs)
    events = []
    runner.log_event = lambda kind, message, **kw: events.append(kind)
    import bulk_downloader.runner as runner_mod

    monkeypatch.setattr(
        runner_mod, "_pending_url_ranker_accepts_media", lambda *a, **k: False
    )
    monkeypatch.setattr(
        runner_mod, "_pending_url_already_downloadable", lambda *_a: False
    )
    _status, body = _start(monkeypatch, runner)
    assert runner.state() != "running", "fixture must model a start that arms nothing"
    assert body.get("blocked_by") == "not_started" and body.get("pending_ready") == 2, (
        body
    )
    assert "start_not_armed" in events


def test_control_armed_start_stays_plain_ok(monkeypatch):
    runner = _FakeRunner(_pending(2), arms=True, state="stopped")
    assert _start(monkeypatch, runner) == (200, {"ok": True})
    assert runner.events == []


@pytest.mark.parametrize(
    "jobs",
    [
        {},
        {"https://app.example/movies/1": {"status": "done"}},
        _pending(2, retry_after=time.time() + 3600),
    ],
    ids=["empty", "all-done", "backoff-not-ready"],
)
def test_control_nothing_ready_stays_plain_ok(monkeypatch, jobs):
    runner = _FakeRunner(jobs, arms=False, state="idle")
    assert _start(monkeypatch, runner) == (200, {"ok": True})
    assert runner.events == []
