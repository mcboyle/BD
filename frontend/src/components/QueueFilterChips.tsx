import { X } from "lucide-react";

// Cut 6.6 — removable filter chips for the Queue. Each active filter renders as
// a chip with a remove affordance; removing one fires onRemove(key) so the page
// can drop it from the URL-encoded view (useUrlState). Renders nothing when no
// filters are active.

export interface FilterChip {
  key: string;
  label: string;
}

export function QueueFilterChips({
  chips,
  onRemove,
}: {
  chips: FilterChip[];
  onRemove: (key: string) => void;
}) {
  if (!chips.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {chips.map((c) => (
        <span
          key={c.key}
          className="inline-flex items-center gap-1 rounded-sm hairline bg-surface px-2 py-0.5 text-xs text-ink-2"
        >
          {c.label}
          <button
            type="button"
            aria-label={`Remove ${c.label}`}
            onClick={() => onRemove(c.key)}
            className="-mr-0.5 rounded-sm p-0.5 text-ink-3 hover:bg-surface-2 hover:text-ink"
          >
            <X aria-hidden className="h-3 w-3" />
          </button>
        </span>
      ))}
    </div>
  );
}
