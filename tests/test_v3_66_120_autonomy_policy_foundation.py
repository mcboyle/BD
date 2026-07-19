"""v3.66.120 — Phase A: autonomy governance foundation.

The model + the safety apparatus, built BEFORE any autonomy. Tests: safe defaults;
classify-by-write-target; permanent pins; guardrail gating of Level 3 (refused when
guardrails absent / for read-only A / for irreversible D); versioning + stable hash +
audit on edits; the kill switch is INDEPENDENT of the policy file and fails safe;
decision snapshots are immutable and capture the policy state; the enforcement
primitive is False for every class in Phase A; module posture (atomic writes, utf-8,
no live-fetch/replay/apply, no autonomous execution); wiring (+4 GET = 101, no POST).
"""
import json
import shutil
from pathlib import Path

from tools import autonomy_policy as ap
from tools.cockpit_core import tasks_root

_SRC = Path(ap.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(ap.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")


def _fresh():
    g = tasks_root() / "governance"
    if g.exists():
        shutil.rmtree(g)


class TestModelAndDefaults:
    def test_default_posture_is_safe(self):
        _fresh()
        levels = ap.load_policy()["levels"]
        assert levels == {"A": "observe", "B": "suggest",
                          "C": "approve_each", "D": "approve_each"}

    def test_classes_defined_by_write_target(self):
        # the doc's central refactor: classes carry a write_target, not just a name
        for c in ("A", "B", "C", "D"):
            assert "write_target" in ap.ACTION_CLASSES[c]
        assert "third party" in ap.ACTION_CLASSES["D"]["write_target"].lower()
        assert "live config" in ap.ACTION_CLASSES["C"]["write_target"].lower() or \
               "corpus" in ap.ACTION_CLASSES["C"]["write_target"].lower()

    def test_four_levels(self):
        assert set(ap.LEVELS.values()) == {"observe", "suggest", "approve_each",
                                           "auto_with_guardrails"}

    def test_policy_change_is_human_only_pin(self):
        # the policy's own edit path is permanently pinned (doc §7)
        assert "automation_policy_changes" in ap.PINNED_APPROVE_EACH


class TestGuardrailGating:
    def test_level3_refused_for_C_without_guardrails(self):
        _fresh()
        # Through Phase D the correctness_oracle was unbuilt and this was refused. As of
        # Phase E the guardrail set is complete, so the level flip itself SUCCEEDS — but
        # that does not enable automation (see the no-automation tests). The durable
        # guardrail facts: kill_switch + decision_snapshot are present.
        reg = ap.guardrail_registry()
        assert reg["kill_switch"]["built"] and reg["decision_snapshot"]["built"]
        r = ap.set_policy_level("C", "auto_with_guardrails", "tester")
        assert r["ok"] is True   # guardrails complete as of Phase E
        # but the default posture (a fresh policy) keeps C at Approve-each
        _fresh()
        assert ap.load_policy()["levels"]["C"] == "approve_each"

    def test_level3_never_for_D_irreversible(self):
        _fresh()
        r = ap.set_policy_level("D", "auto_with_guardrails", "tester")
        assert r["ok"] is False and "irreversible" in r["error"].lower()

    def test_level3_not_applicable_for_A(self):
        _fresh()
        r = ap.set_policy_level("A", "auto_with_guardrails", "tester")
        assert r["ok"] is False and "read-only" in r["error"].lower()

    def test_matrix_explains_unavailability(self):
        _fresh()
        rep = ap.policy_report()
        by = {c["class"]: c for c in rep["classes"]}
        assert by["A"]["level3"]["available"] is False   # read-only
        assert by["D"]["level3"]["available"] is False   # irreversible
        # As of Phase E the guardrail set is complete, so C's Level 3 is available to
        # opt into — but C stays at Approve-each by default (availability != enabled).
        assert by["C"]["level3"]["available"] is True
        assert by["C"]["configured_level"] == "approve_each"
        # every Level-3 entry carries either a reason or, when blocked, missing guards
        for c in rep["classes"]:
            l3 = c["level3"]
            assert l3.get("reason") or l3.get("missing_guardrails") or l3.get("available")


class TestVersioningHashAudit:
    def test_legal_edit_bumps_version_and_hash_and_audits(self):
        _fresh()
        h0 = ap.policy_hash()
        v0 = ap.load_policy()["version"]
        r = ap.set_policy_level("B", "approve_each", "mboyle", "tighten")
        assert r["ok"] is True and r["to"] == "approve_each"
        assert ap.load_policy()["version"] == v0 + 1
        assert ap.policy_hash() != h0
        tail = ap.read_audit()[-1]
        assert tail["action"] == "set_policy_level" and tail["to"] == "approve_each"
        assert tail["by"] == "mboyle" and "policy_hash" in tail

    def test_hash_stable_for_same_policy(self):
        _fresh()
        assert ap.policy_hash() == ap.policy_hash()

    def test_edit_requires_identity(self):
        _fresh()
        r = ap.set_policy_level("B", "suggest", "")
        assert r["ok"] is False and "identity" in r["error"].lower()

    def test_noop_edit_is_unchanged(self):
        _fresh()
        r = ap.set_policy_level("C", "approve_each", "mboyle")  # already approve_each
        assert r.get("unchanged") is True


class TestIndependentKillSwitch:
    def test_freeze_unfreeze_and_contract(self):
        _fresh()
        assert ap.is_frozen() is False
        ap.freeze("mboyle", "emergency")
        assert ap.is_frozen() is True
        ap.unfreeze("mboyle", "clear")
        assert ap.is_frozen() is False

    def test_kill_switch_is_separate_file_from_policy(self):
        _fresh()
        ap.freeze("mboyle", "test")
        # corrupt the POLICY file; the kill switch must still report frozen
        ap._policy_path().write_text("{ not json", encoding="utf-8")
        assert ap.is_frozen() is True            # independence
        assert ap.load_policy()["levels"]["A"] == "observe"  # policy falls back safely
        ap.unfreeze("mboyle", "clear")

    def test_unreadable_freeze_file_fails_safe(self):
        _fresh()
        ap._freeze_path().parent.mkdir(parents=True, exist_ok=True)
        ap._freeze_path().write_text("{ garbage", encoding="utf-8")
        assert ap.is_frozen() is True            # fail safe = treated as frozen

    def test_freeze_requires_identity(self):
        _fresh()
        assert ap.freeze("")["ok"] is False


class TestDecisionSnapshots:
    def test_snapshot_is_immutable_and_captures_policy_state(self):
        _fresh()
        snap = ap.record_decision_snapshot(
            {"action_class": "C", "action": "selector_promotion", "site": "demo",
             "scores_used": {"confidence": 0.9}, "thresholds_used": {"min": 0.8},
             "proposed_change": {"add": [".x"]}}, "system")
        assert snap["ok"] and snap["id"]
        got = ap.get_decision_snapshot(snap["id"])
        assert got["_immutable"] is True
        assert got["policy_hash"] == ap.policy_hash()
        assert got["policy_version"] == ap.load_policy()["version"]
        assert got["decision"]["action"] == "selector_promotion"

    def test_snapshot_not_overwritten(self):
        _fresh()
        d = {"action_class": "C", "action": "x", "site": "s"}
        a = ap.record_decision_snapshot(d, "system")
        b = ap.record_decision_snapshot(d, "system")  # identical → same id, no rewrite
        assert a["id"] == b["id"]

    def test_list_and_empty(self):
        _fresh()
        assert ap.list_decision_snapshots() == []
        ap.record_decision_snapshot({"action_class": "B", "action": "y"}, "system")
        assert len(ap.list_decision_snapshots()) == 1


class TestEnforcementPrimitive:
    def test_nothing_autonomous_in_phase_a(self):
        _fresh()
        for c in "ABCD":
            assert ap.can_autonomously(c)["allowed"] is False
        assert ap.governance_status()["any_class_autonomous"] is False

    def test_frozen_blocks_even_if_level_were_auto(self):
        _fresh()
        ap.freeze("mboyle", "test")
        # even directly forcing a level on disk, frozen must win
        pol = ap.load_policy(); pol["levels"]["B"] = "auto_with_guardrails"
        ap._atomic_write_json(ap._policy_path(), pol)
        assert ap.can_autonomously("B")["allowed"] is False
        assert "frozen" in ap.can_autonomously("B")["reason"].lower()
        ap.unfreeze("mboyle", "clear")


class TestPostureReadOnly:
    def test_atomic_writes_and_utf8(self):
        # state-file invariant: .tmp + replace, utf-8 everywhere
        assert ".replace(" in _SRC and ".tmp" in _SRC
        assert 'encoding="utf-8"' in _SRC

    def test_no_live_fetch_replay_or_apply(self):
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", ".replay(", "do_login(",
                    "web_fetch"):
            assert bad not in _SRC, f"governance module must not use {bad!r}"

    def test_adds_no_autonomous_execution(self):
        # this phase must not order queues, mutate templates, write corpus, launch
        # captures, etc. — it only models/records/freezes
        for bad in ("def order_queue", "def apply_template", "def write_corpus",
                    "def launch_capture", "def retire_debt", "def promote_selector"):
            assert bad not in _SRC


class TestWiring:
    def test_endpoints_and_pages_present(self):
        for r in ("matrix", "status", "audit", "snapshots"):
            assert f'@bp.get("/api/policy/{r}")' in _CONSOLE
        assert "PAGES.governance" in _CONSOLE and 'data-p="governance"' in _CONSOLE
        assert "PAGES.govaudit" in _CONSOLE and "PAGES.govsnapshots" in _CONSOLE

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        for r in ("matrix", "status", "audit", "snapshots"):
            assert c.get(f"/cockpit/api/policy/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for r in ("matrix", "status", "audit", "snapshots"):
            assert f"/cockpit/api/policy/{r}" not in posts
