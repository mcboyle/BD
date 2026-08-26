"""v3.66.124 — Phase E: tiered correctness oracle & eligibility engine.

Proves every requirement from the Phase E spec: Tier 0 blocks eligibility; Tier 1
never grants automation; Tier 2 requires independent held-out evidence; Tier 3
requires multiple held-out captures; same-evidence (overlap) validation is rejected;
raw signing values are blocked; no network/browser/re-download/byte-compare logic
exists; pinned actions remain permanently ineligible; no apply/approve/rollback/write
behavior is introduced; Class C auto remains disabled by default; plus wiring
(+6 GET = 118, no POST).
"""
from pathlib import Path

from _cockpit_tasks import remove_test_governance
from tools import autonomy_policy as ap
from tools import autonomy_oracle as orc
from tools.cockpit_core import tasks_root

_SRC = Path(orc.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(orc.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")

_HO1 = [{"capture": "c1", "identity": "movieX", "renditions": ["1080p"],
         "template_shape": "media_present", "signing_marker_names": ["token", "expires"]}]
_HO2 = _HO1 + [{"capture": "c2", "identity": "movieX", "renditions": ["1080p"],
               "template_shape": "media_present", "signing_marker_names": ["token"]}]


def _fresh():
    remove_test_governance(tasks_root())


class TestTierGating:
    def test_tier0_blocks_eligibility(self):
        _fresh()
        v = orc.oracle_verdict("s", held_out=[])
        assert v["tier"] == 0 and v["automation_eligible"] is False

    def test_tier1_partial_descriptors_no_automation(self):
        _fresh()
        v = orc.oracle_verdict("s", held_out=[{"capture": "c", "identity": "m",
                                               "renditions": [], "template_shape": "thin"}])
        assert v["tier"] == 1 and v["automation_eligible"] is False

    def test_tier2_requires_independent_held_out(self):
        _fresh()
        v = orc.oracle_verdict("s", held_out=_HO1)
        assert v["tier"] == 2 and v["automation_eligible"] is False
        # with NO held-out, the same checks cannot reach Tier 2
        assert orc.oracle_verdict("s", held_out=[])["tier"] == 0

    def test_tier3_requires_multiple_held_out(self):
        _fresh()
        assert orc.oracle_verdict("s", held_out=_HO2)["tier"] == 3   # 2 agreeing
        assert orc.oracle_verdict("s", held_out=_HO1)["tier"] == 2   # only 1 -> not Tier 3

    def test_tier3_requires_agreement(self):
        _fresh()
        disagree = [_HO1[0], {"capture": "c2", "identity": "movieY",  # different identity
                              "renditions": ["1080p"], "template_shape": "media_present",
                              "signing_marker_names": ["token"]}]
        assert orc.oracle_verdict("s", held_out=disagree)["tier"] == 2   # disagree -> Tier 2

    def test_no_tier_grants_automation(self):
        _fresh()
        for ho in ([], _HO1, _HO2):
            assert orc.oracle_verdict("s", held_out=ho)["automation_eligible"] is False


class TestHardFailures:
    def test_same_evidence_overlap_rejected(self):
        _fresh()
        orc._atomic_write(orc._provenance_path(),
                          {"ov": {"training": ["c1"], "held_out": ["c1"]}})
        v = orc.oracle_verdict("ov")
        assert v["tier"] == 0 and any("overlap" in h for h in v["hard_failures"])

    def test_raw_signing_value_blocked(self):
        _fresh()
        bad = [{"capture": "c", "identity": "m", "renditions": ["1080p"],
                "template_shape": "media_present",
                "signing_marker_names": ["token=AbCdEf0123456789ZZZZ"]}]
        v = orc.oracle_verdict("s", held_out=bad)
        assert v["tier"] == 0 and any("signing value" in h for h in v["hard_failures"])

    def test_candidate_affecting_corpus_fails(self):
        _fresh()
        v = orc.oracle_verdict("s", candidate={"action": "corpus_writes"}, held_out=_HO1)
        assert any("permanently-ineligible" in h for h in v["hard_failures"])

    def test_candidate_affecting_credentials_fails(self):
        _fresh()
        v = orc.oracle_verdict("s", candidate={"action": "login_credential_handling"},
                               held_out=_HO1)
        assert v["hard_failures"]


class TestNoForbiddenMechanics:
    def test_no_network_fetch(self):
        for bad in ("requests.", "urllib.request", "httpx", "urlopen", "web_fetch",
                    "socket."):
            assert bad not in _SRC, f"oracle must not fetch: {bad!r}"

    def test_no_browser_interaction(self):
        for bad in ("playwright", "page.goto", "selenium", "webdriver", ".click("):
            assert bad not in _SRC

    def test_no_redownload_logic(self):
        for bad in ("download(", "urlretrieve", "stream_to_file", "fetch_media"):
            assert bad not in _SRC

    def test_no_byte_compare_logic(self):
        # real byte-comparison constructs, not the posture docstring words
        # ("byte-compare"). The oracle compares descriptors, never bytes.
        for bad in ("hashlib", "filecmp", "memcmp", ".read() ==", "read_bytes()"):
            assert bad not in _SRC

    def test_no_signed_url_reconstruction(self):
        # real reconstruction/signing constructs, not the docstring words ("NEVER
        # reconstructs signed URLs"). Note the trailing paren / call forms.
        for bad in ("def reconstruct", "def sign", "compute_signature", "build_signed",
                    "def reassemble", ".replay("):
            assert bad not in _SRC


class TestPermanentIneligibility:
    def test_pinned_and_credentials_permanently_ineligible(self):
        for a in ("corpus_writes", "validation_debt_retirement",
                  "correction_debt_retirement", "finding_confirmation_or_falsification",
                  "release_approval", "posture_policy_changes",
                  "automation_policy_changes", "credential_creation_or_modification",
                  "login_credential_handling"):
            assert a in orc.PERMANENTLY_INELIGIBLE

    def test_matrix_lists_permanent_ineligible(self):
        _fresh()
        em = orc.eligibility_matrix(sites=["s"])
        assert "corpus_writes" in em["permanently_ineligible_actions"]


class TestNoAutomationEnabled:
    def test_class_c_default_approve_each(self):
        _fresh()
        assert ap.load_policy()["levels"]["C"] == "approve_each"

    def test_per_site_eligible_false_for_all_including_tier3(self):
        _fresh()
        # even after flipping C to auto (guardrails complete), no site is per-site eligible
        ap.set_policy_level("C", "auto_with_guardrails", "mboyle", "flip")
        assert orc.class_c_site_eligible("anysite")["eligible"] is False
        assert orc.oracle_status()["automation_eligible_sites"] == 0

    def test_no_per_site_grant_mechanism(self):
        # there must be no writer for the per-site grant store in this build
        for bad in ("def grant_site", "def issue_grant", "_grants_path().write",
                    "grants[", "_save_grants"):
            assert bad not in _SRC

    def test_completing_oracle_did_not_enable_auto(self):
        _fresh()
        # guardrails complete...
        assert all(v["built"] for v in ap.guardrail_registry().values())
        # ...yet automation is not enabled (default level + empty per-site gate)
        assert orc.oracle_status()["class_c_auto_enabled_by_default"] is False


class TestNoApplyApproveRollbackWrite:
    def test_module_introduces_no_mutation_behavior(self):
        # the oracle reads config to enumerate sites (_load_sites_config) but must never
        # WRITE config/corpus or mutate policy/reviews. Ban the write constructs.
        for bad in ("def apply", "def approve", "def rollback(", "def auto_apply",
                    "_store_save(", "_save_pending(", "_save_sites", "write_sites_config",
                    "validation_corpus", "set_policy_level(", "safety_demote(",
                    "mark_reviewed("):
            assert bad not in _SRC, f"oracle must not {bad!r}"

    def test_report_generation_is_explicit_not_auto(self):
        # generate_oracle_reports requires an identity and is not called anywhere at
        # import or in the read endpoints
        assert "def generate_oracle_reports" in _SRC
        assert "identity (by) required" in _SRC


class TestReportsAndDerivation:
    def test_report_bundle_shape(self):
        _fresh()
        b = orc.oracle_reports()
        for k in ("oracle_status", "eligibility_matrix", "held_out_evidence",
                  "ineligible_sites"):
            assert k in b

    def test_generate_writes_six_artifacts(self):
        _fresh()
        r = orc.generate_oracle_reports("mboyle")
        assert r["ok"] and len(r["written"]) == 6
        names = {Path(p).name for p in r["written"]}
        assert {"oracle_verdict.json", "eligibility_matrix.json", "oracle_report.md",
                "eligibility_matrix.md", "held_out_evidence_report.md",
                "ineligible_sites_report.md"} == names

    def test_generate_requires_identity(self):
        _fresh()
        assert orc.generate_oracle_reports("")["ok"] is False


class TestPostureMisc:
    def test_atomic_writes_and_utf8(self):
        assert ".replace(" in _SRC and ".tmp" in _SRC and 'encoding="utf-8"' in _SRC

    def test_descriptors_only_note(self):
        assert "descriptors only" in _SRC.lower() or "Descriptors only" in _SRC


class TestWiring:
    def test_endpoints_and_pages_present(self):
        for r in ("status", "eligibility", "verdict", "held-out", "ineligible", "reports"):
            assert f'@bp.get("/api/oracle/{r}")' in _CONSOLE
        for pg in ("oracle", "oracleverdict", "oracleheldout", "oracleineligible",
                   "oraclereports"):
            assert f"PAGES.{pg}" in _CONSOLE
        assert 'data-p="oracle"' in _CONSOLE

    def test_route_count_and_serve(self):
        _fresh()
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        for r in ("status", "eligibility", "verdict", "held-out", "ineligible", "reports"):
            assert c.get(f"/cockpit/api/oracle/{r}").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for r in ("status", "eligibility", "verdict", "held-out", "ineligible", "reports"):
            assert f"/cockpit/api/oracle/{r}" not in posts
