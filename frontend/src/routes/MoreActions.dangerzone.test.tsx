import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { MoreActions } from "./MoreActions";

// Cut A (Cut 1 adoption remainder) — the Rights "block content" destructive
// controls (Block URL / Block hash) are grouped in a shared DangerZone, so the
// red destructive buttons don't stand alone. Presentational/grouping ONLY: the
// arm/confirm flow + endpoints are unchanged.

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MoreActions />
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

describe("MoreActions block-content DangerZone (Cut A)", () => {
  it("groups Block URL + Block hash inside a DangerZone region", () => {
    mount();
    const zones = screen.getAllByRole("region");
    const dz = zones.find((z) =>
      /danger|block content/i.test(z.getAttribute("aria-label") ?? ""),
    );
    expect(dz).toBeTruthy();
    const zone = dz as HTMLElement;
    expect(
      within(zone).getByRole("button", { name: /block url/i }),
    ).toBeInTheDocument();
    expect(
      within(zone).getByRole("button", { name: /block hash/i }),
    ).toBeInTheDocument();
  });
});
