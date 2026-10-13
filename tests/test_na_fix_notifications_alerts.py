"""na-fix: the notifications/alerts bug-hunt rows (O1789 sweep, F1-F5, F7-F9, F11).

Each test is one finding: the defect as an assertion, beside a control that
shows the probe can also answer the other way.
"""
import json
import time

import pytest

from bulk_downloader import alerts_engine as ae
from bulk_downloader import notify_apprise as na
from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"

RULE = {"metric": "bd_failure_rate_1h", "op": ">=", "threshold": 0,
        "duration_minutes": 0, "cooldown_minutes": 0}


def _db():
    from bulk_downloader import db
    db.db_init()
    ae._ensure_tables()
    return db


def _client():
    from bulk_downloader import app as A
    _db()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, {"X-CSRF-Token": csrf}


def _history(cx, status, modifier):
    cx.execute("INSERT INTO history(site_id,site_name,url,status,ts) VALUES('s','s','u',?,"
               "strftime('%Y-%m-%dT%H:%M:%S','now',?))", (status, modifier))


def _stored_rule(rid, column=1, **fields):
    """A row written straight to the store: what an older build could have saved."""
    with _db().db_conn() as cx:
        cx.execute("INSERT INTO alert_rules(rule_id, rule_json, enabled) VALUES (?,?,?)",
                   (rid, json.dumps({**RULE, "id": rid, **fields}), column))


def _strict(text):
    def refuse(name):
        raise ValueError("non-JSON constant " + name)
    return json.loads(text, parse_constant=refuse)


def _result(rule_id):
    return {r["rule_id"]: r for r in ae.evaluate()["results"]}[rule_id]


@pytest.fixture
def webhook_calls(monkeypatch):
    from bulk_downloader import webhooks
    calls = []
    monkeypatch.setattr(webhooks, "fire", lambda ev, payload: calls.append(payload["rule_id"]))
    return calls


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(na, "send", lambda urls, title, body, tag=None, **k:
                        (out.append((tag, title)), na.SendResult(sent_count=1))[1])
    return out


def _dispatcher(**kw):
    d = na.NotificationDispatcher()
    d.configure(urls=["json://127.0.0.1:9/x"], enabled=True, **kw)
    return d


def _age_batches(d):
    for batch in d._pending.values():
        batch.started_at -= 10_000


# ── F1: the "last hour" metrics measured the whole UTC day ────────────
def test_f1_last_hour_window_excludes_rows_older_than_an_hour(clean_workdir):
    """history.ts is `...T...`; the old bound was `... ...`, and 'T' > ' '.
    (Between 00:00 and 02:00 UTC the old rows fall on the previous date, so
    the pre-fix code also passes there; the fixed code passes at any hour.)"""
    with _db().db_conn() as cx:
        cx.execute("DELETE FROM history")
        for _ in range(3):
            _history(cx, "failed", "-2 hours")
    assert ae._evaluate_metric("bd_job_failures_1h") == 0.0
    assert ae._evaluate_metric("bd_failure_rate_1h") == 0.0
    with _db().db_conn() as cx:
        _history(cx, "failed", "-1 minutes")       # control: a recent failure counts
        _history(cx, "done", "-2 minutes")
    assert ae._evaluate_metric("bd_job_failures_1h") == 1.0
    assert ae._evaluate_metric("bd_failure_rate_1h") == 50.0


# ── F2: pending metrics read `history`; pending jobs live in `queue` ──
def test_f2_pending_metrics_read_the_queue(clean_workdir):
    with _db().db_conn() as cx:
        cx.execute("DELETE FROM queue")
        cx.execute("DELETE FROM history")
    assert ae._evaluate_metric("bd_pending_count") == 0.0          # control: empty
    assert ae._evaluate_metric("bd_oldest_pending_hours") == 0.0
    with _db().db_conn() as cx:
        for i in range(7):
            cx.execute("INSERT INTO queue(site_id,url,status) VALUES('s',?,'pending')", (f"http://x/{i}",))
        cx.execute("INSERT INTO queue(site_id,url,status,ts_added) VALUES('s','http://x/old','pending',"
                   "strftime('%Y-%m-%dT%H:%M:%S','now','-3 hours'))")
        cx.execute("INSERT INTO queue(site_id,url,status,ts_added) VALUES('s','http://x/done','done',"
                   "strftime('%Y-%m-%dT%H:%M:%S','now','-9 hours'))")
    assert ae._evaluate_metric("bd_pending_count") == 8.0
    assert 2.9 < ae._evaluate_metric("bd_oldest_pending_hours") < 3.1


