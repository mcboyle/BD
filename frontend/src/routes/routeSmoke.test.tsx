import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Home } from "./Home";
import { Settings } from "./Settings";
import { Secrets } from "./Secrets";

// Slice-0 regression smoke (UX_IMPROVEMENT_PLAN.md): mount the operator home,
// the big Settings scroll, and a token/secret form page and assert each mounts
// without a thrown render. This is the cheap pre-slice tripwire that catches a
// crash-on-mount regression before the (expensive) chromium re-render diff.
//
// Pages fetch on mount via react-query; we hold every request pending so each
// page renders its loading/skeleton state deterministically (no network, no
// data-shape coupling) — the contract under test is "renders, doesn't throw",
// not any populated layout.

function mount(node: React.ReactNode, path: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // Hold all fetches pending → components stay in their loading state.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>(() => {})),
  );
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("route mount smoke (Slice-0 gate)", () => {
  it("mounts Home without throwing", () => {
    const { container } = mount(<Home />, "/");
    expect(container.firstChild).not.toBeNull();
  });

  it("mounts Settings without throwing", () => {
    const { container } = mount(<Settings />, "/settings");
    expect(container.firstChild).not.toBeNull();
  });

  it("mounts a form page (Secrets) without throwing", () => {
    const { container } = mount(<Secrets />, "/secrets");
    expect(container.firstChild).not.toBeNull();
  });
});
