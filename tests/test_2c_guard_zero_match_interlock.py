"""2c-guard (v3.66.513): live-trigger zero-match enable interlock.

RED-first. A reviewed template whose download trigger matches 0 elements on the
live page can silently drive a dead run (the reptyle case: stored trigger ->
0 live matches). This adds a SOFT, non-blocking pre-enable warning:

  - `template_manager.promote_gate_warnings(t, *, trigger_match_count=None)`
    returns a one-item warn list when the live trigger match count is 0, and
    `[]` otherwise (>0, or None == "not checked"). It is a WARN, never an
    error -- a live fetch can transiently fail, so a hard block would be brittle.
    Fail-open.

  - `/api/template_manager/promote_check` accepts an optional
    `trigger_match_count` in the body and surfaces the warn as `gate_warnings`
    WITHOUT flipping `ok` to False (the existing gate_errors still hard-block).

This is the systemic guard; the live match count itself is produced by the
existing /api/template/sandbox (or /api/playground/test) live fetch on the FE,
so it is injected here as an int -> fully sandbox-testable.
"""
import json
import tempfile
from pathlib import Path

from bulk_downloader import template_manager as tm


def _norm_template():
    """A minimal already-normalized reviewed-shape template that PASSES
    promote_gate_errors, so the only thing under test is the warn path."""
    return {
        "schema_version": "template/1",
        "host": "app.reptyle.com",
        "network_patterns": ["/api/movie/download"],
        "selectors": {"download": {"trigger": "[aria-label*='Download' i]"}},
        "resolutions": ["1080p"],
    }


def test_promote_gate_warnings_fires_at_zero():
    t = _norm_template()
    warns = tm.promote_gate_warnings(t, trigger_match_count=0)
    assert len(warns) == 1, warns
    assert "0" in warns[0] and "live" in warns[0].lower(), warns


def test_promote_gate_warnings_silent_when_matches():
    t = _norm_template()
    assert tm.promote_gate_warnings(t, trigger_match_count=3) == []


def test_promote_gate_warnings_silent_when_unchecked():
    # None == "no live check was run" -> never warn, never block.
    t = _norm_template()
    assert tm.promote_gate_warnings(t, trigger_match_count=None) == []


def test_promote_gate_warnings_fail_open_on_garbage():
    # A non-int count must not raise (fail-open).
    t = _norm_template()
    assert tm.promote_gate_warnings(t, trigger_match_count="oops") == []


def test_promote_check_surfaces_gate_warnings_without_blocking():
    """The promote_check route returns gate_warnings + keeps ok True when the
    only issue is a 0-match live trigger."""
    from bulk_downloader import app as bd_app
    app = bd_app.app
    # seed a passing draft into the drafts dir the route reads
    draft = {
        "schema_version": "template/1",
        "host": "app.reptyle.com",
        "network_patterns": ["/api/movie/download"],
        "selectors": {"download": {"trigger": "[aria-label*='Download' i]"}},
        "resolutions": ["1080p"],
    }
    fname = "app.reptyle.com.template-draft.json"
    tm.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    (tm.DRAFTS_DIR / fname).write_text(json.dumps(draft), "utf-8")
    try:
        c = app.test_client()
        r = c.post("/api/template_manager/promote_check",
                   json={"file": fname, "trigger_match_count": 0})
        body = r.get_json()
        assert body.get("ok") is True, body
        gw = body.get("gate_warnings") or []
        assert len(gw) == 1 and "live" in gw[0].lower(), body
        # and with a positive count -> no warning
        r2 = c.post("/api/template_manager/promote_check",
                    json={"file": fname, "trigger_match_count": 5})
        assert not (r2.get_json().get("gate_warnings") or []), r2.get_json()
    finally:
        (tm.DRAFTS_DIR / fname).unlink(missing_ok=True)
