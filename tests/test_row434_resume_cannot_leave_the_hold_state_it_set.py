"""Row 434 -- resume() cannot leave the hold state it set itself.

MEASURED CONTEXT.  ``SiteRunner.resume()`` acts only from
``("paused", "low_disk", "paused_no_button")``, but its own hold-refusal branch
sets ``_state`` to ``download_held`` / ``download_hold_unknown`` -- neither of
which is in that tuple.  One hold-refused Resume click therefore converts a
resumable runner into a state ``resume()`` can never leave.

Lifting the hold does not recover it: the lift endpoint touches no runner, and
the next Resume runs ``_do_action -> runner.resume() -> silent no-op -> ok:true``.
The operator is told the resume succeeded while the runner keeps publishing
``download_held`` against a hold ``GET /api/download_hold`` reports CLEAR, and
``/api/resume_all`` skips it identically forever.  Recovery needs stop()+start().

The asymmetry is the tell: the resumable tuple was widened to include
``low_disk``, but the hold tokens -- set FROM those very states -- were not.

WHAT THIS FILE PROVES

  1. THE VERDICT.  A paused runner refused by a hold recovers on the next
     resume() once the hold is lifted, reaching ``running`` with ``_pause`` set.
     RED on the defective parent: it stays ``download_held`` forever.
  2. NEGATIVE CONTROL -- THE HOLD STILL BINDS.  While the hold STANDS, resume()
     keeps refusing with the distinctive ``download_hold`` event and never
     flips to running, however many times it is called.
  3. NEGATIVE CONTROL -- NO WORKER-LESS "running".  A runner refused by
     ``start()`` (which returns BEFORE spawning any worker) must NOT be flipped
     to ``running`` by a later resume(): that would replace one operator lie
     with a worse one -- a runner reporting ``running`` with zero workers.
  4. UNKNOWN IS NOT OK (CLAUDE.md A7).  An unmeasurable hold refuses with
     ``download_hold_unknown`` rather than a silent OK resume, and the runner
     is still recoverable once the store becomes measurable again.

Preconditions are asserted, never assumed: the pause is read back before the
hold is placed, the store is read back after every write, and the refusal event
count is exact.
"""
from __future__ import annotations

import json
import threading

import pytest


BD_GATE_SCOPE = "module"


@pytest.fixture()
def hold_store(tmp_path, monkeypatch):
    """Point the durable hold store at an isolated file and prove it is empty."""
    from bulk_downloader import download_hold, global_config

    store = tmp_path / "app_config.json"
    monkeypatch.setattr(global_config, "_CONFIG_FILE", store)
    monkeypatch.setattr(global_config, "_cached", None)
    monkeypatch.setattr(global_config, "_cached_mtime", 0.0)
    assert download_hold._store_path() == store, (
        "the isolated store was not adopted; this test would read the host's "
        "real app_config.json")
    assert download_hold.hold_state()["state"] == download_hold.CLEAR, (
        "the isolated store did not start CLEAR")
    return store


class _EventLog:
    def __init__(self, runner):
        self.events = []
        runner.log_event = self._record

    def _record(self, kind, message, extra=None):
        self.events.append((kind, message, dict(extra or {})))

    def count(self, kind):
        return sum(1 for e in self.events if e[0] == kind)


def _paused_runner(site_id):
    """A runner in the real ``paused`` state, driven through the real verb."""
    from bulk_downloader.runner import SiteRunner

    r = SiteRunner(site_id, {"name": site_id, "max_concurrent": 1})
    r._state = "running"
    r.pause()
    assert r._state == "paused", (
        "the fixture did not reach the real paused state: %r" % (r._state,))
    assert not r._pause.is_set(), (
        "pause() did not clear the pause gate, so the resume path below would "
        "not be the one under test")
    return r


def test_a_hold_refused_resume_recovers_once_the_hold_is_lifted(hold_store):
    from bulk_downloader import download_hold

    r = _paused_runner("row-434-verdict")
    log = _EventLog(r)

    assert download_hold.hold(reason="row-434", by="test") is True, (
        "the fixture could not write the durable hold")
    held = download_hold.hold_state()
    assert held["state"] == download_hold.HELD, (
        "the hold was not read back as HELD: %r" % (held,))

    # -- the refusal that creates the trap ---------------------------------
    r.resume()
    assert r._state == download_hold.STATE_HELD, (
        "resume() under a hold must publish the distinctive held token: %r"
        % (r._state,))
    assert log.count("download_hold") == 1, (
        "the refusal must log exactly one download_hold event: %r"
        % (log.events,))
    assert not r._pause.is_set(), (
        "the refusal must leave the pause gate cleared")

    # -- the operator lifts the hold; the lift touches no runner -----------
    assert download_hold.lift(by="test") is True, "the lift was not written"
    allowed, state = download_hold.downloads_allowed()
    assert allowed is True and state["state"] == download_hold.CLEAR, (
        "the hold did not read back as CLEAR after the lift: %r" % (state,))
    assert r._state == download_hold.STATE_HELD, (
        "precondition: the runner still carries the held token before the "
        "recovery resume, otherwise the verdict below is vacuous")

    # -- the verdict: exactly one further resume() must recover it ---------
    r.resume()
    assert r._state == "running", (
        "resume() after the hold was lifted left the runner in %r; the "
        "operator was told ok:true while the runner is permanently stuck and "
        "/api/resume_all will skip it forever" % (r._state,))
    assert r._pause.is_set(), (
        "the runner reached 'running' without releasing the pause gate")
    assert log.count("download_hold") == 1, (
        "the recovery resume must not log a second refusal: %r" % (log.events,))


