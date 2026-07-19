"""Builder-side supplemental recognizers — corpus generalization (v3.66.197).

Repro-first, ALL SYNTHETIC fixtures (no real WACZ; real captures carry
F2-sensitive values — local-only on stash). These exercise the four signal-based
recognizers added to `build_template_from_wacz._supplemental_media_patterns` to
close the extraction-overfit gap (1/11 → ≥10/11 real sites). Each recognizer is
keyed on a site-agnostic signal, never on a project-original URL shape:

  R1  Content-Disposition: attachment  ⇒ download target (the strongest signal)
  R2  dedicated-download-host + ranged (206 / Content-Range) video/mp4
  R3  generalized resolution-from-filename (…{delim}{res}[pP]?…\\.mp4 beyond x{res})
  R4  HLS rendition derivation from .ts segment structure (not a contentless stub)

Invariants every recognizer must hold (signing-free / no-clobber):
  * emitted patterns are templated — no slug/name/query, no '://', no '?'
  * extraction_core stays byte-identical (consumed read-only)
  * api_patterns are NEVER fabricated for a direct-download site
  * core-handled URLs (VP9_/AVC_/.m3u8/.mpd) are not double-emitted
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))

import build_template_from_wacz as btw          # noqa: E402
from bulk_downloader import extraction_core as ec      # noqa: E402  (read-only)


def _resp(url, *, status=200, headers=None, method="GET"):
    """A network_log entry shaped like the real loader's. response_headers is a
    HAR-style list of {"name","value"} dicts (the shape the WACZ loader emits and
    extraction_core consumes), NOT a plain dict."""
    har = [{"name": k, "value": v} for k, v in (headers or {}).items()]
    return {
        "method": method,
        "url": url,
        "response_status": status,
        "response_headers": har,
    }


# ── R1: Content-Disposition: attachment ───────────────────────────────────────
DISPO_NET = [
    _resp("https://cdn-download.example.com/ASSET_af19_1080P.mp4",
          headers={"Content-Disposition": 'attachment; filename="movie.mp4"',
                   "Content-Type": "video/mp4"}),
    _resp("https://content2a.example-cdn.com/8e21bd.mp4",
          headers={"content-disposition": "attachment", "content-type": "video/mp4"}),
    # a non-attachment inline mp4 must NOT be flagged as a download target by R1
    _resp("https://static.example.com/preview.mp4",
          headers={"Content-Disposition": "inline", "Content-Type": "video/mp4"}),
]


def test_r1_attachment_flags_download_target():
    sup = btw._supplemental_media_patterns(DISPO_NET)
    sigs = sup.get("download_signals") or []
    assert "content-disposition" in sigs, f"attachment not flagged: {sup}"
    # both attachment URLs become media patterns; the inline one does not via R1
    assert sup["media_patterns"], "expected attachment downloads to yield patterns"


def test_r1_inline_disposition_not_treated_as_download():
    # only the two attachment hosts should be recorded as media hosts via R1
    sup = btw._supplemental_media_patterns([DISPO_NET[2]])  # inline-only
    assert "content-disposition" not in (sup.get("download_signals") or [])


def test_r1_patterns_are_templated_no_filename_value():
    sup = btw._supplemental_media_patterns(DISPO_NET)
    for pat in sup["media_patterns"]:
        assert "://" not in pat and "?" not in pat
        assert "movie.mp4" not in pat          # disposition filename value never leaks
        assert "8e21bd" not in pat             # no per-asset slug


# ── R2: dedicated-download-host + ranged video/mp4 ────────────────────────────
RANGED_NET = [
    _resp("https://wizkey-dl.com/clipname-1080p.mp4", status=206,
          headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-/1700000000"}),
    _resp("https://cdn-download.example.net/STREAM_5550.mp4", status=206,
          headers={"Content-Type": "video/mp4",
                   "Content-Range": "bytes 0-1048575/6000000000"}),
    # a 206 on a non-mp4 (e.g. a font) must not be treated as a media download
    _resp("https://fonts.example.com/icons.woff2", status=206,
          headers={"Content-Type": "font/woff2"}),
]


def test_r2_ranged_mp4_on_dl_host_flags_target():
    sup = btw._supplemental_media_patterns(RANGED_NET)
    sigs = sup.get("download_signals") or []
    assert "ranged-mp4" in sigs, f"ranged-mp4 not flagged: {sup}"
    assert "wizkey-dl.com" in sup["media_hosts"]
    assert "cdn-download.example.net" in sup["media_hosts"]


def test_r2_ranged_non_mp4_ignored():
    sup = btw._supplemental_media_patterns([RANGED_NET[2]])
    assert "ranged-mp4" not in (sup.get("download_signals") or [])
    assert "fonts.example.com" not in (sup.get("media_hosts") or [])


# ── R3: generalized resolution-from-filename (trailing p/P, varied delimiters) ─
RESNAME_NET = [
    {"method": "GET", "url": "https://cdn.example.com/VIXEN_5f1a_1080P.mp4"},
    {"method": "GET", "url": "https://cdn.example.com/clipname-720p.mp4"},
    {"method": "GET", "url": "https://cdn.example.com/stream_mp4_480.mp4"},
    {"method": "GET", "url": "https://cdn.example.com/AVC_1080.mp4"},   # core-handled → skip
    {"method": "GET", "url": "https://cdn.example.com/poster.jpg"},     # not media
]


def test_r3_core_derives_nothing_for_trailing_p_shapes():
    # extraction_core (frozen) does not recognize the {res}P / -{res}p shapes
    core = ec.network_patterns(RESNAME_NET)
    assert 1080 not in core["resolutions_seen"] or core["media_patterns"] == \
        [p for p in core["media_patterns"] if "AVC" in p or "VP9" in p]


def test_r3_supplemental_recognizes_trailing_p_resolutions():
    sup = btw._supplemental_media_patterns(RESNAME_NET)
    assert {1080, 720, 480} <= set(sup["resolutions"]), sup["resolutions"]
    assert sup["media_patterns"], "expected res-in-name patterns"


def test_r3_does_not_double_emit_core_avc():
    # the AVC_1080.mp4 entry is core-handled; supplemental must skip it
    sup = btw._supplemental_media_patterns([RESNAME_NET[3]])
    assert sup["media_patterns"] == []
    assert sup["resolutions"] == []


def test_r3_patterns_signing_free():
    sup = btw._supplemental_media_patterns(RESNAME_NET)
    for pat in sup["media_patterns"]:
        assert "{resolution}" in pat
        assert "://" not in pat and "?" not in pat
        assert "5f1a" not in pat               # no per-asset id leak


# ── R4: HLS rendition derivation from .ts segment structure ───────────────────
HLS_NET = [
    {"method": "GET", "url": "https://hls.example.com/path/title_1_1080p_00001.ts"},
    {"method": "GET", "url": "https://hls.example.com/path/title_1_1080p_00002.ts"},
    {"method": "GET", "url": "https://hls.example.com/path/title_1_720p_00001.ts"},
    {"method": "GET", "url": "https://hls2.example.com/priv/seg-12-v1-a1.ts"},
    {"method": "GET", "url": "https://hls.example.com/path/master.m3u8"},  # core stub → skip
]


def test_r4_emits_ts_rendition_pattern_not_just_manifest():
    sup = btw._supplemental_media_patterns(HLS_NET)
    ts_pats = [p for p in sup["media_patterns"] if p.endswith(".ts")]
    assert ts_pats, f"expected a .ts rendition pattern, got {sup['media_patterns']}"


def test_r4_ts_pattern_is_templated():
    sup = btw._supplemental_media_patterns(HLS_NET)
    for pat in (p for p in sup["media_patterns"] if p.endswith(".ts")):
        assert "://" not in pat and "?" not in pat
        # variable numeric/id segments must be parametrized, not literal
        assert "00001" not in pat and "seg-12" not in pat
        assert "{" in pat and "}" in pat


def test_r4_resolution_in_ts_path_recorded():
    sup = btw._supplemental_media_patterns(HLS_NET)
    assert {1080, 720} <= set(sup["resolutions"]), sup["resolutions"]


def test_r4_m3u8_left_to_core():
    # the master .m3u8 is core's (contentless stub); supplemental does not emit one
    sup = btw._supplemental_media_patterns([HLS_NET[4]])
    assert not any(p.endswith(".m3u8") for p in sup["media_patterns"])


# ── cross-cutting: no API invented; merge unions, never clobbers ──────────────
def test_no_api_fabricated_across_all_recognizers():
    for net in (DISPO_NET, RANGED_NET, RESNAME_NET, HLS_NET):
        core = ec.network_patterns(net)
        merged = btw._merge_supplemental_media(core, net)
        assert merged["api_patterns"] == [], f"api fabricated for {net[0]['url']}"
        assert merged["observed_api_hosts"] == []


def test_merge_unions_supplemental_media_into_network_discovery():
    core = ec.network_patterns(HLS_NET)
    before = set(core["media_patterns"])
    merged = btw._merge_supplemental_media(ec.network_patterns(HLS_NET), HLS_NET)
    after = set(merged["media_patterns"])
    assert before <= after, "merge must union, not drop core patterns"
    assert any(p.endswith(".ts") for p in after), "ts rendition not merged in"
