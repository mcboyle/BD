"""v3.43.35: Multi-account session pool with lease/release semantics.

The existing v3.20-era rotation logic (`_rotate_account_if_available`)
is single-threaded: one active account at a time, with a 24h cooldown
when a failure happens. That's too coarse for the actual workload:

  1. **Wastes parallelism**: with 5 accounts and 5 workers, all five
     workers compete for the same active account. The other four
     accounts sit idle until a failure rotates to them.
  2. **24h cooldown is overkill** for transient 429s. Most adult-site
     rate limits clear in 5-60 minutes.
  3. **No health tracking**: a perma-broken account (wrong password,
     captcha that never resolves) cycles forever, getting picked
     again 24h later, failing again, rotating again. Wastes login
     attempts.
  4. **No introspection**: user can't see why their downloads are
     stalled — is it captcha? rate limit? bad creds? — without
     reading stderr.

## Architecture

Per-(site_id, account_idx) state record in a process-wide pool:

  state = "available" | "in_use" | "cooling_down" | "dead"
  in_use_by    — worker thread id holding the lease (if in_use)
  cooldown_until — Unix ts when state auto-reverts to available
  fail_count   — consecutive failures; >= MAX_FAILS → dead
  last_used    — Unix ts of last lease (for LRU selection)
  last_error   — short reason string for the UI

Workers call `lease(site_id)` before downloading; it returns the
(account_idx, credentials) of the best available account. After
the work completes, `release(site_id, account_idx)` returns it
to the pool. On rate-limit-style errors, `mark_cooldown()` shifts
state to cooling_down for a configurable duration.

## Lease selection policy

When multiple accounts are available, pick the one with the OLDEST
`last_used` — load-balances across accounts so no single one
takes the brunt of the throttling.

## Persistence

State persists per-account in cfg["accounts"][idx]:
  cooldown_until, fail_count, last_used, last_error

Restored on app startup via `restore_from_config()`. This means
a 24h dead account survives a server restart — which is the right
behavior for permanent account problems but means the user has to
manually reset.

## Cooldown duration

Configured per-site via `cfg["account_cooldown_seconds"]`. Default
300 (5 min). Sites known to throttle hard can configure 3600 (1h).

## Backward compatibility

Sites without `cfg["accounts"]` get a single implicit slot using
the top-level username/password fields. Lease/release still work
identically — the pool just has one element.
"""
from __future__ import annotations

import threading
import time
from typing import Optional


# Default cooldown when a 429 or rate-limit pattern is detected
DEFAULT_COOLDOWN_S = 300       # 5 min
# Consecutive failures before an account is marked dead
MAX_CONSECUTIVE_FAILURES = 3
# Lease timeout default — how long to wait for an account
DEFAULT_LEASE_TIMEOUT_S = 60.0


class AccountUnavailable(Exception):
    """Raised by lease() when no account is available within timeout.
    Carries a `reason` so the caller can surface it to the user."""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _AccountState:
    """Per-account state inside the pool. Lives in memory; persisted
    fields mirror back to cfg["accounts"][idx] on each mutation so
    they survive restart."""
    __slots__ = ("idx", "username", "state", "in_use_by",
                  "cooldown_until", "fail_count", "last_used",
                  "last_error", "lease_count")

    def __init__(self, idx: int, username: str):
        self.idx = idx
        self.username = username
        self.state = "available"
        self.in_use_by = None
        self.cooldown_until = 0.0
        self.fail_count = 0
        self.last_used = 0.0
        self.last_error = ""
        # Total times this account has been leased (running counter
        # for the diagnostic endpoint)
        self.lease_count = 0


