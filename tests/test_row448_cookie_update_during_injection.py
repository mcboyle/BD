"""ROW 448 -- a cookie update published DURING injection must still land.

The worker cookie-refresh handshake compares ``self._cookies_updated_at``
against the worker's own ``my_cookie_ts``.  The defect this module pins is a
read-then-stamp window: the handshake read the cookie LIST, spent tens of
milliseconds inside ``persistent_ctx.add_cookies`` (a CDP round-trip), and only
THEN read the publish clock to stamp it.  A ``set_cookies`` landing inside that
window published v2 with timestamp T2, and the stamp recorded T2 while the
context still held v1 -- so ``_cookies_updated_at > my_cookie_ts`` was false
forever after and v2 was never injected into that worker.

No browser, no network, no real cookie jar: the persistent context is a fake
that records what it was handed, and the cookie values are obviously-fake
zero-entropy fixture strings.
"""
from __future__ import annotations

import importlib

import pytest

BD_GATE_SCOPE = "module"

runner_mod = importlib.import_module("bulk_downloader.runner")

# Zero-entropy fixture cookies.  These are not credentials and are not
# accepted by anything; they exist so the two published versions are
# distinguishable by value.
V1 = [{"name": "sid", "value": "fixture-v1", "domain": "example.invalid",
       "path": "/"}]
V2 = [{"name": "sid", "value": "fixture-v2", "domain": "example.invalid",
       "path": "/"}]


class _StepClock:
    """Stand-in for the ``time`` module inside runner: ``time()`` advances by a
    fixed step on every read so two publishes can never share a timestamp.
    Every other attribute delegates to the real module."""

    def __init__(self, real, start=1000.0, step=1.0):
        self._real = real
        self._now = float(start)
        self._step = float(step)
        self.reads = 0

    def time(self):
        self.reads += 1
        value = self._now
        self._now += self._step
        return value

    def __getattr__(self, name):
        return getattr(self._real, name)


class _FakePersistentContext:
    """Records every ``add_cookies`` payload.  Optionally publishes a new
    cookie version from INSIDE the first call -- the race the row names."""

    def __init__(self, publish=None, raise_on=()):
        self.calls = []
        self._publish = publish
        self._raise_on = set(raise_on)
        self.publishes_fired = 0

    def add_cookies(self, cookies):
        self.calls.append([dict(c) for c in cookies])
        n = len(self.calls)
        if self._publish is not None and n == 1:
            # A re-login thread / the session keeper lands here, mid-CDP.
            self._publish()
            self.publishes_fired += 1
        if n in self._raise_on:
            raise RuntimeError("fixture add_cookies failure")


def _make_runner(monkeypatch):
    """A SiteRunner carrying only the handshake's own state.

    ``__init__`` is deliberately not run: it starts schedulers and touches the
    filesystem.  The handshake reads exactly ``cookies``,
    ``_cookies_updated_at`` and ``site_id``, and ``set_cookies`` writes exactly
    ``cookies``, ``cookie_saved_at`` and ``_cookies_updated_at``.
    """
    clock = _StepClock(runner_mod.time)
    monkeypatch.setattr(runner_mod, "time", clock)
    r = object.__new__(runner_mod.SiteRunner)
    r.site_id = "row448-fixture"
    r.cookies = []
    r.cookie_saved_at = 0.0
    r._cookies_updated_at = 0.0
    return r, clock


def test_row448_precondition_helper_and_publisher_exist():
    """Precondition: the handshake and the publisher this row races are both
    present and are the real ones, not a shape this test invented."""
    assert hasattr(runner_mod.SiteRunner, "_refresh_worker_cookies"), (
        "the worker cookie handshake is not where this row measures it")
    assert callable(runner_mod.SiteRunner.set_cookies)
    assert callable(runner_mod.SiteRunner.set_cookies_from_file)


def test_row448_update_published_during_injection_lands_on_next_pass(
        monkeypatch):
    """RED on the defective parent: v2 is published from inside the v1
    ``add_cookies`` call, and the next handshake pass must inject it."""
    r, clock = _make_runner(monkeypatch)
    r.set_cookies(list(V1))
    published = [list(V1)]

    def _publish_v2():
        r.set_cookies(list(V2))
        published.append(list(V2))

    ctx = _FakePersistentContext(publish=_publish_v2)

    # --- preconditions, asserted before any verdict ---------------------
    assert r.cookies == V1, "fixture failed to publish v1"
    t_v1 = r._cookies_updated_at
    assert t_v1 > 0.0, "publisher did not stamp the publish clock"

    my_cookie_ts = 0.0
    my_cookie_ts = r._refresh_worker_cookies(ctx, my_cookie_ts)

    assert ctx.publishes_fired == 1, (
        "the mid-injection publish did not fire exactly once from inside "
        f"add_cookies (fired {ctx.publishes_fired})")
    assert len(published) == 2, (
        f"fixture published {len(published)} cookie versions, expected 2")
    assert len(ctx.calls) == 1, (
        f"first pass made {len(ctx.calls)} injections, expected 1")
    assert ctx.calls[0] == V1, (
        "the first injection did not carry v1; the race window this row "
        "measures was never entered")
    assert r._cookies_updated_at > t_v1, (
        "v2 was published without advancing the publish clock past v1")

    # --- the verdict -----------------------------------------------------
    my_cookie_ts = r._refresh_worker_cookies(ctx, my_cookie_ts)

    assert len(ctx.calls) == 2, (
        "SECOND INJECTION NEVER FIRED: the update published during injection "
        "is permanently skipped (add_cookies called "
        f"{len(ctx.calls)} time(s), expected 2)")
    assert ctx.calls[1] == V2, (
        f"the second injection carried {ctx.calls[1]!r}, expected v2")
    assert my_cookie_ts == r._cookies_updated_at, (
        "after injecting v2 the worker stamp must equal the publish clock")


