"""Row 449 -- the live ``runners`` registry must be iterated from a snapshot
taken under ``_watch_registry_lock``, not walked while another thread mutates it.

``app_state.runners`` is inserted on the site-create request thread with no lock
(app.py:5019, app.py:1725, app_sites_collection.py:198, app_sites_id_core.py:791,
app_config.py:536) and popped on delete under ``_watch_registry_lock``
(app_sites_id_core.py:481, :1046) -- a lock none of its ITERATORS took.  CPython
raises ``RuntimeError: dictionary changed size during iteration`` on any size
change mid-walk, so a site create or delete landing while ``/api/runners/pause_all``
is walking the registry gives the operator a 500 AFTER ``stop()`` has already been
applied to a prefix of the fleet.  The returned count is the operator's only truth
about what the fleet is doing, and it never arrives: a half-paused fleet with no
report of which sites stopped.

WHY A SNAPSHOT AND NOT A HELD LOCK.  ``runner.stop()`` and ``runner.start()`` are
slow (they join worker threads), and a watch worker's own finalizer acquires
``_watch_registry_lock`` on its way out -- so holding the registry lock across the
loop converts an iteration bug into a stall or a deadlock.  ``list(...)`` under the
lock is O(n) over pointers and gives the loop a stable, complete generation to walk
while create/delete proceed normally.  That is the whole contract: agree on ONE
lock for the moment of mutation and the moment of enumeration, and nowhere else.

THE METRICS SECTION IS THE SAME DEFECT WITH A QUIETER FAILURE.  metrics_prom's
active-jobs block walks the registry AND each runner's live ``jobs`` dict inside a
``try/except: pass``, so the same RuntimeError does not 500 -- it silently drops
``bd_jobs_active`` and reports a quieter fleet than exists.  Per CLAUDE.md A7 an
unmeasurable population reports UNKNOWN rather than looking like zero, so the fix
emits ``bd_jobs_active_unknown{site}`` -- following the ``bd_budget_usage_unknown``
precedent already in that module -- and a scrape that CAN measure must not emit it.
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time

import pytest

BD_GATE_SCOPE = "module"


# ── fixtures ──────────────────────────────────────────────────────────


class _FakeRunner:
    """A runner whose stop()/start() are observable and can be made to mutate
    the live registry exactly once, mid-iteration."""

    def __init__(self, sid, on_stop=None, stop_raises=None):
        self.sid = sid
        self._state = "running"
        self.stopped = 0
        self.started = 0
        self.jobs = {}
        self._lock = threading.Lock()
        self._on_stop = on_stop
        self._stop_raises = stop_raises

    def stop(self):
        self.stopped += 1
        if self._on_stop is not None:
            self._on_stop()
        if self._stop_raises is not None:
            raise self._stop_raises
        self._state = "stopped"

    def start(self):
        self.started += 1
        self._state = "running"


def _seed_registry(runners, pairs):
    """Replace the live registry contents in place and return a restorer.

    The registry is a process-global mutated-in-place object (app_state's
    docstring pins that identity), so the ONLY safe edit is in place.
    """
    original = dict(runners)
    runners.clear()
    runners.update(pairs)
    def restore():
        runners.clear()
        runners.update(original)
    return restore


def _client_with_runners_bp():
    """A minimal Flask app carrying only the runners blueprint.

    PROPAGATE_EXCEPTIONS is forced False so an escaping RuntimeError is
    OBSERVED as the operator's 500 rather than re-raised into the test -- the
    row's claim is about what the endpoint returns, not about what it raises.
    """
    from flask import Flask
    from bulk_downloader.app_runners import runners_bp
    app = Flask(__name__)
    app.config["TESTING"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.register_blueprint(runners_bp)
    return app.test_client()


@pytest.fixture()
def registry():
    """Yield the LIVE app_state.runners dict, restoring its exact prior
    contents afterwards.  Restoration must never be what makes a test green,
    so every test asserts its verdict BEFORE this teardown runs."""
    from bulk_downloader import app_state
    runners = app_state.runners
    original = dict(runners)
    try:
        yield runners
    finally:
        runners.clear()
        runners.update(original)


# ── precondition: the seam is real ────────────────────────────────────


def test_the_endpoint_reads_the_same_object_app_state_owns(registry):
    """A snapshot fix is only meaningful if the view really walks THIS dict."""
    from bulk_downloader import app_runners
    assert app_runners._app_runners() is registry, (
        "the runners view must resolve to the live app_state registry; if it "
        "resolves to a copy this whole row is unmeasurable")


def test_cpython_still_raises_on_a_size_change_mid_iteration():
    """The negative control for the defect's MECHANISM.

    If a future interpreter stopped raising here, every assertion below would
    pass for the wrong reason, so the mechanism is pinned independently.
    """
    d = {"a": 1, "b": 2, "c": 3}
    with pytest.raises(RuntimeError) as ei:
        for k in d:
            d["mutation-%s" % k] = 1
    assert "changed size during iteration" in str(ei.value)


# ── row 449 core: pause_all under a concurrent registry mutation ──────


def test_pause_all_survives_a_site_created_mid_iteration(registry):
    """RED on the defective parent: the RuntimeError escapes at the `for`
    statement's next(), AFTER stop() has been applied to a prefix."""
    mutations = []

    def insert_a_new_site():
        # Exactly the shape of app.py:5019 -- a create request landing on
        # another thread while this walk is in flight.
        if not mutations:
            mutations.append("insert")
            registry["site-created-mid-walk"] = _FakeRunner("created")

    made = {
        "s1": _FakeRunner("s1", on_stop=insert_a_new_site),
        "s2": _FakeRunner("s2"),
        "s3": _FakeRunner("s3"),
    }
    restore = _seed_registry(registry, made)
    try:
        # Preconditions, asserted explicitly and BEFORE the verdict.
        assert len(registry) == 3, "fixture did not build a 3-site registry"
        assert all(r.stopped == 0 for r in made.values())

        client = _client_with_runners_bp()
        resp = client.post("/api/runners/pause_all")

        # The mutation must actually have fired, exactly once -- otherwise
        # a green result proves nothing about the race.
        assert mutations == ["insert"], (
            "the concurrent insert never fired; the race was not exercised")
        assert len(registry) == 4, "the insert did not reach the live registry"

        assert resp.status_code == 200, (
            "a site created mid-walk turned pause_all into a %d; the operator "
            "gets no report of which sites stopped" % resp.status_code)
        body = resp.get_json()
        assert body["ok"] is True
        # Every site in the generation the view enumerated was stopped.
        assert body["paused"] == 3, (
            "paused=%r -- the count is the operator's truth about the fleet"
            % (body["paused"],))
        assert body["failures"] == []
        assert [made[k].stopped for k in ("s1", "s2", "s3")] == [1, 1, 1], (
            "stop() must reach every site in the snapshot generation")
    finally:
        restore()


