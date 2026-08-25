// T5/T6/row-U endpoint contract, exercised through the real hooks and route.
// The old gate credited 30 families when their string literals appeared in
// source. These tests invoke the hooks/components and reconcile the paths the
// transport was actually handed against an independent, pinned denominator.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const {
  apiGetMock,
  apiPostMock,
  apiPutMock,
  apiPatchMock,
  apiDeleteMock,
  apiPostFormMock,
  apiPostDownloadMock,
  toastMock,
} = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPutMock: vi.fn(),
  apiPatchMock: vi.fn(),
  apiDeleteMock: vi.fn(),
  apiPostFormMock: vi.fn(),
  apiPostDownloadMock: vi.fn(),
  toastMock: Object.assign(vi.fn(), {
    success: vi.fn(), error: vi.fn(), message: vi.fn(), warning: vi.fn(),
  }),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: apiPutMock,
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
  apiPostForm: apiPostFormMock,
  apiPostDownload: apiPostDownloadMock,
  ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import { Integrations } from "@/routes/Integrations";
import { Maintenance } from "@/routes/Maintenance";
import { Vpn } from "@/routes/Vpn";
import { calledPaths, renderWired } from "@/test/wiredGateHarness";

const T5_FAMILIES = [
  "/api/retention/preview/*",
  "/api/retention/apply",
  "/api/retention/audit",
  "/api/rights/audit",
  "/api/rights/blocklist",
  "/api/rights/remove/*",
  "/api/scheduled_exports/add",
  "/api/scheduled_exports/list",
  "/api/scheduled_exports/remove/*",
  "/api/scheduled_exports/run_now",
  "/api/diagnostics_bundle/preview",
  "/api/diagnostics_bundle/download",
];

const T6_FAMILIES = [
  "/api/plex_advanced/status",
  "/api/plex_advanced/server_info/*",
  "/api/plex_advanced/library_stats/*",
  "/api/plex_advanced/recently_added/*",
  "/api/plex_advanced/on_deck/*",
  "/api/plex_advanced/search/*",
  "/api/tpdb/lookup/*",
  "/api/tpdb/apply/*",
  "/api/subtitles/fetch/*",
  "/api/thumbnail_sheets/contact_sheet/*",
  "/api/marketplace/export/*",
  "/api/jsonapi/probe",
  "/api/ai/status",
  "/api/ai/models",
];

const VPN_FAMILIES = [
  "/api/vpn/kill_switch/*",
  "/api/vpn/providers/*",
  "/api/vpn/settings",
];

let fetchMock: ReturnType<typeof vi.fn>;

function normalize(path: string): string {
  const plain = path.split("?")[0];
  if (/^\/api\/retention\/preview\/[^/]+$/.test(plain)) return "/api/retention/preview/*";
  if (/^\/api\/rights\/remove\/[^/]+$/.test(plain)) return "/api/rights/remove/*";
  if (/^\/api\/scheduled_exports\/remove\/[^/]+$/.test(plain)) return "/api/scheduled_exports/remove/*";
  if (/^\/api\/plex_advanced\/(server_info|library_stats|recently_added|on_deck|search)\/[^/]+$/.test(plain)) {
    return `${plain.slice(0, plain.lastIndexOf("/"))}/*`;
  }
  if (/^\/api\/(tpdb\/(lookup|apply)|subtitles\/fetch|thumbnail_sheets\/contact_sheet|marketplace\/export)\/[^/]+$/.test(plain)) {
    return `${plain.slice(0, plain.lastIndexOf("/"))}/*`;
  }
  if (/^\/api\/vpn\/kill_switch\/[^/]+/.test(plain)) return "/api/vpn/kill_switch/*";
  if (/^\/api\/vpn\/providers(?:\/|$)/.test(plain)) return "/api/vpn/providers/*";
  return plain;
}

function transportPaths(): string[] {
  return [
    ...calledPaths(apiGetMock),
    ...calledPaths(apiPostMock),
    ...calledPaths(apiPutMock),
    ...calledPaths(apiPatchMock),
    ...calledPaths(apiDeleteMock),
    ...fetchMock.mock.calls.map((call) => String(call[0])),
  ];
}

function observed(declared: string[]): string[] {
  const wanted = new Set(declared);
  return [...new Set(transportPaths().map(normalize).filter((path) => wanted.has(path)))].sort();
}

