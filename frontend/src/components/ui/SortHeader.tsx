import { ChevronDown, ChevronsUpDown, ChevronUp } from "lucide-react";

import { type SortDir } from "@/hooks/useTableSort";
import { cn } from "@/lib/utils";

// P6-1 (data display) — a sortable table column header. Renders a <th> with
// aria-sort and an inner button that calls onToggle(sortKey). The caret shows
// the active direction (or a neutral up/down hint when inactive). Pair with
// useTableSort: active = sortKey, dir = sortDir, onToggle = toggle.

export interface SortHeaderProps {
  sortKey: string;
  /** The currently-active sort key (from useTableSort.sortKey), or null. */
  active: string | null;
  dir: SortDir;
  onToggle: (key: string) => void;
  className?: string;
  /** Right-align numeric columns. */
  align?: "left" | "right";
  children: React.ReactNode;
}

export function SortHeader({
  sortKey,
  active,
  dir,
  onToggle,
  className,
  align = "left",
  children,
}: SortHeaderProps) {
  const isActive = active === sortKey;
  const ariaSort = !isActive
    ? "none"
    : dir === "asc"
      ? "ascending"
      : "descending";
  const Caret = !isActive ? ChevronsUpDown : dir === "asc" ? ChevronUp : ChevronDown;

  return (
    <th
      scope="col"
      aria-sort={ariaSort}
      className={cn("px-2 py-1.5 font-medium text-ink-3", className)}
    >
      <button
        type="button"
        onClick={() => onToggle(sortKey)}
        className={cn(
          "group inline-flex items-center gap-1 rounded-sm text-left transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          align === "right" && "flex-row-reverse",
          isActive && "text-ink",
        )}
      >
        <span>{children}</span>
        <Caret
          className={cn(
            "h-3 w-3 shrink-0 transition-opacity",
            isActive ? "opacity-100" : "opacity-40 group-hover:opacity-70",
          )}
          aria-hidden
        />
      </button>
    </th>
  );
}
