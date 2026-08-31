"""Row 448 -- a cookie update that lands DURING injection is permanently skipped.

MEASURED CONTEXT. ``_worker_loop``'s cookie-refresh handshake
(bulk_downloader/runner.py) sees ``_cookies_updated_at`` exceed the worker's
``my_cookie_ts``, reads ``self.cookies`` (v1), enters
``persistent_ctx.add_cookies(v1)`` -- a CDP round-trip taking tens of
milliseconds -- and only THEN stamps ``my_cookie_ts = self._cookies_updated_at``.

A re-login thread or the session keeper publishing v2 with timestamp T2 inside
that window lands AFTER the v1 list was read and BEFORE the stamp, so the stamp
records T2 while the context actually holds v1.  ``_cookies_updated_at >
my_cookie_ts`` is then false forever, and v2 is never injected into that
worker's persistent context until some unrelated THIRD update arrives.  The
worker navigates the authenticated site with stale or revoked session cookies
and the resulting auth walls are recorded against the site.

THE SEAM IS THE ROUND TRIP, NOT A SLEEP.  The fake persistent context publishes
v2 from INSIDE ``add_cookies``, which is exactly the window the defect lives in,
so the reproduction is deterministic on any host at any load.

RED on the defective parent: the second loop pass never injects -- ``add_cookies``
is called exactly once and the context's final cookie set is still v1.  GREEN:
exactly two injections, the second carrying v2.

NEGATIVE CONTROL: with no mid-injection publish, exactly ONE injection occurs
across two loop passes, proving the fix does not degrade the handshake into an
every-loop re-inject.
"""
from __future__ import annotations

import contextlib
import queue
import threading
import time

import pytest


BD_GATE_SCOPE = "module"

_URL_A = "https://example.test/row-448-a.mp4"
_URL_B = "https://example.test/row-448-b.mp4"

_V1 = [{"name": "sess", "value": "v1", "domain": "example.test", "path": "/"}]
_V2 = [{"name": "sess", "value": "v2", "domain": "example.test", "path": "/"}]


class _FakePersistentContext:
    """Records every injection; optionally publishes v2 from inside the call.

    ``add_cookies`` IS the CDP round trip.  Publishing from inside it places the
    competing writer exactly where the real re-login thread lands.
    """

    def __init__(self, on_inject=None):
        self._on_inject = on_inject
        self.injections = []
        self.inside = False

    def add_cookies(self, cookies):
        self.inside = True
        try:
            # Copy at entry: the runner may hand us its live list.
            self.injections.append([dict(c) for c in cookies])
            if self._on_inject is not None:
                self._on_inject(len(self.injections))
        finally:
            self.inside = False

    @property
    def cookies_held(self):
        return self.injections[-1] if self.injections else []


class _TwoPassStop:
    """A stop Event that never blocks; the harness sets it after two passes."""

    def __init__(self):
        self._event = threading.Event()
        self.waits = []

    def is_set(self):
        return self._event.is_set()

    def set(self):
        self._event.set()

    def wait(self, timeout=None):
        self.waits.append(timeout)
        # A park inside the loop would spin forever here; end the run instead so
        # an unexpected gate is a visible zero-pass failure, never a hang.
        self._event.set()
        return True


def _build_runner(ctx):
    from bulk_downloader import runner as runner_mod

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "row-448"
    runner.config = {"max_concurrent": 1}
    runner.jobs = {_URL_A: {"status": "pending"}, _URL_B: {"status": "pending"}}
    runner.cookies = []
    runner._cookies_updated_at = 0.0
    runner._lock = threading.Lock()
    runner._worker_heartbeats_lock = threading.Lock()
    runner._worker_heartbeats = {}
    runner._worker_run_generation = 1
    runner._worker_context = threading.local()
    runner._pause = threading.Event()
    runner._pause.set()
    runner._session_ok = threading.Event()
    runner._session_ok.set()
    runner._stop = _TwoPassStop()
    runner._url_queue = queue.Queue()
    runner._url_queue.put((1, _URL_A))
    runner._url_queue.put((1, _URL_B))

    processed = []

    def launch_browser(*, worker_idx, netns):
        return None, ctx, None, "row-448-test"

    def process_worker_url(worker_idx, browser, url, *, persistent_ctx,
                           run_generation):
        processed.append(url)
        if len(processed) >= 2:
            runner._stop.set()
        return runner_mod.SiteRunner._WORKER_CLAIM_PROCESSED

    runner._launch_browser = launch_browser
    runner._effective_concurrency = lambda: 1
    runner._generation_item_is_processable = lambda generation, url: True
    runner._resource_admission_hold = lambda: None
    runner._process_worker_url = process_worker_url
    runner._maybe_drift_recover = lambda: None
    return runner, processed


