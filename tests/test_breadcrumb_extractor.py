"""Tests for Row 950: RESILIENT-BREADCRUMB-AND-PARENT-CATEGORY-PATH-EXTRACTOR.

ACCEPTANCE:
(1) hierarchical path extraction from JSON-LD and HTML breadcrumbs
(2) filesystem path sanitization
(3) fallback handling
"""

import pytest

from bulk_downloader.breadcrumb_extractor import (
    BreadcrumbExtractor,
    breadcrumbs_to_path,
    extract_breadcrumbs,
    extract_category_path,
    extract_html_breadcrumbs,
    extract_jsonld_breadcrumbs,
    extract_url_breadcrumbs,
    sanitize_path_segment,
)

BD_GATE_SCOPE = "module"


# ─── (1) Hierarchical path extraction from JSON-LD and HTML ───────────


def test_extract_jsonld_breadcrumbs_standard():
    """Extract ordered hierarchical path from schema.org BreadcrumbList JSON-LD."""
    jsonld_html = """
    <!DOCTYPE html>
    <html>
    <head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
          {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://example.com/"},
          {"@type": "ListItem", "position": 2, "name": "Entertainment", "item": "https://example.com/ent"},
          {"@type": "ListItem", "position": 3, "name": "Documentaries", "item": "https://example.com/ent/docs"}
        ]
      }
      </script>
    </head>
    <body></body>
    </html>
    """
    crumbs = extract_jsonld_breadcrumbs(jsonld_html)
    assert crumbs == ["Home", "Entertainment", "Documentaries"]

    # When converting to category path, root ("Home") is skipped by default
    path = extract_category_path(jsonld_html)
    assert path == "Entertainment/Documentaries"


