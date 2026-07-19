"""BP-VH2 (v3.66.282): watchForVerdict must handle all 7 runner statuses so no
status produces a silent 30s/45min no-verdict. The 5 terminal statuses resolve;
running/pending keep waiting. SPA-scan (the live UI is stash-only).
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "frontend" / "src" / "routes" / "CaptureWorkflow.tsx").read_text()


def test_all_five_terminal_statuses_in_terminal_set():
    # the terminal Set previously held only done/failed/needs_review; the silent
    # gap was skipped_duplicate + stopped waiting out MAX_WALL.
    for s in ("done", "failed", "needs_review", "skipped_duplicate", "stopped"):
        assert f'"{s}"' in SRC, f"missing terminal status {s}"


def test_live_job_at_terminal_status_resolves_immediately():
    assert "terminal.has(live.status" in SRC


def test_running_and_pending_are_the_nonterminal_statuses():
    assert 'live.status === "running"' in SRC
    assert 'live.status === "pending"' in SRC
