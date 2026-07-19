import { ArrowDownNarrowWide, ArrowUpNarrowWide } from "lucide-react";

import { type SortDir } from "@/hooks/useTableSort";
import { cn } from "@/lib/utils";

// P6-1 (data display) — sort control for non-tabular (<ul>) list pages, where
// column headers don't exist. A field <select> plus a direction toggle button.
// Selecting a new field defaults to ascending; the button flips the current
// field's direction. Drives useTableSort.setSort.

export interface SortOption {
  key: string;
  label: string;
}

export interface SortSelectProps {
  options: SortOption[];
  /** Active sort key (from useTableSort.sortKey). null shows the first option. */
  sortKey: string | null;
  dir: SortDir;
  onSet: (key: string | null, dir: SortDir) => void;
  className?: string;
}

export function SortSelect({
  options,
  sortKey,
  dir,
  onSet,
  className,
}: SortSelectProps) {
  const DirIcon = dir === "asc" ? ArrowUpNarrowWide : ArrowDownNarrowWide;
  const current = sortKey ?? options[0]?.key ?? "";
  return (
    <div className={cn("inline-flex items-center gap-1.5", className)}>
      <label className="text-[11px] font-medium text-ink-3" htmlFor="sortselect">
        Sort by
      </label>
      <select
        id="sortselect"
        aria-label="Sort by"
        value={current}
        onChange={(e) => onSet(e.target.value, "asc")}
        className="hairline rounded-md bg-surface px-2 py-1 text-xs text-ink"
      >
        {options.map((o) => (
          <option key={o.key} value={o.key}>
            {o.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        aria-label={`Sort direction: ${dir === "asc" ? "ascending" : "descending"}`}
        title={dir === "asc" ? "Ascending" : "Descending"}
        onClick={() => onSet(current, dir === "asc" ? "desc" : "asc")}
        className="hairline inline-flex h-[26px] w-[26px] items-center justify-center rounded-md bg-surface text-ink-3 transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <DirIcon className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );
}
