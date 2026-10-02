import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { apiGet } from "@/lib/api-client";
import { SiteActions } from "./SiteActions";

// F011 (UIUX-20260928): /sites/<unknown-id>/actions never looked the site up and
// rendered the full action set -- incl. Destructive "Reset learned selectors" --
// with an enabled Confirm for a site that does not exist.

vi.mock("@/components/AppShell", () => ({
  AppShell: ({ title, children }: { title: string; children: ReactNode }) => (
    <main><h1>{title}</h1>{children}</main>
  ),
}));
vi.mock("@/lib/api-client", () => ({ apiPost: vi.fn(), apiGet: vi.fn(), apiDelete: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const SITES = { ok: true, count: 1, ts: 0, sites: [{ site_id: "real-site", name: "Real site" }] };

function mount(siteId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/sites/${siteId}/actions`]}>
    <Routes><Route path="/sites/:siteId/actions" element={<SiteActions />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiGet).mockResolvedValue(SITES);
});

describe("SiteActions site lookup (F011)", () => {
  it("shows a not-found state, not the action set, for an unknown site id", async () => {
    mount("no-such-site");
    expect(await screen.findByRole("alert")).toHaveTextContent("Site not found");
    expect(apiGet).toHaveBeenCalledWith("/api/sites/v2", expect.anything());
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset learned selectors" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to sites/ })).toHaveAttribute("href", "/sites");
  });

  it("renders the action set for a known site id (positive control)", async () => {
    mount("real-site");
    expect(await screen.findByRole("button", { name: "Reset learned selectors" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.queryByText("Site not found")).not.toBeInTheDocument();
  });
});
