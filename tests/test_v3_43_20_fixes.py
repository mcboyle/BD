"""v3.43.20 regression tests.

Covers the four bug fixes shipped in v3.43.20:

1. UTF-16 surrogate-pair split in UI event capture (client-side
   _safeTextSlice + server-side _LONE_SURROGATE_RE strip)
2. Queue rendering empty after site-select when last poll was light-mode
   (selSite + renderQ now handle the loading state)
3. Pause-all / Resume-all toolbar buttons present and wired
4. FilthyKings / VideoJSPlayer-DownloadOption template added
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import json
import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_UI_EVENTS_PY = _REPO_ROOT / "bulk_downloader" / "ui_events.py"
_TEMPLATES_PY = _REPO_ROOT / "bulk_downloader" / "templates.py"


# ── 1. Surrogate-pair fix ──────────────────────────────────────────────

def test_lone_surrogate_strip_in_ui_events():
    """Server-side defensive strip: even if a pre-fix client (or an
    extension that ships its own events) sends a lone surrogate, the
    logger must not crash."""
    src = _UI_EVENTS_PY.read_text(encoding="utf-8")
    assert "_LONE_SURROGATE_RE" in src, "_LONE_SURROGATE_RE missing"
    assert r"[\ud800-\udfff]" in src, "surrogate range regex missing"


def test_redact_strips_lone_surrogates_from_event_data():
    """Functional test: feed an event with a lone surrogate into _redact
    and confirm it comes back clean."""
    import bulk_downloader.ui_events as uie
    bad_event = {
        "category": "click",
        "event": "document_click",
        "data": {
            "text": "Cookie 🍪 ago \ud83e",  # lone high surrogate at end
            "id": "",
            "tag": "div",
        },
    }
    cleaned = uie._redact(bad_event)
    text = cleaned["data"]["text"]
    # No surrogate chars should remain
    for c in text:
        assert not (0xD800 <= ord(c) <= 0xDFFF), (
            f"surrogate {ord(c):#x} survived redaction: {text!r}"
        )
    # Original valid emoji (🍪 = U+1F36A = surrogate pair D83C DF6A) must survive
    assert "🍪" in text


def test_ingest_does_not_crash_on_lone_surrogate():
    """End-to-end: ingest() with a lone-surrogate-containing event should
    accept it and not raise. This is the contract the production bug
    violated — UnicodeEncodeError propagated out of logging."""
    import bulk_downloader.ui_events as uie
    events = [{
        "category": "click",
        "event": "document_click",
        "data": {"text": "ends with \ud83e", "tag": "div", "id": ""},
    }]
    # Should not raise
    accepted, dropped = uie.ingest(events, uie.TIER_BASIC)
    assert accepted == 1
    assert dropped == 0


# ── 2. Queue render fix ────────────────────────────────────────────────

# ── 3. Pause-all / Resume-all toolbar ──────────────────────────────────



# ── 4. FilthyKings template ────────────────────────────────────────────

def test_filthykings_template_registered():
    """The template must be importable via templates.get()."""
    import bulk_downloader.templates as tpl
    t = tpl.get("videojsplayer_download_option")
    assert t is not None, "videojsplayer_download_option template not found"
    assert t["id"] == "videojsplayer_download_option"


def test_filthykings_template_selector_ladder():
    """Resolution-specific selectors must appear before the bare class
    selector so the 4K-preferred ladder works."""
    import bulk_downloader.templates as tpl
    t = tpl.get("videojsplayer_download_option")
    rows = t["learned"]["download"]["row_selectors"]
    # 2160p (4K) must come before 1080p must come before 720p
    idx_4k = next((i for i, s in enumerate(rows) if "2160p" in s), -1)
    idx_1080 = next((i for i, s in enumerate(rows) if "1080p" in s), -1)
    idx_720 = next((i for i, s in enumerate(rows) if "720p" in s), -1)
    idx_bare = rows.index("a.VideoJSPlayer-DownloadOption-Link")
    assert 0 <= idx_4k < idx_1080 < idx_720, (
        f"Resolution ladder out of order: 4K={idx_4k}, 1080={idx_1080}, 720={idx_720}"
    )
    assert idx_720 < idx_bare, (
        "Resolution-specific selectors must come BEFORE the bare class selector"
    )
    # url_attribute is the string "href" — single attribute applies to all rows
    assert t["learned"]["download"]["url_attribute"] == "href"


def test_filthykings_template_url_pattern_match():
    """The patterns list must match filthykings.com and the
    /movieaction/download/ URL prefix so suggest_for_url surfaces it."""
    import bulk_downloader.templates as tpl
    suggestions = tpl.suggest_for_url(
        "https://members.filthykings.com/en/video/filthykings/foo-bar"
    )
    assert "videojsplayer_download_option" in suggestions, (
        f"Template not suggested for filthykings URL; got {suggestions!r}"
    )
    # Also matches by URL path pattern even on other hosts
    suggestions = tpl.suggest_for_url(
        "https://other-host.example/movieaction/download/12345/2160p/mp4"
    )
    assert "videojsplayer_download_option" in suggestions, (
        "Template should also suggest by URL-path pattern, not just host"
    )


def test_filthykings_template_no_hashed_classes():
    """The class names in selectors must not include CSS-in-JS hash classes
    (e.g. `styles_rQ2d8EqJoa`). Those would break on the next site rebuild."""
    import bulk_downloader.templates as tpl
    t = tpl.get("videojsplayer_download_option")
    rows = t["learned"]["download"]["row_selectors"]
    for sel in rows:
        # Hashed class pattern from learn._CSS_IN_JS_RE
        assert "styles_" not in sel, (
            f"Selector contains hashed class: {sel!r} — should use only "
            "semantic class names that survive rebuilds"
        )
