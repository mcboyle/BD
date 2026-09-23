"""Row 1020 -- the gateway's route-module registry.

``bulk_downloader/app.py`` used to wire every blueprint by hand: 152 try/except blocks, 161
``register_routes(app)`` calls, spread over 1,200 lines of an 8,217-line module. Two things were
impossible while that was the only wiring:

* nothing could be *asked* which route modules exist -- the set only existed as source text;
* every block swallowed its own failure into a stderr line, so a blueprint that stopped
  registering left a running app with missing routes and no data anywhere saying so.

This module is the answer to both. :data:`ROUTE_MODULE_ORDER` is the set, pinned as data and
checked against the filesystem by :func:`discover_route_modules`; :func:`register_all` registers
a run of modules and *returns* the per-module outcome instead of printing it.

Scope, stated because it is deliberately not everything: the gateway still hand-writes eight
registrations that are not the uniform shape -- they register a second module inside the same
``try`` (``app_auth`` + ``app_oidc``, ``app_tags`` + ``app_tool_bridge``, ``app_ytdlp_status`` +
the two gallery-dl modules) or carry different stderr text. Collapsing those would change what
happens when the *first* module of a pair fails, so they stay where they are and this module
names them in :data:`HAND_REGISTERED` rather than pretending they do not exist.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

#: Every ``bulk_downloader/app_*.py`` that exposes a top-level ``register_routes``. This is the
#: answer to "which blueprint modules exist"; MEASURED at base: 161, exactly the number of
#: ``register_routes(app)`` calls app.py made by hand.
ROUTE_MODULE_ORDER = (
    'app_a11y', 'app_account_pool', 'app_accounts', 'app_activity', 'app_ai', 'app_alerts',
    'app_analyzer', 'app_api_tokens', 'app_apple', 'app_audit', 'app_auth', 'app_auth_health',
    'app_automation_status', 'app_backup', 'app_batch', 'app_bg', 'app_bitrot', 'app_budget',
    'app_bulk', 'app_bw_chart', 'app_capacity', 'app_captcha_relay', 'app_captures',
    'app_changelog', 'app_circuit', 'app_cleanup', 'app_cluster_rate', 'app_cockpit_home',
    'app_community_scrapers', 'app_concurrent', 'app_config', 'app_cookie_clipboard',
    'app_cookie_quality', 'app_cookie_relogin', 'app_cost', 'app_crash_recovery', 'app_csrf',
    'app_daily_budget', 'app_dashboard', 'app_data_layer', 'app_dedup', 'app_deploy', 'app_dev',
    'app_diagnostics', 'app_diagnostics_bundle', 'app_discovery', 'app_doctor',
    'app_download_hold', 'app_edge_deploy', 'app_envfile_editor', 'app_eol', 'app_events_all',
    'app_export', 'app_extension', 'app_fed', 'app_file', 'app_fixtures', 'app_flaresolverr',
    'app_gallerydl_status', 'app_gallerydl_update', 'app_gamification', 'app_global_config',
    'app_health', 'app_history', 'app_hourly_stats', 'app_i18n', 'app_import', 'app_integrations',
    'app_interop', 'app_jobs', 'app_jsonapi', 'app_knowledge', 'app_library', 'app_live_recorder',
    'app_login_templates', 'app_logs', 'app_macros', 'app_marketplace', 'app_multi_conn',
    'app_notify', 'app_oidc', 'app_openapi', 'app_pair', 'app_palette', 'app_pause_all',
    'app_phoenix', 'app_playground', 'app_plex', 'app_plugins', 'app_provenance', 'app_push',
    'app_queue', 'app_queue_templates', 'app_quick_add', 'app_ramdisk', 'app_rate_limit',
    'app_rebalance', 'app_recommendations', 'app_replication', 'app_report_center',
    'app_resume_all', 'app_retention', 'app_retry_policy', 'app_rights', 'app_route_preview',
    'app_route_urls', 'app_runners', 'app_runs', 'app_saved_searches', 'app_scene_score',
    'app_scheduled_exports', 'app_schedules', 'app_scrape_listing', 'app_scrapling', 'app_search',
    'app_secrets', 'app_selector_drift', 'app_selftest', 'app_semantic_search',
    'app_session_history', 'app_session_status', 'app_settings_center', 'app_shares',
    'app_shortcuts', 'app_sites', 'app_sites_list', 'app_sse_status', 'app_start_all',
    'app_stats', 'app_status', 'app_storage', 'app_storage_rebalance', 'app_store_raw_editor',
    'app_stream', 'app_subtitles', 'app_supervisor', 'app_synthetic_tests', 'app_tags',
    'app_template', 'app_template_manager', 'app_template_manager_ui', 'app_templates', 'app_tg',
    'app_thumbnail_sheets', 'app_thumbnails', 'app_thumbs', 'app_tool_bridge', 'app_tools',
    'app_tpdb', 'app_ui_events', 'app_user_templates', 'app_vpn', 'app_vpn_api', 'app_wakeup',
    'app_wayback', 'app_weather', 'app_webhooks', 'app_widgets_api', 'app_ytdlp_archive',
    'app_ytdlp_status', 'app_ytdlp_update',
)

#: The (module, stderr-label) run this registry registers for the gateway, in the source order the
#: hand-written blocks had. The label is kept per module because 48 of them did not match the
#: module name, and the message text is the only operator-visible trace a failed blueprint leaves.
GATEWAY_ROUTE_MODULES = (
    # v3.43.60: VPN feature routes — 23 endpoints registered as a Flask
    # blueprint in bulk_downloader/app_vpn_api.py. Soft import so app.py
    # still starts if the VPN modules fail to load.
    ('app_vpn_api', 'VPN'),
    # v3.43.60: Dashboard widget picker routes — 5 endpoints.
    ('app_widgets_api', 'Widget'),
    # v3.43.60: Captcha relay routes — 5 endpoints for manual-takeover flow.
    ('app_captcha_relay', 'Captcha relay'),
    ('app_openapi', 'openapi'),
    ('app_tools', 'tools'),
    ('app_sites_list', 'sites_list'),
    ('app_scrape_listing', 'scrape_listing'),
    ('app_runners', 'runners'),
    ('app_jobs', 'jobs'),
    ('app_i18n', 'i18n'),
    ('app_health', 'health'),
    ('app_search', 'search'),
    ('app_subtitles', 'subtitles'),
    ('app_stream', 'stream'),
    ('app_pair', 'pair'),
    ('app_history', 'history'),
    ('app_global_config', 'global_config'),
    ('app_cookie_quality', 'cookie_quality'),
    ('app_activity', 'activity'),
    ('app_ytdlp_archive', 'ytdlp_archive'),
    ('app_thumbnails', 'thumbnails'),
    ('app_tg', 'tg'),
    ('app_supervisor', 'supervisor'),
    ('app_scrapling', 'scrapling'),
    ('app_phoenix', 'phoenix'),
    ('app_multi_conn', 'multi_conn'),
    ('app_flaresolverr', 'flaresolverr'),
    ('app_weather', 'weather'),
    ('app_ui_events', 'ui_events'),
    ('app_tpdb', 'tpdb'),
    ('app_synthetic_tests', 'synthetic_tests'),
    ('app_status', 'status'),
    ('app_session_status', 'session_status'),
    ('app_selftest', 'selftest'),
    ('app_schedules', 'schedules'),
    ('app_scheduled_exports', 'scheduled_exports'),
    ('app_route_urls', 'route_urls'),
    ('app_route_preview', 'route_preview'),
    ('app_retention', 'retention'),
    ('app_rebalance', 'rebalance'),
    ('app_ramdisk', 'ramdisk'),
    ('app_quick_add', 'quick_add'),
    ('app_queue_templates', 'queue_templates'),
    ('app_marketplace', 'marketplace'),
    ('app_integrations', 'integrations'),
    ('app_fixtures', 'fixtures'),
    ('app_extension', 'extension'),
    ('app_events_all', 'events_all'),
    ('app_eol', 'eol'),
    ('app_doctor', 'doctor'),
    ('app_discovery', 'discovery'),
    ('app_diagnostics_bundle', 'diagnostics_bundle'),
    ('app_diagnostics', 'diagnostics'),
    ('app_daily_budget', 'daily_budget'),
    ('app_csrf', 'csrf'),
    ('app_cost', 'cost'),
    ('app_cookie_relogin', 'cookie_relogin'),
    ('app_cookie_clipboard', 'cookie_clipboard'),
    ('app_config', 'config'),
    ('app_concurrent', 'concurrent'),
    ('app_cleanup', 'cleanup'),
    ('app_changelog', 'changelog'),
    ('app_captures', 'captures'),
    ('app_capacity', 'capacity'),
    ('app_bulk', 'bulk'),
    ('app_budget', 'budget'),
    ('app_auth_health', 'auth_health'),
    ('app_alerts', 'alerts'),
    ('app_ytdlp_update', 'ytdlp_update'),
    ('app_wayback', 'wayback'),
    ('app_wakeup', 'wakeup'),
    ('app_vpn', 'vpn'),
    ('app_thumbs', 'thumbs'),
    ('app_thumbnail_sheets', 'thumbnail_sheets'),
    ('app_templates', 'templates'),
    ('app_template_manager', 'template_manager'),
    ('app_storage_rebalance', 'storage_rebalance'),
    ('app_storage', 'storage'),
    ('app_stats', 'stats'),
    ('app_start_all', 'start_all'),
    ('app_sse_status', 'sse_status'),
    ('app_shortcuts', 'shortcuts'),
    ('app_shares', 'shares'),
    ('app_session_history', 'session_history'),
    ('app_selector_drift', 'selector_drift'),
    ('app_scene_score', 'scene_score'),
    ('app_runs', 'runs'),
    ('app_retry_policy', 'retry_policy'),
    ('app_resume_all', 'resume_all'),
    ('app_recommendations', 'recommendations'),
    ('app_rate_limit', 'rate_limit'),
    ('app_provenance', 'provenance'),
    ('app_plugins', 'plugins'),
    ('app_playground', 'playground'),
    ('app_pause_all', 'pause_all'),
    # Row 390: the DURABLE counterpart to pause_all. /api/pause_all is a runtime
    # call that a restart erases; these routes record and lift the hold in
    # app_config.json, where runner.start()/resume() re-apply it after a restart.
    ('app_download_hold', 'download_hold'),
    ('app_palette', 'palette'),
    ('app_logs', 'logs'),
    ('app_login_templates', 'login_templates'),
    ('app_jsonapi', 'jsonapi'),
    ('app_hourly_stats', 'hourly_stats'),
    ('app_gamification', 'gamification'),
    ('app_file', 'file'),
    ('app_export', 'export'),
    ('app_edge_deploy', 'edge_deploy'),
    ('app_deploy', 'deploy'),
    ('app_cluster_rate', 'cluster_rate'),
    ('app_circuit', 'circuit'),
    ('app_bw_chart', 'bw_chart'),
    ('app_bitrot', 'bitrot'),
    ('app_bg', 'bg'),
    ('app_batch', 'batch'),
    ('app_audit', 'audit'),
    ('app_api_tokens', 'api_tokens'),
    ('app_accounts', 'accounts'),
    ('app_account_pool', 'account_pool'),
    ('app_a11y', 'a11y'),
    ('app_plex', 'plex'),
    ('app_ai', 'ai'),
    ('app_dev', 'dev'),
    ('app_sites', 'sites'),
    ('app_library', 'library'),
    ('app_queue', 'queue'),
    ('app_semantic_search', 'semantic-search'),
    ('app_backup', 'backup'),
    # v3.66.635: replication status route -- exposes db_replication.replication_status()
    # (C5 durability signal) that was previously unreachable (module was an island).
    # Read-only GET; the mutating lifecycle controls stay out (separate cut).
    ('app_replication', 'replication'),
    # INTEROP-GOV-1b (v3.66.639): operator surface over the interop_registry keystone.
    ('app_interop', 'interop'),
    ('app_secrets', 'secrets'),
    ('app_dashboard', 'dashboard'),
    ('app_notify', 'notify'),
    ('app_template', 'template'),
    ('app_dedup', 'dedup'),
    ('app_import', 'import'),
    ('app_community_scrapers', 'community_scrapers'),
    ('app_user_templates', 'user_templates'),
    ('app_crash_recovery', 'crash_recovery'),
    ('app_knowledge', 'knowledge'),
    ('app_analyzer', 'analyzer'),
    ('app_fed', 'fed'),
    ('app_push', 'push'),
    ('app_rights', 'rights'),
    ('app_saved_searches', 'saved_searches'),
    ('app_macros', 'macros'),
)

#: Registrations app.py still performs itself; see the module docstring for why.
HAND_REGISTERED = (
    'app_apple', 'app_auth', 'app_automation_status', 'app_cockpit_home', 'app_data_layer',
    'app_envfile_editor', 'app_gallerydl_status', 'app_gallerydl_update', 'app_live_recorder',
    'app_oidc', 'app_report_center', 'app_settings_center', 'app_store_raw_editor', 'app_tags',
    'app_template_manager_ui', 'app_tool_bridge', 'app_webhooks', 'app_ytdlp_status',
)


def discover_route_modules(package_dir):
    """Return the route modules present in *package_dir*, sorted.

    A route module is ``app_<name>.py`` with a module-level ``def register_routes(``. The source
    is read as text rather than imported: discovery must be answerable without executing 161
    modules, and an import-time failure is exactly the condition this registry has to report on.
    """
    found = []
    for path in sorted(Path(package_dir).glob('app_*.py')):
        try:
            src = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for line in src.splitlines():
            if line.startswith('def register_routes(') or line.startswith('def register_routes ('):
                found.append(path.stem)
                break
    return found


def _resolve(modules):
    """Yield ``(name, module_or_None, import_error_or_None)`` for a module spec run."""
    for entry in modules:
        if isinstance(entry, str):
            name, mod = entry, None
        else:
            # A run may be plain names, ``(name, stderr-label)`` -- the pinned form -- or
            # ``(name, module)`` when the caller already holds the module. Only the last carries
            # something to register; a label is a string and must not be mistaken for a module.
            name, second = entry[0], entry[1]
            mod = None if isinstance(second, str) else second
        if mod is not None:
            yield name, mod, None
            continue
        try:
            yield name, importlib.import_module('bulk_downloader.%s' % name), None
        except Exception as err:            # noqa: BLE001 -- an unimportable blueprint is DATA
            yield name, None, err


def register_all(app, modules=None, labels=None, stderr=None):
    """Register *modules* on *app* and return what happened, per module.

    ``modules`` defaults to :data:`GATEWAY_ROUTE_MODULES`; it also accepts plain names or
    ``(name, module)`` pairs so a caller -- or a test -- can drive a set it built itself.

    One module's failure never stops the ones after it: the hand-written blocks had that property
    and losing it would turn a missing optional dependency into a dead app. The difference is that
    the failure is now returned as ``report['failed'][name]`` as well as written to *stderr*, so a
    caller can assert on it. A failure whose stderr line could not be written is also listed in
    ``report['unreported']``.
    """
    if modules is None:
        modules = GATEWAY_ROUTE_MODULES
    label_for = dict(labels or ())
    for entry in GATEWAY_ROUTE_MODULES:
        label_for.setdefault(entry[0], entry[1])
    out = sys.stderr if stderr is None else stderr

    registered, failed, unreported = [], {}, []
    for name, mod, import_error in _resolve(modules):
        err = import_error
        if err is None:
            try:
                mod.register_routes(app)
            except Exception as exc:        # noqa: BLE001 -- see docstring
                err = exc
        if err is None:
            registered.append(name)
            continue
        failed[name] = '%s: %s' % (type(err).__name__, err)
        label = label_for.get(name, name[4:].replace('_', '-') if name.startswith('app_') else name)
        try:
            out.write('[app] %s routes not registered: %s\n' % (label, err))
        except Exception:                   # noqa: BLE001 -- reporting must not become the fault
            # The stream is gone (closed / detached): the line is lost, so the report carries it.
            unreported.append(name)
    return {
        'registered': registered,
        'failed': failed,
        'unreported': unreported,
        'counts': {'registered': len(registered), 'failed': len(failed)},
    }
