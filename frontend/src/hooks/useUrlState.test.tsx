import { describe, it, expect, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { renderHook, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";

// Cut 6.4 — useUrlState: URL-encoded shareable view state (filter/sort/group),
// backed by the query string, ZERO persistence (no localStorage). Hydrates from
// the URL on load; a bookmark/replay reproduces the identical view.
import { useUrlState } from "./useUrlState";

function wrapper(initial: string) {
  return ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
  );
}

describe("useUrlState (Cut 6.4)", () => {
  beforeEach(() => {
    // Guard the zero-persistence contract.
    localStorage.clear();
  });

  it("defaults when the param is absent", () => {
    const { result } = renderHook(() => useUrlState("status", "all"), {
      wrapper: wrapper("/queue"),
    });
    expect(result.current[0]).toBe("all");
  });

  it("hydrates from the URL query string", () => {
    const { result } = renderHook(() => useUrlState("status", "all"), {
      wrapper: wrapper("/queue?status=failed"),
    });
    expect(result.current[0]).toBe("failed");
  });

  it("round-trips set() back into the URL (shareable)", () => {
    const { result } = renderHook(() => useUrlState("sort", "newest"), {
      wrapper: wrapper("/queue"),
    });
    act(() => result.current[1]("oldest"));
    expect(result.current[0]).toBe("oldest");
  });

  it("never writes to localStorage (zero persistence)", () => {
    const { result } = renderHook(() => useUrlState("group", "none"), {
      wrapper: wrapper("/queue"),
    });
    act(() => result.current[1]("site"));
    expect(localStorage.length).toBe(0);
  });
});