def test_pause_all_survives_a_site_deleted_mid_iteration(registry):
    """The delete direction: a pop mid-walk is the same RuntimeError."""
    mutations = []

    def delete_another_site():
        if not mutations:
            mutations.append("pop")
            registry.pop("s3", None)

    made = {
        "s1": _FakeRunner("s1", on_stop=delete_another_site),
        "s2": _FakeRunner("s2"),
        "s3": _FakeRunner("s3"),
    }
    restore = _seed_registry(registry, made)
    try:
        assert len(registry) == 3, "fixture did not build a 3-site registry"
        client = _client_with_runners_bp()
        resp = client.post("/api/runners/pause_all")

        assert mutations == ["pop"], "the concurrent delete never fired"
        assert len(registry) == 2, "the pop did not reach the live registry"
        assert resp.status_code == 200, (
            "a site deleted mid-walk turned pause_all into a %d"
            % resp.status_code)
        assert resp.get_json()["paused"] == 3
    finally:
        restore()


def test_resume_all_survives_a_site_created_mid_iteration(registry):
    """resume_all walks the same dict at app_runners.py:55."""
    mutations = []

    class _MutatingOnStart(_FakeRunner):
        def start(self):
            if not mutations:
                mutations.append("insert")
                registry["site-created-mid-walk"] = _FakeRunner("created")
            super().start()

    r1 = _MutatingOnStart("s1")
    r1._state = "paused"
    made = {"s1": r1, "s2": _FakeRunner("s2"), "s3": _FakeRunner("s3")}
    made["s2"]._state = "paused"
    made["s3"]._state = "paused"
    restore = _seed_registry(registry, made)
    try:
        assert len(registry) == 3
        assert all(r._state == "paused" for r in made.values()), (
            "fixture did not build a fully paused fleet")
        client = _client_with_runners_bp()
        resp = client.post("/api/runners/resume_all")

        assert mutations == ["insert"], "the concurrent insert never fired"
        assert resp.status_code == 200, (
            "resume_all became a %d under a concurrent create"
            % resp.status_code)
        assert resp.get_json()["resumed"] == 3
    finally:
        restore()


