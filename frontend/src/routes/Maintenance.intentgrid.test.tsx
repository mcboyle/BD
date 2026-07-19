import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Maintenance } from "./Maintenance";

// Cut 3 — Maintenance is reorganized into an INTENT GRID (not the input->result
// spine): Inspect / Repair / Maintenance / Export / Dangerous. Retention
// real-apply lives in a DangerZone confirmed via ConfirmDialog (No default, NO
// typed entry — v3.66.209). RED on pristine 372 (sections are flat
// section-heads, no intent grouping).

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Maintenance />
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

describe("Maintenance intent grid (Cut 3)", () => {
  it("labels the five operator intents", () => {
    const { container } = mount();
    const text = container.textContent ?? "";
    for (const intent of ["Inspect", "Repair", "Maintenance", "Export", "Dangerous"]) {
      expect(text).toContain(intent);
    }
    // The grid marks each intent group for testability/targeted styling.
    expect(container.querySelector('[data-intent]')).not.toBeNull();
  });

  it("keeps retention real-apply inside a DangerZone region", () => {
    mount();
    const zones = Array.from(document.querySelectorAll('section[aria-label]'));
    const dz = zones.find((z) =>
      /retention|danger/i.test(z.getAttribute("aria-label") ?? ""),
    );
    expect(dz).toBeTruthy();
  });
});
