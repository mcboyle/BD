import { useQuery } from "@tanstack/react-query";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SiteAvatar } from "@/components/SiteAvatar";
import { apiGet } from "@/lib/api-client";
import { adaptiveInterval } from "@/lib/polling";
import type { QueueV2 } from "@/lib/api-types";
import { formatEta, formatPercent } from "@/lib/format";

// Now-Running list — bottom-of-fold widget showing each currently-
// downloading job with a live progress bar. Polls /api/queue/v2;
// the SSE stream from /api/stream is a U5 (Queue tab) enhancement —
// here, fast adaptive polling is plenty since this widget only
// shows ≤ worker_pool_size rows at a time.

export function NowRunningList() {
  const { data, isLoading } = useQuery<QueueV2>({
    queryKey: ["queue-v2-running"],
    queryFn: ({ signal }) => apiGet<QueueV2>("/api/queue/v2", signal),
    refetchInterval: (query) =>
      adaptiveInterval<QueueV2>({
        query,
        isBusy: (d) => (d.running?.length ?? 0) > 0,
        fast: 2000,
        slow: 8000,
      }),
  });

  if (isLoading || !data) {
    return (
      <Card className="p-4">
        <Skeleton className="mb-3 h-3 w-24" />
        <Skeleton className="h-12 w-full" />
      </Card>
    );
  }

  const running = data.running ?? [];

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="eyebrow">
          Now running
        </span>
        <span className="text-xs tabular text-ink-3">
          <span className="font-semibold text-ink-2">{running.length}</span>
          {" · "}
          {data.done_today_count} done today
        </span>
      </div>
      {running.length === 0 ? (
        <p className="py-2 text-sm text-ink-3">No downloads in flight</p>
      ) : (
        <ul className="space-y-3">
          {running.map((job) => {
            const pct = Math.max(0, Math.min(100, job.progress || 0));
            return (
              <li key={`${job.site_id}-${job.url}`} className="space-y-1.5">
                <div className="flex items-center gap-2 text-sm">
                  <SiteAvatar
                    name={job.site_name}
                    color={job.avatar_color}
                    size="sm"
                  />
                  <span className="min-w-0 flex-1 truncate font-medium">
                    {job.filename || job.url}
                  </span>
                  <span className="tabular text-xs font-semibold text-ink-2">
                    {formatPercent(pct)}
                  </span>
                </div>
                <div
                  className="h-1 w-full overflow-hidden rounded-full bg-surface-2"
                  role="progressbar"
                  aria-valuenow={pct}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div
                    className="h-full bg-primary transition-[width] duration-500 ease-out"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <div className="flex items-center justify-between text-[11px] text-ink-3">
                  <span className="truncate">{job.site_name}</span>
                  <span className="tabular">
                    {job.rate_human || "—"} · ETA {formatEta(job.eta_seconds)}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