# ── negative controls: a genuine runner failure still reports ─────────


def test_a_failing_stop_still_lands_in_failures_and_the_loop_completes(
        registry):
    """Snapshotting must not swallow a REAL per-runner error, and must not
    abort the loop.  This is the control that proves the fix did not simply
    wrap the whole walk in a try/except."""
    made = {
        "s1": _FakeRunner("s1"),
        "s2": _FakeRunner("s2", stop_raises=ValueError("worker would not die")),
        "s3": _FakeRunner("s3"),
    }
    restore = _seed_registry(registry, made)
    try:
        assert len(registry) == 3
        client = _client_with_runners_bp()
        resp = client.post("/api/runners/pause_all")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["paused"] == 2, (
            "the two healthy sites must still be counted; got %r"
            % (body["paused"],))
        assert len(body["failures"]) == 1
        assert body["failures"][0]["site_id"] == "s2"
        assert "worker would not die" in body["failures"][0]["error"], (
            "the failure must carry the runner's own words, not a generic one")
        # The loop reached s3 AFTER s2 raised.
        assert made["s3"].stopped == 1, "the loop aborted at the failure"
    finally:
        restore()


def test_pause_all_on_a_quiescent_registry_is_unchanged(registry):
    """The ordinary path keeps working: no mutation, no failures."""
    made = {"s1": _FakeRunner("s1"), "s2": _FakeRunner("s2")}
    restore = _seed_registry(registry, made)
    try:
        assert len(registry) == 2
        resp = _client_with_runners_bp().post("/api/runners/pause_all")
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True, "paused": 2, "failures": []}
    finally:
        restore()


def test_an_empty_registry_reports_zero_rather_than_erroring(registry):
    """An empty iterable must not be able to manufacture green elsewhere, so
    its own behaviour is pinned: 200 with an explicit zero."""
    restore = _seed_registry(registry, {})
    try:
        assert len(registry) == 0
        resp = _client_with_runners_bp().post("/api/runners/pause_all")
        assert resp.status_code == 200
        assert resp.get_json()["paused"] == 0
    finally:
        restore()


# ── row 449 metrics half: an unmeasurable jobs population is UNKNOWN ──


def _render_metrics(runners):
    from bulk_downloader import metrics_prom
    return metrics_prom.render(s_cfg={}, runners=runners)


