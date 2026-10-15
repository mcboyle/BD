"""F039: one malformed alert_rules row must not hide every custom rule.

list_rules() decoded all rows inside one try; a single row whose rule_json
is not JSON (live 05:1xZ: "idx_sh_site_ts") raised JSONDecodeError and the
fallback dropped ALL custom rules, so a rule saved from /alerts never
appeared in the table and could not be edited or removed from the UI. A
row whose rule_json is JSON but not an object made list_rules raise past
its try (GET /api/alerts/rules -> 500). Bad rows are now skipped one by
one; the good rows and the built-ins still list. r2: rows whose merged
fields the table or evaluate() cannot use are skipped and reported too.
"""

from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

_BUILTIN_IDS = {
    "high_failure_rate",
    "queue_backlog",
    "disk_low",
    "circuits_open",
    "bitrot_growing",
    "stale_pending",
}
_GOOD = {
    "id": "f039_good",
    "metric": "bd_pending_count",
    "op": ">=",
    "threshold": 999999,
    "name": "F039 good",
}


def _seed(bad_rows):
    from bulk_downloader import alerts_engine as ae
    from bulk_downloader.db import db_conn, db_init

    db_init()
    assert ae.save_rule(dict(_GOOD)) == "f039_good"
    with db_conn() as cx:
        for rid, raw in bad_rows:
            cx.execute(
                "INSERT INTO alert_rules(rule_id, rule_json) VALUES (?,?)", (rid, raw)
            )


def _ids():
    from bulk_downloader import alerts_engine as ae

    return [r["id"] for r in ae.list_rules()]


def test_good_rule_lists_beside_a_non_json_row(clean_workdir):
    _seed([("idx_sh_site_ts", "idx_sh_site_ts")])
    ids = _ids()
    assert _BUILTIN_IDS <= set(ids), f"built-ins missing: {ids}"
    assert "f039_good" in ids, (
        f"F039: custom rule f039_good dropped by a malformed alert_rules row: {ids}"
    )
    assert "idx_sh_site_ts" not in ids


def test_good_rule_lists_beside_non_object_json_rows(clean_workdir):
    _seed([("f039_list", "[1, 2]"), ("f039_num", "5"), ("f039_null", "null")])
    ids = _ids()
    assert "f039_good" in ids, (
        f"F039: custom rule f039_good dropped by a non-object alert_rules row: {ids}"
    )
    assert not {"f039_list", "f039_num", "f039_null"} & set(ids)
    assert _BUILTIN_IDS <= set(ids)


def test_no_bad_rows_lists_the_good_rule(clean_workdir):
    # control: the seeding alone lists the rule on both sides of the fix
    _seed([])
    assert "f039_good" in _ids()


def test_api_lists_the_good_rule_beside_bad_rows(clean_workdir):
    _seed([("idx_sh_site_ts", "idx_sh_site_ts"), ("f039_list", "[1, 2]")])
    from bulk_downloader import app as A

    resp = A.app.test_client().get("/api/alerts/rules")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    ids = [r["id"] for r in resp.get_json()["rules"]]
    assert "f039_good" in ids, f"F039: GET /api/alerts/rules omits f039_good: {ids}"


def test_api_create_edit_delete_beside_a_bad_row(clean_workdir):
    # the F039 bar (create TEST rule, edit, delete) through the routes the
    # /alerts page calls, with the live malformed row present
    _seed([("idx_sh_site_ts", "idx_sh_site_ts")])
    from bulk_downloader import app as A

    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    H = {"X-CSRF-Token": csrf}

    def rules():
        resp = c.get("/api/alerts/rules")
        assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
        return {r["id"]: r for r in resp.get_json()["rules"]}

    body = {
        "id": "f039_ui",
        "metric": "bd_pending_count",
        "op": ">=",
        "threshold": 999999,
    }
    assert c.post("/api/alerts/rules", json=body, headers=H).status_code == 200
    assert rules().get("f039_ui", {}).get("threshold") == 999999, (
        f"F039: created rule not listed: {sorted(rules())}"
    )
    body["threshold"] = 888888
    assert c.post("/api/alerts/rules", json=body, headers=H).status_code == 200
    assert rules()["f039_ui"]["threshold"] == 888888
    assert c.post("/api/alerts/rules/f039_ui/remove", headers=H).status_code == 200
    assert "f039_ui" not in rules()


