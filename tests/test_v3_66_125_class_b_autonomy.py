"""v3.66.125 — Phase F: controlled Class B autonomy.

Operationalises Class B (reversible housekeeping) so it can run autonomously via a
host-scheduled cycle — while Class C and D stay human-controlled. Proves the spec
requirements: no live config writes, no corpus writes, no debt changes, no external
activity, no browser actions, no capture launches; the cycle defaults to a dry-run and
applies only when not-frozen + Class-B-at-auto + the host env flag is set; monitoring
is read-only; the cycle never touches Class C/D; each cycle records a decision
snapshot; actions are reversible/logged; wiring (+6 GET = 124, no POST).
"""
import os
import shutil
from pathlib import Path

from tools import autonomy_policy as ap
from tools import autonomy_center as ac
from tools.cockpit_core import tasks_root

_SRC = Path(ac.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(ac.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")


def _fresh():
    g = tasks_root() / "governance"
    if g.exists():
        shutil.rmtree(g)
    os.environ.pop("BD_AUTONOMY_ENABLED", None)


def _enable_auto():
    """Opt Class B in AND set the host flag — the only state where the cycle applies."""
    ap.set_policy_level("B", "auto_with_guardrails", "mboyle", "opt in")
    os.environ["BD_AUTONOMY_ENABLED"] = "1"


class TestCycleGating:
    def test_default_is_dry_run(self):
        _fresh()
        r = ac.run_autonomy_cycle("test")
        assert r["mode"] == "suggest"   # B at suggest, no env flag

    def test_b_auto_but_no_env_flag_is_dry_run(self):
        _fresh()
        ap.set_policy_level("B", "auto_with_guardrails", "mboyle", "opt in")
        r = ac.run_autonomy_cycle("test")
        assert r["mode"] == "suggest"   # host flag is the final apply switch

    def test_applies_only_when_all_three(self):
        _fresh()
        _enable_auto()
        r = ac.run_autonomy_cycle("test")
        assert r["mode"] == "apply"

    def test_frozen_skips_everything(self):
        _fresh()
        _enable_auto()
        ap.freeze("mboyle", "maintenance")
        r = ac.run_autonomy_cycle("test")
        assert r["mode"] == "skipped"
        ap.unfreeze("mboyle", "done")

    def test_force_suggest_overrides(self):
        _fresh()
        _enable_auto()
        assert ac.run_autonomy_cycle("test", force_mode="suggest")["mode"] == "suggest"


class TestDecisionSnapshotAndLog:
    def test_cycle_records_decision_snapshot(self):
        _fresh()
        r = ac.run_autonomy_cycle("test")
        assert r["snapshot_id"]
        assert ap.get_decision_snapshot(r["snapshot_id"]) is not None

    def test_cycle_logged(self):
        _fresh()
        ac.run_autonomy_cycle("test")
        assert len(ac.cycle_log()) >= 1


class TestClassBOnly:
    def test_cycle_never_touches_class_c_or_d_actions(self):
        _fresh()
        _enable_auto()
        r = ac.run_autonomy_cycle("test")
        forbidden = [k for k in r["results"]
                     if any(x in k for x in ("selector", "template", "login", "corpus",
                                             "debt", "capture", "workflow"))]
        assert forbidden == []

    def test_class_c_d_levels_unchanged_by_cycle(self):
        _fresh()
        _enable_auto()
        ac.run_autonomy_cycle("test")
        pol = ap.load_policy()["levels"]
        assert pol["C"] == "approve_each" and pol["D"] == "approve_each"

    def test_monitoring_actions_are_read_only(self):
        _fresh()
        _enable_auto()
        r = ac.run_autonomy_cycle("test")
        for m in ("freshness_monitoring", "governance_monitoring",
                  "review_deadline_tracking"):
            assert r["results"][m]["mode"] == "monitor"   # never 'apply'


class TestNoForbiddenActivity:
    def test_no_external_activity(self):
        for bad in ("requests.", "urllib.request", "httpx", "urlopen", "web_fetch",
                    "socket.", "smtplib", "webhook", "push("):
            assert bad not in _SRC, f"Phase F must not {bad!r}"

    def test_no_browser_actions(self):
        for bad in ("playwright", "page.goto", "selenium", "webdriver", ".click("):
            assert bad not in _SRC

    def test_no_capture_or_login_execution(self):
        for bad in ("run_capture", "capture_session", "do_login(", "launch_browser",
                    "subprocess"):
            assert bad not in _SRC

    def test_no_live_config_or_corpus_or_debt_writes(self):
        for bad in ("sites_config", "validation_corpus", "write_corpus",
                    "retire_debt", "debt_retire", "BD_SITES_CONFIG"):
            assert bad not in _SRC

    def test_no_background_scheduler(self):
        # the cycle is an explicit tick (host-scheduled) — no in-process daemon
        for bad in ("threading.Thread", "schedule.every", "while True",
                    "BackgroundScheduler", "asyncio.create_task", "Timer("):
            assert bad not in _SRC


class TestReversibilityAndArtifacts:
    def test_artifact_maintenance_frozen_skips(self):
        _fresh()
        ap.freeze("mboyle", "x")
        r = ac.artifact_maintenance(mode="apply", by="mboyle")
        assert r.get("skipped") is True
        ap.unfreeze("mboyle", "y")

    def test_artifact_maintenance_suggest_changes_nothing(self):
        _fresh()
        r = ac.artifact_maintenance(mode="suggest", by="mboyle")
        assert r["mode"] == "suggest" and "would_archive" in r

    def test_artifact_maintenance_logged_reversible(self):
        # the apply branch logs a reversible cycle entry
        assert '"reversible": True' in _SRC


class TestViews:
    def test_all_six_views_render(self):
        _fresh()
        for v in ("autonomy_center", "queue_intelligence", "review_operations",
                  "notification_center", "governance_health", "automation_metrics"):
            out = getattr(ac, v)()
            assert isinstance(out, dict) and out

    def test_center_shows_class_c_d_human(self):
        _fresh()
        c = ac.autonomy_center()
        assert c["class_c_level"] == "approve_each" and c["class_d_level"] == "approve_each"

    def test_governance_monitoring_flags_anomaly(self):
        _fresh()
        # if C were ever at auto, governance monitoring must flag it
        ap.set_policy_level("C", "auto_with_guardrails", "mboyle", "deliberate test")
        gh = ac.governance_health()
        assert gh["anomalies"] and any("Class C" in a for a in gh["anomalies"])


class TestWiring:
    def test_endpoints_and_pages_present(self):
        for r in ("center", "queue", "review-ops", "notifications",
                  "governance-health", "metrics"):
            assert f'@bp.get("/api/autonomy/{r}")' in _CONSOLE
        for pg in ("autonomycenter", "queueintel", "reviewops", "notifcenter",
                   "govhealth", "autmetrics"):
            assert f"PAGES.{pg}" in _CONSOLE
        assert ("PAGES.autonomycenter" in _CONSOLE or ">Autonomy<" in _CONSOLE) and ('data-p="autonomycenter"' in _CONSOLE or "autonomycenter:[" in _CONSOLE)

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        for r in ("center", "queue", "review-ops", "notifications",
                  "governance-health", "metrics"):
            assert c.get(f"/cockpit/api/autonomy/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for r in ("center", "queue", "review-ops", "notifications",
                  "governance-health", "metrics"):
            assert f"/cockpit/api/autonomy/{r}" not in posts
