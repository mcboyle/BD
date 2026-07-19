import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Maintenance } from "./Maintenance";

// v3.66.469: BD_PLUGINS_NODE_BIN is a real GUI control (not display-only) -- a
// "Node runtime path" text field in the Plugin settings panel, backed by
// plugins.json via GET/POST /api/plugins/config. Asserts the control renders
// and is seeded from the config value.

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/plugins/config")) {
        return Promise.resolve(
          jsonResponse({
            ok: true,
            config: { enabled: null, disabled: [], order: [], allow_full_access: false, node_bin: "/usr/bin/node" },
            discovered: [],
            full_access_enabled: false,
          }),
        );
      }
      return new Promise<Response>(() => {});
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

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

describe("Maintenance plugin node_bin control", () => {
  it("renders the node runtime path field seeded from config", async () => {
    mount();
    const input = (await screen.findByDisplayValue("/usr/bin/node")) as HTMLInputElement;
    expect(input.tagName).toBe("INPUT");
    expect(input.getAttribute("aria-label")).toBe("node_bin");
  });
});
