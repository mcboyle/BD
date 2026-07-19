import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({ apiGetMock: vi.fn(), apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiGet: apiGetMock, apiPost: apiPostMock, ApiError: class extends Error {} }));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { SiteTemplateCard } from "./SiteTemplateCard";

const BASE = {
  ok: true,
  site: "s1",
  url: "https://auth.example/login",
  template: { enabled: false, host: null, selectors: [], resolutions: [], patterns: [] },
  onboarding: null,
  auto_teach_first_run: false,
  template_auto_detect_mode: null,
  label: "No reviewed template",
};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SiteTemplateCard siteId="s1" /></QueryClientProvider>);
}
beforeEach(() => { apiGetMock.mockReset(); apiPostMock.mockReset(); });

describe("SiteTemplateCard job-host template (3c)", () => {
  it("explains an enabled host-level template will apply at download time", async () => {
    apiGetMock.mockResolvedValue({
      ...BASE,
      download_template: { enabled: true, host: "app.reptyle.com", selectors: [], resolutions: [], patterns: [] },
    });
    mount();
    await waitFor(() =>
      expect(screen.getByTestId("download-host-template")).toHaveTextContent(
        /enabled host-level template \(app\.reptyle\.com\) will\s+apply at download time/i,
      ),
    );
  });

  it("shows no job-host line when download_template is absent", async () => {
    apiGetMock.mockResolvedValue({ ...BASE });
    mount();
    await waitFor(() => expect(screen.getByText("No reviewed template")).toBeInTheDocument());
    expect(screen.queryByTestId("download-host-template")).not.toBeInTheDocument();
  });
});

describe("SiteTemplateCard reuse onboarding session (3e/C1)", () => {
  it("posts to reuse_onboarding when the button is clicked", async () => {
    apiGetMock.mockResolvedValue({ ...BASE });
    apiPostMock.mockResolvedValue({
      ok: true,
      site: "s1",
      reused: true,
      host: "app.reptyle.com",
      seeded: [{ profile: "main", items: ["Cookies"], count: 1 }],
      skipped_reason: null,
    });
    mount();
    const btn = await screen.findByRole("button", { name: /reuse the onboarding session/i });
    await waitFor(() => expect(btn).not.toBeDisabled());
    fireEvent.click(btn);
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith(
        "/api/sites/s1/session/reuse_onboarding",
        {},
      ),
    );
  });
});
