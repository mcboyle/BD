"""v3.66.939 -- the CI gate lane is sharded, and a shard can silently lose a file.

WHY THE LANE WAS SPLIT. `ci.yml`'s own comment set the rule on 2026-08-03:
"81 tests, 52s -- keep it under a minute; if it grows past that, SPLIT rather
than silently dropping files, because a truncated list here reads as coverage
it does not have." Re-measured 2026-08-07 at v3.66.938: 161 tests, 140s in CI.

Per-file timings on this container, which are what the shard boundaries are
drawn from -- the lane is not evenly distributed and a split by COUNT would
have missed that entirely:

    test_toolchain_534                 72.5s     <- 40% of the lane alone
    test_gui_parity                    30.6s
    test_import_graph_no_new_edges     16.6s
    test_v3_66_653_dep_freshness       11.2s
    test_route_index_in_sync           10.8s
    the remaining ten, combined        38.1s
                                      ------
                                      179.8s

So `test_toolchain_534` gets a shard to itself; no two-way split could have put
every lane under the budget while that file stayed whole, and profiling it
shows 59s of its 68s in four subprocess-heavy tests that walk the 240-tool
suite -- not a cheap win, and not safe to trim.

WHAT THIS FILE GUARDS, AND IT IS NOT THE TIMING. Sharding introduces exactly
one new failure mode, and it is the one the original comment named: a file that
falls out of every shard still leaves a GREEN tick. Nothing else in the tree
would notice -- the job passes, the check is green, and the gate that was
supposed to run simply did not. That is a denominator quietly shrinking, which
is the defect class CLAUDE.md section 0 is entirely about.

The assertions are therefore about COVERAGE, never about duration:

  * the union of the shards is exactly the declared set -- a drop fails, and so
    does an addition nobody declared;
  * no file appears in two shards, because a duplicate inflates the apparent
    coverage while the real one may still be missing;
  * every named path exists and is tracked, because `pytest tests/typo.py`
    exits non-zero but a path that merely MOVED would be a silent no-op if the
    runner were ever made lenient;
  * the declared set is non-empty, because every assertion above passes
    vacuously over an empty list.

DELIBERATELY NOT ASSERTED: how long any shard takes. A timing assertion here
would fail on a slow runner -- a gate firing on identity rather than content,
which CLAUDE.md section 0 counts as a soundness bug of equal weight. The budget
is a rule for humans reading the comment, not a test.
"""
from __future__ import annotations

import ast
import io
import json
import re
import runpy
import shlex
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

yaml = pytest.importorskip(
    "yaml",
    reason="PyYAML is declared in requirements-test.txt; a missing import here "
           "means the test environment is unprovisioned, not that the workflow "
           "is correct")

_REPO = Path(__file__).resolve().parent.parent
_CI = _REPO / ".github" / "workflows" / "ci.yml"

# Files that cannot share one serial Actions runner without recreating the
# measured measurement-tools long pole. This is a scheduling constraint, not a
# duration assertion: runner speed may vary, while putting two serial files
# back in one shard always adds their durations.
_INDEPENDENT_LONG_POLES = {
    "tests/test_v3_66_1043_measurement_and_fleet_tools.py",
    # @1241 row 237. The split half is a long pole in its own right:
    # it carries the slowest registration-lifecycle nodes of the
    # module it came out of.
}

# Its subject is which gates CI runs, which is a property of the tree.
BD_GATE_SCOPE = "repo-wide"

# The gates that must run on every PR. DERIVED from the tracked tree -- every
# tests/test*.py whose own BD_GATE_SCOPE says "repo-wide" -- plus the closed
# legacy remainder _NON_DERIVABLE_DECLARED below, and NEVER from ci.yml,
# because deriving the expectation from the thing under test is how a dropped
# file passes: the union would simply shrink to match. The markers are not the
# thing under test; ci.yml is. Adding a repo-wide gate to CI is therefore a
# TWO-part change: its scope marker and one workflow shard entry. Until row 810
# this file carried a third copy of that fact, a 323-entry literal that every
# landed train forced every in-flight cut to rebase over. The binding itself
# sits after the marker reader (_scope_map) it is built from.
#
# The explicit shard claims that lived beside those entries are kept here
# verbatim: _ci_shard_claims reads them from COMMENT tokens of this file and
# test_every_explicit_shard_claim_is_bound_in_ci judges each against ci.yml.
# CI-SHARD-CLAIM version-at-land version-at-land tests/test_row_version_at_land.py
# CI-SHARD-CLAIM row-659 gates-rows-b tests/test_row659_witness_run_does_not_leak_capture_state.py
# CI-SHARD-CLAIM test2D-1 application-safety tests/test_test2d_1_persistent_profile_cookie_jar.py
# CI-SHARD-CLAIM row-703 application-safety tests/test_row703_ssrf_transport_is_installed_everywhere.py
# CI-SHARD-CLAIM row-703 application-safety tests/test_row703_a_proxy_shadows_the_guarded_transport.py
# CI-SHARD-CLAIM row-703 application-safety tests/test_row703_the_site_to_policy_map_is_asserted.py
# CI-SHARD-CLAIM row-806 application-safety tests/test_row806_health_payload_names_the_deployed_cloak_state.py
# CI-SHARD-CLAIM row-785 application-safety tests/test_row785_login_evidence_filenames_are_shell_safe.py
# CI-SHARD-CLAIM row-780 application-safety tests/test_row780_sites_config_listing_url_round_trip.py
# CI-SHARD-CLAIM row-772 application-safety tests/test_row772_astra_settling.py
# CI-SHARD-CLAIM row-705 mutation-tools tests/test_row705_published_denominators.py
# CI-SHARD-CLAIM row-797 mutation-tools tests/test_row797_three_login_seams_carry_durable_mutant_pins.py
# CI-SHARD-CLAIM row-820 mutation-tools tests/test_row810_spec_collection_slice_0.py
# CI-SHARD-CLAIM row-700 gates-rows-b tests/test_row700_captcha_egress_disclosure.py
# CI-SHARD-CLAIM row-723 application-safety tests/test_row723_login_flow_channel_fallback_is_filed_under_its_site.py
# CI-SHARD-CLAIM row-741 application-safety tests/test_row741_relogin_refusals_are_typed.py
# CI-SHARD-CLAIM row-750 application-safety tests/test_row750_ipv6_unwrapped_metadata_bypass.py
# CI-SHARD-CLAIM row-772 application-safety tests/test_row772_rejected_login_is_not_success.py
# CI-SHARD-CLAIM row-774 application-safety tests/test_row774_login_submit_refuses_cross_origin_navigation.py
# CI-SHARD-CLAIM row-709 safety-censuses tests/test_row709_state_seed_is_not_a_verdict.py
# CI-SHARD-CLAIM row-678 gates-rows-b tests/test_row678_bandcheck_exclusion_tables.py
# CI-SHARD-CLAIM receipts gates-rows-b tests/test_row724_preflight_bandcheck_counts_failures.py
# CI-SHARD-CLAIM row-267 application-safety tests/test_app_measurements_fail_closed.py
# CI-SHARD-CLAIM row-434 application-safety tests/test_row434_resume_cannot_leave_the_hold_state_it_set.py
# CI-SHARD-CLAIM row-284 application-safety tests/test_v3_66_284_integrity.py
# CI-SHARD-CLAIM row-645 application-safety tests/test_v3_62_2_guards.py
# CI-SHARD-CLAIM row-507 application-safety tests/test_row492_a_release_proves_what_it_frees.py
# CI-SHARD-CLAIM campaign-loginsession download-chain tests/test_login_session_does_not_cover_the_scene_host.py
# CI-SHARD-CLAIM row-663 download-chain tests/test_row663_inspect_rung_matches_runner.py
# CI-SHARD-CLAIM row-666 download-chain tests/test_row666_candidates_inspect_prefers_caller_url.py
# CI-SHARD-CLAIM row-672 download-chain tests/test_row672_reviewed_template_is_reachable.py
# CI-SHARD-CLAIM row-673 download-chain tests/test_row673_reviewed_probe_adapter_ships_once.py
# CI-SHARD-CLAIM row-674 download-chain tests/test_row674_live_state_round_trips.py
# CI-SHARD-CLAIM footgun-import-dodge gates-named-a tests/test_import_dodge_is_caught_in_the_cut_diff.py
# CI-SHARD-CLAIM footgun-endpoint artifacts-pins tests/test_endpoint_catalog_in_sync.py
# CI-SHARD-CLAIM footgun-function-index artifacts-pins tests/test_function_index_in_sync.py
# CI-SHARD-CLAIM footgun-route-map artifacts-pins tests/test_route_map_invariant.py
# CI-SHARD-CLAIM footgun-slice5 artifacts-pins tests/test_settings_center_slice5.py
# CI-SHARD-CLAIM footgun-ci-link artifacts-pins tests/test_footgun_detectors_are_executed_by_ci.py
# CI-SHARD-CLAIM row-240 parity-graph tests/test_v3_66_1240_supervisor_settings_seeded.py
# CI-SHARD-CLAIM row-386 download-chain tests/test_row386_the_download_chain_is_gated.py
# CI-SHARD-CLAIM row-761 download-chain tests/test_row761_listing_facet_is_not_a_download_candidate.py
# CI-SHARD-CLAIM row-761b download-chain tests/test_row761b_deep.py
# CI-SHARD-CLAIM row-761 download-chain tests/test_row761_astra_acceptance.py
# CI-SHARD-CLAIM row-1157 gates-v3-a tests/test_v3_66_1157_build_output_is_from_this_attempt.py
# CI-SHARD-CLAIM row-1035 gates-rows-a tests/test_row331_guarded_imports_are_declared.py
# Row 648. The react-router 7 migration gate: declared range, lock, zero
# react-router-dom specifiers under frontend/src, and no dependabot MAJOR
# ignore. Static (no node), so it rides parity-static beside dep_freshness.
# CI-SHARD-CLAIM row-648 gates-rows-b tests/test_row648_react_router_7_migration.py
# CI-SHARD-CLAIM row-753 gates-rows-b tests/test_row753_a_run_records_its_own_outcome.py
# CI-SHARD-CLAIM row-245 gates-v3-b tests/test_v3_66_1255_test_roots_publish_ownership_atomically.py
# CI-SHARD-CLAIM row-1452 gates-v3-b tests/test_v3_66_1452_a_shuffle_lane_finds_order_dependencies.py
# CI-SHARD-CLAIM row-1085 gates-v3-a tests/test_v3_66_1085_module_identity_survives_a_sys_modules_patch.py
# CI-SHARD-CLAIM row-689 gates-rows-b tests/test_row689_install_linux_converges_test_manifest.py
# CI-SHARD-CLAIM row-717 gates-rows-b tests/test_row717_fresh_host_documents_cut_quality.py
# CI-SHARD-CLAIM row-817 gates-rows-a tests/test_row817_astra_ack_consumption.py
# Row 728 follow-up: the redirect Location header is quoted stdlib-style
# (iso-8859-1) before the logical host is restored, so a non-ASCII redirect
# no longer raises UnicodeEncodeError inside the pinned opener. Repo-wide, so
# it rides the same shard as the pin contract it extends.
# CI-SHARD-CLAIM row-728 gates-rows-b tests/test_row728_astra_boundaries.py
#
# One retired gate's WHY is kept because a test pins the prose: @1215 judges
# two PRODUCTION toolchain scripts -- bd-wedge-hunt's remote transport and
# bd-run's cap declaration -- and both are module-scoped, so without its
# _NON_DERIVABLE_DECLARED entry a regression would be caught by nothing in CI.

