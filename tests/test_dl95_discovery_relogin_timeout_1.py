"""dl95-discovery-relogin-timeout r1 -- scene_crawler._relogin_for_discovery wait bound.

Defect observed live on .95 (NOTE-S135-B-discovery-relogin-2351Z, run 632a06668d6e):
discover #1 met the login wall, _relogin_for_discovery started the stored-credential
login (Turnstile + submit, ~87 s: 23:38:39 -> settled ok 23:40:06Z) but waited only
RELOGIN_TIMEOUT_S = 60 s, returned "failed" at 23:39:43Z, and the run finished
NOT_LOGGED_IN with 0 scenes while the login then settled ok. _mark_login_wall then
recorded a live jar as walled. The sibling fix dl95-naughtyamerica-2 (ec35b4befc,
9a38926534) raised only runner_auth._check_cookies_or_relogin (ev.wait 120 s) and
_await_in_flight_login (115 s); this callsite says it is "bounded like" that consumer
but kept 60 s.

Contract:
1. RELOGIN_TIMEOUT_S equals the runner_auth consumer wait (one value, pinned here by
   source like tests/test_dl95_naughtyamerica_2_relogin_timeout.py pins the waiter).
2. A relogin whose on_done(True) lands at ~87 s returns "ok" under the DEFAULT bound.
3. The bound still exists: a login that settles after it still returns "failed"; a login
   that settles False returns "failed"; no stored credentials -> "no_credentials".
"""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace


BD_GATE_SCOPE = "module"

from bulk_downloader import scene_crawler as crawler  # noqa: E402
from bulk_downloader.runner_auth import AuthMixin  # noqa: E402

LIVE_LOGIN_S = (
    87.0  # .95 naughtyamerica cb1af79f: login start -> "attempt 1 settled ok"
)


def _consumer_wait_s() -> float:
    src = inspect.getsource(AuthMixin._check_cookies_or_relogin)
    waits = [int(m) for m in re.findall(r"ev\.wait\(timeout=(\d+)\)", src)]
    assert waits, "no ev.wait(timeout=N) found in AuthMixin._check_cookies_or_relogin"
    return float(min(waits))


class _Runner:
    """login_async records on_done; the fake clock below decides when it fires."""

    def __init__(self, creds: bool = True):
        self.config = {"username": "member", "password": "secret"} if creds else {}
        self.on_done = None
        self.calls = 0

    def login_async(self, on_done=None, allow_manual=True):
        assert allow_manual is False, "discovery relogin must never open a manual login"
        self.calls += 1
        self.on_done = on_done


def _fake_clock(monkeypatch, runner: _Runner, settles_at_s: float, ok: bool):
    """Event.wait(timeout) returns at once: the login settles at settles_at_s; a wait
    that spans it sees on_done(ok) first (True), a shorter wait expires (False)."""
    seen: dict[str, float] = {}

    class _Event:
        def __init__(self):
            self._set = False

        def set(self):
            self._set = True

        def is_set(self):
            return self._set

        def wait(self, timeout=None):
            seen["timeout"] = timeout
            if timeout is None or timeout >= settles_at_s:
                runner.on_done(ok)
                return self._set
            return False

    monkeypatch.setattr(crawler, "threading", SimpleNamespace(Event=_Event))
    return seen


def test_discovery_relogin_bound_equals_runner_auth_consumer_wait():
    """One value: the discovery relogin waits exactly as long as the job path does."""
    consumer = _consumer_wait_s()
    assert crawler.RELOGIN_TIMEOUT_S == consumer, (
        f"dl95-discovery-relogin-timeout: scene_crawler.RELOGIN_TIMEOUT_S="
        f"{crawler.RELOGIN_TIMEOUT_S}s but runner_auth._check_cookies_or_relogin waits "
        f"{consumer}s; an ~{LIVE_LOGIN_S:.0f} s Turnstile login is cut off on discovery"
    )


def test_relogin_that_settles_ok_at_87s_is_ok_under_the_default_bound(monkeypatch):
    """RED at BASE: 60 s bound -> 'failed' while the login settles ok at 87 s."""
    r = _Runner()
    seen = _fake_clock(monkeypatch, r, LIVE_LOGIN_S, ok=True)
    got = crawler._relogin_for_discovery(r)
    assert r.calls == 1
    assert got == "ok", (
        f"dl95-discovery-relogin-timeout: relogin returned {got!r}: waited "
        f"{seen['timeout']}s, login settled ok at {LIVE_LOGIN_S:.0f}s -> discovery reports "
        "NOT_LOGGED_IN / 0 scenes and marks a live jar as walled"
    )


def test_relogin_that_settles_after_the_bound_is_still_failed(monkeypatch):
    """Negative control (GREEN at BASE and fix): the bound exists."""
    r = _Runner()
    seen = _fake_clock(monkeypatch, r, crawler.RELOGIN_TIMEOUT_S + 5.0, ok=True)
    assert crawler._relogin_for_discovery(r) == "failed"
    assert seen["timeout"] == crawler.RELOGIN_TIMEOUT_S


def test_relogin_that_settles_false_within_the_bound_is_failed(monkeypatch):
    """Negative control: success is the callback's verdict, not the wait returning."""
    r = _Runner()
    _fake_clock(monkeypatch, r, 10.0, ok=False)
    assert crawler._relogin_for_discovery(r) == "failed"


def test_no_stored_credentials_never_starts_a_login(monkeypatch):
    r = _Runner(creds=False)
    _fake_clock(monkeypatch, r, 10.0, ok=True)
    assert crawler._relogin_for_discovery(r) == "no_credentials"
    assert r.calls == 0
