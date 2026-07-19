import { describe, it, expect, vi, afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Home } from "./Home";

// Cut 2 — first-run onboarding on Home. A GENUINELY fresh instance (no sites
// AND no run history) shows a guided onboarding panel (add-site -> capture ->
// queue) instead of the dashboard grid. A user who has history (even with zero
// sites loaded right now) is NOT fresh -> normal dashboard, no onboarding.
//
// Robust signal: dashboard-v2 by_site empty AND /api/history (limit=1) returns
// an empty array. Both reads are existing endpoints (no new backend).

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function mockFetch(opts: { bySite: unknown[]; history: unknown[] }) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/dashboard/v2/sparkline")) {
      return Promise.resolve(jsonResponse({ ok: true, current: 0, history: [] }));
    }
    if (url.includes("/api/dashboard/v2")) {
      return Promise.resolve(
        jsonResponse({
          ok: true,
          attention: [],
          by_site: opts.bySite,
          today: { done: 0, running: 0, failed: 0 },
          sites_count: opts.bySite.length,
          ts: 0,
        }),
      );
    }
    if (url.includes("/api/history")) {
      return Promise.resolve(jsonResponse(opts.history));
    }
    // any other (widget data, etc.) — empty-ish
    return Promise.resolve(jsonResponse({ ok: true }));
  });
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Home first-run onboarding (Cut 2)", () => {
  it("shows the onboarding flow on a fresh instance (no sites, no history)", async () => {
    vi.stubGlobal("fetch", mockFetch({ bySite: [], history: [] }));
    mount();
    // The dedicated first-run onboarding panel (distinct from the per-tile
    // "No sites yet" empty) names the guided three-step path explicitly.
    expect(
      await screen.findByText(/get started with your first capture/i),
    ).toBeInTheDocument();
    // It lays out the guided steps add-site -> capture -> queue.
    expect(screen.getByText(/add a site/i)).toBeInTheDocument();
    expect(screen.getByText(/run a capture/i)).toBeInTheDocument();
  });

  it("does NOT show onboarding when history exists (sites may be 0)", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ bySite: [], history: [{ id: 1, status: "done" }] }),
    );
    mount();
    // Give the queries a tick to resolve, then assert no onboarding panel.
    await waitFor(() => {
      // dashboard grid path renders; the dedicated onboarding heading is absent.
      expect(screen.queryByText(/get started with your first capture/i)).toBeNull();
    });
  });
});
