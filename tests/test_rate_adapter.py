"""Row 951: Retry-After pauses only the throttled domain."""
from bulk_downloader.rate_adapter import RateLimitAdapter

BD_GATE_SCOPE = "module"


def test_retry_after_paces_one_domain_without_blocking_another():
    """Removing the per-domain deadline would either retry early or stall both lanes."""
    adapter = RateLimitAdapter()
    assert adapter.observe("api.example", 429, {"Retry-After": "5"}, now=10) == 5
    assert adapter.delay_for("api.example", now=12) == 3
    assert adapter.delay_for("other.example", now=12) == 0
    assert adapter.delay_for("api.example", now=15) == 0
    assert adapter.observe("api.example", 200, {}, now=15) == 0


# ── FIXER (row951 REFUTE E1/E2/E3) ───────────────────────────────────────

import pytest


def test_retry_after_http_date_form_is_honoured():
    """E1: RFC 7231 allows an HTTP-date; it must pace like the seconds form."""
    adapter = RateLimitAdapter()
    now = 1700000000.0  # 2023-11-14T22:13:20Z
    delay = adapter.observe("d", 429, {"Retry-After": "Tue, 14 Nov 2023 22:13:50 GMT"}, now=now)
    assert delay == pytest.approx(30.0)
    assert adapter.delay_for("d", now=now + 10) == pytest.approx(20.0)
    assert adapter.observe("d", 429, {"Retry-After": "30"}, now=now) == 30.0  # control


def test_success_before_deadline_recovers_immediately():
    """E2: a 200 BEFORE the deadline clears the limit (a mutant that drops the
    success-path pop leaves delay_for > 0 here)."""
    adapter = RateLimitAdapter()
    assert adapter.observe("api.example", 429, {"Retry-After": "10"}, now=10) == 10
    assert adapter.delay_for("api.example", now=12) == 8
    assert adapter.observe("api.example", 200, {}, now=12) == 0
    assert adapter.delay_for("api.example", now=12) == 0


@pytest.mark.parametrize("value", ["1e999", "inf", "nan", "-inf", "-5", "garbage",
                                   "Tue, 14 Nov 1970 00:00:00 GMT", "Tue, 14 Nov 99999 00:00:00 GMT"])
def test_malformed_negative_or_non_finite_retry_after_yields_zero(value):
    """E3: a bad header never blocks a domain forever (or negatively)."""
    adapter = RateLimitAdapter()
    now = 1700000000.0
    assert adapter.observe("d", 429, {"Retry-After": value}, now=now) == 0.0
    assert adapter.delay_for("d", now=now) == 0
    assert adapter.delay_for("d", now=now + 1e12) == 0
