"""Row 953: INGRESS-FLOW-CONTROL-AND-ASYNCHRONOUS-QUEUE-BACKPRESSURE-TRIGGER

Tests for bulk_downloader/ingress_flow_controller.py.
Verifies:
(1) Classification of blocking document hierarchies (login walls, CAPTCHAs,
    maintenance pages, redirect loops, unexpected MIME types)
(2) Deterministic backpressure signal emission on lane-level pause
(3) Graceful task descheduling when lane is paused
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from bulk_downloader.ingress_flow_controller import (
        BlockingCategory,
        IngressDecision,
        IngressFlowController,
        classify_response,
    )
except ImportError:
    BlockingCategory = None
    IngressDecision = None
    IngressFlowController = None
    classify_response = None

BD_GATE_SCOPE = "module"


# ---------------------------------------------------------------------------
# Behavioral RED: without the controller, blocking documents pass through
# ---------------------------------------------------------------------------

def test_behavioral_red_blocking_documents_unrecognized():
    """Behavioral RED: without ingress flow control, blocking response
    documents (login walls, CAPTCHAs, maintenance pages) are not classified,
    so workers keep pulling tasks into a stalled lane."""
    responses = [
        {"status": 200, "content_type": "text/html", "body": "<html><head><title>Login</title></head><body><form id='login'><input name='username'><input name='password'></form></body></html>"},
        {"status": 200, "content_type": "text/html", "body": "<html><body><div class='captcha-container'><iframe src='https://www.google.com/recaptcha'></iframe></div></body></html>"},
        {"status": 503, "content_type": "text/html", "body": "<html><body><h1>Service Temporarily Unavailable</h1><p>Maintenance in progress</p></body></html>"},
        {"status": 429, "content_type": "text/html", "body": "<html><body>Rate limited</body></html>"},
    ]
    assert len(responses) == 4, "Fixture must have exactly 4 blocking responses"

    if classify_response is None:
        naive_blocked = 0
        assert naive_blocked == 4, (
            f"BEHAVIORAL RED: without ingress flow controller, 0 of 4 blocking "
            f"responses are classified (assert 0 == 4)"
        )

    classified = [classify_response(r["content_type"], r["status"], r["body"]) for r in responses]
    assert all(d.is_blocking for d in classified), "All blocking responses must be classified"


# ---------------------------------------------------------------------------
# (1) Classification of blocking document hierarchies
# ---------------------------------------------------------------------------

def test_classify_login_wall():
    """Login wall detection via form with password field."""
    assert classify_response is not None, "classify_response required"
    html = "<html><head><title>Sign In</title></head><body><form action='/auth'><input type='password' name='pass'><button>Login</button></form></body></html>"
    result = classify_response("text/html", 200, html)
    assert result.is_blocking is True
    assert result.category == BlockingCategory.LOGIN_WALL


def test_classify_captcha():
    """CAPTCHA detection via recaptcha iframe or captcha class."""
    assert classify_response is not None
    html = "<html><body><div class='g-recaptcha' data-sitekey='abc'></div></body></html>"
    result = classify_response("text/html", 200, html)
    assert result.is_blocking is True
    assert result.category == BlockingCategory.CAPTCHA


def test_classify_maintenance_page():
    """Maintenance/unavailable page detection via 503 + maintenance keywords."""
    assert classify_response is not None
    html = "<html><body><h1>Under Maintenance</h1><p>We'll be back shortly.</p></body></html>"
    result = classify_response("text/html", 503, html)
    assert result.is_blocking is True
    assert result.category == BlockingCategory.MAINTENANCE


def test_classify_redirect_loop():
    """Redirect loop detection via repeated Location headers."""
    assert classify_response is not None
    result = classify_response("text/html", 302, "", redirect_count=15)
    assert result.is_blocking is True
    assert result.category == BlockingCategory.REDIRECT_LOOP


def test_classify_unexpected_mime():
    """Unexpected MIME type for a document fetch lane (e.g., PDF when expecting HTML)."""
    assert classify_response is not None
    result = classify_response("application/pdf", 200, "", expected_mime="text/html")
    assert result.is_blocking is True
    assert result.category == BlockingCategory.UNEXPECTED_MIME


def test_classify_passable_html():
    """Normal HTML page with real content passes through."""
    assert classify_response is not None
    html = "<html><head><title>Gallery</title></head><body><div class='content'><a href='/img/1.jpg'>Photo 1</a><a href='/img/2.jpg'>Photo 2</a></div></body></html>"
    result = classify_response("text/html", 200, html)
    assert result.is_blocking is False
    assert result.category == BlockingCategory.PASSABLE


def test_classify_rate_limit():
    """429 Too Many Requests is a blocking state."""
    assert classify_response is not None
    result = classify_response("text/html", 429, "<html><body>Rate limited</body></html>")
    assert result.is_blocking is True
    assert result.category == BlockingCategory.RATE_LIMITED


# ---------------------------------------------------------------------------
# (2) Deterministic backpressure signal emission
# ---------------------------------------------------------------------------

def test_backpressure_signal_on_threshold():
    """Backpressure activates when blocking count reaches threshold."""
    assert IngressFlowController is not None
    ctrl = IngressFlowController(blocking_threshold=3)

    signals_emitted = []
    ctrl.on_backpressure = lambda lane, metrics: signals_emitted.append((lane, metrics))

    for i in range(3):
        ctrl.record_blocking("lane-A", BlockingCategory.LOGIN_WALL)

    assert ctrl.is_lane_paused("lane-A") is True
    assert len(signals_emitted) == 1
    assert signals_emitted[0][0] == "lane-A"


def test_backpressure_not_emitted_below_threshold():
    """No backpressure below threshold."""
    assert IngressFlowController is not None
    ctrl = IngressFlowController(blocking_threshold=3)

    signals_emitted = []
    ctrl.on_backpressure = lambda lane, metrics: signals_emitted.append((lane, metrics))

    ctrl.record_blocking("lane-A", BlockingCategory.LOGIN_WALL)
    ctrl.record_blocking("lane-A", BlockingCategory.LOGIN_WALL)

    assert ctrl.is_lane_paused("lane-A") is False
    assert len(signals_emitted) == 0


def test_backpressure_deterministic_across_lanes():
    """Each lane tracks independently; backpressure is per-lane."""
    assert IngressFlowController is not None
    ctrl = IngressFlowController(blocking_threshold=2)

    ctrl.record_blocking("lane-A", BlockingCategory.LOGIN_WALL)
    ctrl.record_blocking("lane-A", BlockingCategory.LOGIN_WALL)
    ctrl.record_blocking("lane-B", BlockingCategory.CAPTCHA)

    assert ctrl.is_lane_paused("lane-A") is True
    assert ctrl.is_lane_paused("lane-B") is False


def test_health_metrics_structured():
    """Health metrics contain lane state, blocking counts, and categories."""
    assert IngressFlowController is not None
    ctrl = IngressFlowController(blocking_threshold=2)
    ctrl.record_blocking("lane-A", BlockingCategory.LOGIN_WALL)
    ctrl.record_blocking("lane-A", BlockingCategory.CAPTCHA)

    metrics = ctrl.get_health_metrics()
    assert "lanes" in metrics
    assert "lane-A" in metrics["lanes"]
    lane_m = metrics["lanes"]["lane-A"]
    assert lane_m["blocking_count"] == 2
    assert lane_m["paused"] is True
    assert BlockingCategory.LOGIN_WALL.value in lane_m["categories"]
    assert BlockingCategory.CAPTCHA.value in lane_m["categories"]


# ---------------------------------------------------------------------------
# (3) Graceful task descheduling
# ---------------------------------------------------------------------------

def test_deschedule_returns_tasks_when_paused():
    """When a lane is paused, pending tasks are descheduled and returned."""
    assert IngressFlowController is not None
    ctrl = IngressFlowController(blocking_threshold=1)

    pending = [{"url": "https://example.com/1"}, {"url": "https://example.com/2"}]
    ctrl.record_blocking("lane-A", BlockingCategory.MAINTENANCE)

    descheduled = ctrl.deschedule("lane-A", pending)
    assert len(descheduled) == 2
    assert all(t["url"] in ("https://example.com/1", "https://example.com/2") for t in descheduled)


def test_deschedule_noop_when_not_paused():
    """When a lane is not paused, deschedule returns empty list."""
    assert IngressFlowController is not None
    ctrl = IngressFlowController(blocking_threshold=5)

    pending = [{"url": "https://example.com/1"}]
    descheduled = ctrl.deschedule("lane-A", pending)
    assert len(descheduled) == 0


def test_resume_lane_clears_backpressure():
    """Resuming a lane clears backpressure and allows scheduling again."""
    assert IngressFlowController is not None
    ctrl = IngressFlowController(blocking_threshold=1)
    ctrl.record_blocking("lane-A", BlockingCategory.RATE_LIMITED)
    assert ctrl.is_lane_paused("lane-A") is True

    ctrl.resume_lane("lane-A")
    assert ctrl.is_lane_paused("lane-A") is False
    descheduled = ctrl.deschedule("lane-A", [{"url": "https://example.com/1"}])
    assert len(descheduled) == 0


# ---------------------------------------------------------------------------
# Negative control
# ---------------------------------------------------------------------------

def test_negative_control_blocking_detection_exact_count():
    """Negative control: prove detection instrument can produce both positive
    and negative outcomes with exact counts, and fixture built the shape."""
    assert classify_response is not None

    blocking_fixtures = [
        ("text/html", 200, "<html><body><form><input type='password'></form></body></html>"),
        ("text/html", 200, "<html><body><div class='g-recaptcha'></div></body></html>"),
        ("text/html", 503, "<html><body><h1>Service Unavailable</h1><p>Under maintenance</p></body></html>"),
        ("text/html", 429, "<html><body>Rate limited</body></html>"),
        ("text/html", 302, ""),
    ]
    passable_fixtures = [
        ("text/html", 200, "<html><body><div class='gallery'><a href='/photo1.jpg'>Photo</a></div></body></html>"),
        ("text/html", 200, "<html><body><h1>Welcome</h1><p>Content here</p></body></html>"),
        ("application/json", 200, '{"items": [1,2,3]}'),
    ]

    assert len(blocking_fixtures) == 5, "Blocking fixture must have 5 entries"
    assert len(passable_fixtures) == 3, "Passable fixture must have 3 entries"

    blocked_count = 0
    for ct, status, body in blocking_fixtures:
        kwargs = {"redirect_count": 15} if status == 302 else {}
        r = classify_response(ct, status, body, **kwargs)
        assert r.is_blocking is True, f"Expected blocking for {ct}/{status}"
        blocked_count += 1
    assert blocked_count == 5, f"Expected exactly 5 blocked, got {blocked_count}"

    passed_count = 0
    for ct, status, body in passable_fixtures:
        r = classify_response(ct, status, body)
        assert r.is_blocking is False, f"Expected passable for {ct}/{status}"
        passed_count += 1
    assert passed_count == 3, f"Expected exactly 3 passed, got {passed_count}"
