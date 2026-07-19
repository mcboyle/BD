"""v3.66.14 — P9 (confidence ceilings) + P17 (structured disclaimers).

Both reshape `deep_detect()`'s output schema. P9 adds
`confidence_ceiling` (float) + `ceiling_reasons` (list of strings) to
each download candidate. P17 adds top-level `disclaimers` (list of
`{type, severity, message}` dicts) alongside the legacy `warnings`
string list.

Tests cover:

  P9 — ceiling table well-formed, strictest-wins composition, every
       known signal lowers the right candidates, no signals → 1.0.

  P17 — every documented rule routes to the right type/severity,
        unmapped warnings fall through to `unclassified`/`info`,
        `disclaimers` is present on every report-emit path,
        `warnings` is preserved verbatim (backwards-compat).
"""
import pytest

from bulk_downloader.deep_detect import (
    _CEILINGS,
    _DISCLAIMER_RULES,
    _attach_confidence_ceiling,
    _build_disclaimers,
    _classify_disclaimer,
    _detect_ceiling_signals,
    deep_detect,
)

pytestmark = pytest.mark.bd_module_wipe


# ═══════════════════════════════════════════════════════════════════════
# P9 — confidence ceilings
# ═══════════════════════════════════════════════════════════════════════

# ── ceiling table sanity ─────────────────────────────────────────────


def test_ceilings_table_well_formed():
    """Every entry is (float in [0,1], non-empty str). Tightest gates
    have the lowest ceiling, less-tight gates higher."""
    for key, (ceiling, msg) in _CEILINGS.items():
        assert isinstance(ceiling, float)
        assert 0.0 <= ceiling <= 1.0
        assert isinstance(msg, str) and msg.strip()
    # Sanity: DRM is the tightest (bytes unreachable); needs_workflow
    # is looser (it'll resolve, just multi-step).
    assert _CEILINGS["drm"][0] < _CEILINGS["needs_workflow"][0]
    assert _CEILINGS["trap"][0] < _CEILINGS["captcha"][0]


# ── _detect_ceiling_signals: each signal recognized ─────────────────


@pytest.mark.parametrize("warning,expected_signal", [
    ("HLS media playlist is encrypted, do not bypass", "drm"),
    ("page has CAPTCHA; this candidate goes through the UI", "captcha"),
    ("URL matches a trap-link signature", "trap"),
    ("page-level DRM/encryption markers detected", "drm"),
    ("ContentProtection element found", "drm"),
])
def test_signal_detected_from_warning_text(warning, expected_signal):
    c = {"warnings": [warning]}
    signals = _detect_ceiling_signals(c)
    assert expected_signal in signals


def test_signed_url_flag_triggers_signal():
    c = {"warnings": [], "signed_url": {"scheme": "cloudfront"}}
    assert "signed_url" in _detect_ceiling_signals(c)


def test_needs_provider_resolution_triggers_signal():
    c = {"warnings": [], "needs_provider_resolution": True}
    assert "needs_provider" in _detect_ceiling_signals(c)


def test_needs_workflow_triggers_signal():
    c = {"warnings": [], "needs_workflow": True}
    assert "needs_workflow" in _detect_ceiling_signals(c)


def test_no_signals_on_clean_candidate():
    c = {"warnings": [], "url": "https://x.example/a.mp4",
         "source_type": "direct_file"}
    assert _detect_ceiling_signals(c) == []


# ── _attach_confidence_ceiling: composition ──────────────────────────


def test_clean_candidate_gets_ceiling_1():
    cands = [{"warnings": [], "url": "https://x.example/a.mp4",
              "source_type": "direct_file"}]
    _attach_confidence_ceiling(cands)
    assert cands[0]["confidence_ceiling"] == 1.0
    assert cands[0]["ceiling_reasons"] == []


def test_single_signal_uses_that_ceiling():
    cands = [{"warnings": ["HLS encryption detected"]}]
    _attach_confidence_ceiling(cands)
    assert cands[0]["confidence_ceiling"] == _CEILINGS["drm"][0]
    # Reason is the table's message verbatim
    assert cands[0]["ceiling_reasons"] == [_CEILINGS["drm"][1]]


def test_strictest_signal_wins_when_multiple():
    """A candidate with both DRM and captcha signals: ceiling = min."""
    cands = [{"warnings": ["HLS encryption detected",
                           "page has CAPTCHA"]}]
    _attach_confidence_ceiling(cands)
    expected = min(_CEILINGS["drm"][0], _CEILINGS["captcha"][0])
    assert cands[0]["confidence_ceiling"] == expected
    # Both reasons surfaced
    assert len(cands[0]["ceiling_reasons"]) == 2


def test_attach_returns_same_list():
    cands = [{"warnings": []}]
    out = _attach_confidence_ceiling(cands)
    assert out is cands


# ── integration: every report path attaches ceiling ─────────────────


HLS_DRM = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-KEY:METHOD=AES-128,URI="https://example/key"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
720p.m3u8
"""


HLS_CLEAN = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
720p.m3u8
"""


def test_drm_hls_input_lowers_ceiling():
    r = deep_detect(HLS_DRM, base_url="https://x.example/m.m3u8")
    c = r["buckets"]["accepted"][0]
    assert c["confidence_ceiling"] == _CEILINGS["drm"][0]
    assert any("DRM" in reason or "encryption" in reason
               for reason in c["ceiling_reasons"])


def test_clean_hls_input_keeps_ceiling_at_1():
    r = deep_detect(HLS_CLEAN, base_url="https://x.example/m.m3u8")
    c = r["buckets"]["accepted"][0]
    assert c["confidence_ceiling"] == 1.0
    assert c["ceiling_reasons"] == []


