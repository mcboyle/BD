import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiGet: apiGetMock, apiPost: vi.fn(), ApiError: class extends Error {} }));

import Budget from "./Budget";

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><Budget /></MemoryRouter></QueryClientProvider>);
}
beforeEach(() => { apiGetMock.mockReset(); });

describe("Budget (380)", () => {
  it("renders inside the AppShell chrome (dark bg fills the route, 3d)", () => {
    apiGetMock.mockResolvedValue({ site_id: "ex.com", history: [] });
    mount();
    // AppShell's mobile branch renders PageHeader's <header> (role=banner).
    // A bare <div className="space-y-4 p-4"> route has no banner -> RED pre-fix.
    expect(screen.getByRole("banner")).toBeTruthy();
  });
  it("loads history from GET /api/daily_budget/history/<sid> and renders rows", async () => {
    apiGetMock.mockResolvedValue({ site_id: "ex.com", history: [{ ymd: "2026-06-24", bytes: 1048576 }] });
    mount();
    fireEvent.change(screen.getByLabelText("site id"), { target: { value: "ex.com" } });
    fireEvent.click(screen.getByRole("button", { name: /load/i }));
    await waitFor(() =>
      expect(apiGetMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/daily_budget/history/ex.com"),
        expect.anything(),
      ),
    );
    expect(await screen.findByText(/2026-06-24/)).toBeTruthy();
  });
});
