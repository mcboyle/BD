"""Row 1047: Textual Similarity Indexing & Duplicate Record Reconciliation.

Validates:
1. Textual normalization and tokenization tailored for media filenames and titles.
2. Inverted index textual similarity search with customizable threshold gating.
3. Duplicate record clustering based on textual similarity metrics.
4. Reconciliation strategies (KEEP_FIRST, KEEP_MOST_COMPLETE, MERGE_ATTRIBUTES)
   generating structured, reproducible reconciliation plans.
5. Integration with bulk_downloader.dedup and bdctl CLI companion without breaking
   existing perceptual video dedup contracts.
6. Mutation resistance enforcing threshold boundaries and reconciliation state changes.

RED on baseline: fails with AssertionError (dedup lacks TextualSimilarityIndex).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "module"

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_red_baseline_capability_probe():
    """Verify baseline lacks Row 1047 TextualSimilarityIndex and reconciler.

    RED on baseline (e2989f716d52): fails with AssertionError.
    """
    from bulk_downloader import dedup

    assert hasattr(
        dedup, "TextualSimilarityIndex"
    ), "Row 1047 capability missing: TextualSimilarityIndex not exposed in dedup"
    assert hasattr(
        dedup, "DuplicateRecordReconciler"
    ), "Row 1047 capability missing: DuplicateRecordReconciler not exposed in dedup"


def test_dedup_baseline_positive_control():
    """Positive control proving probe distinguishes existing dedup capabilities from missing ones."""
    from bulk_downloader import dedup

    assert hasattr(dedup, "TitleSimilarityIndex"), "Positive control failed: TitleSimilarityIndex missing"
    assert hasattr(dedup, "HashRegistry"), "Positive control failed: HashRegistry missing"
    assert hasattr(dedup, "get_default_registry"), "Positive control failed: get_default_registry missing"
    assert callable(dedup.get_default_registry)


def test_textual_normalization_and_tokenization():
    """Verify title/filename normalization strips noise tags while preserving core semantic tokens."""
    from bulk_downloader.text_similarity import normalize_text, tokenize_shingles

    raw_title = "Big.Buck.Bunny.2008.1080p.x264.AAC-Sample [Official Video] (HD)"
    normalized = normalize_text(raw_title)

    # Common tags like 1080p, x264, official video should be stripped
    assert "1080p" not in normalized
    assert "x264" not in normalized
    assert "official video" not in normalized
    assert "big buck bunny 2008" in normalized

    # Shingles generation
    shingles = tokenize_shingles("big buck bunny", n=3)
    assert len(shingles) > 0
    assert isinstance(shingles, set)


def test_similarity_scoring_metrics():
    """Verify string similarity metrics (Jaccard, token overlap ratio)."""
    from bulk_downloader.text_similarity import compute_similarity

    s1 = "Elephant's Dream Open Source 1080p"
    s2 = "Elephants Dream (Open Source) [720p]"
    s3 = "Tears of Steel Blender Foundation VFX"

    score_similar = compute_similarity(s1, s2)
    score_different = compute_similarity(s1, s3)

    assert score_similar > 0.75
    assert score_different < 0.3
    assert compute_similarity(s1, s1) == 1.0


def test_inverted_index_indexing_and_query():
    """Verify TextualSimilarityIndex indexing, querying, and threshold pruning."""
    from bulk_downloader.text_similarity import TextualSimilarityIndex

    index = TextualSimilarityIndex(min_similarity=0.6)

    index.add_record("rec-1", "Cosmos Laundromat First Cycle", {"duration": 720})
    index.add_record("rec-2", "Cosmos Laundromat (First Cycle) [1080p]", {"duration": 720})
    index.add_record("rec-3", "Sintel The Durian Open Movie Project", {"duration": 910})

    # Query matches rec-1 and rec-2, but excludes rec-3
    matches = index.find_similar("Cosmos Laundromat First Cycle 4K", threshold=0.7)
    match_ids = [m.record_id for m in matches]

    assert "rec-1" in match_ids
    assert "rec-2" in match_ids
    assert "rec-3" not in match_ids
    assert len(matches) == 2
    assert matches[0].similarity >= matches[1].similarity


def test_duplicate_clustering():
    """Verify finding clusters of duplicate records across the index."""
    from bulk_downloader.text_similarity import TextualSimilarityIndex

    index = TextualSimilarityIndex()
    index.add_record("a1", "Spring Open Movie by Blender")
    index.add_record("a2", "Spring - Open Movie (Blender)")
    index.add_record("b1", "Route 66 Cross Country Roadtrip")

    clusters = index.find_duplicate_clusters(threshold=0.75)
    assert len(clusters) == 1
    cluster_ids = clusters[0].record_ids
    assert "a1" in cluster_ids
    assert "a2" in cluster_ids
    assert "b1" not in cluster_ids


def test_reconciliation_strategies():
    """Verify reconciliation strategies: KEEP_FIRST, KEEP_MOST_COMPLETE, and MERGE_ATTRIBUTES."""
    from bulk_downloader.text_similarity import (
        DuplicateRecordReconciler,
        ReconciliationStrategy,
        TextualRecord,
    )

    reconciler = DuplicateRecordReconciler()

    records = [
        TextualRecord(
            record_id="rec-1",
            text="Big Buck Bunny 1080p",
            timestamp=100.0,
            metadata={"source": "site_a", "views": 1000},
        ),
        TextualRecord(
            record_id="rec-2",
            text="Big Buck Bunny (Original Animation)",
            timestamp=200.0,
            metadata={"source": "site_b", "duration": 596, "tags": ["blender", "rabbit"]},
        ),
    ]

    # 1. KEEP_FIRST chooses earliest timestamp
    plan_first = reconciler.plan_reconciliation(records, strategy=ReconciliationStrategy.KEEP_FIRST)
    assert plan_first.canonical_id == "rec-1"
    assert "rec-2" in plan_first.duplicate_ids

    # 2. KEEP_MOST_COMPLETE chooses record with highest metadata field count
    plan_complete = reconciler.plan_reconciliation(records, strategy=ReconciliationStrategy.KEEP_MOST_COMPLETE)
    assert plan_complete.canonical_id == "rec-2"

    # 3. MERGE_ATTRIBUTES merges metadata dictionaries into canonical record
    plan_merge = reconciler.plan_reconciliation(records, strategy=ReconciliationStrategy.MERGE_ATTRIBUTES)
    assert plan_merge.canonical_id == "rec-1"
    assert "duration" in plan_merge.merged_metadata
    assert plan_merge.merged_metadata["views"] == 1000


def test_dedup_module_integration():
    """Verify that bulk_downloader.dedup exposes TextualSimilarityIndex and reconciler."""
    from bulk_downloader import dedup

    assert hasattr(dedup, "TextualSimilarityIndex")
    assert hasattr(dedup, "DuplicateRecordReconciler")
    assert hasattr(dedup, "reconcile_text_records")

    records = [
        {"id": "r1", "title": "Test Title Video HD"},
        {"id": "r2", "title": "Test Title Video [1080p]"},
    ]
    plan = dedup.reconcile_text_records(records, text_key="title", id_key="id")
    assert plan is not None
    assert len(plan) == 1
    assert plan[0].canonical_id == "r1"


def _run_bdctl(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(tmp_path))
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "bdctl.py"), *argv],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60,
    )


def test_bdctl_dedup_text_cli_integration(tmp_path):
    """E1: `bdctl dedup text-scan FILE` reads records, clusters them and writes the reconciled set."""
    records = [
        {"id": "a1", "title": "Big Buck Bunny 1080p", "timestamp": 1, "source": "x"},
        {"id": "a2", "title": "Big.Buck.Bunny.[720p]", "timestamp": 2, "views": 5},
        {"id": "b1", "title": "Sintel Official Trailer", "timestamp": 3},
    ]
    src = tmp_path / "records.json"
    src.write_text(json.dumps(records))
    out = tmp_path / "reconciled.jsonl"

    res = _run_bdctl(tmp_path, "dedup", "text-scan", str(src), "--strategy", "merge_attributes",
                     "--output", str(out), "--json")
    assert res.returncode == 0, res.stderr
    report = json.loads(res.stdout)
    assert report["records"] == 3 and report["clusters"] == 1
    assert report["plans"][0]["canonical_id"] == "a1"
    assert report["plans"][0]["duplicate_ids"] == ["a2"]
    kept = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["id"] for r in kept] == ["a1", "b1"]
    assert kept[0]["views"] == 5 and kept[0]["source"] == "x"

    human = _run_bdctl(tmp_path, "dedup", "text-scan", str(src))
    assert human.returncode == 0, human.stderr
    assert "1 duplicate cluster(s) in 3 record(s)" in human.stdout
    assert "a1 <- a2" in human.stdout

    # Negative controls: unreadable input and bad record shape exit 1 with a message.
    missing = _run_bdctl(tmp_path, "dedup", "text-scan", str(tmp_path / "nope.json"))
    assert missing.returncode == 1 and "text-scan:" in missing.stderr
    src.write_text(json.dumps([1, 2]))
    bad = _run_bdctl(tmp_path, "dedup", "text-scan", str(src))
    assert bad.returncode == 1 and "text-scan:" in bad.stderr


def test_e1_apply_reconciliation_consumes_plans():
    """E1/P5: plans are applied by dedup.apply_text_reconciliation, not left inert."""
    from bulk_downloader import dedup

    records = [
        {"id": "r1", "title": "Test Title Video HD", "timestamp": 1},
        {"id": "r2", "title": "Test Title Video [1080p]", "timestamp": 2, "extra": "y"},
        {"id": "r3", "title": "Completely Different Thing", "timestamp": 3},
    ]
    plans = dedup.reconcile_text_records(records, strategy=dedup.ReconciliationStrategy.MERGE_ATTRIBUTES)
    kept = dedup.apply_text_reconciliation(records, plans)
    assert [r["id"] for r in kept] == ["r1", "r3"]
    assert kept[0]["extra"] == "y" and kept[0]["title"] == "Test Title Video HD"
    assert records[0].get("extra") is None, "input records must not be mutated"
    with pytest.raises(ValueError, match="duplicate record id"):
        dedup.reconcile_text_records(records + [{"id": "r1", "title": "again"}])


def test_e2_large_duplicate_cluster_keeps_every_member():
    """E2/P4: 60 identical titles form one cluster of 60; none are dropped."""
    from bulk_downloader.text_similarity import TextualSimilarityIndex

    index = TextualSimilarityIndex(min_similarity=0.8)
    for i in range(60):
        index.add_record(f"d{i:02d}", "Same Exact Title")
    index.add_record("other", "Unrelated Words Here")
    clusters = index.find_duplicate_clusters()
    assert [len(c.record_ids) for c in clusters] == [60]
    assert len(index.find_similar("Same Exact Title", top_k=None)) == 60
    assert len(index.find_similar("Same Exact Title", top_k=5)) == 5


def test_e3_merge_does_not_mutate_sources_and_avg_excludes_self():
    """E3/P2/P3: MERGE copies nested values; cluster confidence excludes the self-match."""
    from bulk_downloader.text_similarity import (
        DuplicateRecordReconciler,
        ReconciliationStrategy,
        TextualRecord,
        TextualSimilarityIndex,
        compute_similarity,
    )

    canon = TextualRecord("c", "Title", timestamp=1.0, metadata={"tags": {"k": 1}, "l": [1]})
    dup = TextualRecord("d", "Title", timestamp=2.0, metadata={"tags": {"z": 9}, "l": [2, {"u": 1}]})
    plan = DuplicateRecordReconciler().plan_reconciliation([canon, dup], ReconciliationStrategy.MERGE_ATTRIBUTES)
    assert plan.merged_metadata == {"tags": {"k": 1, "z": 9}, "l": [1, 2, {"u": 1}]}
    assert canon.metadata == {"tags": {"k": 1}, "l": [1]}
    plan.merged_metadata["tags"]["k"] = 7
    assert canon.metadata["tags"]["k"] == 1

    a, b = "Alpha Beta Gamma Delta", "Alpha Beta Gamma Epsilon"
    score = compute_similarity(a, b)
    assert 0.3 < score < 0.9
    index = TextualSimilarityIndex(min_similarity=score - 0.01)
    index.add_record("a", a)
    index.add_record("b", b)
    (cluster,) = index.find_duplicate_clusters()
    assert cluster.avg_similarity == pytest.approx(score)


def test_e4_similarity_blend_is_pinned():
    """E4/m2: score is exactly 0.6 * shingle Jaccard + 0.4 * word Jaccard."""
    from bulk_downloader.text_similarity import (
        compute_similarity,
        jaccard_similarity,
        tokenize_shingles,
        tokenize_words,
    )

    a, b = "abc def", "abc deg"
    shingle = jaccard_similarity(tokenize_shingles(a), tokenize_shingles(b))
    word = jaccard_similarity(set(tokenize_words(a)), set(tokenize_words(b)))
    assert (shingle, word) == (pytest.approx(4 / 6), pytest.approx(1 / 3))
    assert compute_similarity(a, b) == pytest.approx(0.6 * shingle + 0.4 * word)


def test_mutation_resistance_threshold_strictness():
    """Verify threshold boundary condition strictly separates matches from non-matches."""
    from bulk_downloader.text_similarity import TextualSimilarityIndex

    index = TextualSimilarityIndex()
    index.add_record("base", "Alpha Beta Gamma Delta")

    # High threshold match vs low threshold reject
    query = "Alpha Beta Gamma Epsilon"
    score = index.compute_score("Alpha Beta Gamma Delta", query)

    strict_matches = index.find_similar(query, threshold=score + 0.05)
    assert len(strict_matches) == 0

    lenient_matches = index.find_similar(query, threshold=score - 0.05)
    assert len(lenient_matches) == 1
    assert lenient_matches[0].record_id == "base"