# ── F3: one stored rule with a bad field stopped the whole pass ───────
@pytest.mark.parametrize("field", ["duration_minutes", "cooldown_minutes"])
@pytest.mark.parametrize("bad", ["soon", None, -1, "nan", True, [5]])
def test_f3_save_rule_refuses_a_field_evaluate_cannot_parse(clean_workdir, field, bad):
    _db()
    assert ae.save_rule({**RULE, "id": "r", field: bad}) is None
    assert ae.save_rule({**RULE, "id": "r", field: "15"}) == "r"   # control
    assert {r["id"]: r for r in ae.list_rules()}["r"][field] == 15


@pytest.mark.parametrize("field", ["duration_minutes", "cooldown_minutes"])
def test_f3_a_stored_bad_rule_is_one_error_not_the_end_of_the_pass(clean_workdir, field, webhook_calls):
    _stored_rule("a_poison", **{field: "soon"})
    _stored_rule("b_after", actions=["webhook"])
    report = ae.evaluate()
    results = {r["rule_id"]: r for r in report["results"]}
    assert f"bad {field}" in results["a_poison"]["error"]
    assert results["a_poison"]["fired"] is False
    assert results["b_after"]["fired"] is True      # the rule AFTER it still ran
    assert set(results) >= {r["id"] for r in ae.DEFAULT_RULES}


def test_f3_api_refuses_the_rule_and_evaluate_stays_200(clean_workdir):
    c, h = _client()
    r = c.post("/api/alerts/rules", headers=h, json={**RULE, "id": "high_failure_rate", "duration_minutes": "soon"})
    assert r.status_code == 400
    assert c.post("/api/alerts/evaluate", headers=h).status_code == 200


def test_f3_a_numeric_rule_name_fires_and_is_labelled(clean_workdir, webhook_calls):
    _db()
    assert ae.save_rule({**RULE, "id": "n1", "name": 123, "actions": ["webhook"]}) == "n1"
    res = _result("n1")
    assert res["fired"] is True and "error" not in res
    assert [a["message"] for a in ae.active_alerts() if a["rule_id"] == "n1"] == ["123"]


# ── F4: NaN / Infinity thresholds made the responses not JSON ─────────
@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", float("nan"), float("inf")])
def test_f4_save_rule_refuses_a_non_finite_threshold(clean_workdir, bad):
    _db()
    assert ae.save_rule({**RULE, "id": "t", "threshold": bad}) is None
    assert ae.save_rule({**RULE, "id": "t", "threshold": "1e3"}) == "t"     # control


def test_f4_a_stored_nan_threshold_keeps_the_responses_json(clean_workdir):
    c, h = _client()
    _stored_rule("nanrule", threshold=float("nan"))
    _stored_rule("infrule", threshold=float("inf"))
    rules = _strict(c.get("/api/alerts/rules").get_data(as_text=True))["rules"]
    assert {r["id"]: r["threshold"] for r in rules if r["id"] in ("nanrule", "infrule")} == \
        {"nanrule": None, "infrule": None}
    r = c.post("/api/alerts/evaluate", headers=h)
    results = {x["rule_id"]: x for x in _strict(r.get_data(as_text=True))["results"]}
    assert r.status_code == 200
    assert "bad threshold" in results["nanrule"]["error"]
    assert "bad threshold" in results["infrule"]["error"]


# ── F5: `enabled` was never read ──────────────────────────────────────
@pytest.mark.parametrize("how", ["json_false", "json_string_off", "column_0"])
def test_f5_a_disabled_rule_is_not_evaluated_and_does_not_fire(clean_workdir, webhook_calls, how):
    if how == "column_0":
        _stored_rule("off1", column=0, actions=["webhook"])
    else:
        _stored_rule("off1", actions=["webhook"], enabled=False if how == "json_false" else "off")
    _stored_rule("on1", actions=["webhook"])                       # control
    report = ae.evaluate()
    results = {r["rule_id"]: r for r in report["results"]}
    assert results["off1"]["fired"] is False and results["off1"]["enabled"] is False
    assert results["on1"]["fired"] is True
    assert webhook_calls == ["on1"]
    assert report["evaluated"] == len(ae.DEFAULT_RULES) + 1
    assert {r["id"]: r["enabled"] for r in ae.list_rules() if r["id"] in ("off1", "on1")} == \
        {"off1": False, "on1": True}


