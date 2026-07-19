# RUNNER_CALLGRAPH.md (generated)

runner.py SiteRunner=167 methods, 12 module funcs, _ManualDownloadSession=12 methods. via tools/runner_struct.py.

method | lines | calls(self) [mod:..] | called-by


## manual (4 methods, 143 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `start_manual_download` | 3294-3331 (38) | _teach_base_url | - |
| `finish_manual_download` | 3333-3410 (78) | _override_suppresses_persist, _persist_learned_to_draft, _update_job, set_cookies, start | - |
| `cancel_manual_download` | 3412-3436 (25) | - | - |
| `is_awaiting_manual_download` | 3438-3439 (2) | - | get_status |

## challenge (5 methods, 327 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `_handle_captcha_check` | 5488-5538 (51) | _has_captcha, _screenshot, _try_captcha_solve, _try_ytdlp_fallback, _update_job, log_event | _process_one |
| `_has_captcha` | 11489-11502 (14) | - | _handle_captcha_check |
| `_try_turnstile_solve` | 11708-11713 (6) | _try_captcha_solve | - |
| `_try_captcha_solve` | 11715-11812 (98) | log_event | _handle_captcha_check, _try_turnstile_solve |
| `_try_turnstile_solve_LEGACY` | 11814-11971 (158) | log_event | - |

## teach (10 methods, 289 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `_teach_base_url` | 3443-3452 (10) | - | start_manual_download |
| `teach_verify` | 3454-3466 (13) | - | - |
| `teach_test_download` | 3468-3476 (9) | - | - |
| `teach_commit` | 3478-3562 (85) | _override_suppresses_persist, _persist_learned_to_draft, _update_job, set_cookies, start | - |
| `teach_cancel` | 3564-3589 (26) | - | - |
| `_recover_selector` | 4992-5039 (48) | log_event | _process_one |
| `_draft_override_template` | 5362-5377 (16) | - | _process_one |
| `_override_suppresses_persist` | 5379-5391 (13) | - | _maybe_drift_recover, finish_manual_download, finish_manual_login, teach_commit |
| `_persist_learned_to_draft` | 5393-5425 (33) | - | finish_manual_download, teach_commit |
| `_handle_auto_teach_check` | 5427-5462 (36) | _update_job, log_event | _process_one |

## integrity (6 methods, 352 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `_dedup_hash_worker` | 4930-4990 (61) | log_event | - |
| `_apply_quality_preference` | 5540-5592 (53) | - | _process_one |
| `_dedup_preflight` | 5594-5631 (38) | - | _process_one |
| `_verify_hash_or_quarantine` | 6383-6419 (37) | _update_job, log_event | _do_download |
| `_verify_integrity_or_quarantine` | 6421-6476 (56) | _update_job, log_event | _do_download |
| `_embed_metadata_if_mp4` | 7524-7630 (107) | log_event | _do_download, _try_aylo_extractor, _try_dl8_extractor, _try_jsonapi_extractor, _try_library_extractor, _try_vixen_extractor |

## extractors (10 methods, 1841 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `_try_ytdlp_fallback` | 4291-4363 (73) | log_event | _handle_captcha_check |
| `_try_deep_detect_fallback` | 6551-6899 (349) | _download_proxy_url, _persist_deep_detect_selectors | _process_one |
| `_persist_deep_detect_selectors` | 6901-6936 (36) | - | _try_deep_detect_fallback |
| `_try_jsonapi_extractor` | 7634-7876 (243) | _do_direct_http_download, _embed_metadata_if_mp4, _update_job, log_event | _process_one |
| `_try_vixen_extractor` | 7880-8117 (238) | _do_direct_http_download, _embed_metadata_if_mp4, _probe_for_higher_tier, _update_job, log_event | _process_one |
| `_try_dl8_extractor` | 8121-8339 (219) | _do_direct_http_download, _embed_metadata_if_mp4, _update_job, log_event | _process_one |
| `_try_aylo_extractor` | 8343-8601 (259) | _do_direct_http_download, _embed_metadata_if_mp4, _probe_for_higher_tier, _update_job, log_event | _process_one |
| `_probe_for_higher_tier` | 8605-8700 (96) | log_event | _do_download, _try_aylo_extractor, _try_library_extractor, _try_vixen_extractor |
| `_run_pre_scrape_action` | 8702-8767 (66) | log_event | _process_one |
| `_try_library_extractor` | 8769-9030 (262) | _do_direct_http_download, _embed_metadata_if_mp4, _probe_for_higher_tier, _update_job, log_event | _process_one |

