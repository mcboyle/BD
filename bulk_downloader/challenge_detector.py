"""bulk_downloader.challenge_detector -- Interactive verification challenge detector and queue pauser.

Phase 935: Session reliability challenge detection and automated lane pacing.
Scans DOM descriptors, iframe hierarchies, and frame metadata for challenge widget signatures.
Upon positive classification, immediately pauses the affected site lane and emits structured alert events.
Zero site logins touched (Fleet Rule 21).
"""
from __future__ import annotations

import collections
import dataclasses
import logging
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger("bulk_downloader.challenge_detector")


@dataclasses.dataclass(frozen=True)
class ChallengeSignature:
    """Descriptor for a known challenge widget signature."""
    challenge_type: str
    pattern: str
    description: str
    severity: str = "alert"


# Known interactive verification signatures across major challenge providers
CHALLENGE_SIGNATURES: Tuple[ChallengeSignature, ...] = (
    # Cloudflare Turnstile / Managed Challenge
    ChallengeSignature("turnstile", r"challenges\.cloudflare\.com", "Cloudflare Challenge Platform host"),
    ChallengeSignature("turnstile", r"cf-challenge-stage", "Cloudflare Challenge Stage element"),
    ChallengeSignature("turnstile", r"cf-chl-widget", "Cloudflare Turnstile widget identifier"),
    ChallengeSignature("turnstile", r"turnstile/v0/api\.js", "Cloudflare Turnstile API script"),

    # Google reCAPTCHA
    ChallengeSignature("recaptcha", r"recaptcha/api2/anchor", "Google reCAPTCHA v2 anchor frame"),
    ChallengeSignature("recaptcha", r"recaptcha/enterprise", "Google reCAPTCHA Enterprise frame"),
    ChallengeSignature("recaptcha", r"g-recaptcha", "Google reCAPTCHA container class"),
    ChallengeSignature("recaptcha", r"recaptcha-anchor", "Google reCAPTCHA anchor widget"),

    # hCaptcha
    ChallengeSignature("hcaptcha", r"hcaptcha\.com/captcha", "hCaptcha iframe source"),
    ChallengeSignature("hcaptcha", r"assets\.hcaptcha\.com", "hCaptcha assets host"),
    ChallengeSignature("hcaptcha", r"hcaptcha-widget", "hCaptcha widget element"),
    ChallengeSignature("hcaptcha", r"\.h-captcha\b", "hCaptcha container class"),

    # Arkose Labs / Funcaptcha
    ChallengeSignature("arkose", r"arkoselabs\.com/fc", "Arkose Labs challenge frame URL"),
    ChallengeSignature("arkose", r"client-api\.arkoselabs\.com", "Arkose Labs Client API host"),
    ChallengeSignature("arkose", r"fc-iframe-wrap", "Arkose Labs iframe wrapper"),

    # AWS WAF Challenge / Captcha
    ChallengeSignature("aws_waf", r"awswaf\.com", "AWS WAF challenge host"),
    ChallengeSignature("aws_waf", r"token-interceptor/challenge\.js", "AWS WAF challenge token script"),
    ChallengeSignature("aws_waf", r"aws-waf-challenge", "AWS WAF challenge element ID"),

    # GeeTest
    ChallengeSignature("geetest", r"geetest\.com", "GeeTest verification host"),
    ChallengeSignature("geetest", r"geetest_holder", "GeeTest container class"),
    ChallengeSignature("geetest", r"geetest_radar_tip", "GeeTest radar element"),

    # DataDome
    ChallengeSignature("datadome", r"geo\.captcha-delivery\.com/captcha", "DataDome captcha delivery URL"),
    ChallengeSignature("datadome", r"datadome\.co", "DataDome protection host"),
    ChallengeSignature("datadome", r"datadome-frame", "DataDome frame identifier"),
)


