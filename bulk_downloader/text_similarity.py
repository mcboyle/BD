"""Row 1047: Textual Similarity Indexing & Duplicate Record Reconciliation.

Provides:
1. Media filename/title normalization stripping noise artifacts (resolution, codec,
   source containers, release groups, and bracketed editorial tags).
2. Shingle-based and token-based similarity indexing with candidate pruning.
3. Inverted index (TextualSimilarityIndex) for finding near-duplicate textual records.
4. DuplicateRecordReconciler with strategies (KEEP_FIRST, KEEP_MOST_COMPLETE,
   MERGE_ATTRIBUTES) producing structured, deterministic reconciliation plans.
5. High-level reconciliation helpers integrated into dedup and CLI.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

log = logging.getLogger(__name__)

# Noise patterns commonly present in media titles/filenames
_NOISE_TAGS = [
    r"\b(?:1080p|720p|480p|2160p|4k|uhd|hd|sd|fhd)\b",
    r"\b(?:x264|x265|h264|h265|hevc|av1|xvid|divx|aac|ac3|mp3|flac|opus)\b",
    r"\b(?:webrip|web-dl|bluray|bdrip|dvdrip|hdtv|camrip|remux|sample|proper|repack)\b",
    r"\[(?:official\s+video|official\s+audio|music\s+video|lyric\s+video|hd|1080p|720p|4k)\]",
    r"\((?:official\s+video|official\s+audio|music\s+video|lyric\s+video|hd|1080p|720p|4k)\)",
    r"\b(?:official\s+video|official\s+audio|music\s+video|lyric\s+video)\b",
]
_NOISE_RE = re.compile("|".join(_NOISE_TAGS), re.IGNORECASE)
_PUNCT_COLLAPSE_RE = re.compile(r"[\._\-\+\[\]\(\)\{\}\/\\\|]+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Normalize media title or filename by stripping noise tags, punctuation, and casing."""
    if not text:
        return ""
    # Lowercase
    t = text.lower()
    # Strip noise tags
    t = _NOISE_RE.sub(" ", t)
    # Replace punctuation / delimiters with space
    t = _PUNCT_COLLAPSE_RE.sub(" ", t)
    # Remove non-alphanumeric except whitespace
    t = re.sub(r"[^\w\s]", "", t)
    # Collapse whitespace
    t = _WHITESPACE_RE.sub(" ", t).strip()
    return t


def tokenize_words(text: str) -> List[str]:
    """Tokenize normalized text into semantic words."""
    norm = normalize_text(text)
    return [w for w in norm.split() if w]


def tokenize_shingles(text: str, n: int = 3) -> Set[str]:
    """Generate character n-grams (shingles) over normalized text."""
    norm = normalize_text(text)
    compact = norm.replace(" ", "_")
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def compute_similarity(text_a: str, text_b: str) -> float:
    """Compute combined textual similarity score between two strings [0.0 - 1.0]."""
    norm_a = normalize_text(text_a)
    norm_b = normalize_text(text_b)
    if norm_a == norm_b:
        return 1.0
    if not norm_a or not norm_b:
        return 0.0

    # 1. Shingle similarity (captures character-level spelling and suffix matches)
    shingles_a = tokenize_shingles(norm_a, n=3)
    shingles_b = tokenize_shingles(norm_b, n=3)
    shingle_score = jaccard_similarity(shingles_a, shingles_b)

    # 2. Word token overlap
    words_a = set(tokenize_words(norm_a))
    words_b = set(tokenize_words(norm_b))
    word_score = jaccard_similarity(words_a, words_b)

    # Balanced harmonic/weighted blend
    return 0.6 * shingle_score + 0.4 * word_score


class ReconciliationStrategy(str, Enum):
    """Strategy for resolving duplicate record clusters into a single canonical record."""

    KEEP_FIRST = "keep_first"
    KEEP_MOST_COMPLETE = "keep_most_complete"
    MERGE_ATTRIBUTES = "merge_attributes"


@dataclass
class TextualRecord:
    """A record indexed for textual similarity analysis."""

    record_id: str
    text: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SimilarityMatch:
    """Match result for a similarity query."""

    record_id: str
    similarity: float
    matched_text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DuplicateCluster:
    """A group of mutually similar records."""

    cluster_id: str
    record_ids: List[str]
    primary_id: str
    avg_similarity: float


