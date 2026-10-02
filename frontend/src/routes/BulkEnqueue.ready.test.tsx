import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

const { apiPostMock } = vi.hoisted(() => ({ apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiGet: vi.fn(), apiPost: apiPostMock, ApiError: class extends Error {} }));

import BulkEnqueue from "./BulkEnqueue";

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><BulkEnqueue /></MemoryRouter></QueryClientProvider>);
}
beforeEach(() => { apiPostMock.mockReset(); });

// F040 (UIUX-20260928): 2 URLs + "not a url" + a blank line + a repeat of URL 1 showed "4 ready".
const PASTE = "https://test.example/a\nnot a url\n\nhttps://test.example/b\nhttps://test.example/a\n";

describe("BulkEnqueue ready count (F040)", () => {
  it("counts only valid, distinct URLs and names what it dropped", () => {
    mount();
    fireEvent.change(screen.getByLabelText("urls"), { target: { value: PASTE } });
    const label = screen.getByLabelText("urls").closest("label")!.textContent!;
    expect(label).toContain("2 ready (1 invalid, 1 duplicate)");
    expect(screen.getByRole("button", { name: /enqueue/i }).textContent).toBe("Enqueue 2 URL(s)");
  });
  it("submits only the valid, distinct URLs", async () => {
    apiPostMock.mockResolvedValue({ ok: true, site_id: "ex.com", requested: 2, added: 2, dupes: 0, skipped: 0 });
    mount();
    fireEvent.change(screen.getByLabelText("site id"), { target: { value: "ex.com" } });
    fireEvent.change(screen.getByLabelText("urls"), { target: { value: PASTE } });
    fireEvent.click(screen.getByRole("button", { name: /enqueue/i }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/bulk/enqueue", {
      site_id: "ex.com", urls: ["https://test.example/a", "https://test.example/b"],
    }));
  });
  it("disables enqueue when every line is invalid", () => {
    mount();
    fireEvent.change(screen.getByLabelText("site id"), { target: { value: "ex.com" } });
    fireEvent.change(screen.getByLabelText("urls"), { target: { value: "not a url\nftp://x.example/f\n" } });
    expect(screen.getByLabelText("urls").closest("label")!.textContent).toContain("0 ready (2 invalid)");
    expect((screen.getByRole("button", { name: /enqueue/i }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("clean paste keeps the plain count (no breakdown)", () => {
    mount();
    fireEvent.change(screen.getByLabelText("urls"), { target: { value: "https://test.example/a\nhttps://test.example/b" } });
    const label = screen.getByLabelText("urls").closest("label")!.textContent!;
    expect(label).toContain("2 ready");
    expect(label).not.toMatch(/ready \(/);
  });
});
