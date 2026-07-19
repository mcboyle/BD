import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical } from "lucide-react";

import { SiteAvatar } from "@/components/SiteAvatar";
import type { QueueWaitingEntry } from "@/lib/api-types";
import { cn } from "@/lib/utils";

// Sortable wrapper for a waiting job row inside reorder mode.
//
// Why a dedicated component instead of conditionally calling
// `useSortable` inside WaitingJobRow: the dnd-kit hook returns a
// large set of attributes/listeners that need to be spread on a
// specific DOM element (the drag handle). Forcing WaitingJobRow to
// know about dnd-kit when it's only used during reorder mode would
// couple it to a library it doesn't need 95% of the time. Reorder
// mode shows a simplified row anyway (no swipe, no inline cancel,
// no tap-to-open) so the shapes diverge enough to justify a separate
// component.
//
// Layout matches WaitingJobRow's selection-mode shape so switching
// between "Select" and "Sort" modes isn't visually jarring.

export interface SortableWaitingJobRowProps {
  job: QueueWaitingEntry;
  isDragOverlay?: boolean;
}

export function SortableWaitingJobRow({
  job,
  isDragOverlay = false,
}: SortableWaitingJobRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: job.url });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        "hairline relative flex items-center gap-2 rounded-md bg-surface p-2.5",
        "select-none touch-none",
        isDragging && "opacity-30",
        isDragOverlay && "shadow-lg ring-2 ring-primary",
      )}
    >
      {/* Drag handle — the listeners attach here, not to the row,
          so taps on the row body do nothing in reorder mode (no
          accidental drags when the user just wants to scroll). */}
      <button
        type="button"
        aria-label={`Drag to reorder ${job.filename || job.url}`}
        className={cn(
          "grid h-8 w-8 shrink-0 cursor-grab place-items-center rounded-sm",
          "text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
          "active:cursor-grabbing",
        )}
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4" aria-hidden />
      </button>
      <SiteAvatar name={job.site_name} color={job.avatar_color} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">
          {job.filename || job.url}
        </div>
        <div className="text-[11px] text-ink-3 truncate">{job.site_name}</div>
      </div>
      {job.priority !== 0 && (
        <span
          className="rounded-sm bg-primary-soft px-1.5 py-0.5 text-[10px] font-bold tabular-nums text-primary"
          aria-label={`priority ${job.priority}`}
        >
          P{job.priority}
        </span>
      )}
    </div>
  );
}
