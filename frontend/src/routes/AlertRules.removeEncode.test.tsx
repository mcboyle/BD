import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

// scs-fix F4 + F5a (bughunt spa-cockpit-settings, finder bd-worker-S52-A).
// F4: Remove built `/api/alerts/rules/${id}/remove` from the raw id, so "s52a?x"
// became a query string (POST .../s52a?x/remove -> 404) and the rule stayed.
// F5a: a Remove that answered ok:false (nothing deleted) left the row with no
// feedback at all; built-ins (builtin:true from the server now) show no Remove.

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { apiGetMock: vi.fn(), apiPostMock: vi.fn(), toastMock: toast };
});
vi.mock("@/lib/api-client", () => ({ apiGet: apiGetMock, apiPost: apiPostMock, ApiError: class extends Error {} }));
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import AlertRules from "./AlertRules";

const RULES = [
  { id: "disk_low", name: "Disk space low", metric: "bd_disk_free_gb", op: "<=", threshold: 10, builtin: true },
  { id: "s52a?x", metric: "bd_job_failures_1h", op: ">=", threshold: 2, builtin: false },
  { id: "plain_rule", metric: "bd_job_failures_1h", op: ">=", threshold: 2, builtin: false },
];

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><AlertRules /></MemoryRouter></QueryClientProvider>);
}

function row(text: string): HTMLElement {
  return screen.getByText(text).closest("tr") as HTMLElement;
}

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  toastMock.error.mockReset();
  apiGetMock.mockResolvedValue({ rules: RULES });
});

describe("scs-fix F4: Remove encodes the rule id", () => {
  it("a '?' id posts to the percent-encoded path", async () => {
    apiPostMock.mockResolvedValue({ ok: true });
    mount();
    await screen.findByText("s52a?x");
    fireEvent.click(within(row("s52a?x")).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
    expect(apiPostMock.mock.calls[0][0], "BD-SCS-F4-RAW-ID-IN-PATH").toBe("/api/alerts/rules/s52a%3Fx/remove");
  });

  it("a plain id posts unchanged (control)", async () => {
    apiPostMock.mockResolvedValue({ ok: true });
    mount();
    await screen.findByText("plain_rule");
    fireEvent.click(within(row("plain_rule")).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/alerts/rules/plain_rule/remove", {}));
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});

describe("scs-fix F5a: Remove feedback", () => {
  it("ok:false from Remove raises an error toast", async () => {
    apiPostMock.mockResolvedValue({ ok: false });
    mount();
    await screen.findByText("plain_rule");
    fireEvent.click(within(row("plain_rule")).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(toastMock.error, "BD-SCS-F5A-SILENT-REMOVE").toHaveBeenCalledWith("Rule plain_rule was not removed"));
  });

  it("a built-in row offers no Remove", async () => {
    mount();
    await screen.findByText("Disk space low");
    expect(within(row("Disk space low")).queryByRole("button", { name: "Remove" })).toBeNull();
  });
});
