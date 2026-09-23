#!/usr/bin/env python3
"""CI gate shards: the ONE owner of which test file runs in which gate-suites shard.

O1264(d) / O1265(g), 2026-09-23. `.github/workflows/ci.yml` carries shard NAMES
only; membership is derived here, at run time, from the declared gate census:

  population = (every tracked tests/test*.py that assigns BD_GATE_SCOPE = "repo-wide"
                | _NON_DERIVABLE_DECLARED) - tests/PROCESS_TESTS.txt
               | _DB_PRUNE_SAFETY_FAMILY

The two literal sets live in tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py
(the declaration owner; read by AST, never imported) exactly as
tools/generate_ci_docsonly_shards.py already reads them. A new gate declares the
marker and joins a shard by name glob. Nobody adds a line to ci.yml.

Two kinds of shard, resolved in order, first match wins:
  PINNED -- explicit membership: capability shards (node, Chromium), the H686
            quarantine, the long-pole isolation runner and the contexts O1264(d)
            keeps required. A pinned file must be in the population.
  GLOBS  -- name globs over the remaining population, runtime-balanced from the
            job durations of run 35763678888 (train/60, 2026-09-22): each shard
            <= ~6 min serial pytest. A glob shard with no member is an error --
            `pytest` with no paths would run the whole tree.

CLI (stdlib only; runs before any pip install):
  python tools/ci_shards.py names            one shard name per line
  python tools/ci_shards.py files <shard>    space-separated paths, exit 3 if empty
  python tools/ci_shards.py check            exit 0 iff every population file is in
                                             exactly one shard and no shard is empty
  python tools/ci_shards.py report           per-shard counts
"""
from __future__ import annotations

import ast
import fnmatch
import subprocess
import sys
from pathlib import Path

DECLARATION = Path("tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py")
PROCESS_TESTS = Path("tests/PROCESS_TESTS.txt")
SCOPE_MARKER = "BD_GATE_SCOPE"


class ShardError(RuntimeError):
    """A membership rule that cannot be satisfied; CI must go red, not guess."""


def gate_scope(text: str) -> str | None:
    """Module-level BD_GATE_SCOPE assignment value, or None. AST, module scope
    only (a docstring or comment naming the marker declares nothing)."""
    if SCOPE_MARKER not in text:          # cheap substring guard before parsing
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == SCOPE_MARKER:
                    try:
                        return ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        return None
    return None


def _literal_set(repo: Path, name: str) -> set[str]:
    source = (repo / DECLARATION).read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, set):
                return value
    raise ShardError(f"{DECLARATION} declares no set literal {name}")


