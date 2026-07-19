"""Render tests for the Wave-1 recognizer cockpit display (read-only).

Covers: the recognition block is carried through the inventory rows; the
sub-row render summarizes family/candidates/flags + an expandable <details>;
it no-ops for pre-168 drafts (no recognition) and for non-draft sections; and
it introduces no action affordances (stays read-only).

Zero-arg test functions; repo root from __file__ (run_tests.py convention).
"""
import sys
from pathlib import Path

from flask import Flask

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from bulk_downloader.app_template_manager_ui import (  # noqa: E402
    register_routes, _recognition_html)


def _client():
    app = Flask(__name__)
    register_routes(app)
    return app.test_client()


_REC = {
    "player_family": "videojs",
    "candidates": ["videojs", "jwplayer"],
    "delivery": "inline",
    "policy": "inline_review_only",
    "flags": {"drm": False, "ad_overlay": True},
    "concerns": ["ad_wrapper"],
    "api_classes": ["download_resolution"],
    "notes": ["candidate-grade fingerprint"],
    "workflow_hints": [
        {"hint": "vimeo_ott", "kind": "membership_workflow", "note": "hint only"},
        {"hint": "wordpress", "kind": "cms_wrapper", "note": "wrapper"},
    ],
    "platform_hints": [
        {"hint": "verotel", "category": "biller", "note": "structure-only"},
    ],
}


def test_inventory_rows_carry_recognition_key():
    data = _client().get("/api/template_manager/inventory").get_json()
    # key must be present on every row (value may be None for pre-168 drafts)
    for rows in data["dirs"].values():
        for row in rows:
            assert "recognition" in row, row.keys()


def test_recognition_renders_hints_and_membership_badge():
    out = _recognition_html("drafts", {"recognition": _REC})
    # summary: membership badge + total hint count (2 workflow + 1 platform = 3)
    assert "membership workflow" in out
    assert "3 platform hint(s)" in out
    # detail: labels surfaced (label-only)
    assert "vimeo_ott" in out and "verotel" in out
    assert "workflow_hints" in out and "platform_hints" in out


def test_recognition_no_membership_badge_without_membership():
    rec = dict(_REC, workflow_hints=[{"hint": "wordpress", "kind": "cms_wrapper"}],
               platform_hints=[])
    out = _recognition_html("drafts", {"recognition": rec})
    assert "membership workflow" not in out
    assert "1 platform hint(s)" in out


def test_recognition_no_hint_badge_when_empty():
    rec = dict(_REC, workflow_hints=[], platform_hints=[])
    out = _recognition_html("drafts", {"recognition": rec})
    assert "platform hint(s)" not in out
    assert "membership workflow" not in out


def test_detect_exposes_hint_keys_that_build_template_persists():
    # Contract the 1b persistence relies on: detect() returns these keys, and
    # build_template copies them verbatim into the draft recognition block.
    import importlib, sys as _sys
    from pathlib import Path as _P
    _tools = str(_P(__file__).resolve().parent.parent / "tools")
    if _tools not in _sys.path:
        _sys.path.insert(0, _tools)
    pr = importlib.import_module("player_recognition")
    rec = pr.detect("<html><video></video></html>", script_srcs=[], iframe_hosts=[])
    assert "workflow_hints" in rec and "platform_hints" in rec


def test_recognition_renders_summary_and_details():
    out = _recognition_html("drafts", {"recognition": _REC})
    assert "family: videojs" in out
    assert "candidate(s)" in out
    assert "ad_overlay" in out
    assert "<details" in out and "recognition (review-only)" in out
    # full block surfaced in the expandable detail
    assert "jwplayer" in out
    assert "download_resolution" in out


def test_recognition_flags_drm_never_bypass():
    rec = dict(_REC, flags={"drm": True, "ad_overlay": False})
    out = _recognition_html("drafts", {"recognition": rec})
    assert "DRM" in out and "never bypass" in out
    assert "ad_overlay" not in out


def test_recognition_noop_when_absent_or_non_draft():
    # pre-168 draft: no recognition block
    assert _recognition_html("drafts", {"recognition": None}) == ""
    assert _recognition_html("drafts", {}) == ""
    # non-draft sections never show recognition even if present
    assert _recognition_html("reviewed", {"recognition": _REC}) == ""
    assert _recognition_html("enabled", {"recognition": _REC}) == ""


def test_recognition_render_is_read_only():
    out = _recognition_html("review_candidates", {"recognition": _REC})
    low = out.lower()
    assert "<form" not in low
    assert "<button" not in low
    assert 'method="post"' not in low


def test_page_still_read_only_with_helper_present():
    body = _client().get("/cockpit/template-manager").get_data(as_text=True)
    assert "<form" not in body.lower()
    assert "<button" not in body.lower()
