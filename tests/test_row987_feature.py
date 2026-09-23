"""Row 987: Contextual Error Classification & Remediation Advisor.

Provides structured contextual error classification, root-cause diagnostics,
machine-executable automated remediation parameters, and human operator action
guidance across network, TLS, bot challenges, authentication, extraction, storage,
database lock, and browser runtime failure domains.

On the pre-row base the advisor module does not exist, so these tests fail there
with an honest ImportError: that is the subject missing, NOT RED provenance. RED
provenance for the P2-B REFUTE items is measured at the refuted tree 4d440d9d,
where every symbol imports and the failures are the assertions below.
"""
from __future__ import annotations

import sqlite3

BD_GATE_SCOPE = "module"


def _get_advisor():
    """Import the advisor symbols. A missing module raises ImportError unchanged."""
    from bulk_downloader.friendly_error import advise_error
    from bulk_downloader.error_advisor import (
        ErrorAdvisor,
        ErrorCategory,
        RemediationActionType,
        RemediationUrgency,
        get_remediation_advisor,
    )
    return advise_error, ErrorAdvisor, ErrorCategory, RemediationActionType, RemediationUrgency, get_remediation_advisor


def test_friendly_error_baseline_positive_control():
    """Positive control: verify existing friendly_error is functional at baseline."""
    from bulk_downloader.friendly_error import friendly_error

    # Verify baseline probe can say YES to basic error string translation
    msg = friendly_error("HTTP Error 404: Not Found")
    assert "Not found (404)" in msg

    cf_msg = friendly_error("cloudflare challenge encountered")
    assert "Cloudflare challenge" in cf_msg

    turnstile_msg = friendly_error("turnstile verification required")
    assert "Turnstile" in turnstile_msg


def test_error_advisor_import_and_metadata():
    """Verify error advisor module exports and version metadata."""
    advise_error, ErrorAdvisor, ErrorCategory, RemediationActionType, RemediationUrgency, get_remediation_advisor = _get_advisor()

    advisor = get_remediation_advisor()
    assert isinstance(advisor, ErrorAdvisor)
    assert ErrorCategory.NETWORK_CONNECTIVITY == "network_connectivity"
    assert ErrorCategory.BOT_CHALLENGE == "bot_challenge"
    assert ErrorCategory.AUTHENTICATION == "authentication"
    assert ErrorCategory.RATE_LIMIT == "rate_limit"
    assert ErrorCategory.DATABASE_LOCK == "database_lock"
    assert RemediationActionType.BACKOFF_RETRY == "backoff_retry"
    assert RemediationActionType.TAKE_OVER_LOGIN == "take_over_login"
    assert RemediationUrgency.CRITICAL == "CRITICAL"


def test_classify_network_and_dns_errors():
    """Verify classification and remediation for DNS and network failure."""
    advise_error, ErrorAdvisor, ErrorCategory, RemediationActionType, _, _ = _get_advisor()

    # DNS failure
    advice = advise_error("net::ERR_NAME_NOT_RESOLVED: could not resolve host example.com", context={"url": "https://example.com/item"})
    assert advice.category == ErrorCategory.NETWORK_CONNECTIVITY
    assert advice.is_retryable is True
    assert advice.action_type == RemediationActionType.BACKOFF_RETRY
    assert "DNS" in advice.root_cause_summary
    assert any("DNS" in step or "network" in step for step in advice.remediation_steps)

    # Connection refused
    advice_conn = advise_error(ConnectionRefusedError("Connection refused to 10.0.0.1:443"))
    assert advice_conn.category == ErrorCategory.NETWORK_CONNECTIVITY
    assert advice_conn.is_retryable is True


def test_classify_tls_certificate_errors():
    """Verify classification and remediation for TLS/SSL certificate failures."""
    advise_error, _, ErrorCategory, _, RemediationUrgency, _ = _get_advisor()

    err = "SSL: CERTIFICATE_VERIFY_FAILED certificate has expired (_ssl.c:1007)"
    advice = advise_error(err, context={"url": "https://expired.badssl.com/"})
    assert advice.category == ErrorCategory.TLS_CERTIFICATE
    assert advice.is_retryable is False
    assert advice.urgency in (RemediationUrgency.MEDIUM, RemediationUrgency.HIGH)
    assert "TLS" in advice.root_cause_summary or "cert" in advice.root_cause_summary.lower()


