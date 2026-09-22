"""Decorrelated Full-Jitter Exponential Backoff Profiles.

Implements full jitter, equal jitter, decorrelated jitter, and truncated exponential
backoff profiles for high-throughput distributed scheduling and network retry policies.
Prevents synchronized thundering herds, resource contention, and network lock-stepping.
"""
from __future__ import annotations

import dataclasses
import random
from typing import Any, Optional


class BackoffStrategy:
    """Supported backoff and jitter algorithms."""
    FULL_JITTER = "full_jitter"
    EQUAL_JITTER = "equal_jitter"
    DECORRELATED_JITTER = "decorrelated_jitter"
    EXPONENTIAL = "exponential"
    PERCENTAGE_JITTER = "percentage_jitter"


@dataclasses.dataclass
class BackoffProfile:
    """Configuration profile for a backoff strategy."""
    name: str = "default"
    strategy: str = BackoffStrategy.FULL_JITTER
    base_delay_s: float = 1.0
    max_delay_s: float = 300.0
    factor: float = 2.0
    decorrelated_multiplier: float = 3.0
    min_delay_s: float = 0.5
    jitter_pct: float = 0.25


# Catalog of pre-configured profiles
DEFAULT_PROFILE = BackoffProfile(
    name="default",
    strategy=BackoffStrategy.FULL_JITTER,
    base_delay_s=1.0,
    max_delay_s=300.0,
    factor=2.0,
    min_delay_s=0.5,
)

AGGRESSIVE_DECORRELATED = BackoffProfile(
    name="aggressive_decorrelated",
    strategy=BackoffStrategy.DECORRELATED_JITTER,
    base_delay_s=0.5,
    max_delay_s=60.0,
    factor=2.0,
    decorrelated_multiplier=3.0,
    min_delay_s=0.2,
)

RATE_LIMIT_PROFILE = BackoffProfile(
    name="rate_limit",
    strategy=BackoffStrategy.FULL_JITTER,
    base_delay_s=30.0,
    max_delay_s=900.0,
    factor=2.0,
    min_delay_s=5.0,
)

TRANSIENT_PROFILE = BackoffProfile(
    name="transient",
    strategy=BackoffStrategy.EQUAL_JITTER,
    base_delay_s=2.0,
    max_delay_s=120.0,
    factor=2.0,
    min_delay_s=1.0,
)

DATABASE_LOCK_PROFILE = BackoffProfile(
    name="database_lock",
    strategy=BackoffStrategy.DECORRELATED_JITTER,
    base_delay_s=0.1,
    max_delay_s=5.0,
    factor=2.0,
    decorrelated_multiplier=2.5,
    min_delay_s=0.05,
)

_CATALOG: dict[str, BackoffProfile] = {
    "default": DEFAULT_PROFILE,
    "aggressive_decorrelated": AGGRESSIVE_DECORRELATED,
    "rate_limit": RATE_LIMIT_PROFILE,
    "transient": TRANSIENT_PROFILE,
    "database_lock": DATABASE_LOCK_PROFILE,
}


def register_profile(profile: BackoffProfile) -> None:
    """Register or override a named backoff profile in the catalog."""
    _CATALOG[profile.name] = profile


def get_profile(name_or_profile: BackoffProfile | str) -> BackoffProfile:
    """Resolve a profile instance or look up by name in catalog."""
    if isinstance(name_or_profile, BackoffProfile):
        return name_or_profile
    return _CATALOG.get(name_or_profile, DEFAULT_PROFILE)


def list_profiles() -> list[str]:
    """Return sorted list of registered profile names."""
    return sorted(_CATALOG.keys())


def compute_backoff(
    attempt: int,
    profile: BackoffProfile | str = "default",
    prev_delay: Optional[float] = None,
    rng: Optional[random.Random] = None,
) -> float:
    """Compute backoff delay in seconds for a given attempt count and profile.

    Algorithms:
      - FULL_JITTER: sleep = uniform(0, min(cap, base * factor^attempt))
      - EQUAL_JITTER: temp = min(cap, base * factor^attempt); sleep = (temp/2) + uniform(0, temp/2)
      - DECORRELATED_JITTER: sleep = min(cap, uniform(base, prev_delay * multiplier))
      - EXPONENTIAL: sleep = min(cap, base * factor^attempt)
      - PERCENTAGE_JITTER: sleep = temp + uniform(-temp*pct, temp*pct)
    """
    prof = get_profile(profile)
    rand = rng or random

    # Protect against huge exponents
    safe_attempt = max(0, min(int(attempt), 60))
    temp = min(prof.max_delay_s, prof.base_delay_s * (prof.factor ** safe_attempt))

    strategy = prof.strategy
    if strategy == BackoffStrategy.FULL_JITTER:
        delay = rand.uniform(0.0, temp)
    elif strategy == BackoffStrategy.EQUAL_JITTER:
        half = temp / 2.0
        delay = half + rand.uniform(0.0, half)
    elif strategy == BackoffStrategy.DECORRELATED_JITTER:
        if prev_delay is None or prev_delay <= 0:
            high = min(prof.max_delay_s, prof.base_delay_s * prof.decorrelated_multiplier)
            delay = rand.uniform(prof.base_delay_s, high)
        else:
            high = min(prof.max_delay_s, prev_delay * prof.decorrelated_multiplier)
            low = min(prof.base_delay_s, high)
            delay = rand.uniform(low, high)
    elif strategy == BackoffStrategy.EXPONENTIAL:
        delay = temp
    elif strategy == BackoffStrategy.PERCENTAGE_JITTER:
        jit = temp * prof.jitter_pct
        delay = temp + rand.uniform(-jit, jit)
    else:
        delay = temp

    return float(min(prof.max_delay_s, max(prof.min_delay_s, delay)))


class BackoffSequence:
    """Stateful iterator tracking in-flight attempt sequence and cumulative elapsed delay."""

    def __init__(
        self,
        profile: BackoffProfile | str = "default",
        rng: Optional[random.Random] = None,
    ) -> None:
        self.profile = get_profile(profile)
        self.rng = rng
        self.current_attempt: int = 0
        self.total_elapsed: float = 0.0
        self.prev_delay: Optional[float] = None

    def next_delay(self) -> float:
        """Compute next backoff step, advance attempt counter, and update total elapsed duration."""
        delay = compute_backoff(
            attempt=self.current_attempt,
            profile=self.profile,
            prev_delay=self.prev_delay,
            rng=self.rng,
        )
        self.prev_delay = delay
        self.total_elapsed += delay
        self.current_attempt += 1
        return delay

    def reset(self) -> None:
        """Reset sequence to initial state."""
        self.current_attempt = 0
        self.total_elapsed = 0.0
        self.prev_delay = None
