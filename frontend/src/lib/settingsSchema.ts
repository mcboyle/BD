// Cut 1 substrate (hidden long-pole), EXPANDED in Cut 5: the field -> section map
// for global config. Shared by Cut 5 (changed-markers + mini-ToC) AND Cut 6.3
// (command-palette settings search), so it's built early to make those free.
//
// Sections mirror routes/Settings.tsx SettingSection `label=` values (the page's
// nav order). `sectionForField` returns null for an unknown field — never a
// fabricated section. The map is PAGE-TRUTH: every assignment matches the
// section a field actually renders under (e.g. log_level renders under Network,
// not Diagnostics). Sections with no global_config fields (System, Tools &
// operations, Supervisor throttle, Import / Export) are declared for the ToC but
// hold no field entries.

export type SettingsSection =
  | "Downloads"
  | "AI assist"
  | "Network"
  | "Queue housekeeping"
  | "Capture"
  | "Diagnostics"
  | "Session keep-alive"
  | "System"
  | "Tools & operations"
  | "Supervisor throttle"
  | "Browser"
  | "Challenge handling"
  | "Advanced"
  | "Security & access"
  | "Automation"
  | "Environment (restart required)"
  | "Store metadata (raw / advanced)"
  | "Import / Export";

// Ordered for the mini-ToC (matches the page's section render order).
export const SETTINGS_SECTIONS: SettingsSection[] = [
  "Downloads",
  "AI assist",
  "Network",
  "Queue housekeeping",
  "Capture",
  "Diagnostics",
  "Session keep-alive",
  "System",
  "Tools & operations",
  "Supervisor throttle",
  "Browser",
  "Challenge handling",
  "Advanced",
  "Automation",
  "Security & access",
  "Environment (restart required)",
  "Store metadata (raw / advanced)",
  "Import / Export",
];

export interface SettingFieldMeta {
  section: SettingsSection;
  /** Human label for palette search; falls back to the field name elsewhere. */
  label?: string;
  /** Secret-bearing field (palette/ToC/chips/copy must never echo its value). */
  secret?: boolean;
}

