// T2 / row 182 -- ENDPOINT CONSUMPTION, EXERCISED.
//
// tests/test_t2_history_wired.py used to answer "are the 12 history/logs/
// saved-search families SPA-wired?" by searching useHistoryData.ts for the
// substrings '"/api/logs/clear"' and friends. A path NAMED is not a path
// CALLED: replacing a queryFn with a stub and leaving the literal in a trailing
// comment kept that scan green while the SPA stopped talking to the endpoint.
// This spec mounts the real route, drives all four tabs, and reads the paths
// the mocked transport was actually handed.
//
// FIXTURE CONSTANTS ARE DUPLICATED PER SPEC ON PURPOSE. A shared
// historyFixtures.ts would carry all the "/api/..." literals and would be
// classified PRODUCT by tools/spa_population.py (only *.test.*/*.spec.* is
// SPEC), so the parity scanner would count a TEST FIXTURE as proof the SPA
// wires those routes -- the exact laundering v3.66.1217 closed.
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock, apiPatchMock, apiDeleteMock, toastMock } =
  vi.hoisted(() => ({
    apiGetMock: vi.fn(),
    apiPostMock: vi.fn(),
    apiPatchMock: vi.fn(),
    apiDeleteMock: vi.fn(),
    toastMock: { success: vi.fn(), error: vi.fn() },
  }));

// ALL FIVE VERBS. useHistoryData imports apiGet/apiPost/apiPatch/apiDelete;
// omitting one leaves the corresponding hook undefined and the route throws on
// mount, which would read as a product defect rather than a mock omission.
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
  ApiError: class extends Error {},
}));

vi.mock("sonner", () => ({ toast: toastMock }));

import History from "@/routes/History";
import { calledPaths, installApiFixtures, renderWired } from "@/test/wiredGateHarness";

// THE ROW'S OWN DENOMINATOR, PINNED -- and then CLOSED against the hook, which
// is the half a pure pin cannot do. Pinning alone lets a NEW family be added to
// the hook silently; deriving alone lets an evader delete a queryFn and its
// derived expectation in one edit (row 188's mutant M3). So: the pinned list is
// the verdict at runtime, and test 1 asserts the hook's own comment-stripped
// literals are EXACTLY this list.
const HOOK_FAMILIES = [
  "/api/events_all",
  "/api/history",
  "/api/history/vacuum",
  "/api/logs/clear",
  "/api/logs/tail",
  "/api/saved_searches",
  "/api/saved_searches/*", // DELETE + PATCH /api/saved_searches/{id}
  "/api/saved_searches/*/run",
  "/api/saved_searches/digest",
  "/api/search",
  "/api/session_history",
  "/api/ui_events",
  "/api/ui_events/download",
];

// /api/search/facets is consumed by the route but lives in
// components/ui/SearchFacetsStrip.tsx, not in useHistoryData.ts. It is declared
// here rather than filtered out, so the runtime verdict below stays a SET
// EQUALITY over everything /history actually asks for.
const OFF_HOOK_FAMILIES = ["/api/search/facets"];
const RUNTIME_FAMILIES = [...HOOK_FAMILIES, ...OFF_HOOK_FAMILIES];

// Everything /history owns; /api/queue/v2 (AppShell) and /api/auth/whoami
// (AuthGate) are other components' business and are excluded by name here.
const T2_PATH_RE =
  /^\/api\/(events_all|history|logs|saved_searches|search|session_history|ui_events)(\/|$)/;

const SAVED = [
  { id: 7, name: "nightly", query: "alpha", action: "enqueue" },
  { id: 9, name: "weekly", query: "beta", action: "notify" },
];

const FIXTURES: Record<string, unknown> = {
  "/api/history?limit=200": [
    {
      id: 1,
      ts: "2026-08-29T00:00:00",
      site_name: "UltraFilms",
      status: "done",
      title: "With Leo In Bed",
      title_source: "og:title",
      filename: "download-server-name.mp4",
      file_size: 15,
      message: "",
    },
    {
      id: 2,
      ts: "2026-08-29T00:01:00",
      site_name: "Untitled",
      status: "done",
      title: "",
      title_source: "",
      filename: "untitled-file.mp4",
      file_size: 20,
      message: "",
    },
  ],
  "/api/session_history?limit=100": { events: [] },
  "/api/events_all?limit=200": { events: [] },
  "/api/logs/tail?lines=200": { lines: ["boot"], file_size: 12, current_level: "INFO" },
  "/api/search?q=alpha&limit=100": { results: [], count: 0 },
  "/api/search/facets?query=alpha": { ok: true, facets: { by_site: {}, by_status: {}, total: 0 } },
  "/api/saved_searches": { searches: SAVED },
  "/api/saved_searches/digest?hours_back=168": { searches: [{ id: 7, matches: 3 }] },
  "/api/queue/v2": { waiting: [], running: [] },
};

