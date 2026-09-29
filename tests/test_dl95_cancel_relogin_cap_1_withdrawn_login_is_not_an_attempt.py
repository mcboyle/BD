"""dl95-cancel-relogin-cap-1 (LOW): a login withdrawn before submit must not spend the daily cap.

Measured on .82 build 648a8802 (harness-work/DOT95-LANE/live-dl95-cancel-relogin-1/LIVE-RESULT-B6-B.md ADDENDUM):
a re-login cancelled before any credential fill ('Login cancelled before submit') was still a session_history
login_attempt (12:01:57Z) and brazzers read 3/3 -- a day's slot spent by a login that never submitted.

GREEN: login_async withdraws its own reservation when do_login returns the cancelled verdict; the row is re-filed
as 'login_attempt_withdrawn' (kept for audit, outside the cap denominator). Every other outcome still counts.

Hermetic: real SiteRunner.login_async, real session_keeper accounting on the isolated DB. No site.
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from bulk_downloader import db, session_keeper  # noqa: E402
from bulk_downloader.db import db_init  # noqa: E402
from bulk_downloader.login_impl.submit import LOGIN_CANCELLED_PREFIX  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

BD_GATE_SCOPE = "module"

SITE = "capWithdrawSite"


@pytest.fixture(autouse=True)
def _isolate(clean_workdir):
    yield clean_workdir


def _runner(cap=3):
    db_init()
    cfg = {
        "login_url": "https://example.com/login",
        "username": "u",
        "password": "p",
        "auto_teach_first_run": False,
        "learned": {"login": {"user_field": ["#username"], "pass_field": ["#password"], "submit_btn": ["#go"]}},
        "manual_use_persistent_profile": False,
        session_keeper.LOGIN_CAP_KEY: cap,
    }
    r = SiteRunner(SITE, cfg)
    r.start_manual_login = lambda *a, **k: (False, "test: no manual window")   # never a real browser
    return r


def _login(r, verdict):
    with mock.patch("bulk_downloader.runner_auth.do_login", return_value=verdict):
        r.login_async()
        r._login_thread.join(15)
    assert not r._login_thread.is_alive()


def _counted():
    got = session_keeper.login_attempts_for_day(SITE)
    assert got["status"] == "OK", got
    return got["count"]


def _events():
    with db.db_conn() as cx:
        return [row[0] for row in cx.execute(
            "SELECT event_type FROM session_history WHERE site_id=? AND event_type LIKE 'login_attempt%' "
            "ORDER BY id", (SITE,)).fetchall()]


CANCELLED = (False, f"{LOGIN_CANCELLED_PREFIX}the job that asked for it was cancelled", [])


def test_a_login_cancelled_before_submit_does_not_spend_a_daily_slot():
    r = _runner()
    _login(r, CANCELLED)
    assert _counted() == 0, (
        f"DL95_CANCEL_CAP: a login withdrawn before submit was counted against the daily cap: {_events()}")
    assert _events() == ["login_attempt_withdrawn"], (
        f"DL95_CANCEL_CAP: the withdrawn reservation must stay on record, re-filed: {_events()}")


def test_three_cancels_leave_the_day_open_for_a_real_login():
    """The live shape: cancels must not lock the site out of its one real login today."""
    r = _runner(cap=3)
    for _ in range(3):
        _login(r, CANCELLED)
    _login(r, (True, "ok", [{"name": "sid", "value": "v", "expires": 2**31}]))
    assert r._login_outcome[1] is True, (
        f"DL95_CANCEL_CAP: the cap refused a real login after cancels only: {r._login_status!r}")
    assert _counted() == 1


@pytest.mark.parametrize("verdict", [
    (False, "Login failed: credentials rejected", []),
    (True, "ok", [{"name": "sid", "value": "v", "expires": 2**31}]),
])
def test_a_submitted_login_still_counts(verdict):
    """Control: a login that ran (failed or not) spends its slot, as before."""
    r = _runner()
    _login(r, verdict)
    assert _counted() == 1 and _events() == ["login_attempt"], _events()


def test_the_real_entry_check_withdraws_the_reservation():
    """End to end through the real do_login: the abort predicate fires at the entry check, before any browser."""
    r = _runner()
    r._relogin_abort_reason = lambda: "the job that asked for it was cancelled"
    r.login_async()
    r._login_thread.join(30)
    assert str(r._login_status).find(LOGIN_CANCELLED_PREFIX) >= 0, r._login_status
    assert _counted() == 0, f"DL95_CANCEL_CAP: real cancelled do_login still counted: {_events()}"


def test_withdraw_touches_only_its_own_attempt_row():
    db_init()
    first = session_keeper.reserve_login_attempt(SITE, "test.first", 3)
    second = session_keeper.reserve_login_attempt(SITE, "test.second", 3)
    assert first["granted"] and second["granted"]
    assert session_keeper.withdraw_login_attempt(second["row_id"], "cancelled") is True
    assert session_keeper.withdraw_login_attempt(second["row_id"], "again") is False, "a row withdraws once"
    assert session_keeper.withdraw_login_attempt(None, "no reservation") is False
    got = session_keeper.login_attempts_for_day(SITE)
    assert [a["source"] for a in got["attempts"]] == ["test.first"], got
