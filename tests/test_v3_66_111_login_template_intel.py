"""v3.66.111 — Phase 2 Login Template Intelligence.

The credential-safety boundaries are the load-bearing tests: no credential values
read or echoed, no login submitted, dry-run is recognition-only, suggestions are
data-only. Plus behaviour: health/drift/history shape, MFA/captcha detection, the
honest 'needs_dry_run' marking, and wiring.
"""
import json
from pathlib import Path

from tools import cockpit_templates as ct

_SRC = Path(ct.__file__).read_text(encoding="utf-8")


class TestCredentialSafety:
    def test_dry_run_never_submits(self):
        assert ct.login_dry_run()["would_submit"] is False
        assert ct.login_dry_run("<form><input type=password></form>")["would_submit"] is False

    def test_dry_run_does_not_echo_credential_values(self):
        # a prefilled credential value must never appear in the output
        html = "<form><input type='text' name='username' value='alice@example.com'>" \
               "<input type='password' name='password' value='HUNTER2'>" \
               "<button type='submit'>Login</button></form>"
        blob = json.dumps(ct.login_dry_run(html))
        assert "HUNTER2" not in blob
        assert "alice@example.com" not in blob

    def test_no_login_submission_constructs_in_module(self):
        # the module must not call do_login / fill / submit anywhere
        for bad in ("do_login(", ".fill(", ".click(", "submit_form", "page.goto",
                    "_try_fill", "_try_click", "requests.", "playwright"):
            assert bad not in _SRC, f"login intel must not use {bad!r}"

    def test_suggestions_are_data_only(self):
        s = ct.suggested_login_template_update("anysite")
        assert s["applies_automatically"] is False
        for bad in ("def apply", "def promote", "auto_apply", "save_config",
                    "write_text", "json.dump("):
            assert bad not in _SRC

    def test_review_queue_is_read_only(self):
        rq = ct.login_review_queue()
        assert "queue" in rq and "count" in rq
        # the approve/reject (mutation) is explicitly deferred to phase 4
        assert "_phase4" in rq


class TestLoginHealth:
    def test_health_shape(self):
        h = ct.login_template_health()
        assert "sites" in h and "site_count" in h and "config_present" in h
        assert h["site_count"] == len(h["sites"])

    def test_health_fields_present(self):
        import os
        os.environ["BD_SITES_CONFIG_PATH"] = "sites_config.example.json"
        try:
            h = ct.login_template_health()
        finally:
            os.environ.pop("BD_SITES_CONFIG_PATH", None)
        for s in h["sites"]:
            for k in ("site", "template_present", "selector_counts",
                      "selector_confidence", "session", "mfa_captcha_indicated"):
                assert k in s

    def test_mfa_captcha_detection(self):
        # the known invisible-captcha markers drive detection
        for m in ("cf-turnstile-response", "h-captcha-response", "g-recaptcha-response"):
            assert m in ct._CAPTCHA_MARKERS
        d = ct.login_dry_run("<form><div class='cf-turnstile'></div>"
                             "<input type=password></form>")
        assert d["captcha_detected"] is True


class TestLoginDrift:
    def test_field_changes_marked_needs_dry_run_not_guessed(self):
        # field/form/marker changes can't be inferred from state — must be honest
        dr = ct.login_drift_report()
        for s in dr["sites"]:
            for k in ("user_field_changed", "pass_field_changed", "submit_changed",
                      "form_moved", "success_marker_changed"):
                assert s["signals"][k] == "needs_dry_run"

    def test_history_is_outcomes_only(self):
        h = ct.login_history()
        assert "sites" in h and "history_available" in h
        for s in h["sites"]:
            for e in s["events"]:
                assert set(e.keys()) <= {"ts", "cookie_score", "action", "outcome"}


class TestDryRunRecognition:
    def test_identifies_fields_in_sample(self):
        d = ct.login_dry_run()
        f = d["fields_identified"]
        assert f["username_field"] and f["password_field"] and f["submit_button"]
        assert d["confidence"] == 1.0

    def test_partial_form_lower_confidence(self):
        d = ct.login_dry_run("<form><input type='password'></form>")
        assert d["confidence"] < 1.0


class TestWiring:
    def test_endpoints_and_pages_present(self):
        console = Path((Path(ct.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")
        for ep in ("login-health", "login-drift", "login-review"):
            assert f'@bp.get("/api/template/{ep}")' in console
        for pg in ("logintemplates", "logindrift", "loginreview"):
            assert f"PAGES.{pg}" in console and (f'data-p="{pg}"' in console or f"{pg}:[" in console)

    def test_route_count_and_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        for ep in ("login-health", "login-drift", "login-review"):
            assert c.get(f"/cockpit/api/template/{ep}").status_code == 200

    def test_login_intel_added_no_posts(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for ep in ("login-health", "login-drift", "login-review"):
            assert f"/cockpit/api/template/{ep}" not in posts
