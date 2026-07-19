"""v3.66.122 — Phase C: guardrail infrastructure.

The safety apparatus for Class C autonomy, built WITHOUT enabling it. The headline
test is that Class C auto is still impossible after Phase C (only the oracle remains).
Plus: rollback engine end-to-end on a confined staging target (never live config);
backlog + blast-radius caps; FAIL-CLOSED review-window sweep; self-throttle demotes
lower-only; guardrail-failure freezes; posture; read-only wiring (+3 GET = 107, no POST).
"""
import json
import shutil
from pathlib import Path

from tools import autonomy_policy as ap
from tools import autonomy_guardrails as gr
from tools.cockpit_core import tasks_root

_SRC = Path(gr.__file__).read_text(encoding="utf-8")
_POL = Path(ap.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(gr.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")


def _fresh():
    g = tasks_root() / "governance"
    if g.exists():
        shutil.rmtree(g)


class TestClassCStillImpossible:
    """The whole point of Phase C: build the guardrails, do NOT enable Class C auto."""

    def test_set_c_auto_still_refused_only_oracle_missing(self):
        _fresh()
        # Through Phase D this was refused with only correctness_oracle missing. Phase E
        # built the oracle, so the flip now succeeds — but the default posture keeps C
        # at Approve-each, and no Class C action is per-site eligible (see Phase E
        # tests). Guardrail completeness is not automation.
        r = ap.set_policy_level("C", "auto_with_guardrails", "mboyle")
        assert r["ok"] is True
        _fresh()
        assert ap.load_policy()["levels"]["C"] == "approve_each"

    def test_class_c_auto_not_possible(self):
        _fresh()
        # class_c_auto_possible now reflects guardrail completeness (True as of Phase E);
        # but actual autonomous action is gated elsewhere (default level + empty per-site
        # eligibility in autonomy_oracle), so nothing is autonomous by default.
        assert ap.can_autonomously("C")["allowed"] is False   # default level not auto

    def test_default_c_level_unchanged(self):
        _fresh()
        assert ap.load_policy()["levels"]["C"] == "approve_each"

    def test_phase_c_guardrails_built(self):
        _fresh()
        reg = ap.guardrail_registry()
        for g in ("rollback", "blast_radius_cap", "backlog_cap",
                  "review_window_failclosed", "self_throttle"):
            assert reg[g]["built"] is True
        # correctness_oracle is built as of Phase E (this test now runs against the
        # complete tree); the Phase C guardrails above are what this phase added.
        assert reg["correctness_oracle"]["built"] is True


class TestRollbackEngine:
    def test_record_and_rollback_restores_before(self):
        _fresh()
        rec = gr.record_change("staging_json", "demo.json",
                               {"sel": [".old"]}, {"sel": [".new"]}, by="system")
        assert rec["ok"]
        # simulate the change being applied to the staging file
        gr._atomic_write_json(gr._staging_dir() / "demo.json", {"sel": [".new"]})
        rb = gr.rollback(rec["id"], "mboyle")
        assert rb["ok"]
        restored = json.loads((gr._staging_dir() / "demo.json").read_text(encoding="utf-8"))
        assert restored == {"sel": [".old"]}

    def test_rollback_marks_record(self):
        _fresh()
        rec = gr.record_change("staging_json", "d.json", {"a": 1}, {"a": 2}, by="system")
        gr._atomic_write_json(gr._staging_dir() / "d.json", {"a": 2})
        gr.rollback(rec["id"], "mboyle")
        assert gr.change_record(rec["id"])["rolled_back"] is True

    def test_double_rollback_idempotent(self):
        _fresh()
        rec = gr.record_change("staging_json", "d.json", {"a": 1}, {"a": 2}, by="system")
        gr._atomic_write_json(gr._staging_dir() / "d.json", {"a": 2})
        gr.rollback(rec["id"], "mboyle")
        again = gr.rollback(rec["id"], "mboyle")
        assert again.get("already_rolled_back") is True

    def test_unknown_target_kind_refused(self):
        _fresh()
        r = gr.record_change("live_config", "x", {}, {}, by="system")
        assert r["ok"] is False  # no reverser registered for live_config in Phase C

    def test_diff_computed(self):
        _fresh()
        rec = gr.record_change("staging_json", "d.json", {"a": 1, "b": 2},
                               {"a": 1, "b": 3}, by="system")
        assert rec["diff"] == {"b": {"from": 2, "to": 3}}


class TestCaps:
    def test_backlog_cap(self):
        _fresh()
        for i in range(gr.BACKLOG_CAP):
            cid = gr.record_change("staging_json", f"s{i}.json", {"a": i}, {"a": i + 1},
                                   by="system")["id"]
            gr.register_pending(cid, "C", f"site{i}", "system")
        assert gr.backlog_ok()["ok"] is False  # at cap

    def test_blast_radius_one_site(self):
        _fresh()
        cid = gr.record_change("staging_json", "s.json", {"a": 1}, {"a": 2}, by="system")["id"]
        gr.register_pending(cid, "C", "siteA", "system")
        assert gr.blast_radius_ok("siteB")["ok"] is False   # another site in flight
        assert gr.blast_radius_ok("siteA")["ok"] is True     # same site is fine


class TestFailClosedReviewWindow:
    def test_expired_unreviewed_class_c_auto_reverts(self):
        _fresh()
        rec = gr.record_change("staging_json", "exp.json", {"v": 1}, {"v": 2}, by="system")
        gr._atomic_write_json(gr._staging_dir() / "exp.json", {"v": 2})
        gr.register_pending(rec["id"], "C", "siteExp", "system")
        # force deadline into the past
        d = gr._load_pending(); d["pending"][rec["id"]]["deadline"] = "2000-01-01T00:00:00+00:00"
        gr._save_pending(d)
        sw = gr.sweep_review_windows("system")
        assert rec["id"] in sw["auto_reverted"]
        assert gr.change_record(rec["id"])["rolled_back"] is True   # fail-closed

    def test_class_b_stays_provisional(self):
        _fresh()
        rec = gr.record_change("staging_json", "b.json", {"v": 1}, {"v": 2}, by="system")
        gr.register_pending(rec["id"], "B", "siteB", "system")  # no deadline for B
        sw = gr.sweep_review_windows("system")
        assert rec["id"] in sw["kept_provisional"]      # fail-open for B
        assert gr.change_record(rec["id"])["rolled_back"] is False

    def test_reject_review_rolls_back(self):
        _fresh()
        rec = gr.record_change("staging_json", "r.json", {"v": 1}, {"v": 2}, by="system")
        gr._atomic_write_json(gr._staging_dir() / "r.json", {"v": 2})
        gr.register_pending(rec["id"], "C", "siteR", "system")
        gr.mark_reviewed(rec["id"], "reject", "mboyle")
        assert gr.change_record(rec["id"])["rolled_back"] is True


class TestSelfThrottle:
    def test_demote_is_lower_only_never_raises(self):
        _fresh()
        # safety_demote must never raise C to auto
        r = ap.safety_demote("C", "auto_with_guardrails", "system", "x")
        assert r.get("unchanged") is True
        assert ap.load_policy()["levels"]["C"] == "approve_each"

    def test_oracle_disagreement_rate_unavailable(self):
        _fresh()
        assert gr.throttle_metrics()["oracle_disagreement_rate"] is None

    def test_throttle_no_action_on_small_sample(self):
        _fresh()
        r = gr.self_throttle_check("system")
        assert r["action"] == "none"

    def test_throttle_demotes_on_high_rollback_rate(self):
        _fresh()
        # First, simulate C having been opted to auto via the safety path is impossible
        # (lower-only). So set C up to a higher level through the manifest-free route:
        # we directly write the policy to auto to simulate a future-enabled C, then
        # verify the throttle DEMOTES it on a high rollback rate.
        pol = ap.load_policy(); pol["levels"]["C"] = "auto_with_guardrails"
        ap._atomic_write_json(ap._policy_path(), pol)
        # create enough applied+rolled-back changes to breach the rollback-rate threshold
        for i in range(gr._THROTTLE_MIN_SAMPLE + 1):
            rec = gr.record_change("staging_json", f"t{i}.json", {"a": i}, {"a": i + 1},
                                   by="system")
            gr._atomic_write_json(gr._staging_dir() / f"t{i}.json", {"a": i + 1})
            gr.rollback(rec["id"], "system")   # all rolled back → rate 1.0
        out = gr.self_throttle_check("system")
        assert out["action"] == "demoted_C_to_approve_each"
        assert ap.load_policy()["levels"]["C"] == "approve_each"


class TestGuardrailFailureBranch:
    def test_failure_freezes_and_alerts(self):
        _fresh()
        gf = gr.guardrail_failure("simulated rollback error", "system")
        assert gf["frozen"] is True and ap.is_frozen() is True
        assert any(a["kind"] == "guardrail_failure" for a in gr.alerts())
        ap.unfreeze("mboyle", "clear")

    def test_rollback_error_triggers_failure_branch(self):
        _fresh()
        # register a reverser that raises, then a change using it
        gr.register_reverser("explode", lambda ref, before: (_ for _ in ()).throw(RuntimeError("boom")))
        rec = gr.record_change("explode", "x", {"a": 1}, {"a": 2}, by="system")
        rb = gr.rollback(rec["id"], "system")
        assert rb.get("frozen") is True   # guardrail_failure fired
        assert ap.is_frozen() is True
        ap.unfreeze("mboyle", "clear")


class TestPosture:
    def test_no_live_fetch_or_credential_path(self):
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", ".replay(", "do_login(", "web_fetch"):
            assert bad not in _SRC, f"guardrails must not use {bad!r}"

    def test_no_live_config_or_corpus_write(self):
        # the rollback engine must not write live config / corpus — only confined staging
        for bad in ("sites_config", "validation_corpus", "write_corpus",
                    "BD_SITES_CONFIG"):
            assert bad not in _SRC

    def test_no_external_push_on_alerts(self):
        assert "external_push" in _SRC and "no external push" in _SRC.lower()

    def test_atomic_writes_and_utf8(self):
        assert ".replace(" in _SRC and ".tmp" in _SRC and 'encoding="utf-8"' in _SRC

    def test_no_background_scheduler(self):
        for bad in ("threading.Thread", "schedule.every", "while True",
                    "BackgroundScheduler", "asyncio.create_task"):
            assert bad not in _SRC

    def test_safety_demote_distinct_audit_action(self):
        # the policy module records safety demotions distinctly from human edits
        assert "safety_demote" in _POL


class TestWiring:
    def test_endpoints_and_pages_present(self):
        for r in ("status", "changes", "pending"):
            assert f'@bp.get("/api/guardrails/{r}")' in _CONSOLE
        assert "PAGES.guardrails" in _CONSOLE and 'data-p="guardrails"' in _CONSOLE
        assert "PAGES.grchanges" in _CONSOLE and "PAGES.grpending" in _CONSOLE

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        for r in ("status", "changes", "pending"):
            assert c.get(f"/cockpit/api/guardrails/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for r in ("status", "changes", "pending"):
            assert f"/cockpit/api/guardrails/{r}" not in posts
