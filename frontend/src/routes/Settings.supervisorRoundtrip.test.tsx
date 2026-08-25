// Backlog row 240 (v3.66.1240), the TRANSPORT half.
//
// Settings.tsx:403-406 (pre-fix line numbers) put supEnabled / supGlobalBps /
// supPerSiteJson into POST /api/supervisor/configure UNCONDITIONALLY. The
// .trim() guard above it gates PARSING, not inclusion. With no GET consumer for
// supervisor state anywhere in the SPA, those three were pure component
// defaults, so an operator who opened the page and pressed Apply without
// touching anything POSTed {enabled:false, global_bps:0, per_site_bps:{}} --
// app_supervisor.py hands that straight to download_supervisor.configure() and
// every live byte-rate limit is gone.
//
// SEVERITY, STATED RATHER THAN ASSUMED, because the sibling row 238 was worse:
// this is LIVE IN-MEMORY throttle state that a restart resets anyway, not a
// persisted list, and it sits behind an Apply + Confirm dialog. It is still a
// silent reset of state the operator never saw.
//
// This spec asserts what the transport is handed. Settings.supervisorSeeding
// .test.tsx owns the display half and the async-arrival case.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  apiPostForm: vi.fn(),
  apiPostDownload: vi.fn(),
  ApiError: class extends Error {},
}));

import { Settings } from "@/routes/Settings";
import { freshQueryClient, installApiFixtures } from "@/test/wiredGateHarness";

const STATUS_PATH = "/api/supervisor/status";
const CONFIGURE_PATH = "/api/supervisor/configure";
const STATUS_KEY = ["supervisor", "status"];

const STORED_GLOBAL_BPS = 12500000;
const STORED_PER_SITE = { "example.com": 500000, aylo: 250000 };
const TYPED_PER_SITE_JSON = '{"typed.example":42}';
// user-event reads "{" as the start of a special-key descriptor, so a literal
// brace has to be doubled in the TYPING string only. The assertion below still
// compares against the real JSON.
const TYPED_PER_SITE_KEYSTROKES = TYPED_PER_SITE_JSON.replace(/\{/g, "{{");
const STORED_STATUS = {
  ok: true,
  stats: {
    enabled: true,
    global: { name: "global", rate_bps: STORED_GLOBAL_BPS },
    per_site: {},
    config: { global_bps: STORED_GLOBAL_BPS, per_site_bps: STORED_PER_SITE },
  },
};

const FIXTURES: Record<string, unknown> = {
  "/api/settings/env/effective": { env: [] },
  "/api/settings/envfile": { env: [], path: "/x/.env", exists: true, writable: true },
  [STATUS_PATH]: STORED_STATUS,
};

function mount(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/settings"]}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function section(): HTMLElement {
  const el = document.getElementById("supervisor-throttle");
  expect(el, "the supervisor throttle section is not in the DOM").toBeTruthy();
  return el as HTMLElement;
}

// PRECONDITION HELPER. The section is `collapsible defaultOpen={false}` and
// ui/collapsible.tsx renders `{shown && children}`, so nothing inside it is
// mounted until the header is clicked. Asserting the three controls exist here
// holds on the DEFECTIVE tree as well, which keeps each test's BODY assertion
// the first thing that can fail.
async function openSupervisor(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() =>
    expect(document.getElementById("supervisor-throttle")).toBeTruthy(),
  );
  const header = within(section()).getAllByRole("button")[0];
  expect(header).toHaveAttribute("aria-expanded", "false");
  await user.click(header);
  expect(header).toHaveAttribute("aria-expanded", "true");
  expect(enabledSwitch()).toBeInTheDocument();
  expect(globalInput()).toBeInTheDocument();
  expect(perSiteBox()).toBeInTheDocument();
}

function enabledSwitch(): HTMLElement {
  return screen.getByRole("switch", { name: "Supervisor throttle enabled" });
}

function globalInput(): HTMLInputElement {
  return within(section()).getByRole("spinbutton") as HTMLInputElement;
}

function perSiteBox(): HTMLTextAreaElement {
  return screen.getByLabelText(
    "Per-site bytes per second JSON",
  ) as HTMLTextAreaElement;
}

function statusGets(): string[] {
  return apiGetMock.mock.calls
    .map((c) => String(c[0]))
    .filter((p) => p === STATUS_PATH);
}

