import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));

import App from "@/App";
import { CommandPalette } from "@/components/CommandPalette";
import { Dashboard } from "@/routes/Dashboard";
import {
  calledPaths,
  installApiFixtures,
  renderWired,
} from "@/test/wiredGateHarness";

// DERIVED FROM THE HOOK, NOT HAND-MAINTAINED. The first draft of this spec
// pinned a 12-item constant, which is the SAME denominator shape as the text
// gate it replaced -- merely observed at runtime instead of grepped. An
// adversarial review made the point: an endpoint added to useDashboardData.ts
// and never added to the list would be invisible to the new gate exactly as it
// was to the old one, and an evader deletes the hook line and the list entry
// together. CLAUDE.md A7: a fix must not reproduce the defect's shape.
//
// So the expected set is READ OUT OF THE HOOK at test time. Adding a query to
// the hook adds it to this gate's denominator automatically; deleting the call
// is what the assertions below then catch.
const HOOK_PATH = resolve(process.cwd(), "src/hooks/useDashboardData.ts");

// COMMENTS ARE STRIPPED BEFORE DERIVING, AND THAT IS NOT A DETAIL. The first
// version of this derivation matched the raw file, so a COMMENTED-OUT
// `apiGet("/api/x")` line was counted as an endpoint the dashboard consumes --
// which is exactly the "a path NAMED is not a path CALLED" laundering that
// v3.66.754b closed for tools/gui_parity_inventory.py, reproduced here by the
// fix for a gate that had the same disease. It was caught by planting a
// commented call and watching the gate demand it be fetched. CLAUDE.md A7: every
// fix tends to reproduce the defect's shape; this one did.
const HOOK_SOURCE = readFileSync(HOOK_PATH, "utf-8")
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/(^|[^:])\/\/.*$/gm, "$1");

const AUTOMATIC_ENDPOINTS = Array.from(
  new Set(
    Array.from(
      HOOK_SOURCE.matchAll(/apiGet<[^>]*>\(\s*"(\/api\/[^"]+)"/g),
      (m) => m[1],
    ),
  ),
);

const FIXTURES = {
  "/api/auth/whoami": { ok: true, user: null, multi_user: false },
  "/api/dashboard": { totals: {}, active_workers: 0 },
  "/api/stats": {},
  "/api/stats/bandwidth": { series: [] },
  "/api/stats/timeline": { points: [] },
  "/api/hourly_stats": { hours: [] },
  "/api/capacity": { disks: [] },
  "/api/status": {},
  "/api/session_status": { keepers: [] },
  "/api/health/checklist": { checks: [] },
  "/api/widgets/all": { per_site: {}, catalog: [] },
  "/api/weather": {},
  "/api/changelog": { sites: [] },
  "/api/queue/v2": { waiting: [], running: [] },
  "/api/templates": { templates: [] },
  "/api/sites/v2": { sites: [] },
  "POST /api/route_urls": { results: [] },
};

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

describe("T1 dashboard endpoint derivation", () => {
  it("reads a substantial endpoint set out of the hook itself", () => {
    // WITHOUT THIS, A BROKEN REGEX MAKES EVERY ASSERTION BELOW VACUOUS. An empty
    // derived set means "assert every endpoint was called" is satisfied by
    // calling none of them -- the exact empty-denominator pass this repository
    // keeps finding. Measured at v3.66.1218: 12 endpoints.
    expect(AUTOMATIC_ENDPOINTS.length).toBeGreaterThanOrEqual(10);
    for (const endpoint of AUTOMATIC_ENDPOINTS) {
      expect(endpoint).toMatch(/^\/api\//);
    }
    // and the derivation must track the hook rather than a snapshot of it
    expect(AUTOMATIC_ENDPOINTS).toContain("/api/dashboard");
    expect(AUTOMATIC_ENDPOINTS).toContain("/api/stats");
  });
});

describe("T1 dashboard runtime wiring", () => {
  it("mounting Dashboard consumes every automatic dashboard endpoint", async () => {
    renderWired(<Dashboard />, "/dashboard");

    await waitFor(() => {
      const calls = new Set(calledPaths(apiGetMock));
      for (const endpoint of AUTOMATIC_ENDPOINTS) {
        expect(calls.has(endpoint), endpoint).toBe(true);
      }
    });
  });

  it("route lookup stays inert until Resolve and then calls its exact endpoint", async () => {
    const user = userEvent.setup();
    renderWired(<Dashboard />, "/dashboard");
    expect(apiPostMock).not.toHaveBeenCalled();

    await user.type(
      screen.getByPlaceholderText("Paste URLs to resolve their site…"),
      "https://example.test/video",
    );
    await user.click(screen.getByRole("button", { name: "Resolve" }));

    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/route_urls", {
        text: "https://example.test/video",
      }),
    );
  });

  it("the real App resolves /dashboard to the lazy Dashboard route", async () => {
    renderWired(<App />, "/dashboard");
    expect(
      await screen.findByRole("heading", { name: "System Overview" }),
    ).toBeInTheDocument();
  });

  it("selecting System Overview in the command palette navigates inbound", async () => {
    function Location() {
      return <output>{useLocation().pathname}</output>;
    }
    renderWired(
      <>
        <CommandPalette />
        <Routes>
          <Route path="*" element={<Location />} />
        </Routes>
      </>,
    );
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await userEvent.click(screen.getByText("System Overview"));
    expect(screen.getByText("/dashboard")).toBeInTheDocument();
  });
});
