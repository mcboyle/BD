import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({ apiGetMock: vi.fn(), apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiGet: apiGetMock, apiPost: apiPostMock, ApiError: class extends Error {} }));

import Schedules from "./Schedules";

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><Schedules /></MemoryRouter></QueryClientProvider>);
}
beforeEach(() => { apiGetMock.mockReset(); apiPostMock.mockReset(); });

describe("Schedules (379)", () => {
  it("renders inside the AppShell chrome (dark bg fills the route, 3d)", () => {
    mount();
    // AppShell mobile branch renders PageHeader's <header> (role=banner).
    // A bare <div className="space-y-4 p-4"> route has no banner -> RED pre-fix.
    expect(screen.getByRole("banner")).toBeTruthy();
  });
  it("lists schedules from GET /api/schedules", async () => {
    apiGetMock.mockResolvedValue({ ok: true, schedules: [{ id: 1, site_id: "ex.com", cadence_hours: 24, label: "daily", urls: [] }] });
    mount();
    await waitFor(() => expect(apiGetMock).toHaveBeenCalledWith("/api/schedules", expect.anything()));
    expect(await screen.findByText(/ex\.com/)).toBeTruthy();
  });
  it("posts a new schedule to /api/schedules", async () => {
    apiGetMock.mockResolvedValue({ ok: true, schedules: [] });
    apiPostMock.mockResolvedValue({ ok: true, id: 2 });
    mount();
    fireEvent.change(screen.getByLabelText("site id"), { target: { value: "foo.com" } });
    fireEvent.change(screen.getByLabelText("cadence hours"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: /add schedule/i }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/schedules", expect.objectContaining({ site_id: "foo.com", cadence_hours: 12 })));
  });
});