def test_extract_jsonld_breadcrumbs_out_of_order_and_graph():
    """Handle out-of-order positions, string positions, and @graph nesting."""
    jsonld_html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@graph": [
        {"@type": "WebPage", "name": "Other Stuff"},
        {
          "@type": "BreadcrumbList",
          "itemListElement": [
            {"@type": "ListItem", "position": "3", "item": {"@id": "https://example.com/c", "name": "Deep Space"}},
            {"@type": "ListItem", "position": "1", "name": "Catalog"},
            {"@type": "ListItem", "position": "2", "item": {"name": "Sci-Fi"}}
          ]
        }
      ]
    }
    </script>
    """
    crumbs = extract_jsonld_breadcrumbs(jsonld_html)
    assert crumbs == ["Catalog", "Sci-Fi", "Deep Space"]


def test_extract_html_breadcrumbs_nav_and_microdata():
    """Extract breadcrumbs from semantic HTML nav and microdata markup."""
    html = """
    <html>
    <body>
      <nav aria-label="breadcrumb">
        <ol class="breadcrumb">
          <li class="breadcrumb-item"><a href="/">Home</a></li>
          <li class="breadcrumb-item"><a href="/nature">Nature</a></li>
          <li class="breadcrumb-item active" aria-current="page">Wildlife</li>
        </ol>
      </nav>
    </body>
    </html>
    """
    crumbs = extract_html_breadcrumbs(html)
    assert crumbs == ["Home", "Nature", "Wildlife"]
    assert extract_category_path(html) == "Nature/Wildlife"


def test_extract_html_breadcrumbs_microdata_itemprop():
    """Extract breadcrumbs from microdata itemprop markup."""
    html = """
    <div itemscope itemtype="http://schema.org/BreadcrumbList">
      <span itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem">
        <a itemprop="item" href="/"><span itemprop="name">Main</span></a>
      </span> &gt;
      <span itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem">
        <a itemprop="item" href="/tech"><span itemprop="name">Technology</span></a>
      </span> &gt;
      <span itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem">
        <span itemprop="name">Software</span>
      </span>
    </div>
    """
    crumbs = extract_html_breadcrumbs(html)
    assert crumbs == ["Main", "Technology", "Software"]
    assert extract_category_path(html) == "Technology/Software"


# ─── (2) Filesystem path sanitization ───────────


def test_sanitize_path_segment_forbidden_chars():
    """Strip or replace illegal filesystem characters."""
    raw = 'Category / Sub: "Wild*Life"? <Special> | 2026'
    sanitized = sanitize_path_segment(raw)
    assert "/" not in sanitized
    assert "\\" not in sanitized
    assert ":" not in sanitized
    assert "*" not in sanitized
    assert "?" not in sanitized
    assert '"' not in sanitized
    assert "<" not in sanitized
    assert ">" not in sanitized
    assert "|" not in sanitized


def test_sanitize_path_segment_windows_reserved_names():
    """Escape Windows reserved device names."""
    for reserved in ["CON", "PRN", "AUX", "NUL", "COM1", "LPT1"]:
        sanitized = sanitize_path_segment(reserved)
        assert sanitized != reserved
        assert not sanitized.upper().startswith(reserved + ".")


def test_sanitize_path_segment_traversal_and_dots():
    """Prevent directory traversal and trailing dots."""
    assert sanitize_path_segment("..") != ".."
    assert sanitize_path_segment("../..") != "../.."
    assert not sanitize_path_segment("Category...").endswith(".")


def test_breadcrumbs_to_path_depth_and_skip_root():
    """Validate breadcrumbs_to_path converts lists to clean hierarchical paths."""
    crumbs = ["Home", "Media", "Video", "Clips"]
    assert breadcrumbs_to_path(crumbs, skip_root=True) == "Media/Video/Clips"
    assert breadcrumbs_to_path(crumbs, skip_root=False) == "Home/Media/Video/Clips"
    assert breadcrumbs_to_path(crumbs, max_depth=2, skip_root=True) == "Media/Video"


# ─── (3) Fallback handling ───────────


def test_fallback_to_url_structure_when_html_has_no_breadcrumbs():
    """When HTML lacks breadcrumbs, fallback to URL path hierarchy."""
    html_without_crumbs = "<html><body><h1>Direct Video</h1></body></html>"
    url = "https://example.com/archive/interviews/2026/video-12345"
    path = extract_category_path(html_without_crumbs, url=url)
    assert path == "archive/interviews/2026"


def test_fallback_url_filters_noise_prefixes():
    """URL fallback drops noise prefixes like watch, video, and item."""
    url = "https://example.com/watch/documentaries/space/v9988"
    crumbs = extract_url_breadcrumbs(url)
    assert crumbs == ["documentaries", "space"]


def test_fallback_when_jsonld_is_malformed():
    """Malformed or invalid JSON-LD gracefully falls back to HTML or URL."""
    bad_jsonld_html = """
    <html>
    <head>
      <script type="application/ld+json">
        {malformed json syntax ...
      </script>
    </head>
    <body>
      <div class="breadcrumb">
        <a href="/">Start</a> &gt; <a href="/podcasts">Podcasts</a> &gt; <span>Interviews</span>
      </div>
    </body>
    </html>
    """
    # Does not raise JSONDecodeError; falls back to HTML breadcrumbs
    path = extract_category_path(bad_jsonld_html)
    assert path == "Podcasts/Interviews"


def test_empty_or_none_inputs_return_clean_fallback():
    """Empty or None inputs return default fallback without raising."""
    assert extract_category_path("", default="Uncategorized") == "Uncategorized"
    assert extract_category_path(None, default="Uncategorized") == "Uncategorized"
    assert extract_breadcrumbs("") == []
    assert extract_url_breadcrumbs("") == []
    assert breadcrumbs_to_path([]) == ""


def test_unicode_category_paths():
    """Support unicode characters and accented category directories."""
    crumbs = ["Accueil", "Émissions", "Cinéma & Séries", "日本語"]
    path = breadcrumbs_to_path(crumbs, skip_root=False)
    assert path == "Accueil/Émissions/Cinéma & Séries/日本語"


def test_breadcrumb_extractor_class_config():
    """BreadcrumbExtractor class respects custom configuration."""
    extractor = BreadcrumbExtractor(skip_root=False, max_depth=2, default="General")
    assert extractor.extract_path("") == "General"
    html = """
    <nav aria-label="breadcrumb">
      <ol>
        <li><a href="/">Home</a></li>
        <li><a href="/cat1">Category 1</a></li>
        <li><a href="/cat2">Category 2</a></li>
        <li><a href="/cat3">Category 3</a></li>
      </ol>
    </nav>
    """
    assert extractor.extract_path(html) == "Home/Category 1"
