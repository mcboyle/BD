"""Row 665 -- terminal queue jobs remain addressable through /api/queue/v2.

CENSUS: 21 producer/consumer sites, 21 judged.  The terminal denominator is
done, skipped_duplicate, failed, error, cancelled, needs_review, stopped, and
dead_letter.  Running and pending retain their pre-existing response shapes;
an unknown future state and a URL never present in the queue remain absent.
"""
from __future__ import annotations

import importlib
import threading
import time

from flask import Flask
import pytest

BD_GATE_SCOPE = "repo-wide"

app_queue = importlib.import_module("bulk_downloader.app_queue")

_SITE = "row665-site"
# Documented zero-entropy synthetic avatar color.
_COLOR = "#000000"
_TERMINAL_STATES = (
    "done",
    "skipped_duplicate",
    "failed",
    "error",
    "cancelled",
    "needs_review",
    "stopped",
    "dead_letter",
)


class _Runner:
    def __init__(self, jobs):
        self._lock = threading.Lock()
        self.jobs = jobs
        self._recent_per_min = 0


def _queue_client(monkeypatch, jobs):
    runner = _Runner(jobs)
    monkeypatch.setattr(app_queue, "_app_runners", lambda: {_SITE: runner})
    monkeypatch.setattr(
        app_queue,
        "_app_s_cfg",
        lambda: {_SITE: {"name": "Row 665 Site"}},
    )
    monkeypatch.setattr(app_queue, "_m2_avatar_color", lambda _name: _COLOR)
    flask_app = Flask("row665-terminal-queue")
    flask_app.register_blueprint(app_queue.queue_bp)
    return flask_app.test_client(), runner


def _url(status):
    return f"https://example.invalid/row665/{status}"


def _terminal_map(body):
    return {entry["url"]: entry["status"] for entry in body.get("terminal", [])}


def test_app_queue_import_transform_control():
    """Import-only control: deliberately asserts no terminal behavior."""
    assert app_queue.queue_bp.name == "queue"


def test_done_and_skipped_duplicate_are_both_visible_by_url(monkeypatch):
    today = time.strftime("%Y-%m-%d")
    seeded = {
        _url("done"): {
            "status": "done", "filename": "done.mp4",
            "message": "complete", "ts_iso": today + "T12:00:00",
        },
        _url("skipped"): {
            "status": "skipped_duplicate", "filename": "skipped.mp4",
            "message": "duplicate", "ts_iso": today + "T12:01:00",
        },
    }
    client, runner = _queue_client(monkeypatch, seeded)
    expected = {
        _url("done"): "done",
        _url("skipped"): "skipped_duplicate",
    }
    assert {url: job["status"] for url, job in runner.jobs.items()} == expected

    body = client.get("/api/queue/v2").get_json()

    observed = _terminal_map(body)
    assert observed == expected, (
        "terminal jobs disappeared from /api/queue/v2: "
        f"seeded={expected} "
        f"observed={observed}; done_today_count={body.get('done_today_count')}"
    )
    assert len(body["terminal"]) == 2
    assert body["terminal_truncated_count"] == 0
    assert body["done_today_count"] == 1


@pytest.mark.parametrize("status", _TERMINAL_STATES[2:])
def test_each_other_terminal_state_is_visible_by_url(monkeypatch, status):
    target = _url(status)
    seeded = {
        target: {
            "status": status,
            "filename": status + ".mp4",
            "message": "terminal fixture",
            "ts_iso": "2026-09-04T12:00:00",
        },
    }
    client, runner = _queue_client(monkeypatch, seeded)
    assert len(runner.jobs) == 1
    assert runner.jobs[target]["status"] == status

    body = client.get("/api/queue/v2").get_json()

    assert _terminal_map(body) == {target: status}, body
    assert len(body["terminal"]) == 1
    assert body["terminal_truncated_count"] == 0


def test_a_never_enqueued_url_remains_absent(monkeypatch):
    present = _url("failed")
    absent = _url("never-enqueued")
    client, runner = _queue_client(
        monkeypatch,
        {present: {"status": "failed", "message": "fixture failure"}},
    )
    assert present in runner.jobs
    assert absent not in runner.jobs

    body = client.get("/api/queue/v2").get_json()

    assert [entry["url"] for entry in body["terminal"]] == [present]
    assert absent not in _terminal_map(body)


def test_running_and_pending_shapes_are_unchanged(monkeypatch):
    running_url = _url("running")
    pending_url = _url("pending")
    done_url = _url("done-control")
    jobs = {
        running_url: {
            "status": "running", "filename": "running.mp4",
            "progress": 25, "bytes_done": 10, "bytes_total": 40,
            "eta_seconds": 3, "rate_human": "10 B/s",
        },
        pending_url: {
            "status": "pending", "filename": "pending.mp4",
            "priority": 4, "queued_ts": 7,
        },
        done_url: {
            "status": "done", "filename": "done.mp4",
            "ts_iso": time.strftime("%Y-%m-%d") + "T12:00:00",
        },
    }
    client, runner = _queue_client(monkeypatch, jobs)
    assert {url: job["status"] for url, job in runner.jobs.items()} == {
        running_url: "running", pending_url: "pending", done_url: "done",
    }

    body = client.get("/api/queue/v2").get_json()

    assert body["running"] == [{
        "site_id": _SITE, "site_name": "Row 665 Site",
        "avatar_color": _COLOR, "url": running_url,
        "filename": "running.mp4", "progress": 25,
        "bytes_done": 10, "bytes_total": 40, "eta_seconds": 3,
        "rate_human": "10 B/s",
    }]
    assert body["waiting"] == [{
        "site_id": _SITE, "site_name": "Row 665 Site",
        "avatar_color": _COLOR, "url": pending_url,
        "filename": "pending.mp4", "priority": 4, "queued_ts": 7,
    }]
    assert _terminal_map(body) == {done_url: "done"}
    assert body["done_today_count"] == 1


def test_terminal_bucket_is_capped_and_reports_the_exact_remainder(monkeypatch):
    jobs = {
        _url(f"failed-{index:03d}"): {
            "status": "failed", "message": "fixture failure",
        }
        for index in range(202)
    }
    client, runner = _queue_client(monkeypatch, jobs)
    assert len(runner.jobs) == 202
    expected_urls = list(jobs)[:200]

    body = client.get("/api/queue/v2").get_json()

    assert len(body["terminal"]) == 200
    assert [entry["url"] for entry in body["terminal"]] == expected_urls
    assert body["terminal_truncated_count"] == 2


def test_an_unknown_future_status_is_not_misclassified_as_terminal(monkeypatch):
    known = _url("cancelled")
    unknown = _url("future-state")
    client, runner = _queue_client(monkeypatch, {
        known: {"status": "cancelled", "message": "cancelled"},
        unknown: {"status": "future_state", "message": "not classified"},
    })
    assert runner.jobs[known]["status"] == "cancelled"
    assert runner.jobs[unknown]["status"] == "future_state"

    body = client.get("/api/queue/v2").get_json()

    assert _terminal_map(body) == {known: "cancelled"}
    assert unknown not in _terminal_map(body)
