"""Contracts for bounded, cycle-safe crawl frontier admission."""

BD_GATE_SCOPE = "module"

import pytest

from bulk_downloader.crawl_sentinel import CrawlSentinel


def test_repeated_path_segment_is_rejected_as_a_cycle():
    sentinel = CrawlSentinel(max_depth=8, max_segment_repeats=2)

    assert sentinel.admit("https://example.test/library/scene/42") is True
    assert sentinel.admit("https://example.test/library/scene/library/scene") is False


def test_prune_queue_removes_cyclic_and_duplicate_branches():
    sentinel = CrawlSentinel(max_depth=6, max_segment_repeats=2)
    urls = [
        "https://example.test/library/scene/42",
        "https://example.test/library/scene/42",
        "https://example.test/a/b/a/b",
        "https://example.test/library/scene/43",
    ]

    assert sentinel.prune_queue(urls) == [
        "https://example.test/library/scene/42",
        "https://example.test/library/scene/43",
    ]


def test_paths_deeper_than_limit_are_rejected():
    sentinel = CrawlSentinel(max_depth=3)

    assert sentinel.admit("https://example.test/one/two/three") is True
    assert sentinel.admit("https://example.test/one/two/three/four") is False


# ---- fixer (O928) controls: correctness REFUTE E1 / E3 ----

def test_dot_segments_are_normalised_before_dedup_and_depth():
    """E3: /library/scene/../scene/42 IS /library/scene/42 -- the second
    spelling is a duplicate, not a new frontier entry; '..' never buys depth."""
    from bulk_downloader.crawl_sentinel import _canonical_path
    s = CrawlSentinel(max_depth=3)
    assert s.admit("https://example.test/library/scene/42") is True
    assert s.admit("https://example.test/library/scene/../scene/42") is False
    assert s.admit("https://example.test/library//scene/./42") is False
    assert s.admit("https://example.test/./library/scene/42/") is True   # trailing slash: distinct resource
    canonical, parts = _canonical_path("https://Example.test/a/b/../../c/d/../e")
    assert canonical == "https://example.test/c/e" and parts == ("c", "e")
    # '..' cannot fake a shallow path: the resolved depth is what is bounded
    assert CrawlSentinel(max_depth=2).admit("https://example.test/a/b/c/../../x/y/z") is False


def test_path_entropy_prunes_deep_low_entropy_generated_paths():
    """E1 (register SCOPE: path-entropy detection): a deep path built from
    few distinct segments is a generated cycle even when no single segment
    exceeds max_segment_repeats and no adjacent block repeats."""
    from bulk_downloader.crawl_sentinel import path_entropy, path_entropy_ratio
    assert path_entropy(()) == 0.0
    assert path_entropy(("a", "a", "a")) == 0.0 and path_entropy_ratio(("a", "a", "a")) == 0.0
    assert abs(path_entropy(("a", "b", "c", "d")) - 2.0) < 1e-9
    assert path_entropy_ratio(("a", "b", "c", "d")) == 1.0 and path_entropy_ratio(("a",)) == 1.0
    s = CrawlSentinel(max_depth=12, max_segment_repeats=3)
    # a/b/c/a/c/b/a: no segment > 3 times, no adjacent repeated block, but
    # 7 deep from 3 distinct segments -> entropy ratio 0.55 < 0.6
    assert abs(path_entropy_ratio(("a", "b", "c", "a", "c", "b", "a")) - 0.555) < 0.01
    assert s.admit("https://example.test/a/b/c/a/c/b/a") is False
    # genuine deep paths are admitted (all distinct; one repeated segment)
    assert s.admit("https://example.test/library/2024/june/scenes/studio/42") is True
    assert s.admit("https://example.test/a/b/c/d/a") is True          # ratio 0.83
    # short low-entropy paths are legitimate (below entropy_min_depth)
    assert s.admit("https://example.test/a/b/a") is True
    # the bound is configurable and 0 disables it
    assert CrawlSentinel(max_depth=12, max_segment_repeats=3, min_path_entropy=0).admit(
        "https://other.test/a/b/c/a/c/b/a") is True
    with pytest.raises(ValueError):
        CrawlSentinel(min_path_entropy=1.5)
