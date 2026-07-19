# RUNNER_IMPORT_MAP.md

Per target-module: the module-level names its methods reference, so each cut's
import block is mechanical. **Cycle rule:** a mixin must NOT `from .runner import X`
(runner imports the mixin -> cycle). Anything a mixin needs that lives in runner.py
today must move to `runner_util.py` (the kernel: imported by all, imports nothing back).
Names tagged [KERNEL] go to runner_util; [CORE] stay in runner.py (worker-coupled).

via tools/runner_seams.py.


## transport
- from `runner_util`: `_bump_learned_stat`, `gate_candidate_url`, `record_bandwidth`, `resolve_url_attribute`
- classes: `SiteRunner`  **(rewrite `SiteRunner._foo`->`self._foo` to avoid importing SiteRunner)**
- 3p/stdlib (import from original source): `PWTimeout`, `Path`, `_DownloadTruncated`, `_HTTPDownloadFailed`, `datetime`, `db_log`, `effective_download_proxy`, `fmt_bytes`, `json`, `os`, `res_label`, `resolve_filename_template`, `safe_dest`, `shutil`, `sqlite3`, `sys`, `time`

## extractors
- module consts: `DEFAULT_MIN_RESOLUTION` [KERNEL]
- 3p/stdlib (import from original source): `datetime`, `db_log`, `find_best_download`, `fmt_bytes`, `format_duration_for_filename`, `os`, `resolve_filename_template`, `subprocess`, `sys`

## integrations
- 3p/stdlib (import from original source): `time`

## manual
- classes: `_ManualDownloadSession`
- 3p/stdlib (import from original source): `sys`

## auth
- 3p/stdlib (import from original source): `AUTH_BODY_RE`, `AUTH_HINTS`, `BLOCK_HINTS`, `RL_RE`, `cookies_expiry_info`, `db_log`, `do_login`, `sys`, `threading`, `time`

## teach
- 3p/stdlib (import from original source): `sys`, `time`

## scheduler
- from `runner_util`: `_ts`
- 3p/stdlib (import from original source): `datetime`, `json`, `os`, `queue_upsert`, `sys`, `threading`, `time`, `timedelta`

## queue
- from `runner_util`: `_ts`
- 3p/stdlib (import from original source): `Path`, `queue`, `queue_bulk_delete`, `queue_bulk_update`, `queue_bulk_upsert`, `queue_delete_status`, `queue_load`, `queue_reorder`, `queue_set_priority`, `queue_upsert`, `sqlite3`, `sys`, `time`

## browser
- 3p/stdlib (import from original source): `safe_dest`, `sys`, `time`

## challenge
- 3p/stdlib (import from original source): `db_log`, `sys`, `time`

## telemetry
- module consts: `_BD_TO_APPRISE_EVENT` [KERNEL]
- 3p/stdlib (import from original source): `RETRY_DELAYS`, `db_log`, `re`, `sys`, `threading`, `time`

## integrity
- 3p/stdlib (import from original source): `db_log`, `format_duration_for_filename`, `shutil`, `sys`, `verify_media_integrity`

## accounts
- from `runner_util`: `_resolve_safe`
- 3p/stdlib (import from original source): `Path`, `sys`, `threading`, `time`

## core
- from `runner_util`: `_bump_learned_stat`, `_bump_per_selector`, `_maybe_demote_selectors`, `_ts`
- module consts: `DEFAULT_MAX_CONCURRENT` [CORE], `DEFAULT_MIN_RESOLUTION` [KERNEL], `_global_sem` [CORE]
- 3p/stdlib (import from original source): `PWTimeout`, `Path`, `SCREENSHOTS_DIR`, `collections`, `cookie_age_str`, `cookies_expiry_info`, `db_log`, `disk_free_gb`, `find_best_download`, `fmt_bytes`, `itertools`, `load_cookies_from_file`, `os`, `queue`, `queue_upsert`, `res_label`, `sys`, `threading`, `time`