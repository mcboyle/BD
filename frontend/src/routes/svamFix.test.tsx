import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// svam-fix (O1789 sweep spa-vpn-admin-misc, finder bd-worker-S42-D) -- the SPA halves:
//   F1/F2 Users: bootstrap is keyed on whoami.bootstrap (no account yet), and the
//         bootstrap form POSTs the "admin" role it displays.
//   F3    Vpn: system kill switch plan/apply send the VPN server endpoint; apply is real.
//   F5    Maintenance: scheduled exports render last_run_* (the list rows carry no last_status).
//   F8    Backup: a 200 ok:false smoke-restore is reported as a failure, not "done".

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock("@/lib/api-client", async (orig) => ({
  ...(await orig<object>()),
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(() => new Promise(() => {})),
  apiDelete: vi.fn(() => new Promise(() => {})),
}));
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import { Users } from "./Users";
import { Vpn } from "./Vpn";
import { Maintenance } from "./Maintenance";
import { Backup } from "./Backup";

const never = () => new Promise(() => {});

function mount(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function getRoutes(routes: Record<string, unknown>) {
  apiGetMock.mockImplementation((url: string) =>
    url in routes ? Promise.resolve(routes[url]) : never(),
  );
}

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  Object.values(toastMock).forEach((m) => m.mockReset());
});

describe("svam-fix F1/F2 Users bootstrap", () => {
  it("bootstrap (no account yet) POSTs role admin", async () => {
    getRoutes({ "/api/auth/whoami": { ok: true, multi_user: false, bootstrap: true, user: null } });
    apiPostMock.mockResolvedValue({ ok: true, user: { username: "first", role: "admin" } });
    mount(<Users />);
    await screen.findByText(/Create the first admin/);
    fireEvent.change(screen.getByPlaceholderText("username"), { target: { value: "first" } });
    fireEvent.change(screen.getByLabelText("New user password"), { target: { value: "svam-pw-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
    const [path, body] = apiPostMock.mock.calls[0];
    expect(path).toBe("/api/auth/users");
    expect(body.role, "bootstrap form displays admin but POSTs another role").toBe("admin");
  });

  it("an admin with accounts on the default config (multi_user off) is not bootstrapping", async () => {
    getRoutes({
      "/api/auth/whoami": { ok: true, multi_user: false, bootstrap: false, user: { username: "root", role: "admin" } },
      "/api/auth/users": { ok: true, users: [{ username: "root", role: "admin" }, { username: "op1", role: "operator" }] },
    });
    mount(<Users />);
    await screen.findByText("op1"); // data control: the accounts rendered
    expect(screen.queryByText(/No users exist yet/), "bootstrap copy shown while accounts exist").toBeNull();
    expect((screen.getAllByRole("combobox")[0] as HTMLSelectElement).disabled).toBe(false);
  });
});

describe("svam-fix F3 Vpn system kill switch", () => {
  it("plan and apply send the endpoint the operator typed; apply is not a dry run", async () => {
    getRoutes({
      "/api/vpn/status": {
        tunnels: [{ tunnel_id: "t1", name: "wg-1", provider: "mullvad", backend: "wireguard", state: "up" }],
        kill_states: [], providers: [], system_killswitch_available: true,
      },
      "/api/vpn/system_killswitch/available": { ok: true, available: true },
      "/api/vpn/kill_switch/state": { auto_recover: false },
      "/api/vpn/blacklist": { blacklist: [] },
    });
    apiPostMock.mockResolvedValue({ ok: true, commands: [], count: 0 });
    mount(<Vpn />);
    const ep = await screen.findByLabelText("VPN server endpoint for t1");
    const plan = screen.getByRole("button", { name: "Plan" });
    expect(plan).toBeDisabled(); // no endpoint yet -> the 400 is not reachable from the UI
    fireEvent.change(ep, { target: { value: "203.0.113.7" } });
    fireEvent.click(plan);
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledTimes(1));
    expect(apiPostMock.mock.calls[0][0]).toBe("/api/vpn/system_killswitch/t1/plan");
    expect(apiPostMock.mock.calls[0][1], "plan posted without vpn_endpoint").toEqual({ vpn_endpoint: "203.0.113.7" });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm apply" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledTimes(2));
    expect(apiPostMock.mock.calls[1][0]).toBe("/api/vpn/system_killswitch/t1/apply");
    expect(apiPostMock.mock.calls[1][1]).toEqual({ vpn_endpoint: "203.0.113.7", dry_run: false });
  });

  it("a 200 ok:false apply is reported as a failure", async () => {
    getRoutes({
      "/api/vpn/status": {
        tunnels: [{ tunnel_id: "t1", name: "wg-1", provider: "mullvad", backend: "wireguard", state: "up" }],
        kill_states: [], providers: [], system_killswitch_available: true,
      },
      "/api/vpn/system_killswitch/available": { ok: true, available: true },
    });
    apiPostMock.mockResolvedValue({ ok: false, error: "netsh refused" });
    mount(<Vpn />);
    fireEvent.change(await screen.findByLabelText("VPN server endpoint for t1"), { target: { value: "203.0.113.7" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm apply" }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(String(toastMock.error.mock.calls[0][0])).toContain("netsh refused");
    expect(toastMock.success).not.toHaveBeenCalled();
  });
});

describe("svam-fix F5 Maintenance scheduled exports", () => {
  it("renders the last run the list rows carry", async () => {
    getRoutes({
      "/api/scheduled_exports/list": {
        schedules: [
          { id: 1, label: "nightly", format: "csv", destination: "/x", cadence_hours: 24,
            last_run_ts: 1760000000, last_run_ok: 0, last_run_message: "disk full" },
          { id: 2, label: "fresh", format: "json", destination: "/y", cadence_hours: 6, last_run_ts: 0 },
        ],
      },
    });
    mount(<Maintenance />);
    await screen.findByText("nightly");
    const row = screen.getByText("nightly").closest("tr")!;
    expect(row.textContent, "a failed run still reads 'never run'").toMatch(/failed .*disk full/);
    expect(screen.getByText("fresh").closest("tr")!.textContent).toContain("never run"); // control
  });
});

describe("svam-fix F8 Backup smoke-restore", () => {
  async function runSmoke(res: unknown) {
    apiPostMock.mockResolvedValue(res);
    getRoutes({});
    const v = mount(<Backup />);
    fireEvent.change(screen.getByPlaceholderText("backup path on host"), { target: { value: "/srv/b.zip" } });
    fireEvent.click(screen.getByRole("button", { name: /Smoke-restore \(sandbox\)/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/backup/smoke_restore", { path: "/srv/b.zip" }));
    await waitFor(() => expect(toastMock.success.mock.calls.length + toastMock.error.mock.calls.length).toBe(1));
    v.unmount();
  }

  it("ok:false is a failure toast, ok:true still says done", async () => {
    await runSmoke({ ok: false, error: "sandbox restore failed: bad zip" });
    expect(toastMock.success, "a failed smoke-restore toasted done").not.toHaveBeenCalled();
    expect(String(toastMock.error.mock.calls[0][0])).toContain("bad zip");
    Object.values(toastMock).forEach((m) => m.mockReset());
    await runSmoke({ ok: true, state: "ok" }); // control
    expect(toastMock.success).toHaveBeenCalledWith("Smoke-restore done");
  });
});
