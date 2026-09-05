"""Row 667: every credential-login attempt spends one shared site/day count."""
from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import threading
import time
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

BD_GATE_SCOPE = "module"


class _Log:
    def warning(self, *_args, **_kwargs):
        pass


class _Runner:
    """Small AuthMixin host that reaches the real asynchronous login seam."""

    def __init__(self, auth_mixin):
        self.__class__ = type("Row667Runner", (auth_mixin, _Runner), {})
        self.site_id = "row667-site"
        self.config = {
            "login_url": "https://invalid.test/login",
            "auto_teach_first_run": False,
        }
        self._login_thread = None
        self._manual_login_handle = None
        self._login_status = ""
        self.log = _Log()
        self.cookies = []

    def set_cookies(self, cookies):
        self.cookies = list(cookies)


def _capture_keeper_callback(monkeypatch, app, login, session_keeper, cfg):
    """Return the one real keeper adapter plus an exact site-contact log."""
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app, "_SITE_RUNTIME_RETIRING", False)
    monkeypatch.setattr(app, "s_cfg", {"row667-cap-site": cfg})
    callbacks = []
    login_calls = []
    monkeypatch.setattr(
        session_keeper,
        "start_keeper",
        lambda site_id, account_idx, keeper_cfg, callback: callbacks.append(
            (site_id, account_idx, keeper_cfg, callback)),
    )
    monkeypatch.setattr(
        login,
        "do_login",
        lambda *args, **kwargs: (
            login_calls.append((args, kwargs)) or
            (False, "fixture rejection", [])),
    )
    app._start_session_keepers()
    assert len(callbacks) == 1, (
        "precondition: the fixture must register exactly one keeper callback")
    return callbacks[0], login_calls


def test_both_real_login_callers_spend_the_same_accounting_denominator(
        monkeypatch, tmp_path):
    app = importlib.import_module("bulk_downloader.app")
    login = importlib.import_module("bulk_downloader.login")
    runner_auth = importlib.import_module("bulk_downloader.runner_auth")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.chdir(tmp_path)
    accounting_rows = []

    def _fixture_reserve(site_id, source, cap, account_idx=None):
        """Stand in for the ONE seam both callers must spend."""
        taken = sum(row[0] == site_id for row in accounting_rows)
        if taken >= int(cap):
            return {"granted": False, "status": "OK", "count": taken,
                    "cap": int(cap), "reason": None}
        accounting_rows.append((site_id, source, account_idx))
        return {"granted": True, "status": "OK", "count": taken + 1,
                "cap": int(cap), "reason": None}

    monkeypatch.setattr(
        session_keeper, "reserve_login_attempt", _fixture_reserve,
        raising=False)
    monkeypatch.setattr(
        session_keeper,
        "login_attempts_for_day",
        lambda site_id: {
            "status": "OK",
            "count": sum(row[0] == site_id for row in accounting_rows),
        },
        raising=False,
    )
    assert accounting_rows == [], "precondition: site/day count must begin at zero"

    runner_calls = []
    runner_accounting_counts_at_contact = []

    def runner_do_login(config, allow_manual_takeover=False):
        runner_accounting_counts_at_contact.append(len(accounting_rows))
        runner_calls.append((config, allow_manual_takeover))
        return True, "runner ok", []

    monkeypatch.setattr(
        runner_auth,
        "do_login",
        runner_do_login,
    )
    completed = threading.Event()
    outcomes = []
    runner = _Runner(runner_auth.AuthMixin)
    runner.login_async(
        on_done=lambda ok: (outcomes.append(ok), completed.set()),
        allow_manual=False,
    )
    assert completed.wait(2), "runner login callback did not complete"
    runner._login_thread.join(2)
    assert not runner._login_thread.is_alive()
    assert runner_accounting_counts_at_contact == [1], (
        "runner site contact must follow its accounting row; observed counts "
        f"{runner_accounting_counts_at_contact}")
    assert len(runner_calls) == 1, "runner_auth.login_async did not attempt login exactly once"
    assert outcomes == [True]

    keeper_callbacks = []
    keeper_calls = []
    keeper_accounting_counts_at_contact = []

    def keeper_do_login(config, allow_manual_takeover=False):
        keeper_accounting_counts_at_contact.append(len(accounting_rows))
        keeper_calls.append((config, allow_manual_takeover))
        return False, "fixture rejection", []

    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app, "_SITE_RUNTIME_RETIRING", False)
    monkeypatch.setattr(app, "s_cfg", {
        "row667-site": {
            "keep_alive_enabled": True,
            "username": "fixture-user",
            "password": "fixture-password",
        }
    })
    monkeypatch.setattr(
        session_keeper,
        "start_keeper",
        lambda site_id, account_idx, cfg, callback: keeper_callbacks.append(
            (site_id, account_idx, cfg, callback)),
    )
    monkeypatch.setattr(
        login,
        "do_login",
        keeper_do_login,
    )
    app._start_session_keepers()
    assert len(keeper_callbacks) == 1, "keeper callback registration did not fire exactly once"
    keeper_result = keeper_callbacks[0][3](
        keeper_callbacks[0][0], keeper_callbacks[0][1], keeper_callbacks[0][2])
    assert keeper_accounting_counts_at_contact == [2], (
        "keeper site contact must follow its accounting row; observed counts "
        f"{keeper_accounting_counts_at_contact}")
    assert len(keeper_calls) == 1, "keeper callback did not attempt login exactly once"
    assert keeper_result == (False, "login failed: fixture rejection")

    assert len(accounting_rows) == 2, (
        "expected two counted attempts after runner and keeper logins; "
        f"observed {len(accounting_rows)}")
    assert accounting_rows == [
        ("row667-site", "runner_auth.login_async", None),
        ("row667-site", "app._do_login_for_keeper", 0),
    ]


