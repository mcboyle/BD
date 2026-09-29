// tpl95-bang-2: the card names an applied template and the learned roles it merged,
// instead of "No reviewed template for this host yet."
import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiGet: apiGetMock, apiPost: vi.fn(), ApiError: class extends Error {} }));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { SiteTemplateCard } from "./SiteTemplateCard";

const BASE = {
  ok: true,
  site: "79b1c15f",
  url: "https://www.bang.com/login",
  template: { enabled: false, host: null, selectors: [], resolutions: [], patterns: [] },
  onboarding: null,
  auto_teach_first_run: false,
  template_auto_detect_mode: null,
  label: "User template applied: A1A bang O1517",
};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SiteTemplateCard siteId="79b1c15f" /></QueryClientProvider>);
}
beforeEach(() => apiGetMock.mockReset());

describe("SiteTemplateCard applied template (tpl95-bang-2)", () => {
  it("lists the roles the applied user template merged", async () => {
    apiGetMock.mockResolvedValue({
      ...BASE,
      applied_template: { id: "user_a1a", name: "A1A bang O1517", source: "user",
        roles: ["row_selectors", "trigger_selectors", "url_attribute"] },
    });
    mount();
    await waitFor(() => expect(screen.getByText("User template applied: A1A bang O1517")).toBeInTheDocument());
    expect(screen.getByText(
      "3 learned roles from the user template (row_selectors, trigger_selectors, url_attribute) · auto-teach off",
    )).toBeInTheDocument();
    expect(screen.queryByText("No reviewed template for this host yet.")).not.toBeInTheDocument();
  });

  it("keeps the no-template line when the applied template merged no roles", async () => {
    apiGetMock.mockResolvedValue({
      ...BASE, label: "No reviewed template",
      applied_template: { id: "<auto-detect>", name: "<auto-detect>", source: "unknown", roles: [] },
    });
    mount();
    await waitFor(() => expect(screen.getByText("No reviewed template for this host yet.")).toBeInTheDocument());
  });
});