# A PARTITION, NOT A COUNT (rows 569/570, superseding the row-531 floor).
#
# This block first held two exact-count literals -- one for the declared gate
# census and one for the seven-member H15 safety family -- behind eighty lines
# of bump comments ("208 -> 209", "233 -> 234"). Row 531 replaced the census
# literal with a monotonic floor, which stopped the chore but bought a blind
# window instead: at v3.66.1388 len(_DECLARED) was 236 against a floor of 235,
# and because a floor is never RAISED on growth that window widened by one with
# every gate added. Every other assertion in this file is a relative comparison
# between _DECLARED and ci.yml, so both sides of one deletion move together and
# only the ratchet can see it -- and the ratchet had a spare notch.
#
# The exact total never answered the question it was written for either: its
# stated purpose was to make a SAME-SIZE SUBSTITUTION visible, and a
# substitution leaves the total unchanged.
#
# So the census is now a PARTITION of the declared set into a half that derives
# itself from the tracked tree and a half that cannot:
#
#   derived   {tracked tests/test*.py declaring BD_GATE_SCOPE = "repo-wide"}
#             -- grows on its own, no literal; since row 810 it IS the
#             derived half of _DECLARED (_derived_repo_wide), not a set a
#             second list must be kept in step with
#   remainder _NON_DERIVABLE_DECLARED below -- a CLOSED set, pinned by IDENTITY
#             rather than by count, exactly as gate_scope_baseline.txt is
#
# The identities and not the count is the whole point: a same-size swap inside
# the remainder is named, and a member deleted from _DECLARED and ci.yml in one
# commit is named. Adding a gate never edits this block, because a new gate
# declares `repo-wide` and lands in the derived half. This set may only SHRINK,
# and only when a legacy gate is deliberately retired or promoted to a marker.
# H622 / O1330 is a named exception: restore the nine omitted module acceptance
# suites below without misclassifying their subjects as repo-wide.
#
# WHAT IT STILL CANNOT SEE, stated because an instrument that hides its blind
# spots is worse than none. Any within-tree derivation moves with a coordinated
# edit, so a repo-wide gate whose marker is flipped to `module` in the SAME
# commit that removes it from _DECLARED and from ci.yml leaves the derived half
# smaller with nothing to compare against -- as does deleting the file outright.
# Both are three-part deliberate edits sitting in the diff; the failure this
# gate addresses is forgetting, not evasion. Closing either one needs a
# comparison against a merge base, which is a different instrument than this
# file and is not attempted here.
# The H15 floor is KEPT, deliberately, and it is not the chore row 531 retired:
# that family is a closed seven-member population no ordinary cut grows, so the
# floor has zero slack today and growth never edits it. Its members are pinned
# by identity in _CONFIRMED_SAFETY_GATES below; the number only refuses a silent
# emptying of the set.
_CONFIRMED_SAFETY_GATE_FLOOR = 7

_NON_DERIVABLE_DECLARED = {
    # H622 / O1330: explicit restoration of nine landed acceptance modules.
    # Their subject remains module-scoped; CI scheduling is independently
    # required. This named expansion of the legacy registry is pinned by
    # test_h622_ci_test_census.py; ordinary new repo-wide gates use the marker.
    "tests/test_adaptive_chunk_sizing.py",  # required module acceptance
    "tests/test_esxi_vm_clone_harness.py",  # required module acceptance
    "tests/test_inmemory_sqlite_fixture.py",  # required module acceptance
    "tests/test_local_wheelhouse.py",  # required module acceptance
    "tests/test_proactive_token_refresh.py",  # required module acceptance
    "tests/test_shard_rebalancer.py",  # required module acceptance
    "tests/test_sparse_worktree.py",  # required module acceptance
    "tests/test_video_dedup.py",  # required module acceptance
    "tests/test_zero_copy_assembly.py",  # required module acceptance
    "tests/test_row667_login_attempt_accounting.py",  # module
    "tests/test_row740_login_cap_writer_atomicity.py",  # module
    "tests/test_row785_login_evidence_filenames_are_shell_safe.py",  # module
    "tests/test_row797_three_login_seams_carry_durable_mutant_pins.py",  # module
    "tests/test_row806_health_payload_names_the_deployed_cloak_state.py",  # module
    # H622 slice A retired one line here deliberately, which the partition
    # above requires to be a named act rather than a silent deletion. The file
    # was a legacy-baseline entry -- declared here because nothing in the tree
    # could re-derive it -- and it now carries a repo-wide marker of its own,
    # so it sits in the DERIVED half instead. Keeping both would leave
    # _declared_partition naming it no longer declared. Its workflow entry is
    # untouched, so _DECLARED and the CI union are unchanged.
    "tests/test_app_measurements_fail_closed.py",  # module
    "tests/test_ffmpeg_capability_health.py",  # module
    "tests/test_gui_parity.py",  # legacy-baseline
    "tests/test_import_graph_no_new_edges.py",  # legacy-baseline
    "tests/test_no_test_writes_the_repo_plugins_dir.py",  # legacy-baseline
    "tests/test_pin_index_in_sync.py",  # legacy-baseline
    "tests/test_provider_resolve_surface_lock.py",  # legacy-baseline
    "tests/test_route_index_in_sync.py",  # legacy-baseline
    "tests/test_row311_app_config_writers_are_serialized.py",  # module
    "tests/test_row345_opv_a11y_requires_served_cockpit.py",  # module
    "tests/test_row350_job_api_durable_truth.py",  # module
    "tests/test_row356_cookie_quality_reports_unknown.py",  # module
    "tests/test_row089_capture_corpus_backup_restore.py",  # module
    "tests/test_row700_captcha_egress_disclosure.py",  # module
    "tests/test_row723_login_flow_channel_fallback_is_filed_under_its_site.py",  # module
    "tests/test_row741_relogin_refusals_are_typed.py",  # module
    "tests/test_row750_ipv6_unwrapped_metadata_bypass.py",  # module
    "tests/test_row772_rejected_login_is_not_success.py",  # module
    "tests/test_row774_login_submit_refuses_cross_origin_navigation.py",  # module
    "tests/test_row360_turnstile_bypass_is_installed.py",  # module
    "tests/test_rows617_623_624_625_628_629_631_secrets_family.py",  # module
    "tests/test_row363_affordance_learning.py",  # module
    "tests/test_row434_resume_cannot_leave_the_hold_state_it_set.py",  # module
    "tests/test_row_282_bd_opv_isolates_every_store.py",  # module
    "tests/test_settings_center_slice4.py",  # legacy-baseline
    "tests/test_v3_57_phase9.py",  # module
    "tests/test_v3_62_2_guards.py",  # legacy-baseline
    "tests/test_v3_66_1034_guards_survive_a_module_wipe.py",  # legacy-baseline
    "tests/test_v3_66_1043_measurement_and_fleet_tools.py",  # legacy-baseline
    "tests/test_v3_66_1157_build_output_is_from_this_attempt.py",  # module
    "tests/test_v3_66_1178_orphan_tempfiles_are_recursive.py",  # module
    "tests/test_v3_66_1208_the_heartbeat_keeps_foreground_signal_semantics.py",  # module
    "tests/test_v3_66_1209_every_detached_launch_keeps_signal_semantics.py",  # module
    "tests/test_v3_66_1215_a_wrapper_must_not_alter_its_subject.py",  # module
    "tests/test_v3_66_1216_vitest_is_a_real_ci_denominator.py",  # module
    "tests/test_v3_66_121_login_flow_derives_the_observed_drive.py",  # module
    "tests/test_v3_66_261_contended_lifecycle_lock.py",  # module
    "tests/test_v3_66_284_integrity.py",  # module
}
_CONFIRMED_SAFETY_GATES = {
    "tests/test_capture_execution_lanes.py",
    "tests/test_capture_csrf_diag_redacts_cookies.py",
    "tests/test_home_config_stores_are_guarded.py",
    "tests/test_no_raw_unicode_escape_in_jsx.py",
    "tests/test_v3_66_285_cloak_parity.py",
    "tests/test_v3_66_795_mod3_seam.py",
    "tests/test_v3_66_1009_live_results_are_bundled.py",
}

# Row 613. These are module-scoped behavioural tests, not repo-wide census
# gates. Their safety value is in the family: together they guard db_prune and
# the skip-identity/dedup ownership seam, so a module-derived local band is not
# sufficient CI reachability. Keep this denominator independent of ci.yml; the
# workflow is the artifact it judges. This deliberately does not join
# _DECLARED or the shrink-only _NON_DERIVABLE_DECLARED partition.
_DB_PRUNE_SAFETY_FAMILY = {
    "tests/test_a_prune_repairs_only_the_links_it_broke.py",
    "tests/test_a_skip_must_prove_it_is_the_same_work.py",
    "tests/test_row544_the_dedup_preflight_asks_the_ownership_question.py",
    "tests/test_row545_the_skip_arm_carries_its_whole_result.py",
    "tests/test_row607_a_history_row_proves_a_real_transfer.py",
}

