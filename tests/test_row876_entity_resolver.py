"""Cut 876: entity resolver contract tests."""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import time
import pytest


def test_fuzzy_alias_mapping_unifies_divergent_spellings():
    """Verify that fuzzy alias mapping correctly unifies divergent spellings and minor typos."""
    from bulk_downloader import entity_resolver

    resolver = entity_resolver.EntityResolver()
    resolver.register_entity("Studio Trigger", aliases=["Trigger", "Trigger Inc"])
    resolver.register_entity("Kyoto Animation", aliases=["KyoAni", "KyotoAni"])

    test_cases = [
        ("studio trigger", "Studio Trigger"),
        ("Studio_Trigger", "Studio Trigger"),
        ("Studio-Trigger", "Studio Trigger"),
        ("Studo Trigger", "Studio Trigger"),  # Levenshtein distance 1
        ("Trigger", "Studio Trigger"),
        ("KyoAni", "Kyoto Animation"),
        ("kyoto animation", "Kyoto Animation"),
        ("Kyoto_Animation", "Kyoto Animation"),
    ]

    for variant, expected in test_cases:
        resolved = resolver.resolve(variant)
        assert resolved == expected, f"Failed resolving '{variant}': got '{resolved}', expected '{expected}'"


def test_deterministic_output_across_dataset():
    """Verify that entity resolution produces 100% deterministic output across test runs."""
    from bulk_downloader import entity_resolver

    resolver = entity_resolver.EntityResolver()
    resolver.register_entity("Studio Ghibli")
    resolver.register_entity("Madhouse")
    resolver.register_entity("Bones")

    dataset = [
        "studio ghibli", "Studio_Ghibli", "ghibli studio",
        "madhouse", "Mad House", "MadHouse Inc",
        "bones", "Studio Bones", "Bones studio",
    ]

    run1 = [resolver.resolve(item) for item in dataset]
    run2 = [resolver.resolve(item) for item in dataset]
    run3 = [resolver.resolve(item) for item in dataset]

    assert run1 == run2 == run3, "Entity resolution output is non-deterministic!"


def test_in_memory_cache_bounds_lookup_latency_under_1ms():
    """Verify that in-memory cache bounds repeated lookup latency to strictly < 1.0 ms."""
    from bulk_downloader import entity_resolver

    resolver = entity_resolver.EntityResolver()
    resolver.register_entity("Production I.G")

    # Prime cache
    assert resolver.resolve("production i.g") == "Production I.G"

    # Measure 1000 lookups
    iterations = 1000
    start = time.perf_counter()
    for _ in range(iterations):
        resolver.resolve("production i.g")
    total_s = time.perf_counter() - start

    avg_ms = (total_s / iterations) * 1000.0
    assert avg_ms < 1.0, f"Average cached lookup latency {avg_ms:.4f} ms exceeds 1.0 ms threshold"


# ---- fixer (O928) controls for the correctness REFUTE P2/P2 ---------------

def test_nearest_entity_outranks_bare_substring():
    """P2: Ann and Joanne registered; 'Joanna' is distance 1 from Joanne and must
    not resolve to Ann via the 'ann' substring."""
    from bulk_downloader.entity_resolver import EntityResolver
    r = EntityResolver(max_edit_distance=2)
    r.register_entity("Ann")
    r.register_entity("Joanne")
    assert r.resolve("Joanna") == "Joanne"
    assert r.resolve("Joanne") == "Joanne"
    assert r.resolve("Ann") == "Ann"
    # whole-token containment still resolves (boundary-aware)
    r.register_entity("Madhouse")
    r.register_entity("Bones")
    assert r.resolve("MadHouse Inc") == "Madhouse"
    assert r.resolve("Studio Bones") == "Bones"
    # an unrelated name stays unresolved
    assert r.resolve("Zebra Productions") == "Zebra Productions"


def test_empty_normalized_names_never_resolve_or_register():
    """P2: whitespace/punctuation-only queries stay unresolved; an alias that
    normalizes to empty is ignored (never a wildcard); an empty canonical is refused."""
    import pytest
    from bulk_downloader.entity_resolver import EntityResolver
    r = EntityResolver()
    r.register_entity("Ann")
    for q in ("   ", "...", "-_-", "\t"):
        assert r.resolve(q) == q
    r.register_entity("Studio A", aliases=["   ", "-", "Studio Alpha"])
    assert r.resolve("Completely unrelated name") == "Completely unrelated name"
    assert r.resolve("studio alpha") == "Studio A"
    with pytest.raises(ValueError):
        r.register_entity("   ")
    with pytest.raises(ValueError):
        r.register_entity("...")