let fetchMock: ReturnType<typeof vi.fn>;

/** Every path the transport was handed, normalized to a family. Numeric id
 *  segments collapse to `*`; the querystring is dropped. Longest-prefix hazards
 *  do not arise because only DIGIT segments are wildcarded, so
 *  /api/saved_searches/digest is never swallowed by /api/saved_searches/*. */
function normalizeRuntime(path: string): string {
  return path.split("?")[0].replace(/\/\d+(?=\/|$)/g, "/*");
}

function observedFamilies(): string[] {
  const raw = [
    ...calledPaths(apiGetMock),
    ...calledPaths(apiPostMock),
    ...calledPaths(apiPatchMock),
    ...calledPaths(apiDeleteMock),
    ...fetchMock.mock.calls.map((c) => String(c[0])),
  ];
  return [...new Set(raw.map(normalizeRuntime).filter((p) => T2_PATH_RE.test(p)))].sort();
}

async function tab(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("tab", { name }));
}

/** Click a control, then take the confirmation dialog it arms. */
async function confirmed(
  user: ReturnType<typeof userEvent.setup>,
  trigger: HTMLElement,
  accept: "Yes, proceed" | "Confirm",
) {
  await user.click(trigger);
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: accept }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function () {};
  }
  // jsdom implements neither, and downloadUiEventsLog uses both.
  if (!URL.createObjectURL) URL.createObjectURL = () => "blob:stub";
  if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {};
});

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPatchMock.mockReset();
  apiDeleteMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
  apiPatchMock.mockResolvedValue({ ok: true });
  apiDeleteMock.mockResolvedValue({ ok: true });
  fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    blob: async () => new Blob(["log"]),
    headers: { get: () => 'attachment; filename="ui_events.log"' },
  }));
  vi.stubGlobal("fetch", fetchMock);
});

