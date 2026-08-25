// Row 183: exercise the one-interaction safety boundary.  The retired gate
// searched raw source spelling; this spec asks the rendered components whether
// crash-file deletion, overwrite NFO regeneration, or bulk site import reached
// a transport before confirmation.  See tests/test_t3_t4_wired.py for the
// explicitly bounded event/transport evasion surface.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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
import { renderWired } from "@/test/wiredGateHarness";

type RequestRecord = {
  method: string;
  path: string;
  body: unknown;
};

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

function parsedFetchBody(init: RequestInit | undefined): unknown {
  if (typeof init?.body !== "string") return init?.body;
  try {
    return JSON.parse(init.body);
  } catch {
    return init.body;
  }
}

function records(): RequestRecord[] {
  return [
    ...apiGetMock.mock.calls.map(([path]) => ({ method: "GET", path: String(path), body: undefined })),
    ...apiPostMock.mock.calls.map(([path, body]) => ({ method: "POST", path: String(path), body })),
    ...apiDeleteMock.mock.calls.map(([path]) => ({ method: "DELETE", path: String(path), body: undefined })),
    ...fetchMock.mock.calls.map(([input, init]) => ({
      method: String((init as RequestInit | undefined)?.method ?? "GET").toUpperCase(),
      path: typeof input === "string" ? input : (input as Request).url,
      body: parsedFetchBody(init as RequestInit | undefined),
    })),
  ];
}

function pathname(raw: string): string {
  return new URL(raw, "http://bd.test").pathname;
}

function isDangerous(record: RequestRecord): boolean {
  if (record.method !== "POST") return false;
  const path = pathname(record.path);
  if (path === "/api/crash_recovery/delete") return true;
  if (path === "/api/sites/bulk_csv") return true;
  if (path !== "/api/library/regen_nfos") return false;
  return typeof record.body === "object"
    && record.body !== null
    && (record.body as Record<string, unknown>).overwrite === true;
}

function dangerousCalls(): RequestRecord[] {
  return records().filter(isDangerous);
}

function mainRoot(container: HTMLElement): HTMLElement {
  const root = container.querySelector<HTMLElement>("main");
  expect(root, "the route's own <main> subtree is absent").toBeTruthy();
  return root as HTMLElement;
}

function clearTransport() {
  apiDeleteMock.mockClear();
  apiGetMock.mockClear();
  apiPostMock.mockClear();
  fetchMock.mockClear();
}

type BoundProps = Record<string, ((event: Record<string, unknown>) => unknown) | unknown>;

const BOUND_STIMULI = [
  { prop: "onPointerDown", type: "pointerdown", key: "" },
  { prop: "onMouseDown", type: "mousedown", key: "" },
  { prop: "onClick", type: "click", key: "" },
  { prop: "onMouseUp", type: "mouseup", key: "" },
  { prop: "onPointerUp", type: "pointerup", key: "" },
  { prop: "onKeyDown", type: "keydown", key: "Enter" },
  { prop: "onKeyDown", type: "keydown", key: " " },
  { prop: "onKeyUp", type: "keyup", key: "Enter" },
] as const;

function reactProps(element: HTMLElement): BoundProps {
  const key = Object.getOwnPropertyNames(element).find((name) => name.startsWith("__reactProps$"));
  expect(key, `React attached no runtime props to ${element.outerHTML.slice(0, 160)}`).toBeTruthy();
  return (element as unknown as Record<string, BoundProps>)[key as string];
}

async function stimulateEveryBoundHandler(root: HTMLElement): Promise<number> {
  // Runtime-bound handlers, not a tag/role selector: a plain
  // <div onMouseDown> is in this population. SVG and aria-hidden descendants
  // are presentational and their owning controls are covered instead.
  await waitFor(() => expect(
    root.querySelector('[class*="animate-pulse"]'),
    "a loading skeleton kept the safety denominator volatile",
  ).toBeNull());
  const elements = [root, ...root.querySelectorAll<HTMLElement>("*")]
    .filter((element) => (
      element instanceof HTMLElement
      && element.getAttribute("aria-hidden") !== "true"
      && element.closest("svg") === null
    ));
  const bound = elements
    .map((element) => ({ element, props: reactProps(element) }))
    .filter(({ props }) => BOUND_STIMULI.some(({ prop }) => typeof props[prop] === "function"));
  const expectedStimuli = bound.reduce(
    (total, { props }) => total + BOUND_STIMULI.filter(({ prop }) => typeof props[prop] === "function").length,
    0,
  );
  expect(bound.length).toBeGreaterThan(0);
  expect(expectedStimuli).toBeGreaterThan(0);
  let stimuli = 0;

  for (let index = 0; index < bound.length; index += 1) {
    const { element, props } = bound[index];
    for (const stimulus of BOUND_STIMULI) {
      const handler = props[stimulus.prop];
      if (typeof handler !== "function") continue;
      const event = {
        type: stimulus.type,
        key: stimulus.key,
        code: stimulus.key === " " ? "Space" : stimulus.key,
        target: element,
        currentTarget: element,
        preventDefault: vi.fn(),
        stopPropagation: vi.fn(),
        nativeEvent: new Event(stimulus.type),
      };
      await act(async () => {
        await handler(event);
        stimuli += 1;
      });
      expect(
        dangerousCalls(),
        `bound ${stimulus.prop} #${index} <${element.tagName.toLowerCase()}> dispatched a dangerous selection write`,
      ).toEqual([]);
      await act(async () => {
        fireEvent.keyDown(document, { key: "Escape", code: "Escape" });
      });
    }
  }

  expect(stimuli).toBe(expectedStimuli);
  return bound.length;
}

