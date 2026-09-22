"""Row 1003: Decorrelated Full-Jitter Exponential Backoff Profiles.

Provides enterprise jittered exponential backoff algorithms (Full Jitter, Equal Jitter,
Decorrelated Jitter, and Exponential) to eliminate thundering herd and collision resonance
across distributed workers, HTTP clients, and database locks.

RED on baseline: fails with AssertionError (missing backoff profiles capability in retry_policy),
with positive control test_baseline_retry_policy_positive_control passing.
"""
from __future__ import annotations

import random
from typing import Any

BD_GATE_SCOPE = "module"


def _get_backoff_profiles():
    """Retrieve backoff profile symbols or assert capability failure on baseline."""
    try:
        from bulk_downloader.backoff_profiles import (
            BackoffProfile,
            BackoffSequence,
            BackoffStrategy,
            compute_backoff,
            get_profile,
            list_profiles,
        )
        return BackoffProfile, BackoffSequence, BackoffStrategy, compute_backoff, get_profile, list_profiles
    except (ImportError, ModuleNotFoundError) as exc:
        from bulk_downloader import retry_policy as rp
        strategy = getattr(rp, "BackoffStrategy", None)
        assert strategy is not None, (
            f"Row 1003 capability missing: Decorrelated Full-Jitter Exponential Backoff Profiles "
            f"not present on baseline: {exc}"
        )
        raise


def test_baseline_retry_policy_positive_control():
    """Positive control: verify existing retry_policy compute_next_delay is functional at baseline."""
    from bulk_downloader.retry_policy import compute_next_delay

    delay = compute_next_delay("transient", attempt=0, apply_jitter=False)
    assert delay == 30  # baseline transient base_delay_s = 30


def test_backoff_profiles_import_and_metadata():
    """Verify backoff profiles module exports and strategy taxonomy."""
    BackoffProfile, BackoffSequence, BackoffStrategy, compute_backoff, get_profile, list_profiles = _get_backoff_profiles()

    assert BackoffStrategy.FULL_JITTER == "full_jitter"
    assert BackoffStrategy.EQUAL_JITTER == "equal_jitter"
    assert BackoffStrategy.DECORRELATED_JITTER == "decorrelated_jitter"
    assert BackoffStrategy.EXPONENTIAL == "exponential"
    assert BackoffStrategy.PERCENTAGE_JITTER == "percentage_jitter"

    prof = get_profile("default")
    assert isinstance(prof, BackoffProfile)
    assert prof.strategy == BackoffStrategy.FULL_JITTER


def test_full_jitter_distribution_and_bounds():
    """Verify Full Jitter generates delays strictly within [min_delay, min(cap, base*factor^attempt)]."""
    BackoffProfile, _, BackoffStrategy, compute_backoff, _, _ = _get_backoff_profiles()

    profile = BackoffProfile(
        name="test_full",
        strategy=BackoffStrategy.FULL_JITTER,
        base_delay_s=2.0,
        max_delay_s=60.0,
        factor=2.0,
        min_delay_s=0.5,
    )

    # For attempt 3, max possible is min(60, 2 * 2^3) = 16.0
    samples = [compute_backoff(attempt=3, profile=profile) for _ in range(50)]
    assert all(0.5 <= s <= 16.0 for s in samples)
    # Proves jitter is active (not constant)
    assert len(set(samples)) > 20


def test_equal_jitter_distribution_and_floor():
    """Verify Equal Jitter enforces half-deterministic ceiling and half-random jitter."""
    BackoffProfile, _, BackoffStrategy, compute_backoff, _, _ = _get_backoff_profiles()

    profile = BackoffProfile(
        name="test_equal",
        strategy=BackoffStrategy.EQUAL_JITTER,
        base_delay_s=2.0,
        max_delay_s=100.0,
        factor=2.0,
        min_delay_s=0.1,
    )

    # For attempt 2, temp is 2 * 4 = 8.0. Half is 4.0. Range is [4.0, 8.0].
    samples = [compute_backoff(attempt=2, profile=profile) for _ in range(50)]
    assert all(4.0 <= s <= 8.0 for s in samples)
    assert min(samples) >= 4.0
    assert max(samples) <= 8.0


def test_decorrelated_jitter_sequence():
    """Verify Decorrelated Jitter samples between base and prev_delay * 3."""
    BackoffProfile, _, BackoffStrategy, compute_backoff, _, _ = _get_backoff_profiles()

    profile = BackoffProfile(
        name="test_decorrelated",
        strategy=BackoffStrategy.DECORRELATED_JITTER,
        base_delay_s=1.0,
        max_delay_s=30.0,
        decorrelated_multiplier=3.0,
        min_delay_s=0.5,
    )

    prev = 2.0
    # Next sample must be in [base, min(max_delay, prev * 3)] = [1.0, 6.0]
    samples = [compute_backoff(attempt=1, profile=profile, prev_delay=prev) for _ in range(40)]
    assert all(1.0 <= s <= 6.0 for s in samples)
    assert len(set(samples)) > 15


def test_deterministic_reproducibility_with_seed():
    """Verify deterministic delay sequence when seeded Random generator is provided."""
    BackoffProfile, _, BackoffStrategy, compute_backoff, _, _ = _get_backoff_profiles()

    profile = BackoffProfile(
        name="test_seeded",
        strategy=BackoffStrategy.FULL_JITTER,
        base_delay_s=1.0,
        max_delay_s=100.0,
        factor=2.0,
    )

    rng1 = random.Random(42)
    rng2 = random.Random(42)

    seq1 = [compute_backoff(attempt=i, profile=profile, rng=rng1) for i in range(5)]
    seq2 = [compute_backoff(attempt=i, profile=profile, rng=rng2) for i in range(5)]
    assert seq1 == seq2


