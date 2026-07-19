"""B1 — event-bus extension.

Job-lifecycle events append to the EXISTING per-runner event feed (the same
_event_log the sidebar polls via /api/events_all with an `after=` cursor and
useEventStream consumes). The integration seam is run_history.emit_lifecycle(
runner, phase, ...): it records a run_events row AND fires runner.log_event with
a `run_<phase>` kind, so a lifecycle event is visible through get_events()
exactly like every other event kind. No new feed, no new endpoint.

The emit is advisory (fail-open) like log_event itself.

RED-first: run_history.emit_lifecycle does not exist on pristine source.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _runner():
    # SiteRunner constructs without Playwright/network; it only touches the
    # event ring buffer + a screenshots dir under BD_HOME (isolated by the
    # runner env). Construct a bare one for the event-feed contract.
    from bulk_downloader.runner import SiteRunner
    return SiteRunner("evtbus_site", {"name": "EvtBus"})


def test_emit_lifecycle_shows_in_event_feed():
    from bulk_downloader import db, run_history as rh
    db.db_init(); rh.init()
    r = _runner()
    before = r._event_seq
    rid = rh.record_run_start("evtbus_site", "https://example.test/x")
    rh.emit_lifecycle(r, "finish", run_id=rid, url="https://example.test/x",
                      message="completed")
    evs = r.get_events(after_seq=before, kind_filter="run_finish")
    assert evs, "run_finish lifecycle event must appear in the runner feed"
    assert evs[0]["url"] == "https://example.test/x"


def test_emit_lifecycle_is_advisory():
    from bulk_downloader import run_history as rh

    class _BrokenRunner:
        def log_event(self, *a, **k):
            raise RuntimeError("broken feed")

    # Must NOT raise even if the underlying feed is broken.
    rh.emit_lifecycle(_BrokenRunner(), "start", run_id=1, url="u", message="m")
