"""Consolidation (v3.66.134) — the kind-aware authority model + the generic Class-C apply
harness. Proves: old single-kind grants migrate to live_site_config; grants are per-(site,
kind) and isolated; no kind applies without a registered reverser; unsafe kinds are rejected;
the gate is COMPUTED (D0=A) yet dark by default (grant + Class C auto + tier 3 all required);
reconcile auto-suspends across kinds but never creates or un-suspends; H and v1 behavior are
unchanged through the harness; the Authority view is read-only with no new POST; and
/api/live/* equals /api/authority/* for live_site_config. No pytest fixtures — save/restore
monkeypatch; _fresh() clears governance between tests."""
import contextlib
import datetime as _dt
import json
import os
import shutil
import tempfile

from _cockpit_tasks import remove_test_governance
from tools.cockpit_core import tasks_root
from tools import autonomy_apply as aap
from tools import autonomy_grant as G
from tools import autonomy_oracle as ao
from tools import autonomy_eligibility as el
from tools import autonomy_policy as ap
from tools import autonomy_live as L
from tools import autonomy_staging as S
from tools.cockpit_templates import _site_id

SENT_PW = "SENTINEL_PW_134_must_not_leak"
TARGET = {"url": "https://t134.com/login", "username": "u@x.com", "password": SENT_PW,
          "user_field": "#u", "pass_field": "#p", "submit_btn": ".s",
          "success_url": "t134.com/in", "domain": "t134.com", "output": "cookies/t.json",
          "learned": {"row_selectors": [".old"]}, "scoring": {"w": 1}}
PROPOSED = {"learned": {"row_selectors": [".new"],
                        "_expectations": {"identity_descriptors": ["vidA"],
                                          "rendition_descriptors": ["720p"]}},
            "scoring": {"w": 2}}
SID = _site_id(TARGET)


def _fresh():
    remove_test_governance(tasks_root())


def _future():
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=30)).isoformat()


def _past():
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).isoformat()


def _write_old_grant(site):
    """Write a pre-consolidation single-kind grant `{site: {granted: …}}` (no kind key)."""
    p = ao._grants_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({site: {"granted": True, "granted_by": "legacy",
                                    "granted_at": "2026-01-01T00:00:00+00:00",
                                    "reason": "pre-consolidation", "expires_at": None,
                                    "suspended": False, "suspend_reason": None}}),
                 encoding="utf-8")


@contextlib.contextmanager
def _tier3_classc():
    """Force oracle tier 3 + Class C at auto, so the ONLY remaining variable is the grant."""
    ov, ca = ao.oracle_verdict, ap.can_autonomously
    ao.oracle_verdict = lambda s, *a, **k: {"tier": 3, "tier_name": "t3",
                                            "held_out_count": 2, "hard_failures": []}
    ap.can_autonomously = lambda c: {"allowed": True}
    try:
        yield
    finally:
        ao.oracle_verdict = ov
        ap.can_autonomously = ca


@contextlib.contextmanager
def _cfg(sites):
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


class TestMigration:
    def test_old_single_kind_grant_migrates_to_live_site_config(self):
        _fresh()
        _write_old_grant(SID)
        norm = ao._load_grants()
        assert set(norm[SID].keys()) == {"live_site_config"}      # nested under the kind
        assert norm[SID]["live_site_config"]["granted_by"] == "legacy"
        assert G.is_active(SID) is True                            # default kind = live
        assert G.is_active(SID, "live_site_config") is True

    def test_migration_is_on_write(self):
        _fresh()
        _write_old_grant(SID)
        # the first write after a legacy read persists the nested shape; reconcile under
        # freeze auto-suspends the legacy grant (a real background write)
        ca = ap.can_autonomously
        ap.can_autonomously = lambda c: {"allowed": False, "reason": "frozen"}
        try:
            G.reconcile_grants(by="system")
        finally:
            ap.can_autonomously = ca
        raw = json.loads(ao._grants_path().read_text(encoding="utf-8"))
        assert "live_site_config" in raw[SID] and "granted" not in raw[SID]