beforeEach(() => {
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
    path === "/api/sites/bulk_csv" ? { ok: true, results: [] } : { ok: true },
  ));
  fetchMock.mockResolvedValue({
    ok: true,
    json: async () => ({}),
    text: async () => "",
  } as Response);
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("T3/T4 dangerous selection writes require confirmation", () => {
  it("defines three exact hazards and rejects near-miss negative controls", () => {
    const positives: RequestRecord[] = [
      { method: "POST", path: "/api/crash_recovery/delete", body: { path: "/x.part" } },
      { method: "POST", path: "/api/library/regen_nfos", body: { overwrite: true } },
      { method: "POST", path: "/api/sites/bulk_csv", body: { csv: "x" } },
    ];
    const negatives: RequestRecord[] = [
      { method: "POST", path: "/api/crash_recovery/ignore", body: { path: "/x.part" } },
      { method: "POST", path: "/api/library/regen_nfos", body: { overwrite: false, dry_run: false } },
      { method: "GET", path: "/api/sites/bulk_csv", body: undefined },
    ];
    expect(positives).toHaveLength(3);
    expect(positives.filter(isDangerous)).toHaveLength(3);
    expect(negatives.filter(isDangerous)).toEqual([]);
  });

  it("catches overwrite regen behind any one pointer/keyboard handler in Library", async () => {
    const user = userEvent.setup();
    const first = renderWired(<Library />, "/library");
    expect(await screen.findByRole("heading", { name: "Library audit" })).toBeInTheDocument();

    // Instrument liveness: a known safe one-click sibling must reach the same
    // mutation/transport seam exactly once.
    await user.click(screen.getByRole("button", { name: "Preview regen (dry run)" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith(
      "/api/library/regen_nfos",
      { dry_run: true },
    ));
    expect(apiPostMock.mock.calls.filter(([path]) => path === "/api/library/regen_nfos")).toHaveLength(1);
    expect(dangerousCalls()).toEqual([]);

    first.unmount();
    clearTransport();
    const swept = renderWired(<Library />, "/library");
    expect(await screen.findByRole("heading", { name: "Library audit" })).toBeInTheDocument();
    clearTransport();
    const population = await stimulateEveryBoundHandler(mainRoot(swept.container));
    expect(population).toBeGreaterThan(3);
    expect(records().some((record) =>
      pathname(record.path) === "/api/library/regen_nfos"
      && (record.body as Record<string, unknown> | undefined)?.dry_run === true,
    )).toBe(true);
  });

  it("catches crash-file delete from any one pointer/keyboard handler in Maintenance", async () => {
    const user = userEvent.setup();
    const first = renderWired(<Maintenance />, "/maintenance");
    expect(await screen.findByText("/downloads/example.part")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(dangerousCalls()).toEqual([]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("This is destructive and cannot be undone. Proceed?"))
      .toBeInTheDocument();
    expect(document.activeElement).toBe(
      within(dialog).getByRole("button", { name: "No, cancel" }),
    );
    await user.click(within(dialog).getByRole("button", { name: "Yes, proceed" }));
    await waitFor(() => expect(dangerousCalls()).toHaveLength(1));
    expect(dangerousCalls()[0]).toEqual({
      method: "POST",
      path: "/api/crash_recovery/delete",
      body: { path: "/downloads/example.part" },
    });

    first.unmount();
    clearTransport();
    const swept = renderWired(<Maintenance />, "/maintenance");
    expect(await screen.findByText("/downloads/example.part")).toBeInTheDocument();
    clearTransport();
    const population = await stimulateEveryBoundHandler(mainRoot(swept.container));
    expect(population).toBeGreaterThan(3);
    expect(dangerousCalls()).toEqual([]);
  });

  it("catches bulk site import from any one pointer/keyboard handler in Imports", async () => {
    const user = userEvent.setup();
    const csv = "fixture,https://fixture.test,default";
    const first = renderWired(<ImportsCenter />, "/imports");
    expect(await screen.findByRole("heading", { name: "Bulk site import" })).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("name,login_url,template,…"), csv);
    await user.click(screen.getByRole("button", { name: "Import sites" }));
    expect(dangerousCalls()).toEqual([]);
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(dangerousCalls()).toHaveLength(1));
    expect(dangerousCalls()[0]).toEqual({
      method: "POST",
      path: "/api/sites/bulk_csv",
      body: { csv },
    });

    first.unmount();
    clearTransport();
    const swept = renderWired(<ImportsCenter />, "/imports");
    expect(await screen.findByRole("heading", { name: "Bulk site import" })).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("name,login_url,template,…"), {
      target: { value: csv },
    });
    clearTransport();
    const population = await stimulateEveryBoundHandler(mainRoot(swept.container));
    expect(population).toBeGreaterThan(3);
    expect(dangerousCalls()).toEqual([]);
  });
});
