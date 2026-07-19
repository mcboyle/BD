import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Maintenance } from "./Maintenance";

// Cut A (Cut 1 adoption remainder) — the Rights / blocklist removal controls are
// grouped in a shared DangerZone, consistent with the other destructive
// surfaces. Presentational/grouping ONLY: the arm/confirm flow + endpoints are
// unchanged. (Per-row Remove buttons only render when blocklist data resolves,
// so this pins the section framing, not a specific row.)

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
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>(() => {})),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Maintenance blocklist DangerZone (Cut A)", () => {
  it("frames the Rights / blocklist section in a DangerZone region", () => {
    mount();
    const zones = screen.getAllByRole("region");
    const dz = zones.find((z) =>
      /blocklist|rights/i.test(z.getAttribute("aria-label") ?? ""),
    );
    expect(dz).toBeTruthy();
    // The region is labelled by its DangerZone title ("Rights · blocklist").
    expect(dz?.getAttribute("aria-label")).toMatch(/blocklist/i);
  });
});
