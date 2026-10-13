// sltm-fix (bughunt spa-library-templates-mobile F5/F6/F7, finder bd-worker-C2-C).
//
// At 390 px three no-wrap flex rows pushed the page wider than the viewport:
// /templates semantic search (Reindex r=412), /history tab header (Compact
// database r=552), /queue site search "(pick one)" select (r=401). jsdom has no
// layout, so the overflow itself is measured in Chromium (sltm-fix evidence);
// these pins hold the wrapping classes the fix relies on.
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));

import { SemanticSearchPanel } from "@/components/ui/SemanticSearchPanel";
import { SiteSearchPanel } from "@/components/ui/SiteSearchPanel";
import History from "@/routes/History";
import { installApiFixtures, renderWired } from "@/test/wiredGateHarness";

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, {});
});

describe("sltm-fix 390 px rows wrap", () => {
  it("F5 semantic search row wraps and its input may shrink", async () => {
    renderWired(<SemanticSearchPanel />, "/templates");
    const input = await screen.findByPlaceholderText("Semantic search…");
    expect(input.className).toContain("min-w-0");
    expect(input.parentElement?.className).toContain("flex-wrap");
  });

  it("F7 site search row wraps and the site select may shrink", async () => {
    renderWired(<SiteSearchPanel />, "/queue");
    const select = await screen.findByRole("combobox", { name: "Site" });
    expect(select.className).toContain("min-w-0");
    expect(select.parentElement?.className).toContain("flex-wrap");
  });

  it("F6 history tab header and its tab list wrap", async () => {
    renderWired(<History />, "/history");
    const tabs = await screen.findByRole("tablist");
    expect(tabs.className).toContain("flex-wrap");
    expect(tabs.className).toContain("h-auto");
    expect(tabs.parentElement?.className).toContain("flex-wrap");
    expect(tabs.parentElement).toContainElement(
      screen.getByRole("button", { name: "Compact database" }),
    );
  });
});
