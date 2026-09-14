"""Row 740: record_login_attempt must delegate to the atomic reservation.

reserve_login_attempt's count/decision/insert is one SQLite statement; before
this row, record_login_attempt wrote an unconditional row through
db.session_event_record instead, so a same-process caller of each function
could both see a stale count and both write, letting the pair exceed a cap
either one alone would have respected. 738 owns the multi-process case; this
row is same-process only.
"""
from __future__ import annotations

import importlib

import pytest

BD_GATE_SCOPE = "module"


def _fresh(monkeypatch, tmp_path, name):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / f"{name}.db"))
    db.db_init()
    return db, session_keeper


def test_direct_and_reserve_writers_share_one_cap_same_process(
        monkeypatch, tmp_path):
    """ACCEPT: direct+reserve same-process cap3 -> count3/one refusal."""
    db, session_keeper = _fresh(
        monkeypatch, tmp_path, "row740-interleave")
    site_id = "row740-cap-site"
    cap = 3
    assert session_keeper.DEFAULT_LOGIN_ATTEMPT_CAP_PER_DAY == cap, (
        "precondition: record_login_attempt's implicit cap must equal the "
        "cap this test drives reserve_login_attempt with")

    granted = session_keeper.reserve_login_attempt(
        site_id, "reserve.one", cap)
    assert granted["status"] == "OK" and granted["granted"] is True
    session_keeper.record_login_attempt(site_id, "direct.two")
    granted3 = session_keeper.reserve_login_attempt(
        site_id, "reserve.three", cap)
    assert granted3["status"] == "OK" and granted3["granted"] is True

    measured = session_keeper.login_attempts_for_day(site_id)
    assert measured["status"] == "OK" and measured["count"] == 3, (
        "precondition: three grants must have actually built a 3-row "
        "denominator before the fourth call is judged")

    refusals = []
    try:
        session_keeper.record_login_attempt(site_id, "direct.four")
    except RuntimeError as exc:
        refusals.append(str(exc))

    after = session_keeper.login_attempts_for_day(site_id)
    assert after["status"] == "OK" and after["count"] == 3, (
        "the direct writer bypassed the cap and wrote a fourth row")
    assert len(refusals) == 1, (
        "the direct writer must refuse exactly once at the shared cap, not "
        "silently succeed or raise more than once")
    assert refusals == ["daily login attempt cap reached (3/3)"]


def test_direct_writer_cannot_bypass_an_already_exhausted_cap(
        monkeypatch, tmp_path):
    """direct cannot bypass: reservation alone exhausts the cap first."""
    db, session_keeper = _fresh(monkeypatch, tmp_path, "row740-bypass")
    site_id = "row740-bypass-site"
    cap = session_keeper.DEFAULT_LOGIN_ATTEMPT_CAP_PER_DAY
    for idx in range(cap):
        outcome = session_keeper.reserve_login_attempt(
            site_id, f"reserve.{idx}", cap)
        assert outcome["granted"] is True

    before = session_keeper.login_attempts_for_day(site_id)
    assert before["status"] == "OK" and before["count"] == cap

    with pytest.raises(
            RuntimeError,
            match=rf"^daily login attempt cap reached \({cap}/{cap}\)$"):
        session_keeper.record_login_attempt(site_id, "direct.overflow")

    after = session_keeper.login_attempts_for_day(site_id)
    assert after["status"] == "OK" and after["count"] == cap, (
        "the refused direct call wrote a row anyway"
    )


def test_unavailable_reservation_raises_runtimeerror_not_typeerror(
        monkeypatch, tmp_path):
    """A refused/UNKNOWN reservation must surface RuntimeError with the
    reservation's own reason, never TypeError from touching a None count."""
    db, session_keeper = _fresh(monkeypatch, tmp_path, "row740-unknown")
    calls = []

    def _fake_reserve(site_id, source, cap, account_idx=None):
        calls.append((site_id, source, cap, account_idx))
        return {
            "granted": False, "status": "UNKNOWN", "count": None,
            "cap": cap, "reason": "fixture reservation unavailable: boom",
        }

    monkeypatch.setattr(session_keeper, "reserve_login_attempt", _fake_reserve)

    with pytest.raises(
            RuntimeError, match=r"^fixture reservation unavailable: boom$"):
        session_keeper.record_login_attempt("row740-site", "row740-source")

    assert calls == [
        ("row740-site", "row740-source",
         session_keeper.DEFAULT_LOGIN_ATTEMPT_CAP_PER_DAY, None)
    ], "record_login_attempt must delegate to reserve_login_attempt exactly once"
