"""E1 (plugin-v3): capture.* lifecycle events — capture.started / capture.done.

RED-first (v3.66.502). These are the program's ONE declared GUARD cut: the emit
lives in tools/capture_session.py:run() (guard #3, 430f5070), the universal
capture entry every flow spawns. run() is the once-per-run lifecycle owner;
session_capture.capture_via_cdp fires per-page and to_capture_dict is multi-called,
so neither is a clean once-per-run seam.

Sandbox conventions: derive repo root from __file__ (+ tools/ on sys.path);
zero-arg fns; no pytest builtins; reset plugin registries in try/finally.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bulk_downloader import plugins as P  # noqa: E402
import capture_session as CS  # noqa: E402


def _collect(event):
    """Register a recording hook on `event`; return (fired_list, cleanup)."""
    P.reset()
    fired = []
    P.register_hook(event, lambda payload: fired.append(payload))
    return fired, P.reset


# ── events are documented in the HOOK_EVENTS registry (R3 golden source) ──
def test_capture_events_documented_in_hook_events():
    assert "capture.started" in P.HOOK_EVENTS
    assert "capture.done" in P.HOOK_EVENTS
    started_doc = P.HOOK_EVENTS["capture.started"]
    done_doc = P.HOOK_EVENTS["capture.done"]
    # documented payload key-sets (the golden derives keys from this text)
    assert "url" in started_doc and "ts" in started_doc
    assert "url" in done_doc and "network_count" in done_doc and "ts" in done_doc


# ── the producer helper fires with the documented payload ─────────────
def test_emit_capture_started_fires_with_payload():
    fired, cleanup = _collect("capture.started")
    try:
        CS._emit_capture_event("capture.started", {"url": "https://x", "ts": 1})
        assert len(fired) == 1
        assert set(fired[0]) >= {"url", "ts"}
        assert fired[0]["url"] == "https://x"
    finally:
        cleanup()


def test_emit_capture_done_fires_with_payload():
    fired, cleanup = _collect("capture.done")
    try:
        CS._emit_capture_event("capture.done",
                               {"url": "https://x", "network_count": 5, "ts": 2})
        assert len(fired) == 1
        assert set(fired[0]) >= {"url", "network_count", "ts"}
        assert fired[0]["network_count"] == 5
    finally:
        cleanup()


# ── a throwing consumer never breaks the producer ─────────────────────
def test_emit_is_isolated_from_throwing_consumer():
    P.reset()

    def _boom(_payload):
        raise RuntimeError("consumer blew up")

    P.register_hook("capture.started", _boom)
    try:
        CS._emit_capture_event("capture.started", {"url": "u", "ts": 0})  # must NOT raise
    finally:
        P.reset()


# ── run() emits each exactly once (structural once-per-run guarantee) ──
def test_run_emits_started_and_done_exactly_once():
    src = Path(CS.__file__).read_text("utf-8")
    assert src.count('_emit_capture_event("capture.started"') == 1
    assert src.count('_emit_capture_event("capture.done"') == 1
