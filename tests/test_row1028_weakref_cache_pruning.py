"""Row 1028: Weakref Callback Lifecycle Manager and Unbounded Cache Pruning Engine (WeakrefLifecycle).

Provides safe weak reference lifecycle tracking, callback execution on garbage collection,
deterministic bounded cache pruning (FIFO, size limit, TTL with fail-closed validation),
and self-evicting WeakValueCache for user-defined object instances.

RED on baseline: bulk_downloader lacks WeakrefCallbackManager and CachePruningEngine.
"""
from __future__ import annotations

import gc
import pytest
import time

try:
    from bulk_downloader.weakref_lifecycle import (
        POLICY_NOT_EVALUABLE,
        CachePruningEngine,
        WeakValueCache,
        WeakrefCallbackManager,
        get_cache_pruning_engine,
        get_weakref_lifecycle_manager,
    )
except ImportError:
    POLICY_NOT_EVALUABLE = None
    CachePruningEngine = None
    WeakValueCache = None
    WeakrefCallbackManager = None
    get_cache_pruning_engine = None
    get_weakref_lifecycle_manager = None

BD_GATE_SCOPE = "repo-wide"


def test_base_positive_control_filename_normalization():
    """Positive control on pre-existing baseline behavior (Fleet Rule 7 / F7)."""
    from bulk_downloader import aiassist

    meta = aiassist.normalize_filename("Cosmos.Laundromat.2015.1080p.mkv")
    assert meta["ok"] is True
    assert meta["title"] == "Cosmos Laundromat"


def test_weakref_lifecycle_contract():
    """Verify weakref_lifecycle exposes WeakrefCallbackManager and CachePruningEngine."""
    assert WeakrefCallbackManager is not None, "Row 1028 capability missing: WeakrefCallbackManager"
    assert CachePruningEngine is not None, "Row 1028 capability missing: CachePruningEngine"


def test_weakref_callback_lifecycle():
    """Verify WeakrefCallbackManager triggers callbacks safely upon object destruction."""
    assert WeakrefCallbackManager is not None, "Row 1028 capability missing: WeakrefCallbackManager"

    manager = WeakrefCallbackManager()
    callback_called = []

    class Target:
        pass

    target = Target()

    def on_cleanup(ref):
        callback_called.append(True)

    ref = manager.register(target, on_cleanup)
    assert ref() is target
    assert manager.active_ref_count() == 1
    assert not callback_called

    del target
    gc.collect()

    assert len(callback_called) == 1
    assert manager.active_ref_count() == 0


def test_callback_manager_metrics_accuracy():
    """Verify callback metrics distinguish finalizations from successful callbacks (F5 fix / kills M4)."""
    assert WeakrefCallbackManager is not None, "Row 1028 capability missing: WeakrefCallbackManager"

    manager = WeakrefCallbackManager()

    class Target:
        pass

    # 1. Target with NO callback
    t1 = Target()
    manager.register(t1, callback=None)
    del t1
    gc.collect()

    m1 = manager.get_metrics()
    assert m1["total_finalizations"] == 1
    assert m1["total_callbacks_invoked"] == 0
    assert m1["callback_exceptions"] == 0

    # 2. Target whose callback raises
    t2 = Target()

    def bad_callback(ref):
        raise RuntimeError("Callback failure simulation")

    manager.register(t2, callback=bad_callback)
    del t2
    gc.collect()

    m2 = manager.get_metrics()
    assert m2["total_finalizations"] == 2
    assert m2["total_callbacks_invoked"] == 0
    assert m2["callback_exceptions"] == 1

    # 3. Target with successful callback
    t3 = Target()
    called = []
    manager.register(t3, callback=lambda ref: called.append(1))
    del t3
    gc.collect()

    m3 = manager.get_metrics()
    assert m3["total_finalizations"] == 3
    assert m3["total_callbacks_invoked"] == 1
    assert m3["callback_exceptions"] == 1


def test_fifo_eviction_order():
    """Verify size pruning enforces FIFO order: oldest key evicted, newest key survives (F6 fix / kills M2)."""
    assert CachePruningEngine is not None, "Row 1028 capability missing: CachePruningEngine"

    engine = CachePruningEngine()
    cache: dict[str, str] = {}
    cache["k_oldest"] = "val1"
    cache["k_middle"] = "val2"
    cache["k_newest"] = "val3"

    engine.register_cache("fifo_cache", cache, max_size=2)
    evicted = engine.prune_cache("fifo_cache")

    assert evicted == 1
    assert "k_oldest" not in cache, "FIFO violation: oldest inserted key was not evicted first"
    assert "k_newest" in cache, "FIFO violation: newest inserted key was evicted prematurely"
    assert "k_middle" in cache


