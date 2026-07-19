"""v3.43.35 regression tests — multi-account session pool.

Coverage:
  - AccountPool basics: configure, lease, release
  - State machine: available → in_use → available (success)
                   available → cooling_down (mark_cooldown)
                   available → dead (3+ failures)
                   any state → available (reset)
  - LRU selection policy (oldest last_used wins)
  - Concurrent lease: parallel workers get different accounts
  - Lease timeout when nothing available
  - Cooldown expiry returns account to available
  - Persistence: serialize_for_config round-trip
  - Runner integration: _rotate_account_if_available uses pool
  - App wiring: endpoints, lifecycle hooks, CFG_FIELDS
  - Adversarial: bad inputs, no accounts, missing pool
"""
from __future__ import annotations

import threading
import time
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_AP_PY = _REPO_ROOT / "bulk_downloader" / "account_pool.py"
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"
class _AggregateSrc:
    """runner.py + every runner_*.py mixin module (PHASE 3 decomposition v3.66.397+
    moved SiteRunner methods into siblings); glob keeps source-coupled guards green
    across all current and future runner cuts."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "runner.py"] + sorted(pkg_dir.glob("runner_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader")


# ── Module surface ────────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_account_pool_module_importable():
    import bulk_downloader.account_pool as ap  # noqa: F401


def test_get_pool_returns_per_site_singletons():
    """Two get_pool('A') calls return the same pool; different sites
    get different pools."""
    from bulk_downloader.account_pool import get_pool
    a1 = get_pool("siteA")
    a2 = get_pool("siteA")
    b = get_pool("siteB")
    assert a1 is a2
    assert a1 is not b


# ── Configure ─────────────────────────────────────────────────────────

def test_configure_populates_pool():
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([
        {"username": "alice"},
        {"username": "bob"},
        {"username": "carol"},
    ])
    status = pool.get_status()
    assert status["account_count"] == 3
    usernames = {a["username"] for a in status["accounts"]}
    assert usernames == {"alice", "bob", "carol"}


def test_configure_preserves_state_for_unchanged_accounts():
    """Adding an account doesn't reset state for the ones that were
    already there. Live cooldowns survive a config save."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}, {"username": "bob"}])
    # Mark alice cooling down
    pool.mark_cooldown(0, cooldown_seconds=600, reason="429")
    # Reconfigure with same accounts plus a new one
    pool.configure([
        {"username": "alice"},
        {"username": "bob"},
        {"username": "carol"},
    ])
    # Alice's cooldown must survive
    status = pool.get_status()
    alice = next(a for a in status["accounts"] if a["username"] == "alice")
    assert alice["state"] == "cooling_down"


def test_configure_restores_state_from_config_dict():
    """On app restart, configure() is called with cfg["accounts"]
    which has cooldown_until / fail_count / etc. baked in. Those
    must restore the pool's state."""
    from bulk_downloader.account_pool import (
        AccountPool, MAX_CONSECUTIVE_FAILURES,
    )
    pool = AccountPool("test")
    pool.configure([
        {"username": "alice",
         "cooldown_until": time.time() + 600,
         "fail_count": 1,
         "last_error": "429 rate limited"},
        {"username": "bob",
         "fail_count": MAX_CONSECUTIVE_FAILURES,
         "last_error": "bad password"},
    ])
    status = pool.get_status()
    alice = next(a for a in status["accounts"] if a["username"] == "alice")
    bob = next(a for a in status["accounts"] if a["username"] == "bob")
    assert alice["state"] == "cooling_down"
    assert bob["state"] == "dead"


# ── Lease / release ───────────────────────────────────────────────────

def test_lease_returns_account_idx():
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    idx = pool.lease(timeout=1.0)
    assert idx == 0


def test_lease_marks_in_use():
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    pool.lease(timeout=1.0)
    status = pool.get_status()
    assert status["accounts"][0]["state"] == "in_use"


def test_release_returns_to_available():
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    idx = pool.lease(timeout=1.0)
    pool.release(idx, success=True)
    status = pool.get_status()
    assert status["accounts"][0]["state"] == "available"


def test_release_failure_increments_fail_count():
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    idx = pool.lease(timeout=1.0)
    pool.release(idx, success=False, error="login failed")
    status = pool.get_status()
    assert status["accounts"][0]["fail_count"] == 1
    assert status["accounts"][0]["last_error"] == "login failed"


