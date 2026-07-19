import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BatchOps } from "./BatchOps";

// Cut 7 (Track A) — BatchOps adopts the WorkflowPage spine; the destructive
// batch-delete stays grouped in its DangerZone, now inside the `danger` slot.

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/batch"]}>
        <BatchOps />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
});

describe("BatchOps adopts the WorkflowPage spine (Cut 7 / Track A)", () => {
  it("lays the body out in workflow slots", () => {
    const { container } = mount();
    expect(container.querySelector('[data-slot="purpose"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="danger"]')).toBeTruthy();
  });

  it("keeps batch-delete inside the danger slot's DangerZone", () => {
    const { container } = mount();
    const danger = container.querySelector('[data-slot="danger"]');
    expect(danger).toBeTruthy();
    expect(danger!.querySelector('[aria-label="Batch delete history"]')).toBeTruthy();
  });
});
