"""Contextual Error Classification & Remediation Advisor.

Provides structured root-cause classification, machine-actionable automated remediation
parameters, and human operator guidance across network, TLS, bot challenges,
authentication, media processing, storage, database locking, and browser engine failure domains.
"""
from __future__ import annotations

import dataclasses
import re
import sys
from typing import Any, Optional


class ErrorCategory:
    """Taxonomy of failure domains."""
    NETWORK_CONNECTIVITY = "network_connectivity"
    TLS_CERTIFICATE = "tls_certificate"
    RATE_LIMIT = "rate_limit"
    BOT_CHALLENGE = "bot_challenge"
    AUTHENTICATION = "authentication"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    EXTRACTOR_FAULT = "extractor_fault"
    STORAGE_FILESYSTEM = "storage_filesystem"
    DATABASE_LOCK = "database_lock"
    MEDIA_PROCESSING = "media_processing"
    TIMEOUT = "timeout"
    BROWSER_ENGINE = "browser_engine"
    CLIENT_REQUEST = "client_request"
    INTERNAL_SERVER = "internal_server"
    UNKNOWN = "unknown"


class RemediationActionType:
    """Actionable remediation directives for machines and human operators."""
    BACKOFF_RETRY = "backoff_retry"
    ROTATE_IP = "rotate_ip"
    SOLVE_CAPTCHA = "solve_captcha"
    TAKE_OVER_LOGIN = "take_over_login"
    REFRESH_COOKIES = "refresh_cookies"
    UPDATE_EXTRACTOR = "update_extractor"
    FREE_STORAGE = "free_storage"
    FIX_PERMISSIONS = "fix_permissions"
    SWITCH_QUALITY_OR_FORMAT = "switch_quality_or_format"
    RELOAD_OR_RETRY_BROWSER = "reload_or_retry_browser"
    RETRY_WITH_RESUME = "retry_with_resume"
    REPORT_DEAD_URL = "report_dead_url"
    ADJUST_CONCURRENCY = "adjust_concurrency"
    NO_ACTION = "no_action"


