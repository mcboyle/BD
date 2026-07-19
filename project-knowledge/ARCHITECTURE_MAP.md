<!-- verified-against: v3.66.464 -->
# BulkDownloader -- ARCHITECTURE MAP (source topology + pipeline)

Where things live and how a capture becomes a download, as of @464 (decomposition CLOSED, so
this topology is now stable). Navigation aid -- the authoritative source is always the tree;
re-derive counts before trusting them. Pairs with `CROSS_MONOLITH_IMPORT_GRAPH.md` (import edges)
and `DANGER_MAPv2.md` (load-bearing invariants in this topology).

## The pipeline: capture -> template -> download

```
 operator (noVNC / sentinel / manual)
   |
   v
 CAPTURE  -- session_capture.py, dom_recorder.py (rrweb, maskAllInputs:true),
   |        dom_capture.py, capture_bodies.py, tools/capture_session.py        [GUARD]
   v
 WACZ     -- wacz_export.py  (rrweb DOM log + network log + metadata;
   |        dom_integrity_ok = dom-log count == DOM count)
   v
 REDACT   -- capture_artifact_redact.py / redact_capture  (-> <scrubbed>)
   |        then scan_floor_secrets = the FLOOR (fail-loud WaczRedactionError on residual)
   v
 EXTRACT  -- extraction_core.py  [GUARD, FROZEN: network_patterns / observed_api_hosts]
   |        + tools/build_template_from_wacz.py  [NON-guard: recognizer breadth, D++/DPP,
   |        Content-Disposition]  -> a template DRAFT
   v
 REVIEW   -- draft -> review-candidate -> reviewed   (the 3 schemas in SCHEMAS.md);
   |        screenshot-triage / approve / promote (F2.3, cockpit)
   v
 PROMOTE  -- reviewed template enters the live set; runtime auto-applies ENABLED templates
   |
   v
 DOWNLOAD -- runner.py (kernel + 13 mixins) drives login/play/resolve/transport;
            provider_resolve.py resolves the stream; ytdlp / multi_conn / transport do the pull
```

**The guard boundary** sits at EXTRACT: everything capture-side through `extraction_core` is in
the 7-file byte-identical guard set; recognizer breadth is added in the NON-guard builder. This is
the "guard-defer is a recognition ceiling" rule -- new shapes go to the builder, never the guard.

## runner.py -- kernel + 13 mixins (Phase 3)

`runner.py` (kernel, ~3100L) + `runner_util.py` (helpers) + 13 behavior mixins:
`AccountsMixin` (runner_accounts) - `AuthMixin` (runner_auth) - `BrowserMixin` (runner_browser) -
`ChallengeMixin` (runner_challenge) - `ExtractorsMixin` (runner_extractors) - `IntegrationsMixin`
(runner_integrations) - `IntegrityMixin` (runner_integrity) - `ManualMixin` (runner_manual) -
`QueueMixin` (runner_queue) - `SchedulerMixin` (runner_scheduler) - `TeachMixin` (runner_teach) -
`TelemetryMixin` (runner_telemetry) - `TransportMixin` (runner_transport).
Load-bearing: `_process_one` dispatch order; `session_keeper` nested-playwright pause;
`resume_site_keepers` must NEVER exist. (Full invariants: `DANGER_MAPv2.md`.)

## app.py -- 169 blueprints (Phase 4), by functional domain

`app.py` is now a thin shell; routes live in `app_<group>.py` (169 blueprints, ~746 routes),
registered fail-open in the wire section. A leaf blueprint imports flask/stdlib, never `app`;
`s_cfg` is imported inside the function body. Grouped (representative members):

- **Capture & sessions**: captures, live_recorder, session_history, session_status, provenance
- **Templates & recognizer**: template, template_manager(_ui), templates, user_templates,
  login_templates, queue_templates, selector_drift, route_preview, route_urls
- **Accounts / auth / cookies / secrets**: accounts, account_pool, auth_health, cookie_quality,
  cookie_relogin, cookie_clipboard, login_templates, secrets, csrf, api_tokens
- **Queue / jobs / runs / concurrency**: queue, jobs, runs, runners, batch, bulk, quick_add,
  multi_conn, concurrent, start_all, pause_all, resume_all
- **Scrapers / discovery / sources**: community_scrapers, discovery, scrape_listing, scrapling,
  search, saved_searches, sites, sites_list, marketplace, tpdb, wayback, plex, tg, ytdlp_*
- **VPN / network / rate**: vpn, vpn_api, flaresolverr, circuit, rate_limit, cluster_rate,
  edge_deploy, captcha_relay_api
- **AI / LLM**: ai, recommendations, knowledge, playground
- **Cockpit / UI / dashboard**: cockpit_home, dashboard, settings_center, report_center, palette,
  widgets_api, ui_events, shortcuts, macros, quick_add
- **Media / thumbnails / scoring**: thumbnails, thumbs, thumbnail_sheets, subtitles, scene_score,
  stream, bw_chart
- **Storage / backup / maintenance**: storage, storage_rebalance, backup, cleanup, crash_recovery,
  ramdisk, bitrot, rebalance, eol, wakeup
- **Monitoring / telemetry / stats / health**: stats, hourly_stats, daily_budget, budget, cost,
  capacity, history, activity, events_all, logs, status, health, diagnostics(_bundle), selftest,
  synthetic_tests, audit, circuit
- **Config / admin / schedules / deploy**: config, global_config, retention, retry_policy,
  schedules, scheduled_exports, sched_exports, deploy, supervisor, phoenix
- **Integration / extension / notify**: extension, integrations, webhooks, notify, push, openapi,
  jsonapi, fed, shares, gamification

(Exact membership drifts; regenerate the blueprint list from `ls app_*.py` + `gui_parity_inventory`.)

## deep_detect/ -- 12 submodules (Phase 2)

`deep_detect.py` (8,907L monolith) -> a package of 12 submodules; largest is `orchestrate.py`
(~1,813L). The deep page/player analysis layer feeding recognition.

## Leaf modules (Phase 4 leaf band, 448-454)

Extracted from app.py as standalone modules: `templates.py` (DATA), `template_extractor.py`,
`login.py` (ManualLogin), `capture_workbench.py` (goal_select; the deliberate home of
`decision_confidence`/`CP_*` -- see EXTRACTION_CORE_DECISION), `learn.py`, `provider_resolve.py`.

## Data layer

`db.py` -- the SQLite layer; `db_conn`'s `isolation_level` / `journal_mode=WAL` hack is
load-bearing (multiple readers coexist). Secrets in `secrets_store.py` (encrypted backends RAISE
when locked; PlaintextBackend by design returns None/False). VPN creds in `vpn_config.py`
(`_CRED_PREFIX = "@cred:"`, a separate prefix from secrets).

## Where to look first (symptom -> module)

- capture wedges / nav blocked -> stale Chromium `Singleton*` lock; `session_capture` /
  `runner_browser`; check `dom_integrity_ok`.
- redaction halt -> `wacz_export.py` floor (`scan_floor_secrets`); fix the scrubber, not the scanner.
- a site yields no download pattern -> recognizer ceiling; extend `build_template_from_wacz`
  (NON-guard), not `extraction_core`.
- a route 404s in the SPA -> `gui_parity_inventory` `spa_wired`; wiring must use full `/api/...`.
- backend reports `playwright` not `cloakbrowser` -> wrong interpreter; use `venv/bin/python`.
