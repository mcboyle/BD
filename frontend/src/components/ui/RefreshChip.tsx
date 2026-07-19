import { RotateCw } from "lucide-react";

import { cn } from "@/lib/utils";
import { useLastUpdated } from "@/hooks/useLastUpdated";

// Cut 2 — RefreshChip: a header chip showing the data's last-updated label +
// a manual refresh button. Presentational + a refetch callback; it does not
// fetch anything itself (the page owns the query). Pairs with react-query's
// `dataUpdatedAt` via useLastUpdated.

export interface RefreshChipProps {
  /** react-query dataUpdatedAt (epoch ms). 0 -> no label yet. */
  updatedAt: number;
  /** Invoked when the refresh button is clicked (usually refetch/invalidate). */
  onRefresh: () => void;
  /** Optional: show a spinning icon while a refetch is in flight. */
  refreshing?: boolean;
  className?: string;
}

export function RefreshChip({
  updatedAt,
  onRefresh,
  refreshing = false,
  className,
}: RefreshChipProps) {
  const rel = useLastUpdated(updatedAt);

  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 text-xs text-ink-3",
        className,
      )}
    >
      <span className="tabular-nums">Updated {rel ?? "—"}</span>
      <button
        type="button"
        onClick={onRefresh}
        aria-label="Refresh"
        title="Refresh"
        className="inline-flex items-center justify-center rounded p-1 text-ink-3 hover:text-ink-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
      >
        <RotateCw
          className={cn("h-3.5 w-3.5", refreshing && "animate-spin")}
          aria-hidden
        />
      </button>
    </div>
  );
}
