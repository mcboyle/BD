# RUNNER_EVENT_VOCAB.md

The event-kind vocabulary SiteRunner emits via `self.log_event(kind, ...)`, consumed
downstream by the SSE/event stream (`get_events`) and the notification layer
(`_BD_TO_APPRISE_EVENT` -> apprise). **Regression guard:** a cut that drops or renames
a kind breaks notifications/SSE SILENTLY (no test names most of these). `log_event`
lives in the telemetry unit, but the kind LITERALS are scattered across every unit
(each caller owns its string) -- moving log_event does not change them; verify this
list is unchanged after a cut that touches telemetry or any emitting unit.

via tools/runner_contracts.py.

## log_event kinds (78)

`account_rotate`, `admission`, `auto_retry`, `auto_teach`, `aylo_done`, `aylo_extract_failed`, `aylo_hls_failed`, `aylo_skip_nonpremium`, `bandwidth_scale`, `captcha`, `corruption_retry`, `cross_site_dupe`, `dedup_match`, `dedup_skip`, `dedup_unique`, `disk_throttle`, `fingerprint_observed`, `flaresolverr_failed`, `flaresolverr_inject_failed`, `flaresolverr_solve_start`, `flaresolverr_solved`, `hash`, `import`, `jd_auth_refresh`, `jd_done`, `jd_fallback`, `jd_unreachable`, `jellyfin_enrich`, `js_error`, `jsonapi_done`, `jsonapi_extract_failed`, `jsonapi_hls_failed`, `library_extract_failed`, `library_hls_failed`, `login_template_fallback`, `maintenance`, `metadata_tagged`, `mirror`, `mirror_speculative`, `multi_conn_done`, `multi_conn_failed`, `multi_conn_start`, `network`, `parallel_fallback`, `playlist_expand_failed`, `playlist_expanded`, `plex_enrich`, `pre_scrape_action_ok`, `preemptive_relogin`, `qb_auth_refresh`, `qb_done`, `qb_fallback`, `qb_unreachable`, `quota_hit`, `resume`, `selector_recovered`, `smart_wakeup`, `stash_dedup`, `stash_enrich`, `state`, `subscription`, `tier_probe_noop`, `tier_promoted`, `transform`, `turnstile_bypass_failed`, `turnstile_bypassed`, `turnstile_detected`, `turnstile_inject_failed`, `vixen_done`, `vixen_extract_failed`, `vixen_hls_failed`, `warmup`, `webhook_rewrite`, `webhook_skip`, `window`, `worker_hung`, `ytdlp`, `ytdlp_archive_skip`

## _BD_TO_APPRISE_EVENT mapping (runner kind -> apprise event)

| runner kind | apprise event |
|---|---|
| `done` | `download_done` |
| `aylo_done` | `download_done` |
| `vixen_done` | `download_done` |
| `jsonapi_done` | `download_done` |
| `fail` | `download_failed` |
| `aylo_extract_failed` | `download_failed` |
| `vixen_extract_failed` | `download_failed` |
| `aylo_hls_failed` | `download_failed` |
| `vixen_hls_failed` | `download_failed` |
| `captcha` | `captcha` |
| `captcha_pending` | `captcha` |
| `auth_required` | `auth_required` |
| `disk_full` | `disk_full` |
| `disk_low` | `disk_full` |
| `queue_empty` | `queue_empty` |
| `queue_paused` | `queue_paused` |
| `queue_resumed` | `queue_resumed` |