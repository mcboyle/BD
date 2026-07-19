import { useRef, useState, type ReactNode } from "react";
import { ArrowUpToLine, Check, Trash2, X } from "lucide-react";

import { SiteAvatar } from "@/components/SiteAvatar";
import type { QueueWaitingEntry } from "@/lib/api-types";
import { cn } from "@/lib/utils";

// Waiting job row. Mockup signature:
//   - colored avatar
//   - filename (truncated)
//   - tiny priority badge (only if !=0)
//   - small X button to cancel (NOT a button-in-button — wraps the
//     whole row in a clickable area for open-detail, and isolates
//     the X with stopPropagation)
//
// U8: adds swipe-left-to-cancel gesture. The row tracks horizontal
// touch drag; if released past the threshold (60% of row width), it
// auto-cancels. Below threshold, it snaps back. A tap (drag < 8px)
// passes through to the row click handler.
//
// v3.64.x D3 follow-up: optional bulk-select mode. When `onToggleSelect`
// is provided:
//   - Swipe-to-cancel is suppressed (selecting vs cancelling on the
//     same gesture would be ambiguous).
//   - Tap toggles selection instead of opening the modal.
//   - The inline X cancel button is suppressed (the BulkActionBar
//     handles cancel for the whole selection).
//   - The row visually indicates selected state via ring + soft bg.
// When `onToggleSelect` is absent the row behaves exactly as before.

const SWIPE_THRESHOLD_PCT = 0.6;
const TAP_PIXEL_TOLERANCE = 8;

export interface WaitingJobRowProps {
  job: QueueWaitingEntry;
  onOpen: (job: QueueWaitingEntry) => void;
  onCancel: (job: QueueWaitingEntry) => void;
  isCancelling?: boolean;
  selected?: boolean;
  onToggleSelect?: (job: QueueWaitingEntry) => void;
  // F1.7: pin-to-front. When provided (and not in selection mode), a pin
  // button POSTs priority=high so the runner moves this URL to the queue head.
  onPin?: (job: QueueWaitingEntry) => void;
  /** Cut 6.6 — optional hover quick-actions (e.g. RowQuickActions). Rendered
   *  in the row's action cluster; revealed on hover via the row's group. */
  quickActions?: ReactNode;
  /** P6-1 density — tighten padding for the compact queue density. */
  compact?: boolean;
}

