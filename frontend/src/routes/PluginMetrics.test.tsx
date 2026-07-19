import { describe, it, expect, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// v3.66.499 O2 — the per-plugin metrics SPA route (replaces the cockpit panel).
import { PluginMetrics } from "./PluginMetrics";

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PluginMetrics />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("PluginMetrics route (O2)", () => {
  it("renders a row per metric from /api/plugins/status", async () => {
    beforeEachStub({
      metrics: [
        { key: "processor:demo.proc", calls: 5, fails: 1, total_s: 0.1, avg_ms: 20, last_ms: 18 },
      ],
    });
    mount();
    expect(await screen.findByText("processor:demo.proc")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("shows an empty state when no metrics are recorded", async () => {
    beforeEachStub({ metrics: [] });
    mount();
    expect(
      await screen.findByText(/No plugin invocations recorded yet/),
    ).toBeInTheDocument();
  });

  // v3.66.776 V3-E residual: tail percentiles + the quarantine state joined
  // into the same table (backend plugin_metrics p50_ms/p95_ms/quarantined).
  it("renders p50/p95 columns and a quarantine badge (v3.66.776)", async () => {
    beforeEachStub({
      metrics: [
        {
          key: "processor:slow.tail",
          calls: 9,
          fails: 6,
          total_s: 0.5,
          avg_ms: 55.6,
          last_ms: 60,
          p50_ms: 42.5,
          p95_ms: 197.3,
          quarantined: true,
        },
      ],
    });
    mount();
    expect(await screen.findByText("42.5")).toBeInTheDocument();
    expect(screen.getByText("197.3")).toBeInTheDocument();
    expect(screen.getByTestId("quarantined-processor:slow.tail")).toBeInTheDocument();
  });
});

function beforeEachStub(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/plugins/status")) {
        return Promise.resolve(jsonResponse(body));
      }
      return new Promise<Response>(() => {});
    }),
  );
}
