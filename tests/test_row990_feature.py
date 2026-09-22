"""Row 990 -- Queue Starvation & Priority Inversion Visualizer.

The runner's dispatch order is ``runner.urls`` (front runs first) and each
job carries ``priority`` in {"normal", "high"} plus ``last_progress_at``
(creation time for a job that has never started).  Two pathologies are
invisible in /api/queue/v2 today:

  * priority inversion -- a "high" pending job sits behind a "normal"
    pending job in dispatch order (happens after reorder_urls or when
    priority is set via queue_upsert without the front-insert);
  * starvation -- a pending job has waited longer than a threshold.

``bulk_downloader.queue_starvation.analyze_queue`` is the pure detector;
``/api/queue/starvation`` is the per-site report the SPA renders.
"""
import importlib
import threading

import pytest
from flask import Flask

BD_GATE_SCOPE = "repo-wide"

app_queue = importlib.import_module("bulk_downloader.app_queue")

NOW = 1_000_000.0


def _detector():
    """The pure detector, or a RED for the row's reason: on base nothing
    owns runner.urls x runner.jobs -> {inversions, starved}. The route
    tests below are the behavioural RED (no queue endpoint reports
    a high job parked behind normal ones); this keeps the unit tests from
    dying at collection with a bare ImportError."""
    try:
        return importlib.import_module("bulk_downloader.queue_starvation")
    except ImportError:
        pytest.fail("row990: no queue starvation / priority-inversion detector "
                    "owns runner.urls x runner.jobs (bulk_downloader.queue_starvation absent)")


def _job(priority="normal", status="pending", age=0.0):
    return {"status": status, "priority": priority,
            "last_progress_at": NOW - age, "retries": 0, "retry_after": 0}


# ---------------------------------------------------------------- pure detector

def test_inversion_high_behind_normal():
    urls = ["n1", "h1"]
    jobs = {"n1": _job("normal"), "h1": _job("high")}
    rep = _detector().analyze_queue(urls, jobs, now=NOW)
    assert [i["url"] for i in rep["inversions"]] == ["h1"]
    inv = rep["inversions"][0]
    assert inv["position"] == 1 and inv["blockers"] == 1
    assert inv["first_blocker"] == "n1"
    assert rep["inversion_count"] == 1


def test_no_inversion_when_high_leads():
    """Negative control: the same jobs in the order the runner itself
    produces (set_priority front-inserts) report nothing."""
    urls = ["h1", "n1"]
    jobs = {"n1": _job("normal"), "h1": _job("high")}
    rep = _detector().analyze_queue(urls, jobs, now=NOW)
    assert rep["inversions"] == [] and rep["inversion_count"] == 0


def test_inversion_ignores_non_pending_blockers():
    """A running or done job ahead of a high job is not a blocker --
    only pending normal jobs still compete for the dispatcher."""
    urls = ["r1", "d1", "h1"]
    jobs = {"r1": _job("normal", status="running"),
            "d1": _job("normal", status="done"),
            "h1": _job("high")}
    rep = _detector().analyze_queue(urls, jobs, now=NOW)
    assert rep["inversions"] == []


def test_starvation_by_wait_age():
    urls = ["old", "young"]
    jobs = {"old": _job(age=5000), "young": _job(age=5)}
    rep = _detector().analyze_queue(urls, jobs, now=NOW, starvation_seconds=3600)
    assert [s["url"] for s in rep["starved"]] == ["old"]
    assert rep["starved"][0]["wait_seconds"] == pytest.approx(5000)
    assert rep["starved_count"] == 1
    assert rep["oldest_wait_seconds"] == pytest.approx(5000)
    assert rep["pending_count"] == 2


def test_starvation_negative_control_under_threshold():
    urls = ["a", "b"]
    jobs = {"a": _job(age=100), "b": _job(age=200)}
    rep = _detector().analyze_queue(urls, jobs, now=NOW, starvation_seconds=3600)
    assert rep["starved"] == [] and rep["starved_count"] == 0
    assert rep["oldest_wait_seconds"] == pytest.approx(200)


def test_pending_job_missing_from_urls_is_orphaned():
    """A pending job that is not in dispatch order can never run; it is
    reported as orphaned, never silently dropped."""
    urls = ["a"]
    jobs = {"a": _job(), "ghost": _job()}
    rep = _detector().analyze_queue(urls, jobs, now=NOW)
    assert rep["orphaned"] == ["ghost"]


def test_empty_queue_is_clean():
    rep = _detector().analyze_queue([], {}, now=NOW)
    assert rep["pending_count"] == 0
    assert rep["inversions"] == [] and rep["starved"] == []
    assert rep["oldest_wait_seconds"] is None


# ---------------------------------------------------------------- route wiring

class _Runner:
    def __init__(self, urls, jobs):
        self._lock = threading.Lock()
        self.urls = list(urls)
        self.jobs = jobs


def _client(monkeypatch, runners):
    monkeypatch.setattr(app_queue, "_app_runners", lambda: runners)
    monkeypatch.setattr(app_queue, "_app_s_cfg",
                        lambda: {sid: {"name": f"Site {sid}"} for sid in runners})
    app = Flask("row990")
    app.register_blueprint(app_queue.queue_bp)
    return app.test_client()


def test_route_reports_per_site(monkeypatch):
    monkeypatch.setattr("time.time", lambda: NOW)
    runners = {
        "s1": _Runner(["n1", "h1"], {"n1": _job(), "h1": _job("high")}),
        "s2": _Runner(["x"], {"x": _job(age=99999)}),
        "s3": None,
    }
    c = _client(monkeypatch, runners)
    r = c.get("/api/queue/starvation?starvation_seconds=3600")
    # Behavioural RED at base: h1 (high) sits behind n1 (normal) in s1's
    # dispatch order and x has waited 99999s in s2, and no queue endpoint
    # can say so -- the blueprint has no such rule (measured: 500 at base).
    assert r.status_code == 200, (
        f"row990: high job h1 parked behind normal n1 and x starved 99999s are "
        f"invisible -- /api/queue/starvation answered {r.status_code}")
    body = r.get_json()
    assert body["ok"] is True
    assert body["starvation_seconds"] == 3600
    by_site = {s["site_id"]: s for s in body["sites"]}
    assert set(by_site) == {"s1", "s2"}
    assert by_site["s1"]["site_name"] == "Site s1"
    assert by_site["s1"]["inversion_count"] == 1
    assert by_site["s2"]["starved_count"] == 1
    assert body["inversion_count"] == 1 and body["starved_count"] == 1


def test_route_rejects_bad_threshold(monkeypatch):
    c = _client(monkeypatch, {})
    r = c.get("/api/queue/starvation?starvation_seconds=nope")
    assert r.status_code == 400, f"row990: bad threshold answered {r.status_code}, not 400"
    assert r.get_json()["ok"] is False
    r = c.get("/api/queue/starvation?starvation_seconds=-1")
    assert r.status_code == 400
