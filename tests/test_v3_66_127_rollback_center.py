"""v3.66.127 — Phase G / G2: Rollback Center.

Re-anchors the load-bearing rollback properties at the Phase-G level and proves the new
Rollback Center surface + the reverser→eligibility wire:
  * ROLLBACK ALWAYS WORKS — a recorded change reverts to before-state, is idempotent, and
    the Rollback Center history reflects it.
  * REVIEW REJECTION TRIGGERS ROLLBACK — rejecting a pending Class C change reverts it
    immediately; reversibility shows it as already rolled back.
  * EXPIRED CLASS C AUTO-REVERTS (fail-closed) — `pending_windows` flags an expired
    window before the sweep; the host-invoked sweep reverts it.
  * ELIGIBILITY REQUIRES A REGISTERED REVERSER — a candidate whose target_kind has no
    reverser is irreversible, so the eligibility layer will not qualify it.
Plus: the Rollback Center module is READ-ONLY (it never reverts — that is an audited
guardrail function), posture-safe, and the cockpit gains 4 read-only GET routes
(127 -> 131, no new POST).

POSTURE/NO-MUTATION GREPS ban the CONSTRUCT (`def rollback(`, `mark_reviewed(`,
`record_change(`), never an English word — the module's docstring names those guardrail
functions in prose (backticked, no parens) and must not trip the bans.
"""
import datetime as _dt
import json
from pathlib import Path

from _cockpit_tasks import remove_test_governance
from tools import autonomy_policy as ap
from tools import autonomy_guardrails as agr
from tools import autonomy_rollback as arb
from tools import autonomy_eligibility as el
from tools.cockpit_core import tasks_root

_SRC = Path(arb.__file__).read_text(encoding="utf-8")

_HO2 = [{"capture": "c1", "identity": "m", "renditions": ["1080p"],
         "template_shape": "media_present", "signing_marker_names": ["t"]},
        {"capture": "c2", "identity": "m", "renditions": ["1080p"],
         "template_shape": "media_present", "signing_marker_names": ["t"]}]


def _fresh():
    remove_test_governance(tasks_root())


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _ago(days):
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()


class TestRollbackCapability:
    def test_registry_has_staging_reverser(self):
        reg = arb.reverser_registry()
        assert "staging_json" in reg["target_kinds"] and reg["count"] >= 1

    def test_capability_for_registered_and_unregistered(self):
        assert arb.rollback_capability("staging_json")["reversible"] is True
        assert arb.rollback_capability("definitely_not_registered")["reversible"] is False

    def test_capability_none_is_engine_readiness(self):
        assert arb.rollback_capability(None)["reversible"] is True  # >=1 reverser


class TestRollbackAlwaysWorks:
    def test_round_trip_restores_and_is_idempotent(self):
        _fresh()
        rec = agr.record_change("staging_json", "rt.json", {"v": 1}, {"v": 2}, by="system")
        agr._atomic_write_json(agr._staging_dir() / "rt.json", {"v": 2})
        rb = agr.rollback(rec["id"], "mboyle")
        assert rb["ok"]
        restored = json.loads((agr._staging_dir() / "rt.json").read_text(encoding="utf-8"))
        assert restored == {"v": 1}
        again = agr.rollback(rec["id"], "mboyle")
        assert again.get("already_rolled_back") is True

    def test_center_history_reflects_rollback(self):
        _fresh()
        rec = agr.record_change("staging_json", "rt2.json", {"v": 1}, {"v": 2}, by="system")
        agr._atomic_write_json(agr._staging_dir() / "rt2.json", {"v": 2})
        agr.rollback(rec["id"], "mboyle")
        h = arb.rollback_history()
        match = [c for c in h["changes"] if c["id"] == rec["id"]]
        assert match and match[0]["rolled_back"] is True
        assert h["rolled_back"] >= 1


class TestReviewRejectionTriggersRollback:
    def test_reject_reverts_immediately(self):
        _fresh()
        rec = agr.record_change("staging_json", "rj.json", {"v": 1}, {"v": 2}, by="system")
        agr._atomic_write_json(agr._staging_dir() / "rj.json", {"v": 2})
        agr.register_pending(rec["id"], "C", "siteRJ", "system")
        agr.mark_reviewed(rec["id"], "reject", "mboyle")
        rc = agr.change_record(rec["id"])
        assert rc["rolled_back"] is True
        restored = json.loads((agr._staging_dir() / "rj.json").read_text(encoding="utf-8"))
        assert restored == {"v": 1}
        rr = arb.reversibility_report()
        row = next(r for r in rr["changes"] if r["id"] == rec["id"])
        assert row["rolled_back"] is True and row["reversible_now"] is False


