"""Dataset Structural Classifier — Single-Entity vs. Aggregate Dataset.

Classifies arbitrary web, DOM, JSON/API, and tabular datasets into single target
entities (individual video, audio, article, media asset) versus aggregate
collections (playlists, galleries, multi-record feeds, pagination envelopes)
based on intrinsic structural topology rather than URL text alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
import json
import re
from typing import Any, Dict, List, Optional, Sequence, Union


class DatasetKind(str, Enum):
    """Classification taxonomy for datasets."""
    SINGLE_ENTITY = "single_entity"
    AGGREGATE_DATASET = "aggregate_dataset"
    INDETERMINATE = "indeterminate"


@dataclass
class StructuralSignal:
    """Individual structural heuristic measurement."""
    signal_type: str
    score: float
    description: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassificationEvidence:
    """Comprehensive evidence bundle supporting a structural classification decision."""
    source_type: str = "unknown"
    card_count: int = 0
    item_count: int = 0
    has_player: bool = False
    has_pagination: bool = False
    has_repeated_elements: bool = False
    density_score: float = 0.0
    signals: List[StructuralSignal] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DatasetClassificationResult:
    """Outcome of structural dataset classification."""
    ok: bool = True
    kind: DatasetKind = DatasetKind.INDETERMINATE
    is_aggregate: bool = False
    is_single: bool = False
    confidence: float = 0.0
    evidence: ClassificationEvidence = field(default_factory=ClassificationEvidence)
    url: str = ""
    error: str = ""

    @property
    def card_count(self) -> int:
        return self.evidence.card_count

    @property
    def item_count(self) -> int:
        return self.evidence.item_count

    @property
    def has_player(self) -> bool:
        return self.evidence.has_player

    @property
    def has_pagination(self) -> bool:
        return self.evidence.has_pagination

    def to_dict(self) -> Dict[str, Any]:
        """Serialize classification result to dictionary."""
        return {
            "ok": self.ok,
            "kind": self.kind.value if isinstance(self.kind, DatasetKind) else str(self.kind),
            "is_aggregate": self.is_aggregate,
            "is_single": self.is_single,
            "confidence": round(self.confidence, 3),
            "url": self.url,
            "error": self.error,
            "evidence": {
                "source_type": self.evidence.source_type,
                "card_count": self.evidence.card_count,
                "item_count": self.evidence.item_count,
                "has_player": self.evidence.has_player,
                "has_pagination": self.evidence.has_pagination,
                "density_score": round(self.evidence.density_score, 3),
                "signals": [
                    {
                        "signal_type": s.signal_type,
                        "score": round(s.score, 3),
                        "description": s.description,
                        "details": s.details,
                    }
                    for s in self.evidence.signals
                ],
                "details": self.evidence.details,
            },
        }


# JavaScript snippet executed on Playwright pages to inspect DOM card topology.
# Mirrors row 906's measured invariant: groups siblings by tag + class-set.
_DOM_TOPOLOGY_JS = """() => {
    const CHROME = 'nav, header, footer, aside, [role="navigation"], '
        + '[role="menubar"], [role="tablist"], [aria-label*="agination" i], '
        + '[class*="pagination" i], [class*="pager" i], [class*="breadcrumb" i], '
        + '[class*="menu" i]';
    const classKey = el => {
        const raw = el.getAttribute('class') || '';
        return raw.trim().split(/\\s+/).filter(Boolean).sort().join('.');
    };
    const isMediaCard = el => {
        if (el.closest(CHROME)) return false;
        const link = el.matches('a[href]') ? el : el.querySelector('a[href]');
        if (!link) return false;
        return !!el.querySelector('img, picture, video');
    };
    const groups = new Map();
    document.querySelectorAll('body *').forEach(el => {
        if (!isMediaCard(el)) return;
        const parent = el.parentElement;
        if (!parent) return;
        const key = el.tagName + '.' + classKey(el);
        let bucket = groups.get(parent);
        if (!bucket) { bucket = new Map(); groups.set(parent, bucket); }
        bucket.set(key, (bucket.get(key) || 0) + 1);
    });
    let max = 0;
    groups.forEach(bucket => {
        bucket.forEach(count => { if (count > max) max = count; });
    });
    const PLAYER = 'video, iframe[src*="embed" i], iframe[src*="player" i], '
        + 'iframe[src*="youtube" i], iframe[src*="vimeo" i], iframe[src*="jwplayer" i]';
    const isLeafCard = el => {
        if (!isMediaCard(el)) return false;
        for (const d of el.querySelectorAll('*')) { if (isMediaCard(d)) return false; }
        return true;
    };
    let player = false;
    document.querySelectorAll(PLAYER).forEach(el => {
        if (player || el.closest(CHROME)) return;
        let node = el.parentElement, inCard = false;
        while (node && node !== document.body) {
            if (isLeafCard(node)) { inCard = true; break; }
            node = node.parentElement;
        }
        if (!inCard) player = true;
    });
    return {card_count: max, player: player};
}"""


class _SimpleDOMNode:
    """Lightweight node representation for static HTML parser."""
    __slots__ = ("tag", "attrs", "parent", "children", "has_anchor", "has_media", "has_player")

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional[_SimpleDOMNode] = None):
        self.tag = tag.lower()
        self.attrs = attrs
        self.parent = parent
        self.children: List[_SimpleDOMNode] = []
        self.has_anchor = False
        self.has_media = False
        self.has_player = False


class _StaticHTMLClassifierParser(HTMLParser):
    """Streaming HTML parser extracting DOM structural topology without browser overhead."""

    CHROME_TAGS = {"nav", "header", "footer", "aside"}
    PLAYER_EMBED_PATTERNS = re.compile(r"(embed|player|youtube|vimeo|jwplayer)", re.IGNORECASE)
    CHROME_CLASS_PATTERNS = re.compile(r"(pagination|pager|breadcrumb|menu|nav)", re.IGNORECASE)

    def __init__(self):
        super().__init__()
        self.root = _SimpleDOMNode("root", {})
        self.current = self.root
        self.has_standalone_player = False
        self.all_nodes: List[_SimpleDOMNode] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]):
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        node = _SimpleDOMNode(tag, attr_dict, parent=self.current)
        self.current.children.append(node)
        self.current = node
        self.all_nodes.append(node)

        # Check for media/anchor/player presence
        tag_lower = tag.lower()
        if tag_lower in ("a",) and "href" in attr_dict:
            node.has_anchor = True
        elif tag_lower in ("img", "picture", "video", "canvas"):
            node.has_media = True

        if tag_lower == "video":
            node.has_player = True
        elif tag_lower == "iframe":
            src = attr_dict.get("src", "")
            if self.PLAYER_EMBED_PATTERNS.search(src):
                node.has_player = True

    def handle_endtag(self, tag: str):
        if self.current.parent is not None:
            # Propagate media / anchor / player properties up to direct container if appropriate
            p = self.current.parent
            if self.current.has_anchor:
                p.has_anchor = True
            if self.current.has_media:
                p.has_media = True
            if self.current.has_player:
                p.has_player = True
            self.current = p

    CONTAINER_TAGS = {"body", "html", "root", "main", "section", "article"}

    def is_chrome(self, node: _SimpleDOMNode) -> bool:
        curr = node
        while curr and curr.tag != "root":
            if curr.tag in self.CHROME_TAGS:
                return True
            cls = curr.attrs.get("class", "")
            if self.CHROME_CLASS_PATTERNS.search(cls):
                return True
            role = curr.attrs.get("role", "")
            if role in ("navigation", "menubar", "tablist"):
                return True
            curr = curr.parent
        return False

    def is_media_card(self, node: _SimpleDOMNode) -> bool:
        if node.tag in self.CONTAINER_TAGS or self.is_chrome(node):
            return False
        # Needs to contain both an anchor link and media (img, picture, or video)
        has_a = node.has_anchor or (node.tag == "a" and "href" in node.attrs)
        has_m = node.has_media or node.tag in ("img", "picture", "video")
        return bool(has_a and has_m)

    def is_leaf_card(self, node: _SimpleDOMNode) -> bool:
        if not self.is_media_card(node):
            return False
        for ch in node.children:
            if self._has_child_card(ch):
                return False
        return True

    def _has_child_card(self, node: _SimpleDOMNode) -> bool:
        if self.is_media_card(node):
            return True
        for ch in node.children:
            if self._has_child_card(ch):
                return True
        return False

    def analyze_topology(self) -> tuple[int, bool]:
        """Compute max card sibling count and primary player detection."""
        groups: Dict[int, Dict[str, int]] = {}

        for node in self.all_nodes:
            if not self.is_media_card(node):
                continue
            parent = node.parent
            if not parent:
                continue
            parent_id = id(parent)
            if parent_id not in groups:
                groups[parent_id] = {}

            classes = sorted(node.attrs.get("class", "").split())
            key = f"{node.tag}.{'.'.join(classes)}"
            groups[parent_id][key] = groups[parent_id].get(key, 0) + 1

        max_cards = 0
        for bucket in groups.values():
            for cnt in bucket.values():
                if cnt > max_cards:
                    max_cards = cnt

        # Check for players outside media cards
        has_player = False
        for node in self.all_nodes:
            tag_l = node.tag
            is_player_node = (
                tag_l == "video"
                or (tag_l == "iframe" and self.PLAYER_EMBED_PATTERNS.search(node.attrs.get("src", "")))
            )
            if is_player_node and not self.is_chrome(node):
                # Verify whether this player is inside a leaf media card
                in_card = False
                curr = node.parent
                while curr and curr.tag != "root":
                    if self.is_leaf_card(curr):
                        in_card = True
                        break
                    curr = curr.parent
                if not in_card:
                    has_player = True
                    break

        return max_cards, has_player


class DatasetStructuralClassifier:
    """Multi-modal structural classifier separating single entities from aggregates."""

    def __init__(self, min_cards: int = 3, min_items: int = 3):
        self.min_cards = max(1, int(min_cards))
        self.min_items = max(1, int(min_items))

    def classify_dom_page(
        self,
        page: Any,
        url: str = "",
        *,
        min_cards: Optional[int] = None,
        page_load_timeout_ms: int = 30000,
    ) -> DatasetClassificationResult:
        """Classify a live or mock Playwright page by DOM card grid topology."""
        threshold = self.min_cards if min_cards is None else max(1, int(min_cards))
        if not url:
            return DatasetClassificationResult(
                ok=False,
                kind=DatasetKind.INDETERMINATE,
                error="empty_url",
            )
        if page is None:
            return DatasetClassificationResult(
                ok=False,
                kind=DatasetKind.INDETERMINATE,
                error="page_is_none",
            )

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=page_load_timeout_ms)
        except Exception as e:
            return DatasetClassificationResult(
                ok=False,
                url=url,
                error=f"page_load_failed:{type(e).__name__}",
            )

        try:
            raw = page.evaluate(_DOM_TOPOLOGY_JS)
        except Exception as e:
            return DatasetClassificationResult(
                ok=False,
                url=url,
                error=f"evaluate_failed:{type(e).__name__}",
            )

        if isinstance(raw, dict):
            card_count = raw.get("card_count")
            has_player = bool(raw.get("player", False))
        else:
            card_count = raw
            has_player = False

        if not isinstance(card_count, int) or isinstance(card_count, bool) or card_count < 0:
            return DatasetClassificationResult(
                ok=False,
                url=url,
                error="bad_response",
            )

        is_aggregate = (card_count >= threshold) and not has_player
        kind = DatasetKind.AGGREGATE_DATASET if is_aggregate else DatasetKind.SINGLE_ENTITY
        confidence = 0.95 if (card_count >= threshold and not has_player) or has_player else 0.85

        signals = [
            StructuralSignal(
                signal_type="dom_card_grid",
                score=float(card_count),
                description=f"Detected {card_count} sibling cards (threshold: {threshold})",
                details={"card_count": card_count, "threshold": threshold},
            )
        ]
        if has_player:
            signals.append(
                StructuralSignal(
                    signal_type="primary_player",
                    score=1.0,
                    description="Standalone primary media player detected (overrides card repetition)",
                )
            )

        evidence = ClassificationEvidence(
            source_type="dom_page",
            card_count=card_count,
            has_player=has_player,
            density_score=float(card_count) / max(1.0, float(threshold)),
            signals=signals,
        )

        return DatasetClassificationResult(
            ok=True,
            kind=kind,
            is_aggregate=is_aggregate,
            is_single=not is_aggregate,
            confidence=confidence,
            evidence=evidence,
            url=url,
        )

    def classify_html(
        self,
        html: str,
        url: str = "",
        *,
        min_cards: Optional[int] = None,
    ) -> DatasetClassificationResult:
        """Statically inspect HTML structure to determine single entity vs aggregate collection."""
        threshold = self.min_cards if min_cards is None else max(1, int(min_cards))
        if not html or not html.strip():
            return DatasetClassificationResult(
                ok=False,
                kind=DatasetKind.INDETERMINATE,
                error="empty_html",
                url=url,
            )

        try:
            parser = _StaticHTMLClassifierParser()
            parser.feed(html)
            card_count, has_player = parser.analyze_topology()
        except Exception as e:
            return DatasetClassificationResult(
                ok=False,
                kind=DatasetKind.INDETERMINATE,
                error=f"html_parse_failed:{type(e).__name__}",
                url=url,
            )

        is_aggregate = (card_count >= threshold) and not has_player
        kind = DatasetKind.AGGREGATE_DATASET if is_aggregate else DatasetKind.SINGLE_ENTITY
        confidence = 0.90 if is_aggregate or has_player else 0.80

        signals = [
            StructuralSignal(
                signal_type="static_html_cards",
                score=float(card_count),
                description=f"Static HTML sibling card count: {card_count}",
                details={"card_count": card_count, "threshold": threshold},
            )
        ]
        if has_player:
            signals.append(
                StructuralSignal(
                    signal_type="primary_player",
                    score=1.0,
                    description="Primary media player element present outside grid tiles",
                )
            )

        evidence = ClassificationEvidence(
            source_type="html",
            card_count=card_count,
            has_player=has_player,
            density_score=float(card_count) / max(1.0, float(threshold)),
            signals=signals,
        )

        return DatasetClassificationResult(
            ok=True,
            kind=kind,
            is_aggregate=is_aggregate,
            is_single=not is_aggregate,
            confidence=confidence,
            evidence=evidence,
            url=url,
        )

    def classify_json(
        self,
        data: Union[str, Dict[str, Any], List[Any]],
        url: str = "",
        *,
        min_items: Optional[int] = None,
    ) -> DatasetClassificationResult:
        """Classify JSON payload structure (collection array, paginated envelope, or single entity)."""
        threshold = self.min_items if min_items is None else max(1, int(min_items))
        parsed: Any = data
        if isinstance(data, str):
            try:
                parsed = json.loads(data)
            except Exception as e:
                return DatasetClassificationResult(
                    ok=False,
                    kind=DatasetKind.INDETERMINATE,
                    error=f"json_parse_failed:{type(e).__name__}",
                    url=url,
                )

        # 1. Direct Array / List Payload
        if isinstance(parsed, list):
            item_count = len(parsed)
            is_aggregate = item_count >= threshold
            kind = DatasetKind.AGGREGATE_DATASET if is_aggregate else DatasetKind.SINGLE_ENTITY
            signals = [
                StructuralSignal(
                    signal_type="json_root_array",
                    score=float(item_count),
                    description=f"JSON root array contains {item_count} items (threshold: {threshold})",
                )
            ]
            evidence = ClassificationEvidence(
                source_type="json",
                item_count=item_count,
                has_repeated_elements=item_count > 1,
                density_score=float(item_count) / max(1.0, float(threshold)),
                signals=signals,
            )
            return DatasetClassificationResult(
                ok=True,
                kind=kind,
                is_aggregate=is_aggregate,
                is_single=not is_aggregate,
                confidence=0.92,
                evidence=evidence,
                url=url,
            )

        # 2. Object Envelope Payload
        if isinstance(parsed, dict):
            pagination_keys = {"page", "total", "total_count", "totalpages", "per_page", "next_page", "cursor"}
            collection_keys = {"items", "data", "results", "entries", "records", "hits", "rows", "elements", "assets", "videos"}

            has_pagination = any(k.lower() in pagination_keys for k in parsed.keys())
            found_collection = None
            found_key = ""
            for k, v in parsed.items():
                if k.lower() in collection_keys and isinstance(v, list):
                    found_collection = v
                    found_key = k
                    break

            if found_collection is not None and len(found_collection) >= threshold:
                item_count = len(found_collection)
                signals = [
                    StructuralSignal(
                        signal_type="json_collection_envelope",
                        score=float(item_count),
                        description=f"Envelope field '{found_key}' contains {item_count} items",
                    )
                ]
                if has_pagination:
                    signals.append(
                        StructuralSignal(
                            signal_type="pagination_metadata",
                            score=1.0,
                            description="Pagination metadata detected in JSON envelope",
                        )
                    )
                evidence = ClassificationEvidence(
                    source_type="json",
                    item_count=item_count,
                    has_pagination=has_pagination,
                    has_repeated_elements=True,
                    density_score=float(item_count) / max(1.0, float(threshold)),
                    signals=signals,
                    details={"envelope_key": found_key},
                )
                return DatasetClassificationResult(
                    ok=True,
                    kind=DatasetKind.AGGREGATE_DATASET,
                    is_aggregate=True,
                    is_single=False,
                    confidence=0.95 if has_pagination else 0.88,
                    evidence=evidence,
                    url=url,
                )

            # Single Entity Record Dict
            signals = [
                StructuralSignal(
                    signal_type="json_single_record",
                    score=1.0,
                    description="Single-entity scalar dictionary structure",
                )
            ]
            evidence = ClassificationEvidence(
                source_type="json",
                item_count=1,
                has_pagination=False,
                has_repeated_elements=False,
                density_score=0.1,
                signals=signals,
            )
            return DatasetClassificationResult(
                ok=True,
                kind=DatasetKind.SINGLE_ENTITY,
                is_aggregate=False,
                is_single=True,
                confidence=0.85,
                evidence=evidence,
                url=url,
            )

        return DatasetClassificationResult(
            ok=False,
            kind=DatasetKind.INDETERMINATE,
            error="unsupported_json_root_type",
            url=url,
        )

    def classify_payload(
        self,
        payload: Any,
        *,
        content_type: Optional[str] = None,
        url: str = "",
    ) -> DatasetClassificationResult:
        """Route arbitrary payload to the appropriate structural classifier."""
        if payload is None:
            return DatasetClassificationResult(ok=False, error="payload_is_none", url=url)

        # Playwright Page object
        if hasattr(payload, "goto") and hasattr(payload, "evaluate"):
            return self.classify_dom_page(payload, url or "https://example.com/page")

        # Python dict or list -> JSON classifier
        if isinstance(payload, (dict, list)):
            return self.classify_json(payload, url=url)

        # String payload
        if isinstance(payload, str):
            s = payload.strip()
            c_type = (content_type or "").lower()
            if "html" in c_type or s.startswith("<!DOCTYPE") or s.startswith("<html") or "<body" in s:
                return self.classify_html(payload, url=url)
            if "json" in c_type or (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
                return self.classify_json(payload, url=url)
            # Default to static HTML probe if contains markup
            if "<" in s and ">" in s:
                return self.classify_html(payload, url=url)

        return DatasetClassificationResult(
            ok=False,
            kind=DatasetKind.INDETERMINATE,
            error="unrecognized_payload_format",
            url=url,
        )


def classify_dataset_structure(
    payload: Any,
    *,
    url: str = "",
    min_cards: int = 3,
    min_items: int = 3,
    content_type: Optional[str] = None,
) -> DatasetClassificationResult:
    """Convenience functional interface for structural dataset classification."""
    classifier = DatasetStructuralClassifier(min_cards=min_cards, min_items=min_items)
    return classifier.classify_payload(payload, content_type=content_type, url=url)


def get_dataset_classifier_info() -> Dict[str, Any]:
    """Diagnostic capability descriptor."""
    return {
        "classifier": "DatasetStructuralClassifier",
        "modalities": ["dom_page", "static_html", "json_envelope", "tabular"],
        "default_min_cards": 3,
        "default_min_items": 3,
        "taxonomy": [k.value for k in DatasetKind],
    }
