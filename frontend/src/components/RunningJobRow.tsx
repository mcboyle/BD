import { SiteAvatar } from "@/components/SiteAvatar";
import type { QueueRunningEntry } from "@/lib/api-types";
import { formatEta, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

// Running job row used by the Queue tab. Distinct from
// NowRunningList (which lives on the Home tab) because:
//   - this one is tappable for the error/log modal
//   - it accepts an externally-controlled `pct` so the SSE stream
//     can override the polled value for live smoothing

export interface RunningJobRowProps {
  job: QueueRunningEntry;
  /** Optional override pct from SSE; falls back to job.progress. */
  pctOverride?: number;
  onClick?: (job: QueueRunningEntry) => void;
  /** P6-1 density — tighten padding for the compact queue density. */
  compact?: boolean;
}

export function RunningJobRow({ job, pctOverride, onClick, compact = false }: RunningJobRowProps) {
  const pct = Math.max(
    0,
    Math.min(100, pctOverride ?? job.progress ?? 0),
  );
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onClick?.(job)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick?.(job);
        }
      }}
      className={cn(
        "hairline space-y-1.5 rounded-md bg-surface p-3",
        compact && "space-y-1 p-2",
        "cursor-pointer transition-colors hover:bg-surface-2",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
      )}
    >
      <div className="flex items-center gap-2 text-sm">
        <SiteAvatar name={job.site_name} color={job.avatar_color} size="sm" />
        <span className="min-w-0 flex-1 truncate font-medium">
          {job.filename || job.url}
        </span>
        <span className="tabular text-xs font-semibold text-ink-2">{formatPercent(pct)}</span>
      </div>
      <div
        className="h-1 w-full overflow-hidden rounded-full bg-surface-2"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        {/* The 700ms transition smooths SSE-driven jumps. Without it
         * the bar visibly jitters as events arrive ~1Hz. */}
        <div
          className="h-full bg-primary transition-[width] duration-700 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex items-center justify-between text-[11px] text-ink-3">
        <span className="truncate">{job.site_name}</span>
        <span className="tabular">
          {job.rate_human || "—"} · ETA {formatEta(job.eta_seconds)}
        </span>
      </div>
    </div>
  );
}
