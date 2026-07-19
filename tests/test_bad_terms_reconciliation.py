"""BAD_TERMS reconciliation — parity tests (zero-arg, custom-runner style).

Verifies:
  * one authoritative BAD_TERMS source (bulk_downloader.bad_terms) is the object
    used by the promote gate AND the inventory diagnostic;
  * the pattern scrubber drops everything the promote gate would reject
    (scrubber ⊇ gate), including the reconciled cdnjs / cloudflare cases;
  * a Reptyle-like polluted pattern set scrubs/normalizes correctly;
  * legitimate API/media patterns (api2.reptyle.com-style, .m3u8, AVC_<h>.mp4)
    survive.
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

from bulk_downloader import bad_terms as BT
from bulk_downloader.pattern_hygiene import scrub_network_patterns
from bulk_downloader.template_normalize import normalize_draft
import promote_template as PT
import template_inventory as TI


def _gate_rejects(patterns, api_values=None):
    """Replicate the promote gate's blocked-term check exactly."""
    api_values = api_values or []
    lint_text = "\n".join([str(x) for x in patterns] + [str(v) for v in api_values]).lower()
    return any(bad.lower() in lint_text for bad in PT.BAD_TERMS)


# ── single source of truth ────────────────────────────────────────
def test_promote_uses_shared_source():
    assert PT.BAD_TERMS is BT.BAD_TERMS, "promote gate must use the shared BAD_TERMS object"


def test_inventory_uses_shared_source():
    assert TI.BAD_TERMS is BT.BAD_TERMS, "inventory must use the shared BAD_TERMS object"


def test_inventory_gate_parity():
    # same list (and, here, the same object) → no divergence possible
    assert TI.BAD_TERMS == PT.BAD_TERMS


def test_no_duplicate_literal_in_consumers():
    # The canonical list lives only in bad_terms.py; consumers import it.
    promote_src = (_ROOT / "tools" / "promote_template.py").read_text()
    inv_src = (_ROOT / "tools" / "template_inventory.py").read_text()
    # the distinctive trailing token of the literal must not be redefined inline
    assert promote_src.count('"active-subscriptions"') == 0, "promote must not inline the list"
    assert inv_src.count('"active-subscriptions"') == 0, "inventory must not inline the list"


# ── scrubber ⊇ gate (parity) ──────────────────────────────────────
def _sample_pattern_for(term):
    """A realistic pattern embedding a blocked term."""
    if term.endswith("="):  # query-key marker like token= / sig=
        return f"https://media.example.com/file.mp4?{term}abc123"
    if "-" in term or term.isalpha():  # host-ish term
        return f"https://x.{term}.example.com/asset.js"
    return f"https://example.com/{term}/x"


def test_scrubber_is_superset_of_gate():
    for term in BT.BAD_TERMS:
        p = _sample_pattern_for(term)
        # the gate would reject it ...
        assert _gate_rejects([p]), f"sanity: gate should reject {p!r}"
        # ... so the scrubber must drop it
        res = scrub_network_patterns([p])
        assert res["kept"] == [], f"scrubber kept a gate-rejected pattern for term {term!r}: {p!r}"
        assert p in res["dropped"]


def test_cdnjs_cloudflare_reconciled():
    polluted = [
        "https://cdnjs.cloudflare.com/ajax/libs/video.js/5.19.2/video.js",
        "https://cdnjs.cloudflare.com/ajax/libs/video.js/5.19.2/alt/video-js-cdn.css",
    ]
    res = scrub_network_patterns(polluted)
    assert res["kept"] == [], "cdnjs/cloudflare must be dropped (gate parity)"
    # and the gate would indeed have rejected them
    assert _gate_rejects(polluted)


# ── Reptyle-like polluted set ─────────────────────────────────────
_REPTYLE_POLLUTED = [
    "https://o851585.ingest.sentry.io/api/123/envelope/",
    "https://cdnjs.cloudflare.com/ajax/libs/video.js/5.19.2/video.js",
    "https://tsyndicate.com/api/v1/retargeting/set/abc",
    "https://www.googletagmanager.com/gtm.js?id=GTM-MGGX9WK",
    "https://www.google-analytics.com/analytics.js",
    "<!DOCTYPE html><html>inlined blob</html>",            # junk
    "function trafficStarsLoader(b){var a=document;}",      # junk (whitespace)
    # legitimate survivors:
    "https://api2.reptyle.com/api/v1/movie/32088/download-resolution/1080",
    "https://stream.reptyle.com/media/AVC_1080.mp4",
    "https://stream.reptyle.com/media/master.m3u8",
]


def test_reptyle_polluted_set_scrubs_correctly():
    res = scrub_network_patterns(_REPTYLE_POLLUTED)
    kept = res["kept"]
    # survivors
    assert "https://api2.reptyle.com/api/v1/movie/32088/download-resolution/1080" in kept
    assert "https://stream.reptyle.com/media/AVC_1080.mp4" in kept
    assert "https://stream.reptyle.com/media/master.m3u8" in kept
    # pollutants gone
    assert not any(BT.first_bad_term(p) for p in kept), "no kept pattern may contain a blocked term"
    for bad_host in ("sentry", "cloudflare", "cdnjs", "tsyndicate", "googletagmanager", "google-analytics"):
        assert not any(bad_host in p for p in kept), f"{bad_host} survived scrub"
    # and crucially: a candidate built from `kept` passes the gate's blocked-term check
    assert not _gate_rejects(kept)


def test_valid_api_media_survive():
    valid = [
        "https://api2.reptyle.com/api/v1/movie/{movie_id}/download-resolution/{resolution}",
        "https://stream.reptyle.com/AVC_1080.mp4",
        "https://stream.reptyle.com/VP9_2160.mp4",
        "https://stream.reptyle.com/master.m3u8",
        "https://stream.reptyle.com/manifest.mpd",
    ]
    res = scrub_network_patterns(valid)
    assert res["dropped"] == [], f"dropped legitimate patterns: {res['dropped']}"
    assert res["kept"] == valid


# ── full normalize integration ────────────────────────────────────
def test_normalized_candidate_has_no_blocked_terms():
    # A flat draft routes its top-level network_patterns through the scrubber
    # (the rich-draft path sources builder-curated nd.* patterns instead).
    draft = {
        "host": "app.reptyle.com",
        "network_patterns": list(_REPTYLE_POLLUTED),
        "selectors": {"download": {"trigger": "button[data-tooltip=\"Download\"]"}},
        "resolutions": [1080, 720, 540],
        "match": {"hosts": ["app.reptyle.com"], "url_patterns": []},
    }
    cand = normalize_draft(draft)
    nps = cand.get("network_patterns") or []
    assert nps, "candidate should retain at least the legitimate patterns"
    assert not any(BT.first_bad_term(p) for p in nps), \
        f"normalized candidate carries a blocked term: {nps}"
    # gate blocked-term check passes on the normalized patterns
    assert not _gate_rejects(nps)
