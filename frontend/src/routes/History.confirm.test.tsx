// T2 / row 182 -- "NOTHING FIRES ON A SINGLE CLICK", EXERCISED.
//
// THE SCAN THIS REPLACES IS BLIND ON THE CURRENT TREE, not merely in theory.
// tests/test_t2_history_wired.py asserted
//   not re.search(r"onClick=\{[^}]*\.mutate", History.tsx)
// while History.tsx defines `onToggleAction` -- a named handler that dispatches
// savedUpdate.mutate with no dialog -- and wires it to a button as a bare
// identifier, `onClick={() => ... onToggleAction(...)}`. The text ".mutate"
// never appears inside an onClick brace, so the regex reported clean over a
// real one-click write. Every indirection of that family (named handler, prop
// wrapper, an `arm` that dispatches, [m].map((m) => m.mutate())) is a genuine
// unconfirmed destructive write it consents to.
//
// Here every gated control is clicked for real and the transport is asked
// whether anything was sent BEFORE the dialog is confirmed -- and then the
// CLOSED-ALLOWLIST SWEEP clicks every enabled button on every tab, one per
// fresh mount, and reconciles the writes that reached the transport with no
// dialog against an exact allowlist. That is the half that catches a write
// nobody thought to name.
//
// Fixture literals are duplicated per spec rather than shared: see the note at
// the top of History.endpoints.test.tsx.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock, apiPatchMock, apiDeleteMock, toastMock } =
  vi.hoisted(() => ({
    apiGetMock: vi.fn(),
    apiPostMock: vi.fn(),
    apiPatchMock: vi.fn(),
    apiDeleteMock: vi.fn(),
    toastMock: { success: vi.fn(), error: vi.fn() },
  }));

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
import { installApiFixtures, renderWired } from "@/test/wiredGateHarness";

const SAVED = [
  // BOTH lanes on purpose. The enqueue row's toggle DE-ESCALATES to notify,
  // which History.tsx applies immediately by design; the notify row's toggle
  // ESCALATES, which must arm the dialog. One row could only ever test one of
  // the two directions, and the pair is this gate's over-sensitivity control.
  { id: 7, name: "nightly", query: "alpha", action: "enqueue" },
  { id: 9, name: "weekly", query: "beta", action: "notify" },
];

const FIXTURES: Record<string, unknown> = {
  // A non-empty history page so the results table (and its sort-header buttons)
  // renders: an empty fixture shows an EmptyState instead, and the sweep's
  // population would silently shrink.
  "/api/history?limit=200": [
    { id: 1, ts: "2026-01-01 00:00", site_name: "site", status: "done", filename: "f.mp4", file_size: 10, message: "ok" },
  ],
  "/api/session_history?limit=100": { events: [] },
  "/api/events_all?limit=200": { events: [] },
  "/api/logs/tail?lines=200": { lines: ["boot"], file_size: 12, current_level: "INFO" },
  "/api/saved_searches": { searches: SAVED },
  "/api/saved_searches/digest?hours_back=168": { searches: [{ id: 7, matches: 3 }] },
  "/api/queue/v2": { waiting: [], running: [] },
};

// The ONE write this route may perform without a dialog, and the reason it may:
// History.tsx's onToggleAction de-escalates the enqueue lane back to notify,
// which is reversible and "applies immediately" by design. Stating it as an
// EXACT allowlist is what makes the sweep a closed rule rather than a blanket
// ban -- and the overcorrection mutant in
// tests/mutants/v3_66_1242_t2_history_runtime.json deletes this entry to prove
// the allowance is load-bearing rather than dead text.
const ALLOWED_IMMEDIATE_WRITES = [
  'PATCH /api/saved_searches/7 {"action":"notify"}',
];

// Telemetry, not an operator write: History mounts and posts one page_view.
// Named explicitly rather than filtered by a wildcard, and asserted to have
// happened, so the exclusion cannot quietly swallow a real write.
const PAGE_VIEW = "/api/ui_events";

const TABS = ["History & Search", "Events", "Logs", "Saved searches"];

const WRITE_BUTTONS = ["Compact database", "Clear log", "Save", "Delete"];

let fetchMock: ReturnType<typeof vi.fn>;

function writeRecords(): string[] {
  const records: string[] = [];
  for (const [path, body] of apiPostMock.mock.calls as [string, unknown][]) {
    if (path === PAGE_VIEW) continue;
    records.push(`POST ${path} ${JSON.stringify(body ?? null)}`);
  }
  for (const [path, body] of apiPatchMock.mock.calls as [string, unknown][]) {
    records.push(`PATCH ${path} ${JSON.stringify(body ?? null)}`);
  }
  for (const [path, body] of apiDeleteMock.mock.calls as [string, unknown][]) {
    records.push(`DELETE ${path} ${JSON.stringify(body ?? null)}`);
  }
  for (const call of fetchMock.mock.calls) {
    const init = call[1] as { method?: string } | undefined;
    const method = (init?.method ?? "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD") {
      records.push(`FETCH:${method} ${String(call[0])} ${JSON.stringify(init ?? null)}`);
    }
  }
  return records;
}