def test_stateful_backoff_sequence_and_reset():
    """Verify stateful BackoffSequence tracks iterations, elapsed duration, and resets properly."""
    BackoffProfile, BackoffSequence, BackoffStrategy, _, _, _ = _get_backoff_profiles()

    profile = BackoffProfile(
        name="test_seq",
        strategy=BackoffStrategy.EXPONENTIAL,
        base_delay_s=1.0,
        max_delay_s=50.0,
        factor=2.0,
    )

    seq = BackoffSequence(profile)
    assert seq.current_attempt == 0
    assert seq.total_elapsed == 0.0

    d0 = seq.next_delay()  # attempt 0: 1.0
    assert d0 == 1.0
    assert seq.current_attempt == 1
    assert seq.total_elapsed == 1.0

    d1 = seq.next_delay()  # attempt 1: 2.0
    assert d1 == 2.0
    assert seq.current_attempt == 2
    assert seq.total_elapsed == 3.0

    d2 = seq.next_delay()  # attempt 2: 4.0
    assert d2 == 4.0
    assert seq.total_elapsed == 7.0

    seq.reset()
    assert seq.current_attempt == 0
    assert seq.total_elapsed == 0.0


def test_preconfigured_profiles_catalog():
    """Verify preconfigured catalog profiles exist with valid bounds and strategy assignments."""
    _, _, BackoffStrategy, _, get_profile, list_profiles = _get_backoff_profiles()

    profiles = list_profiles()
    assert "default" in profiles
    assert "rate_limit" in profiles
    assert "transient" in profiles
    assert "database_lock" in profiles
    assert "aggressive_decorrelated" in profiles

    rl = get_profile("rate_limit")
    assert rl.base_delay_s >= 10.0
    assert rl.max_delay_s >= 300.0

    db_prof = get_profile("database_lock")
    assert db_prof.strategy == BackoffStrategy.DECORRELATED_JITTER
    assert db_prof.max_delay_s <= 10.0


def test_clamping_and_overflow_protection():
    """Verify extreme attempt numbers do not overflow or exceed max_delay_s."""
    BackoffProfile, _, BackoffStrategy, compute_backoff, _, _ = _get_backoff_profiles()

    profile = BackoffProfile(
        name="test_clamp",
        strategy=BackoffStrategy.FULL_JITTER,
        base_delay_s=1.0,
        max_delay_s=120.0,
        factor=2.0,
    )

    delay = compute_backoff(attempt=1000, profile=profile)
    assert delay <= 120.0
    assert delay >= profile.min_delay_s


def test_retry_policy_seamless_integration():
    """Verify integration bridge with bulk_downloader.retry_policy.next_delay."""
    _, _, BackoffStrategy, _, _, _ = _get_backoff_profiles()
    from bulk_downloader.retry_policy import next_delay

    # Baseline call (no profile specified) behaves identically
    d_legacy = next_delay("transient", attempt=1, apply_jitter=False)
    assert d_legacy == 60  # base 30 * factor 2 = 60

    # Call with custom strategy
    d_jittered = next_delay("transient", attempt=1, apply_jitter=True, jitter_strategy=BackoffStrategy.FULL_JITTER)
    assert isinstance(d_jittered, (int, float))
    assert d_jittered >= 1


def test_r1_retry_after_header_not_overridden_by_profile():
    """R1: a server's Retry-After must not be discarded by a profile."""
    from bulk_downloader.retry_policy import compute_next_delay
    for attempt in range(5):
        d = compute_next_delay(
            "rate_limited", attempt,
            retry_after_header=300,
            apply_jitter=True,
            profile="rate_limit",
        )
        assert d >= 300, (
            f"attempt {attempt}: Retry-After 300 but got {d}; "
            f"profile must not undercut the server's header")


def test_r2_profile_delay_never_zero():
    """R2: a profile path must never return 0 (which means 'don't retry')."""
    _, _, _, _, compute_backoff, get_profile = _get_backoff_profiles()
    from bulk_downloader.retry_policy import compute_next_delay, CLASSES
    for name in ("database_lock", "aggressive_decorrelated", "rate_limit"):
        budget = CLASSES["transient"]["max_attempts"]
        for attempt in range(budget):
            d = compute_next_delay(
                "transient", attempt,
                apply_jitter=True,
                profile=name,
            )
            assert d >= 1, (
                f"profile={name} attempt={attempt} returned {d}; "
                f"delay must be >= 1 (0 means don't retry)")


def test_r3_decorrelated_prev_delay_changes_sequence():
    """R3: decorrelated jitter must depend on prev_delay state."""
    _, _, _, compute_backoff, get_profile, _ = _get_backoff_profiles()
    import random as _random
    prof = get_profile("aggressive_decorrelated")
    rng1 = _random.Random(99)
    rng2 = _random.Random(99)
    d1 = compute_backoff(0, prof, prev_delay=None, rng=rng1)
    d2 = compute_backoff(0, prof, prev_delay=500.0, rng=rng2)
    assert d1 != d2, (
        "decorrelated jitter returned the same value regardless of prev_delay; "
        "state must influence the sequence")