## browser (9 methods, 337 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `_pw_save` | 10935-10939 (5) | - | _do_download |
| `_context_options` | 10941-10969 (29) | - | _launch_browser, _process_one |
| `_launch_args` | 10981-11009 (29) | - | _launch_browser |
| `_manual_profile_dir` | 11011-11032 (22) | - | start_captcha_solve_session, start_manual_login, verify_login_after_wizard |
| `_profile_dir` | 11034-11064 (31) | - | _launch_browser |
| `_launch_browser` | 11066-11196 (131) | _context_options, _install_stealth, _launch_args, _profile_dir | _worker_loop |
| `_install_stealth` | 11198-11208 (11) | - | _launch_browser, _process_one |
| `_apply_stealth_library_to_page` | 11210-11230 (21) | - | _process_one |
| `_warm_session` | 11430-11487 (58) | log_event | _process_one |

## queue (18 methods, 513 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `_restore_queue` | 1678-1719 (42) | - | __init__ |
| `load_urls` | 1759-1973 (215) | _playlist_expand_one, log_event  [mod: _ts] | _scan_subscriptions |
| `reorder_urls` | 1975-1986 (12) | - | - |
| `set_priority` | 1988-1997 (10) | - | - |
| `bulk_priority` | 1999-2016 (18) | - | - |
| `bulk_delete` | 2018-2031 (14) | - | - |
| `bulk_approve` | 2033-2050 (18) | -  [mod: _ts] | - |
| `bulk_pause` | 2058-2080 (23) | -  [mod: _ts] | - |
| `bulk_resume` | 2082-2100 (19) | -  [mod: _ts] | - |
| `bulk_retry` | 2102-2121 (20) | -  [mod: _ts] | - |
| `bulk_reorder` | 2123-2146 (24) | - | - |
| `bulk_url_transform` | 2149-2194 (46) | log_event | - |
| `clear_completed` | 2196-2207 (12) | - | clear |
| `retry_failed` | 2209-2222 (14) | - | retry |
| `retry` | 2228-2229 (2) | retry_failed | - |
| `clear` | 2233-2234 (2) | clear_completed | - |
| `export_urls` | 2236-2241 (6) | - | - |
| `_drain_url_queue` | 2243-2258 (16) | - | start |

## integrations (17 methods, 794 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `_get_stash_client` | 6480-6488 (9) | - | _stash_dedup_check, _stash_enrich_after_scan, _stash_scrape_preview |
| `_stash_dedup_check` | 6490-6549 (60) | _get_stash_client, _update_job, log_event | _process_one |
| `_stash_scrape_preview` | 6938-6958 (21) | _get_stash_client | - |
| `_stash_enrich_after_scan` | 6960-7056 (97) | _get_stash_client, log_event | - |
| `_get_plex_client` | 7060-7068 (9) | - | _plex_enrich_after_scan |
| `_plex_enrich_after_scan` | 7070-7171 (102) | _get_plex_client, log_event | - |
| `_get_jellyfin_client` | 7175-7182 (8) | - | _jellyfin_enrich_after_scan |
| `_jellyfin_enrich_after_scan` | 7184-7272 (89) | _get_jellyfin_client, log_event | - |
| `_get_qb_client` | 7276-7286 (11) | - | _try_qb_download |
| `_record_qb_outcome` | 7288-7291 (4) | - | _process_one |
| `qb_health` | 7293-7316 (24) | - | get_status |
| `_try_qb_download` | 7318-7450 (133) | _get_qb_client, _update_job, log_event | _process_one |
| `_get_jd_client` | 7454-7464 (11) | - | _try_jd_download |
| `_read_cookies_for_jd` | 7466-7488 (23) | - | _try_jd_download |
| `_record_jd_outcome` | 7490-7495 (6) | - | _process_one |
| `jd_health` | 7497-7520 (24) | - | get_status |
| `_try_jd_download` | 9331-9493 (163) | _get_jd_client, _read_cookies_for_jd, _update_job, log_event, login_async | _process_one |

