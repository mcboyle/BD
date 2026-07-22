"""v3.43.44 regression tests — heuristic scoring improvements.

Coverage:
  - Tiered resolution scoring (A): 8K > 4K > 1080p > ...
  - Filesize parsing + scoring (B)
  - Ancestor context (C): positive + negative ancestor classes
  - URL pattern fingerprinting (D): host match + path-prefix match
  - Position/shape (E): above-fold, button-sized, deep-in-page
  - Combinatorial download+resolution pair (F)
  - Expanded negatives (G), incl. trailer-suppression-unless-4K+ exception
  - rank_candidates: filters by min_score, sorts correctly
  - update_fingerprint_from_success: dedups + caps at 20
  - Defensive: handles None, non-string inputs everywhere
  - JS port: learn.py contains the new tiered scoring code
  - Runner integration: _update_job fires fingerprint update on done
  - CFG wiring: url_fingerprint in fields + defaults
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HS_PY = _REPO_ROOT / "bulk_downloader" / "heuristic_scoring.py"
_LEARN_PY = _REPO_ROOT / "bulk_downloader" / "learn.py"


def _learn_impl_src():
    """Concatenated source of the decomposed learn_impl/ package. learn.py is now an
    ADD-only re-export shim (DECOMP-LEAF cut 5); RECORDER_JS/TEACH_OVERLAY_JS and the
    function bodies live in learn_impl/*.py, so source/structure tests read the package."""
    from pathlib import Path as _P
    import bulk_downloader.learn_impl as _pkg
    _d = _P(_pkg.__file__).parent
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_d.glob("*.py")))


class _AggregateSrc:
    """runner.py + every runner_*.py mixin module, concatenated. The PHASE 3
    decomposition (v3.66.397+) moved SiteRunner methods (and the
    _ManualDownloadSession class @399, which installs the teach overlay) into
    sibling modules; these are FLOOR (>=) literal-count guards, so reading the
    aggregate keeps every marker findable regardless of which module owns it."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "runner.py"] + sorted(pkg_dir.glob("runner_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)


_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader")
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"


# ── Module surface ───────────────────────────────────────────────────



def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_module_importable():
    import bulk_downloader.heuristic_scoring  # noqa: F401


def test_required_exports():
    from bulk_downloader import heuristic_scoring as hs
    for name in (
        "RESOLUTION_TIERS", "detect_resolution_tier",
        "parse_filesize_bytes", "score_filesize",
        "score_ancestor_context", "score_url_fingerprint",
        "score_position_shape", "score_text_signals",
        "score_candidate", "rank_candidates",
        "update_fingerprint_from_success",
    ):
        assert hasattr(hs, name), f"missing export: {name}"


# ── (A) Tiered resolution scoring ────────────────────────────────────


def test_tiered_resolution_8k_beats_1080():
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    tier_8k, label_8k = detect_resolution_tier("Download 8K Ultra HD")
    tier_1080, label_1080 = detect_resolution_tier("Download 1080p HD")
    assert tier_8k > tier_1080
    assert label_8k == "8K"
    assert label_1080 == "1080p"


def test_tiered_resolution_returns_highest_when_multiple():
    """Old: any res mention = +40. New: highest tier wins."""
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    # Page lists multiple options; we want 4K not 720p
    tier, label = detect_resolution_tier("4K / 1080p / 720p")
    assert label == "4K"


def test_tiered_resolution_recognizes_dimension_notation():
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    assert detect_resolution_tier("3840x2160")[1] == "4K"
    assert detect_resolution_tier("1920×1080")[1] == "1080p"
    assert detect_resolution_tier("7680 x 4320")[1] == "8K"


def test_tiered_resolution_no_match_returns_zero():
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    tier, label = detect_resolution_tier("just some random text")
    assert tier == 0
    assert label == ""


def test_tiered_resolution_defensive():
    from bulk_downloader.heuristic_scoring import detect_resolution_tier
    for bad in (None, 12345, [], {}):
        tier, label = detect_resolution_tier(bad)
        assert tier == 0


# ── (B) Filesize parsing + scoring ───────────────────────────────────


def test_parse_filesize_gb():
    from bulk_downloader.heuristic_scoring import parse_filesize_bytes
    n = parse_filesize_bytes("Download MP4 4K (5.7 GB)")
    # 5.7 GB ≈ 6.12e9 bytes
    assert 5_500_000_000 < n < 6_500_000_000


def test_parse_filesize_mb():
    from bulk_downloader.heuristic_scoring import parse_filesize_bytes
    n = parse_filesize_bytes("Size: 230 MB")
    # 230 MB ≈ 241e6
    assert 240_000_000 < n < 242_000_000


def test_parse_filesize_kb_is_negative():
    """A KB-scale 'Download' button is almost certainly a thumbnail
    or poster, not a video — score should be negative."""
    from bulk_downloader.heuristic_scoring import (
        parse_filesize_bytes, score_filesize)
    n = parse_filesize_bytes("Download (12 KB)")
    delta, _ = score_filesize(n)
    assert delta < 0


def test_parse_filesize_gb_is_positive():
    from bulk_downloader.heuristic_scoring import (
        parse_filesize_bytes, score_filesize)
    n = parse_filesize_bytes("5.7 GB")
    delta, _ = score_filesize(n)
    assert delta >= 30


def test_parse_filesize_comma_decimal():
    """European locale: 5,7 GB → same as 5.7 GB."""
    from bulk_downloader.heuristic_scoring import parse_filesize_bytes
    a = parse_filesize_bytes("5,7 GB")
    b = parse_filesize_bytes("5.7 GB")
    assert a == b


def test_parse_filesize_no_match_returns_zero():
    from bulk_downloader.heuristic_scoring import parse_filesize_bytes
    assert parse_filesize_bytes("no size here") == 0
    assert parse_filesize_bytes("") == 0
    assert parse_filesize_bytes(None) == 0


def test_score_filesize_zero_no_contribution():
    from bulk_downloader.heuristic_scoring import score_filesize
    delta, _ = score_filesize(0)
    assert delta == 0


# ── (C) Ancestor context ─────────────────────────────────────────────


def test_ancestor_positive_class_bonus():
    from bulk_downloader.heuristic_scoring import score_ancestor_context
    delta, label = score_ancestor_context("download-options video-actions")
    assert delta == 25
    assert "download" in label.lower()


def test_ancestor_negative_class_penalty():
    """The single biggest win: a "Download" button inside
    related-videos rail gets demoted hard."""
    from bulk_downloader.heuristic_scoring import score_ancestor_context
    delta, label = score_ancestor_context("related-videos sidebar")
    assert delta == -40
    assert "related" in label.lower() or "sidebar" in label.lower()


def test_ancestor_both_signals_net():
    """If both positive and negative appear, the net is +25 -40 = -15."""
    from bulk_downloader.heuristic_scoring import score_ancestor_context
    delta, _ = score_ancestor_context(
        "download-section comments-area")
    assert delta == -15


def test_ancestor_no_match_zero():
    from bulk_downloader.heuristic_scoring import score_ancestor_context
    delta, _ = score_ancestor_context("just-some-random-class")
    assert delta == 0


# ── (D) URL fingerprinting ───────────────────────────────────────────


def test_fingerprint_host_match():
    from bulk_downloader.heuristic_scoring import score_url_fingerprint
    fp = {"known_hosts": {"cdn.example.com"}}
    delta, label = score_url_fingerprint(
        "https://cdn.example.com/v/abc.mp4", "", fp)
    assert delta == 30
    assert "cdn.example.com" in label


def test_fingerprint_path_prefix_match():
    from bulk_downloader.heuristic_scoring import score_url_fingerprint
    fp = {"known_path_prefixes": {"/v/"}}
    delta, label = score_url_fingerprint(
        "https://anything.com/v/abc.mp4", "", fp)
    assert delta == 30


def test_fingerprint_no_match_zero():
    from bulk_downloader.heuristic_scoring import score_url_fingerprint
    fp = {"known_hosts": {"other.com"},
            "known_path_prefixes": {"/something/"}}
    delta, _ = score_url_fingerprint(
        "https://different.com/path.mp4", "", fp)
    assert delta == 0


def test_fingerprint_empty_state_zero():
    from bulk_downloader.heuristic_scoring import score_url_fingerprint
    for fp in (None, {}, {"known_hosts": []},
                 {"known_hosts": [], "known_path_prefixes": []}):
        delta, _ = score_url_fingerprint(
            "https://cdn.example.com/v.mp4", "", fp)
        assert delta == 0


def test_fingerprint_accepts_list_or_set():
    """Caller might persist as list (JSON-serializable); module
    must coerce."""
    from bulk_downloader.heuristic_scoring import score_url_fingerprint
    for fp in ({"known_hosts": ["cdn.example.com"]},
                 {"known_hosts": {"cdn.example.com"}}):
        delta, _ = score_url_fingerprint(
            "https://cdn.example.com/v.mp4", "", fp)
        assert delta == 30


def test_update_fingerprint_records_host_and_prefix():
    from bulk_downloader.heuristic_scoring import (
        update_fingerprint_from_success)
    fp = update_fingerprint_from_success(
        {}, "https://cdn.example.com/videos/abc/8k.mp4")
    assert "cdn.example.com" in fp["known_hosts"]
    # The path-prefix is everything up to the last slash
    assert "/videos/abc/" in fp["known_path_prefixes"]


def test_update_fingerprint_dedups():
    from bulk_downloader.heuristic_scoring import (
        update_fingerprint_from_success)
    fp = update_fingerprint_from_success(
        {}, "https://cdn.example.com/v/abc.mp4")
    fp = update_fingerprint_from_success(
        fp, "https://cdn.example.com/v/xyz.mp4")
    # Two URLs with same host + same path-prefix → one entry each
    assert fp["known_hosts"].count("cdn.example.com") == 1
    assert fp["known_path_prefixes"].count("/v/") == 1


def test_update_fingerprint_caps_at_20():
    """Cap each set at 20 entries so a misclassified download can't
    poison scoring forever."""
    from bulk_downloader.heuristic_scoring import (
        update_fingerprint_from_success)
    fp = {}
    for i in range(40):
        fp = update_fingerprint_from_success(
            fp, f"https://host-{i}.example.com/v/x.mp4")
    assert len(fp["known_hosts"]) <= 20


def test_update_fingerprint_defensive():
    """Bad inputs return fingerprint unchanged, never crash."""
    from bulk_downloader.heuristic_scoring import (
        update_fingerprint_from_success)
    for bad_url in (None, "", 12345, "not a url"):
        # Must not raise
        update_fingerprint_from_success({}, bad_url)
    for bad_fp in (None, "string", 12345, []):
        update_fingerprint_from_success(bad_fp, "https://example.com/x.mp4")


# ── (E) Position + shape ─────────────────────────────────────────────


def test_position_above_fold_bonus():
    from bulk_downloader.heuristic_scoring import score_position_shape
    delta, _ = score_position_shape(
        {"top": 400, "width": 120, "height": 40},
        viewport_height=1080)
    # 400 < 1080 = above-fold (+10), and 120x40 = button (+15)
    assert delta == 25


def test_position_deep_in_page_penalty():
    from bulk_downloader.heuristic_scoring import score_position_shape
    delta, _ = score_position_shape(
        {"top": 8000, "width": 50, "height": 20},
        viewport_height=1080)
    # 8000 > 1080 * 5 = -10. Width/height too small for button bonus.
    assert delta < 0


def test_position_no_rect_zero():
    """Server-side scoring (pasted HTML) has no rect data;
    contribution must be zero."""
    from bulk_downloader.heuristic_scoring import score_position_shape
    assert score_position_shape(None)[0] == 0
    assert score_position_shape({})[0] == 0


def test_position_defensive():
    from bulk_downloader.heuristic_scoring import score_position_shape
    # Non-numeric rect values
    delta, _ = score_position_shape(
        {"top": "abc", "width": None, "height": []})
    assert delta == 0


# ── (F) Combinatorial download+resolution ────────────────────────────


def test_combinatorial_download_resolution_bonus():
    from bulk_downloader.heuristic_scoring import score_text_signals
    score, _ = score_text_signals("Download 4K MP4", "", "", "a")
    # download(+25) + 4K(+60) + dl+res pair(+20) + video ext(0, no .mp4 in href) = 105
    # Actually no .mp4 in url args so should NOT add the +30 for extension
    # download(25) + 4K(60) + combinatorial(20) = 105
    assert score >= 105


def test_combinatorial_works_both_directions():
    """Both 'Download 4K' and '4K Download' should match."""
    from bulk_downloader.heuristic_scoring import score_text_signals
    a, _ = score_text_signals("Download 4K", "", "", "a")
    b, _ = score_text_signals("4K Download", "", "", "a")
    assert a == b


# ── (G) Expanded weighted negatives ──────────────────────────────────


def test_report_button_strong_negative():
    from bulk_downloader.heuristic_scoring import score_text_signals
    # Avoid "video" word — it triggers the download keyword (+25)
    score, reasons = score_text_signals("Report abuse", "", "", "a")
    # -60 for report alone, no offsetting positives
    assert score <= -55
    assert any("report" in r[1] for r in reasons)


def test_related_videos_navigation_negative():
    from bulk_downloader.heuristic_scoring import score_text_signals
    score, _ = score_text_signals(
        "Next Episode", "", "", "a")
    # -50 for navigation. No positive signals.
    assert score <= -45


def test_embed_share_negative():
    from bulk_downloader.heuristic_scoring import score_text_signals
    score, _ = score_text_signals("Embed Code", "", "", "a")
    assert score <= -40


def test_trailer_suppressed_unless_4k():
    """A 'Download trailer' is NOT what we want (low priority).
    But a '4K Trailer' download IS desirable content."""
    from bulk_downloader.heuristic_scoring import score_text_signals
    # 1080p trailer — penalty applies
    a, _ = score_text_signals(
        "Download 1080p trailer", "", "", "a")
    # 4K trailer — penalty SHOULD be suppressed
    b, _ = score_text_signals(
        "Download 4K trailer", "", "", "a")
    # The 4K one should score higher than the 1080p one BY MORE
    # than the resolution-tier difference alone (i.e. the trailer
    # penalty is removed for 4K, doubling the effective gap)
    assert b > a + (60 - 40)  # 4K(60) vs 1080(40) = +20 min,
                                 # plus another +40 from removed penalty


# ── score_candidate top-level ────────────────────────────────────────


def test_score_candidate_realistic_4k_download_button():
    """Realistic scenario: a 4K download CTA with a filesize. Should
    score very high."""
    from bulk_downloader.heuristic_scoring import score_candidate
    cand = {
        "tag": "a",
        "text": "Download 4K MP4 (5.7 GB)",
        "href": "/v/abc/2160p.mp4",
        "ancestor_classes": "download-options video-actions",
        "surrounding_text": "",
    }
    result = score_candidate(cand)
    # 4K(60) + download(25) + dl+res(20) + video ext(30)
    # + filesize GB(30) + positive ancestor(25) = 190
    assert result["score"] >= 150
    assert result["resolution_tier"] == 60
    assert result["estimated_size_bytes"] > 5_000_000_000


def test_score_candidate_related_videos_sidebar():
    """Same-looking button but in the related-videos rail —
    should score MUCH lower."""
    from bulk_downloader.heuristic_scoring import score_candidate
    cand_good = {
        "tag": "a",
        "text": "Download 1080p",
        "href": "/v/123.mp4",
        "ancestor_classes": "video-actions main-content",
    }
    cand_bad = {
        "tag": "a",
        "text": "Download 1080p",
        "href": "/v/123.mp4",
        "ancestor_classes": "related-videos sidebar",
    }
    good = score_candidate(cand_good)
    bad = score_candidate(cand_bad)
    # The "related" wrapper costs 25 (positive) + 40 (negative) = 65 points
    assert (good["score"] - bad["score"]) >= 60


def test_score_candidate_thumbnail_with_kb_size():
    """A 'Download' button next to '12 KB' should fail to clear
    the threshold."""
    from bulk_downloader.heuristic_scoring import score_candidate
    cand = {
        "tag": "a",
        "text": "Download poster (12 KB)",
        "href": "/posters/abc.jpg",
    }
    result = score_candidate(cand)
    # download(+25) - tiny size(-20) - thumbnail kw(-30) = -25
    assert result["score"] < 25


def test_score_candidate_defensive():
    """Bad input types must not crash."""
    from bulk_downloader.heuristic_scoring import score_candidate
    for bad in (None, "string", 12345, []):
        # Must not raise
        r = score_candidate(bad)
        assert r["score"] == 0


def test_score_candidate_non_string_tag():
    """Audit catch: tag=12345 crashed `.lower()` with
    AttributeError. Must coerce to empty string."""
    from bulk_downloader.heuristic_scoring import score_candidate
    for bad_tag in (12345, None, [], {}):
        r = score_candidate({"tag": bad_tag, "text": "Download 4K MP4"})
        # Doesn't crash; basic text signals still produce a score
        assert isinstance(r["score"], int)


# ── rank_candidates ──────────────────────────────────────────────────


def test_rank_candidates_sorts_by_score_then_resolution_then_size():
    from bulk_downloader.heuristic_scoring import rank_candidates
    cands = [
        {"tag": "a", "text": "Download 1080p (1.2 GB)", "href": "/a.mp4"},
        {"tag": "a", "text": "Download 4K (5.7 GB)", "href": "/b.mp4"},
        {"tag": "a", "text": "Download 720p (500 MB)", "href": "/c.mp4"},
    ]
    ranked = rank_candidates(cands)
    # Highest-resolution should be first (highest score, highest tier)
    assert "/b.mp4" in ranked[0]["href"]
    # 1080p second
    assert "/a.mp4" in ranked[1]["href"]


def test_rank_candidates_filters_below_min_score():
    from bulk_downloader.heuristic_scoring import rank_candidates
    cands = [
        {"tag": "a", "text": "Random link", "href": "/x"},
        {"tag": "a", "text": "Download 4K MP4", "href": "/v.mp4"},
    ]
    ranked = rank_candidates(cands, min_score=25)
    # Only the strong candidate survives
    assert len(ranked) == 1
    assert "v.mp4" in ranked[0]["href"]


def test_rank_candidates_annotates_with_score_fields():
    from bulk_downloader.heuristic_scoring import rank_candidates
    cands = [{"tag": "a", "text": "Download 4K MP4", "href": "/v.mp4"}]
    ranked = rank_candidates(cands)
    assert "score" in ranked[0]
    assert "score_reasons" in ranked[0]
    assert "resolution_tier" in ranked[0]


def test_rank_candidates_defensive_input():
    from bulk_downloader.heuristic_scoring import rank_candidates
    for bad in (None, "string", 12345, {}):
        assert rank_candidates(bad) == []


# ── JS port mirrors Python (learn.py) ────────────────────────────────


def test_learn_js_has_resolution_tiers():
    """The teach panel JS must mirror the Python tiered scoring."""
    src = _learn_impl_src()
    assert "RESOLUTION_TIERS" in src
    # The same tier values must appear
    assert "80, '8K'" in src or "80, \"8K\"" in src
    assert "60, '4K'" in src or "60, \"4K\"" in src


def test_learn_js_has_ancestor_walk():
    """JS scoreCandidates walks ancestors 4 levels."""
    src = _learn_impl_src()
    assert "ANCESTOR_POSITIVE" in src
    assert "ANCESTOR_NEGATIVE" in src
    # The ancestor walk (depth 4)
    assert "depth < 4" in src


def test_learn_js_has_filesize_parser():
    src = _learn_impl_src()
    assert "parseFilesize" in src
    assert "SIZE_MULTIPLIERS" in src


def test_learn_js_has_fingerprint_bonus():
    src = _learn_impl_src()
    assert "fingerprintBonus" in src
    assert "fp_hosts" in src


def test_learn_js_has_position_shape_scoring():
    src = _learn_impl_src()
    assert "above-fold" in src
    assert "button-sized" in src


# ── Runner integration ──────────────────────────────────────────────


def test_runner_update_job_calls_fingerprint_update():
    """On status=done, _update_job must fold the URL into the
    per-site fingerprint via heuristic_scoring."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _update_job_current")
    nxt = src.find("\n    def ", pos + 1)
    body = src[pos:nxt]
    assert "heuristic_scoring" in body
    assert "update_fingerprint_from_success" in body
    assert "url_fingerprint" in body


def test_runner_fingerprint_update_failopen():
    """Scoring failures must NOT propagate out of _update_job — a
    bug in scoring code shouldn't break the queue."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _update_job_current")
    nxt = src.find("\n    def ", pos + 1)
    body = src[pos:nxt]
    # The fingerprint block is wrapped in try/except
    assert "Never let scoring code break the queue" in body


# ── App.py wiring ───────────────────────────────────────────────────


def test_cfg_fields_has_url_fingerprint():
    src = _APP_SRC()
    assert '"url_fingerprint"' in src


def test_defaults_has_empty_url_fingerprint():
    src = _APP_SRC()
    assert '"url_fingerprint": {}' in src


# ── v3.43.44 new wiring ─────────────────────────────────────────────


def test_install_teach_overlay_accepts_fingerprint():
    """install_teach_overlay must take a fingerprint kwarg (default
    None) so the per-site URL pattern signal flows to the panel."""
    from bulk_downloader.learn import install_teach_overlay
    import inspect
    sig = inspect.signature(install_teach_overlay)
    assert "fingerprint" in sig.parameters


def test_install_teach_overlay_injects_fp_globals():
    """The bootstrap script that runs before TEACH_OVERLAY_JS must
    set window.__pw_teach_fp_hosts and __pw_teach_fp_prefixes."""
    learn_py = _learn_impl_src()
    pos = learn_py.find("def install_teach_overlay")
    body = learn_py[pos:pos + 3000]
    assert "__pw_teach_fp_hosts" in body
    assert "__pw_teach_fp_prefixes" in body
    # And the values are JSON-serialized (not f-string interpolated
    # — that would let a malicious hostname inject JS)
    assert "json.dumps" in body or "_json.dumps" in body


def test_install_teach_overlay_filters_non_string_entries():
    """Defensive: a corrupted fingerprint with int/None entries
    must not crash the bootstrap script generation."""
    learn_py = _learn_impl_src()
    pos = learn_py.find("def install_teach_overlay")
    body = learn_py[pos:pos + 3000]
    # Look for the isinstance check
    assert "isinstance(h, str)" in body or "isinstance(p, str)" in body


def test_runner_passes_fingerprint_to_overlay():
    """The two install_teach_overlay calls in runner.py must pass
    the per-site fingerprint from cfg."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    # Both call sites
    install_count = src.count("install_teach_overlay(page,")
    assert install_count >= 2
    fingerprint_count = src.count("fingerprint=fp")
    assert fingerprint_count >= 2


def test_teach_panel_reads_window_globals():
    """The teach panel state init must read the fp_hosts /
    fp_prefixes from window-scoped bootstrap vars rather than
    initialize them empty."""
    learn_py = _learn_impl_src()
    # The state.fp_hosts initializer references the window global
    assert "window.__pw_teach_fp_hosts" in learn_py
    assert "window.__pw_teach_fp_prefixes" in learn_py


def test_fingerprint_endpoint_registered():
    src = _APP_SRC()
    assert "/api/sites/<sid>/heuristic/fingerprint" in src
    assert "def api_heuristic_fingerprint" in src


def test_fingerprint_endpoint_supports_delete():
    """DELETE on the fingerprint endpoint wipes the per-site state.
    Useful when a site's CDN changes and the prior fingerprint is
    actively misleading."""
    src = _APP_SRC()
    pos = src.find("def api_heuristic_fingerprint")
    body = src[pos:pos + 1500]
    assert "methods=" in src[max(0, pos-200):pos]  # decorator above
    assert "DELETE" in src[max(0, pos-200):pos]
    assert 'request.method == "DELETE"' in body or \
           "request.method=='DELETE'" in body


def test_fingerprint_endpoint_returns_404_for_unknown_site():
    src = _APP_SRC()
    pos = src.find("def api_heuristic_fingerprint")
    body = src[pos:pos + 1500]
    assert "404" in body


def test_score_candidate_handles_non_string_fields():
    """Audit catch: score_candidate({'href': 12345}) crashed
    `str + int` with TypeError. The recorder always emits strings,
    but corrupted input shouldn't crash scoring."""
    from bulk_downloader.heuristic_scoring import score_candidate
    for bad_candidate in (
        {"href": 12345},
        {"data_href": 12345},
        {"text": None, "href": None, "data_href": None,
         "data_url": None, "data_src": None, "data_download": None,
         "tag": None, "ancestor_classes": None,
         "surrounding_text": None, "rect": None,
         "viewport_height": "not a number"},
    ):
        # Must not raise
        result = score_candidate(bad_candidate)
        assert isinstance(result, dict)
        assert "score" in result
