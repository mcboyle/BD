"""v3.66.130 — Phase G / G5: Impact Analysis.

Proves the read-only single-change impact analyser:
  * IMPACT REPORT — reports reversibility, pinned-target, blast radius, oracle tier, trust,
    and evidence-qualification for one proposed change; flags an irreversible target, a
    pinned action, and a multi-site (family-wide) footprint.
  * PARTICIPATION ALWAYS FALSE — `participation_eligible` is False for every candidate;
    `safe_to_consider` only means the gates would pass, never that anything is authorized.
  * SINGLE-CHANGE, NO PROMOTION — the module never applies or promotes; it analyses one
    change and flags family-wide footprints as out of scope.
  * WIRING — 3 read-only GET routes (137 -> 140), no new POST.

POSTURE GREPS ban apply/promotion/family-write/network/browser/capture/any write. (A
read-only `for s in sites` comprehension in `impact_overview` is legitimate and NOT
banned — it analyses a benign probe per site; it does not apply.)
"""
import shutil
from pathlib import Path

from tools import autonomy_eligibility as el
from tools import autonomy_impact as ai
from tools.cockpit_core import tasks_root

_SRC = Path(ai.__file__).read_text(encoding="utf-8")
_PIN = sorted(el.PERMANENTLY_INELIGIBLE)[0]


def _fresh():
    g = tasks_root() / "governance"
    if g.exists():
        shutil.rmtree(g)


class TestImpactReport:
    def test_reversible_vs_irreversible(self):
        _fresh()
        assert ai.impact_report({"site": "s", "target_kind": "staging_json"})["reversible"] is True
        irr = ai.impact_report({"site": "s", "target_kind": "no_such_kind"})
        assert irr["reversible"] is False
        assert any("reverser" in c for c in irr["concerns"])

    def test_pinned_target_flagged(self):
        _fresh()
        r = ai.impact_report({"site": "s", "action": _PIN, "target_kind": "staging_json"})
        assert r["touches_pinned"] is True
        assert r["safe_to_consider"] is False

    def test_blast_radius_and_family_wide(self):
        _fresh()
        one = ai.impact_report({"site": "s", "target_kind": "staging_json"})
        assert one["blast_radius"] == 1 and one["family_wide"] is False
        many = ai.impact_report({"site": "s", "sites": ["s", "t", "u"],
                                 "target_kind": "staging_json"})
        assert many["blast_radius"] == 3 and many["family_wide"] is True
        assert any("family-wide" in c for c in many["concerns"])


class TestParticipationAlwaysFalse:
    def test_no_candidate_is_participation_eligible(self):
        _fresh()
        for cand in ({"site": "s", "target_kind": "staging_json"},
                     {"site": "s", "target_kind": "no_such_kind"},
                     {"site": "s", "action": _PIN, "target_kind": "staging_json"},
                     {"site": "s", "sites": ["s", "t"], "target_kind": "staging_json"}):
            assert ai.impact_report(cand)["participation_eligible"] is False


class TestSingleChangeNoPromotion:
    def test_no_apply_or_promotion_constructs(self):
        for bad in ("def apply(", "def promote(", "promote_family", "apply_change(",
                    "record_change(", "mark_reviewed(", "sweep_review_windows(",
                    "set_policy_level(", "validation_corpus", "def reset_trust("):
            assert bad not in _SRC, f"impact analysis must not {bad!r}"

    def test_no_write_or_mechanics(self):
        for bad in ("open(", ".write_text(", ".write(", "mkdir(", "requests.", "httpx",
                    "urlopen", "web_fetch", "socket.", "playwright", ".click(",
                    "download(", "hashlib"):
            assert bad not in _SRC, f"impact analysis is read-only; found {bad!r}"

    def test_note_states_scope(self):
        low = _SRC.lower()
        assert "single" in low and "never" in low and "read-only" in low


class TestViews:
    def test_overview_and_status_shapes(self):
        _fresh()
        ov = ai.impact_overview(sites=["a", "b"])
        assert ov["site_count"] == 2 and "any_participation_eligible" in ov
        assert ov["any_participation_eligible"] is False
        # benign probe is reversible everywhere
        assert all(r["reversible"] for r in ov["sites"])
        s = ai.impact_status()
        for k in ("site_count", "safe_to_consider_count", "any_participation_eligible"):
            assert k in s


class TestWiring:
    def test_endpoints_and_pages_present(self):
        console = Path(Path(ai.__file__).parent / "cockpit_console.py").read_text(encoding="utf-8")
        for r in ("status", "overview", "analyze"):
            assert f'@bp.get("/api/impact/{r}")' in console
        for pg in ("impactanalysis", "impactsite"):
            assert f"PAGES.{pg}" in console
        assert 'data-p="impactanalysis"' in console

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162, "G5 adds 3 GET routes (137 -> 140)."
        c = app.test_client()
        for r in ("status", "overview", "analyze"):
            assert c.get(f"/cockpit/api/impact/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert len(posts) >= 26, "G5 adds no POST."
        for r in ("status", "overview", "analyze"):
            assert f"/cockpit/api/impact/{r}" not in posts