## auth (15 methods, 765 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `login_async` | 2704-2825 (122) | log_event, set_cookies, start_manual_login | _check_cookies_or_relogin, _handle_auth_required, _rotate_account_if_available, _sched_loop, _try_jd_download, maybe_preemptive_relogin |
| `start_manual_login` | 2827-2893 (67) | _manual_profile_dir | login_async |
| `_poll_manual_cookies` | 2895-2920 (26) | - | - |
| `start_captcha_solve_session` | 2925-2973 (49) | _manual_profile_dir, log_event | - |
| `end_captcha_solve_session` | 2975-3009 (35) | _update_job, log_event | - |
| `finish_manual_login` | 3011-3215 (205) | _override_suppresses_persist, set_cookies | - |
| `verify_login_after_wizard` | 3217-3266 (50) | _manual_profile_dir | - |
| `get_last_verify_result` | 3268-3272 (5) | - | - |
| `cancel_manual_login_pending` | 3274-3288 (15) | - | - |
| `is_awaiting_manual_login` | 3290-3291 (2) | - | get_status |
| `_check_redirect` | 3975-3996 (22) | - | _process_one |
| `_handle_auth_required` | 3998-4068 (71) | _update_job, login_async | _process_one |
| `_cookie_age_hours` | 4171-4178 (8) | - | get_status, maybe_preemptive_relogin |
| `maybe_preemptive_relogin` | 4180-4244 (65) | _cookie_age_hours, log_event, login_async | _auto_retry_loop |
| `_check_cookies_or_relogin` | 5464-5486 (23) | _handle_failure, _update_job, login_async | _process_one |

## transport (15 methods, 1800 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `_download_proxy_url` | 9032-9056 (25) | - | _do_direct_http_download, _http_download, _http_download_parallel, _pick_fastest_mirror, _probe_size, _scrape_listing_urls, _try_deep_detect_fallback |
| `_do_direct_http_download` | 9058-9182 (125) | _download_proxy_url, _try_multi_conn_download, _update_job | _try_aylo_extractor, _try_dl8_extractor, _try_jsonapi_extractor, _try_library_extractor, _try_vixen_extractor |
| `_try_multi_conn_download` | 9184-9329 (146) | _update_job, log_event | _do_direct_http_download |
| `_looks_like_media` | 9495-9527 (33) | - | - |
| `_probe_outcome` | 9529-9534 (6) | - | - |
| `_integrity_size_ok` | 9536-9545 (10) | - | - |
| `_promote_or_abort` | 9547-9571 (25) | - | - |
| `_do_probe_fetch` | 9573-9648 (76) | _update_job | _do_download |
| `_do_download` | 9650-10117 (468) | _build_mirror_urls, _do_probe_fetch, _embed_metadata_if_mp4, _extract_host, _handle_failure, _http_download, _probe_for_higher_tier, _pw_save, _screenshot, _update_job, _verify_hash_or_quarantine, _verify_integrity_or_quarantine, log_event, set_cookies  [mod: _bump_learned_stat, gate_candidate_url, resolve_url_attribute] | _process_one |
| `_http_download` | 10119-10546 (428) | _current_cap_mbps, _download_proxy_url, _http_download_parallel, _observe_throughput, _pick_fastest_mirror, _probe_size, _recommended_chunk_bytes, _update_job, log_event  [mod: record_bandwidth] | _do_download |
| `_probe_size` | 10549-10595 (47) | _download_proxy_url | _http_download |
| `_http_download_parallel` | 10597-10933 (337) | _current_cap_mbps, _download_proxy_url, _update_job, log_event | _http_download |
| `_current_cap_mbps` | 11505-11537 (33) | _parse_hm | _http_download, _http_download_parallel |
| `_recommended_chunk_bytes` | 11548-11571 (24) | - | _http_download |
| `_observe_throughput` | 11573-11589 (17) | - | _http_download |