def test_resume_still_refuses_while_the_hold_stands(hold_store):
    """NEGATIVE CONTROL. A genuinely held runner must not resume."""
    from bulk_downloader import download_hold

    r = _paused_runner("row-434-still-held")
    log = _EventLog(r)

    assert download_hold.hold(reason="row-434-standing", by="test") is True
    assert download_hold.hold_state()["state"] == download_hold.HELD

    r.resume()
    assert r._state == download_hold.STATE_HELD
    assert log.count("download_hold") == 1, (
        "exactly one refusal event for one refused resume: %r" % (log.events,))
    assert log.events[0][2].get("hold_state") == download_hold.HELD, (
        "the refusal event must name the measured hold state: %r"
        % (log.events[0],))
    assert not r._pause.is_set()

    # Repeated Resume clicks while the hold STANDS must keep refusing.
    r.resume()
    assert r._state == download_hold.STATE_HELD, (
        "a second resume under a standing hold flipped the runner to %r"
        % (r._state,))
    assert not r._pause.is_set(), (
        "a standing hold was defeated: the pause gate was released")
    # Deliberately NOT an exact count here: whether the second click re-logs is
    # the behaviour the fix changes (the parent's second call is a silent
    # no-op).  The control's subject is the REFUSAL, and every event this
    # runner logged must be a hold refusal naming the measured HELD state.
    assert log.events, "the control logged nothing at all"
    assert all(kind == "download_hold"
               and extra.get("hold_state") == download_hold.HELD
               for kind, _msg, extra in log.events), (
        "a standing hold produced a non-refusal event: %r" % (log.events,))


def test_a_start_refused_runner_is_not_resumed_into_a_worker_less_running(
        hold_store):
    """NEGATIVE CONTROL. start() refuses BEFORE spawning any worker.

    Recovering that runner through resume() would publish ``running`` for a
    runner with zero worker threads -- the fix reproducing the defect's shape
    (CLAUDE.md A7), trading one operator lie for a worse one.
    """
    from bulk_downloader import download_hold
    from bulk_downloader.runner import SiteRunner

    r = SiteRunner("row-434-start", {"name": "row-434-start",
                                     "max_concurrent": 1})
    log = _EventLog(r)
    assert r._state != "running", (
        "precondition: the fixture runner must not already be running")

    assert download_hold.hold(reason="row-434-start", by="test") is True
    assert download_hold.hold_state()["state"] == download_hold.HELD

    r.start()
    assert r._state == download_hold.STATE_HELD, (
        "start() under a hold must publish the held token: %r" % (r._state,))
    assert r._worker_threads == [], (
        "start() spawned workers under a hold; the refusal did not fire")

    assert download_hold.lift(by="test") is True
    assert download_hold.downloads_allowed()[0] is True

    r.resume()
    assert r._state != "running", (
        "resume() flipped a START-refused runner to 'running'; there are no "
        "worker threads, so this is a new operator lie: %r"
        % (r._worker_threads,))
    assert r._worker_threads == [], (
        "resume() must never be the path that spawns workers")


def test_an_unmeasurable_hold_refuses_and_stays_recoverable(hold_store):
    """CLAUDE.md A7: UNKNOWN is a failing third state, never a silent OK."""
    from bulk_downloader import download_hold

    r = _paused_runner("row-434-unknown")
    log = _EventLog(r)

    # A malformed record is unmeasurable, not "no hold, carry on".
    hold_store.write_text(json.dumps({download_hold.HOLD_KEY: "yes"}),
                          encoding="utf-8")
    unknown = download_hold.hold_state()
    assert unknown["state"] == download_hold.UNKNOWN, (
        "the fixture did not produce an UNKNOWN store: %r" % (unknown,))

    r.resume()
    assert r._state == download_hold.STATE_UNKNOWN, (
        "an unmeasurable hold must refuse with its own distinctive token, "
        "distinguishable from a measured hold: %r" % (r._state,))
    assert r._state != download_hold.STATE_HELD
    assert not r._pause.is_set(), (
        "an unmeasurable hold silently admitted the resume")
    assert log.count("download_hold") == 1, (
        "the UNKNOWN refusal must log exactly one event: %r" % (log.events,))
    assert log.events[0][2].get("hold_state") == download_hold.UNKNOWN

    # Once the store is measurable and CLEAR again, the runner recovers.
    assert download_hold.lift(by="test") is True
    assert download_hold.downloads_allowed()[0] is True
    r.resume()
    assert r._state == "running", (
        "a runner refused by an UNMEASURABLE hold stayed stuck after the "
        "store became measurable: %r" % (r._state,))
    assert r._pause.is_set()

