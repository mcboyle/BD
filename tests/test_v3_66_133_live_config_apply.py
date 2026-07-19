"""H — live site-config apply (learned + scoring). Proves the narrow scope, the grant gate,
fail-closed review, the reverser, the authority invariant (auto-suspend only), read-only
wiring, and build-dark inertness. No pytest fixtures — save/restore monkeypatch; _fresh()
clears governance between tests."""
import contextlib
import datetime as _dt
import json
import os
import shutil
import tempfile

from tools.cockpit_core import tasks_root
from tools import autonomy_live as L
from tools import autonomy_grant as G
from tools import autonomy_guardrails as agr
from tools import autonomy_eligibility as el
from tools import autonomy_oracle as ao
from tools import autonomy_policy as ap
from tools import cockpit_core as cc
from tools.cockpit_templates import _site_id

SENTINEL_PW = "SENTINEL_PW_must_not_leak"
SENTINEL_USER = "SENTINEL_USER_must_not_leak"

TARGET = {"url": "https://t.com/login", "username": SENTINEL_USER, "password": SENTINEL_PW,
          "user_field": "#u", "pass_field": "#p", "submit_btn": ".s",
          "success_url": "t.com/in", "domain": "t.com", "output": "cookies/t.json",
          "wait": 4, "learned": {"row_selectors": [".old"]}, "scoring": {"w": 1}}
BYSTANDER = {"url": "https://b.com", "username": "b_user", "password": "b_pw",
             "domain": "b.com", "learned": {"row_selectors": [".b"]}, "scoring": {"w": 9}}
PROPOSED = {"learned": {"row_selectors": [".new"],
                        "_expectations": {"identity_descriptors": ["vidA"],
                                          "rendition_descriptors": ["720p"]}},
            "scoring": {"w": 2}}

SID = _site_id(TARGET)
BID = _site_id(BYSTANDER)


def _fresh():
    g = tasks_root() / "governance"
    if g.exists():
        shutil.rmtree(g)


@contextlib.contextmanager
def cfg_file(sites):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "sites_config.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(sites, f)
    os.environ["BD_SITES_CONFIG_PATH"] = p
    try:
        yield p
    finally:
        os.environ.pop("BD_SITES_CONFIG_PATH", None)
        shutil.rmtree(d, ignore_errors=True)


def _force(site, *, valid=True):
    """Force participation + a proposal + corroboration + validation, so the only thing
    under test is the apply/scope/fail-closed behavior."""
    o = {"ev": el.evaluate_site, "pb": L._proposed_block,
         "oc": L._oracle_corroborates, "pv": L._post_apply_validation}
    el.evaluate_site = lambda s, **k: {"participation_eligible": s == site,
                                       "decay_reasons": None, "oracle_tier": 3, "trust": 0.9}
    L._proposed_block = lambda s: (json.loads(json.dumps(PROPOSED)) if s == site else None)
    L._oracle_corroborates = lambda s: True
    L._post_apply_validation = lambda s, p: ({"ok": True, "tier": 3} if valid
                                             else {"ok": False, "reason": "mismatch"})
    return o


def _unforce(o):
    el.evaluate_site = o["ev"]; L._proposed_block = o["pb"]
    L._oracle_corroborates = o["oc"]; L._post_apply_validation = o["pv"]


def _read(p):
    return json.loads(open(p, encoding="utf-8").read())


# ── scope + secrets ───────────────────────────────────────────────────────────