def tracked_test_files(repo: Path) -> list[str]:
    """`git ls-files tests/test*.py`; falls back to a directory glob outside git."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--", "tests/test*.py"],
            capture_output=True, text=True, check=True).stdout
        return sorted(line for line in out.splitlines() if line)
    except (OSError, subprocess.CalledProcessError):
        return sorted(f"tests/{p.name}" for p in (repo / "tests").glob("test*.py"))


def process_tests(repo: Path) -> set[str]:
    path = repo / PROCESS_TESTS
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


def population(repo: Path) -> set[str]:
    derived = {
        rel for rel in tracked_test_files(repo)
        if gate_scope((repo / rel).read_text(encoding="utf-8", errors="replace"))
        == "repo-wide"}
    declared = (derived | _literal_set(repo, "_NON_DERIVABLE_DECLARED")) - process_tests(repo)
    family = _literal_set(repo, "_DB_PRUNE_SAFETY_FAMILY")
    # Mutation anchor (tests/mutants/*ci*.json "CI omits <file>" mutants): keep
    # this return structural -- no literal, no `=` -- so row357 classes it stable.
    return declared | family


PINNED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("version-at-land", (
        # O1264(d) required context: the version-at-land gate.
        "tests/test_row_version_at_land.py",
    )),
    ("safety-censuses", (
        # O1264(d) required context (failed 4x on train PRs, catches things).
        "tests/test_row532_a_mutant_anchor_must_resolve_into_code.py",
        "tests/test_row709_state_seed_is_not_a_verdict.py",
        "tests/test_capture_csrf_diag_redacts_cookies.py",
        "tests/test_home_config_stores_are_guarded.py",
        "tests/test_v3_66_1009_live_results_are_bundled.py",
        "tests/test_v3_66_285_cloak_parity.py",
        "tests/test_row667_keeper_browser_log_names_persistence.py",
        "tests/test_v3_66_795_mod3_seam.py",
    )),
    ("mutation-tools", (
        # O1264(d) required context: mutation tools + verifiers (was mutation-tools + mutation-verifiers).
        "tests/test_row810_spec_collection_slice_0.py",
        "tests/test_row705_published_denominators.py",
        "tests/test_row788_transform_control_is_separate.py",
        "tests/test_v3_66_1184_mutation_specs_are_tracked.py",
        "tests/test_row797_three_login_seams_carry_durable_mutant_pins.py",
        "tests/test_cut_quality_permits.py",
        "tests/test_row357_mutant_anchors_are_not_fragile.py",
        "tests/test_row470_corpus_tools_fail_closed.py",
        "tests/test_row355_mutate_timing_is_schedule_stable.py",
        "tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py",
        "tests/test_rowssrf_loopback_reason_is_structured.py",
        "tests/test_row779_template_sandbox_browser_mode_pins_the_vetted_address.py",
        "tests/test_row804_browser_redirect_bypasses_metadata_guard.py",
        "tests/test_row531_denominators_are_derived_not_pinned.py",
    )),
    ("mutation-tools-b", (
        # Spec-collection slices 1+2 (~200 s each, serial): kept off mutation-tools so that context stays <= 6 min.
        "tests/test_row810_spec_collection_slice_1.py",
        "tests/test_row810_spec_collection_slice_2.py",
    )),
    ("artifacts-pins", (
        # Regenerated-artifact pins; tests/test_row387_ast_version_pin_guard.py asserts it runs exactly once here.
        "tests/test_authority_documents.py",
        "tests/test_changelog_draft_placeholder_is_refused.py",
        "tests/test_v3_66_1183_inv_tags_generated.py",
        "tests/test_v3_66_1183_source_window_content.py",
        "tests/test_pin_index_in_sync.py",
        "tests/test_row387_ast_version_pin_guard.py",
        "tests/test_row1435_band_verdict_transfer.py",
        "tests/test_settings_center_slice4.py",
        "tests/test_endpoint_catalog_in_sync.py",
        "tests/test_function_index_in_sync.py",
        "tests/test_route_map_invariant.py",
        "tests/test_settings_center_slice5.py",
        "tests/test_rows972_973_rendered_runtime_controls.py",
        "tests/test_rows972_973_rendered_proxy_forms.py",
        "tests/test_footgun_detectors_are_executed_by_ci.py",
        "tests/test_all_sources_parse.py",
    )),
    ("application-safety", (
        # H686 quarantine: the shard that hangs (O1254 non-required until green twice on main). Pinned so it cannot contaminate a required shard.
        "tests/test_app_measurements_fail_closed.py",
        "tests/test_row439_segmented_transfers_honor_the_egress_gate.py",
        "tests/test_row773_records_carry_egress_identity.py",
        "tests/test_v3_66_284_integrity.py",
        "tests/test_row492_a_release_proves_what_it_frees.py",
        "tests/test_ffmpeg_capability_health.py",
        "tests/test_row363_affordance_learning.py",
        "tests/test_row356_cookie_quality_reports_unknown.py",
        "tests/test_row360_turnstile_bypass_is_installed.py",
        "tests/test_turnstile_click.py",
        "tests/test_row763_child_frame_turnstile.py",
        "tests/test_row764_turnstile_bypass_verdict.py",
        "tests/test_row765_scrapling_selector_compatibility.py",
        "tests/test_row763_frame_urls_never_reach_the_model_prompt.py",
        "tests/test_rows706_714_test_hygiene.py",
        "tests/test_row713_deep_integration_token_egress.py",
        "tests/test_row1043_protocol_message_event_dispatching.py",
        "tests/test_row346_plugin_sandbox_is_truthful.py",
        "tests/test_row311_app_config_writers_are_serialized.py",
        "tests/test_v3_66_261_contended_lifecycle_lock.py",
        "tests/test_v3_57_phase9.py",
        "tests/test_row434_resume_cannot_leave_the_hold_state_it_set.py",
        "tests/test_v3_62_2_guards.py",
        "tests/test_row634_live_registry_iteration_is_snapshotted.py",
        "tests/test_login_api_refuses_impossible_manual_start.py",
        "tests/test_row772_rejected_login_is_not_success.py",
        "tests/test_row772_astra_settling.py",
        "tests/test_row667_login_attempt_accounting.py",
        "tests/test_row740_login_cap_writer_atomicity.py",
        "tests/test_row806_health_payload_names_the_deployed_cloak_state.py",
        "tests/test_row774_login_submit_refuses_cross_origin_navigation.py",
        "tests/test_row723_login_flow_channel_fallback_is_filed_under_its_site.py",
        "tests/test_row719_scene_classifier_underselects.py",
        "tests/test_row787_sentinel_must_not_look_found.py",
        "tests/test_row741_relogin_refusals_are_typed.py",
        "tests/test_row769_login_logs_carry_the_site_id.py",
        "tests/test_keepalive_default_off.py",
        "tests/test_row731_signed_url_branch_uses_pair_predicate.py",
        "tests/test_row732_site_editor_redaction_descends.py",
        "tests/test_row751_template_apply_gates_captcha_egress.py",
        "tests/test_row751_template_strip_recurses.py",
        "tests/test_row675_minified_js_is_not_a_secret.py",
        "tests/test_test2d_1_persistent_profile_cookie_jar.py",
        "tests/test_row777_retry_ladder.py",
        "tests/test_row771_interstitial_comma.py",
        "tests/test_row762_gate_click_refuses_prechecked_billing_upsell.py",
        "tests/test_row703_ssrf_transport_is_installed_everywhere.py",
        "tests/test_row703_a_proxy_shadows_the_guarded_transport.py",
        "tests/test_row703_the_site_to_policy_map_is_asserted.py",
        "tests/test_row785_login_evidence_filenames_are_shell_safe.py",
        "tests/test_row805_ssrf_census_covers_every_transport.py",
        "tests/test_row780_sites_config_listing_url_round_trip.py",
        "tests/test_row750_ipv6_unwrapped_metadata_bypass.py",
        "tests/test_row971_onboarding_picks_content_not_login.py",
        "tests/test_row991_resource_quota_budget_file_descriptor_utilization_gauge.py",
        "tests/test_row1025_db_connection_lifecycle_traps.py",
    )),
    ("isolation", (
        # Long poles that need their own runner (@1207: 1043/1046/1040/1132) and the F31 nested-pytest / sacrificial-DB probes.
        "tests/test_v3_66_1046_tool_state_1040.py",
        "tests/test_v3_66_1046_tool_state_1043.py",
        "tests/test_v3_66_1046_tool_state_1044.py",
        "tests/test_v3_66_1046_tool_state_1054.py",
        "tests/test_v3_66_1220_a_timeout_names_its_test.py",
        "tests/test_row_pytest_banners_only_on_failure.py",
        "tests/test_child_test_install_dir_isolation.py",
        "tests/test_rowinstalldir_service_installer.py",
    )),
    ("parity-graph", (
        # NODE shard: the only shard gate-suites provisions node for (setup-node/npm ci condition names it; tests/test_v3_66_1218_* re-derives the delegating population). Was parity-graph + parity-vitest-b.
        "tests/test_t7_notifications_wired.py",
        "tests/test_t3_t4_wired.py",
        "tests/test_t5_t6_wired.py",
        "tests/test_v3_66_1240_supervisor_settings_seeded.py",
        "tests/test_t8_cluster_wired.py",
        "tests/test_t11_approval_wired.py",
        "tests/test_t10_devtools_wired.py",
        "tests/test_secret_display_never.py",
        "tests/test_frontend_dependency_security_floor.py",
        "tests/test_t2_history_wired.py",
        "tests/test_t1_dashboard_wired.py",
        "tests/test_csrf_tool_contracts.py",
        "tests/test_csrf_contract_reachability.py",
        "tests/test_v3_66_1255_frontend_build_is_isolated.py",
        "tests/test_t9a_live_stream_wired.py",
        "tests/test_t9b_push_wired.py",
        "tests/test_spa_root_routing_contract.py",
    )),
    ("download-chain", (
        # CHROMIUM shard: the only shard with `playwright install` (tests/test_row386_* asserts the pairing). Was download-chain + template-selectors.
        "tests/test_row386_the_download_chain_is_gated.py",
        "tests/test_row657_astra_bucket_progress.py",
        "tests/test_row775_turnstile_one_click_affordance.py",
        "tests/test_row761_listing_facet_is_not_a_download_candidate.py",
        "tests/test_row761b_deep.py",
        "tests/test_row761_astra_acceptance.py",
        "tests/test_row701_quality_scan_scopes_to_the_requested_scene.py",
        "tests/test_row759_modal_trigger_click_reenters.py",
        "tests/test_row825_kafka_event_streamer.py",
        "tests/test_row975_aiokafka_client.py",
        "tests/test_row992_high_resolution_socket_i_o_accounting_microsecond_latency_tracker.py",
        "tests/test_row994_dynamic_query_plan_lock_contention_profiler_for_sqlite.py",
        "tests/test_row978_schema_migration.py",
        "tests/test_row362_templates_are_resolvable.py",
        "tests/test_row670_verifier_carries_a_session.py",
        "tests/test_row671_reviewed_template_selectors_are_enumerated.py",
        "tests/test_row377_installed_template_selftest_states.py",
        "tests/test_login_session_does_not_cover_the_scene_host.py",
        "tests/test_row663_inspect_rung_matches_runner.py",
        "tests/test_row666_candidates_inspect_prefers_caller_url.py",
        "tests/test_row_pm_template_gap_matchers_and_corpus.py",
        "tests/test_row672_reviewed_template_is_reachable.py",
        "tests/test_row673_reviewed_probe_adapter_ships_once.py",
        "tests/test_row674_live_state_round_trips.py",
        "tests/test_v3_43_54_resolution.py",
        "tests/test_row821_template_tiers_are_opt_in.py",
        "tests/test_frame_hierarchy.py",
        "tests/test_infinite_scroll_pager.py",
    )),
)

# Runtime-balanced from run 35763678888 (per-file estimate = (shard seconds - 70 s
# setup) / files in shard). Estimated serial pytest seconds per glob shard at the
# cut: rows-a 323, rows-b 295, v3-a 345, v3-b 340, named-a 410, named-b 245
# (pinned: mutation-tools-b 433, parity-graph 412, mutation-tools 388).
GLOBS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gates-rows-a", ("tests/test_row[0-5]*.py", "tests/test_row[89]*.py")),
    ("gates-rows-b", ("tests/test_row[67]*.py",)),
    ("gates-v3-a", ("tests/test_v3_66_1[01]*.py",)),
    ("gates-v3-b", ("tests/test_v3_*.py",)),
    ("gates-named-a", ("tests/test_[a-q]*.py",)),
    ("gates-named-b", ("tests/test_*.py",)),
)


def names() -> list[str]:
    return [name for name, _ in PINNED] + [name for name, _ in GLOBS]


def shards(repo: Path) -> dict[str, list[str]]:
    """{shard: [paths]} -- a partition of population(repo), or ShardError."""
    pop = population(repo)
    out: dict[str, list[str]] = {}
    taken: set[str] = set()
    for name, members in PINNED:
        if name in out:
            raise ShardError(f"duplicate shard name {name!r}")
        undeclared = sorted(m for m in members if m not in pop)
        if undeclared:
            raise ShardError(
                f"shard {name!r} pins file(s) outside the declared gate "
                f"population (deleted, undeclared or moved to PROCESS_TESTS): {undeclared}")
        twice = sorted(m for m in members if m in taken)
        if twice:
            raise ShardError(f"shard {name!r} pins file(s) already pinned: {twice}")
        if len(set(members)) != len(members):
            raise ShardError(f"shard {name!r} lists a file twice")
        out[name] = list(members)
        taken.update(members)
    rest = sorted(pop - taken)
    for name, patterns in GLOBS:
        if name in out:
            raise ShardError(f"duplicate shard name {name!r}")
        got = [f for f in rest if any(fnmatch.fnmatchcase(f, p) for p in patterns)]
        if not got:
            raise ShardError(
                f"glob shard {name!r} {patterns} matches nothing; pytest with no "
                "paths would run the whole tree")
        out[name] = got
        rest = [f for f in rest if f not in got]
    if rest:
        raise ShardError(f"declared gate(s) matched by no shard: {rest}")
    return out


def _main(argv: list[str]) -> int:
    repo = Path(__file__).resolve().parent.parent
    if len(argv) >= 3 and argv[1] == "--repo":
        repo = Path(argv[2]).resolve()
        argv = argv[:1] + argv[3:]
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        if cmd == "names":
            print("\n".join(names()))
        elif cmd == "files" and len(argv) == 3:
            table = shards(repo)
            if argv[2] not in table:
                print(f"ci_shards: no shard named {argv[2]!r}", file=sys.stderr)
                return 3
            files = table[argv[2]]
            if not files:
                return 3
            print(" ".join(files))
        elif cmd == "check":
            table = shards(repo)
            print(f"ci_shards: {sum(len(v) for v in table.values())} declared gates "
                  f"in {len(table)} shards, each exactly once")
        elif cmd == "report":
            for name, files in shards(repo).items():
                print(f"{name}\t{len(files)}")
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except ShardError as exc:
        print(f"ci_shards: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
