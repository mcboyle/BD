"""Tests for F3.2 drift -> AI repair candidate (v3.66.224).

Harness conventions: zero-arg functions, tempfile.mkdtemp, restore module
globals in try/finally. AI status + diff_repair + dom context are injected,
so no live model / network / capture is needed. Drafts are written to a
temp dir and read back.
"""
import json
import tempfile
from pathlib import Path

from bulk_downloader import drift_repair as dr


def _drafts():
    return Path(tempfile.mkdtemp(prefix="drift_drafts_"))



def _ok_status():
    return {"ok": True, "enabled": True, "provider": "ollama"}


def _down_status():
    return {"ok": False, "enabled": False, "error": "disabled"}


def _repair_fn_ok(broken, working, dom, *, page_url=""):
    return {"ok": True, "repairs": [
        {"old_selector": broken[0], "new_selector": ".new-dl-link",
         "role": "row_selectors", "confidence": 80, "reasoning": "moved"}
    ], "removed": []}


def _repair_fn_empty(broken, working, dom, *, page_url=""):
    return {"ok": True, "repairs": [], "removed": []}


def _repair_fn_err(broken, working, dom, *, page_url=""):
    return {"ok": False, "error": "model timeout"}


# ── AI gating ────────────────────────────────────────────────────────────────
def test_ai_down_is_inert_no_draft():
    dd = _drafts()
    res = dr.propose_repairs(
        "site.example", broken_selectors=[".old"], dom_excerpt="<html>",
        drafts_dir=dd, status_fn=_down_status, ai_fn=_repair_fn_ok)
    assert res["repairs"] == 0
    assert res["skipped"] == "ai_unavailable"
    assert list(dd.glob("*")) == []  # nothing written


def test_ai_error_is_inert():
    dd = _drafts()
    res = dr.propose_repairs(
        "site.example", broken_selectors=[".old"], dom_excerpt="<html>",
        drafts_dir=dd, status_fn=_ok_status, ai_fn=_repair_fn_err)
    assert res["repairs"] == 0
    assert res["skipped"] == "ai_error"
    assert list(dd.glob("*")) == []


def test_no_broken_selectors_noop():
    dd = _drafts()
    res = dr.propose_repairs(
        "site.example", broken_selectors=[], dom_excerpt="<html>",
        drafts_dir=dd, status_fn=_ok_status, ai_fn=_repair_fn_ok)
    assert res["repairs"] == 0


def test_model_proposes_nothing():
    dd = _drafts()
    res = dr.propose_repairs(
        "site.example", broken_selectors=[".old"], dom_excerpt="<html>",
        drafts_dir=dd, status_fn=_ok_status, ai_fn=_repair_fn_empty)
    assert res["repairs"] == 0
    assert list(dd.glob("*")) == []


# ── successful proposal writes a REVIEW-ONLY draft ───────────────────────────
def test_success_writes_draft_review_required():
    dd = _drafts()
    res = dr.propose_repairs(
        "site.example", broken_selectors=[".old-dl"],
        working_selectors=[".title"], dom_excerpt="<html>...</html>",
        page_url="https://site.example/x", drafts_dir=dd,
        status_fn=_ok_status, ai_fn=_repair_fn_ok)
    assert res["repairs"] == 1
    files = list(dd.glob("*.template-draft.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text("utf-8"))
    assert doc["status"] == "draft_review_required"
    assert doc["review_required"] is True
    # the new selector landed somewhere in the selectors tree
    blob = json.dumps(doc)
    assert ".new-dl-link" in blob
    # provenance recorded
    assert doc.get("source_capture", "").startswith("drift_repair:")


def test_never_enables_template():
    dd = _drafts()
    res = dr.propose_repairs(
        "site.example", broken_selectors=[".old"], dom_excerpt="<html>",
        drafts_dir=dd, status_fn=_ok_status, ai_fn=_repair_fn_ok)
    assert res["repairs"] == 1
    doc = json.loads(list(dd.glob("*.template-draft.json"))[0].read_text("utf-8"))
    assert doc["status"] != "enabled"


def test_proposed_payload_shape():
    dd = _drafts()
    res = dr.propose_repairs(
        "site.example", broken_selectors=[".old-dl"], dom_excerpt="<html>",
        drafts_dir=dd, status_fn=_ok_status, ai_fn=_repair_fn_ok)
    assert res["proposed"][0]["new_selector"] == ".new-dl-link"
    assert res["proposed"][0]["role"] == "row_selectors"


# ── scheduled sweep (toggle + provider) ──────────────────────────────────────
def test_scheduled_disabled_noop():
    import bulk_downloader.global_config as gc
    orig = gc.get
    try:
        gc.get = lambda k, d=None: False if k == dr.ENABLE_KEY else d
        out = dr.scheduled_drift_repair()
        assert out == {"ran": False, "reason": "disabled"}
    finally:
        gc.get = orig


def test_scheduled_enabled_no_context_skips():
    import bulk_downloader.global_config as gc
    import bulk_downloader.selector_drift as sd
    orig_gc = gc.get
    orig_sa = sd.status_all
    try:
        gc.get = lambda k, d=None: True if k == dr.ENABLE_KEY else d
        sd.status_all = lambda: [
            {"site_id": "stalesite", "flagged_stale": True,
             "last_selector": ".old-dl"}]
        dd = _drafts()
        out = dr.scheduled_drift_repair(
            dom_provider=lambda sid: None, drafts_dir=dd, reviewed_dir=dd,
            status_fn=_ok_status, ai_fn=_repair_fn_ok)
        assert out["ran"] is True
        assert out["considered"] == 1
        assert out["repaired"] == 0
        assert out["skipped"] == 1
        assert list(dd.glob("*.template-draft.json")) == []
    finally:
        gc.get = orig_gc
        sd.status_all = orig_sa


def test_scheduled_enabled_with_context_repairs():
    import bulk_downloader.global_config as gc
    import bulk_downloader.selector_drift as sd
    orig_gc = gc.get
    orig_sa = sd.status_all
    try:
        gc.get = lambda k, d=None: True if k == dr.ENABLE_KEY else d
        sd.status_all = lambda: [
            {"site_id": "ctxsite", "flagged_stale": True,
             "last_selector": ".old-dl"}]
        dd = _drafts()
        ctx = lambda sid: {"dom_excerpt": "<html>x</html>",
                           "page_url": "https://ctxsite/x",
                           "host": "ctxsite",
                           "broken_selectors": [".old-dl"]}
        out = dr.scheduled_drift_repair(
            dom_provider=ctx, drafts_dir=dd, reviewed_dir=dd,
            status_fn=_ok_status, ai_fn=_repair_fn_ok)
        assert out["ran"] is True
        assert out["considered"] == 1
        assert out["repaired"] == 1
        assert len(list(dd.glob("*.template-draft.json"))) == 1
    finally:
        gc.get = orig_gc
        sd.status_all = orig_sa



def test_default_dom_provider_returns_none():
    # the in-sandbox default has no capture store -> None -> sweep skips
    assert dr._default_dom_provider("anysite") is None