def test_classify_bot_challenge_with_context():
    """Verify bot challenge classification with context-dependent remediation."""
    advise_error, _, ErrorCategory, RemediationActionType, _, _ = _get_advisor()

    cf_err = "Cloudflare Turnstile challenge encountered on target page"

    # Context 1: Turnstile bypass available
    ctx_avail = {
        "turnstile_bypass": {"available": True, "status": "available"},
        "site_id": "vixen",
    }
    advice_avail = advise_error(cf_err, context=ctx_avail)
    assert advice_avail.category == ErrorCategory.BOT_CHALLENGE
    assert advice_avail.automated_remediation_available is True
    assert advice_avail.action_type == RemediationActionType.SOLVE_CAPTCHA

    # Context 2: Turnstile bypass unavailable -> requires operator Take Over
    ctx_unavail = {
        "turnstile_bypass": {"available": False, "status": "unavailable", "reason": "no_solver"},
        "site_id": "vixen",
    }
    advice_unavail = advise_error(cf_err, context=ctx_unavail)
    assert advice_unavail.category == ErrorCategory.BOT_CHALLENGE
    assert advice_unavail.automated_remediation_available is False
    assert advice_unavail.action_type == RemediationActionType.TAKE_OVER_LOGIN
    assert "Take Over" in advice_unavail.operator_message


def test_classify_auth_and_rate_limit_with_attempt_escalation():
    """Verify auth failure detection and rate limit escalation based on retry count."""
    advise_error, _, ErrorCategory, RemediationActionType, RemediationUrgency, _ = _get_advisor()

    # Auth 401
    advice_auth = advise_error("HTTP Error 401: Unauthorized", context={"site_id": "patreon", "url": "https://patreon.com/post/1"})
    assert advice_auth.category == ErrorCategory.AUTHENTICATION
    assert advice_auth.action_type in (RemediationActionType.TAKE_OVER_LOGIN, RemediationActionType.REFRESH_COOKIES)
    assert advice_auth.is_retryable is False

    # Rate limit 429 - 1st attempt: simple backoff
    advice_rl1 = advise_error("HTTP Error 429: Too Many Requests", context={"attempt_count": 1})
    assert advice_rl1.category == ErrorCategory.RATE_LIMIT
    assert advice_rl1.is_retryable is True
    assert advice_rl1.action_type == RemediationActionType.BACKOFF_RETRY
    assert advice_rl1.suggested_backoff_seconds >= 30.0

    # Rate limit 429 - 4th attempt: escalate to IP rotation or higher urgency
    advice_rl4 = advise_error("HTTP Error 429: Too Many Requests", context={"attempt_count": 4})
    assert advice_rl4.category == ErrorCategory.RATE_LIMIT
    assert advice_rl4.urgency in (RemediationUrgency.HIGH, RemediationUrgency.CRITICAL)
    assert advice_rl4.action_type in (RemediationActionType.ROTATE_IP, RemediationActionType.BACKOFF_RETRY)


def test_classify_storage_and_filesystem_errors():
    """Verify storage exhaustion and filesystem permission error diagnostics."""
    advise_error, _, ErrorCategory, RemediationActionType, RemediationUrgency, _ = _get_advisor()

    # ENOSPC
    advice_disk = advise_error(
        OSError(28, "No space left on device"),
        context={"download_dir": "/mnt/storage", "free_disk_bytes": 1024 * 1024},
    )
    assert advice_disk.category == ErrorCategory.STORAGE_FILESYSTEM
    assert advice_disk.is_retryable is False
    assert advice_disk.urgency == RemediationUrgency.CRITICAL
    assert advice_disk.action_type == RemediationActionType.FREE_STORAGE
    assert "space" in advice_disk.root_cause_summary.lower()

    # Permission denied
    advice_perm = advise_error(PermissionError("Permission denied: '/data/downloads/file.mp4'"))
    assert advice_perm.category == ErrorCategory.STORAGE_FILESYSTEM
    assert advice_perm.action_type == RemediationActionType.FIX_PERMISSIONS


def test_classify_database_lock_contention():
    """Verify SQLite database lock detection and concurrency remediation advice."""
    advise_error, _, ErrorCategory, RemediationActionType, _, _ = _get_advisor()

    err = sqlite3.OperationalError("database is locked")
    advice = advise_error(err, context={"busy_timeout_ms": 5000})
    assert advice.category == ErrorCategory.DATABASE_LOCK
    assert advice.is_retryable is True
    assert advice.action_type in (RemediationActionType.BACKOFF_RETRY, RemediationActionType.ADJUST_CONCURRENCY)
    assert any("lock" in step.lower() or "concurrency" in step.lower() for step in advice.remediation_steps)