beforeAll(() => {
  if (!URL.createObjectURL) URL.createObjectURL = () => "blob:fixture";
  if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {};
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = function () {};
  HTMLAnchorElement.prototype.click = function () {};
});

beforeEach(() => {
  for (const mock of [
    apiGetMock, apiPostMock, apiPutMock, apiPatchMock, apiDeleteMock,
    apiPostFormMock, apiPostDownloadMock,
  ]) mock.mockReset();
  toastMock.mockReset();
  toastMock.success.mockReset();
  toastMock.error.mockReset();
  toastMock.message.mockReset();
  toastMock.warning.mockReset();

  apiGetMock.mockImplementation((path: string) => {
    if (path === "/api/vpn/status") {
      return Promise.resolve({
        tunnels: [],
        kill_states: [{ tunnel_id: "tunnel-1", state: "killed", cycle_attempts: 0, reason: "fixture" }],
        providers: [],
        system_killswitch_active: [],
      });
    }
    if (path === "/api/vpn/kill_switch/state") return Promise.resolve({ ok: true, auto_recover: false });
    if (path === "/api/vpn/settings") return Promise.resolve({ ok: true, settings: {} });
    if (path === "/api/vpn/providers") return Promise.resolve({ ok: true, providers: [] });
    if (path === "/api/webhooks") return Promise.resolve({ ok: true, subscriptions: [] });
    if (path === "/api/retention/audit?limit=50") return Promise.resolve({ audit: [] });
    if (path === "/api/rights/audit?limit=100") return Promise.resolve({ entries: [] });
    if (path === "/api/rights/blocklist") {
      return Promise.resolve({
        blocks: [{ id: 7, kind: "hash", hash_hex: "00", reason: "fixture" }],
      });
    }
    if (path === "/api/scheduled_exports/list") {
      return Promise.resolve({
        schedules: [{
          id: 9, label: "nightly", format: "json", destination: "/tmp/export",
          cadence_hours: 24,
        }],
      });
    }
    if (path === "/api/diagnostics_bundle/preview") return Promise.resolve({ ok: true });
    if (path === "/api/retention/preview/site-one") {
      return Promise.resolve({
        site_id: "site-one", candidate_count: 1, total_bytes: 10,
        candidates: [{ id: 41, filename: "old.bin", reason: "age" }],
        retention_days: 30, retention_max_gb: 100, retention_keep_tagged_with: [],
      });
    }
    return Promise.resolve({});
  });
  apiPostMock.mockResolvedValue({ ok: true, result: { title: "match" } });
  apiPutMock.mockResolvedValue({ ok: true });
  apiPatchMock.mockResolvedValue({ ok: true });
  apiDeleteMock.mockResolvedValue({ ok: true });
  apiPostFormMock.mockResolvedValue({ ok: true });
  apiPostDownloadMock.mockResolvedValue({ ok: true });
  fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    headers: { get: () => 'attachment; filename="diagnostics.zip"' },
    blob: async () => new Blob(["fixture"]),
  }));
  vi.stubGlobal("fetch", fetchMock);
});

