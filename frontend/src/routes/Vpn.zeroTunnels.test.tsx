import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: vi.fn(),
  apiPut: vi.fn(),
  apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));

import { Vpn } from "./Vpn";

// F031 (O1671 r8b) — with 0 tunnels registered, "Toggle auto-recover" and
// "Auto-blacklist now" have nothing to act on; they must be disabled and the
// page must say why.

const ONE_TUNNEL = { tunnel_id: "t1", name: "wg-1", provider: "mullvad", backend: "wireguard", state: "down" };

function stubApi(tunnels: unknown[]) {
  apiGetMock.mockImplementation((url: string) => {
    if (url === "/api/vpn/status") return Promise.resolve({ tunnels, kill_states: [], providers: [] });
    if (url === "/api/vpn/kill_switch/state") return Promise.resolve({ auto_recover: false });
    if (url === "/api/vpn/blacklist") return Promise.resolve({ blacklist: [] });
    return Promise.resolve({});
  });
}

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/vpn"]}>
        <Vpn />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiGetMock.mockReset();
});

describe("Vpn with zero tunnels (F031)", () => {
  it("disables 'Toggle auto-recover' and 'Auto-blacklist now' and explains why", async () => {
    stubApi([]);
    mount();
    await screen.findByText("No tunnels registered.");
    await waitFor(() => expect(screen.getByText("Auto-recover:").querySelector("b")).toHaveTextContent("off"));
    expect(screen.getByRole("button", { name: "Toggle auto-recover" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Auto-blacklist now" })).toBeDisabled();
    expect(screen.getAllByText(/register a tunnel first/i).length).toBeGreaterThanOrEqual(2);
  });

  it("negative control: with a tunnel registered both buttons stay enabled and no hint shows", async () => {
    stubApi([ONE_TUNNEL]);
    mount();
    await screen.findAllByText("wg-1");
    await waitFor(() => expect(screen.getByRole("button", { name: "Toggle auto-recover" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Auto-blacklist now" })).toBeEnabled();
    expect(screen.queryByText(/register a tunnel first/i)).toBeNull();
  });
});
