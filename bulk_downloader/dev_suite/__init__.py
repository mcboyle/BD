"""Dev suite — in-app inspection / introspection tools.

Dev-only, gated like the rest of /api/dev/* via dev_tools.is_dev_mode().
This is the broad operator-facing dev surface; it complements:
  • dev_tools.py — the in-GUI test runner
  • perf_lab.py  — memory audit + load injector
  • bd-doctor / /api/diagnostics_bundle — the full environment report

Everything in this module is strictly READ-ONLY — it inspects state,
never changes it. State-changing maintenance tools live elsewhere.
Nothing here runs at import time.
"""
# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.

from ._common import (  # noqa: F401
    _repo_root, _percentile, _redact, _collect_cred_refs, _human_secs, _read_version, _iter_route_sources, _dev_mode, _proc_uptime_seconds, _SECRET_KEY_HINTS, _BD_ENV_VARS, _resolve_site_config, _resolve_all_site_configs,
)
from .introspection import (  # noqa: F401
    route_map, thread_inventory, db_overview, runner_state, log_tail, process_info, invariant_audit, template_audit, force_gc,
)
from .logs import (  # noqa: F401
    set_log_level, get_log_level, log_search, event_tap, event_tap_ui_html, sse_status, _EVENT_TAP_UI_HTML,
)
from .config_tools import (  # noqa: F401
    effective_settings, config_dump, config_integrity, config_schema_audit, config_hot_reload, config_snapshot, config_snapshot_list, config_restore, config_snapshot_diff, _scalar_type_mismatches, _snapshot_dir, _SNAPSHOT_DIRNAME, _SNAPSHOT_NAME_RE,
)
from .db_tools import (  # noqa: F401
    wal_checkpoint, sql_console, integrity_check, backup_check, stuck_jobs, duplicate_sites, orphan_rows, stale_references, slow_query_profiler, index_advisor, queue_table_inspect, fts_index_inspect, db_growth_report, queue_throughput, retry_schedule_inspect, worker_thread_profile, account_pool_inspect, migration_status, _norm_ref, _explain_query_plan, _history_day_series, _parse_schedule_str, _mask_username, _ADVISOR_TABLES, _DRIFT_DB_TABLES, _HOT_QUERIES, _QUEUE_EXPECTED_COLUMNS, _SQL_FORBIDDEN,
)
from .release_lint import (  # noqa: F401
    version_consistency, changelog_lint, bat_lint, sh_lint, zip_manifest_check, systemd_unit_check, dependency_pin_drift, _manifest_excluded, _manifest_required_missing, _find_systemd_unit, _parse_unit, _MANIFEST_EXCLUDE_DIRS, _MANIFEST_EXCLUDE_NAMES, _MANIFEST_EXCLUDE_PATHS, _MANIFEST_EXCLUDE_SUFFIXES, _MANIFEST_REQUIRED_PRESENT,
)
from .audit_security import (  # noqa: F401
    csrf_coverage, auth_surface, dispatch_chain, dispatch_dry_run, import_preflight, dependency_audit, secret_scan, path_allowlist_test, sast_summary, manual_takeover_log, csrf_token_inspect, tls_cert_check, _before_request_hook_names, _read_process_one_source, _verify_chain_against_source, _branch_enabled, _import_preflight_impl, _classify_takeover_line, _extract_hosts_from_site_configs, _san_matches, _DISPATCH_CHAIN, _REQ_FILES, _SECRET_PATTERNS, _SECRET_SKIP_DIRS, _STATE_METHODS, _TAKEOVER_KEYWORDS, _TAKEOVER_PHASES,
)
from .perf_metrics import (  # noqa: F401
    leak_scan, rate_limit_state, latency_histogram, slow_endpoints, error_rate, exception_log, thread_dump, deadlock_detector, _IDLE_WAIT_HINTS, route_timing,
)
from .jobs_runner import (  # noqa: F401
    keeper_monitor, runner_console, job_replay, window_simulate,
)
from .capture_diag import (  # noqa: F401
    cookie_jar_inspect, cookie_age_report, auth_cookie_test, login_template_dry_run, credential_resolver, extractor_matrix, extractor_fastpath_sim, ffmpeg_command_preview, resolution_scoring_test, manifest_probe, magic_bytes_check, mp4_metadata_inspect, dedup_hash_explore, partial_download_finder, filename_template_preview, flaresolverr_health, captcha_relay_status, stealth_audit, request_replay_list, login_flows_status, _iter_site_jars, _cookie_expiry_label, _classify_credential, _fetch_manifest_text, _parse_hls_attrs, _probe_hls, _probe_dash, _detect_magic, _resolve_path_against_allowlist, _walk_mp4_atoms, _LABEL_TO_EXTS, _MAGIC_SIGNATURES, _MANIFEST_FETCH_CAP,
)
from .integrations_diag import (  # noqa: F401
    ollama_inventory, prompt_preview, ai_fallback_trace, ai_latency_log, ai_health_history, vision_test_harness, token_estimate, model_pull_check, _estimate_tokens, _PROMPT_REGISTRY, _TEST_PNG_B64, _AI_FALLBACK_STAGES, _TEST_VISION_PROMPT,
)
from .vpn_diag import (  # noqa: F401
    vpn_config_render, vpn_provider_rotation_view, vpn_connectivity_probe, egress_ip_monitor,
)
from .housekeeping import (  # noqa: F401
    lockfile_scan, tempdir_clean, cache_clear, dead_css_finder, storage_tier_status, maintenance_mode_status, i18n_coverage, feature_flags_status, disk_usage_breakdown, download_folder_scan, _filesystem_audit, _scan_bd_temp_artifacts, _extract_css_selectors, _BD_TEMP_PREFIXES, _BD_VPN_DIRNAMES, _TEMPDIR_MIN_AGE_S,
)
from .test_meta import (  # noqa: F401
    guard_test_status, test_coverage_map, test_run_diff, parametrize_fanout, flaky_test_detector, fixture_site_start, fixture_site_stop, fixture_site_status, golden_file_manager, test_timing, _is_guard_test_file, _load_test_results, _count_parametrize_cases, _load_fixture_app, _goldens_dir, _FIXTURE_SERVERS, _FIXTURE_DEFS, _GUARD_TEST_MARKERS,
)