# ── the declaration policy, @1072 ────────────────────────────────────────────
#
# WHY A MARKER AND NOT A PREDICATE. The obvious fix is to DERIVE the repo-wide
# set from source shape and compare it against _DECLARED. It does not work, and
# the measurement is the reason rather than an opinion. Against the eight files
# that have actually gone undeclared (944, 947, 1031, 1034, 1062, 1064, 1067,
# 1068), measured at v3.66.1071 over AST call nodes:
#
#     a real `git ls-files` call argument            catches 3 of 8
#     that, plus naming repo infrastructure in code  catches 4 of 8
#         (and the second widens the candidate pool from 34 files to 136,
#          so it costs a 124-entry exemption list to buy one more hit)
#
# 947, 1031, 1067 and 1068 carry NO structural signal separating them from an
# ordinary feature test. The @1035 note in this very set says as much in prose
# -- "repo-wide despite not looking it" -- and that is a property of the class,
# not a gap in the predicates tried. A gate is repo-wide because of what it
# ASSERTS ABOUT, which no reader of its syntax can recover.
#
# So the class is not derivable, and the decision is the author's. What a gate
# CAN do is refuse to let the decision go unmade: every tracked test file must
# either carry a BD_GATE_SCOPE or sit in the frozen legacy baseline, and a file
# that calls itself repo-wide is thereby in _DECLARED, which the union
# assertion then forces into a shard.
#
# WHAT THIS DOES NOT CATCH, stated here because an instrument that hides its
# blind spots is worse than none: nothing verifies that a "module" answer is
# HONEST. A repo-wide gate mislabelled `module` passes every assertion in this
# file. The policy converts a silent omission into a forced decision; it does
# not check the decision. Nor does it reach the 1314 baselined files -- 26 of
# which make a real `git ls-files` call and are in no shard (recorded as
# backlog row 99).
_SCOPE_MARKER = "BD_GATE_SCOPE"
_VALID_SCOPES = {"repo-wide", "module"}
_BASELINE = _REPO / "tests" / "gate_scope_baseline.txt"

# The baseline may only shrink. Pinned at adoption; classifying a file removes
# its line. A count alone cannot stop a swap -- delete one line, add another --
# but that is a deliberate edit visible in the diff, and the failure this gate
# exists for is forgetting, not evasion.
_BASELINE_MAX = 1314


def _workflow() -> dict:
    return yaml.safe_load(_CI.read_text("utf-8"))


def _runs_the_shard_resolver(job: dict) -> bool:
    return any("ci_shards.py" in str(step.get("run", ""))
               for step in (job.get("steps") or []) if isinstance(step, dict))


def _gate_suite_job() -> tuple[str, dict]:
    """The one matrix job whose run step hands pytest the resolved shard."""
    for job_name, job in ((_workflow().get("jobs") or {}).items()):
        if _runs_the_shard_resolver(job):
            return str(job_name), job
    pytest.fail("no sharded gate job found to check")


def _shard_lists() -> dict[str, list[str]]:
    """{shard name: [test paths]} exactly as CI will resolve them.

    O1264(d)/O1265(g): ci.yml carries shard NAMES only. tools/ci_shards.py
    partitions the declared gate census at run time and the gate-suites run
    step invokes it, so the resolver is read here (never the run block, for the
    same reason as before: a grep counts a path named in a comment).
    tests/test_ci_shards.py holds the matrix names and the resolver's names to
    each other.
    """
    from tools import ci_shards
    return ci_shards.shards(_REPO)


_SHARD_CLAIM_PREFIX = "CI-SHARD-CLAIM"


def _ci_shard_claims(source: str | None = None) -> list[tuple[str, str, str]]:
    """Return (claim id, shard, suite) from explicit source comments only."""
    text = (Path(__file__).read_text(encoding="utf-8")
            if source is None else source)
    claims: list[tuple[str, str, str]] = []
    malformed: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.COMMENT:
            continue
        comment = token.string.removeprefix("#").strip()
        if not comment.startswith(_SHARD_CLAIM_PREFIX):
            continue
        fields = comment.split()
        if len(fields) != 4 or fields[0] != _SHARD_CLAIM_PREFIX:
            malformed.append(f"line {token.start[0]}: {token.string}")
            continue
        _prefix, claim_id, shard, suite = fields
        claims.append((claim_id, shard, suite))
    assert not malformed, (
        "malformed CI shard claim(s) are UNKNOWN rather than ignored: "
        f"{malformed}")
    return claims


def _unbound_named_shard_claims(
        source: str, shard_names: set[str]
) -> list[str]:
    """Named ``<matrix-name> shard`` prose lacking an explicit suite binding."""
    unbound: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        comment = token.string.removeprefix("#").strip()
        if comment.startswith(_SHARD_CLAIM_PREFIX):
            continue
        for shard in sorted(shard_names):
            name = re.escape(shard)
            if (re.search(rf"\b{name}\b\s+shard\b", comment)
                    or re.search(rf"\bshard\b[^.]*\b{name}\b", comment)):
                unbound.append(f"line {token.start[0]}: {token.string}")
    return unbound


def _shard_claim_mismatches(
        claims: list[tuple[str, str, str]],
        shards: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Claim diagnostics grouped by durable claim id, never source line."""
    locations: dict[str, list[str]] = {}
    for shard, suites in shards.items():
        for suite in suites:
            locations.setdefault(suite, []).append(shard)

    mismatches: dict[str, list[str]] = {}
    for claim_id, claimed_shard, suite in claims:
        listed = locations.get(suite, [])
        if listed != [claimed_shard]:
            mismatches.setdefault(claim_id, []).append(
                f"{suite}: claimed={claimed_shard!r}, listed={listed!r}")
    return mismatches


def _tracked(rel: str) -> bool:
    return subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel],
                          cwd=str(_REPO), capture_output=True).returncode == 0


def _tracked_test_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", "tests/test*.py"],
                         cwd=str(_REPO), capture_output=True, text=True, check=True)
    return sorted(out.stdout.split())


def _baseline_entries() -> set[str]:
    if not _BASELINE.is_file():
        return set()
    return {ln.strip() for ln in _BASELINE.read_text("utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")}


def _declared_scope(path: Path | str):
    """The module-level BD_GATE_SCOPE value, or None if the file declares none.

    AST, and MODULE SCOPE ONLY, so the marker has to be an assignment that
    actually executes. A docstring, a comment or an assertion message naming it
    answers nothing -- which matters here more than usual, because the policy
    block above names the marker a dozen times and this function is pointed at
    its own file. CLAUDE.md section 0: a comment is inside the denominator of
    every gate that reads source text.

    The raw-substring pre-filter is sound in the only direction it is used: a
    file that never mentions the name cannot assign it, so skipping the parse
    cannot manufacture a False negative. It is there because this runs over
    every tracked test file.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if _SCOPE_MARKER not in text:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    scope = None
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == _SCOPE_MARKER for t in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant):
            scope = value.value
        else:
            scope = f"<non-literal: {type(value).__name__}>"
    return scope


def _scope_map(paths) -> dict[str, object]:
    """{path: declared scope} for every path that declares one."""
    out = {}
    for rel in paths:
        scope = _declared_scope(_REPO / rel)
        if scope is not None:
            out[rel] = scope
    return out


@pytest.mark.parametrize(("first", "final"), [
    ("module", "repo-wide"),
    ("repo-wide", "module"),
])
def test_declared_scope_uses_the_final_module_assignment(tmp_path, first, final):
    """Python's last module assignment is the effective marker binding."""
    marker = tmp_path / "test_marker.py"
    marker.write_text(
        f"BD_GATE_SCOPE = {first!r}\nBD_GATE_SCOPE = {final!r}\n",
        encoding="utf-8",
    )
    assert _declared_scope(marker) == final


def _derived_repo_wide() -> set[str]:
    """Every tracked test file that calls itself repo-wide, read from the tree.

    Row 810. This is the half of the declared census that derives itself; it
    reads `git ls-files` and each file's own BD_GATE_SCOPE and never ci.yml,
    so a shard that drops one of these files still leaves it in the
    expectation and the union assertion names it missing.
    """
    return {rel for rel, scope in _scope_map(_tracked_test_files()).items()
            if scope == "repo-wide"}


def _process_tests() -> set[str]:
    """Tests listed in PROCESS_TESTS.txt: run in nightly/train, not in gate-suites."""
    pt_path = _REPO / "tests" / "PROCESS_TESTS.txt"
    if not pt_path.is_file():
        return set()
    out = set()
    for line in pt_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


# The three PRODUCT-touching signals PROCESS_TESTS.txt's header promises this
# file re-derives (F4-B F2, RULING-0045 item 3). A file may sit in the PROCESS
# lane only if its text carries none of them; anything else is a product
# invariant and stays in its hot shard.
_PRODUCT_IMPORT = re.compile(r"^\s*(?:from\s+bulk_downloader\b|import\s+bulk_downloader\b)", re.M)
# (b) is the whole-string form import_module / monkeypatch / sys.modules take
# ("bulk_downloader.app"), not a module name mentioned inside a prose message.
_PRODUCT_DOTTED = re.compile(r"[\"']bulk_downloader(?:\.[A-Za-z_][A-Za-z0-9_]*)+[\"']")
_PRODUCT_EXEC = re.compile(
    r"python3?\s+-m\s+bulk_downloader\b"
    r"|[\"']-m[\"']\s*,\s*[\"']bulk_downloader[\"']"
    r"|\bbdctl\b"
    r"|exec\s+\./capture\.sh"
    r"|subprocess\.(?:run|Popen|check_call|check_output|call)\([^\n]*capture\.sh"
)


def _product_signals(text: str) -> list[str]:
    hits = []
    if _PRODUCT_IMPORT.search(text):
        hits.append("(a) imports bulk_downloader")
    if _PRODUCT_DOTTED.search(text):
        hits.append("(b) quotes a dotted bulk_downloader module name")
    if _PRODUCT_EXEC.search(text):
        hits.append("(c) runs the product as a subprocess")
    return hits


def test_process_tests_never_touch_the_product():
    """Membership in PROCESS_TESTS.txt is measured, not keyword-guessed: every
    listed file is free of the three product-touching signals, and the probe
    can say YES (positive control: a product test trips it)."""
    control = "from bulk_downloader import app\n"
    assert _product_signals(control), "positive control: the probe cannot see a product import"
    assert _product_signals('importlib.import_module("bulk_downloader.db")\n')
    assert _product_signals("subprocess.run(['python', '-m', 'bulk_downloader'])\n")
    process = sorted(_process_tests())
    assert process, "PROCESS_TESTS.txt declares nothing"
    offenders = {}
    for rel in process:
        path = _REPO / rel
        assert path.is_file(), f"{rel} is listed as a PROCESS test but is not in the tree"
        hits = _product_signals(path.read_text(encoding="utf-8"))
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "PROCESS tests that touch the product (they belong in a hot shard): "
        + json.dumps(offenders, indent=1)
    )


def _declared_gates() -> set[str]:
    """The declared gate census: derived repo-wide files plus the closed
    legacy remainder that predates the markers, minus process tests
    (O934.1: process tests run in nightly, not in per-PR gate-suites)."""
    return (_derived_repo_wide() | _NON_DERIVABLE_DECLARED) - _process_tests()


# Bound at import so the shrink tests can monkeypatch one census, and so the
# consumers that judge THIS file (rows 353 and 531) keep a module-level set.
_DECLARED = _declared_gates()


