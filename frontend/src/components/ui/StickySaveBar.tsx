import { cn } from "@/lib/utils";
import { Button } from "./button";

// Cut 5 — StickySaveBar: governs the global_config form's single save. Inert
// when there are no pending changes; when dirty it surfaces the changed count
// plus Save/Discard. `position: sticky` (NOT fixed) so it stays pinned to the
// bottom of the scroll container AND survives the mobile drawer's translateX
// (a fixed descendant of a transformed ancestor re-anchors — footgun @365).
// Presentational: the page owns the draft engine + the actual mutation.

export interface StickySaveBarProps {
  /** Number of fields whose draft differs from the saved baseline. */
  changedCount: number;
  /** Commit the draft (the page's save handler). */
  onSave: () => void;
  /** Discard the draft back to the saved baseline. */
  onDiscard: () => void;
  /** Mutation in flight — disables Save and shows a saving label. */
  saving?: boolean;
  className?: string;
}

export function StickySaveBar({
  changedCount,
  onSave,
  onDiscard,
  saving = false,
  className,
}: StickySaveBarProps) {
  // Inert when clean — nothing renders, no layout footprint.
  if (changedCount <= 0) return null;

  const label =
    changedCount === 1 ? "1 unsaved change" : `${changedCount} unsaved changes`;

  return (
    <div
      role="region"
      aria-label="Unsaved changes"
      className={cn(
        "sticky bottom-0 z-20 mt-4 flex items-center justify-between gap-3",
        "border-t hairline bg-surface-1/95 px-4 py-3 backdrop-blur",
        className,
      )}
    >
      <span className="text-sm font-medium text-ink-2 tabular-nums">{label}</span>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          onClick={onDiscard}
          disabled={saving}
        >
          Discard
        </Button>
        <Button type="button" onClick={onSave} disabled={saving}>
          {saving ? "Saving…" : "Save changes"}
        </Button>
      </div>
    </div>
  );
}
