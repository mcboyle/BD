"""Tests for Row 896: CONNECTION-LIVENESS-MONITORING-AND-HEALTH-PROBING.

Acceptance requirements:
(1) scheduled keep-alive execution (25 min / 1500s period)
(2) structured alerts on simulated HTTP 401/403
(3) proactive queue rerouting with zero disruption to active downloads
"""
from __future__ import annotations

import importlib
import time
import pytest

BD_GATE_SCOPE = "module"


def _get_account_health():
    return importlib.import_module("bulk_downloader.account_health")


def test_scheduled_keep_alive_cadence():
    """Verify 25-minute (1500s) scheduled interval and monitor lifecycle."""
    ah = _get_account_health()
    interval = getattr(ah, "LIVENESS_INTERVAL_SECONDS", 0)
    assert interval == 1500, (
        f"Row 896 requires 25-minute (1500s) scheduled interval; got {interval}"
    )
    monitor_cls = getattr(ah, "SessionLivenessMonitor", None)
    assert monitor_cls is not None, (
        "SessionLivenessMonitor must be defined in bulk_downloader.account_health"
    )
    monitor = monitor_cls(interval_seconds=1500)
    assert monitor.interval_seconds == 1500
    assert not monitor.is_alive()

    # Test execution lifecycle
    probes_called = []

    def dummy_probe():
        probes_called.append(time.time())
        return 200

    monitor.register_endpoint("siteA", 0, dummy_probe)
    results = monitor.run_once()
    assert len(results) == 1
    assert len(probes_called) == 1
    assert results[0]["healthy"] is True
    assert results[0]["status"] == 200
    assert results[0]["alert"] is None


def test_structured_alerts_on_unauthorized_and_unreachable():
    """Verify structured alert payload on simulated HTTP 401, 403, and unreachable."""
    ah = _get_account_health()
    probe_fn = getattr(ah, "probe_session", None)
    assert probe_fn is not None, "probe_session must be implemented"

    # Simulated HTTP 401
    alert_401 = probe_fn(lambda: 401, site_id="site1", account_idx=1)
    assert alert_401["healthy"] is False
    assert alert_401["status"] == 401
    assert alert_401["alert"] == "unauthorized"
    assert alert_401["site_id"] == "site1"
    assert alert_401["account_idx"] == 1

    # Simulated HTTP 403
    alert_403 = probe_fn(lambda: 403, site_id="site2", account_idx=2)
    assert alert_403["healthy"] is False
    assert alert_403["status"] == 403
    assert alert_403["alert"] == "unauthorized"
    assert alert_403["site_id"] == "site2"
    assert alert_403["account_idx"] == 2

    # Simulated network failure / unreachable
    def _failing_head():
        raise ConnectionError("Network unreachable")

    alert_unreachable = probe_fn(_failing_head, site_id="site3", account_idx=0)
    assert alert_unreachable["healthy"] is False
    assert alert_unreachable["status"] is None
    assert alert_unreachable["alert"] == "unreachable"

    # Healthy 200 check
    healthy_res = probe_fn(lambda: 200, site_id="site1", account_idx=0)
    assert healthy_res["healthy"] is True
    assert healthy_res["status"] == 200
    assert healthy_res["alert"] is None


