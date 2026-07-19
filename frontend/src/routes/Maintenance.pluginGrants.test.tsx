import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Maintenance } from "./Maintenance";

// v3.66.775: V3-A grant-UI surfacing. The Plugin settings panel renders a
// per-capability grant toggle for each GATED capability (derived from
// /api/plugins/status `gated_capabilities`, never hardcoded), seeded from the
// config's granted_capabilities, and shows each discovered plugin's declared
// capabilities + its skip reason when the load was denied.

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/plugins/status")) {
        return Promise.resolve(
          jsonResponse({
            gated_capabilities: ["lifecycle", "page_access"],
            granted_capabilities: ["lifecycle"],
            // v3.66.779: W6 operator-forced isolation, surfaced per plugin.
            force_isolated: ["pa.py"],
            loaded: [
              {
                filename: "life.py",
                ok: true,
                skipped_reason: "",
                manifest: { name: "life", capabilities: ["lifecycle"] },
              },
              {
                filename: "pa.py",
                ok: false,
                skipped_reason:
                  "requires ungranted capability ['page_access'] -- grant it via " +
                  "plugins.json granted_capabilities or enable full-access",
                manifest: { name: "pa", capabilities: ["page_access"] },
              },
            ],
          }),
        );
      }
      if (url.includes("/api/plugins/config")) {
        return Promise.resolve(
          jsonResponse({
            ok: true,
            config: {
              enabled: null,
              disabled: [],
              order: [],
              allow_full_access: false,
              node_bin: "",
              granted_capabilities: ["lifecycle"],
            },
            discovered: ["life.py", "pa.py"],
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

describe("Maintenance plugin capability grants (v3.66.775)", () => {
  it("renders a grant toggle per gated capability, seeded from config", async () => {
    mount();
    const life = (await screen.findByLabelText("grant-lifecycle")) as HTMLInputElement;
    const pa = (await screen.findByLabelText("grant-page_access")) as HTMLInputElement;
    expect(life.checked).toBe(true); // seeded from granted_capabilities
    expect(pa.checked).toBe(false); // deny-by-default visible
  });

  it("shows a denied plugin's skip reason and its declared capabilities", async () => {
    mount();
    // the denied plugin surfaces WHY it was skipped
    expect(
      await screen.findByText(/requires ungranted capability/),
    ).toBeInTheDocument();
    // declared capability chips render (gated cap on the denied plugin)
    expect(await screen.findByTestId("plugin-caps-pa.py")).toHaveTextContent(
      "page_access",
    );
  });
});

describe("Maintenance plugin force_isolated surfacing (v3.66.779)", () => {
  it("badges a plugin the operator forced to isolation", async () => {
    mount();
    // pa.py is in status.force_isolated -> a forced-isolation badge renders
    const badge = await screen.findByTestId("plugin-force-isolated-pa.py");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent(/forced-isolated/i);
  });

  it("does not badge a plugin that is not force-isolated", async () => {
    mount();
    // life.py is NOT in force_isolated; wait for the panel to paint via pa.py
    await screen.findByTestId("plugin-force-isolated-pa.py");
    expect(
      screen.queryByTestId("plugin-force-isolated-life.py"),
    ).not.toBeInTheDocument();
  });

  it("badges every plugin when force_isolated is ['*']", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/plugins/status")) {
          return Promise.resolve(
            jsonResponse({
              gated_capabilities: ["lifecycle", "page_access"],
              granted_capabilities: ["lifecycle"],
              force_isolated: ["*"],
              loaded: [],
            }),
          );
        }
        if (url.includes("/api/plugins/config")) {
          return Promise.resolve(
            jsonResponse({
              ok: true,
              config: {
                enabled: null,
                disabled: [],
                order: [],
                allow_full_access: false,
                node_bin: "",
                granted_capabilities: ["lifecycle"],
              },
              discovered: ["life.py", "pa.py"],
              full_access_enabled: false,
            }),
          );
        }
        return new Promise<Response>(() => {});
      }),
    );
    mount();
    // "*" forces ALL discovered plugins -> both are badged
    expect(
      await screen.findByTestId("plugin-force-isolated-life.py"),
    ).toBeInTheDocument();
    expect(
      await screen.findByTestId("plugin-force-isolated-pa.py"),
    ).toBeInTheDocument();
  });
});