# ── r2 (REFUTE cx101 F1): rows that decode to an object but carry fields the
# table or the evaluator cannot use are skipped AND reported, never listed.
_OVERFLOW = (
    '{"metric": "bd_pending_count", "op": ">=", "threshold": 1' + "0" * 400 + "}"
)


def _row(extra):
    return '{"metric": "bd_pending_count", "op": ">=", "threshold": 1, %s}' % extra


_SEMANTIC_BAD = {
    "empty": "{}",
    "object_threshold": '{"metric": "bd_pending_count", "op": ">=", "threshold": {"bad": 1}}',
    "string_threshold": '{"metric": "bd_pending_count", "op": ">=", "threshold": "abc"}',
    "bool_threshold": '{"metric": "bd_pending_count", "op": ">=", "threshold": true}',
    # r3 (REFUTE cx115 F1): an int past float range made float() overflow
    "overflow_threshold": _OVERFLOW,
    # r3: fields evaluate() reads after a trip, and non-finite JSON anywhere
    "bad_duration": _row('"duration_minutes": "abc"'),
    "bad_cooldown": _row('"cooldown_minutes": {}'),
    "number_actions": _row('"actions": 5'),
    "nan_severity": _row('"severity": NaN'),
    "inf_duration": _row('"duration_minutes": 1e400'),
    "id_mismatch": _row('"id": "other_id"'),
    # r4 (REFUTE cx127 F1): evaluate() reads rule["op"] once the rule trips
    "missing_op": '{"metric": "bd_pending_count", "threshold": 1}',
    "null_op": '{"metric": "bd_pending_count", "op": null, "threshold": 1}',
    # r4: a non-finite float in a field nothing reads still breaks the SPA parse
    "inf_unused_field": _row('"note": 1e400'),
    "neg_inf_unused_field": _row('"note": -1e400'),
    "unknown_metric": '{"metric": "unknown_metric", "op": ">=", "threshold": 1}',
    "list_op": '{"metric": "bd_pending_count", "op": [">="], "threshold": 1}',
    "bad_op": '{"metric": "bd_pending_count", "op": "~", "threshold": 1}',
    "object_name": '{"metric": "bd_pending_count", "op": ">=", "threshold": 1, "name": {"x": 1}}',
    "not_json": "idx_sh_site_ts",
    "array": "[1, 2]",
}


@pytest.mark.parametrize("raw", list(_SEMANTIC_BAD.values()), ids=list(_SEMANTIC_BAD))
def test_semantic_bad_row_is_skipped_and_reported(clean_workdir, capsys, raw):
    _seed([("f039_bad", raw)])
    capsys.readouterr()
    ids = _ids()
    assert "f039_good" in ids and _BUILTIN_IDS <= set(ids), ids
    assert "f039_bad" not in ids, f"F039: malformed row exposed to /alerts: {raw}"
    err = capsys.readouterr().err
    assert "skipping malformed alert_rules row 'f039_bad'" in err, (
        f"F039: malformed row skipped silently: {raw}"
    )


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_non_finite_threshold_lists_as_null_and_evaluates_as_bad(
    clean_workdir, capsys, monkeypatch, token
):
    # main c5884f8a0 (na-fix F4) owns this row: a stored NaN / Infinity
    # threshold is listed (threshold null), not skipped, and evaluate() keeps
    # its "bad threshold" error (tests/test_na_fix_notifications_alerts.py)
    from bulk_downloader import alerts_engine as ae

    _seed([("f039_nan", _row('"severity": "warn"').replace('"threshold": 1', '"threshold": ' + token))])
    capsys.readouterr()
    rules = {r["id"]: r for r in ae.list_rules()}
    assert "f039_good" in rules and "f039_nan" in rules, sorted(rules)
    assert rules["f039_nan"]["threshold"] is None, rules["f039_nan"]
    assert "skipping malformed" not in capsys.readouterr().err
    monkeypatch.setattr(ae, "_evaluate_metric", lambda *a, **k: 6)
    monkeypatch.setattr(ae, "_condition_was_held", lambda *a, **k: False)
    monkeypatch.setattr(ae, "_record_event", lambda *a, **k: None)
    results = {r["rule_id"]: r for r in ae.evaluate()["results"]}
    assert "bad threshold" in results["f039_nan"]["error"], results["f039_nan"]
    assert results["f039_nan"]["fired"] is False