def test_proactive_queue_rerouting_preserves_active_downloads():
    """Verify proactive queue rerouting on session expiration without disrupting active work.

    Active jobs (status == 'running') must NEVER be modified or interrupted.
    Pending jobs for the expired account must be rerouted to target account.
    """
    ah = _get_account_health()
    probe_fn = getattr(ah, "probe_session", None)
    reroute_fn = getattr(ah, "reroute_expired_session_queue", None)
    assert probe_fn is not None, "probe_session must be implemented"
    assert reroute_fn is not None, "reroute_expired_session_queue must be implemented"

    queue = [
        {"url": "https://example.com/item1", "status": "running", "account_idx": 1},
        {"url": "https://example.com/item2", "status": "pending", "account_idx": 1},
        {"url": "https://example.com/item3", "status": "pending", "account_idx": 1},
        {"url": "https://example.com/item4", "status": "pending", "account_idx": 2},
    ]

    # Session for account 1 fails with 401
    res = probe_fn(lambda: 401, queue=queue, site_id="site1", account_idx=1, target_account_idx=2)
    assert res["healthy"] is False
    assert res["alert"] == "unauthorized"
    assert res["rerouted"] == 2

    # Active download on account 1 was NOT disrupted
    assert queue[0]["status"] == "running"
    assert queue[0]["account_idx"] == 1

    # Pending downloads for account 1 were rerouted to account 2
    assert queue[1]["status"] == "pending"
    assert queue[1]["account_idx"] == 2
    assert queue[1].get("rerouted") is True
    assert queue[2]["status"] == "pending"
    assert queue[2]["account_idx"] == 2
    assert queue[2].get("rerouted") is True

    # Account 2 jobs remain unchanged
    assert queue[3]["status"] == "pending"
    assert queue[3]["account_idx"] == 2


# ---- fixer (O928) round 2: correctness REFUTE E1/E2/E3 + NOTED ------------

import http.server
import threading
import urllib.error


class _Loopback:
    """A loopback HTTP server that answers HEAD with a fixed status per path."""

    def __init__(self):
        seen = self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                seen.append((self.command, self.path))
                code = int(self.path.rsplit("/", 1)[-1])
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                seen.append((self.command, self.path))
                self.send_response(405); self.end_headers()

            def log_message(self, *a):
                pass
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def url(self, code):
        return f"http://127.0.0.1:{self.srv.server_port}/session/{code}"

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


@pytest.fixture
def loopback():
    lb = _Loopback()
    try:
        yield lb
    finally:
        lb.close()


def test_r2_e1_urllib_responses_and_http_errors_classify_by_status(loopback):
    """E1: on the app's own transport (urllib), a 200 HEAD is healthy and a
    401/403 -- which urlopen raises as HTTPError -- is 'unauthorized', not
    'unreachable'. Control: a raised ConnectionError is still unreachable."""
    ah = _get_account_health()
    ok = ah.probe_session(ah.head_request_for(loopback.url(200)), site_id="s", account_idx=0)
    assert ok["healthy"] is True and ok["status"] == 200 and ok["alert"] is None
    for code in (401, 403):
        bad = ah.probe_session(ah.head_request_for(loopback.url(code)), site_id="s", account_idx=0)
        assert bad["healthy"] is False and bad["status"] == code and bad["alert"] == "unauthorized", bad
    server_error = ah.probe_session(ah.head_request_for(loopback.url(503)), site_id="s", account_idx=0)
    assert server_error["status"] == 503 and server_error["alert"] == "http_503"
    assert all(cmd == "HEAD" for cmd, _ in loopback.seen) and len(loopback.seen) == 4   # non-mutating

    def raw_urllib_head():
        import urllib.request
        return urllib.request.urlopen(urllib.request.Request(loopback.url(200), method="HEAD"), timeout=5)
    raw = ah.probe_session(raw_urllib_head, site_id="s", account_idx=0)
    assert raw["healthy"] is True and raw["status"] == 200          # HTTPResponse has .status, not .status_code

    def refused():
        raise ConnectionError("Network unreachable")
    assert ah.probe_session(refused, site_id="s", account_idx=0)["alert"] == "unreachable"


def test_r2_e1_default_head_issuer_refuses_a_metadata_address():
    """The default issuer goes through the pinned hook opener: a link-local
    metadata target is refused before any connection (reported unreachable)."""
    ah = _get_account_health()
    res = ah.probe_session(ah.head_request_for("http://169.254.169.254/latest/meta-data/"), site_id="s", account_idx=0)
    assert res["healthy"] is False and res["alert"] == "unreachable" and res["status"] is None


