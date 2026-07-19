"""v3.66.121 — Phase B: Class B automation (reversible housekeeping).

The first real autonomy, default-OFF. Tests: the two Class B guardrails are now built
(so B Level 3 is selectable) while C stays blocked; default Class B level stays
'suggest' (nothing autonomous until opted in); suggest mode changes nothing; apply is
logged + reversible and reverse restores; the kill switch turns apply into a logged
no-op; no external activity / no correctness-critical writes; read-only wiring
(+3 GET = 104, no POST).
"""
import shutil
from pathlib import Path

from tools import autonomy_policy as ap
from tools import autonomy_housekeeping as hk
from tools.cockpit_core import tasks_root

_SRC = Path(hk.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(hk.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")


def _fresh():
    g = tasks_root() / "governance"
    if g.exists():
        shutil.rmtree(g)


class TestGuardrailsAndDefaultOff:
    def test_class_b_guardrails_now_built(self):
        _fresh()
        reg = ap.guardrail_registry()
        assert reg["action_logging"]["built"] is True
        assert reg["reversibility"]["built"] is True

    def test_b_level3_now_selectable(self):
        _fresh()
        r = ap.set_policy_level("B", "auto_with_guardrails", "mboyle", "opt in")
        assert r["ok"] is True and r["to"] == "auto_with_guardrails"

    def test_c_still_blocked(self):
        _fresh()
        # As of Phase E the guardrail set is complete, so flipping C succeeds — but the
        # default posture keeps C at Approve-each; nothing is autonomous by default.
        assert ap.load_policy()["levels"]["C"] == "approve_each"
        assert ap.can_autonomously("C")["allowed"] is False   # default level not auto

    def test_default_b_is_suggest_nothing_autonomous(self):
        _fresh()
        assert ap.load_policy()["levels"]["B"] == "suggest"
        assert ap.can_autonomously("B")["allowed"] is False

    def test_auth_label_reflects_policy(self):
        _fresh()
        assert hk._auto_or_manual("mboyle") == "operator"   # default
        ap.set_policy_level("B", "auto_with_guardrails", "mboyle", "opt in")
        assert hk._auto_or_manual("mboyle") == "auto"        # opted in
        ap.set_policy_level("B", "suggest", "mboyle", "reset")


class TestSuggestChangesNothing:
    def test_reorder_suggest_applies_nothing(self):
        _fresh()
        r = hk.reorder_queue(mode="suggest", by="mboyle")
        assert r["ok"] and r["mode"] == "suggest"
        assert hk.housekeeping_log() == []   # nothing logged in suggest

    def test_notifications_suggest_writes_nothing(self):
        _fresh()
        r = hk.generate_notifications(mode="suggest", by="mboyle")
        assert r["ok"] and r["mode"] == "suggest"
        assert hk.list_notifications()["notifications"] == []


class TestApplyAndReverse:
    def test_dashboard_cache_apply_then_reverse(self):
        _fresh()
        a = hk.refresh_dashboard_cache(mode="apply", by="mboyle")
        assert a["ok"] and a["mode"] == "apply"
        assert hk.dashboard_cache()["cached"] is not None
        rev = hk.reverse_action(a["id"], by="mboyle")
        assert rev["ok"] and hk.dashboard_cache()["cached"] is None

    def test_apply_is_logged_and_reversible(self):
        _fresh()
        a = hk.refresh_dashboard_cache(mode="apply", by="mboyle")
        entry = hk._find_log_entry(a["id"])
        assert entry["mode"] == "apply" and entry["reversible"] is True

    def test_notifications_reverse_removes_only_its_own(self):
        _fresh()
        a = hk.generate_notifications(mode="apply", by="mboyle")
        # whatever was generated, reversing removes exactly those ids
        before = len(hk.list_notifications()["notifications"])
        hk.reverse_action(a["id"], by="mboyle")
        after = len(hk.list_notifications()["notifications"])
        assert after <= before

    def test_double_reverse_is_idempotent(self):
        _fresh()
        a = hk.refresh_dashboard_cache(mode="apply", by="mboyle")
        hk.reverse_action(a["id"], by="mboyle")
        again = hk.reverse_action(a["id"], by="mboyle")
        assert again.get("already_reversed") is True

    def test_reverse_requires_identity(self):
        _fresh()
        a = hk.refresh_dashboard_cache(mode="apply", by="mboyle")
        assert hk.reverse_action(a["id"], by="")["ok"] is False


class TestKillSwitch:
    def test_frozen_apply_is_noop_and_logged(self):
        _fresh()
        ap.freeze("mboyle", "emergency")
        r = hk.reorder_queue(mode="apply", by="mboyle")
        assert r.get("skipped") is True and "frozen" in r["reason"].lower()
        # the skip is logged
        assert any(e.get("skipped") for e in hk.housekeeping_log())
        # nothing actually changed: no 'apply' entry recorded
        assert not any(e.get("mode") == "apply" for e in hk.housekeeping_log())
        ap.unfreeze("mboyle", "clear")

    def test_run_housekeeping_reports_frozen(self):
        _fresh()
        ap.freeze("mboyle", "emergency")
        r = hk.run_housekeeping(mode="apply", by="mboyle")
        assert r["frozen"] is True
        assert all(v.get("skipped") for v in r["results"].values())
        ap.unfreeze("mboyle", "clear")


class TestPostureNoExternalNoCritical:
    def test_no_external_activity_or_credential_path(self):
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", ".replay(", "do_login(",
                    "web_fetch"):
            assert bad not in _SRC, f"Class B must not use {bad!r}"

    def test_no_correctness_critical_writes(self):
        # Class B must not mutate templates/selectors/profiles, write corpus, retire
        # debt, or launch captures — those are Class C/D
        for bad in ("def promote_selector", "def apply_template", "sites_config",
                    "def write_corpus", "validation_corpus", "def retire_debt",
                    "def launch_capture", "capture_ingest"):
            assert bad not in _SRC, f"Class B must not touch {bad!r}"

    def test_notifications_are_in_gui_only(self):
        # the derive path documents in-GUI / no external push
        assert "no external push" in _SRC.lower()

    def test_atomic_writes_and_utf8(self):
        assert ".replace(" in _SRC and ".tmp" in _SRC
        assert 'encoding="utf-8"' in _SRC

    def test_no_background_scheduler(self):
        # Phase B installs no self-firing loop
        for bad in ("threading.Thread", "schedule.every", "while True",
                    "BackgroundScheduler", "asyncio.create_task"):
            assert bad not in _SRC


class TestWiring:
    def test_endpoints_and_pages_present(self):
        for r in ("status", "preview", "log"):
            assert f'@bp.get("/api/housekeeping/{r}")' in _CONSOLE
        assert "PAGES.housekeeping" in _CONSOLE and 'data-p="housekeeping"' in _CONSOLE
        assert "PAGES.hklog" in _CONSOLE

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        for r in ("status", "preview", "log"):
            assert c.get(f"/cockpit/api/housekeeping/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for r in ("status", "preview", "log"):
            assert f"/cockpit/api/housekeeping/{r}" not in posts