async function applyConfirmed(user: ReturnType<typeof userEvent.setup>) {
  await user.click(within(section()).getByRole("button", { name: "Apply" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
}

function lastBody(path: string): Record<string, unknown> {
  const call = [...apiPostMock.mock.calls].reverse().find((c) => c[0] === path);
  expect(call, `no POST to ${path} was observed`).toBeTruthy();
  return (call as unknown[])[1] as Record<string, unknown>;
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
});

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("row 240 -- an Apply sends the limits the operator can see", () => {
  it("THE WIRING: the SPA consumes GET /api/supervisor/status at all", async () => {
    // Before this cut `grep -rc supervisor/status frontend/src` matched
    // nothing: the write endpoint was wired and the read endpoint was not.
    // Nothing else in this file can hold if this does not.
    const user = userEvent.setup();
    mount(freshQueryClient());
    await openSupervisor(user);
    await waitFor(() => expect(statusGets().length).toBeGreaterThan(0));
    expect(statusGets()[0]).toBe(STATUS_PATH);
  });

  it("an untouched Apply sends the stored limits back, not the component defaults", async () => {
    const user = userEvent.setup();
    const qc = freshQueryClient();
    qc.setQueryData(STATUS_KEY, STORED_STATUS);
    mount(qc);
    await openSupervisor(user);
    // Nothing is typed, nothing is toggled: exactly the operator who came to
    // this page for another setting and pressed Apply.
    await applyConfirmed(user);

    const body = lastBody(CONFIGURE_PATH);
    expect(body.enabled).toBe(true);
    expect(body.global_bps).toBe(STORED_GLOBAL_BPS);
    expect(body.per_site_bps).toEqual(STORED_PER_SITE);
  });

  it("OVER-CORRECTION CONTROL: a DELIBERATELY cleared field still sends 0 and {}", async () => {
    // The tempting over-correction is to guard inclusion the way a write-only
    // secret is guarded -- "omit the key when the field is blank" -- which would
    // make the limits impossible to REMOVE from the UI. Clearing on purpose and
    // never typing must stay distinguishable, so all three keys must still be
    // sent with their emptied values.
    //
    // MEASURED ON THE BASE: this one is RED there as well, because the switch
    // starts from the SERVER's true on a seeded tree and from the component's
    // false on the defective one, so the same click lands on opposite states.
    // Its control role is against the wrong fixes, not against the base.
    const user = userEvent.setup();
    const qc = freshQueryClient();
    qc.setQueryData(STATUS_KEY, STORED_STATUS);
    mount(qc);
    await openSupervisor(user);

    await user.click(enabledSwitch());
    await user.clear(globalInput());
    await user.type(globalInput(), "0");
    await user.clear(perSiteBox());
    expect(enabledSwitch()).toHaveAttribute("aria-checked", "false");
    expect(globalInput().value).toBe("0");
    expect(perSiteBox().value).toBe("");

    await applyConfirmed(user);
    const body = lastBody(CONFIGURE_PATH);
    expect(Object.keys(body).sort()).toEqual([
      "enabled",
      "global_bps",
      "per_site_bps",
    ]);
    expect(body.enabled).toBe(false);
    expect(body.global_bps).toBe(0);
    expect(body.per_site_bps).toEqual({});
  });

  it("an edit wins over the seed", async () => {
    const user = userEvent.setup();
    const qc = freshQueryClient();
    qc.setQueryData(STATUS_KEY, STORED_STATUS);
    mount(qc);
    await openSupervisor(user);

    await user.clear(perSiteBox());
    await user.type(perSiteBox(), TYPED_PER_SITE_KEYSTROKES);
    expect(perSiteBox().value).toBe(TYPED_PER_SITE_JSON);

    await applyConfirmed(user);
    const body = lastBody(CONFIGURE_PATH);
    expect(body.per_site_bps).toEqual({ "typed.example": 42 });
    // the untouched fields still carry the server's values
    expect(body.global_bps).toBe(STORED_GLOBAL_BPS);
  });

  it("a successful apply re-reads the status it just changed", async () => {
    // The POST is what made the cached payload stale. Without the
    // invalidation the next mount seeds from a snapshot that predates the
    // operator's own apply.
    const user = userEvent.setup();
    mount(freshQueryClient());
    await openSupervisor(user);
    await waitFor(() => expect(statusGets().length).toBe(1));

    await applyConfirmed(user);
    await waitFor(() => expect(statusGets().length).toBeGreaterThan(1));
  });
});