## accounts (8 methods, 215 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `is_rate_limited` | 3751-3759 (9) | _clear_rl | get_status, start |
| `rl_remaining` | 3761-3768 (8) | - | get_status |
| `trigger_rate_limit` | 3770-3797 (28) | _rotate_account_if_available, _save_rl | _process_one |
| `_get_active_account` | 3800-3821 (22) | -  [mod: _resolve_safe] | - |
| `_rotate_account_if_available` | 3823-3922 (100) | _persist_account_state, _persist_pool_state, login_async, set_cookies | trigger_rate_limit |
| `_persist_account_state` | 3924-3932 (9) | - | _rotate_account_if_available |
| `_persist_pool_state` | 3934-3962 (29) | - | _rotate_account_if_available |
| `_wait_rl_autostart` | 3964-3973 (10) | start | - |

## scheduler (15 methods, 407 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `_start_auto_retry` | 1308-1316 (9) | - | __init__ |
| `_stop_auto_retry` | 1318-1325 (8) | - | stop |
| `_parse_retry_schedule` | 1327-1343 (17) | - | _auto_retry_scan |
| `_auto_retry_loop` | 1345-1372 (28) | _auto_retry_scan, _scan_subscriptions, maybe_preemptive_relogin | - |
| `_scan_subscriptions` | 1374-1444 (71) | _scrape_listing_urls, load_urls, log_event | _auto_retry_loop |
| `_auto_retry_scan` | 1540-1666 (127) | _fmt_dur, _parse_retry_schedule, log_event  [mod: _ts] | _auto_retry_loop |
| `_maybe_drift_recover` | 3592-3616 (25) | _override_suppresses_persist | _worker_loop |
| `_load_rl` | 3619-3630 (12) | - | __init__ |
| `_save_rl` | 3632-3646 (15) | - | trigger_rate_limit |
| `_clear_rl` | 3648-3651 (4) | - | is_rate_limited |
| `_next_sched_dt` | 3654-3663 (10) | - | _sched_loop, sched_next_str |
| `sched_next_str` | 3665-3674 (10) | _next_sched_dt | get_status |
| `start_scheduler` | 3676-3686 (11) | - | __init__, update_config |
| `stop_scheduler` | 3688-3699 (12) | - | update_config |
| `_sched_loop` | 3701-3748 (48) | _next_sched_dt, login_async, start | - |

## telemetry (12 methods, 405 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `_fmt_dur` | 1668-1676 (9) | - | _auto_retry_scan |
| `log_event` | 11233-11313 (81) | - | _auto_retry_scan, _dedup_hash_worker, _do_download, _effective_concurrency, _embed_metadata_if_mp4, _flush_fingerprint_observation, _handle_auto_teach_check, _handle_captcha_check, _http_download, _http_download_parallel, _install_event_listeners, _jellyfin_enrich_after_scan, _pick_fastest_mirror, _plex_enrich_after_scan, _probe_for_higher_tier, _process_one, _recover_selector, _run_pre_scrape_action, _scan_subscriptions, _stash_dedup_check, _stash_enrich_after_scan, _try_aylo_extractor, _try_captcha_solve, _try_dl8_extractor, _try_jd_download, _try_jsonapi_extractor, _try_library_extractor, _try_multi_conn_download, _try_qb_download, _try_turnstile_solve_LEGACY, _try_vixen_extractor, _try_ytdlp_fallback, _update_job, _verify_hash_or_quarantine, _verify_integrity_or_quarantine, _warm_session, _watchdog_loop, bulk_url_transform, end_captcha_solve_session, load_urls, login_async, maybe_preemptive_relogin, start, start_captcha_solve_session |
| `get_events` | 11315-11329 (15) | - | - |
| `_install_event_listeners` | 11331-11409 (79) | log_event | _process_one |
| `_flush_fingerprint_observation` | 11411-11428 (18) | log_event | _process_one |
| `_parse_hm` | 11539-11545 (7) | - | _current_cap_mbps |
| `_extract_host` | 11592-11598 (7) | - | _do_download |
| `_pick_fastest_mirror` | 11600-11664 (65) | _build_mirror_urls, _download_proxy_url, log_event | _http_download |
| `_build_mirror_urls` | 11666-11706 (41) | - | _do_download, _pick_fastest_mirror |
| `_classify_error` | 11973-11995 (23) | - | _handle_failure |
| `_handle_failure` | 12006-12049 (44) | _classify_error, _update_job | _check_cookies_or_relogin, _do_download, _process_one, _worker_loop |
| `_screenshot` | 12051-12066 (16) | - | _do_download, _handle_captcha_check, _process_one |

