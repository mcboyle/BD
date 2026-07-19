import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ImportsCenter } from "./ImportsCenter";

// Cut 3 — Imports adopts the WorkflowPage spine and gains a read-only
// PREVIEW-before-apply step: the destructive template import shows
// new/changed/conflict/destructive/secrets-omitted (from
// /api/user_templates/import/preview) BEFORE the apply. The captcha queue
// stays a side card. RED on pristine 372 (no spine slots, no preview control).

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ImportsCenter />
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

describe("ImportsCenter on the WorkflowPage spine + preview (Cut 3)", () => {
  it("renders WorkflowPage slot markers", () => {
    const { container } = mount();
    expect(container.querySelector('[data-slot]')).not.toBeNull();
  });

  it("offers a read-only Preview control before applying an import", () => {
    mount();
    const previews = screen.getAllByRole("button", { name: /preview/i });
    expect(previews.length).toBeGreaterThan(0);
  });
});
