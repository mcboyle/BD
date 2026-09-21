"""row858: proactive session-lifetime refresh must fire before 15% of the
session's predicted total lifetime remains, not merely before a fixed
(operator-tunable) lead time.

Defect fixed: SessionKeeper._compute_next_check_time() used ONLY the fixed
lead time (default 30min) as the refresh margin. For the common no-history
case (4h assumed lifetime), 30min is 12.5% of the lifetime -- LESS than the
15% the brief requires -- so the scheduler let the session run down past the
15%-remaining line before scheduling the check that would refresh it. The
fix takes whichever margin is LARGER: the fixed lead time (a floor) or
PROACTIVE_REFRESH_FRACTION (15%) of the session's total predicted lifetime.

These tests exercise the scheduling function directly (no browser, no
network, no login) -- see CLAUDE.md A5 for the isolation pattern reused from
tests/test_session_keeper.py.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tests.test_session_keeper import _isolated_cwd, _keeper_config, _reset_module_state

BD_GATE_SCOPE = "module"


def _register_keeper(sk, site_id, account_idx, login_cb):
    """Build a SessionKeeper and publish it in the module registry WITHOUT
    starting its thread -- predict_next_expiry() and _compute_next_check_time()
    only need keeper.state, never the running loop."""
    keeper = sk.SessionKeeper(site_id, account_idx, _keeper_config(), login_cb)
    sk._keepers[(site_id, account_idx)] = keeper
    return keeper


def test_refresh_margin_covers_15pct_of_default_lifetime():
    """RED on the pre-fix scheduler: with no observed history (assumed 4h
    lifetime) and 2200s (~15.3%) actually remaining, the next scheduled
    check must still land with >=15% of the total lifetime remaining.

    Pre-fix, the margin was the fixed 30min lead: time_to_lead =
    2200 - 1800 = 400, so the next check landed at remaining=1800s = 12.5%
    of the 14400s lifetime -- already past the 15% line."""
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        try:
            login_calls = []
            cb = lambda *a: (login_calls.append(a) or (True, ""))
            keeper = _register_keeper(sk, "row858", 0, cb)

            total_lifetime = sk.DEFAULT_SESSION_LIFETIME_SEC  # 4h, no history
            remaining = 2200.0  # 2200/14400 = 15.28% remaining right now
            now = time.time()
            keeper.state["last_login_ts"] = now - (total_lifetime - remaining)

            predicted = sk.predict_next_expiry("row858", 0)
            assert abs(predicted - (now + remaining)) < 2, (
                f"prediction setup wrong: predicted={predicted} now+remaining={now + remaining}")

            next_check = keeper._compute_next_check_time()
            remaining_at_check = predicted - next_check
            fraction_at_check = remaining_at_check / total_lifetime

            assert fraction_at_check >= 0.15 - 1e-6, (
                f"scheduled next check leaves only {fraction_at_check:.3%} of the "
                f"session's lifetime remaining, below the required 15% floor "
                f"(next_check in {next_check - now:.1f}s, remaining was {remaining}s)")
            # Never a login call from mere scheduling.
            assert login_calls == []
        finally:
            sk.stop_all()


def test_already_inside_margin_rechecks_soon_not_at_full_fetch_interval():
    """RED on the pre-fix scheduler: once the fixed lead time can't be met
    at all (predicted expiry is closer than the margin), the old code fell
    through to `now + fetch_interval` (~30min) -- which is AFTER the session
    is already predicted to have expired. The fix must schedule a near-term
    recheck instead."""
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        try:
            cb = lambda *a: (True, "")
            keeper = _register_keeper(sk, "row858b", 0, cb)

            total_lifetime = sk.DEFAULT_SESSION_LIFETIME_SEC
            remaining = 500.0  # deep inside the 15% margin (2160s)
            now = time.time()
            keeper.state["last_login_ts"] = now - (total_lifetime - remaining)

            predicted = sk.predict_next_expiry("row858b", 0)
            next_check = keeper._compute_next_check_time()

            # Must recheck comfortably before the session is predicted to
            # expire -- never at the ~30min default fetch cadence, which
            # would land AFTER `predicted`.
            assert next_check < predicted, (
                "next check is scheduled after the session is predicted to "
                "have already expired")
            assert (next_check - now) <= sk.PROACTIVE_REFRESH_RETRY_SEC * (1 + sk.JITTER_FRACTION) + 1
        finally:
            sk.stop_all()


def test_short_lived_session_retry_never_overshoots_expiry():
    """RED on the round-1 fix (correctness-lens finding, row858-A9-local
    REFUTE): PROACTIVE_REFRESH_RETRY_SEC=60 jittered up to +20% is 72s.
    For a session with a single observed 60s lifetime, checked right after
    a fresh login, the fixed lead time (1800s default) dwarfs the whole
    lifetime, so the scheduler is immediately inside the refresh margin --
    and a flat 60s(+jitter) retry can land at 72s, past BOTH the
    15%-remaining deadline (51s) and the session's own predicted expiry
    (60s). The retry must scale down with what's actually left."""
    from bulk_downloader import db, session_keeper as sk
    with _isolated_cwd():
        db.db_init()
        _reset_module_state()
        try:
            # Seed a single observed 60s lifetime for this site/account so
            # predict_next_expiry uses it instead of the 4h default.
            login_ts = time.time() - 100000
            fail_ts = login_ts + 60
            db.session_event_record("row858e", 0, "login", "")
            with db.db_conn() as cx:
                cx.execute(
                    "UPDATE session_history SET ts=? WHERE id=(SELECT MAX(id) FROM session_history)",
                    (login_ts,))
            db.session_event_record("row858e", 0, "heartbeat_fail", "")
            with db.db_conn() as cx:
                cx.execute(
                    "UPDATE session_history SET ts=? WHERE id=(SELECT MAX(id) FROM session_history)",
                    (fail_ts,))
            assert db.session_lifetime_observations("row858e", 0) == [60.0]

            cb = lambda *a: (True, "")
            keeper = _register_keeper(sk, "row858e", 0, cb)
            now = time.time()
            keeper.state["last_login_ts"] = now  # fresh login, right now

            predicted = sk.predict_next_expiry("row858e", 0)
            assert abs(predicted - (now + 60)) < 2, (
                f"prediction setup wrong: predicted={predicted} now+60={now + 60}")

            deadline = predicted - sk.PROACTIVE_REFRESH_FRACTION * 60  # ~51s out
            next_check = keeper._compute_next_check_time()

            assert next_check < predicted, (
                f"next check ({next_check - now:.1f}s out) is scheduled AT OR AFTER "
                f"the session's own predicted expiry ({predicted - now:.1f}s out)")
            assert next_check <= deadline + 1, (
                f"next check ({next_check - now:.1f}s out) overshoots the "
                f"15%-remaining deadline ({deadline - now:.1f}s out)")
        finally:
            sk.stop_all()