# The three policy comparisons, EXTRACTED so they can be driven with synthetic
# inputs. At adoption every live call below is nearly vacuous -- five files
# carry a marker and 1314 sit in the baseline -- so without a positive control
# a comparison could be severed from its inputs and every assertion would still
# pass. That is the escape a mutation battery found in `_coverage_delta` one
# cut after this file was written.

def _unclassified(tracked, baseline, scopes) -> list[str]:
    """Tracked test files that neither sit in the baseline nor declare a scope."""
    return sorted(t for t in tracked if t not in baseline and t not in scopes)


def _invalid_scopes(scopes) -> list[str]:
    return sorted(f"{p} = {s!r}" for p, s in scopes.items() if s not in _VALID_SCOPES)


def _repo_wide_not_declared(scopes, declared) -> list[str]:
    return sorted(p for p, s in scopes.items()
                  if s == "repo-wide" and p not in declared)


# ── coverage ─────────────────────────────────────────────────────────────────

def test_every_explicit_shard_claim_matches_the_matrix():
    """A shard claim in this gate is evidence only when CI agrees with it."""
    claims = _ci_shard_claims()
    assert claims, "the explicit CI shard claim denominator is empty"
    claim_ids = {claim_id for claim_id, _shard, _suite in claims}
    suites = [suite for _claim_id, _shard, suite in claims]
    assert claim_ids, "the durable shard-claim identity denominator is empty"
    assert len(suites) == len(set(suites)), (
        "one suite has multiple explicit shard claims, so its evidence is ambiguous")
    assert all((_REPO / suite).is_file() for suite in suites), (
        "an explicit shard claim binds to no suite path, so its verdict is UNKNOWN")
    undeclared = sorted(set(suites) - _DECLARED)
    assert not undeclared, (
        f"explicit shard claim(s) bind to undeclared suites: {undeclared}")

    shards = _shard_lists()
    claimed_shards = {shard for _claim_id, shard, _suite in claims}
    unknown_shards = sorted(claimed_shards - set(shards))
    assert not unknown_shards, (
        f"explicit claims name shard(s) absent from the matrix: {unknown_shards}")
    source = Path(__file__).read_text(encoding="utf-8")
    unbound = _unbound_named_shard_claims(source, set(shards))
    assert not unbound, (
        "named shard prose has no suite-path binding and is UNKNOWN: "
        f"{unbound}")

    mismatches = _shard_claim_mismatches(claims, shards)
    assert not mismatches, f"CI shard claim mismatch group(s): {mismatches}"


def test_a_wrong_but_real_shard_claim_fails_exactly_once():
    """Negative control: one injected stale claim is distinctly diagnosed."""
    claims = _ci_shard_claims()
    shards = _shard_lists()
    assert claims and len(shards) > 1
    locations = {
        suite: shard
        for shard, suites in shards.items()
        for suite in suites
    }
    assert all(suite in locations for _claim_id, _shard, suite in claims), (
        "precondition: a claimed suite is absent before the negative control")
    corrected = [(claim_id, locations[suite], suite)
                 for claim_id, _shard, suite in claims]
    assert _shard_claim_mismatches(corrected, shards) == {}, (
        "precondition: the corrected control population is not clean")

    first_id, actual, first_suite = corrected[0]
    wrong = next(shard for shard in sorted(shards) if shard != actual)
    mutant = list(corrected)
    mutant[0] = (first_id, wrong, first_suite)
    assert sum(left != right for left, right in zip(corrected, mutant)) == 1, (
        "precondition: the injected condition did not fire exactly once")
    mismatches = _shard_claim_mismatches(mutant, shards)
    assert list(mismatches) == [first_id]
    assert len(mismatches[first_id]) == 1
    assert first_suite in mismatches[first_id][0]
    assert wrong in mismatches[first_id][0] and actual in mismatches[first_id][0]


def test_ordinary_toolchain_prose_is_not_a_shard_claim():
    """English naming a tool class must not enter the claim denominator."""
    source = Path(__file__).read_text(encoding="utf-8")
    suite = "tests/test_v3_66_1215_a_wrapper_must_not_alter_its_subject.py"
    assert "two PRODUCTION toolchain scripts" in source
    claims = _ci_shard_claims(source)
    assert claims, "precondition: the explicit claim denominator is empty"
    assert suite not in {path for _claim_id, _shard, path in claims}
    shards = _shard_lists()
    listed_in = [name for name, suites in shards.items() if suite in suites]
    assert len(listed_in) == 1, f"{suite} is scheduled in {listed_in}"
    assert _shard_claim_mismatches(
        [("ordinary-prose-control", listed_in[0], suite)],
        shards,
    ) == {}
    unbound = _unbound_named_shard_claims(
        "# two PRODUCTION toolchain scripts\n"
        "# scheduled in the parity-static shard\n",
        {"toolchain", "parity-static"},
    )
    assert len(unbound) == 1
    assert "parity-static shard" in unbound[0]


def test_shard_claim_reader_ignores_string_literals():
    source = (
        '"""# CI-SHARD-CLAIM prose wrong tests/test_prose.py"""\n'
        "# CI-SHARD-CLAIM real-id real-shard tests/test_real.py\n"
    )
    assert _ci_shard_claims(source) == [
        ("real-id", "real-shard", "tests/test_real.py")]
    with pytest.raises(AssertionError, match="malformed CI shard claim"):
        _ci_shard_claims("# CI-SHARD-CLAIM missing-fields\n")


def test_the_shards_exist_at_all():
    """RED on pristine: there is no matrix, so there is nothing to cover with."""
    shards = _shard_lists()
    assert shards, (
        "no sharded gate job found in ci.yml -- expected a job whose "
        "run step hands pytest a shard resolved by tools/ci_shards.py. Without "
        "it every assertion below passes over an empty set.")
    assert len(shards) >= 2, (
        f"found {len(shards)} shard(s); a one-shard 'split' is the unsplit lane "
        f"wearing a matrix.")


def _coverage_delta(declared: set[str], got: set[str]) -> tuple[list[str], list[str]]:
    """(missing, extra) between the declared gate set and what the shards name.

    EXTRACTED SO IT CAN BE TESTED DIRECTLY. A mutation battery severed each of
    these two comparisons from its meaning -- `declared - got` became `got - got`,
    and `got - declared` became a constant -- and NO test noticed either, because
    the only assertions about them lived inside the test being mutated. A
    detector with no detector, which is the same escape @938 closed one cut ago
    and the reason it is worth extracting on sight rather than after a battery.
    """
    return sorted(declared - got), sorted(got - declared)


def _declared_partition(declared: set[str]) -> tuple[list[str], list[str]]:
    """(no_longer_declared, unexpected_in_remainder) for the census partition.

    EXTRACTED so it can be driven with synthetic inputs, for the reason
    `_coverage_delta` was: a comparison whose only assertions live inside the
    test being mutated is a detector with no detector.

    `declared` minus the DERIVED half -- every tracked test file that declares
    `BD_GATE_SCOPE = "repo-wide"` -- must be exactly the closed legacy set
    `_NON_DERIVABLE_DECLARED`. Growth lands in the derived half and touches no
    literal; a legacy member deleted from _DECLARED and ci.yml together is named
    by the first list; a repo-wide marker downgraded to `module` while its file
    stays declared is named by the second.
    """
    scopes = _scope_map(_tracked_test_files())
    derived = {rel for rel, scope in scopes.items() if scope == "repo-wide"}
    remainder = set(declared) - derived
    return (sorted(_NON_DERIVABLE_DECLARED - remainder),
            sorted(remainder - _NON_DERIVABLE_DECLARED))


def _assert_exact_gate_coverage(
        declared: set[str], shards: dict[str, list[str]]
) -> None:
    """Assert a nonzero, one-to-one declaration/execution population.

    The expected size is the DECLARED set itself, not a literal a human keeps in
    step by hand (row 531). The anti-shrink ratchet is no longer a count with
    slack; it is the identity partition asserted by the live gate below.
    """
    executed = [suite for suites in shards.values() for suite in suites]
    missing, extra = _coverage_delta(declared, set(executed))
    duplicate_count = len(executed) - len(set(executed))

    assert declared, "the declared gate denominator is empty, so this proves nothing"
    expected_count = len(declared)
    assert expected_count > 0, "the expected gate denominator must be nonzero"
    assert len(executed) == expected_count, (
        f"CI would execute {len(executed)} gate paths, expected exactly "
        f"{expected_count}; missing from CI: {missing}; extra in CI: {extra}; "
        f"duplicate entries: {duplicate_count}")
    assert duplicate_count == 0, (
        f"CI repeats {duplicate_count} gate path(s), so its {len(executed)} "
        "executions do not cover that many distinct declarations")
    assert not missing, f"declared gate(s) missing from CI: {missing}"
    assert not extra, f"undeclared gate(s) present in CI: {extra}"


def _assert_family_reachable_from_shards(
        family: set[str], shards: dict[str, list[str]]
) -> None:
    """Require every member of one closed behavioural family in named shards."""
    assert family, "the db-prune safety family denominator is empty"
    assert shards, "there are no named CI shards, so reachability is UNKNOWN"
    assert all(shards), "a CI shard has no name, so reachability is UNKNOWN"
    scheduled = {suite for suites in shards.values() for suite in suites}
    missing = sorted(family - scheduled)
    reachable = len(family) - len(missing)
    assert reachable == len(family), (
        f"db-prune safety family reachable from CI: {reachable} of "
        f"{len(family)}; missing: {missing}")


def test_db_prune_safety_family_is_reachable_from_ci():
    """Row 613: all five module-scope owners must run in an explicit shard."""
    assert len(_DB_PRUNE_SAFETY_FAMILY) == 5, (
        "the independently named db-prune safety denominator is not exactly "
        f"five files: {sorted(_DB_PRUNE_SAFETY_FAMILY)}")
    for rel in sorted(_DB_PRUNE_SAFETY_FAMILY):
        assert (_REPO / rel).is_file(), f"family member is absent: {rel}"
        assert _tracked(rel), f"family member is untracked: {rel}"
        assert _declared_scope(_REPO / rel) == "module", (
            f"{rel} is not a module-scope behavioural test")
    _assert_family_reachable_from_shards(
        _DB_PRUNE_SAFETY_FAMILY, _shard_lists())


def test_db_prune_safety_family_missing_member_control():
    """The intended 0/5 refusal fires on a nonempty, named synthetic shard."""
    assert len(_DB_PRUNE_SAFETY_FAMILY) == 5
    with pytest.raises(AssertionError, match=(
            r"db-prune safety family reachable from CI: 0 of 5; missing:")):
        _assert_family_reachable_from_shards(
            _DB_PRUNE_SAFETY_FAMILY,
            {"unrelated-only": ["tests/test_unrelated.py"]})


