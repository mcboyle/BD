"""v3.43.31: per-domain rate limiting.

Stops BulkDownloader from hammering a single backend when multiple
sites share a CDN. Common case: wowgirls.com and ultrafilms.com both
front through Cloudflare; pulling from both at 4 parallel chunks
each = 8 concurrent requests to a single Cloudflare PoP, which
triggers "challenge" pages or 429 responses on the second-tier
backend.

## Architecture

A single process-wide `DomainRateLimiter` singleton. Each download
acquires a slot on its URL's eTLD+1 before starting and releases
after completion. The acquire/release happens at the lowest layer
(per HTTP request), so parallel-chunk downloads of one file count
as N slots on one domain.

Two configurable caps per domain:
  - max_concurrent: simultaneous in-flight requests
  - max_per_sec: throttle on request *starts* (token bucket)

Both caps can be set globally (defaults) or per-domain (overrides).

## Why not per-site?

Per-site would miss the multi-site case — wowgirls and ultrafilms
each capped at 4 concurrent gives 8 to the shared CDN. The rate
limit MUST be keyed on what's actually shared (the registrable
domain) not what's nominally configured (the site).

## API

  acquire(url, timeout=...) -> AcquiredSlot context manager
  release(slot)  # also via context-manager exit

  set_global_limits(max_concurrent, max_per_sec)
  set_domain_limits(domain, max_concurrent, max_per_sec)
  remove_domain_limits(domain)  # revert to global

  get_status() -> dict for the UI status panel
"""
from __future__ import annotations

import threading
import time
from collections import deque


# ── Module-level singleton ────────────────────────────────────────────

# Default global caps. "0" = disabled (no throttling). The user-facing
# default is 8 concurrent / 4 per sec which is generous enough that
# normal workloads aren't slowed, but still catches the multi-site
# hammering case.
DEFAULT_MAX_CONCURRENT = 0  # 0 = disabled by default
DEFAULT_MAX_PER_SEC = 0.0


# How long to wait in acquire() before giving up. Long enough that
# normal contention doesn't fail; short enough that a stuck worker
# doesn't deadlock the whole queue.
DEFAULT_ACQUIRE_TIMEOUT_S = 300.0


class _DomainState:
    """Internal tracking state for one domain. Lazy-created on first
    request to that domain.

    Token bucket model: refilled at `rate` tokens/sec up to `bucket`
    capacity. Each acquire() consumes one token. We use a request-
    timestamp deque rather than a continuous bucket — slightly more
    memory but easier to reason about.
    """
    __slots__ = ("active", "recent_starts", "lock", "cond")
    def __init__(self):
        # Count of in-flight requests on this domain
        self.active = 0
        # Timestamps of recent request starts (for per-second cap)
        self.recent_starts = deque(maxlen=512)
        # Lock + condition for acquire to wait on
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)


class _AcquiredSlot:
    """Context manager returned by acquire(). Releases on __exit__
    even if the caller raises."""
    __slots__ = ("limiter", "domain", "released")
    def __init__(self, limiter, domain):
        self.limiter = limiter
        self.domain = domain
        self.released = False
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.release()
    def release(self):
        if not self.released:
            self.released = True
            self.limiter._release(self.domain)


