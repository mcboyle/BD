import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

import { IntegrationsHealthPanel } from "@/components/ui/IntegrationsHealthPanel";
import { SecretsUsageList } from "@/components/ui/SecretsUsageList";
import { useIntegrationsHealth, useSecretsUsage } from "@/hooks/useIntegrations";

// Cut 7 (Track A) — read-only consumers of the two new endpoints. The panels
// surface health/usage; the hooks must hit the FULL /api/ literals so the
// parity scanner counts them spa_wired and they never leak secret values.

describe("IntegrationsHealthPanel (Cut 7 / Track A)", () => {
  it("renders a row per integration with a status word", () => {
    render(
      <IntegrationsHealthPanel
        data={{
          ok: true,
          integrations: {
            ai: { ok: true, calls: 3 },
            plex: { configured: false },
          },
        }}
      />,
    );
    expect(screen.getByText("ai")).toBeInTheDocument();
    expect(screen.getByText("healthy")).toBeInTheDocument();
    expect(screen.getByText("plex")).toBeInTheDocument();
    expect(screen.getByText("not configured")).toBeInTheDocument();
  });

  it("shows an empty state when nothing is reported", () => {
    render(<IntegrationsHealthPanel data={{ ok: true, integrations: {} }} />);
    expect(screen.getByText(/no integrations reported/i)).toBeInTheDocument();
  });
});

describe("SecretsUsageList (Cut 7 / Track A)", () => {
  it("lists keys by name and their referencing sites — never a value", () => {
    const { container } = render(
      <SecretsUsageList
        data={{
          ok: true,
          stored_keys: ["plex_token", "tpdb_api_key"],
          usage: { plex_token: ["siteA", "siteB"], tpdb_api_key: [] },
          unreferenced: ["tpdb_api_key"],
        }}
      />,
    );
    expect(screen.getByText("plex_token")).toBeInTheDocument();
    expect(screen.getByText(/used by 2 sites/i)).toBeInTheDocument();
    expect(screen.getByText(/siteA, siteB/)).toBeInTheDocument();
    expect(screen.getByText("unreferenced")).toBeInTheDocument();
    // no value-shaped content
    expect(container.textContent || "").not.toMatch(/secret_value|password|bearer/i);
  });
});

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("Track A hooks hit the new endpoints", () => {
  let urls: string[];
  beforeEach(() => {
    urls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const u = typeof input === "string" ? input : input.toString();
        urls.push(u);
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ ok: true, integrations: {}, stored_keys: [], usage: {}, unreferenced: [] }),
        } as unknown as Response);
      }),
    );
  });

  it("useIntegrationsHealth GETs /api/integrations/health", async () => {
    renderHook(() => useIntegrationsHealth(), { wrapper });
    await waitFor(() =>
      expect(urls.some((u) => u.includes("/api/integrations/health"))).toBe(true),
    );
  });

  it("useSecretsUsage GETs /api/secrets/usage", async () => {
    renderHook(() => useSecretsUsage(), { wrapper });
    await waitFor(() =>
      expect(urls.some((u) => u.includes("/api/secrets/usage"))).toBe(true),
    );
  });
});