__all__ = [
    'account_pool_inspect',
    'ai_fallback_trace',
    'ai_health_history',
    'ai_latency_log',
    'auth_cookie_test',
    'auth_surface',
    'backup_check',
    'bat_lint',
    'cache_clear',
    'captcha_relay_status',
    'changelog_lint',
    'config_dump',
    'config_hot_reload',
    'config_integrity',
    'config_restore',
    'config_schema_audit',
    'config_snapshot',
    'config_snapshot_diff',
    'config_snapshot_list',
    'cookie_age_report',
    'cookie_jar_inspect',
    'credential_resolver',
    'csrf_coverage',
    'csrf_token_inspect',
    'db_growth_report',
    'db_overview',
    'dead_css_finder',
    'deadlock_detector',
    'dedup_hash_explore',
    'dependency_audit',
    'dependency_pin_drift',
    'disk_usage_breakdown',
    'dispatch_chain',
    'dispatch_dry_run',
    'download_folder_scan',
    'duplicate_sites',
    'effective_settings',
    'egress_ip_monitor',
    'error_rate',
    'event_tap',
    'event_tap_ui_html',
    'exception_log',
    'extractor_fastpath_sim',
    'extractor_matrix',
    'feature_flags_status',
    'ffmpeg_command_preview',
    'filename_template_preview',
    'fixture_site_start',
    'fixture_site_status',
    'fixture_site_stop',
    'flaky_test_detector',
    'flaresolverr_health',
    'force_gc',
    'fts_index_inspect',
    'get_log_level',
    'golden_file_manager',
    'guard_test_status',
    'i18n_coverage',
    'import_preflight',
    'index_advisor',
    'integrity_check',
    'invariant_audit',
    'job_replay',
    'keeper_monitor',
    'latency_histogram',
    'leak_scan',
    'lockfile_scan',
    'log_search',
    'log_tail',
    'login_flows_status',
    'login_template_dry_run',
    'magic_bytes_check',
    'maintenance_mode_status',
    'manifest_probe',
    'manual_takeover_log',
    'migration_status',
    'model_pull_check',
    'mp4_metadata_inspect',
    'ollama_inventory',
    'orphan_rows',
    'parametrize_fanout',
    'partial_download_finder',
    'path_allowlist_test',
    'process_info',
    'prompt_preview',
    'queue_table_inspect',
    'queue_throughput',
    'rate_limit_state',
    'request_replay_list',
    'resolution_scoring_test',
    'retry_schedule_inspect',
    'route_map',
    'runner_console',
    'runner_state',
    'sast_summary',
    'secret_scan',
    'set_log_level',
    'sh_lint',
    'slow_endpoints',
    'route_timing',
    'slow_query_profiler',
    'sql_console',
    'sse_status',
    'stale_references',
    'stealth_audit',
    'storage_tier_status',
    'stuck_jobs',
    'systemd_unit_check',
    'tempdir_clean',
    'template_audit',
    'test_coverage_map',
    'test_run_diff',
    'test_timing',
    'thread_dump',
    'thread_inventory',
    'tls_cert_check',
    'token_estimate',
    'version_consistency',
    'vision_test_harness',
    'vpn_config_render',
    'vpn_connectivity_probe',
    'vpn_provider_rotation_view',
    'wal_checkpoint',
    'window_simulate',
    'worker_thread_profile',
    'zip_manifest_check',
]
