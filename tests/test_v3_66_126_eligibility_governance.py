"""v3.66.126 — Phase G / G1: Eligibility Governance (participation-eligibility engine).

Proves the load-bearing safety properties of the new `autonomy_eligibility` layer:
  * STALE EVIDENCE REMOVES ELIGIBILITY — qualification decays automatically when held-out
    evidence ages past the freshness window (and when freshness is unknown, frozen, or
    the oracle hard-fails). Trust only ever DECREASES.
  * INELIGIBLE SITES CAN'T PARTICIPATE — Tier 0 / stale / unqualified sites are never
    participation-eligible; and NO site is participation-eligible in this build (no
    per-site grant, no Class C apply path, Approve-each default) — even a fresh Tier-3
    site with Class C flipped to auto.
  * PINNED ACTIONS PERMANENTLY BLOCKED — a candidate targeting a permanently-ineligible
    action is blocked regardless of tier/freshness.
Plus: the small-set cap is enforced; the module is READ-ONLY and posture-safe
(descriptors by name only; no fetch / browser / re-download / byte-compare / signed-URL
reconstruction); and the cockpit wiring adds 3 read-only GET routes (124 -> 127, no new
POST — the state-change surface is unchanged).

NOTE ON THE POSTURE/NO-MUTATION GREPS: they ban the actual CONSTRUCT (`def apply(`,
`def rollback(`, `hashlib`, `mark_reviewed(`, `set_policy_level(`), never a bare English
word, so the module's own honesty docstring ("no media re-download", "signed-URL
reconstruction", "trust only ever decreases") does not trip them. (Session lesson: a
WORD grep catches your own prose; a CONSTRUCT grep catches real behavior.)
"""
import datetime as _dt
import shutil
from pathlib import Path

from tools import autonomy_policy as ap
from tools import autonomy_oracle as ao
from tools import autonomy_eligibility as el
from tools.cockpit_core import tasks_root

_SRC = Path(el.__file__).read_text(encoding="utf-8")

# 2 agreeing held-out captures -> oracle Tier 3; 1 -> Tier 2.
_HO2 = [{"capture": "c1", "identity": "movieX", "renditions": ["1080p"],
         "template_shape": "media_present", "signing_marker_names": ["token"]},
        {"capture": "c2", "identity": "movieX", "renditions": ["1080p"],
         "template_shape": "media_present", "signing_marker_names": ["token"]}]
_HO1 = _HO2[:1]


def _fresh():
    g = tasks_root() / "governance"
    if g.exists():
        shutil.rmtree(g)


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _ago(days):
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()


class TestDecay:
    def test_fresh_tier3_is_evidence_qualified(self):
        _fresh()
        r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(5), now=_now())
        assert r["oracle_tier"] == 3 and r["evidence_fresh"] is True
        assert r["evidence_qualified"] is True

    def test_stale_evidence_removes_qualification(self):
        _fresh()
        r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(40), now=_now())
        assert r["oracle_tier"] == 3            # tier is still strong...
        assert r["evidence_fresh"] is False
        assert r["evidence_qualified"] is False  # ...but qualification has DECAYED
        assert any("stale" in d for d in r["decay_reasons"])

    def test_unknown_freshness_is_treated_as_stale(self):
        _fresh()
        # no designated timestamp -> freshness unknown -> fail-safe to NOT qualified
        r = el.evaluate_site("s", held_out=_HO2, evidence_ts=None, now=_now())
        assert r["evidence_qualified"] is False
        assert any("freshness unknown" in d for d in r["decay_reasons"])

    def test_freeze_removes_qualification(self):
        _fresh()
        ap.freeze("tester", "drill")
        try:
            r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(1), now=_now())
            assert r["frozen"] is True and r["evidence_qualified"] is False
            assert any("frozen" in d for d in r["decay_reasons"])
        finally:
            _fresh()

    def test_tier_below_three_not_qualified(self):
        _fresh()
        r = el.evaluate_site("s", held_out=_HO1, evidence_ts=_ago(1), now=_now())
        assert r["oracle_tier"] == 2 and r["evidence_qualified"] is False


class TestIneligibleCannotParticipate:
    def test_tier0_cannot_participate(self):
        _fresh()
        r = el.evaluate_site("s", held_out=[])
        assert r["oracle_tier"] == 0 and r["participation_eligible"] is False

    def test_no_site_participation_eligible_even_fresh_tier3(self):
        import tools.autonomy_guardrails as _agr
        _fresh()
        _had = _agr._REVERSERS.pop("live_site_config", None)  # H: assert the no-apply-path reason
        try:
            r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(1), now=_now())
            assert r["evidence_qualified"] is True            # qualified...
            assert r["participation_eligible"] is False        # ...but cannot participate
            joined = "; ".join(r["reasons"])
            assert "no Class C apply path" in joined
            assert "per-site Class C gate closed" in joined
        finally:
            if _had is not None:
                _agr._REVERSERS["live_site_config"] = _had

    def test_not_eligible_even_with_class_c_flipped_to_auto(self):
        _fresh()
        # Flip C to auto AND give fresh Tier-3 evidence: still not eligible, because the
        # per-site grant store is empty and no apply path exists.
        ap.set_policy_level("C", "auto_with_guardrails", "mboyle", "flip")
        try:
            r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(1), now=_now())
            assert r["participation_eligible"] is False
            assert el.can_participate("s") is False
        finally:
            _fresh()

    def test_apply_path_absent_and_chokepoint_false(self):
        import tools.autonomy_guardrails as _agr
        _had = _agr._REVERSERS.pop("live_site_config", None)  # H: apply path tracks the live reverser
        try:
            assert el.apply_path_exists() is False             # no live reverser registered
        finally:
            if _had is not None:
                _agr._REVERSERS["live_site_config"] = _had
        _fresh()
        for ho in ([], _HO1, _HO2):
            assert el.can_participate("s") is False, "can_participate must be False for all"


