"""Cut 8 write surface: job-lifecycle alert rule type.

Adds a rule WRITE API to alerts_engine (save_rule / delete_rule over the
existing alert_rules table) plus a job-lifecycle metric
(`bd_job_failures_1h`, counted from job_runs) that rides the existing
polled evaluate() path -- no new event-bus subscription. New POST routes:
    POST /api/alerts/rules                 -> save (CSRF, validated)
    POST /api/alerts/rules/<rule_id>/remove -> delete (CSRF)
list_rules is extended to surface custom-only rule ids (today it only
merges overrides onto DEFAULT_RULES).

RED on pristine 379: no save_rule/delete_rule, the new metric is unknown,
custom-only rules don't surface, and the POST routes are absent.
"""
from __future__ import annotations


def _new_client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, csrf


# ── engine: rule write API ────────────────────────────────────────────

def test_save_rule_validates_metric():
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import alerts_engine as ae
    # unknown metric rejected
    assert ae.save_rule({"id": "bad", "metric": "not_a_metric",
                         "op": ">=", "threshold": 1}) is None
    # missing id rejected
    assert ae.save_rule({"metric": "bd_job_failures_1h",
                         "op": ">=", "threshold": 1}) is None


def test_save_rule_accepts_job_lifecycle_metric():
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import alerts_engine as ae
    rid = ae.save_rule({"id": "jobfail", "metric": "bd_job_failures_1h",
                        "op": ">=", "threshold": 3, "name": "Job failures"})
    assert rid == "jobfail"
    ids = [r["id"] for r in ae.list_rules()]
    assert "jobfail" in ids  # custom-only rule now surfaces


def test_save_rule_is_idempotent_upsert():
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import alerts_engine as ae
    ae.save_rule({"id": "u1", "metric": "bd_job_failures_1h",
                 "op": ">=", "threshold": 3})
    ae.save_rule({"id": "u1", "metric": "bd_job_failures_1h",
                 "op": ">=", "threshold": 9})
    matches = [r for r in ae.list_rules() if r["id"] == "u1"]
    assert len(matches) == 1
    assert matches[0]["threshold"] == 9  # upsert overwrote


def test_delete_rule():
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import alerts_engine as ae
    ae.save_rule({"id": "del1", "metric": "bd_job_failures_1h",
                 "op": ">=", "threshold": 1})
    assert ae.delete_rule("del1") is True
    assert "del1" not in [r["id"] for r in ae.list_rules()]
    assert ae.delete_rule("del1") is False


def test_job_failures_metric_evaluable():
    """The new metric resolves to a number (>=0), not None."""
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import alerts_engine as ae
    val = ae._evaluate_metric("bd_job_failures_1h")
    assert val is not None
    assert val >= 0


# ── routes ────────────────────────────────────────────────────────────

def test_post_rule_requires_csrf():
    c, _csrf = _new_client()
    r = c.post("/api/alerts/rules",
               json={"id": "x", "metric": "bd_job_failures_1h",
                     "op": ">=", "threshold": 2})
    assert r.status_code == 403


def test_post_rule_saves():
    c, csrf = _new_client()
    r = c.post("/api/alerts/rules",
               json={"id": "viaapi", "metric": "bd_job_failures_1h",
                     "op": ">=", "threshold": 2, "name": "API rule"},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ok"] is True
    ids = [x["id"] for x in c.get("/api/alerts/rules").get_json()["rules"]]
    assert "viaapi" in ids


def test_post_rule_bad_metric_is_400():
    c, csrf = _new_client()
    r = c.post("/api/alerts/rules",
               json={"id": "bad", "metric": "nope", "op": ">=", "threshold": 1},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_post_rule_remove():
    c, csrf = _new_client()
    H = {"X-CSRF-Token": csrf}
    c.post("/api/alerts/rules",
           json={"id": "rmrule", "metric": "bd_job_failures_1h",
                 "op": ">=", "threshold": 2}, headers=H)
    r = c.post("/api/alerts/rules/rmrule/remove", headers=H)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ok"] is True