class DomainRateLimiter:
    """Process-wide singleton. Construct via get_limiter()."""

    def __init__(self):
        # Per-domain state, lazily populated. Outer lock protects the
        # dict itself; per-domain locks handle the actual queueing.
        self._states: dict[str, _DomainState] = {}
        self._states_lock = threading.Lock()
        # Configured caps
        self._global_max_concurrent = DEFAULT_MAX_CONCURRENT
        self._global_max_per_sec = DEFAULT_MAX_PER_SEC
        self._per_domain_overrides: dict[str, dict] = {}

    # ── Configuration ───────────────────────────────────────────────

    def set_global_limits(self, max_concurrent: int,
                            max_per_sec: float):
        """Set the defaults applied to all domains without an override.
        Both must be >= 0; 0 means "no limit"."""
        self._global_max_concurrent = max(0, int(max_concurrent))
        self._global_max_per_sec = max(0.0, float(max_per_sec))
        # Wake any waiters in case the new limits unblock them
        with self._states_lock:
            states = list(self._states.values())
        for s in states:
            with s.cond:
                s.cond.notify_all()

    def set_domain_limits(self, domain: str, max_concurrent: int,
                            max_per_sec: float):
        """Override the global cap for one domain. Useful for known-
        weak backends ('crank Cloudflare site X down to 2 concurrent
        even though most domains can do 8')."""
        if not domain:
            return
        self._per_domain_overrides[domain.lower()] = {
            "max_concurrent": max(0, int(max_concurrent)),
            "max_per_sec": max(0.0, float(max_per_sec)),
        }
        state = self._get_state(domain.lower())
        with state.cond:
            state.cond.notify_all()

    def remove_domain_limits(self, domain: str):
        """Revert to the global cap for this domain."""
        if not domain:
            return
        self._per_domain_overrides.pop(domain.lower(), None)
        state = self._get_state(domain.lower())
        with state.cond:
            state.cond.notify_all()

    def get_effective_limits(self, domain: str) -> dict:
        """Return the actually-applied caps for a domain, merging
        per-domain override over global default."""
        override = self._per_domain_overrides.get((domain or "").lower())
        if override:
            return dict(override)
        return {
            "max_concurrent": self._global_max_concurrent,
            "max_per_sec": self._global_max_per_sec,
        }

    # ── Acquire / release ───────────────────────────────────────────

    def _get_state(self, domain: str) -> _DomainState:
        """Lazy state lookup. Domain is assumed lowercased."""
        with self._states_lock:
            state = self._states.get(domain)
            if state is None:
                state = _DomainState()
                self._states[domain] = state
            return state

    def acquire(self, url: str,
                  timeout: float = DEFAULT_ACQUIRE_TIMEOUT_S) -> _AcquiredSlot:
        """Block until a slot is available on `url`'s eTLD+1, or
        until `timeout` elapses. Returns a context manager.

        When no cap is configured (max_concurrent=0 AND max_per_sec=0),
        returns immediately — the limiter is effectively a no-op for
        domains without limits. This keeps the per-request overhead
        near zero in the common case.

        Raises TimeoutError if the timeout elapses.
        """
        domain = self._extract_domain(url)
        if not domain:
            # No domain extractable (file:// URL, magnet:, weird format) —
            # don't block, just return a no-op slot.
            return _AcquiredSlot(self, "")
        limits = self.get_effective_limits(domain)
        max_concurrent = limits["max_concurrent"]
        max_per_sec = limits["max_per_sec"]
        if max_concurrent <= 0 and max_per_sec <= 0:
            # No effective limits — fast path, no state mutation
            return _AcquiredSlot(self, "")
        state = self._get_state(domain)
        deadline = time.time() + timeout
        with state.cond:
            while True:
                # Recompute effective limits each iteration so live
                # config changes (set_global_limits) unblock waiters.
                limits = self.get_effective_limits(domain)
                max_concurrent = limits["max_concurrent"]
                max_per_sec = limits["max_per_sec"]
                # 1. Concurrent check
                if max_concurrent > 0 and state.active >= max_concurrent:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"rate limit acquire timeout: {domain} "
                            f"({state.active}/{max_concurrent} concurrent)")
                    state.cond.wait(min(remaining, 5.0))
                    continue
                # 2. Per-second check — token-bucket-ish: count the
                # request starts in the last 1.0s; if >= max, wait
                # until the oldest falls off.
                if max_per_sec > 0:
                    now = time.time()
                    cutoff = now - 1.0
                    # Drop expired entries from the left
                    while state.recent_starts and state.recent_starts[0] < cutoff:
                        state.recent_starts.popleft()
                    if len(state.recent_starts) >= max_per_sec:
                        wait_for = max(0.01,
                                        state.recent_starts[0] + 1.0 - now)
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            raise TimeoutError(
                                f"rate limit acquire timeout: {domain} "
                                f"({len(state.recent_starts)} reqs/sec)")
                        state.cond.wait(min(remaining, wait_for))
                        continue
                # All checks passed — commit
                state.active += 1
                state.recent_starts.append(time.time())
                return _AcquiredSlot(self, domain)

    def _release(self, domain: str):
        """Called by _AcquiredSlot. domain="" is the no-op-slot case."""
        if not domain:
            return
        state = self._states.get(domain)
        if state is None:
            return
        with state.cond:
            if state.active > 0:
                state.active -= 1
            state.cond.notify()

    # ── Status / introspection ──────────────────────────────────────

    def get_status(self) -> dict:
        """Snapshot for the UI status panel. Returns global config +
        per-domain in-flight counts + recent throughput estimates."""
        with self._states_lock:
            states_snapshot = dict(self._states)
        domains = []
        now = time.time()
        cutoff = now - 1.0
        for domain, state in states_snapshot.items():
            # Don't lock state.lock here — it's a snapshot, slightly
            # stale reads are fine for status display
            recent_count = sum(1 for ts in state.recent_starts
                                if ts > cutoff)
            limits = self.get_effective_limits(domain)
            domains.append({
                "domain": domain,
                "active": state.active,
                "recent_per_sec": recent_count,
                "max_concurrent": limits["max_concurrent"],
                "max_per_sec": limits["max_per_sec"],
                "has_override": domain in self._per_domain_overrides,
            })
        # Sort: most-active first so the user sees hot domains
        domains.sort(key=lambda d: (-d["active"], -d["recent_per_sec"]))
        return {
            "global_max_concurrent": self._global_max_concurrent,
            "global_max_per_sec": self._global_max_per_sec,
            "domain_overrides": dict(self._per_domain_overrides),
            "domains": domains,
        }

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_domain(url) -> str:
        """Extract the rate-limit key for a URL.

          - Only http/https URLs get a key (magnet links, file://, etc.
            are no-ops since rate limiting them makes no sense)
          - The key IS the registrable domain (eTLD+1), asked of the one
            canonical rule in `registrable_domain` (v3.66.1018), so two
            .co.uk registrants -- or two github.io pages -- no longer
            share a bucket
          - Falls back to the bare hostname only when that rule yields
            nothing at all (a host it cannot normalize)
          - Empty string for anything we can't parse

        v3.66.1020: this docstring described three things that stopped being
        true at @1018 -- that the derivation is "intentionally simpler than
        full eTLD+1", that it delegates to extension_vault, and that it
        repairs a bare-suffix answer afterwards. @1018 replaced all of that
        with one call and REMOVED the import edge (declared with --shrink), so
        the prose was describing a mechanism the code no longer had.

        Caller blocks acquire() on this key. Empty string means
        "no rate limiting" — bypass the cap entirely.
        """
        if not isinstance(url, str) or not url:
            return ""
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
        except Exception:
            return ""
        # Only rate-limit http/https schemes. magnet, file, data, etc.
        # don't go through our HTTP downloader and don't benefit from
        # per-domain throttling.
        if parsed.scheme not in ("http", "https"):
            return ""
        host = (parsed.hostname or "").lower()
        if not host:
            return ""
        # v3.66.1018: one rule, asked once. What stood here was a THREE-layer
        # repair of a wrong answer: extension_vault's last-two-labels join, a
        # suffix check to notice it had returned a bare public suffix, a local
        # set of eight two-label TLDs to patch the ones it knew about, and a
        # last-two-labels fallback underneath. Every layer existed because the
        # layer below it was wrong, and the eight-entry set was a second source
        # of truth for a question registrable_domain answers completely --
        # anything outside those eight (github.io, com.br's neighbours, the
        # user-content hosts) still keyed two unrelated registrants to one
        # bucket, so they shared a rate limit.
        from .registrable_domain import registrable_domain
        return registrable_domain(host) or host