def test_row448_negative_control_no_publish_means_exactly_one_injection(
        monkeypatch):
    """Without a mid-injection publish, two passes inject exactly once.

    This is the control that stops the fix degrading the handshake into an
    every-loop re-inject, which would hammer CDP on every URL pull."""
    r, _clock = _make_runner(monkeypatch)
    r.set_cookies(list(V1))
    ctx = _FakePersistentContext(publish=None)

    assert r.cookies == V1
    ts = 0.0
    ts = r._refresh_worker_cookies(ctx, ts)
    assert ctx.publishes_fired == 0, "control fixture must not publish"
    assert len(ctx.calls) == 1, "first pass must inject once"
    ts_after_first = ts
    ts = r._refresh_worker_cookies(ctx, ts)
    assert len(ctx.calls) == 1, (
        f"handshake re-injected with no new publish ({len(ctx.calls)} calls)")
    assert ts == ts_after_first, "stamp moved with no publish"


def test_row448_negative_control_failed_injection_is_retried(monkeypatch):
    """A failed ``add_cookies`` must NOT stamp: the worker retries next pass
    rather than believing it holds cookies the context never received."""
    r, _clock = _make_runner(monkeypatch)
    r.set_cookies(list(V1))
    ctx = _FakePersistentContext(publish=None, raise_on=(1,))

    ts = r._refresh_worker_cookies(ctx, 0.0)
    assert len(ctx.calls) == 1, "the raising call must still have been made"
    assert ts == 0.0, "a failed injection stamped the worker clock anyway"
    ts = r._refresh_worker_cookies(ctx, ts)
    assert len(ctx.calls) == 2, "the failed injection was never retried"


def test_row448_negative_control_no_context_and_no_cookies_are_noops(
        monkeypatch):
    """The handshake must stay inert when there is nothing to inject."""
    r, _clock = _make_runner(monkeypatch)
    r.set_cookies(list(V1))
    assert r._refresh_worker_cookies(None, 0.0) == 0.0

    r2, _c2 = _make_runner(monkeypatch)
    r2.cookies = []
    r2._cookies_updated_at = 5.0
    ctx = _FakePersistentContext()
    assert r2._refresh_worker_cookies(ctx, 0.0) == 0.0
    assert ctx.calls == [], "injected an empty cookie list"


class _ReadOrderRunner:
    """Records the ORDER in which the handshake reads the two racing pieces of
    state.  The invariant the fix installs is that the publish clock is
    snapshotted NO LATER than the cookie list; if the list is read first, a
    publish landing between the two reads is stamped as already-injected."""

    def __init__(self):
        self.order = []
        self._cookies = list(V1)
        self._ts = 100.0
        self.site_id = "row448-order"

    @property
    def cookies(self):
        self.order.append("cookies")
        return list(self._cookies)

    @property
    def _cookies_updated_at(self):
        self.order.append("clock")
        return self._ts


def test_row448_publish_clock_is_snapshotted_no_later_than_the_cookie_list():
    """Adversarial ordering check, independent of the mid-injection fixture.

    RED on the defective parent: the condition reads ``self.cookies`` first,
    so a publish landing before the clock read is stamped without ever being
    injected."""
    probe = _ReadOrderRunner()
    ctx = _FakePersistentContext()
    ts = runner_mod.SiteRunner._refresh_worker_cookies(probe, ctx, 0.0)

    assert probe.order, "the handshake read neither piece of racing state"
    assert len(ctx.calls) == 1, (
        f"precondition: expected one injection, got {len(ctx.calls)}")
    assert probe.order[0] == "clock", (
        "the handshake read the COOKIE LIST before the publish clock "
        f"(read order: {probe.order}); a publish landing between those two "
        "reads is stamped as injected and permanently skipped")
    assert ts == 100.0, f"stamped {ts!r}, expected the snapshotted clock"