## core (23 methods, 2504 lines)

| method | lines | calls (self) | called-by |
|---|---|---|---|
| `__init__` | 1135-1306 (172) | _load_rl, _restore_queue, _start_auto_retry, start_scheduler | - |
| `_scrape_listing_urls` | 1446-1538 (93) | _download_proxy_url | _scan_subscriptions |
| `update_config` | 1721-1727 (7) | start_scheduler, stop_scheduler | - |
| `set_cookies_from_file` | 1729-1740 (12) | - | - |
| `set_cookies` | 1742-1749 (8) | - | _do_download, _rotate_account_if_available, finish_manual_download, finish_manual_login, login_async, teach_commit |
| `cookie_info` | 1751-1757 (7) | - | get_status |
| `start` | 2260-2516 (257) | _compute_site_usage, _drain_url_queue, _update_job, is_rate_limited, log_event | _sched_loop, _wait_rl_autostart, _watch_done, finish_manual_download, teach_commit |
| `_watchdog_loop` | 2518-2565 (48) | log_event | - |
| `_effective_concurrency` | 2567-2643 (77) | log_event | _worker_loop |
| `pause` | 2645-2658 (14) | - | - |
| `resume` | 2660-2668 (9) | - | - |
| `stop` | 2670-2702 (33) | _stop_auto_retry  [mod: _ts] | - |
| `get_status` | 4070-4169 (100) | _cookie_age_hours, _learned_summary, cookie_info, is_awaiting_manual_download, is_awaiting_manual_login, is_rate_limited, jd_health, qb_health, rl_remaining, sched_next_str | - |
| `_learned_summary` | 4246-4261 (16) | - | get_status |
| `state` | 4263-4263 (1) | - | - |
| `_compute_site_usage` | 4265-4289 (25) | - | start |
| `_update_job` | 4365-4796 (432) | log_event  [mod: _ts] | _check_cookies_or_relogin, _do_direct_http_download, _do_download, _do_probe_fetch, _handle_auth_required, _handle_auto_teach_check, _handle_captcha_check, _handle_failure, _http_download, _http_download_parallel, _process_one, _stash_dedup_check, _try_aylo_extractor, _try_dl8_extractor, _try_jd_download, _try_jsonapi_extractor, _try_library_extractor, _try_multi_conn_download, _try_qb_download, _try_vixen_extractor, _verify_hash_or_quarantine, _verify_integrity_or_quarantine, end_captcha_solve_session, finish_manual_download, start, teach_commit |
| `_wait_for_lazy_video` | 4798-4825 (28) | - | - |
| `_playlist_expand_one` | 4827-4868 (42) | - | load_urls |
| `_search_site` | 4874-4928 (55) | - | - |
| `_watch_done` | 5041-5109 (69) | start | - |
| `_worker_loop` | 5111-5360 (250) | _effective_concurrency, _handle_failure, _launch_browser, _maybe_drift_recover, _process_one | - |
| `_process_one` | 5633-6381 (749) | _apply_quality_preference, _apply_stealth_library_to_page, _check_cookies_or_relogin, _check_redirect, _context_options, _dedup_preflight, _do_download, _draft_override_template, _flush_fingerprint_observation, _handle_auth_required, _handle_auto_teach_check, _handle_captcha_check, _handle_failure, _install_event_listeners, _install_stealth, _record_jd_outcome, _record_qb_outcome, _recover_selector, _run_pre_scrape_action, _screenshot, _stash_dedup_check, _try_aylo_extractor, _try_deep_detect_fallback, _try_dl8_extractor, _try_jd_download, _try_jsonapi_extractor, _try_library_extractor, _try_qb_download, _try_vixen_extractor, _update_job, _warm_session, log_event, trigger_rate_limit  [mod: _bump_learned_stat, _bump_per_selector, _maybe_demote_selectors] | _worker_loop |