import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDensity, setDensity } from "./useDensity";

// P6-1 (data display) — shared density preference. A module store synced via
// useSyncExternalStore so the DensityToggle and every list row on a page
// re-render together (and across tabs), backed by localStorage["bd-density"].

describe("useDensity (P6-1)", () => {
  beforeEach(() => {
    try { window.localStorage.clear(); } catch { /* ignore */ }
  });

  it("defaults to comfortable when nothing is stored", () => {
    const { result } = renderHook(() => useDensity());
    expect(result.current.density).toBe("comfortable");
    expect(result.current.isCompact).toBe(false);
  });

  it("reads an existing stored preference on mount", () => {
    window.localStorage.setItem("bd-density", "compact");
    const { result } = renderHook(() => useDensity());
    expect(result.current.density).toBe("compact");
    expect(result.current.isCompact).toBe(true);
  });

  it("setDensity persists to localStorage and flips the value", () => {
    const { result } = renderHook(() => useDensity());
    act(() => setDensity("compact"));
    expect(result.current.density).toBe("compact");
    expect(window.localStorage.getItem("bd-density")).toBe("compact");
  });

  it("keeps two independent hook consumers in sync (cross-component)", () => {
    const a = renderHook(() => useDensity());
    const b = renderHook(() => useDensity());
    act(() => setDensity("compact"));
    expect(a.result.current.density).toBe("compact");
    expect(b.result.current.density).toBe("compact");
  });
});