def test_classify_browser_engine_crash_and_timeout():
    """Verify Playwright browser crash and navigation timeout remediation."""
    advise_error, _, ErrorCategory, RemediationActionType, _, _ = _get_advisor()

    crash_err = "playwright._impl._api_types.Error: Target page, context or browser has been closed"
    advice_crash = advise_error(crash_err)
    assert advice_crash.category == ErrorCategory.BROWSER_ENGINE
    assert advice_crash.is_retryable is True
    assert advice_crash.action_type == RemediationActionType.RELOAD_OR_RETRY_BROWSER

    timeout_err = "Timeout 30000ms exceeded while waiting for locator"
    advice_timeout = advise_error(timeout_err)
    assert advice_timeout.category == ErrorCategory.TIMEOUT
    assert advice_timeout.is_retryable is True


def test_batch_advise_and_aggregation():
    """Verify batch error analysis and statistical classification breakdown."""
    _, ErrorAdvisor, ErrorCategory, _, _, _ = _get_advisor()

    advisor = ErrorAdvisor()
    errors = [
        ("HTTP Error 429: Too Many Requests", {"attempt_count": 1}),
        ("HTTP Error 429: Rate limited", {"attempt_count": 2}),
        ("net::ERR_NAME_NOT_RESOLVED", {}),
        ("No space left on device", {}),
    ]

    report = advisor.batch_analyze(errors)
    assert len(report["results"]) == 4
    counts = report["category_counts"]
    assert counts[ErrorCategory.RATE_LIMIT] == 2
    assert counts[ErrorCategory.NETWORK_CONNECTIVITY] == 1
    assert counts[ErrorCategory.STORAGE_FILESYSTEM] == 1


def test_friendly_error_integration_compatibility():
    """Verify seamless backward-compatible bridge with friendly_error."""
    advise_error, _, _, _, _, _ = _get_advisor()
    from bulk_downloader.friendly_error import friendly_error

    # Existing friendly_error behaves identically
    msg = friendly_error("HTTP Error 404: Not Found")
    assert "Not found (404)" in msg

    # advise_error produces matching operator message
    adv = advise_error("HTTP Error 404: Not Found")
    assert "Not found (404)" in adv.operator_message or "404" in adv.operator_message


# ── P2-B bounce: the runner caller, the payload shape, the fallbacks ────────

_ADVICE_KEYS = {
    "category", "category_confidence", "root_cause_summary", "is_retryable",
    "suggested_backoff_seconds", "action_type", "operator_message", "remediation_steps",
    "urgency", "automated_remediation_available", "remediation_parameters",
    "diagnostic_metadata",
}
_URL = "https://example.test/row987-video"


def _site_runner(clean_workdir, site_id):
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner
    db_init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    return SiteRunner(site_id, {"name": site_id})


# (raw failure, category, action_type, urgency, is_retryable). The first two are the
# texts the display translation rewrites into prose the classifier cannot read (E4).
_RAW_CASES = (
    ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local "
     "issuer certificate (_ssl.c:1006)", "tls_certificate", "no_action", "HIGH", False),
    ("[Errno 28] No space left on device", "storage_filesystem", "free_storage", "CRITICAL", False),
    ("HTTP Error 429: Too Many Requests", "rate_limit", "backoff_retry", "LOW", True),
)
_UNMATCHED = "qzx row987 nothing recognisable here"
_TURNSTILE_RAW = "Turnstile challenge blocked this page"


def test_a_failed_job_is_advised_on_its_raw_failure(clean_workdir):
    """E1 + E4: through the real caller (SiteRunner._update_job, status failed). The display
    message is translated; the advice must classify the RAW failure, with exact values."""
    from bulk_downloader.friendly_error import friendly_error
    r = _site_runner(clean_workdir, "row987_e1")
    for i, (raw, category, action, urgency, retryable) in enumerate(_RAW_CASES):
        url = f"{_URL}-{i}"
        r._update_job(url, "failed", raw)
        job = r.jobs[url]
        assert job["status"] == "failed", job
        # precondition: the queue shows the translation, which differs from the raw text
        assert job["message"] == friendly_error(raw), job["message"]
        assert job["message"] != raw, job["message"]
        advice = job.get("remediation")
        assert advice is not None, f"a failed job carries no remediation: {raw!r}"
        assert set(advice) == _ADVICE_KEYS, sorted(advice)
        got = (advice["category"], advice["action_type"], advice["urgency"], advice["is_retryable"])
        assert got == (category, action, urgency, retryable), (raw, got)
        assert advice["remediation_steps"], advice
    tls = r.jobs[f"{_URL}-0"]["remediation"]
    assert tls["diagnostic_metadata"] == {"url": f"{_URL}-0"}, tls["diagnostic_metadata"]


