"""v3.66.757 -- MOD-1 Architecture A, folded cut A-1 + A-2 + A-3.

Remote captcha-takeover: screencast the solve browser to the cockpit and inject
operator input, wired end-to-end in ONE cut so both backend routes are BORN
WIRED by the viewer (unwired_operator_endpoints delta = 0; no dark-endpoint
classification needed).

RED-first on pristine v3.66.756 (routes/helpers/FE wiring all ABSENT):

  A-1  GET  /cockpit/api/takeover/<sid>/screencast   -> 404 on pristine
       (after: text/event-stream SSE for a `solving` sid).
  A-2  POST /cockpit/api/takeover/<sid>/input         -> 404 on pristine
       security contract (each fails RED because the route/helper is absent):
         * lives under a CSRF_GUARDED_PREFIX (auth+CSRF inherited)  [structural]
         * sid-binding: unknown / non-`solving` sid is rejected      [behavioral]
         * CDP-input allowlist validator rejects a non-allowlisted method [unit]
         * audit redaction: injected text is hashed, never stored raw [unit]
         * per-sid token bucket: floods 429 after the burst           [unit]
  A-3  cockpit viewer wiring: a TakeoverViewer component + spa_wired FULL
       literals for BOTH routes                                       [source]

All of the below MUST fail on pristine. After the folded implementation they go
green. The auth/CSRF legs are pinned structurally because the app's test client
runs with TESTING=True (global auth + CSRF are bypassed in-test), so a 401/403
cannot be exercised through fresh_app -- the guaranteed guard is that the routes
sit under CSRF_GUARDED_PREFIXES, which is what we assert instead.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


FE_SRC = _repo_root() / "frontend" / "src"

SCREENCAST_LITERAL = "/cockpit/api/takeover/${sid}/screencast"
INPUT_LITERAL = "/cockpit/api/takeover/${sid}/input"


def _seed_solving_sid(sid="tk-red-1", url="https://example.test/challenge"):
    """Put a captcha into `solving` with solve_session_id == sid, so the
    takeover routes have a legitimate binding target. Returns (sid, url)."""
    from bulk_downloader import captcha_relay as cr
    with cr._lock:
        cr._pending[url] = cr.PendingCaptcha(
            url=url, site_id="s1", captcha_type="turnstile", detected_at=0.0,
            status="solving", solve_session_id=sid,
        )
    return sid, url


def _clear_pending():
    from bulk_downloader import captcha_relay as cr
    with cr._lock:
        cr._pending.clear()


# ── A-1: screencast SSE route ───────────────────────────────────────────────

def test_a1_screencast_route_exists_and_streams(fresh_app):
    """GET the screencast route for a `solving` sid -> SSE (text/event-stream).
    RED on pristine: route absent -> 404."""
    sid, _url = _seed_solving_sid()
    try:
        # buffered=False so an infinite SSE loop is not fully consumed (which
        # would hang the client). Read status + headers + the first chunk, then
        # close -> GeneratorExit -> the route's finally-teardown fires.
        r = fresh_app.get(
            f"/cockpit/api/takeover/{sid}/screencast", buffered=False)
        assert r.status_code == 200, (
            "screencast route missing (pristine 404) -- expected 200 SSE. "
            f"got {r.status_code}"
        )
        assert r.headers.get("Content-Type", "").startswith("text/event-stream")
        first = next(r.response)  # streaming iterable; immediate open comment
        assert first, "no initial SSE chunk emitted"
        r.close()
    finally:
        _clear_pending()


def test_a1_screencast_unknown_sid_not_streamed(fresh_app):
    """An sid with no `solving` binding must NOT get a stream (404/403/410).
    RED on pristine: route absent -> 404 (passes for the wrong reason on
    pristine; after impl it must still reject, so this pins the binding)."""
    _clear_pending()
    r = fresh_app.get("/cockpit/api/takeover/ghost-sid/screencast")
    assert r.status_code in (403, 404, 410)


# ── A-2: input injection route + security contract ──────────────────────────

def test_a2_input_route_exists(fresh_app):
    """POST the input route for a `solving` sid with an allowlisted event ->
    accepted (200/204). RED on pristine: route absent -> 404.

    Input requires an OPEN takeover channel -- in the real flow the operator's
    screencast subscription (GET .../screencast) opens it before any input is
    sent. Establish that precondition explicitly here (v3.66.760: the A-5b
    SSE-disconnect teardown now closes the channel when a viewer stream ends,
    so this test can no longer rely on a channel leaked from test_a1)."""
    from bulk_downloader import takeover
    sid, _url = _seed_solving_sid()
    takeover.open_channel(sid)  # mirrors the viewer's screencast subscription
    try:
        r = fresh_app.post(
            f"/cockpit/api/takeover/{sid}/input",
            json={"type": "mouseMoved", "x": 10, "y": 10},
        )
        assert r.status_code in (200, 204), (
            f"input route missing (pristine 404) -- got {r.status_code}"
        )
    finally:
        takeover.close_channel(sid)
        _clear_pending()


def test_a2_input_route_under_csrf_guarded_prefix():
    """Structural: the input path sits under a CSRF_GUARDED_PREFIX, so the
    global before_request auth + CSRF apply. RED on pristine: no route is
    registered whose rule starts with the takeover prefix."""
    import bulk_downloader.app as bd_app
    takeover_rules = [
        r.rule for r in bd_app.app.url_map.iter_rules()
        if "/cockpit/api/takeover/" in r.rule
    ]
    assert takeover_rules, "no /cockpit/api/takeover/ routes registered (pristine)"
    for rule in takeover_rules:
        assert rule.startswith(bd_app.CSRF_GUARDED_PREFIXES), (
            f"takeover route {rule} is not under a CSRF_GUARDED_PREFIX"
        )


def test_a2_input_sid_binding_rejects_non_solving(fresh_app):
    """An sid that is not in `solving` state is rejected. RED on pristine:
    route absent -> 404 (correct rejection code); after impl a resolved sid
    must 410 and an unknown sid 404, never 200."""
    from bulk_downloader import captcha_relay as cr
    _clear_pending()
    with cr._lock:
        cr._pending["https://x.test/"] = cr.PendingCaptcha(
            url="https://x.test/", site_id="s1", captcha_type="turnstile",
            detected_at=0.0, status="resolved", solve_session_id="tk-done",
        )
    try:
        r = fresh_app.post(
            "/cockpit/api/takeover/tk-done/input",
            json={"type": "mouseMoved", "x": 1, "y": 1},
        )
        assert r.status_code in (403, 404, 410), (
            f"resolved-sid input must be rejected, got {r.status_code}"
        )
    finally:
        _clear_pending()


def test_a2_cdp_input_allowlist_validator_exists():
    """A pure validator gates the CDP Input subset -- allowlisted types pass,
    everything else (esp. navigation / arbitrary dispatch) is rejected.
    RED on pristine: the helper module/function does not exist -> ImportError."""
    from bulk_downloader import takeover  # noqa: F401  (absent on pristine)
    ok = takeover.validate_input_event({"type": "mouseMoved", "x": 0, "y": 0})
    assert ok is True
    for bad in (
        {"type": "Page.navigate", "url": "http://evil.test"},
        {"type": "dispatchKeyEvent", "raw": "../../etc"},
        {"type": "clientCutText"},
        {"type": ""},
    ):
        assert takeover.validate_input_event(bad) is False, bad


def test_a2_injected_text_is_redacted_in_audit():
    """insertText payloads must be hashed/redacted before audit, never stored
    raw. RED on pristine: helper absent -> ImportError."""
    from bulk_downloader import takeover
    secret = "hunter2-solved-token"
    red = takeover.redact_input_for_audit({"type": "insertText", "text": secret})
    blob = repr(red)
    assert secret not in blob, "raw injected text leaked into the audit record"


def test_a2_per_sid_token_bucket_floods_429():
    """A per-sid token bucket bounds input rate. RED on pristine: absent ->
    ImportError."""
    from bulk_downloader import takeover
    bucket = takeover.InputRateBucket(capacity=3, refill_per_s=0.0)
    allowed = [bucket.allow("sid-a") for _ in range(6)]
    assert allowed[:3] == [True, True, True]
    assert allowed[3] is False, "bucket did not throttle after the burst"
    # per-sid isolation: a different sid has its own budget
    assert bucket.allow("sid-b") is True


# ── A-3: cockpit viewer wiring (spa_wired FULL literals) ─────────────────────

def _fe_blob() -> str:
    src = ""
    for dp, dn, fns in os.walk(FE_SRC):
        dn[:] = [d for d in dn if d not in ("node_modules", "dist")]
        for fn in sorted(fns):
            if fn.endswith((".ts", ".tsx")):
                try:
                    src += (Path(dp) / fn).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
    return src


def test_a3_takeover_viewer_component_exists():
    """A TakeoverViewer surface renders the screencast + forwards input.
    RED on pristine: component file absent."""
    hits = list(FE_SRC.rglob("TakeoverViewer.tsx"))
    assert hits, "frontend/src/**/TakeoverViewer.tsx is absent (pristine)"


def test_a3_screencast_literal_spa_wired():
    """FE references the FULL screencast literal (spa_wired counts full
    /cockpit/api/ literals, not a concatenated base var). RED on pristine."""
    blob = _fe_blob()
    assert SCREENCAST_LITERAL in blob, (
        f"FE does not wire the full literal {SCREENCAST_LITERAL} (pristine)"
    )


def test_a3_input_literal_spa_wired():
    """FE references the FULL input literal. RED on pristine."""
    blob = _fe_blob()
    assert INPUT_LITERAL in blob, (
        f"FE does not wire the full literal {INPUT_LITERAL} (pristine)"
    )