# Singleton instance
_LIMITER = None
_LIMITER_LOCK = threading.Lock()


def get_limiter() -> DomainRateLimiter:
    """Module-level accessor. Lazy-construct on first call so we
    don't bootstrap state on import."""
    global _LIMITER
    if _LIMITER is None:
        with _LIMITER_LOCK:
            if _LIMITER is None:
                _LIMITER = DomainRateLimiter()
    return _LIMITER


def acquire(url: str, timeout: float = DEFAULT_ACQUIRE_TIMEOUT_S):
    """Module-level shortcut: get_limiter().acquire(url, timeout)."""
    return get_limiter().acquire(url, timeout=timeout)


# ── Config loading ────────────────────────────────────────────────────

def configure_from_app_config(app_cfg: dict):
    """Apply rate limits from app_config.json. Called at startup
    after the config is loaded. Schema:
      {
        "rate_limit_global_concurrent": int,
        "rate_limit_global_per_sec": float,
        "rate_limit_domain_overrides": {
          "example.com": {"max_concurrent": 2, "max_per_sec": 1.0},
          ...
        }
      }
    """
    if not isinstance(app_cfg, dict):
        return
    limiter = get_limiter()
    try:
        gc = int(app_cfg.get("rate_limit_global_concurrent", 0))
    except (TypeError, ValueError):
        gc = 0
    try:
        gs = float(app_cfg.get("rate_limit_global_per_sec", 0.0))
    except (TypeError, ValueError):
        gs = 0.0
    limiter.set_global_limits(gc, gs)
    overrides = app_cfg.get("rate_limit_domain_overrides") or {}
    if isinstance(overrides, dict):
        for domain, limits in overrides.items():
            if not isinstance(limits, dict):
                continue
            try:
                mc = int(limits.get("max_concurrent", 0))
                mps = float(limits.get("max_per_sec", 0.0))
                limiter.set_domain_limits(domain, mc, mps)
            except (TypeError, ValueError):
                continue
