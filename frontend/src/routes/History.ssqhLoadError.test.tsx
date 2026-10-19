// ssqh-fix r2 F2 -- a failed history/search read is an error, not "No history yet."
//
// Sweep spa-sites-queue-history (bd-worker-S21-C) SWEEP-H1: /api/history rejected with
// "HTTP 500 database is locked" and HistorySearchTab rendered the empty state, exactly
// what a real [] renders. Both lanes (list and FTS search) are driven; a real empty
// answer is the control.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));

vi.mock("sonner", () => ({ toast: toastMock }));

import History from "@/routes/History";
import { calledPaths, installApiFixtures, renderWired } from "@/test/wiredGateHarness";

const FIXTURES: Record<string, unknown> = {
  "/api/auth/whoami": { ok: true, user: null, multi_user: false },
  "/api/queue/v2": { waiting: [], running: [] },
  "/api/status?light=1": {},
  "/api/history?limit=200": [],
  "/api/search?q=zebra&limit=100": { ok: true, results: [], count: 0 },
};

/** Reject GETs whose path starts with `prefix`; everything else from FIXTURES. */
function failGets(prefix: string) {
  apiGetMock.mockImplementation((path: string) =>
    path.startsWith(prefix)
      ? Promise.reject(new Error("HTTP 500 database is locked"))
      : Promise.resolve(FIXTURES[path] ?? {}),
  );
}

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function () {};
  }
});

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("ssqh F2: HistorySearchTab error state", () => {
  it("failed /api/history -> alert, not the empty state", async () => {
    failGets("/api/history");
    renderWired(<History />, "/history");
    await waitFor(() => expect(calledPaths(apiGetMock)).toContain("/api/history?limit=200"));
    // Wait for the read to SETTLE (empty state or error), then judge which one drew.
    await waitFor(() =>
      expect(
        screen.queryByText("No history yet.") ?? screen.queryByText("Couldn't load history."),
      ).not.toBeNull(),
    );
    expect(screen.queryByText("No history yet."), "SSQH_F2_ERROR_RENDERED_AS_EMPTY").toBeNull();
    expect(screen.getByText("Couldn't load history.")).toHaveAttribute("role", "alert");
  });

  it("failed /api/search -> alert, not 'No matches.'", async () => {
    failGets("/api/search?");
    const user = userEvent.setup();
    renderWired(<History />, "/history");
    await screen.findByText("No history yet.");
    await user.type(screen.getByPlaceholderText(/Search history/), "zebra");
    await waitFor(
      () => expect(calledPaths(apiGetMock)).toContain("/api/search?q=zebra&limit=100"),
      { timeout: 5000 },
    );
    await waitFor(() =>
      expect(
        screen.queryByText("No matches.") ?? screen.queryByText("Couldn't load search results."),
      ).not.toBeNull(),
    );
    expect(screen.queryByText("No matches."), "SSQH_F2_SEARCH_ERROR_RENDERED_AS_EMPTY").toBeNull();
    expect(screen.getByText("Couldn't load search results.")).toHaveAttribute("role", "alert");
  });

  it("control: a real [] still renders 'No history yet.' with no alert", async () => {
    renderWired(<History />, "/history");
    expect(await screen.findByText("No history yet.")).toBeInTheDocument();
    expect(screen.queryByText("Couldn't load history.")).toBeNull();
  });
});
