"""Row 1044: Single-Entity vs. Aggregate Dataset Structural Classifier.

Provides structural classification across DOM page topologies, static HTML,
JSON/API envelopes, and tabular/feed datasets to accurately differentiate
between individual target items (single video/audio/document) and collections
(playlists, galleries, multi-record datasets) prior to extraction/queueing.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import dataset_structural_classifier
except ImportError:
    dataset_structural_classifier = None


class _MockPlaywrightPage:
    """Fixture Playwright page for testing DOM topology classification without a browser."""

    def __init__(self, *, card_count=None, player=False, evaluate_raises=None, goto_raises=None):
        self.card_count = card_count
        self.player = player
        self.evaluate_raises = evaluate_raises
        self.goto_raises = goto_raises
        self.goto_calls = []
        self.evaluate_calls = []

    def goto(self, url, *, wait_until, timeout):
        self.goto_calls.append(url)
        if self.goto_raises:
            raise self.goto_raises

    def evaluate(self, javascript):
        self.evaluate_calls.append(javascript)
        if self.evaluate_raises:
            raise self.evaluate_raises
        if isinstance(self.card_count, dict):
            return self.card_count
        return {"card_count": self.card_count, "player": self.player}


def test_positive_control_existing_topology_classifier_baseline():
    """Verify test harness integrity and positive baseline probe in playlist_extractor."""
    from bulk_downloader.playlist_extractor import classify_dataset_topology, TopologyResult
    assert callable(classify_dataset_topology), "Existing classify_dataset_topology missing on base"
    assert issubclass(TopologyResult, object)


def test_dataset_structural_classifier_capability_implemented():
    """Verify bulk_downloader.dataset_structural_classifier module and core symbols exist."""
    assert dataset_structural_classifier is not None, (
        "Row 1044 capability missing: Single-Entity vs. Aggregate Dataset Structural Classifier "
        "not implemented in bulk_downloader.dataset_structural_classifier"
    )
    from bulk_downloader.dataset_structural_classifier import (
        DatasetStructuralClassifier,
        DatasetKind,
        DatasetClassificationResult,
        classify_dataset_structure,
    )
    assert issubclass(DatasetKind, object)
    assert callable(classify_dataset_structure)
    clf = DatasetStructuralClassifier()
    assert hasattr(clf, "classify_dom_page")
    assert hasattr(clf, "classify_html")
    assert hasattr(clf, "classify_json")


def test_dom_page_classification_single_vs_aggregate():
    """Verify DOM page classification accurately separates aggregate collections from single-entity pages."""
    assert dataset_structural_classifier is not None, "capability missing"
    from bulk_downloader.dataset_structural_classifier import DatasetStructuralClassifier, DatasetKind

    clf = DatasetStructuralClassifier(min_cards=3)

    # 24 cards under grid parent -> AGGREGATE_DATASET
    agg_page = _MockPlaywrightPage(card_count=24, player=False)
    agg_res = clf.classify_dom_page(agg_page, "https://example.com/gallery/landscape")
    assert agg_res.ok is True
    assert agg_res.kind == DatasetKind.AGGREGATE_DATASET
    assert agg_res.is_aggregate is True
    assert agg_res.is_single is False
    assert agg_res.card_count == 24
    assert agg_res.has_player is False

    # 1 card -> SINGLE_ENTITY
    single_page = _MockPlaywrightPage(card_count=1, player=False)
    single_res = clf.classify_dom_page(single_page, "https://example.com/media/item-42")
    assert single_res.ok is True
    assert single_res.kind == DatasetKind.SINGLE_ENTITY
    assert single_res.is_aggregate is False
    assert single_res.is_single is True

    # 10 cards BUT standalone primary player present -> SINGLE_ENTITY (related cards do not override subject player)
    player_page = _MockPlaywrightPage(card_count=10, player=True)
    player_res = clf.classify_dom_page(player_page, "https://example.com/watch?v=123")
    assert player_res.ok is True
    assert player_res.kind == DatasetKind.SINGLE_ENTITY
    assert player_res.is_aggregate is False
    assert player_res.has_player is True


def test_static_html_structural_classification():
    """Verify static HTML classifier parses repeated sibling cards and player elements without Playwright."""
    assert dataset_structural_classifier is not None, "capability missing"
    from bulk_downloader.dataset_structural_classifier import DatasetStructuralClassifier, DatasetKind

    clf = DatasetStructuralClassifier(min_cards=3)

    # Static HTML gallery with repeated item cards
    html_gallery = """<!DOCTYPE html>
    <html>
      <body>
        <nav><a href="/">Home</a><a href="/login">Login</a></nav>
        <div class="media-grid">
          <div class="card"><a href="/item/1"><img src="/t1.jpg"/><h3>Item 1</h3></a></div>
          <div class="card"><a href="/item/2"><img src="/t2.jpg"/><h3>Item 2</h3></a></div>
          <div class="card"><a href="/item/3"><img src="/t3.jpg"/><h3>Item 3</h3></a></div>
          <div class="card"><a href="/item/4"><img src="/t4.jpg"/><h3>Item 4</h3></a></div>
        </div>
      </body>
    </html>
    """
    res_gallery = clf.classify_html(html_gallery, url="https://example.com/gallery")
    assert res_gallery.ok is True
    assert res_gallery.is_aggregate is True
    assert res_gallery.kind == DatasetKind.AGGREGATE_DATASET
    assert res_gallery.card_count >= 4

    # Static HTML single video item page with embedded player and only 1 related link
    html_video = """<!DOCTYPE html>
    <html>
      <body>
        <div class="player-container">
          <video src="https://cdn.example.com/stream.mp4" controls></video>
        </div>
        <div class="sidebar">
          <div class="card"><a href="/item/99"><img src="/t99.jpg"/></a></div>
        </div>
      </body>
    </html>
    """
    res_video = clf.classify_html(html_video, url="https://example.com/video/100")
    assert res_video.ok is True
    assert res_video.is_aggregate is False
    assert res_video.kind == DatasetKind.SINGLE_ENTITY
    assert res_video.has_player is True


def test_json_payload_structural_classification():
    """Verify JSON classifier discriminates paginated collection envelopes, item lists, and single entities."""
    assert dataset_structural_classifier is not None, "capability missing"
    from bulk_downloader.dataset_structural_classifier import DatasetStructuralClassifier, DatasetKind

    clf = DatasetStructuralClassifier(min_items=3)

    # List of records -> Aggregate
    items = [{"id": i, "title": f"Media {i}", "url": f"https://cdn/{i}.mp4"} for i in range(5)]
    res_list = clf.classify_json(items)
    assert res_list.ok is True
    assert res_list.is_aggregate is True
    assert res_list.item_count == 5

    # Paginated envelope dictionary -> Aggregate
    envelope = {
        "status": "success",
        "page": 1,
        "per_page": 10,
        "total_count": 50,
        "items": [{"id": i, "name": f"asset_{i}"} for i in range(6)],
    }
    res_env = clf.classify_json(envelope)
    assert res_env.ok is True
    assert res_env.is_aggregate is True
    assert res_env.has_pagination is True
    assert res_env.item_count == 6

    # Single item record dictionary -> Single entity
    single_record = {
        "id": "asset-987",
        "title": "Full Length Feature",
        "duration_seconds": 3600,
        "download_url": "https://cdn.example.com/video.mp4",
        "thumbnail": "https://cdn.example.com/thumb.jpg",
        "tags": ["hd", "feature"],
    }
    res_single = clf.classify_json(single_record)
    assert res_single.ok is True
    assert res_single.is_aggregate is False
    assert res_single.kind == DatasetKind.SINGLE_ENTITY


def test_classify_dataset_structure_unified_dispatch():
    """Verify unified functional entrypoint automatically detects format and classifies payload."""
    assert dataset_structural_classifier is not None, "capability missing"
    from bulk_downloader.dataset_structural_classifier import classify_dataset_structure, DatasetKind

    # JSON string dispatch
    json_str = json.dumps([{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}])
    res = classify_dataset_structure(json_str)
    assert res.ok is True
    assert res.kind == DatasetKind.AGGREGATE_DATASET

    # HTML string dispatch
    html_str = "<html><body><video src='v.mp4'></video></body></html>"
    res_html = classify_dataset_structure(html_str, content_type="text/html")
    assert res_html.ok is True
    assert res_html.has_player is True
    assert res_html.kind == DatasetKind.SINGLE_ENTITY


def test_playlist_extractor_integration_and_backwards_compatibility():
    """Verify bulk_downloader.playlist_extractor.classify_dataset_topology interoperates with structural classifier."""
    assert dataset_structural_classifier is not None, "capability missing"
    from bulk_downloader import playlist_extractor as p

    page = _MockPlaywrightPage(card_count=12, player=False)
    result = p.classify_dataset_topology(page, "https://example.com/models/curated")
    assert result.ok is True
    assert result.is_aggregate is True
    assert result.card_count == 12