def test_unrelated_absent_file_does_not_expand_db_prune_safety_family():
    """Negative control: this is family-specific, not a full CI census."""
    outsider = "tests/test_negative.py"
    shards = _shard_lists()
    assert (_REPO / outsider).is_file(), "negative-control file is absent"
    assert _tracked(outsider), "negative-control file is untracked"
    assert outsider not in _DB_PRUNE_SAFETY_FAMILY
    assert all(outsider not in suites for suites in shards.values()), (
        "negative-control file unexpectedly runs in CI")
    scheduled_family = {
        suite for suites in shards.values() for suite in suites
        if suite in _DB_PRUNE_SAFETY_FAMILY
    }
    assert scheduled_family == _DB_PRUNE_SAFETY_FAMILY
    _assert_family_reachable_from_shards(_DB_PRUNE_SAFETY_FAMILY, shards)


def test_transform_control_imports_gate_without_judging_db_prune_reachability():
    """Mutation control: collection/import alone does not judge row 613."""
    assert _CI.is_file()


def test_a_new_declared_gate_missing_from_a_shard_fails_the_exact_check():
    """Negative control: the live assertion's intended failure is reachable."""
    declared = {"tests/test_existing_gate.py", "tests/test_newly_added_gate.py"}
    shards = {"only-shard": ["tests/test_existing_gate.py"]}

    with pytest.raises(AssertionError, match=(
            r"CI would execute 1 gate paths, expected exactly 2; "
            r"missing from CI: \['tests/test_newly_added_gate.py'\]")):
        _assert_exact_gate_coverage(declared, shards)


def test_declared_and_ci_executed_gate_denominators_are_exact():
    """All declared gates and the row-613 family form the CI population."""
    assert _CONFIRMED_SAFETY_GATES, "the confirmed H15 safety-gate set is empty"
    assert len(_CONFIRMED_SAFETY_GATES) >= _CONFIRMED_SAFETY_GATE_FLOOR, (
        "the confirmed H15 safety-gate denominator shrank below "
        f"{_CONFIRMED_SAFETY_GATE_FLOOR}: {sorted(_CONFIRMED_SAFETY_GATES)}")
    wrong_scopes = sorted(
        f"{rel}: {_declared_scope(_REPO / rel)!r}"
        for rel in _CONFIRMED_SAFETY_GATES
        if _declared_scope(_REPO / rel) != "repo-wide"
    )
    assert not wrong_scopes, (
        "confirmed H15 safety gate(s) do not declare repo-wide scope: "
        f"{wrong_scopes}")
    missing_required = sorted(_CONFIRMED_SAFETY_GATES - _DECLARED)
    assert not missing_required, (
        "confirmed safety gate(s) remain undeclared and therefore unreachable "
        f"from every CI shard: {missing_required}")

    # Rows 569/570. The census ratchet, by identity rather than by count.
    assert _NON_DERIVABLE_DECLARED, (
        "the closed legacy declaration set is empty, so the partition below "
        "would accept any population at all")
    scopes = _scope_map(_tracked_test_files())
    derived = {rel for rel, scope in scopes.items() if scope == "repo-wide"}
    assert derived, (
        "no tracked test file declares repo-wide scope, so the derived half of "
        "the census collapsed and this ratchet proves nothing")
    gone, strayed = _declared_partition(_DECLARED)
    assert not gone, (
        f"gate(s) no longer declared: {gone}. They were the legacy half of the "
        f"census -- the half nothing in the tree can re-derive -- so removing "
        f"them from _DECLARED and from ci.yml together leaves every relative "
        f"comparison in this file satisfied and CI green. Retiring one is a "
        f"deliberate act: delete its line from _NON_DERIVABLE_DECLARED in the "
        f"same cut and say why.")
    assert not strayed, (
        f"declared gate(s) that neither declare {_SCOPE_MARKER} = 'repo-wide' "
        f"nor sit in the closed legacy set: {strayed}. A new gate declares the "
        f"marker; it does not join the legacy set, which may only shrink.")

    _assert_exact_gate_coverage(
        _DECLARED | _DB_PRUNE_SAFETY_FAMILY, _shard_lists())


def test_the_live_gate_refuses_a_silent_shrink_of_both_lists(monkeypatch):
    """Rows 569/570. THE control this cut exists for: plant the exact shrink.

    A gate that is deleted from _DECLARED and from ci.yml in ONE commit moves
    both sides of every relative comparison in this file together, so coverage,
    uniqueness and membership all stay satisfied. Only a ratchet can see it, and
    an integer ratchet with slack sees nothing until the slack is spent -- at
    v3.66.1388 len(_DECLARED) was 236 against a floor of 235, and the slack grew
    by one with every gate added.

    The victim is chosen from the part of the population that is NOT derivable
    from the tracked tree (a member declaring `module` or sitting in the frozen
    legacy baseline), because that is the half no other assertion reaches: a
    repo-wide member dropped from _DECLARED alone is already named by
    test_every_repo_wide_file_is_in_the_declared_set.
    """
    scopes = _scope_map(_tracked_test_files())
    derived = {rel for rel, scope in scopes.items() if scope == "repo-wide"}
    assert derived, (
        "precondition: no tracked test file declares repo-wide scope, so the "
        "derived half of the partition is empty and this control proves nothing")
    victims = sorted(_DECLARED - derived)
    assert victims, (
        "precondition: every declared gate is derivable from its own marker, so "
        "there is no non-derivable member to plant a shrink with")
    victim = victims[0]

    # The victim must not be able to launder the verdict through an EARLIER
    # refusal in the live assertion (CLAUDE.md A5).
    assert victim not in _CONFIRMED_SAFETY_GATES, (
        f"precondition: {victim} is an H15 safety gate, so dropping it would "
        f"fail on the missing_required assertion instead of on the ratchet")

    shards = _shard_lists()
    assert any(victim in suites for suites in shards.values()), (
        f"precondition: {victim} is declared but in no shard, so the tree is "
        f"already broken and this control would pass for the wrong reason")

    shrunk_declared = _DECLARED - {victim}
    shrunk_shards = {name: [s for s in suites if s != victim]
                     for name, suites in shards.items()}
    assert len(_DECLARED) - len(shrunk_declared) == 1, "the plant removed no declaration"
    before = sum(len(s) for s in shards.values())
    after = sum(len(s) for s in shrunk_shards.values())
    assert before - after == 1, "the plant removed no shard entry"

    monkeypatch.setattr(sys.modules[__name__], "_DECLARED", shrunk_declared)
    monkeypatch.setattr(sys.modules[__name__], "_shard_lists", lambda: shrunk_shards)
    with pytest.raises(AssertionError, match="no longer declared"):
        test_declared_and_ci_executed_gate_denominators_are_exact()


def test_the_shrink_control_passes_an_unshrunk_population(monkeypatch):
    """Negative control for the control: the same harness, nothing removed.

    Without this, a ratchet widened into refusing EVERYTHING would still make
    the test above green.
    """
    shards = _shard_lists()
    monkeypatch.setattr(sys.modules[__name__], "_DECLARED", set(_DECLARED))
    monkeypatch.setattr(sys.modules[__name__], "_shard_lists", lambda: dict(shards))
    test_declared_and_ci_executed_gate_denominators_are_exact()


def test_the_census_partition_actually_compares(monkeypatch):
    """Positive control for the ratchet above: every outcome is reachable.

    Synthetic populations whose answer is not in doubt, so a partition severed
    from its inputs cannot pass. Without this the live call is nearly vacuous --
    it returns two empty lists on a healthy tree, which is also what a broken
    comparison returns.
    """
    fake = {"tests/test_new_gate.py": "repo-wide",
            "tests/test_old_module.py": "module"}
    monkeypatch.setattr(sys.modules[__name__], "_tracked_test_files",
                        lambda: sorted(fake))
    monkeypatch.setattr(sys.modules[__name__], "_scope_map", lambda paths: fake)
    monkeypatch.setattr(sys.modules[__name__], "_NON_DERIVABLE_DECLARED",
                        {"tests/test_old_module.py"})

    healthy = {"tests/test_new_gate.py", "tests/test_old_module.py"}
    assert _declared_partition(healthy) == ([], []), (
        "the partition refuses a healthy population, so the live gate is "
        "failing for the wrong reason")

    # GROWTH. A second brand-new repo-wide gate joins the derived half and
    # edits no literal -- this is the 2026-08-31 chore staying dead.
    fake["tests/test_another_gate.py"] = "repo-wide"
    assert _declared_partition(healthy | {"tests/test_another_gate.py"}) == ([], []), (
        "declaring a new repo-wide gate demanded a literal edit; the ratchet "
        "has become the chore it replaced")

    # SHRINK. The legacy member leaves _DECLARED and ci.yml in one commit.
    assert _declared_partition({"tests/test_new_gate.py"}) == (
        ["tests/test_old_module.py"], []), (
        "a silently dropped legacy gate was not named")

    # STRAY. A module-scope suite wired into CI without joining the closed set.
    assert _declared_partition(healthy | {"tests/test_stray.py"}) == (
        [], ["tests/test_stray.py"]), (
        "an undeclared-by-marker suite entered the census unnamed")


def test_transform_control_imports_ci_gate_without_judging_row348_reachability():
    """Mutation transform control: collection/import alone judges no gate."""
    assert _CI.is_file()


def test_transform_control_imports_ci_gate_without_judging_row645_membership():
    """Mutation control: import alone does not judge guard-file scheduling."""
    assert _CI.is_file()


def test_transform_control_imports_ci_gate_without_judging_row810_derivation():
    """Mutation control: import alone does not judge the derived census."""
    assert _CI.is_file()


def test_the_coverage_comparison_actually_compares():
    """The positive control for the gate below.

    Synthetic sets whose answer is not in doubt: one declared-but-absent, one
    named-but-undeclared, one present in both. If either direction stops
    depending on its inputs, the gate underneath it is decoration.
    """
    missing, extra = _coverage_delta({"a", "shared"}, {"shared", "c"})
    assert missing == ["a"], (
        f"the declared-but-absent direction returned {missing!r}; a gate that "
        f"cannot see a dropped suite is the whole failure mode of sharding.")
    assert extra == ["c"], (
        f"the named-but-undeclared direction returned {extra!r}; the two lists "
        f"could drift with only one of them being read.")

    same = {"x", "y"}
    assert _coverage_delta(same, set(same)) == ([], []), (
        "identical sets reported a delta -- the gate would fire on every clean "
        "tree and be switched off, which CLAUDE.md section 0 counts as a "
        "soundness bug of equal weight to a false clean.")


