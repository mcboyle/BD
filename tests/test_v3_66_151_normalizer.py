"""v3.66.151 — normalize a rich WACZ-builder draft into a runtime reviewed-shape
review candidate, then promote the candidate (not the raw draft).

  normalize_draft:
    - download.button_hint -> download.trigger (never row_selectors)
    - row_selectors only when pre-existing, safe AND modal-scoped
    - quality.select_resolution_template -> quality.resolution_option
    - resolution_priority/resolutions_seen -> resolutions
    - network_discovery.api/media -> flat network_patterns (host-bearing
      scrubbed, media suffixes kept), trackers -> rejected_patterns
    - provenance preserved; warnings emitted; status review_ready /
      draft_review_required, NEVER enabled
  promote_template.py:
    - promotes a normalized candidate; refuses a raw builder draft
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from bulk_downloader.template_normalize import normalize_draft

RICH = {
    "schema_version": "bulk_downloader.template_draft.v1",
    "template_status": "draft_requires_review",
    "confidence": "high",
    "source": {"capture_file": "app.reptyle.com_x.wacz", "host": "app.reptyle.com",
               "dom_log_count": 110, "network_log_count": 523},
    "match": {"hosts": ["app.reptyle.com"], "url_patterns": [r"^https://app\.reptyle\.com/"]},
    "selectors": {
        "login": {"email": 'input[type="email"]', "password": 'input[type="password"]'},
        "player": {"container": ".video-js"},
        "quality": {
            "open_menu": '[aria-label="Open the video quality settings menu"]',
            "select_resolution_template": '[aria-label="Set video quality to {resolution}"]',
            "available_resolutions": [2160, 1080],
        },
        "download": {"button_hint": '[aria-label*="Download" i]'},
    },
    "network_discovery": {
        "top_hosts": [{"host": "o851585.ingest.sentry.io", "count": 3},
                      {"host": "app.reptyle.com", "count": 50}],
        "api_patterns": ["/api/v{version}/movie/{movie_id}/download-resolution/{resolution}"],
        "media_patterns": [".../AVC_{resolution}.mp4"],
        "resolutions_seen": [2160, 1080],
    },
    "resolution_priority": [2160, 1080],
    "guardrails": ["Do not persist tokens."],
}


# ── normalize_draft ──────────────────────────────────────────────────────


def test_button_hint_becomes_trigger_not_rows():
    dl = normalize_draft(RICH)["selectors"]["download"]
    assert dl["trigger"] == '[aria-label*="Download" i]'
    assert "row_selectors" not in dl


def test_no_fabricated_rows_warns():
    c = normalize_draft(RICH)
    assert any("modal-scoped row" in w for w in c["warnings"])


def test_quality_and_resolutions_mapped():
    c = normalize_draft(RICH)
    assert c["selectors"]["quality"]["resolution_option"] == '[aria-label="Set video quality to {resolution}"]'
    assert c["resolutions"] == [2160, 1080]


def test_patterns_media_kept_api_relative():
    c = normalize_draft(RICH)
    assert any("AVC" in p for p in c["network_patterns"]), "media suffix dropped"
    # RICH has no explicit API host -> the /api path stays RELATIVE (never guessed)
    assert "/api/v{version}/movie/{movie_id}/download-resolution/{resolution}" in c["network_patterns"]
    assert not any(p.startswith("https://app.reptyle.com/api/") for p in c["network_patterns"])
    assert any("relative" in w.lower() for w in c["warnings"])
    assert not any("sentry" in p for p in c["network_patterns"])  # tracker only in top_hosts


def test_explicit_api_host_is_prefixed():
    d = dict(RICH)
    d["network_discovery"] = dict(RICH["network_discovery"], api_host="api2.reptyle.com")
    c = normalize_draft(d)
    assert "https://api2.reptyle.com/api/v{version}/movie/{movie_id}/download-resolution/{resolution}" in c["network_patterns"]


def test_status_never_enabled_and_ready_when_complete():
    c = normalize_draft(RICH)
    assert c["status"] != "enabled"
    assert c["status"] == "review_ready"


def test_incomplete_draft_is_review_required():
    thin = {"schema_version": "bulk_downloader.template_draft.v1",
            "selectors": {}, "network_discovery": {}}
    c = normalize_draft(thin)
    assert c["status"] == "draft_review_required"
    assert c["status"] != "enabled"


def test_provenance_preserved():
    c = normalize_draft(RICH)
    assert c["source_capture"] == "app.reptyle.com_x.wacz"
    assert c["source"]["dom_log_count"] == 110


def test_flat_draft_tracker_rejected():
    flat = {"schema": "bulk_downloader.template.draft.v1", "host": "x.com",
            "network_patterns": ["https://o1.ingest.sentry.io/api/1/envelope/",
                                 "https://x.com/movie/9/watch"],
            "selectors": {"download": {"button_hint": 'a[download]'}}}
    c = normalize_draft(flat)
    assert any("sentry" in r for r in c["rejected_patterns"])
    assert not any("sentry" in p for p in c["network_patterns"])


def test_modal_rows_kept_generic_dropped():
    draft = {"schema_version": "bulk_downloader.template_draft.v1", "host": "x.com",
             "network_discovery": {"api_patterns": ["/api/v1/movie/9/download-resolution/1080"]},
             "resolution_priority": [1080],
             "selectors": {"download": {
                 "button_hint": '[aria-label*="Download" i]',
                 "row_selectors": ['[role="dialog"] a[href*="download" i]', "a"]}}}
    c = normalize_draft(draft)
    rows = c["selectors"]["download"].get("row_selectors", [])
    assert any('role="dialog"' in r for r in rows)   # modal-scoped kept
    assert "a" not in rows                            # generic/unscoped dropped
    assert any("dropped row selector" in w for w in c["warnings"])


def test_modal_suffix_class_rows_are_kept():
    draft = {
        "schema_version": "bulk_downloader.template_draft.v1",
        "host": "x.com",
        "selectors": {"download": {
            "button_hint": '[title*="Download" i]',
            "row_selectors": [
                ".VideoJSPlayer-Modal .VideoJSPlayer-DownloadOption-Link"
            ],
        }},
    }
    rows = normalize_draft(draft)["selectors"]["download"]["row_selectors"]
    assert rows == [".VideoJSPlayer-Modal .VideoJSPlayer-DownloadOption-Link"]


def test_download_button_maps_to_trigger():
    flat = {"schema": "bulk_downloader.template.draft.v1", "host": "x.com",
            "network_patterns": ["https://x.com/movie/9/watch"],
            "selectors": {"download": {"button": 'a[href*="download" i]'}}}
    dl = normalize_draft(flat)["selectors"]["download"]
    assert dl.get("trigger") == 'a[href*="download" i]'
    assert "row_selectors" not in dl
    assert "button" not in dl


def test_signed_url_amz_rejected():
    flat = {"schema": "bulk_downloader.template.draft.v1", "host": "cdn.x.com",
            "network_patterns": [
                "https://cdn.x.com/v/movie.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256"
                "&X-Amz-Signature=abc&X-Amz-Expires=3600",
                "https://x.com/movie/9/watch"],
            "selectors": {"download": {"button": "a[download]"}}}
    c = normalize_draft(flat)
    assert any("X-Amz" in r for r in c["rejected_patterns"])
    assert not any("X-Amz" in p for p in c["network_patterns"])


def test_signed_url_cloudfront_rejected():
    flat = {"schema": "bulk_downloader.template.draft.v1", "host": "cdn.x.com",
            "network_patterns": [
                "https://cdn.x.com/v/movie.mp4?Policy=eyJ&Signature=xyz"
                "&Key-Pair-Id=APKA&Expires=99999",
                "https://x.com/api/v1/movie/{id}/watch"],
            "selectors": {"download": {"button": "a[download]"}}}
    c = normalize_draft(flat)
    assert any("Key-Pair-Id" in r for r in c["rejected_patterns"])
    assert not any(("Key-Pair-Id" in p) or ("Policy=" in p) for p in c["network_patterns"])


def test_scrub_rejects_signed_and_token_queries():
    from bulk_downloader.pattern_hygiene import scrub_network_patterns
    res = scrub_network_patterns([
        "https://x.com/a.mp4?X-Amz-Signature=z",
        "https://x.com/a.mp4?Policy=p&Key-Pair-Id=k",
        "https://x.com/watch?token=abc",
        "https://x.com/v/{id}/stream?bearer=xyz",
        "/api/v1/movie/{id}/watch",     # clean relative -> kept
        "https://x.com/auth/login",     # 'auth' as a PATH segment -> kept
    ])
    assert "/api/v1/movie/{id}/watch" in res["kept"]
    assert "https://x.com/auth/login" in res["kept"]
    assert len(res["dropped"]) == 4


# ── promote_template.py (CLI, via subprocess) ────────────────────────────


_REPO = Path(__file__).resolve().parent.parent


def _promote(candidate_path, *extra):
    return subprocess.run(
        [sys.executable, str(_REPO / "tools" / "promote_template.py"),
         str(candidate_path), *extra],
        cwd=str(_REPO), capture_output=True, text=True)


def test_promote_accepts_normalized_candidate():
    d = tempfile.mkdtemp()
    cf = Path(d) / "cand.json"
    cf.write_text(json.dumps(normalize_draft(RICH)), "utf-8")
    outdir = Path(d) / "reviewed"
    r = _promote(cf, "--out-dir", str(outdir))
    assert r.returncode == 0, r.stderr
    out = outdir / "app.reptyle.com.template.json"
    assert out.exists()
    assert json.loads(out.read_text())["status"] == "reviewed_not_enabled"


def test_promote_enable_flag_sets_enabled():
    d = tempfile.mkdtemp()
    cf = Path(d) / "cand.json"
    cf.write_text(json.dumps(normalize_draft(RICH)), "utf-8")
    outdir = Path(d) / "reviewed"
    r = _promote(cf, "--enable", "--out-dir", str(outdir))
    assert r.returncode == 0, r.stderr
    assert json.loads((outdir / "app.reptyle.com.template.json").read_text())["status"] == "enabled"


def test_promote_refuses_raw_builder_draft():
    d = tempfile.mkdtemp()
    rf = Path(d) / "raw.json"
    rf.write_text(json.dumps(RICH), "utf-8")  # raw: has network_discovery
    r = _promote(rf, "--out-dir", str(Path(d) / "reviewed"))
    assert r.returncode == 1
    assert "raw builder draft" in r.stderr.lower()