def test_html_path_attaches_ceiling():
    page = ('<!doctype html><html><body>'
            '<script id="__NEXT_DATA__" type="application/json">'
            '{"source":{"hls":"https://cdn.example.test/master.m3u8"}}'
            '</script></body></html>')
    r = deep_detect(page, base_url="https://example.test/p")
    assert r["buckets"]["accepted"], "expected state-blob candidate"
    for c in r["buckets"]["accepted"]:
        assert "confidence_ceiling" in c
        assert "ceiling_reasons" in c


# ═══════════════════════════════════════════════════════════════════════
# P17 — structured disclaimers
# ═══════════════════════════════════════════════════════════════════════

# ── classifier: every documented rule resolves correctly ─────────────


def test_disclaimer_rules_table_well_formed():
    for needle, dtype, severity in _DISCLAIMER_RULES:
        assert isinstance(needle, str) and needle == needle.lower(), (
            f"needle {needle!r} should be lowercase for the matching "
            f"loop to work uniformly")
        assert isinstance(dtype, str) and dtype
        assert severity in ("error", "warn", "info")


@pytest.mark.parametrize("message,expected_type,expected_severity", [
    ("HLS encryption detected; do not bypass", "drm", "error"),
    ("page-level DRM markers found", "drm", "warn"),
    ("ContentProtection element detected", "drm", "error"),
    ("page has CAPTCHA; click flow required", "captcha", "warn"),
    ("bot defense present on best login candidate", "bot_defense", "warn"),
    ("URL matches a trap-link signature", "trap", "warn"),
    ("signed URL with short TTL", "signed_url", "warn"),
    ("URL has expired signature", "signed_url", "error"),
    ("MPD XML parse failed: bad token at line 3", "parse_error", "warn"),
    ("honeypot field detected in login form", "honeypot", "warn"),
])
def test_classify_disclaimer_documented_routes(
        message, expected_type, expected_severity):
    out = _classify_disclaimer(message)
    assert out["type"] == expected_type
    assert out["severity"] == expected_severity
    # Original message preserved
    assert out["message"] == message


def test_unmapped_message_falls_through_to_unclassified():
    """Unknown warning text doesn't get dropped — falls through with
    a sentinel type so consumers can still surface it."""
    out = _classify_disclaimer("a totally novel warning we've never seen")
    assert out["type"] == "unclassified"
    assert out["severity"] == "info"
    assert out["message"] == "a totally novel warning we've never seen"


def test_classify_disclaimer_case_insensitive():
    out = _classify_disclaimer("HLS ENCRYPTION DETECTED")
    assert out["type"] == "drm"


def test_classify_disclaimer_empty_input():
    """Empty/None message → unclassified/info, message stays as given.
    Don't raise."""
    out = _classify_disclaimer("")
    assert out == {"type": "unclassified",
                   "severity": "info", "message": ""}


# ── _build_disclaimers: preserves ordering and count ────────────────


def test_build_disclaimers_preserves_order():
    report = {
        "warnings": [
            "HLS encryption detected",
            "page has CAPTCHA",
            "a novel warning",
        ],
    }
    out = _build_disclaimers(report)
    assert [d["type"] for d in out] == ["drm", "captcha", "unclassified"]


def test_build_disclaimers_empty_warnings():
    assert _build_disclaimers({"warnings": []}) == []
    assert _build_disclaimers({}) == []  # missing key tolerated


# ── integration: disclaimers attached at every emit path ────────────


def test_hls_path_emits_disclaimers():
    r = deep_detect(HLS_DRM, base_url="https://x.example/m.m3u8")
    assert "disclaimers" in r
    # DRM should have produced at least one error-severity entry
    drm_entries = [d for d in r["disclaimers"] if d["type"] == "drm"]
    assert drm_entries
    assert any(d["severity"] == "error" for d in drm_entries)
    # Backwards-compat: warnings (legacy) still present and not empty
    assert r["buckets"]["warnings"]


def test_dash_path_emits_disclaimers():
    mpd = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">
  <Period>
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <Representation id="v" bandwidth="2000000" width="1280" height="720">
        <BaseURL>v720.mp4</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""
    r = deep_detect(mpd, base_url="https://x.example/m.mpd")
    assert "disclaimers" in r
    assert isinstance(r["disclaimers"], list)


def test_html_path_emits_disclaimers():
    page = ('<!doctype html><html><body>'
            '<script id="__NEXT_DATA__" type="application/json">'
            '{"source":{"hls":"https://cdn.example.test/master.m3u8"}}'
            '</script></body></html>')
    r = deep_detect(page, base_url="https://example.test/p")
    assert "disclaimers" in r
    assert isinstance(r["disclaimers"], list)


def test_warnings_preserved_alongside_disclaimers():
    """Legacy string-list `warnings` is preserved verbatim; new
    `disclaimers` is added alongside. Downstream code reading only
    `warnings` keeps working."""
    r = deep_detect(HLS_DRM, base_url="https://x.example/m.m3u8")
    assert isinstance(r["buckets"]["warnings"], list)
    assert all(isinstance(w, str) for w in r["buckets"]["warnings"])
    # disclaimers count matches warnings count
    assert len(r["disclaimers"]) == len(r["buckets"]["warnings"])
    # Each disclaimer's message matches the corresponding warning
    for w, d in zip(r["buckets"]["warnings"], r["disclaimers"]):
        assert d["message"] == w
