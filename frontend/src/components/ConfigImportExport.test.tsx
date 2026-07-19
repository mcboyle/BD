import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ConfigImportExport } from "./ConfigImportExport";

// P6-4 (PHASE6_PLAN): the cleartext-export opt-in ("Include passwords — the file
// will contain secrets in clear text") is one of the high-risk clusters that
// must be framed by a presentational Caution callout — same convergence target
// as the Security & access danger zone and the gated-write banners. This is
// presentational only: the existing checkbox + export link behavior is unchanged.

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

describe("ConfigImportExport — cleartext export framing (P6-4)", () => {
  it("frames the cleartext-export opt-in in a Caution callout", () => {
    mount();
    // The shared Callout renders role=note for caution. The cleartext export
    // cluster gets a recognizable title.
    const notes = screen.getAllByRole("note");
    const cleartext = notes.find((n) =>
      /cleartext export/i.test(n.textContent ?? ""),
    );
    expect(cleartext).toBeTruthy();
  });

  it("keeps the include-passwords opt-in control (behavior preserved)", () => {
    mount();
    // The opt-in label text is unchanged and still present.
    expect(
      screen.getByText(/the file will contain secrets\s+in clear text/i),
    ).toBeTruthy();
    // and the checkbox is still rendered.
    const cb = document.querySelector('input[type="checkbox"]');
    expect(cb).toBeTruthy();
  });

  it("keeps the export link present", () => {
    mount();
    expect(screen.getByRole("link", { name: /export config/i })).toBeTruthy();
  });
});