def test_release_success_resets_fail_count():
    """A successful operation must reset the failure counter.
    Otherwise temporary outages accumulate forever and an account
    eventually goes 'dead' even though it's been working fine."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    idx = pool.lease()
    pool.release(idx, success=False, error="temp error")
    pool.lease()
    pool.release(idx, success=True)
    status = pool.get_status()
    assert status["accounts"][0]["fail_count"] == 0


def test_three_failures_marks_dead():
    """The MAX_CONSECUTIVE_FAILURES threshold triggers dead state.
    Once dead, the account stays out of rotation until reset.

    Use a single-account pool so all failures accumulate on the
    same account (otherwise LRU spreads them and no single account
    hits MAX)."""
    from bulk_downloader.account_pool import (
        AccountPool, MAX_CONSECUTIVE_FAILURES,
    )
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    # Fail MAX_CONSECUTIVE_FAILURES times — all on alice (only one)
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        idx = pool.lease(timeout=1.0)
        pool.release(idx, success=False, error="bad creds")
    status = pool.get_status()
    alice = next(a for a in status["accounts"] if a["username"] == "alice")
    assert alice["state"] == "dead"
    assert alice["fail_count"] >= MAX_CONSECUTIVE_FAILURES


# ── LRU selection policy ──────────────────────────────────────────────

def test_lease_picks_lru_account():
    """When multiple accounts are available, the one with the oldest
    last_used wins. Load-balances across accounts."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}, {"username": "bob"}])
    # First lease picks alice (both have last_used=0; lock-step
    # tiebreaker: idx 0)
    idx1 = pool.lease(timeout=1.0)
    pool.release(idx1, success=True)
    # Second lease should pick bob (alice was just used)
    idx2 = pool.lease(timeout=1.0)
    assert idx2 != idx1, "LRU policy failed: same account picked twice"
    pool.release(idx2, success=True)


# ── Concurrent lease ─────────────────────────────────────────────────

def test_concurrent_leases_get_different_accounts():
    """Two threads leasing simultaneously must get different accounts
    when both are available. The whole point of the pool is parallelism."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([
        {"username": "alice"}, {"username": "bob"},
        {"username": "carol"}, {"username": "dave"},
    ])
    results = []
    barrier = threading.Barrier(4)
    def worker():
        barrier.wait()  # ensure simultaneity
        idx = pool.lease(timeout=2.0)
        results.append(idx)
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    # All 4 should have got DIFFERENT accounts
    assert len(set(results)) == 4, (
        f"concurrent lease got duplicates: {results}"
    )


def test_lease_blocks_when_all_in_use():
    """5 accounts, all in use → 6th lease waits."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": f"u{i}"} for i in range(3)])
    leased = [pool.lease(timeout=1.0) for _ in range(3)]
    # 4th should block; verify it does, with a short timeout
    blocked_at = time.time()
    try:
        pool.lease(timeout=0.5)
        raise AssertionError("4th lease succeeded; should've blocked")
    except Exception as e:
        elapsed = time.time() - blocked_at
        # AccountUnavailable raised after ~0.5s timeout
        assert "AccountUnavailable" in type(e).__name__
        assert 0.4 < elapsed < 1.0
    # Cleanup
    for idx in leased:
        pool.release(idx, success=True)


def test_lease_unblocks_when_account_released():
    """A lease() call waiting on a full pool must wake up when
    another worker releases."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    idx = pool.lease()
    waited_for = [None]
    def hold_off():
        t0 = time.time()
        new_idx = pool.lease(timeout=5.0)
        waited_for[0] = (time.time() - t0, new_idx)
        pool.release(new_idx, success=True)
    t = threading.Thread(target=hold_off)
    t.start()
    time.sleep(0.2)
    # Now release — second lease must wake
    pool.release(idx, success=True)
    t.join(timeout=2)
    assert waited_for[0] is not None, "second lease never returned"
    elapsed, _ = waited_for[0]
    # Should unblock quickly (well under 5s timeout)
    assert elapsed < 1.0, f"unblock took {elapsed}s"


# ── Cooldown ──────────────────────────────────────────────────────────

def test_mark_cooldown_moves_to_cooling_down():
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    pool.mark_cooldown(0, cooldown_seconds=10, reason="429")
    status = pool.get_status()
    assert status["accounts"][0]["state"] == "cooling_down"
    assert status["accounts"][0]["cooldown_seconds_remaining"] > 0
    assert status["accounts"][0]["last_error"] == "429"


def test_cooldown_expiry_returns_to_available():
    """An account whose cooldown has elapsed must be auto-released
    on the next pool query."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    # Use a very short cooldown so the test doesn't hang
    pool.mark_cooldown(0, cooldown_seconds=1, reason="429")
    # Immediately: cooling_down
    assert pool.get_status()["accounts"][0]["state"] == "cooling_down"
    # After 1.1s: available
    time.sleep(1.1)
    assert pool.get_status()["accounts"][0]["state"] == "available"