def test_the_shard_union_is_exactly_the_declared_gate_set():
    """The assertion the whole file exists for.

    A dropped file leaves CI green while the gate does not run. Nothing else in
    the tree notices, which is why this is pinned against a set declared here
    rather than against ci.yml itself.
    """
    union: list[str] = []
    for names in _shard_lists().values():
        union.extend(names)
    got = set(union)

    expected = _DECLARED | _DB_PRUNE_SAFETY_FAMILY
    missing, extra = _coverage_delta(expected, got)
    assert not missing, (
        f"repo-wide gate(s) declared but in NO shard, so they no longer run on "
        f"any PR while the check stays green: {missing}")
    assert not extra, (
        f"shard(s) name suite(s) that are not in the declared gate set: "
        f"{extra}. Give them a repo-wide {_SCOPE_MARKER} marker, or remove "
        f"them -- an "
        f"undeclared entry means the two lists have drifted and only one of "
        f"them is being read.")


def test_no_suite_is_listed_in_two_shards():
    """A duplicate inflates apparent coverage and wastes the budget the split
    exists to respect."""
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for shard, names in _shard_lists().items():
        for n in names:
            if n in seen:
                dupes.append(f"{n} (in {seen[n]} and {shard})")
            seen[n] = shard
    assert not dupes, f"suite(s) listed in more than one shard: {dupes}"


def test_measured_serial_long_poles_have_independent_runners():
    """A split only reduces the budget when its long poles run independently."""
    locations = {
        suite: shard
        for shard, suites in _shard_lists().items()
        for suite in suites
        if suite in _INDEPENDENT_LONG_POLES
    }
    missing = sorted(_INDEPENDENT_LONG_POLES - set(locations))
    assert not missing, f"measured long-pole suite(s) absent from CI: {missing}"

    by_shard: dict[str, list[str]] = {}
    for suite, shard in locations.items():
        by_shard.setdefault(shard, []).append(suite)
    collisions = {
        shard: sorted(suites)
        for shard, suites in by_shard.items()
        if len(suites) > 1
    }
    assert not collisions, (
        "measured serial long poles were recombined in one runner, restoring "
        f"the shard budget defect: {collisions}")


def test_every_sharded_suite_exists_and_is_tracked():
    """A path that moved would run nothing. pytest exits non-zero on a missing
    file today, but that is the runner's behaviour and not a property this
    workflow states."""
    bad = []
    for shard, names in _shard_lists().items():
        for n in names:
            if not (_REPO / n).is_file():
                bad.append(f"{n} ({shard}): not on disk")
            elif not _tracked(n):
                bad.append(f"{n} ({shard}): untracked")
    assert not bad, "sharded suite path(s) that cannot run:\n  " + "\n  ".join(bad)


def test_the_declared_set_is_not_empty():
    """Every assertion above is vacuous over an empty declaration."""
    assert _DECLARED, "the declared gate set is empty; this file proves nothing"
    for rel in sorted(_DECLARED):
        assert (_REPO / rel).is_file(), (
            f"{rel} is declared as a repo-wide gate but is not in the "
            f"checkout -- fix the declaration rather than letting the union "
            f"assertion fail for the wrong reason.")


# ── the split must not have cost the setup the lane depends on ───────────────

def test_the_shard_job_checks_out_full_history():
    """bd-freshcheck (inside test_toolchain_534) resolves the register's close
    tip with `merge-base --is-ancestor`.

    Under a shallow checkout that commit is absent and the exit code is 128 --
    "I cannot see it", which is a different thing from 1, "not in this history".
    The gates job carries `fetch-depth: 0` for this reason; a shard job running
    the same suite needs it too, and losing it in the split would be a silent
    downgrade.
    """
    wf = _workflow()
    for job_name, job in (wf.get("jobs") or {}).items():
        include = (((job.get("strategy") or {}).get("matrix") or {})
                   .get("include") or [])
        if not _runs_the_shard_resolver(job):
            continue
        checkouts = [s for s in (job.get("steps") or [])
                     if str(s.get("uses", "")).startswith("actions/checkout")]
        assert checkouts, f"{job_name} never checks out the repository"
        depths = [(s.get("with") or {}).get("fetch-depth") for s in checkouts]
        assert 0 in depths, (
            f"{job_name} does not set fetch-depth: 0. bd-freshcheck's "
            f"close-tip ancestry check needs full history; without it the "
            f"answer is UNKNOWN and the failure reads as a stale register.")
        return
    pytest.fail("no sharded gate job found to check")


def test_the_shard_job_installs_runtime_dependencies():
    """The suites import the product. A shard that skips the install fails for
    an environmental reason that reads as a real defect (CLAUDE.md section 5)."""
    ci = _CI.read_text("utf-8")
    wf = _workflow()
    for job_name, job in (wf.get("jobs") or {}).items():
        if not _runs_the_shard_resolver(job):
            continue
        body = "\n".join(str(s.get("run", "")) for s in (job.get("steps") or []))
        assert "requirements.txt" in body, (
            f"{job_name} never installs requirements.txt")
        assert "requirements-test.txt" in body, (
            f"{job_name} never installs requirements-test.txt -- PyYAML and the "
            f"test-only dependencies live there, and this very file "
            f"importorskips on one of them, so the omission would present as a "
            f"SKIP rather than a failure.")
        return
    pytest.fail("no sharded gate job found to check")


def test_every_gate_suite_has_the_canonical_per_test_timeout():
    """A wedged gate must name the node before the whole job cap fires.

    The repository's local experiment uses this exact pytest-timeout contract.
    CI is serial rather than xdist, but a blocking select/read/wait is still the
    same hang and must retain the same diagnostic boundary.
    """
    job_name, job = _gate_suite_job()
    run_steps = [str(step.get("run", "")) for step in (job.get("steps") or [])
                 if "pytest" in str(step.get("run", ""))]
    assert run_steps, f"{job_name} has no pytest run step"
    for command in run_steps:
        tokens = shlex.split(command)
        assert "--timeout=240" in tokens, (
            f"{job_name} pytest has no 240-second per-test bound: {command!r}")
        assert "--timeout-method=signal" in tokens, (
            f"{job_name} pytest cannot expose a hung test's thread stacks: "
            f"{command!r}")


def test_the_gate_suite_job_has_a_bounded_outer_lifetime():
    """pytest-timeout lives inside pytest; the Actions job owns pytest itself."""
    job_name, job = _gate_suite_job()
    minutes = job.get("timeout-minutes")
    assert isinstance(minutes, int) and not isinstance(minutes, bool), (
        f"{job_name} has no integer timeout-minutes outer bound: {minutes!r}")
    assert 0 < minutes <= 60, (
        f"{job_name} timeout-minutes={minutes!r} is not a useful outer hang "
        "guard; the default Actions bound is six hours")


# ── the declaration policy, @1072 ────────────────────────────────────────────

def test_the_scope_reader_answers_from_code_not_prose(tmp_path):
    """The reader is EXECUTED against real files, never inspected as text.

    Every case below is a file this test writes and the reader then parses.
    Source-text assertions about a source-reading function were the shape that
    escaped three mutation batteries on 2026-08-12: a check that reads the
    implementation agrees with whatever the implementation says.

    tmp_path rather than tests/, deliberately -- PIN_INDEX's regen globs
    tests/*.py and races a file created there even briefly.
    """
    def probe(body: str):
        f = tmp_path / "probe.py"
        f.write_text(body, encoding="utf-8")
        return _declared_scope(f)

    assert probe('BD_GATE_SCOPE = "repo-wide"\n') == "repo-wide"
    assert probe('BD_GATE_SCOPE: str = "module"\n') == "module", (
        "an annotated assignment executes exactly like a plain one; reading "
        "only ast.Assign would let a real declaration go unseen")

    assert probe('"""BD_GATE_SCOPE = \\"repo-wide\\" is how you declare."""\n') is None, (
        "a DOCSTRING describing the marker was accepted as a declaration. That "
        "is the trap the policy block above would spring on this very file, "
        "which names the marker a dozen times in prose.")
    assert probe('# BD_GATE_SCOPE = "repo-wide"\n') is None, (
        "a commented-out declaration was accepted")
    assert probe('MSG = "set BD_GATE_SCOPE = repo-wide"\n') is None, (
        "a message string quoting the marker was accepted")
    assert probe("import os\n") is None

    assert probe('def f():\n    BD_GATE_SCOPE = "repo-wide"\n') is None, (
        "a marker inside a function body never executes at import and must "
        "not count; module scope is the whole point")

    assert str(probe("BD_GATE_SCOPE = SOMETHING\n")).startswith("<non-literal"), (
        "a non-literal value must be reported rather than silently treated as "
        "absent -- it is a malformed declaration, which is a third state")


def test_the_policy_predicates_actually_compare():
    """Positive controls with synthetic sets whose answers are not in doubt.

    The live assertions below are near-vacuous at adoption, so each of these
    three comparisons could be severed from its inputs today and every live
    gate would stay green.
    """
    assert _unclassified(["a.py", "b.py", "c.py"], {"a.py"}, {"b.py": "module"}) == ["c.py"], (
        "the unclassified predicate stopped depending on its inputs; a new "
        "test file would then never be asked to classify itself")
    assert _unclassified(["a.py"], {"a.py"}, {}) == []
    assert _unclassified(["a.py"], set(), {"a.py": "module"}) == []

    assert _invalid_scopes({"a.py": "repo-wide", "b.py": "module"}) == []
    assert _invalid_scopes({"a.py": "repowide"}) == ["a.py = 'repowide'"], (
        "a typo'd scope must fail rather than read as one of the valid values")

    assert _repo_wide_not_declared({"a.py": "repo-wide"}, set()) == ["a.py"]
    assert _repo_wide_not_declared({"a.py": "repo-wide"}, {"a.py"}) == []
    assert _repo_wide_not_declared({"a.py": "module"}, set()) == [], (
        "a module-scoped file was demanded of _DECLARED; the gate would fire "
        "on every clean tree and be switched off")


