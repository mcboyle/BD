import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSavingState } from "./useSavingState";

// Cut 2 — useSavingState: a transient "saving… -> saved -> idle" micro-state
// for mutations. start() -> "saving"; succeed() -> "saved" then auto-clears to
// "idle" after a short delay; fail() -> "idle" (the error surfaces elsewhere).

describe("useSavingState (Cut 2)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("starts idle", () => {
    const { result } = renderHook(() => useSavingState());
    expect(result.current.status).toBe("idle");
  });

  it("goes to saving on start()", () => {
    const { result } = renderHook(() => useSavingState());
    act(() => result.current.start());
    expect(result.current.status).toBe("saving");
  });

  it("succeed() -> saved, then auto-clears to idle", () => {
    const { result } = renderHook(() => useSavingState());
    act(() => result.current.start());
    act(() => result.current.succeed());
    expect(result.current.status).toBe("saved");
    act(() => vi.advanceTimersByTime(3000));
    expect(result.current.status).toBe("idle");
  });

  it("fail() returns to idle", () => {
    const { result } = renderHook(() => useSavingState());
    act(() => result.current.start());
    act(() => result.current.fail());
    expect(result.current.status).toBe("idle");
  });
});
