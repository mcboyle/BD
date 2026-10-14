import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({ apiGetMock: vi.fn(), apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiGet: apiGetMock, apiPost: apiPostMock, ApiError: class extends Error {} }));

import Budget from "./Budget";

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><Budget /></MemoryRouter></QueryClientProvider>);
  return qc;
}
// What the cap hook makes React Query retain: its query data plus every mutation's
// data and variables (the hook owns the only mutation on this route).
function retained(qc: QueryClient): string {
  const mutations = qc.getMutationCache().getAll();
  return JSON.stringify([
    qc.getQueryData(["global-byte-budget"]),
    mutations.length,
    mutations.map((m) => [m.state.data, m.state.variables]),
  ]);
}
const capInput = () => screen.getByLabelText("global daily byte cap") as HTMLInputElement;

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiGetMock.mockImplementation(async (path: string) =>
    path === "/api/global_config" ? { global_daily_byte_budget: 1048576 } : { site_id: "x", history: [] },
  );
});

describe("Budget F041 edit then cancel", () => {
  it("renders the stored global cap from GET /api/global_config", async () => {
    mount();
    expect((await screen.findByTestId("budget-cap-value")).textContent).toContain("1048576 bytes");
    expect(apiGetMock).toHaveBeenCalledWith("/api/global_config", expect.anything());
  });

  it("edit then cancel discards the draft and sends no write", async () => {
    mount();
    await screen.findByTestId("budget-cap-value");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(capInput().value).toBe("1048576");
    fireEvent.change(capInput(), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("global daily byte cap")).toBeNull();
    expect(screen.getByTestId("budget-cap-value").textContent).toContain("1048576 bytes");
    expect(apiPostMock).not.toHaveBeenCalled();
    // A write deferred past the click (microtask or timer) must not appear either.
    await new Promise((r) => setTimeout(r, 25));
    expect(apiPostMock).not.toHaveBeenCalled();
    // Reopening starts from the stored value, not the abandoned draft.
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(capInput().value).toBe("1048576");
  });

  it("save POSTs the edited cap and shows the stored result", async () => {
    apiPostMock.mockResolvedValue({ global_daily_byte_budget: 2048 });
    mount();
    await screen.findByTestId("budget-cap-value");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(capInput(), { target: { value: "2048" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/global_config", { global_daily_byte_budget: 2048 }),
    );
    await waitFor(() => expect(screen.getByTestId("budget-cap-value").textContent).toContain("2048 bytes"));
  });

  it("retains only the cap, never the rest of the global_config response", async () => {
    const MARK = "F041-UNRELATED-CONFIG-MARKER";
    apiGetMock.mockImplementation(async (path: string) =>
      path === "/api/global_config"
        ? { global_daily_byte_budget: 1048576, auth_token: MARK }
        : { site_id: "x", history: [] },
    );
    apiPostMock.mockResolvedValue({ global_daily_byte_budget: 2048, auth_token: MARK });
    const qc = mount();
    await screen.findByTestId("budget-cap-value");
    expect(retained(qc)).toBe("[1048576,0,[]]");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(capInput(), { target: { value: "2048" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByTestId("budget-cap-value").textContent).toContain("2048 bytes"));
    expect(apiPostMock).toHaveBeenCalledTimes(1);
    expect(retained(qc)).toBe("[2048,1,[[2048,2048]]]");
    expect(retained(qc)).not.toContain(MARK);
    expect(document.body.innerHTML).not.toContain(MARK);
  });

  it("refuses a non-integer draft instead of coercing it", async () => {
    mount();
    await screen.findByTestId("budget-cap-value");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(capInput(), { target: { value: "1.5GB" } });
    expect(screen.getByRole("alert").textContent).toMatch(/whole number of bytes/);
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(capInput().closest("form")!);
    expect(apiPostMock).not.toHaveBeenCalled();
  });
});
