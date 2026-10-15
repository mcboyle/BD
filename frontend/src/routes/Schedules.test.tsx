import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

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
  it("edits a schedule's cadence + label via POST /api/schedules/:id/update (F038)", async () => {
    apiGetMock.mockResolvedValue({ ok: true, schedules: [{ id: 7, site_id: "ec82cec5", cadence_hours: 720, label: "TEST", urls: [] }] });
    apiPostMock.mockResolvedValue({ ok: true, id: 7 });
    mount();
    await screen.findByText(/ec82cec5/);
    // F038-RED: pre-fix the row has only Run now / Remove -- no Edit control.
    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    fireEvent.change(screen.getByLabelText("edit cadence hours"), { target: { value: "48" } });
    fireEvent.change(screen.getByLabelText("edit label"), { target: { value: "TEST-edited" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/schedules/7/update", { cadence_hours: 48, label: "TEST-edited" }));
    await waitFor(() => expect(screen.queryByLabelText("edit cadence hours")).toBeNull());
  });
  it("disables Save for a fractional cadence (F038 r2)", async () => {
    apiGetMock.mockResolvedValue({ ok: true, schedules: [{ id: 7, site_id: "ec82cec5", cadence_hours: 720, label: "TEST", urls: [] }] });
    mount();
    await screen.findByText(/ec82cec5/);
    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    fireEvent.change(screen.getByLabelText("edit cadence hours"), { target: { value: "1.5" } });
    expect((screen.getByRole("button", { name: /^save$/i }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("edit cadence hours"), { target: { value: "2" } });
    expect((screen.getByRole("button", { name: /^save$/i }) as HTMLButtonElement).disabled).toBe(false);
    expect(apiPostMock).not.toHaveBeenCalled();
  });
});
