// T5/T6/row-U route reachability through the real App route table and inbound
// navigation controls. A route literal in source is not a rendered route.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn().mockResolvedValue({ ok: true }),
  apiPatch: vi.fn().mockResolvedValue({ ok: true }),
  apiDelete: vi.fn().mockResolvedValue({ ok: true }),
  apiPostForm: vi.fn().mockResolvedValue({ ok: true }),
  apiPostDownload: vi.fn().mockResolvedValue({ ok: true }),
  ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), message: vi.fn() }),
  Toaster: () => null,
}));

import { CommandPalette } from "@/components/CommandPalette";
import { renderAppAt, renderWired } from "@/test/wiredGateHarness";

const ROUTE_TIMEOUT = 20000;

beforeAll(() => {
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = function () {};
  if (!("ResizeObserver" in globalThis)) {
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
  }
});

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/api/auth/whoami") return Promise.resolve({ ok: true, user: null, multi_user: false });
    if (path === "/api/vpn/status") {
      return Promise.resolve({ tunnels: [], kill_states: [], providers: [], system_killswitch_active: [] });
    }
    if (path === "/api/vpn/kill_switch/state") return Promise.resolve({ ok: true, auto_recover: false });
    if (path === "/api/vpn/settings") return Promise.resolve({ ok: true, settings: {} });
    if (path === "/api/vpn/providers") return Promise.resolve({ ok: true, providers: [] });
    if (path === "/api/webhooks") return Promise.resolve({ ok: true, subscriptions: [] });
    return Promise.resolve({});
  });
  apiPostMock.mockResolvedValue({ ok: true });
});

describe("T5/T6/row-U route reachability", () => {
  it("resolves /maintenance through the real App route table", async () => {
    renderAppAt("/maintenance");
    expect(await screen.findByRole("heading", { name: "Maintenance · Diagnostics", level: 1 }, { timeout: ROUTE_TIMEOUT }))
      .toBeInTheDocument();
  }, ROUTE_TIMEOUT + 5000);

  it("resolves /integrations through the real App route table", async () => {
    renderAppAt("/integrations");
    expect(await screen.findByRole("heading", { name: "Integrations", level: 1 }, { timeout: ROUTE_TIMEOUT }))
      .toBeInTheDocument();
  }, ROUTE_TIMEOUT + 5000);

  it("resolves /vpn through the real App route table", async () => {
    renderAppAt("/vpn");
    expect(await screen.findByRole("heading", { name: "VPN", level: 1 }, { timeout: ROUTE_TIMEOUT }))
      .toBeInTheDocument();
  }, ROUTE_TIMEOUT + 5000);

  it("NEGATIVE CONTROL: an unrelated path does not render Integrations", async () => {
    renderAppAt("/cluster");
    expect(await screen.findByText(/Federation peers, edge deployment artifacts/, {}, { timeout: ROUTE_TIMEOUT }))
      .toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Integrations", level: 1 })).toBeNull();
  }, ROUTE_TIMEOUT + 5000);

  it("the command palette item navigates inbound to /integrations", async () => {
    function Location() { return <output>{useLocation().pathname}</output>; }
    renderWired(
      <>
        <CommandPalette />
        <Routes><Route path="*" element={<Location />} /></Routes>
      </>,
    );
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const item = await screen.findByText("Integrations");
    expect(item).toBeInTheDocument();
    await userEvent.click(item);
    expect(screen.getByText("/integrations")).toBeInTheDocument();
  }, ROUTE_TIMEOUT + 5000);
});
