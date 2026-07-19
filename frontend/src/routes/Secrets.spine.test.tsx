import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Secrets } from "./Secrets";

// Cut 7 (Track A) — Secrets adopts the WorkflowPage spine; the destructive
// "Delete a stored secret" stays grouped in its DangerZone, in the danger slot.

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/secrets"]}>
        <Secrets />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
});

describe("Secrets adopts the WorkflowPage spine (Cut 7 / Track A)", () => {
  it("lays the body out in workflow slots", () => {
    const { container } = mount();
    expect(container.querySelector('[data-slot="purpose"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="inputs"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="danger"]')).toBeTruthy();
  });

  it("keeps secret-delete inside the danger slot's DangerZone", () => {
    const { container } = mount();
    const danger = container.querySelector('[data-slot="danger"]');
    expect(danger).toBeTruthy();
    expect(danger!.querySelector('[aria-label="Delete a stored secret"]')).toBeTruthy();
  });
});