def test_durable_site_day_query_counts_attempts_and_preserves_attribution(
        monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667.db"))
    db.db_init()
    day = dt.date.today().isoformat()
    record = getattr(session_keeper, "record_login_attempt", lambda *_a, **_k: None)
    query = getattr(
        session_keeper,
        "login_attempts_for_day",
        lambda *_a, **_k: {
            "status": "OK", "count": 0, "attempts": [], "day": day,
        },
    )

    before = query("derived-fixture-site", day)
    assert before["status"] == "OK" and before["count"] == 0
    record("derived-fixture-site", "runner_auth.login_async")
    record("derived-fixture-site", "app._do_login_for_keeper", 3)
    after = query("derived-fixture-site", day)

    assert after["status"] == "OK"
    assert after["count"] == 2
    assert [row["source"] for row in after["attempts"]] == [
        "runner_auth.login_async", "app._do_login_for_keeper"]
    assert [row["account_idx"] for row in after["attempts"]] == [None, 3]


def test_runner_attempt_row_preserves_active_account_identity(monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    runner_auth = importlib.import_module("bulk_downloader.runner_auth")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-identity.db"))
    db.db_init()
    before = session_keeper.login_attempts_for_day("row667-site")
    assert before["status"] == "OK" and before["count"] == 0
    contacts = []
    monkeypatch.setattr(
        runner_auth,
        "do_login",
        lambda *_args, **_kwargs: (
            contacts.append("contacted") or (False, "fixture rejection", [])),
    )
    completed = threading.Event()
    runner = _Runner(runner_auth.AuthMixin)
    runner._active_account_idx = 4

    runner.login_async(on_done=lambda _ok: completed.set(), allow_manual=False)
    assert completed.wait(2), "runner login callback did not complete"
    runner._login_thread.join(2)
    assert not runner._login_thread.is_alive()
    assert contacts == ["contacted"], "precondition: login must contact once"

    result = session_keeper.login_attempts_for_day("row667-site")
    assert result["status"] == "OK" and result["count"] == 1
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["source"] == "runner_auth.login_async"
    assert result["attempts"][0]["account_idx"] == 4


def test_query_scopes_to_one_site_and_one_local_day(monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-scope.db"))
    db.db_init()
    assert hasattr(time, "tzset"), "local-day gate is unavailable without time.tzset"
    target_site = "row667-target-site"
    other_site = "row667-other-site"
    local_zone = ZoneInfo("America/New_York")

    def bounds(day):
        start = dt.datetime.combine(day, dt.time.min, local_zone).timestamp()
        finish = dt.datetime.combine(
            day + dt.timedelta(days=1), dt.time.min, local_zone).timestamp()
        return start, finish

    with monkeypatch.context() as timezone:
        timezone.setenv("TZ", "America/New_York")
        time.tzset()
        today = dt.date.today()
        explicit_day = today - dt.timedelta(days=10)
        populations = []
        expected_sources = {}
        for label, day in (("today", today), ("explicit", explicit_day)):
            start, finish = bounds(day)
            sources = {f"{label}-inside-one", f"{label}-inside-two"}
            expected_sources[day] = sources
            populations.extend([
                (start + 60, target_site, None, "login_attempt",
                 json.dumps({"source": f"{label}-inside-one"})),
                (start + 120, target_site, 1, "login_attempt",
                 json.dumps({"source": f"{label}-inside-two"})),
                (start + 90, other_site, None, "login_attempt",
                 json.dumps({"source": f"{label}-other-site"})),
                (start - 30, target_site, None, "login_attempt",
                 json.dumps({"source": f"{label}-yesterday-235930"})),
                (finish + 30, target_site, None, "login_attempt",
                 json.dumps({"source": f"{label}-tomorrow-000030"})),
                (start + 180, target_site, None, "heartbeat",
                 json.dumps({"source": f"{label}-not-a-login"})),
            ])
        with db.db_conn() as cx:
            cx.executemany(
                "INSERT INTO session_history"
                "(ts,site_id,account_idx,event_type,detail) VALUES(?,?,?,?,?)",
                populations,
            )
            assert cx.execute("SELECT COUNT(*) FROM session_history").fetchone()[0] == 12
            assert cx.execute(
                "SELECT COUNT(*) FROM session_history WHERE event_type='heartbeat'"
            ).fetchone()[0] == 2, (
                "precondition: the shared session_history table must hold both "
                "non-login rows inside the judged windows")

        measured = (
            (today, session_keeper.login_attempts_for_day(target_site)),
            (explicit_day, session_keeper.login_attempts_for_day(
                target_site, explicit_day.isoformat())),
        )
        assert len(measured) == 2
        for day, result in measured:
            start, finish = bounds(day)
            assert result["status"] == "OK"
            assert result["count"] == 2
            assert len(result["attempts"]) == 2
            assert {row["source"] for row in result["attempts"]} == expected_sources[day]
            assert all(start <= row["ts"] < finish for row in result["attempts"])

        with db.db_conn() as cx:
            before_guard = cx.execute(
                "SELECT COUNT(*) FROM session_history").fetchone()[0]
        assert before_guard == 12
        with pytest.raises(
                ValueError,
                match=r"^login attempt requires nonempty site_id and source$"):
            session_keeper.record_login_attempt("", "x")
        with db.db_conn() as cx:
            after_guard = cx.execute(
                "SELECT COUNT(*) FROM session_history").fetchone()[0]
        assert after_guard == before_guard, "invalid attribution wrote a history row"
    time.tzset()


@pytest.mark.parametrize(
    ("site_id", "source"),
    (("site", ""), ("   ", "x")),
    ids=("empty-source", "blank-site"),
)
def test_record_attempt_rejects_blank_identity_without_writing(
        monkeypatch, tmp_path, site_id, source):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(
        db, "DB_PATH", str(tmp_path / f"row667-guard-{site_id!r}.db"))
    db.db_init()
    with db.db_conn() as cx:
        assert cx.execute("SELECT COUNT(*) FROM session_history").fetchone()[0] == 0
    with pytest.raises(
            ValueError,
            match=r"^login attempt requires nonempty site_id and source$"):
        session_keeper.record_login_attempt(site_id, source)
    with db.db_conn() as cx:
        written = cx.execute("SELECT COUNT(*) FROM session_history").fetchone()[0]
    assert written == 0, "invalid attribution wrote a history row"


def test_reservation_strips_source_and_refuses_whitespace_before_writing(
        monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(
        db, "DB_PATH", str(tmp_path / "row667-reservation-source.db"))
    db.db_init()
    with db.db_conn() as cx:
        assert cx.execute(
            "SELECT COUNT(*) FROM session_history").fetchone()[0] == 0

    with pytest.raises(
            ValueError,
            match=r"^login attempt requires nonempty site_id and source$"):
        session_keeper.reserve_login_attempt(
            "row667-source-site", "   ", 2)

    accepted = session_keeper.reserve_login_attempt(
        "row667-source-site", "  fixture.source  ", 2)
    assert accepted["status"] == "OK" and accepted["granted"] is True
    assert accepted["count"] == 1
    measured = session_keeper.login_attempts_for_day("row667-source-site")
    assert measured["status"] == "OK" and measured["count"] == 1
    assert len(measured["attempts"]) == 1
    assert measured["attempts"][0]["source"] == "fixture.source"


def test_reservation_window_counts_only_login_attempt_events(
        monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(
        db, "DB_PATH", str(tmp_path / "row667-reservation-event.db"))
    db.db_init()
    db.session_event_record(
        "row667-event-site", None, "heartbeat_ok", "fixture control event")
    with db.db_conn() as cx:
        total_before = cx.execute(
            "SELECT COUNT(*) FROM session_history").fetchone()[0]
        attempts_before = cx.execute(
            "SELECT COUNT(*) FROM session_history WHERE event_type=?",
            ("login_attempt",),
        ).fetchone()[0]
    assert total_before == 1 and attempts_before == 0

    first = session_keeper.reserve_login_attempt(
        "row667-event-site", "fixture.event-window", 1)
    second = session_keeper.reserve_login_attempt(
        "row667-event-site", "fixture.event-window", 1)

    assert first["status"] == "OK" and first["granted"] is True, (
        "an unrelated session event exhausted the login-attempt cap")
    assert first["count"] == 1
    assert second["status"] == "OK" and second["granted"] is False
    assert second["count"] == 1
    with db.db_conn() as cx:
        by_event = dict(cx.execute(
            "SELECT event_type, COUNT(*) AS n FROM session_history "
            "GROUP BY event_type").fetchall())
    assert by_event == {"heartbeat_ok": 1, "login_attempt": 1}


def _insert_one_attempt(db, site_id, when, source):
    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO session_history"
            "(ts,site_id,account_idx,event_type,detail) VALUES(?,?,?,?,?)",
            (when.timestamp(), site_id, 2, "login_attempt",
             json.dumps({"source": source})),
        )
        assert cx.execute(
            "SELECT COUNT(*) FROM session_history WHERE site_id=?",
            (site_id,),
        ).fetchone()[0] == 1


def test_date_argument_uses_the_supplied_calendar_day(monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-date.db"))
    db.db_init()
    assert hasattr(time, "tzset"), "date-branch gate requires time.tzset"
    selected = dt.date(2020, 2, 3)
    with monkeypatch.context() as timezone:
        timezone.setenv("TZ", "UTC")
        time.tzset()
        _insert_one_attempt(
            db, "row667-date-site",
            dt.datetime(2020, 2, 3, 12, tzinfo=dt.timezone.utc), "date-branch")
        result = session_keeper.login_attempts_for_day(
            "row667-date-site", selected)
        assert result["day"] == "2020-02-03"
        assert result["status"] == "OK" and result["count"] == 1
        assert [row["source"] for row in result["attempts"]] == ["date-branch"]
    time.tzset()


def test_datetime_argument_uses_its_calendar_day(monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-datetime.db"))
    db.db_init()
    assert hasattr(time, "tzset"), "datetime-branch gate requires time.tzset"
    selected = dt.datetime(2021, 4, 5, 22, tzinfo=dt.timezone.utc)
    with monkeypatch.context() as timezone:
        timezone.setenv("TZ", "UTC")
        time.tzset()
        _insert_one_attempt(
            db, "row667-datetime-site", selected, "datetime-branch")
        result = session_keeper.login_attempts_for_day(
            "row667-datetime-site", selected)
        assert result["day"] == "2021-04-05"
        assert result["status"] == "OK" and result["count"] == 1
        assert [row["source"] for row in result["attempts"]] == [
            "datetime-branch"]
    time.tzset()


def test_keeper_daily_login_cap_refuses_n_plus_one(monkeypatch, tmp_path):
    app = importlib.import_module("bulk_downloader.app")
    db = importlib.import_module("bulk_downloader.db")
    login = importlib.import_module("bulk_downloader.login")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-cap.db"))
    db.db_init()
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app, "_SITE_RUNTIME_RETIRING", False)
    monkeypatch.setattr(app, "s_cfg", {
        "row667-cap-site": {
            "keep_alive_enabled": True,
            "username": "fixture-user",
            "password": "fixture-password",
            "login_attempt_cap_per_day": 2,
        }
    })
    callbacks = []
    login_calls = []
    monkeypatch.setattr(
        session_keeper,
        "start_keeper",
        lambda site_id, account_idx, cfg, callback: callbacks.append(
            (site_id, account_idx, cfg, callback)),
    )
    monkeypatch.setattr(
        login,
        "do_login",
        lambda *args, **kwargs: (
            login_calls.append((args, kwargs)) or
            (False, "fixture rejection", [])),
    )

    app._start_session_keepers()
    assert len(callbacks) == 1
    site_id, account_idx, cfg, callback = callbacks[0]
    outcomes = [callback(site_id, account_idx, cfg) for _ in range(3)]

    assert len(login_calls) == 2, "daily cap did not refuse keeper login N+1"
    result = session_keeper.login_attempts_for_day(site_id)
    assert result["status"] == "OK"
    assert result["count"] == 2
    assert len(result["attempts"]) == 2
    assert [row["source"] for row in result["attempts"]] == [
        "app._do_login_for_keeper", "app._do_login_for_keeper"]
    assert outcomes[:2] == [
        (False, "login failed: fixture rejection"),
        (False, "login failed: fixture rejection"),
    ]
    assert outcomes[2] == (
        False, "daily login attempt cap reached (2/2)")


def test_keeper_shipped_default_cap_refuses_the_fourth_attempt(
        monkeypatch, tmp_path):
    app = importlib.import_module("bulk_downloader.app")
    db = importlib.import_module("bulk_downloader.db")
    login = importlib.import_module("bulk_downloader.login")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-default-cap.db"))
    db.db_init()
    cfg = {
        "keep_alive_enabled": True,
        "username": "fixture-user",
        "password": "fixture-password",
    }
    assert "login_attempt_cap_per_day" not in cfg, (
        "precondition: this test must exercise the shipped default")
    for account_idx in range(3):
        session_keeper.record_login_attempt(
            "row667-cap-site", "fixture.seed", account_idx)
    before = session_keeper.login_attempts_for_day("row667-cap-site")
    assert before["status"] == "OK" and before["count"] == 3
    assert len(before["attempts"]) == 3
    callback_args, login_calls = _capture_keeper_callback(
        monkeypatch, app, login, session_keeper, cfg)

    outcome = callback_args[3](*callback_args[:3])

    assert outcome == (False, "daily login attempt cap reached (3/3)")
    assert login_calls == [], "the refused fourth attempt contacted the site"
    after = session_keeper.login_attempts_for_day("row667-cap-site")
    assert after["status"] == "OK" and after["count"] == 3


def test_keeper_invalid_cap_refuses_without_contact(monkeypatch):
    app = importlib.import_module("bulk_downloader.app")
    login = importlib.import_module("bulk_downloader.login")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    cfg = {
        "keep_alive_enabled": True,
        "username": "fixture-user",
        "password": "fixture-password",
        "login_attempt_cap_per_day": "not-an-integer",
    }
    query_calls = []
    monkeypatch.setattr(
        session_keeper, "login_attempts_for_day",
        lambda site_id: (
            query_calls.append(site_id) or {"status": "OK", "count": 0}),
    )
    callback_args, login_calls = _capture_keeper_callback(
        monkeypatch, app, login, session_keeper, cfg)

    outcome = callback_args[3](*callback_args[:3])

    assert outcome == (False, "daily login attempt cap is invalid")
    assert login_calls == [], "an invalid cap contacted the site"
    assert query_calls == [], "an invalid cap proceeded into measurement"


def test_keeper_zero_cap_refuses_without_contact(monkeypatch):
    app = importlib.import_module("bulk_downloader.app")
    login = importlib.import_module("bulk_downloader.login")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    cfg = {
        "keep_alive_enabled": True,
        "username": "fixture-user",
        "password": "fixture-password",
        "login_attempt_cap_per_day": 0,
    }
    query_calls = []
    monkeypatch.setattr(
        session_keeper, "login_attempts_for_day",
        lambda site_id: (
            query_calls.append(site_id) or {"status": "OK", "count": 0}),
    )
    callback_args, login_calls = _capture_keeper_callback(
        monkeypatch, app, login, session_keeper, cfg)

    outcome = callback_args[3](*callback_args[:3])

    assert outcome == (False, "daily login attempt cap must be positive")
    assert login_calls == [], "a zero cap contacted the site"
    assert query_calls == [], "a zero cap proceeded into measurement"


def test_keeper_unknown_count_refuses_without_contact(monkeypatch):
    app = importlib.import_module("bulk_downloader.app")
    login = importlib.import_module("bulk_downloader.login")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    cfg = {
        "keep_alive_enabled": True,
        "username": "fixture-user",
        "password": "fixture-password",
    }
    query_calls = []
    accounting_rows = []
    monkeypatch.setattr(
        session_keeper, "login_attempts_for_day",
        lambda site_id: (
            query_calls.append(site_id) or {
                "status": "UNKNOWN", "count": None,
                "reason": "fixture count unavailable",
            }),
    )
    monkeypatch.setattr(
        session_keeper, "record_login_attempt",
        lambda *args: accounting_rows.append(args),
    )
    callback_args, login_calls = _capture_keeper_callback(
        monkeypatch, app, login, session_keeper, cfg)

    outcome = callback_args[3](*callback_args[:3])

    assert outcome == (
        False, "daily login attempt cap unavailable: fixture count unavailable")
    assert query_calls == ["row667-cap-site"]
    assert accounting_rows == [], "UNKNOWN measurement wrote an attempt row"
    assert login_calls == [], "UNKNOWN measurement contacted the site"


def test_unavailable_attempt_measurement_is_unknown(monkeypatch):
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    def unavailable():
        raise OSError("fixture database unavailable")

    monkeypatch.setattr(session_keeper.db, "db_conn", unavailable)
    query = getattr(
        session_keeper,
        "login_attempts_for_day",
        lambda *_a, **_k: {"status": "UNKNOWN", "count": None},
    )
    result = query("row667-site")
    assert result["status"] == "UNKNOWN"
    assert result["count"] is None


def test_malformed_attempt_attribution_is_unknown(monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-malformed.db"))
    db.db_init()
    db.session_event_record(
        "row667-malformed-site", None, "login_attempt", "not-json")
    with db.db_conn() as cx:
        rows = cx.execute(
            "SELECT detail FROM session_history WHERE site_id=? AND event_type=?",
            ("row667-malformed-site", "login_attempt"),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["detail"] == "not-json"

    result = session_keeper.login_attempts_for_day("row667-malformed-site")
    assert result["status"] == "UNKNOWN"
    assert result["count"] == 1
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["source"] == "UNKNOWN"
    assert result["reason"] == "1 attempt row(s) lack attribution"


def test_whitespace_only_attempt_source_is_unknown(monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    site_id = "row667-whitespace-source-site"
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-whitespace.db"))
    db.db_init()
    db.session_event_record(
        site_id, None, "login_attempt", json.dumps({"source": "   "}))
    with db.db_conn() as cx:
        rows = cx.execute(
            "SELECT detail FROM session_history WHERE site_id=? AND event_type=?",
            (site_id, "login_attempt"),
        ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["detail"])["source"] == "   "

    result = session_keeper.login_attempts_for_day(site_id)

    assert result["status"] == "UNKNOWN"
    assert result["count"] == 1 and len(result["attempts"]) == 1
    assert result["attempts"][0]["source"] == "UNKNOWN"
    assert result["reason"] == "1 attempt row(s) lack attribution"


def test_login_cloak_choice_is_nonpersistent_by_design(monkeypatch):
    cloak = importlib.import_module("bulk_downloader.cloak")
    submit = importlib.import_module("bulk_downloader.login_impl.submit")

    calls = []

    class Browser:
        def new_context(self, **_kwargs):
            raise RuntimeError("stop after cloak selection")

        def close(self):
            pass

    monkeypatch.setattr(
        cloak, "launch_browser",
        lambda **kwargs: (calls.append(("launch", kwargs))
                          or (Browser(), None, "fixture-cloak")),
    )
    monkeypatch.setattr(
        cloak, "log_choice",
        lambda *args: calls.append(("choice", args)),
    )
    result = submit.do_login({
        "name": "row667-fixture",
        "login_url": "https://invalid.test/login",
        "username": "fixture-user",
        "password": "fixture-password",
        "use_real_chrome": False,
        "use_persistent_profile": True,
    })

    assert len([call for call in calls if call[0] == "launch"]) == 1
    assert [call for call in calls if call[0] == "choice"] == [
        ("choice", ("login", "fixture-cloak", "non-persistent"))]
    assert result[0] is False


# ── E1: the cap must be a bound, not a suggestion ────────────────────


def test_two_account_site_cannot_exceed_the_cap_under_concurrent_keepers(
        monkeypatch, tmp_path):
    """Two accounts on one site race ONE site-keyed denominator.

    ``_auto_relogin`` serialises the callback with
    ``get_takeover_lock(site_id, account_idx)`` -- keyed on (site, ACCOUNT) --
    while the counter is keyed on SITE alone, so the two keepers
    ``_start_session_keepers`` spawns for a two-account site enter the callback
    concurrently against one budget.  The barrier releases every racer only
    after all of them have read the count, which is exactly the interleaving an
    unlocked read-then-write admits and the shape a serial drive cannot see.
    """
    app = importlib.import_module("bulk_downloader.app")
    db = importlib.import_module("bulk_downloader.db")
    login = importlib.import_module("bulk_downloader.login")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-race.db"))
    db.db_init()
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app, "_SITE_RUNTIME_RETIRING", False)

    cap = 3
    racers = 8
    cfg = {
        "keep_alive_enabled": True,
        "login_attempt_cap_per_day": cap,
        "accounts": [
            {"username": "fixture-user-0", "password": "fixture-password"},
            {"username": "fixture-user-1", "password": "fixture-password"},
        ],
    }
    contacts = []
    shared = threading.Lock()

    def _fixture_do_login(*_args, **_kwargs):
        with shared:
            contacts.append("contacted")
        return False, "fixture rejection", []

    monkeypatch.setattr(login, "do_login", _fixture_do_login)
    real_query = session_keeper.login_attempts_for_day
    callbacks = []
    monkeypatch.setattr(
        session_keeper,
        "start_keeper",
        lambda site_id, account_idx, keeper_cfg, callback: callbacks.append(
            (site_id, account_idx, keeper_cfg, callback)),
    )

    def _one_trial(trial):
        site = f"row667-race-site-{trial}"
        monkeypatch.setattr(app, "s_cfg", {site: cfg})
        callbacks.clear()
        app._start_session_keepers()
        assert len(callbacks) == 2, (
            "precondition: a two-account site must spawn one keeper per "
            f"account; observed {len(callbacks)}")
        assert [entry[1] for entry in callbacks] == [0, 1]
        assert callbacks[0][3] is callbacks[1][3], (
            "precondition: both accounts must share ONE callback closure and "
            "therefore one site-keyed denominator")

        barrier = threading.Barrier(racers, timeout=30)
        entered = []

        def _barriered_query(site_id, *args, **kwargs):
            answer = real_query(site_id, *args, **kwargs)
            with shared:
                entered.append(site_id)
            barrier.wait()
            return answer

        monkeypatch.setattr(
            session_keeper, "login_attempts_for_day", _barriered_query)
        results = []
        registered = list(callbacks)

        def _racer(index):
            site_id, account_idx, keeper_cfg, callback = registered[index % 2]
            outcome = callback(site_id, account_idx, keeper_cfg)
            with shared:
                results.append(outcome)

        threads = [
            threading.Thread(target=_racer, args=(index,),
                             name=f"row667-racer-{trial}-{index}")
            for index in range(racers)
        ]
        before = len(contacts)
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        assert not any(thread.is_alive() for thread in threads), (
            "a racing keeper thread never finished")
        monkeypatch.setattr(
            session_keeper, "login_attempts_for_day", real_query)
        assert len(entered) == racers, (
            "precondition: every racer must read the count before any of them "
            f"records an attempt; observed {len(entered)} of {racers}")
        assert len(results) == racers
        measured = real_query(site)
        assert measured["status"] == "OK", measured
        return len(contacts) - before, measured["count"], results

    observed = [_one_trial(trial) for trial in range(5)]
    assert len(observed) == 5, "the race must be sampled repeatedly"
    for trial, (contacted, rows, results) in enumerate(observed):
        assert contacted == cap, (
            f"trial {trial}: {racers} concurrent keepers on a two-account site "
            f"made {contacted} site contacts against a cap of {cap}")
        assert rows == cap, (
            f"trial {trial}: {rows} attempt rows recorded against a cap of "
            f"{cap}; a refusal must never write a row")
        refusals = [detail for ok, detail in results
                    if not ok and "daily login attempt cap reached" in detail]
        assert len(refusals) == racers - cap, (
            f"trial {trial}: expected {racers - cap} cap refusals, observed "
            f"{len(refusals)} in {results}")


# ── E2: the caller that spends the budget also reads it ──────────────


def test_runner_login_reads_the_same_cap_it_spends(monkeypatch, tmp_path):
    """The UI/worker path is bounded by the budget it spends.

    Before this, four UI logins spent the day's budget and the keeper -- the
    caller the cap exists to govern -- was the only one refused.
    """
    app = importlib.import_module("bulk_downloader.app")
    db = importlib.import_module("bulk_downloader.db")
    login = importlib.import_module("bulk_downloader.login")
    runner_auth = importlib.import_module("bulk_downloader.runner_auth")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-runner-cap.db"))
    db.db_init()
    contacts = []
    monkeypatch.setattr(
        runner_auth,
        "do_login",
        lambda *_args, **_kwargs: (
            contacts.append("runner") or (False, "fixture rejection", [])),
    )
    statuses = []
    for _ in range(4):
        runner = _Runner(runner_auth.AuthMixin)
        runner.config["login_attempt_cap_per_day"] = 2
        completed = threading.Event()
        runner.login_async(on_done=lambda _ok: completed.set(),
                           allow_manual=False)
        assert completed.wait(10), "runner login callback did not complete"
        runner._login_thread.join(10)
        assert not runner._login_thread.is_alive()
        statuses.append(runner._login_status)

    assert len(contacts) == 2, (
        f"the runner path made {len(contacts)} site contacts against a cap "
        "of 2; a caller that spends the budget must also read it")
    measured = session_keeper.login_attempts_for_day("row667-site")
    assert measured["status"] == "OK" and measured["count"] == 2, measured
    assert statuses[2] == statuses[3], statuses
    assert statuses[2] == (
        "✗ daily login attempt cap reached (2/2); raise "
        "login_attempt_cap_per_day for this site to log in again today"), (
        f"the refusal must name the knob that lifts it; got {statuses[2]!r}")

    # One denominator AND one bound: the keeper is refused at the same
    # boundary rather than being the only caller the cap can reach.
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app, "_SITE_RUNTIME_RETIRING", False)
    monkeypatch.setattr(app, "s_cfg", {"row667-site": {
        "keep_alive_enabled": True,
        "username": "fixture-user",
        "password": "fixture-password",
        "login_attempt_cap_per_day": 2,
    }})
    keeper_callbacks = []
    monkeypatch.setattr(
        session_keeper,
        "start_keeper",
        lambda site_id, account_idx, keeper_cfg, callback: (
            keeper_callbacks.append(
                (site_id, account_idx, keeper_cfg, callback))),
    )
    keeper_contacts = []
    monkeypatch.setattr(
        login,
        "do_login",
        lambda *_args, **_kwargs: (
            keeper_contacts.append("keeper") or (False, "fixture", [])),
    )
    app._start_session_keepers()
    assert len(keeper_callbacks) == 1
    assert keeper_callbacks[0][3](*keeper_callbacks[0][:3]) == (
        False, "daily login attempt cap reached (2/2)")
    assert keeper_contacts == [], "the refused keeper attempt contacted the site"


def test_runner_refuses_when_reservation_write_makes_count_unknown(
        monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    runner_auth = importlib.import_module("bulk_downloader.runner_auth")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-write-fails.db"))
    db.db_init()
    real_reserve = session_keeper.reserve_login_attempt
    reservations = []

    def unavailable_accounting_write():
        raise OSError("fixture accounting write unavailable")

    def observed_reserve(*args, **kwargs):
        result = real_reserve(*args, **kwargs)
        reservations.append(dict(result))
        return result

    monkeypatch.setattr(db, "db_conn", unavailable_accounting_write)
    monkeypatch.setattr(
        session_keeper, "reserve_login_attempt", observed_reserve)
    contacts = []
    monkeypatch.setattr(
        runner_auth, "do_login",
        lambda *_args, **_kwargs: (
            contacts.append("contacted") or (True, "fixture ok", [])))

    runner = _Runner(runner_auth.AuthMixin)
    completed = threading.Event()
    outcomes = []
    runner.login_async(
        on_done=lambda ok: (outcomes.append(ok), completed.set()),
        allow_manual=False)
    assert completed.wait(10), "runner login callback did not complete"
    runner._login_thread.join(10)
    assert not runner._login_thread.is_alive()

    assert reservations == [{
        "granted": False,
        "status": "UNKNOWN",
        "count": None,
        "cap": 3,
        "reason": (
            "login attempt reservation unavailable: "
            "fixture accounting write unavailable"),
    }], "reservation storage failure must remain UNKNOWN and ungranted"
    assert outcomes == [False], "the UNKNOWN refusal must settle exactly once"
    assert runner._login_status == (
        "✗ daily login attempt cap unavailable: "
        "login attempt reservation unavailable: "
        "fixture accounting write unavailable")
    assert contacts == [], "an UNKNOWN reservation contacted the site"


# ── N1: a failed accounting import names the step that failed ────────


def test_unimportable_session_keeper_names_the_failing_step(
        monkeypatch, tmp_path):
    import sys as _sys

    package = importlib.import_module("bulk_downloader")
    db = importlib.import_module("bulk_downloader.db")
    runner_auth = importlib.import_module("bulk_downloader.runner_auth")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-n1.db"))
    db.db_init()
    contacts = []
    monkeypatch.setattr(
        runner_auth,
        "do_login",
        lambda *_args, **_kwargs: (
            contacts.append("contacted") or (True, "fixture ok", [])),
    )
    monkeypatch.delattr(package, "session_keeper", raising=False)
    monkeypatch.setitem(_sys.modules, "bulk_downloader.session_keeper", None)

    runner = _Runner(runner_auth.AuthMixin)
    completed = threading.Event()
    runner.login_async(on_done=lambda _ok: completed.set(), allow_manual=False)
    assert completed.wait(10), "runner login callback did not complete"
    runner._login_thread.join(10)
    assert not runner._login_thread.is_alive()

    assert contacts == [], "a login with no accounting still reached the site"
    status = runner._login_status
    assert "session_keeper is not importable" in status, (
        f"the refusal must name the failing step; got {status!r}")
    assert "cannot access local variable" not in status, (
        f"the operator is shown a Python scoping artifact: {status!r}")
    assert "None in sys.modules" in status, (
        f"the refusal must carry the importer's own words; got {status!r}")


# ── N2: we refused ourselves is not the site refused us ──────────────


def test_a_self_refusal_and_a_site_refusal_have_distinct_event_types(
        monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-n2.db"))
    db.db_init()

    def _drive(site_id, detail):
        keeper = session_keeper.SessionKeeper(
            site_id, 0,
            {"keep_alive_enabled": True, "password": "fixture-password"},
            lambda *_args, **_kwargs: (False, detail))
        monkeypatch.setattr(
            keeper, "_heartbeat",
            lambda: (session_keeper.DEAD, "fixture heartbeat failure"))
        keeper._run_one_check()
        with db.db_conn() as cx:
            return [row["event_type"] for row in cx.execute(
                "SELECT event_type FROM session_history WHERE site_id=? "
                "ORDER BY id ASC", (site_id,)).fetchall()]

    refused = _drive("row667-n2-refused",
                     "daily login attempt cap reached (3/3)")
    rejected = _drive("row667-n2-rejected",
                      "login failed: password rejected by the site")

    assert refused.count("heartbeat_fail") == 1, refused
    assert rejected.count("heartbeat_fail") == 1, rejected
    assert refused.count("auto_relogin_refused") == 1, (
        "a cap refusal never reached the site and must not be filed as one: "
        f"{refused}")
    assert refused.count("auto_relogin_fail") == 0, (
        f"our own refusal was filed as a site rejection: {refused}")
    assert rejected.count("auto_relogin_fail") == 1, (
        f"a real credential rejection lost its event type: {rejected}")
    assert rejected.count("auto_relogin_refused") == 0, (
        f"a site rejection was filed as our own refusal: {rejected}")


# ── N3: a day is bucketed with the offset in force on that day ───────


def test_day_bounds_use_the_offset_in_force_on_that_day(monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-dst.db"))
    db.db_init()
    assert hasattr(time, "tzset"), "the local-day gate needs time.tzset"
    zone = ZoneInfo("America/New_York")
    today_offset = dt.datetime.now(zone).utcoffset()
    judged = None
    for delta in (183, 91, 274, 150):
        candidate = dt.date.today() + dt.timedelta(days=delta)
        probe = dt.datetime(candidate.year, candidate.month, candidate.day,
                            12, 0, tzinfo=zone)
        if probe.utcoffset() != today_offset:
            judged = candidate
            break
    assert judged is not None, (
        "precondition: no candidate day sits on the other side of a DST "
        "transition, so this gate cannot see the defect")

    edges = [
        dt.datetime(judged.year, judged.month, judged.day, 0, 30,
                    tzinfo=zone).timestamp(),
        dt.datetime(judged.year, judged.month, judged.day, 23, 30,
                    tzinfo=zone).timestamp(),
    ]
    with db.db_conn() as cx:
        cx.executemany(
            "INSERT INTO session_history"
            "(ts,site_id,account_idx,event_type,detail) VALUES(?,?,?,?,?)",
            [(ts, "row667-dst-site", None, "login_attempt",
              json.dumps({"source": "fixture.dst-edge"})) for ts in edges])
        assert cx.execute(
            "SELECT COUNT(*) FROM session_history"
        ).fetchone()[0] == 2, "precondition: both edge rows must exist"

    with monkeypatch.context() as timezone:
        timezone.setenv("TZ", "America/New_York")
        time.tzset()
        on_day = session_keeper.login_attempts_for_day(
            "row667-dst-site", judged.isoformat())
        before = session_keeper.login_attempts_for_day(
            "row667-dst-site", (judged - dt.timedelta(days=1)).isoformat())
        after = session_keeper.login_attempts_for_day(
            "row667-dst-site", (judged + dt.timedelta(days=1)).isoformat())
    time.tzset()

    assert on_day["status"] == "OK" and on_day["count"] == 2, (
        f"both edges of {judged.isoformat()} belong to that local day; the "
        f"window bucketed {on_day['count']} of 2")
    assert before["count"] == 0, (
        f"an offset sampled today spilled a row into the previous day: "
        f"{before['count']}")
    assert after["count"] == 0, (
        f"an offset sampled today spilled a row into the next day: "
        f"{after['count']}")


# ── N4/N5: the knob and the vocabulary are discoverable ──────────────


def test_the_login_attempt_cap_is_documented_for_the_operator():
    import pathlib

    setup = (pathlib.Path(__file__).resolve().parents[1]
             / "SETUP.md").read_text(encoding="utf-8")
    heading = "### Daily login attempt cap"
    assert heading in setup, (
        "SETUP.md must carry the operator-facing section for the auth knob")
    section = setup.split(heading, 1)[1].split("\n## ", 1)[0]
    assert len(section.strip()) > 0, "the documented section is empty"
    for phrase in ("login_attempt_cap_per_day", "`3`", "keeper",
                   "auto_relogin_refused"):
        assert phrase in section, (
            f"the documented cap section omits {phrase!r}")


def test_session_event_record_documents_the_login_event_vocabulary():
    import pathlib
    import re as _re

    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    doc = db.session_event_record.__doc__ or ""
    source = pathlib.Path(session_keeper.__file__).read_text(encoding="utf-8")
    recorded = set(_re.findall(r'_record_event\(\s*"([a-z_]+)"', source))
    recorded.add(session_keeper._LOGIN_ATTEMPT_EVENT)
    # The relogin branch names its event through a classifier rather than a
    # literal, so ask the classifier for BOTH of its answers instead of
    # reading the call site -- the denominator must not shrink when a literal
    # moves behind a function.
    classifier = getattr(session_keeper, "relogin_event_type", None)
    if classifier is not None:
        recorded.add(classifier("daily login attempt cap reached (1/1)"))
        recorded.add(classifier("login failed: rejected by the site"))
    assert len(recorded) >= 7, (
        "precondition: the measured vocabulary must be nonzero and complete; "
        f"observed {sorted(recorded)}")
    missing = sorted(event for event in recorded if f"'{event}'" not in doc)
    assert missing == [], (
        f"session_event_record's documented vocabulary omits {missing}")


def test_runner_shipped_default_and_invalid_cap_refuse_without_contact(
        monkeypatch, tmp_path):
    """The runner's SHIPPED default is exercised, not just an explicit cap."""
    db = importlib.import_module("bulk_downloader.db")
    runner_auth = importlib.import_module("bulk_downloader.runner_auth")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-runner-default.db"))
    db.db_init()
    assert session_keeper.DEFAULT_LOGIN_ATTEMPT_CAP_PER_DAY == 3, (
        "precondition: this gate must exercise the value that ships")
    for account_idx in range(3):
        session_keeper.record_login_attempt(
            "row667-site", "fixture.seed", account_idx)
    seeded = session_keeper.login_attempts_for_day("row667-site")
    assert seeded["status"] == "OK" and seeded["count"] == 3

    contacts = []
    monkeypatch.setattr(
        runner_auth,
        "do_login",
        lambda *_args, **_kwargs: (
            contacts.append("runner") or (True, "fixture ok", [])),
    )

    def _drive(config_updates):
        runner = _Runner(runner_auth.AuthMixin)
        runner.config.update(config_updates)
        assert session_keeper.LOGIN_CAP_KEY == "login_attempt_cap_per_day"
        completed = threading.Event()
        outcomes = []
        runner.login_async(
            on_done=lambda ok: (outcomes.append(ok), completed.set()),
            allow_manual=False)
        assert completed.wait(10), "runner login callback did not complete"
        runner._login_thread.join(10)
        assert not runner._login_thread.is_alive()
        assert outcomes == [False], outcomes
        return runner._login_status

    default_status = _drive({})
    assert "login_attempt_cap_per_day" not in _Runner(
        runner_auth.AuthMixin).config, (
        "precondition: the default drive must carry no cap key")
    assert default_status == (
        "✗ daily login attempt cap reached (3/3); raise "
        "login_attempt_cap_per_day for this site to log in again today"), (
        f"the shipped default did not bound the runner: {default_status!r}")

    invalid_status = _drive({"login_attempt_cap_per_day": "not-an-integer"})
    assert invalid_status == (
        "✗ daily login attempt cap is invalid: "
        "login_attempt_cap_per_day='not-an-integer'"), (
        f"an invalid cap did not refuse: {invalid_status!r}")

    assert contacts == [], (
        f"a refused runner login contacted the site {len(contacts)} time(s)")
    after = session_keeper.login_attempts_for_day("row667-site")
    assert after["status"] == "OK" and after["count"] == 3, (
        f"a refusal wrote an attempt row: {after['count']}")


def test_a_self_refusal_is_not_counted_as_a_site_failure(monkeypatch, tmp_path):
    """The consumer of the failure vocabulary judges the new event too.

    ``db._SESSION_FAILURE_EVENTS`` drives the cockpit's per-site failure rate.
    A refusal WE issued must not inflate it: attributing our own bookkeeping to
    the site is the same collapse as N2 wearing the other face.  The
    ``heartbeat_fail`` that preceded it is still counted, so nothing is hidden.
    """
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")

    refused_event = session_keeper.RELOGIN_REFUSED_EVENT
    assert "auto_relogin_fail" in db._SESSION_FAILURE_EVENTS, (
        "precondition: the site-failure vocabulary must be intact")
    assert refused_event not in db._SESSION_FAILURE_EVENTS, (
        "a refusal we issued would be reported as a failure the site caused")
    assert refused_event not in db._SESSION_SUCCESS_EVENTS

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "row667-cluster.db"))
    db.db_init()

    def _drive(site_id, detail):
        keeper = session_keeper.SessionKeeper(
            site_id, 0,
            {"keep_alive_enabled": True, "password": "fixture-password"},
            lambda *_args, **_kwargs: (False, detail))
        monkeypatch.setattr(
            keeper, "_heartbeat",
            lambda: (session_keeper.DEAD, "fixture heartbeat failure"))
        keeper._run_one_check()

    _drive("row667-cluster-refused", "daily login attempt cap reached (3/3)")
    _drive("row667-cluster-rejected",
           "login failed: password rejected by the site")

    clusters = db.db_session_failure_clusters(lookback_days=1)
    per_site = clusters["per_site"]
    assert set(per_site) == {"row667-cluster-refused",
                             "row667-cluster-rejected"}, per_site
    assert per_site["row667-cluster-refused"]["failures"] == 1, (
        "our own refusal was clustered as a second site failure: "
        f"{per_site['row667-cluster-refused']}")
    assert per_site["row667-cluster-rejected"]["failures"] == 2, (
        "a real site rejection stopped being counted as one: "
        f"{per_site['row667-cluster-rejected']}")
    assert clusters["total_failures"] == 3, (
        f"expected 3 site failures across both drives; got {clusters}")