def test_r2_e3_interval_is_a_float_with_a_floor_and_the_thread_does_not_spin():
    """E3: a sub-second interval can no longer truncate to 0 and spin: the
    monitor keeps a float interval floored at MIN_LIVENESS_INTERVAL_SECONDS,
    and a real running thread probes once per interval, not thousands of
    times (the lens measured 91,370 probes in 0.3 s at interval 0.05)."""
    ah = _get_account_health()
    assert ah.SessionLivenessMonitor(0.05).interval_seconds == ah.MIN_LIVENESS_INTERVAL_SECONDS == 1.0
    assert ah.SessionLivenessMonitor(2.5).interval_seconds == 2.5
    probes = []
    monitor = ah.SessionLivenessMonitor(0.05)
    monitor.register_endpoint("s", 0, lambda: probes.append(time.monotonic()) or 200)
    monitor.start()
    try:
        assert monitor.is_alive()
        time.sleep(0.3)
    finally:
        monitor.stop()
    assert not monitor.is_alive() and 1 <= len(probes) <= 2, len(probes)


def test_r2_e2_attach_starts_a_real_monitor_over_the_runners_jobs_and_reroutes_under_its_lock(loopback):
    """E2: the documented start point. attach_liveness_monitor(runner, endpoints)
    builds the monitor over runner.jobs guarded by runner._lock, HEADs the
    registered URLs itself on a real thread, and on a 401 reroutes the
    expired account's pending jobs (never a running one) while the alert
    callback fires; stop() joins the thread."""
    ah = _get_account_health()
    alerts, lock_held = [], []

    class Lock:
        def __init__(self):
            self._l = threading.Lock()

        def __enter__(self):
            self._l.acquire(); lock_held.append(True)

        def __exit__(self, *a):
            self._l.release()

    class Runner:
        site_id = "site1"
        _lock = Lock()
        jobs = {
            "u1": {"status": "running", "account_idx": 1},
            "u2": {"status": "pending", "account_idx": 1},
            "u3": {"status": "pending", "account_idx": 2},
        }
    runner = Runner()
    monitor = ah.attach_liveness_monitor(
        runner, [(1, loopback.url(401), 2), (2, loopback.url(200))], interval_seconds=1, on_alert=alerts.append)
    try:
        deadline = time.monotonic() + 5
        while not alerts and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        monitor.stop()
    assert not monitor.is_alive()
    assert monitor.queue is runner.jobs and monitor.queue_lock is runner._lock and lock_held
    assert alerts and alerts[0]["alert"] == "unauthorized" and alerts[0]["site_id"] == "site1" and alerts[0]["account_idx"] == 1
    assert alerts[0]["rerouted"] == 1
    assert runner.jobs["u1"] == {"status": "running", "account_idx": 1}          # active download untouched
    assert runner.jobs["u2"] == {"status": "pending", "account_idx": 2, "rerouted": True}
    assert runner.jobs["u3"] == {"status": "pending", "account_idx": 2}          # other account untouched


def test_r2_noted_a_401_without_an_account_reroutes_nothing():
    """NOTED: with account_idx=None a 401 must not reassign every pending
    task of every account; the alert is still raised."""
    ah = _get_account_health()
    queue = [{"url": "a", "status": "pending", "account_idx": 1}, {"url": "b", "status": "pending", "account_idx": 2}]
    res = ah.probe_session(lambda: 401, queue=queue, site_id="s", target_account_idx=3)
    assert res["alert"] == "unauthorized" and res["rerouted"] == 0
    assert all("rerouted" not in item and item["account_idx"] in (1, 2) for item in queue)
    # positive control: the same 401 with the account named reroutes exactly its pending task
    assert ah.probe_session(lambda: 401, queue=queue, site_id="s", account_idx=1, target_account_idx=3)["rerouted"] == 1
    assert queue[0]["account_idx"] == 3 and queue[1]["account_idx"] == 2
