# DEVSUITE SECTION MAP — the executable assignment manifest (v3.66.392)

Every dev_suite section → its target submodule, with the exact sibling imports (apply the F7 absolute
transform), `_common` helpers, and guard/private constraints. This is the blueprint that makes the move
mechanical: build each submodule from the rows below. Pairs with the plan, forensics (F1–F21), playbook,
and pass-log. **82 sections, 125 public fns, 49 private, 14 submodules** (incl `_common`).

Legend: **pub** = public fns (re-exported by `__init__`); **priv** = section-local privates (move with the
section); **sib** = siblings to import absolutely & lazily (`from bulk_downloader import X` — F7); **C** =
`_common` helpers used.

---

## `_common.py` — shared leaf (imported by submodules; imports nothing from them)

The ONE depth-correct `_repo_root()` / `_pkg_dir()` (F1 — must use `.parents[2]` from its new location),
plus every helper/constant used by ≥2 submodules:

- **fns:** `_repo_root` (9 callers), `_percentile` (8), `_redact` (5), `_collect_cred_refs` (5),
  `_human_secs` (4), `_read_version` (3), `_iter_route_sources` (3), `_dev_mode` (2),
  `_proc_uptime_seconds` (2)
- **constants (F15):** `_SECRET_KEY_HINTS`, `_BD_ENV_VARS`

---

## `introspection.py` — core read-only state inspectors
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 1 | `route_map` | | | |
| 2 | `thread_inventory` | | | |
| 3 | `db_overview` | | | |
| 4 | `runner_state` | | | |
| 5 | `log_tail` | | | |
| 6 | `effective_settings` | | | |
| 7 | `config_dump` | | | (redacts → uses `_redact`/`_SECRET_KEY_HINTS` from C) |
| 8 | `process_info` | | | `_proc_uptime_seconds` |
| 9 | `invariant_audit` | | | (carries `# INV-` tags — F4) |
| 10 | `template_audit` | | login_templates_data | |
| 13 | `force_gc` | | | |

## `logs.py` — logging + SSE/event surface
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 15a | `set_log_level`, `get_log_level` | | log | `_repo_root`,`_read_version` |
| 25 | `log_search` | | | |
| 41 | `event_tap`, `event_tap_ui_html` | | dev_events | |
| 15b | `sse_status` | | sse_broker | |

> **§15 is a cross-domain bundle** (L525–631): split it — log-level + sse here, `sql_console` → `db_tools`.

## `db_tools.py` — database inspection
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 14 | `wal_checkpoint` | | constants, db | |
| 15c | `sql_console` | | db | (from the §15 split) |
| 23 | `integrity_check` | | db | |
| 24 | `backup_check` | | backup_verify | |
| 26 | `stuck_jobs` | | db | |
| 35 | `duplicate_sites`, `orphan_rows`, `stale_references` | `_norm_ref` | db, login_templates_data, secrets_store | `_collect_cred_refs` |
| 42 | `slow_query_profiler`, `index_advisor` | `_explain_query_plan` | db | |
| 53 | `queue_table_inspect`, `fts_index_inspect` | | constants, db | |
| 54 | `db_growth_report`, `queue_throughput` | `_history_day_series` | constants, db | |
| 55 | `retry_schedule_inspect`, `worker_thread_profile`, `account_pool_inspect` | `_parse_schedule_str`,`_mask_username` | db | `_human_secs` |

## `release_lint.py` — release/build linting  **(holds the F19 guard set)**
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 17 | `version_consistency` | | | `_repo_root`,`_read_version` |
| 18 | `changelog_lint` | | | `_repo_root`,`_read_version` |
| 19 | `bat_lint` | | _bat_lint | `_repo_root` |
| 20 | `sh_lint` | | | `_repo_root` |
| 21 | `zip_manifest_check` | `_manifest_excluded`, `_manifest_required_missing` | | `_repo_root` |
| 50 | `systemd_unit_check`, `dependency_pin_drift` | `_find_systemd_unit`,`_parse_unit` | | (F1 site L4852) |

> **F2/F19:** `__init__` must export from here: `zip_manifest_check`, `_manifest_excluded`,
> `_MANIFEST_EXCLUDE_DIRS/NAMES/PATHS/SUFFIXES`. The byte-locked build_release guard + ~6 tests import these directly.

## `audit_security.py` — security & config audit
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 12 | `config_integrity` | `_collect_cred_refs`→C | | `_collect_cred_refs` |
| 22 | `csrf_coverage`, `auth_surface` | `_before_request_hook_names` | | `_iter_route_sources` |
| U5 | `dispatch_chain`, `dispatch_dry_run` | `_read_process_one_source`,`_verify_chain_against_source`,`_resolve_site_config`,`_branch_enabled` | extractors, jd_bridge, qb_bridge | (F1 site L1817 `with_name("runner.py")`) |
| 34 | `config_schema_audit`, `import_preflight` | `_resolve_all_site_configs`,`_scalar_type_mismatches`,`_import_preflight_impl` | capture_artifact_redact, csv_bulk, site_editor | |
| 46 | `dependency_audit`, `secret_scan`, `path_allowlist_test`, `sast_summary` | | app | (F1 sites L4075/4184) |
| 56 | `manual_takeover_log`, `csrf_token_inspect` | `_classify_takeover_line` | | |
| T43 | `tls_cert_check` | `_extract_hosts_from_site_configs`,`_san_matches` | | |

## `perf_metrics.py` — performance & runtime metrics
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 11 | `leak_scan` | | perf_lab | |
| 27 | `rate_limit_state` | | | |
| 30 | `latency_histogram` | | dev_metrics | `_percentile` |
| 31 | `slow_endpoints` | | dev_metrics | |
| 32 | `error_rate` | | dev_metrics | |
| 33 | `exception_log` | | dev_metrics | |
| 34t | `thread_dump` | | | |
| 35d | `deadlock_detector` | | | |

