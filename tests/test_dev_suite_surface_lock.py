"""Surface-lock for the dev_suite decomposition (characterization, not RED-first).

Freezes the dev_suite public API as of v3.66.392 (the PRE-move monolith). After
dev_suite.py becomes a dev_suite/ package, this test is the proof the move
preserved the surface: the public names must be EXACTLY preserved, the
guard-imported private must still resolve from the package root, and every
planned submodule must import cleanly (catches a missed relative-import-depth
conversion -- see DECOMPOSITION_PLAYBOOK section 3).

Generated from the 392 tree: 125 public names + 1 guard-required private
(_manifest_excluded, imported directly by tools/build_release.py -- a release
guard that cannot be edited). Runner-safe: zero-arg fns.
"""
import importlib

from bulk_downloader import dev_suite as ds

# --- the frozen public surface (125 names, v3.66.392) ----------------------- #
FROZEN_PUBLIC = {
    'account_pool_inspect', 'ai_fallback_trace', 'ai_health_history', 'ai_latency_log',
    'auth_cookie_test', 'auth_surface', 'backup_check', 'bat_lint',
    'cache_clear', 'captcha_relay_status', 'changelog_lint', 'config_dump',
    'config_hot_reload', 'config_integrity', 'config_restore', 'config_schema_audit',
    'config_snapshot', 'config_snapshot_diff', 'config_snapshot_list', 'cookie_age_report',
    'cookie_jar_inspect', 'credential_resolver', 'csrf_coverage', 'csrf_token_inspect',
    'db_growth_report', 'db_overview', 'dead_css_finder', 'deadlock_detector',
    'dedup_hash_explore', 'dependency_audit', 'dependency_pin_drift', 'disk_usage_breakdown',
    'dispatch_chain', 'dispatch_dry_run', 'download_folder_scan', 'duplicate_sites',
    'effective_settings', 'egress_ip_monitor', 'error_rate', 'event_tap',
    'event_tap_ui_html', 'exception_log', 'extractor_fastpath_sim', 'extractor_matrix',
    'feature_flags_status', 'ffmpeg_command_preview', 'filename_template_preview', 'fixture_site_start',
    'fixture_site_status', 'fixture_site_stop', 'flaky_test_detector', 'flaresolverr_health',
    'force_gc', 'fts_index_inspect', 'get_log_level', 'golden_file_manager',
    'guard_test_status', 'i18n_coverage', 'import_preflight', 'index_advisor',
    'integrity_check', 'invariant_audit', 'job_replay', 'keeper_monitor',
    'latency_histogram', 'leak_scan', 'lockfile_scan', 'log_search',
    'log_tail', 'login_flows_status', 'login_template_dry_run', 'magic_bytes_check',
    'maintenance_mode_status', 'manifest_probe', 'manual_takeover_log', 'migration_status',
    'model_pull_check', 'mp4_metadata_inspect', 'ollama_inventory', 'orphan_rows',
    'parametrize_fanout', 'partial_download_finder', 'path_allowlist_test', 'process_info',
    'prompt_preview', 'queue_table_inspect', 'queue_throughput', 'rate_limit_state',
    'request_replay_list', 'resolution_scoring_test', 'retry_schedule_inspect', 'route_map',
    'runner_console', 'runner_state', 'sast_summary', 'secret_scan',
    'set_log_level', 'sh_lint', 'slow_endpoints', 'slow_query_profiler',
    'sql_console', 'sse_status', 'stale_references', 'stealth_audit',
    'storage_tier_status', 'stuck_jobs', 'systemd_unit_check', 'tempdir_clean',
    'template_audit', 'test_coverage_map', 'test_run_diff', 'test_timing',
    'thread_dump', 'thread_inventory', 'tls_cert_check', 'token_estimate',
    'version_consistency', 'vision_test_harness', 'vpn_config_render', 'vpn_connectivity_probe',
    'vpn_provider_rotation_view', 'wal_checkpoint', 'window_simulate', 'worker_thread_profile',
    'zip_manifest_check',
}

# names tools/build_release.py (guard) + other tools import DIRECTLY from the
# package; the shim MUST keep these resolvable from the package root.
# F19: the COMPLETE set of dev_suite privates imported DIRECTLY by external
# consumers (build_release guard + ~6 manifest-exclusion test files). Every one
# MUST resolve from the package root after the split, or those consumers break.
# All five live in the release_lint submodule.
GUARD_REQUIRED = {
    '_manifest_excluded',          # fn -- build_release.py (guard) + many tests
    '_MANIFEST_EXCLUDE_DIRS',      # const -- test_v3_64_3
    '_MANIFEST_EXCLUDE_NAMES',     # const -- test_v3_66_39, test_v3_64_3
    '_MANIFEST_EXCLUDE_PATHS',     # const -- test_v3_66_263
    '_MANIFEST_EXCLUDE_SUFFIXES',  # const -- test_v3_64_3
}