def test_an_unrecognised_failure_is_advised_unknown_through_the_runner(clean_workdir):
    """E1: an unmatched failure reaches the job as 'unknown', never a guessed domain."""
    r = _site_runner(clean_workdir, "row987_e1u")
    r._update_job(_URL, "failed", _UNMATCHED)
    advice = r.jobs[_URL].get("remediation")
    assert advice is not None, "a failed job carries no remediation"
    assert set(advice) == _ADVICE_KEYS, sorted(advice)
    got = (advice["category"], advice["urgency"], advice["action_type"])
    assert got == ("unknown", "LOW", "backoff_retry"), got
    assert advice["root_cause_summary"] == _UNMATCHED


def test_only_a_failure_is_advised(clean_workdir):
    """Negative control: the same failure text under a non-failed status carries no advice."""
    r = _site_runner(clean_workdir, "row987_neg")
    statuses = ("pending", "running", "needs_review")
    for status in statuses:
        url = f"{_URL}-{status}"
        r._update_job(url, status, _RAW_CASES[2][0])
        assert r.jobs[url]["status"] == status, r.jobs[url]
        assert "remediation" not in r.jobs[url], (status, r.jobs[url])
    assert sum(1 for u in r.jobs if u.startswith(_URL)) == len(statuses)


def test_a_success_retires_the_failure_advice(clean_workdir):
    """S2: the job dict is merged, not replaced, and job detail serves it whole -- the advice
    for a failure must not survive the success that follows it. A retry keeps it."""
    r = _site_runner(clean_workdir, "row987_s2")
    r._update_job(_URL, "failed", _RAW_CASES[2][0])
    assert (r.jobs[_URL].get("remediation") or {}).get("category") == "rate_limit"
    r._update_job(_URL, "pending", "Retry requested")
    assert (r.jobs[_URL].get("remediation") or {}).get("category") == "rate_limit"
    r._update_job(_URL, "done", "ok")
    assert r.jobs[_URL]["status"] == "done", r.jobs[_URL]
    assert "remediation" not in r.jobs[_URL], r.jobs[_URL]["remediation"]


def test_an_advisor_fault_is_logged_and_recorded(clean_workdir, monkeypatch):
    """E2: an advisor crash must not vanish inside the translator's except, and must not
    block the queue update."""
    from bulk_downloader import friendly_error
    r = _site_runner(clean_workdir, "row987_e2")
    real_log = r.log

    class _Log:
        def __init__(self):
            self.warnings = []

        def warning(self, msg, *args, **kwargs):
            self.warnings.append(msg % args)

        def __getattr__(self, name):
            return getattr(real_log, name)

    log = _Log()
    monkeypatch.setattr(r, "log", log)

    def boom(*args, **kwargs):
        raise RuntimeError("row987 advisor boom")

    monkeypatch.setattr(friendly_error, "advise_error", boom)
    raw = _RAW_CASES[2][0]
    r._update_job(_URL, "failed", raw)
    job = r.jobs[_URL]
    assert job.get("remediation") == {"category": "unknown", "error": "row987 advisor boom"}, job
    assert log.warnings == [f"error advisor failed for {_URL}: row987 advisor boom"]
    assert job["status"] == "failed"
    assert job["message"] == friendly_error.friendly_error(raw)


def test_advice_payload_keys_and_values_are_pinned():
    """E1: to_dict() is the payload the runner stores; pin its key set and real values."""
    advise_error, _, _, _, _, _ = _get_advisor()
    payload = advise_error("HTTP Error 429: Too Many Requests").to_dict()
    assert set(payload) == _ADVICE_KEYS, sorted(payload)
    assert payload["category"] == "rate_limit"
    assert payload["remediation_parameters"] == {"backoff_seconds": 30.0, "attempt_count": 1}
    assert payload["remediation_steps"][0] == "Pause request pipeline for 30.0 seconds."


def test_unmatched_text_stays_unknown():
    """E1: unmatched text is 'unknown' (literal, so a relabelled constant cannot hide)."""
    advise_error, _, _, _, _, _ = _get_advisor()
    unmatched = advise_error(_UNMATCHED)
    got = (unmatched.category, unmatched.urgency, unmatched.action_type)
    assert got == ("unknown", "LOW", "backoff_retry"), got
    assert unmatched.root_cause_summary == _UNMATCHED
    empty = advise_error("")
    assert (empty.category, empty.root_cause_summary) == ("unknown", "Unspecified operational error")