@pytest.fixture()
def _quiet_gates(monkeypatch):
    """Neutralise the loop gates that precede the cookie block."""
    from bulk_downloader import maintenance, netns_isolation, smart_wakeup
    from bulk_downloader import runner as runner_mod

    monkeypatch.setattr(maintenance, "is_action_paused", lambda action: False)
    monkeypatch.setattr(smart_wakeup, "should_wake_now",
                        lambda **kw: {"wake": True})
    monkeypatch.setattr(
        netns_isolation, "capture_netns",
        lambda config, kind, label: contextlib.nullcontext(None))
    monkeypatch.setattr(runner_mod, "_VPN_RUNTIME_AVAILABLE", True)
    monkeypatch.setattr(runner_mod.vpn_runtime, "maybe_wait_for_vpn",
                        lambda site_id, *, timeout: True)
    yield


def test_a_publish_landing_inside_add_cookies_is_injected_on_the_next_pass(
        _quiet_gates):
    from bulk_downloader import runner as runner_mod

    publishes = []
    real_set_cookies = runner_mod.SiteRunner.set_cookies
    holder = {}

    def publish_v2_mid_injection(injection_index):
        if injection_index != 1:
            return
        runner = holder["runner"]
        # The competing writer is the REAL publisher, not a hand-set attribute.
        real_set_cookies(runner, [dict(c) for c in _V2])
        publishes.append((runner._cookies_updated_at, holder["ctx"].inside))

    ctx = _FakePersistentContext(on_inject=publish_v2_mid_injection)
    holder["ctx"] = ctx

    runner, processed = _build_runner(ctx)
    holder["runner"] = runner

    # v1 is already published when the worker starts, stamped 10s in the past so
    # the mid-injection publish is STRICTLY newer on any clock resolution.
    t1 = time.time() - 10.0
    runner.cookies = [dict(c) for c in _V1]
    runner._cookies_updated_at = t1

    runner._worker_loop(worker_idx=0, run_generation=1)

    # -- preconditions, asserted before any verdict ------------------------
    assert processed == [_URL_A, _URL_B], (
        "the fixture did not complete two worker passes; the verdict below "
        "would be about an unrun loop: %r" % (processed,))
    assert len(publishes) == 1, (
        "the mid-injection publish must fire exactly once, from inside "
        "add_cookies: %r" % (publishes,))
    t2, inside = publishes[0]
    assert inside is True, (
        "the competing publish did not land INSIDE the add_cookies round "
        "trip, so this test is not exercising row 448's window")
    assert t2 > t1, (
        "the fixture's second version is not strictly newer (%r <= %r); the "
        "handshake comparison would be undefined" % (t2, t1))
    assert ctx.injections, "no cookie injection happened at all"
    assert ctx.injections[0] == _V1, (
        "the first injection must carry v1: %r" % (ctx.injections[0],))

    # -- the verdict -------------------------------------------------------
    assert len(ctx.injections) == 2, (
        "the update published during the v1 injection was never injected: "
        "add_cookies fired %d time(s) across two passes, so the worker is "
        "still navigating with v1" % (len(ctx.injections),))
    assert ctx.injections[1] == _V2, (
        "the second injection carried the wrong version: %r"
        % (ctx.injections[1],))
    assert ctx.cookies_held == _V2, (
        "the context's final cookie set is not v2: %r" % (ctx.cookies_held,))


def test_an_unchanged_cookie_set_is_not_reinjected_every_pass(_quiet_gates):
    """NEGATIVE CONTROL. The fix must not degrade into an every-loop re-inject."""
    ctx = _FakePersistentContext(on_inject=None)
    runner, processed = _build_runner(ctx)
    runner.cookies = [dict(c) for c in _V1]
    runner._cookies_updated_at = time.time() - 10.0

    runner._worker_loop(worker_idx=0, run_generation=1)

    assert processed == [_URL_A, _URL_B], (
        "the control did not complete two worker passes: %r" % (processed,))
    assert len(ctx.injections) == 1, (
        "an unchanged cookie set was injected %d times across two passes; the "
        "handshake has degraded into an every-loop re-inject"
        % (len(ctx.injections),))
    assert ctx.injections[0] == _V1
