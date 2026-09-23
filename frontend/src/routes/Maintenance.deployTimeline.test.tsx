import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Maintenance } from "./Maintenance";

// Row 1057: the Maintenance page carries the deployment rollout timeline card, read
// from GET /api/deploy/timeline. Every other request the page makes stays pending.

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

const ACTIVE = {
  deployment_id: "dep-new", revision: "3.66.1655+abc123def456", target_env: "production",
  state: "active", created_at: 1790150000, updated_at: 1790150000, progress_percent: 100,
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes("/api/deploy/timeline")) {
        return Promise.resolve(jsonResponse({ active_deployment: ACTIVE, deployments: [ACTIVE], timeline: [] }));
      }
      return new Promise<Response>(() => {});
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Maintenance deployment rollout timeline card (row 1057)", () => {
  it("shows the running revision read from GET /api/deploy/timeline", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <Maintenance />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const heading = await screen.findByRole("heading", { name: "Deployments · revision rollout timeline" });
    const card = heading.parentElement as HTMLElement;
    const running = await within(card).findByText(/^Running/);
    expect(running).toHaveTextContent("Running 3.66.1655+abc123def456 in production since");
    expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u) === "/api/deploy/timeline")).toBe(true);
  });
});
