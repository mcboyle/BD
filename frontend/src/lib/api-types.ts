// Types matching the /api/*/v2 endpoint payloads. Single source of
// truth for the SPA — if backend U2 changes a shape, this file is the
// one place to update.

export type AttentionKind = "captcha_pending" | "login_expired" | "rate_limited";

export interface AttentionEntry {
  site_id: string;
  name: string;
  kind: AttentionKind;
  label: string;
  since_ts: number;
  age_human?: string;
  until_ts?: number;
}

export interface BySiteEntry {
  site_id: string;
  name: string;
  avatar_color: string;
  queued: number;
  running: number;
  today_done: number;
}

export interface DashboardV2 {
  ok: boolean;
  attention: AttentionEntry[];
  by_site: BySiteEntry[];
  today: { done: number; running: number; failed: number };
  /**
   * Sites whose runner state is "running" (one tick per site, not
   * per concurrent download worker). Pre-V2.1 this was the only
   * worker-related field; the V2 redesign needs the proper download-
   * worker counts (workers_active / workers_total) and this field
   * is kept for back-compat with any caller that read it before.
   */
  active_workers: number;
  /**
   * V2.1 (D3 visual redesign): sum of `active_worker_count()` across
   * all runners — the concurrent download workers currently running.
   * Optional in the type because pre-V2.1 servers don't emit it.
   */
  workers_active?: number;
  /**
   * V2.1: sum of `max_concurrent` across all runners (the configured
   * download-worker pool size). `null` when no runners are configured
   * (FROZEN by DANGER_MAP: "workers_total is None on empty fleet —
   * never the magic 16", enforced by
   * tests/test_u49_workers_total_honest.py). Optional in the type
   * because pre-V2.1 servers don't emit it.
   */
  workers_total?: number | null;
  sites_count: number;
  ts: number;
}

export interface SparklineSample {
  ts: number;
  value: number;
}

export interface SparklineV2 {
  ok: boolean;
  current: number;
  history: SparklineSample[];
  ts: number;
}

export interface QueueRunningEntry {
  site_id: string;
  site_name: string;
  avatar_color: string;
  url: string;
  filename: string;
  progress: number;
  bytes_done: number;
  bytes_total: number;
  eta_seconds: number | null;
  rate_human: string;
}

export interface QueueV2 {
  ok: boolean;
  running: QueueRunningEntry[];
  waiting: unknown[];
  waiting_truncated_count: number;
  done_today_count: number;
  ts: number;
}

export type AuthState = "ok" | "expired" | "unknown";

export interface SiteEntryV2 {
  site_id: string;
  name: string;
  avatar_color: string;
  state: string;
  auth_state: AuthState;
  captcha_pending: boolean;
  downloaded_total: number;
  active_workers: number;
  last_event_ts: number;
  last_event_age: string;
  // F3.4 advisory honeypot drop-threshold suggestion (surfacing only —
  // never changes drop behaviour). `suggested` is null until the site
  // has enough confirmed-trap evidence; both fields are additive/optional.
  honeypot_threshold_suggested?: number | null;
  honeypot_threshold_samples?: number;
}

export interface SitesV2 {
  ok: boolean;
  sites: SiteEntryV2[];
  count: number;
  ts: number;
}

// T11 (v3.66.264) — per-site approval gate. A deep_detect run surfaces
// auto-submit (login form / page blocker) and post-reveal candidates
// that carry bot-defense / CAPTCHA / challenge markers; while pending,
// nothing auto-submits. The operator approves/declines per site. The
// payload carries marker LABELS only (kind/why), never a secret value.
export interface PendingApproval {
  surface: "auto_submit" | "post_reveal";
  key: string; // approval_key (auto_submit) | normalized action_url (post_reveal)
  kind: string; // marker label, e.g. "cf-turnstile"
  why: string; // short human reason
  at: string; // ISO timestamp the candidate was last surfaced
}

export interface PendingApprovalsResponse {
  ok: boolean;
  pending: PendingApproval[];
  count: number;
}

export interface ApprovalDecisionResult {
  ok: boolean;
  decision?: "approve" | "decline";
  key?: string; // echoed for the auto_submit surface
  action_url?: string; // echoed for the post_reveal surface
  error?: string;
}

export interface ResolveResponse {
  ok: boolean;
  action?: string;
  detail?: string;
  url?: string;
  error?: string;
}

// ── U4 types: site editor / Add Site wizard ──────────────────────────

