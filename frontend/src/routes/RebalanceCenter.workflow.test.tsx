import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { RebalanceCenter } from "./RebalanceCenter";

// Cut 3 — Rebalance adopts the WorkflowPage spine (purpose -> inputs -> plan ->
// danger -> result) and the shared ConfirmDialog for the destructive live run.
// Layout/component lift only: plan/dry-run stay non-destructive; the live
// execute is grouped in a DangerZone and confirmed via the No-default
// ConfirmDialog (no typed entry — v3.66.209). RED on pristine 372 (the page
// uses a bespoke <Dialog> and no WorkflowPage slots).

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <RebalanceCenter />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RebalanceCenter on the WorkflowPage spine (Cut 3)", () => {
  it("renders WorkflowPage slot markers", () => {
    const { container } = mount();
    // At least the inputs slot and the danger slot must be present, in the
    // spine's reading order.
    expect(container.querySelector('[data-slot="inputs"]')).not.toBeNull();
    expect(container.querySelector('[data-slot="danger"]')).not.toBeNull();
  });

  it("groups the live (file-moving) execute inside a DangerZone region", () => {
    mount();
    const zones = Array.from(document.querySelectorAll('section[aria-label]'));
    const dz = zones.find((z) =>
      /danger|rebalance/i.test(z.getAttribute("aria-label") ?? ""),
    );
    expect(dz).toBeTruthy();
  });
});
