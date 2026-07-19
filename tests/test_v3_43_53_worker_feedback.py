"""v3.43.53: worker-success feedback into keeper state.

Bug: the Sessions UI showed "disconnected" for sites where workers
were actively succeeding at downloads. The keeper's heartbeat
browser can fail for reasons (captcha, stale cookie, blocked IP)
while workers using a different profile + on-disk cookies download
just fine. The UI was showing keeper state with no knowledge of
worker success.

Fix:
  1. session_keeper.note_worker_success(site_id) records a
     last_worker_success_ts on the matching keeper(s).
  2. Runner._update_job calls this when a job transitions to "done".
  3. UI: if last_worker_success_ts is within last 5 minutes AND
     keeper state is disconnected/needs_takeover, the UI overrides
     the state to "connected" and labels the pill "worker
     confirmed N seconds ago".

This is a UI override only — the keeper itself continues to run
its own heartbeat cadence and may eventually recover or stay
broken; what changes is whether the user is alarmed when the
system is actually working.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── note_worker_success helper ───────────────────────────────────


def test_note_worker_success_exists():
    from bulk_downloader import session_keeper as sk
    assert hasattr(sk, "note_worker_success")
    assert callable(sk.note_worker_success)


def test_note_worker_success_records_ts_on_matching_keeper():
    """A success notification updates last_worker_success_ts on
    keepers matching the site_id."""
    from bulk_downloader import session_keeper as sk
    import time as _time

    keeper = mock.MagicMock()
    keeper.state = {}
    saved = dict(sk._keepers)
    try:
        sk._keepers.clear()
        sk._keepers[("siteX", 0)] = keeper
        before = _time.time()
        sk.note_worker_success("siteX")
        after = _time.time()
        ts = keeper.state.get("last_worker_success_ts")
        assert ts is not None
        assert before <= ts <= after
    finally:
        sk._keepers.clear()
        sk._keepers.update(saved)


def test_note_worker_success_no_op_on_unknown_site():
    """Calling for a site with no keeper is a safe no-op."""
    from bulk_downloader import session_keeper as sk
    # Shouldn't raise
    sk.note_worker_success("nonexistent_site_xyz")


def test_note_worker_success_swallows_state_errors():
    """A keeper whose state dict is in a partial / readonly state
    must not cause note_worker_success to raise."""
    from bulk_downloader import session_keeper as sk

    keeper = mock.MagicMock()
    # MagicMock returns truthy from .state[key] = val, so simulate
    # an exception path
    keeper.state = mock.MagicMock()
    keeper.state.__setitem__.side_effect = TypeError("readonly")
    saved = dict(sk._keepers)
    try:
        sk._keepers.clear()
        sk._keepers[("siteX", 0)] = keeper
        # Should not raise
        sk.note_worker_success("siteX")
    finally:
        sk._keepers.clear()
        sk._keepers.update(saved)


# ── Runner wires note_worker_success on done ─────────────────────


def test_runner_calls_note_worker_success_on_done():
    """_update_job must call note_worker_success when status
    transitions to 'done'."""
    src = (_REPO_ROOT / "bulk_downloader" / "runner.py").read_text(encoding="utf-8")
    # Find the relevant block
    pos = src.find('if status in ("done", "failed", "needs_review") and prev_status != status:')
    assert pos > 0
    body = src[pos:pos + 3000]
    assert "note_worker_success" in body
    # And it's gated on status == "done" (not failed/needs_review)
    assert 'if status == "done":' in body


def test_runner_worker_success_call_is_best_effort():
    """The note_worker_success call must be wrapped in try/except
    so a keeper module failure doesn't abort the download flow."""
    src = (_REPO_ROOT / "bulk_downloader" / "runner.py").read_text(encoding="utf-8")
    pos = src.find("note_worker_success")
    assert pos > 0
    # Look backwards for the enclosing try
    before = src[max(0, pos - 500):pos]
    after = src[pos:pos + 500]
    assert "try:" in before
    assert "except" in after


# ── UI override logic ─────────────────────────────────────────────


# ── Defensive: stale worker_success_ts doesn't keep stale 'connected' forever ─


