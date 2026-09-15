"""Independent row719 corpus replay and navigation-query adversaries."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from bulk_downloader.playlist_extractor import _looks_like_scene_url as scene

BD_GATE_SCOPE = "module"
FIXTURES = Path(__file__).parent / "fixtures"


def test_captured_population_selects_exactly_seventeen_films():
    report = json.loads((FIXTURES / "row719_scene_classifier" /
                        "wowgirls_fresh_20260904T1430Z_discovery_report.json").read_text())
    selected = [entry["url"] for entry in report["selected"]]
    assert report["link_population"] == 482 and len(set(selected)) == len(selected) == 159
    films = [u for u in selected if re.search(r"/film/[^/]+/[^/]+$", u)]
    masked = [u for u, _ in report["top_link_shapes"] if re.search(r"/film/[^/]+/[^/]+$", u)]
    assert len(films) == 16 and len(masked) == 1 and len(set(films + masked)) == 17
    assert sum(scene(u) for u in films + masked) == 17
    assert sum(scene(u) for u in selected) == 16
    assert len([u for u in selected if not scene(u)]) == 143


def test_ultrafilms_corpus_and_three_item_identifier_classes():
    html = (FIXTURES / "row374_scene_crawler" / "ultrafilms.html").read_text()
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert len(hrefs) == 5
    items = [u for u in hrefs if "/content/item/" in u]
    assert len(items) == 4
    items += ["/members/content/item/" + suffix for suffix in
              ("1234-scene-title", "82144c7c-scene-title", "scene-title")]
    assert len(items) == 7
    assert sum(scene("https://example.test" + u) for u in items) == 7
    assert not scene("https://example.test/account/logout")


def test_template_extension_preserves_four_listing_controls_and_regex_queries():
    custom = "https://second.example/screening/123/title"
    template = {"scene_url_hints": [" /SCREENING/ ", "/videos/"]}
    assert not scene(custom) and scene(custom, template=template)
    controls = ["/en/videos/" + tail for tail in
                ("sort/latest", "sort/latest/page/2", "page/3", "page/337")]
    assert len(controls) == 4
    assert sum(scene("https://second.example" + u, template=template) for u in controls) == 0
    query_template = {"url_patterns": [r"gallery\.php\?id=[0-9]+&type=vids"]}
    assert scene("https://second.example/gallery.php?id=123&type=vids", template=query_template)
    assert not scene("https://second.example/gallery.php?id=123&type=photos", template=query_template)
    assert scene("https://second.example/watch?v=123")


@pytest.mark.parametrize("path", ["/my/activity", "/my/favorites", "/my/comments",
                                  "/members/home/whatsnew", "/members/content/",
                                  "/members/content/items/"])
def test_navigation_does_not_become_scene_from_query_value(path):
    navigation = "https://example.test" + path
    target = "/film/123/scene-title"
    assert scene("https://example.test" + target) and not scene(navigation)
    spoofed = navigation + "?return_to=" + target
    assert urlparse(spoofed).path == path and urlparse(spoofed).query
    assert not scene(spoofed), f"navigation query minted a scene: {spoofed}"
