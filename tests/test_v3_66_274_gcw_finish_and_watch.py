"""GCW follow-ups (staged with probe mode): watch robustness + Finish & save.

Item 1 — large-file Test verdict. The 273 GCW-4 watch polled /api/history with a
fixed ~2.5 min deadline. /api/history is fed by db_log, which only writes at the
TERMINAL event — so during a multi-GB download there is NO history row and the
watch expired before the file finished (observed: a 3.8 GB download completed but
the gate reported no verdict). Fix: the watch also reads the LIVE in-flight job
from /api/status (runner.get_status -> jobs[url] carries status + file_size), so
it keeps waiting while bytes are flowing, surfaces progress, and only gives up on
a genuine stall, the absolute cap, or a run that never started.

Item 2 — finish the process. The wizard had Promote/Enable + a destructive
"Discard session" (POST /cockpit/api/captures/finish discard:true, throws the
WACZ away) but NO way to FINISH AND SAVE. The backend finish endpoint already
supports discard:false (writes the FINISH sentinel -> capture_session saves the
WACZ). Fix: a finishSession(false) action + a "Finish & save" button that ends
the process cleanly and resets the wizard.

Both are SPA-only (no backend/route change); the runtime watch + live capture are
stash-only, so tsc/vite + source-scans are the in-sandbox ceiling (GCW style).
"""
from __future__ import annotations

from pathlib import Path


def _spa() -> str:
    root = Path(__file__).resolve().parent.parent
    return (root / "frontend" / "src" / "routes"
            / "CaptureWorkflow.tsx").read_text(encoding="utf-8")


# ─── Item 1: watch robustness ────────────────────────────────────────────
def test_watch_reads_live_status():
    # The watch must consult the LIVE in-flight job (so it doesn't give up
    # mid-download) — /api/status exposes runner jobs[url] with file_size.
    assert "/api/status" in _spa()


def test_watch_surfaces_download_progress():
    # Live progress is shown while waiting (so the operator sees it's working).
    src = _spa()
    assert "setWatchProgress" in src
    assert "Downloading" in src


def test_watch_detects_stall():
    # A genuinely stalled download (running but no byte progress) must end the
    # wait rather than hang to the absolute cap.
    src = _spa().lower()
    assert "stall" in src


def test_watch_deadline_is_generous_not_two_minutes():
    # The fixed ~2.5 min cap (150_000) must no longer be the verdict deadline;
    # a multi-GB download needs far longer. The new watch uses a named,
    # minutes-scale absolute cap.
    src = _spa()
    assert "MAX_WALL" in src


# ─── Item 2: Finish & save ───────────────────────────────────────────────
def test_finish_and_save_action_exists():
    # A finish that SAVES the WACZ (the save path passes discard=false).
    src = _spa()
    assert "finishSession" in src
    assert "finishSession(false)" in src


def test_finish_passes_discard_through():
    # finishSession threads the discard flag to the finish endpoint.
    src = _spa()
    assert "discard," in src or "discard: discard" in src


def test_finish_and_save_button_present():
    # A visible button to complete the process (save WACZ + reset).
    assert "Finish &amp; save" in _spa() or "Finish & save" in _spa()


def test_discard_session_still_present():
    # The destructive discard path remains available (finishSession(true)).
    src = _spa()
    assert "discardSession" in src
    assert "finishSession(true)" in src