def test_bad_builtin_override_keeps_the_builtin_default(clean_workdir, capsys):
    from bulk_downloader import alerts_engine as ae

    _seed([("queue_backlog", '{"threshold": {"bad": 1}}')])
    capsys.readouterr()
    rules = {r["id"]: r for r in ae.list_rules()}
    default = {r["id"]: r for r in ae.DEFAULT_RULES}["queue_backlog"]
    assert rules["queue_backlog"]["threshold"] == default["threshold"]
    assert (
        "skipping malformed alert_rules row 'queue_backlog'" in capsys.readouterr().err
    )


def test_numeric_string_threshold_still_lists(clean_workdir, capsys):
    # control: evaluate() float()s the threshold, so "5" stayed usable at BASE
    _seed(
        [("f039_str", '{"metric": "bd_pending_count", "op": ">=", "threshold": "5"}')]
    )
    capsys.readouterr()
    assert "f039_str" in _ids()
    assert "skipping malformed" not in capsys.readouterr().err


def test_valid_partial_builtin_override_still_applies(clean_workdir, capsys):
    # control: a partial override (threshold only) is valid once merged
    from bulk_downloader import alerts_engine as ae

    _seed([("queue_backlog", '{"threshold": 7}')])
    capsys.readouterr()
    assert {r["id"]: r for r in ae.list_rules()}["queue_backlog"]["threshold"] == 7
    assert "skipping malformed" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "raw",
    ["idx_sh_site_ts", "[1, 2]", "{}", _SEMANTIC_BAD["object_threshold"], _OVERFLOW],
    ids=["not_json", "array", "empty", "object_threshold", "overflow_threshold"],
)
def test_evaluator_runs_good_rules_and_reports_bad_rows(
    clean_workdir, monkeypatch, raw
):
    from bulk_downloader import alerts_engine as ae

    _seed([("f039_bad", raw)])
    monkeypatch.setattr(ae, "_evaluate_metric", lambda *a, **k: 6)
    monkeypatch.setattr(ae, "_condition_was_held", lambda *a, **k: False)
    monkeypatch.setattr(ae, "_record_event", lambda *a, **k: None)
    report = ae.evaluate()
    results = {item["rule_id"]: item for item in report["results"]}
    assert "f039_good" in results, (
        f"F039: evaluator dropped f039_good: {sorted(results)}"
    )
    assert "high_failure_rate" in results
    bad = results.get("f039_bad")
    assert bad is not None, f"F039: evaluator skipped f039_bad silently: {report}"
    assert bad["fired"] is False and bad["tripping"] is False
    assert bad["error"].startswith("malformed rule skipped: ")
    assert report["unknown"] >= 1


def test_api_lists_rules_beside_an_overflowing_threshold(clean_workdir):
    _seed([("f039_bad", _OVERFLOW)])
    from bulk_downloader import app as A

    resp = A.app.test_client().get("/api/alerts/rules")
    assert resp.status_code == 200, (
        f"F039: overflowing threshold broke GET /api/alerts/rules: {resp.status_code}"
    )
    ids = [r["id"] for r in resp.get_json()["rules"]]
    assert "f039_good" in ids and "f039_bad" not in ids, ids


def test_bad_threshold_report_keeps_the_evaluator_wording(clean_workdir, monkeypatch):
    # tests/test_hunt_custom_alert_evaluation.py pins "bad threshold" for a
    # junk threshold; the skip report keeps that wording
    from bulk_downloader import alerts_engine as ae

    _seed([("f039_bad", _SEMANTIC_BAD["string_threshold"])])
    monkeypatch.setattr(ae, "_evaluate_metric", lambda *a, **k: 6)
    monkeypatch.setattr(ae, "_condition_was_held", lambda *a, **k: False)
    monkeypatch.setattr(ae, "_record_event", lambda *a, **k: None)
    results = {r["rule_id"]: r for r in ae.evaluate()["results"]}
    assert "bad threshold" in results["f039_bad"]["error"], results["f039_bad"]


def test_api_body_stays_strict_json_beside_non_finite_rows(clean_workdir):
    import json

    _seed(
        [
            ("f039_nan", _SEMANTIC_BAD["nan_severity"]),
            ("f039_inf", _SEMANTIC_BAD["inf_duration"]),
        ]
    )
    from bulk_downloader import app as A

    resp = A.app.test_client().get("/api/alerts/rules")
    assert resp.status_code == 200

    def _refuse(name):
        raise AssertionError(
            f"F039: GET /api/alerts/rules emitted {name}; the SPA cannot parse it"
        )

    body = json.loads(resp.get_data(as_text=True), parse_constant=_refuse)
    assert "f039_good" in [r["id"] for r in body["rules"]]


