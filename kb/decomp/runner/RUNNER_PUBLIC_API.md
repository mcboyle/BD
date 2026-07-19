# RUNNER_PUBLIC_API.md

SiteRunner is instantiated per site into a global `runners = {}` dict in `app.py`
(keyed by `site_id`), which is dependency-injected into ~10 helper modules (perf_lab,
capacity, dispatch_chain, circuit-review, ...). So the method API is broad and
multi-module. The decomposition must preserve every method NAME (the snapshot's
set-freeze enforces this); moving a method between mixins is safe (resolved via the
instance), but RENAMING any name below breaks an external caller.

via tools/runner_contracts.py.

## Distinctive methods called outside runner.py: 41  (the rename-unsafe floor)
## Plus obvious lifecycle (matched-but-filtered as common names): start, stop, pause, resume, clear, state, retry, update_config

### `_`-prefixed but EXTERNALLY CALLED -- do NOT rename (despite the underscore): ['_launch_browser', '_persist_pool_state', '_rotate_account_if_available', '_search_site', '_stash_scrape_preview', '_stop_auto_retry', '_update_job']

| method | unit | ext calls | caller modules |
|---|---|---|---|
| `get_status` | core | 22 | account_pool.py, app.py, app_data_layer.py, dev_suite.py |
| `log_event` | telemetry | 15 | app.py, detect.py, login.py, run_history.py, storage_tier.py, watch_folder.py |
| `load_urls` | queue | 15 | app.py, watch_folder.py |
| `is_rate_limited` | accounts | 5 | app.py, capacity.py |
| `get_events` | telemetry | 4 | app.py |
| `set_cookies_from_file` | core | 4 | app.py |
| `bulk_retry` | queue | 3 | app.py, dev_suite.py |
| `_update_job` | core | 3 | app.py |
| `login_async` | auth | 2 | app.py |
| `start_manual_login` | auth | 2 | app.py |
| `reorder_urls` | queue | 2 | app.py |
| `bulk_delete` | queue | 2 | app.py |
| `_search_site` | core | 2 | app.py |
| `_stop_auto_retry` | scheduler | 2 | app.py |
| `is_awaiting_manual_login` | auth | 1 | capacity.py |
| `_launch_browser` | browser | 1 | session_keeper.py |
| `_rotate_account_if_available` | accounts | 1 | app.py |
| `finish_manual_login` | auth | 1 | app.py |
| `cancel_manual_login_pending` | auth | 1 | app.py |
| `get_last_verify_result` | auth | 1 | app.py |
| `start_manual_download` | manual | 1 | app.py |
| `finish_manual_download` | manual | 1 | app.py |
| `cancel_manual_download` | manual | 1 | app.py |
| `teach_verify` | teach | 1 | app.py |
| `teach_test_download` | teach | 1 | app.py |
| `teach_commit` | teach | 1 | app.py |
| `teach_cancel` | teach | 1 | app.py |
| `set_priority` | queue | 1 | app.py |
| `bulk_priority` | queue | 1 | app.py |
| `bulk_approve` | queue | 1 | app.py |
| `bulk_pause` | queue | 1 | app.py |
| `bulk_resume` | queue | 1 | app.py |
| `bulk_reorder` | queue | 1 | app.py |
| `_persist_pool_state` | accounts | 1 | app.py |
| `_stash_scrape_preview` | integrations | 1 | app.py |
| `verify_login_after_wizard` | auth | 1 | app.py |
| `set_cookies` | core | 1 | app.py |
| `export_urls` | queue | 1 | app.py |
| `start_captcha_solve_session` | auth | 1 | app.py |
| `bulk_url_transform` | queue | 1 | app.py |
| `end_captcha_solve_session` | auth | 1 | app.py |