/** THE DISCRIMINATING ASSERTION, with a diagnostic that names the defect
 *  instead of reading "expected 0 to be 1". */
function refuseEarlyWrite(label: string): void {
  const early = writeRecords();
  if (early.length) {
    throw new Error(`${label} dispatched a write before confirmation: ${JSON.stringify(early)}`);
  }
}

/** One turn of the event loop inside act(). NOT a slept-through race: the
 *  sweep's own positive case -- the allowlisted PATCH -- is observed through
 *  this exact settle, so "no write recorded" cannot be an artifact of not
 *  having waited long enough. */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function enabledButtons(): HTMLButtonElement[] {
  return (screen.getAllByRole("button") as HTMLButtonElement[]).filter((b) => !b.disabled);
}

function buttonName(b: HTMLElement): string {
  return (b.textContent ?? "").replace(/\s+/g, " ").trim();
}

async function mount(): Promise<ReturnType<typeof userEvent.setup>> {
  // GatedWriteBanner's dismiss control writes sessionStorage, and jsdom keeps
  // that storage for the whole file. MEASURED: without this clear, the sweep's
  // own click on "Dismiss for this session" removed the banner's two buttons
  // from every later mount and the population silently shrank mid-sweep.
  sessionStorage.clear();
  const user = userEvent.setup();
  renderWired(<History />, "/history");
  // PRECONDITION: the route is really up, and its mount telemetry (the one
  // allowed POST) has landed, before any click is attributed to it.
  expect(await screen.findByRole("button", { name: "Compact database" })).toBeEnabled();
  await waitFor(() =>
    expect((apiPostMock.mock.calls as [string, unknown][]).map(([p]) => p)).toEqual([PAGE_VIEW]),
  );
  return user;
}

async function openTab(user: ReturnType<typeof userEvent.setup>, name: string): Promise<void> {
  if (name === TABS[0]) return;
  await user.click(screen.getByRole("tab", { name }));
  await settle();
}

/** The saved tab's "Save" is disabled until BOTH inputs are non-blank, so a
 *  sweep that did not type would click a dead control and record "no write" for
 *  the wrong reason. */
async function fillSavedInputs(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByPlaceholderText("name"), "gamma");
  await user.type(screen.getByPlaceholderText("query"), "delta");
  await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled());
}

function resetTransport(): void {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPatchMock.mockReset();
  apiDeleteMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
  apiPatchMock.mockResolvedValue({ ok: true });
  apiDeleteMock.mockResolvedValue({ ok: true });
  fetchMock.mockClear();
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
  if (!URL.createObjectURL) URL.createObjectURL = () => "blob:stub";
  if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {};
});

beforeEach(() => {
  fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    blob: async () => new Blob(["log"]),
    headers: { get: () => 'attachment; filename="ui_events.log"' },
  }));
  vi.stubGlobal("fetch", fetchMock);
  resetTransport();
});

// (label, tab, index among same-named buttons, accept label, the write the
// confirm must produce, the token the destructive dialog must display)
const GATED: Array<{
  label: string;
  tab: string;
  index: number;
  accept: "Yes, proceed" | "Confirm";
  expected: string;
  token: string | null;
}> = [
  {
    label: "Compact database",
    tab: "History & Search",
    index: 0,
    accept: "Yes, proceed",
    expected: 'POST /api/history/vacuum {}',
    token: "VACUUM HISTORY",
  },
  {
    label: "Clear log",
    tab: "Logs",
    index: 0,
    accept: "Yes, proceed",
    expected: 'POST /api/logs/clear {}',
    token: "CLEAR LOGS",
  },
  {
    label: "Save",
    tab: "Saved searches",
    index: 0,
    accept: "Confirm",
    expected: 'POST /api/saved_searches {"name":"gamma","query":"delta"}',
    token: null,
  },
  {
    label: "Delete",
    tab: "Saved searches",
    index: 0,
    accept: "Yes, proceed",
    expected: 'DELETE /api/saved_searches/7 null',
    token: "DELETE 7",
  },
];