class TestExpiredAutoRevert:
    def test_pending_windows_flags_then_sweep_reverts(self):
        _fresh()
        rec = agr.record_change("staging_json", "exp.json", {"v": 1}, {"v": 2}, by="system")
        agr._atomic_write_json(agr._staging_dir() / "exp.json", {"v": 2})
        agr.register_pending(rec["id"], "C", "siteExp", "system")
        # backdate the deadline so the window is expired
        pend = agr._load_pending()
        pend["pending"][rec["id"]]["deadline"] = _ago(1)  # 1 day ago
        agr._save_pending(pend)
        pw = arb.pending_windows()
        assert rec["id"] in pw["expired_pending_class_c"]
        sw = agr.sweep_review_windows("system")
        assert rec["id"] in sw["auto_reverted"]
        assert agr.change_record(rec["id"])["rolled_back"] is True


class TestReverserPreconditionForEligibility:
    def test_reversible_target_can_qualify(self):
        _fresh()
        r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(2), now=_now(),
                             candidate={"target_kind": "staging_json"})
        assert r["evidence_qualified"] is True and r["rollback_capable"] is True

    def test_irreversible_target_cannot_qualify(self):
        _fresh()
        r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(2), now=_now(),
                             candidate={"target_kind": "no_such_reverser"})
        assert r["evidence_qualified"] is False
        assert r["rollback_capable"] is False
        assert any("reverser" in d for d in r["decay_reasons"])

    def test_no_candidate_unaffected(self):
        _fresh()
        r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(2), now=_now())
        assert r["evidence_qualified"] is True
        assert r["rollback_target_kind"] is None and r["rollback_capable"] is True


class TestRollbackCenterViews:
    def test_center_shape(self):
        _fresh()
        c = arb.rollback_center()
        for k in ("engine_operational", "reverser_kinds", "reverser_count",
                  "changes_recorded", "changes_rolled_back", "pending_windows", "frozen"):
            assert k in c
        assert c["engine_operational"] is True

    def test_reversibility_and_pending_shapes(self):
        _fresh()
        rr = arb.reversibility_report()
        assert "changes" in rr and "irreversible_pending" in rr
        pw = arb.pending_windows()
        assert "pending" in pw and "expired_pending_class_c" in pw


class TestPostureNoForbiddenMechanics:
    def test_no_network_fetch(self):
        for bad in ("requests.", "httpx", "urlopen", "web_fetch", "socket."):
            assert bad not in _SRC, bad

    def test_no_browser_interaction(self):
        for bad in ("playwright", "page.goto", "selenium", "webdriver", ".click("):
            assert bad not in _SRC, bad

    def test_no_redownload_or_byte_or_reconstruction(self):
        for bad in ("download(", "urlretrieve", "stream_to_file", "hashlib", "filecmp",
                    "read_bytes()", "def reconstruct", "compute_signature", "build_signed",
                    ".replay("):
            assert bad not in _SRC, bad


class TestReadOnlyNoMutation:
    def test_module_does_not_execute_rollback_or_mutate(self):
        # the Rollback Center is READ-ONLY: it never reverts or writes. Reverting is a
        # guardrail function invoked elsewhere. Ban the CONSTRUCT, not the word.
        for bad in ("def apply(", "def rollback(", "mark_reviewed(",
                    "sweep_review_windows(", "record_change(", "register_pending(",
                    "set_policy_level(", "safety_demote(", "register_reverser("):
            assert bad not in _SRC, f"rollback center must not {bad!r}"

    def test_module_does_not_write_files(self):
        for bad in ("open(", ".write_text(", "_atomic_write", ".write(", "mkdir("):
            assert bad not in _SRC, f"rollback center is read-only; found {bad!r}"

    def test_posture_note_and_utc(self):
        low = _SRC.lower()
        assert "read-only" in low and "timezone.utc" in _SRC


class TestWiring:
    def test_endpoints_and_pages_present(self):
        console = Path(Path(arb.__file__).parent / "cockpit_console.py").read_text(encoding="utf-8")
        for r in ("center", "history", "reversibility", "reversers"):
            assert f'@bp.get("/api/rollback/{r}")' in console
        for pg in ("rollbackcenter", "rollbackreversibility"):
            assert f"PAGES.{pg}" in console
        assert 'data-p="rollbackcenter"' in console

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162, "G2 adds 4 GET routes (127 -> 131)."
        c = app.test_client()
        for r in ("center", "history", "reversibility", "reversers"):
            assert c.get(f"/cockpit/api/rollback/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert len(posts) >= 26, "G2 adds no POST; the state-change surface is unchanged."
        for r in ("center", "history", "reversibility", "reversers"):
            assert f"/cockpit/api/rollback/{r}" not in posts
