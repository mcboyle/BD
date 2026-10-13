// na-fix F11 (r2, answers lens cx155): the SPA must not turn an unreadable alert store into a clean state.
//   /notifications: GET /api/alerts/active -> 503 must render an error, never "No active alerts."
//   /alerts/rules:  GET /api/alerts/rules  -> 200 + store_error must say the saved rules are missing.
// Each refusal beside the healthy twin that proves the probe can say no.
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({ apiGetMock: vi.fn(), apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("@/components/sections/PushSection", () => ({ PushSection: () => null }));

import Notifications from "./Notifications";
import AlertRules from "./AlertRules";

const ALERTS = "/api/alerts/active?hours=24";
const FIXTURES: Record<string, unknown> = {
  "/api/auth/whoami": { ok: true, user: null, multi_user: false },
  "/api/notify/apprise/settings": { settings: { notify_apprise_urls_set: false, notify_apprise_urls_count: 0 } },
  "/api/tg/status": { available: true, running: false, allowlist_size: 0 },
  "/api/tg/settings": { settings: { tg_bot_token_set: false } },
  "/api/queue/v2": { waiting: [], running: [] },
};
const BUILTIN = { id: "high_failure_rate", name: "High failure rate", metric: "bd_failure_rate_1h", op: ">=", threshold: 25, builtin: true };

function routeGets(alerts: () => Promise<unknown>) {
  apiGetMock.mockImplementation((url: string) =>
    url === ALERTS ? alerts() : Promise.resolve(FIXTURES[url] ?? {}),
  );
}

function mount(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
}

beforeEach(() => { apiGetMock.mockReset(); apiPostMock.mockReset(); });

describe("na-fix F11 truthful alert-store state", () => {
  it("active alerts 503 renders an error, not 'No active alerts.'", async () => {
    routeGets(() => Promise.reject(new Error("503: alert event store unavailable")));
    mount(<Notifications />);
    expect(await screen.findByText(/Could not read active alerts: 503: alert event store unavailable/)).toBeTruthy();
    expect(screen.queryByText("No active alerts.")).toBeNull();
  });

  it("control: an empty alert list still reads 'No active alerts.'", async () => {
    routeGets(() => Promise.resolve({ alerts: [] }));
    mount(<Notifications />);
    expect(await screen.findByText("No active alerts.")).toBeTruthy();
    expect(screen.queryByText(/Could not read active alerts/)).toBeNull();
  });

  it("rules store_error is shown beside the built-in rules", async () => {
    apiGetMock.mockResolvedValue({ rules: [BUILTIN], store_error: "custom rules unreadable: database is locked" });
    mount(<AlertRules />);
    expect(await screen.findByText(/High failure rate/)).toBeTruthy();
    const warn = await screen.findByTestId("alert-rules-store-error");
    expect(warn.textContent).toContain("database is locked");
    expect(warn.getAttribute("role")).toBe("alert");
  });

  it("control: no store_error, no warning", async () => {
    apiGetMock.mockResolvedValue({ rules: [BUILTIN] });
    mount(<AlertRules />);
    expect(await screen.findByText(/High failure rate/)).toBeTruthy();
    await waitFor(() => expect(apiGetMock).toHaveBeenCalled());
    expect(screen.queryByTestId("alert-rules-store-error")).toBeNull();
  });
});