class AccountPool:
    """Per-site pool. The pool manager (module-level) holds one of
    these per site_id that has account state."""

    def __init__(self, site_id: str):
        self.site_id = site_id
        self._accounts: list[_AccountState] = []
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        # Per-site config snapshot for cooldown duration; updated on
        # each lease so config edits take effect within one lease.
        self._cooldown_s = DEFAULT_COOLDOWN_S

    def configure(self, accounts: list[dict],
                    cooldown_seconds: int = DEFAULT_COOLDOWN_S):
        """Initialize or update the pool from cfg["accounts"]. Called
        on app startup and after any site config save. Preserves
        live state for accounts that haven't changed; resets only
        when accounts are added/removed/replaced.

        Defensive: malformed entries (None, non-dict, bad numeric
        values) are silently skipped. A corrupted sites_config.json
        shouldn't crash the pool init — the user can fix it via
        the UI later."""
        self._cooldown_s = max(60, int(cooldown_seconds))
        with self._lock:
            old_by_user = {a.username: a for a in self._accounts}
            new_states = []
            real_idx = 0
            for i, acc in enumerate(accounts or []):
                # Defensive: skip entries that aren't dicts
                if not isinstance(acc, dict):
                    continue
                username = acc.get("username", "") or ""
                if not isinstance(username, str):
                    continue
                # Preserve existing state by username
                if username in old_by_user:
                    state = old_by_user[username]
                    state.idx = real_idx
                else:
                    state = _AccountState(real_idx, username)
                # Defensive numeric parses — bad types reset to defaults
                try:
                    cd = float(acc.get("cooldown_until", 0) or 0)
                except (TypeError, ValueError):
                    cd = 0.0
                if cd > time.time():
                    state.cooldown_until = cd
                    state.state = "cooling_down"
                try:
                    fc = int(acc.get("fail_count", 0) or 0)
                except (TypeError, ValueError):
                    fc = 0
                state.fail_count = max(0, fc)
                if state.fail_count >= MAX_CONSECUTIVE_FAILURES:
                    state.state = "dead"
                try:
                    lu = float(acc.get("last_used", 0) or 0)
                except (TypeError, ValueError):
                    lu = 0.0
                if lu > state.last_used:
                    state.last_used = lu
                err = acc.get("last_error", "")
                if err and not state.last_error:
                    state.last_error = str(err)[:200]
                new_states.append(state)
                real_idx += 1
            self._accounts = new_states
            self._cond.notify_all()

    def _expire_cooldowns(self):
        """Move any cooling_down accounts back to available if their
        cooldown has elapsed. Called inside the lock at every state
        query — cheap, lock-friendly."""
        now = time.time()
        for s in self._accounts:
            if s.state == "cooling_down" and s.cooldown_until <= now:
                s.state = "available"
                s.cooldown_until = 0.0

    # ── Lease / release ────────────────────────────────────────────

    def lease(self, timeout: float = DEFAULT_LEASE_TIMEOUT_S) -> int:
        """Acquire an available account. Returns the account's index
        (the position in cfg["accounts"]). Blocks up to `timeout`
        seconds for one to become available.

        Selection policy: oldest-`last_used` first → load-balances
        across accounts.

        Raises AccountUnavailable if nothing's available in time.
        """
        deadline = time.time() + timeout
        worker_id = threading.get_ident()
        with self._cond:
            while True:
                self._expire_cooldowns()
                # Find available accounts, sorted by last_used ascending
                available = [s for s in self._accounts
                              if s.state == "available"]
                if not available:
                    # Diagnose why nothing's available
                    if not self._accounts:
                        raise AccountUnavailable(
                            "no accounts configured")
                    states = {s.state: 0 for s in self._accounts}
                    for s in self._accounts:
                        states[s.state] = states.get(s.state, 0) + 1
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        reason = ("all accounts unavailable: "
                                   + ", ".join(f"{k}={v}"
                                                 for k, v in states.items()))
                        raise AccountUnavailable(reason)
                    # Wait for state change (release or cooldown
                    # expiry). The cooldown_expiry path is handled
                    # via the bounded wait — we re-check every 5s.
                    self._cond.wait(min(remaining, 5.0))
                    continue
                # Pick the LRU available account
                available.sort(key=lambda s: s.last_used)
                chosen = available[0]
                chosen.state = "in_use"
                chosen.in_use_by = worker_id
                chosen.last_used = time.time()
                chosen.lease_count += 1
                return chosen.idx

    def release(self, account_idx: int, success: bool = True,
                  error: str = ""):
        """Return an account to the pool.

        Args:
          account_idx: idx returned by lease()
          success: True if the URL succeeded → reset fail_count
                    False if it failed (caller didn't mark cooldown
                    or dead specifically)
          error: short reason if success=False
        """
        with self._cond:
            for s in self._accounts:
                if s.idx == account_idx:
                    if s.state == "in_use":
                        s.state = "available"
                    s.in_use_by = None
                    if success:
                        s.fail_count = 0
                        s.last_error = ""
                    else:
                        s.fail_count += 1
                        if error:
                            s.last_error = error[:200]
                        if s.fail_count >= MAX_CONSECUTIVE_FAILURES:
                            s.state = "dead"
                    self._cond.notify_all()
                    return

    def mark_cooldown(self, account_idx: int,
                        cooldown_seconds: Optional[int] = None,
                        reason: str = ""):
        """Move an account into cooling_down state. Called when a
        rate-limit error is detected (429, "too many requests" in
        body, captcha challenge, etc.). Different from release(
        success=False) because this is a TRANSIENT problem — the
        account isn't broken, it just needs to wait."""
        cd = cooldown_seconds or self._cooldown_s
        with self._cond:
            for s in self._accounts:
                if s.idx == account_idx:
                    s.state = "cooling_down"
                    s.cooldown_until = time.time() + cd
                    s.in_use_by = None
                    if reason:
                        s.last_error = reason[:200]
                    # Note: don't bump fail_count for cooldowns —
                    # this is a transient throttle, not an account
                    # health issue
                    self._cond.notify_all()
                    return

    def mark_dead(self, account_idx: int, reason: str = ""):
        """Permanently disable an account (until manually reset).
        Called for terminal errors like wrong password, account
        banned, captcha that doesn't resolve."""
        with self._cond:
            for s in self._accounts:
                if s.idx == account_idx:
                    s.state = "dead"
                    s.in_use_by = None
                    s.fail_count = MAX_CONSECUTIVE_FAILURES
                    if reason:
                        s.last_error = reason[:200]
                    self._cond.notify_all()
                    return

    def reset(self, account_idx: int):
        """Manually reset an account to available — clears dead,
        cooldown, fail_count. Used by the UI's 'Retry this account'
        button when the user has fixed something (e.g. updated
        password)."""
        with self._cond:
            for s in self._accounts:
                if s.idx == account_idx:
                    s.state = "available"
                    s.cooldown_until = 0.0
                    s.fail_count = 0
                    s.in_use_by = None
                    s.last_error = ""
                    self._cond.notify_all()
                    return

    # ── Introspection ──────────────────────────────────────────────

    def get_status(self) -> dict:
        """Snapshot for the diagnostic endpoint. No lock — slight
        staleness is fine for status display."""
        self._expire_cooldowns()
        accounts = []
        now = time.time()
        for s in self._accounts:
            entry = {
                "idx": s.idx,
                "username": s.username,
                "state": s.state,
                "fail_count": s.fail_count,
                "lease_count": s.lease_count,
                "in_use_by": s.in_use_by,
                "last_error": s.last_error,
            }
            if s.cooldown_until > now:
                entry["cooldown_seconds_remaining"] = int(
                    s.cooldown_until - now)
            if s.last_used:
                entry["seconds_since_last_use"] = int(now - s.last_used)
            accounts.append(entry)
        # Sort: available first (alphabetically by username), then
        # cooling_down (shortest remaining first), then dead, then
        # in_use
        sort_priority = {"available": 0, "cooling_down": 1,
                          "in_use": 2, "dead": 3}
        accounts.sort(key=lambda a: (sort_priority.get(a["state"], 99),
                                       a.get("cooldown_seconds_remaining", 0),
                                       a.get("username", "")))
        return {
            "site_id": self.site_id,
            "account_count": len(self._accounts),
            "cooldown_seconds": self._cooldown_s,
            "accounts": accounts,
        }


