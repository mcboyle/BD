import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ConfigImportExport } from "./ConfigImportExport";

// Cut A (Cut 1 adoption remainder) — the cleartext config-export control is
// grouped in a shared DangerZone (red-accented region) rather than only a
// Caution callout, so the destructive export reads consistently with the other
// destructive surfaces. Presentational/grouping ONLY: the existing checkbox +
// export link behavior (and the import REPLACE gate) are unchanged.

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ConfigImportExport />
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

describe("ConfigImportExport cleartext export DangerZone (Cut A)", () => {
  it("groups the export control inside a DangerZone region", () => {
    mount();
    // DangerZone exposes role=region named by its title. The cleartext export
    // is grouped under an "Export configuration" danger region.
    const zones = screen.getAllByRole("region");
    const dz = zones.find((z) =>
      /export configuration|danger/i.test(z.getAttribute("aria-label") ?? ""),
    );
    expect(dz).toBeTruthy();
    // The DangerZone is the red-accented destructive frame.
    expect((dz as HTMLElement).className).toMatch(/border-red/);
    // The export action (a download link) lives inside that region.
    expect(
      within(dz as HTMLElement).getByRole("link", { name: /export config/i }),
    ).toBeInTheDocument();
  });
});