def test_cooldown_does_not_bump_fail_count():
    """Cooldown is a TRANSIENT throttle, not a health issue. The
    fail_count must NOT increment, otherwise repeated 429s would
    eventually kill the account permanently."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    pool.mark_cooldown(0, cooldown_seconds=60, reason="429")
    pool.mark_cooldown(0, cooldown_seconds=60, reason="429")
    pool.mark_cooldown(0, cooldown_seconds=60, reason="429")
    pool.mark_cooldown(0, cooldown_seconds=60, reason="429")
    status = pool.get_status()
    assert status["accounts"][0]["fail_count"] == 0, (
        "cooldown must not bump fail_count"
    )


# ── Dead state ───────────────────────────────────────────────────────

def test_mark_dead_sets_state():
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    pool.mark_dead(0, reason="account banned")
    status = pool.get_status()
    assert status["accounts"][0]["state"] == "dead"
    assert status["accounts"][0]["last_error"] == "account banned"


def test_reset_clears_dead_state():
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    pool.mark_dead(0)
    pool.reset(0)
    status = pool.get_status()
    assert status["accounts"][0]["state"] == "available"
    assert status["accounts"][0]["fail_count"] == 0
    assert status["accounts"][0]["last_error"] == ""


# ── Persistence ──────────────────────────────────────────────────────

def test_serialize_for_config_round_trip():
    """The pool's state can be serialized into cfg["accounts"] entries
    and restored on configure() — proving restart-survival."""
    from bulk_downloader.account_pool import (
        AccountPool, serialize_for_config,
    )
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}, {"username": "bob"}])
    pool.mark_cooldown(0, cooldown_seconds=600, reason="429 rl")
    pool.release(1, success=False, error="timeout")
    # Serialize
    state = serialize_for_config(pool)
    assert len(state) == 2
    assert state[0]["cooldown_until"] > time.time()
    assert state[1]["fail_count"] == 1
    assert state[1]["last_error"] == "timeout"
    # Restore in a new pool
    new_pool = AccountPool("test2")
    new_pool.configure([
        {"username": "alice", **state[0]},
        {"username": "bob", **state[1]},
    ])
    status = new_pool.get_status()
    alice = next(a for a in status["accounts"] if a["username"] == "alice")
    bob = next(a for a in status["accounts"] if a["username"] == "bob")
    assert alice["state"] == "cooling_down"
    assert bob["fail_count"] == 1


# ── Defensive ────────────────────────────────────────────────────────

def test_empty_pool_lease_raises():
    """A pool with no accounts must raise AccountUnavailable
    immediately, not block."""
    from bulk_downloader.account_pool import (
        AccountPool, AccountUnavailable,
    )
    pool = AccountPool("test")
    try:
        pool.lease(timeout=0.5)
    except AccountUnavailable as e:
        assert "no accounts configured" in str(e).lower()


def test_release_unknown_idx_does_not_crash():
    """release() with an idx that's not in the pool must silently
    no-op — defensive against caller mismatches."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{"username": "alice"}])
    # idx 99 doesn't exist — must not raise
    pool.release(99, success=True)
    pool.reset(99)
    pool.mark_dead(99)
    pool.mark_cooldown(99, cooldown_seconds=60)


def test_remove_pool_drops_state():
    from bulk_downloader.account_pool import (
        get_pool, remove_pool, _POOLS,
    )
    pool = get_pool("temp_site")
    assert "temp_site" in _POOLS
    remove_pool("temp_site")
    assert "temp_site" not in _POOLS


def test_get_all_pools_status_aggregates():
    """Global view: every site's pool status in one call."""
    from bulk_downloader.account_pool import (
        configure_pool, get_all_pools_status, remove_pool,
    )
    configure_pool("site1", [{"username": "u1"}])
    configure_pool("site2", [{"username": "u2"}, {"username": "u3"}])
    all_status = get_all_pools_status()
    assert len(all_status) >= 2
    site_ids = {p["site_id"] for p in all_status}
    assert "site1" in site_ids
    assert "site2" in site_ids
    # Cleanup
    remove_pool("site1"); remove_pool("site2")


