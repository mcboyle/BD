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
import re
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
    "tests/test_v3_66_1046_gates_for_this_sessions_shapes.py",
    "tests/test_v3_66_1040_remote_job_registry.py",
    "tests/test_v3_66_1132_the_hunt_reaps_what_it_abandons.py",
    # @1241 row 237. The split half is a long pole in its own right:
    # it carries the slowest registration-lifecycle nodes of the
    # module it came out of.
    "tests/test_v3_66_1132_the_hunt_reaps_registration_lifecycle.py",
}

# Its subject is which gates CI runs, which is a property of the tree.
BD_GATE_SCOPE = "repo-wide"

# The gates that must run on every PR. Pinned HERE rather than derived from
# ci.yml, because deriving the expectation from the thing under test is how a
# dropped file passes: the union would simply shrink to match. Adding a
# repo-wide gate to CI is a three-part change: its scope marker, this independent
# declaration, and one workflow shard entry all land together.
_DECLARED = {
    # Rows 566/571. The meta-gate over this file and over the tracked Markdown
    # corpus. It holds the EXACT bidirectional Markdown denominator -- the
    # modules themselves keep only shrink-only floors -- and the only refusal of
    # a `>=` -> `==` re-pin of a derived population. Marked `module` it ran in
    # no shard, so neither refusal reached a PR.
    "tests/test_row531_denominators_are_derived_not_pinned.py",
    # Row 532. bd-mutate finds its anchor by TEXT and never asks whether the
    # text is executable, so an anchor can resolve exactly once onto a comment:
    # the mutation edits prose, behaviour is unchanged, the catcher passes, and
    # the battery records a caught regression it never caused. Its subject is
    # every tracked mutant spec, so no diff selects it.
    "tests/test_row532_a_mutant_anchor_must_resolve_into_code.py",
    # Row 346. The plugin sandbox checker judges an exact tree-wide population
    # of three bridges/six launches, while the runtime cases execute both
    # launch shapes with leaked and benign environment controls.
    "tests/test_row346_plugin_sandbox_is_truthful.py",
    # Row 292. This census pins the existing curated parallel allowlist by a
    # mechanical digest and ratchet while allowing new, unreviewed tracked
    # files to remain in the classifier's fail-closed serial default.
    "tests/test_capture_execution_lanes.py",
    # Row 261. Replication start/stop owns one sidecar and signals only the
    # process identity it launched. This real-process gate forces lifecycle
    # lock contention and checks every direct/HTTP acquisition for re-entry.
    "tests/test_v3_66_261_contended_lifecycle_lock.py",
    # Backlog row 311 / F16. Separate app processes write one shared config;
    # this real-process gate forces their stale-read schedule, proves the two
    # lock acquisitions use one exact file, and preserves both disjoint keys.
    "tests/test_row311_app_config_writers_are_serialized.py",
    # Backlog row 267. Seven application measurements had each collapsed an
    # unavailable result into the same value as measured permission. This
    # module drives the real enqueue/start/admission and integrity seams, so it
    # is pinned into the safety shard claimed below on every PR.
    # CI-SHARD-CLAIM row-267 application-safety tests/test_app_measurements_fail_closed.py
    "tests/test_app_measurements_fail_closed.py",
    # Row 434. A held resume replaces the paused state with a public refusal
    # token; this runtime gate proves a later clear measurement can recover
    # only the paused worker pool that created that token. Keep it in the
    # direct application safety lane because no diff-derived route can substitute
    # for the held, clear, and UNKNOWN lifecycle transitions it executes.
    # CI-SHARD-CLAIM row-434 application-safety tests/test_row434_resume_cannot_leave_the_hold_state_it_set.py
    "tests/test_row434_resume_cannot_leave_the_hold_state_it_set.py",
    # The download-integrity promotion seam is module-scoped, but a hardened
    # staging-claim API left four of its six tests red on main while no CI job
    # executed the file. Keep the real promote/abort contract in the same
    # safety shard as the other integrity boundaries.
    # CI-SHARD-CLAIM row-284 application-safety tests/test_v3_66_284_integrity.py
    "tests/test_v3_66_284_integrity.py",
    # Row 341. cloud-setup and its emitted recovery helper are READY-verdict
    # boundaries. This behavioral module proves missing, malformed, degraded,
    # and command-failed artifacts are distinct from the two healthy paths, so
    # it runs directly rather than relying on a diff-derived shell-script band.
    "tests/test_cloud_setup_truthfulness.py",
    # Row 334. Four operator health surfaces used an empty collection for both
    # measured-empty and unavailable. This runtime gate pairs every exception
    # probe with a measured-healthy control and is pinned into CI here.
    "tests/test_ffmpeg_capability_health.py",
    # Row 356. Cookie quality must not call a session-only jar perfect when no
    # freshness, expected-name, Cloudflare, or history check ran. This runtime
    # gate also keeps API-adjacent relogin and queue consumers from defaulting
    # the explicit UNKNOWN state back to a numeric 100.
    "tests/test_row356_cookie_quality_reports_unknown.py",
    # Row 363. The held-open Capture page is the only authoritative subject for
    # live affordance learning. This offline Chromium gate proves BAR-first and
    # one-click DROPDOWN discovery, policy refusal, network disagreement,
    # pagination/scroll enumeration, and selector-only Review staging.
    "tests/test_row363_affordance_learning.py",
    # Row 360. A declared Scrapling package is not a usable Turnstile bypass
    # unless its fetcher capability imports. This runtime gate proves the
    # absent, probe-unknown, recovery-only, and fully available states.
    "tests/test_row360_turnstile_bypass_is_installed.py",
    # Row 334. The library integrity route is a second consumer of the bitrot
    # issue census. Its runtime gate refuses to call a locked inventory clean.
    "tests/test_v3_57_phase9.py",
    # Row 362. This gate compiles the selector population of every committed
    # template and renders two network-blocked shapes with a real browser. A
    # changed selector in any family changes its denominator, so it must run
    # directly rather than depend on a diff-derived template band.
    "tests/test_row362_templates_are_resolvable.py",
    # Row 377. The installed verifier has PASS/FAIL/UNKNOWN outcomes; the CI
    # consumer must preserve UNKNOWN as a distinct refusal, never a pass.
    "tests/test_row377_installed_template_selftest_states.py",
    # Backlog row 176. Verdict pins cannot detect a fixture whose recognizer
    # result is correct for the wrong site's bytes, so this gate independently
    # declares every recognizer fixture's page host and checks the payload.
    "tests/test_recognizer_fixture_site_identity.py",
    # Row 121. Two distinct real-capture slices drive derive_login_flow and a
    # fake browser independently; the gate catches role-collapsed input clicks,
    # missing-selector fallback and post-login media origins posing as steps.
    "tests/test_v3_66_121_login_flow_derives_the_observed_drive.py",
    # @1240, the preflight that runs what a derived band cannot select. Four
    # defects shipped between v3.66.1223 and v3.66.1238 because bd-band-derive
    # derives from CHANGED PATHS while those gates judge the TREE, so no diff
    # ever reached them. This gate has the same property and is declared for
    # the same reason.
    "tests/test_v3_66_1239_precut_runs_the_underived_gates.py",
    # Row 530, the docs-only lane's classifier. Its subject is which tracked
    # paths of this tree can be proven inert, so it is derived from
    # `git ls-files` and no changed path can select it. A classifier whose
    # allow set has quietly grown a new top-level directory is precisely the
    # failure it exists to prevent, and it would look like nothing at all.
    "tests/test_row530_docs_only_lane_fails_closed.py",
    # @1378, the same tool, the other half of the same question. 1239 pins that
    # bd-precut RUNS the gates a diff cannot select; this one pins that its
    # version/pin/surface check can be MEASURED AT ALL. It judges the tool and
    # the tree rather than a diff, so no changed path selects it either.
    "tests/test_row463_precut_derives_its_baseline.py",
    # Rows 416/464/472/527. Verification tools must preserve each measured
    # state through their real entry points; the suite also pins bd-precut's
    # baseline/tracked-only main() wiring, which the row-463 component tests do
    # not reach. Its denominator is the merge-lane tree, not one application
    # module, so it is always scheduled.
    "tests/test_verify_lane_state_naming.py",
    # @1222, the budget ratchet. An inner budget at or above the bound
    # governing its item has a dead error path, so a hang kills the process
    # instead of failing the test -- twice over on 2026-08-24. The frozen
    # population may shrink and may not grow.
    "tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py",
    # @1220, the timeout-method contract. It runs REAL sub-pytests in both
    # shapes and asserts that a test exceeding its bound is reported BY NAME
    # with no worker killed -- the property the sanctioned command was changed
    # to obtain. Its negative arm stops it passing vacuously and fails loudly
    # if pytest-timeout or xdist ever change underneath it.
    "tests/test_v3_66_1220_a_timeout_names_its_test.py",
    # @1208, the capture execution signal contract. These three judge the
    # heartbeat boundary that made all seven fleet captures fail at once:
    # 1208 RUNS the wrapper and reads the wrapped process's own signal
    # dispositions, 1111 proves the wall-clock bound still fires, and u45
    # pins the shipped launch form 1208 exercises. They are module-scoped,
    # so nothing else in CI would have seen a regression of
    # scripts/lib/heartbeat.sh -- and capture is not CI.
    "tests/test_v3_66_1208_the_heartbeat_keeps_foreground_signal_semantics.py",
    # @1209 extends the same contract to every OTHER detached launch site
    # and to what the wrapper hands BACK to its caller.
    "tests/test_v3_66_1209_every_detached_launch_keeps_signal_semantics.py",
    # @1215, the same contract seen from the WRAPPER side rather than the
    # launcher side: a wrapper must not silently alter the subject it bounds
    # or carries. It judges two PRODUCTION toolchain scripts -- bd-wedge-hunt's
    # remote transport and bd-run's cap declaration -- and both are
    # module-scoped, so without an entry here a regression would be caught by
    # nothing in CI. bd-sweep-run runs pytest through bd-run's cap, which is
    # exactly how a whole-fleet suite came to carry weaker signal evidence
    # than its own direct probes suggested.
    "tests/test_v3_66_1215_a_wrapper_must_not_alter_its_subject.py",
    # @1216. The frontend suite entered CI in this cut; this gate judges the
    # CHECKER that gives that job a denominator, because `vitest run` exits 0
    # over an empty collection and the job alone would go green over nothing.
    "tests/test_v3_66_1216_vitest_is_a_real_ci_denominator.py",
    # @1217. The parity scanner's POPULATION is its denominator, and a test
    # fixture must not be admissible as evidence that the SPA wires a route.
    "tests/test_v3_66_1217_a_fixture_is_not_wiring.py",
    # @1218. The five T-series gates now RUN Vitest instead of grepping for it,
    # so the shard carrying them must be given node. This asserts that contract
    # from the tree rather than from a pinned shard name.
    "tests/test_v3_66_1218_vitest_delegating_shards_have_node.py",
    # @1225, row 232. Generalises 1217 and 1218: it DERIVES every site in
    # tracked tests/ and tools/ that reads frontend/src text, requires each to
    # declare its population, and runs each product-only site against a planted
    # tree whose two arms differ only in a filename. Repo-wide because a new
    # scanner can appear in any file in that population.
    "tests/test_v3_66_1225_spa_scanner_populations.py",
    # Row 348 / H15-2. This live all-source TSX gate was classified by the SPA
    # scanner census but absent from every CI shard. A planted raw Unicode
    # escape therefore failed here while the scheduled denominator stayed
    # green. Pin the live verdict beside the census that classifies it.
    "tests/test_no_raw_unicode_escape_in_jsx.py",
    "tests/test_v3_66_1111_a_wedged_capture_lane_is_bounded.py",
    "tests/test_u45_capture_sh_shipped.py",
    "tests/test_all_sources_parse.py",
    "tests/test_pin_index_in_sync.py",
    # Row 387. A release-time versync check is not enough to prevent a
    # same-valued duplicate version pin from becoming another bump site. This
    # gate AST-scans the live test population, so it must run on every PR.
    "tests/test_row387_ast_version_pin_guard.py",
    # Cut 1435. A transferred band is a release-wide safety claim, and each
    # disposition plus UNKNOWN fallback must remain reachable on every PR.
    "tests/test_row1435_band_verdict_transfer.py",
    "tests/test_route_index_in_sync.py",
    "tests/test_import_graph_no_new_edges.py",
    "tests/test_source_windows_do_not_shift.py",
    "tests/test_generated_artifacts_are_not_tracked.py",
    "tests/test_settings_center_slice4.py",
    # Row 439. Segmented (HLS/DASH) transfers bypassed the fail-closed VPN
    # egress gate every sibling transfer path passes, so ffmpeg fetched every
    # segment on the clear interface for a vpn_required site whose tunnel was
    # down. The fix routes all six arms through one gate; the tree-wide half of
    # this module holds the denominator that keeps a SEVENTH arm from quietly
    # reopening it, which only works if it runs on every PR.
    "tests/test_row439_segmented_transfers_honor_the_egress_gate.py",
    "tests/test_versync_gate.py",
    "tests/test_release_hygiene_gates.py",
    # Row 335. Both release verifiers previously passed over absent evidence.
    # This module drives their real gate seams plus measured-empty controls;
    # it is pinned into CI because neither legacy test file ran in any shard.
    "tests/test_row335_release_gate_populations.py",
    # Row 339 (2026-08-28). Module-scoped: it judges verify_release.py's
    # measurement walls. Declared and scheduled beside its row 335 sibling
    # because it exercises the same release-verifier seam.
    "tests/test_row339_measurement_noise_bounds.py",
    "tests/test_scan_version_pins_fixture.py",
    "tests/test_gui_parity.py",
    "tests/test_t1_dashboard_wired.py",
    "tests/test_t2_history_wired.py",
    # @1255, backlog rows 247/248. build_manifest() must build only in a copied
    # frontend tree so a suite cannot empty the deployed SPA or delete its
    # .bd-built-from provenance marker. The host-dependent byte invariant skips
    # UNKNOWN on clean CI clones, but this shard installs Node and runs its
    # deterministic copy-shape and negative-control siblings on every PR.
    "tests/test_v3_66_1255_frontend_build_is_isolated.py",
    "tests/test_t3_t4_wired.py",
    "tests/test_t5_t6_wired.py",
    "tests/test_t7_notifications_wired.py",
    # @1240, backlog row 240. The supervisor throttle form had no GET consumer
    # at all, so an untouched Apply POSTed the component's own defaults over
    # every live byte-rate limit. Delegates to three Vitest specs; it belongs in
    # the node-enabled shard claimed below.
    # CI-SHARD-CLAIM row-240 parity-graph tests/test_v3_66_1240_supervisor_settings_seeded.py
    "tests/test_v3_66_1240_supervisor_settings_seeded.py",
    "tests/test_t8_cluster_wired.py",
    "tests/test_t9a_live_stream_wired.py",
    "tests/test_t9b_push_wired.py",
    "tests/test_t10_devtools_wired.py",
    "tests/test_t11_approval_wired.py",
    # Row 281. The five original Python UI wrappers must consume proof that
    # their focused Vitest delegate actually executed. The separate complete
    # frontend job is redundancy, not evidence about these wrapper nodes.
    "tests/test_row281_ui_wrappers_delegate.py",
    "tests/test_config_parity_ratchet.py",
    "tests/test_skip_baseline.py",
    "tests/test_pytest_capture_results.py",
    "tests/test_framework_gui.py",
    "tests/test_v3_66_552_playground_ssrf.py",
    "tests/test_v3_66_1181_capture_reconciliation.py",
    "tests/test_csrf_session_bootstrap.py",
    "tests/test_csrf_contract_reachability.py",
    "tests/test_csrf_tool_contracts.py",
    "tests/test_spa_root_routing_contract.py",
    # Row 310. This gate judges every eligible runtime GET rule and the shipped
    # template/SPA surface, so it is tree-wide. It lives in the Node-enabled
    # parity shard because its static half performs an attempt-owned SPA build.
    "tests/test_secret_display_never.py",
    "tests/test_cockpit_route_contract.py",
    "tests/test_cockpit_navigation_contract.py",
    "tests/test_pk_mirrors_stay_retired.py",
    "tests/test_authority_documents.py",
    "tests/test_v3_66_1183_inv_tags_generated.py",
    "tests/test_v3_66_1183_safe_temp_janitors.py",
    "tests/test_v3_66_1183_source_window_content.py",
    # Backlog row 252. This gate inventories every destructive cockpit-task
    # cleanup in tests/, proves the autouse environment pop in a nested pytest,
    # and drives both the refusal and owned-cleanup branches. A diff-derived
    # band cannot protect callers who export BD_COCKPIT_TASKS before any test.
    "tests/test_v3_66_1257_cockpit_tasks_test_root_is_confined.py",
    # Row 300. The real X display is host-global, so this gate forces the
    # foreign-process race, proves the atomic claim, and verifies exact owned
    # teardown on every PR independently of which test files a diff touches.
    "tests/test_row300_parallel_display_cleanup_owns_process.py",
    # F31. This gate measures every child-test launch that used to forward an
    # operator BD_INSTALL_DIR, drives a real nested bd-band against a
    # sacrificial database root, and proves the central autouse pop plus the
    # explicit test-owned negative control. It is tree-wide because another
    # launcher can appear anywhere in the measured production/tool population.
    "tests/test_child_test_install_dir_isolation.py",
    "tests/test_toolchain_534.py",
    # Row 336. Audit promotion and the default static-analysis battery are both
    # release-verdict boundaries: absent witness/analyzer evidence must remain
    # UNKNOWN on every PR, independently of a diff-derived module band.
    "tests/test_row336_audit_and_scan_evidence.py",
    # Row 349. Three operational tools each exposed one subject to another's
    # cached bytes.  This runtime gate forces all three two-identity seams and
    # must run directly in CI rather than depend on a changed-path band.
    "tests/test_row349_shared_caches_are_identity_bound.py",
    # Row 355. This gate calibrates bd-mutate's timeout window from a real
    # warm-up and proves its zero-row diagnostic, negative control, exact
    # restore, and absent JUnit evidence under scheduling load. A diff-derived
    # band cannot establish the host-scheduling premise, so CI runs it directly.
    "tests/test_row355_mutate_timing_is_schedule_stable.py",
    # Row 386. Nothing in the suite downloaded a file: a 623-file, 8,528-test
    # band was green while the deployed app could not complete a download on
    # two of its 32 sites, and green again while it downloaded the wrong scene.
    # This gate runs the whole chain -- page DOM, candidate discovery, ranking,
    # direct-URL resolution, filename, a real loopback transfer, history row,
    # library title -- against recorded fixtures. Its subject is the tree's
    # ability to complete a download at all, which no changed path implies, so
    # it is declared here and scheduled in the dedicated shard claimed below.
    # CI-SHARD-CLAIM row-386 download-chain tests/test_row386_the_download_chain_is_gated.py
    "tests/test_row386_the_download_chain_is_gated.py",
    # @1143. The FIRST of the BD_GATE_SCOPE = "module" entries here, and each is
    # deliberate. (This comment read "the ONLY entry" while the entry directly
    # below it was a second one -- stale within five releases of being written,
    # in the file whose whole subject is a hand-pinned set going stale. Count
    # the entries carrying this reason rather than trusting a word here.)
    # Its subject is one tool, so calling it "repo-wide" would be
    # the mislabelling this file's own docstring says nothing catches. It is
    # pinned into a shard anyway because the property it guards is a SAFETY
    # BOUNDARY that must run on every PR regardless of what the diff touched:
    # bd-fleet-run's v3.66.1140 selftest could reach `ssh 192.0.2.10` and ran
    # on GitHub runners via test_toolchain_534, kept off the network only by
    # procfs. The failure mode is someone reintroducing a network-capable
    # fixture, which no diff-derived band would necessarily catch.
    "tests/test_v3_66_1142_fleet_run_is_hermetic.py",
    "tests/test_v3_66_1158_fleet_provenance_fails_closed.py",
    # @1255 row 250 applies 1158's fail-closed provenance contract to the
    # sibling fleet census. Its failure seam needs a real probe environment,
    # so a source-only check cannot substitute for executing this module.
    "tests/test_v3_66_1255_bd_fleet_measurements_fail_closed.py",
    # Row 282. bd-opv executes application writers, so a verifier run can edit
    # the operator's config, database, subscriptions, and cwd-relative stores
    # unless its process boundary is exercised on every pull request.
    "tests/test_row_282_bd_opv_isolates_every_store.py",
    # Row 345. OPV-A11Y must distinguish a served cockpit from an HTTP error or
    # a different 200 page, and F4.3 must not leak its authenticated app state.
    "tests/test_row345_opv_a11y_requires_served_cockpit.py",
    # Row 313. bd-job is the detached local-job safety boundary: concurrent
    # starters must not share one state directory, and status/kill must not
    # mistake a reused numeric PID for the process the job originally owned.
    "tests/test_row313_bd_job_identity.py",
    # Row 350. Four lifecycle APIs must not report success when admission,
    # recovery, capture identity, or cancellation disagrees with durable state.
    "tests/test_row350_job_api_durable_truth.py",
    # Row 353. Regex mutation anchors and the tracked row-348 regression read
    # mutable whole-tree source/spec state, so this gate runs directly.
    "tests/test_row353_mutant_anchors_survive_a_moved_census.py",
    # Row 354. Capture's deployment-local graph pin has three states. This gate
    # executes all three against isolated exact-tree records and prevents a
    # never-deployed checkout from masquerading as either PASS or graph drift.
    "tests/test_row354_capture_verdict_separates_an_inapplicable_pin.py",
    "tests/test_v3_66_1159_fleet_prune_is_object_bound.py",
    "tests/test_v3_66_1160_bridge_verifier_isolation.py",
    "tests/test_v3_66_1161_context_census_is_retired.py",
    "tests/test_v3_66_1162_canonical_full_suite_uses_fixed_n24.py",
    "tests/test_v3_66_1164_one_task_authority.py",
    # Row 263. The register's evidence tags and the changelog's release
    # headings are both tree-wide authorities. A worker can edit any row, so a
    # diff-derived module band cannot provide this reconciliation.
    "tests/test_register_closed_versions_exist.py",
    # v3.66.1359 / row 402. The amendment suite is module-scoped, but its
    # atomic compare-and-swap is the only sanctioned correction path for
    # release-register prose, so it remains an explicit CI safety boundary.
    "tests/test_register_content_amend.py",
    # The append path shares the same stable-directory transaction with amend
    # and close, so a regression can otherwise lose a release row without any
    # diff-derived module test reaching the cross-writer schedule.
    "tests/test_register_append.py",
    # Row 323. A human-review census expires when main moves, so this gate
    # drives the pytest collection hook before a stale row can spend its band.
    # It is tree-wide because the version comparison is about the row's base
    # tree rather than about the checker module itself.
    "tests/test_row323_census_pins_declare_expiry.py",
    "tests/test_v3_66_1165_one_agent_contract.py",
    "tests/test_v3_66_1166_historical_docs_are_adjudicated.py",
    "tests/test_v3_66_1167_safety_authorities_are_single_source.py",
    "tests/test_v3_66_1168_tests_are_current_contracts.py",
    "tests/test_v3_66_1169_openapi_has_one_producer.py",
    "tests/test_v3_66_1170_claude_is_concise_authority.py",
    "tests/test_v3_66_1171_backlog_truth_is_current.py",
    # Row 246. Every lexical backlog-id reference in canonical row prose must
    # resolve against the complete parsed row population. This is tree-wide:
    # a stale reference can be introduced by editing any backlog row.
    "tests/test_v3_66_1255_backlog_references_resolve.py",
    "tests/test_v3_66_1172_nested_freshness_and_legacy_retirement.py",
    "tests/test_v3_66_1173_gate_scope_debt_is_paid.py",
    "tests/test_v3_66_1174_defect_suppressions_are_ast_bound.py",
    "tests/test_v3_66_1177_ai_boot_observation_is_bounded.py",
    "tests/test_v3_66_1178_orphan_tempfiles_are_recursive.py",
    "tests/test_v3_66_1179_frontend_secret_regen_is_canonical.py",
    # Row 333. The recognizer corpus split four ways. Each shard declares
    # repo-wide scope because its subject is the whole corpus population,
    # and a repo-wide gate must reach a shard in its own right or the
    # split would have moved coverage out of CI rather than parallelised it.
    "tests/test_recognizer_corpus_shard_a.py",
    "tests/test_recognizer_corpus_shard_b.py",
    "tests/test_recognizer_corpus_shard_c.py",
    "tests/test_recognizer_corpus_shard_d.py",
    # Row 332. The four-way split of test_v3_66_1046: each shard declares
    # repo-wide scope because its subject is still the whole tool-state
    # population, and each must reach a shard in its own right or the
    # split would have moved work out of CI rather than parallelised it.
    "tests/test_v3_66_1046_tool_state_1040.py",
    "tests/test_v3_66_1046_tool_state_1043.py",
    "tests/test_v3_66_1046_tool_state_1044.py",
    "tests/test_v3_66_1046_tool_state_1054.py",
    # Row 326. The frontend security floor was legacy-classified and named in
    # NO shard, so it never ran -- and sat RED on main at v3.66.1304 because a
    # legitimate patch upgrade tripped its exact-equality pin. Declaring it
    # here makes removing it from ci.yml a failure of THIS gate.
    "tests/test_frontend_dependency_security_floor.py",
    # Row 283. bd-claim coordinates separate writer processes in a shared
    # checkout, so its real-process transaction gate runs on every PR rather
    # than depending on a diff router to infer this operational-tool coupling.
    "tests/test_v3_66_283_bd_claim_transactions.py",
    # Row 295. Non-cooperating observers do not take the registry lock, so the
    # claim publisher must keep the old complete record visible until one
    # atomic pathname replacement publishes the new complete record.
    "tests/test_v3_66_295_bd_claim_atomic_union.py",
    # @1256, backlog row 250. bd-regen-order is run on every cut, and its
    # selftest is the only precondition for treating the complete CHAIN as the
    # current generator denominator. A dropped member changes no application
    # path from which bd-band-derive could select this gate, so CI must run it
    # directly on every tree.
    "tests/test_v3_66_1256_regen_order_selftest_has_an_independent_denominator.py",
    # Row 243. Registration resolution is a tree-wide launcher contract, and
    # its copied-bd-mutate CI reproduction plus durable mutation battery cannot
    # be derived from whichever one of the seven owning tools a diff touches.
    "tests/test_row243_registration_resolves_without_ambient_luck.py",
    # Row 298. The end-to-end idempotence gate executes the entire canonical
    # regen chain twice, so its work-root ownership is a tree-wide concurrency
    # boundary. It runs in a dedicated shard against a disposable copy rather
    # than rewriting generated artifacts beneath sibling workers.
    "tests/test_v3_66_947_the_kb_manifest_can_be_regenerated.py",
    "tests/test_v3_66_1184_mutation_specs_are_tracked.py",
    "tests/test_v3_66_1185_bd_mutate_emits_canonical_specs.py",
    # Row 357. Anchor fragility is a whole-population property: no changed
    # subject path can make a diff router select the gate that audits every
    # tracked mutation spec and its independently recorded producer evidence.
    "tests/test_row357_mutant_anchors_are_not_fragile.py",
    "tests/test_v3_66_1186_bd_mutate_named_controls.py",
    "tests/test_v3_66_1187_bd_mutate_band_is_bounded.py",
    "tests/test_v3_66_1188_bd_mutate_review_controls.py",
    "tests/test_v3_66_1189_bd_mutate_durable_contract.py",
    "tests/test_v3_66_1190_bd_mutate_kills_process_tree.py",
    # Backlog 27's executable route is module-scoped, but it decides whether
    # bd-mutate may publish a false CAUGHT after a fixture contaminates the
    # restored-source control.  Mutation evidence is a safety boundary, so the
    # test runs on every PR in the verifier shard claimed below.
    # CI-SHARD-CLAIM backlog-27 mutation-verifiers tests/test_backlog_27_bd_mutate_replays_fixture_controls.py
    "tests/test_backlog_27_bd_mutate_replays_fixture_controls.py",
    "tests/test_v3_66_1191_the_sweep_cannot_take_a_live_run.py",
    "tests/test_frontend_secret_keys_in_sync.py",
    "tests/test_templates_list_identity.py",
    "tests/test_defect_scan_precision.py",
    # @1148. Also BD_GATE_SCOPE = "module" and also deliberately so: its subject
    # is bd-cut's release gate, not the tree. It is pinned into a shard because
    # the contract it holds -- only a measured exit 0 authorizes a cut -- is the
    # thing that decides whether ANY other gate's verdict is honoured. Until
    # v3.66.1145 the gate failed open and no test anywhere pinned it; the 12/12
    # GitHub result on that PR did not execute this contract at all.
    "tests/test_v3_66_1145_step0_fails_closed.py",
    # @1149. Third "module" entry, same reasoning one step further out. Its
    # subject is what the two release-gating tools DO TO THE TREE: bd-cut's
    # --rm-runtime-db defaulted to deleting downloader_history.db and its WAL,
    # which on test5 is the live service database, and bd-footguns wrote into
    # the tree it was judging. Neither is diff-derivable -- the destructive
    # default is one argparse keyword and the write happens in a delegate
    # subprocess -- so no band would necessarily run this, and the failure mode
    # is silent data loss on the box rather than a red test.
    "tests/test_v3_66_1149_a_cut_never_deletes_the_operators_database.py",
    # @1150. Fourth "module" entry. Its subject is the integrity of the ONE
    # archive object a --resume-zip cut judges, and the honesty of the two
    # discard helpers. Pinned into a shard for the same reason as 1149: none of
    # it is diff-derivable -- the seal is one chmod, the cleanup defect is an
    # `except OSError: pass`, and the leak it closes is invisible to the default
    # test harness, which erases residue on a green run. The failure mode is a
    # silent swap or a silent leak, never a red test.
    "tests/test_v3_66_1150_the_snapshot_is_really_sealed.py",
    # @1151. Fifth "module" entry, same subject one layer deeper: what a
    # --resume-zip cut's consumers are BOUND to. A pathname can be renamed out
    # from under them between two hashes; a descriptor cannot. Pinned into a
    # shard because none of it is diff-derivable -- the binding is one os.open
    # and a pass_fds, the symlink escape is a chmod that follows a link, and
    # the failure mode of every one is silent rather than red.
    "tests/test_v3_66_1151_the_snapshot_is_bound_to_a_descriptor.py",
    # @1152. Sixth "module" entry, and the one that makes the other five cost
    # something: a cleanup failure now sets an exit code in bd-cut and a
    # session exit status in _tmproot. Pinned into a shard because the failure
    # it guards is a SILENT GREEN -- three cuts made cleanup report honestly
    # and none made the report matter, so the regression to watch for is a
    # future edit quietly restoring "print and return 0".
    "tests/test_v3_66_1152_a_failed_cleanup_fails_the_run.py",
    # @1153. Seventh "module" entry. Its subject is what a destructive
    # operation is BOUND to: v3.66.1149-1152 moved the ownership proof nearer
    # the deletion four times and never joined them, so each cut left the same
    # rename+recreate seam one step further along. Pinned into a shard because
    # the failure is a silent wrong-object deletion -- never a red test -- and
    # because bd-footguns' UNKNOWN policy decides whether bd-cut's step 0 is
    # authorized at all.
    "tests/test_v3_66_1153_deletion_is_bound_to_the_object.py",
    # @1154. Eighth "module" entry, and the one that finally binds the CHILDREN.
    # 1153 bound the top object and left every child opened by name, so a
    # directory renamed onto a child pathname mid-walk was entered and emptied;
    # and success was still read off a pathname, so a tree renamed AWAY reported
    # clean. Pinned into a shard for the reason all seven above are: the failure
    # is a silent wrong-object deletion or a silent leak, never a red test, and
    # the matrix runs against all THREE removers so a drift between the copies
    # is red rather than being a fourth implementation nobody compares.
    "tests/test_v3_66_1154_the_object_not_the_name.py",
    # @1157. Ninth module-scoped safety boundary. Row 148 is another silent
    # green: stale or concurrently replaced hashed output can look like a new
    # Vite build unless cleanup authorization and final publication identity
    # are both bound. A diff-derived local band is not CI execution, so keep the
    # production-path regression in the deep tool shard claimed below.
    # CI-SHARD-CLAIM row-1157 toolchain-deep tests/test_v3_66_1157_build_output_is_from_this_attempt.py
    "tests/test_v3_66_1157_build_output_is_from_this_attempt.py",
    # Row 259. Five operator-facing measurement sites used their clean sentinel
    # when Git, source reads, scanners, artifact reads, or JSON parsing failed.
    # The subjects are module-scoped, but the shared fail-closed safety contract
    # must run on every PR because each regression otherwise exits or reports
    # green. The explicitly excluded bd-fleet site remains owned by row 254.
    "tests/test_failed_measurements_have_distinct_states.py",
    "tests/test_v3_66_799_audit_tool_selftests.py",
    # CI-SHARD-CLAIM row-1035 parity-static tests/test_v3_66_653_dep_freshness.py
    "tests/test_v3_66_653_dep_freshness.py",
    # CI-SHARD-CLAIM row-1035 parity-static tests/test_row331_guarded_imports_are_declared.py
    "tests/test_row331_guarded_imports_are_declared.py",
    # @1035. These three are repo-wide despite not looking it: they assert
    # invariants about the SUITE rather than a module -- the plugins guard
    # holding, the leaker population not growing, and no live PyPI call from a
    # dependency. Their exact, independently parsed shard homes are claimed per
    # suite rather than collapsed into one prose name. Added in the SAME cut
    # that created them, because 944, 947, 1031 and 1034 were all added to the
    # tree and never to this list, and a gate CI does not run does not exist.
    # CI-SHARD-CLAIM row-1035 measurement-isolation tests/test_v3_66_1046_gates_for_this_sessions_shapes.py
    "tests/test_v3_66_1046_gates_for_this_sessions_shapes.py",
    "tests/test_v3_66_1044_run_context_and_chains.py",
    # Row 289. SigIgn/SigBlk changed six test verdicts without appearing in
    # the run context. This gate compares both masks with the current process,
    # so every PR records and exercises that environment-identity boundary.
    "tests/test_row_289_inherited_signals_are_environment_identity.py",
    # @1207, scope decision 3. The two suites that assert what row 212 changes:
    # 1054 launches through the REAL CLI and proves `reap` kills the whole
    # process group (backlog 88), and 1087 proves a launched job's log exists
    # and is recorded in its entry. Both were diff-derivable only -- 1054 sits
    # in the frozen baseline below and 1087 declares `module` -- so the cut that
    # rewrites the launch transaction would have shipped with neither contract
    # measured on any PR. A gate CI does not run does not exist.
    "tests/test_v3_66_1054_launched_work_is_bounded_and_reapable.py",
    "tests/test_v3_66_1087_jobs_report_progress_not_just_liveness.py",
    # Row 312. A numeric PID signal is a bystander-kill boundary even though
    # this gate's source population is one tool. Run the forced exit/reuse
    # interleave on every PR; a diff-derived band is not release evidence that
    # the safety assertion itself remains reachable in CI.
    "tests/test_row312_bd_jobs_reap_holds_identity.py",
    # @1207 determinism review. These are the other row-212 contracts whose
    # subjects do not become safe merely because a diff-derived local band can
    # find them: 1132 owns registration-failure process restoration and 1106
    # owns the preflight UNKNOWN grade for an unreadable jobs registry.
    # A pull request that never runs them cannot prove any of those contracts.
    "tests/test_v3_66_1132_the_hunt_reaps_what_it_abandons.py",
    # @1241 row 237. Half of 1132's registration-failure contracts
    # moved here when the module was split; a pull request that runs
    # one file and not the other proves half of what it used to.
    "tests/test_v3_66_1132_the_hunt_reaps_registration_lifecycle.py",
    "tests/test_v3_66_1106_preflight_sees_scratch_and_orphans.py",
    # Row 344. The shell library accepts a caller-selected capture glob and
    # removes evidence, so its target-binding fixture is an always-scheduled
    # safety boundary rather than a test left to diff-derived reachability.
    "tests/test_row344_capture_prune_is_target_bound.py",
    # @1206 provider-facade. Retained implementation modules and re-imported
    # public facades form a process-wide generation boundary, so the direct
    # concurrency/ownership gate must execute even when no provider file is in
    # the diff that triggered CI.
    "tests/test_provider_resolve_surface_lock.py",
    # Rows 617/623/624/625/628/629/631. This module owns secrets state across
    # filesystem probes, two backend types, a config-writer interleaving and
    # extension rate accounting, so its contract is scheduled on every PR.
    "tests/test_rows617_623_624_625_628_629_631_secrets_family.py",
    # Row 347. The provider-band gate's own early return escaped its only
    # catcher. Its independent exact-call receipt now runs on every PR beside
    # the provider facade whose affected band it constrains.
    "tests/test_v3_66_1180_band_derivation_paths.py",
    "tests/test_v3_66_1043_measurement_and_fleet_tools.py",
    "tests/test_v3_66_1040_remote_job_registry.py",
    "tests/test_v3_66_1034_guards_survive_a_module_wipe.py",
    "tests/test_v3_66_1031_socket_recorder_stages.py",
    # @1256. This drives real serial and -n 2 subprocess sessions because its
    # subject is the ordering between pytest cleanup/summary hooks and the
    # worker/master filesystem boundary. No application-module diff can derive
    # that population, and a missing run is the same silent clean zero it pins.
    "tests/test_v3_66_1256_socket_recorder_keeps_its_measurements.py",
    "tests/test_no_test_writes_the_repo_plugins_dir.py",
    "tests/test_v3_66_1191_a_run_root_records_its_own_outcome.py",
    # Row 245. A public test root that appears before its marker and held lock
    # becomes permanent UNKNOWN evidence if setup loses any resource. This
    # gate injects every pre-publication boundary and therefore belongs beside
    # the session-root lifecycle owners in the shard claimed below.
    # CI-SHARD-CLAIM row-245 isolation tests/test_v3_66_1255_test_roots_publish_ownership_atomically.py
    "tests/test_v3_66_1255_test_roots_publish_ownership_atomically.py",
    # @1452. The shuffle lane's containment gate. Its subject is the TREE: the
    # exact bytes of the A5 canonical full-suite command, which requirements
    # manifests may declare pytest-randomly, and whether anything on the merge
    # path invokes the lane. No changed path derives that population, and
    # pytest AUTO-LOADS an installed plugin -- so the failure it guards against
    # is a one-line manifest edit that silently turns every OTHER shard into a
    # different experiment. It belongs beside the session-isolation owners in
    # the shard claimed below because cross-file order dependency is the class
    # they all police.
    # CI-SHARD-CLAIM row-1452 isolation tests/test_v3_66_1452_a_shuffle_lane_finds_order_dependencies.py
    "tests/test_v3_66_1452_a_shuffle_lane_finds_order_dependencies.py",
    # @1085. Its subject is the test SESSION's module table, not the tree -- the
    # same reason 1034 and 1031 sit in the shard claimed below. A
    # patch.dict(sys.modules) that evicts a lazily-imported module poisons an
    # identity-keyed cache for the rest of a worker process, which is how a
    # v3.66.1083 capture on test6 saw httpx re-raise a raw httpcore error.
    # CI-SHARD-CLAIM row-1085 isolation tests/test_v3_66_1085_module_identity_survives_a_sys_modules_patch.py
    "tests/test_v3_66_1085_module_identity_survives_a_sys_modules_patch.py",
    # @1072, and the first entry is this file. MEASURED at v3.66.1071: the
    # `gates` job runs ZERO pytest, and this suite is in no shard -- so the
    # only thing that would notice a dropped shard entry has never run on a
    # PR. It was created at f736748, the same commit that deleted the gates
    # job's pytest step, and appeared in that diff only inside a comment.
    # The gate against a file falling out of every shard fell out of every
    # shard, in the cut that wrote it.
    "tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py",
    # @1072. The four gates shipped 2026-08-12 that this policy exists to
    # catch: each was added to the tree and to neither list, which is the
    # eighth occurrence of the failure (944, 947, 1031, 1034 preceded them).
    "tests/test_v3_66_1062_vision_probes_are_loadable_images.py",
    "tests/test_v3_66_1064_provisioning_paths_do_not_diverge.py",
    "tests/test_v3_66_1067_the_leaker_census_reads_code_not_prose.py",
    "tests/test_v3_66_1068_modwatch_measures_per_file.py",
    # @1080. The policy caught its own author one cut later: this file declares
    # itself repo-wide and the union assertion refused it until it was wired in.
    "tests/test_v3_66_1080_the_suite_reclaims_its_tmpdirs.py",
    # @1116, backlog row 105. THE TWO GATES THAT GUARD THE REGISTERS, which
    # until now ran on no PR at all: both appeared ZERO times in ci.yml, zero
    # times in this set, and both sat in the frozen baseline below. Section 7
    # says a gate CI does not run is a gate that does not exist -- and the
    # register is the one subject where that is invisible, because a stale
    # register still READS fine. A 2026-08-13 audit found the cost: one row
    # CLOSED for a cut that did not touch it, one closed without the PARTIAL
    # its own text needed, and four ids that never existed.
    "tests/test_v3_66_1052_the_backlog_is_machine_visible.py",
    # @1082, backlog 99. Twenty-four suites that enumerate the tree and
    # ran on no PR -- tombstones, anti-duplication ratchets and
    # denominator gates, several named by CLAUDE.md section 4's own
    # axis-6 table. Split across five shards drawn from MEASURED time
    # (196s total locally), not count, per the @939 precedent.
    # First tree-gate partition.
    "tests/test_capture_shell_runtime.py",
    # Row 342. This module executes the capture preflight's shared tree-state
    # predicate. A failed Git status is UNKNOWN, never affirmative clean
    # evidence, and its clean/dirty controls make the inverse reachable too.
    "tests/test_v3_66_1079_capture_refuses_a_dirty_tree.py",
    # Row 285. Deployment, capture-instance teardown, PostgreSQL capability
    # persistence and configured storage all fail closed on unavailable state.
    "tests/test_row_285_deploy_fail_open.py",
    # Row 175: capture service/port/state ownership is a tree-wide execution
    # boundary. A fixed resource reintroduced anywhere in capture.sh, its
    # service installer, the seeder, or the live runner must fail every PR.
    "tests/test_parallel_capture_services.py",
    # Row 340. A version-matching error body used to make deploy.sh report
    # health verified over HTTP 500. This real deploy-process gate is pinned
    # independently of the source-derived band because every fleet deploy
    # rests on the step-12 readiness claim.
    "tests/test_deploy_script.py",
    # Row 290: no-argument capture must reach its local fixture and every later
    # step; the same executable harness proves --parallel still owns and routes
    # a real distinct port pair.
    "tests/test_row290_capture_serial_fixture_port.py",
    "tests/test_capture_vault_is_isolated.py",
    "tests/test_v3_66_1191_two_captures_cannot_share_a_vault.py",
    "tests/test_v3_66_1191_retention_review_edges.py",
    "tests/test_v3_66_1018_registrable_domain_drain.py",
    "tests/test_census_file_size_drift.py",
    "tests/test_zip_era_tools_stay_retired.py",
    # @1117, backlog row 113. A tombstone over the tracked tree: no served page
    # may link to the cockpit landing route retired at v3.66.344. Same family as
    # the two retirement gates above it. The path is deliberately NOT spelled
    # here -- that gate bans the literal, and a comment is inside the
    # denominator of every check that reads source text, so naming the removed
    # thing in order to explain it recreates it. It did, on this line, in the
    # cut that added the gate.
    "tests/test_v3_66_1117_cockpit_home_stays_retired.py",
    # Second tree-gate partition.
    "tests/test_v3_66_1013_registrable_domain.py",
    "tests/test_v3_66_1197_ambient_locale_into_subprocess.py",
    "tests/test_history_records_whether_bytes_were_fetched.py",
    "tests/test_v3_66_972_library_missing_stays_retired.py",
    # @1234, row 186. It judges .github/workflows/ci.yml itself -- whether the
    # guard-drift lane is scheduled, failure-propagating, and actually detects a
    # tampered guard when its own run script is executed. A gate about CI that
    # CI does not run is the purest form of the defect it exists to close, and
    # the workflow is not in any diff-derived band, so nothing else would pull
    # it in on the pull request that disabled the step.
    "tests/test_v3_66_1234_ci_really_executes_the_guard_lane.py",
    # @1242, row 228. It sources scripts/provision_test_host.sh under
    # instrumentation and reads what the shell DID -- whether both optional
    # capabilities were really dispatched, once each, graded optional, with the
    # shared library really loaded. The provisioner is not application code and
    # sits in no diff-derived band, so a pull request that moves the dispatch
    # below the verdict or into a branch nothing takes would otherwise reach
    # main with every text arm still green.
    "tests/test_v3_66_1242_the_provisioner_really_dispatches_the_capabilities.py",
    # F35, backlog row 266. An unavailable critical IPv6 measurement must hold
    # an armed kill switch without being called a leak. The gate drives all
    # four HTTP outcomes in process and is safety-bearing regardless of which
    # application path a future change touches.
    "tests/test_v3_66_1265_ipv6_unknown_holds_killswitch.py",
    # Row 286 independently re-verifies F35 through F38 at their permission
    # consumers. In particular, it covers the dual-stack IPv4 outcome that
    # the first F35 repair still called a measured pass, so this safety gate
    # must run regardless of which of the four application paths changes.
    "tests/test_row_286_laundered_failures_hold.py",
    # Row 293 closes the inverse reachability hole left by row 286: the real
    # IPv6 probe must be able to compare a provider-supplied expected address,
    # advance an armed switch's streak, and clear it, while every unmeasured
    # outcome remains UNKNOWN. This production-path safety contract is not
    # replaceable by a diff-derived run or a fabricated ProbeResult.
    "tests/test_row_293_ipv6_measured_pass_is_reachable.py",
    # Row 299. Merely collecting U42 used to overwrite the live sampler's
    # process-wide count and gap even when every U42 node was deselected. This
    # gate runs a real same-process collection and asserts the exact values an
    # unrelated selected test sees, so it is independent of changed paths.
    "tests/test_row_299_live_sampling_collection_isolation.py",
    # Row 309. The capture manifest cannot define its own completeness. This
    # gate independently pins all 52 views / 104 themed rows, reaches every
    # failure seam, and drives the real navigator refusal. Its visual-census
    # subject remains tree-wide regardless of which route or builder changes.
    "tests/test_row_309_capture_manifest_contract.py",
    # Row 296 drives the runner's real pre-claim VPN admission seam. A raised
    # measurement and an unavailable runtime must never reach the download,
    # while the no-VPN-configured fast path must still proceed without waiting.
    "tests/test_row_296_vpn_runner_gate_holds_on_unmeasurable_tunnel.py",
    # Third tree-gate partition.
    "tests/test_v3_66_820_share_tools_saw_no_session_keys.py",
    "tests/test_history_file_size_is_the_size_on_disk.py",
    "tests/test_playwright_engines_single_source.py",
    # Row 308. Visual-audit identity is a release boundary across the complete
    # capture population and both offline builders, independent of which
    # capture or builder source changes in a future cut.
    "tests/test_row308_visual_audit_identity.py",
    # Fourth tree-gate partition.
    # Walks every tracked tests/test*.py for assertions that are true for
    # every input, so a new test file changes its denominator (@1098).
    "tests/test_v3_66_1098_no_assertion_can_be_trivially_true.py",
    # The mirror slice: assertions FALSE for every input, plus statically
    # unreachable ones. Same denominator, same reason it is repo-wide (@1108).
    "tests/test_v3_66_1108_no_assertion_can_be_trivially_false.py",
    # Row 297. Every real recon-corpus fixture contributes to the synthesized
    # request/parameter census, so changing any fixture changes this gate's
    # exact denominator independently of an application-module diff.
    "tests/test_ct1_corpus_validation.py",
    "tests/test_v3_66_938_atomic_write_sidecars_are_ignored.py",
    "tests/test_v3_66_935_scan_wait_reports_non_convergence.py",
    "tests/test_history_columns_go_through_migrations.py",
    "tests/test_v3_66_1059_recorder_derives_its_blind_spot_counts.py",
    "tests/test_v3_66_968_anchor_gate_sees_frontend_citations.py",
    "tests/test_codex_handoff_stays_retired.py",
    "tests/test_sandbox_home_stays_retired.py",
    "tests/test_v3_66_1192_build_release_sh_stays_retired.py",
    "tests/test_deploy_manifest_stays_retired.py",
    "tests/test_gitignore_rules_actually_match.py",
    "tests/test_task_tracker_stays_retired.py",
    "tests/test_v3_66_918_tracked_source_denominator.py",
    "tests/test_v3_66_944_static_kb_manifest_describes_the_tree.py",
    "tests/test_generated_artifact_workflow.py",
    "tests/test_git_deploy_gaps_are_documented.py",
    # Row 343. This executes the cloud provisioner and the canonical runbook in
    # isolated fresh-host fixtures, covering ordering, cross-host transfer and
    # the application-written sites_config shape on every PR.
    "tests/test_row343_fresh_host_bringup.py",
    # Row 259. These five source-derived safety censuses already rejected
    # credential disclosure, operator-state writes, migration-seam bypasses,
    # and uncontained browser launches, but all five remained legacy-baselined
    # and therefore ran in no PR shard. They form one scheduling contract: a
    # gate CI does not execute does not exist.
    "tests/test_capture_csrf_diag_redacts_cookies.py",
    "tests/test_home_config_stores_are_guarded.py",
    "tests/test_v3_66_1009_live_results_are_bundled.py",
    "tests/test_v3_66_285_cloak_parity.py",
    "tests/test_v3_66_795_mod3_seam.py",
    # Toolchain verifier partition.
    "tests/test_desandbox_tool_verifiers.py",
    # Cut 1459 (the shape of rows 440/441/442, whose own fix left this site).
    # The ffmpeg_path pin is only worth what its WEAKEST exec site is
    # worth, and its subject is therefore every shipped module rather than any
    # changed path: rows 440/441/442 fixed four modules and left live_recorder
    # handing a bare "ffmpeg" to a live HLS-over-HTTPS recording, the exact case
    # the pin exists for. No diff can select a tree-wide denominator.
    "tests/test_row1459_the_verified_binary_is_the_executed_binary.py",
}

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
#             -- grows on its own, no literal, and is already forced into
#             _DECLARED by test_every_repo_wide_file_is_in_the_declared_set
#   remainder _NON_DERIVABLE_DECLARED below -- a CLOSED set, pinned by IDENTITY
#             rather than by count, exactly as gate_scope_baseline.txt is
#
# The identities and not the count is the whole point: a same-size swap inside
# the remainder is named, and a member deleted from _DECLARED and ci.yml in one
# commit is named. Adding a gate never edits this block, because a new gate
# declares `repo-wide` and lands in the derived half. This set may only SHRINK,
# and only when a legacy gate is deliberately retired or promoted to a marker.
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
    "tests/test_all_sources_parse.py",  # legacy-baseline
    "tests/test_app_measurements_fail_closed.py",  # module
    "tests/test_backlog_27_bd_mutate_replays_fixture_controls.py",  # module
    "tests/test_cloud_setup_truthfulness.py",  # module
    "tests/test_deploy_script.py",  # module
    "tests/test_failed_measurements_have_distinct_states.py",  # module
    "tests/test_ffmpeg_capability_health.py",  # module
    "tests/test_generated_artifacts_are_not_tracked.py",  # legacy-baseline
    "tests/test_gui_parity.py",  # legacy-baseline
    "tests/test_import_graph_no_new_edges.py",  # legacy-baseline
    "tests/test_no_test_writes_the_repo_plugins_dir.py",  # legacy-baseline
    "tests/test_pin_index_in_sync.py",  # legacy-baseline
    "tests/test_pk_mirrors_stay_retired.py",  # legacy-baseline
    "tests/test_provider_resolve_surface_lock.py",  # legacy-baseline
    "tests/test_register_content_amend.py",  # module
    "tests/test_release_hygiene_gates.py",  # legacy-baseline
    "tests/test_route_index_in_sync.py",  # legacy-baseline
    "tests/test_row311_app_config_writers_are_serialized.py",  # module
    "tests/test_row312_bd_jobs_reap_holds_identity.py",  # module
    "tests/test_row313_bd_job_identity.py",  # module
    "tests/test_row335_release_gate_populations.py",  # module
    "tests/test_row339_measurement_noise_bounds.py",  # module
    "tests/test_row344_capture_prune_is_target_bound.py",  # module
    "tests/test_row345_opv_a11y_requires_served_cockpit.py",  # module
    "tests/test_row349_shared_caches_are_identity_bound.py",  # module
    "tests/test_row350_job_api_durable_truth.py",  # module
    "tests/test_row356_cookie_quality_reports_unknown.py",  # module
    "tests/test_row360_turnstile_bypass_is_installed.py",  # module
    "tests/test_rows617_623_624_625_628_629_631_secrets_family.py",  # module
    "tests/test_row363_affordance_learning.py",  # module
    "tests/test_row434_resume_cannot_leave_the_hold_state_it_set.py",  # module
    "tests/test_row_282_bd_opv_isolates_every_store.py",  # module
    "tests/test_scan_version_pins_fixture.py",  # legacy-baseline
    "tests/test_settings_center_slice4.py",  # legacy-baseline
    "tests/test_source_windows_do_not_shift.py",  # legacy-baseline
    "tests/test_toolchain_534.py",  # legacy-baseline
    "tests/test_u45_capture_sh_shipped.py",  # legacy-baseline
    "tests/test_v3_57_phase9.py",  # module
    "tests/test_v3_66_1031_socket_recorder_stages.py",  # legacy-baseline
    "tests/test_v3_66_1034_guards_survive_a_module_wipe.py",  # legacy-baseline
    "tests/test_v3_66_1040_remote_job_registry.py",  # legacy-baseline
    "tests/test_v3_66_1043_measurement_and_fleet_tools.py",  # legacy-baseline
    "tests/test_v3_66_1044_run_context_and_chains.py",  # legacy-baseline
    "tests/test_v3_66_1046_gates_for_this_sessions_shapes.py",  # legacy-baseline
    "tests/test_v3_66_1054_launched_work_is_bounded_and_reapable.py",  # legacy-baseline
    "tests/test_v3_66_1079_capture_refuses_a_dirty_tree.py",  # module
    "tests/test_v3_66_1087_jobs_report_progress_not_just_liveness.py",  # module
    "tests/test_v3_66_1106_preflight_sees_scratch_and_orphans.py",  # module
    "tests/test_v3_66_1111_a_wedged_capture_lane_is_bounded.py",  # module
    "tests/test_v3_66_1132_the_hunt_reaps_registration_lifecycle.py",  # module
    "tests/test_v3_66_1132_the_hunt_reaps_what_it_abandons.py",  # module
    "tests/test_v3_66_1142_fleet_run_is_hermetic.py",  # module
    "tests/test_v3_66_1145_step0_fails_closed.py",  # module
    "tests/test_v3_66_1149_a_cut_never_deletes_the_operators_database.py",  # module
    "tests/test_v3_66_1150_the_snapshot_is_really_sealed.py",  # module
    "tests/test_v3_66_1151_the_snapshot_is_bound_to_a_descriptor.py",  # module
    "tests/test_v3_66_1152_a_failed_cleanup_fails_the_run.py",  # module
    "tests/test_v3_66_1153_deletion_is_bound_to_the_object.py",  # module
    "tests/test_v3_66_1154_the_object_not_the_name.py",  # module
    "tests/test_v3_66_1157_build_output_is_from_this_attempt.py",  # module
    "tests/test_v3_66_1158_fleet_provenance_fails_closed.py",  # module
    "tests/test_v3_66_1159_fleet_prune_is_object_bound.py",  # module
    "tests/test_v3_66_1178_orphan_tempfiles_are_recursive.py",  # module
    "tests/test_v3_66_1180_band_derivation_paths.py",  # module
    "tests/test_v3_66_1183_safe_temp_janitors.py",  # module
    "tests/test_v3_66_1185_bd_mutate_emits_canonical_specs.py",  # module
    "tests/test_v3_66_1186_bd_mutate_named_controls.py",  # module
    "tests/test_v3_66_1187_bd_mutate_band_is_bounded.py",  # module
    "tests/test_v3_66_1188_bd_mutate_review_controls.py",  # module
    "tests/test_v3_66_1189_bd_mutate_durable_contract.py",  # module
    "tests/test_v3_66_1190_bd_mutate_kills_process_tree.py",  # module
    "tests/test_v3_66_1208_the_heartbeat_keeps_foreground_signal_semantics.py",  # module
    "tests/test_v3_66_1209_every_detached_launch_keeps_signal_semantics.py",  # module
    "tests/test_v3_66_1215_a_wrapper_must_not_alter_its_subject.py",  # module
    "tests/test_v3_66_1216_vitest_is_a_real_ci_denominator.py",  # module
    "tests/test_v3_66_1217_a_fixture_is_not_wiring.py",  # module
    "tests/test_v3_66_121_login_flow_derives_the_observed_drive.py",  # module
    "tests/test_v3_66_1255_bd_fleet_measurements_fail_closed.py",  # module
    "tests/test_v3_66_261_contended_lifecycle_lock.py",  # module
    "tests/test_v3_66_283_bd_claim_transactions.py",  # module
    "tests/test_v3_66_284_integrity.py",  # module
    "tests/test_v3_66_295_bd_claim_atomic_union.py",  # module
    "tests/test_v3_66_653_dep_freshness.py",  # legacy-baseline
    "tests/test_v3_66_799_audit_tool_selftests.py",  # legacy-baseline
    "tests/test_versync_gate.py",  # legacy-baseline
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
# that calls itself repo-wide must be in _DECLARED, which the union assertion
# then forces into a shard.
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


