"""Row 357 -- mutation anchors do not freeze values that are re-derived.

Syntax cannot tell whether ``TIMEOUT = 149`` is fixed policy or the rounded
result of a measurement.  This gate therefore does not pretend that it can.
It combines an immutable, completely audited adoption census with narrow
producer records for the values proven to be derived.  A new value-bearing
anchor outside those two populations is UNKNOWN, never silently OK.

The adoption Git tree is deliberately immutable rather than a list authors can
append to.  It names the tree this gate SHIPS into, not the tree it was drafted
against; the single advance from draft to ship is itself measured, by
``test_the_adoption_pin_advanced_only_by_audited_addition``, which refuses any
advance that is not pure addition or that would absorb an anchor over a
registered derived value.  A legitimate new fixed literal can be admitted only
through the reasoned stable-value exception registry, whose exact size is
separately ratcheted.  The registry is empty at adoption.

Honest limit: no text-only gate can see a future tool begin deriving an
unchanged, previously fixed value if neither the anchor nor its source site
changes.  Such a semantic change must add producer evidence here during the
producer's review.  The value-bearing rule is intentionally conservative for
numbers, booleans, quoted values, assignments/comparisons, versions, digests,
and spelled counts; an unrecognised value format may look structural until
that producer is registered.  Diverse alternate-value probes catch literal and
small-enumeration regexes, but finite samples cannot prove a regex against
every possible future value.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import subprocess
import tarfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
# ADOPTION IS THE TREE THIS GATE SHIPS INTO, and it is frozen from then on.
# Moving this pin absorbs a whole population without a per-anchor reason, so it
# is legitimate exactly once: while row 357 is still unmerged and main is still
# moving underneath it. `72ae230a` (merged 4d636df) was the tree when this file
# was first drafted; rows 356, 362 and 363 merged afterwards and added 7 spec
# files / 77 anchors that no producer re-derives -- audited one by one before
# this pin advanced, and absorbed here rather than laundered through 77
# reasonless stable-value exceptions. AFTER THIS CUT MERGES, ADVANCING THIS PIN
# IS LAUNDERING: a new anchor earns its class from a producer record or from a
# reasoned stable-value exception, never from a wider census.
_ADOPTION_TREE = "67e84b316e399cda474b2c46e9a396b7cbf12bdd"
_ADOPTION_COUNTS = {
    "specs": 212,
    "mutants": 1169,
    "old": 1166,
    "old_regex": 3,
}

# The tree this file was drafted against, kept so the pin advance above is
# MEASURABLE rather than asserted: the difference between the two trees must be
# pure addition, and every absorbed anchor must be provably not fragile.
_PREADOPTION_TREE = "72ae230a932cdd96ebd1c6d6e4c516697435fcc2"
_ABSORBED_SPECS = (
    "tests/mutants/row356_cookie_quality_unknown.json",
    "tests/mutants/row356_cookie_quality_unknown_transform_control.json",
    "tests/mutants/row362_template_resolution_truth.json",
    "tests/mutants/row362_template_resolution_truth_transform_control.json",
    "tests/mutants/row363_affordance_learning.json",
    "tests/mutants/row363_affordance_learning_hardening.json",
    "tests/mutants/row363_affordance_learning_transform_control.json",
)
_ABSORBED_ANCHORS = 77


class State(str, Enum):
    STABLE = "STABLE"
    FRAGILE = "FRAGILE"
    UNKNOWN = "UNKNOWN"


class UnknownEvidence(RuntimeError):
    """The available evidence cannot support STABLE or FRAGILE."""


@dataclass(frozen=True)
class Anchor:
    spec: str
    label: str
    file: str
    field: str
    text: str
    new: str

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "spec": self.spec,
                "label": self.label,
                "file": self.file,
                "field": self.field,
                "text": self.text,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class Audit:
    anchor: Anchor
    state: State
    compliant: bool
    detail: str


@dataclass(frozen=True)
class FragileRule:
    spec: str
    label_prefix: str
    producer_file: str
    producer_regex: str
    value_regex: str
    reason: str


@dataclass(frozen=True)
class ResolvedSite:
    rule: FragileRule
    file: str
    value_spans: tuple[tuple[int, int], ...]
    site_line: int
    producer_line: int


@dataclass(frozen=True)
class StableValueException:
    reason: str
    evidence_file: str
    evidence_regex: str


# A fixed literal is not fragile merely because it is numeric.  New fixed
# literals that the immutable adoption census cannot know belong here with
# reviewable evidence.  Exact equality makes changing the ratchet a separate,
# visible act; an entry cannot grow the population by itself.
_STABLE_VALUE_EXCEPTIONS: dict[str, StableValueException] = {
    # Row 466: these are fixed protocol/control-flow contracts, not values
    # copied from a measurement producer.  Each entry names the independent
    # behavioral test that audits the literal's stability and intent.
    "df1e2d3629e0bfd2f0df032ff68fa2873e9399cbee8b897a01696579e3e58256":
        StableValueException(
            "HTTPError is a reached-application verdict, never a transport failure",
            "tests/test_v3_53_phase6.py",
            r"(?m)^def test_readiness_and_health_are_separate_verdicts\(\):$",
        ),
    "f5a405d08dead61b687ffdc9b01401b3fb80d6152701978bd4336af6360765d0":
        StableValueException(
            "NOT_LISTENING is the fixed diagnostic identity for a TCP refusal",
            "tests/test_v3_53_phase6.py",
            r"(?m)^def test_listener_wait_names_a_port_that_never_accepts\(\):$",
        ),
    "6a22224c34c0e1ccf1597dbc0d4f69b5fcdc042fcb7cf37c6ff4f7abe35ea62c":
        StableValueException(
            "HTTP 200 plus ok=true is the health endpoint's audited success contract",
            "tests/test_v3_53_phase6.py",
            r"(?m)^def test_health_precondition_accepts_a_genuinely_ok_server\(\):$",
        ),
    "54ce90f4a59bcb9e93a787558131102bcf8422ae518f7c345099c8534effcff4":
        StableValueException(
            "cwd equality is the fixed boundary preventing fixture-vault escape",
            "tests/test_v3_53_phase6.py",
            r"(?m)^def test_vault_isolation_refuses_a_path_outside_the_fixture_home\(\):$",
        ),
    "2761826e2b40cff26858cdeea4479fe12a37f11260276ed9e7f3d5343f7f8c3f":
        StableValueException(
            "the learned trigger must call the audited settle seam exactly here",
            "tests/test_row446_download_trigger_settle.py",
            r"(?m)^def test_row446_both_trigger_paths_settle_and_neither_sleeps_a_fixed_budget\(\):$",
        ),
    "20f5ef32f58ce5c2282eb30410b5f34494fbc70438350a9f05657deb61b58a68":
        StableValueException(
            "the recovered trigger must call the same audited settle seam",
            "tests/test_row446_download_trigger_settle.py",
            r"(?m)^def test_row446_both_trigger_paths_settle_and_neither_sleeps_a_fixed_budget\(\):$",
        ),
    "f037b262f6fd40fd4fde6ca4b2ffa877108cfd43ddad8475eab8c3b4fa615b54":
        StableValueException(
            "before-is-None is the fixed fallback from change-plus-stability to stability",
            "tests/test_row446_download_trigger_settle.py",
            r"(?m)^def test_row446_settle_observes_the_modal_that_lands_after_the_old_budget\(\):$",
        ),
    "d4cbd93ef60f044b8410d14c038ad14c8326c51cd00339e270296fae0466d38e":
        StableValueException(
            "zero observed reads is the audited boundary for an UNOBSERVED verdict",
            "tests/test_row446_download_trigger_settle.py",
            r"(?m)^def test_row446_negative_control_an_unreadable_page_is_UNOBSERVED\(\):$",
        ),
    # Row 613: the shard list names the family's test FILES; a filename is a
    # fixed identity, not a measured value.  The catcher below asserts the
    # family is reachable from CI, which is exactly what the anchor freezes.
    "148b61bdcd483e53224dc52eaea15d70ffabd0dec2e36fe2a3ffde6c23e30074":
        StableValueException(
            "tests/test_a_prune_repairs_only_the_links_it_broke.py is a fixed member of the db-prune safety family's CI shard",
            "tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py",
            r"(?m)^def test_db_prune_safety_family_is_reachable_from_ci\(\):$",
        ),
    # Row 491 (h326b): fixed restoration assignments audited by the leak catcher.
    "19e072ee0a0b48073526089e6535d52e27ee669fe3cdf894d29469e79a17ac80":
        StableValueException(
            "the assignment restores the exact app.SITES_FILE object saved on entry",
            "tests/test_v3_66_41_pwmgr_audit_followups.py",
            r"(?m)^                A\.SITES_FILE = original_sites_file$",
        ),
    # Row 491 (h326b): fixed restoration assignments audited by the leak catcher.
    "9785ff9f7ba1e8cb8c27033695bec4f5b80dd89406e4f0ecbf1f8c96a49cab87":
        StableValueException(
            "the assignment restores the exact sites-file identity latch saved on entry",
            "tests/test_v3_66_41_pwmgr_audit_followups.py",
            r"(?m)^                A\._SITES_FILE_LAST_AUTO_OBJECT = original_sites_file_latch$",
        ),
    # Row 491 (h326b): fixed restoration assignments audited by the leak catcher.
    "be3a6f55c4949f1bb2ad1c21cdaaf6cbe92d48133f077c798cb7c6176fc750cc":
        StableValueException(
            "the transform control targets the fixed app.SITES_FILE restoration assignment",
            "tests/test_v3_66_41_pwmgr_audit_followups.py",
            r"(?m)^                A\.SITES_FILE = original_sites_file$",
        ),
    # ported from row645c (worker base predates this registry)
    "87fef0b7485a4f374fe35d3000115d3199a88a043f006bd0c982ea9d1530d2e8":
        StableValueException(
            "credential_vault_locked is the audited degraded reason for an initialized vault whose in-memory key is locked",
            "tests/test_v3_62_2_guards.py",
            r"(?m)^def test_locked_master_password_vault_is_a_named_structured_503\($",
        ),
    # ported from row645c (worker base predates this registry)
    "970a240fa5336a84712844b86b488664931977aa040a567241f1f0b57165f6ff":
        StableValueException(
            "HTTP 503 is the audited readiness status for a locked credential vault",
            "tests/test_v3_62_2_guards.py",
            r"(?m)^def test_locked_master_password_vault_is_a_named_structured_503\($",
        ),
    # ported from row645c (worker base predates this registry)
    "675208397dc7f3cb21ee30326f802ab8f82b0ec0cba257c6aeb1630e0de7295c":
        StableValueException(
            "HTTP 200 is the audited readiness status after real API vault initialization and unlock",
            "tests/test_v3_62_2_guards.py",
            r"(?m)^def test_status_endpoints_are_get_and_json\(initialized_unlocked_vault\):$",
        ),
    # ported from roww2-evidenceb (worker base predates this registry)
    "ccb6742517163fd91814e78ace42dc80ccb9e0dd5fa84a2c7cb96602e11ca6de":
        StableValueException(
            "2a/9 is a fixed capture phase identity in the complete phase population",
            "tests/test_capture_shell_runtime.py",
            r"(?m)^def test_capture_source_arms_all_phase_banners_once\(\) -> None:$",
        ),
    # ported from roww2-evidenceb (worker base predates this registry)
    "35c5824afe5e2bd80f11c933329e24924348f1e3af93a9ed26f9caa9feaf541f":
        StableValueException(
            "UNKNOWN is the fixed marker replaced when a measured phase completes",
            "tests/test_capture_shell_runtime.py",
            r"(?m)^def test_capture_runtime_records_every_reached_phase_duration\(tmp_path\) -> None:$",
        ),
    # ported from roww2-evidenceb (worker base predates this registry)
    "918377d80d2a9aca0ac3e5f583446ce2db163a0f8c9b230e2c667d0ed2716df9":
        StableValueException(
            "repository_path is a required field of the transferable verdict identity",
            "tests/test_row407_integration_verdict.py",
            r"(?m)^def test_verdict_records_checkout_and_tree_identity_without_gating_on_dirt\($",
        ),
    # ported from roww2-evidenceb (worker base predates this registry)
    "51d6e4cc1788fd413ee397c846c03c5d3ade9cf73403f40c502bf55db1b28981":
        StableValueException(
            "clean and dirty are the fixed measured working-tree states",
            "tests/test_row407_integration_verdict.py",
            r"(?m)^def test_verdict_records_checkout_and_tree_identity_without_gating_on_dirt\($",
        ),
    # ported from roww2-evidenceb (worker base predates this registry)
    "2737feb6e36f52e7a714dde4a1a10e993da760c5cf427eac05018e0e878fc2b8":
        StableValueException(
            "candidate_tree_sha is a required field of the transferable verdict identity",
            "tests/test_row407_integration_verdict.py",
            r"(?m)^def test_verdict_records_checkout_and_tree_identity_without_gating_on_dirt\($",
        ),
    # ported from roww2-evidenceb (worker base predates this registry)
    "599946ac1a608dc3fd5e2169b706c50b41ddd4c0eed9dc70a745f684ef8fe5e3":
        StableValueException(
            "host is a required key on the human-readable verdict surface",
            "tests/test_row407_integration_verdict.py",
            r"(?m)^def test_text_verdict_names_host_and_repository\(verdict_repo: VerdictRepo\) -> None:$",
        ),
    # ported from roww2-evidenceb (worker base predates this registry)
    "0221c2b630be6368243256c5343be6efba4b20c104d9eb7651dc424d8150038e":
        StableValueException(
            "the transform control intentionally shares the audited clean/dirty boundary",
            "tests/test_row407_integration_verdict.py",
            r"(?m)^def test_verdict_records_checkout_and_tree_identity_without_gating_on_dirt\($",
        ),
    # ported from roww3-rotation-leak2 (worker base predates this registry)
    "f29edcbaa2b0371aa37ed786b600b63f14eaa30ba6f74697a5eb852938eb9e2a":
        StableValueException(
            "the fixture test must reinstall the exact inherited backend object",
            "tests/test_v3_66_729_body_contract_fixtures.py",
            r"(?m)^def test_the_fixture_world_owns_an_open_vault_and_ensure_reopens_it\(\):$",
        ),
    # ported from roww3-rotation-leak2 (worker base predates this registry)
    "c41beaf32ce52defd60b389de8489285bed99160771324eb3734f470faf30aa1":
        StableValueException(
            "the transform repeats the same fixed backend-restoration contract",
            "tests/test_v3_66_729_body_contract_fixtures.py",
            r"(?m)^def test_the_fixture_world_owns_an_open_vault_and_ensure_reopens_it\(\):$",
        ),
    # ported from roww3-rotation-leak2 (worker base predates this registry)
    "b88e01a81a2d7b24cc3b18ae92e5d29e59f804970bd9ef3b51b1cc900602045b":
        StableValueException(
            "the module fixture registers the inherited backend pair for exact restoration",
            "tests/test_v3_66_729_body_contract_fixtures.py",
            r"(?m)^def _secrets_store_state_is_test_owned\(monkeypatch\):$",
        ),
    # ported from roww3-rotation-leak2 (worker base predates this registry)
    "469e3801a38840d8f97d42206a4eec9e130ee5267266abb47c72dae23f2a384b":
        StableValueException(
            "the transform repeats the module fixture's fixed restoration contract",
            "tests/test_v3_66_729_body_contract_fixtures.py",
            r"(?m)^def _secrets_store_state_is_test_owned\(monkeypatch\):$",
        ),
    # Row 793 round b: the suffix guard's own literal source text, each audited
    # by the specific test in test_row793_screenshots_suffix_guard.py that
    # proves the mutant it names is a real regression against that decision.
    "d4c188e1a4b4e3c91f8da42a361f4abf147f2f05626b6a4b15333c5440e177e6":
        StableValueException(
            "M1: the guard's exact predicate text is audited by the nested-PNG-vs-non-PNG test",
            "tests/test_row793_screenshots_suffix_guard.py",
            r"(?m)^def test_screenshots_route_rejects_non_png_but_serves_nested_png\(evidence_client\):$",
        ),
    "a188127585f20e641039812e7073600f4570bd2898ecca15682b1fbf0863a105":
        StableValueException(
            "M2: the guard's exact predicate text is audited by the nested-PNG-vs-non-PNG test",
            "tests/test_row793_screenshots_suffix_guard.py",
            r"(?m)^def test_screenshots_route_rejects_non_png_but_serves_nested_png\(evidence_client\):$",
        ),
    "29551f0c90386d89fe186673741d7abe0adae659c8133818aa1d7d921ff91020":
        StableValueException(
            "M3: the guard's exact predicate text is audited by the upper-case-extension test",
            "tests/test_row793_screenshots_suffix_guard.py",
            r"(?m)^def test_upper_case_png_extension_is_still_served_as_an_image\(evidence_client\):$",
        ),
    "f4e1fc4a6cedba1cece8fbfc9f460753f475ecea159b879752b9098074744671":
        StableValueException(
            "M4: the guard's exact predicate text is audited by the double-extension test",
            "tests/test_row793_screenshots_suffix_guard.py",
            r"(?m)^def test_double_extension_is_rejected_by_suffix_not_substring\(evidence_client\):$",
        ),
    "dab984a469a2a60ad098f2d67525819cb4548a2567620cbefd26bde916dab592":
        StableValueException(
            "M5 CONTROL: the meaning-preserving rewrite shares the guard's exact predicate text, audited by the same double-extension test",
            "tests/test_row793_screenshots_suffix_guard.py",
            r"(?m)^def test_double_extension_is_rejected_by_suffix_not_substring\(evidence_client\):$",
        ),
    # Row 738: the matcher's four literal terms are a fixed identity contract
    # (which invariant statement counts as declaring the login-cap
    # single-process premise), not a value any producer re-derives.
    "377c9c36cb8b7900293503af262537c8250534b48dc37c1f5c28fd7a3d6c6df9":
        StableValueException(
            "requiring all four terms (not any one) is the fixed discrimination contract the negative control audits",
            "tests/test_row738_login_cap_single_process_premise.py",
            r"(?m)^def test_negative_control_absence_is_detected_for_the_right_reason\(\):$",
        ),
    "3e00fbbae39674d4124a95dbc12e1fff87f29a3feee7dce83aa3f462ece4d4fe":
        StableValueException(
            "returning the matched entry id (never None) on a genuine hit is the fixed positive-control contract",
            "tests/test_row738_login_cap_single_process_premise.py",
            r"(?m)^def test_the_matcher_can_say_yes_before_it_says_no\(\):$",
        ),
    "a42200d201f450ad14e0b4232aff61f505cf9a1c04e006d9cab69a58b49d9622":
        StableValueException(
            "\"cap\" (not \"caps\") is the fixed term the real I0011 statement is audited against",
            "tests/test_row738_login_cap_single_process_premise.py",
            r"(?m)^def test_invariants_declares_single_process_login_cap_premise\(\):$",
        ),
    # Row 804: the route-registration-plus-goto pairing is a fixed
    # control-flow contract (page.route must be armed before page.goto),
    # not a value copied from a measurement producer; audited by the
    # defect test this anchor's mutant is the catcher for.
    "7b2febc731aee40f4a5ce4b6d709fc33c38dc26dce1ed9eee34ad9bd6e5e2669":
        StableValueException(
            "the route guard must be registered before the pinned navigation, a fixed control-flow contract",
            "tests/test_row804_browser_redirect_bypasses_metadata_guard.py",
            r"(?m)^def test_a_redirect_to_the_metadata_address_after_the_pinned_navigation_is_refused\($",
        ),
    # Row 804: the classifier's hop verdict is a fixed True/None safety
    # bypass used only to prove the guard is load-bearing, not a value
    # copied from a measurement producer; audited by the same catcher.
    "c6f0073b74ce8a84faf3fa71b79aa4cca5519a7a441bd0db42dd9e7a54f72d71":
        StableValueException(
            "always-safe is the fixed bypass that proves the per-hop classifier call is load-bearing",
            "tests/test_row804_browser_redirect_bypasses_metadata_guard.py",
            r"(?m)^def test_a_redirect_to_the_metadata_address_after_the_pinned_navigation_is_refused\($",
        ),
    # Row 776: the helper's verdict check and the preflight's call to it are
    # new fixed protocol text, not values copied from a measurement producer.
    "b72936902178e721c5ee3a36cbf3d1cf7877a3e9f2f5686863118f94dfd4f95c":
        StableValueException(
            "download is the fixed candidate_filter verdict kind that marks a pending URL already downloadable",
            "tests/test_row776_accepted_media_skips_needs_review.py",
            r"(?m)^def test_direct_media_url_is_already_downloadable\(\):$",
        ),
    "aa76c58ef2d8f7d97b0e05a62bf17dd97f0d791af26d072f64c1b94bb9e4b9e7":
        StableValueException(
            "download is the fixed candidate_filter verdict kind that marks a pending URL already downloadable",
            "tests/test_row776_accepted_media_skips_needs_review.py",
            r"(?m)^def test_plain_page_url_is_not_already_downloadable\(\):$",
        ),
    # Row 776rp2 (correctness REFUTE): `httpx.Cookies(jar)` is the fixed
    # decision that the jar, not a flattened dict, reaches the client, so
    # domain/path/secure/expiry scoping is the library's; audited by the
    # wire-level scoping test this mutant names as its catcher.
    "da3201832513a611cdb3c455bb0607c6f465f30ed8a18daa516c332cb442818b":
        StableValueException(
            "httpx.Cookies(jar) is the fixed decision that keeps cookie scoping with the jar",
            "tests/test_row776_accepted_media_skips_needs_review.py",
            r"(?m)^def test_ranker_helper_sends_a_jar_cookie_only_to_the_url_it_is_scoped_to\(",
        ),
    "704621157972db89fffdc5b013c32b9a28394b2905b75e0886f5886bb3f3e0f1":
        StableValueException(
            "the auto-teach preflight must consult the audited helper before flagging needs_review",
            "tests/test_row776_accepted_media_skips_needs_review.py",
            r"(?m)^def test_auto_teach_preflight_consults_the_helper_before_flagging_needs_review\(\):$",
        ),
    # Row 776c (correctness REFUTE fix): the ranker helper's SSRF host-guard
    # call is new fixed protocol text -- not a value copied from a
    # measurement producer -- audited by its own dedicated regression test.
    "653fd354e23826b6688398aae02932324f1dc87f9d814991c1a9fa935b95a98c":
        StableValueException(
            "the ranker helper must refuse a non-public host before ever fetching it",
            "tests/test_row776_accepted_media_skips_needs_review.py",
            r"(?m)^def test_ranker_helper_refuses_a_link_local_url_with_no_request_made\(monkeypatch\):$",
        ),
    # Row 776d (shape REFUTE fix): the 3xx status-code tuple is new fixed
    # protocol text -- not a value copied from a measurement producer --
    # audited by its own dedicated regression test.
    "17328f4c444f2a9e6b038736d2ab4f4698139f878b23264cbf6303459ece9e3f":
        StableValueException(
            "the ranker helper must refuse a redirect response by status code alone",
            "tests/test_row776_accepted_media_skips_needs_review.py",
            r"(?m)^def test_ranker_helper_refuses_a_redirect_response_even_with_a_strong_winner\(monkeypatch\):$",
        ),
    # Row 776d (correctness REFUTE fix): follow_redirects/proxy/fail-closed
    # are new fixed protocol text -- not values copied from a measurement
    # producer -- each audited by its own dedicated regression test.
    "1d7fdba8b011ae3f698c47bbde266b74de28d0529e1ccad171ee66b290f3713c":
        StableValueException(
            "the ranker helper's transport must be built with follow_redirects=False and the caller's proxy",
            "tests/test_row776_accepted_media_skips_needs_review.py",
            r"(?m)^def test_ranker_helper_installs_the_guarded_transport_with_no_redirects_and_the_given_proxy\(monkeypatch\):$",
        ),
    "da9acdd798ae088570c13656463260715ec614e15603644b650aaf8a74f64539":
        StableValueException(
            "the ranker helper's transport must be built with follow_redirects=False and the caller's proxy",
            "tests/test_row776_accepted_media_skips_needs_review.py",
            r"(?m)^def test_ranker_helper_installs_the_guarded_transport_with_no_redirects_and_the_given_proxy\(monkeypatch\):$",
        ),
    "fd68924da537e75b9f431e51e82fe66d5b2110e4edf5cf2a681bbb9e14f8832c":
        StableValueException(
            "a down proxy tunnel must fail closed, never fall through with proxy=None",
            "tests/test_row776_accepted_media_skips_needs_review.py",
            r"(?m)^def test_real_start_fails_closed_and_never_fetches_when_the_proxy_tunnel_is_down\(tmp_path, monkeypatch\):$",
        ),
    # Row 722 (row722_access_brand_gateway): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "e4827420c5455cad930687ea6476d30d511a187dfc963e41a8b8fd7ca3a7af21":
        StableValueException(
            "M1 (drop the ACCESS_AFFORDANCE admission at the interstitial tier): the anchor is the fixed decision text this mutant severs; audited by test_the_row_access_nookies_is_pressed_and_the_partner_links_are_not",
            "tests/test_row722_access_brand_gateway.py",
            r"(?m)^def test_the_row_access_nookies_is_pressed_and_the_partner_links_are_not\(",
        ),
    # Row 722 (row722_async_post_login_interstitial_rewalk): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "f4d6e808fbef878d131929c0c5e1d235f80a25865b94f4d001e23c70d463d40c":
        StableValueException(
            "M1 (single post-login walk again): the anchor is the fixed decision text this mutant severs; audited by test_the_row_a_late_rendered_interstitial_gets_a_second_walk",
            "tests/test_row722_async_post_login_interstitial_rewalk.py",
            r"(?m)^def test_the_row_a_late_rendered_interstitial_gets_a_second_walk\(",
        ),
    # Row 722 (row722_blacked_i_agree_age_gate): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "cb79f9cf660d87c0799dca66787487714755fba01d0f9ac16476d900326ba1a7":
        StableValueException(
            "M1 (admission removed: bare AGREE never checked in the age tier): the anchor is the fixed decision text this mutant severs; audited by test_bare_i_agree_clears_the_blacked_age_wall",
            "tests/test_row722_blacked_i_agree_age_gate.py",
            r"(?m)^def test_bare_i_agree_clears_the_blacked_age_wall\(",
        ),
    "1227895df74ea2265cbf8aad9b1aa7f71ae5b4942d31a251d9891c5be2a92086":
        StableValueException(
            "M2 (corroboration gate removed: bare I AGREE pressed without age language): the anchor is the fixed decision text this mutant severs; audited by test_negative_control_a_agree_with_no_age_language_is_not_pressed",
            "tests/test_row722_blacked_i_agree_age_gate.py",
            r"(?m)^def test_negative_control_a_agree_with_no_age_language_is_not_pressed\(",
        ),
    # Row 722 (row722_cloudflare_challenge_before_form): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "0bd8050203fc9cc4c3c59bae6ba15cdb4f30293da9d6870d03094c0b13702977":
        StableValueException(
            "M3 (post-submit challenge page not cleared): the anchor is the fixed decision text this mutant severs; audited by test_a_post_submit_challenge_page_is_cleared_before_the_success_url_check",
            "tests/test_row722_cloudflare_challenge_before_form.py",
            r"(?m)^def test_a_post_submit_challenge_page_is_cleared_before_the_success_url_check\(",
        ),
    "d281bf4a6891ddcf5796587ac60c7a27648216a53d57ed9083d6fdea1dbad18b":
        StableValueException(
            "M5 (managed-challenge widget container ids dropped): the anchor is the fixed decision text this mutant severs; audited by test_the_explicit_widget_container_is_the_click_anchor_when_the_frame_is_proxied",
            "tests/test_row722_cloudflare_challenge_before_form.py",
            r"(?m)^def test_the_explicit_widget_container_is_the_click_anchor_when_the_frame_is_proxied\(",
        ),
    "4ee3582b914298b3b468a9037848777af9aedfdbe22a565646597b70cebe0eeb":
        StableValueException(
            "M6 (post-login upsell uncheck dropped): the anchor is the fixed decision text this mutant severs; audited by test_upsell_boxes_are_unchecked_before_the_post_login_interstitial_walk",
            "tests/test_row722_cloudflare_challenge_before_form.py",
            r"(?m)^def test_upsell_boxes_are_unchecked_before_the_post_login_interstitial_walk\(",
        ),
    "04f8ed96d3419bb2780d2cff1b7ef76e7cf15017f2c07c46fec4c03de7c22b21":
        StableValueException(
            "M7 (random-id widget host never tagged): the anchor is the fixed decision text this mutant severs; audited by test_the_tagged_random_id_host_is_the_click_anchor_when_the_frame_is_proxied",
            "tests/test_row722_cloudflare_challenge_before_form.py",
            r"(?m)^def test_the_tagged_random_id_host_is_the_click_anchor_when_the_frame_is_proxied\(",
        ),
    "cb1d71a7e37f6f58ef158f5216dde862246e07ac700afd7988dfdf75ca99ce6a":
        StableValueException(
            "M8 (auto-verifying challenge abandoned): the anchor is the fixed decision text this mutant severs; audited by test_a_challenge_that_auto_verifies_is_waited_for_not_abandoned",
            "tests/test_row722_cloudflare_challenge_before_form.py",
            r"(?m)^def test_a_challenge_that_auto_verifies_is_waited_for_not_abandoned\(",
        ),
    "6cd122adacef17480db8ba89b44a7fe79a761fa7b473e45fb5c8589207d0db30":
        StableValueException(
            "M10 (site-drawn Security Check page not recognised): the anchor is the fixed decision text this mutant severs; audited by test_the_security_check_title_alone_marks_a_site_drawn_challenge",
            "tests/test_row722_cloudflare_challenge_before_form.py",
            r"(?m)^def test_the_security_check_title_alone_marks_a_site_drawn_challenge\(",
        ),
    "52ca9eba32d6351e4bc855939ebbcde7760fee81ddd56689c04218205cb5bcfc":
        StableValueException(
            "M11 (dead page after clearance not re-entered): the anchor is the fixed decision text this mutant severs; audited by test_a_cleared_challenge_that_lands_on_a_dead_page_re_enters_the_site",
            "tests/test_row722_cloudflare_challenge_before_form.py",
            r"(?m)^def test_a_cleared_challenge_that_lands_on_a_dead_page_re_enters_the_site\(",
        ),
    "6edf8757841c9a0ae7c2b2e7928a86a5b98bebb7377432b5fdd2850af750a920":
        StableValueException(
            "M13 (swallowed POST never re-submitted): the anchor is the fixed decision text this mutant severs; audited by test_after_a_cleared_challenge_an_empty_login_form_is_re_submitted_once",
            "tests/test_row722_cloudflare_challenge_before_form.py",
            r"(?m)^def test_after_a_cleared_challenge_an_empty_login_form_is_re_submitted_once\(",
        ),
    # Row 722 (row722_consent_gate_remeasured): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "db9f5083235da012d8f0a8a5125e01a256945fd3664271e040c65d985576f1ae":
        StableValueException(
            "M1 (drop the re-measurement loop (falls straight back to the immediate refusal)): the anchor is the fixed decision text this mutant severs; audited by test_control_that_rerenders_once_is_clicked_on_the_remeasure",
            "tests/test_row722_consent_gate_remeasured_after_dom_change.py",
            r"(?m)^def test_control_that_rerenders_once_is_clicked_on_the_remeasure\(",
        ),
    "e223a4733b69fa0442252a0410b49cde9016d4feb2cd5d376ebf07e341445ce8":
        StableValueException(
            "M2 (drop the re-measurement bound (a control that never stabilises is re-measured unboundedly instead of refused after two tries)): the anchor is the fixed decision text this mutant severs; audited by test_control_that_keeps_changing_stays_measurement_unknown",
            "tests/test_row722_consent_gate_remeasured_after_dom_change.py",
            r"(?m)^def test_control_that_keeps_changing_stays_measurement_unknown\(",
        ),
    # Row 722 (row722_custom_selector_ranks): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "22eb3542f06d32f2bcb5472b359043205d303d936b23b0e50163fa0c90c6b9b1":
        StableValueException(
            "M1 (`.first` restored: multi-match never ranked): the anchor is the fixed decision text this mutant severs; audited by test_five_matches_with_preference_1080_720_pick_1080",
            "tests/test_row722_custom_selector_ranks_by_quality.py",
            r"(?m)^def test_five_matches_with_preference_1080_720_pick_1080\(",
        ),
    # Row 722 (row722_dropdown_download_affordance): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "d1acb68aad957b06e62a2b1629331262898346ae2e1fec860f9c2254112962e6":
        StableValueException(
            "M1 (runner never consults the dropdown helper): the anchor is the fixed decision text this mutant severs; audited by test_runner_seam_consults_the_helper_when_score_is_0",
            "tests/test_row722_dropdown_download_affordance.py",
            r"(?m)^def test_runner_seam_consults_the_helper_when_score_is_0\(",
        ),
    "46dc0f6b0ec7e0b2c16ba6feeb4398546b8ca1f77a998ab01d44528a7daf19d1":
        StableValueException(
            "M2 (helper never opens the menu): the anchor is the fixed decision text this mutant severs; audited by test_the_runner_opens_the_dropdown_and_picks_4k",
            "tests/test_row722_dropdown_download_affordance.py",
            r"(?m)^def test_the_runner_opens_the_dropdown_and_picks_4k\(",
        ),
    # Row 722 (row722_handoff_keeps_evidence): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "3dcb64c4d00ad42147546dd3e6e28ba617dd114c84cb42ff760d23764ddf4ceb":
        StableValueException(
            "M1 (hand-off keeps no evidence): the anchor is the fixed decision text this mutant severs; audited by test_handoff_keeps_the_page_it_gave_up_on",
            "tests/test_row722_handoff_keeps_evidence.py",
            r"(?m)^def test_handoff_keeps_the_page_it_gave_up_on\(",
        ),
    "19f49a94c54b5f53e6c3b7065b5519742d22296f3352ba2df698ef929f862f05":
        StableValueException(
            "M2 (evidence writer keeps no PNG): the anchor is the fixed decision text this mutant severs; audited by test_evidence_writer_keeps_a_png_beside_the_html",
            "tests/test_row722_handoff_keeps_evidence.py",
            r"(?m)^def test_evidence_writer_keeps_a_png_beside_the_html\(",
        ),
    # Row 722 (row722_hidden_duplicate_login_form): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "62a13d946ad21c544dac87e3f79011c52366b72dcc8e6ff8a4750daf8562c910":
        StableValueException(
            "M1 (click only the first match of each selector): the anchor is the fixed decision text this mutant severs; audited by test_submit_acts_on_the_visible_form_not_the_hidden_duplicate",
            "tests/test_row722_hidden_duplicate_login_form.py",
            r"(?m)^def test_submit_acts_on_the_visible_form_not_the_hidden_duplicate\(",
        ),
    "be49a9520401beeaab2a23fcbd38a6b079ce956c4d9f1b4f51da125b5c010125":
        StableValueException(
            "M2 (JS scope prefers the first password field's form): the anchor is the fixed decision text this mutant severs; audited by test_js_request_submit_scopes_to_the_visible_form",
            "tests/test_row722_hidden_duplicate_login_form.py",
            r"(?m)^def test_js_request_submit_scopes_to_the_visible_form\(",
        ),
    # Row 722 (row722_integrity_check_is_format_aware): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "3ba55f1b593f7937d8da1fd7e62186860b77574bbd9b3cda8dc275be89c12bd3":
        StableValueException(
            "M1 (zip routed back to ffprobe): the anchor is the fixed decision text this mutant severs; audited by test_a_valid_zip_is_not_failed_by_ffprobe",
            "tests/test_row722_integrity_check_is_format_aware.py",
            r"(?m)^def test_a_valid_zip_is_not_failed_by_ffprobe\(",
        ),
    "568cf42602a4b38eabe57b70aa6f0f48c9a3e6111fc7a59a9a49ad6b1ccb854f":
        StableValueException(
            "M2 (testzip failure passes): the anchor is the fixed decision text this mutant severs; audited by test_a_zip_with_a_bad_member_crc_is_failed",
            "tests/test_row722_integrity_check_is_format_aware.py",
            r"(?m)^def test_a_zip_with_a_bad_member_crc_is_failed\(",
        ),
    # Row 722 (row722_invalid_selector_config): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "71e2b78c7ac330852339144e210bba295e371a2dfbd9cd7acbc9002355217560":
        StableValueException(
            "M1 (PUT selector validation dropped): the anchor is the fixed decision text this mutant severs; audited by test_put_invalid_dl_selector_is_400_with_field_and_reason_and_not_persisted",
            "tests/test_row722_invalid_selector_config_is_terminal.py",
            r"(?m)^def test_put_invalid_dl_selector_is_400_with_field_and_reason_and_not_persisted\(",
        ),
    "711eb867a258ce6a08674e67d8534a8dc0a2cbba86a00db624571fd935cd5370":
        StableValueException(
            "M2 (selector SyntaxError retried again): the anchor is the fixed decision text this mutant severs; audited by test_selector_syntax_error_from_config_is_needs_review_with_full_selector",
            "tests/test_row722_invalid_selector_config_is_terminal.py",
            r"(?m)^def test_selector_syntax_error_from_config_is_needs_review_with_full_selector\(",
        ),
    # Row 722 (row722_kink_enter_brand): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "84957b463af0cec523033ff1294bcb921ae3c2adebf4415f44d2b873894d9f76":
        StableValueException(
            "M1 (drop the instruction-word exclusion after ENTER): the anchor is the fixed decision text this mutant severs; audited by test_negative_control_instruction_labels_stay_refused",
            "tests/test_row722_kink_enter_brand_affordance.py",
            r"(?m)^def test_negative_control_instruction_labels_stay_refused\(",
        ),
    "c0e0aebefaa4d285ce9451b08f88b7fe086acea6754a6a63a070b2738fa79e4d":
        StableValueException(
            "M2 (widen the brand token to any run of words): the anchor is the fixed decision text this mutant severs; audited by test_a_card_control_whose_label_merely_contains_enter_is_never_clicked",
            "tests/test_row792_age_gate_enter_substring_and_three_consumers.py",
            r"(?m)^def test_a_card_control_whose_label_merely_contains_enter_is_never_clicked\(",
        ),
    "35feafad78d8a4f8b641bf93c6ecac4f4ad1e56913336518415e61ea9b402edc":
        StableValueException(
            "M3 (drop the brand arm (pre-row-722 vocabulary)): the anchor is the fixed decision text this mutant severs; audited by test_enter_brand_clears_the_kink_age_wall",
            "tests/test_row722_kink_enter_brand_affordance.py",
            r"(?m)^def test_enter_brand_clears_the_kink_age_wall\(",
        ),
    # Row 722 (row722_login_browser_mirrors_stepper_profile): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "beaf09c1c1dae58993dc9f886a34b49a014f55ea72c246f8f8770439e3eda36b":
        StableValueException(
            "M1 (implicit channel chrome restored (use_real_chrome absent -> system Chrome)): the anchor is the fixed decision text this mutant severs; audited by test_login_launch_mirrors_cloaked_page_profile",
            "tests/test_row722_login_browser_mirrors_stepper_profile.py",
            r"(?m)^def test_login_launch_mirrors_cloaked_page_profile\(",
        ),
    "5d007383b49763087308b1914a7741dcac2f3ac96e3f4bb61ab1770feb7d86cd":
        StableValueException(
            "M2 (hand-built launch args list restored (overrides cloak stealth defaults)): the anchor is the fixed decision text this mutant severs; audited by test_login_launch_mirrors_cloaked_page_profile",
            "tests/test_row722_login_browser_mirrors_stepper_profile.py",
            r"(?m)^def test_login_launch_mirrors_cloaked_page_profile\(",
        ),
    # Row 722 (row722_login_cap_survives_restart): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "6abbb3c8f9f3717e5d1704a6aa2e9946645bf4f5f30a633e4d357005d962d741":
        StableValueException(
            "M1 (cap key out of CFG_FIELDS again): the anchor is the fixed decision text this mutant severs; audited by test_the_row_the_cap_survives_the_reload_rebuild",
            "tests/test_row722_login_cap_survives_restart.py",
            r"(?m)^def test_the_row_the_cap_survives_the_reload_rebuild\(",
        ),
    # Row 722 (row722_media_leaf_names): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "cec5a69c736a504450247d1f79491a0fb3aa43bd3366f5864e6d355440c814c6":
        StableValueException(
            "M2 (title fallback skipped): the anchor is the fixed decision text this mutant severs; audited by test_high_leaf_nookies",
            "tests/test_row722_media_leaf_names_fall_back_to_title.py",
            r"(?m)^def test_high_leaf_nookies\(",
        ),
    # Row 722 (row722_post_submit_anonymous_page): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "7cec8399c83d4a2f48f667619c4761e7784532daf330d024f66ed4722ae55adf":
        StableValueException(
            "M1 (navigated return ignores the anonymous surface): the anchor is the fixed decision text this mutant severs; audited by test_visible_password_field_after_navigation_is_not_ok",
            "tests/test_row722_post_submit_anonymous_page_is_not_ok.py",
            r"(?m)^def test_visible_password_field_after_navigation_is_not_ok\(",
        ),
    "151fba60517c273695e1b88c2bf77659fc78678fe55f2e13d4cafd6b5187081d":
        StableValueException(
            "M2 (a visible password field is not an anonymous surface): the anchor is the fixed decision text this mutant severs; audited by test_visible_password_field_after_navigation_is_not_ok",
            "tests/test_row722_post_submit_anonymous_page_is_not_ok.py",
            r"(?m)^def test_visible_password_field_after_navigation_is_not_ok\(",
        ),
    # Row 722 (row722_pre_submit_evidence): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "7f68bb6660361f75fc1e00b871ff0a4270f7e36f0c09ae8abe96423cec097f1a":
        StableValueException(
            "M1 (do_login keeps no pre-submit screenshot): the anchor is the fixed decision text this mutant severs; audited by test_the_capture_runs_inside_do_login_before_the_submit_sweep",
            "tests/test_row722_pre_submit_evidence.py",
            r"(?m)^def test_the_capture_runs_inside_do_login_before_the_submit_sweep\(",
        ),
    "3bc5e2bd58977a7287c476f2df6d9068166320d0d42baa7b4f2aea4bf2c79ed1":
        StableValueException(
            "M2 (capture writes nothing): the anchor is the fixed decision text this mutant severs; audited by test_the_filled_form_is_kept_as_a_png_before_the_submit",
            "tests/test_row722_pre_submit_evidence.py",
            r"(?m)^def test_the_filled_form_is_kept_as_a_png_before_the_submit\(",
        ),
    "6dc08e35c1b4ae2791f159fb6736deac8385e0fd5150581e07205f72ef0fe386":
        StableValueException(
            "M3 (username unmasked in the pre-submit shot): the anchor is the fixed decision text this mutant severs; audited by test_the_username_is_masked_for_the_shot_and_restored_after",
            "tests/test_row722_pre_submit_evidence.py",
            r"(?m)^def test_the_username_is_masked_for_the_shot_and_restored_after\(",
        ),
    # Row 722 (row722_readded_url_starts_fresh): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "4375b05af88edc9c3ef6eef2c693c5d055e79f120afc0a66181edb2a894bf8de":
        StableValueException(
            "M1 (queue upsert reset dropped (stale row keeps its ladder)): the anchor is the fixed decision text this mutant severs; audited by test_readded_url_after_bulk_delete_starts_at_retry_zero",
            "tests/test_row722_readded_url_starts_fresh.py",
            r"(?m)^def test_readded_url_after_bulk_delete_starts_at_retry_zero\(",
        ),
    "ad48ce9c2b6d234a597fbe3c4004ab72362ba8d2cad69fb977c09a9427095cdb":
        StableValueException(
            "M2 (deleted-in-flight failure resurrects the job): the anchor is the fixed decision text this mutant severs; audited by test_failure_for_a_job_deleted_in_flight_is_not_published",
            "tests/test_row722_readded_url_starts_fresh.py",
            r"(?m)^def test_failure_for_a_job_deleted_in_flight_is_not_published\(",
        ),
    # Row 722 (row722_reveal_download_and_listing_links): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "054c47a8e9a7000766b28e96204239f60b3b40aff180b48883d448f19e779d62":
        StableValueException(
            "M1 (listing-link exclusion dropped): the anchor is the fixed decision text this mutant severs; audited by test_the_row_listing_links_are_not_candidates_and_reveal_picks_the_stream",
            "tests/test_row722_reveal_download_and_listing_links.py",
            r"(?m)^def test_the_row_listing_links_are_not_candidates_and_reveal_picks_the_stream\(",
        ),
    "cf88339ba3eecc5bdc81df315c958bd758020e12cfeea81dd9695978357097ca":
        StableValueException(
            "M2 (reveal path dropped): the anchor is the fixed decision text this mutant severs; audited by test_the_row_listing_links_are_not_candidates_and_reveal_picks_the_stream",
            "tests/test_row722_reveal_download_and_listing_links.py",
            r"(?m)^def test_the_row_listing_links_are_not_candidates_and_reveal_picks_the_stream\(",
        ),
    # Row 722 (row722_reveal_href_less_quality_buttons): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "45506edd9f09e65ec2934c0680850f0166fd7a0fb434ff2528c7db2295f680e8":
        StableValueException(
            "M1 (href-less controls excluded again): the anchor is the fixed decision text this mutant severs; audited by test_the_row_href_less_tier_buttons_are_options_and_the_2160p_one_is_clicked",
            "tests/test_row722_reveal_href_less_quality_buttons.py",
            r"(?m)^def test_the_row_href_less_tier_buttons_are_options_and_the_2160p_one_is_clicked\(",
        ),
    # Row 722 (row722_root_success_url): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "92805f2404800e0b019e80109274ef86cf32c8152dc26cd3865ce95e00639c28":
        StableValueException(
            "M1 (root success_url matches every path again): the anchor is the fixed decision text this mutant severs; audited by test_the_row_a_root_success_url_no_longer_matches_the_login_page",
            "tests/test_row722_root_success_url_is_not_the_login_page.py",
            r"(?m)^def test_the_row_a_root_success_url_no_longer_matches_the_login_page\(",
        ),
    "cc3f703c2438d1dc161c032da7ec38fd6a1fe3ae5b1ca2886575f290bc3f0587":
        StableValueException(
            "M2 (the login page counts as success): the anchor is the fixed decision text this mutant severs; audited by test_success_url_reached",
            "tests/test_row722_root_success_url_is_not_the_login_page.py",
            r"(?m)^def test_success_url_reached\(",
        ),
    # Row 722 (row722_same_brand_cross_origin_landing): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "c0bbeb31d7473962588050b455a263b01dad0b0d7e7b45a0c447b9264a1dcf98":
        StableValueException(
            "M1 (predicate always False: a same-brand landing is refused as cross-origin again): the anchor is the fixed decision text this mutant severs; audited by test_same_brand_cross_origin_landing_with_member_surface_is_ok",
            "tests/test_row722_same_brand_cross_origin_landing.py",
            r"(?m)^def test_same_brand_cross_origin_landing_with_member_surface_is_ok\(",
        ),
    "9b8d3bb26dfd43eba790e0b7637b1fbe9c3feb256f1db11452f71821d420bc4a":
        StableValueException(
            "M2 (predicate always True: accounts.google.com is accepted as a same-brand landing): the anchor is the fixed decision text this mutant severs; audited by test_negative_control_a_foreign_domain_keeps_the_row_774_refusal",
            "tests/test_row722_same_brand_cross_origin_landing.py",
            r"(?m)^def test_negative_control_a_foreign_domain_keeps_the_row_774_refusal\(",
        ),
    "612bbe5d3fe2a3ceff11d56fd6bfceee46bc40e083c6e49bc8e0e05e2ab2d6aa":
        StableValueException(
            "M3 (do_login same-brand branch dropped: landing refused before judgment): the anchor is the fixed decision text this mutant severs; audited by test_same_brand_cross_origin_landing_with_member_surface_is_ok",
            "tests/test_row722_same_brand_cross_origin_landing.py",
            r"(?m)^def test_same_brand_cross_origin_landing_with_member_surface_is_ok\(",
        ),
    "67c493a163ed1d3086ceeb484e3a3688891f86dcfa59f89b84c35bfa5c2e9482":
        StableValueException(
            "M4 (evidence not written on the same-brand landing): the anchor is the fixed decision text this mutant severs; audited by test_same_brand_cross_origin_landing_with_member_surface_is_ok",
            "tests/test_row722_same_brand_cross_origin_landing.py",
            r"(?m)^def test_same_brand_cross_origin_landing_with_member_surface_is_ok\(",
        ),
    "7cbf00c246bfdc8db8a743d0c14e26ddc490d3e219d7043e6b9af63a46d22a66":
        StableValueException(
            "M5 (declared success origin not admitted in the sweep): the anchor is the fixed decision text this mutant severs; audited by test_in_sweep_a_declared_success_origin_is_a_submit_for_a_different_brand",
            "tests/test_row722_same_brand_cross_origin_landing.py",
            r"(?m)^def test_in_sweep_a_declared_success_origin_is_a_submit_for_a_different_brand\(",
        ),
    # Row 722 (row722_site_templates_verified): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "d9827f41fdff1343334e956a49afe563c36924a422c375fa6d5658f2150abc74":
        StableValueException(
            "M1 (kink trigger selector drifts from the verified button.buy-shoot): the anchor is the fixed decision text this mutant severs; audited by test_config_defaults_carry_what_the_verified_run_needed",
            "tests/test_row722_site_templates_verified.py",
            r"(?m)^def test_config_defaults_carry_what_the_verified_run_needed\(",
        ),
    "a1c8deb2a51d27d5112ff21e7d41286f18935dbdab8ccec9d01934b3d7cbf5cd":
        StableValueException(
            "M2 (bangbros CONTINUE div dismiss is dropped): the anchor is the fixed decision text this mutant severs; audited by test_config_defaults_carry_what_the_verified_run_needed",
            "tests/test_row722_site_templates_verified.py",
            r"(?m)^def test_config_defaults_carry_what_the_verified_run_needed\(",
        ),
    "f1eacef2dc8cb69d669eaafef2cde0960e701434823a0077fdbe007ba3e743c6":
        StableValueException(
            "M3 (nookies dl_selector is left with an unclosed attribute bracket): the anchor is the fixed decision text this mutant severs; audited by test_every_selector_passes_the_site_editor_validator",
            "tests/test_row722_site_templates_verified.py",
            r"(?m)^def test_every_selector_passes_the_site_editor_validator\(",
        ),
    "1c63ab2755899fd52fd036aca64104c0c44d7198be0edfa7590ac5c13f699f7c":
        StableValueException(
            "M4 (vip4k listing URL is stored under a key that is not a CFG_FIELD): the anchor is the fixed decision text this mutant severs; audited by test_config_defaults_keys_are_all_cfg_fields",
            "tests/test_row722_site_templates_verified.py",
            r"(?m)^def test_config_defaults_keys_are_all_cfg_fields\(",
        ),
    "d46369864f982966fbabcc9da25a87bc145edddf0deff5d2104260b0addf804a":
        StableValueException(
            "M5 (the stepsiblingscaught pattern no longer matches the login host): the anchor is the fixed decision text this mutant severs; audited by test_the_login_url_resolves_to_the_template",
            "tests/test_row722_site_templates_verified.py",
            r"(?m)^def test_the_login_url_resolves_to_the_template\(",
        ),
    "7eb2728cc1288164733d2c4d929ec6ab3f7997a5fcdce03c779f6fbb1e9fb533":
        StableValueException(
            "M6 (the brazzers entry is duplicated under the filthykings id): the anchor is the fixed decision text this mutant severs; audited by test_every_verified_template_id_is_present_exactly_once",
            "tests/test_row722_site_templates_verified.py",
            r"(?m)^def test_every_verified_template_id_is_present_exactly_once\(",
        ),
    "d018ace75cd08198d9ebc149481488a164ab6120373aaad9d25ae862874feeb7":
        StableValueException(
            "M7 (the never-completed Vixen network claims the row 722 stamp): the anchor is the fixed decision text this mutant severs; audited by test_sites_that_did_not_complete_the_cycle_have_no_verified_template",
            "tests/test_row722_site_templates_verified.py",
            r"(?m)^def test_sites_that_did_not_complete_the_cycle_have_no_verified_template\(",
        ),
    "1d48923e75898f30d1722e2700ad0b34d23557f439080d31904ff5a1d51d8701":
        StableValueException(
            "M8 (the bangbros parallel url_attribute list loses the slot for its first row): the anchor is the fixed decision text this mutant severs; audited by test_learned_selectors_parse_and_parallel_attributes_line_up",
            "tests/test_row722_site_templates_verified.py",
            r"(?m)^def test_learned_selectors_parse_and_parallel_attributes_line_up\(",
        ),
    "a43b81f8f5a6e1dc6925ca024cd0e2e5f2a1c9d0eec5f6c73a53707e135dd235":
        StableValueException(
            "M9 (the evilangel description drops the verified stamp): the anchor is the fixed decision text this mutant severs; audited by test_the_verified_stamp_leads_every_description",
            "tests/test_row722_site_templates_verified.py",
            r"(?m)^def test_the_verified_stamp_leads_every_description\(",
        ),
    # Row 722 (row722_spa_api_media_extraction): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "55f6a07d9173beb2e87da8f67dfbd1b9bf3b0ca9f53c3cc1639972a80be4d8d1":
        StableValueException(
            "M2 (ranking ignores resolution (list order wins)): the anchor is the fixed decision text this mutant severs; audited by test_ranking_prefers_resolution_over_list_order_and_api_over_page_media",
            "tests/test_row722_spa_api_media_extraction.py",
            r"(?m)^def test_ranking_prefers_resolution_over_list_order_and_api_over_page_media\(",
        ),
    "6c2e4e311c41d1666c2debbbc5db1e6ef68dd9deb6395ccea074ba3925d093ef":
        StableValueException(
            "M3 (API path writes history without harvesting the title): the anchor is the fixed decision text this mutant severs; audited by test_the_api_path_harvests_the_page_title_before_the_history_row",
            "tests/test_row722_spa_api_media_extraction.py",
            r"(?m)^def test_the_api_path_harvests_the_page_title_before_the_history_row\(",
        ),
    # Row 722 (row722_turnstile_checkbox): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "0dc4cd93b063e6c42dd7893890b8e005ebfe9574021c7f03b9e6f06f43a57d0a":
        StableValueException(
            "M2 (NOT-populated log collapses back to 'populated after 30.0s'): the anchor is the fixed decision text this mutant severs; audited by test_call_site_logs_not_populated_distinctly",
            "tests/test_row722_turnstile_checkbox.py",
            r"(?m)^def test_call_site_logs_not_populated_distinctly\(",
        ),
    "034f3e22ddd6206f02f92ec072891b72254cb0e19cad46b4c626e287bdeae766":
        StableValueException(
            "M3 (closed-shadow-root widget container never clicked): the anchor is the fixed decision text this mutant severs; audited by test_the_container_selectors_carry_the_click_when_the_frame_is_proxied",
            "tests/test_row722_turnstile_checkbox.py",
            r"(?m)^def test_the_container_selectors_carry_the_click_when_the_frame_is_proxied\(",
        ),
    # Row 722 (row722_upsell_checkbox_unchecked): the anchored text is the fixed
    # decision the row's fix introduced (control flow / vocabulary /
    # template identity), not a value any producer re-derives; each
    # named catcher is the behavioral test that audits that decision.
    "82c895eb0f095ab1deeca71f15604d1a527de3da36863b84ece0a6aaf6048cdd":
        StableValueException(
            "M1 (the uncheck call dropped): the anchor is the fixed decision text this mutant severs; audited by test_upsell_boxes_unchecked_and_verified_remember_me_and_unknown_untouched",
            "tests/test_row722_upsell_checkbox_unchecked.py",
            r"(?m)^def test_upsell_boxes_unchecked_and_verified_remember_me_and_unknown_untouched\(",
        ),
}
_STABLE_VALUE_EXCEPTION_MAX = 108


def _family(
    spec: str,
    prefixes: tuple[str, ...],
    producer_file: str,
    producer_regex: str,
    value_regex: str,
    reason: str,
) -> tuple[FragileRule, ...]:
    return tuple(
        FragileRule(
            spec,
            prefix,
            producer_file,
            producer_regex,
            value_regex,
            reason,
        )
        for prefix in prefixes
    )


_VITEST_PRODUCER = r"(?m)^    derived = math\.ceil\(_VITEST_LOADED_WORST_MS \* 1\.5\)$"
_ROW338_PRODUCER = r"(?m)^_ROW_338_MEASUREMENTS = \($"
_HUNT = "tests/test_v3_66_1132_the_hunt_reaps_what_it_abandons.py"

_FRAGILE_RULES = (
    *_family(
        "tests/mutants/row281_ui_wrapper_delegation.json",
        tuple(f"M{i} " for i in range(1, 6)),
        "tests/frontend_vitest.py",
        r"(?m)^    assert passed == collected == expected_tests, \($",
        r"(?<=expected_tests=)[0-9]+",
        "run_vitest parses the live Vitest receipt and reconciles its test count",
    ),
    *_family(
        "tests/mutants/row281_ui_wrapper_delegation_transform_control.json",
        ("TC1 ",),
        "tests/frontend_vitest.py",
        r"(?m)^    assert passed == collected == expected_tests, \($",
        r"(?<=expected_tests=)[0-9]+",
        "the transform duplicates a wrapper count derived from the Vitest receipt",
    ),
    *_family(
        "tests/mutants/row297_real_corpus_credential_denominator.json",
        ("M6 ",),
        "tests/test_ct1_corpus_validation.py",
        r"(?m)^def _credential_census\(\) -> dict\[str, int\]:$",
        r"(?<=\": )[0-9]+",
        "_credential_census derives all six metrics from the live fixture corpus",
    ),
    *_family(
        "tests/mutants/row325_forward_deadline_population.json",
        ("M5 ",),
        _HUNT,
        r"(?m)^_W1_PARTIAL_FRAME_LOADED_WAIT_S = \(",
        r"(?<=reap_seconds=)[0-9]+",
        "the partial-frame deadline is the integral ceiling over loaded arrivals",
    ),
    *_family(
        "tests/mutants/row329_vitest_timeout.json",
        ("M1 ",),
        "tests/test_t3_t4_wired.py",
        _VITEST_PRODUCER,
        r"(?<=testTimeout: )[0-9_]+",
        "row 339 derives the Vitest wall from the loaded worst case",
    ),
    *_family(
        "tests/mutants/row329_vitest_timeout_transform_control.json",
        ("M1 ",),
        "tests/test_t3_t4_wired.py",
        _VITEST_PRODUCER,
        r"(?<=testTimeout: )[0-9_]+",
        "the transform duplicates the measurement-derived Vitest wall",
    ),
    *_family(
        "tests/mutants/row338_inner_bounds.json",
        tuple(f"M{i:02d} " for i in range(1, 21)),
        "tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py",
        _ROW338_PRODUCER,
        r"(?<=timeout=)[0-9]+(?=[),])",
        "row 338 derives each inner wall from its measured call-site cost",
    ),
    *_family(
        "tests/mutants/row338_inner_bounds_transform_control.json",
        ("CONTROL ",),
        "tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py",
        _ROW338_PRODUCER,
        r"(?<=timeout=)[0-9]+(?=[),])",
        "the transform duplicates row 338's measured tool-smoke wall",
    ),
    *_family(
        "tests/mutants/row339_measurement_noise_bounds.json",
        ("M1 ",),
        "tests/test_t3_t4_wired.py",
        _VITEST_PRODUCER,
        r"(?<=testTimeout: )[0-9_]+",
        "row 339 derives the Vitest wall from the loaded worst case",
    ),
    *_family(
        "tests/mutants/row339_measurement_noise_bounds.json",
        ("M2 ",),
        "tools/verify_release.py",
        r"(?m)^# ended at 16\.43: ceil\(99\.06s \* 1\.5\) = 149s ",
        r"(?<=_STANDARD_TEST_FILE_TIMEOUT_S = )[0-9]+",
        "verify_release derives the standard-file wall from its loaded worst case",
    ),
    *_family(
        "tests/mutants/row339_measurement_noise_bounds_transform_control.json",
        ("M1 ",),
        "tests/test_t3_t4_wired.py",
        _VITEST_PRODUCER,
        r"(?<=testTimeout: )[0-9_]+",
        "the transform duplicates row 339's measurement-derived Vitest wall",
    ),
    *_family(
        "tests/mutants/row339_measurement_noise_bounds_transform_control.json",
        ("M2 ",),
        "tools/verify_release.py",
        r"(?m)^# ended at 16\.43: ceil\(99\.06s \* 1\.5\) = 149s ",
        r"(?<=_STANDARD_TEST_FILE_TIMEOUT_S = )[0-9]+",
        "the transform duplicates row 339's measurement-derived verifier wall",
    ),
    # Row 531 (v3.66.1381) retired row348's M4 rather than re-pointing it.
    # M4 set _EXPECTED_DECLARED_GATE_COUNT to a stale value, and it was catchable
    # only because a hand-maintained literal can be wrong about the population by
    # itself. That literal is gone: the expectation is now derived from the
    # declared set. A mutant aimed at the derivation leaves a consistent tree
    # consistent and ESCAPES, which would be a false negative dressed as a
    # mutant, so the honest move is to stop claiming the coverage. row348's M1,
    # M2 and M3 still sever scope, declaration and shard by making the TREE
    # inconsistent, which is what that spec is for.
    *_family(
        "tests/mutants/v3_66_1111_capture_stage_cap.json",
        ("the default cap ",),
        "scripts/lib/heartbeat.sh",
        r"(?m)^# Default 5400 \(90 min\) is ~17x the slowest lane measured",
        r"(?<=CAPTURE_STAGE_CAP:=)[0-9]+",
        "the capture cap is derived from the measured slowest fleet lane",
    ),
    *_family(
        "tests/mutants/v3_66_1204_shared_state_attribution.json",
        ("M15 ",),
        "tests/test_v3_66_1046_gates_for_this_sessions_shapes.py",
        r"(?m)^_SUITE_BASELINE_S = \{$",
        r"(?:(?<=\": )|(?<=# ))[0-9]+",
        "the suite duration and test-count comment are live census results",
    ),
    *_family(
        "tests/mutants/v3_66_1226_inner_budgets.json",
        ("M1 ",),
        _HUNT,
        r"(?m)^# _CONTENTION_FACTOR IS MEASURED, NOT CHOSEN\.",
        r"(?<=_CONTENTION_FACTOR = )[0-9]+\.[0-9]+",
        "the factor comes from the three-copy contention measurement",
    ),
    *_family(
        "tests/mutants/v3_66_1226_inner_budgets.json",
        ("M2 ",),
        "project-knowledge/BUDGET_RATCHET.json",
        r'(?m)^ "governing_bound_s": 240,$',
        r"(?<=_GOVERNING_BOUND_S = )[0-9]+\.[0-9]+",
        "the governing bound is synchronized with the independent budget ratchet",
    ),
    *_family(
        "tests/mutants/v3_66_1226_inner_budgets.json",
        ("M3 ",),
        _HUNT,
        r"(?m)^    derived = math\.ceil\(measured \* _CONTENTION_FACTOR\)$",
        r"(?<=\()[0-9]+\.[0-9]+(?=,)",
        "the table cost is policed against a live elapsed measurement",
    ),
    *_family(
        "tests/mutants/v3_66_1226_inner_budgets.json",
        ("M4 ",),
        _HUNT,
        r"(?m)^    assert _MIN_BUDGET_S >= 30\.0, \($",
        r"(?<=_MIN_BUDGET_S = )[0-9]+\.[0-9]+",
        "the floor is pinned by the measured scheduling-stall failure shape",
    ),
    *_family(
        "tests/mutants/v3_66_1231_settlement_and_census.json",
        ("M1 ",),
        "toolchain/bin/bd-wedge-hunt",
        r"(?m)^# 15 is DERIVED, not chosen: SIGINT-delivered to runner-exited measured$",
        r"(?<=W1_CLEANUP_SECONDS=)[0-9]+",
        "the cleanup wall is ceil(2.4737 seconds times the measured stretch)",
    ),
    *_family(
        "tests/mutants/v3_66_1231_settlement_and_census.json",
        ("M2 ",),
        _HUNT,
        r"(?m)^#: Named separately from `_CONTENTION_FACTOR` so that moving one cannot$",
        r"(?<=_W1_RUNNER_STRETCH_FACTOR = )[0-9]+\.[0-9]+",
        "the runner stretch is derived from the measured contention maximum",
    ),
    *_family(
        "tests/mutants/v3_66_1231_settlement_and_census.json",
        ("M3 ",),
        _HUNT,
        r"(?m)^#: SIGINT delivered -> runner exited, for the shape this cut is about: a$",
        r"(?<=_W1_SETTLEMENT_MEASURED_S = )[0-9]+\.[0-9]+",
        "the settlement cost is a direct repeated measurement",
    ),
    *_family(
        "tests/mutants/v3_66_1231_settlement_and_census.json",
        ("M4 ",),
        _HUNT,
        r"(?m)^    assert _W1_RUNNER_RESERVE_S >= reserve \* 0\.2, \($",
        r"(?<=_W1_RUNNER_RESERVE_S = )[0-9]+\.[0-9]+",
        "the reserve is derived from the production cleanup reserve and cap",
    ),
    *_family(
        "tests/mutants/v3_66_1239_precut_underived_gates.json",
        ("M4 ",),
        "toolchain/bin/bd-precut",
        r"(?m)^    _UNDERIVED_GATES = \[$",
        r"(?<=none of the )[a-z]+(?:-[a-z]+)*(?= are present)",
        "the English count is manually re-derived from _UNDERIVED_GATES",
    ),
    *_family(
        "tests/mutants/v3_66_1241_owner_observation_deadline.json",
        ("M4 ",),
        "toolchain/bin/bd-wedge-hunt",
        r"(?m)^# THE FLOOR IS DERIVED, NOT CHOSEN\. One complete observation spawn --$",
        r"(?<=W1_OWNER_OBSERVATION_SECONDS=)[0-9]+",
        "the owner-observation floor is derived from measurement and lifecycle cap",
    ),
    *_family(
        "tests/mutants/v3_66_1241_owner_observation_deadline.json",
        ("M5 ",),
        _HUNT,
        r"(?m)^#: ONE COMPLETE OWNER OBSERVATION, measured as the runner actually drives$",
        r"(?<=_W1_OBSERVATION_MEASURED_S = )[0-9]+\.[0-9]+",
        "the observation input is a direct repeated measurement",
    ),
)


_VALUE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_$])(?:0[xX][0-9A-Fa-f_]+|[0-9][0-9_]*(?:\.[0-9]+)?)"
    r"(?![A-Za-z0-9_$])"
    r"|\b(?:True|False|None|true|false|null)\b"
    r"|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)\b"
    r"|\b[0-9a-fA-F]{16,}\b"
)
_QUOTED_VALUE = re.compile(
    r"(?:[rubfRUBF]{0,2})?(?:\"[^\"\n]*\"|'[^'\n]*')"
)
_VALUE_OPERATOR = re.compile(r"=")


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _one_regex(pattern: str, source: str, subject: str) -> re.Match[str]:
    try:
        matches = list(re.finditer(pattern, source))
    except re.error as exc:
        raise UnknownEvidence(f"UNKNOWN: invalid {subject} regex: {exc}") from exc
    if len(matches) != 1:
        raise UnknownEvidence(
            f"UNKNOWN: {subject} resolves {len(matches)} times, expected exactly 1"
        )
    return matches[0]


def _anchors(documents: dict[str, dict]) -> list[Anchor]:
    found: list[Anchor] = []
    for spec, document in sorted(documents.items()):
        mutants = document.get("mutants")
        if not isinstance(mutants, list) or not mutants:
            raise UnknownEvidence(f"UNKNOWN: {spec} has no mutant denominator")
        labels: set[str] = set()
        for mutant in mutants:
            if not isinstance(mutant, dict):
                raise UnknownEvidence(f"UNKNOWN: {spec} has a non-object mutant")
            label = mutant.get("label")
            if not isinstance(label, str) or not label or label in labels:
                raise UnknownEvidence(
                    f"UNKNOWN: {spec} has a missing or duplicate label {label!r}"
                )
            labels.add(label)
            fields = {"old", "old_regex"} & set(mutant)
            if len(fields) != 1:
                raise UnknownEvidence(
                    f"UNKNOWN: {spec}::{label} has {sorted(fields)} anchor fields"
                )
            field = next(iter(fields))
            if not all(
                isinstance(mutant.get(key), str) and mutant[key]
                for key in ("file", field, "new")
            ):
                raise UnknownEvidence(f"UNKNOWN: {spec}::{label} has invalid text")
            found.append(
                Anchor(
                    spec,
                    label,
                    mutant["file"],
                    field,
                    mutant[field],
                    mutant["new"],
                )
            )
    return found


def _tree_documents(repo: Path, tree: str) -> dict[str, dict]:
    run = subprocess.run(
        ["git", "archive", "--format=tar", tree, "tests/mutants"],
        cwd=repo,
        capture_output=True,
    )
    if run.returncode:
        raise UnknownEvidence(
            "UNKNOWN: immutable row357 adoption tree is unavailable: "
            + run.stderr.decode("utf-8", "replace")[-500:]
        )
    documents: dict[str, dict] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(run.stdout), mode="r:") as archive:
            for member in archive:
                if not member.isfile() or not member.name.endswith(".json"):
                    continue
                if (
                    not member.name.startswith("tests/mutants/")
                    or ".." in Path(member.name).parts
                    or member.name in documents
                ):
                    raise UnknownEvidence(
                        f"UNKNOWN: malformed adoption member {member.name!r}"
                    )
                stream = archive.extractfile(member)
                if stream is None:
                    raise UnknownEvidence(
                        f"UNKNOWN: unreadable adoption member {member.name}"
                    )
                documents[member.name] = json.loads(stream.read())
    except (tarfile.TarError, UnicodeError, json.JSONDecodeError) as exc:
        raise UnknownEvidence(f"UNKNOWN: unreadable adoption census: {exc}") from exc
    return documents


def _adoption_documents(repo: Path = _REPO) -> dict[str, dict]:
    documents = _tree_documents(repo, _ADOPTION_TREE)
    anchors = _anchors(documents)
    observed = {
        "specs": len(documents),
        "mutants": len(anchors),
        "old": sum(anchor.field == "old" for anchor in anchors),
        "old_regex": sum(anchor.field == "old_regex" for anchor in anchors),
    }
    if observed != _ADOPTION_COUNTS:
        raise UnknownEvidence(
            f"UNKNOWN: partial adoption census {observed} != {_ADOPTION_COUNTS}"
        )
    return documents


def _current_documents(repo: Path = _REPO) -> dict[str, dict]:
    run = subprocess.run(
        ["git", "ls-files", "-z", "--", "tests/mutants/*.json"],
        cwd=repo,
        capture_output=True,
    )
    if run.returncode:
        raise UnknownEvidence(
            "UNKNOWN: tracked mutation population cannot be enumerated: "
            + run.stderr.decode("utf-8", "replace")[-500:]
        )
    raw_paths = [item for item in run.stdout.split(b"\0") if item]
    try:
        paths = [item.decode("utf-8") for item in raw_paths]
    except UnicodeDecodeError as exc:
        raise UnknownEvidence("UNKNOWN: a tracked spec path is not UTF-8") from exc
    if not paths or len(paths) != len(set(paths)):
        raise UnknownEvidence(
            f"UNKNOWN: invalid tracked mutation denominator ({len(paths)} paths)"
        )
    documents: dict[str, dict] = {}
    try:
        for rel in sorted(paths):
            documents[rel] = json.loads((repo / rel).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UnknownEvidence(f"UNKNOWN: current mutation census is unreadable: {exc}") from exc
    return documents


def _anchor_span(anchor: Anchor, source: str) -> tuple[int, int]:
    if anchor.field == "old_regex":
        return _one_regex(
            anchor.text,
            source,
            f"{anchor.spec}::{anchor.label} old_regex anchor",
        ).span()
    count = source.count(anchor.text)
    if count != 1:
        raise UnknownEvidence(
            f"UNKNOWN: {anchor.spec}::{anchor.label} literal anchor occurs {count} times"
        )
    start = source.find(anchor.text)
    return start, start + len(anchor.text)


def _rule_anchor(rule: FragileRule, anchors: list[Anchor]) -> Anchor:
    matches = [
        anchor
        for anchor in anchors
        if anchor.spec == rule.spec and anchor.label.startswith(rule.label_prefix)
    ]
    if len(matches) != 1:
        raise UnknownEvidence(
            "UNKNOWN: fragile registry key "
            f"{rule.spec}::{rule.label_prefix!r} resolves {len(matches)} times"
        )
    return matches[0]


def _alternates(anchor: Anchor, value: str) -> tuple[str, ...]:
    """Return shape-distinct probes a value-generic regex must accept.

    Hash-derived probes avoid a tiny public sentinel list that a literal
    alternation could accidentally satisfy. This is still a finite
    metamorphic check, not a proof over the regex language.
    """
    digest = hashlib.sha256(f"{anchor.fingerprint}:{value}".encode("utf-8")).digest()
    if re.fullmatch(r"[a-z]+(?:-[a-z]+)*", value):
        candidates = (
            "zero",
            "nine",
            "twenty-one",
            "nine-hundred-ninety-nine",
        )
        return tuple(item for item in candidates if item != value)
    if "_" in value:
        candidates = (
            "0",
            "42",
            "1_234",
            f"{int.from_bytes(digest[:4], 'big'):_}",
            "987_654_321",
        )
        return tuple(item for item in candidates if item != value)
    if "." in value:
        candidates = (
            "0.1",
            "12.345678",
            f"{int.from_bytes(digest[:3], 'big')}.{int.from_bytes(digest[3:6], 'big')}",
            "987654321.0",
        )
        return tuple(item for item in candidates if item != value)
    candidates = (
        "0",
        "7",
        "42",
        str(int.from_bytes(digest[:4], "big")),
        "987654321",
    )
    return tuple(item for item in candidates if item != value)


def _prove_regex_value_generic(
    anchor: Anchor,
    source: str,
    anchor_span: tuple[int, int],
    value_spans: tuple[tuple[int, int], ...],
) -> None:
    for value_span in value_spans:
        if not (
            anchor_span[0] <= value_span[0]
            and value_span[1] <= anchor_span[1]
        ):
            raise UnknownEvidence(
                "UNKNOWN: regex anchor only partially covers the derived value for "
                f"{anchor.spec}::{anchor.label}"
            )
        current = source[value_span[0] : value_span[1]]
        probes = _alternates(anchor, current)
        if len(probes) < 3 or len(set(probes)) != len(probes):
            raise UnknownEvidence(
                f"UNKNOWN: insufficient alternate-value probes for {current!r}"
            )
        for replacement in probes:
            changed = source[: value_span[0]] + replacement + source[value_span[1] :]
            changed_match = _one_regex(
                anchor.text,
                changed,
                f"{anchor.spec}::{anchor.label} after derived value {replacement!r}",
            )
            expected = (
                anchor_span[0],
                anchor_span[1] + len(replacement) - len(current),
            )
            if changed_match.span() != expected:
                raise UnknownEvidence(
                    "UNKNOWN: regex anchor's sole alternate match changed semantic "
                    f"site for {anchor.spec}::{anchor.label}: "
                    f"{changed_match.span()} != {expected}"
                )


def _read_source(repo: Path, rel: str, cache: dict[str, str]) -> str:
    if rel not in cache:
        try:
            cache[rel] = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise UnknownEvidence(f"UNKNOWN: cannot read {rel}: {exc}") from exc
    return cache[rel]


def _resolve_rule(
    repo: Path,
    rule: FragileRule,
    anchors: list[Anchor],
    cache: dict[str, str],
) -> ResolvedSite:
    anchor = _rule_anchor(rule, anchors)
    source = _read_source(repo, anchor.file, cache)
    site_pattern = anchor.text if anchor.field == "old_regex" else re.escape(anchor.text)
    site = _one_regex(site_pattern, source, f"derived site {rule.spec}::{anchor.label}")
    site_text = site.group(0)
    try:
        values = list(re.finditer(rule.value_regex, site_text))
    except re.error as exc:
        raise UnknownEvidence(
            f"UNKNOWN: invalid value selector for {rule.spec}::{anchor.label}: {exc}"
        ) from exc
    if not values:
        raise UnknownEvidence(
            f"UNKNOWN: derived site {rule.spec}::{anchor.label} exposes zero values"
        )
    value_spans = tuple(
        (site.start() + value.start(), site.start() + value.end()) for value in values
    )

    producer_source = _read_source(repo, rule.producer_file, cache)
    producer = _one_regex(
        rule.producer_regex,
        producer_source,
        f"producer for {rule.spec}::{anchor.label}",
    )

    return ResolvedSite(
        rule,
        anchor.file,
        value_spans,
        _line(source, site.start()),
        _line(producer_source, producer.start()),
    )


def _validate_exceptions(
    repo: Path,
    anchors: list[Anchor],
    exceptions: dict[str, StableValueException],
    expected_size: int,
    cache: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    if len(exceptions) != expected_size:
        errors.append(
            "UNKNOWN: stable-value exception population changed without its exact "
            f"ratchet ({len(exceptions)} != {expected_size})"
        )
    current = {anchor.fingerprint for anchor in anchors}
    for fingerprint, exception in sorted(exceptions.items()):
        if fingerprint not in current:
            errors.append(f"UNKNOWN: orphan stable-value exception {fingerprint}")
            continue
        if not exception.reason.strip():
            errors.append(f"UNKNOWN: stable-value exception {fingerprint} has no reason")
        try:
            evidence = _read_source(repo, exception.evidence_file, cache)
            _one_regex(
                exception.evidence_regex,
                evidence,
                f"stable-value evidence {fingerprint}",
            )
        except UnknownEvidence as exc:
            errors.append(str(exc))
    return errors


def _validate_rules(rules: tuple[FragileRule, ...]) -> list[str]:
    errors: list[str] = []
    keys = [(rule.spec, rule.label_prefix) for rule in rules]
    if len(keys) != len(set(keys)):
        errors.append("UNKNOWN: duplicate fragile producer registry key")
    for rule in rules:
        fields = {
            "spec": rule.spec,
            "label prefix": rule.label_prefix,
            "producer file": rule.producer_file,
            "producer regex": rule.producer_regex,
            "value selector": rule.value_regex,
            "reason": rule.reason,
        }
        missing = [name for name, value in fields.items() if not value.strip()]
        if missing:
            errors.append(
                "UNKNOWN: fragile producer registry entry "
                f"{rule.spec}::{rule.label_prefix!r} lacks {', '.join(missing)}"
            )
    return errors


def _audit_anchor(
    anchor: Anchor,
    source: str,
    adoption: frozenset[str],
    sites: tuple[ResolvedSite, ...],
    exceptions: dict[str, StableValueException],
) -> Audit:
    try:
        span = _anchor_span(anchor, source)
    except UnknownEvidence as exc:
        return Audit(anchor, State.UNKNOWN, False, str(exc))

    overlaps = [
        site
        for site in sites
        if site.file == anchor.file
        and any(_overlaps(span, value_span) for value_span in site.value_spans)
    ]
    # Duplicate transform-control records can name the same physical site.
    unique: dict[tuple[str, tuple[tuple[int, int], ...]], ResolvedSite] = {
        (site.file, site.value_spans): site for site in overlaps
    }
    if len(unique) > 1:
        return Audit(
            anchor,
            State.UNKNOWN,
            False,
            "UNKNOWN MUTANT ANCHOR REFUSED: anchor overlaps multiple derived sites: "
            f"{anchor.spec}::{anchor.label}",
        )
    if unique:
        site = next(iter(unique.values()))
        where = f"{site.file}:{site.site_line}"
        producer = f"{site.rule.producer_file}:{site.producer_line}"
        if anchor.field == "old":
            return Audit(
                anchor,
                State.FRAGILE,
                False,
                "FRAGILE MUTANT ANCHOR REFUSED: "
                f"{anchor.spec}::{anchor.label} uses literal old at {where}; "
                f"re-derived by {producer} ({site.rule.reason}); use old_regex",
            )
        try:
            _prove_regex_value_generic(anchor, source, span, site.value_spans)
        except UnknownEvidence as exc:
            return Audit(
                anchor,
                State.UNKNOWN,
                False,
                "UNKNOWN MUTANT ANCHOR REFUSED: " + str(exc),
            )
        return Audit(
            anchor,
            State.FRAGILE,
            True,
            f"regex-anchored derived value at {where}; producer {producer}",
        )

    if anchor.fingerprint in adoption:
        return Audit(
            anchor,
            State.STABLE,
            True,
            "unchanged member of the immutable row357 audited census",
        )

    matched = source[span[0] : span[1]]
    if not (
        _VALUE_TOKEN.search(matched)
        or _QUOTED_VALUE.search(matched)
        or _VALUE_OPERATOR.search(matched)
    ):
        return Audit(
            anchor,
            State.STABLE,
            True,
            "new anchor is structural and contains no value-bearing token",
        )
    if anchor.fingerprint in exceptions:
        return Audit(
            anchor,
            State.STABLE,
            True,
            exceptions[anchor.fingerprint].reason,
        )
    return Audit(
        anchor,
        State.UNKNOWN,
        False,
        "UNKNOWN MUTANT ANCHOR REFUSED: "
        f"{anchor.spec}::{anchor.label} contains value-bearing source text, "
        "but no producer or audited stable exception establishes its class",
    )


def _audit_documents(
    repo: Path,
    documents: dict[str, dict],
    adoption: frozenset[str],
    rules: tuple[FragileRule, ...],
    exceptions: dict[str, StableValueException] | None = None,
    exception_max: int = 0,
) -> tuple[list[Audit], list[str]]:
    exceptions = exceptions or {}
    try:
        anchors = _anchors(documents)
    except UnknownEvidence as exc:
        return [], [str(exc)]
    cache: dict[str, str] = {}
    errors = _validate_rules(rules)
    sites: list[ResolvedSite] = []
    for rule in rules:
        try:
            sites.append(_resolve_rule(repo, rule, anchors, cache))
        except UnknownEvidence as exc:
            errors.append(str(exc))
    errors.extend(
        _validate_exceptions(repo, anchors, exceptions, exception_max, cache)
    )
    audits: list[Audit] = []
    for anchor in anchors:
        try:
            source = _read_source(repo, anchor.file, cache)
        except UnknownEvidence as exc:
            audits.append(Audit(anchor, State.UNKNOWN, False, str(exc)))
            continue
        audits.append(
            _audit_anchor(anchor, source, adoption, tuple(sites), exceptions)
        )
    if len(audits) != len(anchors) or not audits:
        errors.append(
            "UNKNOWN: anchor audit did not reconcile its nonzero denominator "
            f"({len(audits)} of {len(anchors)})"
        )
    return audits, errors


def _assert_compliant(audits: list[Audit], errors: list[str]) -> None:
    blocked = [audit.detail for audit in audits if not audit.compliant]
    assert not errors and not blocked, "\n".join([*errors, *blocked])


def _synthetic_document(anchor_field: str, anchor: str, *, label: str) -> dict:
    return {
        "mutants": [
            {
                "label": label,
                "file": "settings.py",
                anchor_field: anchor,
                "new": "TIMEOUT_S = 5",
            }
        ]
    }


def test_a_literal_over_a_registered_derived_value_is_refused(tmp_path):
    (tmp_path / "settings.py").write_text("TIMEOUT_S = 149\n", encoding="utf-8")
    (tmp_path / "measure.py").write_text(
        "# measured worst case times headroom\nMEASURED_TIMEOUT = 149\n",
        encoding="utf-8",
    )
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old", "TIMEOUT_S = 149", label="M1 measured timeout"
        )
    }
    rule = FragileRule(
        "tests/mutants/synthetic.json",
        "M1 ",
        "measure.py",
        r"(?m)^# measured worst case times headroom$",
        r"(?<=TIMEOUT_S = )[0-9]+",
        "the measurement producer recomputes this timeout",
    )
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), (rule,))
    assert len(audits) == 1 and audits[0].state is State.FRAGILE
    with pytest.raises(AssertionError, match="FRAGILE MUTANT ANCHOR REFUSED"):
        _assert_compliant(audits, errors)


def test_a_structural_anchor_passes_as_the_negative_control(tmp_path):
    (tmp_path / "settings.py").write_text(
        "def stable_name():\n    return object()\n", encoding="utf-8"
    )
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old", "def stable_name():", label="M1 stable function scope"
        )
    }
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), ())
    _assert_compliant(audits, errors)
    assert [audit.state for audit in audits] == [State.STABLE]


def test_an_unclassified_value_is_UNKNOWN_and_never_OK(tmp_path):
    (tmp_path / "settings.py").write_text("LIMIT = 7\n", encoding="utf-8")
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old", "LIMIT = 7", label="M1 unexplained limit"
        )
    }
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), ())
    assert len(audits) == 1 and audits[0].state is State.UNKNOWN
    assert not audits[0].compliant
    assert "UNKNOWN MUTANT ANCHOR REFUSED" in audits[0].detail


def test_a_fixed_literal_needs_reasoned_evidence_and_a_visible_ratchet(tmp_path):
    (tmp_path / "settings.py").write_text("PROTOCOL_PORT = 8899\n", encoding="utf-8")
    (tmp_path / "contract.md").write_text(
        "The fixture protocol identity is the fixed local port 8899.\n",
        encoding="utf-8",
    )
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old", "PROTOCOL_PORT = 8899", label="M1 fixed fixture identity"
        )
    }
    anchor = _anchors(documents)[0]
    exceptions = {
        anchor.fingerprint: StableValueException(
            "8899 is a fixed fixture protocol identity, not measured output",
            "contract.md",
            r"(?m)^The fixture protocol identity is the fixed local port 8899\.$",
        )
    }
    audits, errors = _audit_documents(
        tmp_path, documents, frozenset(), (), exceptions, exception_max=1
    )
    _assert_compliant(audits, errors)
    assert audits[0].state is State.STABLE

    _audits, stale_ratchet = _audit_documents(
        tmp_path, documents, frozenset(), (), exceptions, exception_max=0
    )
    assert any("ratchet" in error for error in stale_ratchet)


def test_a_regex_must_survive_a_different_derived_value(tmp_path):
    (tmp_path / "settings.py").write_text("TIMEOUT_S = 149\n", encoding="utf-8")
    (tmp_path / "measure.py").write_text("MEASURED = True\n", encoding="utf-8")
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old_regex", "TIMEOUT_S = 149", label="M1 fake regex"
        )
    }
    rule = FragileRule(
        "tests/mutants/synthetic.json",
        "M1 ",
        "measure.py",
        r"(?m)^MEASURED = True$",
        r"(?<=TIMEOUT_S = )[0-9]+",
        "measurement",
    )
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), (rule,))
    assert not errors
    assert len(audits) == 1 and audits[0].state is State.UNKNOWN
    assert "after derived value" in audits[0].detail
    with pytest.raises(AssertionError, match="UNKNOWN"):
        _assert_compliant(audits, errors)


def test_every_regex_over_a_known_site_faces_the_alternate_proof(tmp_path):
    (tmp_path / "settings.py").write_text("TIMEOUT_S = 149\n", encoding="utf-8")
    (tmp_path / "measure.py").write_text("MEASURED = True\n", encoding="utf-8")
    documents = {
        "tests/mutants/synthetic.json": {
            "mutants": [
                {
                    "label": "M1 registered generic regex",
                    "file": "settings.py",
                    "old_regex": r"TIMEOUT_S = [0-9]+",
                    "new": "TIMEOUT_S = 5",
                },
                {
                    "label": "M2 unregistered literal regex",
                    "file": "settings.py",
                    "old_regex": "TIMEOUT_S = 149",
                    "new": "TIMEOUT_S = 6",
                },
            ]
        }
    }
    rule = FragileRule(
        "tests/mutants/synthetic.json",
        "M1 ",
        "measure.py",
        r"(?m)^MEASURED = True$",
        r"(?<=TIMEOUT_S = )[0-9]+",
        "measurement",
    )
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), (rule,))
    assert not errors
    assert audits[0].state is State.FRAGILE and audits[0].compliant
    assert audits[1].state is State.UNKNOWN and not audits[1].compliant
    assert "after derived value" in audits[1].detail


def test_literal_enumeration_is_not_a_value_generic_regex(tmp_path):
    (tmp_path / "settings.py").write_text("TIMEOUT_S = 149\n", encoding="utf-8")
    (tmp_path / "measure.py").write_text("MEASURED = True\n", encoding="utf-8")
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old_regex",
            r"TIMEOUT_S = (?:149|0|7|42|987654321)",
            label="M1 enumerated probes",
        )
    }
    rule = FragileRule(
        "tests/mutants/synthetic.json",
        "M1 ",
        "measure.py",
        r"(?m)^MEASURED = True$",
        r"(?<=TIMEOUT_S = )[0-9]+",
        "measurement",
    )
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), (rule,))
    assert not errors
    assert len(audits) == 1 and audits[0].state is State.UNKNOWN
    assert "after derived value" in audits[0].detail


@pytest.mark.parametrize(
    "source_text",
    [
        'BUILD_VERSION = "release-candidate"',
        'EXPECTED_GATES = {"alpha", "beta"}',
        "MODE = release_candidate",
    ],
)
def test_unexplained_string_or_rhs_values_are_UNKNOWN(tmp_path, source_text):
    (tmp_path / "settings.py").write_text(source_text + "\n", encoding="utf-8")
    documents = {
        "tests/mutants/synthetic.json": _synthetic_document(
            "old",
            source_text,
            label="M1 unexplained string or RHS value",
        )
    }
    audits, errors = _audit_documents(tmp_path, documents, frozenset(), ())
    assert not errors
    assert len(audits) == 1 and audits[0].state is State.UNKNOWN
    assert not audits[0].compliant
    assert "UNKNOWN MUTANT ANCHOR REFUSED" in audits[0].detail


def test_the_immutable_adoption_population_is_complete():
    documents = _adoption_documents()
    anchors = _anchors(documents)
    assert len(documents) == 212
    assert len(anchors) == 1169
    assert sum(anchor.field == "old_regex" for anchor in anchors) == 3


def test_the_adoption_pin_advanced_only_by_audited_addition():
    """The one legitimate pin move is proved, not asserted.

    Absorbing a population without a per-anchor reason is only honest while
    this gate is unmerged AND the absorbed anchors are provably outside every
    registered derived-value site.  Both halves are measured here, so a later
    author cannot quietly widen the census past a fragile anchor: the addition
    must be pure, its size exact, and no absorbed anchor may resolve FRAGILE.
    """
    before = _tree_documents(_REPO, _PREADOPTION_TREE)
    after = _adoption_documents()
    assert before and after

    shared = sorted(set(before) & set(after))
    assert len(shared) == len(before), "the pin advance dropped an audited spec"
    for spec in shared:
        assert before[spec] == after[spec], (
            f"{spec} changed under the adoption pin advance; a modified spec is "
            "not an audited addition"
        )
    added = sorted(set(after) - set(before))
    assert added == sorted(_ABSORBED_SPECS), added

    before_fingerprints = {anchor.fingerprint for anchor in _anchors(before)}
    absorbed = [
        anchor
        for anchor in _anchors(after)
        if anchor.fingerprint not in before_fingerprints
    ]
    assert len(absorbed) == _ABSORBED_ANCHORS, len(absorbed)
    assert {anchor.spec for anchor in absorbed} == set(_ABSORBED_SPECS)

    # Audit the live tree with an EMPTY census so absorption cannot mask a
    # producer overlap, then read only the absorbed anchors' verdicts.
    audits, errors = _audit_documents(
        _REPO,
        _current_documents(),
        frozenset(),
        _FRAGILE_RULES,
        _STABLE_VALUE_EXCEPTIONS,
        _STABLE_VALUE_EXCEPTION_MAX,
    )
    assert not errors, errors
    absorbed_fingerprints = {anchor.fingerprint for anchor in absorbed}
    judged = [
        audit
        for audit in audits
        if audit.anchor.fingerprint in absorbed_fingerprints
    ]
    assert len(judged) == _ABSORBED_ANCHORS, len(judged)
    fragile = [audit for audit in judged if audit.state is State.FRAGILE]
    assert not fragile, [audit.detail for audit in fragile]
    # The absorption is load-bearing: without it these anchors are not silently
    # OK.  A census that absorbed only already-structural anchors would prove
    # nothing about the rule it is standing in for.
    unknown = [audit for audit in judged if audit.state is State.UNKNOWN]
    assert len(unknown) == 69, len(unknown)


def test_an_unavailable_adoption_tree_is_UNKNOWN(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with pytest.raises(UnknownEvidence, match="UNKNOWN: immutable row357 adoption"):
        _adoption_documents(tmp_path)


def test_the_preconversion_literal_population_is_RED():
    """Replay the original anchor forms without writing any subject file."""
    adoption_documents = _adoption_documents()
    adoption_anchors = _anchors(adoption_documents)
    replay = copy.deepcopy(_current_documents())
    converted = 0
    for rule in _FRAGILE_RULES:
        historical = _rule_anchor(rule, adoption_anchors)
        if historical.field == "old_regex":
            continue  # rows 329 and 348 were already converted before row 357
        candidates = [
            mutant
            for mutant in replay[rule.spec]["mutants"]
            if mutant["label"].startswith(rule.label_prefix)
        ]
        assert len(candidates) == 1, (rule.spec, rule.label_prefix)
        mutant = candidates[0]
        pattern = mutant.pop("old_regex")
        source = (_REPO / mutant["file"]).read_text(encoding="utf-8")
        mutant["old"] = _one_regex(pattern, source, "preconversion replay").group(0)
        converted += 1
    assert converted == 46

    adoption = frozenset(anchor.fingerprint for anchor in adoption_anchors)
    audits, errors = _audit_documents(
        _REPO,
        replay,
        adoption,
        _FRAGILE_RULES,
        _STABLE_VALUE_EXCEPTIONS,
        _STABLE_VALUE_EXCEPTION_MAX,
    )
    assert not errors
    refused = [
        audit
        for audit in audits
        if audit.state is State.FRAGILE and not audit.compliant
    ]
    assert len(refused) == 46
    assert all(
        audit.detail.startswith("FRAGILE MUTANT ANCHOR REFUSED:")
        for audit in refused
    )
    with pytest.raises(AssertionError, match="FRAGILE MUTANT ANCHOR REFUSED"):
        _assert_compliant(audits, errors)


def test_every_tracked_mutant_anchor_has_an_honest_classification():
    adoption_documents = _adoption_documents()
    adoption = frozenset(
        anchor.fingerprint for anchor in _anchors(adoption_documents)
    )
    current = _current_documents()
    audits, errors = _audit_documents(
        _REPO,
        current,
        adoption,
        _FRAGILE_RULES,
        _STABLE_VALUE_EXCEPTIONS,
        _STABLE_VALUE_EXCEPTION_MAX,
    )
    # 49 -> 48 at row 531: row348::M4 retired with its subject, not dropped
    # silently. See the comment beside the removed _family entry above.
    assert len(_FRAGILE_RULES) == 48, "the measured fragile denominator drifted"
    _assert_compliant(audits, errors)
    assert sum(audit.state is State.FRAGILE for audit in audits) >= 48
    assert all(audit.state is not State.UNKNOWN for audit in audits)


def _base_mutant(documents: dict[str, dict], anchor: Anchor) -> dict:
    matches = [
        mutant
        for mutant in documents[anchor.spec]["mutants"]
        if mutant["label"] == anchor.label and mutant["file"] == anchor.file
    ]
    assert len(matches) == 1, (anchor.spec, anchor.label)
    return matches[0]


def _semantic_intent(rule: FragileRule, before: str, replacement: str) -> None:
    spec = Path(rule.spec).name
    if spec.startswith("row281_"):
        assert "receipt = run_vitest" in before and "receipt = None" in replacement
    elif spec.startswith("row297_"):
        assert "assert metrics ==" in before and "assert set(metrics)" in replacement
    elif spec.startswith("row325_"):
        assert "reap_seconds=9" in before and "reap_seconds=3" in replacement
    elif spec.startswith("row329_"):
        assert "13_262" in before and "5_000" in replacement
    elif spec.startswith("row338_"):
        old = int(re.search(rule.value_regex, before).group().replace("_", ""))
        new = int(re.search(r"(?<=timeout=)[0-9]+(?=[),])", replacement).group())
        assert new > old
    elif spec.startswith("row339_"):
        old = int(re.search(rule.value_regex, before).group().replace("_", ""))
        numbers = [int(value.replace("_", "")) for value in re.findall(r"[0-9][0-9_]*", replacement)]
        assert numbers and numbers[-1] < old
    # row348_ had a branch here for M4; the mutant was retired at row 531 with
    # the literal it severed, so no rule for that spec reaches this function.
    elif spec.startswith("v3_66_1111_"):
        assert "5400" in before and "200" in replacement
    elif spec.startswith("v3_66_1204_"):
        assert "test_v3_66_1054" in before and "omitted" in replacement
    elif spec.startswith("v3_66_1226_"):
        assert replacement in {
            "_CONTENTION_FACTOR = 1.0",
            "_GOVERNING_BOUND_S = 600.0",
            '"registration_receipt_drift_before_go_refuses_release/wait":                 (0.5, 7),',
            "_MIN_BUDGET_S = 0.0",
        }
    elif spec.startswith("v3_66_1231_"):
        assert replacement in {
            "W1_CLEANUP_SECONDS=$W1_GATE_SECONDS",
            "_W1_RUNNER_STRETCH_FACTOR = 1.0",
            "_W1_SETTLEMENT_MEASURED_S = 0.1",
            "_W1_RUNNER_RESERVE_S = 0.0",
        }
    elif spec.startswith("v3_66_1239_"):
        assert "unknown.append" in before and "pass  # nothing to report" in replacement
    elif spec.startswith("v3_66_1241_"):
        assert replacement in {
            "W1_OWNER_OBSERVATION_SECONDS=1\n",
            "_W1_OBSERVATION_MEASURED_S = 0.0300\n",
        }
    else:  # pragma: no cover - a registry addition must add its semantic proof
        raise AssertionError(f"no intent proof for {spec}")


def test_every_converted_spec_resolves_once_and_preserves_its_original_intent():
    current_documents = _current_documents()
    current_anchors = _anchors(current_documents)
    adoption_documents = _adoption_documents()
    for rule in _FRAGILE_RULES:
        anchor = _rule_anchor(rule, current_anchors)
        assert anchor.field == "old_regex", (
            f"{anchor.spec}::{anchor.label} remains a literal fragile anchor"
        )
        source = (_REPO / anchor.file).read_text(encoding="utf-8")
        match = _one_regex(anchor.text, source, f"converted {anchor.spec}::{anchor.label}")
        before = match.group(0)
        base = _base_mutant(adoption_documents, anchor)
        base_fields = set(base) & {"old", "old_regex"}
        assert len(base_fields) == 1
        base_text = base[next(iter(base_fields))]
        assert before.endswith("\n") == base_text.endswith("\n"), (
            f"{anchor.spec}::{anchor.label} changed its historical newline boundary"
        )
        assert anchor.new == base["new"], (
            f"{anchor.spec}::{anchor.label} changed its original literal mutation"
        )
        mutated = source[: match.start()] + anchor.new + source[match.end() :]
        assert mutated != source
        assert mutated[match.start() : match.start() + len(anchor.new)] == anchor.new
        assert anchor.new != before
        _semantic_intent(rule, before, anchor.new)