export interface ValidationResult {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

// Minimal config shape the Add Site wizard collects. The backend
// accepts the full CFG_FIELDS surface; the wizard only collects the
// fields that are required + the ones most likely to be wrong on
// first creation (per the v3.63.10 trigger bug class).
export interface SiteConfigDraft {
  name: string;
  start_url?: string;
  login_url?: string;
  username?: string;
  password?: string;
  cookie_file?: string;
  download_dir?: string;
  filename_template?: string;
  // Optional login-form CSS selectors so the quick-add wizard
  // can self-drive login on a host with no curated login template. Backend
  // accepts these as cfg keys (CFG_FIELDS + app.py do_login).
  user_field?: string;
  pass_field?: string;
  submit_btn?: string;
  /** Existing runner settings surfaced by row 363's GUI workflow. */
  quality_preference?: string;
  min_resolution?: number;
  log_network?: boolean;
  login_trigger?: string;
  // ROW 374: seed the authenticated library crawler while creating a site.
  // Zero newest_n is the explicit whole-library mode; the UI defaults to 50.
  crawler_listing_url?: string;
  crawler_newest_n?: number;
}

/** Canonical app_kernel.DEFAULTS values used before a site record exists. */
export const DEFAULT_SITE_QUALITY_PREFERENCE = "4320,3160,2880,2160,1440,1080,720";
export const DEFAULT_SITE_MIN_RESOLUTION = 1080;

// ── U5 types: Queue tab ──────────────────────────────────────────────

export interface JobLogEvent {
  ts: number;
  kind: string;
  message: string;
}

export interface JobLogResponse {
  ok: boolean;
  events: JobLogEvent[];
  current: {
    status: string;
    message: string;
    filename: string;
  };
  truncated: boolean;
  error?: string;
}

// U5 — log-diff side-by-side. Returned by /api/queue/v2/job_log_diff.
// Each side mirrors the single job_log payload's events + current,
// plus the ok/error pair so a 'b' that doesn't exist anymore doesn't
// nuke the response.
export interface JobLogDiffSide {
  ok: boolean;
  error: string | null;
  site_id: string;
  url: string;
  events: JobLogEvent[];
  current: {
    status: string;
    message: string;
    filename: string;
  } | null;
}

export interface JobLogDiffResponse {
  ok: boolean;
  a: JobLogDiffSide;
  b: JobLogDiffSide;
  diff: string[];
}

export interface QueueWaitingEntry {
  site_id: string;
  site_name: string;
  avatar_color: string;
  url: string;
  filename: string;
  priority: number;
  queued_ts: number;
}

// Tightened QueueV2: the U2 version typed `waiting` as `unknown[]`
// because U2 didn't render waiting jobs. U5 does, so refine it.
// F1.6: per-site queue-drain summary. drain_eta_seconds is null when the
// site has no recent completion rate yet (SPA renders "—").
export interface QueueSiteDrain {
  site_id: string;
  site_name: string;
  avatar_color: string;
  waiting_count: number;
  running_count: number;
  drain_eta_seconds: number | null;
}

export interface QueueV2Full {
  ok: boolean;
  running: QueueRunningEntry[];
  waiting: QueueWaitingEntry[];
  waiting_truncated_count: number;
  done_today_count: number;
  per_site?: QueueSiteDrain[];
  ts: number;
}

// SSE event shape from /api/stream — the only event U5 consumes.
export interface DownloadProgressEvent {
  site_id: string;
  url: string;
  got: number;
  total: number;
  pct: number;
  chunks?: number;
}

// ── U6 types: Activity tab ───────────────────────────────────────────

export type ActivityWindow = "24h" | "7d" | "30d" | "all";

export interface ActivityRow {
  id: number;
  ts: string;
  site_id: string;
  site_name: string;
  avatar_color: string;
  filename: string;
  status: string;
  file_size: number | null;
  message: string;
}

export interface ActivityV2 {
  ok: boolean;
  window: ActivityWindow;
  q: string;
  count_current_period: number;
  count_prev_period: number | null;
  delta_abs: number | null;
  delta_pct: number | null;
  items: ActivityRow[];
}

// ── U7 types: Settings ───────────────────────────────────────────────

// v3.64.x — themes restoration. The legacy `/` UI had 31 named themes
// that didn't survive the D3 redesign. This re-export keeps imports of
// `ThemeMode` working while the canonical definition (which now spans
// 31 names + "system") lives in @/lib/themes.
export type { ThemeMode } from "@/lib/themes";

// Subset of /api/global_config the Settings page reads/writes. The
// backend accepts/returns many more fields (AI, watch folder, etc.);
// the SPA only touches the ones it surfaces a UI for. Unknown fields
// round-trip untouched because the SPA POSTs only what it owns.
export interface GlobalConfigSubset {
  global_max_concurrent?: number;
  // v3.66.312 (CLI->GUI parity Phase 4.2): browser backend group. browser_backend
  // is the canonical select (cloakbrowser|playwright); covers the legacy
  // BD_BROWSER_BACKEND/BD_USE_CLOAK/BD_USE_CLOAKBROWSER env vars. novnc_url backs
  // BD_NOVNC_URL. Both honored when the matching env var is unset (env = deploy override).
  browser_backend?: string;
  novnc_url?: string;
  // v3.66.313 (CLI->GUI parity Phase 4.2): Challenge-handling honeypot tunables.
  // honeypot_score_threshold is a string (float in (0,1] or empty=off; read site
  // float()-parses it). honeypot_per_site toggles per-site threshold learning.
  honeypot_score_threshold?: string;
  honeypot_per_site?: boolean;
  // v3.66.314 (CLI->GUI parity Phase 4.2, guard cut): challenge_wait_s backs
  // BD_CHALLENGE_WAIT_S (capture_session guard). String of seconds; read site
  // float()-parses it. "0" disables the wait; empty falls back to the env seed
  // / default "20". A Settings write takes effect on the next capture.
  challenge_wait_s?: string;
  // v3.66.315 (CLI->GUI parity Phase 4.2): Advanced env tranche. Numerics are
  // strings ("" = unset -> env/default; read site casts). Redaction greys loosen
  // retention above the unconditional floor (danger_note in the UI).
  auth_throttle?: boolean;
  // v3.66.319 (CLI->GUI parity Phase 4.3a): cross-site selector reuse (opt-in).
  // Store > env (BD_CROSS_SITE_SELECTORS) seed > default.
  cross_site_selectors?: boolean;
  // v3.66.336: operator switch for the AI-6 struct_embed tie-breaker (opt-in,
  // default OFF -> byte-identical recognition). When ON the WACZ->template build
  // lets the structural verdict re-rank ONLY a genuine 2-way tie. Store > default.
  player_struct_tiebreak?: boolean;
  // v3.66.319 (Phase 4.3b): autonomy final-apply switch (DANGER). Tri-state
  // string ""=default(off via env) / "1"=on / "0"=off. Store > env > default.
  autonomy_enabled?: string;
  auth_throttle_free?: string;
  auth_throttle_base?: string;
  auth_throttle_max?: string;
  redact_emails?: string;
  redact_extra_headers?: string;
  redact_network_urls?: string;
  secrets_audit?: string;
  secrets_audit_sink?: string;
  secrets_audit_file?: string;
  secrets_audit_max_bytes?: string;
  held_out_stale_days?: string;
  lib_reconcile_missing_days?: string;
  fleet_nodes?: string;
  youtube_cipher?: string;
  // v3.66.316 (CLI->GUI parity Phase 4.2, guard cut): hud_overlay backs the
  // capture HUD (guard #3); lint_kb_allow backs the build-time KB-lint allow list
  // (guard #7). Both carry the guard danger_note in the UI.
  hud_overlay?: boolean;
  lint_kb_allow?: string;
  // v3.66.317 (CLI->GUI parity, FINAL env tranche): the 4 deferred + 3 previously-
  // excluded vars, promoted to full live-writable controls per operator directive.
  // auth_token / bd_token are MASKED on read ("<configured>"|"") and preserve-on-
  // blank on write — never the raw value. dev_mode/cockpit_shell are "" | "1" | "0"
  // (unset|on|off). test_mode is an advisory flag only (no behavior effect).
  auth_token?: string;
  bd_token?: string;
  // v3.66.336: the AI-assist provider API key (cloud backends). MASKED on read
  // ("<configured>"|"") and preserve-on-blank on write (the "<configured>"
  // sentinel keeps the stored key) — the raw value never round-trips to the UI.
  ai_api_key?: string;
  dev_mode?: string;
  test_mode?: boolean;
  cockpit_shell?: string;
  cockpit_tasks?: string;
  framework_reports?: string;
  watch_folder?: string;
  watch_interval_sec?: number;
  watch_archive?: boolean;
  log_level?: string;
  // v3.66.323 (Phase 4 gap A1): global_config keys that round-trip but had no
  // SPA control until now. ui_logging_level = basic|verbose|extreme;
  // template_auto_detect_mode = static|detect|detect_then_static|deep.
  ui_logging_level?: string;
  template_auto_detect_mode?: string;
  // v3.66.324 (Phase 4 gap A3 / GAP2): AI-assist config. ai_provider in
  // ollama|claude|openai|gemini; ai_endpoint a provider URL; the two model
  // names free-form. The secret ai_api_key stays out of this subset (handled
  // via the masking sentinel path, not exposed here).
  ai_enabled?: boolean;
  ai_provider?: string;
  ai_endpoint?: string;
  ai_model_vision?: string;
  ai_model_text?: string;
  path_allowlist?: string[];
  // v3.64.2: session keep-alive timing knobs. All three in MINUTES,
  // [1, 360]. Defaults applied in session_keeper.py are 30/30/30
  // (raised from 10/5/30 once the persistent-Chromium fix landed in
  // the same release). Setting these in Settings takes effect on the
  // next keeper iteration — no restart required.
  session_keep_alive_lead_time_min?: number;
  session_keep_alive_fetch_interval_min?: number;
  session_keep_alive_navigate_interval_min?: number;
  // v3.66.139: prefer CloakBrowser for keep-alive browser launches
  // (canonical default). false → vanilla Playwright. Read by
  // cloak.use_cloak() as the global default; per-site config and the
  // BD_*_CLOAK env vars override it.
  session_keeper_use_cloakbrowser?: boolean;
  // v3.64.3: opt-in cross-device sync of the completion-sound toggle.
  // Default is sync-off (the legacy per-device localStorage path). When
  // a device sets sound_sync_enabled=true, both devices read/write
  // sound_on_complete server-side instead. See useSyncedSoundPref hook.
  sound_sync_enabled?: boolean;
  sound_on_complete?: boolean;
  // v3.43.31: per-domain rate-limit config. Two global scalars (0 =
  // uncapped) + a per-domain override map. Live changes applied
  // immediately via rate_limit.configure_from_app_config().
  rate_limit_global_concurrent?: number;
  rate_limit_global_per_sec?: number;
  rate_limit_domain_overrides?: Record<string, { max_concurrent: number; max_per_sec: number }>;
  // v3.66.306 (CLI->GUI parity Phase 4.2a): queue-housekeeping tunables, promoted
  // from BD_QUEUE_HK_* env vars into global_config. Setting these takes effect on
  // the next housekeeping run — no restart. Getters clamp to sane ranges.
  queue_hk_gc_age_days?: number;
  queue_hk_abandon?: boolean;
  queue_hk_max_retries?: number;
  queue_hk_stale_hours?: number;
  // v3.66.308 (CLI->GUI parity Phase 4.2): guard-backed Capture tunables,
  // promoted from BD_CAPTURE_* / BD_DOM_HONEYPOT_FILTER / BD_REDACT_DOM_URLS
  // env vars into global_config. Take effect on the next capture — no restart.
  // capture_raw is the redaction-disable override (value-honored, not coerced);
  // the controls for the irrecoverable ones surface a disclaimer.
  capture_bodies?: boolean;
  capture_wait_until?: string;
  dom_honeypot_filter?: string;
  redact_dom_urls?: string;
  capture_raw?: boolean;
  // v3.66.309 (Phase 4.2): slow-query diagnostics (call-time getters in db.py).
  slow_query_log?: boolean;
  slow_query_ms?: number;
  // v3.66.503 (Bucket 1): HLS / Live-recorder / Captcha-relay tunables, promoted
  // from import-time module constants to full live controls (call-time getters,
  // runtime_flags.num: store > env seed > default). Stored as strings ("" = unset
  // -> env/default; read site casts) to mirror challenge_wait_s. A Settings write
  // takes effect on the next download / poll / capture without a restart.
  hls_input_timeout_us?: string;
  hls_max_runtime_s?: string;
  hls_progress_poll_s?: string;
  live_poll_interval_s?: string;
  live_disconnect_tolerance_s?: string;
  live_max_active_recordings?: string;
  live_launch_timeout_s?: string;
  captcha_pending_timeout_s?: string;
  // v3.66.720 (Cut 9): OIDC / SSO config -- promoted to live controls in the Security
  // section. All are global_config keys; the secret is write-only (SecretField).
  oidc_enabled?: boolean;
  oidc_issuer?: string;
  oidc_client_id?: string;
  oidc_client_secret?: string;
  oidc_redirect_uri?: string;
  oidc_scopes?: string;
  captcha_push_dedupe_s?: string;
  // v3.66.758 (MOD-1 A-4): how a captcha solve session presents (visible|remote).
  captcha_takeover_mode?: string;
  // v3.66.759 (MOD-1 A-5a): remote-takeover admission controls.
  captcha_takeover_enabled?: boolean;
  captcha_takeover_max_concurrent?: string;
  // v3.66.760 (MOD-1 A-5b): idle-timeout for a remote solve session.
  captcha_takeover_idle_timeout_s?: string;
  // v3.66.811 (MOD-1 Arch-B): the KasmVNC display + websocket port the takeover
  // browser renders on (str; int-coerced backend-side). Declared 808.
  captcha_vnc_display?: string;
  captcha_vnc_websocket_port?: string;
  // v3.66.811 (MOD-1 C-7): egress-isolation toggle. The GUI control sets the bool
  // form; the advanced {enabled, egress:{...}} dict form is file-managed.
  netns_isolation?: boolean;

