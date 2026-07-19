import { useQuery, useMutation } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import { adaptiveInterval } from "@/lib/polling";
import { isStreamConnected } from "./useEventStream";
import type {
  DashboardSummary,
  StatsSnapshot,
  StatsBandwidth,
  StatsTimeline,
  HourlyStats,
  CapacitySnapshot,
  StatusSnapshot,
  SessionStatus,
  HealthChecklist,
  WidgetsAllConfig,
  WeatherSnapshot,
  ChangelogResponse,
  RouteUrlsResult,
} from "@/lib/api-types";

// ── T1 read-only dashboard tranche (v3.66.205) ──────────────────────
//
// One hook per legacy-only read endpoint ported into the SPA. Each
// queryFn uses the FULL "/api/…" string literal (NOT a concatenated
// base var) so tools/legacy_parity.py + gui_parity_inventory.py count
// the endpoint as spa_wired and it drops out of the legacy-only set.
// See docs/LEGACY_MIGRATION_PLAN.md (Phase 2, T1).
//
// Cadence: live operational panels poll adaptively (fast while the
// queue is busy, slow when idle); reference panels (changelog, weather,
// widgets config) refresh lazily or once.

const FAST = 4_000;
const SLOW = 30_000;
// F4.5: slow safety poll used while the shared SSE stream is live — a
// backstop in case a push is missed; the stream carries the real updates.
const STREAM_SAFETY = 60_000;
const REFERENCE_STALE = 5 * 60_000;

/** GET /api/dashboard — the consolidated operational snapshot. */
export function useDashboard() {
  return useQuery<DashboardSummary>({
    queryKey: ["dashboard"],
    queryFn: ({ signal }) => apiGet<DashboardSummary>("/api/dashboard", signal),
    refetchInterval: (query) => {
      // F4.5: while the shared /api/stream is pushing `dashboard` snapshots,
      // back off to a slow safety poll instead of the fast/adaptive cadence.
      if (isStreamConnected()) return STREAM_SAFETY;
      return adaptiveInterval({
        query,
        isBusy: (d) =>
          (d?.totals?.running ?? 0) > 0 || (d?.active_workers ?? 0) > 0,
        fast: FAST,
        slow: SLOW,
      });
    },
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/stats — aggregate counters. */
export function useStats() {
  return useQuery<StatsSnapshot>({
    queryKey: ["stats"],
    queryFn: ({ signal }) => apiGet<StatsSnapshot>("/api/stats", signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/stats/bandwidth — throughput series for the sparkline. */
export function useStatsBandwidth() {
  return useQuery<StatsBandwidth>({
    queryKey: ["stats", "bandwidth"],
    queryFn: ({ signal }) =>
      apiGet<StatsBandwidth>("/api/stats/bandwidth", signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/stats/timeline — completion timeline. */
export function useStatsTimeline() {
  return useQuery<StatsTimeline>({
    queryKey: ["stats", "timeline"],
    queryFn: ({ signal }) =>
      apiGet<StatsTimeline>("/api/stats/timeline", signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/hourly_stats — per-hour completion buckets. */
export function useHourlyStats() {
  return useQuery<HourlyStats>({
    queryKey: ["hourly-stats"],
    queryFn: ({ signal }) => apiGet<HourlyStats>("/api/hourly_stats", signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/capacity — disk capacity aggregate. */
export function useCapacity() {
  return useQuery<CapacitySnapshot>({
    queryKey: ["capacity"],
    queryFn: ({ signal }) => apiGet<CapacitySnapshot>("/api/capacity", signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/status — service status line. */
export function useStatus() {
  return useQuery<StatusSnapshot>({
    queryKey: ["status"],
    queryFn: ({ signal }) => apiGet<StatusSnapshot>("/api/status", signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/session_status — per-site login/cookie freshness. */
export function useSessionStatus() {
  return useQuery<SessionStatus>({
    queryKey: ["session-status"],
    queryFn: ({ signal }) =>
      apiGet<SessionStatus>("/api/session_status", signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/health/checklist — readiness checks. */
export function useHealthChecklist() {
  return useQuery<HealthChecklist>({
    queryKey: ["health", "checklist"],
    queryFn: ({ signal }) =>
      apiGet<HealthChecklist>("/api/health/checklist", signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/widgets/all — full widget config (global + per-site). */
export function useWidgetsAll() {
  return useQuery<WidgetsAllConfig>({
    queryKey: ["widgets", "all"],
    queryFn: ({ signal }) =>
      apiGet<WidgetsAllConfig>("/api/widgets/all", signal),
    staleTime: REFERENCE_STALE,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/weather — ambient weather widget (best-effort). */
export function useWeather() {
  return useQuery<WeatherSnapshot>({
    queryKey: ["weather"],
    queryFn: ({ signal }) => apiGet<WeatherSnapshot>("/api/weather", signal),
    staleTime: REFERENCE_STALE,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/changelog — site-behavior drift feed (most-broken first). */
export function useChangelog() {
  return useQuery<ChangelogResponse>({
    queryKey: ["changelog"],
    queryFn: ({ signal }) => apiGet<ChangelogResponse>("/api/changelog", signal),
    staleTime: REFERENCE_STALE,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/**
 * POST /api/route_urls — resolve which site each URL routes to. Read-only
 * in effect (no state mutation) but POST-shaped because it takes the URL
 * text as a body, so it is a mutation hook rather than a query. The handler
 * expects {text: "<newline-separated http URLs>"}.
 */
export function useRouteUrlsLookup() {
  return useMutation<RouteUrlsResult, Error, string>({
    mutationFn: (text) => apiPost<RouteUrlsResult>("/api/route_urls", { text }),
  });
}