def test_every_tracked_test_file_is_classified_or_baselined():
    """The forced decision. A new test file must say what it is.

    This is the assertion the whole policy exists for, and it is the one that
    would have caught all eight historical misses -- not because it recognises
    a repo-wide gate, which is not derivable, but because it refuses to let the
    question go unanswered.
    """
    tracked = _tracked_test_files()
    assert tracked, "no tracked test files found; every assertion here is vacuous"
    baseline = _baseline_entries()
    scopes = _scope_map(tracked)

    missing = _unclassified(tracked, baseline, scopes)
    assert not missing, (
        f"{len(missing)} tracked test file(s) declare no {_SCOPE_MARKER} and are "
        f"not in {_BASELINE.name}:\n  " + "\n  ".join(missing) + "\n\n"
        f"Add one of {sorted(_VALID_SCOPES)} at module scope. 'repo-wide' means "
        f"the gate's subject is the tree rather than a module, so it holds "
        f"whatever the diff touched and belongs in CI -- the marker declares "
        f"it; add it to a gate-suites shard in the same cut. 'module' means an "
        f"ordinary test. Do not add the file to the baseline: that list is "
        f"frozen legacy and may only shrink.")

    bad = _invalid_scopes(scopes)
    assert not bad, (
        f"{_SCOPE_MARKER} must be one of {sorted(_VALID_SCOPES)}:\n  "
        + "\n  ".join(bad))


def test_every_repo_wide_file_is_in_the_declared_set():
    """A file that calls itself repo-wide must be declared, which the union
    assertion above then forces into a shard.

    This is the walk-forward the author gets for free: one marker in the file
    they are already writing, and the gate names the other edit. Since row 810
    the live census is derived from these very markers, so on the real tree
    this holds by construction; the comparison itself is kept because the
    synthetic-input tests above drive _repo_wide_not_declared, and because a
    binding that stopped reading the markers would surface here first.
    """
    scopes = _scope_map(_tracked_test_files())
    repo_wide = {p for p, s in scopes.items() if s == "repo-wide"}
    assert repo_wide, (
        "no file declares itself repo-wide, so this assertion is vacuous -- at "
        "adoption five do, and a drop to zero means the marker was renamed or "
        "the reader broke")

    undeclared = _repo_wide_not_declared(scopes, _DECLARED | _process_tests())
    assert not undeclared, (
        f"file(s) declaring {_SCOPE_MARKER} = 'repo-wide' but absent from "
        f"_DECLARED (and not in PROCESS_TESTS.txt), so they run on no PR "
        f"while the check stays green: {undeclared}")


def test_the_baseline_is_frozen_legacy_that_may_only_shrink():
    """It is an exemption list, and an exemption list that can grow is not one.

    A count ratchet cannot stop a swap -- delete one line, add another -- but
    that is a deliberate edit sitting in the diff, and the failure this policy
    addresses is forgetting rather than evasion.
    """
    baseline = _baseline_entries()
    assert baseline, f"{_BASELINE.name} is empty or missing"
    assert len(baseline) <= _BASELINE_MAX, (
        f"the baseline grew to {len(baseline)} from a pinned {_BASELINE_MAX}. It "
        f"is the UNCLASSIFIED legacy population and may only shrink -- a new "
        f"test file declares {_SCOPE_MARKER} instead of being exempted here.")

    tracked = set(_tracked_test_files())
    stale = sorted(baseline - tracked)
    assert not stale, (
        f"baseline entr(ies) naming file(s) that are no longer tracked: {stale}. "
        f"A deleted or renamed file must lose its line -- a rename is a new "
        f"file, and a new file classifies itself.")

    both = sorted(baseline & set(_scope_map(sorted(baseline))))
    assert not both, (
        f"file(s) both baselined and declaring a scope: {both}. Classifying a "
        f"file means deleting its baseline line in the same cut, or the "
        f"exemption list stops describing what is actually unclassified.")


# ── row 810: the declared set is DERIVED from the markers, not hand-pinned ───
#
# Every new repo-wide test file used to be a three-part edit: its marker, one
# line in the _DECLARED literal above, and one shard line in ci.yml. The middle
# edit put every in-flight cut on this file's seam (14-way at the time of row
# 810) and carried no information the marker did not already carry. The
# expectation is now derived from the TREE -- `git ls-files` plus each file's
# own BD_GATE_SCOPE -- and never from ci.yml, which is the artifact under
# judgement. That keeps the property the header defends: a marked file in no
# shard is still "missing from CI", and a shard line naming an unmarked file is
# still "undeclared". What changes is only where the author writes the second
# copy of a fact: nowhere.

_SELF_REL = "tests/" + Path(__file__).name
# A tracked, unmarked, unscheduled file; the same outsider the row-613 family
# control uses.
_UNMARKED_OUTSIDER = "tests/test_negative.py"


def _declared_binding() -> ast.Assign:
    tree = ast.parse(Path(__file__).read_text("utf-8"), filename=__file__)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_DECLARED"
                for t in node.targets):
            return node
    pytest.fail("_DECLARED is not bound at module scope")


def test_the_declared_set_is_derived_not_hand_pinned():
    """Row 810. The binding is a derivation, not a literal a human keeps in step.

    Parsed, not grepped (A7): this file names `_DECLARED = {` in prose.
    """
    value = _declared_binding().value
    assert not isinstance(value, (ast.Set, ast.List, ast.Tuple, ast.Dict)), (
        f"_DECLARED is a hand-pinned literal of {len(value.elts)} entries at "
        f"line {value.lineno}; row 810 derives it from {_SCOPE_MARKER} markers "
        f"plus _NON_DERIVABLE_DECLARED, so a new gate is its marker and one "
        f"shard line, and this file is no longer a seam every cut rebases over")


def test_a_new_marked_file_is_declared_without_editing_this_file(monkeypatch):
    """Row 810. The walk-forward: one marker, and the gate expects the file."""
    live = _scope_map(_tracked_test_files())
    newcomer = "tests/test_row810_synthetic_newcomer.py"
    assert newcomer not in live and not (_REPO / newcomer).exists()
    before = _declared_gates()
    assert before == _DECLARED, "the import-time binding is not the derivation"

    monkeypatch.setattr(sys.modules[__name__], "_scope_map",
                        lambda paths: {**live, newcomer: "repo-wide"})
    after = _declared_gates()
    assert after == before | {newcomer}, sorted(after ^ before)

    shards = _shard_lists()
    first = sorted(shards)[0]
    scheduled = {**shards, first: shards[first] + [newcomer]}
    _assert_exact_gate_coverage(after | _DB_PRUNE_SAFETY_FAMILY, scheduled)

    # ... and a `module` answer does not join the census.
    monkeypatch.setattr(sys.modules[__name__], "_scope_map",
                        lambda paths: {**live, newcomer: "module"})
    assert _declared_gates() == before


def test_a_marked_file_dropped_from_its_shard_fails_the_derived_gate():
    """Row 810 (1). The property the derivation must not lose: a repo-wide
    file in NO shard is named missing, because the expectation reads the
    tree's markers and never ci.yml."""
    assert _declared_scope(_REPO / _SELF_REL) == "repo-wide"
    shards = _shard_lists()
    holders = [name for name, suites in shards.items() if _SELF_REL in suites]
    assert len(holders) == 1, holders
    dropped = {name: [s for s in suites if s != _SELF_REL]
               for name, suites in shards.items()}
    assert sum(map(len, shards.values())) - sum(map(len, dropped.values())) == 1

    with pytest.raises(AssertionError, match=re.escape(
            f"missing from CI: ['{_SELF_REL}']")):
        _assert_exact_gate_coverage(
            _declared_gates() | _DB_PRUNE_SAFETY_FAMILY, dropped)


def test_an_unmarked_file_added_to_a_shard_fails_the_derived_gate():
    """Row 810 (2). A shard line is not a declaration: an unmarked file that a
    shard names is 'undeclared', so the two lists cannot drift apart
    silently in the other direction either."""
    assert _tracked(_UNMARKED_OUTSIDER)
    assert _declared_scope(_REPO / _UNMARKED_OUTSIDER) != "repo-wide"
    assert _UNMARKED_OUTSIDER not in _NON_DERIVABLE_DECLARED
    assert _UNMARKED_OUTSIDER not in _DB_PRUNE_SAFETY_FAMILY
    shards = _shard_lists()
    assert all(_UNMARKED_OUTSIDER not in suites for suites in shards.values())
    first = sorted(shards)[0]
    added = {**shards, first: shards[first] + [_UNMARKED_OUTSIDER]}

    with pytest.raises(AssertionError, match=re.escape(
            f"extra in CI: ['{_UNMARKED_OUTSIDER}']")):
        _assert_exact_gate_coverage(
            _declared_gates() | _DB_PRUNE_SAFETY_FAMILY, added)


def test_the_real_tree_is_covered_exactly_by_derivation_plus_legacy():
    """Row 810 (3). GREEN on the real tree, with the count asserted exactly:
    the shard union is the derived half plus the closed legacy half plus the
    row-613 family, three disjoint parts, none empty."""
    derived = _derived_repo_wide()
    assert derived, "no tracked test file declares repo-wide scope"
    process = _process_tests()
    gate_derived = derived - process
    gate_legacy = _NON_DERIVABLE_DECLARED - process
    assert not gate_derived & gate_legacy, (
        sorted(gate_derived & gate_legacy))
    assert not (gate_derived | gate_legacy) & _DB_PRUNE_SAFETY_FAMILY
    declared = _declared_gates()
    assert declared == gate_derived | gate_legacy
    assert declared == _DECLARED

    executed = [s for suites in _shard_lists().values() for s in suites]
    expected_count = (len(gate_derived) + len(gate_legacy)
                      + len(_DB_PRUNE_SAFETY_FAMILY))
    assert len(executed) == expected_count, (
        f"CI executes {len(executed)} gate paths; derived {len(gate_derived)} + "
        f"legacy {len(gate_legacy)} + family "
        f"{len(_DB_PRUNE_SAFETY_FAMILY)} = {expected_count}")
    assert set(executed) == declared | _DB_PRUNE_SAFETY_FAMILY
    _assert_exact_gate_coverage(declared | _DB_PRUNE_SAFETY_FAMILY, _shard_lists())


def test_the_derivation_control_names_a_severed_marker_reader(monkeypatch):
    """Row 810 (4), in-process: an emptied derivation is not a quieter gate.

    The bd-mutate spec `row810_derived_declaration.json` plants the same
    mutant in the source; this is the control that proves test (1) is the
    catcher rather than an import error."""
    monkeypatch.setattr(sys.modules[__name__], "_derived_repo_wide", set)
    assert _declared_gates() == _NON_DERIVABLE_DECLARED
    shards = _shard_lists()
    dropped = {name: [s for s in suites if s != _SELF_REL]
               for name, suites in shards.items()}
    with pytest.raises(AssertionError) as excinfo:
        _assert_exact_gate_coverage(
            _declared_gates() | _DB_PRUNE_SAFETY_FAMILY, dropped)
    # The refusal is loud but it no longer names the dropped file as missing,
    # which is exactly what test (1) asserts and why it catches the mutant.
    assert f"missing from CI: ['{_SELF_REL}']" not in str(excinfo.value)
    assert "expected exactly" in str(excinfo.value)


