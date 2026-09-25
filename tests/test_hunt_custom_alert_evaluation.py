"""Saved custom alert rules are included in the periodic evaluation pass."""
from bulk_downloader import alerts_engine

BD_GATE_SCOPE = "module"


def test_saved_custom_rule_is_evaluated_by_default(clean_workdir, monkeypatch):
    rule_id = "hunt_custom_pending"
    assert alerts_engine.save_rule({
        "id": rule_id,
        "metric": "bd_pending_count",
        "op": ">=",
        "threshold": 5,
        "duration_minutes": 0,
        "actions": [],
    }) == rule_id
    monkeypatch.setattr(alerts_engine, "_evaluate_metric", lambda *a, **k: 6)
    monkeypatch.setattr(alerts_engine, "_condition_was_held", lambda *a, **k: False)
    monkeypatch.setattr(alerts_engine, "_record_event", lambda *a, **k: None)
    report = alerts_engine.evaluate()
    results = {item["rule_id"]: item for item in report["results"]}
    assert "high_failure_rate" in results  # built-in positive control
    assert rule_id in results
    assert results[rule_id]["tripping"] is True


def _quiet(monkeypatch):
    monkeypatch.setattr(alerts_engine, "_evaluate_metric", lambda *a, **k: 6)
    monkeypatch.setattr(alerts_engine, "_condition_was_held", lambda *a, **k: False)
    monkeypatch.setattr(alerts_engine, "_record_event", lambda *a, **k: None)


def test_legacy_string_threshold_rule_does_not_stop_the_pass(clean_workdir, monkeypatch):
    """Lens: a stored rule whose threshold is a string (save_rule accepted
    "5" before it normalised) must not raise out of evaluate() and silence
    every built-in alert; it evaluates numerically, a junk one reports an error."""
    import json
    from bulk_downloader import db
    alerts_engine._ensure_tables()
    with db.db_conn() as cx:
        for rid, thr in (("legacy_str", "5"), ("legacy_junk", "five")):
            cx.execute("INSERT INTO alert_rules(rule_id, rule_json, enabled) VALUES (?,?,1)",
                       (rid, json.dumps({"id": rid, "metric": "bd_pending_count", "op": ">=",
                                         "threshold": thr, "duration_minutes": 0, "actions": []})))
    _quiet(monkeypatch)
    results = {r["rule_id"]: r for r in alerts_engine.evaluate()["results"]}
    assert "high_failure_rate" in results
    assert results["legacy_str"]["tripping"] is True
    assert "bad threshold" in results["legacy_junk"]["error"]
    assert alerts_engine.save_rule({"id": "new_str", "metric": "bd_pending_count", "op": ">=",
                                    "threshold": "7"}) == "new_str"
    assert {r["id"]: r for r in alerts_engine.list_rules()}["new_str"]["threshold"] == 7.0


def test_unreadable_rule_store_is_unknown_not_silence(clean_workdir, monkeypatch):
    import sqlite3
    from bulk_downloader import db
    _quiet(monkeypatch)

    def _boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(db, "db_conn", _boom)
    report = alerts_engine.evaluate()
    results = {r["rule_id"]: r for r in report["results"]}
    assert "high_failure_rate" in results  # built-ins still evaluated
    assert report["unknown"] >= 1
    assert "unavailable" in results["custom_rules"]["error"]