export function WaitingJobRow({
  job,
  onOpen,
  onCancel,
  isCancelling,
  selected = false,
  onToggleSelect,
  onPin,
  quickActions,
  compact = false,
}: WaitingJobRowProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const startXRef = useRef<number | null>(null);
  const draggedRef = useRef(false);
  const [dragX, setDragX] = useState(0);
  // While "transitioning" the foreground snaps back/away with a CSS
  // transition; while dragging we want it to track the finger.
  const [transitioning, setTransitioning] = useState(false);
  const selectionMode = !!onToggleSelect;

  const onPointerDown = (e: React.PointerEvent) => {
    if (isCancelling) return;
    if (selectionMode) return; // tap-to-select handles this row
    // Only primary button / touch
    if (e.button !== 0 && e.pointerType === "mouse") return;
    startXRef.current = e.clientX;
    draggedRef.current = false;
    setTransitioning(false);
    // Capture so move events still fire after the finger leaves the row.
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* iOS Safari occasionally throws here; safe to ignore */
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (startXRef.current === null) return;
    const dx = e.clientX - startXRef.current;
    if (Math.abs(dx) > TAP_PIXEL_TOLERANCE) draggedRef.current = true;
    // Only allow leftward drag. Rightward stays at 0.
    setDragX(Math.min(0, dx));
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (startXRef.current === null) return;
    const dx = e.clientX - startXRef.current;
    startXRef.current = null;
    // Tap: no drag movement, fire onOpen.
    if (!draggedRef.current) {
      onOpen(job);
      return;
    }
    setTransitioning(true);
    const width = containerRef.current?.offsetWidth ?? 320;
    if (-dx > width * SWIPE_THRESHOLD_PCT) {
      // Past threshold — snap fully open then fire cancel.
      setDragX(-width);
      // Slight delay so the user sees the snap before the row
      // disappears via TanStack Query's optimistic removal.
      window.setTimeout(() => onCancel(job), 120);
    } else {
      // Snap back.
      setDragX(0);
    }
  };

  const onPointerCancel = () => {
    if (startXRef.current === null) return;
    startXRef.current = null;
    draggedRef.current = false;
    setTransitioning(true);
    setDragX(0);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    // Keyboard equivalent: Enter/Space opens (or selects in selection mode).
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (selectionMode) onToggleSelect?.(job);
      else onOpen(job);
    }
  };

  const onSelectClick = () => {
    if (selectionMode) onToggleSelect?.(job);
  };

  // In selection mode we render a simpler row — no swipe layer, no
  // X button, but with a check overlay on the avatar.
  if (selectionMode) {
    return (
      <div
        role="button"
        tabIndex={0}
        aria-label={`${selected ? "Deselect" : "Select"} ${job.filename || job.url}`}
        aria-pressed={selected}
        onClick={onSelectClick}
        onKeyDown={onKeyDown}
        className={cn(
          "hairline relative flex items-center gap-3 rounded-md bg-surface p-3",
          compact && "gap-2 p-2",
          "cursor-pointer select-none transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
          selected && "bg-primary-soft ring-2 ring-primary",
        )}
      >
        <div className="relative shrink-0">
          <SiteAvatar name={job.site_name} color={job.avatar_color} size="sm" />
          {selected && (
            <span
              aria-hidden
              className="absolute -right-1 -bottom-1 grid h-4 w-4 place-items-center rounded-full bg-primary text-white ring-2 ring-surface"
            >
              <Check className="h-2.5 w-2.5" strokeWidth={3} />
            </span>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-ink">
            {job.filename || job.url}
          </div>
          <div className="text-[11px] text-ink-3 truncate">
            {job.site_name}
          </div>
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

  return (
    <div
      ref={containerRef}
      className="group relative overflow-hidden rounded-md"
    >
      {/* Red "Cancel" reveal layer, sits behind the foreground. */}
      <div
        aria-hidden
        className="absolute inset-0 flex items-center justify-end bg-red px-4 text-white"
      >
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide">
          <Trash2 className="h-4 w-4" />
          Cancel
        </span>
      </div>
      {/* Foreground — tracks the finger horizontally. */}
      <div
        role="button"
        tabIndex={0}
        aria-label={`Open ${job.filename || job.url}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        onKeyDown={onKeyDown}
        className={cn(
          "hairline relative flex items-center gap-3 rounded-md bg-surface p-3",
          compact && "gap-2 p-2",
          "cursor-pointer touch-pan-y select-none",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
          transitioning && "transition-transform duration-200 ease-out",
        )}
        style={{ transform: `translateX(${dragX}px)` }}
      >
        <SiteAvatar name={job.site_name} color={job.avatar_color} size="sm" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-ink">
            {job.filename || job.url}
          </div>
          <div className="text-[11px] text-ink-3 truncate">
            {job.site_name}
          </div>
        </div>
        {job.priority !== 0 && (
          <span
            className="rounded-sm bg-primary-soft px-1.5 py-0.5 text-[10px] font-bold tabular-nums text-primary"
            aria-label={`priority ${job.priority}`}
          >
            P{job.priority}
          </span>
        )}
        {quickActions != null && !onToggleSelect ? (
          <span
            className="opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
          >
            {quickActions}
          </span>
        ) : null}
        {onPin && job.priority !== 1 && (
          <button
            type="button"
            aria-label="Pin to front"
            title="Pin to front"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onPin(job);
            }}
            className={cn(
              "grid h-7 w-7 shrink-0 place-items-center rounded-sm",
              "text-ink-3 transition-colors hover:bg-primary-soft hover:text-primary",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
            )}
          >
            <ArrowUpToLine className="h-4 w-4" aria-hidden />
          </button>
        )}
        <button
          type="button"
          aria-label="Cancel"
          disabled={isCancelling}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            onCancel(job);
          }}
          className={cn(
            "grid h-7 w-7 shrink-0 place-items-center rounded-sm",
            "text-ink-3 transition-colors hover:bg-red-soft hover:text-red",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
            isCancelling && "opacity-50",
          )}
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
    </div>
  );
}
