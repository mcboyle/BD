"""Builder-side recognition for direct-stream MP4 sites + reptyle modal/api
metadata — all SYNTHETIC fixtures (no real WACZ; those carry F2-sensitive
values). Verifies:

  * wowgirls-shaped direct renditions (.../{name}x{res}_{variant}.mp4) now yield
    media patterns + resolutions, with NO API invented;
  * extraction_core's reptyle recognition is unchanged (it is frozen / byte-
    identical — these tests only consume it);
  * reptyle modal rows in the rrweb dom_log produce modal-scoped, lint-safe row
    candidates; the observed API host is surfaced as review-only metadata and is
    NOT forced into the runtime api base.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))

import build_template_from_wacz as btw          # noqa: E402
from bulk_downloader import extraction_core as ec      # noqa: E402  (consumed read-only)
from bulk_downloader import template_normalize as tn    # noqa: E402
from bulk_downloader import selector_lint as sl         # noqa: E402

# ── synthetic network logs (shaped like the observed URLs, never the real ones) ──
WOW_NET = [
    {"method": "GET", "url": "https://content-video2.wowgirls.com/stream/jab10e6f/clip01x2160_S.mp4"},
    {"method": "GET", "url": "https://content-video2.wowgirls.com/stream/we8e9259/clip01x1080_30FPS.mp4"},
    {"method": "GET", "url": "https://content-video2.wowgirls.com/stream/yf0dff34/clip01x720_30FPS.mp4"},
    {"method": "GET", "url": "https://content-vthumbs2.wowgirls.com/thumb/abc.jpg"},
    {"method": "GET", "url": "https://venus.wowgirls.com/film/sf0dbf9a"},
]
REP_NET = [
    {"method": "GET", "url": "https://api2.reptyle.com/api/v1/movie/123/download-resolution/1080"},
    {"method": "GET", "url": "https://vod1.cachefly.net/SIGNED==/vod/x/videos/full/VP9_ABR/VP9_2160.mp4"},
    {"method": "GET", "url": "https://vod1.cachefly.net/SIGNED==/vod/x/videos/full/VP9_ABR/VP9_1080.mp4"},
]


# ── WOWGIRLS: before (core empty) → after (supplemental derives) ──────────────
def test_wowgirls_core_derives_nothing_before():
    core = ec.network_patterns(WOW_NET)
    assert core["media_patterns"] == []
    assert core["resolutions_seen"] == []
    assert core["api_patterns"] == []
    assert core["observed_api_hosts"] == []


def test_wowgirls_supplemental_recognizes_renditions():
    sup = btw._supplemental_media_patterns(WOW_NET)
    assert set(sup["resolutions"]) >= {2160, 1080, 720}
    assert sup["media_patterns"], "expected non-empty media patterns"
    assert "content-video2.wowgirls.com" in sup["media_hosts"]
    assert {"S", "30FPS"} <= set(sup["media_variants"])


def test_wowgirls_merged_populates_media_and_resolutions_no_api():
    core = ec.network_patterns(WOW_NET)
    merged = btw._merge_supplemental_media(core, WOW_NET)
    assert merged["media_patterns"], "media patterns must not be empty after merge"
    assert set(merged["resolutions_seen"]) >= {2160, 1080, 720}
    # No API may be invented for a direct-stream site.
    assert merged["api_patterns"] == []
    assert merged["observed_api_hosts"] == []


def test_wowgirls_patterns_store_no_secrets_or_signed_urls():
    sup = btw._supplemental_media_patterns(WOW_NET)
    for pat in sup["media_patterns"]:
        assert "{resolution}" in pat            # templated, not a literal URL
        assert "?" not in pat and "://" not in pat
        for slug in ("jab10e6f", "we8e9259", "yf0dff34", "sf0dbf9a"):
            assert slug not in pat              # no per-asset slug leaks


# ── REPTYLE: extraction_core behavior preserved; supplemental is a no-op ──────
def test_reptyle_core_download_resolution_preserved():
    core = ec.network_patterns(REP_NET)
    assert "/api/v{version}/movie/{movie_id}/download-resolution/{resolution}" in core["api_patterns"]
    assert ".../VP9_{resolution}.mp4" in core["media_patterns"]
    assert set(core["resolutions_seen"]) >= {2160, 1080}
    assert core["observed_api_hosts"] == ["api2.reptyle.com"]


def test_reptyle_supplemental_is_noop_does_not_duplicate_core_media():
    core = ec.network_patterns(REP_NET)
    before = sorted(core["media_patterns"])
    merged = btw._merge_supplemental_media(ec.network_patterns(REP_NET), REP_NET)
    assert sorted(merged["media_patterns"]) == before          # VP9_ already covered → skipped
    assert merged["api_patterns"] == core["api_patterns"]      # API untouched
    assert not any(p.startswith(".../x{resolution}") for p in merged["media_patterns"])


# ── REPTYLE: modal-scoped row selectors mined from the rrweb dom_log ──────────
def _li(role="menuitem"):
    return {"tagName": "li", "attributes": {"role": role}, "childNodes": []}


REP_MODAL_DOM = [{
    "type": "full_snapshot",
    "data": {"node": {
        "tagName": "div",
        "attributes": {"role": "dialog", "class": "ant-modal download-modal"},
        "childNodes": [{
            "tagName": "ul",
            "attributes": {"role": "menu"},
            "childNodes": [_li(), _li(), _li()],   # three resolution rows
        }],
    }},
}]


def test_modal_rows_are_modal_scoped_and_lint_safe():
    rows = btw._modal_row_selectors_from_dom(REP_MODAL_DOM)
    assert rows, "expected at least one modal-scoped row candidate"
    for sel in rows:
        assert tn._is_modal_scoped(sel), f"{sel!r} is not modal-scoped"
        issues = sl.lint_selector(sel, role="row")
        assert not sl.has_blocking_issues(issues), f"{sel!r} has blocking lint: {issues}"
    assert '[role="dialog"] li[role="menuitem"]' in rows


def test_modal_rows_require_repetition_no_oneoff():
    # a modal with a single non-repeating row shape yields nothing
    single = [{"type": "full_snapshot", "data": {"node": {
        "tagName": "div", "attributes": {"role": "dialog"},
        "childNodes": [_li()]}}}]
    assert btw._modal_row_selectors_from_dom(single) == []


# ── REPTYLE: normalizer surfaces observed API host as review-only metadata ────
REP_DRAFT = {
    "source": {"host": "app.reptyle.com"},
    "match": {"hosts": ["app.reptyle.com"]},
    "network_discovery": {
        "api_patterns": ["/api/v{version}/movie/{movie_id}/download-resolution/{resolution}"],
        "observed_api_hosts": ["api2.reptyle.com"],
        "media_patterns": [".../VP9_{resolution}.mp4"],
        "resolutions_seen": [2160, 1080],
    },
    "selectors": {"download": {
        "button_hint": '[aria-label*="Download" i]',
        "row_selectors": ['[role="dialog"] li[role="menuitem"]'],
    }},
}


def test_normalizer_surfaces_observed_host_review_only():
    cand = tn.normalize_draft(REP_DRAFT)
    assert cand["observed_api_hosts"] == ["api2.reptyle.com"]
    assert cand["api_base_candidate"] == "https://api2.reptyle.com"
    # the modal-scoped row survives the safety gate
    assert '[role="dialog"] li[role="menuitem"]' in cand["selectors"]["download"]["row_selectors"]
    # API host is NOT forced: patterns stay relative, not host-bearing.
    assert "/api/v{version}/movie/{movie_id}/download-resolution/{resolution}" in cand["network_patterns"]
    assert not any(str(p).startswith("https://api2.reptyle.com") for p in cand["network_patterns"])


def test_normalizer_directstream_has_no_api_candidate():
    wow_draft = {
        "source": {"host": "venus.wowgirls.com"},
        "match": {"hosts": ["venus.wowgirls.com"]},
        "network_discovery": {
            "api_patterns": [],
            "observed_api_hosts": [],
            "media_patterns": [".../x{resolution}_{variant}.mp4"],
            "media_hosts": ["content-video2.wowgirls.com"],
            "resolutions_seen": [2160, 1080, 720],
        },
        "selectors": {"player": {"container": ".video-js"}},
    }
    cand = tn.normalize_draft(wow_draft)
    assert cand["api_base_candidate"] is None
    assert cand["observed_api_hosts"] == []
    assert set(cand["resolutions"]) >= {2160, 1080, 720}
    assert any("x{resolution}" in p for p in cand["network_patterns"])
