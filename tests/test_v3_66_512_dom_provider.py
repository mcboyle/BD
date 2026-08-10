"""v3.66.512 — C2/F3.2: a real default dom_provider for scheduled_drift_repair.

`_default_dom_provider` was a stub that always returned None, so the daily
drift sweep repaired ZERO sites even with the toggle on. This builds repair
context from the capture store: the latest capture for the site's host ->
redacted DOM -> the site's configured selectors split into broken (0 matches)
vs working. Review-only / fail-open; the DOM excerpt is the F2-redacted html
(redacted_dom fails closed on residual secrets).

Harness: zero-arg functions, tempfile.mkdtemp, restore module globals in
try/finally (no monkeypatch fixture). Uses the shipped redacted WACZ fixture.
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from bulk_downloader import drift_repair as dr
from capture_test_fixtures import capture_fixture_lane

_CAPTURE_FIXTURES = capture_fixture_lane(allow_synthetic=True)

# www.miruro.tv DOM: 'a' matches (working), 'button' matches 0 (broken).
CFG = {"start_url": "https://www.miruro.tv/",
       "trigger_selector": "a", "dl_selector": "button"}


def _fixture():
    if not _CAPTURE_FIXTURES.enabled:
        pytest.skip("vidstack capture artifact not enabled")
    fixture = (
        _CAPTURE_FIXTURES.root / "tests" / "fixtures" / "vidstack"
        / "miruro.redacted.wacz"
    )
    if not fixture.is_file():
        pytest.skip(
            "vidstack capture artifact not present under "
            f"{_CAPTURE_FIXTURES.env_name}: {fixture.name}"
        )
    return fixture


def _captures_root():
    fixture = _fixture()
    root = Path(tempfile.mkdtemp(prefix="dompv_"))
    capdir = root / "captures"
    capdir.mkdir(parents=True)
    shutil.copy(fixture, capdir / fixture.name)
    return root


def test_default_dom_provider_builds_ctx_from_capture():
    root = _captures_root()
    ctx = dr._default_dom_provider("miru", cfg=CFG, captures_root=str(root))
    assert ctx is not None
    assert ctx["host"] == "www.miruro.tv"
    assert ctx["page_url"] == "https://www.miruro.tv/"
    assert ctx["dom_excerpt"]                      # non-empty redacted html
    assert len(ctx["dom_excerpt"]) <= 24000        # bounded
    assert "button" in ctx["broken_selectors"]     # 0 matches -> broken
    assert "a" in ctx["working_selectors"]         # matches -> working
    assert "a" not in ctx["broken_selectors"]


def test_default_dom_provider_none_when_no_capture_for_host():
    root = _captures_root()                          # only carries www.miruro.tv
    ctx = dr._default_dom_provider(
        "other", cfg={"start_url": "https://nomatch.example/"},
        captures_root=str(root))
    assert ctx is None


def test_default_dom_provider_none_without_resolvable_host():
    root = _captures_root()
    assert dr._default_dom_provider("x", cfg={}, captures_root=str(root)) is None


def test_default_dom_provider_unknown_site_still_none():
    # the live-default (no cfg, no host for an unknown site) stays inert.
    assert dr._default_dom_provider("totally-unknown-site") is None


def test_provider_feeds_scheduled_sweep():
    import bulk_downloader.global_config as gc
    import bulk_downloader.selector_drift as sd
    root = _captures_root()
    dd = Path(tempfile.mkdtemp(prefix="drift_drafts_"))
    orig_gc, orig_sa = gc.get, sd.status_all

    def _ok_status():
        return {"ok": True, "enabled": True, "provider": "ollama"}

    def _repair_fn_ok(broken, working, dom, *, page_url=""):
        return {"ok": True, "repairs": [
            {"old_selector": broken[0], "new_selector": ".new",
             "role": "row_selectors", "confidence": 80, "reasoning": "moved"}],
            "removed": []}

    try:
        gc.get = lambda k, d=None: True if k == dr.ENABLE_KEY else d
        sd.status_all = lambda: [
            {"site_id": "miru", "flagged_stale": True, "last_selector": "button"}]
        out = dr.scheduled_drift_repair(
            dom_provider=lambda sid: dr._default_dom_provider(
                sid, cfg=CFG, captures_root=str(root)),
            drafts_dir=dd, reviewed_dir=dd,
            status_fn=_ok_status, ai_fn=_repair_fn_ok)
        assert out["ran"] is True
        assert out["considered"] == 1
        assert out["repaired"] == 1                 # provider gave context -> repaired
        assert len(list(dd.glob("*.template-draft.json"))) == 1
    finally:
        gc.get = orig_gc
        sd.status_all = orig_sa
