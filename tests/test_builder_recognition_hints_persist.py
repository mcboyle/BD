"""Wave 1b: build_template_from_wacz persists the platform/workflow hint
channel into the draft recognition block, and the hints are F2-clean.

Before 1b, detect() computed workflow_hints/platform_hints but the builder
dropped them. These tests prove they now reach the draft AND that persisting
them does not introduce any secret/PII (scan_artifact_secrets stays []).

Zero-arg test functions; repo root from __file__ (run_tests.py convention).
"""
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

import build_template_from_wacz as btw  # noqa: E402
from bulk_downloader.capture_artifact_redact import scan_artifact_secrets  # noqa: E402


def _make_wacz(capture):
    d = Path(tempfile.mkdtemp(prefix="hints_"))
    wacz = d / "synthetic.wacz"
    with zipfile.ZipFile(wacz, "w") as z:
        z.writestr("archive/capture.json", json.dumps(capture))
    return wacz


def _capture_with_hints():
    # A native player + a membership wrapper marker (vhx.tv) + a biller marker.
    # All public structural markers; no tokens/PII. Query string included on the
    # biller link to confirm the hint channel strips it.
    html = (
        '<html><body>'
        '<video controls><source src="/v/clip" type="application/x-mpegURL"></video>'
        '<script src="https://embed.vhx.tv/assets/player.js"></script>'
        '<a href="https://secure.verotel.com/startorder?backURL=x&token=NOPE">join</a>'
        '</body></html>'
    )
    return {
        "url": "https://app.example.com/x", "host": "app.example.com",
        "captured_at": "2026-06-08T00:00:00Z",
        "dom_log_count": 1, "network_log_count": 0,
        "dom_log": [{"type": "full_snapshot", "html": html}],
        "network_log": [],
    }


def test_build_template_persists_hint_channel():
    wacz = _make_wacz(_capture_with_hints())
    try:
        draft = btw.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    rec = draft.get("recognition") or {}
    assert "workflow_hints" in rec, rec.keys()
    assert "platform_hints" in rec, rec.keys()
    wh = rec.get("workflow_hints") or []
    ph = rec.get("platform_hints") or []
    # at least one of the two channels detected something from the markers
    assert wh or ph, (wh, ph)
    labels = {h.get("hint") for h in (wh + ph) if isinstance(h, dict)}
    assert "vimeo_ott" in labels or "verotel" in labels, labels


def test_persisted_hints_are_f2_clean():
    wacz = _make_wacz(_capture_with_hints())
    try:
        draft = btw.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    # Persisting hints must not introduce any secret/PII; the biller query
    # string (token=NOPE) must never appear in the derived draft.
    assert scan_artifact_secrets(draft) == [], scan_artifact_secrets(draft)
    assert "token=NOPE" not in json.dumps(draft), "biller query persisted"


def test_no_hints_yields_empty_lists_not_crash():
    cap = {
        "url": "https://plain.example.com/x", "host": "plain.example.com",
        "captured_at": "2026-06-08T00:00:00Z",
        "dom_log_count": 1, "network_log_count": 0,
        "dom_log": [{"type": "full_snapshot",
                     "html": '<html><body><video controls></video></body></html>'}],
        "network_log": [],
    }
    wacz = _make_wacz(cap)
    try:
        draft = btw.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    rec = draft.get("recognition") or {}
    assert rec.get("workflow_hints") == []
    assert rec.get("platform_hints") == []
