import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Vpn } from "./Vpn";

// Cut 7 (Track A) — Vpn adopts the WorkflowPage spine and groups the kill
// switch (clearing it re-enables blocked traffic) in a DangerZone in the
// danger slot.

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/vpn"]}>
        <Vpn />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
});

describe("Vpn adopts the WorkflowPage spine (Cut 7 / Track A)", () => {
  it("lays the body out in workflow slots", () => {
    const { container } = mount();
    expect(container.querySelector('[data-slot="purpose"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="inputs"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="danger"]')).toBeTruthy();
  });

  it("groups the kill switch in the danger slot's DangerZone", () => {
    const { container } = mount();
    const danger = container.querySelector('[data-slot="danger"]');
    expect(danger).toBeTruthy();
    expect(danger!.querySelector('[aria-label="Kill switch"]')).toBeTruthy();
  });
});
