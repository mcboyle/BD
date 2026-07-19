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
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

import { CaptureBrowser } from "./CaptureBrowser";

const EMPTY = {
  ok: true,
  scanned: false,
  built_at: null,
  total: 0,
  page: 1,
  per_page: 50,
  captures: [],
  summary: null,
};
const ROW = {
  rel_path: "captures/template_onboarding/app.reptyle.com_0b60f1ec_ts/x.wacz",
  name: "x.wacz",
  dir: "captures",
  host: "app.reptyle.com",
  captured_at: 1_700_000_000,
  size: 2_500_000,
  kind: "wacz",
  redacted: false,
};
const LISTED = {
  ok: true,
  scanned: true,
  built_at: 1_700_000_100,
  total: 1,
  page: 1,
  per_page: 50,
  captures: [ROW],
  summary: { ok: true, total: 1, by_host: { "app.reptyle.com": 1 }, new_since_last: 1, took_ms: 12 },
};

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <CaptureBrowser />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
});

describe("CaptureBrowser (Item 3 read-only scan + browse)", () => {
  it("shows a Scan for captures button and the not-yet-scanned hint", async () => {
    apiGetMock.mockResolvedValue({ ...EMPTY });
    mount();
    expect(
      await screen.findByRole("button", { name: /scan for captures/i }),
    ).toBeInTheDocument();
    expect(await screen.findByText(/no scan yet/i)).toBeInTheDocument();
  });

  it("POSTs /api/captures/scan when the button is clicked", async () => {
    apiGetMock.mockResolvedValue({ ...EMPTY });
    apiPostMock.mockResolvedValue({
      ok: true,
      total: 1,
      by_host: { "app.reptyle.com": 1 },
      new_since_last: 1,
      took_ms: 12,
    });
    mount();
    const btn = await screen.findByRole("button", { name: /scan for captures/i });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/captures/scan", {}),
    );
  });

  it("renders the cached inventory with host, kind and raw/redacted badge", async () => {
    apiGetMock.mockResolvedValue({ ...LISTED });
    mount();
    expect(await screen.findByText("x.wacz")).toBeInTheDocument();
    expect((await screen.findAllByText(/app\.reptyle\.com/)).length).toBeGreaterThan(0);
    expect(await screen.findByText("wacz")).toBeInTheDocument();
    // a non-redacted capture is badged "raw"
    expect(await screen.findByText("raw")).toBeInTheDocument();
  });
});
