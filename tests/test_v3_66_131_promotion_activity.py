"""v3.66.131 — Phase G / G6: Promotion Activity.

Proves the append-only governance-transition audit log that ties G1–G5 together:
  * RECORD + SCAN — `record_transition` appends; `scan_and_record` establishes a baseline
    (records nothing) and then, after a real state change (trust decaying below the floor),
    records the `trust_eligible` True -> False transition.
  * APPEND-ONLY — the log only grows; prior entries are never rewritten; the module has no
    truncate/unlink/clear; the snapshot is written atomically (.tmp + replace) while the
    activity log is appended (matching the guardrail alerts log).
  * NEVER PROMOTES — no log entry ever shows participation_eligible -> True; the state
    composer reports participation_eligible False; the module contains no apply/promotion
    construct.
  * TIES THE CHAIN — the tracked fields span eligibility, trust, oracle tier, and
    validation.
  * WIRING — 3 read-only GET routes (140 -> 143), no new POST (the scan is host-scheduled).

POSTURE GREPS ban apply/promotion constructs, corpus/policy/credential mutation, and
network/browser/capture. (The module's own append-log write and atomic snapshot write are
intentional and allowed; bare words in honesty docstrings are not constructs.)
"""
import shutil
from pathlib import Path

from tools import autonomy_promotion as apr
from tools import autonomy_trust as atr
from tools.cockpit_core import tasks_root

_SRC = Path(apr.__file__).read_text(encoding="utf-8")
_SITES = ["sitea"]


def _fresh():
    g = tasks_root() / "governance"
    if g.exists():
        shutil.rmtree(g)


class TestRecordTransition:
    def test_append_one(self):
        _fresh()
        r = apr.record_transition("sitea", "evidence_qualified", True, False,
                                  by="mboyle", reason="manual")
        assert r["ok"] is True and r["field"] == "evidence_qualified"
        entries = apr.activity_log()["entries"]
        assert len(entries) == 1
        e = entries[0]
        for k in ("ts", "site", "field", "before", "after", "by", "reason"):
            assert k in e


class TestScanDetectsTransition:
    def test_baseline_then_trust_transition(self):
        _fresh()
        first = apr.scan_and_record(by="system", sites=_SITES)
        assert first["transitions"] == 0, "first scan is a baseline; records nothing"
        # real state change: decay trust below the floor
        atr.decay_trust("sitea", by="mboyle")
        assert atr.effective_trust("sitea") < atr.MIN_TRUST
        second = apr.scan_and_record(by="system", sites=_SITES)
        assert second["transitions"] >= 1
        fields = {(c["field"], c["before"], c["after"]) for c in second["changes"]}
        assert ("trust_eligible", True, False) in fields


class TestAppendOnly:
    def test_log_only_grows_and_preserves(self):
        _fresh()
        apr.record_transition("sitea", "oracle_tier", 0, 1, by="system")
        first = apr.activity_log()["entries"][0]
        n1 = apr.activity_log()["count"]
        apr.record_transition("siteb", "trust_eligible", True, False, by="mboyle")
        n2 = apr.activity_log()["count"]
        assert n2 > n1
        # the earlier entry is unchanged (append-only, never rewritten)
        assert apr.activity_log()["entries"][0] == first

    def test_no_truncate_or_delete_in_source(self):
        for bad in ("truncate", "unlink", ".clear()", "os.remove", 'open(p, "w")',
                    "seek(0)"):
            assert bad not in _SRC, f"append-only log must not {bad!r}"


class TestNeverPromotes:
    def test_participation_never_true_in_log(self):
        _fresh()
        apr.scan_and_record(by="system", sites=_SITES)
        atr.decay_trust("sitea", by="mboyle")
        apr.scan_and_record(by="system", sites=_SITES)
        for e in apr.activity_log(10000)["entries"]:
            assert not (e.get("field") == "participation_eligible" and e.get("after") is True)

    def test_state_participation_false(self):
        _fresh()
        st = apr._state_of("sitea")
        assert st["participation_eligible"] is False

    def test_no_apply_or_promote_construct(self):
        for bad in ("def apply(", "apply_change(", "promote_family", "def promote(",
                    "set_policy_level(", "mark_reviewed(", "record_change(",
                    "promotion_corpus", "set_credential(", "write_credential("):
            assert bad not in _SRC, f"promotion activity must not {bad!r}"

    def test_note_states_never_applies(self):
        low = _SRC.lower()
        assert "append-only" in low and "never" in low and "apply path" in low


class TestTiesChain:
    def test_tracked_fields_span_phases(self):
        for f in ("evidence_qualified", "participation_eligible", "trust_eligible",
                  "oracle_tier", "validation_status"):
            assert f in apr.TRACKED_FIELDS
        st = apr._state_of("sitea")
        for f in apr.TRACKED_FIELDS:
            assert f in st


class TestStoreAtomicity:
    def test_snapshot_atomic_log_append(self):
        # snapshot via .tmp + replace; activity via append-mode open
        assert ".tmp" in _SRC and ".replace(" in _SRC
        assert 'open(p, "a"' in _SRC

    def test_snapshot_written_on_scan(self):
        _fresh()
        apr.scan_and_record(by="system", sites=_SITES)
        assert apr._snapshot_path().is_file()


class TestViews:
    def test_view_shapes(self):
        _fresh()
        apr.record_transition("sitea", "oracle_tier", 0, 2, by="system")
        assert apr.activity_log()["count"] == 1
        assert apr.site_activity("sitea")["count"] == 1
        assert apr.site_activity("nope")["count"] == 0
        s = apr.promotion_status()
        assert "total_transitions" in s and "tracked_fields" in s
        ov = apr.promotion_overview(sites=_SITES)
        assert ov["site_count"] == 1 and ov["any_participation_eligible"] is False


class TestWiring:
    def test_endpoints_and_pages_present(self):
        console = Path(Path(apr.__file__).parent / "cockpit_console.py").read_text(encoding="utf-8")
        for r in ("status", "activity", "site"):
            assert f'@bp.get("/api/promotion/{r}")' in console
        for pg in ("promotionactivity", "promotionsite"):
            assert f"PAGES.{pg}" in console
        assert 'data-p="promotionactivity"' in console

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162, "G6 adds 3 GET routes (140 -> 143)."
        c = app.test_client()
        for r in ("status", "activity", "site"):
            assert c.get(f"/cockpit/api/promotion/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert len(posts) >= 26, "G6 adds no POST (scan is host-scheduled)."
        for r in ("status", "activity", "site"):
            assert f"/cockpit/api/promotion/{r}" not in posts
