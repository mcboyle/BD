import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDirtyForm } from "./useDirtyForm";

// Cut 1 substrate: one draft-vs-saved engine. Drives sticky save/discard,
// changed-markers, and disabled-until-changed across Settings AND every form
// page. No adoption in Cut 1 — just the scaffold + its contract.

describe("useDirtyForm", () => {
  it("starts clean with no changed keys", () => {
    const { result } = renderHook(() => useDirtyForm({ a: 1, b: "x" }));
    expect(result.current.isDirty).toBe(false);
    expect(result.current.changedKeys).toEqual([]);
    expect(result.current.values).toEqual({ a: 1, b: "x" });
  });

  it("tracks a changed field and goes dirty", () => {
    const { result } = renderHook(() => useDirtyForm({ a: 1, b: "x" }));
    act(() => result.current.setValue("a", 2));
    expect(result.current.isDirty).toBe(true);
    expect(result.current.changedKeys).toEqual(["a"]);
    expect(result.current.values.a).toBe(2);
  });

  it("a value set back to the saved value is no longer changed", () => {
    const { result } = renderHook(() => useDirtyForm({ a: 1 }));
    act(() => result.current.setValue("a", 2));
    act(() => result.current.setValue("a", 1));
    expect(result.current.isDirty).toBe(false);
    expect(result.current.changedKeys).toEqual([]);
  });

  it("reset restores the saved snapshot", () => {
    const { result } = renderHook(() => useDirtyForm({ a: 1 }));
    act(() => result.current.setValue("a", 9));
    act(() => result.current.reset());
    expect(result.current.values.a).toBe(1);
    expect(result.current.isDirty).toBe(false);
  });

  it("markSaved adopts the current values as the new baseline", () => {
    const { result } = renderHook(() => useDirtyForm({ a: 1 }));
    act(() => result.current.setValue("a", 5));
    act(() => result.current.markSaved());
    expect(result.current.isDirty).toBe(false);
    expect(result.current.changedKeys).toEqual([]);
    expect(result.current.values.a).toBe(5);
  });
});
