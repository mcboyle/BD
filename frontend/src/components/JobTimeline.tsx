import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { apiGet } from "@/lib/api-client";
import type { RunTimelineResponse } from "@/lib/api-types";
import { reasonMeta } from "@/lib/failure-reason-meta";
import { StatusPill } from "@/components/StatusPill";
import { cn } from "@/lib/utils";

// Cut 4 — job run timeline.
//
// GET /api/runs/<id>/timeline returns the ordered lifecycle events for a single
// run (start … finish) plus the run row. When the run failed, the persisted
// reason_code is turned into a classified header (title + suggested action +
// retry posture) via the shared reason-meta map. Read-only.

export interface JobTimelineProps {
  runId: number;
  className?: string;
}

export function JobTimeline({ runId, className }: JobTimelineProps) {
  const { data, isLoading, isError } = useQuery<RunTimelineResponse>({
    queryKey: ["run-timeline", runId],
    queryFn: ({ signal }) =>
      apiGet<RunTimelineResponse>(`/api/runs/${runId}/timeline`, signal),
    refetchOnWindowFocus: false,
    retry: 0,
  });

  if (isLoading) {
    return (
      <p
        className={cn("flex items-center gap-1.5 text-xs text-ink-3", className)}
        aria-busy
      >
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        Loading timeline…
      </p>
    );
  }

  if (isError || !data) {
    return (
      <p className={cn("text-xs text-ink-3", className)}>
        Couldn't load this run's timeline.
      </p>
    );
  }

  const run = data.run;
  const failed = run?.status === "failed" || run?.status === "error";
  const meta = failed ? reasonMeta(run?.reason_code) : null;

  return (
    <div className={cn("text-xs", className)}>
      {meta && (
        <div
          className="hairline mb-2 rounded-md bg-red-soft/50 p-2"
          role="note"
          aria-label="Failure reason"
        >
          <div className="mb-1 flex items-center gap-2">
            <span className="text-sm font-semibold text-red">{meta.title}</span>
            <StatusPill tone={meta.retryable ? "amber" : "red"} size="sm">
              {meta.retryable ? "will retry" : "won't retry"}
            </StatusPill>
          </div>
          <p className="text-ink-2">{meta.action}</p>
        </div>
      )}

      {data.events.length === 0 ? (
        <p className="text-ink-3">No timeline events.</p>
      ) : (
        <ol className="relative space-y-2 border-l border-hairline pl-4">
          {data.events.map((ev) => (
            <li key={ev.id} className="relative">
              <span
                className="absolute -left-[1.30rem] top-1 h-2 w-2 rounded-full bg-ink-3"
                aria-hidden
              />
              <div className="flex items-baseline gap-2">
                <span className="font-mono uppercase tracking-wide text-ink-2">
                  {ev.event_type}
                </span>
                {ev.ts && (
                  <span className="tabular text-[10px] text-ink-3">
                    {String(ev.ts).replace("T", " ").slice(0, 19)}
                  </span>
                )}
              </div>
              {ev.detail && (
                <p className="break-words text-ink-3">{ev.detail}</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
