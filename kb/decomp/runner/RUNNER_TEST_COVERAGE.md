# RUNNER_TEST_COVERAGE.md

Per-unit test coverage = the canary set each cut runs from the EXTRACTED zip.
Counts = distinct test files NAMING one of the unit's methods (a floor: the worker
path exercises many methods without naming them, so 'uncovered' != untested). Units
with near-zero direct coverage must be validated STRUCTURALLY (import-smoke + the api
snapshot + live operator check), not by sandbox tests.

via tools/runner_contracts.py.

## Coverage by unit (cut order = ascending risk)

| unit | test files | flag |
|---|---|---|
| core | 39 |  |
| queue | 20 |  |
| extractors | 12 |  |
| transport | 11 |  |
| telemetry | 10 |  |
| auth | 9 |  |
| scheduler | 8 |  |
| integrity | 7 |  |
| integrations | 5 |  |
| browser | 4 | low -- lean on snapshot |
| challenge | 3 | low -- lean on snapshot |
| teach | 2 | low -- lean on snapshot |
| accounts | 2 | low -- lean on snapshot |
| manual | 0 | STRUCTURAL-ONLY |

## Methods not directly named in any test (validate via worker-path canary or structurally)

- **accounts**: `_get_active_account`, `_persist_account_state`, `is_rate_limited`, `rl_remaining`
- **auth**: `_check_redirect`, `_handle_auth_required`, `_poll_manual_cookies`, `cancel_manual_login_pending`, `is_awaiting_manual_login`
- **browser**: `_context_options`, `_install_stealth`, `_profile_dir`, `_pw_save`, `_warm_session`
- **challenge**: `_has_captcha`, `_try_turnstile_solve_LEGACY`
- **core**: `_compute_site_usage`, `_effective_concurrency`, `_learned_summary`, `_scrape_listing_urls`, `cookie_info`, `set_cookies_from_file`
- **extractors**: `_try_ytdlp_fallback`
- **integrity**: `_verify_hash_or_quarantine`, `_verify_integrity_or_quarantine`
- **manual**: `cancel_manual_download`, `finish_manual_download`, `is_awaiting_manual_download`, `start_manual_download`
- **queue**: `clear_completed`, `export_urls`, `set_priority`
- **scheduler**: `_auto_retry_loop`, `_clear_rl`, `_load_rl`, `_maybe_drift_recover`, `_next_sched_dt`, `_save_rl`, `_scan_subscriptions`, `_sched_loop`, `_start_auto_retry`, `sched_next_str`
- **teach**: `_handle_auto_teach_check`, `_teach_base_url`, `teach_cancel`, `teach_commit`, `teach_test_download`, `teach_verify`
- **telemetry**: `_build_mirror_urls`, `_extract_host`, `_flush_fingerprint_observation`, `_handle_failure`, `_install_event_listeners`, `_parse_hm`, `_pick_fastest_mirror`
- **transport**: `_current_cap_mbps`, `_observe_throughput`, `_probe_size`, `_recommended_chunk_bytes`