@dataclass(frozen=True)
class ReconciliationPlan:
    """Deterministic plan for reconciling duplicate records into a canonical record."""

    canonical_id: str
    duplicate_ids: List[str]
    strategy: ReconciliationStrategy
    merged_metadata: Dict[str, Any]


class TextualSimilarityIndex:
    """In-memory inverted index for efficient textual similarity and near-duplicate search."""

    def __init__(self, min_similarity: float = 0.7):
        self.min_similarity = min_similarity
        self._records: Dict[str, TextualRecord] = {}
        self._postings: Dict[str, Set[str]] = {}  # shingle -> {record_id, ...}
        self._record_shingles: Dict[str, Set[str]] = {}

    def compute_score(self, text_a: str, text_b: str) -> float:
        """Compute textual similarity between two strings."""
        return compute_similarity(text_a, text_b)

    def add_record(
        self,
        record_id: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: float = 0.0,
    ) -> None:
        """Index a textual record."""
        rec = TextualRecord(
            record_id=str(record_id),
            text=str(text),
            timestamp=float(timestamp) if timestamp else time.time(),
            metadata=dict(metadata or {}),
        )
        self._records[rec.record_id] = rec

        shingles = tokenize_shingles(rec.text)
        self._record_shingles[rec.record_id] = shingles
        for sh in shingles:
            if sh not in self._postings:
                self._postings[sh] = set()
            self._postings[sh].add(rec.record_id)

    def find_similar(
        self, query_text: str, threshold: Optional[float] = None, top_k: Optional[int] = 20
    ) -> List[SimilarityMatch]:
        """Find records similar to query_text exceeding the threshold (top_k=None: all)."""
        thresh = threshold if threshold is not None else self.min_similarity
        query_shingles = tokenize_shingles(query_text)
        if not query_shingles:
            return []

        # Candidate selection via postings intersection
        candidates: Set[str] = set()
        for sh in query_shingles:
            candidates.update(self._postings.get(sh, ()))

        matches: List[SimilarityMatch] = []
        for cid in candidates:
            rec = self._records[cid]
            score = self.compute_score(query_text, rec.text)
            if score >= thresh:
                matches.append(
                    SimilarityMatch(
                        record_id=rec.record_id,
                        similarity=score,
                        matched_text=rec.text,
                        metadata=rec.metadata,
                    )
                )

        matches.sort(key=lambda m: (-m.similarity, m.record_id))
        return matches if top_k is None else matches[:top_k]

    def find_duplicate_clusters(
        self, threshold: Optional[float] = None
    ) -> List[DuplicateCluster]:
        """Group indexed records into clusters of mutually similar duplicates."""
        thresh = threshold if threshold is not None else self.min_similarity
        visited: Set[str] = set()
        clusters: List[DuplicateCluster] = []

        for rid, rec in self._records.items():
            if rid in visited:
                continue

            # Every match, uncapped: a cluster must never silently lose members.
            matches = self.find_similar(rec.text, threshold=thresh, top_k=None)
            others = [m for m in matches if m.record_id != rid and m.record_id not in visited]

            if others:
                cluster_member_ids = [rid] + [m.record_id for m in others]
                visited.update(cluster_member_ids)
                # Confidence is seed-to-member similarity; the seed's self-match is excluded.
                avg_score = sum(m.similarity for m in others) / len(others)

                clusters.append(
                    DuplicateCluster(
                        cluster_id=f"cluster_{rid}",
                        record_ids=cluster_member_ids,
                        primary_id=rid,
                        avg_similarity=avg_score,
                    )
                )

        return clusters