def test_metrics_survives_a_registry_mutated_during_the_scrape():
    """The scrape walks the registry too; a racing create must not silently
    drop bd_jobs_active."""
    live = {}
    mutations = []

    class _MutatingJobs(dict):
        """Iterating this site's jobs inserts a NEW SITE into the registry --
        the exact interleaving of a create request landing mid-scrape."""

        def values(self):
            if not mutations:
                mutations.append("insert")
                live["site-created-mid-scrape"] = _FakeRunner("created")
            return dict.values(self)

    r1 = _FakeRunner("s1")
    r1.jobs = _MutatingJobs({"u1": {"status": "running"}})
    live["s1"] = r1
    live["s2"] = _FakeRunner("s2")
    live["s2"].jobs = {"u2": {"status": "pending"}}

    assert len(live) == 2, "fixture did not build a 2-site registry"
    body = _render_metrics(live)

    assert mutations == ["insert"], "the concurrent insert never fired"
    assert len(live) == 3, "the insert did not reach the registry"
    assert "bd_jobs_active" in body, (
        "the racing scrape dropped bd_jobs_active entirely -- a quieter fleet "
        "than exists, reported as success")
    assert 'bd_jobs_active{site="s1",status="running"} 1' in body
    assert 'bd_jobs_active{site="s2",status="pending"} 1' in body


def test_an_unmeasurable_jobs_population_reports_unknown_not_zero():
    """CLAUDE.md A7: unavailable measurement returns UNKNOWN, never OK.

    A runner whose jobs mapping cannot be enumerated must not look like a site
    with zero active jobs -- those two states lead to opposite operator
    actions (ignore it, or go find out why it cannot be read)."""

    class _UnreadableJobs(dict):
        def values(self):
            raise RuntimeError("dictionary changed size during iteration")

    r1 = _FakeRunner("s1")
    r1.jobs = _UnreadableJobs({"u1": {"status": "running"}})
    r2 = _FakeRunner("s2")
    r2.jobs = {"u2": {"status": "running"}}
    live = {"s1": r1, "s2": r2}

    body = _render_metrics(live)

    assert 'bd_jobs_active_unknown{site="s1"} 1' in body, (
        "a site whose jobs population could not be read must be NAMED, not "
        "silently omitted; body was:\n%s" % body)
    # The measurable site still measures -- one bad site does not blind the
    # whole section.
    assert 'bd_jobs_active{site="s2",status="running"} 1' in body
    # And the unmeasurable site emits no zero-looking sample.
    assert 'bd_jobs_active{site="s1"' not in body


def test_a_healthy_scrape_emits_no_unknown_marker():
    """The negative control for the UNKNOWN marker itself: a marker that fires
    on every scrape names nothing."""
    r1 = _FakeRunner("s1")
    r1.jobs = {"u1": {"status": "running"}}
    body = _render_metrics({"s1": r1})
    assert 'bd_jobs_active{site="s1",status="running"} 1' in body
    assert "bd_jobs_active_unknown" not in body, (
        "the UNKNOWN marker fired on a scrape that measured fine")


# ── A7 self-audit: prove the LOCKED path is the one the live dict takes ──


def test_the_live_registry_generation_is_taken_under_the_registry_lock(
        registry):
    """Self-audit control.  Every other test here would still pass if
    runners_generation quietly returned an UNLOCKED list(...) copy -- a copy
    alone stops the RuntimeError, so the lock could rot away undetected.

    This asserts the lock is genuinely HELD while the copy is taken, which is
    the half that makes create/delete/enumeration agree on one lock rather
    than on CPython's GIL."""
    from bulk_downloader import app_state

    held_during_copy = []
    real_items = dict.items

    class _Watched(dict):
        def items(self):
            # RLock exposes the owning thread only while held.
            held_during_copy.append(
                app_state._watch_registry_lock._is_owned())
            return real_items(self)

    made = _Watched({"s1": _FakeRunner("s1")})
    original = dict(registry)
    saved = app_state.runners
    try:
        app_state.runners = made
        out = app_state.runners_generation(made)
    finally:
        app_state.runners = saved
        registry.clear()
        registry.update(original)

    assert held_during_copy == [True], (
        "the live-registry copy was taken WITHOUT holding "
        "_watch_registry_lock (observed %r)" % (held_during_copy,))
    assert [sid for sid, _r in out] == ["s1"]