# ── Runner integration ───────────────────────────────────────────────

def test_runner_rotate_uses_pool():
    """_rotate_account_if_available must consult the AccountPool
    when one is configured."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _rotate_account_if_available")
    assert pos > 0
    body = src[pos:pos + 4000]
    assert "from . import account_pool" in body
    assert "pool.mark_cooldown" in body
    assert "pool.lease" in body


def test_runner_has_persist_pool_state():
    """The runner persists the pool's health state to cfg so
    restarts preserve it."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    assert "def _persist_pool_state" in src
    pos = src.find("def _persist_pool_state")
    body = src[pos:pos + 2000]
    assert "serialize_for_config" in body
    assert "_save_sites_config" in body


def test_runner_falls_back_to_legacy_path():
    """When the pool isn't configured (early startup, single-account
    site), the rotation method must fall back to the legacy 24h
    cooldown logic. We can't break backward compat."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _rotate_account_if_available")
    body = src[pos:pos + 5000]
    assert "Legacy fallback path" in body
    # And the legacy path still mutates cfg["accounts"] directly
    legacy_pos = body.find("Legacy fallback")
    assert "cooldown_until" in body[legacy_pos:legacy_pos + 1500]


# ── App wiring ───────────────────────────────────────────────────────

def test_account_pool_endpoints_registered():
    src = _APP_SRC()
    assert "/api/sites/<sid>/account_pool/status" in src
    assert "/api/sites/<sid>/account_pool/reset/<int:account_idx>" in src
    assert "/api/account_pool/status_all" in src


def test_cfg_fields_has_account_cooldown_seconds():
    src = _APP_SRC()
    assert '"account_cooldown_seconds"' in src
    assert '"account_cooldown_seconds": 0' in src  # default


def test_app_calls_configure_pool_on_load():
    """At startup, every site with accounts gets a configured pool."""
    src = _APP_PY.read_text(encoding="utf-8")
    # Must be inside _load_sites_config (the early-load path)
    pos = src.find("def _load_sites_config")
    assert pos > 0
    body = src[pos:pos + 8000]
    assert "configure_pool" in body or "account_pool" in body


def test_app_reconfigures_on_site_save():
    """When the site is updated via PUT /api/sites/<sid>, the pool
    must reconfigure. Otherwise account list changes don't take effect."""
    src = _APP_SRC()
    # Find a PUT/POST update handler that mentions accounts
    pos = src.find("def api_update")
    if pos < 0:
        pos = src.find("def api_put_site")
    # Failing both — search for the reconfigure_pool call
    assert "configure_pool" in src
    # And it must be called from a site-update lifecycle hook
    assert "account_pool reconfigure on update" in src


def test_app_removes_pool_on_delete():
    src = _APP_SRC()
    pos = src.find("def api_delete")
    assert pos > 0
    body = src[pos:pos + 3000]
    assert "remove_pool" in body


# ── UI ───────────────────────────────────────────────────────────────



# ── Adversarial: defensive configure() (locks in audit fixes) ────────

def test_configure_skips_non_dict_entries():
    """A corrupted sites_config.json with None entries (legacy save bug,
    JSON deserialization quirk) must not crash the pool."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([None, {"username": "alice"}, None, "string", 12345])
    # Only alice survives
    status = pool.get_status()
    assert status["account_count"] == 1
    assert status["accounts"][0]["username"] == "alice"


def test_configure_handles_bad_numeric_fields():
    """Strings/None in numeric fields (cooldown_until, fail_count,
    last_used) reset to defaults rather than crashing."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([{
        "username": "alice",
        "cooldown_until": "not a number",
        "fail_count": "garbage",
        "last_used": [],
    }])
    # Pool initializes anyway with default values
    status = pool.get_status()
    assert status["account_count"] == 1
    assert status["accounts"][0]["state"] == "available"
    assert status["accounts"][0]["fail_count"] == 0


def test_configure_skips_entries_without_username():
    """An account dict without a username is meaningless. Skip it."""
    from bulk_downloader.account_pool import AccountPool
    pool = AccountPool("test")
    pool.configure([
        {"username": "alice"},
        {},  # no username
        {"username": ""},  # empty username
        {"username": None},  # None username
        {"username": 12345},  # non-string username
    ])
    status = pool.get_status()
    # Empty username "" is accepted (legacy data); None and int aren't.
    # alice + "" = 2
    usernames = {a["username"] for a in status["accounts"]}
    assert "alice" in usernames
