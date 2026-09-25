BD_GATE_SCOPE = "module"

from bulk_downloader.extractors_aylo import pick_best_variant
from bulk_downloader.extractors_jsonapi import JsonApiSource, pick_source


def test_jsonapi_best_keyword_respects_earlier_quality_preference():
    sources = [
        JsonApiSource(url="https://example.test/720.mp4", height=720),
        JsonApiSource(url="https://example.test/1080.mp4", height=1080),
    ]

    assert pick_source(sources, [720, "best"]).height == 720
    assert pick_source(sources, ["best", 720]).height == 1080
    assert pick_source(sources, [480, "best"]).height == 1080


def test_aylo_best_keyword_respects_earlier_quality_preference():
    variants = [
        {"videoUrl": "https://example.test/720.mp4", "quality": 720},
        {"videoUrl": "https://example.test/1080.mp4", "quality": 1080},
    ]

    assert pick_best_variant(variants, [720, "best"]).quality == 720
    assert pick_best_variant(variants, ["best", 720]).quality == 1080
    assert pick_best_variant(variants, [480, "best"]).quality == 1080