def test_tls_verification_text_is_a_tls_failure():
    """E3: python ssl/requests ('certificate verify failed') and libcurl / curl_cffi
    ('SSL certificate problem') verification failures classify as TLS."""
    advise_error, _, _, _, _, _ = _get_advisor()
    texts = (
        "ssl certificate verify failed",
        "certificate verify failed: self-signed certificate in certificate chain",
        "Failed to perform, curl: (60) SSL certificate problem: unable to get local issuer "
        "certificate. See https://curl.se/libcurl/errors.html first for more details.",
    )
    for msg in texts:
        a = advise_error(msg)
        got = (a.category, a.action_type, a.urgency, a.is_retryable)
        assert got == ("tls_certificate", "no_action", "HIGH", False), (msg, got)
    # negative control: a path that merely NAMES a certificate is not a TLS failure
    assert advise_error("HTTP Error 404: Not Found /docs/certificate.pdf").category == "resource_unavailable"


def test_turnstile_advice_claims_only_a_measured_bypass_state():
    """S1 (row 360 contract): no measurement, or a probe that could not decide, is UNKNOWN;
    only a measured 'unavailable' may say unavailable. The queue's display text agrees."""
    advise_error, _, _, _, _, _ = _get_advisor()
    from bulk_downloader.friendly_error import friendly_error
    probe_failed = {"available": False, "status": "unknown",
                    "reason": "capability_probe_failed:RuntimeError"}
    absent = {"available": False, "status": "unavailable",
              "reason": "stealthy_fetcher_unavailable"}
    cases = (
        (None, "unknown", "measurement_not_supplied",
         "Cloudflare Turnstile. Bypass availability unknown (measurement_not_supplied). Use Take Over."),
        (probe_failed, "unknown", "capability_probe_failed:RuntimeError",
         "Cloudflare Turnstile. Bypass availability unknown "
         "(capability_probe_failed:RuntimeError). Use Take Over."),
        (absent, "unavailable", "stealthy_fetcher_unavailable",
         "Cloudflare Turnstile. Bypass unavailable (stealthy_fetcher_unavailable). Use Take Over."),
    )
    for state, bypass_status, reason, operator_message in cases:
        ctx = None if state is None else {"turnstile_bypass": state}
        advice = advise_error(_TURNSTILE_RAW, context=ctx)
        assert advice.operator_message == operator_message, (state, advice.operator_message)
        assert advice.remediation_parameters == {"reason": reason, "bypass_status": bypass_status}
        assert (advice.category, advice.action_type) == ("bot_challenge", "take_over_login")
        assert friendly_error(_TURNSTILE_RAW, context=ctx) == operator_message


def test_runner_turnstile_advice_uses_the_measurement_its_display_uses(clean_workdir, monkeypatch):
    """S1: through the runner, the advice sees the same live bypass measurement as the
    translated queue message, so the two never contradict each other."""
    from bulk_downloader import runner as runner_mod
    r = _site_runner(clean_workdir, "row987_s1")
    available = {"available": True, "status": "available", "reason": "stealthy_fetcher_available"}
    absent = {"available": False, "status": "unavailable", "reason": "stealthy_fetcher_unavailable"}

    monkeypatch.setattr(runner_mod, "_turnstile_bypass_state", lambda: dict(available))
    r._update_job(f"{_URL}-a", "failed", _TURNSTILE_RAW)
    job = r.jobs[f"{_URL}-a"]
    assert job["message"] == "Cloudflare Turnstile. Scrapling/nodriver bypass usually works."
    advice = job.get("remediation") or {}
    got = (advice.get("category"), advice.get("action_type"), advice.get("urgency"))
    assert got == ("bot_challenge", "solve_captcha", "LOW"), got
    assert advice["remediation_parameters"] == {"solver": "turnstile_bypass"}

    monkeypatch.setattr(runner_mod, "_turnstile_bypass_state", lambda: dict(absent))
    r._update_job(f"{_URL}-u", "failed", _TURNSTILE_RAW)
    job = r.jobs[f"{_URL}-u"]
    expected = "Cloudflare Turnstile. Bypass unavailable (stealthy_fetcher_unavailable). Use Take Over."
    assert job["message"] == expected
    advice = job.get("remediation") or {}
    assert advice.get("operator_message") == expected, advice
    assert advice["remediation_parameters"] == {
        "reason": "stealthy_fetcher_unavailable", "bypass_status": "unavailable"}