def test_cache_pruning_engine_ttl_fail_closed():
    """Verify TTL pruning refuses or flags unevaluable cache entries (F2 fix)."""
    assert CachePruningEngine is not None, "Row 1028 capability missing: CachePruningEngine"

    engine = CachePruningEngine()
    # 1. Non-numeric values at registration time raise ValueError upfront
    cache_with_items = {"item1": {"payload": "no timestamp"}}
    with pytest.raises(ValueError, match="requires timestamp_getter"):
        engine.register_cache("invalid_ttl", cache_with_items, ttl_seconds=0.001)

    # 2. Items inserted after empty registration return POLICY_NOT_EVALUABLE (-1) on prune
    empty_cache: dict[str, dict] = {}
    engine.register_cache("untyped_ttl", empty_cache, ttl_seconds=0.001)
    empty_cache["late_item"] = {"payload": "no timestamp"}

    res = engine.prune_cache("untyped_ttl")
    assert res == POLICY_NOT_EVALUABLE or res == -1
    metrics = engine.get_metrics()
    assert metrics["policy_evaluation_errors"] >= 1
    assert len(empty_cache) == 1


def test_weak_value_cache_primitive_rejection():
    """Verify WeakValueCache rejects non-weakref primitive types with typed TypeError (F3 fix)."""
    assert WeakValueCache is not None, "Row 1028 capability missing: WeakValueCache"

    cache = WeakValueCache()

    for primitive in [{}, "text", 42, [], (1, 2)]:
        with pytest.raises(TypeError, match="WeakValueCache requires a weakly-referenceable object"):
            cache["key"] = primitive


def test_weak_value_cache_dead_entry_cleanup_on_contains():
    """Verify __contains__ and get() clean up dead keys when referenced value is collected (kills M8)."""
    assert WeakValueCache is not None, "Row 1028 capability missing: WeakValueCache"

    cache = WeakValueCache()

    class Item:
        def __init__(self, val: int):
            self.val = val

    item = Item(42)
    cache["alive_key"] = item
    assert "alive_key" in cache
    assert cache.get("alive_key").val == 42

    del item
    gc.collect()

    assert "alive_key" not in cache
    assert cache.get("alive_key") is None
    assert "alive_key" not in cache._data


def test_aiassist_prune_override_preserves_policy_and_avoids_sawtooth():
    """Verify prune_metadata_cache(max_size=N) does not permanently overwrite default bound (F1 fix)."""
    from bulk_downloader import aiassist

    assert hasattr(aiassist, "prune_metadata_cache"), (
        "Baseline aiassist lacks prune_metadata_cache and WeakrefLifecycle cache pruning"
    )

    aiassist._FILENAME_META_CACHE.clear()

    # Fill cache up to 50 entries
    for i in range(50):
        aiassist.normalize_filename(f"Test.Movie.F1.{i}.2024.1080p.mkv")

    assert len(aiassist._FILENAME_META_CACHE) == 50

    # Diagnostic call with one-off max_size override
    evicted = aiassist.prune_metadata_cache(max_size=20)
    assert evicted == 30
    assert len(aiassist._FILENAME_META_CACHE) == 20

    # Crucial F1 check: registered policy MUST still be 500, not permanently overwritten to 20!
    engine = aiassist.get_cache_pruning_engine()
    registered_bound = engine.get_cache_max_size("_FILENAME_META_CACHE")
    assert registered_bound == 500, f"Registered bound was corrupted to {registered_bound}!"

    # Subsequent stores between 21 and 50 must NOT shed entries
    for i in range(100, 120):
        aiassist.normalize_filename(f"Test.Movie.F1.Next.{i}.2024.1080p.mkv")

    assert len(aiassist._FILENAME_META_CACHE) == 40  # 20 surviving + 20 added


def test_runner_weakref_integration_through_caller():
    """Verify SiteRunner registers with WeakValueCache and WeakrefCallbackManager (F4 fix)."""
    from bulk_downloader.runner import SiteRunner, get_active_runner
    from bulk_downloader.weakref_lifecycle import get_weakref_lifecycle_manager

    mgr = get_weakref_lifecycle_manager()
    initial_refs = mgr.active_ref_count()

    runner = SiteRunner("test_site_row1028", {"download_dir": "/tmp/bd_test"})
    assert get_active_runner("test_site_row1028") is runner
    assert mgr.active_ref_count() > initial_refs

    runner.stop()
    # stop() signals the auto-retry scanner without joining it, and the scanner's frame
    # holds the runner; row 1021's subsystems make the runner a reference cycle, so a
    # scanner that exits after the gc.collect() below leaves it for the NEXT collection.
    # Retire the scanner the way site delete does (a quiescence verdict) first.
    assert runner.retire_auto_retry(timeout=10.0) is True, (
        "auto-retry scanner not proven stopped; its thread still references the runner")
    del runner
    gc.collect()

    assert get_active_runner("test_site_row1028") is None


# ── r2 (ORDERS-2323; refutes N6-A + P1-A) ──────────────────────────────────