@dataclasses.dataclass(frozen=True)
class ChallengeDetectionResult:
    """Result of challenge widget scanning across frame/DOM descriptors."""
    detected: bool
    challenge_type: Optional[str] = None
    signature: Optional[str] = None
    severity: str = "none"
    frame_url: Optional[str] = None
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class ChallengeAlertEvent:
    """Structured event emitted when an interactive challenge is detected."""
    event_id: str
    timestamp: float
    site_id: str
    lane: str
    challenge_type: str
    signature: str
    severity: str
    action_taken: str
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class LanePauseRegistry:
    """Thread-safe registry managing lane pause states and metadata."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paused_lanes: Dict[str, Dict[str, Any]] = {}

    def pause_lane(self, lane: str, reason: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Pause the given lane immediately. Returns True if state changed."""
        with self._lock:
            already_paused = lane in self._paused_lanes
            self._paused_lanes[lane] = {
                "lane": lane,
                "paused_at": time.time(),
                "reason": reason,
                "metadata": metadata or {},
            }
            logger.warning("Lane [%s] paused: %s", lane, reason)
            return not already_paused

    def resume_lane(self, lane: str) -> bool:
        """Resume the given lane. Returns True if lane was previously paused."""
        with self._lock:
            if lane in self._paused_lanes:
                del self._paused_lanes[lane]
                logger.info("Lane [%s] resumed", lane)
                return True
            return False

    def is_lane_paused(self, lane: str) -> bool:
        """Query if the lane is currently paused."""
        with self._lock:
            return lane in self._paused_lanes

    def get_paused_lanes(self) -> Dict[str, Dict[str, Any]]:
        """Return a copy of the currently paused lanes mapping."""
        with self._lock:
            return dict(self._paused_lanes)

    def clear(self) -> None:
        """Clear all paused lanes."""
        with self._lock:
            self._paused_lanes.clear()


