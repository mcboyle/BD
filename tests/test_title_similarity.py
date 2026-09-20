"""RED-first tests for Row 909: textual-similarity indexing and duplicate
record reconciliation.

``bulk_downloader.dedup.TitleSimilarityIndex`` builds an in-memory trigram
(character 3-gram) index over catalog titles so a prospective queue item's
title can be checked against the whole catalog for a likely duplicate
(>90% Jaccard similarity over trigram sets) without a full-catalog scan --
an inverted postings index (trigram -> candidate ids) means a query only
ever compares against titles sharing at least one trigram.

Three load-bearing behaviors (Row 909 acceptance):
  1. Duplicate detection across minor title variations (retitled re-uploads,
     punctuation/whitespace drift, brand prefixes).
  2. Avoidance of false matches on episodic content: "Show Episode 1" and
     "Show Episode 2" differ by a single character and score very high on
     trigram similarity alone, but are NOT duplicates -- the index must not
     flag them.
  3. Query latency stays low (bounded) even against a sizeable catalog,
     because the postings index prunes to only trigram-sharing candidates.

Note: the brief's OWNS line named this file ``tests/test_row909.py``; the
register row's ACCEPTANCE line names ``tests/test_title_similarity.py``.
The register's acceptance path is canonical (fixer, correctness item 3).
"""
from __future__ import annotations

import time

import pytest

from bulk_downloader.dedup import TitleSimilarityIndex

BD_GATE_SCOPE = "module"


# ── 1. Duplicate detection across minor title variations ───────────────

def test_minor_title_variation_is_flagged_as_duplicate():
    idx = TitleSimilarityIndex()
    idx.add("cat-1", "Reptyle Studios - Summer Nights (2024)")
    matches = idx.find_duplicates("Reptyle Studios: Summer Nights 2024", threshold=0.9)
    ids = [m.catalog_id for m in matches]
    assert "cat-1" in ids


def test_unrelated_title_is_not_flagged():
    idx = TitleSimilarityIndex()
    idx.add("cat-1", "Reptyle Studios - Summer Nights (2024)")
    matches = idx.find_duplicates("Completely Different Content Entirely", threshold=0.9)
    assert matches == []


def test_scores_are_sorted_best_match_first():
    idx = TitleSimilarityIndex()
    idx.add("close", "Wowgirls HLS Manifest Ladder Test")
    idx.add("closer", "Wowgirls HLS Manifest Ladder Tests")
    matches = idx.find_duplicates("Wowgirls HLS Manifest Ladder Tests", threshold=0.5)
    assert len(matches) == 2
    assert matches[0].catalog_id == "closer"
    assert matches[0].score >= matches[1].score


# ── 2. Avoidance of false matches on episodic content ───────────────────

def test_different_episode_numbers_are_not_duplicates():
    idx = TitleSimilarityIndex()
    idx.add("ep1", "Bros Dash Show Episode 1")
    matches = idx.find_duplicates("Bros Dash Show Episode 2", threshold=0.5)
    ids = [m.catalog_id for m in matches]
    assert "ep1" not in ids


def test_same_episode_number_still_matches():
    idx = TitleSimilarityIndex()
    idx.add("ep1", "Bros Dash Show Episode 1")
    matches = idx.find_duplicates("Bros Dash Show - Episode 1", threshold=0.9)
    ids = [m.catalog_id for m in matches]
    assert "ep1" in ids


def test_leading_zero_episode_numbers_are_treated_as_equal():
    idx = TitleSimilarityIndex()
    idx.add("s01e01", "Series Name S01E01")
    matches = idx.find_duplicates("Series Name S1E1", threshold=0.5)
    ids = [m.catalog_id for m in matches]
    assert "s01e01" in ids


def test_sequel_with_added_number_is_not_a_duplicate_of_the_original():
    idx = TitleSimilarityIndex()
    idx.add("orig", "The Great Adventure")
    matches = idx.find_duplicates("The Great Adventure 2", threshold=0.5)
    ids = [m.catalog_id for m in matches]
    assert "orig" not in ids