def test_a_caller_scoped_copy_is_not_locked(registry):
    """The negative control for the above: a private {sid: runner} copy must
    NOT pay for the lock, or every single-site widget would serialise against
    site creation for no reason."""
    from bulk_downloader import app_state

    observed = []
    real_items = dict.items

    class _Watched(dict):
        def items(self):
            observed.append(app_state._watch_registry_lock._is_owned())
            return real_items(self)

    scoped = _Watched({"s1": _FakeRunner("s1")})
    out = app_state.runners_generation(scoped)

    assert observed == [False], (
        "a caller-scoped copy took the registry lock unnecessarily")
    assert [sid for sid, _r in out] == ["s1"]


# ── fork safety of the lock this row introduced ───────────────────────
#
# THE LOCK IS NEW ON READ PATHS, AND THAT IS WHAT MAKES FORK A NEW HAZARD.
# Measured on this tree against the merge base with the secret gate's own
# scanned GET denominator (588 concretized routes, tests/test_secret_display
# _never.py::_scan_targets): routes acquiring _watch_registry_lock went from
# 0 on the base to 3 here -- /metrics, /api/dashboard/v2, /api/widgets/data.
#
# os.fork() carries ONLY the calling thread.  A lock another thread held at
# the instant of the fork is inherited LOCKED by a child that contains no
# thread able to release it, so the first child request to reach
# runners_snapshot() blocks forever.  tests/test_secret_display_never.py
# forks 32 shard children out of an already-booted, multi-threaded app
# (CPython 3.12 warns "use of fork() may lead to deadlocks in the child" on
# every one of them), and one child that never answers is exactly the
# "expected_shards=32 collected_shards=31" refusal that blocked this cut.
#
# The remedy is the pattern the os.register_at_fork documentation names: the
# forking thread takes the registry lock BEFORE the fork and releases it in
# both processes after, so no fork can be taken while another thread holds
# it and every child starts with a lock it can acquire.  Re-initialising the
# lock in the child instead would have had to REBIND it, and this module's
# whole contract is that its objects are created once and never reassigned,
# so app.py's `from .app_state import _watch_registry_lock` alias would then
# name a different lock from runners_snapshot() -- create/delete and
# enumeration would stop agreeing on ONE lock, which is the defect this row
# exists to remove.
#
# NOT CLOSED HERE: runner._lock is a separate, PRE-EXISTING hazard of the
# same family -- the same census measured 10 scanned GET routes taking it on
# the merge base and 11 here.  It is a different lock with a different owner
# and belongs to its own row; this row fixes only the exposure it created.

_FORK_HOLD_S = 3.0
_FORK_CHILD_DEADLINE_S = 20.0


def _fork_while_held(lock, child_body, deadline=_FORK_CHILD_DEADLINE_S,
                     hold_s=_FORK_HOLD_S):
    """Fork a child while ANOTHER thread holds ``lock``; return its verdict.

    The holder proves it is holding before the fork is taken and releases on
    its own timer, never on a signal from the forking thread -- a protection
    that makes the forking thread WAIT for the holder would otherwise deadlock
    against a holder waiting for the fork.  Returns
    ``(verdict, exitcode, fork_elapsed)`` with verdict in
    {"COMPLETED", "CHILD-ERROR", "HUNG"}; a hung child is killed, never left.
    """
    holding = threading.Event()
    holds = []

    def holder():
        with lock:
            holds.append(1)
            holding.set()
            time.sleep(hold_s)

    thread = threading.Thread(target=holder, daemon=True,
                              name="row449-fork-lock-holder")
    thread.start()
    assert holding.wait(10), (
        "precondition failed: the holder thread never acquired the lock, so "
        "the fork below would not have been taken while it was held")
    assert holds == [1], (
        "precondition failed: expected exactly one hold, observed %r" % (holds,))

    sys.stdout.flush()
    sys.stderr.flush()
    started = time.monotonic()
    pid = os.fork()
    if pid == 0:                                    # pragma: no cover - child
        code = 4
        try:
            code = child_body()
        except BaseException:
            code = 4
        finally:
            os._exit(code)
    fork_elapsed = time.monotonic() - started

    waited = time.monotonic()
    status = None
    while time.monotonic() - waited < deadline:
        done, raw = os.waitpid(pid, os.WNOHANG)
        if done:
            status = raw
            break
        time.sleep(0.02)
    if status is None:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        thread.join(timeout=hold_s + 10)
        return "HUNG", None, fork_elapsed
    thread.join(timeout=hold_s + 10)
    exitcode = os.waitstatus_to_exitcode(status)
    return ("COMPLETED" if exitcode == 0 else "CHILD-ERROR"), exitcode, fork_elapsed


