"""Row 434 -- resume() can leave the hold state that it set itself.

The public hold tokens deliberately collapse a held and an unmeasurable store
into runner-visible refusal states.  They do not say whether ``start()``
refused before spawning workers or ``resume()`` refused an existing paused
pool.  Recovery therefore needs provenance: only a token published while
resuming one of the real resumable states may return to running after the hold
is lifted.

This file is re-derived from the recovery tag's RED test on current main.  It
also covers the stale-provenance shape that the old tag's implementation could
reproduce when ``start()`` returned early for an empty queue.
"""
from __future__ import annotations

import json

import pytest


BD_GATE_SCOPE = "module"


@pytest.fixture()
def hold_store(tmp_path, monkeypatch):
    """Point the durable hold store at an isolated file and prove it is clear."""
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
        return sum(1 for event in self.events if event[0] == kind)


def _paused_runner(site_id):
    """Drive a runner through the real pause verb and prove the pause gate."""
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner(site_id, {"name": site_id, "max_concurrent": 1})
    runner._state = "running"
    runner.pause()
    assert runner._state == "paused", (
        "the fixture did not reach the real paused state: %r"
        % (runner._state,))
    assert not runner._pause.is_set(), (
        "pause() did not clear the pause gate; resume would not be tested")
    return runner


def _record_hold_reads(monkeypatch, download_hold):
    """Record exact calls through the real hold reader, preserving behavior."""
    real_downloads_allowed = download_hold.downloads_allowed
    states = []

    def measured_downloads_allowed(*args, **kwargs):
        result = real_downloads_allowed(*args, **kwargs)
        states.append(result[1]["state"])
        return result

    monkeypatch.setattr(
        download_hold, "downloads_allowed", measured_downloads_allowed)
    return states


def test_a_hold_refused_resume_recovers_once_the_hold_is_lifted(hold_store):
    from bulk_downloader import download_hold

    runner = _paused_runner("row-434-verdict")
    log = _EventLog(runner)

    assert download_hold.hold(reason="row-434", by="test") is True, (
        "the fixture could not write the durable hold")
    held = download_hold.hold_state()
    assert held["state"] == download_hold.HELD, (
        "the hold was not read back as HELD: %r" % (held,))

    runner.resume()
    assert runner._state == download_hold.STATE_HELD, (
        "resume() under a hold must publish the held token: %r"
        % (runner._state,))
    assert log.count("download_hold") == 1, (
        "one refused resume must log exactly one hold event: %r" % (log.events,))
    assert not runner._pause.is_set(), (
        "the refusal must leave the pause gate cleared")

    assert download_hold.lift(by="test") is True, "the lift was not written"
    allowed, state = download_hold.downloads_allowed()
    assert allowed is True and state["state"] == download_hold.CLEAR, (
        "the hold did not read back as CLEAR after the lift: %r" % (state,))
    assert runner._state == download_hold.STATE_HELD, (
        "precondition: the runner must still carry the held token before "
        "the recovery resume")

    runner.resume()
    assert runner._state == "running", (
        "resume() after lift left the runner in %r; the operator was told "
        "ok:true while the runner stayed stuck" % (runner._state,))
    assert runner._pause.is_set(), (
        "the runner reached running without releasing the pause gate")
    assert log.count("download_hold") == 1, (
        "the successful recovery must not publish another refusal: %r"
        % (log.events,))


def test_repeated_resume_still_measures_and_refuses_a_standing_hold(
        hold_store, monkeypatch):
    """Negative control: recovery provenance must never defeat a live hold."""
    from bulk_downloader import download_hold

    runner = _paused_runner("row-434-still-held")
    log = _EventLog(runner)
    assert download_hold.hold(reason="row-434-standing", by="test") is True
    assert download_hold.hold_state()["state"] == download_hold.HELD
    measured_states = _record_hold_reads(monkeypatch, download_hold)

    runner.resume()
    runner.resume()

    assert measured_states == [download_hold.HELD, download_hold.HELD], (
        "two Resume clicks must fire exactly two real hold measurements: %r"
        % (measured_states,))
    assert runner._state == download_hold.STATE_HELD, (
        "a standing hold was defeated: %r" % (runner._state,))
    assert not runner._pause.is_set(), (
        "a standing hold released the pause gate")
    assert log.count("download_hold") == 1, (
        "the state-deduplicated diagnostic must fire exactly once: %r"
        % (log.events,))
    assert log.events[0][2].get("hold_state") == download_hold.HELD