def test_f5_a_built_in_rule_can_be_switched_off_and_on_again(clean_workdir, monkeypatch, webhook_calls):
    _db()
    monkeypatch.setattr(ae, "_evaluate_metric", lambda *a, **k: 1.0)
    rule = {"id": "disk_low", "metric": "bd_disk_free_gb", "op": "<=", "threshold": 10,
            "duration_minutes": 0, "cooldown_minutes": 0, "actions": ["webhook"]}
    assert ae.save_rule({**rule, "enabled": False}) == "disk_low"
    assert _result("disk_low")["fired"] is False
    assert webhook_calls == []
    assert ae.save_rule({**rule, "enabled": True}) == "disk_low"
    assert _result("disk_low")["fired"] is True
    assert webhook_calls == ["disk_low"]


# ── F7: "batched" with no cap buffered for ever ───────────────────────
@pytest.mark.parametrize("cfg", [
    {"notify_captcha_mode": "batched"},
    {"notify_download_done_batch": 0, "notify_download_done_wait_s": 0},
    {"notify_download_done_batch": 0, "notify_download_done_wait_s": "inf"},
    {"notify_download_done_batch": 0, "notify_download_done_wait_s": "nan"},
])
def test_f7_no_configured_policy_can_buffer_without_a_way_out(cfg, sent):
    overrides = na.policy_from_config(cfg)
    (event, (mode, size, wait)), = overrides.items()
    assert mode == "batched" and (size > 0 or 0 < wait < float("inf")), overrides
    d = _dispatcher(policy_overrides=overrides)
    try:
        for i in range(50):
            d.notify(event, f"t{i}", "b")
        _age_batches(d)
        d._check_and_flush_expired()
        assert sum(len(b.items) for b in d._pending.values()) == 0
        assert sent and len(sent) in (1, 5)         # one timer flush, or 50 events at size cap 10
    finally:
        d.shutdown()


def test_f7_a_policy_with_no_cap_set_on_the_dispatcher_sends_at_once(sent):
    d = _dispatcher(policy_overrides={na.EVENT_CAPTCHA: ("batched", 0, 0.0)})
    control = _dispatcher(policy_overrides={na.EVENT_CAPTCHA: ("batched", 3, 0.0)})
    try:
        d.notify(na.EVENT_CAPTCHA, "t", "b")
        assert len(sent) == 1 and not d._pending
        control.notify(na.EVENT_CAPTCHA, "t", "b")      # control: a real cap still batches
        assert len(sent) == 1 and len(control._pending[na.EVENT_CAPTCHA].items) == 1
    finally:
        d._pending.clear(), control._pending.clear()
        d.shutdown(), control.shutdown()


def test_f7_an_explicit_batched_policy_is_kept(sent):
    assert na.policy_from_config({"notify_captcha_mode": "batched", "notify_captcha_batch": 3}) == \
        {na.EVENT_CAPTCHA: ("batched", 3, 0.0)}
    assert na.policy_from_config({"notify_download_done_wait_s": 30}) == \
        {na.EVENT_DOWNLOAD_DONE: ("batched", 10, 30.0)}


# ── F8: a batch buffered before "off" was still sent after it ─────────
@pytest.mark.parametrize("flush", ["timer", "flush_all", "size_cap_race"])
def test_f8_nothing_is_sent_after_notifications_are_switched_off(sent, flush):
    d = _dispatcher()
    try:
        d.notify(na.EVENT_DOWNLOAD_DONE, "before", "b")
        assert len(d._pending[na.EVENT_DOWNLOAD_DONE].items) == 1      # control: it was buffered
        if flush == "size_cap_race":
            d._enabled = False          # the flag flips while a flush is already on its way
            d._flush_batch(na.EVENT_DOWNLOAD_DONE)
        else:
            d.configure(enabled=False)
            _age_batches(d)
            d._check_and_flush_expired() if flush == "timer" else d.flush_all()
        assert sent == []
        assert not d._pending
    finally:
        d.shutdown()


def test_f8_control_an_enabled_dispatcher_still_flushes_on_the_timer(sent):
    d = _dispatcher()
    try:
        d.notify(na.EVENT_DOWNLOAD_DONE, "a", "b")
        _age_batches(d)
        d._check_and_flush_expired()
        assert [tag for tag, _ in sent] == [na.EVENT_DOWNLOAD_DONE]
    finally:
        d.shutdown()


