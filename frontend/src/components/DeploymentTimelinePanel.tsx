import { useQuery } from "@tanstack/react-query";

import { Skeleton } from "@/components/ui/skeleton";
import { apiGet } from "@/lib/api-client";
import { formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";

// Row 1057: deployment lifecycle / revision rollout timeline. The service records a
// deployment each time it boots a revision it was not already running (the one it
// replaced becomes superseded, or rolled back when the new revision ran here before)
// and keeps every lifecycle event in the app DB. This panel is the read side of
// GET /api/deploy/timeline. Read-only: nothing here starts or rolls back a deployment.

export type DeploymentRevision = {
  deployment_id: string;
  revision: string;
  target_env: string;
  state: string;
  created_at: number;
  updated_at: number;
  progress_percent: number;
};
export type RolloutEntry = {
  ts: number;
  kind: string;
  severity: "info" | "warn" | "error";
  title: string;
  description: string;
};
type TimelineResp = {
  timeline?: RolloutEntry[];
  deployments?: DeploymentRevision[];
  active_deployment?: DeploymentRevision | null;
};

const SHOWN = 10;
const SEVERITY_CLASS: Record<string, string> = { error: "text-danger", warn: "text-amber-dim" };

function newest<T>(rows: T[] | undefined): T[] {
  return Array.isArray(rows) ? rows.slice(0, SHOWN) : []; // the API lists newest first
}

export function DeploymentTimelinePanel() {
  const q = useQuery<TimelineResp, Error>({
    queryKey: ["deploy", "timeline"],
    queryFn: () => apiGet<TimelineResp>("/api/deploy/timeline"),
  });

  if (q.isLoading) return <Skeleton className="h-16 w-full" />;
  if (q.error) {
    return <p className="text-sm text-danger">Could not read the deployment timeline: {q.error.message}</p>;
  }
  const active = q.data?.active_deployment ?? null;
  const deployments = newest(q.data?.deployments);
  const events = newest(q.data?.timeline);

  return (
    <div className="space-y-3 text-sm" data-testid="deployment-timeline">
      {active ? (
        <p data-state={active.state}>
          Running <span className="font-mono">{active.revision}</span> in {active.target_env} since{" "}
          {formatTimestamp(active.created_at)}.
        </p>
      ) : (
        <p className="text-ink-3">
          No active deployment recorded yet. The service records one when it boots a revision it was
          not already running.
        </p>
      )}

      {deployments.length ? (
        <ol aria-label="Revision history" className="space-y-1">
          {deployments.map((d) => (
            <li key={d.deployment_id} data-state={d.state} className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-mono text-xs">{d.revision}</span>
              <span className="text-xs text-ink-3">
                {d.target_env} · {d.state.replace(/_/g, " ")} · {formatTimestamp(d.created_at)}
              </span>
            </li>
          ))}
        </ol>
      ) : null}

      {events.length ? (
        <ol aria-label="Rollout events" className="space-y-1">
          {events.map((e, i) => (
            <li key={`${e.ts}-${i}`} data-severity={e.severity} className={cn("text-xs", SEVERITY_CLASS[e.severity])}>
              <span className="tabular-nums text-ink-3">{formatTimestamp(e.ts)}</span> {e.description || e.title}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