def test_start_during_a_resume_refusal_preserves_the_paused_pool(
        hold_store, monkeypatch):
    """A7 control: another start caller must not erase resume provenance."""
    from bulk_downloader import download_hold

    runner = _paused_runner("row-434-start-interleaving")
    log = _EventLog(runner)
    assert download_hold.hold(reason="row-434-interleaving", by="test") is True
    assert download_hold.hold_state()["state"] == download_hold.HELD

    runner.resume()
    assert runner._state == download_hold.STATE_HELD
    assert not runner._pause.is_set()
    assert log.count("download_hold") == 1
    measured_states = _record_hold_reads(monkeypatch, download_hold)

    # The window scheduler and the per-site Start endpoint can both arrive
    # while the paused pool is represented by its hold-refusal token.
    runner.start()
    assert measured_states == [download_hold.HELD], (
        "the interleaving start must fire exactly one real hold measurement: "
        "%r" % (measured_states,))
    assert runner._state == download_hold.STATE_HELD
    assert not runner._pause.is_set()
    assert log.count("download_hold") == 1

    assert download_hold.lift(by="test") is True
    assert download_hold.hold_state()["state"] == download_hold.CLEAR
    runner.resume()

    assert runner._state == "running", (
        "start() during the refusal erased resume provenance and left the "
        "paused pool stuck in %r after lift" % (runner._state,))
    assert runner._pause.is_set()
    assert measured_states == [download_hold.HELD, download_hold.CLEAR], (
        "the interleaved start and recovery resume must fire exactly two real "
        "hold measurements: %r" % (measured_states,))
    assert log.count("download_hold") == 1


def test_a_start_refused_runner_is_not_resumed_into_workerless_running(
        hold_store):
    """Negative control: start() refuses before any worker exists."""
    from bulk_downloader import download_hold
    from bulk_downloader.runner import SiteRunner

    runner = SiteRunner(
        "row-434-start", {"name": "row-434-start", "max_concurrent": 1})
    log = _EventLog(runner)
    assert runner._state != "running"
    assert runner._worker_threads == [], (
        "precondition: the start-refused fixture already owns workers")

    assert download_hold.hold(reason="row-434-start", by="test") is True
    assert download_hold.hold_state()["state"] == download_hold.HELD
    runner.start()
    assert runner._state == download_hold.STATE_HELD
    assert runner._worker_threads == [], (
        "start() spawned workers despite the hold")
    assert log.count("download_hold") == 1

    assert download_hold.lift(by="test") is True
    assert download_hold.downloads_allowed()[0] is True
    runner.resume()

    assert runner._state == download_hold.STATE_HELD, (
        "resume() changed a start-refusal token with no resumable provenance: "
        "%r" % (runner._state,))
    assert runner._worker_threads == []
    assert log.count("download_hold") == 1


def test_an_unmeasurable_hold_refuses_and_stays_recoverable(hold_store):
    """UNKNOWN is a refusal, and becomes recoverable only after a clear read."""
    from bulk_downloader import download_hold

    runner = _paused_runner("row-434-unknown")
    log = _EventLog(runner)
    hold_store.write_text(
        json.dumps({download_hold.HOLD_KEY: "yes"}), encoding="utf-8")
    unknown = download_hold.hold_state()
    assert unknown["state"] == download_hold.UNKNOWN, (
        "the fixture did not produce an UNKNOWN store: %r" % (unknown,))

    runner.resume()
    assert runner._state == download_hold.STATE_UNKNOWN, (
        "an unmeasurable hold must publish its distinctive token: %r"
        % (runner._state,))
    assert runner._state != download_hold.STATE_HELD
    assert not runner._pause.is_set()
    assert log.count("download_hold") == 1
    assert log.events[0][2].get("hold_state") == download_hold.UNKNOWN

    assert download_hold.lift(by="test") is True
    assert download_hold.downloads_allowed()[0] is True
    runner.resume()

    assert runner._state == "running", (
        "an UNKNOWN-refused runner stayed stuck after a measurable lift: %r"
        % (runner._state,))
    assert runner._pause.is_set()
    assert log.count("download_hold") == 1


def test_a_start_request_after_lift_recovers_the_refused_resume_in_place(
        hold_store, monkeypatch):
    """A7 control: start callers must share the refused resume's lifecycle."""
    from bulk_downloader import download_hold

    runner = _paused_runner("row-434-start-recovers-provenance")
    log = _EventLog(runner)

    assert download_hold.hold(reason="row-434-stale", by="test") is True
    runner.resume()
    assert runner._state == download_hold.STATE_HELD
    assert log.count("download_hold") == 1

    assert download_hold.lift(by="test") is True
    assert download_hold.hold_state()["state"] == download_hold.CLEAR
    measured_states = _record_hold_reads(monkeypatch, download_hold)

    runner.start()
    assert measured_states == [download_hold.CLEAR], (
        "the recovery start did not reach exactly one real hold read: %r"
        % (measured_states,))
    assert runner._state == "running", (
        "start() did not recover the resume-refused lifecycle: %r"
        % (runner._state,))
    assert runner._pause.is_set()

    runner.resume()
    assert measured_states == [download_hold.CLEAR], (
        "resume() re-read the hold after start() had already recovered it: %r"
        % (measured_states,))
    assert runner._state == "running"
    assert runner._pause.is_set()
    assert log.count("download_hold") == 1


def test_transform_control_imports_runner_without_asserting_hold_recovery():
    """Transform control: importability alone does not judge row 434."""
    from bulk_downloader import runner as runner_module

    assert runner_module.SiteRunner is not None