def test_a_forked_child_can_read_live_state_while_another_thread_holds_the_lock():
    """The subject: a child forked while a NON-surviving thread holds
    _watch_registry_lock must still be able to take a runners snapshot."""
    from bulk_downloader import app_state

    def child():
        lock = app_state._watch_registry_lock
        if not lock.acquire(blocking=False):
            return 5                    # inherited LOCKED by a dead thread
        lock.release()
        app_state.runners_snapshot()
        return 0

    verdict, exitcode, _elapsed = _fork_while_held(
        app_state._watch_registry_lock, child)
    assert verdict == "COMPLETED", (
        "a child forked while another thread held _watch_registry_lock could "
        "not read live state: verdict=%s exitcode=%r. exitcode 5 means the "
        "child inherited the lock LOCKED with no thread alive to release it; "
        "HUNG means runners_snapshot() blocked forever, which is the "
        "'collected_shards=31' refusal in tests/test_secret_display_never.py"
        % (verdict, exitcode))


def test_an_unprotected_lock_of_the_same_construction_still_hangs_a_child():
    """Teeth for the test above.  An identically-constructed RLock that is NOT
    covered by the at-fork protection must still hang its child -- otherwise
    the check above could pass because fork is harmless here, or because the
    detector cannot see a hang at all."""
    unprotected = threading.RLock()

    def child():
        unprotected.acquire()           # inherited LOCKED: blocks forever
        unprotected.release()
        return 0

    verdict, exitcode, _elapsed = _fork_while_held(
        unprotected, child, deadline=6.0)
    assert verdict == "HUNG", (
        "an unprotected inherited lock did NOT hang its child "
        "(verdict=%s exitcode=%r); the fork-hazard detector has no teeth, so "
        "the subject test above proves nothing" % (verdict, exitcode))