# ── docs-only exact-head shard selection ────────────────────────────────────

_DOCS_ONLY_TOOL = _REPO / "toolchain" / "bin" / "bd-docs-only"
_DOCSONLY_GENERATOR = _REPO / "tools" / "generate_ci_docsonly_shards.py"
_DOCSONLY_MANIFEST = _REPO / "project-knowledge" / "CI_DOCSONLY_SHARDS.json"
_ROW530_PATH = "tests/test_row530_docs_only_lane_fails_closed.py"


def _ci_docs_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        check=False, timeout=60,
    )
    if check:
        assert result.returncode == 0, (
            f"fixture git {' '.join(args)} failed: {result.stderr}")
    return result


def _ci_docs_commit(repo: Path, message: str) -> str:
    _ci_docs_git(repo, "add", "-A")
    _ci_docs_git(
        repo, "-c", "user.email=ci-docsonly@example.invalid",
        "-c", "user.name=ci-docsonly", "commit", "-q", "-m", message,
    )
    return _ci_docs_git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def ci_docs_only_candidates(tmp_path):
    """Reuse bd-docs-only's own complete miniature repository authority."""
    tool = runpy.run_path(str(_DOCS_ONLY_TOOL))
    repo = tool["_fixture_repo"](tmp_path / "candidate")
    row530 = repo / _ROW530_PATH
    row530.write_text("def test_row530_fixture():\n    assert 1 == 1\n", encoding="utf-8")
    _ci_docs_git(repo, "init", "-q", "-b", "main")
    tool["_fixture_regen"](repo)
    base = _ci_docs_commit(repo, "base")

    def branch(name: str, mutate) -> str:
        _ci_docs_git(repo, "checkout", "-q", "-B", name, base)
        mutate()
        return _ci_docs_commit(repo, name)

    def docs_change() -> None:
        docs = repo / "docs" / "repo" / "TOPOLOGY.md"
        docs.write_text(docs.read_text("utf-8") + "\nDocs-only selection.\n", "utf-8")
        knowledge = repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
        knowledge.write_text(
            knowledge.read_text("utf-8") + "\nDocs-only evidence.\n", "utf-8")
        tool["_fixture_regen"](repo)

    docs = branch("docs", docs_change)

    def mixed_change() -> None:
        docs_change()
        (repo / "bulk_downloader" / "ci_docsonly_probe.py").write_text(
            "RUNTIME_PROBE = object()\n", encoding="utf-8")

    mixed = branch("mixed", mixed_change)

    def test_change() -> None:
        row530.write_text(
            row530.read_text("utf-8") + "\ndef test_runtime_change():\n    assert 2 == 2\n",
            encoding="utf-8",
        )

    test_only = branch("test-only", test_change)
    _ci_docs_git(repo, "checkout", "-q", "--detach", base)
    return {"repo": repo, "base": base, "docs": docs, "mixed": mixed,
            "test-only": test_only}


def _classify_fixture(repo: Path, base: str, head: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_DOCS_ONLY_TOOL), "classify", "--repo", str(repo),
         "--base", base, "--head", head, "--json"],
        capture_output=True, text=True, check=False, timeout=180,
    )


def _selected_shards(classification_exit: int) -> dict:
    result = subprocess.run(
        [sys.executable, str(_DOCSONLY_GENERATOR), "select", "--repo", str(_REPO),
         "--classification-exit", str(classification_exit)],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _independent_test_jobs(workflow: dict) -> set[str]:
    jobs = workflow.get("jobs") or {}
    expected = {"postgres-integration", "frontend-vitest"}
    assert expected <= set(jobs), "independent test-job denominator is incomplete"
    for name in expected:
        body = "\n".join(str(step.get("run", "")) for step in jobs[name]["steps"])
        assert ("pytest" in body) if name == "postgres-integration" else ("vitest" in body)
    return expected


def _expected_docs_shards() -> dict[str, list[str]]:
    return {
        name: sorted(set(suites) & _DECLARED)
        for name, suites in _shard_lists().items()
        if set(suites) & _DECLARED
    }


def test_ci_docs_only_selection_is_wired_only_to_the_real_classifier():
    assert _DOCSONLY_MANIFEST.is_file(), (
        "CI docs-only shard manifest is absent: "
        "project-knowledge/CI_DOCSONLY_SHARDS.json")
    assert _DOCSONLY_GENERATOR.is_file(), "CI docs-only shard generator is absent"
    workflow = _workflow()
    job = (workflow.get("jobs") or {}).get("ci_docs_only")
    assert isinstance(job, dict), "ci.yml has no ci_docs_only classification job"
    steps = job.get("steps") or []
    assert steps and str(steps[0].get("uses", "")).startswith("actions/checkout@v4")
    assert (steps[0].get("with") or {}).get("fetch-depth") == 0
    commands = [str(step.get("run", "")) for step in steps]
    assert sum("bd-docs-only classify" in command for command in commands) == 1, (
        "ci.yml must call bd-docs-only classify exactly once")
    outputs = job.get("outputs") or {}
    assert "steps.classify.outputs.docs_only" in str(outputs.get("docs_only", ""))
    assert "steps.classify.outputs.selected_shards" in str(
        outputs.get("selected_shards", ""))
    executable = repr(job)
    assert "pull_request.labels" not in executable
    assert "head_commit.message" not in executable
    manifest_checks = [command for command in commands
                       if "generate_ci_docsonly_shards.py check" in command]
    assert len(manifest_checks) == 1

    selected_ref = "needs.ci_docs_only.outputs.selected_shards"
    jobs = workflow["jobs"]
    for name in _independent_test_jobs(workflow):
        assert jobs[name].get("needs") == "ci_docs_only"
        condition = str(jobs[name].get("if", ""))
        assert selected_ref in condition and name in condition
    gate_name, gate_job = _gate_suite_job()
    assert gate_name == "gate-suites"
    assert gate_job.get("needs") == "ci_docs_only"
    gated_steps = gate_job.get("steps") or []
    assert gated_steps, "gate-suites has zero steps"
    assert sum(selected_ref in str(step.get("if", ""))
               for step in gated_steps) == len(gated_steps)


def test_generated_docs_only_manifest_is_the_exact_measured_intersection():
    manifest = json.loads(_DOCSONLY_MANIFEST.read_text("utf-8"))
    expected_docs = _expected_docs_shards()
    expected_all = sorted(set(_shard_lists()) | _independent_test_jobs(_workflow()))
    assert expected_docs, "zero shards intersect _DECLARED"
    assert expected_all, "full test-shard denominator is zero"
    assert manifest.get("docs_only_shards") == sorted(expected_docs)
    assert manifest.get("all_shards") == expected_all
    declared_in_independent = {
        name: sorted(set(re.findall(
            r"tests/test[A-Za-z0-9_./-]*\.py",
            "\n".join(str(step.get("run", ""))
                       for step in _workflow()["jobs"][name]["steps"]))) & _DECLARED)
        for name in _independent_test_jobs(_workflow())
    }
    assert declared_in_independent == {
        "frontend-vitest": [], "postgres-integration": []}
    check = subprocess.run(
        [sys.executable, str(_DOCSONLY_GENERATOR), "check", "--repo", str(_REPO)],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert check.returncode == 0, check.stderr

    victim = sorted(expected_docs)[0]
    omitted = set(manifest["docs_only_shards"])
    assert victim in omitted, "negative-control victim held no declared gate"
    omitted.remove(victim)
    escaped = sorted(
        name for name, suites in _shard_lists().items()
        if set(suites) & _DECLARED and name not in omitted)
    assert escaped == [victim], (
        f"omitting one manifest shard must expose exactly that shard, got {escaped}")


@pytest.mark.parametrize(
    "candidate,base_ref,expected_exit,expect_docs",
    [
        ("docs", "base", 0, True),
        ("mixed", "base", 1, False),
        ("test-only", "base", 1, False),
        ("docs", "missing-base-ref", 2, False),
    ],
)
def test_real_diff_classification_selects_the_exact_shard_population(
        ci_docs_only_candidates, candidate, base_ref, expected_exit, expect_docs):
    fixture = ci_docs_only_candidates
    repo, base, head = fixture["repo"], fixture["base"], fixture[candidate]
    changed = _ci_docs_git(repo, "diff", "--name-only", base, head).stdout.splitlines()
    assert changed, "fixture changed-path denominator is zero"
    if candidate == "docs":
        assert any(path.startswith("docs/") for path in changed)
        assert any(path.startswith("project-knowledge/") for path in changed)
        assert not any(path.startswith("tests/") for path in changed)
    elif candidate == "mixed":
        assert sum(path.startswith("bulk_downloader/") for path in changed) == 1
    else:
        assert changed == [_ROW530_PATH]

    selected_base = base if base_ref == "base" else base_ref
    if base_ref != "base":
        assert _ci_docs_git(repo, "rev-parse", "--verify", selected_base,
                            check=False).returncode != 0
    classification = _classify_fixture(repo, selected_base, head)
    assert classification.returncode == expected_exit, (
        classification.stdout + classification.stderr)
    selection = _selected_shards(classification.returncode)
    expected = (set(_expected_docs_shards()) if expect_docs else
                set(_shard_lists()) | _independent_test_jobs(_workflow()))
    assert selection.get("docs_only") is expect_docs
    assert len(selection.get("selected_shards", [])) == len(expected) > 0
    assert set(selection["selected_shards"]) == expected


def test_generator_refuses_and_names_each_unreadable_input(tmp_path):
    missing_workflow = subprocess.run(
        [sys.executable, str(_DOCSONLY_GENERATOR), "check", "--repo", str(tmp_path)],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert missing_workflow.returncode != 0
    assert "workflow" in missing_workflow.stderr.lower()
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_bytes(_CI.read_bytes())
    missing_declaration = subprocess.run(
        [sys.executable, str(_DOCSONLY_GENERATOR), "check", "--repo", str(tmp_path)],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert missing_declaration.returncode != 0
    assert "declaration" in missing_declaration.stderr.lower()


def test_docs_only_transform_control_imports_without_judging_selection():
    subject = runpy.run_path(str(_DOCSONLY_GENERATOR))
    assert callable(subject.get("select_shards"))


def test_every_gate_suites_shard_actually_runs_at_least_one_test():
    """A shard with no suites is a job that reports GREEN having checked nothing."""
    empty = sorted(name for name, suites in _shard_lists().items() if not suites)
    assert not empty, (
        "gate-suites matrix entries that execute no tests: " + ", ".join(empty) +
        " -- delete the entry, or give it the suites it is meant to run; an empty shard is a "
        "green job that checked nothing")