# ── Module-level manager ──────────────────────────────────────────────

# site_id -> AccountPool
_POOLS: dict[str, AccountPool] = {}
_POOLS_LOCK = threading.Lock()


def get_pool(site_id: str) -> AccountPool:
    """Get or lazily create the pool for a site. Pools are kept
    in-process; restart loses lease state (but cfg-persisted
    cooldown/fail_count are restored)."""
    with _POOLS_LOCK:
        pool = _POOLS.get(site_id)
        if pool is None:
            pool = AccountPool(site_id)
            _POOLS[site_id] = pool
        return pool


def configure_pool(site_id: str, accounts: list[dict],
                     cooldown_seconds: int = DEFAULT_COOLDOWN_S):
    """Initialize / update a site's pool. Called from app.py after
    sites_config load and after each site save."""
    pool = get_pool(site_id)
    pool.configure(accounts, cooldown_seconds=cooldown_seconds)


def remove_pool(site_id: str):
    """Drop the pool when a site is deleted."""
    with _POOLS_LOCK:
        _POOLS.pop(site_id, None)


def get_all_pools_status() -> list[dict]:
    """All pools across all sites — for the global diagnostic view."""
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
    return [p.get_status() for p in pools]


# ── Persistence helpers ──────────────────────────────────────────────

def serialize_for_config(pool: AccountPool) -> list[dict]:
    """Build the cfg["accounts"]-shaped list with health state baked
    in, for persisting via _save_sites_config. The caller merges
    these fields back into the existing account dicts (preserving
    username/password/cookie_file)."""
    out = []
    for s in pool._accounts:
        out.append({
            "cooldown_until": s.cooldown_until if s.cooldown_until > time.time() else 0.0,
            "fail_count": s.fail_count,
            "last_used": s.last_used,
            "last_error": s.last_error,
        })
    return out
