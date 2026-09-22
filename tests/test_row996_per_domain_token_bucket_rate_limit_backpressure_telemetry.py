"""Row 996: Per-Domain Token Bucket Rate-Limit & Backpressure Telemetry.

Provides continuous token bucket rate-limiting with fractional token refill,
burst capacity limits, wait queue tracking, backpressure coefficient calculation,
and granular per-domain telemetry export.

RED on baseline: DomainRateLimiter has no get_backpressure_telemetry or token bucket engine.
"""
from __future__ import annotations

import time

import pytest

BD_GATE_SCOPE = "module"

try:
    import bulk_downloader.token_bucket_backpressure as _tb_mod
    from bulk_downloader.token_bucket_backpressure import (
        BackpressureState,
        DomainTokenBucketLimiter,
        TokenBucket,
    )
except ImportError:
    _tb_mod = None
    BackpressureState = None
    DomainTokenBucketLimiter = None
    TokenBucket = None


def test_positive_control_domain_rate_limiter_baseline():
    """Positive control: DomainRateLimiter baseline exists and operates."""
    from bulk_downloader.rate_limit import DomainRateLimiter

    limiter = DomainRateLimiter()
    assert limiter is not None
    assert hasattr(limiter, "acquire")


def test_behavioral_red_missing_backpressure_telemetry():
    """Verify that baseline DomainRateLimiter lacks per-domain backpressure telemetry."""
    from bulk_downloader.rate_limit import DomainRateLimiter

    limiter = DomainRateLimiter()
    # Baseline DomainRateLimiter does not provide get_backpressure_telemetry
    assert hasattr(limiter, "get_backpressure_telemetry"), (
        "DomainRateLimiter missing get_backpressure_telemetry method"
    )


def test_token_bucket_refill_and_burst():
    """Verify token bucket capacity, consumption, and continuous temporal refill."""
    if TokenBucket is None:
        pytest.skip("TokenBucket not yet available")

    bucket = TokenBucket(capacity=5.0, refill_rate=10.0)
    assert bucket.tokens == 5.0

    # Consume 3 tokens
    assert bucket.try_consume(3.0) is True
    assert bucket.tokens == pytest.approx(2.0, abs=0.05)

    # Attempt to consume more than available
    assert bucket.try_consume(5.0) is False

    # Simulate 0.2s elapsed -> 2.0 + (0.2 * 10.0) = 4.0 tokens
    time.sleep(0.25)
    assert bucket.tokens >= 4.0

    # Never exceeds capacity
    time.sleep(0.35)
    assert bucket.tokens == pytest.approx(5.0, abs=0.05)


def test_domain_token_bucket_limiter_acquire_and_release():
    """Verify per-domain token bucket rate-limiting and slot lifecycle."""
    if DomainTokenBucketLimiter is None:
        pytest.skip("DomainTokenBucketLimiter not yet available")

    limiter = DomainTokenBucketLimiter()
    limiter.configure_domain("api.example.com", capacity=3.0, refill_rate=5.0, max_concurrent=2)

    # Acquire within concurrency and token limits
    slot1 = limiter.acquire("https://api.example.com/item/1", timeout=1.0)
    assert slot1.domain == "api.example.com"
    assert slot1.is_acquired is True

    slot2 = limiter.acquire("https://api.example.com/item/2", timeout=1.0)
    assert slot2.domain == "api.example.com"

    # 3rd concurrent request should fail with timeout when max_concurrent=2
    with pytest.raises(TimeoutError, match="backpressure timeout"):
        limiter.acquire("https://api.example.com/item/3", timeout=0.1)

    slot1.release()
    assert slot1.is_acquired is False

    # Now 3rd slot can acquire
    slot3 = limiter.acquire("https://api.example.com/item/3", timeout=1.0)
    assert slot3.domain == "api.example.com"

    slot2.release()
    slot3.release()


def test_backpressure_telemetry_calculation():
    """Verify backpressure telemetry fields: wait depth, congestion coefficient, state."""
    if DomainTokenBucketLimiter is None or BackpressureState is None:
        pytest.skip("DomainTokenBucketLimiter not yet available")

    limiter = DomainTokenBucketLimiter()
    limiter.configure_domain("fast.cdn.com", capacity=10.0, refill_rate=20.0, max_concurrent=5)

    telemetry = limiter.get_backpressure_telemetry("fast.cdn.com")
    assert telemetry["domain"] == "fast.cdn.com"
    assert telemetry["tokens_available"] == pytest.approx(10.0, abs=0.1)
    assert telemetry["capacity"] == 10.0
    assert telemetry["refill_rate"] == 20.0
    assert telemetry["active_requests"] == 0
    assert telemetry["wait_queue_depth"] == 0
    assert telemetry["backpressure_score"] == pytest.approx(0.0, abs=0.05)
    assert telemetry["state"] == BackpressureState.NORMAL.value


def test_backpressure_congestion_state_detection():
    """Verify backpressure state transitions to CONGESTED when load increases."""
    if DomainTokenBucketLimiter is None or BackpressureState is None:
        pytest.skip("DomainTokenBucketLimiter not yet available")

    limiter = DomainTokenBucketLimiter()
    limiter.configure_domain("busy.net", capacity=2.0, refill_rate=2.0, max_concurrent=2)

    # Exhaust all tokens and concurrent capacity
    s1 = limiter.acquire("https://busy.net/a", timeout=1.0)
    s2 = limiter.acquire("https://busy.net/b", timeout=1.0)

    # Under saturation
    telemetry = limiter.get_backpressure_telemetry("busy.net")
    assert telemetry["active_requests"] == 2
    assert telemetry["tokens_available"] < 1.0
    assert telemetry["backpressure_score"] > 0.3
    assert telemetry["state"] in (BackpressureState.CONGESTED.value, BackpressureState.SHEDDING.value)

    s1.release()
    s2.release()


def test_domain_isolation():
    """Verify rate limits and backpressure on one domain do not cross to another."""
    if DomainTokenBucketLimiter is None:
        pytest.skip("DomainTokenBucketLimiter not yet available")

    limiter = DomainTokenBucketLimiter()
    limiter.configure_domain("domain-a.org", capacity=1.0, refill_rate=1.0, max_concurrent=1)
    limiter.configure_domain("domain-b.org", capacity=10.0, refill_rate=10.0, max_concurrent=10)

    s_a = limiter.acquire("https://domain-a.org/data", timeout=1.0)

    # domain-a is saturated
    with pytest.raises(TimeoutError):
        limiter.acquire("https://domain-a.org/data2", timeout=0.05)

    # domain-b is completely unhindered
    s_b = limiter.acquire("https://domain-b.org/data", timeout=1.0)
    assert s_b.domain == "domain-b.org"

    s_a.release()
    s_b.release()


def test_rate_limit_module_integration():
    """Verify integration of backpressure telemetry into the global rate_limit module."""
    if TokenBucket is None:
        pytest.skip("TokenBucket not yet available")
    from bulk_downloader.rate_limit import get_limiter

    limiter = get_limiter()
    assert hasattr(limiter, "get_backpressure_telemetry")
    status = limiter.get_backpressure_telemetry("example.com")
    assert isinstance(status, dict)
    assert "backpressure_score" in status
    assert "tokens_available" in status
