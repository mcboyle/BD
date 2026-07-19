"""v3.66.150 — selector-lint enforced at creation + network-pattern hygiene.

  #2  pattern_hygiene.scrub_network_patterns drops analytics/ad/telemetry
      beacons and non-URL junk, keeps real asset/media/relative patterns.
      promote_draft scrubs network_patterns on the way to the ENABLED reviewed
      template (and still refuses blocking-lint drafts).
  #1  save_user_template refuses a learned block with generic/nav row selectors.
      api_template_status surfaces lint of the active reviewed template.

All offline — no fetch, no download, no stored-value reads.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import bulk_downloader.app as bd_app
from bulk_downloader import pattern_hygiene as ph
from bulk_downloader import template_manager as tm
from bulk_downloader import user_templates as ut


REPTYLE = "https://app.reptyle.com/"

# A realistic dirty patterns list: trackers + inlined blobs + real hints.
DIRTY = [
    "https://o851585.ingest.sentry.io/api/123/envelope/?sentry_version=7",
    "https://www.googletagmanager.com/gtm.js?id=GTM-X",
    "https://www.google-analytics.com/analytics.js",
    "https://scripts.clarity.ms/0.8.64/clarity.js",
    "//tsyndicate.com/api/v1/retargeting/set/{uuid}",
    "https://hrsvzn.psmcode.com/js/container_x.js",
    "<!DOCTYPE html><html>... captured page blob ...</html>",
    "function trafficStarsLoader(b){console.log(b)}",
    '{ "url": "https://pxl.tsyndicate.com/api/v1/heavy-ad/report" }',
    "https://cdnjs.cloudflare.com/ajax/libs/video.js/5.19.2/video.js",  # keep
    "https://auth.reptyle.com/vjs/videojs-tube.css?id=dd77",            # keep
    "/movie/9/download-resolution/1080",                               # keep
]


# ── #2 scrub_network_patterns ────────────────────────────────────────────


def test_scrub_drops_trackers_and_junk():
    out = ph.scrub_network_patterns(DIRTY)
    joined = " ".join(out["kept"])
    for bad in ("sentry.io", "googletagmanager", "google-analytics",
                "clarity.ms", "tsyndicate", "psmcode"):
        assert bad not in joined, f"{bad} should have been dropped"
    # no inlined HTML/JS/JSON blobs survived
    assert not any(k[:1] in "<{" or any(c.isspace() for c in k)
                   for k in out["kept"])
    # v3.66.x BAD_TERMS reconciliation: cdnjs/cloudflare are now DROPPED so the
    # scrubber matches the promote gate (which has always rejected them). Real
    # site-host assets and relative API paths are still kept.
    assert "https://cdnjs.cloudflare.com/ajax/libs/video.js/5.19.2/video.js" in out["dropped"]
    assert "https://auth.reptyle.com/vjs/videojs-tube.css?id=dd77" in out["kept"]
    assert "/movie/9/download-resolution/1080" in out["kept"]
    assert len(out["dropped"]) >= 6


def test_scrub_handles_non_list():
    assert ph.scrub_network_patterns(None) == {"kept": [], "dropped": []}
    assert ph.scrub_network_patterns("nope") == {"kept": [], "dropped": []}


def test_scrub_dedups_kept_preserving_order():
    out = ph.scrub_network_patterns(["/a", "/a", "/b", "/a"])
    assert out["kept"] == ["/a", "/b"]


# ── promote_draft: scrub + still refuse blocking-lint ────────────────────


def _tmp_dirs():
    base = Path(tempfile.mkdtemp())
    rd, dd = base / "reviewed", base / "drafts"
    rd.mkdir(); dd.mkdir()
    return rd, dd


def _write_draft(dd, host, *, selectors, patterns, resolutions=(1080, 720), schema=None):
    name = f"{host}.template-draft.json"
    body = {
        "host": host, "status": "draft",
        "selectors": selectors, "network_patterns": patterns,
        "resolutions": list(resolutions),
    }
    if schema:
        body["schema"] = schema
    (dd / name).write_text(json.dumps(body), "utf-8")
    return name


def test_promote_scrubs_network_patterns():
    rd, dd = _tmp_dirs()
    name = _write_draft(
        dd, "good.example",
        selectors={"download": {"trigger": ".movie-card a.download"}},
        patterns=DIRTY,
        schema="bulk_downloader.template_draft.v1")  # raw -> normalize scrubs DIRTY
    res = tm.promote_draft(name, reviewed_dir=str(rd), drafts_dir=str(dd))
    assert res["ok"], res
    assert res["enabled"] is True
    assert res["dropped_patterns"] >= 6
    written = json.loads((rd / res["promoted"]).read_text("utf-8"))
    joined = " ".join(written["network_patterns"])
    for bad in ("sentry.io", "googletagmanager", "tsyndicate", "psmcode"):
        assert bad not in joined
    assert not any(p[:1] in "<{" for p in written["network_patterns"])
    assert "/movie/9/download-resolution/1080" in written["network_patterns"]


def test_promote_still_refuses_unsafe_selectors():
    rd, dd = _tmp_dirs()
    name = _write_draft(
        dd, "bad.example",
        selectors={"download": {"row_selectors": ["a"]}},  # generic → blocking
        patterns=["/x"])
    res = tm.promote_draft(name, reviewed_dir=str(rd), drafts_dir=str(dd))
    assert res["ok"] is False
    assert "unsafe" in res["error"].lower()
    assert not list(rd.glob("*.template.json"))


# ── #1 save_user_template lints learned selectors at creation ────────────


def test_save_user_template_refuses_generic_selector():
    ok, err = ut.save_user_template(
        "bad", "desc", ["https://x/.*"],
        {"download": {"row_selectors": ["a"]}})  # generic → blocking
    assert ok is False
    assert "unsafe" in str(err).lower()


def test_save_user_template_accepts_specific_selector():
    ut.USER_TEMPLATES_FILE = Path(tempfile.mkdtemp()) / "ut.json"  # isolate I/O
    ok, res = ut.save_user_template(
        "good", "desc", ["https://x/.*"],
        {"download": {"row_selectors": [".movie-card a.download-link"]}})
    assert ok is True, res
    assert isinstance(res, dict) and res.get("id")


# ── #1 surface: template_status exposes lint of the active template ──────


def test_template_status_surfaces_lint(fresh_app):
    bd_app.s_cfg["s_lint"] = {"name": "s_lint", "url": REPTYLE}
    r = fresh_app.get("/api/sites/s_lint/template_status")
    assert r.status_code == 200
    body = r.get_json()
    assert "lint" in body and isinstance(body["lint"], list)
    assert "has_blocking_lint" in body and isinstance(body["has_blocking_lint"], bool)