class TestPerSiteKind:
    def test_grants_are_per_site_kind(self):
        _fresh()
        G.grant_site(SID, kind="live_site_config", by="op", reason="r")
        assert G.is_active(SID, "live_site_config") is True
        assert G.is_active(SID, "operational_rows") is False      # different kind, no grant

    def test_granting_one_kind_never_grants_another(self):
        _fresh()
        G.grant_site(SID, kind="live_site_config", by="op", reason="r")
        with _tier3_classc():
            assert ao.class_c_site_eligible(SID, "live_site_config")["eligible"] is True
            other = ao.class_c_site_eligible(SID, "operational_rows")
            assert other["eligible"] is False
            assert any("no per-site auto grant" in r for r in other["reasons"])


class TestApplyPathAndUnsafeKinds:
    def test_register_apply_kind_requires_reverser(self):
        try:
            aap.register_apply_kind("smoke_safe_kind", gate=lambda s: False,
                                    current=lambda s: None, proposer=lambda s: None,
                                    applier=lambda s, a: None, reverser=None)
            assert False, "expected ValueError for missing reverser"
        except ValueError:
            pass
        assert not aap.is_registered("smoke_safe_kind")           # nothing stored

    def test_missing_reverser_means_no_apply_path_and_not_eligible(self):
        _fresh()
        G.grant_site(SID, kind="operational_rows", by="op", reason="r")
        assert el.apply_path_exists("operational_rows") is False  # no reverser registered
        with _tier3_classc():
            ev = el.evaluate_site(SID, kind="operational_rows")
            assert ev["participation_eligible"] is False
            assert any("no Class C apply path" in r for r in ev["reasons"])

    def test_unsafe_kinds_rejected(self):
        rv = lambda *a, **k: None
        for bad in ("login_template_changes", "corpus_writes", "credential_blob",
                    "release_approval", "posture_policy_changes"):
            try:
                aap.register_apply_kind(bad, gate=lambda s: False, current=lambda s: None,
                                        proposer=lambda s: None, applier=lambda s, a: None,
                                        reverser=rv)
                assert False, f"expected rejection of unsafe kind {bad!r}"
            except ValueError:
                pass
            assert not aap.is_registered(bad)


class TestGateActivationDarkByDefault:
    def test_dark_by_default_no_grant(self):
        _fresh()
        with _tier3_classc():                                     # tier 3 + Class C auto…
            assert ao.class_c_site_eligible(SID)["eligible"] is False   # …but no grant
        # and with nothing forced at all, also closed
        _fresh()
        assert ao.class_c_site_eligible(SID)["eligible"] is False

    def test_all_three_required(self):
        _fresh()
        G.grant_site(SID, kind="live_site_config", by="op", reason="r")
        with _tier3_classc():
            assert ao.class_c_site_eligible(SID)["eligible"] is True     # grant+tier3+classC
        # remove tier-3 only
        ov = ao.oracle_verdict
        ao.oracle_verdict = lambda s, *a, **k: {"tier": 1, "hard_failures": []}
        ca = ap.can_autonomously
        ap.can_autonomously = lambda c: {"allowed": True}
        try:
            assert ao.class_c_site_eligible(SID)["eligible"] is False    # tier<3 closes it
        finally:
            ao.oracle_verdict = ov; ap.can_autonomously = ca
        # remove Class C auto only
        ao.oracle_verdict = lambda s, *a, **k: {"tier": 3, "hard_failures": []}
        ap.can_autonomously = lambda c: {"allowed": False, "reason": "approve-each"}
        try:
            assert ao.class_c_site_eligible(SID)["eligible"] is False    # not-auto closes it
        finally:
            ao.oracle_verdict = ov; ap.can_autonomously = ca
        # remove grant only
        G.revoke_site(SID, kind="live_site_config", by="op")
        with _tier3_classc():
            assert ao.class_c_site_eligible(SID)["eligible"] is False    # no grant closes it

    def test_gate_is_computed_not_hardcoded(self):
        # the former stub returned eligible:False unconditionally; prove a True is reachable
        _fresh()
        G.grant_site(SID, kind="live_site_config", by="op", reason="r")
        with _tier3_classc():
            assert ao.class_c_site_eligible(SID)["eligible"] is True


