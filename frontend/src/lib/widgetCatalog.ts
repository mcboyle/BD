// v3.64.x — widget catalog restoration (Unit B).
//
// The legacy `/` UI shipped 36 KPI widgets across 7 categories;
// the v3.64.0 SPA collapsed to 5 hardcoded widgets. This module brings
// the full catalog back as data — one generic `KPICard` component
// renders any of them based on the declarative `kpiSpec` returned
// here.
//
// Data flow:
//   /api/widgets/data         GET  → snapshot of all KPI values
//   useWidgetData() polls it adaptively (fast when busy, slow when idle).
//
// Configuration (which widgets to show):
//   Lives in localStorage under "bd-widget-selection". GLOBAL ONLY for
//   now — the legacy `/` had per-site overrides via /api/widgets/<sid>
//   but those are deferred to a follow-up unit (OPEN_THREADS B-2). The
//   site_id parameter is omitted from /api/widgets/data calls so the
//   backend returns the global aggregate, which is what the SPA Home
//   tab wants regardless.
//
// Categories from legacy widgets.js + the ic (Tabler icon, mapped
// from the legacy emoji) for the picker UI.

import type { LucideIcon } from "lucide-react";
import {
  Zap, TrendingUp, Database, AlertTriangle, Settings as SettingsIcon,
  Library, Stethoscope,
} from "lucide-react";

export interface WidgetCategory {
  id: string;
  label: string;
  icon: LucideIcon;
}

export const CATEGORIES: WidgetCategory[] = [
  { id: "activity",    label: "Activity",    icon: Zap },
  { id: "performance", label: "Performance", icon: TrendingUp },
  { id: "capacity",    label: "Capacity",    icon: Database },
  { id: "health",      label: "Health",      icon: AlertTriangle },
  { id: "system",      label: "System",      icon: SettingsIcon },
  { id: "collection",  label: "Collection",  icon: Library },
  { id: "diagnostics", label: "Diagnostics", icon: Stethoscope },
];

/** Shape returned by the catalog's `spec(data)` call. The KPICard
 *  component knows how to render all of these fields; any field
 *  set to undefined or null is simply omitted. */
export interface KPISpec {
  /** Main display value. `null`/`undefined` → "—". */
  value: number | string | null | undefined;
  /** Visible label below or above the value. */
  label: string;
  /** Optional second-line text. */
  extra?: string | null;
  /** Optional change delta — string like "+12" or "-3"; sign drives
   *  the up/down/flat coloring. */
  delta?: string | null;
  /** Optional meter (0–100). Renders as a 4px bar under the value. */
  meter?: number | null;
  /** Color band for the meter — same semantics as the legacy CSS:
   *  ok=green, warn=amber, alert=red, neutral=primary. */
  meterKind?: "ok" | "warn" | "alert" | "neutral" | null;
  /** Optional sparkline values. */
  spark?: number[] | null;
  /** Optional emphasis on the value itself (color tint). */
  valueKind?: "ok" | "warn" | "alert" | "action" | null;
}

/** A KPI widget. `id` is the persisted identifier; `cat` is the
 *  category; `title`/`desc` go on the picker card; `spec(data)`
 *  transforms the raw data envelope into render-ready props. */
export interface KPIWidget {
  id: string;
  cat: string;
  title: string;
  desc: string;
  spec: (data: WidgetData) => KPISpec;
}

/** Raw data envelope from /api/widgets/data. Every field is optional
 *  because the backend returns partial data when a collector is
 *  missing (psutil absent, etc.). The KPICard renders "—" for null. */