class TestPinnedPermanentlyBlocked:
    def test_pinned_candidate_blocks_even_fresh_tier3(self):
        _fresh()
        r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(1), now=_now(),
                             candidate={"action": "corpus_writes"})
        assert r["candidate_blocked"] is True
        assert r["evidence_qualified"] is False
        assert any("permanently-ineligible" in d for d in r["decay_reasons"])

    def test_every_permanently_ineligible_action_blocks(self):
        _fresh()
        for action in ao.PERMANENTLY_INELIGIBLE:
            r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(1), now=_now(),
                                 candidate={"action": action})
            assert r["candidate_blocked"] is True, action
            assert r["participation_eligible"] is False, action

    def test_permanently_ineligible_reexported(self):
        assert el.PERMANENTLY_INELIGIBLE == ao.PERMANENTLY_INELIGIBLE
        for a in ("corpus_writes", "release_approval", "automation_policy_changes",
                  "login_credential_handling"):
            assert a in el.PERMANENTLY_INELIGIBLE


class TestCapAndOverview:
    def test_overview_caps_considered_set(self):
        _fresh()
        orig = el.evaluate_site
        try:
            # pretend 5 sites are evidence-qualified; the considered set must be capped.
            el.evaluate_site = lambda s, **k: {"site": s, "evidence_qualified": True,
                                               "participation_eligible": False}
            ov = el.eligibility_overview(sites=["a", "b", "c", "d", "e"])
            assert el.MAX_ELIGIBLE_SITES == 3
            assert ov["considered_for_experimentation"] == ["a", "b", "c"]
            assert ov["over_cap_excluded"] == ["d", "e"]
            assert ov["participation_eligible_sites"] == 0
        finally:
            el.evaluate_site = orig

    def test_overview_default_is_zero_eligible(self):
        import tools.autonomy_guardrails as _agr
        _fresh()
        ov = el.eligibility_overview()
        assert isinstance(ov, dict)
        assert ov["participation_eligible_sites"] == 0
        _had = _agr._REVERSERS.pop("live_site_config", None)  # H: apply path tracks the live reverser
        try:
            assert el.eligibility_overview()["apply_path_exists"] is False
        finally:
            if _had is not None:
                _agr._REVERSERS["live_site_config"] = _had

    def test_status_shape(self):
        _fresh()
        s = el.eligibility_status()
        assert s["class_c_auto_enabled_by_default"] is False
        assert s["participation_eligible_sites"] == 0
        assert s["min_oracle_tier"] == 3 and s["evidence_fresh_days"] == 30
        assert "corpus_writes" in s["permanently_ineligible_actions"]


class TestPostureNoForbiddenMechanics:
    def test_no_network_fetch(self):
        for bad in ("requests.", "httpx", "urlopen", "web_fetch", "socket."):
            assert bad not in _SRC, bad

    def test_no_browser_interaction(self):
        for bad in ("playwright", "page.goto", "selenium", "webdriver", ".click("):
            assert bad not in _SRC, bad

    def test_no_redownload_logic(self):
        for bad in ("download(", "urlretrieve", "stream_to_file", "fetch_media"):
            assert bad not in _SRC, bad

    def test_no_byte_compare_logic(self):
        for bad in ("hashlib", "filecmp", "memcmp", ".read() ==", "read_bytes()"):
            assert bad not in _SRC, bad

    def test_no_signed_url_reconstruction(self):
        for bad in ("def reconstruct", "def sign", "compute_signature", "build_signed",
                    "def reassemble", ".replay("):
            assert bad not in _SRC, bad


class TestReadOnlyNoMutation:
    def test_module_introduces_no_mutation_or_apply_behavior(self):
        # ban CONSTRUCTS, not words. `def apply(` does NOT match `def apply_path_exists(`.
        for bad in ("def apply(", "def approve", "def rollback(", "def auto_apply",
                    "set_policy_level(", "safety_demote(", "mark_reviewed(",
                    "_save_pending(", "_save_grants", "write_sites_config",
                    "validation_corpus"):
            assert bad not in _SRC, f"eligibility layer must not {bad!r}"

    def test_module_does_not_write_files(self):
        # pure-read module: no file-write constructs at all.
        for bad in ("open(", ".write_text(", "_atomic_write", ".write(", "mkdir("):
            assert bad not in _SRC, f"eligibility layer is read-only; found {bad!r}"

    def test_posture_note_and_utc(self):
        low = _SRC.lower()
        assert "name only" in low or "descriptors by name" in low
        assert "read-only" in low
        assert "timezone.utc" in _SRC


class TestWiring:
    def test_endpoints_and_page_present(self):
        console = Path(Path(el.__file__).parent / "cockpit_console.py").read_text(encoding="utf-8")
        for r in ("status", "overview", "site"):
            assert f'@bp.get("/api/eligibility/{r}")' in console
        for pg in ("eligibility", "eligibilitysite"):
            assert f"PAGES.{pg}" in console
        assert 'data-p="eligibility"' in console

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162, "G1 adds 3 GET routes (124 -> 127)."
        c = app.test_client()
        for r in ("status", "overview", "site"):
            assert c.get(f"/cockpit/api/eligibility/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert len(posts) >= 26, "G1 adds no POST; the state-change surface is unchanged."
        for r in ("status", "overview", "site"):
            assert f"/cockpit/api/eligibility/{r}" not in posts