class TestScopeAndSecrets:
    def test_only_learned_and_scoring_change_others_byte_identical(self):
        _fresh()
        with cfg_file([dict(TARGET), dict(BYSTANDER)]) as p:
            o = _force(SID)
            try:
                before_bystander = dict(_read(p)[1])
                r = L.maintain_live_config(SID)
                assert r["ok"] is True and not r.get("skipped")
                t = _read(p)[0]
                # learned + scoring changed
                assert t["learned"]["row_selectors"] == [".new"]
                assert t["scoring"]["w"] == 2
                # every OTHER key on the target byte-identical
                for k in ("url", "username", "password", "user_field", "pass_field",
                          "submit_btn", "success_url", "domain", "output", "wait"):
                    assert t[k] == TARGET[k], k
                # the bystander site fully byte-identical
                assert _read(p)[1] == before_bystander
            finally:
                _unforce(o)

    def test_credentials_never_in_change_record_or_backup(self):
        _fresh()
        with cfg_file([dict(TARGET), dict(BYSTANDER)]) as p:
            o = _force(SID)
            try:
                r = L.maintain_live_config(SID)
                rec = agr.change_record(r["change_id"])
                blob = json.dumps(rec)
                assert SENTINEL_PW not in blob and SENTINEL_USER not in blob
                assert sorted(rec["before"].keys()) == ["learned", "scoring"]
                assert sorted(rec["after"].keys()) == ["learned", "scoring"]
                # backup is credential-redacted
                btxt = open(r["backup"], encoding="utf-8").read()
                assert SENTINEL_PW not in btxt and SENTINEL_USER not in btxt
                bjson = json.loads(btxt)[0]
                assert "learned" in bjson and "user_field" in bjson  # non-secret kept
                assert "password" not in bjson and "username" not in bjson
            finally:
                _unforce(o)

    def test_backup_keeps_last_ten(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            for _ in range(13):
                L._backup_sites_config()
            bdir = L._backup_dir()
            assert len(list(bdir.glob("sites_config.*.json"))) == L._BACKUP_KEEP == 10


# ── grant gate ──────────────────────────────────────────────────────────────

class TestGrantGate:
    def _gate_on_grant(self):
        # participation tracks the REAL grant; proposal/corroborate/validation pass.
        o = {"ev": el.evaluate_site, "pb": L._proposed_block,
             "oc": L._oracle_corroborates, "pv": L._post_apply_validation}
        el.evaluate_site = lambda s, **k: {"participation_eligible": G.is_active(s),
                                           "decay_reasons": None}
        L._proposed_block = lambda s: json.loads(json.dumps(PROPOSED))
        L._oracle_corroborates = lambda s: True
        L._post_apply_validation = lambda s, p: {"ok": True}
        return o

    def test_no_grant_no_apply(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = self._gate_on_grant()
            try:
                r = L.maintain_live_config(SID)
                assert r.get("skipped") is True
                assert _read(p)[0]["learned"]["row_selectors"] == [".old"]  # untouched
            finally:
                _unforce(o)

    def test_suspended_grant_no_apply(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = self._gate_on_grant()
            try:
                G.grant_site(SID, by="op", reason="t")
                assert G.is_active(SID) is True
                # auto-suspend the granted site (frozen), then it must not apply
                _ap = ap.can_autonomously
                ap.can_autonomously = lambda c: {"allowed": False, "reason": "frozen"}
                try:
                    G.reconcile_grants()
                finally:
                    ap.can_autonomously = _ap
                assert G.is_active(SID) is False
                r = L.maintain_live_config(SID)
                assert r.get("skipped") is True
                assert _read(p)[0]["learned"]["row_selectors"] == [".old"]
            finally:
                _unforce(o)

    def test_expired_grant_no_apply(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = self._gate_on_grant()
            try:
                past = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).isoformat()
                G.grant_site(SID, by="op", reason="t", expires_at=past)
                G.reconcile_grants()
                assert G.is_active(SID) is False  # expired -> suspended
                r = L.maintain_live_config(SID)
                assert r.get("skipped") is True
            finally:
                _unforce(o)

    def test_freeze_blocks_applies(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = self._gate_on_grant()
            try:
                G.grant_site(SID, by="op", reason="t")
                _ap = ap.can_autonomously
                ap.can_autonomously = lambda c: {"allowed": False, "reason": "frozen"}
                try:
                    G.reconcile_grants()           # freeze contracts authority
                finally:
                    ap.can_autonomously = _ap
                assert G.is_active(SID) is False
                assert L.maintain_live_config(SID).get("skipped") is True
            finally:
                _unforce(o)


# ── fail-closed review ────────────────────────────────────────────────────────

class TestFailClosed:
    def test_validation_failure_rolls_back(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = _force(SID, valid=False)
            try:
                r = L.maintain_live_config(SID)
                assert r.get("reverted") is True
                assert _read(p)[0]["learned"]["row_selectors"] == [".old"]
            finally:
                _unforce(o)

    def test_reject_rolls_back_immediately(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = _force(SID)
            try:
                cid = L.maintain_live_config(SID)["change_id"]
                cc.review_decide(cid, "reject", "no")
                assert _read(p)[0]["learned"]["row_selectors"] == [".old"]
            finally:
                _unforce(o)

    def test_accept_keeps_change_live(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = _force(SID)
            try:
                cid = L.maintain_live_config(SID)["change_id"]
                cc.review_decide(cid, "accept", "ok")
                t = _read(p)[0]
                assert t["learned"]["row_selectors"] == [".new"]   # stays live
                assert t["password"] == SENTINEL_PW                # creds intact
            finally:
                _unforce(o)

    def test_silence_rolls_back_after_deadline(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = _force(SID)
            try:
                cid = L.maintain_live_config(SID)["change_id"]
                d = agr._load_pending()
                d["pending"][cid]["deadline"] = (
                    _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)).isoformat()
                agr._save_pending(d)
                agr.sweep_review_windows("system")
                assert _read(p)[0]["learned"]["row_selectors"] == [".old"]  # reverted
            finally:
                _unforce(o)


# ── reverser ──────────────────────────────────────────────────────────────────

class TestReverser:
    def test_rollback_restores_prior_live_block(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = _force(SID)
            try:
                cid = L.maintain_live_config(SID)["change_id"]
                assert _read(p)[0]["learned"]["row_selectors"] == [".new"]
                agr.rollback(cid, "op")
                t = _read(p)[0]
                assert t["learned"]["row_selectors"] == [".old"] and t["scoring"]["w"] == 1
                assert t["password"] == SENTINEL_PW
            finally:
                _unforce(o)

    def test_rollback_idempotent(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = _force(SID)
            try:
                cid = L.maintain_live_config(SID)["change_id"]
                agr.rollback(cid, "op")
                again = agr.rollback(cid, "op")
                assert again.get("already_rolled_back") is True or again.get("ok") is True
                assert _read(p)[0]["learned"]["row_selectors"] == [".old"]
            finally:
                _unforce(o)

    def test_apply_path_exists_tracks_registry(self):
        had = agr._REVERSERS.pop(L.LIVE_TARGET_KIND, None)
        try:
            assert el.apply_path_exists() is False
            agr.register_reverser(L.LIVE_TARGET_KIND, L._restore_live_block)
            assert el.apply_path_exists() is True
        finally:
            if had is not None:
                agr._REVERSERS[L.LIVE_TARGET_KIND] = had


# ── authority invariant (auto-suspend only; never auto-grant) ────────────────

class TestAuthorityInvariant:
    def test_reconcile_never_creates_a_grant(self):
        _fresh()
        before = G.grant_overview()["count"]
        G.reconcile_grants()
        assert G.grant_overview()["count"] == before == 0

    def test_reconcile_never_unsuspends(self):
        _fresh()
        G.grant_site(SID, by="op", reason="t")
        # suspend via freeze
        _ap = ap.can_autonomously
        ap.can_autonomously = lambda c: {"allowed": False, "reason": "frozen"}
        try:
            G.reconcile_grants()
        finally:
            ap.can_autonomously = _ap
        assert G.is_active(SID) is False
        # a normal reconcile must NOT lift the suspension
        G.reconcile_grants()
        assert G.is_active(SID) is False

    def test_grant_and_unsuspend_are_human_only(self):
        # autonomy_live (the autonomous apply path) contains NO grant-store writer.
        src = open(L.__file__, encoding="utf-8").read()
        for forbidden in ("grant_site(", "revoke_site(", "unsuspend_site(", "_save_pending"):
            assert forbidden not in src, forbidden
        # un-suspend is human-only and lifts the suspension
        _fresh()
        G.grant_site(SID, by="op", reason="t")
        _ap = ap.can_autonomously
        ap.can_autonomously = lambda c: {"allowed": False, "reason": "frozen"}
        try:
            G.reconcile_grants()
        finally:
            ap.can_autonomously = _ap
        assert G.is_active(SID) is False
        G.unsuspend_site(SID, by="op")
        assert G.is_active(SID) is True

    def test_atomic_write_uses_tmp_and_replace(self):
        src = open(L.__file__, encoding="utf-8").read()
        assert "os.replace(" in src and '.suffix + ".tmp"' in src


# ── wiring (read-only; no new POST) + build-dark ─────────────────────────────

class TestWiringAndDark:
    def test_route_count_and_live_routes_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162  # +1 v3.66.250: /cockpit/api/captures/pick (element-pick bridge)
        c = app.test_client()
        for r in ("status", "grants", "pending", "change"):
            assert c.get(f"/cockpit/api/live/{r}").status_code == 200

    def test_no_new_post_routes(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for r in ("status", "grants", "pending", "change"):
            assert f"/cockpit/api/live/{r}" not in posts

    def test_cockpit_pages_present(self):
        src = open(cc.__file__.replace("cockpit_core", "cockpit_console"),
                   encoding="utf-8").read()
        assert "PAGES.authority" in src and "PAGES.authoritychange" in src
        assert 'data-p="authority"' in src

    def test_build_dark_applies_nothing(self):
        _fresh()
        with cfg_file([dict(TARGET), dict(BYSTANDER)]) as p:
            # real gate: empty oracle + no grant -> participation False everywhere
            res = L.maintain_all_live(sites=[SID, BID])
            assert res["applied_count"] == 0
            assert _read(p)[0]["learned"]["row_selectors"] == [".old"]
            assert _read(p)[1]["learned"]["row_selectors"] == [".b"]

    def test_no_synthesis_proposal_skips(self):
        _fresh()
        with cfg_file([dict(TARGET)]) as p:
            o = {"ev": el.evaluate_site, "pb": L._proposed_block}
            el.evaluate_site = lambda s, **k: {"participation_eligible": True, "decay_reasons": None}
            L._proposed_block = lambda s: None      # synthesis produced nothing
            try:
                assert L.maintain_live_config(SID).get("skipped") is True
            finally:
                el.evaluate_site = o["ev"]; L._proposed_block = o["pb"]
