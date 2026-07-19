"""v3.66.470 thread-2 -- wire the deferred ``site.cooldown`` / ``site.recovered``
plugin hook events to their core transition points in ``AccountsMixin``.

``site.cooldown`` fires once when ``trigger_rate_limit`` actually applies the 24h
site-wide cooldown -- NOT on the account-rotation early-return (rotating to a
fresh account is a recovery, not a cooldown). ``site.recovered`` fires once when
``is_rate_limited`` detects the cooldown window has elapsed and clears it (the
single point where ``_rl_until`` flips back to 0). Both are HOOK_EVENTS
(``fire_hook``, non-gated, like ``download.done``), routed through a small
exception-isolated ``_fire_site_hook`` seam so a plugin error can never affect
rate-limit handling.

RED-first: proven failing on pristine v3.66.469 -- ``trigger_rate_limit`` /
``is_rate_limited`` fire nothing, and ``SiteRunner._fire_site_hook`` does not
exist. Runner construction is avoided via the unbound-method-on-stub pattern
(mirrors ``test_audit_p3a.py``); the real plugin registry is exercised directly.
"""
import threading
import time

from bulk_downloader.runner import SiteRunner
from bulk_downloader import plugins as _pl


def _fresh_stub(rotate=False):
    class _Stub:
        def __init__(self):
            self.site_id = "sid_cool"
            self._rl_until = 0.0
            self._lock = threading.RLock()
            self.jobs = {}
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._state = ""
            self._rl_autostart = False
            self._rotate_ret = rotate
            self.saved = 0
            self.cleared = 0

        def _rotate_account_if_available(self, reason):
            return self._rotate_ret

        def _save_rl(self):
            self.saved += 1

        def _clear_rl(self):
            self._rl_until = 0.0
            self.cleared += 1

        def _wait_rl_autostart(self):
            # trigger_rate_limit spawns this on a daemon thread; no-op keeps
            # the test from sleeping on the real 60s autostart loop.
            pass

    return _Stub()


def test_site_cooldown_fires_on_real_cooldown():
    """RED until wired: applying the 24h cooldown fires exactly one
    site.cooldown with {site_id, reason, duration_seconds}."""
    stub = _fresh_stub(rotate=False)
    fired = []
    stub._fire_site_hook = lambda name, payload: fired.append((name, payload))

    SiteRunner.trigger_rate_limit(stub, "https://x/y", reason="429 burst")

    assert len(fired) == 1, f"expected one site.cooldown fire, got {fired!r}"
    name, payload = fired[0]
    assert name == "site.cooldown", name
    assert payload["site_id"] == "sid_cool"
    assert payload["reason"] == "429 burst"
    assert payload["duration_seconds"] == 86400
    assert stub._rl_until > time.time(), "cooldown window should be active"


def test_site_cooldown_not_fired_when_account_rotated():
    """Guard: rotating to a fresh account is the recovery path -- no cooldown
    is applied, so site.cooldown must NOT fire."""
    stub = _fresh_stub(rotate=True)
    fired = []
    stub._fire_site_hook = lambda name, payload: fired.append((name, payload))

    SiteRunner.trigger_rate_limit(stub, "https://x/y", reason="429")

    assert fired == [], f"site.cooldown fired on the rotate path: {fired!r}"
    assert stub._rl_until == 0.0, "no cooldown should have been applied"


def test_site_recovered_fires_once_on_expiry():
    """RED until wired: the lazy-expiry branch fires exactly one site.recovered
    and is edge-triggered (no re-fire once cleared)."""
    stub = _fresh_stub()
    stub._rl_until = time.time() - 1  # active-but-expired cooldown
    fired = []
    stub._fire_site_hook = lambda name, payload: fired.append((name, payload))

    rl1 = SiteRunner.is_rate_limited(stub)
    assert rl1 is False, "expired cooldown should report not-rate-limited"
    assert stub.cleared == 1, "_clear_rl should run on expiry"
    assert len(fired) == 1, f"expected one site.recovered, got {fired!r}"
    assert fired[0][0] == "site.recovered"
    assert fired[0][1] == {"site_id": "sid_cool"}

    # second call: no active cooldown -> must not re-fire
    SiteRunner.is_rate_limited(stub)
    assert len(fired) == 1, "site.recovered must be edge-triggered (no re-fire)"


def test_site_recovered_not_fired_while_active():
    """Guard: while the cooldown window is still active, no recovery fires."""
    stub = _fresh_stub()
    stub._rl_until = time.time() + 1000  # active, not expired
    fired = []
    stub._fire_site_hook = lambda name, payload: fired.append((name, payload))

    rl = SiteRunner.is_rate_limited(stub)
    assert rl is True
    assert fired == [], f"recovery fired while still cooling: {fired!r}"


def test_fire_site_hook_reaches_registry_and_isolates():
    """RED until the seam exists: _fire_site_hook routes to plugins.fire_hook,
    delivers the payload to a registered hook, and isolates a raising hook."""
    received = []

    def good(payload):
        received.append(payload)

    def bad(payload):
        raise RuntimeError("boom")

    _pl.register_hook("site.cooldown", bad)
    _pl.register_hook("site.cooldown", good)
    try:
        stub = _fresh_stub()
        SiteRunner._fire_site_hook(
            stub, "site.cooldown",
            {"site_id": "z", "reason": "r", "duration_seconds": 5})
        assert received == [{"site_id": "z", "reason": "r", "duration_seconds": 5}], (
            f"registered plugin hook did not receive the payload: {received!r}")
    finally:
        _pl.unregister_hook("site.cooldown", good)
        _pl.unregister_hook("site.cooldown", bad)
        _pl.clear_quarantine()