export interface WidgetData {
  // activity
  done_today?: number | null;
  done_today_delta?: string | null;
  done_today_extra?: string | null;
  done_hour?: number | null;
  done_hour_delta?: string | null;
  bytes_today_fmt?: string | null;
  bytes_today_breakdown?: string | null;
  files_hour?: number | null;
  files_hour_spark?: number[] | null;
  files_hour_delta?: string | null;
  // performance
  throughput_fmt?: string | null;
  throughput_delta?: string | null;
  throughput_spark?: number[] | null;
  success_rate?: number | null;
  avg_speed_fmt?: string | null;
  avg_speed_breakdown?: string | null;
  avg_size_fmt?: string | null;
  avg_quality_label?: string | null;
  avg_quality_breakdown?: string | null;
  // capacity
  queue_depth?: number | null;
  queue_depth_delta?: string | null;
  workers_active?: number | null;
  workers_total?: number | null;
  disk_free_fmt?: string | null;
  disk_used_pct?: number | null;
  bandwidth_fmt?: string | null;
  bandwidth_spark?: number[] | null;
  // health
  action_req?: number | null;
  action_req_breakdown?: string | null;
  stuck?: number | null;
  stuck_breakdown?: string | null;
  failures_hr?: number | null;
  failures_hr_delta?: string | null;
  retries_pending?: number | null;
  retries_extra?: string | null;
  // system
  cpu_pct?: number | null;
  ram_pct?: number | null;
  cpu_cores?: number | null;
  ram_total_fmt?: string | null;
  gpu_util_pct?: number | null;
  gpu_mem_pct?: number | null;
  gpu_name?: string | null;
  gpu_mem_total_fmt?: string | null;
  eta_clear_fmt?: string | null;
  eta_clear_extra?: string | null;
  cookies_oldest_days?: number | null;
  cookies_oldest_site?: string | null;
  sites_running?: number | null;
  sites_total?: number | null;
  sites_breakdown?: string | null;
  // collection
  lib_total?: number | null;
  lib_total_extra?: string | null;
  lib_size_fmt?: string | null;
  lib_size_extra?: string | null;
  lib_watched_pct?: number | null;
  lib_watched_extra?: string | null;
  lib_unrated?: number | null;
  lib_unrated_extra?: string | null;
  lib_missing?: number | null;
  lib_missing_extra?: string | null;
  lib_recent?: number | null;
  lib_recent_spark?: number[] | null;
  lib_top_studio?: string | null;
  lib_top_studio_extra?: string | null;
  audit_recent?: number | null;
  audit_recent_extra?: string | null;
  // diagnostics
  success_rate_24h?: number | null;
  success_rate_24h_extra?: string | null;
  error_top_cluster?: string | null;
  error_top_cluster_extra?: string | null;
  captcha_24h?: number | null;
  captcha_24h_extra?: string | null;
  cookie_warnings?: number | null;
  cookie_warnings_extra?: string | null;
  sites_need_attention?: number | null;
  sites_need_attention_extra?: string | null;
  active_alerts?: number | null;
  active_alerts_extra?: string | null;
}

// ── Helpers (ported from widgets.js) ───────────────────────────────

function pct(v: number | null | undefined): number {
  if (v == null) return 0;
  return Math.round(Number(v));
}

function pct2(a: number | null | undefined, b: number | null | undefined): number {
  if (!b) return 0;
  return Math.round(((a ?? 0) / b) * 100);
}

function meterKindForSuccess(v: number | null | undefined): "ok" | "warn" | "alert" {
  const n = Number(v ?? 0);
  if (n >= 90) return "ok";
  if (n >= 70) return "warn";
  return "alert";
}

function meterKindForDisk(used: number | null | undefined): "ok" | "warn" | "alert" | "neutral" {
  if (used == null) return "neutral";
  if (used > 90) return "alert";
  if (used > 75) return "warn";
  return "ok";
}

function cookieAgeKind(days: number | null | undefined): "warn" | "alert" | null {
  if (days == null) return null;
  if (days <= 3) return "alert";
  if (days <= 7) return "warn";
  return null;
}

// ── Catalog (36 widgets across 7 categories) ───────────────────────