def test_run_loop_actually_uses_the_percentage_margin(monkeypatch):
    """The real _run() loop (not a direct call) must schedule its wait from
    _compute_next_check_time()'s percentage-aware margin -- catches a mutant
    that stops the loop from calling that scheduler at all (e.g. hardcoding
    next_ts), which a direct-call-only test cannot see."""
    from bulk_downloader import db, session_keeper as sk
    with _isolated_cwd():
        db.db_init()
        _reset_module_state()
        try:
            config = {"keep_alive_enabled": True, "password": "fixture"}
            keeper = _register_keeper(sk, "row858d", 0, lambda *a: (True, ""))
            keeper.config = config

            total_lifetime = sk.DEFAULT_SESSION_LIFETIME_SEC
            remaining = 2200.0  # same scenario as the first test: 15.28%
            now = time.time()
            keeper.state["last_login_ts"] = now - (total_lifetime - remaining)

            monkeypatch.setattr(keeper, "_heartbeat",
                                 lambda: (sk.ALIVE, "fixture heartbeat"))

            waits = []

            def finish_iteration(timeout):
                waits.append(timeout)
                keeper._stop.set()

            monkeypatch.setattr(keeper._wake, "wait", finish_iteration)
            keeper._run()

            assert len(waits) == 1
            expected_margin = max(sk._lead_time_sec(),
                                   sk.PROACTIVE_REFRESH_FRACTION * total_lifetime)
            expected_wait = remaining - expected_margin
            assert expected_wait > 0, "test setup: scenario must stay in the pre-margin branch"
            assert abs(waits[0] - expected_wait) < 5, (
                f"loop's wait ({waits[0]:.1f}s) does not match the percentage-margin "
                f"schedule ({expected_wait:.1f}s) -- the scheduler was not consulted")
        finally:
            sk.stop_all()


