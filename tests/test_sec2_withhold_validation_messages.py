"""sec2-withhold-messages r1 (F4 fix-forward of BD-SEC-2 withhold r3-restack-d).

The SEC-2 after_request hook (app._withhold_diagnostic_text) served every
"error" string through withhold_text, so the app's own fixed refusal texts
read "url must be [url withheld]" / "need an id without [url withheld]".
A fixed refusal text the app authors is served verbatim; any other text in a
diagnostic field -- a 4xx or 5xx that quotes an exception, a 2xx status that
quotes a stored target -- is still withheld.
"""

from __future__ import annotations

import json

BD_GATE_SCOPE = "module"

_SECRET_URL = "https://u:pw@hooks.slack.com/services/T0/B0/SEC2WMSECRET"


def _client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init

    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, {"X-CSRF-Token": csrf}


def _served(status, body):
    """The body a client reads after the SEC-2 hook, for a view that answered
    ``body`` with ``status`` and recorded no app message."""
    from bulk_downloader import app as A

    with A.app.test_request_context("/api/sec2-wm-probe"):
        resp = A.app.response_class(json.dumps(body), status=status,
                                    mimetype="application/json")
        return json.loads(A._withhold_diagnostic_text(resp).get_data())


def test_alert_rule_refusal_text_is_served_verbatim():
    c, h = _client()
    r = c.post("/api/alerts/rules", headers=h,
               json={"id": "wm/x", "metric": "bd_job_failures_1h", "op": ">=", "threshold": 2})
    err = (r.get_json() or {}).get("error", "")
    assert r.status_code == 400, ("SEC2-WM-ALERT-STATUS", r.status_code, r.get_json())
    assert "need an id without '/'" in err and "op in >=/<=/>/</==" in err, \
        ("SEC2-WM-ALERT-TEXT-WITHHELD", err)


def test_template_sandbox_scheme_refusal_is_served_verbatim():
    c, h = _client()
    r = c.post("/api/template/sandbox", headers=h, json={"url": "file:///etc/passwd", "template": {}})
    assert r.status_code == 400, ("SEC2-WM-SANDBOX-STATUS", r.status_code, r.get_json())
    assert r.get_json()["error"] == "url must be http(s)://", \
        ("SEC2-WM-SANDBOX-TEXT-WITHHELD", r.get_json())


def test_negative_control_4xx_exception_text_is_still_withheld():
    # A 400 that quotes an exception (str(e)) carries no recorded app message:
    # its URL stays withheld, so the fix is not "skip the hook on 4xx".
    shown = _served(400, {"ok": False, "error": f"connect failed: {_SECRET_URL}"})
    assert "SEC2WMSECRET" not in json.dumps(shown), ("SEC2-WM-4XX-LEAK", shown)
    assert shown["error"] == "connect failed: [url withheld]", ("SEC2-WM-4XX-SHAPE", shown)


def test_negative_control_stored_diagnostic_text_is_still_withheld():
    # The SEC-2 target: a stored last_error / 5xx text quoting the target's path.
    for status, body in ((200, {"webhooks": [{"last_error": f"POST /api/webhooks/1/SEC2WMSECRET -> 404"}]}),
                         (500, {"ok": False, "error": f"boom {_SECRET_URL}"})):
        shown = _served(status, body)
        assert "SEC2WMSECRET" not in json.dumps(shown), ("SEC2-WM-STORED-LEAK", status, shown)


def test_secrets_audit_sink_choice_is_served_and_a_real_secret_is_not():
    from bulk_downloader.egress_withhold import CONFIGURED, site_view

    shown = site_view({"secrets_audit_sink": "file", "client_secret": "SEC2WMSECRET",
                       "webhook_secret": "SEC2WMSECRET"})
    assert shown["secrets_audit_sink"] == "file", ("SEC2-WM-SINK-MASKED", shown)
    # negative control: the exemption is the one name, not the "secret" floor
    assert shown["client_secret"] == CONFIGURED and shown["webhook_secret"] == CONFIGURED, \
        ("SEC2-WM-SECRET-SERVED", shown)
