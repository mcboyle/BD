import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// fx-im-done-wiring (O1567): bd4 blacked 04:39Z -- the takeover said "click I'm Done" but no UI rendered one, so the
// operator's click never reached POST /api/sites/<sid>/login_manual_done. The Home "Needs attention" banner now
// offers I'm Done / Cancel for a pending manual login; every other kind keeps the frozen Resolve contract.
const { apiPostMock } = vi.hoisted(() => ({ apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiPost: apiPostMock, apiGet: vi.fn(), ApiError: class extends Error {} }));

import { AttentionBanner } from "./AttentionBanner";
import type { AttentionEntry } from "@/lib/api-types";

function mount(attention: AttentionEntry[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><AttentionBanner attention={attention} /></QueryClientProvider>);
}

const pending = {
  site_id: "bk", name: "blacked", kind: "manual_login_pending",
  label: "Manual login open: finish it in the browser, then click I'm Done", since_ts: 0,
} as AttentionEntry;

beforeEach(() => {
  apiPostMock.mockReset();
  apiPostMock.mockResolvedValue({ ok: true, message: "ok" });
});

describe("AttentionBanner -- pending manual login (fx-im-done-wiring)", () => {
  it("offers I'm Done, which POSTs login_manual_done", async () => {
    mount([pending]);
    fireEvent.click(screen.getByRole("button", { name: /^I'm Done/ }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/sites/bk/login_manual_done", {}));
    expect(apiPostMock).not.toHaveBeenCalledWith("/api/dashboard/v2/resolve", expect.anything());
  });

  it("offers Cancel, which POSTs login_manual_cancel", async () => {
    mount([pending]);
    fireEvent.click(screen.getByRole("button", { name: /^Cancel/ }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/sites/bk/login_manual_cancel", {}));
  });

  it("control: any other kind keeps Resolve and no I'm Done", async () => {
    mount([{ site_id: "x", name: "x", kind: "captcha_pending", label: "Captcha pending", since_ts: 0 }]);
    expect(screen.queryByRole("button", { name: /^I'm Done/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Resolve/ }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/dashboard/v2/resolve", { site_id: "x", kind: "captcha_pending" }));
  });
});