def test_fail_soft_backoff_unaffected_and_never_initiates_login():
    """Negative control / regression: a keeper in backoff (repeated heartbeat
    failures) is scheduled by the BACKOFF branch, not by the new proactive
    margin, and computing the schedule never calls the login callback --
    scheduling is fail-soft and passive, it must never itself log in.

    row965: this test asserted a FLAT hourly first wait, which row897 had
    already superseded. That fixer replaced the flat interval with a jittered
    exponential ladder driven by breaker_trips (first trip BACKOFF_BASE_SEC,
    ceiling BACKOFF_INTERVAL_SEC) precisely because the hour was unreachable in
    the old state machine -- so on main this assertion failed at 55s against a
    2879s floor, and the red was the assertion, not the scheduler. The subject
    of this test is the BRANCH and the login prohibition, never the magnitude
    of one step, so both ends of the ladder are pinned here instead: the first
    trip is one base interval, and repeated trips still climb to the hour the
    original assertion was reaching for.
    """
    from bulk_downloader import session_keeper as sk
    with _isolated_cwd():
        from bulk_downloader import db
        db.db_init()
        _reset_module_state()
        try:
            login_calls = []
            cb = lambda *a: (login_calls.append(a) or (False, "simulated refresh failure"))
            keeper = _register_keeper(sk, "row858c", 0, cb)
            keeper.state["last_login_ts"] = time.time() - 100
            keeper.state["consecutive_failures"] = sk.BACKOFF_AFTER_N_FAILURES

            now = time.time()
            next_check = keeper._compute_next_check_time()
            delay = next_check - now
            # THE BRANCH: repeated failures trip the circuit breaker. If the
            # margin logic had swallowed this case the keeper would be in no
            # trip at all and would be scheduled off the predicted expiry.
            assert keeper.state["breaker_trips"] == 1, (
                "repeated failures did not reach the backoff branch: "
                f"breaker_trips={keeper.state['breaker_trips']}, state="
                f"{keeper.state['state']}")
            assert keeper.state["state"] == "circuit_open"
            # FIRST RUNG: one jittered base interval, never immediate.
            lo = sk.BACKOFF_BASE_SEC * (1 - sk.JITTER_FRACTION) - 1
            hi = sk.BACKOFF_BASE_SEC * (1 + sk.JITTER_FRACTION) + 1
            assert lo <= delay <= hi, (
                f"first trip waited {delay:.1f}s, outside the jittered base "
                f"interval [{lo:.1f}, {hi:.1f}]")
            # LAST RUNG: the ladder still reaches the hour the pre-row897
            # assertion was reaching for. Each iteration clears the open
            # window so _compute_next_check_time re-trips at the next exponent.
            for _ in range(12):
                keeper.state["open_until_ts"] = 0.0
                keeper.state["consecutive_failures"] = sk.BACKOFF_AFTER_N_FAILURES
                ceiling_delay = keeper._compute_next_check_time() - time.time()
            assert keeper.state["breaker_trips"] == 13
            assert ceiling_delay >= sk.BACKOFF_INTERVAL_SEC * (1 - sk.JITTER_FRACTION) - 1, (
                f"the backoff ladder tops out at {ceiling_delay:.1f}s, short of "
                f"the {sk.BACKOFF_INTERVAL_SEC}s ceiling")
            # NO LOGIN, at any rung: scheduling is passive (Rule 21 in spirit --
            # this suite never initiates a login flow on any site).
            assert login_calls == []
        finally:
            sk.stop_all()


# ── row858 FIXER (E1/E2): renewal simulation on real keeper paths ────


