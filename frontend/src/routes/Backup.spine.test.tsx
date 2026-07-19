import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Backup } from "./Backup";

// Cut 7 (Track A) — Backup adopts the WorkflowPage spine: the body is laid out
// in named slots (purpose -> inputs -> danger -> result) so the reading flow is
// consistent with the other form-heavy routes. The destructive restore stays
// grouped in its DangerZone, now inside the `danger` slot.

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/backup"]}>
        <Backup />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>(() => {})),
  );
});

describe("Backup adopts the WorkflowPage spine (Cut 7 / Track A)", () => {
  it("lays the body out in workflow slots", () => {
    const { container } = mount();
    expect(container.querySelector('[data-slot="purpose"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="inputs"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="danger"]')).toBeTruthy();
  });

  it("keeps the destructive restore inside the danger slot's DangerZone", () => {
    const { container } = mount();
    const danger = container.querySelector('[data-slot="danger"]');
    expect(danger).toBeTruthy();
    // the Restore DangerZone (aria-label "Restore") lives within the danger slot
    expect(danger!.querySelector('[aria-label="Restore"]')).toBeTruthy();
  });
});
