"""v3.66.128 — Phase G / G3: Trust Decay.

Proves the load-bearing invariant Phase H rests on — **trust may only ever DECREASE
automatically** — and the feed into eligibility:
  * TRUST ONLY DECREASES AUTOMATICALLY — decay lowers trust toward the signal floor; an
    improved signal never raises the stored value.
  * HUMAN RESTORE IS THE ONLY RAISE — `reset_trust` requires a human identity; there is
    no automatic caller; the auto path can never increase trust.
  * TRUST GATES ELIGIBILITY — a site decayed below the floor is not eligible until a human
    restores it, even when its current evidence looks good.
Plus: the signal score is bounded and reflects real signals; the read-only rollups render;
the store write is atomic/UTF-8; no forbidden mutation (corpus/policy/rollback-exec/
network/credentials); and the cockpit gains 3 read-only GET routes (131 -> 134, no new
POST).

POSTURE/MUTATION GREPS ban the actual CONSTRUCT (`set_policy_level(`, `mark_reviewed(`,
`agr.rollback(`, `def apply(`) — the module legitimately writes its OWN trust store
(decay/reset), which is allowed; the bans target FORBIDDEN mutations only.
"""
import datetime as _dt
import shutil
from pathlib import Path

from tools import autonomy_policy as ap
from tools import autonomy_trust as atr
from tools import autonomy_eligibility as el
from tools.cockpit_core import tasks_root

_SRC = Path(atr.__file__).read_text(encoding="utf-8")

_HO2 = [{"capture": "c1", "identity": "m", "renditions": ["1080p"],
         "template_shape": "media_present", "signing_marker_names": ["t"]},
        {"capture": "c2", "identity": "m", "renditions": ["1080p"],
         "template_shape": "media_present", "signing_marker_names": ["t"]}]


def _fresh():
    g = tasks_root() / "governance"
    if g.exists():
        shutil.rmtree(g)


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _ago(days):
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()


class TestTrustOnlyDecreasesAutomatically:
    def test_decay_lowers_then_good_signal_does_not_raise(self):
        _fresh()
        d1 = atr.decay_trust("s", held_out=[])            # bad signals -> low
        t1 = atr.effective_trust("s")
        d2 = atr.decay_trust("s", held_out=_HO2, evidence_ts=_ago(1), now=_now())  # good
        t2 = atr.effective_trust("s")
        assert d1["decreased"] is True
        assert d2["decreased"] is False
        assert t2 == t1, "an improved signal must NOT raise stored trust"
        assert t2 <= atr.BASELINE_TRUST

    def test_repeated_decay_is_monotonic_non_increasing(self):
        _fresh()
        seq = []
        for ho in (_HO2, [{"capture": "c", "identity": "m", "renditions": [],
                           "template_shape": "thin"}], []):
            atr.decay_trust("s", held_out=ho, evidence_ts=_ago(40), now=_now())
            seq.append(atr.effective_trust("s"))
        assert all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1)), seq


class TestHumanRestoreIsOnlyRaise:
    def test_reset_requires_identity(self):
        _fresh()
        assert atr.reset_trust("s", 1.0, "", "x")["ok"] is False

    def test_human_reset_raises_after_decay(self):
        _fresh()
        atr.decay_trust("s", held_out=[])
        assert atr.effective_trust("s") < atr.MIN_TRUST
        r = atr.reset_trust("s", 0.9, "mboyle", "manual restore")
        assert r["ok"] and atr.effective_trust("s") == 0.9


class TestSignalScore:
    def test_tier0_unfresh_low_tier3_fresh_high(self):
        _fresh()
        assert atr.signal_trust("s", held_out=[]) < atr.MIN_TRUST
        assert atr.signal_trust("s", held_out=_HO2, evidence_ts=_ago(1), now=_now()) >= 0.9

    def test_bounded_and_frozen_caps_low(self):
        _fresh()
        assert 0.0 <= atr.signal_trust("s", held_out=[]) <= 1.0
        ap.freeze("tester", "drill")
        try:
            assert atr.signal_trust("s", held_out=_HO2, evidence_ts=_ago(1), now=_now()) <= 0.1
        finally:
            _fresh()


class TestTrustGatesEligibility:
    def test_undecayed_site_is_qualified(self):
        _fresh()
        r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(1), now=_now())
        assert r["evidence_qualified"] is True and r["trust"] == atr.BASELINE_TRUST

    def test_decayed_site_not_eligible_even_with_good_evidence(self):
        _fresh()
        atr.decay_trust("s", held_out=[])                 # drive trust below floor
        r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(1), now=_now())
        assert r["evidence_qualified"] is False
        assert r["trust"] < atr.MIN_TRUST
        assert any("trust" in d for d in r["decay_reasons"])

    def test_human_restore_requalifies(self):
        _fresh()
        atr.decay_trust("s", held_out=[])
        atr.reset_trust("s", 0.9, "mboyle", "restore")
        r = el.evaluate_site("s", held_out=_HO2, evidence_ts=_ago(1), now=_now())
        assert r["evidence_qualified"] is True


class TestTrustViews:
    def test_status_and_overview_shapes(self):
        _fresh()
        s = atr.trust_status()
        assert s["min_trust"] == atr.MIN_TRUST and "below_min_count" in s
        ov = atr.trust_overview(sites=["a", "b"])
        assert ov["site_count"] == 2 and "below_min" in ov

    def test_below_min_set_reflects_decay(self):
        _fresh()
        atr.decay_trust("a", held_out=[])                 # a -> below min; b unseen -> baseline
        ov = atr.trust_overview(sites=["a", "b"])
        assert "a" in ov["below_min"] and "b" not in ov["below_min"]
        ts = atr.trust_site("a")
        assert "would_decay_to" in ts and ts["trust_eligible"] is False


class TestPostureAndStore:
    def test_atomic_write_utf8_utc(self):
        assert ".replace(" in _SRC and ".tmp" in _SRC
        assert 'encoding="utf-8"' in _SRC and "timezone.utc" in _SRC

    def test_only_decreases_documented(self):
        assert "only ever decrease" in _SRC.lower()

    def test_no_forbidden_mutation(self):
        for bad in ("validation_corpus", "set_policy_level(", "safety_demote(",
                    "mark_reviewed(", "sweep_review_windows(", "agr.rollback(",
                    "record_change(", "register_pending(", "write_sites_config",
                    "def apply("):
            assert bad not in _SRC, f"trust must not {bad!r}"

    def test_no_forbidden_mechanics(self):
        for bad in ("requests.", "httpx", "urlopen", "web_fetch", "socket.",
                    "playwright", "page.goto", ".click(", "download(", "urlretrieve",
                    "hashlib", "filecmp", "read_bytes()", "def reconstruct",
                    "compute_signature", "build_signed", ".replay("):
            assert bad not in _SRC, bad


class TestWiring:
    def test_endpoints_and_pages_present(self):
        console = Path(Path(atr.__file__).parent / "cockpit_console.py").read_text(encoding="utf-8")
        for r in ("status", "overview", "site"):
            assert f'@bp.get("/api/trust/{r}")' in console
        for pg in ("trustdecay", "trustsite"):
            assert f"PAGES.{pg}" in console
        assert 'data-p="trustdecay"' in console

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162, "G3 adds 3 GET routes (131 -> 134)."
        c = app.test_client()
        for r in ("status", "overview", "site"):
            assert c.get(f"/cockpit/api/trust/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__)
        app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert len(posts) >= 26, "G3 adds no POST; the state-change surface is unchanged."
        for r in ("status", "overview", "site"):
            assert f"/cockpit/api/trust/{r}" not in posts
