"""Cut 4 (operator intelligence) — failure_reasons.

`failure_reasons.reason_for(message, status_code)` extends the runner's existing
4-class map (`retry_policy.classify_failure` → transient|rate_limited|auth|
permanent) into an operator-facing reason object:

    {category, reason_code, title, suggested_action, retryable}

- `category` is the underlying class (so it stays consistent with the runner).
- `reason_code` is a stable machine slug (persisted on job_runs.reason_code).
- `title` + `suggested_action` are short operator strings.
- `retryable` is True only for the auto-retry classes (transient, rate_limited);
  auth/permanent are NOT auto-retryable (a wrong "retryable" silently loops).

RED on pristine 373: the module does not exist.
"""


def test_reason_for_auth_is_not_retryable():
    from bulk_downloader import failure_reasons as fr
    r = fr.reason_for("401 Unauthorized: login required", status_code=401)
    assert r["category"] == "auth"
    assert r["retryable"] is False
    assert r["reason_code"]
    assert r["title"]
    assert r["suggested_action"]


def test_reason_for_rate_limited_is_retryable():
    from bulk_downloader import failure_reasons as fr
    r = fr.reason_for("429 Too Many Requests", status_code=429)
    assert r["category"] == "rate_limited"
    assert r["retryable"] is True


def test_reason_for_transient_default_is_retryable():
    from bulk_downloader import failure_reasons as fr
    r = fr.reason_for("connection reset by peer")
    assert r["category"] == "transient"
    assert r["retryable"] is True


def test_reason_for_permanent_is_not_retryable():
    from bulk_downloader import failure_reasons as fr
    r = fr.reason_for("404 Not Found", status_code=404)
    assert r["category"] == "permanent"
    assert r["retryable"] is False


def test_reason_code_is_stable_per_category():
    from bulk_downloader import failure_reasons as fr
    a = fr.reason_for("429 slow down", status_code=429)["reason_code"]
    b = fr.reason_for("rate limited again", status_code=429)["reason_code"]
    assert a == b  # same category -> same stable code


def test_reason_for_coerces_bad_input():
    from bulk_downloader import failure_reasons as fr
    # non-string message / non-int status must not raise (runner hot path).
    r = fr.reason_for(None, status_code="not-an-int")
    assert r["category"] in ("transient", "rate_limited", "auth", "permanent")
    assert isinstance(r["retryable"], bool)