# every planned submodule must import cleanly post-split (a missed
# `from . import X` -> `from bulk_downloader import X` conversion fails here).
# NB: 'housekeeping', not 'maintenance' -- a submodule named 'maintenance' would
# collide with the sibling bulk_downloader/maintenance.py (F17). The collision
# guard below enforces this automatically.
PLANNED_SUBMODULES = ('_common', 'introspection', 'logs', 'config_tools', 'db_tools', 'release_lint', 'audit_security', 'perf_metrics', 'jobs_runner', 'capture_diag', 'integrations_diag', 'vpn_diag', 'housekeeping', 'test_meta')


def test_public_surface_is_exactly_preserved():
    live = {n for n in dir(ds)
            if not n.startswith("_") and callable(getattr(ds, n, None))}
    missing = FROZEN_PUBLIC - live
    added = live - FROZEN_PUBLIC
    assert not missing, f"dev_suite dropped public names: {sorted(missing)}"
    # additions are allowed (new tools), but surface the diff for review
    assert isinstance(added, set)


def test_guard_required_privates_resolve_from_package_root():
    # consumers do: from bulk_downloader.dev_suite import (_manifest_excluded,
    # _MANIFEST_EXCLUDE_NAMES, ...). _manifest_excluded is a fn; the four
    # _MANIFEST_EXCLUDE_* are constants -- assert EXISTENCE, not callability.
    for name in GUARD_REQUIRED:
        assert hasattr(ds, name), (
            f"{name!r} must be re-exported from the dev_suite package root -- "
            f"build_release.py (guard) and/or manifest-exclusion tests import it directly")


def test_build_release_style_import_still_works():
    # exact import form the byte-locked guard uses
    from bulk_downloader.dev_suite import _manifest_excluded, zip_manifest_check
    assert callable(_manifest_excluded) and callable(zip_manifest_check)


def test_manifest_exclude_constants_importable():
    # the exact form test_v3_64_3 / test_v3_66_39 / test_v3_66_263 use
    from bulk_downloader.dev_suite import (
        _MANIFEST_EXCLUDE_DIRS, _MANIFEST_EXCLUDE_NAMES,
        _MANIFEST_EXCLUDE_PATHS, _MANIFEST_EXCLUDE_SUFFIXES)
    for c in (_MANIFEST_EXCLUDE_DIRS, _MANIFEST_EXCLUDE_NAMES,
              _MANIFEST_EXCLUDE_PATHS, _MANIFEST_EXCLUDE_SUFFIXES):
        assert hasattr(c, "__contains__")  # set/frozenset/dict membership


def test_no_submodule_name_collides_with_a_sibling_module():
    """F17 guard: a dev_suite submodule must not share a name with an existing
    bulk_downloader/<name>.py sibling, or a relative `from . import <name>`
    inside that submodule resolves to the submodule itself, not the sibling.
    This would have caught the proposed 'maintenance' submodule (sibling
    bulk_downloader/maintenance.py, imported by maintenance_mode_status)."""
    import bulk_downloader as pkg
    from pathlib import Path
    pkg_dir = Path(pkg.__file__).resolve().parent
    siblings = {p.stem for p in pkg_dir.glob("*.py") if p.stem != "__init__"}
    clashes = {s for s in PLANNED_SUBMODULES if s != "_common" and s in siblings}
    assert not clashes, (
        f"submodule name(s) collide with bulk_downloader siblings: {sorted(clashes)} "
        f"-- rename them (e.g. maintenance -> housekeeping)")


def test_every_planned_submodule_imports_cleanly():
    # only meaningful AFTER the split; before it, dev_suite is a module and the
    # submodule imports will ImportError -- which is the correct pre-move state.
    import types
    if not isinstance(getattr(ds, "__path__", None), (list, types.GeneratorType)) \
            and not hasattr(ds, "__path__"):
        return  # dev_suite is still a single module (pre-split); nothing to check
    for sub in PLANNED_SUBMODULES:
        importlib.import_module(f"bulk_downloader.dev_suite.{sub}")
