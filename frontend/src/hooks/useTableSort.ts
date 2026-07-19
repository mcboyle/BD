import { useMemo, useState } from "react";

// P6-1 (data display) — client-side column sort over the rows already fetched
// for a list page (these endpoints return a capped page; sorting the visible
// set is honest and matches "column sort on the same list pages").
//
// Per-column 3-state cycle: asc -> desc -> none. "none" restores the original
// (server / recency) order, which is meaningful on these pages, so it stays a
// first-class state rather than collapsing to a 2-way flip. Comparisons:
// numbers numerically, strings via localeCompare, null/undefined always sink
// to the bottom; ties keep original order (stable).

export type SortDir = "asc" | "desc";

type Cmp = string | number | null | undefined;

export interface UseTableSortOptions<T> {
  initialKey?: string;
  initialDir?: SortDir;
  /** Per-key value extractors. Falls back to (row as any)[key] when absent. */
  accessors?: Record<string, (row: T) => Cmp>;
}

export interface UseTableSort<T> {
  sorted: T[];
  sortKey: string | null;
  sortDir: SortDir;
  /** Advance a column through asc -> desc -> none. */
  toggle: (key: string) => void;
  /** Set an explicit key + direction (key=null clears). For dropdown-driven lists. */
  setSort: (key: string | null, dir?: SortDir) => void;
  ariaSort: (key: string) => "ascending" | "descending" | "none";
}

function valueFor<T>(
  row: T,
  key: string,
  accessors?: Record<string, (row: T) => Cmp>,
): Cmp {
  const fn = accessors?.[key];
  if (fn) return fn(row);
  return (row as Record<string, Cmp>)[key];
}

function isNullish(v: Cmp): boolean {
  return v == null || v === "";
}

// Compares two NON-null values; null handling is done by the caller (before
// the direction multiplier) so empties always sink regardless of direction.
function compareNonNull(a: Cmp, b: Cmp): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

export function useTableSort<T>(
  rows: T[],
  options: UseTableSortOptions<T> = {},
): UseTableSort<T> {
  const { initialKey = null, initialDir = "asc", accessors } = options as {
    initialKey?: string | null;
    initialDir?: SortDir;
    accessors?: Record<string, (row: T) => Cmp>;
  };
  const [sortKey, setSortKey] = useState<string | null>(initialKey);
  const [sortDir, setSortDir] = useState<SortDir>(initialDir);

  const toggle = (key: string): void => {
    if (key !== sortKey) {
      setSortKey(key);
      setSortDir("asc");
      return;
    }
    if (sortDir === "asc") {
      setSortDir("desc");
      return;
    }
    // was desc on the active key -> clear back to original order
    setSortKey(null);
    setSortDir("asc");
  };

  const setSort = (key: string | null, dir: SortDir = "asc"): void => {
    setSortKey(key);
    setSortDir(key ? dir : "asc");
  };

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const dir = sortDir === "asc" ? 1 : -1;
    // decorate-sort-undecorate to keep it stable (index tiebreak)
    return rows
      .map((row, i) => ({ row, i }))
      .sort((x, y) => {
        const av = valueFor(x.row, sortKey, accessors);
        const bv = valueFor(y.row, sortKey, accessors);
        const aNull = isNullish(av);
        const bNull = isNullish(bv);
        // empties always sink to the bottom, before the direction flip
        if (aNull && bNull) return x.i - y.i;
        if (aNull) return 1;
        if (bNull) return -1;
        const c = compareNonNull(av, bv);
        if (c !== 0) return c * dir;
        return x.i - y.i;
      })
      .map((d) => d.row);
  }, [rows, sortKey, sortDir, accessors]);

  const ariaSort = (key: string): "ascending" | "descending" | "none" => {
    if (key !== sortKey) return "none";
    return sortDir === "asc" ? "ascending" : "descending";
  };

  return { sorted, sortKey, sortDir, toggle, setSort, ariaSort };
}
