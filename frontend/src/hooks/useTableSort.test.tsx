import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useTableSort } from "./useTableSort";

// P6-1 (data display) — client-side column sort over already-fetched rows.
// 3-state cycle per column: asc -> desc -> none (restores the original /
// server order). Numbers sort numerically, strings via localeCompare, and
// null/undefined always sink to the bottom. Stable (original order breaks ties).

type Row = { name: string; size: number; missing?: number | null };
const ROWS: Row[] = [
  { name: "banana", size: 30, missing: 2 },
  { name: "apple", size: 10, missing: null },
  { name: "cherry", size: 20, missing: 1 },
];

describe("useTableSort (P6-1)", () => {
  it("returns rows in original order with no sort applied", () => {
    const { result } = renderHook(() => useTableSort(ROWS));
    expect(result.current.sortKey).toBeNull();
    expect(result.current.sorted.map((r) => r.name)).toEqual([
      "banana", "apple", "cherry",
    ]);
  });

  it("sorts numbers numerically ascending then descending", () => {
    const { result } = renderHook(() => useTableSort(ROWS));
    act(() => result.current.toggle("size"));
    expect(result.current.sortDir).toBe("asc");
    expect(result.current.sorted.map((r) => r.size)).toEqual([10, 20, 30]);
    act(() => result.current.toggle("size"));
    expect(result.current.sortDir).toBe("desc");
    expect(result.current.sorted.map((r) => r.size)).toEqual([30, 20, 10]);
  });

  it("third toggle on the same key clears the sort (restores order)", () => {
    const { result } = renderHook(() => useTableSort(ROWS));
    act(() => result.current.toggle("size"));
    act(() => result.current.toggle("size"));
    act(() => result.current.toggle("size"));
    expect(result.current.sortKey).toBeNull();
    expect(result.current.sorted.map((r) => r.name)).toEqual([
      "banana", "apple", "cherry",
    ]);
  });

  it("sorts strings with localeCompare", () => {
    const { result } = renderHook(() => useTableSort(ROWS));
    act(() => result.current.toggle("name"));
    expect(result.current.sorted.map((r) => r.name)).toEqual([
      "apple", "banana", "cherry",
    ]);
  });

  it("sinks null/undefined to the bottom regardless of direction", () => {
    const { result } = renderHook(() => useTableSort(ROWS));
    act(() => result.current.toggle("missing"));
    // asc: 1,2 then null last
    expect(result.current.sorted.map((r) => r.missing)).toEqual([1, 2, null]);
    act(() => result.current.toggle("missing"));
    // desc: 2,1 then null STILL last
    expect(result.current.sorted.map((r) => r.missing)).toEqual([2, 1, null]);
  });

  it("supports a custom accessor for a derived value", () => {
    const { result } = renderHook(() =>
      useTableSort(ROWS, { accessors: { lastChar: (r) => r.name.slice(-1) } }),
    );
    act(() => result.current.toggle("lastChar"));
    // 'banana'->a, 'apple'->e, 'cherry'->y
    expect(result.current.sorted.map((r) => r.name)).toEqual([
      "banana", "apple", "cherry",
    ]);
  });

  it("exposes ariaSort per key", () => {
    const { result } = renderHook(() => useTableSort(ROWS));
    expect(result.current.ariaSort("size")).toBe("none");
    act(() => result.current.toggle("size"));
    expect(result.current.ariaSort("size")).toBe("ascending");
    expect(result.current.ariaSort("name")).toBe("none");
  });

  it("setSort sets an explicit key + direction (for dropdown-driven lists)", () => {
    const { result } = renderHook(() => useTableSort(ROWS));
    act(() => result.current.setSort("size", "desc"));
    expect(result.current.sortKey).toBe("size");
    expect(result.current.sortDir).toBe("desc");
    expect(result.current.sorted.map((r) => r.size)).toEqual([30, 20, 10]);
    // setSort(null) restores the original order
    act(() => result.current.setSort(null));
    expect(result.current.sortKey).toBeNull();
    expect(result.current.sorted.map((r) => r.name)).toEqual([
      "banana", "apple", "cherry",
    ]);
  });
});
