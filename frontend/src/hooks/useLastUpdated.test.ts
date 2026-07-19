import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useLastUpdated } from "./useLastUpdated";

// Cut 2 — useLastUpdated: turn a react-query dataUpdatedAt epoch-ms into a
// short relative label ("just now", "5s ago", "3m ago"). Absent/0 -> null.

describe("useLastUpdated (Cut 2)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-23T20:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns null when there is no timestamp", () => {
    const { result } = renderHook(() => useLastUpdated(0));
    expect(result.current).toBeNull();
  });

  it("reads 'just now' for a very recent update", () => {
    const now = Date.now();
    const { result } = renderHook(() => useLastUpdated(now));
    expect(result.current).toMatch(/just now/i);
  });

  it("reads seconds ago", () => {
    const ts = Date.now() - 8_000;
    const { result } = renderHook(() => useLastUpdated(ts));
    expect(result.current).toMatch(/8s ago/);
  });

  it("reads minutes ago", () => {
    const ts = Date.now() - 3 * 60_000;
    const { result } = renderHook(() => useLastUpdated(ts));
    expect(result.current).toMatch(/3m ago/);
  });
});