# ── 3. Query latency stays low against a sizeable catalog ───────────────

def test_query_latency_is_bounded_against_a_large_catalog():
    idx = TitleSimilarityIndex()
    for i in range(2000):
        idx.add(f"cat-{i}", f"Distinct Catalog Entry Number {i} Title Padding")
    idx.add("target", "Reptyle Studios - Summer Nights (2024)")

    iterations = 200
    start = time.perf_counter()
    for _ in range(iterations):
        idx.find_duplicates("Reptyle Studios: Summer Nights 2024", threshold=0.9)
    elapsed = time.perf_counter() - start
    per_query = elapsed / iterations
    # Sub-millisecond in the steady state; a generous 5ms/query CI-noise
    # ceiling still proves the postings index isn't doing a full catalog
    # scan (2000 unrelated entries share no trigrams with the query).
    assert per_query < 0.005, f"{per_query * 1000:.3f}ms/query too slow"


def test_empty_query_title_returns_no_matches():
    idx = TitleSimilarityIndex()
    idx.add("cat-1", "Some Title")
    assert idx.find_duplicates("", threshold=0.9) == []


def test_len_reports_catalog_size():
    idx = TitleSimilarityIndex()
    assert len(idx) == 0
    idx.add("a", "Title A")
    idx.add("b", "Title B")
    assert len(idx) == 2


# ── FIXER (O928) controls for VERDICT-correctness E1/E2/E3 ─────────────

import statistics
import string

from bulk_downloader.dedup import _episode_markers, _normalize_title


def test_e1_unrelated_unicode_titles_do_not_collapse_together():
    idx = TitleSimilarityIndex()
    idx.add("a", "宇宙探險")
    idx.add("b", "春天的故事")
    idx.add("c", "Приключения в космосе")
    assert [m.catalog_id for m in idx.find_duplicates("宇宙探險")] == ["a"]
    assert [m.catalog_id for m in idx.find_duplicates("春天的故事")] == ["b"]
    assert [m.catalog_id for m in idx.find_duplicates("Приключения в космосе!")] == ["c"]
    assert _normalize_title("宇宙探險") == "宇宙探險"


def test_e1_blank_after_normalization_never_matches_anything():
    idx = TitleSimilarityIndex()
    idx.add("blank", "   ")
    idx.add("punct", "...!!!")
    idx.add("real", "Some Title")
    for query in ("", "   ", "...", "-- --", "___"):
        assert idx.find_duplicates(query, threshold=0.0) == [], repr(query)
    assert len(idx) == 3
    # a blank catalog record is never a candidate even for a blank-ish score
    assert [m.catalog_id for m in idx.find_duplicates("Some Title")] == ["real"]


def test_e2_labeled_season_episode_identity_is_positional():
    show = "The Extremely Long Running Documentary Series About Everything "
    idx = TitleSimilarityIndex()
    idx.add("s1e2", show + "Season 1 Episode 2")
    assert idx.find_duplicates(show + "Episode 1 Season 2") == []
    assert idx.find_duplicates(show + "S02E01") == []
    # same identity, different spelling forms, still a duplicate
    for form in ("S01E02", "season 01 - episode 02", "Season 1, Ep 2", "s1 e2"):
        ids = [m.catalog_id for m in idx.find_duplicates(show + form, threshold=0.7)]
        assert ids == ["s1e2"], form
    assert _episode_markers(_normalize_title("Season 1 Episode 2")) == \
        _episode_markers(_normalize_title("S01E02"))


def test_e2_roman_episode_numerals_are_distinct_episodes():
    show = "The Extremely Long Running Documentary Series About Everything "
    idx = TitleSimilarityIndex()
    idx.add("iv", show + "Episode IV")
    assert idx.find_duplicates(show + "Episode VI") == []
    assert idx.find_duplicates(show + "Part IV") == []           # different label
    assert [m.catalog_id for m in idx.find_duplicates(show + "Episode 4")] == ["iv"]
    assert [m.catalog_id for m in idx.find_duplicates(show + "episode iv!")] == ["iv"]