class TestSuspendExpire:
    def test_suspended_grant_blocks(self):
        _fresh()
        G.grant_site(SID, kind="live_site_config", by="op", reason="r")
        with _tier3_classc():
            assert ao.class_c_site_eligible(SID)["eligible"] is True
            # auto-suspend via reconcile under freeze, then re-check with good conditions
            ca = ap.can_autonomously
            ap.can_autonomously = lambda c: {"allowed": False, "reason": "frozen"}
            try:
                G.reconcile_grants(by="system")
            finally:
                ap.can_autonomously = ca
            assert G.is_active(SID, "live_site_config") is False
            g = ao.class_c_site_eligible(SID)
            assert g["eligible"] is False
            assert any("suspended" in r for r in g["reasons"])

    def test_expired_grant_blocks(self):
        _fresh()
        G.grant_site(SID, kind="live_site_config", by="op", reason="r", expires_at=_past())
        with _tier3_classc():
            g = ao.class_c_site_eligible(SID)
            assert g["eligible"] is False
            assert any("expired" in r for r in g["reasons"])


class TestReconcileContractionOnly:
    def test_reconcile_auto_suspends_across_kinds(self):
        _fresh()
        G.grant_site(SID, kind="live_site_config", by="op", reason="r")
        G.grant_site(SID, kind="operational_rows", by="op", reason="r")
        ca = ap.can_autonomously
        ap.can_autonomously = lambda c: {"allowed": False, "reason": "frozen"}
        try:
            out = G.reconcile_grants(by="system")
        finally:
            ap.can_autonomously = ca
        assert out["suspended_count"] == 2                        # both kinds suspended
        assert G.is_active(SID, "live_site_config") is False
        assert G.is_active(SID, "operational_rows") is False

    def test_reconcile_never_creates_a_grant(self):
        _fresh()                                                  # empty store
        out = G.reconcile_grants(by="system")
        assert out["suspended_count"] == 0
        assert ao._load_grants() == {}                            # still empty — no creation

    def test_reconcile_never_unsuspends(self):
        _fresh()
        G.grant_site(SID, kind="live_site_config", by="op", reason="r")
        # manually suspend, then reconcile under HEALTHY conditions (tier3, not frozen)
        ca = ap.can_autonomously
        ap.can_autonomously = lambda c: {"allowed": False, "reason": "frozen"}
        try:
            G.reconcile_grants(by="system")                       # -> suspended
        finally:
            ap.can_autonomously = ca
        assert G.is_active(SID, "live_site_config") is False
        with _tier3_classc():
            G.reconcile_grants(by="system")                       # healthy now
        assert G.is_active(SID, "live_site_config") is False      # STILL suspended (no auto-lift)


class TestHAndV1Unchanged:
    def test_h_registered_and_apply_is_reversible(self):
        _fresh()
        assert aap.is_registered("live_site_config")
        assert el.apply_path_exists("live_site_config") is True
        from tools import autonomy_guardrails as agr
        o = {"ev": el.evaluate_site, "pb": L._proposed_block,
             "oc": L._oracle_corroborates, "pv": L._post_apply_validation}
        el.evaluate_site = lambda s, **k: {"participation_eligible": s == SID}
        L._proposed_block = lambda s: (json.loads(json.dumps(PROPOSED)) if s == SID else None)
        L._oracle_corroborates = lambda s: True
        L._post_apply_validation = lambda s, p: {"ok": True, "tier": 3}
        try:
            with _cfg([dict(TARGET)]):
                r = L.maintain_live_config(SID)               # routes through the harness
                assert r["ok"] is True and r.get("change_id")
                assert agr.change_record(r["change_id"]) is not None   # reversible record
                # scope: only learned+scoring in the change; no secret anywhere
                rec = agr.change_record(r["change_id"])
                blob = json.dumps(rec)
                assert SENT_PW not in blob
                assert set((rec.get("after") or {}).keys()) <= {"learned", "scoring"}
        finally:
            el.evaluate_site = o["ev"]; L._proposed_block = o["pb"]
            L._oracle_corroborates = o["oc"]; L._post_apply_validation = o["pv"]

    def test_h_fail_closed_validation_reverts(self):
        _fresh()
        from tools import autonomy_guardrails as agr
        o = {"ev": el.evaluate_site, "pb": L._proposed_block,
             "oc": L._oracle_corroborates, "pv": L._post_apply_validation}
        el.evaluate_site = lambda s, **k: {"participation_eligible": s == SID}
        L._proposed_block = lambda s: json.loads(json.dumps(PROPOSED))
        L._oracle_corroborates = lambda s: True
        L._post_apply_validation = lambda s, p: {"ok": False, "reason": "held-out disagrees"}
        try:
            with _cfg([dict(TARGET)]):
                r = L.maintain_live_config(SID)
                assert r["ok"] is False and r.get("reverted") is True  # validation miss reverted
        finally:
            el.evaluate_site = o["ev"]; L._proposed_block = o["pb"]
            L._oracle_corroborates = o["oc"]; L._post_apply_validation = o["pv"]

    def test_v1_staging_uses_separate_gate_not_participation(self):
        _fresh()
        assert aap.is_registered("staging_json")
        # participation_eligible is False (no grant) — staging must NOT depend on it
        assert el.evaluate_site(SID, kind="staging_json")["participation_eligible"] is False
        og, ol = S.staging_eligible, S._live_block
        S.staging_eligible = lambda s: {"ok": True, "site": s, "reasons": [],
                                        "participation_eligible": False}
        S._live_block = lambda s: dict(TARGET) if s == SID else None
        try:
            r = S.maintain_staged_candidate(SID)
            assert r.get("ok") is True and r.get("change_id")     # staged despite participation False
            assert S._read_candidate(SID).get("behavioral") is not None
        finally:
            S.staging_eligible = og; S._live_block = ol


