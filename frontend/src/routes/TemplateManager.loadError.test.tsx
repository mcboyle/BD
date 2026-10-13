// sltm-fix (bughunt spa-library-templates-mobile F4, finder bd-worker-C2-C).
//
// A failed /api/template_manager load rendered "Reviewed (0) / Drafts (0) /
// No reviewed templates." -- identical to a real empty list, so an operator
// reads "templates gone" when the server only failed to answer.
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

import { TemplateManager } from "@/routes/TemplateManager";
import { installApiFixtures, renderWired } from "@/test/wiredGateHarness";

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
});

describe("sltm-fix Template Manager load failure", () => {
  it("F4 a failed template list load shows an error, not empty sections", async () => {
    installApiFixtures(apiGetMock, apiPostMock, {});
    const impl = apiGetMock.getMockImplementation()!;
    apiGetMock.mockImplementation((p: string, ...rest: unknown[]) =>
      p === "/api/template_manager"
        ? Promise.reject(new Error("HTTP 500 template store unreadable"))
        : impl(p, ...rest),
    );
    renderWired(<TemplateManager />, "/templates");
    const alert = await screen.findByText(/Couldn.t load templates/);
    expect(alert.closest("[role=alert]")).not.toBeNull();
    expect(alert.textContent).toContain("template store unreadable");
    expect(screen.queryByText("No reviewed templates.")).toBeNull();
    expect(screen.queryByText(/Reviewed \(0\)/)).toBeNull();
  });

  it("negative control: a real empty list still shows zero counts and no alert", async () => {
    installApiFixtures(apiGetMock, apiPostMock, {
      "/api/template_manager": { reviewed: [], drafts: [] },
    });
    renderWired(<TemplateManager />, "/templates");
    expect(await screen.findByText("No reviewed templates.")).toBeInTheDocument();
    expect(screen.getByText(/Reviewed \(0\)/)).toBeInTheDocument();
    expect(screen.queryByText(/Couldn.t load templates/)).toBeNull();
  });
});