def test_e2_bare_roman_looking_words_are_not_episode_numbers():
    # "I", "mix", "civic" contain only roman letters but are words, not numerals
    assert _episode_markers(_normalize_title("I Am Legend")) == ()
    assert _episode_markers(_normalize_title("Mix Civic Dim")) == ()
    idx = TitleSimilarityIndex()
    idx.add("a", "I Am Legend")
    assert [m.catalog_id for m in idx.find_duplicates("I am Legend!")] == ["a"]


def _common_prefix_catalog():
    idx = TitleSimilarityIndex()
    prefix = "The International Documentary Archive Presents a Natural History Feature "
    for n in range(2000):
        suffix = "".join(string.ascii_lowercase[(n // (26 ** p)) % 26] for p in (2, 1, 0))
        idx.add(n, prefix + suffix)
    return idx, prefix


def test_e3_candidate_pruning_is_threshold_aware_not_marker_based():
    """No episode markers anywhere (the verdict's E3 shape): pruning must
    come from the trigram bound, not from a marker mismatch shortcut."""
    idx, prefix = _common_prefix_catalog()
    query = prefix + "zzzzzzzzzz"
    # a realistic query (a distinct title) is pruned to nothing before scoring
    assert idx.candidate_count("Reptyle Studios - Summer Nights (2024)") == 0
    # the adversarial common-prefix query legitimately admits the catalog
    # (every entry shares >= 90% of its trigrams by construction) ...
    assert idx.candidate_count(query) == 2000
    # ... and still returns only the true >= 0.9 matches
    matches = idx.find_duplicates(query)
    assert 0 < len(matches) < 20
    assert all(m.score >= 0.9 for m in matches)
    # negative control on the bound itself: lower the threshold and the
    # prefix filter admits more of a partially-overlapping catalog
    idx2 = TitleSimilarityIndex()
    idx2.add("near", "Reptyle Studios - Summer Nights (2024)")
    idx2.add("far", "Summer Nights")
    assert idx2.candidate_count("Reptyle Studios: Summer Nights 2024", 0.9) == 1
    assert idx2.candidate_count("Reptyle Studios: Summer Nights 2024", 0.3) == 2


def test_e3_common_prefix_catalog_query_is_submillisecond_median():
    """Representative latency: 2000 near-identical titles, no markers,
    every entry a scoring candidate. Median of 15 queries on the author's
    host: ~0.75ms (list-comprehension bitmask popcount). 2ms ceiling for a
    loaded CI host; the pruning proof is the deterministic test above."""
    idx, prefix = _common_prefix_catalog()
    query = prefix + "zzzzzzzzzz"
    timings = []
    for _ in range(15):
        started = time.perf_counter()
        idx.find_duplicates(query)
        timings.append(time.perf_counter() - started)
    median = statistics.median(timings)
    assert median < 0.002, f"median {median * 1000:.3f}ms"


# ---- fixer (O928) control: correctness item 1 (re-add / update of a catalog_id) ----

def test_re_adding_a_catalog_id_replaces_its_title_without_corrupting_the_index():
    idx = TitleSimilarityIndex()
    idx.add("c1", "The Quick Brown Fox Documentary")
    idx.add("c2", "Completely Unrelated Cooking Show")
    # update c1's title: the OLD title must no longer match, the NEW one must
    idx.add("c1", "Completely Unrelated Cooking Show Special")
    assert len(idx) == 2
    assert idx.find_duplicates("The Quick Brown Fox Documentary", threshold=0.9) == []
    hits = idx.find_duplicates("Completely Unrelated Cooking Show Special", threshold=0.9)
    assert [m.catalog_id for m in hits][:1] == ["c1"]
    assert all(m.title == "Completely Unrelated Cooking Show Special" for m in hits if m.catalog_id == "c1")
    # the retired row is not a candidate any more (diagnostic count agrees)
    assert idx.candidate_count("The Quick Brown Fox Documentary", threshold=0.9) == 0
    # update to a blank title retires the row too; re-adding revives cleanly
    idx.add("c1", "   ")
    assert idx.find_duplicates("Completely Unrelated Cooking Show Special", threshold=0.9) == [] or \
        all(m.catalog_id != "c1" for m in idx.find_duplicates("Completely Unrelated Cooking Show Special", threshold=0.9))
    idx.add("c1", "The Quick Brown Fox Documentary")
    assert [m.catalog_id for m in idx.find_duplicates("The Quick Brown Fox Documentary")] == ["c1"]


# ---- fixer (O928) round 2: correctness E3 (postings hygiene) / E4 (roman words) ----

def test_retired_rows_leave_no_trace_in_the_postings():
    idx = TitleSimilarityIndex()
    idx.add("c1", "The Quick Brown Fox Documentary")
    for i in range(50):                       # repeated updates of one record
        idx.add("c1", f"The Quick Brown Fox Documentary take {i}")
    live_rows = {r for rows in idx._postings.values() for r in rows}
    assert live_rows == {idx._row_of["c1"]}   # only the live row is posted
    assert all(rows for rows in idx._postings.values())
    assert [m.catalog_id for m in idx.find_duplicates("The Quick Brown Fox Documentary take 49")] == ["c1"]


def test_words_spelled_in_roman_letters_are_not_episode_numbers():
    from bulk_downloader.dedup import _episode_markers, _roman_to_int
    assert _episode_markers("episode mix") == ()        # "mix" reads as MIX=1009: a word
    assert _episode_markers("part civil") == ()         # not well-formed
    assert _episode_markers("chapter dim") == ()
    assert _episode_markers("episode iv") == (("episode", 4),)
    assert _episode_markers("part xii") == (("part", 12),)
    assert _roman_to_int("civil") == 0 and _roman_to_int("ivx") == 0
    idx = TitleSimilarityIndex()
    idx.add("a", "Summer Party Episode Mix")
    assert [m.catalog_id for m in idx.find_duplicates("Summer Party Episode Mix")] == ["a"]


# ---- fixer (O928) round 3: correctness REFUTE4 (compact episode notations) ----

_P = "the longest running documentary series about the deep ocean and its creatures"


@pytest.mark.parametrize("a,b", [
    (f"{_P} S02", f"{_P} S03"),
    (f"{_P} S2", f"{_P} S3"),
    (f"{_P} E05", f"{_P} E06"),
    (f"{_P} 2x05", f"{_P} 2x06"),
    (f"{_P} 2x05", f"{_P} 3x05"),
    (f"{_P} ep05", f"{_P} ep06"),
])
def test_compact_episode_notations_are_distinct_episodes(a, b):
    from bulk_downloader.dedup import _episode_markers, _normalize_title
    assert _episode_markers(_normalize_title(a)) != _episode_markers(_normalize_title(b))
    idx = TitleSimilarityIndex()
    idx.add("a", a)
    assert idx.find_duplicates(b, threshold=0.9) == []
    assert [m.catalog_id for m in idx.find_duplicates(a, threshold=0.9)] == ["a"]


def test_compact_notations_map_to_the_spelled_out_markers():
    from bulk_downloader.dedup import _episode_markers
    assert _episode_markers("show s02") == (("season", 2),)
    assert _episode_markers("show e05") == _episode_markers("show episode 5") == (("episode", 5),)
    assert _episode_markers("show 2x05") == _episode_markers("show s02e05") == (("episode", 5), ("season", 2))
    # a plain word ending in digits is not a marker; a bare number keeps its empty label
    assert _episode_markers("mp3 2024") == (("", 2024),)