class RemediationUrgency:
    """Priority level for required action."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclasses.dataclass
class RemediationAdvice:
    """Structured remediation guidance container."""
    category: str
    category_confidence: float = 1.0
    root_cause_summary: str = ""
    is_retryable: bool = True
    suggested_backoff_seconds: float = 5.0
    action_type: str = RemediationActionType.BACKOFF_RETRY
    operator_message: str = ""
    remediation_steps: list[str] = dataclasses.field(default_factory=list)
    urgency: str = RemediationUrgency.LOW
    automated_remediation_available: bool = False
    remediation_parameters: dict[str, Any] = dataclasses.field(default_factory=dict)
    diagnostic_metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class ErrorAdvisor:
    """Contextual classification engine and remediation advisor."""

    def advise(self, err: Any, context: Optional[dict[str, Any]] = None) -> RemediationAdvice:
        ctx = dict(context or {})
        raw_msg = str(err) if err is not None else ""
        err_cls_name = err.__class__.__name__ if isinstance(err, Exception) else ""
        full_text = f"{err_cls_name}: {raw_msg}" if err_cls_name else raw_msg

        attempt_count = int(ctx.get("attempt_count") or 1)
        url = str(ctx.get("url") or "")
        site_id = str(ctx.get("site_id") or "")

        # 1. Database Lock
        if (
            isinstance(err, (getattr(sys.modules.get("sqlite3", object), "OperationalError", ()),))
            and "locked" in raw_msg.lower()
        ) or re.search(r"database (?:is )?locked|sqlite.*busy|wal.*checkpoint", full_text, re.I):
            busy_timeout = ctx.get("busy_timeout_ms") or 5000
            steps = [
                "Back off to allow concurrent SQLite writers to commit transactions.",
                "Review worker thread pool concurrency and reduce active simultaneous writers.",
                f"Verify PRAGMA busy_timeout is set (current: {busy_timeout}ms).",
            ]
            return RemediationAdvice(
                category=ErrorCategory.DATABASE_LOCK,
                category_confidence=0.98,
                root_cause_summary="SQLite database is locked due to concurrent transaction contention.",
                is_retryable=True,
                suggested_backoff_seconds=2.5 * min(attempt_count, 4),
                action_type=RemediationActionType.ADJUST_CONCURRENCY if attempt_count >= 3 else RemediationActionType.BACKOFF_RETRY,
                operator_message="Database locked under writer contention. Backing off automatically.",
                remediation_steps=steps,
                urgency=RemediationUrgency.MEDIUM if attempt_count < 3 else RemediationUrgency.HIGH,
                automated_remediation_available=True,
                remediation_parameters={"backoff_multiplier": 1.5, "max_busy_timeout_ms": 10000},
                diagnostic_metadata={"attempt_count": attempt_count, "busy_timeout_ms": busy_timeout},
            )

        # 2. Storage & Filesystem Errors
        if isinstance(err, PermissionError) or (isinstance(err, OSError) and getattr(err, "errno", None) == 13) or re.search(r"Permission denied|EACCES", full_text, re.I):
            target_path = ctx.get("download_dir") or "download directory"
            return RemediationAdvice(
                category=ErrorCategory.STORAGE_FILESYSTEM,
                category_confidence=0.99,
                root_cause_summary=f"Filesystem permission denied writing to {target_path}.",
                is_retryable=False,
                suggested_backoff_seconds=0.0,
                action_type=RemediationActionType.FIX_PERMISSIONS,
                operator_message="Permission denied. Check filesystem ACLs on download directory.",
                remediation_steps=[
                    f"Check file and directory permissions on {target_path}.",
                    "Ensure running user has read/write/execute permissions on parent directory.",
                ],
                urgency=RemediationUrgency.HIGH,
                automated_remediation_available=False,
                remediation_parameters={"target_path": target_path},
                diagnostic_metadata={"errno": 13, "error_type": "EACCES"},
            )

        if (isinstance(err, OSError) and getattr(err, "errno", None) == 28) or re.search(r"No space left on device|ENOSPC|disk full", full_text, re.I):
            target_dir = ctx.get("download_dir") or "storage volume"
            free_bytes = ctx.get("free_disk_bytes")
            return RemediationAdvice(
                category=ErrorCategory.STORAGE_FILESYSTEM,
                category_confidence=1.0,
                root_cause_summary=f"Out of disk space on {target_dir}.",
                is_retryable=False,
                suggested_backoff_seconds=0.0,
                action_type=RemediationActionType.FREE_STORAGE,
                operator_message="Out of disk space. Action: free space or move download_dir.",
                remediation_steps=[
                    f"Free space immediately on the volume hosting {target_dir}.",
                    "Purge completed downloads or configure an alternate download_dir with adequate capacity.",
                ],
                urgency=RemediationUrgency.CRITICAL,
                automated_remediation_available=False,
                remediation_parameters={"download_dir": target_dir, "free_disk_bytes": free_bytes},
                diagnostic_metadata={"errno": 28, "error_type": "ENOSPC"},
            )

        # 3. Bot Challenge & Cloudflare Turnstile
        if re.search(r"cloudflare|cf[- ]chl|turnstile|hcaptcha|recaptcha|bot detection", full_text, re.I):
            # Row 360 contract (friendly_error): only a MEASURED state may say the
            # bypass is available or unavailable. No measurement, or a probe that
            # could not decide, is UNKNOWN -- never an availability claim.
            turnstile_state = ctx.get("turnstile_bypass")
            if not isinstance(turnstile_state, dict):
                turnstile_state = {}
            solver_status = turnstile_state.get("status")
            solver_available = turnstile_state.get("available")
            is_turnstile_avail = solver_available is True and solver_status == "available"
            is_turnstile_absent = solver_available is False and solver_status == "unavailable"
            solver_reason = turnstile_state.get("reason") or "measurement_not_supplied"

            if is_turnstile_avail:
                return RemediationAdvice(
                    category=ErrorCategory.BOT_CHALLENGE,
                    category_confidence=0.95,
                    root_cause_summary="Cloudflare Turnstile verification challenge detected; solver online.",
                    is_retryable=True,
                    suggested_backoff_seconds=5.0,
                    action_type=RemediationActionType.SOLVE_CAPTCHA,
                    operator_message="Cloudflare Turnstile challenge encountered. Solving via automated solver.",
                    remediation_steps=["Dispatch automated headless solver to solve turnstile token."],
                    urgency=RemediationUrgency.LOW,
                    automated_remediation_available=True,
                    remediation_parameters={"solver": "turnstile_bypass"},
                    diagnostic_metadata={"site_id": site_id, "url": url},
                )
            else:
                if is_turnstile_absent:
                    bypass_status = "unavailable"
                    summary = f"Cloudflare verification challenge detected without automated bypass ({solver_reason})."
                    availability = f"Bypass unavailable ({solver_reason})"
                else:
                    bypass_status = "unknown"
                    summary = f"Cloudflare verification challenge detected; automated bypass availability unknown ({solver_reason})."
                    availability = f"Bypass availability unknown ({solver_reason})"
                return RemediationAdvice(
                    category=ErrorCategory.BOT_CHALLENGE,
                    category_confidence=0.95,
                    root_cause_summary=summary,
                    is_retryable=False,
                    suggested_backoff_seconds=0.0,
                    action_type=RemediationActionType.TAKE_OVER_LOGIN,
                    operator_message=f"Cloudflare Turnstile. {availability}. Use Take Over.",
                    remediation_steps=[
                        "Open interactive browser via Take Over to solve the challenge manually.",
                        "Inspect FlareSolverr or solver service status to restore automated bypass.",
                    ],
                    urgency=RemediationUrgency.HIGH,
                    automated_remediation_available=False,
                    remediation_parameters={"reason": solver_reason, "bypass_status": bypass_status},
                    diagnostic_metadata={"site_id": site_id, "url": url},
                )

        # 4. Rate Limiting (429)
        if re.search(r"HTTP Error 429|429 Too Many Requests|rate[- ]limit", full_text, re.I):
            backoff_base = 30.0
            backoff_sec = backoff_base * (2 ** min(attempt_count - 1, 3))
            is_escalated = attempt_count >= 4
            action = RemediationActionType.ROTATE_IP if is_escalated else RemediationActionType.BACKOFF_RETRY
            urgency = RemediationUrgency.HIGH if is_escalated else RemediationUrgency.LOW
            steps = [
                f"Pause request pipeline for {backoff_sec} seconds.",
                "Rotate outbound IP address or proxy pool endpoint if rate limit persists.",
                "Lower concurrent worker slots per domain.",
            ]
            return RemediationAdvice(
                category=ErrorCategory.RATE_LIMIT,
                category_confidence=0.99,
                root_cause_summary=f"Target endpoint rate limit reached (HTTP 429, attempt {attempt_count}).",
                is_retryable=True,
                suggested_backoff_seconds=backoff_sec,
                action_type=action,
                operator_message=f"Rate-limited (429). {'Escalating: rotate IP.' if is_escalated else f'Backing off {int(backoff_sec)}s.'}",
                remediation_steps=steps,
                urgency=urgency,
                automated_remediation_available=True,
                remediation_parameters={"backoff_seconds": backoff_sec, "attempt_count": attempt_count},
                diagnostic_metadata={"status_code": 429, "attempt_count": attempt_count},
            )

        # 5. Authentication & Permissions (401 / 403 / Members Only / Sign in)
        if re.search(r"HTTP Error 401|401 Unauthorized|Sign in to confirm|members[- ]only|Private video", full_text, re.I):
            steps = [
                "Authenticate target site account via Take Over.",
                "Verify session cookies are non-expired and loaded in credential store.",
                "Check account subscription tier if accessing members-only content.",
            ]
            return RemediationAdvice(
                category=ErrorCategory.AUTHENTICATION,
                category_confidence=0.98,
                root_cause_summary="Authentication required or session credentials expired (HTTP 401/Sign-in).",
                is_retryable=False,
                suggested_backoff_seconds=0.0,
                action_type=RemediationActionType.TAKE_OVER_LOGIN,
                operator_message="Authentication required. Action: log in via Take Over.",
                remediation_steps=steps,
                urgency=RemediationUrgency.HIGH,
                automated_remediation_available=False,
                remediation_parameters={"site_id": site_id},
                diagnostic_metadata={"status_code": 401, "site_id": site_id},
            )

        if re.search(r"HTTP Error 403|403 Forbidden", full_text, re.I):
            steps = [
                "Rotate proxy IP or VPN location if origin is geoblocking/blacklisting client IP.",
                "Verify active login session or re-export browser cookies.",
            ]
            return RemediationAdvice(
                category=ErrorCategory.AUTHENTICATION,
                category_confidence=0.92,
                root_cause_summary="HTTP 403 Forbidden: IP blocked, credentials invalid, or expired session token.",
                is_retryable=False,
                suggested_backoff_seconds=0.0,
                action_type=RemediationActionType.TAKE_OVER_LOGIN,
                operator_message="Forbidden (403). Likely IP-blocked or cookie expired. Try rotating account.",
                remediation_steps=steps,
                urgency=RemediationUrgency.HIGH,
                automated_remediation_available=False,
                diagnostic_metadata={"status_code": 403, "site_id": site_id},
            )

        # 6. TLS / SSL Certificate Errors. Python ssl/requests say "certificate
        # verify failed"; libcurl (curl_cffi) says "SSL certificate problem".
        if re.search(r"SSLError|SSL: CERTIFICATE_VERIFY_FAILED|certificate verify failed|SSL certificate problem|ERR_CERT_", full_text, re.I):
            steps = [
                "Verify local system clock and timezone are synchronized (NTP drift check).",
                "Check whether site certificate has expired or uses an untrusted private CA.",
                "Verify corporate proxy/firewall is not performing unauthorized MITM inspection.",
            ]
            return RemediationAdvice(
                category=ErrorCategory.TLS_CERTIFICATE,
                category_confidence=0.99,
                root_cause_summary="TLS/SSL certificate validation failed at handshake.",
                is_retryable=False,
                suggested_backoff_seconds=0.0,
                action_type=RemediationActionType.NO_ACTION,
                operator_message="TLS verify failed. Site cert may be expired, untrusted, or MITM'd.",
                remediation_steps=steps,
                urgency=RemediationUrgency.HIGH,
                automated_remediation_available=False,
                diagnostic_metadata={"url": url},
            )

        # 7. Network Connectivity & DNS
        if isinstance(err, ConnectionRefusedError) or re.search(r"ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_REFUSED|Connection refused|getaddrinfo failed", full_text, re.I):
            steps = [
                "Verify DNS resolution and default gateway connectivity.",
                "Check whether target host port is reachable and service is active.",
                "Verify local VPN tunnel or egress proxy routing.",
            ]
            return RemediationAdvice(
                category=ErrorCategory.NETWORK_CONNECTIVITY,
                category_confidence=0.98,
                root_cause_summary="DNS resolution or TCP socket connection refused.",
                is_retryable=True,
                suggested_backoff_seconds=5.0 * attempt_count,
                action_type=RemediationActionType.BACKOFF_RETRY,
                operator_message="DNS/network connection failed. Check host connectivity and VPN.",
                remediation_steps=steps,
                urgency=RemediationUrgency.MEDIUM if attempt_count >= 3 else RemediationUrgency.LOW,
                automated_remediation_available=True,
                remediation_parameters={"backoff_seconds": 5.0 * attempt_count},
                diagnostic_metadata={"url": url},
            )

        # 8. Browser Engine Crashes & Closures
        if re.search(r"Target page, context or browser has been closed|browser has been closed|Target closed", full_text, re.I):
            steps = [
                "Tear down corrupted Playwright browser page and context.",
                "Relaunch fresh isolated browser context and reattempt navigation.",
            ]
            return RemediationAdvice(
                category=ErrorCategory.BROWSER_ENGINE,
                category_confidence=0.95,
                root_cause_summary="Playwright browser instance or page closed unexpectedly.",
                is_retryable=True,
                suggested_backoff_seconds=3.0,
                action_type=RemediationActionType.RELOAD_OR_RETRY_BROWSER,
                operator_message="Browser tab closed mid-operation. Often a Chrome crash; retry.",
                remediation_steps=steps,
                urgency=RemediationUrgency.MEDIUM,
                automated_remediation_available=True,
                remediation_parameters={"action": "recycle_browser_context"},
            )

        # 9. Timeouts
        if re.search(r"Timeout \d+ms exceeded|ConnectTimeout|ReadTimeout|timed out", full_text, re.I):
            steps = [
                "Wait and retry request with extended socket read timeout.",
                "Check remote server load or CDN latency.",
            ]
            return RemediationAdvice(
                category=ErrorCategory.TIMEOUT,
                category_confidence=0.94,
                root_cause_summary="Operation timed out waiting for remote response or DOM locator.",
                is_retryable=True,
                suggested_backoff_seconds=10.0,
                action_type=RemediationActionType.BACKOFF_RETRY,
                operator_message="Operation timed out. May be slow site or blocked CDN; retrying.",
                remediation_steps=steps,
                urgency=RemediationUrgency.LOW,
                automated_remediation_available=True,
                remediation_parameters={"timeout_multiplier": 1.5},
            )

        # 10. Resource Unavailable (404 / Dead URL)
        if re.search(r"HTTP Error 404|404 Not Found|Video unavailable", full_text, re.I):
            return RemediationAdvice(
                category=ErrorCategory.RESOURCE_UNAVAILABLE,
                category_confidence=0.99,
                root_cause_summary="Target resource not found or taken down (HTTP 404).",
                is_retryable=False,
                suggested_backoff_seconds=0.0,
                action_type=RemediationActionType.REPORT_DEAD_URL,
                operator_message="Not found (404). URL is dead or moved.",
                remediation_steps=["Verify original URL or check if item was permanently deleted."],
                urgency=RemediationUrgency.LOW,
                automated_remediation_available=False,
            )

        # 11. Media Processing & FFmpeg
        if re.search(r"ffmpeg.*?exited with code|moov atom not found", full_text, re.I):
            steps = [
                "Verify downloaded stream segments are intact.",
                "Delete corrupt partial mux and resume download from last valid byte.",
            ]
            return RemediationAdvice(
                category=ErrorCategory.MEDIA_PROCESSING,
                category_confidence=0.96,
                root_cause_summary="Media container corrupted or FFmpeg muxing process exited with error.",
                is_retryable=True,
                suggested_backoff_seconds=5.0,
                action_type=RemediationActionType.RETRY_WITH_RESUME,
                operator_message="Media container incomplete or FFmpeg failure. Resuming download.",
                remediation_steps=steps,
                urgency=RemediationUrgency.MEDIUM,
                automated_remediation_available=True,
            )

        # 12. Extractor Fault
        if re.search(r"unable to extract|extractor broken|layout changed", full_text, re.I):
            return RemediationAdvice(
                category=ErrorCategory.EXTRACTOR_FAULT,
                category_confidence=0.92,
                root_cause_summary="Media extractor parsing error: upstream site DOM or player layout changed.",
                is_retryable=False,
                suggested_backoff_seconds=0.0,
                action_type=RemediationActionType.UPDATE_EXTRACTOR,
                operator_message="Extractor broken — site changed layout. Action: update yt-dlp.",
                remediation_steps=["Update yt-dlp and extractor modules to match revised player APIs."],
                urgency=RemediationUrgency.HIGH,
                automated_remediation_available=False,
            )

        # Fallback: Unknown
        cleaned = re.sub(r"^[\w.]+(?:Error|Exception): ", "", raw_msg).strip()
        summary = cleaned[:120] if cleaned else "Unspecified operational error"
        return RemediationAdvice(
            category=ErrorCategory.UNKNOWN,
            category_confidence=0.5,
            root_cause_summary=summary,
            is_retryable=True,
            suggested_backoff_seconds=5.0,
            action_type=RemediationActionType.BACKOFF_RETRY,
            operator_message=summary[:80],
            remediation_steps=["Inspect system trace and retry with debug logging."],
            urgency=RemediationUrgency.LOW,
            automated_remediation_available=True,
        )

    def batch_analyze(self, items: list[tuple[Any, Optional[dict[str, Any]]]]) -> dict[str, Any]:
        """Classify and advise a batch of errors with aggregated statistics."""
        results: list[RemediationAdvice] = []
        category_counts: dict[str, int] = {}
        urgency_counts: dict[str, int] = {}

        for err, ctx in items:
            advice = self.advise(err, ctx)
            results.append(advice)
            category_counts[advice.category] = category_counts.get(advice.category, 0) + 1
            urgency_counts[advice.urgency] = urgency_counts.get(advice.urgency, 0) + 1

        return {
            "results": results,
            "category_counts": category_counts,
            "urgency_counts": urgency_counts,
            "total": len(items),
        }


_GLOBAL_ADVISOR = ErrorAdvisor()


def get_remediation_advisor() -> ErrorAdvisor:
    """Return singleton ErrorAdvisor instance."""
    return _GLOBAL_ADVISOR


def advise_error(err: Any, context: Optional[dict[str, Any]] = None) -> RemediationAdvice:
    """Convenience top-level error classification and remediation advisor."""
    return _GLOBAL_ADVISOR.advise(err, context)