class TestAuthorityReadOnlyAndEquivalence:
    def _app(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        return app

    def test_authority_view_read_only_no_new_post(self):
        _fresh()
        app = self._app()
        c = app.test_client()
        for r in ("status", "grants", "pending", "kinds", "change"):
            assert c.get(f"/cockpit/api/authority/{r}").status_code == 200
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert not any("/api/authority/" in r for r in posts)     # no authority POST
        assert len(posts) >= 26                                   # POST count unchanged

    def test_live_equals_authority_grants_nonempty(self):
        _fresh()
        G.grant_site(SID, kind="live_site_config", by="op", reason="r")
        app = self._app(); c = app.test_client()
        lg = json.loads(c.get("/cockpit/api/live/grants").data)["grants"]
        ag = [r for r in json.loads(c.get("/cockpit/api/authority/grants").data)["grants"]
              if r["kind"] == "live_site_config"]
        assert lg and lg == ag                                    # non-empty AND equal

    def test_live_equals_authority_pending_and_change(self):
        _fresh()
        from tools import autonomy_guardrails as agr
        o = {"ev": el.evaluate_site, "pb": L._proposed_block,
             "oc": L._oracle_corroborates, "pv": L._post_apply_validation}
        el.evaluate_site = lambda s, **k: {"participation_eligible": s == SID}
        L._proposed_block = lambda s: json.loads(json.dumps(PROPOSED))
        L._oracle_corroborates = lambda s: True
        L._post_apply_validation = lambda s, p: {"ok": True}
        try:
            with _cfg([dict(TARGET)]):
                r = L.maintain_live_config(SID)
                cid = r["change_id"]
                app = self._app(); c = app.test_client()
                lp = json.loads(c.get("/cockpit/api/live/pending").data)["pending"]
                ap_ = [x for x in json.loads(c.get("/cockpit/api/authority/pending").data)["pending"]
                       if x["kind"] == "live_site_config"]
                assert lp == ap_ and len(lp) == 1
                lc = json.loads(c.get(f"/cockpit/api/live/change?id={cid}").data)
                acc = json.loads(c.get(f"/cockpit/api/authority/change?id={cid}").data)
                assert lc["exists"] and acc["exists"]
                assert lc["before"] == acc["before"] and lc["after"] == acc["after"]
        finally:
            el.evaluate_site = o["ev"]; L._proposed_block = o["pb"]
            L._oracle_corroborates = o["oc"]; L._post_apply_validation = o["pv"]


class TestHarnessHasNoGrantWriter:
    def test_harness_never_writes_grants(self):
        src = open(aap.__file__, encoding="utf-8").read()
        for bad in ("grant_site", "revoke_site", "site_auto_grants", "_grants_path("):
            assert bad not in src, f"harness must not write grants: found {bad!r}"