def _simulated_site(monkeypatch, sk, keeper, lifetime, fetch_renews, nav_renews=True):
    """Deterministic server-session simulation on the REAL keeper loop /
    heartbeat / scheduler paths (no browser, socket, cookie or login).

    The server session expires `lifetime` after login; a page NAVIGATION
    renews it when `nav_renews` (default True, sliding session), an in-page
    FETCH renews it only when `fetch_renews`. When `nav_renews` is False,
    the session is absolute and expires strictly at start + lifetime."""
    from bulk_downloader import global_config

    start = 100000.0
    clock = [start]
    expires = [start + lifetime]
    horizon = start + 2 * lifetime + 60
    obs = dict(fetches=[], navigations=[], renewals=[], aborts=[],
               login_requests=[], predicted_after_nav=[], checks_in_last_15pct=[])
    monkeypatch.setattr(sk, "_now", lambda: clock[0])
    monkeypatch.setattr(sk, "_jitter", lambda value: value)
    monkeypatch.setattr(global_config, "get_config", lambda: {})
    monkeypatch.setattr(sk.db, "session_lifetime_observations",
                         lambda *a: [lifetime])
    keeper.state["last_login_ts"] = start
    keeper._last_navigate_at = start  # the login itself was a navigation
    for name in ("_persist_cookies", "_teardown_browser", "_record_event"):
        monkeypatch.setattr(keeper, name, lambda *a: None)
    monkeypatch.setattr(keeper, "_browser_alive", lambda: True)
    monkeypatch.setattr(keeper, "_browser_age", lambda: 0)

    def refused_relogin():
        obs["login_requests"].append(clock[0] - start)
        return False, "fixture refuses an actual login"

    monkeypatch.setattr(keeper, "_auto_relogin", refused_relogin)

    def request(renews, calls):
        calls.append(clock[0] - start)
        if clock[0] >= expires[0]:
            return 403
        if renews:
            expires[0] = clock[0] + lifetime
            obs["renewals"].append(clock[0] - start)
        return 200

    class Page:
        url = "https://fixture.invalid/member"

        def goto(self, *a, **kw):
            return SimpleNamespace(status=request(nav_renews, obs["navigations"]))

        def evaluate(self, script):
            if "await fetch(" not in script:
                return False
            return {"status": request(fetch_renews, obs["fetches"]),
                    "body": "member"}

    keeper._page = Page()

    def advance(timeout):
        now_rel = clock[0] - start
        # Check if this check ran inside the last 15% of the session validity window
        remaining = expires[0] - clock[0]
        if 0 < remaining <= 0.15 * lifetime + 1:
            obs["checks_in_last_15pct"].append(now_rel)

        if obs["navigations"]:
            obs["predicted_after_nav"].append(
                (keeper.state["predicted_expiry_ts"] or 0.0) - start)
        target = min(clock[0] + timeout, horizon)
        if target >= expires[0] and not obs["aborts"]:
            obs["aborts"].append(expires[0] - start)
        clock[0] = target
        if clock[0] >= horizon:
            keeper._stop.set()

    monkeypatch.setattr(keeper._wake, "wait", advance)
    keeper._run()
    obs["expires_at_end"] = expires[0] - start
    return obs


@pytest.mark.parametrize("lifetime,fetch_renews", [
    (1200.0, False),    # 20-min session, only a navigation renews (E1)
    (1200.0, True),     # 20-min sliding cookie
    (14400.0, False),   # 4-h session, navigation-only
    (14400.0, True),    # 4-h sliding cookie
])
def test_session_is_renewed_by_navigation_before_expiry_and_prediction_rebases(
        monkeypatch, lifetime, fetch_renews):
    """E1: on a site where navigation renews (sliding session), the keeper must
    NAVIGATE (not merely fetch) before the server session expires, so a stream that
    spans two lifetimes never runs off the end of the session. E2: an
    authenticated navigation re-bases predicted_expiry_ts onto the renewal
    (last_renewal_ts), so the prediction keeps sliding instead of pinning to
    the login time. No login is ever initiated."""
    from bulk_downloader import db, session_keeper as sk
    with _isolated_cwd():
        db.db_init()
        _reset_module_state()
        try:
            keeper = _register_keeper(
                sk, "row858e", 0,
                lambda *a: pytest.fail("no actual login may be initiated"))
            keeper.config = {"keep_alive_enabled": True, "password": "fixture",
                             "keep_alive_check_url": "https://fixture.invalid/member",
                             "session_renewable": True}
            obs = _simulated_site(monkeypatch, sk, keeper, lifetime, fetch_renews, nav_renews=True)

            assert obs["fetches"] or obs["navigations"], "non-vacuous: heartbeat path ran"
            assert obs["login_requests"] == [], obs
            assert obs["aborts"] == [], obs
            # E1: at least one renewing navigation strictly BEFORE the first
            # expiry, and the session stayed valid through two lifetimes.
            assert obs["navigations"], obs
            assert min(obs["navigations"]) < lifetime, obs
            assert obs["expires_at_end"] > 2 * lifetime, obs
            # E2: after a navigation the prediction runs from the renewal,
            # not from login -- it must have moved past login + lifetime.
            assert obs["predicted_after_nav"], obs
            assert max(obs["predicted_after_nav"]) > lifetime, obs
            assert keeper.state["last_renewal_ts"] > keeper.state["last_login_ts"]
        finally:
            sk.stop_all()


