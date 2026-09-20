"""tests/test_challenge_detector.py -- Acceptance tests for Row 935:
INTERACTIVE-VERIFICATION-CHALLENGE-DETECTOR-AND-QUEUE-PAUSER.

ACCEPTANCE CRITERIA:
(1) detection of challenge frame signatures across DOM and frame hierarchies
(2) instantaneous lane pause preventing worker retry escalation
(3) structured alert event emission with complete incident metadata
(4) negative controls: clean frames produce zero alerts/pauses, with positive control beside zero
(5) exact count assertions across mixed descriptor batches
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List
import pytest

try:
    from bulk_downloader.challenge_detector import (
        ChallengeAlertEvent,
        ChallengeDetectionResult,
        ChallengeDetector,
        LanePauseRegistry,
        detect_challenge,
        filter_jobs_by_paused_lanes,
        is_lane_paused,
        pause_lane,
        resume_lane,
    )
except ImportError:
    class ChallengeDetectionResult:  # type: ignore[no-redef]
        def __init__(self, detected=False, challenge_type=None, signature=None, severity="none", frame_url=None, details=None):
            self.detected = detected
            self.challenge_type = challenge_type
            self.signature = signature
            self.severity = severity
            self.frame_url = frame_url
            self.details = details or {}

    class ChallengeAlertEvent:  # type: ignore[no-redef]
        def __init__(self, event_id="", timestamp=0.0, site_id="", lane="", challenge_type="", signature="", severity="", action_taken="", details=None):
            self.event_id = event_id
            self.timestamp = timestamp
            self.site_id = site_id
            self.lane = lane
            self.challenge_type = challenge_type
            self.signature = signature
            self.severity = severity
            self.action_taken = action_taken
            self.details = details or {}

        def to_dict(self):
            return {
                "event_id": self.event_id,
                "timestamp": self.timestamp,
                "site_id": self.site_id,
                "lane": self.lane,
                "challenge_type": self.challenge_type,
                "signature": self.signature,
                "severity": self.severity,
                "action_taken": self.action_taken,
                "details": self.details,
            }

    class LanePauseRegistry:  # type: ignore[no-redef]
        def pause_lane(self, lane, reason, metadata=None): return False
        def resume_lane(self, lane): return False
        def is_lane_paused(self, lane): return False
        def get_paused_lanes(self): return {}
        def clear(self): pass

    class ChallengeDetector:  # type: ignore[no-redef]
        def __init__(self, pause_registry=None):
            self.pause_registry = pause_registry or LanePauseRegistry()
        def scan_frame_descriptor(self, desc):
            return ChallengeDetectionResult(detected=False)
        def scan_frame_descriptors(self, descs):
            return [ChallengeDetectionResult(detected=False) for _ in descs]
        def scan_dom_content(self, html, url=None):
            return ChallengeDetectionResult(detected=False)
        def inspect_and_handle(self, frames, *, site_id, lane=None, db_conn=None):
            return None
        def register_alert_listener(self, listener): pass
        def unregister_alert_listener(self, listener): pass
        def get_recent_alerts(self, limit=50): return []

    def detect_challenge(descriptors): return ChallengeDetectionResult(detected=False)  # type: ignore[no-redef]
    def pause_lane(lane, reason, metadata=None): return False  # type: ignore[no-redef]
    def is_lane_paused(lane): return False  # type: ignore[no-redef]
    def resume_lane(lane): return False  # type: ignore[no-redef]
    def filter_jobs_by_paused_lanes(jobs, pause_registry=None): return list(jobs)  # type: ignore[no-redef]

BD_GATE_SCOPE = "module"


@pytest.fixture
def clean_registry():
    registry = LanePauseRegistry()
    yield registry
    registry.clear()


@pytest.fixture
def detector(clean_registry):
    return ChallengeDetector(pause_registry=clean_registry)


def test_challenge_frame_signatures_detection(detector: ChallengeDetector):
    """AC 1: Verify detection of challenge widget signatures across multiple providers."""
    sample_challenge_frames: List[Dict[str, Any]] = [
        {
            "id": "frame_turnstile_1",
            "url": "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/v0/api.js?onload=onloadTurnstileCallback",
            "name": "cf-turnstile-frame",
            "html": '<div id="cf-challenge-stage"><iframe src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile"></iframe></div>',
            "expected_type": "turnstile",
        },
        {
            "id": "frame_recaptcha_v2",
            "url": "https://www.google.com/recaptcha/api2/anchor?ar=1&k=6Lcb8...",
            "name": "a-48f82819",
            "html": '<iframe title="reCAPTCHA" src="https://www.google.com/recaptcha/api2/anchor"></iframe>',
            "expected_type": "recaptcha",
        },
        {
            "id": "frame_hcaptcha_1",
            "url": "https://assets.hcaptcha.com/captcha/v1/d356ab0/static/hcaptcha.html#frame=challenge",
            "name": "hcaptcha-widget",
            "html": '<iframe src="https://assets.hcaptcha.com/captcha/v1/d356ab0/static/hcaptcha.html"></iframe>',
            "expected_type": "hcaptcha",
        },
        {
            "id": "frame_arkose_1",
            "url": "https://client-api.arkoselabs.com/fc/api/?token=345abc...",
            "name": "fc-iframe-wrap",
            "html": '<iframe id="fc-iframe-wrap" src="https://client-api.arkoselabs.com/fc/api/"></iframe>',
            "expected_type": "arkose",
        },
        {
            "id": "frame_aws_waf_1",
            "url": "https://099de32.awswaf.com/token-interceptor/challenge.js",
            "name": "aws-waf-challenge",
            "html": '<div id="aws-waf-challenge"><script src="https://099de32.awswaf.com/token-interceptor/challenge.js"></script></div>',
            "expected_type": "aws_waf",
        },
        {
            "id": "frame_geetest_1",
            "url": "https://static.geetest.com/static/js/gt.0.5.0.js",
            "name": "geetest_holder",
            "html": '<div class="geetest_holder"><div class="geetest_radar_tip"></div></div>',
            "expected_type": "geetest",
        },
        {
            "id": "frame_datadome_1",
            "url": "https://geo.captcha-delivery.com/captcha/?initialCid=AHrlqAAAA...",
            "name": "datadome-frame",
            "html": '<iframe src="https://geo.captcha-delivery.com/captcha/"></iframe>',
            "expected_type": "datadome",
        },
    ]

    # Exactly 7 distinct provider frame signatures tested
    assert len(sample_challenge_frames) == 7

    for sample in sample_challenge_frames:
        result: ChallengeDetectionResult = detector.scan_frame_descriptor(sample)
        assert result.detected is True, f"Failed to detect challenge in {sample['id']}"
        assert result.challenge_type == sample["expected_type"], (
            f"Expected type {sample['expected_type']}, got {result.challenge_type} for {sample['id']}"
        )
        assert result.signature is not None
        assert result.severity in ("alert", "warn")


def test_challenge_detection_negative_control_with_positive_pairing(detector: ChallengeDetector):
    """AC 1 negative control: Clean pages produce zero detections; positive control pairs beside zero."""
    clean_frames: List[Dict[str, Any]] = [
        {
            "id": "normal_video_player",
            "url": "https://player.vimeo.com/video/123456789?autoplay=0",
            "name": "vimeo-player",
            "html": '<div class="player-container"><video src="https://cdn.example.com/stream.mp4"></video></div>',
        },
        {
            "id": "normal_article_body",
            "url": "https://news.example.com/article/technology-update",
            "name": "main-content",
            "html": '<main><h1>Article Title</h1><p>Normal text without any verification barriers.</p></main>',
        },
        {
            "id": "normal_analytics_beacon",
            "url": "https://analytics.example.com/beacon.js?v=2.1",
            "name": "analytics-frame",
            "html": '<script src="https://analytics.example.com/tracker.js"></script>',
        },
    ]

    # Nonzero denominator verified before testing
    assert len(clean_frames) == 3

    for clean in clean_frames:
        result = detector.scan_frame_descriptor(clean)
        assert result.detected is False, f"False positive detected on clean frame {clean['id']}"
        assert result.challenge_type is None

    # Positive control paired directly beside the clean set
    positive_control = {
        "id": "cf_turnstile_positive_control",
        "url": "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/orchestrate/chl_api/v1",
        "name": "turnstile_box",
        "html": '<div id="cf-chl-widget-12345"></div>',
    }
    paired_result = detector.scan_frame_descriptor(positive_control)
    assert paired_result.detected is True, "Positive control failed to detect Turnstile challenge"
    assert paired_result.challenge_type == "turnstile"


def test_instantaneous_lane_pause_and_queue_isolation(detector: ChallengeDetector, clean_registry: LanePauseRegistry):
    """AC 2: Verify instantaneous lane pause upon challenge detection and isolation from other lanes."""
    lane_a = "site_alpha_downloads"
    lane_b = "site_beta_downloads"

    # Precondition: neither lane is paused initially
    assert clean_registry.is_lane_paused(lane_a) is False
    assert clean_registry.is_lane_paused(lane_b) is False

    synthetic_jobs = [
        {"url": "https://site-a.com/vod/1", "lane": lane_a, "status": "pending", "priority": "high", "ord": 1},
        {"url": "https://site-a.com/vod/2", "lane": lane_a, "status": "pending", "priority": "normal", "ord": 2},
        {"url": "https://site-b.com/vod/10", "lane": lane_b, "status": "pending", "priority": "high", "ord": 1},
        {"url": "https://site-b.com/vod/20", "lane": lane_b, "status": "pending", "priority": "normal", "ord": 2},
    ]

    # Initial dispatch queue admits all 4 jobs
    admitted_init = filter_jobs_by_paused_lanes(synthetic_jobs, pause_registry=clean_registry)
    assert len(admitted_init) == 4

    # Encounter challenge on lane_a
    challenge_frame = {
        "url": "https://challenges.cloudflare.com/turnstile/v0/api.js",
        "html": '<div class="cf-challenge-stage"></div>',
    }
    t_before = time.monotonic()
    alert_event = detector.inspect_and_handle(
        [challenge_frame],
        site_id="site_alpha",
        lane=lane_a,
    )
    t_after = time.monotonic()

    # Instantaneous execution: pause operation takes < 50ms
    assert (t_after - t_before) < 0.050, f"Lane pause took too long: {(t_after - t_before)*1000:.2f}ms"

    # Lane A is now paused; Lane B remains active
    assert clean_registry.is_lane_paused(lane_a) is True
    assert clean_registry.is_lane_paused(lane_b) is False
    assert alert_event is not None
    assert alert_event.action_taken == "lane_paused"
    assert alert_event.lane == lane_a

    # Queue filter now isolates lane A and admits only lane B jobs
    admitted_after_pause = filter_jobs_by_paused_lanes(synthetic_jobs, pause_registry=clean_registry)
    assert len(admitted_after_pause) == 2
    assert all(j["lane"] == lane_b for j in admitted_after_pause)

    # Resume lane A restores dispatch readiness
    resumed = clean_registry.resume_lane(lane_a)
    assert resumed is True
    assert clean_registry.is_lane_paused(lane_a) is False
    admitted_restored = filter_jobs_by_paused_lanes(synthetic_jobs, pause_registry=clean_registry)
    assert len(admitted_restored) == 4


def test_structured_alert_event_emission(detector: ChallengeDetector, clean_registry: LanePauseRegistry):
    """AC 3: Verify structured alert event emission with complete incident metadata and listener notification."""
    received_alerts: List[ChallengeAlertEvent] = []

    def alert_sink(event: ChallengeAlertEvent) -> None:
        received_alerts.append(event)

    detector.register_alert_listener(alert_sink)

    test_frame = {
        "url": "https://www.google.com/recaptcha/api2/anchor?ar=1",
        "name": "recaptcha-anchor",
        "html": '<div class="g-recaptcha" data-sitekey="6Ltest123"></div>',
    }

    t0 = time.time()
    event = detector.inspect_and_handle(
        [test_frame],
        site_id="site_gamma",
        lane="lane_gamma_fast",
    )
    t1 = time.time()

    assert event is not None
    assert len(received_alerts) == 1
    assert received_alerts[0] == event

    # Validate structured schema fields
    event_dict = event.to_dict()
    assert event_dict["event_id"] != ""
    assert t0 <= event_dict["timestamp"] <= t1 + 0.1
    assert event_dict["site_id"] == "site_gamma"
    assert event_dict["lane"] == "lane_gamma_fast"
    assert event_dict["challenge_type"] == "recaptcha"
    assert "recaptcha" in event_dict["signature"]
    assert event_dict["severity"] in ("alert", "warn")
    assert event_dict["action_taken"] == "lane_paused"
    assert "frame_url" in event_dict["details"]

    # Verify event stored in detector buffer
    recent = detector.get_recent_alerts(limit=10)
    assert len(recent) >= 1
    assert recent[-1].event_id == event.event_id

    # Unregister listener check
    detector.unregister_alert_listener(alert_sink)
    detector.inspect_and_handle([test_frame], site_id="site_delta", lane="lane_delta")
    assert len(received_alerts) == 1  # No additional events pushed to unregistered sink


def test_exact_count_batch_classification(detector: ChallengeDetector):
    """Verify exact count quantification over mixed descriptor batches."""
    mixed_batch: List[Dict[str, Any]] = [
        {"url": "https://site.com/media/player.html", "html": "<video src='1.mp4'></video>"},
        {"url": "https://challenges.cloudflare.com/turnstile", "html": "<div id='cf-challenge-stage'></div>"},
        {"url": "https://site.com/about.html", "html": "<h1>About Us</h1>"},
        {"url": "https://assets.hcaptcha.com/captcha/v1/frame.html", "html": "<iframe src='hcaptcha'></iframe>"},
        {"url": "https://site.com/styles.css", "html": "body { margin: 0; }"},
        {"url": "https://geo.captcha-delivery.com/captcha/?cid=123", "html": "<script src='dd.js'></script>"},
    ]

    # Denominator verification: exactly 6 descriptors (3 challenge, 3 clean)
    assert len(mixed_batch) == 6

    results = detector.scan_frame_descriptors(mixed_batch)
    assert len(results) == 6

    detected_subset = [r for r in results if r.detected]
    clean_subset = [r for r in results if not r.detected]

    # Exact count assertions
    assert len(detected_subset) == 3, f"Expected exactly 3 detected challenges, got {len(detected_subset)}"
    assert len(clean_subset) == 3, f"Expected exactly 3 clean frames, got {len(clean_subset)}"

    detected_types = {r.challenge_type for r in detected_subset}
    assert detected_types == {"turnstile", "hcaptcha", "datadome"}