def _load_secret_gate_module():
    """The real secret-display gate module, whose forked route scan is the
    consumer this row broke."""
    import importlib.util
    import pathlib

    existing = sys.modules.get("test_secret_display_never")
    if existing is not None:
        return existing
    path = pathlib.Path(__file__).resolve().parent / "test_secret_display_never.py"
    assert path.is_file(), "secret-display gate module missing at %s" % (path,)
    spec = importlib.util.spec_from_file_location(
        "test_secret_display_never", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["test_secret_display_never"] = module
    spec.loader.exec_module(module)
    return module


def test_the_forked_route_scan_collects_every_shard_while_the_lock_churns():
    """The consumer's verdict, by EXACT SHARD COUNT.

    Drives tests/test_secret_display_never.py's own _scan_all() -- the code
    that forks one child per shard -- while a background thread cycles
    _watch_registry_lock at roughly a 50% duty cycle.  With 32 forks that is a
    ~1 - 0.5**32 chance that at least one fork is taken while the lock is
    held, so an unprotected tree loses at least one shard essentially always.

    The assertion is expected_shards == collected_shards over a nonzero
    denominator, NOT 'the scan returned'.
    """
    gate = _load_secret_gate_module()
    from bulk_downloader import app_state

    with gate._client_seeded() as (client, headers, sid):
        from bulk_downloader import app as app_module

        real_targets = gate._scan_targets(app_module.app, sid)
        assert len(real_targets) > 0, "the gate's route denominator is zero"
        assert "/metrics" in real_targets, (
            "/metrics is not in the gate's scanned denominator, so this test "
            "would not exercise a route that takes the registry lock")

        # Prove the route actually reaches the lock on this tree before using
        # it as the payload: a route that never calls runners_snapshot() could
        # not exhibit the defect and would manufacture a green result.
        calls = []
        real_snapshot = app_state.runners_snapshot
        app_state.runners_snapshot = lambda: (calls.append(1)
                                              or real_snapshot())
        try:
            client.get("/metrics", headers=headers)
        finally:
            app_state.runners_snapshot = real_snapshot
        assert len(calls) > 0, (
            "GET /metrics did not reach runners_snapshot() on this tree; the "
            "shard payload below cannot exhibit the inherited-lock hang")

        # Every shard carries the lock-taking route, so ANY fork taken while
        # the lock is held costs a shard.
        shards_wanted = 32
        per_shard = 8
        targets = ["/metrics"] * (shards_wanted * per_shard)

        observed = {}
        real_reconcile = gate._reconcile_scan_results

        def recording(targets_, results, expected_shards):
            observed["expected_shards"] = expected_shards
            observed["collected_shards"] = len(results)
            observed["targets"] = len(targets_)
            return real_reconcile(targets_, results, expected_shards)

        stop = threading.Event()
        cycles = []

        def churn():
            while not stop.is_set():
                with app_state._watch_registry_lock:
                    cycles.append(1)
                    time.sleep(0.005)
                time.sleep(0.005)

        churner = threading.Thread(target=churn, daemon=True,
                                   name="row449-registry-lock-churn")
        gate._reconcile_scan_results = recording
        churner.start()
        try:
            scanned, leaks = gate._scan_all(targets, headers)
        finally:
            stop.set()
            churner.join(timeout=30)
            gate._reconcile_scan_results = real_reconcile

    assert len(cycles) > 0, (
        "precondition failed: the churn thread never held the registry lock, "
        "so no fork could have been taken while it was held")
    print("SHARD-COUNT targets=%r expected_shards=%r collected_shards=%r "
          "executed=%r lock_holds=%d"
          % (observed.get("targets"), observed.get("expected_shards"),
             observed.get("collected_shards"), scanned, len(cycles)))
    assert observed.get("targets") == len(targets) == shards_wanted * per_shard
    assert observed.get("expected_shards") == shards_wanted, (
        "the scan did not shard into %d workers (observed %r); the shard-count "
        "denominator this test asserts would be a different experiment"
        % (shards_wanted, observed.get("expected_shards")))
    assert observed.get("collected_shards") == observed.get("expected_shards"), (
        "the forked route scan LOST a shard while the registry lock churned: "
        "expected_shards=%r collected_shards=%r"
        % (observed.get("expected_shards"), observed.get("collected_shards")))
    assert scanned == len(targets), (
        "route execution denominator mismatch: collected=%d executed=%d"
        % (len(targets), scanned))
    assert leaks == []


def test_a_lost_shard_still_fails_the_route_scan():
    """Negative control for the shard-count assertion above: the gate's own
    reconciler must still REFUSE when a shard is genuinely missing, so a green
    scan cannot be manufactured by loosening the count."""
    gate = _load_secret_gate_module()

    targets = ["/a", "/b", "/c", "/d"]
    complete = [(2, [], []), (2, [], [])]
    scanned, leaks = gate._reconcile_scan_results(
        targets, complete, expected_shards=2)
    assert (scanned, leaks) == (4, []), (
        "the reconciler rejected a COMPLETE result set, so its refusal below "
        "would not be attributable to the missing shard")

    with pytest.raises(AssertionError) as excinfo:
        gate._reconcile_scan_results(targets, complete[:1], expected_shards=2)
    assert "expected_shards=2 collected_shards=1" in str(excinfo.value), (
        "a lost shard did not produce the exact-count refusal: %s"
        % (excinfo.value,))