def test_e1_normalize_filename_holds_cache_at_500_and_evicts_oldest():
    """E1: the wired store path bounds _FILENAME_META_CACHE; 520 distinct names leave 500, oldest gone."""
    from bulk_downloader import aiassist

    saved = dict(aiassist._FILENAME_META_CACHE)
    aiassist._FILENAME_META_CACHE.clear()
    try:
        names = [f"Movie.Number.{i:04d}.2015.1080p.mkv" for i in range(520)]
        for n in names:
            assert aiassist.normalize_filename(n)["ok"] is True
        keys = aiassist._FILENAME_META_CACHE
        assert len(keys) == 500, f"E1: cache holds {len(keys)} after 520 names, expected exactly 500"
        assert aiassist._filename_cache_key(names[0]) not in keys, "E1: oldest name not evicted"
        assert aiassist._filename_cache_key(names[19]) not in keys, "E1: 20th-oldest name not evicted"
        assert aiassist._filename_cache_key(names[20]) in keys, "E1: 21st name evicted early"
        assert aiassist._filename_cache_key(names[-1]) in keys, "E1: newest name evicted"
    finally:
        aiassist._FILENAME_META_CACHE.clear()
        aiassist._FILENAME_META_CACHE.update(saved)


def test_e1_control_normalize_filename_stores_each_distinct_name():
    """Positive control: below the bound every distinct name is stored under its key."""
    from bulk_downloader import aiassist

    saved = dict(aiassist._FILENAME_META_CACHE)
    aiassist._FILENAME_META_CACHE.clear()
    try:
        names = [f"Movie.Number.{i:04d}.2015.1080p.mkv" for i in range(30)]
        for n in names:
            aiassist.normalize_filename(n)
        assert len(aiassist._FILENAME_META_CACHE) == 30
        assert all(aiassist._filename_cache_key(n) in aiassist._FILENAME_META_CACHE for n in names)
    finally:
        aiassist._FILENAME_META_CACHE.clear()
        aiassist._FILENAME_META_CACHE.update(saved)


def test_e2_unevaluable_ttl_still_enforces_max_size():
    """E2: an unevaluable TTL does not switch the size bound off (registered or override)."""
    engine = CachePruningEngine()
    cache = {}
    engine.register_cache("mixed", cache, max_size=1, ttl_seconds=60)
    cache.update({"a": "x", "b": "y", "c": "z"})
    assert engine.prune_cache("mixed") == POLICY_NOT_EVALUABLE
    assert list(cache) == ["c"], f"E2: size bound skipped under unevaluable TTL, cache={cache}"

    big = {}
    engine.register_cache("ovr", big, ttl_seconds=60)
    big.update({f"k{i}": "s" for i in range(10)})
    assert engine.prune_cache("ovr", max_size_override=2) == POLICY_NOT_EVALUABLE
    assert list(big) == ["k8", "k9"], f"E2: override skipped under unevaluable TTL, cache={big}"
    assert engine.get_metrics()["total_items_evicted"] == 10


def test_e3_numeric_ttl_evicts_expired_and_keeps_fresh():
    """E3: the TTL positive path -- an entry older than ttl is evicted, a fresh one stays."""
    engine = CachePruningEngine()
    now = time.time()
    cache = {"old": now - 100.0, "fresh": now}
    engine.register_cache("ttl", cache, ttl_seconds=10)
    assert engine.prune_cache("ttl") == 1
    assert list(cache) == ["fresh"], f"E3: TTL evicted the wrong entries, cache={cache}"
    assert engine.get_metrics()["policy_evaluation_errors"] == 0


def test_e3_every_unevaluable_entry_is_counted_even_when_others_expire():
    """E3: one rule -- each unevaluable entry is counted, whether or not another entry expired."""
    now = time.time()
    for label, first in (("expired", now - 100.0), ("fresh", now)):
        engine = CachePruningEngine()
        cache = {}
        engine.register_cache("m", cache, ttl_seconds=10)
        cache.update({"ts": first, "s1": "a", "s2": "b"})
        assert engine.prune_cache("m") == POLICY_NOT_EVALUABLE, label
        assert engine.get_metrics()["policy_evaluation_errors"] == 2, label
        assert ("ts" in cache) is (label == "fresh"), label
        assert {"s1", "s2"} <= set(cache), f"{label}: unevaluable entries must be kept"


def test_e3_register_rejects_non_numeric_value_after_a_numeric_first():
    """E3: register_cache checks every value, not only the first."""
    engine = CachePruningEngine()
    with pytest.raises(ValueError, match="requires timestamp_getter"):
        engine.register_cache("m", {"a": 1.0, "b": "str"}, ttl_seconds=10)
    engine.register_cache("ok", {"a": 1.0, "b": 2}, ttl_seconds=10)
    assert engine.get_cache_max_size("ok", default=7) == 7