class ChallengeDetector:
    """Interactive verification challenge detector and lane pause coordinator."""

    def __init__(self, pause_registry: Optional[LanePauseRegistry] = None) -> None:
        self.pause_registry = pause_registry or LanePauseRegistry()
        self._alert_listeners: List[Callable[[ChallengeAlertEvent], None]] = []
        self._alert_buffer: collections.deque[ChallengeAlertEvent] = collections.deque(maxlen=200)
        self._lock = threading.Lock()

    def register_alert_listener(self, listener: Callable[[ChallengeAlertEvent], None]) -> None:
        """Register a callback to receive emitted ChallengeAlertEvents."""
        with self._lock:
            if listener not in self._alert_listeners:
                self._alert_listeners.append(listener)

    def unregister_alert_listener(self, listener: Callable[[ChallengeAlertEvent], None]) -> None:
        """Unregister an existing alert listener."""
        with self._lock:
            if listener in self._alert_listeners:
                self._alert_listeners.remove(listener)

    def get_recent_alerts(self, limit: int = 50) -> List[ChallengeAlertEvent]:
        """Return recently recorded alert events from the ring buffer."""
        with self._lock:
            items = list(self._alert_buffer)
            return items[-limit:]

    def scan_frame_descriptor(self, descriptor: Dict[str, Any]) -> ChallengeDetectionResult:
        """Scan a single frame descriptor for known challenge signatures."""
        url = str(descriptor.get("url") or descriptor.get("src") or "")
        name = str(descriptor.get("name") or "")
        html = str(descriptor.get("html") or descriptor.get("content") or "")
        title = str(descriptor.get("title") or "")

        combined_text = f"{url}\n{name}\n{html}\n{title}"

        for sig in CHALLENGE_SIGNATURES:
            if re.search(sig.pattern, combined_text, re.IGNORECASE):
                return ChallengeDetectionResult(
                    detected=True,
                    challenge_type=sig.challenge_type,
                    signature=sig.pattern,
                    severity=sig.severity,
                    frame_url=url or None,
                    details={
                        "matched_pattern": sig.pattern,
                        "description": sig.description,
                        "frame_url": url,
                        "frame_name": name,
                    },
                )

        return ChallengeDetectionResult(detected=False)

    def scan_frame_descriptors(self, descriptors: Iterable[Dict[str, Any]]) -> List[ChallengeDetectionResult]:
        """Scan a sequence of frame descriptors and return results for all frames."""
        results: List[ChallengeDetectionResult] = []
        for desc in descriptors:
            results.append(self.scan_frame_descriptor(desc))
        return results

    def scan_dom_content(self, html_content: str, url: Optional[str] = None) -> ChallengeDetectionResult:
        """Scan DOM HTML content and page URL for challenge widget signatures."""
        return self.scan_frame_descriptor({
            "html": html_content,
            "url": url or "",
        })

    def inspect_and_handle(
        self,
        frames: Iterable[Dict[str, Any]],
        *,
        site_id: str,
        lane: Optional[str] = None,
        db_conn: Optional[Any] = None,
    ) -> Optional[ChallengeAlertEvent]:
        """Inspect frames for challenge widgets. Upon detection, immediately pauses lane and emits alert."""
        target_lane = lane or site_id
        for frame in frames:
            result = self.scan_frame_descriptor(frame)
            if result.detected:
                # Instantaneous lane pause
                self.pause_registry.pause_lane(
                    target_lane,
                    reason=f"Interactive challenge detected: {result.challenge_type} ({result.signature})",
                    metadata={
                        "site_id": site_id,
                        "challenge_type": result.challenge_type,
                        "signature": result.signature,
                        "frame_url": result.frame_url,
                    },
                )

                # Construct structured alert event
                event = ChallengeAlertEvent(
                    event_id=f"alert-{uuid.uuid4().hex[:12]}",
                    timestamp=time.time(),
                    site_id=site_id,
                    lane=target_lane,
                    challenge_type=result.challenge_type or "unknown",
                    signature=result.signature or "unknown",
                    severity=result.severity,
                    action_taken="lane_paused",
                    details=result.details,
                )

                # Record in buffer and notify listeners
                with self._lock:
                    self._alert_buffer.append(event)
                    listeners = list(self._alert_listeners)

                for listener in listeners:
                    try:
                        listener(event)
                    except Exception as e:
                        logger.error("Error in challenge alert listener: %s", e)

                # Persist to database alert_events if connection provided
                if db_conn is not None:
                    try:
                        db_conn.execute(
                            """INSERT INTO alert_events(rule_id, kind, value, message, ts)
                               VALUES(?, ?, ?, ?, ?)""",
                            (
                                f"challenge_{result.challenge_type}",
                                "challenge_detected",
                                1.0,
                                f"Interactive challenge ({result.challenge_type}) paused lane {target_lane}",
                                event.timestamp,
                            ),
                        )
                    except Exception as e:
                        logger.warning("Failed to write challenge alert to db: %s", e)

                return event

        return None


# Global default instances
default_pause_registry = LanePauseRegistry()
default_detector = ChallengeDetector(pause_registry=default_pause_registry)


def detect_challenge(descriptors: Iterable[Dict[str, Any]]) -> ChallengeDetectionResult:
    """Scan frame descriptors with the default detector and return the first detection."""
    for desc in descriptors:
        res = default_detector.scan_frame_descriptor(desc)
        if res.detected:
            return res
    return ChallengeDetectionResult(detected=False)


def pause_lane(lane: str, reason: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
    """Pause a lane using the default registry."""
    return default_pause_registry.pause_lane(lane, reason, metadata)


def is_lane_paused(lane: str) -> bool:
    """Query if a lane is paused in the default registry."""
    return default_pause_registry.is_lane_paused(lane)


def resume_lane(lane: str) -> bool:
    """Resume a lane in the default registry."""
    return default_pause_registry.resume_lane(lane)


def filter_jobs_by_paused_lanes(
    jobs: Iterable[Dict[str, Any]],
    pause_registry: Optional[LanePauseRegistry] = None,
) -> List[Dict[str, Any]]:
    """Filter out queue jobs whose lane is currently paused."""
    registry = pause_registry or default_pause_registry
    paused_lanes = set(registry.get_paused_lanes().keys())
    if not paused_lanes:
        return list(jobs)
    return [
        j for j in jobs
        if (j.get("lane") or j.get("site_id") or "default") not in paused_lanes
    ]
