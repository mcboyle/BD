# RUNNER_STATE_CONTRACT.md

The instance-attribute **state bus** that mixins share through `self`. Mixins hold no
`__init__`; all state is created by the single `SiteRunner.__init__` (+ lazy first-touch).
Correctness is preserved on extraction regardless of which mixin reads/writes an attr,
but THESE are the cross-cutting attrs to treat as the inter-mixin API.

via tools/runner_seams.py.

## Cross-cutting attrs (touched by >=4 units) -- 9

| attr | written by | read by |
|---|---|---|
| `self.site_id` | core | accounts, auth, browser, challenge, core, extractors, integrations, integrity, manual, queue, scheduler, teach, telemetry, transport |
| `self.config` | core, extractors | accounts, auth, browser, challenge, core, extractors, integrations, integrity, manual, queue, scheduler, teach, telemetry, transport |
| `self.jobs` | core, queue | accounts, auth, core, integrity, manual, queue, scheduler, teach, telemetry, transport |
| `self._lock` | core | accounts, auth, core, integrity, manual, queue, scheduler, teach, telemetry, transport |
| `self.log` | core | auth, core, integrity, manual, queue, scheduler, teach, transport |
| `self._stop` | core | accounts, core, extractors, integrations, teach, transport |
| `self._url_queue` | core | auth, core, manual, queue, teach |
| `self.cookies` | core | auth, core, extractors, integrations |
| `self._login_status` | auth, core, manual, teach | core |

## Construction
- `__init__` sets **48** attrs directly; **13** data attrs are LAZY (first-set outside __init__).
- `__init__` CALLS these methods during construction (their mixins must be bases when their cut lands): `_load_rl`, `_restore_queue`, `_start_auto_retry`, `start_scheduler`.
- total distinct `self` data-attrs: 61 (excludes method-name refs).

## On-disk persistence surface (restart format-contracts; pure motion preserves them)
- `rl_{site_id}.json` (BD_HOME) -- rate-limit state -> **scheduler** (`_load_rl/_save_rl/_clear_rl`)
- account_state / pool_state -> **accounts** (`_persist_account_state/_persist_pool_state`)
- `{DRAFTS_DIR}/...` learned-selector drafts -> **teach** (`_persist_learned_to_draft`)
- `{SCREENSHOTS_DIR}/...` failure screenshots -> **telemetry** (`_screenshot`)
- `{site_id}.json` JD cookies -> **integrations** (`_read_cookies_for_jd`)
- download dir (`config['download_dir']`) -> **transport/core**

## Concurrency surface
- `self._lock` (main) guards the shared mutable state (`self.jobs`, `self._url_queue`); acquired by methods across many units -- a cut must not change locking order.
- `self._worker_heartbeats_lock`, plus `threading.Event`s (`_ready/_closed/_session_ok/_manual_snapshot_stop`). 12 `threading.Thread` spawns (13 thread-target methods incl. `_worker_loop/_watchdog_loop/_sched_loop`).
- module-level: `_bw_lock` (bandwidth, -> runner_util), `_global_sem_lock` (cap, STAYS core).

## Cross-unit exception contract
- `_HTTPDownloadFailed` -- primary download-failure exception, raised ~15x (extractors+transport), caught ~5x (transport/core). Both sides import it from its source module (not runner), so motion is safe; it is a control-flow contract spanning units.
- `_DownloadTruncated` (raised/caught within transport). `VPNRequiredError` (caught 1x, fail-closed Track-K; raised in egress modules).

## Config input
- `self.config` is the central shared input: **145** distinct keys read across all units (top: name, download_dir, learned, user_agent, cookie_file, accounts, ...). Tracked separately by `tools/config_surface_inventory.py` -- the site-template schema.

## Job-record schema (the per-URL state dict in `self.jobs`, mutated by every unit)
Central mutator: `_update_job` (core, ~432 lines) -- also externally called. Common fields:
`status` (most-touched), `message`, `priority`, `retries`/`auto_retry_count`/`corruption_retries`,
`retry_after`/`next_auto_retry_at`/`last_progress_at`/`ts`, `auto_teach_seen`, `force_download`,
`custom_headers`, `thumbnail`, `_run_id`. A cut must not change these keys (frontend + persistence read them).