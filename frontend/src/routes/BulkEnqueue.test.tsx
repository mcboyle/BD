import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const { apiPostMock } = vi.hoisted(() => ({ apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiGet: vi.fn(), apiPost: apiPostMock, ApiError: class extends Error {} }));

import BulkEnqueue from "./BulkEnqueue";

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><BulkEnqueue /></MemoryRouter></QueryClientProvider>);
}
beforeEach(() => { apiPostMock.mockReset(); });

describe("BulkEnqueue (380)", () => {
  it("renders inside the AppShell chrome (dark bg fills the route, 3d)", () => {
    mount();
    // AppShell mobile branch renders PageHeader's <header> (role=banner).
    // A bare <div className="space-y-4 p-4"> route has no banner -> RED pre-fix.
    expect(screen.getByRole("banner")).toBeTruthy();
  });
  it("posts parsed URLs to /api/bulk/enqueue and shows the result", async () => {
    apiPostMock.mockResolvedValue({ ok: true, site_id: "ex.com", requested: 2, added: 2, dupes: 0, skipped: 0 });
    mount();
    fireEvent.change(screen.getByLabelText("site id"), { target: { value: "ex.com" } });
    fireEvent.change(screen.getByLabelText("urls"), { target: { value: "https://a\nhttps://b\n" } });
    fireEvent.click(screen.getByRole("button", { name: /enqueue/i }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/bulk/enqueue", { site_id: "ex.com", urls: ["https://a", "https://b"] }));
    expect((await screen.findByTestId("bulk-enqueue-result")).textContent).toContain("added 2");
  });
  it("disables enqueue with no urls", () => {
    mount();
    fireEvent.change(screen.getByLabelText("site id"), { target: { value: "ex.com" } });
    expect((screen.getByRole("button", { name: /enqueue/i }) as HTMLButtonElement).disabled).toBe(true);
  });
});