describe("T2 confirmation-gated writes", () => {
  for (const spec of GATED) {
    it(`${spec.label} sends ${spec.expected.split(" ")[1]} only on ${spec.accept}`, async () => {
      const user = await mount();
      await openTab(user, spec.tab);
      if (spec.tab === "Saved searches") {
        // PRECONDITION: the fixture rows rendered. Every saved-tab control is
        // disabled when s.id == null, so a row-less fixture would make this
        // case pass by clicking nothing.
        expect(screen.getByText("nightly")).toBeInTheDocument();
        await fillSavedInputs(user);
      }

      const candidates = screen.getAllByRole("button", { name: spec.label });
      expect(candidates.length).toBeGreaterThan(spec.index);
      const trigger = candidates[spec.index];
      expect(trigger).toBeEnabled();

      await user.click(trigger);
      refuseEarlyWrite(spec.label);

      const dialog = await screen.findByRole("dialog");
      if (spec.token) {
        expect(within(dialog).getByText(spec.token)).toBeInTheDocument();
        // The destructive tier defaults to CANCEL. Asserted as the negative --
        // the accept control must not be the focused default -- which is the
        // actual safety property and does not pin whether Radix's FocusScope or
        // React's autoFocus won the race.
        expect(document.activeElement).not.toBe(
          within(dialog).getByRole("button", { name: spec.accept }),
        );
      }
      // Still nothing sent while the dialog is merely OPEN.
      refuseEarlyWrite(`${spec.label} (dialog open)`);

      await user.click(within(dialog).getByRole("button", { name: spec.accept }));
      await waitFor(() => expect(writeRecords()).toEqual([spec.expected]));
    }, 20000);
  }

  it("cancelling the vacuum dialog closes it and sends nothing at all", async () => {
    const user = await mount();
    await user.click(screen.getByRole("button", { name: "Compact database" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "No, cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await settle();
    expect(writeRecords()).toEqual([]);
  }, 20000);

  it("the action-lane toggle de-escalates immediately and escalates only through the dialog", async () => {
    // OVER-SENSITIVITY CONTROL, IN VIVO. A gate that banned every one-click
    // write would reject THIS, which is correct product code. The gate must
    // accept the de-escalation and still refuse the escalation.
    let user = await mount();
    await openTab(user, "Saved searches");
    expect(screen.getByText("nightly")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "→ notify" }));
    await waitFor(() =>
      expect(writeRecords()).toEqual(['PATCH /api/saved_searches/7 {"action":"notify"}']),
    );
    expect(screen.queryByRole("dialog")).toBeNull();

    cleanup();
    resetTransport();
    user = await mount();
    await openTab(user, "Saved searches");
    await user.click(screen.getByRole("button", { name: "→ enqueue" }));
    refuseEarlyWrite("escalating the action lane to enqueue");
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(writeRecords()).toEqual(['PATCH /api/saved_searches/9 {"action":"enqueue"}']),
    );
  }, 30000);

  it("CLOSED ALLOWLIST: clicking every enabled button on every tab writes only the allowed set", async () => {
    const clicked: string[] = [];
    const immediate: string[] = [];

    for (const tabName of TABS) {
      // EVERY mount in this sweep starts from a reset transport, the
      // enumeration mount included: without that reset it inherits the previous
      // iteration's page_view and mount()'s own precondition fails.
      resetTransport();
      let user = await mount();
      await openTab(user, tabName);
      if (tabName === "Saved searches") await fillSavedInputs(user);
      const names = enabledButtons().map(buttonName);
      // VACUITY GUARD: a tab that rendered no enabled control would otherwise
      // contribute a silent zero to a set-equality verdict.
      expect(names.length).toBeGreaterThan(0);
      cleanup();

      for (let i = 0; i < names.length; i += 1) {
        resetTransport();
        user = await mount();
        await openTab(user, tabName);
        if (tabName === "Saved searches") await fillSavedInputs(user);
        const buttons = enabledButtons();
        // PRECONDITION ON THE SELECTION: the population must be the same one
        // enumerated above, or index i is clicking a different control than the
        // name recorded for it.
        expect(buttons.map(buttonName)).toEqual(names);

        await user.click(buttons[i]);
        await settle();
        clicked.push(`${tabName} :: ${names[i]}`);
        immediate.push(...writeRecords().map((r) => `${names[i]} -> ${r}`));
        cleanup();
      }
    }

    // The sweep really swept: a nonzero population, and the four gated controls
    // are inside it rather than assumed to be.
    expect(clicked.length).toBeGreaterThan(0);
    const clickedNames = new Set(clicked.map((c) => c.split(" :: ")[1]));
    for (const required of WRITE_BUTTONS) {
      expect([...clickedNames].some((n) => n.includes(required))).toBe(true);
    }

    const allowed = ALLOWED_IMMEDIATE_WRITES.map((w) => `→ notify -> ${w}`);
    expect([...new Set(immediate)].sort()).toEqual([...allowed].sort());
  }, 120000);
});
