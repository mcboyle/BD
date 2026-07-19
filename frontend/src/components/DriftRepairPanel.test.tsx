import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), message: vi.fn() }),
}));

import { DriftRepairPanel } from "./DriftRepairPanel";

const STATUS = {
  ok: true,
  enabled: false,
  last_run: { ts: 1_700_000_000, ran: true, considered: 2, repaired: 1, skipped: 1, site_ids: ["s1"] },
  drafts_pending: 3,
};

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <DriftRepairPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
});

describe("DriftRepairPanel (Item 4 status + control)", () => {
  it("renders the last-sweep summary + pending drafts", async () => {
    apiGetMock.mockResolvedValue({ ...STATUS });
    mount();
    expect(await screen.findByText(/considered 2 \/ repaired 1 \/ skipped 1/)).toBeInTheDocument();
    expect(await screen.findByText(/3 review drafts pending/)).toBeInTheDocument();
  });

  it("POSTs the run-now endpoint with force", async () => {
    apiGetMock.mockResolvedValue({ ...STATUS });
    apiPostMock.mockResolvedValue({ ok: true, summary: { ran: true, considered: 0, repaired: 0, skipped: 0 } });
    mount();
    const btn = await screen.findByRole("button", { name: /run now/i });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/automation/drift_repair/run", { force: true }),
    );
  });

  it("POSTs the toggle endpoint when the switch changes", async () => {
    apiGetMock.mockResolvedValue({ ...STATUS });
    apiPostMock.mockResolvedValue({ ok: true, enabled: true });
    mount();
    const box = await screen.findByRole("checkbox");
    fireEvent.click(box);
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/automation/drift_repair/toggle", { enabled: true }),
    );
  });
});
