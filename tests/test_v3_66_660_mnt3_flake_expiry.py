"""v3.66.660 -- MNT-3: flake-registry expiry (the "expiring" half).

The flake registry (run_tests._{load,update,save}_flake_registry + _chronic_flakes)
tracked flakes with count/first_seen/last_seen and surfaced chronic ones, but nothing
EXPIRED stale entries -- a test that stopped flaking stayed in the registry forever.
The MNT-3 spec requires the quarantine to be "visible + expiring, never silent". This
adds _prune_flake_registry(registry, now, max_age_days): entries whose last_seen is
older than the TTL are dropped, so the registry self-cleans and a long-quiet test
ages out. Wired into the update flow (load -> update -> PRUNE -> save). MNT-1 (dead-
route gate) was already enforced by test_nav_reachability.
"""
import importlib

rt = importlib.import_module("run_tests")


def test_prune_drops_stale_entries():
    now = 1_000_000.0
    day = 86400.0
    reg = {
        "fresh :: t": {"count": 2, "first_seen": now - day, "last_seen": now - day},
        "stale :: t": {"count": 9, "first_seen": now - 100 * day, "last_seen": now - 40 * day},
    }
    pruned = rt._prune_flake_registry(reg, now, max_age_days=30)
    assert "fresh :: t" in pruned
    assert "stale :: t" not in pruned, "an entry unseen for > TTL must age out"


def test_prune_keeps_boundary_entry():
    now = 1_000_000.0
    day = 86400.0
    reg = {"edge :: t": {"count": 1, "first_seen": now, "last_seen": now - 29 * day}}
    assert "edge :: t" in rt._prune_flake_registry(reg, now, max_age_days=30)


def test_prune_never_mutates_input():
    now = 1_000_000.0
    reg = {"a :: t": {"count": 1, "first_seen": now, "last_seen": now - 999 * 86400.0}}
    before = dict(reg)
    rt._prune_flake_registry(reg, now, max_age_days=30)
    assert reg == before, "prune must return a new registry, never mutate the input"


def test_prune_empty_and_malformed_safe():
    now = 1_000_000.0
    assert rt._prune_flake_registry({}, now, max_age_days=30) == {}
    # a malformed entry (no last_seen) must not crash; treated as fresh (kept)
    reg = {"x :: t": {"count": 1}}
    out = rt._prune_flake_registry(reg, now, max_age_days=30)
    assert "x :: t" in out


def test_prune_disabled_when_ttl_nonpositive():
    now = 1_000_000.0
    reg = {"old :: t": {"count": 1, "first_seen": now, "last_seen": now - 999 * 86400.0}}
    assert rt._prune_flake_registry(reg, now, max_age_days=0) == reg
    assert rt._prune_flake_registry(reg, now, max_age_days=-5) == reg