describe("T5/T6/row-U runtime endpoint contract", () => {
  it("pins the complete nonzero 30-family denominator", () => {
    const all = [...T5_FAMILIES, ...T6_FAMILIES, ...VPN_FAMILIES];
    expect(T5_FAMILIES).toHaveLength(12);
    expect(T6_FAMILIES).toHaveLength(14);
    expect(VPN_FAMILIES).toHaveLength(3);
    expect(all).toHaveLength(29);
    expect(new Set(all).size).toBe(all.length);
    // /api/csrf is the thirtieth family and is exercised by the dedicated
    // api-client CSRF spec, rather than by mocks that replace that client.
    expect([...all, "/api/csrf"]).toHaveLength(30);
  });

  it("executes every T5 governance family through the real Maintenance route", async () => {
    const user = userEvent.setup();
    renderWired(<Maintenance />, "/maintenance");
    await screen.findByText("Retention · preview first");

    const initialReads = [
      "/api/retention/audit",
      "/api/rights/audit",
      "/api/rights/blocklist",
      "/api/scheduled_exports/list",
    ].sort();
    await waitFor(() => expect(observed(T5_FAMILIES)).toEqual(initialReads));

    await user.type(screen.getByPlaceholderText("site id to preview"), "site-one");
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(observed(T5_FAMILIES)).toContain("/api/retention/preview/*"));

    await user.click(screen.getByRole("button", { name: "Preview inline" }));
    await waitFor(() => expect(observed(T5_FAMILIES)).toContain("/api/diagnostics_bundle/preview"));
    await user.click(screen.getByRole("button", { name: "Download zip" }));
    await waitFor(() => expect(observed(T5_FAMILIES)).toContain("/api/diagnostics_bundle/download"));

    await user.click(screen.getByRole("button", { name: "Apply (dry-run)" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(observed(T5_FAMILIES)).toContain("/api/retention/apply"));

    const removeButtons = await screen.findAllByRole("button", { name: "Remove" });
    expect(removeButtons).toHaveLength(2);
    await user.click(removeButtons[0]);
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(observed(T5_FAMILIES)).toContain("/api/rights/remove/*"));

    await user.type(screen.getByPlaceholderText("label"), "daily");
    await user.type(screen.getByPlaceholderText("destination path"), "/tmp/daily");
    await user.click(screen.getByRole("button", { name: "Add schedule" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(observed(T5_FAMILIES)).toContain("/api/scheduled_exports/add"));

    const scheduleRemove = (await screen.findAllByRole("button", { name: "Remove" }))[1];
    await user.click(scheduleRemove);
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(observed(T5_FAMILIES)).toContain("/api/scheduled_exports/remove/*"));

    await user.click(screen.getByRole("button", { name: "Run due now" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(observed(T5_FAMILIES)).toEqual([...T5_FAMILIES].sort()));
    expect(transportPaths().length).toBeGreaterThanOrEqual(T5_FAMILIES.length);
  });

  it("executes every T6 integrations family through the real Integrations route", async () => {
    const user = userEvent.setup();
    renderWired(<Integrations />, "/integrations");
    await screen.findByText(/Plex · TPDB · subtitles/);

    await waitFor(() => expect(observed(T6_FAMILIES)).toEqual([
      "/api/ai/status", "/api/plex_advanced/status",
    ]));
    await user.type(screen.getByPlaceholderText("site id"), "site-one");
    await user.click(screen.getByRole("button", { name: "Load" }));
    await waitFor(() => expect(observed(T6_FAMILIES)).toEqual(expect.arrayContaining([
      "/api/plex_advanced/server_info/*",
      "/api/plex_advanced/library_stats/*",
      "/api/plex_advanced/recently_added/*",
      "/api/plex_advanced/on_deck/*",
    ])));
    await user.type(screen.getByPlaceholderText("search query"), "needle");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(observed(T6_FAMILIES)).toContain("/api/plex_advanced/search/*"));

    await user.type(screen.getByPlaceholderText("history id"), "7");
    await user.click(screen.getByRole("button", { name: "TPDB lookup" }));
    await waitFor(() => expect(observed(T6_FAMILIES)).toContain("/api/tpdb/lookup/*"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Apply metadata" })).toBeEnabled());

    for (const name of ["Apply metadata", "Fetch subtitles", "Contact sheet"]) {
      await user.click(screen.getByRole("button", { name }));
      await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    }

    await user.type(screen.getByPlaceholderText("site id to export"), "site-one");
    await user.click(screen.getByRole("button", { name: "Export template" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));

    await user.type(screen.getByPlaceholderText(/URL to probe/), "https://example.test");
    await user.click(screen.getByRole("button", { name: "Probe" }));
    await user.click(screen.getByRole("button", { name: "List models" }));

    await waitFor(() => expect(observed(T6_FAMILIES)).toEqual([...T6_FAMILIES].sort()));
    expect(transportPaths().length).toBeGreaterThanOrEqual(T6_FAMILIES.length);
  });

  it("mounts the real VPN route and executes all three row-U families", async () => {
    renderWired(<Vpn />, "/vpn");
    await waitFor(() => expect(observed(VPN_FAMILIES)).toEqual([...VPN_FAMILIES].sort()));
    expect(calledPaths(apiGetMock)).toContain("/api/vpn/kill_switch/state");
    expect(calledPaths(apiGetMock)).toContain("/api/vpn/providers");
    expect(calledPaths(apiGetMock)).toContain("/api/vpn/settings");
  });
});

describe("T6 confirmation boundary", () => {
  it("a named-handler click can reach a mutation, so handler spelling is not a shield", async () => {
    const fired = vi.fn();
    const Fixture = () => {
      const fire = () => fired();
      return <button onClick={fire}>Handler fixture</button>;
    };
    renderWired(<Fixture />);
    await userEvent.click(screen.getByRole("button", { name: "Handler fixture" }));
    expect(fired).toHaveBeenCalledTimes(1);
  });

  it("clicking a gated Integrations write sends nothing until Confirm", async () => {
    const user = userEvent.setup();
    renderWired(<Integrations />, "/integrations");
    await screen.findByText(/Plex · TPDB · subtitles/);
    apiPostMock.mockClear();

    await user.type(screen.getByPlaceholderText("history id"), "7");
    await user.click(screen.getByRole("button", { name: "TPDB lookup" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/tpdb/lookup/7", {}));
    await waitFor(() => expect(screen.getByRole("button", { name: "Apply metadata" })).toBeEnabled());

    await user.type(screen.getByPlaceholderText("site id to export"), "site-one");
    for (const name of ["Apply metadata", "Fetch subtitles", "Contact sheet", "Export template"]) {
      apiPostMock.mockClear();
      await user.click(screen.getByRole("button", { name }));
      const armed = await screen.findByRole("dialog");
      expect(apiPostMock, `${name} dispatched before confirmation`).not.toHaveBeenCalled();
      await user.click(within(armed).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    }

    await user.click(screen.getByRole("button", { name: "Fetch subtitles" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Fetch subtitles for history row #7/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/subtitles/fetch/7", {}));
    expect(apiPostMock).toHaveBeenCalledTimes(1);
  });
});

describe("T5 confirmation boundary", () => {
  it("clicking a gated Maintenance write sends nothing until Confirm", async () => {
    const user = userEvent.setup();
    renderWired(<Maintenance />, "/maintenance");
    await screen.findByText("Retention · preview first");
    apiPostMock.mockClear();

    await user.click(screen.getByRole("button", { name: "Apply (dry-run)" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Confirm this operation.")).toBeInTheDocument();
    expect(apiPostMock).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/retention/apply", { dry_run: true }),
    );
    expect(apiPostMock).toHaveBeenCalledTimes(1);
  });

  it("real retention apply is preview-bound and No-default before dispatch", async () => {
    const user = userEvent.setup();
    renderWired(<Maintenance />, "/maintenance");
    await screen.findByText("Retention · preview first");
    await user.type(screen.getByPlaceholderText("site id to preview"), "site-one");
    await user.click(screen.getByRole("button", { name: "Preview" }));
    const previewSummary = await screen.findByText(/candidates · would/);
    expect(previewSummary).toHaveTextContent("1 candidates");
    await user.click(screen.getByRole("checkbox", { name: /dry-run/ }));

    apiPostMock.mockClear();
    await user.click(screen.getByRole("button", { name: "Apply retention" }));
    let dialog = await screen.findByRole("dialog");
    const no = within(dialog).getByRole("button", { name: "No, cancel" });
    expect(document.activeElement).toBe(no);
    expect(apiPostMock).not.toHaveBeenCalled();
    await user.click(no);
    expect(apiPostMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Apply retention" }));
    dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Yes, proceed" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/retention/apply", {
        dry_run: false, confirm_ids: [41], site_id: "site-one",
      }),
    );
    expect(apiPostMock).toHaveBeenCalledTimes(1);
  });
});

describe("row-U destructive confirmation boundary", () => {
  it("kill-switch Clear is No-default and cannot dispatch before Yes", async () => {
    const user = userEvent.setup();
    renderWired(<Vpn />, "/vpn");
    const clear = await screen.findByRole("button", { name: "Clear" });
    apiPostMock.mockClear();
    await user.click(clear);
    let dialog = await screen.findByRole("dialog");
    const no = within(dialog).getByRole("button", { name: "No, cancel" });
    expect(document.activeElement).toBe(no);
    expect(apiPostMock).not.toHaveBeenCalled();
    await user.click(no);
    expect(apiPostMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Clear" }));
    dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Yes, clear kill switch" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/vpn/kill_switch/tunnel-1/clear", {}),
    );
    expect(apiPostMock).toHaveBeenCalledTimes(1);
  });
});
