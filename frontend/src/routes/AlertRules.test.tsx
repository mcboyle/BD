import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({ apiGetMock: vi.fn(), apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiGet: apiGetMock, apiPost: apiPostMock, ApiError: class extends Error {} }));

import AlertRules from "./AlertRules";

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><AlertRules /></MemoryRouter></QueryClientProvider>);
}
beforeEach(() => { apiGetMock.mockReset(); apiPostMock.mockReset(); });

describe("AlertRules (380)", () => {
  it("renders inside the AppShell chrome (dark bg fills the route, 3d)", () => {
    mount();
    // AppShell mobile branch renders PageHeader's <header> (role=banner).
    // A bare <div className="space-y-4 p-4"> route has no banner -> RED pre-fix.
    expect(screen.getByRole("banner")).toBeTruthy();
  });
  it("lists rules from GET /api/alerts/rules", async () => {
    apiGetMock.mockResolvedValue({ rules: [{ id: "high_failure_rate", name: "High failure rate", metric: "bd_failure_rate_1h", op: ">=", threshold: 25, builtin: true }] });
    mount();
    await waitFor(() => expect(apiGetMock).toHaveBeenCalledWith("/api/alerts/rules", expect.anything()));
    expect(await screen.findByText(/High failure rate/)).toBeTruthy();
  });
  it("saves a rule to POST /api/alerts/rules", async () => {
    apiGetMock.mockResolvedValue({ rules: [] });
    apiPostMock.mockResolvedValue({ ok: true, id: "r1" });
    mount();
    fireEvent.change(screen.getByLabelText("rule id"), { target: { value: "r1" } });
    fireEvent.change(screen.getByLabelText("threshold"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: /save rule/i }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/alerts/rules", expect.objectContaining({ id: "r1", threshold: 5 })));
  });
  it("test-send fires POST /api/alerts/evaluate", async () => {
    apiGetMock.mockResolvedValue({ rules: [] });
    apiPostMock.mockResolvedValue({});
    mount();
    fireEvent.click(screen.getByRole("button", { name: /test-send/i }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/alerts/evaluate", expect.anything()));
  });
});
