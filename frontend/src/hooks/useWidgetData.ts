import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api-client";
import { adaptiveInterval } from "@/lib/polling";
import type { WidgetData } from "@/lib/widgetCatalog";

// v3.64.x — widget catalog restoration.
//
// Polls /api/widgets/data and returns the latest snapshot. One query
// per (scope) — every widget tile derives from the same cache entry,
// so adding more widgets to the dashboard doesn't multiply network
// traffic.
//
// v3.64.x B-2: optional siteId argument. When provided, the data
// query passes `?site_id=<id>` and the cache key isolates the
// per-site snapshot from the global one. Each per-site dashboard
// gets its own cache entry — no cross-pollination.
//
// Cadence: adaptive — fast while anything is downloading, slow when
// idle. The "busy" predicate is conservative: queue_depth > 0 OR
// workers_active > 0. If you wanted a long-running KPI to feel snappy
// (e.g. throughput while watching a download), this gives ~3s
// refresh; idle dashboards refresh every ~30s.

interface WidgetDataEnvelope {
  ok: boolean;
  data: WidgetData;
  site_id?: string | null;
  ts: number;
}

const FAST_INTERVAL = 3_000;
const SLOW_INTERVAL = 30_000;

export function useWidgetData(siteId?: string): {
  data: WidgetData;
  isLoading: boolean;
  isError: boolean;
} {
  const scopeKey = siteId ?? "_global";
  const url = siteId
    ? `/api/widgets/data?site_id=${encodeURIComponent(siteId)}`
    : "/api/widgets/data";
  const q = useQuery<WidgetDataEnvelope>({
    queryKey: ["widget-data", scopeKey],
    queryFn: ({ signal }) =>
      apiGet<WidgetDataEnvelope>(url, signal),
    refetchInterval: (query) =>
      adaptiveInterval({
        query,
        isBusy: (envelope) => {
          const d = envelope?.data ?? {};
          return (d.queue_depth ?? 0) > 0 || (d.workers_active ?? 0) > 0;
        },
        fast: FAST_INTERVAL,
        slow: SLOW_INTERVAL,
      }),
    refetchOnWindowFocus: false,
    retry: 0,
  });

  return {
    data: q.data?.data ?? {},
    isLoading: q.isLoading,
    isError: q.isError,
  };
}