describe("T2 endpoint consumption", () => {
  it("the declared denominator is exactly the hook's own comment-stripped literals", () => {
    // VACUITY GUARDS FIRST: an empty or duplicated declaration satisfies every
    // containment claim that follows.
    expect(HOOK_FAMILIES.length).toBeGreaterThanOrEqual(12);
    expect(new Set(RUNTIME_FAMILIES).size).toBe(RUNTIME_FAMILIES.length);
    expect(RUNTIME_FAMILIES.every((f) => f.startsWith("/api/"))).toBe(true);

    // vitest runs with cwd = frontend/. import.meta.url is NOT a file: URL
    // under the jsdom transform, so the path is resolved from cwd and its
    // existence asserted -- a wrong cwd must fail loudly, not read nothing and
    // then compare an empty set.
    const hookPath = resolve(process.cwd(), "src/hooks/useHistoryData.ts");
    expect(existsSync(hookPath)).toBe(true);
    const raw = readFileSync(hookPath, "utf-8");
    // COMMENT-STRIPPED, and that is load-bearing rather than decorative: the
    // raw file carries a quoted "/api/..." inside a prose comment, so the
    // un-stripped set is 14 members and this equality would fail on correct
    // input. The `[^:]` guard keeps "https://" out of the line-comment rule.
    const stripped = raw
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/.*$/gm, "$1");
    expect(stripped.length).toBeLessThan(raw.length);

    const found = new Set<string>();
    for (const m of stripped.matchAll(/"(\/api\/[^"]+)"|`(\/api\/[^`]+)`/g)) {
      const literal = (m[1] ?? m[2]).split("?")[0].replace(/\$\{[^}]*\}/g, "*");
      found.add(literal);
    }
    expect(found.size).toBeGreaterThan(0);
    expect([...found].sort()).toEqual([...HOOK_FAMILIES].sort());
  });

  it("mounting /history reads its mount families and writes nothing but the page view", async () => {
    renderWired(<History />, "/history");
    await waitFor(() => expect(calledPaths(apiGetMock)).toContain("/api/history?limit=200"));

    // The page-view telemetry is the ONLY write a mount may perform.
    await waitFor(() => expect(calledPaths(apiPostMock)).toEqual(["/api/ui_events"]));
    expect(apiPatchMock).not.toHaveBeenCalled();
    expect(apiDeleteMock).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();

    // The search families are DISABLED until the operator types (useSearch is
    // `enabled: query.length > 0`), so their absence here is the contract.
    const mounted = observedFamilies();
    expect(mounted).toContain("/api/history");
    expect(mounted).toContain("/api/ui_events");
    expect(mounted).not.toContain("/api/search");
    expect(mounted).not.toContain("/api/search/facets");

    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3); // one exact header + two fixture rows
    const headers = within(rows[0]).getAllByRole("columnheader");
    expect(headers).toHaveLength(8);
    expect(headers.map((cell) => cell.textContent)).toEqual([
      "When",
      "Site",
      "Status",
      "Website name",
      "Source",
      "File",
      "Size",
      "Message",
    ]);
    const harvestedCells = within(rows[1]).getAllByRole("cell");
    const untitledCells = within(rows[2]).getAllByRole("cell");
    expect(untitledCells).toHaveLength(8);
    expect(harvestedCells).toHaveLength(8);
    expect(harvestedCells[3]).toHaveTextContent("With Leo In Bed");
    expect(harvestedCells[4]).toHaveTextContent("og:title");
    expect(harvestedCells[5]).toHaveTextContent("download-server-name.mp4");
    expect(untitledCells[3].textContent).toBe("");
    expect(untitledCells[4].textContent).toBe("");
    expect(untitledCells[5]).toHaveTextContent("untitled-file.mp4");
    expect(screen.getAllByText("untitled-file.mp4")).toHaveLength(1);
  });

  it("a full drive of all four tabs observes exactly the declared families", async () => {
    const user = userEvent.setup();
    renderWired(<History />, "/history");
    await waitFor(() => expect(calledPaths(apiGetMock)).toContain("/api/history?limit=200"));

    await user.type(screen.getByPlaceholderText(/Search history/), "alpha");
    await waitFor(
      () => expect(calledPaths(apiGetMock)).toContain("/api/search?q=alpha&limit=100"),
      { timeout: 5000 },
    );

    await confirmed(user, screen.getByRole("button", { name: "Compact database" }), "Yes, proceed");

    await tab(user, "Events");
    await waitFor(() => expect(calledPaths(apiGetMock)).toContain("/api/events_all?limit=200"));

    await tab(user, "Logs");
    await waitFor(() => expect(calledPaths(apiGetMock)).toContain("/api/logs/tail?lines=200"));
    await user.click(screen.getByRole("button", { name: /UI events log/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await confirmed(user, screen.getByRole("button", { name: /Clear log/ }), "Yes, proceed");

    await tab(user, "Saved searches");
    await waitFor(() => expect(calledPaths(apiGetMock)).toContain("/api/saved_searches"));
    // PRECONDITION: the fixture rows really rendered. Every saved-tab control is
    // disabled when `s.id == null`, so a row-less fixture would make the whole
    // saved half of this drive vacuous.
    expect(screen.getByText("nightly")).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("name"), "gamma");
    await user.type(screen.getByPlaceholderText("query"), "delta");
    await confirmed(user, screen.getByRole("button", { name: "Save" }), "Confirm");
    await confirmed(user, screen.getAllByRole("button", { name: /Run/ })[0], "Confirm");
    await user.click(screen.getByRole("button", { name: "→ notify" }));
    await waitFor(() => expect(apiPatchMock).toHaveBeenCalled());
    await confirmed(user, screen.getAllByRole("button", { name: "Delete" })[0], "Yes, proceed");

    await waitFor(() => {
      const observed = observedFamilies();
      expect(observed.length).toBeGreaterThan(0);
      expect(observed).toEqual([...RUNTIME_FAMILIES].sort());
    });
  }, 30000);

  it("the FTS families are consumed only once the operator types", async () => {
    const user = userEvent.setup();
    renderWired(<History />, "/history");
    await waitFor(() => expect(calledPaths(apiGetMock)).toContain("/api/history?limit=200"));
    expect(calledPaths(apiGetMock).some((p) => p.startsWith("/api/search"))).toBe(false);

    await user.type(screen.getByPlaceholderText(/Search history/), "alpha");
    await waitFor(
      () => {
        expect(calledPaths(apiGetMock)).toContain("/api/search?q=alpha&limit=100");
        expect(calledPaths(apiGetMock)).toContain("/api/search/facets?query=alpha");
      },
      { timeout: 5000 },
    );
  }, 20000);
});