def _gate_suite_job() -> tuple[str, dict]:
    """The one matrix job whose entries carry the declared suites."""
    for job_name, job in ((_workflow().get("jobs") or {}).items()):
        include = (((job.get("strategy") or {}).get("matrix") or {})
                   .get("include") or [])
        if any("suites" in entry for entry in include):
            return str(job_name), job
    pytest.fail("no sharded gate job found to check")


def _shard_lists() -> dict[str, list[str]]:
    """{shard name: [test paths]} from the matrix include entries.

    Reads the matrix rather than grepping the run block: a grep would count a
    path named in a comment, and this file's own docstring names several.
    """
    wf = _workflow()
    for job_name, job in (wf.get("jobs") or {}).items():
        include = (((job.get("strategy") or {}).get("matrix") or {})
                   .get("include") or [])
        if not include:
            continue
        out = {}
        for entry in include:
            if "suites" not in entry:
                continue
            out[str(entry.get("name") or len(out))] = str(entry["suites"]).split()
        if out:
            return out
    return {}


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
            return value.value
        return f"<non-literal: {type(value).__name__}>"
    return None


def _scope_map(paths) -> dict[str, object]:
    """{path: declared scope} for every path that declares one."""
    out = {}
    for rel in paths:
        scope = _declared_scope(_REPO / rel)
        if scope is not None:
            out[rel] = scope
    return out


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
    assert _shard_claim_mismatches(
        [("ordinary-prose-control", "measurement-tools", suite)],
        _shard_lists(),
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
        "strategy.matrix.include entries each carry a `suites` string. Without "
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
        f"{extra}. Add them to _DECLARED with a reason, or remove them -- an "
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
        if not any("suites" in e for e in include):
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
        include = (((job.get("strategy") or {}).get("matrix") or {})
                   .get("include") or [])
        if not any("suites" in e for e in include):
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
        f"whatever the diff touched and belongs in CI -- add it to _DECLARED "
        f"and to a gate-suites shard in the same cut. 'module' means an "
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
    they are already writing, and the gate names the other two edits.
    """
    scopes = _scope_map(_tracked_test_files())
    repo_wide = {p for p, s in scopes.items() if s == "repo-wide"}
    assert repo_wide, (
        "no file declares itself repo-wide, so this assertion is vacuous -- at "
        "adoption five do, and a drop to zero means the marker was renamed or "
        "the reader broke")

    undeclared = _repo_wide_not_declared(scopes, _DECLARED)
    assert not undeclared, (
        f"file(s) declaring {_SCOPE_MARKER} = 'repo-wide' but absent from "
        f"_DECLARED, so they run on no PR while the check stays green: "
        f"{undeclared}")


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
