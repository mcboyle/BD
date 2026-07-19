import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Settings } from "./Settings";

// P6-4 (PHASE6_PLAN) → Cut 5: the capture/redaction toggles — the remaining
// high-risk cluster NOT under "Security & access" — are framed in a protective
// AMBER IntegrityZone (Cut 5 converged the prior Caution callout to the
// IntegrityZone grouping; same intent: "protect this", not "destroy this").
// The Capture section is collapsible (defaultOpen=false), so we drive the
// settings search (which renders sections fully) as the DOM hook, mirroring the
// real expand-on-search behavior.

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/settings"]}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // Resolve /api/global_config so the sections render (not the skeleton);
  // hold every other request pending.
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/global_config")) {
        return Promise.resolve(jsonResponse({}));
      }
      return new Promise<Response>(() => {});
    }),
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

describe("Settings — capture/redaction IntegrityZone (Cut 5)", () => {
  it("frames the Capture redaction toggles in an amber IntegrityZone", async () => {
    mount();
    // wait for config to resolve and the filter input to be available
    const filter = await screen.findByLabelText("Filter settings");
    // searching renders sections fully — reveal the (collapsed) Capture section
    fireEvent.change(filter, { target: { value: "redaction" } });

    await waitFor(() => {
      // IntegrityZone renders a <section> with the integrity title as its
      // accessible name + heading text.
      expect(
        screen.getByText(/redaction & capture integrity/i),
      ).toBeInTheDocument();
    });
  });

  it("keeps the Raw capture toggle reachable (behavior preserved)", async () => {
    mount();
    const filter = await screen.findByLabelText("Filter settings");
    fireEvent.change(filter, { target: { value: "raw capture" } });
    await waitFor(() => {
      expect(
        screen.getByText(/Raw capture \(disable redaction\)/i),
      ).toBeInTheDocument();
    });
  });
});
