import * as React from "react";
import { Inbox, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

// Shared empty-state pattern (Slice 3 — UX_IMPROVEMENT_PLAN / VISUAL_UNIFICATION
// item 8): MARKER + one LINE + EXPLANATION + primary ACTION. Replaces the Home
// `EmptyTile` stopgap and the per-route ad-hoc empties (Activity/History/Saved
// views/Imports/Rebalance) so every empty surface reads the same and is themed
// from the same tokens (no more bare `text-muted-foreground` leftovers).
//
//   <EmptyState title="No history yet"
//               hint="Captures you run will show up here."
//               action={{ label: "Start a capture", onClick: go }} />
//
// Two shapes:
//   - default  : a centered Card, larger marker, vertical rhythm. Full-width
//                empty regions (a list/table that came back empty).
//   - compact  : fills its parent cell (h-full, justify-center, tighter
//                padding, smaller marker). The Home dashboard tiles, where the
//                grid cell must not collapse.

export interface EmptyStateAction {
  label: string;
  onClick: () => void;
  /** Button variant; defaults to "outline" (a quiet primary action). */
  variant?: React.ComponentProps<typeof Button>["variant"];
}

export interface EmptyStateProps {
  /** The marker glyph. Defaults to a neutral inbox. */
  icon?: LucideIcon;
  /** The one line. */
  title: string;
  /** Optional explanation under the title. */
  hint?: string;
  /** A primary action: either a {label,onClick} spec or a custom node (e.g. a link). */
  action?: EmptyStateAction | React.ReactNode;
  /** Fill-the-cell variant for dashboard tiles. */
  compact?: boolean;
  /** Render without the Card frame — for empties nested inside an existing card. */
  bare?: boolean;
  className?: string;
}

function isActionSpec(a: unknown): a is EmptyStateAction {
  return (
    typeof a === "object" &&
    a !== null &&
    "label" in (a as Record<string, unknown>) &&
    "onClick" in (a as Record<string, unknown>)
  );
}

export function EmptyState({
  icon: Icon = Inbox,
  title,
  hint,
  action,
  compact = false,
  bare = false,
  className,
}: EmptyStateProps) {
  const actionNode = isActionSpec(action) ? (
    <Button
      type="button"
      variant={action.variant ?? "outline"}
      size={compact ? "sm" : "default"}
      onClick={action.onClick}
    >
      {action.label}
    </Button>
  ) : (
    (action as React.ReactNode) ?? null
  );

  const Frame: React.ElementType = bare ? "div" : Card;

  return (
    <Frame
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact ? "h-full gap-1.5 p-4" : "gap-2 px-6 py-10",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "flex items-center justify-center rounded-full bg-surface-2 text-ink-3",
          compact ? "mb-0.5 h-8 w-8" : "mb-1 h-12 w-12",
        )}
      >
        <Icon className={compact ? "h-4 w-4" : "h-6 w-6"} strokeWidth={1.5} />
      </span>
      <div className={cn("font-medium text-ink-2", compact ? "text-sm" : "text-base")}>
        {title}
      </div>
      {hint ? (
        <div className={cn("max-w-sm text-ink-3", compact ? "text-xs" : "text-sm")}>
          {hint}
        </div>
      ) : null}
      {actionNode ? <div className={compact ? "mt-1.5" : "mt-3"}>{actionNode}</div> : null}
    </Frame>
  );
}

export default EmptyState;
