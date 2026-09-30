import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";

// fx-im-done-wiring (O1567): the Site page shows a pending manual login with I'm Done / Cancel
// (the sites-v2 row's awaiting_manual_login), not only Site actions -> "Manual login done".
const { apiGetMock, apiPostMock } = vi.hoisted(() => ({ apiGetMock: vi.fn(), apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock, apiPost: apiPostMock, apiPut: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));

import { SiteDetail } from "./SiteDetail";

function row(awaiting: boolean) {
  return {
    site_id: "bk", name: "blacked", avatar_color: "#000", state: "idle", hold_reason: "", auth_state: "ok",
    captcha_pending: false, awaiting_manual_login: awaiting, downloaded_total: 0, active_workers: 0,
    last_event_ts: 0, last_event_age: "",
  };
}

function mount(awaiting: boolean) {
  apiGetMock.mockImplementation((path: string) =>
    Promise.resolve(path === "/api/sites/v2" ? { ok: true, sites: [row(awaiting)], count: 1, ts: 0 } : {}));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/sites/bk"]}>
        <Routes><Route path="/sites/:siteId" element={<SiteDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPostMock.mockResolvedValue({ ok: true, message: "ok" });
});

describe("SiteDetail -- pending manual login (fx-im-done-wiring)", () => {
  it("offers I'm Done (POST login_manual_done) and Cancel", async () => {
    mount(true);
    fireEvent.click(await screen.findByRole("button", { name: /^I'm Done/ }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/sites/bk/login_manual_done", {}));
    expect(screen.getByRole("button", { name: /^Cancel/ })).toBeInTheDocument();
  });

  it("control: no pending login, no I'm Done", async () => {
    mount(false);
    await screen.findAllByText("blacked");
    expect(screen.queryByRole("button", { name: /^I'm Done/ })).toBeNull();
  });
});