## `jobs_runner.py` — runner/job/scheduling
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 28 | `keeper_monitor` | | session_keeper | |
| 49 | `runner_console`, `job_replay` | | app, batch_ops | |
| T41 | `window_simulate` | | download_window | |

## `capture_diag.py` — capture/download diagnostics
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 36 | `cookie_jar_inspect`, `cookie_age_report`, `auth_cookie_test` | `_iter_site_jars`,`_cookie_expiry_label` | cookies, login | |
| 37 | `login_template_dry_run`, `credential_resolver` | `_classify_credential` | learn, login_templates_data, secrets_store | |
| 38 | `extractor_matrix`, `extractor_fastpath_sim` | | extractors | |
| 39 | `ffmpeg_command_preview`, `resolution_scoring_test` | | detect, heuristic_scoring, hls_downloader | |
| 40 | `manifest_probe` | `_fetch_manifest_text`,`_parse_hls_attrs`,`_probe_hls`,`_probe_dash` | | |
| 60 | `magic_bytes_check`, `mp4_metadata_inspect` | `_detect_magic`,`_resolve_path_against_allowlist`,`_walk_mp4_atoms` | app | |
| 61 | `dedup_hash_explore`, `partial_download_finder` | | app, dedup | |
| 62 | `filename_template_preview` | | fname | |
| 65 | `flaresolverr_health`, `captcha_relay_status` | | app, captcha_relay, flaresolverr_client | |
| 66 | `stealth_audit` | | constants, stealth | |
| T44 | `request_replay_list` | | request_replay | |
| T45 | `login_flows_status` | | login_flow_recorder | |

## `integrations_diag.py` — AI/integration diagnostics
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 29 | `ollama_inventory` | `_percentile`→C | aiassist | `_percentile` |
| 57 | `prompt_preview`, `ai_fallback_trace` | | aiassist (+ `_PROMPT_REGISTRY` strings — F18) | |
| 58 | `ai_latency_log`, `ai_health_history` | | aiassist | `_percentile` |
| 59 | `vision_test_harness` | (`_TEST_PNG_B64` const) | aiassist | |
| T37 | `token_estimate` | `_estimate_tokens` | app | |
| T39 | `model_pull_check` | | ai_provider, app | |

## `vpn_diag.py` — VPN
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 63 | `vpn_config_render`, `vpn_provider_rotation_view` | | vpn, vpn_config | |
| 64 | `vpn_connectivity_probe`, `egress_ip_monitor` | | vpn | |

## `housekeeping.py` — maintenance/config-write/feature-toggle  **(renamed from `maintenance` — F17)**
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 44 | `lockfile_scan`, `tempdir_clean` | `_scan_bd_temp_artifacts` | | |
| 47 | `config_hot_reload`, `cache_clear` | | app, hls_downloader | |
| 48 | `config_snapshot`, `config_snapshot_list`, `config_restore`, `config_snapshot_diff` | `_snapshot_dir` | app | `_SECRET_KEY_HINTS` |
| T34 | `dead_css_finder` | `_extract_css_selectors` | | `_repo_root` |
| T35 | `storage_tier_status` | | storage_tier | |
| T36 | `maintenance_mode_status` | | **maintenance** (sibling — F17, why this submodule is NOT named `maintenance`) | |
| T38 | `i18n_coverage` | | i18n | `_repo_root` |
| T40 | `feature_flags_status` | | feature_flags | |

## `test_meta.py` — test infrastructure  **(holds `_FIXTURE_SERVERS` — F5)**
| § | pub | priv | sib | C |
|---|---|---|---|---|
| 45 | `guard_test_status`, `test_coverage_map`, `test_run_diff` | `_is_guard_test_file`,`_load_test_results` | dev_tools | (F1 site L3859) |
| 51 | `parametrize_fanout`, `flaky_test_detector` | `_count_parametrize_cases` | | |
| 52 | `fixture_site_start`, `fixture_site_stop`, `fixture_site_status` | `_load_fixture_app`, **`_FIXTURE_SERVERS`** | | (F1+F11 sites L5116–5127 — dynamic file-path load) |
| T42 | `golden_file_manager` | `_goldens_dir` | | `_repo_root` |
| 68 | `test_timing` | | | |

---

## Cross-cutting constraints (carried from forensics)

- **`_common` depth (F1):** `_repo_root`/`_pkg_dir` corrected for the deeper location; route ALL 8
  `__file__` code sites through it (L1817, L3859, L4075, L4184, L4852, L5119, + L623, L4012). Plus fix the
  external `test_u27_security_cluster:54` `ds.__file__` read (F20). (A 9th `__file__` mention at L616 is a
  COMMENT, not a site — verified.)
- **F7 transform:** every `sib` cell → `from bulk_downloader import X` (absolute, lazy).
- **`__init__.py` exports:** the 125 public fns ∪ {`zip_manifest_check`} ∪ the 5-name F19 private set. No
  `import *`. `__all__` = that union.
- **Co-location invariants:** `_FIXTURE_SERVERS` touchers all in `test_meta` (F5 ✓); the F19 constants with
  §21 in `release_lint`. **INV tags: dev_suite carries 5 (001,002,004,005,006), not 11** — preserve all 5
  (max-verify correction; the original "11" was a tree-wide misattribution; tree-wide floor = 6 distinct).
- **Band:** the 89-file dev_suite test family + the new surface-lock (6/6) + `test_function_index_in_sync` +
  `test_dependency_graph_in_sync`, from the extracted zip. Only **DEPENDENCY_GRAPH** regenerates.
