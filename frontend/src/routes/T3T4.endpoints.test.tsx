// Row 183: the 23-family T3/T4 wiring claim is runtime behaviour.  Render the
// real route components, drive the controls, and reconcile what reached the
// transport/browser download seam.  A literal left in a comment or an unused
// exported hook contributes nothing here.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const {
  apiDeleteMock,
  apiGetMock,
  apiPostMock,
  fetchMock,
  toastMock,
} = vi.hoisted(() => ({
  apiDeleteMock: vi.fn(),
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  fetchMock: vi.fn(),
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock("@/lib/api-client", () => ({
  apiDelete: apiDeleteMock,
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import { Library } from "@/routes/Library";
import { ImportsCenter } from "@/routes/ImportsCenter";
import { Maintenance } from "@/routes/Maintenance";
import { RebalanceCenter } from "@/routes/RebalanceCenter";
import { renderWired } from "@/test/wiredGateHarness";

const T3 = [
  "POST /api/library/audit",
  "POST /api/library/orphans",
  "POST /api/library/regen_nfos",
  "GET /api/library/stats",
  "POST /api/tags/add",
  "POST /api/tags/for_many",
  "POST /api/tags/remove",
  "POST /api/tags/rename",
  "GET /api/tags/rows/*",
  "GET /api/tags/suggest/*",
  "GET /api/scene_score/bottom",
  "POST /api/storage_rebalance/inventory",
] as const;

const T4 = [
  "POST /api/sites/bulk_csv",
  "GET /api/sites/csv_template",
  "GET /api/sites/xlsx_template",
  "POST /api/runners/pause_all",
  "POST /api/runners/resume_all",
  "POST /api/concurrent/*",
  "GET /api/rate_limit/status",
  "GET /api/retry_policy",
  "GET /api/crash_recovery/scan",
  "POST /api/crash_recovery/*",
  "POST /api/file/reveal",
] as const;

const LIBRARY = T3.filter((family) => family !== "POST /api/storage_rebalance/inventory");
const REBALANCE = ["POST /api/storage_rebalance/inventory"];
const MAINTENANCE = T4.filter((family) => !family.startsWith("GET /api/sites/") && family !== "POST /api/sites/bulk_csv");
const IMPORTS = T4.filter((family) => family.startsWith("GET /api/sites/") || family === "POST /api/sites/bulk_csv");

const downloads: string[] = [];

const GET_FIXTURES: Record<string, unknown> = {
  "/api/queue/v2": { waiting: [], running: [] },
  "/api/library/browse?limit=200": { rows: [] },
  "/api/library/tags": { tags: [] },
  "/api/library/scan/status": { scan: { state: "idle" } },
  "/api/library/stats": { stats: {} },
  "/api/scene_score/bottom?limit=20": { scenes: [] },
  "/api/rate_limit/status": { domains: {}, global: {} },
  "/api/retry_policy": { classes: {} },
  "/api/crash_recovery/scan": {
    orphans: [{ path: "/downloads/example.part", size: 12 }],
  },
  "/api/captcha/pending": { pending: [] },
};

function normalisePath(raw: string): string {
  const path = new URL(raw, "http://bd.test").pathname;
  if (/^\/api\/tags\/rows\/[^/]+$/.test(path)) return "/api/tags/rows/*";
  if (/^\/api\/tags\/suggest\/[^/]+$/.test(path)) return "/api/tags/suggest/*";
  if (/^\/api\/concurrent\/[^/]+$/.test(path)) return "/api/concurrent/*";
  if (/^\/api\/crash_recovery\/(?!scan$)[^/]+$/.test(path)) {
    return "/api/crash_recovery/*";
  }
  return path;
}

function calledFamilies(): string[] {
  const calls = [
    ...apiGetMock.mock.calls.map(([path]) => `GET ${normalisePath(String(path))}`),
    ...apiPostMock.mock.calls.map(([path]) => `POST ${normalisePath(String(path))}`),
    ...apiDeleteMock.mock.calls.map(([path]) => `DELETE ${normalisePath(String(path))}`),
    ...fetchMock.mock.calls.map(([input, init]) => {
      const method = String((init as RequestInit | undefined)?.method ?? "GET").toUpperCase();
      const path = typeof input === "string" ? input : (input as Request).url;
      return `${method} ${normalisePath(path)}`;
    }),
    ...downloads.map((path) => `GET ${normalisePath(path)}`),
  ];
  return [...new Set(calls)].sort();
}

function expectExactly(expected: readonly string[]) {
  const target = new Set([...T3, ...T4]);
  const observed = calledFamilies().filter((family) => target.has(family as never));
  expect(observed).toEqual([...expected].sort());
}

async function confirmCurrent(user: ReturnType<typeof userEvent.setup>) {
  const dialog = await screen.findByRole("dialog");
  const button = within(dialog).queryByRole("button", { name: "Yes, proceed" })
    ?? within(dialog).getByRole("button", { name: "Confirm" });
  await user.click(button);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

async function clickWhenEnabled(user: ReturnType<typeof userEvent.setup>, name: string) {
  const button = screen.getByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
}

beforeEach(() => {
  downloads.length = 0;
  apiDeleteMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  fetchMock.mockReset();
  toastMock.success.mockReset();
  toastMock.error.mockReset();
  toastMock.warning.mockReset();

  apiDeleteMock.mockResolvedValue({ ok: true });
  apiGetMock.mockImplementation((path: string) => Promise.resolve(GET_FIXTURES[path] ?? {}));
  apiPostMock.mockImplementation((path: string) => Promise.resolve(
    path === "/api/concurrent/example" ? { ok: true, max_concurrent: 2 }
      : path === "/api/sites/bulk_csv" ? { ok: true, results: [] }
        : path === "/api/tags/for_many" ? { tags: { "7": ["fixture"] } }
          : { ok: true },
  ));
  fetchMock.mockResolvedValue({
    ok: true,
    json: async () => ({}),
    text: async () => "",
  } as Response);
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    downloads.push(this.getAttribute("href") ?? this.href);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("T3/T4 endpoint families execute through the SPA", () => {
  it("pins a nonzero, unique 12 + 11 = 23 method-aware denominator", () => {
    expect(T3).toHaveLength(12);
    expect(T4).toHaveLength(11);
    const population = [...T3, ...T4];
    expect(population).toHaveLength(23);
    expect(new Set(population).size).toBe(23);

    // Negative controls: near-prefixes are not parameterised-family matches.
    expect(normalisePath("/api/tags/rows_extra/x")).toBe("/api/tags/rows_extra/x");
    expect(normalisePath("/api/crash_recovery/scan")).toBe("/api/crash_recovery/scan");
  });

  it("drives all 12 Library/tag/scene/inventory families", async () => {
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    expect(await screen.findByRole("heading", { name: "Library audit" })).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Download dir to audit"), "/downloads");
    await clickWhenEnabled(user, "Run audit");
    await clickWhenEnabled(user, "Find orphans");
    await clickWhenEnabled(user, "Preview regen (dry run)");

    await user.type(screen.getByPlaceholderText("History ids (comma/space separated)"), "7 9");
    await clickWhenEnabled(user, "Load row tags");
    await user.type(screen.getByPlaceholderText("Tag"), "fixture");
    await clickWhenEnabled(user, "Add to rows");
    await confirmCurrent(user);
    await clickWhenEnabled(user, "Remove from rows");
    await confirmCurrent(user);

    await user.type(screen.getByPlaceholderText("Rename: old tag"), "old");
    await user.type(screen.getByPlaceholderText("new tag"), "new");
    await clickWhenEnabled(user, "Rename / merge");
    await confirmCurrent(user);
    await user.type(screen.getByPlaceholderText("Rows with tag…"), "fixture");
    await user.type(screen.getByPlaceholderText("Suggest tags for history id…"), "42");

    await waitFor(() => expectExactly(LIBRARY));
    expect(apiPostMock.mock.calls.filter(([path]) => String(path).startsWith("/api/")).length)
      .toBeGreaterThan(0);

    cleanup();
    apiGetMock.mockClear();
    apiPostMock.mockClear();
    apiDeleteMock.mockClear();
    downloads.length = 0;

    renderWired(<RebalanceCenter />, "/rebalance");
    expect(await screen.findByRole("heading", { name: "Disk inventory" })).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("Paths (comma separated)"), "/a,/b");
    await clickWhenEnabled(user, "Inventory");
    await waitFor(() => expectExactly(REBALANCE));
  });

  it("drives all eight Maintenance runner/control/recovery families", async () => {
    const user = userEvent.setup();
    renderWired(<Maintenance />, "/maintenance");
    expect(await screen.findByRole("heading", { name: "Runners · all sites" })).toBeInTheDocument();
    expect(await screen.findByText("/downloads/example.part")).toBeInTheDocument();

    await clickWhenEnabled(user, "Pause all");
    await confirmCurrent(user);
    await clickWhenEnabled(user, "Resume all");
    await confirmCurrent(user);

    await user.type(screen.getByPlaceholderText("site id"), "example");
    await clickWhenEnabled(user, "Set concurrency");
    await confirmCurrent(user);
    await clickWhenEnabled(user, "Resume");
    await confirmCurrent(user);
    await user.type(screen.getByPlaceholderText("Path to reveal in the host file manager"), "/downloads/a.mp4");
    await clickWhenEnabled(user, "Reveal file");
    await confirmCurrent(user);

    await waitFor(() => expectExactly(MAINTENANCE));
    expect(MAINTENANCE).toHaveLength(8);
  });

  it("drives bulk import and both browser-native template families", async () => {
    const user = userEvent.setup();
    renderWired(<ImportsCenter />, "/imports");
    expect(await screen.findByRole("heading", { name: "Bulk site import" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "CSV template" }));
    await user.click(screen.getByRole("button", { name: "XLSX template" }));
    expect(downloads).toEqual(["/api/sites/csv_template", "/api/sites/xlsx_template"]);

    await user.type(
      screen.getByPlaceholderText("name,login_url,template,…"),
      "fixture,https://fixture.test,default",
    );
    await clickWhenEnabled(user, "Import sites");
    await confirmCurrent(user);

    await waitFor(() => expectExactly(IMPORTS));
    expect(IMPORTS).toHaveLength(3);
  });
});
