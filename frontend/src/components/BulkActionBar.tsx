import { type ReactNode } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";

// Sticky bottom-attached action bar that appears whenever a bulk
// selection is non-empty. Two consumers (Sites, Queue); same chrome,
// different action buttons.
//
// Placement: positioned `sticky bottom-16` so it sits just above the
// BottomTabBar (which is `fixed bottom-0` h-16). Inside an AppShell's
// scroll container the sticky binds to the route content, NOT the
// viewport — meaning if the route is short the bar still sits at the
// bottom of the visible content, and on a tall list it sticks to the
// bottom of the viewport as you scroll. This matches what we want.
//
// Accessibility: `role="region"` + `aria-label="Bulk actions"` so a
// screen reader announces the bar's appearance. The clear button is
// labelled "Clear selection".
export function BulkActionBar({
  count,
  onClear,
  actions,
}: {
  count: number;
  onClear: () => void;
  actions: ReactNode;
}) {
  if (count === 0) return null;
  return (
    <div
      role="region"
      aria-label="Bulk actions"
      className="sticky bottom-16 z-30 -mx-3 mt-3 border-t border-border bg-surface-1/95 px-3 py-2 backdrop-blur"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm">
          <button
            type="button"
            onClick={onClear}
            className="rounded-sm p-1 text-ink-3 hover:text-ink"
            aria-label="Clear selection"
          >
            <X className="h-4 w-4" />
          </button>
          <span className="tabular-nums font-semibold">{count}</span>
          <span className="text-ink-3">selected</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">{actions}</div>
      </div>
    </div>
  );
}

// Convenience action button used inside the bar. Same shape as the
// shadcn Button but pre-sized for the dense action bar slot.
export function BulkActionButton({
  onClick,
  disabled,
  variant = "outline",
  children,
  ariaLabel,
}: {
  onClick: () => void;
  disabled?: boolean;
  variant?: "default" | "outline" | "destructive";
  children: ReactNode;
  ariaLabel?: string;
}) {
  return (
    <Button
      size="sm"
      variant={variant}
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
    >
      {children}
    </Button>
  );
}