@pytest.mark.parametrize("lifetime", [7200.0, 1200.0])
def test_absolute_session_without_navigation_renewal_does_not_rebase_and_checks_inside_margin(
        monkeypatch, lifetime):
    """Correctness lens findings (row858-A9-local, row858-r2-local REFUTE):
    On an absolute-lifetime site (navigation renewal is OFF), navigation does
    NOT renew the session on the server. predicted_expiry_ts must NOT be pushed
    later into the future (must stay strictly login + L), a check must still
    be executed inside the last 15% of the session's validity -- and ONLY a
    bounded number of them (r2 E1: the pre-fix scheduler re-navigated at a
    retry shrinking with `remaining`, 57 page loads for L=7200 / 36 for
    L=1200, 25 of them in the last 120s, none able to renew anything)."""
    from bulk_downloader import db, session_keeper as sk
    with _isolated_cwd():
        db.db_init()
        _reset_module_state()
        try:
            keeper = _register_keeper(
                sk, "row858_abs", 0,
                lambda *a: pytest.fail("no actual login may be initiated"))
            # session_renewable is False (default)
            keeper.config = {"keep_alive_enabled": True, "password": "fixture",
                             "keep_alive_check_url": "https://fixture.invalid/member",
                             "session_renewable": False}
            obs = _simulated_site(monkeypatch, sk, keeper, lifetime, fetch_renews=False, nav_renews=False)

            # 1. predicted_expiry_ts must strictly equal login + lifetime (never pushed to 10800)
            login_ts = keeper.state["last_login_ts"]
            predicted = keeper.state["predicted_expiry_ts"]
            assert predicted == pytest.approx(login_ts + lifetime, abs=1.0), (
                f"predicted_expiry_ts ({predicted}) drifted from login + lifetime ({login_ts + lifetime})"
            )
            # 2. A check must have run inside the last 15% (between 0.85*L and 1.0*L)
            assert len(obs["checks_in_last_15pct"]) >= 1, (
                f"No check occurred inside the last 15% of the session (checks: {obs['navigations']})"
            )
            # 3. (r2 E1) One in-margin navigation is the whole proactive
            # step on an absolute session: at most 3 page loads before
            # expiry, and no two checks of any kind closer than 30s.
            before_expiry = [t for t in obs["navigations"] if t < lifetime]
            assert 1 <= len(before_expiry) <= 3, (
                f"{len(before_expiry)} navigations before expiry: {before_expiry}")
            checks = sorted(obs["navigations"] + obs["fetches"])
            gaps = [b - a for a, b in zip(checks, checks[1:])]
            assert gaps and min(gaps) >= 30.0, f"check burst, gaps={gaps}"
            # 4. The check that can act (relogin) lands just past expiry.
            assert any(lifetime <= t <= lifetime + 2.0 for t in obs["navigations"]), (
                f"no navigation at the predicted expiry: {obs['navigations']}")
        finally:
            sk.stop_all()


def test_renewal_margin_is_capped_at_half_the_lifetime(monkeypatch):
    """M3 (row858-r2 lens): RENEWAL_MARGIN_MAX_LIFETIME_FRACTION = 0.5 caps
    the navigation window. A 30-min lead time on a 20-min renewable session
    must NOT make every heartbeat a navigation: the renewal is due only once
    half the lifetime (600s) remains, not right after login."""
    from bulk_downloader import db, session_keeper as sk
    with _isolated_cwd():
        db.db_init()
        _reset_module_state()
        try:
            keeper = _register_keeper(sk, "row858cap", 0, lambda *a: (True, ""))
            keeper.config = {**keeper.config, "session_renewable": True}
            lifetime = 1200.0
            monkeypatch.setattr(sk.db, "session_lifetime_observations",
                                lambda *a: [lifetime])
            assert sk._lead_time_sec() >= lifetime, "precondition: lead >= L"
            now = time.time()
            keeper.state["last_login_ts"] = now - 100.0     # 1100s remain
            predicted = sk.predict_next_expiry("row858cap", 0)
            assert predicted - now > 0.5 * lifetime
            assert keeper._renewal_due() is False, "cap ignored: due at 1100s remaining"
            keeper.state["last_login_ts"] = now - 700.0     # 500s remain
            assert keeper._renewal_due() is True
        finally:
            sk.stop_all()
