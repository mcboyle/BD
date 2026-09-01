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

import threading

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