@pytest.mark.parametrize("key", ["bad_duration", "bad_cooldown", "number_actions"])
def test_tripping_bad_row_does_not_abort_the_evaluator(clean_workdir, monkeypatch, key):
    from bulk_downloader import alerts_engine as ae

    _seed([("f039_bad", _SEMANTIC_BAD[key])])
    monkeypatch.setattr(ae, "_evaluate_metric", lambda *a, **k: 6)
    monkeypatch.setattr(ae, "_condition_was_held", lambda *a, **k: True)
    monkeypatch.setattr(ae, "_last_fire_age", lambda *a, **k: None)
    monkeypatch.setattr(ae, "_record_event", lambda *a, **k: None)
    report = ae.evaluate()
    results = {r["rule_id"]: r for r in report["results"]}
    assert "high_failure_rate" in results and "f039_good" in results
    assert results["f039_bad"]["fired"] is False
    assert results["f039_bad"]["error"].startswith("malformed rule skipped: ")


def _trip_everything(monkeypatch):
    """Every rule trips and is due to fire; returns the recorded events."""
    from bulk_downloader import alerts_engine as ae

    events = []
    monkeypatch.setattr(ae, "_evaluate_metric", lambda *a, **k: 3)
    monkeypatch.setattr(ae, "_condition_was_held", lambda *a, **k: True)
    monkeypatch.setattr(ae, "_last_fire_age", lambda *a, **k: None)
    monkeypatch.setattr(ae, "_fire_actions", lambda *a, **k: None)
    monkeypatch.setattr(
        ae, "_record_event", lambda rid, kind, *a, **k: events.append((rid, kind))
    )
    return events


@pytest.mark.parametrize("key", ["missing_op", "null_op"])
def test_tripping_row_without_op_cannot_abort_a_good_rule(
    clean_workdir, monkeypatch, key
):
    # r4 (REFUTE cx127 F1): the op-less row is stored BEFORE the good rule and
    # would trip (metric 3 >= threshold 1); the pass must reach the good rule
    from bulk_downloader import alerts_engine as ae
    from bulk_downloader.db import db_conn, db_init

    db_init()
    ae._ensure_tables()  # save_rule creates alert_rules; this row goes in first
    with db_conn() as cx:
        cx.execute(
            "INSERT INTO alert_rules(rule_id, rule_json) VALUES (?,?)",
            ("f039_a_noop", _SEMANTIC_BAD[key]),
        )
    assert ae.save_rule({**_GOOD, "threshold": 2}) == "f039_good"
    events = _trip_everything(monkeypatch)
    try:
        report = ae.evaluate()
    except KeyError as e:
        pytest.fail(f"F039: a custom row without op aborted the evaluator pass: KeyError {e}")
    results = {r["rule_id"]: r for r in report["results"]}
    assert results["f039_good"]["fired"] is True, results.get("f039_good")
    assert ("f039_good", "fired") in events, events
    bad = results["f039_a_noop"]
    assert bad["fired"] is False and bad["tripping"] is False
    assert bad["error"] == "malformed rule skipped: invalid op None", bad
    assert not [e for e in events if e[0] == "f039_a_noop"], events
    assert _BUILTIN_IDS <= set(results)


def test_builtin_override_without_op_still_trips_and_fires(clean_workdir, monkeypatch):
    # control: an override row carries no op of its own; the merged default
    # supplies it, so the row is neither skipped nor a KeyError on trip
    from bulk_downloader import alerts_engine as ae

    _seed([("queue_backlog", '{"threshold": 1}')])
    events = _trip_everything(monkeypatch)
    report = ae.evaluate()
    results = {r["rule_id"]: r for r in report["results"]}
    assert results["queue_backlog"]["threshold"] == 1
    assert results["queue_backlog"]["fired"] is True, results["queue_backlog"]
    assert "error" not in results["queue_backlog"]
    assert ("queue_backlog", "fired") in events
    assert report["unknown"] == 0, report


@pytest.mark.parametrize("threshold", ["nan", "inf", "-inf"])
def test_save_rule_refuses_a_non_finite_threshold(clean_workdir, threshold):
    from bulk_downloader import alerts_engine as ae
    from bulk_downloader.db import db_init

    db_init()
    rule = {
        "id": "f039_nf",
        "metric": "bd_pending_count",
        "op": ">=",
        "threshold": threshold,
    }
    assert ae.save_rule(rule) is None, f"F039: save_rule stored threshold {threshold}"
