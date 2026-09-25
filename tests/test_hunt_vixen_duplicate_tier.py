BD_GATE_SCOPE = "module"

from bulk_downloader.extractors_vixen import (
    _build_result_from_record,
    _pick_url_by_preference,
)

URL_1080 = "https://cdn.vixen.com/opaque/asset.mp4"
URL_720 = "https://cdn.vixen.com/video/mp4_720/other.mp4"


def _record(include_top_level):
    record = {"sources": [
        {"url": URL_1080, "resolution": 1080},
        {"url": URL_720, "resolution": 720},
    ]}
    if include_top_level:
        record["videoSrc"] = URL_1080
    return record


def test_duplicate_top_level_url_keeps_nested_resolution():
    control = _build_result_from_record(_record(False), "next_data", [1080, 720])
    assert control.url == URL_1080
    assert control.tier == 1080

    duplicate = _build_result_from_record(_record(True), "next_data", [1080, 720])
    assert duplicate.url == URL_1080
    assert duplicate.tier == 1080


def test_lower_tier_duplicate_does_not_discard_known_resolution():
    urls = [(URL_1080, 1080), (URL_1080, 0), (URL_720, 720)]
    assert _pick_url_by_preference(urls, [1080, 720]) == (URL_1080, 1080)
