"""Contract tests for DOM pruning before IPC serialization (Row 925).

Verifies:
1. In-browser pre-serialization DOM pruner JS snippet structure
2. Streaming HTML parser removes non-semantic subtrees (script, style, svg, noscript, template)
3. Preserves navigational markup, link attributes, and media tags
4. >70% serialization memory reduction on heavy DOM trees
5. Negative control: clean semantic markup is preserved with zero tag loss
"""
from __future__ import annotations

import re
from typing import Any

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader.dom_pruner import (
        PRUNE_DOM_JS,
        _PRUNED_TAGS,
        prune_html,
        prune_page_dom,
    )
except ImportError:
    # Behavioral RED fallback on unpatched base: raw pass-through
    _PRUNED_TAGS = frozenset()
    PRUNE_DOM_JS = ""

    def prune_html(html: str) -> str:
        return html

    def prune_page_dom(page: Any) -> str:
        return ""


def test_prune_html_removes_nonsemantic_tags_and_exact_counts():
    """Verify non-semantic tags are removed while preserving media and links with exact counts."""
    heavy_payload = "var a = 1;\n" * 1_000  # ~12 KB payload
    heavy_svg_path = '<path d="M10 10 H 90 V 90 H 10 L 10 10"/>' * 500  # ~24 KB
    heavy_css = "body { margin: 0; padding: 0; display: flex; }\n" * 500  # ~23 KB

    raw = (
        '<main id="content">'
        '<nav><a href="/watch" data-id="42">Watch Link</a>'
        '<a href="/browse" class="nav-item">Browse Link</a></nav>'
        '<video src="movie.mp4" poster="cover.jpg">'
        '<source src="movie.webm" type="video/webm">'
        '</video>'
        '<img src="preview.jpg" alt="Thumbnail" />'
        f'<script type="text/javascript">{heavy_payload}</script>'
        f'<script src="https://cdn.example.com/bundle.js"></script>'
        f'<style>{heavy_css}</style>'
        f'<svg width="100" height="100">{heavy_svg_path}</svg>'
        f'<svg viewBox="0 0 100 100">{heavy_svg_path}</svg>'
        '<noscript><p>Please enable JavaScript</p></noscript>'
        '<template id="card"><div>Template Content</div></template>'
        '<p>First semantic text block</p>'
        '<p>Second semantic text block</p>'
        '</main>'
    )

    # Initial exact count checks in unpruned source
    assert raw.count("<script") == 2
    assert raw.count("<style") == 1
    assert raw.count("<svg") == 2
    assert raw.count("<noscript") == 1
    assert raw.count("<template") == 1
    assert raw.count("<a ") == 2
    assert raw.count("<video") == 1
    assert raw.count("<source") == 1
    assert raw.count("<img") == 1
    assert raw.count("<p>") == 3

    pruned = prune_html(raw)

    # Exact count assertions: all non-semantic tags removed
    assert pruned.count("<script") == 0
    assert pruned.count("<style") == 0
    assert pruned.count("<svg") == 0
    assert pruned.count("<noscript") == 0
    assert pruned.count("<template") == 0

    # Exact count assertions: all semantic elements and media preserved
    assert pruned.count("<a ") == 2
    assert pruned.count("<video") == 1
    assert pruned.count("<source") == 1
    assert pruned.count("<img") == 1
    assert pruned.count("<p>") == 2

    # Exact attribute preservation
    assert 'href="/watch"' in pruned
    assert 'data-id="42"' in pruned
    assert 'href="/browse"' in pruned
    assert 'src="movie.mp4"' in pruned
    assert 'poster="cover.jpg"' in pruned
    assert 'src="movie.webm"' in pruned
    assert 'src="preview.jpg"' in pruned
    assert 'alt="Thumbnail"' in pruned

    # Acceptance criterion: >70% serialization memory reduction
    reduction = 1.0 - (len(pruned) / len(raw))
    assert reduction >= 0.70, f"Expected >= 70% reduction, got {reduction:.2%}"


def test_negative_control_semantic_markup_preserved_without_loss():
    """Negative control: document with only semantic tags is preserved with zero loss."""
    semantic_html = (
        '<article class="post">'
        '<header>'
        '<h1>Semantic Article Title</h1>'
        '<nav><a href="/section1">Section 1</a><a href="/section2">Section 2</a></nav>'
        '</header>'
        '<section>'
        '<p>Introduction paragraph with valid markup.</p>'
        '<figure><img src="diagram.png" alt="Architecture" /><figcaption>Fig 1</figcaption></figure>'
        '<video src="demo.mp4" controls="controls"><source src="demo.webm" type="video/webm" /></video>'
        '<p>Conclusion paragraph with additional details.</p>'
        '</section>'
        '<footer><p>Footer note.</p></footer>'
        '</article>'
    )

    pruned = prune_html(semantic_html)

    # Exact count assertions on negative control: every element is preserved
    assert pruned.count("<article") == 1
    assert pruned.count("<header") == 1
    assert pruned.count("<h1>") == 1
    assert pruned.count("<nav") == 1
    assert pruned.count("<a ") == 2
    assert pruned.count("<section") == 1
    assert pruned.count("<figure") == 1
    assert pruned.count("<img") == 1
    assert pruned.count("<figcaption") == 1
    assert pruned.count("<video") == 1
    assert pruned.count("<source") == 1
    assert pruned.count("<p>") == 3
    assert pruned.count("<footer") == 1

    # Text content preserved exactly
    assert "Semantic Article Title" in pruned
    assert "Introduction paragraph with valid markup." in pruned
    assert "Conclusion paragraph with additional details." in pruned
    assert "Footer note." in pruned


def test_in_browser_prune_dom_js_and_page_dom():
    """Verify the in-browser pre-serialization JS script and helper."""
    assert isinstance(PRUNE_DOM_JS, str)
    assert len(PRUNE_DOM_JS) > 0
    assert "cloneNode(true)" in PRUNE_DOM_JS

    # Verify all non-semantic tags are targeted in the browser script
    for tag in ("script", "style", "svg", "noscript", "template"):
        assert tag in PRUNE_DOM_JS

    class MockPage:
        def __init__(self, return_value: str):
            self.return_value = return_value
            self.last_script: str | None = None

        def evaluate(self, script: str) -> str:
            self.last_script = script
            return self.return_value

    expected_html = "<main><p>Clean DOM</p></main>"
    mock_page = MockPage(expected_html)

    result = prune_page_dom(mock_page)
    assert result == expected_html
    assert mock_page.last_script == PRUNE_DOM_JS

    # Negative control: non-evaluate object returns empty string
    assert prune_page_dom(object()) == ""