  // v3.66.711 (A-GUI Cut 3): the automation program's control surface. These keys
  // are DOTTED, so they must be quoted -- they are not valid TS identifiers.
  //
  // They were read by lifecycle_automation/automation_controller since they were
  // introduced but were never DECLARED in GLOBAL_CONFIG_SCHEMA, so a POST returned
  // 200 and wrote NOTHING (fixed at 709). master_off_switch is the EMERGENCY STOP
  // for all autonomous action; it dominates every other toggle here.
  "automation.master_off_switch"?: boolean;
  // L1/L2/L3 -- observe-and-flag only.
  "automation.drift_sweep_enabled"?: boolean;
  "automation.validation_gate_enabled"?: boolean;
  "automation.auto_flag_enabled"?: boolean;
  // L4/L5 -- DOWNLOAD-AFFECTING.
  "automation.auto_quarantine_enabled"?: boolean;
  "automation.auto_repair_enabled"?: boolean;
  "automation.auto_refresh_enabled"?: boolean;
  "automation.auto_promote_enabled"?: boolean;
  // A9 -- the supervised-autonomy orchestrator.
  "automation.controller_enabled"?: boolean;
  // A-DISCO (788) -- level-4 enumerate -> triage -> auto-queue.
  "automation.disco_enabled"?: boolean;
  // A4/A6/A7/A8 -- prep / restore / self-management.
  "automation.auto_onboard_enabled"?: boolean;
  "automation.auto_ci_enabled"?: boolean;
  "automation.auto_recover_enabled"?: boolean;
  "automation.auto_queue_enabled"?: boolean;
  // auto_refresh trigger modes + drift ceiling.
  "automation.auto_refresh_on_capture_enabled"?: boolean;
  "automation.auto_refresh_confirm_enabled"?: boolean;
  "automation.auto_refresh_max_drift"?: number;
  // Capture-time scrub (redaction) -- ships ON.
  "automation.scrub_on_capture_enabled"?: boolean;
  "automation.scrub_on_capture_tool"?: string;
  // Reporting / canary.
  "automation.daily_digest_enabled"?: boolean;
  "automation.drift_repair_enabled"?: boolean;
  "automation.template_canary_enabled"?: boolean;
  // Safety net + ceilings (declared at 706-708).
  "automation.restore_rehearsal_enabled"?: boolean;
  "automation.pipeline_enabled"?: boolean;
  "automation.cycle_max_steps"?: number;
  "automation.cycle_wall_s"?: number;
  "automation.cycle_max_errors"?: number;
}

// Health v2 full surface — re-declared here so Advanced.tsx can type
// it without re-reading. Matches /api/health/v2.
export interface HealthV2 {
  ok: boolean;
  version: string;
  uptime_s: number;
  queue_depth?: number;
  active_downloads?: number;
  sites_loaded?: number;
  db_ok?: boolean;
  db_journal_mode?: string;
  degraded?: string;
  disks: Array<{
    path: string;
    free_gb: number;
    total_gb: number;
    free_pct: number;
  }>;
  ollama: {
    reachable: boolean;
    model: string | null;
    error: string | null;
  };
  last_suite: {
    available: boolean;
    mtime_ts?: number;
    size_bytes?: number;
  };
}


// U6 — site-health sparkline per Activity row. Returned by
// /api/activity/v2/site_health.
export interface SiteHealthV2 {
  ok: boolean;
  days: number;
  status: string;
  start_date: string;
  by_site: Record<string, number[]>;
}


// v3.66.144 — reviewed-template status + manual onboarding.
// GET /api/sites/<sid>/template_status
export interface TemplateSummary {
  enabled: boolean;
  host: string | null;
  selectors: string[];
  resolutions: number[];
  patterns: string[];
}

// 3e/C1: POST /api/sites/<sid>/session/reuse_onboarding response — value-free
// (profile names + item names + counts + source host; never paths/values).
export interface SessionReuseResult {
  ok: boolean;
  site: string;
  reused: boolean;
  host: string | null;
  seeded: { profile: string; items: string[]; count: number }[];
  skipped_reason: string | null;
}

export interface TemplateStatus {
  ok: boolean;
  site: string;
  url: string;
  template: TemplateSummary;
  onboarding: string | null; // "approved_template_found" | "capture_required" | null
  auto_teach_first_run: boolean;
  template_auto_detect_mode: string | null;
  label: string;
  capture_in_flight?: boolean; // CAP-CANCEL: an onboarding capture is running
  // 3c: the enabled host-level template that applies at download time when it
  // differs from the primary (login-host) resolution; null/absent otherwise.
  download_template?: TemplateSummary | null;
  lint?: TemplateLintIssue[];
  has_blocking_lint?: boolean;
}

// POST /api/sites/<sid>/template_onboard
export interface TemplateOnboardResult {
  ok: boolean;
  site: string;
  template_onboarding?: string;
  auto_teach_first_run?: boolean;
  template_auto_detect_mode?: string;
  launched: boolean;
  capture?: {
    profile_dir: string;
    wacz: string;
    draft: string;
    display: string;
  };
  error?: string;
}

// v3.66.149 — profile tools (#7 seed / #8 status)
export interface ProfileStorageItem {
  name: string;
  present: boolean;
  bytes: number;
  mtime: string | null;
}
export interface ProfileStorageEntry {
  profile: string;
  kind: string; // "manual" | "main" | "worker" | "keeper"
  present: boolean;
  items: ProfileStorageItem[];
}
export interface ProfileStorageStatus {
  ok: boolean;
  site: string;
  profiles_root: string;
  present: boolean;
  sites: { site: string; profiles: ProfileStorageEntry[] }[];
}
export interface ProfileSeedEntry {
  profile: string;
  items: string[];
  count: number;
  backup_count?: number;
  last_backup?: string | null;
}
export interface ProfileSeedResult {
  ok: boolean;
  site: string;
  source: string | null;
  seeded: ProfileSeedEntry[];
  skipped: Record<string, string>;
  errors: Record<string, string>;
  skipped_reason: string | null;
  note: string;
}

// v3.66.149 — template manager (#10)
export interface TemplateLintIssue {
  level: string; // "error" | "warn"
  code: string;
  selector: string;
  role: string;
  message: string;
}
export interface TemplateManagerEntry {
  file: string;
  host: string | null;
  status: string | null;
  enabled: boolean;
  selectors: string[];
  resolutions: number[];
  network_patterns: string[];
  lint_warnings: TemplateLintIssue[];
  has_blocking_lint: boolean;
  // A6-1: review-only derived API endpoints (base + named relative paths,
  // secret-free). Present only on drafts where the builder derived one. null
  // otherwise. The operator may accept it at promotion (ungates build_api_url).
  api_candidate?: { base: string; endpoints: string[] } | null;
  ok?: boolean;
  error?: string;
}
export interface TemplateManagerList {
  ok: boolean;
  reviewed: TemplateManagerEntry[];
  drafts: TemplateManagerEntry[];
  reviewed_dir: string;
  drafts_dir: string;
}
export interface TemplateActionResult {
  ok: boolean;
  promoted?: string;
  disabled?: string;
  enabled?: boolean;
  from?: string;
  api_accepted?: boolean;
  error?: string;
}

// v3.66.149 — dry-run inspector (#1 candidates / #5 template test)
export interface InspectCandidateRow {
  selector: string;
  text: string;
  url: string;
  href: string;
  data_href: string;
  data_url: string;
  data_src: string;
  score: number | null;
  size: number;
  host: string;
  signals: string[];
  kind: string; // "download" | "trigger" | "rejected"
  accepted: boolean;
  reason: string;
}
export interface InspectResult {
  ok: boolean;
  error?: string;
  page_url: string;
  page_host: string;
  winner: InspectCandidateRow | null;
  candidates: InspectCandidateRow[];
  n_candidates: number;
  n_accepted: number;
  n_rejected: number;
  safe_candidate_available: boolean;
}
export interface SelectorHit {
  selector: string;
  hits?: number | string;
}
export interface TemplateDryRunResult {
  ok: boolean;
  error?: string;
  url: string;
  host: string;
  template_matched: boolean;
  template: TemplateSummary | null;
  network_patterns: string[];
  lint_warnings: TemplateLintIssue[];
  has_blocking_lint: boolean;
  selector_hit_counts: SelectorHit[];
  candidate_classification: InspectResult | null;
  safe_candidate_selected: boolean;
  note?: string;
}

// GUI parity (177) — Library actions tier. Read shapes are intentionally loose
// (server rows carry varying optional fields); writes return a common OkResult.
export interface OkResult {
  ok: boolean;
  error?: string;
  [k: string]: unknown;
}

export interface LibraryItem {
  id: number | string;
  title?: string;
  name?: string;
  path?: string;
  rating?: number | null;
  watched?: boolean;
  tags?: string[];
  [k: string]: unknown;
}

export interface LibraryBrowse {
  ok: boolean;
  rows: LibraryItem[];
  count?: number;
  next_cursor?: string | null;
}

export interface LibraryTag {
  id: number | string;
  name?: string;
  tag?: string;
  [k: string]: unknown;
}

export interface LibraryTagList {
  ok: boolean;
  tags: LibraryTag[];
}

export interface LibraryScanStatus {
  ok: boolean;
  scan?: {
    state?: string;
    status?: string;
    running?: boolean;
    [k: string]: unknown;
  };
}

export interface SavedView {
  id: number | string;
  name?: string;
  title?: string;
  kind?: string;
  [k: string]: unknown;
}

export interface SavedViewsList {
  views: SavedView[];
  kinds?: string[];
}

export interface ShareToken {
  id?: number | string;
  token_id?: number | string;
  token?: string;
  label?: string;
  scope?: string;
  [k: string]: unknown;
}

export interface SharesList {
  tokens: ShareToken[];
  known_scopes?: string[];
}

export type TagEntry = string | { tag?: string; name?: string; count?: number; [k: string]: unknown };

export interface TagsAll {
  tags: TagEntry[];
}

// ── T1 read-only dashboard tranche (v3.66.205) ──────────────────────
// Types for the 13 legacy-only read endpoints ported into the SPA
// `/dashboard` route. Permissive index signatures: the backend payloads
// are large/evolving and the dashboard renders defensively, so only the
// fields the UI reads are pinned; the rest stay open.

export interface DashboardSummary {
  totals?: {
    running?: number; pending?: number; done?: number;
    failed?: number; needs_review?: number; stopped?: number;
  };
  active_workers?: number;
  today_done?: number;
  today_failed?: number;
  today_review?: number;
  bytes_per_sec_total?: number;
  expiring_cookies_sites?: unknown[];
  rate_limited_sites?: unknown[];
  low_disk_sites?: unknown[];
  disk_aggregate?: Array<[string, number, number]>;
  [k: string]: unknown;
}

export interface StatsSnapshot {
  [k: string]: unknown;
}

export interface StatsBandwidth {
  series?: Array<{ t?: number | string; bytes?: number; [k: string]: unknown }>;
  [k: string]: unknown;
}

export interface StatsTimeline {
  points?: Array<{ t?: number | string; [k: string]: unknown }>;
  [k: string]: unknown;
}

export interface HourlyStats {
  hours?: Array<{ hour?: number | string; count?: number; [k: string]: unknown }>;
  [k: string]: unknown;
}

export interface CapacitySnapshot {
  disks?: Array<{ path?: string; free_gb?: number; total_gb?: number; [k: string]: unknown }>;
  [k: string]: unknown;
}

// /api/status is a per-site runner-status map: { <site_id>: {name, …} }.
export type StatusSnapshot = Record<
  string,
  { name?: string; disk_free_gb?: number; [k: string]: unknown }
>;

// /api/session_status → {keepers:[{site_id, state, …}], connected, …}.
// `state === "connected"` is the logged-in signal.
export interface SessionKeeper {
  site_id?: string;
  account_idx?: number;
  state?: string;
  session_seconds_remaining?: number | null;
  [k: string]: unknown;
}

export interface SessionStatus {
  keepers?: SessionKeeper[];
  connected?: number;
  total_active?: number;
  [k: string]: unknown;
}

export interface HealthChecklistItem {
  name?: string;
  status?: "ok" | "warn" | "fail" | string;
  severity?: number;
  message?: string;
  duration_ms?: number;
  [k: string]: unknown;
}

export interface HealthChecklist {
  overall_status?: "ok" | "warn" | "fail" | string;
  summary?: { ok?: number; warn?: number; fail?: number };
  check_count?: number;
  checks?: HealthChecklistItem[];
  [k: string]: unknown;
}

export interface WidgetsAllConfig {
  global?: unknown;
  per_site?: Record<string, unknown>;
  catalog?: unknown[];
  [k: string]: unknown;
}

export interface WeatherSnapshot {
  ok?: boolean;
  summary?: string;
  temp?: number;
  [k: string]: unknown;
}

// /api/changelog is the SITE-BEHAVIOR changelog (why did a site break this
// week?), not app release notes: {sites:[…], ts}, most-broken first.
export interface SiteChangeEntry {
  severity?: string;
  kind?: string;
  when?: number;
  message?: string;
  [k: string]: unknown;
}

export interface SiteChangelog {
  site_id?: string;
  headline_severity?: "alert" | "warn" | "info" | "ok" | string;
  entries?: SiteChangeEntry[];
  counts?: { alert?: number; warn?: number; info?: number };
  [k: string]: unknown;
}

export interface ChangelogResponse {
  sites?: SiteChangelog[];
  ts?: number;
  [k: string]: unknown;
}

export interface RouteUrlsResult {
  results?: Array<{ url?: string; site_id?: string | null; matched?: boolean; [k: string]: unknown }>;
  [k: string]: unknown;
}

// ── T2 history/logs/search (v3.66.206) ──────────────────────────────
// Shapes verified against the live handlers (bulk_downloader/app.py +
// db.py + saved_searches.py + ui_events.py) per the T1 lesson: never
// guess a field name behind an index signature.

// /api/history returns a BARE ARRAY of history rows (db_search), not a
// wrapper object. Columns from the history table DDL.
export interface HistoryRow {
  id?: number;
  site_id?: string;
  site_name?: string;
  url?: string;
  status?: string;
  title?: string;
  title_source?: string;
  filename?: string;
  file_size?: number;
  message?: string;
  screenshot?: string;
  honeypot_score?: number | null;
  ts?: string; // ISO "YYYY-MM-DDTHH:MM:SS"
  [k: string]: unknown;
}

// F4.4 (v3.66.219): /api/history?cursor=… (or ?paginate=1) returns the
// {rows, next_cursor} envelope (db_search_cursor). The bare-array form
// (no cursor/paginate param) is unchanged — see the note above HistoryRow.
export interface HistoryPage {
  rows: HistoryRow[];
  next_cursor: number | null;
}

// /api/session_history → {ok, events:[…]} (session_history table rows).
export interface SessionHistoryEvent {
  id?: number;
  site_id?: string;
  account_idx?: number;
  event?: string;
  message?: string;
  ts?: string;
  [k: string]: unknown;
}
export interface SessionHistoryResponse {
  ok?: boolean;
  events?: SessionHistoryEvent[];
  [k: string]: unknown;
}

// /api/events_all → {ok, events:[…], cursor:{sid: last_seq}} — merged
// cross-site runner events, JSON cursor for incremental fetch.
export interface RunnerEvent {
  ts?: string;
  kind?: string;
  message?: string;
  site_id?: string;
  site_name?: string;
  seq?: number;
  [k: string]: unknown;
}
export interface EventsAllResponse {
  ok?: boolean;
  events?: RunnerEvent[];
  cursor?: Record<string, number>;
  [k: string]: unknown;
}

// /api/logs/tail → {ok, lines:[…], total_lines_returned, file_size,
// current_level} (windowed read of the rotating app log).
export interface LogsTailResponse {
  ok?: boolean;
  lines?: string[];
  total_lines_returned?: number;
  file_size?: number;
  current_level?: string;
  note?: string;
  error?: string;
  [k: string]: unknown;
}

// /api/logs/clear → {ok, freed_bytes, archives_removed}.
export interface LogsClearResult {
  ok?: boolean;
  freed_bytes?: number;
  archives_removed?: number;
  error?: string;
  [k: string]: unknown;
}

// /api/search → {results, count, query}; rows are history rows plus
// snippet_url / snippet_filename / snippet_message carrying <mark>
// highlights (rendered as TEXT in the SPA — never via innerHTML).
export interface SearchResultRow extends HistoryRow {
  snippet_url?: string;
  snippet_filename?: string;
  snippet_message?: string;
}
export interface SearchResponse {
  results?: SearchResultRow[];
  count?: number;
  query?: string;
  error?: string;
  [k: string]: unknown;
}

// /api/saved_searches → {searches:[…]} (saved_searches table rows).
export interface SavedSearch {
  id?: number;
  name?: string;
  query?: string;
  site_id?: string;
  status?: string;
  schedule?: string;
  notify_via?: string;
  enabled?: number | boolean;
  // F3.1 action lane: "notify" (default; apprise on new matches) or
  // "enqueue" (feed new matches into the normal download pipeline).
  action?: string;
  daily_cap?: number;
  created_at?: string | number;
  [k: string]: unknown;
}
export interface SavedSearchesList {
  searches?: SavedSearch[];
  error?: string;
  [k: string]: unknown;
}
export interface SavedSearchAddResult {
  ok?: boolean;
  id?: number;
  error?: string;
  [k: string]: unknown;
}
export interface SavedSearchRunResult {
  ok?: boolean;
  [k: string]: unknown;
}

// /api/saved_searches/digest → {hours_back, searches:[{id,name,query,
// matches,…}]} — match counts per enabled search since the cutoff.
export interface SavedSearchDigestEntry {
  id?: number;
  name?: string;
  query?: string;
  matches?: number;
  [k: string]: unknown;
}
export interface SavedSearchDigest {
  hours_back?: number;
  searches?: SavedSearchDigestEntry[];
  error?: string;
  [k: string]: unknown;
}

// POST /api/ui_events → {ok, accepted, dropped} (server-side tier gating).
export interface UiEventsIngestResult {
  ok?: boolean;
  accepted?: number;
  dropped?: number;
  error?: string;
  [k: string]: unknown;
}

// ── T3 library/tags tranche types (v3.66.207) — handler-correct ──────

// POST /api/library/audit → library_final.audit(): counts + capped samples.
// Handler-correct as of v3.66.822 (verified by calling audit()): orphans,
// missing, duplicate_groups and size_drift are COUNTS (ints), not lists; the
// lists are the sample_* fields. Earlier revisions of this interface declared
// arrays and four keys (total_history, total_disk_files, missing_nfo,
// missing_thumbs) that audit() has never returned. (error key on 4xx/5xx.)
export interface LibraryAuditResult {
  orphans?: number;
  missing?: number;
  duplicate_groups?: number;
  duplicate_reclaimable_gb?: number;
  size_drift?: number;
  orphan_size_gb?: number;
  // `missing` and `size_drift` are windowed counts, and each window has its
  // own WHERE clause -- they saturate independently. True means that count is
  // a FLOOR: read it as "audit_row_limit or more", not as a total.
  missing_saturated?: boolean;
  size_drift_saturated?: boolean;
  audit_row_limit?: number;
  sample_orphans?: unknown[];
  sample_missing?: unknown[];
  sample_duplicates?: unknown[];
  sample_size_drift?: unknown[];
  error?: string;
  [k: string]: unknown;
}

// POST /api/library/orphans → {orphans: [...]}.
export interface LibraryOrphansResult {
  orphans?: unknown[];
  error?: string;
  [k: string]: unknown;
}

// GET /api/library/stats → {ok, stats}.
export interface LibraryStatsResult {
  ok?: boolean;
  stats?: Record<string, unknown>;
  error?: string;
  [k: string]: unknown;
}

// POST /api/library/regen_nfos → library_final.regen_nfos_from_history()
// result dict (counts vary by dry_run).
export interface RegenNfosResult {
  ok?: boolean;
  written?: number;
  skipped?: number;
  dry_run?: boolean;
  error?: string;
  [k: string]: unknown;
}

// POST /api/tags/for_many → {tags: {"<hid>": [tag, …]}}.
export interface TagsForManyResult {
  tags?: Record<string, string[]>;
  error?: string;
  [k: string]: unknown;
}

// POST /api/tags/add|remove|rename → tags module result ({ok, …}).
export interface TagOpResult {
  ok?: boolean;
  error?: string;
  [k: string]: unknown;
}

// GET /api/tags/rows/{tag} → {rows: [...]}.
export interface TagRowsResult {
  rows?: Array<Record<string, unknown>>;
  error?: string;
  [k: string]: unknown;
}

// GET /api/tags/suggest/{hid} → {history_id, suggested: [tag, …]}.
export interface TagSuggestResult {
  history_id?: number;
  suggested?: string[];
  error?: string;
  [k: string]: unknown;
}

// GET /api/scene_score/bottom → {scenes: [...]}.
export interface SceneScoreEntry {
  history_id?: number;
  path?: string;
  filename?: string;
  score?: number;
  site_id?: string;
  [k: string]: unknown;
}
export interface SceneScoreList {
  scenes?: SceneScoreEntry[];
  error?: string;
  [k: string]: unknown;
}

// POST /api/storage_rebalance/inventory → {inventory: [...]}.
export interface StorageInventoryResult {
  inventory?: Array<Record<string, unknown>>;
  error?: string;
  [k: string]: unknown;
}

// ── T4 operational-controls tranche types (v3.66.207) ────────────────

// POST /api/runners/pause_all|resume_all → {ok, paused|resumed, failures}.
export interface RunnersAllResult {
  ok?: boolean;
  paused?: number;
  resumed?: number;
  failures?: Array<{ site_id?: string; error?: string }>;
  error?: string;
  [k: string]: unknown;
}

// POST /api/concurrent/{sid} → {ok, max_concurrent}.
export interface ConcurrentResult {
  ok?: boolean;
  max_concurrent?: number;
  error?: string;
  [k: string]: unknown;
}

// GET /api/rate_limit/status → limiter.get_status() snapshot.
export interface RateLimitStatus {
  global?: Record<string, unknown>;
  domains?: Record<string, unknown>;
  error?: string;
  [k: string]: unknown;
}

// GET /api/retry_policy → {classes: {cls: {config, delays_seconds,
// total_window_seconds}}}.
export interface RetryPolicyClass {
  config?: Record<string, unknown>;
  delays_seconds?: number[];
  total_window_seconds?: number;
  [k: string]: unknown;
}
export interface RetryPolicyResult {
  classes?: Record<string, RetryPolicyClass>;
  error?: string;
  [k: string]: unknown;
}

// GET /api/crash_recovery/scan → {orphans: [{path, …}]}.
export interface CrashOrphan {
  path?: string;
  site_id?: string;
  size?: number;
  age_hours?: number;
  url?: string;
  [k: string]: unknown;
}
export interface CrashOrphansResult {
  orphans?: CrashOrphan[];
  error?: string;
  [k: string]: unknown;
}

// POST /api/crash_recovery/{delete|ignore|resume} → {ok, …}.
export interface CrashOpResult {
  ok?: boolean;
  error?: string;
  [k: string]: unknown;
}

// POST /api/file/reveal → {ok, revealed}.
export interface FileRevealResult {
  ok?: boolean;
  revealed?: string;
  error?: string;
  [k: string]: unknown;
}

// POST /api/sites/bulk_csv → {ok, created, results: [{line, name?, status,
// error?}]} — per-row outcome for the review table.
export interface BulkSiteRowResult {
  line?: number;
  name?: string;
  status?: string;
  error?: string;
  [k: string]: unknown;
}
export interface BulkSitesResult {
  ok?: boolean;
  created?: number;
  results?: BulkSiteRowResult[];
  error?: string;
  [k: string]: unknown;
}

// ── T5 (v3.66.208) governance types — handler-correct vs app.py 207 ──

// GET /api/retention/preview/<sid>
export interface RetentionCandidate {
  id?: number;
  filename?: string;
  file_path?: string;
  file_size?: number;
  reason?: string;
  [k: string]: unknown;
}
export interface RetentionPreview {
  site_id?: string;
  candidate_count?: number;
  total_bytes?: number;
  candidates?: RetentionCandidate[];
  retention_days?: number;
  retention_max_gb?: number;
  retention_keep_tagged_with?: string[];
  error?: string;
  [k: string]: unknown;
}

// POST /api/retention/apply (retention module summary; shape is the
// module's own — keep open)
export interface RetentionApplyResult {
  ok?: boolean;
  error?: string;
  dry_run?: boolean;
  preview_bound?: boolean;
  scoped_site?: string | null;
  total_candidates?: number;
  total_deleted?: number;
  total_bytes_freed?: number;
  [k: string]: unknown;
}

// GET /api/retention/audit → {audit: [...]}
export interface RetentionAuditRow {
  deleted_at?: number;
  dry_run?: boolean;
  site_id?: string;
  file_path?: string;
  reason?: string;
  [k: string]: unknown;
}
export interface RetentionAuditResult {
  audit?: RetentionAuditRow[];
  error?: string;
}

// GET /api/rights/blocklist → {blocks: [...]}
export interface RightsBlock {
  id?: number;
  kind?: string;
  pattern?: string;
  hash_hex?: string;
  reason?: string;
  added_by?: string;
  added_at?: number;
  [k: string]: unknown;
}
export interface RightsBlocklist {
  blocks?: RightsBlock[];
  error?: string;
}

// GET /api/rights/audit → {entries: [...]}
export interface RightsAuditResult {
  entries?: Record<string, unknown>[];
  error?: string;
}

// GET /api/scheduled_exports/list → {schedules: [...]}
export interface SchedExport {
  id?: number;
  label?: string;
  format?: string;
  destination?: string;
  cadence_hours?: number;
  retention_count?: number;
  last_run_at?: number;
  last_status?: string;
  [k: string]: unknown;
}
export interface SchedExportsList {
  schedules?: SchedExport[];
  error?: string;
}

// POST /api/scheduled_exports/add → {ok, id} | 400 {ok:false, error}
export interface SchedExportAddResult {
  ok?: boolean;
  id?: number;
  error?: string;
}

// POST /api/scheduled_exports/run_now → run_due_exports summary (open)
export interface SchedExportRunNowResult {
  ok?: boolean;
  error?: string;
  [k: string]: unknown;
}

// ── T6/VPN (v3.66.208) — VPN row-U types ──────────────────────────────

// GET /api/vpn/settings → {ok, settings:{...}}; PUT echoes the same.
export interface VpnGlobalSettings {
  leak_test_interval_s?: number;
  kill_switch_auto_recover?: boolean;
  system_killswitch_default?: boolean;
  [k: string]: unknown;
}
export interface VpnSettingsResult {
  ok?: boolean;
  settings?: VpnGlobalSettings;
  error?: string;
}

// GET /api/vpn/kill_switch/state → {ok, states:[...], auto_recover}
export interface VpnKillState {
  tunnel_id?: string;
  killed?: boolean;
  reason?: string;
  killed_at?: number;
  [k: string]: unknown;
}
export interface VpnKillSwitchState {
  ok?: boolean;
  states?: VpnKillState[];
  auto_recover?: boolean;
  error?: string;
}

// GET /api/vpn/providers → {ok, providers:[{id, name, credentials_schema?...}]}
export interface VpnProviderInfo {
  id?: string;
  name?: string;
  credentials_schema?: { key: string; label?: string; secret?: boolean }[];
  [k: string]: unknown;
}
export interface VpnProvidersResult {
  ok?: boolean;
  providers?: VpnProviderInfo[];
  error?: string;
}

// POST /api/vpn/providers/<pid>/test_credentials → {ok, valid, message}
export interface VpnCredTestResult {
  ok?: boolean;
  valid?: boolean;
  message?: string;
  error?: string;
}

// POST /api/vpn/providers/<pid>/locations → {ok, locations:[...]}
export interface VpnLocationsResult {
  ok?: boolean;
  locations?: (string | Record<string, unknown>)[];
  error?: string;
}

// v3.66.768 -- VPN diagnostics reads (6A dark-cluster wiring).
// GET /api/vpn/stats → {report:{...}}
export interface VpnStatsResult {
  report?: Record<string, unknown>;
  error?: string;
}
// GET /api/vpn/blacklist → {blacklist:[...]}
export interface VpnBlacklistResult {
  blacklist?: (string | Record<string, unknown>)[];
  error?: string;
}
// GET /api/vpn/backends/availability → {ok, wireguard:{available,reason}, openvpn:{...}}
export interface VpnBackendAvail {
  available?: boolean;
  reason?: string;
}
export interface VpnBackendsAvailability {
  ok?: boolean;
  wireguard?: VpnBackendAvail;
  openvpn?: VpnBackendAvail;
  error?: string;
}
// GET /api/vpn/system_killswitch/available → {ok, available, reason}  (v3.66.770)
export interface VpnSysKsAvailResult {
  ok?: boolean;
  available?: boolean;
  reason?: string;
  error?: string;
}
// GET /api/vpn/best_for/<sid> → {vpn_profile:...} | {vpn_profile:null}
export interface VpnBestForResult {
  vpn_profile?: string | null;
  [k: string]: unknown;
}
// POST /api/vpn/auto_blacklist → {new_blacklisted:[...]}
export interface VpnAutoBlacklistResult {
  new_blacklisted?: (string | Record<string, unknown>)[];
  error?: string;
}

// v3.66.769 -- VPN leak-test + single-tunnel detail (6B dark-cluster wiring).
// GET /api/vpn/tunnels/<id> → {ok, tunnel:{...}}
export interface VpnTunnelDetailResult {
  ok?: boolean;
  tunnel?: Record<string, unknown>;
  error?: string;
}
// GET /api/vpn/tunnels/<id>/leak_test/latest → {ok, result:{...}|null}
// POST /api/vpn/tunnels/<id>/leak_test/run → {ok, result:{...}}
export interface VpnLeakResult {
  ok?: boolean;
  result?: Record<string, unknown> | null;
  error?: string;
}
// GET /api/vpn/tunnels/<id>/leak_test/history → {ok, history:[...]}
export interface VpnLeakHistoryResult {
  ok?: boolean;
  history?: Record<string, unknown>[];
  error?: string;
}

// ── T7 notifications tranche (v3.66.210) ────────────────────────────
// notify(apprise) · tg(bot) · alerts. Secrets are WRITE-ONLY: apprise
// URLs and the tg bot token are accepted on POST but NEVER echoed on
// GET (the GET surfaces a *_set flag + count). Mirrors the existing
// tg_bot_token_set masking — closes the PREP_AUDIT §8 raw-leak finding.

export interface AppriseSettings {
  notify_apprise_enabled?: boolean;
  // write-only: GET returns only the set-flag + count, never the URLs.
  notify_apprise_urls_set?: boolean;
  notify_apprise_urls_count?: number;
  [k: string]: unknown;
}
export interface AppriseSettingsResponse {
  ok?: boolean;
  available?: boolean;
  settings?: AppriseSettings;
}
// POST body for apprise settings — notify_apprise_urls is write-only.
export interface AppriseSettingsPatch {
  notify_apprise_enabled?: boolean;
  notify_apprise_urls?: string; // newline-separated; write-only
  [k: string]: unknown;
}
export interface AppriseValidateResult {
  ok?: boolean;
  available?: boolean;
  results?: { url?: string; ok?: boolean; schema?: string; error?: string }[];
  error?: string;
}
export interface AppriseTestResult {
  ok?: boolean;
  available?: boolean;
  sent?: number;
  failed?: number;
  error?: string;
}
export interface TgStatusResponse {
  ok?: boolean;
  available?: boolean;
  enabled?: boolean;
  running?: boolean;
  last_error?: string | null;
  updates_received?: number;
  commands_executed?: number;
  allowlist_size?: number;
  bot_username?: string | null;
}
export interface TgSettings {
  tg_bot_enabled?: boolean;
  tg_bot_token_set?: boolean; // write-only: never the token itself
  tg_bot_allowlist?: string;
}
export interface TgSettingsResponse {
  ok?: boolean;
  available?: boolean;
  settings?: TgSettings;
}
export interface TgSettingsPatch {
  tg_bot_enabled?: boolean;
  tg_bot_token?: string; // write-only
  tg_bot_token_clear?: boolean;
  tg_bot_allowlist?: string;
}
export interface TgTestResult {
  ok?: boolean;
  sent?: number;
  error?: string;
}
export interface ActiveAlert {
  id?: string;
  rule?: string;
  severity?: string;
  message?: string;
  ts?: number;
  [k: string]: unknown;
}
export interface ActiveAlertsResponse {
  alerts?: ActiveAlert[];
  error?: string;
}

// ── T8 (v3.66.211) cluster: fed · edge_deploy · pair ──────────────────
export interface FedPeer {
  instance_id?: string;
  base_url?: string;
  version?: string;
  hostname?: string;
  last_seen_ts?: number;
  last_history_id?: number;
  trust_tier?: string;
  [k: string]: unknown;
}
export interface FedPeerDrift {
  instance_id?: string;
  trust_tier?: string;
  local_max?: number;
  peer_last_id?: number;
  behind?: number;
}
export interface FedPeersResponse {
  peers?: FedPeer[];
  drift?: FedPeerDrift[];
  error?: string;
}
export interface FedStatus {
  peers_active?: number;
  active_claims?: number;
  last_expire_run_ts?: number;
  peers_behind?: number;
  error?: string;
}
export interface FedSyncPullResult {
  rows?: Array<Record<string, unknown>>;
  error?: string;
}
export interface FedManualRegisterBody {
  instance_id: string;
  base_url: string;
  version?: string;
  hostname?: string;
}
export interface EdgeComposeBody {
  image?: string;
  port?: number;
  install_dir?: string;
  downloads_dir?: string;
  tz?: string;
  with_qbittorrent?: boolean;
  with_flaresolverr?: boolean;
  with_vpn?: boolean;
}
export interface EdgeComposeResult {
  ok?: boolean;
  yaml?: string;
  error?: string;
}
export interface EdgeAllResult {
  ok?: boolean;
  artifacts?: Record<string, string>;
  error?: string;
}
export interface PairInfo {
  ok?: boolean;
  url?: string;
  base_url?: string;
  lan_ip?: string;
  port?: number;
  token?: string; // short-lived (5-min TTL) pairing token, shown as a QR by design
  qr_svg?: string | null;
  qr_error?: string | null;
}
export interface PairRedeemResult {
  ok?: boolean;
  csrf_token?: string;
  expires_in?: number;
  error?: string;
}

// ── T10 (v3.66.211) template authoring · macros · dev tools ───────────
export interface TemplateLibEntry {
  id?: string;
  name?: string;
  suggested?: boolean;
  [k: string]: unknown;
}
export interface TemplatesListResponse {
  ok?: boolean;
  templates?: TemplateLibEntry[];
}
export interface TemplateExtractBody {
  html: string;
  page_url?: string;
  site_hint_name?: string;
}
export interface TemplateExtractResult {
  ok?: boolean;
  template?: Record<string, unknown>;
  candidates?: Array<Record<string, unknown>>;
  warnings?: string[];
  stats?: Record<string, unknown>;
  error?: string;
}
export interface TemplateRefineBody {
  html: string;
  template?: Record<string, unknown>;
  candidates?: Array<Record<string, unknown>>;
}
export interface TemplateSandboxBody {
  url: string;
  template: Record<string, unknown>;
  mode?: "http" | "browser";
  wait_ms?: number;
}
export interface TemplateSandboxResult {
  ok?: boolean;
  mode?: string;
  url?: string;
  html_bytes?: number;
  matches?: Record<
    string,
    { selector?: string; match_count?: number; samples?: string[]; error?: string }
  >;
  error?: string;
}
export interface MacroRecord {
  site_id?: string;
  name?: string;
  actions?: Array<Record<string, unknown>>;
  description?: string;
  tags?: string[];
  [k: string]: unknown;
}
export interface MacroSaveBody {
  site_id: string;
  name: string;
  actions: Array<Record<string, unknown>>;
  description?: string;
  tags?: string[];
}
export interface MacroReplayBody {
  start_url?: string;
  headless?: boolean;
  persist_result?: boolean;
}
export interface MacroReplayResult {
  ok?: boolean;
  error?: string;
  [k: string]: unknown;
}
export interface DevEnabledResponse {
  enabled?: boolean;
}
export interface DevDiscoverResponse {
  files?: Array<Record<string, unknown>>;
  [k: string]: unknown;
}
export interface DevRunBody {
  target: string;
  kind?: string;
}
export interface DevRunStartResult {
  ok?: boolean;
  run_id?: string;
  error?: string;
}
export interface DevRunStatus {
  run_id?: string;
  state?: string;
  output?: string;
  summary?: Record<string, unknown>;
  error?: string;
  [k: string]: unknown;
}
export interface PluginsStatus {
  loaded?: Array<Record<string, unknown>>;
  error?: string;
  [k: string]: unknown;
}
export interface PluginsEvents {
  events?: Array<Record<string, unknown>>;
  error?: string;
}
export interface SyntheticFixturesList {
  fixtures?: Array<Record<string, unknown>>;
  error?: string;
}
export interface SyntheticRunAllResult {
  ok?: boolean;
  error?: string;
  [k: string]: unknown;
}
export interface I18nLoadResult {
  lang?: string;
  strings?: Record<string, string>;
  error?: string;
}

// ===== T9a (v3.66.212) — live recorder wiring =====
// Shapes re-derived from bulk_downloader/app_live_recorder.py at 211.
export interface LiveStatus {
  ok?: boolean;
  available?: boolean;
  preferred_backend?: string | null;
  backends?: { streamlink?: boolean; ffmpeg?: boolean };
  active_count?: number;
  max_active?: number;
  counts?: Record<string, number>;
  tunables?: { poll_interval_s?: number; disconnect_tolerance_s?: number };
  error?: string;
}
export interface LiveRecording {
  id?: string;
  state?: string;
  url?: string;
  site?: string;
  room?: string;
  output_dir?: string;
  bytes?: number;
  started_ts?: number;
  [k: string]: unknown;
}
export interface LiveRecordingsResponse {
  ok?: boolean;
  recordings?: LiveRecording[];
  error?: string;
}
export interface LiveWatchBody {
  url: string;
  output_dir: string;
  site_override?: string;
  room_override?: string;
}
export interface LiveWatchResult {
  ok?: boolean;
  recording_id?: string;
  error?: string;
  message?: string;
  [k: string]: unknown;
}
export interface LiveUnwatchResult {
  ok?: boolean;
  error?: string;
  [k: string]: unknown;
}
// v3.66.754c — /api/live/parse_url: recognized?/site/room (no watch armed).
export interface LiveParseUrlResult {
  ok?: boolean;
  recognized?: boolean;
  site?: string;
  room?: string;
  error?: string;
  [k: string]: unknown;
}

// ── T9b push (v3.66.213) ────────────────────────────────────────────
// Shapes re-derived from bulk_downloader/app.py :: /api/push/* + push.py.
export interface PushInfo {
  available?: boolean;
  public_key?: string;
  error?: string;
}
export interface PushSubscribeResult {
  ok?: boolean;
  [k: string]: unknown;
}
export interface PushTestResult {
  ok?: boolean;
  sent?: number;
  failed?: number;
  throttled?: number;
  [k: string]: unknown;
}
export interface PushUnsubscribeResult {
  ok?: boolean;
  [k: string]: unknown;
}

// ── Cut 4: operator intelligence ────────────────────────────────────
// Shapes re-derived from bulk_downloader/app.py (queue/preflight, sites
// readiness, storage/validate, runs) + run_history.py.
export type OiCheckStatus = "ok" | "warn" | "fail";
export interface OiCheck {
  key: string;
  label: string;
  status: OiCheckStatus;
  detail?: string;
}
export interface QueuePreflightResponse {
  ok: boolean;
  ready: boolean;
  checks: OiCheck[];
}
export type ReadinessLevel = "green" | "amber" | "red";
export interface SiteReadinessResponse {
  ok: boolean;
  site_id: string;
  level: ReadinessLevel;
  checks: OiCheck[];
  fixes: string[];
  error?: string;
}
export interface StorageValidateResponse {
  ok: boolean;
  path: string;
  exists: boolean;
  is_dir: boolean;
  writable: boolean;
  free_bytes: number | null;
  problems: string[];
  suggested_fix: string | null;
  error?: string;
}
export interface RunRow {
  id: number;
  site_id: string;
  url: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  reason_code?: string | null;
}
export interface RunsResponse {
  ok: boolean;
  runs: RunRow[];
}
export interface RunTimelineEvent {
  id: number;
  run_id: number;
  ts?: string | null;
  event_type: string;
  detail?: string;
}
export interface RunTimelineResponse {
  ok: boolean;
  run: RunRow | null;
  events: RunTimelineEvent[];
}

// 9.10 AI scratchpad ("Ask the model"). Stateless dev/operator aid over the
// hardened LLM contract (POST /api/ai/chat). No persistence, plain-text output.
export interface AiChatRequest {
  prompt: string;
  model?: string;
  system?: string;
  image_b64?: string;
}

export interface AiChatResponse {
  ok: boolean;
  response: string;
  model: string;
  provider: string;
  latency_ms: number;
  image_included: boolean;
  error: string;
}

// ── Cut 8 / Phase 9 deferred write-surface UIs (wired in v3.66.382) ──────

// 379 — recurring-capture schedules
export interface CaptureSchedule {
  id: number;
  site_id: string;
  cadence_hours: number;
  label: string;
  urls: string[];
  enabled?: number;
  next_run_ts?: number;
  last_run_ts?: number;
  last_run_ok?: number;
}
export interface SchedulesResponse {
  ok: boolean;
  schedules: CaptureSchedule[];
  error?: string;
}
export interface ScheduleAddRequest {
  site_id: string;
  cadence_hours: number;
  label?: string;
  urls?: string[];
}
export interface OkIdResult { ok: boolean; id?: number; error?: string }
export interface ScheduleRunResult { ok: boolean; result?: unknown; error?: string }

// 380 — alert rules
export interface AlertRule {
  id: string;
  name?: string;
  metric: string;
  op: string;
  threshold: number;
  duration_minutes?: number;
  severity?: string;
  cooldown_minutes?: number;
  enabled?: boolean;
  builtin?: boolean;
}
export interface AlertRulesResponse { rules: AlertRule[]; error?: string }
export interface AlertRuleSaveResult { ok: boolean; id?: string; error?: string }

// 380 — bulk enqueue
export interface BulkEnqueueRequest { site_id: string; urls: string[] }
export interface BulkEnqueueResult {
  ok: boolean;
  site_id?: string;
  requested?: number;
  added?: number;
  dupes?: number;
  skipped?: number;
  error?: string;
}

// 380 — daily byte-budget usage history
export interface BudgetHistoryPoint { ymd: string; bytes: number }
export interface BudgetHistoryResponse {
  site_id: string;
  history: BudgetHistoryPoint[];
  error?: string;
}

// 504 (Bucket 2 GUI-config parity) — the .env editor for deploy/path/port/host env vars.
export interface EnvfileRow {
  name: string;
  kind: "path" | "port" | "host" | "url" | "bool";
  applies: "restart" | "restart-recommended" | "cli-tool" | "informational";
  applies_note: string;
  foundation: boolean;
  danger: boolean;
  danger_note: string;
  saved: string | null;
  effective: string | null;
  restart_pending: boolean;
}
export interface EnvfileState {
  ok: boolean;
  env: EnvfileRow[];
  count: number;
  path: string;
  read_only: boolean;
  note: string;
}
export interface EnvfileSaveResult {
  ok: boolean;
  written?: string[];
  warnings?: string[];
  rejected?: Record<string, string>;
  accepted?: Record<string, string>;
  path?: string;
  restart_required?: boolean;
  note?: string;
  error?: string;
}