export const WIDGETS: KPIWidget[] = [
  // activity
  { id: "done_today", cat: "activity", title: "Done today",
    desc: "Total completed today",
    spec: d => ({ value: d.done_today, label: "Done today",
                  delta: d.done_today_delta, extra: d.done_today_extra }) },
  { id: "done_hour", cat: "activity", title: "Done · last hour",
    desc: "Completed in last 60 min",
    spec: d => ({ value: d.done_hour, label: "Done · last hour",
                  delta: d.done_hour_delta }) },
  { id: "bytes_today", cat: "activity", title: "Bytes today",
    desc: "Total volume downloaded",
    spec: d => ({ value: d.bytes_today_fmt, label: "Bytes today",
                  extra: d.bytes_today_breakdown }) },
  { id: "files_hour", cat: "activity", title: "Files / hour",
    desc: "Completion rate trend",
    spec: d => ({ value: d.files_hour, label: "Files / hour",
                  spark: d.files_hour_spark, delta: d.files_hour_delta }) },

  // performance
  { id: "throughput", cat: "performance", title: "Throughput",
    desc: "Site bandwidth now",
    spec: d => ({ value: d.throughput_fmt, label: "Throughput",
                  delta: d.throughput_delta, spark: d.throughput_spark }) },
  { id: "success_rate", cat: "performance", title: "Success rate",
    desc: "Rolling 24h success %",
    spec: d => ({ value: d.success_rate != null ? `${pct(d.success_rate)}%` : null,
                  label: "Success rate",
                  meter: d.success_rate, meterKind: meterKindForSuccess(d.success_rate) }) },
  { id: "avg_speed", cat: "performance", title: "Avg speed",
    desc: "Per-worker download speed",
    spec: d => ({ value: d.avg_speed_fmt, label: "Avg speed",
                  extra: d.avg_speed_breakdown }) },
  { id: "avg_size", cat: "performance", title: "Avg file size",
    desc: "Typical download size",
    spec: d => ({ value: d.avg_size_fmt, label: "Avg file size" }) },
  { id: "avg_quality", cat: "performance", title: "Avg quality",
    desc: "% at 2160p / 1080p",
    spec: d => ({ value: d.avg_quality_label, label: "Avg quality",
                  extra: d.avg_quality_breakdown }) },

  // capacity
  { id: "queue_depth", cat: "capacity", title: "Queue depth",
    desc: "Pending URLs",
    spec: d => ({ value: d.queue_depth, label: "Queue depth",
                  delta: d.queue_depth_delta }) },
  { id: "workers", cat: "capacity", title: "Workers",
    desc: "Active / total capacity",
    spec: d => {
      // Null-guard matching legacy v3.63.6 — when no fleet is
      // configured, workers_total is null and we render "—" rather
      // than misleading "0/16".
      const hasFleet = d.workers_total != null && d.workers_total > 0;
      return {
        value: hasFleet ? `${d.workers_active ?? 0}/${d.workers_total}` : null,
        label: "Workers",
        meter: hasFleet ? pct2(d.workers_active, d.workers_total) : null,
        meterKind: hasFleet ? "ok" : null,
      };
    } },
  { id: "disk_free", cat: "capacity", title: "Disk free",
    desc: "Available output space",
    spec: d => ({ value: d.disk_free_fmt, label: "Disk free",
                  meter: d.disk_used_pct, meterKind: meterKindForDisk(d.disk_used_pct) }) },
  { id: "bandwidth", cat: "capacity", title: "Bandwidth",
    desc: "Total network throughput",
    spec: d => ({ value: d.bandwidth_fmt, label: "Bandwidth",
                  spark: d.bandwidth_spark }) },

  // health
  { id: "action_req", cat: "health", title: "Action required",
    desc: "Captcha/login waiting",
    spec: d => ({ value: d.action_req ?? 0, label: "Action required",
                  valueKind: (d.action_req ?? 0) > 0 ? "action" : null,
                  extra: d.action_req_breakdown }) },
  { id: "stuck", cat: "health", title: "Stuck items",
    desc: "No progress 60min+",
    spec: d => ({ value: d.stuck ?? 0, label: "Stuck items",
                  valueKind: (d.stuck ?? 0) > 0 ? "warn" : null,
                  extra: d.stuck_breakdown }) },
  { id: "failures", cat: "health", title: "Failures · last hour",
    desc: "Errors in last hour",
    spec: d => ({ value: d.failures_hr ?? 0, label: "Failures · last hour",
                  valueKind: (d.failures_hr ?? 0) > 5 ? "alert" : null,
                  delta: d.failures_hr_delta }) },
  { id: "retries", cat: "health", title: "Retries pending",
    desc: "URLs awaiting auto-retry",
    spec: d => ({ value: d.retries_pending ?? 0, label: "Retries pending",
                  extra: d.retries_extra }) },

  // system
  { id: "cpu_ram", cat: "system", title: "CPU / RAM",
    desc: "System utilization",
    spec: d => {
      // Null-guard: if psutil is absent, both fields are null and we
      // show "—" rather than misleading "0% / 0%".
      const hasData = d.cpu_pct != null && d.ram_pct != null;
      return {
        value: hasData ? `${pct(d.cpu_pct)}% / ${pct(d.ram_pct)}%` : null,
        label: "CPU / RAM",
        extra: `${d.cpu_cores ?? "—"} cores · ${d.ram_total_fmt ?? ""}`,
      };
    } },
  { id: "gpu", cat: "system", title: "GPU",
    desc: "NVIDIA GPU utilization",
    spec: d => {
      const hasData = d.gpu_util_pct != null && d.gpu_mem_pct != null;
      return {
        value: hasData ? `${pct(d.gpu_util_pct)}% / ${pct(d.gpu_mem_pct)}%` : null,
        label: "GPU",
        extra: `${d.gpu_name ?? "—"} · ${d.gpu_mem_total_fmt ?? ""}`,
      };
    } },
  { id: "eta_clear", cat: "system", title: "ETA queue clear",
    desc: "Forecast queue empty",
    spec: d => ({ value: d.eta_clear_fmt, label: "ETA queue clear",
                  extra: d.eta_clear_extra }) },
  { id: "cookies", cat: "system", title: "Cookies — oldest",
    desc: "Days to renewal",
    spec: d => ({ value: d.cookies_oldest_days != null ? `${d.cookies_oldest_days}d` : null,
                  label: "Cookies — oldest",
                  valueKind: cookieAgeKind(d.cookies_oldest_days),
                  extra: d.cookies_oldest_site }) },
  { id: "sites_active", cat: "system", title: "Sites active",
    desc: "Sites with running workers",
    spec: d => ({ value: `${d.sites_running ?? 0}/${d.sites_total ?? 0}`,
                  label: "Sites active", extra: d.sites_breakdown }) },

  // collection
  { id: "lib_total", cat: "collection", title: "Library size",
    desc: "Total files in the library",
    spec: d => ({ value: d.lib_total, label: "Library size",
                  extra: d.lib_total_extra }) },
  { id: "lib_size", cat: "collection", title: "Disk used",
    desc: "Total bytes across the collection",
    spec: d => ({ value: d.lib_size_fmt, label: "Disk used",
                  extra: d.lib_size_extra }) },
  { id: "lib_watched", cat: "collection", title: "Watched %",
    desc: "Share of the library marked watched",
    spec: d => ({ value: d.lib_watched_pct != null ? `${pct(d.lib_watched_pct)}%` : null,
                  label: "Watched %", meter: d.lib_watched_pct, meterKind: "neutral",
                  extra: d.lib_watched_extra }) },
  { id: "lib_unrated", cat: "collection", title: "Unrated",
    desc: "Files with no star rating yet",
    spec: d => ({ value: d.lib_unrated, label: "Unrated",
                  extra: d.lib_unrated_extra }) },
  { id: "lib_missing", cat: "collection", title: "Missing files",
    desc: "Library rows whose file is gone from disk",
    spec: d => ({ value: d.lib_missing, label: "Missing files",
                  valueKind: (d.lib_missing ?? 0) > 0 ? "warn" : "ok",
                  extra: d.lib_missing_extra }) },
  { id: "lib_recent", cat: "collection", title: "Added this week",
    desc: "New library entries in the last 7 days",
    spec: d => ({ value: d.lib_recent, label: "Added this week",
                  spark: d.lib_recent_spark }) },
  { id: "lib_top_studio", cat: "collection", title: "Top studio",
    desc: "Most-represented studio in the library",
    spec: d => ({ value: d.lib_top_studio, label: "Top studio",
                  extra: d.lib_top_studio_extra }) },
  { id: "audit_recent", cat: "collection", title: "Config changes",
    desc: "Audited config edits in the last 24h",
    spec: d => ({ value: d.audit_recent, label: "Config changes",
                  extra: d.audit_recent_extra }) },

  // diagnostics
  { id: "success_rate_24h", cat: "diagnostics", title: "Success rate · 24h",
    desc: "Completed vs failed across all sites, last 24h",
    spec: d => ({ value: d.success_rate_24h != null ? `${pct(d.success_rate_24h)}%` : null,
                  label: "Success rate · 24h",
                  meter: d.success_rate_24h,
                  meterKind: meterKindForSuccess(d.success_rate_24h),
                  extra: d.success_rate_24h_extra }) },
  { id: "error_top_cluster", cat: "diagnostics", title: "Top error cluster",
    desc: "Most common failure signature, last 24h",
    spec: d => ({ value: d.error_top_cluster, label: "Top error cluster",
                  extra: d.error_top_cluster_extra }) },
  { id: "captcha_24h", cat: "diagnostics", title: "Captcha encounters",
    desc: "Captcha challenges hit across all sites",
    spec: d => ({ value: d.captcha_24h, label: "Captcha encounters",
                  valueKind: (d.captcha_24h ?? 0) > 0 ? "warn" : "ok",
                  extra: d.captcha_24h_extra }) },
  { id: "cookie_warnings", cat: "diagnostics", title: "Cookie warnings",
    desc: "Sites with stale or expiring cookies",
    spec: d => ({ value: d.cookie_warnings, label: "Cookie warnings",
                  valueKind: (d.cookie_warnings ?? 0) > 0 ? "warn" : "ok",
                  extra: d.cookie_warnings_extra }) },
  { id: "sites_need_attention", cat: "diagnostics", title: "Sites need attention",
    desc: "Sites with an alert- or warn-level health issue",
    spec: d => ({ value: d.sites_need_attention, label: "Sites need attention",
                  valueKind: (d.sites_need_attention ?? 0) > 0 ? "warn" : "ok",
                  extra: d.sites_need_attention_extra }) },
  { id: "active_alerts", cat: "diagnostics", title: "Active alerts",
    desc: "Alert rules that fired in the last 24h",
    spec: d => ({ value: d.active_alerts, label: "Active alerts",
                  valueKind: (d.active_alerts ?? 0) > 0 ? "warn" : "ok",
                  extra: d.active_alerts_extra }) },
];

export const WIDGETS_BY_ID: Record<string, KPIWidget> = Object.fromEntries(
  WIDGETS.map(w => [w.id, w]),
);

/** Default selection — five widgets matching the mockup home page's
 *  default arrangement (V2 redesign). Was four pre-V2; `disk_free` is
 *  the addition. The reading order — activity, capacity, health,
 *  performance, disk — is what the home mockup home.png shows on
 *  first paint. The set is still a subset of EXPECTED_WIDGET_IDS so
 *  the existing test_default_widget_ids_are_real contract passes. */
export const DEFAULT_WIDGET_IDS: string[] = [
  "done_today", "throughput", "queue_depth", "action_req", "disk_free",
];

/** All widget IDs as a string set, used for reconciling persisted
 *  selections against the catalog (drops unknown IDs from old configs). */
export const ALL_WIDGET_IDS: Set<string> = new Set(WIDGETS.map(w => w.id));