# ── F9: a wrong-typed setting was saved, then broke every later apply ─
@pytest.mark.parametrize("body", [
    {"notify_apprise_urls": ["json://127.0.0.1:9/a"]},
    {"notify_apprise_urls": 5},
    {"notify_apprise_urls": {"a": 1}},
    {"notify_apprise_enabled": "yes"},
    {"notify_captcha_mode": "sometimes"},
    {"notify_download_done_batch": "many"},
    {"notify_download_done_batch": 2.5},
    {"notify_download_done_wait_s": "inf"},
    {"notify_download_done_wait_s": -1},
    {"notify_download_done_wait_s": True},
])
def test_f9_a_wrong_typed_setting_is_refused_and_not_stored(clean_workdir, body):
    from bulk_downloader import app as A
    c, h = _client()
    good = {"notify_apprise_enabled": True, "notify_apprise_urls": "json://127.0.0.1:9/a",
            "notify_download_done_batch": 4, "notify_download_done_wait_s": 30,
            "notify_captcha_mode": "per_event"}
    try:
        assert c.post("/api/notify/apprise/settings", headers=h, json=good).status_code == 200    # control
        before = A._load_global_notify_settings()
        r = c.post("/api/notify/apprise/settings", headers=h, json=body)
        assert r.status_code == 400 and next(iter(body)) in r.get_json()["error"]
        assert A._load_global_notify_settings() == before
        assert c.post("/api/notify/apprise/settings", headers=h,
                      json={"notify_apprise_enabled": False}).status_code == 200
        A._apply_global_notify_config()
    finally:
        na.get_dispatcher().configure(urls=[], enabled=False)


@pytest.mark.parametrize("stored,expected", [
    (["json://h/a", " json://h/b ", 7], ["json://h/a", "json://h/b"]),
    (5, []), ({"a": 1}, []), (True, []),
    ("json://h/a\n# c\n\njson://h/b", ["json://h/a", "json://h/b"]),       # control
])
def test_f9_an_already_stored_wrong_typed_url_value_no_longer_raises(stored, expected):
    assert na.parse_urls_text(stored) == expected


# ── F11: an unreadable store answered "nothing firing" ────────────────
def test_f11_active_alerts_on_an_unreadable_store_is_unknown_not_empty(clean_workdir, monkeypatch):
    from bulk_downloader import db
    c, _h = _client()
    r = c.get("/api/alerts/active")
    assert r.status_code == 200 and r.get_json() == {"alerts": []}        # control: readable store

    def boom(*a, **k):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(db, "db_conn", boom)
    with pytest.raises(ae.AlertEventStoreUnavailable, match="database is locked"):
        ae.active_alerts()
    r = c.get("/api/alerts/active")
    assert r.status_code == 503
    assert r.get_json()["store_unavailable"] is True and "alerts" not in r.get_json()
    r = c.get("/api/alerts/rules")
    assert r.status_code == 200 and "store unavailable" in r.get_json()["store_error"]
    assert len(r.get_json()["rules"]) == len(ae.DEFAULT_RULES)


def test_f11_control_a_readable_rule_store_reports_no_store_error(clean_workdir):
    c, _h = _client()
    assert "store_error" not in c.get("/api/alerts/rules").get_json()


@pytest.mark.parametrize("hours,status", [("abc", 400), ("1e3", 400), ("-5", 400), ("0", 400),
                                          ("99999999999999999999", 400), ("24", 200), ("", 200)])
def test_f11_a_bad_hours_value_is_a_400_not_a_500(clean_workdir, hours, status):
    c, _h = _client()
    assert c.get("/api/alerts/active?hours=" + hours).status_code == status


def test_fired_alert_is_visible_through_the_api(clean_workdir, webhook_calls):
    """End to end over the fixed paths: save, evaluate, read back."""
    c, h = _client()
    assert c.post("/api/alerts/rules", headers=h, json={**RULE, "id": "e2e", "name": "E2E"}).status_code == 200
    assert c.post("/api/alerts/evaluate", headers=h).status_code == 200
    alerts = c.get("/api/alerts/active?hours=1").get_json()["alerts"]
    assert [a["message"] for a in alerts if a["rule_id"] == "e2e"] == ["E2E"]
    assert time.time() - alerts[0]["ts"] < 60


def test_f11_spa_renders_store_failure_not_empty_state():
    """r2 (lens cx155 F11): /notifications renders the active-alerts 503 as an error, never "No active alerts.";
    /alerts/rules shows store_error beside the built-ins. 2 refusals + 2 healthy twins."""
    receipt = run_vitest("src/routes/NaFix.f11.test.tsx", expected_tests=4)
    assert receipt["files_passed"] == receipt["files_collected"] == 1
    assert receipt["tests_passed"] == receipt["tests_collected"] == 4
