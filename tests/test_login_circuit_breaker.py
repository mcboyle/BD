BD_GATE_SCOPE = "module"

def test_three_failures_open_the_keeper_circuit(monkeypatch):
    from bulk_downloader.session_keeper import SessionKeeper
    keeper = SessionKeeper("test", 0, {"password": "p", "keep_alive_enabled": True}, lambda *_: (False, "failed"))
    monkeypatch.setattr(keeper, "_heartbeat", lambda: (False, "dead"))
    monkeypatch.setattr(keeper, "_auto_relogin", lambda: (False, "failed"))
    for _ in range(3): keeper._run_one_check()
    assert keeper.state["state"] == "circuit_open"
    assert keeper.state["open_until_ts"] > 0


def test_backoff_is_exponential_and_capped(monkeypatch):
    from bulk_downloader import session_keeper as sk
    monkeypatch.setattr(sk, "_jitter", lambda value: value)
    assert [sk._backoff_delay(n) for n in (1, 2, 3)] == [60, 120, 240]
    assert sk._backoff_delay(99) == sk.BACKOFF_MAX_SEC


# ── FIXER (row897 REFUTE E1 HIGH / E2) ───────────────────────────────────

import pytest


def _keeper(monkeypatch, sk, clock):
    monkeypatch.setattr(sk, "_now", lambda: clock[0])
    monkeypatch.setattr(sk, "_jitter", lambda v: v)
    keeper = sk.SessionKeeper("t", 0, {"password": "p", "keep_alive_enabled": True}, lambda *_: (False, "no"))
    monkeypatch.setattr(keeper, "_record_event", lambda *a: None)
    monkeypatch.setattr(keeper, "_persist_cookies", lambda: None)
    return keeper


def _drive(keeper, clock, horizon, waits):
    """Run the real _run() loop on a fake clock; record every wait."""
    def advance(timeout=None):
        waits.append(timeout)
        clock[0] += timeout if timeout else 1
        if clock[0] >= horizon:
            keeper._stop.set()
    keeper._wake.wait = advance
    keeper._run()


def test_no_busy_loop_after_a_trip_when_checks_raise(monkeypatch):
    """E1 HIGH: after one trip+recovery, checks that RAISE must re-trip into
    an exponential window -- never schedule off a stale past open_until_ts
    (the 1s busy loop)."""
    from bulk_downloader import session_keeper as sk
    clock = [1000.0]
    keeper = _keeper(monkeypatch, sk, clock)
    calls = []

    def raising_check():
        calls.append(clock[0])
        raise RuntimeError("browser gone")

    monkeypatch.setattr(keeper, "_run_one_check", raising_check)
    waits = []
    _drive(keeper, clock, 1000.0 + 6 * 3600, waits)
    # first 3 raises are immediate-ish checks; from the 3rd on, every wait is a backoff window
    assert not any(w == 1 for w in waits[3:]), waits[:12]
    assert min(w for w in waits[3:]) >= sk.BACKOFF_BASE_SEC
    # the windows GROW to the ceiling (E2) rather than pinning at one size
    assert max(waits) == sk.BACKOFF_MAX_SEC, sorted(set(waits))[-4:]
    assert len(calls) < 40, len(calls)  # 6h of outage: tens of probes, not thousands


def test_windows_grow_exponentially_across_trips_to_the_ceiling(monkeypatch):
    """E2: relogin failures over a sustained outage: trip windows are
    60/120/240/.../3600 (trips-based), not a flat 240s; login attempts over
    24h are bounded by the doubling, not by luck."""
    from bulk_downloader import session_keeper as sk
    clock = [1000.0]
    keeper = _keeper(monkeypatch, sk, clock)
    monkeypatch.setattr(keeper, "_browser_alive", lambda: True)
    monkeypatch.setattr(keeper, "_browser_age", lambda: 0)
    logins = []
    monkeypatch.setattr(keeper, "_heartbeat", lambda: (False, "dead"))
    monkeypatch.setattr(keeper, "_auto_relogin", lambda: logins.append(clock[0]) or (False, "failed"))
    waits = []
    _drive(keeper, clock, 1000.0 + 24 * 3600, waits)
    windows = [w for w in waits if w >= sk.BACKOFF_BASE_SEC]
    first = windows.index(60)  # waits before the first trip are the normal fetch cadence
    assert windows[first:first + 6] == [60, 120, 240, 480, 960, 1920], windows[first:first + 8]
    assert max(windows) == sk.BACKOFF_MAX_SEC
    assert keeper.state["breaker_trips"] >= 6
    # sustained 24h outage: base behaviour was ~25 hourly attempts; the breaker must not exceed that
    assert len(logins) <= 30, len(logins)


def test_success_closes_the_breaker_and_resets_the_exponent(monkeypatch):
    from bulk_downloader import session_keeper as sk
    clock = [1000.0]
    keeper = _keeper(monkeypatch, sk, clock)
    monkeypatch.setattr(keeper, "_browser_alive", lambda: True)
    monkeypatch.setattr(keeper, "_browser_age", lambda: 0)
    outcome = {"ok": False}
    monkeypatch.setattr(keeper, "_heartbeat", lambda: (outcome["ok"], "x"))
    monkeypatch.setattr(keeper, "_auto_relogin", lambda: (False, "failed"))
    for _ in range(3):
        keeper._run_one_check()
    assert keeper.state["state"] == "circuit_open" and keeper.state["breaker_trips"] == 1
    # window elapses: the loop goes HALF-OPEN (one probe); that probe fails -> trip 2
    keeper.state["open_until_ts"] = 0.0
    keeper.state["consecutive_failures"] = sk.BACKOFF_AFTER_N_FAILURES - 1
    keeper._run_one_check()
    assert keeper.state["state"] == "circuit_open" and keeper.state["breaker_trips"] == 2
    keeper.state["open_until_ts"] = 0.0
    keeper.state["consecutive_failures"] = sk.BACKOFF_AFTER_N_FAILURES - 1
    outcome["ok"] = True
    keeper._run_one_check()
    assert keeper.state["state"] == "connected"
    assert keeper.state["breaker_trips"] == 0 and keeper.state["open_until_ts"] == 0.0
    assert keeper._compute_next_check_time() > clock[0] + 1


def test_needs_takeover_is_sticky_and_the_breaker_never_fires_a_login(monkeypatch):
    """Round 2 E3: credential rejected / captcha / 2FA -> needs_takeover holds
    (the UI takeover button keys off it) and NO further login is attempted
    over 8h, exactly as on base; the breaker never clobbers the state."""
    from bulk_downloader import session_keeper as sk
    clock = [1000.0]
    keeper = _keeper(monkeypatch, sk, clock)
    monkeypatch.setattr(keeper, "_browser_alive", lambda: True)
    monkeypatch.setattr(keeper, "_browser_age", lambda: 0)
    monkeypatch.setattr(keeper, "_heartbeat", lambda: (False, "dead"))
    answers = ["timeout", "timeout", "captcha challenge required"]
    logins = []

    def relogin():
        logins.append(clock[0])
        return False, answers[min(len(logins) - 1, 2)]

    monkeypatch.setattr(keeper, "_auto_relogin", relogin)
    waits = []
    _drive(keeper, clock, 1000.0 + 8 * 3600, waits)
    assert keeper.state["state"] == "needs_takeover"
    assert len(logins) == 3, logins  # the two timeouts + the rejected one; nothing after
    assert all(w >= 60 for w in waits[3:])