class DuplicateRecordReconciler:
    """Reconciles duplicate records according to configurable resolution strategies."""

    def plan_reconciliation(
        self,
        records: List[TextualRecord],
        strategy: ReconciliationStrategy = ReconciliationStrategy.KEEP_FIRST,
    ) -> ReconciliationPlan:
        """Create a reconciliation plan for a list of duplicate records."""
        if not records:
            raise ValueError("No records provided for reconciliation")

        if len(records) == 1:
            return ReconciliationPlan(
                canonical_id=records[0].record_id,
                duplicate_ids=[],
                strategy=strategy,
                merged_metadata=copy.deepcopy(records[0].metadata),
            )

        # 1. Select canonical record
        if strategy == ReconciliationStrategy.KEEP_MOST_COMPLETE:
            # Score by number of non-empty metadata fields + length of text
            def _completeness(r: TextualRecord) -> int:
                return len([v for v in r.metadata.values() if v is not None]) * 100 + len(r.text)

            canonical = max(records, key=_completeness)
        else:  # KEEP_FIRST / MERGE_ATTRIBUTES: earliest timestamp, ties by input order
            canonical = min(records, key=lambda r: r.timestamp)

        duplicate_ids = [r.record_id for r in records if r.record_id != canonical.record_id]

        # 2. Build merged metadata (a deep copy: source records are never mutated)
        merged: Dict[str, Any] = copy.deepcopy(canonical.metadata)
        if strategy == ReconciliationStrategy.MERGE_ATTRIBUTES:
            for r in records:
                if r is not canonical:
                    _merge_missing(merged, r.metadata)

        return ReconciliationPlan(
            canonical_id=canonical.record_id,
            duplicate_ids=duplicate_ids,
            strategy=strategy,
            merged_metadata=merged,
        )

    def reconcile_records(
        self,
        records: List[Dict[str, Any]],
        text_key: str = "title",
        id_key: str = "id",
        strategy: ReconciliationStrategy = ReconciliationStrategy.KEEP_FIRST,
        threshold: float = 0.8,
    ) -> List[ReconciliationPlan]:
        """High-level batch reconciliation over a list of record dicts.

        Records without a timestamp sort after timestamped ones, in input order.
        """
        index = TextualSimilarityIndex(min_similarity=threshold)
        typed_records: Dict[str, TextualRecord] = {}

        for idx, r in enumerate(records):
            rid = str(r.get(id_key, f"rec_{idx}"))
            if rid in typed_records:
                raise ValueError(f"duplicate record id {rid!r}")
            text = str(r.get(text_key, ""))
            ts = float(r["timestamp"]) if r.get("timestamp") is not None else float("inf")
            meta = {k: v for k, v in r.items() if k not in (id_key, text_key, "timestamp")}
            trec = TextualRecord(record_id=rid, text=text, timestamp=ts, metadata=meta)
            typed_records[rid] = trec
            index.add_record(rid, text, metadata=meta, timestamp=ts)

        clusters = index.find_duplicate_clusters(threshold=threshold)
        plans: List[ReconciliationPlan] = []

        for cl in clusters:
            # Input order, so ties in the strategy's selection key resolve deterministically.
            members = set(cl.record_ids)
            group = [rec for rid, rec in typed_records.items() if rid in members]
            plan = self.plan_reconciliation(group, strategy=strategy)
            plans.append(plan)

        return plans

    def apply_reconciliation(
        self,
        records: List[Dict[str, Any]],
        plans: List[ReconciliationPlan],
        id_key: str = "id",
    ) -> List[Dict[str, Any]]:
        """Return a new record list with each plan applied, in input order.

        Duplicates are dropped; each canonical record gets the plan's merged
        metadata. Input records are not mutated.
        """
        dropped: Set[str] = set()
        merged_by_id: Dict[str, Dict[str, Any]] = {}
        for plan in plans:
            dropped.update(plan.duplicate_ids)
            merged_by_id[plan.canonical_id] = plan.merged_metadata

        out: List[Dict[str, Any]] = []
        for idx, r in enumerate(records):
            rid = str(r.get(id_key, f"rec_{idx}"))
            if rid in dropped:
                continue
            rec = copy.deepcopy(r)
            if rid in merged_by_id:
                rec.update(copy.deepcopy(merged_by_id[rid]))
            out.append(rec)
        return out


def _merge_missing(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Fill target from source without overriding values target already has.

    Empty/None values are filled, lists gain unseen items in order, nested dicts
    merge recursively under the same rule. Values taken from source are copied.
    """
    for k, v in source.items():
        cur = target.get(k)
        if k not in target or cur is None or cur == "":
            target[k] = copy.deepcopy(v)
        elif isinstance(cur, list) and isinstance(v, list):
            cur.extend(copy.deepcopy(item) for item in v if item not in cur)
        elif isinstance(cur, dict) and isinstance(v, dict):
            _merge_missing(cur, v)