export const SETTINGS_SCHEMA: Record<string, SettingFieldMeta> = {
  // ── Downloads ──────────────────────────────────────────────────────────
  global_max_concurrent: { section: "Downloads", label: "Global concurrent cap" },
  watch_folder: { section: "Downloads", label: "Watch folder" },
  watch_interval_sec: { section: "Downloads", label: "Watch interval (sec)" },
  watch_archive: { section: "Downloads", label: "Archive watched files" },
  // ── AI assist ──────────────────────────────────────────────────────────
  ai_enabled: { section: "AI assist", label: "AI assist enabled" },
  ai_provider: { section: "AI assist", label: "AI provider" },
  ai_endpoint: { section: "AI assist", label: "AI endpoint" },
  ai_model_vision: { section: "AI assist", label: "Vision model" },
  ai_model_text: { section: "AI assist", label: "Text model" },
  ai_api_key: { section: "AI assist", label: "AI API key", secret: true },
  // ── Network ────────────────────────────────────────────────────────────
  rate_limit_global_concurrent: { section: "Network", label: "Global rate-limit concurrency" },
  rate_limit_global_per_sec: { section: "Network", label: "Global requests per second" },
  rate_limit_domain_overrides: { section: "Network", label: "Per-domain rate overrides" },
  path_allowlist: { section: "Network", label: "Path allowlist" },
  log_level: { section: "Network", label: "Log level" },
  ui_logging_level: { section: "Network", label: "UI logging level" },
  template_auto_detect_mode: { section: "Network", label: "Template auto-detect mode" },
  // ── Queue housekeeping ─────────────────────────────────────────────────
  queue_hk_abandon: { section: "Queue housekeeping", label: "Abandon stuck jobs" },
  queue_hk_gc_age_days: { section: "Queue housekeeping", label: "GC age (days)" },
  queue_hk_max_retries: { section: "Queue housekeeping", label: "Max retries" },
  queue_hk_stale_hours: { section: "Queue housekeeping", label: "Stale threshold (hours)" },
  // ── Capture ────────────────────────────────────────────────────────────
  capture_bodies: { section: "Capture", label: "Capture response bodies" },
  capture_raw: { section: "Capture", label: "Capture raw payloads" },
  capture_wait_until: { section: "Capture", label: "Capture wait-until" },
  dom_honeypot_filter: { section: "Capture", label: "DOM honeypot filter" },
  redact_dom_urls: { section: "Capture", label: "Redact DOM URLs" },
  // ── Diagnostics ────────────────────────────────────────────────────────
  slow_query_log: { section: "Diagnostics", label: "Slow-query log" },
  slow_query_ms: { section: "Diagnostics", label: "Slow-query threshold (ms)" },
  // ── Session keep-alive ─────────────────────────────────────────────────
  session_keep_alive_fetch_interval_min: { section: "Session keep-alive", label: "Fetch interval (min)" },
  session_keep_alive_lead_time_min: { section: "Session keep-alive", label: "Lead time (min)" },
  session_keep_alive_navigate_interval_min: { section: "Session keep-alive", label: "Navigate interval (min)" },
  session_keeper_use_cloakbrowser: { section: "Session keep-alive", label: "Keeper uses cloakbrowser" },
  // ── Browser ────────────────────────────────────────────────────────────
  browser_backend: { section: "Browser", label: "Browser backend" },
  novnc_url: { section: "Browser", label: "noVNC URL" },
  // ── Challenge handling ─────────────────────────────────────────────────
  challenge_wait_s: { section: "Challenge handling", label: "Challenge wait (sec)" },
  honeypot_per_site: { section: "Challenge handling", label: "Honeypot per-site" },
  honeypot_score_threshold: { section: "Challenge handling", label: "Honeypot score threshold" },
  // v3.66.503 (Bucket 1): captcha-relay timeouts, promoted import-time -> full.
  captcha_pending_timeout_s: { section: "Challenge handling", label: "Captcha pending timeout (sec)" },
  captcha_push_dedupe_s: { section: "Challenge handling", label: "Captcha push dedupe (sec)" },
  captcha_takeover_mode: { section: "Challenge handling", label: "Captcha takeover mode" },
  captcha_takeover_enabled: { section: "Challenge handling", label: "Remote takeover enabled (kill-switch)" },
  captcha_takeover_max_concurrent: { section: "Challenge handling", label: "Remote takeover max concurrent" },
  captcha_takeover_idle_timeout_s: { section: "Challenge handling", label: "Remote takeover idle timeout (sec)" },
  captcha_vnc_display: { section: "Challenge handling", label: "VNC takeover display (e.g. :5)" },
  captcha_vnc_websocket_port: { section: "Challenge handling", label: "VNC takeover websocket port" },
  // ── Advanced ───────────────────────────────────────────────────────────
  auth_throttle: { section: "Advanced", label: "Auth throttle" },
  auth_throttle_base: { section: "Advanced", label: "Auth throttle base" },
  auth_throttle_free: { section: "Advanced", label: "Auth throttle free" },
  auth_throttle_max: { section: "Advanced", label: "Auth throttle max" },
  cross_site_selectors: { section: "Advanced", label: "Cross-site selectors" },
  fleet_nodes: { section: "Advanced", label: "Fleet nodes" },
  held_out_stale_days: { section: "Advanced", label: "Held-out stale (days)" },
  hud_overlay: { section: "Advanced", label: "HUD overlay" },
  lib_reconcile_missing_days: { section: "Advanced", label: "Library reconcile missing (days)" },
  lint_kb_allow: { section: "Advanced", label: "Lint KB allow" },
  player_struct_tiebreak: { section: "Advanced", label: "Player struct tiebreak" },
  redact_emails: { section: "Advanced", label: "Redact emails" },
  redact_extra_headers: { section: "Advanced", label: "Redact extra headers" },
  redact_network_urls: { section: "Advanced", label: "Redact network URLs" },
  secrets_audit: { section: "Advanced", label: "Secrets audit" },
  secrets_audit_file: { section: "Advanced", label: "Secrets audit file" },
  secrets_audit_max_bytes: { section: "Advanced", label: "Secrets audit max bytes" },
  secrets_audit_sink: { section: "Advanced", label: "Secrets audit sink" },
  youtube_cipher: { section: "Advanced", label: "YouTube cipher" },
  // v3.66.503 (Bucket 1): HLS + Live-recorder tunables, promoted import-time ->
  // full (call-time getters). Power-user knobs -> Advanced.
  hls_input_timeout_us: { section: "Advanced", label: "HLS input timeout (us)" },
  hls_max_runtime_s: { section: "Advanced", label: "HLS max runtime (sec)" },
  hls_progress_poll_s: { section: "Advanced", label: "HLS progress poll (sec)" },
  live_poll_interval_s: { section: "Advanced", label: "Live poll interval (sec)" },
  live_disconnect_tolerance_s: { section: "Advanced", label: "Live disconnect tolerance (sec)" },
  live_max_active_recordings: { section: "Advanced", label: "Live max active recordings" },
  live_launch_timeout_s: { section: "Advanced", label: "Live launch timeout (sec)" },
  // ── Security & access ──────────────────────────────────────────────────
  // -- Automation (v3.66.711, A-GUI Cut 3) --------------------------------
  // Dotted keys: quoted, and they must match GLOBAL_CONFIG_SCHEMA exactly -- the
  // parity inventory DERIVES gui_exposure from whether the frontend references the
  // key, so a typo here does not fail loudly, it silently reports the control as
  // missing. The 26 keys below are the automation program's entire surface.
  "automation.master_off_switch": { section: "Automation", label: "Master off-switch (EMERGENCY STOP)" },
  "automation.drift_sweep_enabled": { section: "Automation", label: "Drift sweep (L1)" },
  "automation.validation_gate_enabled": { section: "Automation", label: "Validation gate (L2)" },
  "automation.auto_flag_enabled": { section: "Automation", label: "Auto-flag needs-review (L3)" },
  "automation.auto_quarantine_enabled": { section: "Automation", label: "Auto-quarantine (L4)" },
  "automation.auto_repair_enabled": { section: "Automation", label: "Auto-repair (L5)" },
  "automation.auto_refresh_enabled": { section: "Automation", label: "Auto-refresh templates (L5)" },
  "automation.auto_promote_enabled": { section: "Automation", label: "Auto-promote candidates (A5)" },
  "automation.controller_enabled": { section: "Automation", label: "Autonomy controller (A9)" },
  "automation.disco_enabled": { section: "Automation", label: "A-DISCO auto-discovery (L4)" },
  "automation.auto_onboard_enabled": { section: "Automation", label: "Auto-onboard (A4, prep only)" },
  "automation.auto_ci_enabled": { section: "Automation", label: "Auto-CI loop (A6)" },
  "automation.auto_recover_enabled": { section: "Automation", label: "Self-recovery (A7)" },
  "automation.auto_queue_enabled": { section: "Automation", label: "Queue self-management (A8)" },
  "automation.auto_refresh_on_capture_enabled": { section: "Automation", label: "Refresh on capture" },
  "automation.auto_refresh_confirm_enabled": { section: "Automation", label: "Refresh needs confirm" },
  "automation.auto_refresh_max_drift": { section: "Automation", label: "Max drift for auto-refresh" },
  "automation.scrub_on_capture_enabled": { section: "Automation", label: "Scrub captures (redaction)" },
  "automation.scrub_on_capture_tool": { section: "Automation", label: "Scrub tool path override" },
  "automation.daily_digest_enabled": { section: "Automation", label: "Daily digest" },
  "automation.drift_repair_enabled": { section: "Automation", label: "Drift repair drafts" },
  "automation.template_canary_enabled": { section: "Automation", label: "Template canary" },
  "automation.restore_rehearsal_enabled": { section: "Automation", label: "Restore rehearsal (safety net)" },
  "automation.pipeline_enabled": { section: "Automation", label: "Autonomous pipeline (A-PIPE)" },
  "automation.cycle_max_steps": { section: "Automation", label: "Cycle ceiling: max steps" },
  "automation.cycle_wall_s": { section: "Automation", label: "Cycle ceiling: wall seconds" },
  "automation.cycle_max_errors": { section: "Automation", label: "Cycle ceiling: max errors" },
  auth_token: { section: "Security & access", label: "Auth token", secret: true },
  bd_token: { section: "Security & access", label: "BD token", secret: true },
  autonomy_enabled: { section: "Security & access", label: "Autonomy enabled" },
  cockpit_shell: { section: "Security & access", label: "Cockpit shell" },
  cockpit_tasks: { section: "Security & access", label: "Cockpit tasks" },
  dev_mode: { section: "Security & access", label: "Dev mode" },
  framework_reports: { section: "Security & access", label: "Framework reports" },
  test_mode: { section: "Security & access", label: "Test mode" },
  netns_isolation: { section: "Security & access", label: "Egress isolation (netns / wg0 fail-closed)" },
};

export function sectionForField(field: string): SettingsSection | null {
  return SETTINGS_SCHEMA[field]?.section ?? null;
}
