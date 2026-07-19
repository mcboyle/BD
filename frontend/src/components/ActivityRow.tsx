import { Check, X, AlertTriangle, MoreHorizontal } from "lucide-react";

import { InlineSparkline } from "@/components/InlineSparkline";
import { SiteAvatar } from "@/components/SiteAvatar";
import type { ActivityRow as ActivityRowT } from "@/lib/api-types";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

// One history row in the Activity tab. Mockup signature:
//   - colored avatar
//   - filename (truncated)
//   - status icon (✓/✗/⚠/…) + site name + bytes + time
//
// v3.64.x D3 follow-up (U6): optional `sparkline` prop renders a
// small 14-day site-health curve to the right of the timestamp.
// When the prop is absent (or null), the row renders exactly as
// before — useful for callers that don't want to drive the
// site_health query (e.g. an embedded preview, or a single-row
// surface). The Activity route DOES drive the query and passes
// values down per row.

export interface ActivityRowProps {
  row: ActivityRowT;
  /** Per-day completion counts, oldest → newest, OR null if the
   *  site_health response had no entry for this row's site (no
   *  activity in window). The sparkline still renders for null
   *  with an empty-zero baseline, communicating "this site has been
   *  quiet" rather than disappearing the slot. */
  sparkline?: number[] | null;
  /** P6-1 density — tighten padding/gap for the compact list density. */
  compact?: boolean;
}

function statusVisual(status: string): {
  icon: React.ComponentType<{ className?: string }>;
  color: string;
  label: string;
} {
  switch (status) {
    case "done":
      return { icon: Check, color: "text-green", label: "Done" };
    case "failed":
      return { icon: X, color: "text-red", label: "Failed" };
    case "needs_review":
      return { icon: AlertTriangle, color: "text-amber", label: "Review" };
    case "stopped":
      return { icon: X, color: "text-ink-3", label: "Stopped" };
    default:
      return { icon: MoreHorizontal, color: "text-ink-3", label: status };
  }
}

export function ActivityRow({ row, sparkline, compact = false }: ActivityRowProps) {
  const { icon: Icon, color, label } = statusVisual(row.status);
  // Sparkline is rendered when the prop is provided (including
  // `null` — we render an empty baseline). undefined = don't render
  // the slot at all.
  const showSparkline = sparkline !== undefined;
  return (
    <div
      className={cn(
        "hairline flex items-center rounded-md bg-surface",
        compact ? "gap-2 p-1.5" : "gap-3 p-2.5",
      )}
    >
      <SiteAvatar name={row.site_name} color={row.avatar_color} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-ink">
          {row.filename || "(no filename)"}
        </div>
        <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-ink-3 truncate">
          <Icon className={cn("h-3 w-3 shrink-0", color)} aria-hidden />
          <span className={cn("shrink-0 uppercase tracking-wide", color)}>
            {label}
          </span>
          <span aria-hidden>·</span>
          <span className="truncate">{row.site_name}</span>
          {row.file_size != null && row.file_size > 0 && (
            <>
              <span aria-hidden>·</span>
              <span className="tabular shrink-0">{formatBytes(row.file_size)}</span>
            </>
          )}
        </div>
      </div>
      {showSparkline && (
        <InlineSparkline
          values={sparkline ?? []}
          width={60}
          height={16}
          className="text-ink-3"
          ariaLabel={`${row.site_name} 14-day activity`}
        />
      )}
      <span className="tabular text-[10px] text-ink-3 shrink-0">
        {formatTs(row.ts)}
      </span>
    </div>
  );
}

function formatTs(ts: string): string {
  if (!ts) return "—";
  // SQLite timestamps come as "YYYY-MM-DD HH:MM:SS" (UTC). Show just
  // HH:MM for today, "Xd" relative for older. Always interpret as
  // UTC; the Date constructor for "YYYY-MM-DD HH:MM:SS" is wonky
  // across browsers — replace the space with T and append Z to force
  // UTC parsing.
  const iso = ts.includes("T") ? ts : ts.replace(" ", "T");
  const isoZ = iso.endsWith("Z") ? iso : iso + "Z";
  const d = new Date(isoZ);
  if (isNaN(d.getTime())) return ts.slice(11, 16) || ts;
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  const daysAgo = Math.floor((+now - +d) / 86_400_000);
  if (daysAgo < 7) return `${daysAgo}d`;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}
